
import csv
import sys
from pathlib import Path
from collections import defaultdict, Counter

TRACE_DIR = Path('/data/traces/typeA')

# Find all trace files
trace_files = sorted(TRACE_DIR.glob('IBMObjectStoreTrace*.typeA.mc.aug'))
print(f'Found {len(trace_files)} trace files:')

for tf in trace_files:
    print(f'  {tf.name} ({tf.stat().st_size/1e6:.1f} MB)')

print()
print('='*80)
print('ANALYZING SIZE DISTRIBUTIONS PER BUCKET')
print('='*80)

# For each trace file, analyze the size distribution of PUT operations
for tf in trace_files:
    bucket_id = tf.stem.replace('IBMObjectStoreTrace', '').replace('Part0.typeA.mc.aug', '').replace('Part0.typeA.mc', '')
    bucket_name = f'T{bucket_id}'
    
    print()
    print(f'--- {bucket_name} ({tf.name}) ---')
    
    sizes_put = []
    sizes_get = []
    sizes_all = []
    put_count = 0
    get_count = 0
    
    with open(tf, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                size = int(float(row.get('size', 0)))
            except (ValueError, TypeError):
                continue
            
            op = row.get('op', '')
            if op == 'PUT':
                sizes_put.append(size)
                put_count += 1
            elif op == 'GET':
                sizes_get.append(size)
                get_count += 1
            sizes_all.append(size)
    
    if not sizes_all:
        print('  No data')
        continue
    
    sizes_put.sort()
    
    def percentile(data, p):
        if not data:
            return 0
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data)-1)]
    
    print(f'  Total ops: {len(sizes_all):,} (PUT={put_count:,}, GET={get_count:,})')
    print(f'  Size range: {min(sizes_all):,} - {max(sizes_all):,} B')
    print(f'  Size range: {min(sizes_all)/1e3:.1f} KB - {max(sizes_all)/1e9:.2f} GB')
    
    # Percentiles for PUT operations
    print(f'  PUT Size Percentiles:')
    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        val = percentile(sizes_put, p)
        if val < 1024:
            print(f'    P{p:>2d}: {val:,} B')
        elif val < 1048576:
            print(f'    P{p:>2d}: {val/1024:.1f} KB')
        elif val < 1073741824:
            print(f'    P{p:>2d}: {val/1048576:.1f} MB')
        else:
            print(f'    P{p:>2d}: {val/1073741824:.2f} GB')
    
    if sizes_put:
        avg = sum(sizes_put)/len(sizes_put)
        print(f'  PUT Avg: {avg:,.0f} B ({avg/1048576:.1f} MB)')
    
    bins = [
        (0, 1024, '< 1 KB'),
        (1024, 10240, '1-10 KB'),
        (10240, 102400, '10-100 KB'),
        (102400, 1048576, '100KB-1MB'),
        (1048576, 10485760, '1-10 MB'),
        (10485760, 104857600, '10-100 MB'),
        (104857600, 1073741824, '100MB-1GB'),
        (1073741824, float('inf'), '> 1 GB'),
    ]
    
    if sizes_put:
        for lo, hi, label in bins:
            count = sum(1 for s in sizes_put if lo <= s < hi)
            pct = count / len(sizes_put) * 100
            if count > 0:
                print(f'  [{label:>10s}]: {count:>8,d} PUTs ({pct:>5.1f}%)')

print()
print('Done!')
