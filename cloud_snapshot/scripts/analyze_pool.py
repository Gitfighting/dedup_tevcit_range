import json
from pathlib import Path
from collections import Counter

pool = json.loads(Path('/data/filepool/file_list.json').read_text())

sizes = []
for dataset, files in pool.items():
    for fp in files:
        try:
            sizes.append(Path(fp).stat().st_size)
        except:
            pass

sizes.sort()

def pct(data, p):
    idx = int(len(data) * p / 100)
    return data[min(idx, len(data)-1)]

print('FILE POOL SIZE ANALYSIS')
print(f'Total files: {len(sizes):,}')
if sizes:
    print(f'Min: {min(sizes):,} B')
    print(f'Max: {max(sizes):,} B ({max(sizes)/1e6:.1f} MB)')
    print(f'Mean: {sum(sizes)/len(sizes):,.0f} B ({sum(sizes)/len(sizes)/1024:.1f} KB)')
    print()

    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        val = pct(sizes, p)
        if val < 1024:
            label = f'{val:,} B'
        elif val < 1048576:
            label = f'{val/1024:.1f} KB'
        else:
            label = f'{val/1048576:.1f} MB'
        print(f'  P{p:>2d}: {label}')

    print()
    bins = [
        (0, 1000, '< 1 KB'),
        (1000, 10000, '1-10 KB'),
        (10000, 100000, '10-100 KB'),
        (100000, 1000000, '100KB-1MB'),
        (1000000, 10000000, '1-10 MB'),
        (10000000, 100000000, '10-100 MB'),
        (100000000, 99999999999, '> 100 MB'),
    ]
    for lo, hi, label in bins:
        count = sum(1 for s in sizes if lo <= s < hi)
        if count > 0:
            print(f'  [{label:>12s}]: {count:>8,d} files ({count/len(sizes)*100:5.1f}%)')
