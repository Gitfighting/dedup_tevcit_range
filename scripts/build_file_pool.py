#!/usr/bin/env python3
"""
build_file_pool.py —— 从数据集中提取全部文件，构建三层全局文件池

三层:
  pool_small:  提取的单文件 (0B ~ 100MB)
  pool_medium: 完整归档 tar.xz (50MB ~ 200MB)  
  pool_large:  解压后根文件系统 / SQL dump 整体文件 (500MB ~ 5GB)

输出: /data/file_pool.csv
  columns: file_path, size_bytes, dataset, source_archive, tier

用法:
  python build_file_pool.py
"""

import csv
import os
import subprocess
import tarfile
import time
from pathlib import Path
from collections import defaultdict

DATA_ROOT = os.environ.get("DATA_ROOT", "/data")
DATASETS = Path(DATA_ROOT) / "datasets"
OUTPUT = Path(DATA_ROOT) / "file_pool.csv"
MAX_SMALL = 100 * 1024 * 1024   # 100 MB: small tier upper bound
MAX_MEDIUM = 500 * 1024 * 1024  # 500 MB: medium tier upper bound


def dir_size(path: Path) -> int:
    """Recursively compute total size of all files in directory."""
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def extract_tar_members(tar_path: Path, prefix: str, dataset: str,
                        pool: list, tier: str):
    """流式读取 tar 文件，记录每个成员的大小，不提取到磁盘。"""
    try:
        with tarfile.open(tar_path, 'r:*') as tf:
            for member in tf.getmembers():
                if not member.isfile() or member.size == 0:
                    continue
                if member.size > MAX_SMALL and tier == "small":
                    continue
                entry = (
                    f"{prefix}/{member.name}",
                    member.size,
                    dataset,
                    tar_path.name,
                    tier,
                )
                pool.append(entry)
    except Exception as e:
        print(f"  WARN: {tar_path.name}: {e}")


def walk_extracted_dir(extracted_dir: Path, prefix: str, dataset: str,
                       pool: list, tier: str):
    """遍历已解压的目录，记录每个文件。"""
    count = 0
    for f in extracted_dir.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        if size > MAX_SMALL and tier == "small":
            continue
        entry = (
            f"{prefix}/{f.relative_to(extracted_dir)}",
            size,
            dataset,
            extracted_dir.name,
            tier,
        )
        pool.append(entry)
        count += 1
    return count


def build_pool():
    """构建三层文件池。"""
    pool = []
    stats = defaultdict(lambda: defaultdict(int))

    print("=" * 60)
    print("BUILD FILE POOL")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ---- Tier 1: SMALL (single files from archives) ----
    print("\n[TIER small] Extracting individual files from archives...")

    # Kernel
    kernel_dir = DATASETS / "linux"
    for tar_f in sorted(kernel_dir.glob("linux-*.tar.xz")):
        ver = tar_f.stem.replace("linux-", "").replace(".tar", "")
        extract_tar_members(tar_f, f"linux/{ver}", "linux", pool, "small")

    n_kernel = sum(1 for p in pool if p[2] == "linux")
    print(f"  linux: {n_kernel:,} files")

    # PHP Docker layers
    php_dir = DATASETS / "php"
    for layer_dir in php_dir.glob("*_extracted/layer_*"):
        tag = layer_dir.parent.name.replace("_extracted", "")
        cnt = walk_extracted_dir(layer_dir, f"php/{tag}/layer", "php", pool, "small")
        print(f"  php/{tag}/{layer_dir.name}: {cnt} files") if cnt > 1000 else None

    n_php = sum(1 for p in pool if p[2] == "php")
    print(f"  php: {n_php - sum(1 for p in pool if p[2] != 'php'):,} files (cumulative)")

    # Ubuntu Docker layers
    ubuntu_dir = DATASETS / "ubuntu"
    for layer_dir in ubuntu_dir.glob("*_extracted/layer_*"):
        tag = layer_dir.parent.name.replace("_extracted", "")
        cnt = walk_extracted_dir(layer_dir, f"ubuntu/{tag}/layer", "ubuntu", pool, "small")
        print(f"  ubuntu/{tag}/{layer_dir.name}: {cnt} files") if cnt > 1000 else None

    # SQL dump: individual tables (if split)
    sqldump_dir = DATASETS / "sqldump"
    for sql_f in sqldump_dir.glob("*.sql"):
        sz = sql_f.stat().st_size
        if sz <= MAX_SMALL:
            pool.append((f"sqldump/{sql_f.name}", sz, "sqldump", sql_f.name, "small"))
        # Else: handled in medium/large tiers

    n_small = len(pool)
    print(f"\n  Tier SMALL total: {n_small:,} files")


    # ---- Tier 2: MEDIUM (complete tar.xz archives) ----
    print("\n[TIER medium] Adding complete tar archives...")
    medium_count = 0
    for tar_f in kernel_dir.glob("*.tar.xz"):
        sz = tar_f.stat().st_size
        if MAX_SMALL < sz <= MAX_MEDIUM:
            pool.append((f"linux_archives/{tar_f.name}", sz, "linux", tar_f.name, "medium"))
            medium_count += 1

    for tar_f in php_dir.glob("*.tar"):
        sz = tar_f.stat().st_size
        if MAX_SMALL < sz <= MAX_MEDIUM:
            pool.append((f"php_archives/{tar_f.name}", sz, "php", tar_f.name, "medium"))
            medium_count += 1

    for tar_f in ubuntu_dir.glob("*.tar"):
        sz = tar_f.stat().st_size
        if MAX_SMALL < sz <= MAX_MEDIUM:
            pool.append((f"ubuntu_archives/{tar_f.name}", sz, "ubuntu", tar_f.name, "medium"))
            medium_count += 1

    for sql_f in sqldump_dir.glob("*.sql"):
        sz = sql_f.stat().st_size
        if MAX_SMALL < sz <= MAX_MEDIUM:
            pool.append((f"sqldump/{sql_f.name}", sz, "sqldump", sql_f.name, "medium"))
            medium_count += 1

    print(f"  Tier MEDIUM total: {medium_count} files")


    # ---- Tier 3: LARGE (extracted root filesystems, whole SQL dumps) ----
    print("\n[TIER large] Adding large extracted filesystems and dumps...")

    large_count = 0
    for dist_dir in [php_dir, ubuntu_dir]:
        for extracted in dist_dir.glob("*_extracted"):
            # Whole extracted rootFS as one "file"
            sz = dir_size(extracted)
            if sz > MAX_MEDIUM:
                tag = extracted.name.replace("_extracted", "")
                dataset = dist_dir.name
                pool.append((f"{dataset}_rootfs/{tag}", sz, dataset, extracted.name, "large"))
                large_count += 1
                print(f"  {dataset}_rootfs/{tag}: {sz/1e9:.2f} GB")

    for sql_f in sqldump_dir.glob("*.sql"):
        sz = sql_f.stat().st_size
        if sz > MAX_MEDIUM:
            pool.append((f"sqldump/{sql_f.name}", sz, "sqldump", sql_f.name, "large"))
            large_count += 1
            print(f"  sqldump/{sql_f.name}: {sz/1e9:.2f} GB")

    print(f"  Tier LARGE total: {large_count} files")


    # ---- Sort and write ----
    print("\n" + "=" * 60)
    print("Sorting and writing pool...")
    pool.sort(key=lambda x: x[1])

    with open(OUTPUT, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["file_path", "size_bytes", "dataset", "source_archive", "tier"])
        writer.writerows(pool)

    # ---- Stats ----
    total_gb = sum(p[1] for p in pool) / 1e9
    print(f"\n{'='*60}")
    print(f"FILE POOL SUMMARY")
    print(f"{'='*60}")
    print(f"  Total files:  {len(pool):,}")
    print(f"  Total size:   {total_gb:.1f} GB")
    print(f"  Range:        {pool[0][1]:,} B ~ {pool[-1][1]:,} B")

    # By tier
    tiers = defaultdict(lambda: [0, 0])
    for p in pool:
        tiers[p[4]][0] += 1
        tiers[p[4]][1] += p[1]
    for tier_name, (cnt, sz) in tiers.items():
        print(f"  {tier_name:>8s}:  {cnt:>10,} files, {sz/1e9:.1f} GB")

    # By dataset
    datasets = defaultdict(lambda: [0, 0])
    for p in pool:
        datasets[p[2]][0] += 1
        datasets[p[2]][1] += p[1]
    for ds_name, (cnt, sz) in datasets.items():
        print(f"  {ds_name:>8s}:  {cnt:>10,} files, {sz/1e9:.1f} GB")

    # Size distribution
    bins = [
        ("<1KB", 0, 1024), ("1-10KB", 1024, 10240),
        ("10-100KB", 10240, 102400), ("100KB-1MB", 102400, 1048576),
        ("1-10MB", 1048576, 10485760), ("10-100MB", 10485760, 104857600),
        ("100MB-1GB", 104857600, 1073741824), (">1GB", 1073741824, float("inf")),
    ]
    print(f"\n  Size distribution:")
    for label, lo, hi in bins:
        cnt = sum(1 for p in pool if lo <= p[1] < hi)
        pct = cnt / len(pool) * 100
        bar = "#" * max(1, int(pct))
        print(f"    {label:>12s}: {cnt:>10,} ({pct:5.1f}%) {bar}")

    print(f"\n  Output: {OUTPUT}")
    print("=" * 60)


if __name__ == "__main__":
    build_pool()
