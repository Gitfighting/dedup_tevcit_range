#!/usr/bin/env python3
"""
rebuild_pool.py —— 重建文件池，合并小文件池 + 大压缩包

生成三层文件池:
  L1: 解压后的小文件 (~47万, 1B-48MB, median 3KB)
      → 匹配 T29 (tiny), T15 P50
  L2: 中等压缩包 (PHP .tar.xz ~12MB, ubuntu .tar.gz ~30MB)
      → 匹配 T15 P90, T65/T78, T79 P50-P75
  L3: 大内核压缩包 (15个 .tar.xz, 77-148MB)
      → 匹配 T79 P75+, 通过拼接覆盖 GB 级

映射策略:
  每个 trace PUT(size=S) 选择最接近 S 的真实文件(组)
  用该文件的真实字节运行 CDC → 科学有效
"""

import json
import hashlib
from pathlib import Path
from collections import defaultdict

POOL_FILE = Path("/data/filepool/enriched_file_list.json")
DATASET_DIR = Path("/data/datasets")

def main():
    # --- L1: extracted small files (keep existing) ---
    old_pool_file = Path("/data/filepool/file_list.json")
    l1_files = []
    if old_pool_file.exists():
        old_pool = json.loads(old_pool_file.read_text())
        for dataset, files in old_pool.items():
            for fp in files:
                try:
                    sz = Path(fp).stat().st_size
                    if 10 <= sz <= 50_000_000:  # 10B - 50MB
                        l1_files.append({"path": fp, "size": sz, "layer": "L1_small"})
                except:
                    pass
    
    print(f"L1 (extracted files <50MB): {len(l1_files):,}")
    if l1_files:
        sizes = [f["size"] for f in l1_files]
        sizes.sort()
        print(f"  Size range: {min(sizes):,} - {max(sizes)/1e6:.1f} MB")
        print(f"  Median: {sizes[len(sizes)//2]:,} B")
    
    # --- L2: Medium archives (PHP, ubuntu) ---
    l2_files = []
    for d in ["php", "ubuntu"]:
        dp = DATASET_DIR / d
        for f in dp.glob("*.tar*"):
            sz = f.stat().st_size
            if sz > 10000:
                l2_files.append({"path": str(f), "size": sz, "layer": "L2_medium", "dataset": d})
    
    print(f"\nL2 (medium tarballs): {len(l2_files)}")
    for f in sorted(l2_files, key=lambda x: x["size"]):
        print(f"  {Path(f['path']).name}: {f['size']/1e6:.1f} MB [{f.get('dataset','')}]")
    
    # --- L3: Large kernel tarballs ---
    l3_files = []
    for d in ["linux", "linux_big"]:
        dp = DATASET_DIR / d
        for f in dp.glob("*.tar.xz"):
            sz = f.stat().st_size
            if sz > 50_000_000:  # >50MB
                l3_files.append({"path": str(f), "size": sz, "layer": "L3_large", "dataset": d})
    
    print(f"\nL3 (large kernel tarballs): {len(l3_files)}")
    for f in sorted(l3_files, key=lambda x: x["size"]):
        print(f"  {Path(f['path']).name}: {f['size']/1e6:.1f} MB")
    
    # --- Build enriched pool ---
    all_files = l1_files + l2_files + l3_files
    
    # Sort by size for efficient lookup
    all_files.sort(key=lambda x: x["size"])
    
    # Save
    pool_data = {
        "total_files": len(all_files),
        "layers": {
            "L1_small_count": len(l1_files),
            "L2_medium_count": len(l2_files),
            "L3_large_count": len(l3_files),
        },
        "files": all_files,
    }
    
    POOL_FILE.write_text(json.dumps(pool_data, indent=2))
    
    total_size = sum(f["size"] for f in all_files)
    print(f"\n{'='*50}")
    print(f"POOL SAVED: {len(all_files):,} files, {total_size/1e9:.2f} GB")
    print(f"  {POOL_FILE}")
    
    # Print size distribution
    print(f"\nSize Coverage:")
    sizes_all = sorted(f["size"] for f in all_files)
    def pct(data, p):
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data)-1)]
    
    for p, label in [(1,'P01'),(10,'P10'),(25,'P25'),(50,'P50'),(75,'P75'),(90,'P90'),(95,'P95'),(99,'P99'),(100,'MAX')]:
        val = pct(sizes_all, p)
        if val < 1024: u = f"{val:,} B"
        elif val < 1e6: u = f"{val/1024:.0f} KB"
        elif val < 1e9: u = f"{val/1e6:.1f} MB"
        else: u = f"{val/1e9:.2f} GB"
        print(f"  {label}: {u}")

if __name__ == "__main__":
    main()
