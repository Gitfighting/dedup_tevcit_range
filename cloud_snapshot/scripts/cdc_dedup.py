#!/usr/bin/env python3
"""
cdc_dedup.py —— Content-Defined Chunking + SHA-256 内容寻址去重

传统去重架构：
  文件 → Rabin CDC 分块 → SHA-256 哈希 → 查重 → 仅存储唯一块

用于 SkyStore 实验：
  - PUT: 文件先经 CDC 分块，只上传"新块"到 SkyStore
  - GET: 从 manifest 重建文件（每个块通过 SkyStore 拉取）
  
成本对比：
  - dedup=off: 存储/传输 = 原始文件大小
  - dedup=on:  存储/传输 = SUM(唯一块大小)
"""

import hashlib
import os
import struct
import json
import time
from pathlib import Path
from collections import defaultdict
from typing import List, Tuple, Optional, Dict


# ============================================================
# Rabin CDC (Content-Defined Chunking)
# ============================================================

class RabinCDC:
    """
    Rabin fingerprint based Content-Defined Chunking.
    
    Uses a rolling hash with a 48-byte window. A chunk boundary is declared
    when (hash & mask) == 0, within [min_size, max_size].
    
    Default: avg=8KB, min=2KB, max=64KB
    """
    
    def __init__(self, avg_size: int = 8192, min_size: int = 2048, max_size: int = 65536):
        self.avg_size = avg_size
        self.min_size = min_size
        self.max_size = max_size
        # Mask: use next power-of-2 minus 1 for fast bitwise check
        # e.g., avg_size=8192 → mask=8191
        self.mask = self._next_pow2(avg_size) - 1
        self.window_size = 48  # Rabin window for fingerprint
    
    @staticmethod
    def _next_pow2(n: int) -> int:
        p = 1
        while p < n:
            p <<= 1
        return p
    
    def chunk(self, data: bytes) -> List[bytes]:
        """Split data into variable-sized chunks using CDC."""
        chunks = []
        start = 0
        window = bytearray(self.window_size)
        
        n = len(data)
        for i in range(n):
            window[i % self.window_size] = data[i]
            
            if i - start < self.min_size:
                continue
            
            if i - start >= self.max_size:
                # Force boundary at max_size
                chunks.append(data[start:i + 1])
                start = i + 1
            else:
                # Check Rabin fingerprint
                h = self._fingerprint(window)
                if h & self.mask == 0:
                    chunks.append(data[start:i + 1])
                    start = i + 1
        
        # Last chunk
        if start < n:
            chunks.append(data[start:])
        
        return chunks
    
    @staticmethod
    def _fingerprint(window: bytearray) -> int:
        """Simple rolling hash fingerprint using SHA-256 on the window."""
        return int.from_bytes(hashlib.sha256(window).digest()[-8:], 'big')


# ============================================================
# Chunk Store (Content-Addressable Storage)
# ============================================================

class ChunkStore:
    """
    Content-addressable chunk storage.
    
    Chunks are stored on disk at `store_dir`/<first_2_hex_chars>/<hash>.
    An in-memory index tracks hash -> location.
    
    Dedup: when inserting a chunk with an existing hash, it's skipped.
    """
    
    def __init__(self, store_dir: str = "/data/chunks"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory index: hash -> (file_path, size, ref_count)
        self.index: Dict[str, Tuple[Path, int, int]] = {}
        
        # Statistics
        self.stats = {
            "total_chunks_seen": 0,
            "unique_chunks": 0,
            "duplicate_chunks": 0,
            "total_bytes_raw": 0,
            "total_bytes_stored": 0,
            "dedup_ratio": 1.0,
        }
        
        # Load existing index
        self._load_index()
    
    def _chunk_path(self, chunk_hash: str) -> Path:
        """Get the storage path for a chunk hash."""
        prefix = chunk_hash[:2]
        return self.store_dir / prefix / chunk_hash
    
    def _load_index(self):
        """Load existing chunk index from disk."""
        if not self.store_dir.exists():
            return
        
        for chunk_file in self.store_dir.rglob("*"):
            if chunk_file.is_file() and len(chunk_file.name) == 64:  # SHA-256 hex digest
                chunk_hash = chunk_file.name
                size = chunk_file.stat().st_size
                self.index[chunk_hash] = (chunk_file, size, 1)
                self.stats["unique_chunks"] += 1
                self.stats["total_bytes_stored"] += size
    
    def put(self, chunk_hash: str, chunk_data: bytes) -> Tuple[bool, int]:
        """
        Store a chunk. Returns (is_new, size).
        is_new=False means the chunk already existed (dedup hit).
        """
        self.stats["total_chunks_seen"] += 1
        self.stats["total_bytes_raw"] += len(chunk_data)
        
        if chunk_hash in self.index:
            self.stats["duplicate_chunks"] += 1
            _, size, ref_count = self.index[chunk_hash]
            self.index[chunk_hash] = (self._chunk_path(chunk_hash), size, ref_count + 1)
            return False, size
        
        # New chunk
        chunk_path = self._chunk_path(chunk_hash)
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        chunk_path.write_bytes(chunk_data)
        
        self.index[chunk_hash] = (chunk_path, len(chunk_data), 1)
        self.stats["unique_chunks"] += 1
        self.stats["total_bytes_stored"] += len(chunk_data)
        
        return True, len(chunk_data)
    
    def get(self, chunk_hash: str) -> Optional[bytes]:
        """Retrieve a chunk by hash."""
        if chunk_hash not in self.index:
            return None
        path, _, _ = self.index[chunk_hash]
        if path.exists():
            return path.read_bytes()
        return None
    
    def exists(self, chunk_hash: str) -> bool:
        return chunk_hash in self.index
    
    def dump_stats(self) -> dict:
        """Return dedup statistics."""
        if self.stats["total_bytes_raw"] > 0:
            self.stats["dedup_ratio"] = self.stats["total_bytes_stored"] / self.stats["total_bytes_raw"]
        return dict(self.stats)
    
    def save_index(self):
        """Persist index metadata."""
        idx_path = self.store_dir / "chunk_index.json"
        idx_data = {
            "stats": self.stats,
            "index_size": len(self.index),
        }
        idx_path.write_text(json.dumps(idx_data, indent=2))


# ============================================================
# Dedup Manager
# ============================================================

class DedupManager:
    """
    Orchestrates CDC chunking + dedup for files.
    
    Usage:
        mgr = DedupManager()
        
        # PUT: deduplicate a file
        manifest = mgr.dedup_file("/data/datasets/linux/kernel.tar.xz")
        # manifest = list of chunk_hashes
        
        # GET: reconstruct a file
        data = mgr.reconstruct_file(manifest)
    """
    
    def __init__(self, chunk_store_dir: str = "/data/chunks", avg_chunk_size: int = 8192):
        self.cdc = RabinCDC(avg_size=avg_chunk_size)
        self.store = ChunkStore(chunk_store_dir)
        
        # Object manifest index: obj_key -> list of chunk hashes
        self.manifests: Dict[str, List[str]] = {}
        self.manifest_path = Path(chunk_store_dir) / "manifests.json"
        self._load_manifests()
    
    def _load_manifests(self):
        if self.manifest_path.exists():
            self.manifests = json.loads(self.manifest_path.read_text())
    
    def _save_manifests(self):
        self.manifest_path.write_text(json.dumps(self.manifests, indent=2))
    
    def dedup_bytes(self, obj_key: str, data: bytes) -> List[str]:
        """
        Process raw bytes for deduplication.
        
        Returns manifest: list of SHA-256 chunk hashes.
        Also stores unique chunks in the chunk store.
        """
        chunks = self.cdc.chunk(data)
        
        manifest = []
        for chunk_data in chunks:
            chunk_hash = hashlib.sha256(chunk_data).hexdigest()
            self.store.put(chunk_hash, chunk_data)
            manifest.append(chunk_hash)
        
        self.manifests[obj_key] = manifest
        return manifest
    
    def dedup_file(self, obj_key: str, file_path: str) -> List[str]:
        """
        Process a file for deduplication.
        
        Returns manifest: list of SHA-256 chunk hashes.
        Also stores unique chunks in the chunk store.
        """
        full_path = Path(file_path)
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        data = full_path.read_bytes()
        return self.dedup_bytes(obj_key, data)
    
    def reconstruct_file(self, obj_key: str) -> Optional[bytes]:
        """Reconstruct a file from its chunk manifest."""
        if obj_key not in self.manifests:
            return None
        
        result = bytearray()
        for chunk_hash in self.manifests[obj_key]:
            chunk_data = self.store.get(chunk_hash)
            if chunk_data is None:
                raise ValueError(f"Missing chunk: {chunk_hash}")
            result.extend(chunk_data)
        
        return bytes(result)
    
    def get_manifest(self, obj_key: str) -> Optional[List[str]]:
        return self.manifests.get(obj_key)
    
    def get_unique_stored_bytes(self, obj_key: str) -> int:
        """Calculate how many NEW bytes this object triggered (dedup cost)."""
        manifest = self.manifests.get(obj_key, [])
        total = 0
        for chunk_hash in manifest:
            _, size, _ = self.store.index.get(chunk_hash, (None, 0, 0))
            total += size
        return total
    
    def get_object_dedup_cost_ratio(self, obj_key: str, raw_size: int) -> float:
        """Dedup cost / raw cost ratio for this object."""
        if raw_size == 0:
            return 1.0
        stored = self.get_unique_stored_bytes(obj_key)
        return stored / raw_size
    
    def save(self):
        """Persist manifests."""
        self._save_manifests()
        self.store.save_index()
    
    def stats(self) -> dict:
        """Get combined statistics with proper dedup ratio."""
        s = dict(self.store.stats)
        # Recalculate dedup_ratio (store.stats may have stale initial value)
        if s["total_bytes_raw"] > 0:
            s["dedup_ratio"] = s["total_bytes_stored"] / s["total_bytes_raw"]
        s["total_objects"] = len(self.manifests)
        s["avg_chunks_per_object"] = (
            sum(len(v) for v in self.manifests.values()) / len(self.manifests)
            if self.manifests else 0
        )
        return s


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    import tempfile, random
    
    print("=" * 60)
    print("CDC Dedup Self-Test")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DedupManager(chunk_store_dir=tmpdir)
        
        # Test 1: CDC chunking
        print("\n[Test 1] CDC chunking")
        rng = random.Random(42)
        test_data = bytes([rng.randint(0, 255) for _ in range(1024 * 1024)])  # 1MB random
        # Insert some repeated patterns
        pattern = b"This is a repeated pattern in the data." * 100
        test_data = test_data[:100000] + pattern + test_data[100000 + len(pattern):]
        
        t0 = time.time()
        chunks = mgr.cdc.chunk(test_data)
        elapsed = time.time() - t0
        
        print(f"  Data size: {len(test_data):,} B")
        print(f"  Chunks: {len(chunks)}")
        print(f"  Avg chunk: {sum(len(c) for c in chunks)//len(chunks):,} B")
        print(f"  Min: {min(len(c) for c in chunks):,}, Max: {max(len(c) for c in chunks):,}")
        print(f"  Time: {elapsed:.3f}s ({len(test_data)/elapsed/1e6:.1f} MB/s)")
        
        # Test 2: Dedup with identical file
        print("\n[Test 2] Deduplication")
        
        # Create two "files" - one original, one copy
        file1 = os.path.join(tmpdir, "file1.bin")
        file2 = os.path.join(tmpdir, "file2.bin")
        
        with open(file1, "wb") as f:
            f.write(test_data)
        with open(file2, "wb") as f:
            f.write(test_data)  # Identical!
        
        m1 = mgr.dedup_file("obj1", file1)
        stats_after_1 = mgr.stats()
        print(f"  After obj1: {stats_after_1['unique_chunks']} unique / {stats_after_1['total_chunks_seen']} total")
        print(f"  Stored: {stats_after_1['total_bytes_stored']:,} B / Raw: {stats_after_1['total_bytes_raw']:,} B")
        
        m2 = mgr.dedup_file("obj2", file2)
        stats_after_2 = mgr.stats()
        print(f"  After obj2 (identical): {stats_after_2['unique_chunks']} unique / {stats_after_2['total_chunks_seen']} total")
        print(f"  Stored: {stats_after_2['total_bytes_stored']:,} B / Raw: {stats_after_2['total_bytes_raw']:,} B")
        print(f"  Duplicates: {stats_after_2['duplicate_chunks']}")
        print(f"  Dedup ratio: {stats_after_2['dedup_ratio']:.2%}")
        
        # Test 3: Reconstruction
        print("\n[Test 3] Reconstruction")
        reconstructed = mgr.reconstruct_file("obj1")
        print(f"  Original: {len(test_data):,} B, Reconstructed: {len(reconstructed):,} B")
        print(f"  Match: {reconstructed == test_data}")
        
        assert reconstructed == test_data, "Reconstruction failed!"
        assert stats_after_2['total_bytes_stored'] == stats_after_1['total_bytes_stored'], "Dedup failed!"
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
