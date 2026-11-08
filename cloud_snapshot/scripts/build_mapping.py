#!/usr/bin/env python3
"""
build_mapping.py —— 为每条 IBM 迹生成 object_key → file_path 映射表

用法:
  source /data/venv/bin/activate && python build_mapping.py

输出: /data/mappings/{T15|T29|T65|T78|T79}_{typeA}.csv
"""

import csv, bisect, sys
from pathlib import Path

POOL_PATH = Path("/data/file_pool.csv")
TRACE_DIR = Path("/data/traces")
MAPPING_DIR = Path("/data/mappings")


def load_file_pool():
    pool = []
    with open(POOL_PATH, 'r') as f:
        for row in csv.DictReader(f):
            pool.append((int(row['size_bytes']), row['file_path']))
    pool.sort(key=lambda x: x[0])
    return pool


def find_closest(pool, sizes_list, target, used):
    idx = bisect.bisect_left(sizes_list, target)
    candidates = []
    left, right = idx - 1, idx
    while left >= 0 and len(candidates) < 20:
        if left not in used:
            candidates.append((abs(sizes_list[left] - target), left))
        left -= 1
    while right < len(sizes_list) and len(candidates) < 40:
        if right not in used:
            candidates.append((abs(sizes_list[right] - target), right))
        right += 1
    if not candidates:
        return None, 0
    candidates.sort()
    best_idx = candidates[0][1]
    used.add(best_idx)
    return pool[best_idx], abs(pool[best_idx][0] - target)


def build_mapping(trace_path, pool, pool_sizes, trace_name):
    used = set()
    mapping = {}

    # Collect unique PUT objects
    objects = []
    seen = set()
    with open(trace_path, 'r') as f:
        for row in csv.DictReader(f):
            key = row.get('obj_key', '')
            op = row.get('op', '')
            if op == 'PUT' and key not in seen:
                seen.add(key)
                try:
                    size = int(row.get('size', 0))
                except (ValueError, TypeError):
                    size = 0
                objects.append((key, size))

    # Sort descending so large objects get matched first
    objects.sort(key=lambda x: x[1], reverse=True)

    for obj_key, trace_size in objects:
        match, delta = find_closest(pool, pool_sizes, trace_size, used)
        if match is None:
            used.clear()
            match, delta = find_closest(pool, pool_sizes, trace_size, used)
        if match is None:
            continue
        mapping[obj_key] = (match[1], trace_size, match[0], delta)

    # Write CSV
    output_path = MAPPING_DIR / f"{trace_name}_mapping.csv"
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["obj_key", "file_path", "trace_size", "pool_size", "delta_bytes"])
        for key, (fp, ts, ps, delta) in mapping.items():
            writer.writerow([key, fp, ts, ps, delta])

    deltas = [v[3] for v in mapping.values()]
    avg = sum(deltas) / len(deltas) if deltas else 0
    max_d = max(deltas) if deltas else 0
    print(f"  {trace_name}: {len(mapping)} objects, avg_delta={avg:,.0f} B, max_delta={max_d:,.0f} B")
    return output_path


def main():
    print("=" * 60)
    print("Building Object-to-File Mappings")
    print("=" * 60)

    if not POOL_PATH.exists():
        print(f"ERROR: {POOL_PATH} not found. Run build_file_pool.py first.")
        sys.exit(1)

    pool = load_file_pool()
    pool_sizes = [p[0] for p in pool]
    MAPPING_DIR.mkdir(parents=True, exist_ok=True)

    trace_files = list(TRACE_DIR.glob("*/*.mc.aug")) or list(TRACE_DIR.glob("*.mc.aug"))
    if not trace_files:
        print(f"ERROR: No trace files found in {TRACE_DIR}")
        sys.exit(1)

    print(f"\nFound {len(trace_files)} trace files")
    for tf in sorted(trace_files):
        # IBMObjectStoreTrace015Part0.typeA.mc.aug -> T15_typeA
        fname = tf.name.replace('IBMObjectStoreTrace','').replace('Part0.','_').replace('.mc.aug','')
        try:
            parts = fname.split('_')
            tnum = str(int(parts[0].replace('0','',1) if parts[0].startswith('0') else parts[0]))
            trace_name = f"T{tnum}_{parts[1]}"
        except:
            trace_name = fname.replace('.mc.aug','')
        build_mapping(tf, pool, pool_sizes, trace_name)

    print(f"\n=== Done. Mappings saved to {MAPPING_DIR} ===")


if __name__ == "__main__":
    main()
