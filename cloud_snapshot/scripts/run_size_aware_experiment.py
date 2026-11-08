#!/usr/bin/env python3
"""
run_size_aware_experiment.py —— 精确大小感知 CDC 去重消融实验

核心设计:
  1. 从 enriched file pool 中按大小选择真实文件
  2. 每个 trace PUT(size=S) → 选择总大小≈S 的真实文件组
  3. 用这些真实文件的字节运行 CDC 去重
  4. 对比: 原始字节(baseline) vs 唯一块字节(experiment)

用法:
  source /data/venv/bin/activate && python run_size_aware_experiment.py
"""

import sys, csv, time, json, hashlib, random
from pathlib import Path
from collections import defaultdict
from bisect import bisect_left

sys.path.insert(0, '/data/scripts')
from cdc_dedup import DedupManager

# === CONFIG ===
TRACE_DIR = Path("/data/traces/typeA")
POOL_FILE = Path("/data/filepool/enriched_file_list.json")
OUTPUT_DIR = Path("/data/results")
CHUNK_DIR = Path("/data/chunks_size_aware")
MAX_PUTS_PER_BUCKET = 300000  # Limit for speed

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class SizeAwareMapper:
    """Maps trace object size to real file content from the pool."""
    
    def __init__(self, pool_file: Path):
        pool = json.loads(pool_file.read_text())
        self.files = pool["files"]  # sorted by size
        
        # Build sorted size list for binary search
        self.sizes = [f["size"] for f in self.files]
        
        # Separate large files for GB-scale concatenation
        self.large_files = [f for f in self.files if f["size"] > 50_000_000]
        self.large_files.sort(key=lambda x: x["size"])
        
        print(f"Mapper ready: {len(self.files):,} files, "
              f"{len(self.large_files)} large (>50MB)")
    
    def get_content(self, target_size: int) -> tuple:
        """
        Return (file_paths, total_size, content_bytes) for the best match.
        
        Strategy:
          S < 1KB:    single closest file from pool
          S < 50MB:   single closest file, preferring exact match
          S < 150MB:  single large file (kernel tar.xz)
          S >= 150MB: concatenate multiple large files to reach S
        """
        if target_size < 1024:
            # Tiny: exact match from L1
            return self._single_closest(target_size)
        
        elif target_size < 50_000_000:
            # Small-medium: single file
            return self._single_closest(target_size)
        
        elif target_size < 150_000_000:
            # Medium-large: single kernel tarball
            return self._single_large(target_size)
        
        else:
            # GB-scale: concatenate multiple large files
            return self._concatenate_large(target_size)
    
    def _single_closest(self, target: int) -> tuple:
        """Find single file closest to target size."""
        idx = bisect_left(self.sizes, target)
        candidates = []
        if idx < len(self.sizes):
            candidates.append((abs(self.sizes[idx] - target), idx))
        if idx > 0:
            candidates.append((abs(self.sizes[idx-1] - target), idx-1))
        if not candidates:
            return ([], 0, b"")
        
        best_idx = min(candidates)[1]
        f = self.files[best_idx]
        try:
            content = Path(f["path"]).read_bytes()
        except:
            content = b""
        
        path = str(Path(f["path"]).name)
        return ([path], f["size"], content)
    
    def _single_large(self, target: int) -> tuple:
        """Find single large file closest to target."""
        if not self.large_files:
            return self._single_closest(target)
        
        large_sizes = [f["size"] for f in self.large_files]
        idx = bisect_left(large_sizes, target)
        candidates = []
        if idx < len(large_sizes):
            candidates.append((abs(large_sizes[idx] - target), idx))
        if idx > 0:
            candidates.append((abs(large_sizes[idx-1] - target), idx-1))
        
        best_idx = min(candidates)[1]
        f = self.large_files[best_idx]
        try:
            content = Path(f["path"]).read_bytes()
        except:
            content = b""
        
        path = str(Path(f["path"]).name)
        return ([path], f["size"], content)
    
    def _concatenate_large(self, target: int) -> tuple:
        """Concatenate large files to approximately match target size."""
        if not self.large_files:
            return self._single_large(target)
        
        # Pick files until we exceed target (but not by more than 1 extra file)
        paths = []
        total = 0
        contents = []
        
        # Shuffle deterministically by object key hash
        rng = random.Random(hash(str(target)) & 0xFFFFFFFF)
        available = list(self.large_files)
        rng.shuffle(available)
        
        for f in available:
            if total >= target:
                break
            paths.append(str(Path(f["path"]).name))
            total += f["size"]
            try:
                contents.append(Path(f["path"]).read_bytes())
            except:
                contents.append(b"")
        
        if not paths:
            # Fallback: repeat the largest file
            f = self.large_files[-1]
            repeat = max(1, target // f["size"] + 1)
            paths = [str(Path(f["path"]).name)] * repeat
            total = f["size"] * repeat
            content = Path(f["path"]).read_bytes()
            contents = [content] * repeat
        
        return (paths, total, b"".join(contents))


def run_experiment(bucket_id: str, trace_file: str, mapper: SizeAwareMapper,
                   baseline: bool = True, max_ops: int = MAX_PUTS_PER_BUCKET) -> dict:
    """Run CDC dedup experiment on one trace bucket."""
    
    label = "BASELINE" if baseline else "CDC DEDUP"
    print(f"\n  {'='*60}")
    print(f"  {bucket_id} - {label}")
    print(f"  {'='*60}")
    
    # Clean chunk store for fresh run
    if CHUNK_DIR.exists():
        import shutil
        shutil.rmtree(str(CHUNK_DIR))
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    
    mgr = DedupManager(chunk_store_dir=str(CHUNK_DIR)) if not baseline else None
    
    stats = {
        "bucket": bucket_id,
        "baseline": baseline,
        "put_count": 0,
        "get_count": 0,
        "miss_count": 0,
        "total_ops": 0,
        "bytes_raw": 0,
        "bytes_stored": 0,
        "bytes_transferred": 0,
        "object_sizes_matched": {},  # trace_size -> mapped_file
        "start_time": time.time(),
    }
    
    object_data = {}  # obj_key -> (total_size, content)
    
    print(f"  Replaying trace ({max_ops:,} PUT limit)...")
    t0 = time.time()
    
    with open(trace_file, 'r') as f:
        reader = csv.DictReader(f)
        put_count = 0
        
        for row in reader:
            op = row.get("op", "")
            obj_key = row.get("obj_key", "")
            
            try:
                trace_size = int(float(row.get("size", 0)))
            except:
                continue
            
            if op == "PUT":
                if put_count >= max_ops:
                    break
                
                stats["total_ops"] += 1
                stats["put_count"] += 1
                put_count += 1
                stats["bytes_raw"] += trace_size
                
                # Get real content matching this size
                paths, actual_size, content = mapper.get_content(trace_size)
                
                if content:
                    stats["object_sizes_matched"][obj_key] = {
                        "trace_size": trace_size,
                        "actual_size": actual_size,
                        "files": paths[:3]  # store first 3 paths
                    }
                    object_data[obj_key] = (actual_size, content)
                    
                    if not baseline and mgr:
                        # CDC dedup this content
                        manager_key = f"{bucket_id}_{obj_key}"
                        mgr.dedup_bytes(manager_key, content)
                else:
                    stats["miss_count"] += 1
                
            elif op == "GET":
                if obj_key in object_data:
                    stats["total_ops"] += 1
                    stats["get_count"] += 1
                    actual_size, content = object_data[obj_key]
                    
                    if not baseline and mgr:
                        manager_key = f"{bucket_id}_{obj_key}"
                        manifest = mgr.get_manifest(manager_key)
                        if manifest:
                            get_bytes = sum(
                                mgr.store.index.get(h, (None, 0, 0))[1]
                                for h in manifest
                            )
                        else:
                            get_bytes = actual_size
                    else:
                        get_bytes = actual_size
                    
                    stats["bytes_transferred"] += get_bytes
            
            # Progress
            if put_count % 50000 == 0 and put_count > 0:
                elapsed = time.time() - t0
                print(f"    [{put_count:,} PUTs] raw={stats['bytes_raw']/1e9:.2f}GB "
                      f"elapsed={elapsed:.0f}s")
    
    elapsed = time.time() - t0
    
    # Final storage cost
    if baseline:
        stats["bytes_stored"] = stats["bytes_raw"]
    else:
        ds = mgr.stats()
        stats["bytes_stored"] = ds["total_bytes_stored"]
        stats["cdc_chunks_total"] = ds["total_chunks_seen"]
        stats["cdc_chunks_unique"] = ds["unique_chunks"]
        stats["cdc_chunks_duplicate"] = ds["duplicate_chunks"]
        stats["cdc_dedup_ratio"] = ds["dedup_ratio"]
        mgr.save()
    
    if stats["bytes_raw"] > 0:
        stats["effective_ratio"] = stats["bytes_stored"] / stats["bytes_raw"]
        stats["savings_pct"] = (1 - stats["effective_ratio"]) * 100
    
    print(f"  Complete: {elapsed:.0f}s, PUTs={stats['put_count']:,}")
    print(f"    Raw: {stats['bytes_raw']/1e9:.2f} GB, "
          f"Stored: {stats['bytes_stored']/1e9:.2f} GB, "
          f"Savings: {stats.get('savings_pct', 0):.1f}%")
    
    return stats


def main():
    print("=" * 70)
    print("  SIZE-AWARE CDC DEDUP EXPERIMENT")
    print("  Pool: " + str(POOL_FILE))
    print("=" * 70)
    
    # Initialize mapper
    mapper = SizeAwareMapper(POOL_FILE)
    
    # Define all 5 buckets
    buckets = [
        ("T015", "IBMObjectStoreTrace015Part0.typeA.mc.aug", 300000),
        ("T029", "IBMObjectStoreTrace029Part0.typeA.mc.aug", 300000),
        ("T065", "IBMObjectStoreTrace065Part0.typeA.mc.aug", 3000),   # Only 3K PUTs total
        ("T078", "IBMObjectStoreTrace078Part0.typeA.mc.aug", 100000),
        ("T079", "IBMObjectStoreTrace079Part0.typeA.mc.aug", 12000),  # Only 12K PUTs total
    ]
    
    all_baselines = {}
    all_experiments = {}
    
    t_total = time.time()
    
    for bucket_id, trace_name, max_ops in buckets:
        tf = TRACE_DIR / trace_name
        if not tf.exists():
            print(f"\n  {bucket_id}: TRACE NOT FOUND ({tf}), skipping")
            continue
        
        print(f"\n{'='*70}")
        print(f"  PROCESSING {bucket_id} ({max_ops} PUT limit)")
        print(f"  Trace: {trace_name}")
        print(f"{'='*70}")
        
        # Baseline
        bl = run_experiment(bucket_id, str(tf), mapper, baseline=True, max_ops=max_ops)
        all_baselines[bucket_id] = bl
        
        # Experiment
        ex = run_experiment(bucket_id, str(tf), mapper, baseline=False, max_ops=max_ops)
        all_experiments[bucket_id] = ex
    
    # ===== COMPARATIVE REPORT =====
    print("\n" + "=" * 90)
    print("  COMPARATIVE RESULTS - ALL 5 BUCKETS")
    print("=" * 90)
    
    header = f"  {'Bucket':>6s} {'PUTs':>8s} {'Raw GB':>8s} {'Baseline GB':>10s} {'CDC GB':>10s} {'Savings':>8s} {'CDC Ratio':>10s} {'Chunks':>10s} {'Unique':>10s}"
    print(header)
    print("  " + "-" * 88)
    
    table_rows = []
    
    for bucket_id, trace_name, _ in buckets:
        if bucket_id not in all_baselines:
            continue
        
        bl = all_baselines[bucket_id]
        ex = all_experiments[bucket_id]
        
        b_raw = bl["bytes_raw"] / 1e9
        b_store = bl["bytes_stored"] / 1e9
        e_store = ex["bytes_stored"] / 1e9
        savings = ex.get("savings_pct", 0)
        cdc_ratio = ex.get("cdc_dedup_ratio", 1)
        chunks = ex.get("cdc_chunks_total", 0)
        unique = ex.get("cdc_chunks_unique", 0)
        
        row = f"  {bucket_id:>6s} {bl['put_count']:>8,d} {b_raw:>8.2f} {b_store:>10.2f} {e_store:>10.2f} {savings:>7.1f}% {cdc_ratio:>10.4f} {chunks:>10,d} {unique:>10,d}"
        print(row)
        
        table_rows.append({
            "bucket": bucket_id,
            "puts": bl["put_count"],
            "raw_gb": round(b_raw, 2),
            "baseline_gb": round(b_store, 2),
            "cdc_gb": round(e_store, 2),
            "savings_pct": round(savings, 1),
            "cdc_ratio": round(cdc_ratio, 4),
            "chunks": chunks,
            "unique": unique,
        })
    
    print("  " + "-" * 88)
    total_raw = sum(r["raw_gb"] for r in table_rows)
    total_bl = sum(r["baseline_gb"] for r in table_rows)
    total_cdc = sum(r["cdc_gb"] for r in table_rows)
    total_savings = (1 - total_cdc / max(total_bl, 1)) * 100
    print(f"  {'TOTAL':>6s} {'':>8s} {total_raw:>8.2f} {total_bl:>10.2f} {total_cdc:>10.2f} {total_savings:>7.1f}%")
    
    elapsed = time.time() - t_total
    print(f"\n  Total time: {elapsed/60:.1f} min")
    
    # Save results
    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pool": {
            "total_files": mapper.sizes.__len__(),
            "layers": json.loads(POOL_FILE.read_text()).get("layers", {}),
        },
        "per_bucket": table_rows,
    }
    
    result_file = OUTPUT_DIR / "size_aware_results.json"
    result_file.write_text(json.dumps(results, indent=2))
    print(f"\n  Results: {result_file}")
    print("=" * 90)


if __name__ == "__main__":
    main()
