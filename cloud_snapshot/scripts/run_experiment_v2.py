#!/usr/bin/env python3
"""
run_experiment_v2.py —— SkyStore + CDC 去重消融实验

实验设计:
  基线 (dedup=off): IBM T15 迹直接映射到内核文件 → 计算原始存储/传输成本
  实验 (dedup=on):  IBM T15 迹先经 CDC 去重 → 只计唯一块成本

用法:
  source /data/venv/bin/activate && python run_experiment_v2.py
"""

import sys, csv, time, hashlib, json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '/data/scripts')
from cdc_dedup import DedupManager

# === CONFIGURATION ===
TRACE_FILE = "/data/traces/typeA/IBMObjectStoreTrace015Part0.typeA.mc.aug"
DATA_DIR = "/data/datasets/linux"
KERNEL_FILES = sorted(Path(DATA_DIR).glob("kernel_*.bin"))
OUTPUT_DIR = Path("/data/results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def obj_to_file_index(obj_key: str, n_files: int) -> int:
    """Deterministic mapping from object key to kernel file index."""
    h = int(hashlib.md5(obj_key.encode()).hexdigest(), 16)
    return h % n_files


def run_experiment(dedup_enabled: bool) -> dict:
    """Run the trace replay experiment."""
    
    mgr = DedupManager(chunk_store_dir="/data/chunks") if dedup_enabled else None
    kernel_data = {i: f.read_bytes() for i, f in enumerate(KERNEL_FILES)}
    file_sizes = {i: len(d) for i, d in kernel_data.items()}
    
    stats = {
        "dedup": dedup_enabled,
        "trace": Path(TRACE_FILE).name,
        "kernel_files": len(KERNEL_FILES),
        "put_count": 0,
        "get_count": 0,
        "lines": 0,
        "bytes_stored": 0,  # cost metric
        "bytes_transferred": 0,  # cost metric
    }
    
    # Track which objects were PUT (for GET reconstruction)
    object_to_file_idx = {}
    
    # Preload all kernel files into dedup manager
    if dedup_enabled:
        print("  Pre-deduplicating %d kernel files (for baseline storage)...")
        for i, fp in enumerate(KERNEL_FILES):
            t0 = time.time()
            mgr.dedup_file("kernel_%d" % i, str(fp))
            print("    %s: %d chunks (%.1fs)" % (fp.name, len(mgr.manifests.get("kernel_%d" % i, [])), time.time() - t0))
    
    print("  Replaying trace...")
    t0 = time.time()
    last_report = t0
    
    with open(TRACE_FILE, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["lines"] += 1
            
            op = row.get("op", "")
            obj_key = row.get("obj_key", "")
            
            if op == "PUT":
                stats["put_count"] += 1
                idx = obj_to_file_index(obj_key, len(KERNEL_FILES))
                object_to_file_idx[obj_key] = idx
                
                if not dedup_enabled:
                    stats["bytes_stored"] += file_sizes[idx]
                # For dedup=on, we compute total unique stored bytes at the end
            
            elif op == "GET":
                if obj_key in object_to_file_idx:
                    stats["get_count"] += 1
                    idx = object_to_file_idx[obj_key]
                    
                    if dedup_enabled:
                        # Reconstruct: get all chunks for this file
                        manifest = mgr.manifests.get("kernel_%d" % idx, [])
                        size = sum(
                            mgr.store.index.get(h, (None, 0, 0))[1]
                            for h in manifest
                        )
                    else:
                        size = file_sizes[idx]
                    
                    stats["bytes_transferred"] += size
            
            # Progress
            now = time.time()
            if now - last_report > 5:
                print("    [%s lines] PUT=%s GET=%s elapsed=%.0fs" % (
                    format(stats["lines"], ","),
                    format(stats["put_count"], ","),
                    format(stats["get_count"], ","),
                    now - t0,
                ))
                last_report = now
    
    elapsed = time.time() - t0
    stats["elapsed_s"] = elapsed
    
    # Compute dedup stats
    if dedup_enabled:
        dedup_stats = mgr.stats()
        stats["cdc_chunks_total"] = dedup_stats["total_chunks_seen"]
        stats["cdc_chunks_unique"] = dedup_stats["unique_chunks"]
        stats["cdc_chunks_duplicate"] = dedup_stats["duplicate_chunks"]
        stats["cdc_dedup_ratio"] = dedup_stats["dedup_ratio"]
        
        # The CORRECT dedup storage cost = total unique chunk bytes stored
        # (not per-object sum, because objects share chunks)
        stats["bytes_stored"] = dedup_stats["total_bytes_stored"]
        
        # Transfer cost stays the same (GET reconstructs full file)
        # but bytes_transferred already computed above
        mgr.save()
    
    return stats


def main():
    print("=" * 70)
    print("  SkyStore + Traditional CDC Dedup Ablation Experiment")
    print("=" * 70)
    print("  Trace:    %s" % Path(TRACE_FILE).name)
    print("  Dataset:  %d synthetic kernel files (90%% shared, ~4MB each)" % len(KERNEL_FILES))
    print("  Data dir: %s" % DATA_DIR)
    print("=" * 70)
    
    # === BASELINE: No Dedup ===
    print("\n[1/2] BASELINE: Traditional SkyStore (no dedup)")
    baseline = run_experiment(dedup_enabled=False)
    
    # === EXPERIMENT: With CDC Dedup ===
    print("\n[2/2] EXPERIMENT: SkyStore + CDC Dedup")
    experiment = run_experiment(dedup_enabled=True)
    
    # === COMPARISON ===
    print("\n" + "=" * 70)
    print("  EXPERIMENT RESULTS")
    print("=" * 70)
    
    print("  %-25s %15s %15s %10s" % ("Metric", "Baseline", "Dedup", "Savings"))
    print("  " + "-" * 67)
    
    storage_save = (1 - experiment["bytes_stored"] / max(baseline["bytes_stored"], 1)) * 100
    print("  %-25s %15s %15s %9.1f%%" % (
        "Storage cost (B)",
        format(baseline["bytes_stored"], ","),
        format(experiment["bytes_stored"], ","),
        storage_save,
    ))
    
    transfer_save = (1 - experiment["bytes_transferred"] / max(baseline["bytes_transferred"], 1)) * 100
    print("  %-25s %15s %15s %9.1f%%" % (
        "Transfer cost (B)",
        format(baseline["bytes_transferred"], ","),
        format(experiment["bytes_transferred"], ","),
        transfer_save,
    ))
    
    print("  %-25s %15s %15s" % (
        "PUT count",
        format(baseline["put_count"], ","),
        format(experiment["put_count"], ","),
    ))
    print("  %-25s %15s %15s" % (
        "GET count",
        format(baseline["get_count"], ","),
        format(experiment["get_count"], ","),
    ))
    print("  %-25s %15s %15s" % (
        "Trace lines",
        format(baseline["lines"], ","),
        format(experiment["lines"], ","),
    ))
    
    if "cdc_dedup_ratio" in experiment:
        print("  %-25s %15s %9.1f%%" % (
            "CDC chunk dedup ratio",
            "N/A",
            experiment["cdc_dedup_ratio"] * 100,
        ))
        print("  %-25s %15s %15s" % (
            "CDC chunks (total/uniq)",
            "N/A",
            "%s/%s" % (
                format(experiment["cdc_chunks_total"], ","),
                format(experiment["cdc_chunks_unique"], ","),
            ),
        ))
    
    # Save results
    results = {
        "baseline": baseline,
        "experiment": experiment,
        "storage_savings_pct": storage_save,
        "transfer_savings_pct": transfer_save,
    }
    
    result_path = OUTPUT_DIR / "experiment_results.json"
    result_path.write_text(json.dumps(results, indent=2, default=str))
    print("\n  Results saved: %s" % result_path)
    
    # Final verdict
    print("\n" + "=" * 70)
    print("  VERDICT")
    print("=" * 70)
    print("  Traditional CDC dedup BEFORE SkyStore reduces:")
    print("    Storage cost:  %.1f%%" % storage_save)
    print("    Transfer cost: %.1f%%" % transfer_save)
    print("=" * 70)


if __name__ == "__main__":
    main()
