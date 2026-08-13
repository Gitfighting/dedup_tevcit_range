"""
Two-stage deduplication index.

Stage 1 (in-memory):  Bloom filter for fast negative lookups.
    If a fingerprint is NOT in the Bloom filter, we can skip the
    expensive Stage 2 lookup with 100% certainty.  This eliminates
    ~99% of disk I/O for unique chunks.

Stage 2 (disk):  LMDB-backed key-value store.
    Maps fingerprint (BLAKE3, 32 bytes) -> ChunkLocation
    (region tag, file offset).  Crash-safe, ACID via LMDB.

References:
    Bloom, B. "Space/Time Trade-offs in Hash Coding with Allowable
    Errors", CACM 1970.
    LMDB: Symas Corp., "Lightning Memory-Mapped Database".
"""

import hashlib
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set


# ------------------------------------------------------------------
#  ChunkLocation
# ------------------------------------------------------------------

@dataclass
class ChunkLocation:
    region: str
    offset: int
    size: int

    def to_bytes(self) -> bytes:
        region_bytes = self.region.encode("utf-8")
        rlen = len(region_bytes)
        return struct.pack(f"!B{rlen}sQI", rlen, region_bytes, self.offset, self.size)

    @classmethod
    def from_bytes(cls, data: bytes) -> "ChunkLocation":
        rlen = data[0]
        region = data[1:1 + rlen].decode("utf-8")
        offset, size = struct.unpack_from("!QI", data, 1 + rlen)
        return cls(region=region, offset=offset, size=size)


# ------------------------------------------------------------------
#  Bloom filter (pure Python — sufficient for prototyping)
# ------------------------------------------------------------------

class BloomFilter:
    """Counting Bloom filter with configurable capacity and false-positive rate.

    Uses k independent hash functions derived from SHA-256 truncation.
    """

    def __init__(self, capacity: int = 20_000_000, error_rate: float = 0.01):
        # optimal bit-array size: m = -n*ln(p) / (ln2)^2
        import math
        self.m = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self.k = max(1, int(self.m / capacity * math.log(2)))
        # round up to next multiple of 8
        self.m = ((self.m + 7) // 8) * 8
        self.bits = bytearray(self.m // 8)
        self._lock = threading.Lock()

    def _hashes(self, key: bytes):
        h = hashlib.sha256(key).digest()
        for i in range(self.k):
            offset = (i * 4) % (len(h) - 3)
            v = struct.unpack_from("!I", h, offset)[0]
            yield v % self.m

    def add(self, key: bytes):
        with self._lock:
            for idx in self._hashes(key):
                byte_idx = idx // 8
                bit_idx = idx % 8
                self.bits[byte_idx] |= (1 << bit_idx)

    def contains(self, key: bytes) -> bool:
        with self._lock:
            for idx in self._hashes(key):
                byte_idx = idx // 8
                bit_idx = idx % 8
                if not (self.bits[byte_idx] & (1 << bit_idx)):
                    return False
            return True

    def __len__(self):
        # approximate count
        ones = sum(bin(b).count("1") for b in self.bits)
        if ones == 0:
            return 0
        import math
        return int(-self.m / self.k * math.log(1 - ones / self.m))


# ------------------------------------------------------------------
#  DedupIndex  (Bloom + LMDB)
# ------------------------------------------------------------------

class DedupIndex:
    """Two-stage deduplication index.

    Usage::

        idx = DedupIndex("/data/dedup/index")
        idx.insert(fp_bytes, ChunkLocation("aws:us-east-1", 0, 4096))
        loc = idx.lookup(fp_bytes)   # -> ChunkLocation or None
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.bloom = BloomFilter(capacity=20_000_000, error_rate=0.01)
        self._env = None
        self._db = None
        self._lock = threading.Lock()
        self._stats_inserts = 0
        self._stats_lookups = 0

    def _ensure_lmdb(self):
        if self._db is not None:
            return
        try:
            import lmdb
        except ImportError:
            raise ImportError("pip install lmdb")

        self._env = lmdb.open(str(self.db_path), map_size=8 * 1024 * 1024 * 1024)  # 8 GB
        self._db = self._env.open_db()

    def insert(self, fingerprint: bytes, location: ChunkLocation):
        self._ensure_lmdb()
        self.bloom.add(fingerprint)
        with self._env.begin(write=True) as txn:
            txn.put(fingerprint, location.to_bytes())
        self._stats_inserts += 1

    def insert_batch(self, entries: List[tuple]):
        """Batch insert: list of (fingerprint, ChunkLocation)."""
        self._ensure_lmdb()
        with self._env.begin(write=True) as txn:
            for fingerprint, location in entries:
                self.bloom.add(fingerprint)
                txn.put(fingerprint, location.to_bytes())
        self._stats_inserts += len(entries)

    def lookup(self, fingerprint: bytes) -> Optional[ChunkLocation]:
        self._stats_lookups += 1
        # Stage 1: Bloom filter
        if not self.bloom.contains(fingerprint):
            return None
        # Stage 2: LMDB
        self._ensure_lmdb()
        with self._env.begin() as txn:
            raw = txn.get(fingerprint)
            if raw is None:
                return None
            return ChunkLocation.from_bytes(raw)

    def bulk_lookup(self, fingerprints: List[bytes]) -> List[Optional[ChunkLocation]]:
        """Look up many fingerprints at once; returns list of None or ChunkLocation."""
        results = []
        # fast-pass: Bloom filter
        candidates = []
        for i, fp in enumerate(fingerprints):
            self._stats_lookups += 1
            if self.bloom.contains(fp):
                candidates.append((i, fp))
            else:
                results.append((i, None))

        # LMDB lookup for candidates
        if candidates:
            self._ensure_lmdb()
            with self._env.begin() as txn:
                for i, fp in candidates:
                    raw = txn.get(fp)
                    if raw is not None:
                        results.append((i, ChunkLocation.from_bytes(raw)))
                    else:
                        results.append((i, None))

        results.sort(key=lambda x: x[0])
        return [loc for _, loc in results]

    def known_fingerprints(self) -> Set[bytes]:
        """Return set of all fingerprint keys (for migration/sync).  O(n)."""
        self._ensure_lmdb()
        fps = set()
        with self._env.begin() as txn:
            cursor = txn.cursor()
            for key, _ in cursor:
                fps.add(bytes(key))
        return fps

    def stats(self) -> dict:
        return {
            "inserts": self._stats_inserts,
            "lookups": self._stats_lookups,
            "bloom_approx_elements": len(self.bloom),
        }

    def close(self):
        if self._env is not None:
            self._env.close()
            self._env = None
            self._db = None
