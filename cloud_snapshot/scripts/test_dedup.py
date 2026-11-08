#!/usr/bin/env python3
"""Quick CDC dedup test on synthetic kernel files"""
import sys, time
sys.path.insert(0, '/data/scripts')
from cdc_dedup import DedupManager
from pathlib import Path

mgr = DedupManager(chunk_store_dir='/data/chunks')

files = sorted(Path('/data/datasets/linux').glob('kernel_*.bin'))
print('Processing %d files...' % len(files))
print()

total_raw = 0
for i, fp in enumerate(files):
    data = fp.read_bytes()
    total_raw += len(data)
    
    t0 = time.time()
    manifest = mgr.dedup_file('obj_%d' % (i + 1), str(fp))
    elapsed = time.time() - t0
    
    chunk_sizes = []
    for ch in manifest:
        _, sz, _ = mgr.store.index.get(ch, (None, 0, 0))
        chunk_sizes.append(sz)
    avg_chunk = sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0
    
    print('  %s: %s B -> %d chunks, avg %s B (%.2fs)' % (
        fp.name, format(len(data), ','), len(manifest),
        format(int(avg_chunk), ','), elapsed))

stats = mgr.stats()
print()
print('=' * 50)
print('CDC DEDUP RESULTS')
print('=' * 50)
savings = stats['total_bytes_raw'] - stats['total_bytes_stored']
savings_pct = (1 - stats['dedup_ratio']) * 100
print('  Raw bytes:           %s B (%.1f MB)' % (format(stats['total_bytes_raw'], ','), stats['total_bytes_raw']/1e6))
print('  Unique stored bytes: %s B (%.1f MB)' % (format(stats['total_bytes_stored'], ','), stats['total_bytes_stored']/1e6))
print('  Dedup ratio:         %.2f%%' % (stats['dedup_ratio'] * 100))
print('  Savings:             %s B (%.1f%%)' % (format(savings, ','), savings_pct))
print('  Total chunks:        %s' % format(stats['total_chunks_seen'], ','))
print('  Unique chunks:       %s' % format(stats['unique_chunks'], ','))
print('  Duplicate chunks:    %s' % format(stats['duplicate_chunks'], ','))
print('  Objects processed:   %d' % stats['total_objects'])
print('  Avg chunks/object:   %.1f' % stats['avg_chunks_per_object'])

mgr.save()
print()
print('Results saved to /data/chunks/')
