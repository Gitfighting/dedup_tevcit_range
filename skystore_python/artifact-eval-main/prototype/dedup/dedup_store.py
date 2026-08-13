"""
Chunk physical storage layer.

Each unique chunk is stored as a file named by its BLAKE3 fingerprint
(hex-encoded) under a sharded directory hierarchy.  The first 3 hex
chars determine the sub-directory to avoid millions of files in one
flat directory.

Layout::

    {root}/                         ← e.g. /data/dedup/chunks/
      abc/
        abcdef0123...               ← chunk content (raw bytes)
        abc1112223...
      def/
        ...

Reads are O(1) via direct file path lookup.
"""

import os
from pathlib import Path
from typing import List


class ChunkStore:
    """File-system-backed chunk repository.

    Parameters
    ----------
    root : str
        Root directory for chunk storage.
    """

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, fingerprint_hex: str) -> Path:
        prefix = fingerprint_hex[:3]
        subdir = self.root / prefix
        subdir.mkdir(exist_ok=True)
        return subdir / fingerprint_hex

    def put(self, fingerprint_hex: str, data: bytes) -> int:
        """Write chunk data atomically; return bytes written."""
        path = self._path(fingerprint_hex)
        if path.exists():
            return path.stat().st_size
        # Atomic write: write to a temp file, then rename.
        # Prevents TOCTOU race when two threads write the same chunk.
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            os.write(tmp_fd, data)
        finally:
            os.close(tmp_fd)
        try:
            os.rename(tmp_path, str(path))
        except OSError:
            # Another thread already wrote this chunk — remove temp
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return len(data)

    def put_batch(self, items: List[tuple]) -> int:
        """Batch write: list of (fingerprint_hex, data). Returns total bytes."""
        total = 0
        for fp, data in items:
            total += self.put(fp, data)
        return total

    def get(self, fingerprint_hex: str) -> bytes:
        """Read chunk data by fingerprint."""
        path = self._path(fingerprint_hex)
        if not path.exists():
            raise FileNotFoundError(f"Chunk {fingerprint_hex} not found")
        return path.read_bytes()

    def exists(self, fingerprint_hex: str) -> bool:
        return self._path(fingerprint_hex).exists()

    def total_size(self) -> int:
        """Total bytes stored (walk all shards)."""
        total = 0
        for entry in self.root.iterdir():
            if entry.is_dir():
                for f in entry.iterdir():
                    if f.is_file():
                        total += f.stat().st_size
        return total

    def chunk_count(self) -> int:
        cnt = 0
        for entry in self.root.iterdir():
            if entry.is_dir():
                cnt += sum(1 for f in entry.iterdir() if f.is_file())
        return cnt
