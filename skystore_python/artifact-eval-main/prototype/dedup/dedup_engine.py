"""
Unified deduplication engine.

Ties together FastCDC chunking, BLAKE3 fingerprinting, the two-stage
dedup index, and chunk storage into a single interface for the
store-server PUT/GET handlers.

Typical usage::

    engine = DedupEngine(chunk_root="/data/dedup/chunks",
                         index_path="/data/dedup/index")
    # PUT side
    manifest = engine.chunk_and_dedup(file_path, region="aws:us-east-1")
    # GET side
    data = engine.assemble(manifest)
"""

import os
import threading
from dataclasses import dataclass, field
from typing import List, Optional

import blake3

from .cdc_chunker import FastCDC
from .dedup_index import DedupIndex, ChunkLocation
from .dedup_store import ChunkStore


@dataclass
class ChunkManifestEntry:
    fingerprint: str   # hex-encoded BLAKE3
    size: int
    region: str        # where the chunk physically resides

    def to_dict(self):
        return {"fp": self.fingerprint, "size": self.size, "region": self.region}

    @classmethod
    def from_dict(cls, d):
        return cls(fingerprint=d["fp"], size=d["size"], region=d["region"])


@dataclass
class DedupManifest:
    """Result of chunk_and_dedup — the chunk-level map of an object."""
    entries: List[ChunkManifestEntry] = field(default_factory=list)
    new_chunks: int = 0       # how many chunks were newly stored
    duplicate_chunks: int = 0 # how many chunks already existed
    total_logical_bytes: int = 0
    total_physical_bytes: int = 0  # new bytes actually written

    def dedup_ratio(self) -> float:
        if self.total_logical_bytes == 0:
            return 1.0
        return self.total_physical_bytes / self.total_logical_bytes

    def to_list(self):
        return [e.to_dict() for e in self.entries]

    @classmethod
    def from_list(cls, data):
        return cls(entries=[ChunkManifestEntry.from_dict(d) for d in data])


class DedupEngine:
    """Unified deduplication engine.

    Parameters
    ----------
    chunk_root : str
        File-system path for chunk storage (ChunkStore).
    index_path : str
        LMDB database directory path for the dedup index.
    region : str
        Default region tag for newly stored chunks.
    """

    _instances = {}
    _lock = threading.Lock()

    def __init__(self, chunk_root: str, index_path: str, region: str = "local"):
        self.chunker = FastCDC(min_size=2*1024, avg_size=8*1024, max_size=64*1024)
        self.store = ChunkStore(chunk_root)
        self.index = DedupIndex(index_path)
        self.region = region

    @classmethod
    def get_or_create(cls, chunk_root: str, index_path: str, region: str = "local"):
        key = (chunk_root, index_path)
        with cls._lock:
            if key not in cls._instances:
                cls._instances[key] = cls(chunk_root, index_path, region)
            return cls._instances[key]

    # ---------------------------------------------------------------
    #  PUT side: chunk → fingerprint → dedup → store unique chunks
    # ---------------------------------------------------------------

    def chunk_and_dedup(self, data: bytes, region: Optional[str] = None) -> DedupManifest:
        """Chunk raw bytes, deduplicate, store unique chunks, return manifest."""
        if region is None:
            region = self.region

        chunks = self.chunker.chunk(data)
        manifest = DedupManifest()
        manifest.total_logical_bytes = len(data)

        # Phase 1: fingerprint all chunks
        fps_hex = []
        fps_bytes = []
        new_entries = []

        for chunk in chunks:
            h = blake3.blake3(chunk).digest()
            fp_hex = blake3.blake3(chunk).hexdigest()
            fps_hex.append(fp_hex)
            fps_bytes.append(h)

        # Phase 2: bulk lookup in index
        locations = self.index.bulk_lookup(fps_bytes)

        # Phase 3: store unique chunks
        for i, (fp_hex, fp_bytes, chunk) in enumerate(zip(fps_hex, fps_bytes, chunks)):
            loc = locations[i]

            if loc is not None:
                # duplicate — just reference existing location
                manifest.entries.append(ChunkManifestEntry(
                    fingerprint=fp_hex, size=len(chunk), region=loc.region
                ))
                manifest.duplicate_chunks += 1
            else:
                # new unique chunk — store it
                self.store.put(fp_hex, chunk)
                new_loc = ChunkLocation(region=region, offset=0, size=len(chunk))
                new_entries.append((fp_bytes, new_loc))
                manifest.entries.append(ChunkManifestEntry(
                    fingerprint=fp_hex, size=len(chunk), region=region
                ))
                manifest.new_chunks += 1
                manifest.total_physical_bytes += len(chunk)

        # Phase 4: batch-update index
        if new_entries:
            self.index.insert_batch(new_entries)

        return manifest

    def chunk_and_dedup_file(self, file_path: str, region: Optional[str] = None) -> DedupManifest:
        """Convenience: chunk a file on disk and deduplicate."""
        with open(file_path, "rb") as f:
            data = f.read()
        return self.chunk_and_dedup(data, region)

    # ---------------------------------------------------------------
    #  GET side: assemble object from chunk manifest
    # ---------------------------------------------------------------

    def assemble(self, manifest: DedupManifest) -> bytes:
        """Re-assemble the full object from its chunk manifest."""
        chunks = []
        for entry in manifest.entries:
            chunk = self.store.get(entry.fingerprint)
            chunks.append(chunk)
        return b"".join(chunks)

    def assemble_from_list(self, entries: list) -> bytes:
        """Re-assemble from a list of dicts (e.g. from JSON/Pydantic)."""
        chunks = []
        for d in entries:
            fp = d["fp"] if isinstance(d, dict) else d.fingerprint
            chunk = self.store.get(fp)
            chunks.append(chunk)
        return b"".join(chunks)

    # ---------------------------------------------------------------
    #  Stats
    # ---------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "store_total_bytes": self.store.total_size(),
            "store_chunk_count": self.store.chunk_count(),
            **self.index.stats(),
        }
