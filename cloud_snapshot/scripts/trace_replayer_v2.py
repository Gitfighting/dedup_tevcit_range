#!/usr/bin/env python3
"""
trace_replayer_v2.py —— IBM 迹回放引擎 (CDC 去重版)

用法:
  source /data/venv/bin/activate && python trace_replayer_v2.py \
    --trace /data/traces/typeA/IBMObjectStoreTrace015Part0.typeA.mc.aug \
    --mapping /data/mappings/T15_typeA_mapping.csv \
    --data-dir /data/datasets \
    --dedup on \
    --speedup 1000

实验设计（消融对比）:
  - dedup=off: 原始文件大小直接计入 SkyStore 成本（基线）
  - dedup=on:  CDC 去重后的唯一块大小计入 SkyStore 成本（实验组）
  
  对比指标: 存储字节、传输字节、去重比、成本节省
"""

import csv
import time
import sys
import argparse
import json
from pathlib import Path
from collections import defaultdict
from cdc_dedup import DedupManager


def load_mapping(mapping_path: str) -> dict:
    """Load object_key -> file_path mapping."""
    mapping = {}
    with open(mapping_path, 'r') as f:
        for row in csv.DictReader(f):
            mapping[row['obj_key']] = row['file_path']
    return mapping


def replay_trace(
    trace_path: str,
    mapping: dict,
    data_dir: str,
    use_dedup: bool,
    speedup: float = 1000,
    max_lines: int = 0,
) -> dict:
    """
    Replay an IBM trace against the file pool.
    
    Returns statistics dict with:
      - put_count, get_count, miss_count
      - put_bytes_raw, put_bytes_dedup
      - get_bytes
      - unique_files, unique_chunks
      - dedup_ratio
      - total_lines
    """
    data_base = Path(data_dir)
    
    # Initialize dedup manager if needed
    dedup_mgr = DedupManager(chunk_store_dir="/data/chunks") if use_dedup else None
    
    stats = {
        "dedup_enabled": use_dedup,
        "trace_file": Path(trace_path).name,
        "put_count": 0,
        "get_count": 0,
        "miss_count": 0,
        "put_bytes_raw": 0,      # Raw file sizes (pre-dedup)
        "put_bytes_dedup": 0,    # Unique chunk bytes stored (post-dedup)
        "get_bytes": 0,
        "unique_files": set(),
        "total_lines": 0,
        "put_lines": 0,
        "get_lines": 0,
        "start_time": time.time(),
    }
    
    manifests = {}  # obj_key -> list of chunk hashes (for GET)
    object_sizes = {}  # obj_key -> raw file size
    
    t0 = time.time()
    trace_start_ts = None
    
    with open(trace_path, 'r') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            stats["total_lines"] += 1
            
            try:
                ts = int(float(row.get('timestamp', 0)))
            except (ValueError, TypeError):
                ts = 0
            
            if trace_start_ts is None:
                trace_start_ts = ts
            
            # Time alignment (simulate real-time replay)
            if speedup > 0 and ts > 0:
                elapsed_trace = (ts - trace_start_ts) / 1000.0
                elapsed_real = time.time() - t0
                sleep_time = elapsed_trace / speedup - elapsed_real
                if sleep_time > 0 and sleep_time < 10:
                    time.sleep(sleep_time)
            
            op = row.get('op', '')
            obj_key = row.get('obj_key', '')
            try:
                obj_size = int(float(row.get('size', 0)))
            except (ValueError, TypeError):
                obj_size = 0
            
            if op == 'PUT':
                stats["put_lines"] += 1
                fp = mapping.get(obj_key)
                
                if fp:
                    full_path = data_base / fp
                    
                    if full_path.exists():
                        stats["put_count"] += 1
                        file_size = full_path.stat().st_size
                        stats["put_bytes_raw"] += file_size
                        stats["unique_files"].add(fp)
                        object_sizes[obj_key] = file_size
                        
                        if dedup_mgr:
                            # CDC + Dedup: only count unique chunks
                            try:
                                manifest = dedup_mgr.dedup_file(obj_key, str(full_path))
                                manifests[obj_key] = manifest
                                # Dedup cost = sum of unique chunk sizes stored
                                # (only count chunks that were NEW when this obj was added)
                                dedup_bytes = dedup_mgr.get_unique_stored_bytes(obj_key)
                                stats["put_bytes_dedup"] += dedup_bytes
                            except Exception as e:
                                # Fall back to raw size on error
                                stats["put_bytes_dedup"] += file_size
                        else:
                            stats["put_bytes_dedup"] += file_size
                    else:
                        stats["miss_count"] += 1
                else:
                    stats["miss_count"] += 1
            
            elif op == 'GET':
                stats["get_lines"] += 1
                
                if use_dedup and obj_key in manifests:
                    # Reconstruct from chunks: transfer cost = sum of all chunk sizes
                    manifest = manifests[obj_key]
                    get_size = 0
                    for chunk_hash in manifest:
                        chunk = dedup_mgr.store.get(chunk_hash)
                        if chunk:
                            get_size += len(chunk)
                    stats["get_bytes"] += get_size
                    stats["get_count"] += 1
                elif obj_key in object_sizes:
                    stats["get_bytes"] += object_sizes[obj_key]
                    stats["get_count"] += 1
                else:
                    stats["miss_count"] += 1
            
            # Progress report
            if stats["total_lines"] % 500000 == 0:
                elapsed = time.time() - t0
                print(f"  [{stats['total_lines']:,}] "
                      f"PUT={stats['put_count']:,} "
                      f"GET={stats['get_count']:,} "
                      f"elapsed={elapsed:.0f}s")
            
            if max_lines > 0 and stats["total_lines"] >= max_lines:
                print(f"  Reached max_lines={max_lines}, stopping")
                break
    
    elapsed = time.time() - t0
    stats["elapsed_seconds"] = elapsed
    
    # Calculate derived metrics
    stats["unique_file_count"] = len(stats["unique_files"])
    
    if stats["put_bytes_raw"] > 0:
        stats["dedup_ratio"] = stats["put_bytes_dedup"] / stats["put_bytes_raw"]
    else:
        stats["dedup_ratio"] = 1.0
    
    stats["dedup_savings_bytes"] = stats["put_bytes_raw"] - stats["put_bytes_dedup"]
    
    if dedup_mgr:
        dedup_stats = dedup_mgr.stats()
        stats["cdc_chunks_total"] = dedup_stats.get("total_chunks_seen", 0)
        stats["cdc_chunks_unique"] = dedup_stats.get("unique_chunks", 0)
        stats["cdc_chunks_duplicate"] = dedup_stats.get("duplicate_chunks", 0)
        dedup_mgr.save()
    
    # Convert sets to counts for JSON
    stats["unique_files"] = len(stats["unique_files"])
    
    return stats


def print_report(stats: dict):
    """Print a formatted report of replay statistics."""
    print("\n" + "=" * 70)
    print(f"  IBM TRACE REPLAY REPORT  [{stats['trace_file']}]")
    print("=" * 70)
    print(f"  Dedup:        {'ON (CDC + SHA-256)' if stats['dedup_enabled'] else 'OFF (raw)'}")
    print(f"  Lines:        {stats['total_lines']:,}")
    print(f"  PUT ops:      {stats['put_count']:,}  ({stats['put_lines']:,} lines)")
    print(f"  GET ops:      {stats['get_count']:,}  ({stats['get_lines']:,} lines)")
    print(f"  Misses:       {stats['miss_count']:,}")
    print(f"  Unique files: {stats['unique_file_count']:,}")
    print()
    print(f"  --- Storage / PUT ---")
    print(f"  Raw bytes:    {stats['put_bytes_raw']:>20,}  ({stats['put_bytes_raw']/1e9:.2f} GB)")
    print(f"  Dedup bytes:  {stats['put_bytes_dedup']:>20,}  ({stats['put_bytes_dedup']/1e9:.2f} GB)")
    print(f"  Savings:      {stats['dedup_savings_bytes']:>20,}  ({stats['dedup_savings_bytes']/1e9:.2f} GB)")
    print(f"  Dedup ratio:  {stats['dedup_ratio']:>20.2%}")
    
    if 'cdc_chunks_total' in stats:
        print()
        print(f"  --- CDC Chunks ---")
        print(f"  Total chunks: {stats['cdc_chunks_total']:>15,}")
        print(f"  Unique:       {stats['cdc_chunks_unique']:>15,}")
        print(f"  Duplicates:   {stats['cdc_chunks_duplicate']:>15,}")
    
    print()
    print(f"  --- Transfer / GET ---")
    print(f"  GET bytes:    {stats['get_bytes']:>20,}  ({stats['get_bytes']/1e9:.2f} GB)")
    print()
    print(f"  Elapsed:      {stats['elapsed_seconds']:.1f}s")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description='IBM Trace Replayer with CDC Dedup')
    parser.add_argument('--trace', required=True, help='Trace file (*.mc.aug)')
    parser.add_argument('--mapping', required=True, help='Object-to-file mapping CSV')
    parser.add_argument('--data-dir', default='/data/datasets', help='Dataset root directory')
    parser.add_argument('--dedup', choices=['on', 'off'], default='on',
                        help='Enable CDC dedup (on=experiment, off=baseline)')
    parser.add_argument('--speedup', type=float, default=1000,
                        help='Speedup factor (1000=no sleep, 0=realtime)')
    parser.add_argument('--max-lines', type=int, default=0,
                        help='Max trace lines to replay (0=all)')
    parser.add_argument('--output', default='', help='Output JSON path for stats')
    
    args = parser.parse_args()
    
    use_dedup = (args.dedup == 'on')
    
    print("=" * 70)
    print("  SkyStore CDC Dedup Experiment - Trace Replayer v2")
    print(f"  Trace:   {args.trace}")
    print(f"  Mapping: {args.mapping}")
    print(f"  Data:    {args.data_dir}")
    print(f"  Dedup:   {'ON' if use_dedup else 'OFF (baseline)'}")
    print(f"  Speedup: {args.speedup}x")
    if args.max_lines > 0:
        print(f"  Max:     {args.max_lines:,} lines")
    print("=" * 70)
    
    # Load mapping
    print(f"\n[1/2] Loading mapping...")
    mapping = load_mapping(args.mapping)
    print(f"  Loaded {len(mapping):,} object mappings")
    
    # Replay
    print(f"\n[2/2] Replaying trace...")
    stats = replay_trace(
        trace_path=args.trace,
        mapping=mapping,
        data_dir=args.data_dir,
        use_dedup=use_dedup,
        speedup=args.speedup,
        max_lines=args.max_lines,
    )
    
    # Report
    print_report(stats)
    
    # Save stats
    if not args.output:
        trace_name = Path(args.trace).stem
        mode = "dedup_on" if use_dedup else "dedup_off"
        args.output = f"/data/results/{trace_name}_{mode}_stats.json"
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(stats, indent=2, default=str))
    print(f"\nStats saved: {output_path}")


if __name__ == "__main__":
    main()
