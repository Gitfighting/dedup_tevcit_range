#!/usr/bin/env python3
"""
analyze_all_traces.py —— 分析所有 5 个 IBM trace bucket 的大小分布
上传到 /data/scripts/ 后执行
"""
import csv
from pathlib import Path

TRACE_DIR = Path("/data/traces/typeA")

trace_files = sorted(TRACE_DIR.glob("IBMObjectStoreTrace*.typeA.mc.aug"))

for tf in trace_files:
    bucket_id = tf.stem.replace("IBMObjectStoreTrace", "").replace("Part0.typeA.mc.aug", "")
    bucket_name = f"T{bucket_id}"
    
    sizes_put = []
    sizes_get = []
    put_count = 0
    get_count = 0
    obj_sizes = {}  # obj_key -> size (last PUT size)
    
    with open(tf, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                size = int(float(row.get('size', 0)))
            except (ValueError, TypeError):
                continue
            
            op = row.get('op', '')
            obj_key = row.get('obj_key', '')
            
            if op == 'PUT':
                sizes_put.append(size)
                put_count += 1
                obj_sizes[obj_key] = size
            elif op == 'GET':
                sizes_get.append(size)
                get_count += 1
    
    if not sizes_put:
        continue
    
    sizes_put.sort()
    
    def pct(data, p):
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data)-1)]
    
    print(f"\n{'='*70}")
    print(f"  {bucket_name}  |  {tf.name}")
    print(f"  Total: {put_count+get_count:,} ops (PUT={put_count:,}, GET={get_count:,})")
    print(f"  Unique objects: {len(obj_sizes):,}")
    print(f"  Size range: {min(sizes_put):,} B - {max(sizes_put):,} B")
    print(f"            : {min(sizes_put)/1e3:.1f} KB - {max(sizes_put)/1e6:.1f} MB")
    print(f"  Avg PUT size: {sum(sizes_put)/len(sizes_put):,.0f} B ({sum(sizes_put)/len(sizes_put)/1024:.0f} KB)")
    
    print(f"\n  PUT Size Percentiles:")
    for p, label in [(1,'P01'),(5,'P05'),(10,'P10'),(25,'P25'),(50,'P50'),(75,'P75'),(90,'P90'),(95,'P95'),(99,'P99')]:
        val = pct(sizes_put, p)
        if val < 1024:
            print(f"    {label}: {val:>8,} B")
        elif val < 1048576:
            print(f"    {label}: {val/1024:>7.1f} KB")
        elif val < 1073741824:
            print(f"    {label}: {val/1048576:>7.1f} MB")
        else:
            print(f"    {label}: {val/1073741824:>7.2f} GB")
    
    print(f"\n  PUT Size Bins:")
    bins = [
        (0, 1024, "< 1 KB"),
        (1024, 10240, "1-10 KB"),
        (10240, 102400, "10-100 KB"),
        (102400, 1048576, "100KB-1MB"),
        (1048576, 10485760, "1-10 MB"),
        (10485760, 104857600, "10-100 MB"),
        (104857600, 1073741824, "100MB-1GB"),
        (1073741824, 10**18, "> 1 GB"),
    ]
    for lo, hi, label in bins:
        count = sum(1 for s in sizes_put if lo <= s < hi)
        pct_val = count / len(sizes_put) * 100
        if count > 0:
            bar = '█' * int(pct_val / 2)
            print(f"    {label:>12s}: {count:>8,d} ({pct_val:>5.1f}%) {bar}")

    # Unique object size distribution
    obj_sizes_list = list(obj_sizes.values())
    obj_sizes_list.sort()
    print(f"\n  Unique Object Size Percentiles:")
    for p, label in [(50,'P50'),(75,'P75'),(90,'P90'),(95,'P95'),(99,'P99')]:
        val = pct(obj_sizes_list, p)
        if val < 1048576:
            print(f"    {label}: {val/1024:.0f} KB")
        else:
            print(f"    {label}: {val/1048576:.1f} MB")

print("\n\n=== FILE POOL ANALYSIS ===")
import json
pool = json.loads(Path('/data/filepool/file_list.json').read_text())
pool_sizes = []
for dataset, files in pool.items():
    for fp in files:
        try:
            pool_sizes.append(Path(fp).stat().st_size)
        except:
            pass
pool_sizes.sort()

print(f"Total files: {len(pool_sizes):,}")
print(f"Size range: {min(pool_sizes):,} B - {max(pool_sizes):,} B ({max(pool_sizes)/1e6:.1f} MB)")
print(f"Mean: {sum(pool_sizes)/len(pool_sizes):,.0f} B ({sum(pool_sizes)/len(pool_sizes)/1024:.0f} KB)")

print(f"\nPool Size Distribution:")
bins = [
    (0, 1000, "< 1 KB"),
    (1000, 10000, "1-10 KB"),
    (10000, 100000, "10-100 KB"),
    (100000, 1000000, "100KB-1MB"),
    (1000000, 10000000, "1-10 MB"),
    (10000000, 100000000, "10-100 MB"),
    (100000000, 10**18, "> 100 MB"),
]
for lo, hi, label in bins:
    count = sum(1 for s in pool_sizes if lo <= s < hi)
    pct_val = count / len(pool_sizes) * 100
    if count > 0:
        bar = '█' * int(pct_val / 2)
        print(f"  {label:>12s}: {count:>8,d} ({pct_val:>5.1f}%) {bar}")

for p, label in [(1,'P01'),(5,'P05'),(10,'P10'),(25,'P25'),(50,'P50'),(75,'P75'),(90,'P90'),(95,'P95'),(99,'P99')]:
    val = pct(pool_sizes, p)
    print(f"  {label}: {val:,} B ({val/1024:.0f} KB)")

print("\nDone!")
