"""Quick statistics script for cloud server analysis."""
import numpy as np
import os
import json
from collections import defaultdict

print("=" * 60)
print("1. FILE POOL STATISTICS")
print("=" * 60)

# Pool sizes from numpy array
s = np.load('/data/pool_sizes.npy')
print(f"  Pool entries: {len(s):,}")
print(f"  Min: {s.min():,.0f} B ({s.min()/1e6:.1f} MB)")
print(f"  Max: {s.max():,.0f} B ({s.max()/1e9:.2f} GB)")
print(f"  Mean: {s.mean():,.0f} B ({s.mean()/1e6:.1f} MB)")
print(f"  Median: {np.median(s):,.0f} B ({np.median(s)/1e6:.1f} MB)")
print(f"  Total: {s.sum()/1e9:.2f} GB")
print()

# Size distribution
bins = [0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e12]
labels = ['<1KB', '1-10KB', '10-100KB', '100KB-1MB', '1-10MB', '10-100MB', '100MB-1GB', '>1GB']
print("  Size distribution:")
for i in range(len(bins)-1):
    cnt = ((s >= bins[i]) & (s < bins[i+1])).sum()
    if cnt > 0:
        print(f"    {labels[i]:15s}: {cnt:>10,}  ({cnt/len(s)*100:.1f}%)")

# Category sizes (quick listing)
print()
print("  Category breakdown (file_pool subdirs):")
pool_root = "/data/file_pool"
try:
    categories = os.listdir(pool_root)
    for cat in sorted(categories):
        cat_path = os.path.join(pool_root, cat)
        if os.path.isdir(cat_path):
            # Count files quickly with os.walk
            file_count = 0
            total_size = 0
            for root, dirs, files in os.walk(cat_path):
                file_count += len(files)
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_size += os.path.getsize(fp)
                    except:
                        pass
            if file_count > 0:
                size_gb = total_size / 1e9
                print(f"    {cat:40s}: {file_count:>8,} files  {size_gb:>8.2f} GB")
except Exception as e:
    print(f"    (error scanning: {e})")

print()
print("=" * 60)
print("2. TRACE STATISTICS (GET vs PUT)")
print("=" * 60)

import csv
traces_dir = "/data/skystore/traces"
for fname in sorted(os.listdir(traces_dir)):
    fpath = os.path.join(traces_dir, fname)
    if not fname.endswith('.csv'):
        continue
    gets = 0
    puts = 0
    total_size = 0
    unique_objects = set()
    unique_regions = set()
    with open(fpath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            op = row.get('op', '')
            if op == 'GET':
                gets += 1
            elif op == 'PUT':
                puts += 1
            total_size += int(row.get('size', 0))
            unique_objects.add(row.get('obj_key', ''))
            unique_regions.add(row.get('issue_region', ''))
    total_ops = gets + puts
    total_gb = total_size / 1e9
    get_ratio = gets / total_ops * 100 if total_ops > 0 else 0
    print(f"  {fname}")
    print(f"    Total ops: {total_ops:,}  (GET: {gets:,} [{get_ratio:.0f}%]  PUT: {puts:,})")
    print(f"    Unique objects: {len(unique_objects):,}")
    print(f"    Total data volume: {total_gb:.1f} GB")
    print(f"    Average obj size: {total_size/max(len(unique_objects),1)/1e3:.1f} KB")
    print(f"    Regions: {unique_regions}")

print()
print("=" * 60)
print("3. DEDUP INDEX STATUS")
print("=" * 60)

import sys
sys.path.insert(0, '/data/skystore/prototype')
from dedup.dedup_index import DedupIndex
try:
    idx = DedupIndex("/data/dedup/index")
    stats = idx.stats()
    print(f"  Index inserts: {stats['inserts']:,}")
    print(f"  Index lookups: {stats['lookups']:,}")
    print(f"  Bloom elements: ~{stats['bloom_approx_elements']:,}")
    print(f"  Bloom size: {idx.bloom.m / 8 / 1e6:.1f} MB")
    print(f"  Bloom hash functions: {idx.bloom.k}")
except Exception as e:
    print(f"  Index not initialized: {e}")

print()
print("=" * 60)
print("4. CHUNK STORE STATUS")
print("=" * 60)
try:
    from dedup.dedup_store import ChunkStore
    store = ChunkStore("/data/chunks")
    print(f"  Chunks stored: {store.chunk_count():,}")
    print(f"  Total chunk bytes: {store.total_size()/1e6:.1f} MB")
except Exception as e:
    print(f"  Store not initialized: {e}")

print()
print("=" * 60)
print("5. CURRENT PROCESSES (from ps)")
print("=" * 60)
import subprocess
result = subprocess.run(["ps", "aux", "--sort=-%cpu"], capture_output=True, text=True)
lines = result.stdout.strip().split('\n')
print(f"  Total processes: {len(lines)-1}")
print("  Top 10 by CPU:")
for line in lines[1:11]:
    parts = line.split(None, 10)
    if len(parts) >= 11:
        print(f"    {parts[2]:>5s}%  {parts[10][:80]}")

print()
print("Done. Ready for decision.")
