#!/usr/bin/env python3
"""
run_real_experiment.py —— 使用真实文件池做 CDC 去重消融实验

实验设计:
  基线 (dedup=off): IBM T15 迹 → 文件池 → 原始字节计入存储/传输成本
  实验 (dedup=on):  IBM T15 迹 → 文件池 → CDC 去重后的唯一块计入存储成本

用法:
  source /data/venv/bin/activate && python /data/scripts/run_real_experiment.py

预计运行时间: ~10-20分钟 (取决于文件池大小和CDC块大小)
"""

import sys
import csv
import time
import json
import hashlib
import random
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '/data/scripts')
from cdc_dedup import DedupManager

# === CONFIG ===
TRACE_FILE = "/data/traces/typeA/IBMObjectStoreTrace015Part0.typeA.mc.aug"
POOL_LIST = "/data/filepool/file_list.json"
OUTPUT_DIR = Path("/data/results")
CHUNK_DIR = "/data/chunks_real"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_file_pool(pool_json: str) -> dict:
    """Load file pool mapping from JSON. Returns {dataset: [file_path, ...]}"""
    with open(pool_json, 'r') as f:
        return json.load(f)


def build_flat_file_list(pool: dict, min_size: int = 100, max_size: int = 50_000_000) -> list:
    """Build a flat list of files from the pool, filtering by size range."""
    files = []
    for dataset, paths in pool.items():
        for p in paths:
            try:
                sz = Path(p).stat().st_size
                if min_size <= sz <= max_size:
                    files.append({"path": p, "size": sz, "dataset": dataset})
            except OSError:
                continue
    return files


def obj_to_file(obj_key: str, file_list: list) -> dict:
    """Deterministic mapping from object key to a file in the pool."""
    h = int(hashlib.md5(obj_key.encode()).hexdigest(), 16)
    idx = h % len(file_list)
    return file_list[idx]


def run_experiment(baseline: bool = True) -> dict:
    """
    Run the trace replay experiment.
    
    baseline=True:  no dedup, count raw file sizes
    baseline=False: CDC dedup, count unique chunk bytes
    """
    label = "BASELINE (no dedup)" if baseline else "EXPERIMENT (CDC dedup)"
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    
    # Clear chunk store for fresh run
    chunk_dir = Path(CHUNK_DIR)
    if chunk_dir.exists():
        import shutil
        shutil.rmtree(str(chunk_dir))
    chunk_dir.mkdir(parents=True, exist_ok=True)
    
    # Load file pool
    print("Loading file pool...")
    pool = load_file_pool(POOL_LIST)
    file_list = build_flat_file_list(pool)
    print(f"  Pool: {len(file_list):,} files from {len(pool)} datasets")
    
    # Overall size stats
    pool_total = sum(f["size"] for f in file_list)
    print(f"  Pool total size: {pool_total/1e9:.2f} GB")
    print(f"  Avg file size: {pool_total/len(file_list):.0f} bytes")
    
    # Initialize dedup manager (even for baseline, for compatibility)
    mgr = DedupManager(chunk_store_dir=str(chunk_dir)) if not baseline else None
    
    stats = {
        "baseline": baseline,
        "trace": Path(TRACE_FILE).name,
        "pool_files": len(file_list),
        "pool_size_bytes": pool_total,
        "put_count": 0,
        "get_count": 0,
        "miss_count": 0,
        "total_ops": 0,
        "bytes_raw": 0,        # Raw file bytes (pre-dedup)
        "bytes_stored": 0,     # Storage cost (post-dedup unique chunks)
        "bytes_transferred": 0,# GET transfer bytes
        "unique_pool_files": set(),
        "start_time": time.time(),
    }
    
    # Track object -> file mapping (needed for GET)
    object_mapping = {}
    
    print("\nReplaying trace...")
    t0 = time.time()
    last_report = t0
    processed = 0
    
    with open(TRACE_FILE, 'r') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            stats["total_ops"] += 1
            processed += 1
            
            op = row.get("op", "")
            obj_key = row.get("obj_key", "")
            
            if op == "PUT":
                stats["put_count"] += 1
                
                # Map to a file in the pool
                file_info = obj_to_file(obj_key, file_list)
                file_path = file_info["path"]
                file_size = file_info["size"]
                
                stats["bytes_raw"] += file_size
                stats["unique_pool_files"].add(file_path)
                object_mapping[obj_key] = file_info
                
                if not baseline and mgr:
                    # CDC dedup: process file, count unique chunks
                    try:
                        manifest = mgr.dedup_file(obj_key, file_path)
                        # Individual PUT cost not counted here — use global unique bytes
                    except Exception as e:
                        stats["miss_count"] += 1
                        continue
                
            elif op == "GET":
                if obj_key in object_mapping:
                    stats["get_count"] += 1
                    file_info = object_mapping[obj_key]
                    file_size = file_info["size"]
                    
                    if not baseline and mgr:
                        # Reconstruct from chunks
                        manifest = mgr.get_manifest(obj_key)
                        if manifest:
                            get_bytes = sum(
                                mgr.store.index.get(h, (None, 0, 0))[1]
                                for h in manifest
                            )
                        else:
                            get_bytes = file_size
                    else:
                        get_bytes = file_size
                    
                    stats["bytes_transferred"] += get_bytes
                else:
                    stats["miss_count"] += 1
            
            # Progress every 100K ops
            if processed % 100000 == 0:
                elapsed = time.time() - t0
                print(f"  [{processed:,} ops] PUT={stats['put_count']:,} "
                      f"GET={stats['get_count']:,} elapsed={elapsed:.0f}s")
            
            # Limit: process 500K ops for faster iteration
            if processed >= 500000:
                print(f"  Stopping at {processed:,} ops (limit)")
                break
    
    elapsed = time.time() - t0
    stats["elapsed_s"] = elapsed
    
    # Compute final storage cost
    if baseline:
        # No dedup: storage cost = raw bytes of all unique files accessed
        # (only count each unique file once for storage)
        unique_total = 0
        for fp in stats["unique_pool_files"]:
            unique_total += Path(fp).stat().st_size
        stats["bytes_stored"] = unique_total
    else:
        # With dedup: storage cost = unique chunk bytes across ALL files
        ds = mgr.stats()
        stats["bytes_stored"] = ds["total_bytes_stored"]
        stats["cdc_chunks_total"] = ds["total_chunks_seen"]
        stats["cdc_chunks_unique"] = ds["unique_chunks"]
        stats["cdc_chunks_duplicate"] = ds["duplicate_chunks"]
        stats["cdc_dedup_ratio"] = ds["dedup_ratio"]
        mgr.save()
    
    # Derived metrics
    if stats["bytes_raw"] > 0:
        stats["effective_dedup_ratio"] = stats["bytes_stored"] / stats["bytes_raw"]
        stats["storage_savings_pct"] = (1 - stats["effective_dedup_ratio"]) * 100
    
    stats["unique_file_count"] = len(stats["unique_pool_files"])
    
    print(f"\n  {label} complete:")
    print(f"    Ops: {stats['total_ops']:,} (PUT={stats['put_count']:,}, GET={stats['get_count']:,})")
    print(f"    Raw bytes: {stats['bytes_raw']/1e9:.2f} GB")
    print(f"    Stored bytes: {stats['bytes_stored']/1e9:.2f} GB")
    print(f"    Transferred: {stats['bytes_transferred']/1e9:.2f} GB")
    print(f"    Unique files: {stats['unique_file_count']}")
    if "effective_dedup_ratio" in stats:
        print(f"    Dedup ratio: {stats['effective_dedup_ratio']:.4f}")
        print(f"    Storage savings: {stats['storage_savings_pct']:.1f}%")
    print(f"    Elapsed: {elapsed:.1f}s")
    
    return stats


def main():
    print("=" * 70)
    print("  REAL FILE POOL CDC DEDUP EXPERIMENT")
    print("  Trace: " + TRACE_FILE)
    print("  Pool:  " + POOL_LIST)
    print("=" * 70)
    
    # Run baseline
    t0 = time.time()
    baseline = run_experiment(baseline=True)
    baseline_time = time.time() - t0
    
    # Run experiment
    t0 = time.time()
    experiment = run_experiment(baseline=False)
    experiment_time = time.time() - t0
    
    # ===== Comparative Report =====
    print("\n" + "=" * 70)
    print("  COMPARATIVE RESULTS")
    print("=" * 70)
    
    b_raw = baseline["bytes_raw"] / 1e9
    b_stored = baseline["bytes_stored"] / 1e9
    b_transfer = baseline["bytes_transferred"] / 1e9
    
    e_raw = experiment["bytes_raw"] / 1e9
    e_stored = experiment["bytes_stored"] / 1e9
    e_transfer = experiment["bytes_transferred"] / 1e9
    
    storage_savings = (1 - e_stored / b_stored) * 100 if b_stored > 0 else 0
    
    print(f"  {'':>25s} {'BASELINE':>15s} {'EXPERIMENT':>15s} {'CHANGE':>15s}")
    print(f"  {'-'*70}")
    print(f"  {'Operations':>25s} {baseline['total_ops']:>15,d} {experiment['total_ops']:>15,d} {'':>15s}")
    print(f"  {'PUT ops':>25s} {baseline['put_count']:>15,d} {experiment['put_count']:>15,d} {'':>15s}")
    print(f"  {'GET ops':>25s} {baseline['get_count']:>15,d} {experiment['get_count']:>15,d} {'':>15s}")
    print(f"  {'Raw bytes (GB)':>25s} {b_raw:>15.2f} {e_raw:>15.2f} {'':>15s}")
    print(f"  {'Stored bytes (GB)':>25s} {b_stored:>15.2f} {e_stored:>15.2f} {'':>15s}")
    print(f"  {'Transferred bytes (GB)':>25s} {b_transfer:>15.2f} {e_transfer:>15.2f} {'':>15s}")
    print(f"  {'Storage savings':>25s} {'':>15s} {storage_savings:>14.1f}% {'':>15s}")
    
    if "cdc_dedup_ratio" in experiment:
        print(f"  {'CDC chunk dedup ratio':>25s} {'':>15s} {experiment['cdc_dedup_ratio']:>15.4f} {'':>15s}")
        print(f"  {'Total chunks seen':>25s} {'':>15s} {experiment.get('cdc_chunks_total',0):>15,d} {'':>15s}")
        print(f"  {'Unique chunks':>25s} {'':>15s} {experiment.get('cdc_chunks_unique',0):>15,d} {'':>15s}")
        print(f"  {'Duplicate chunks':>25s} {'':>15s} {experiment.get('cdc_chunks_duplicate',0):>15,d} {'':>15s}")
    
    print(f"  {'Unique pool files':>25s} {baseline['unique_file_count']:>15,d} {experiment['unique_file_count']:>15,d} {'':>15s}")
    print(f"  {'Runtime (s)':>25s} {baseline_time:>15.1f} {experiment_time:>15.1f} {'':>15s}")
    print(f"  {'-'*70}")
    
    # Save results
    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trace": str(Path(TRACE_FILE).name),
        "pool_stats": {
            "total_files": baseline["pool_files"],
            "total_size_gb": baseline["pool_size_bytes"] / 1e9,
        },
        "baseline": baseline,
        "experiment": experiment,
        "comparison": {
            "storage_savings_pct": round(storage_savings, 1),
            "storage_ratio": round(e_stored / b_stored, 4) if b_stored > 0 else 1,
        }
    }
    
    result_file = OUTPUT_DIR / "real_experiment_results.json"
    result_file.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults saved to {result_file}")
    
    # Cleanup old result files
    old_results = OUTPUT_DIR / "experiment_results.json"
    if old_results.exists():
        old_results.unlink()
        print(f"Cleaned up old: {old_results}")
    
    print("\n" + "=" * 70)
    print("  EXPERIMENT COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
