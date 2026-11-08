#!/usr/bin/env python3
"""
download_all_datasets.py —— 下载四个真实数据集到云服务器

数据集:
  linux:   5 个连续内核版本 (6.1.178~182), 压缩包 ~100MB, 解压后 ~650MB
  php:     PHP Docker 镜像 (8.2.33, 8.3.33, 8.4.24) x 3 变体 = 9 个, ~2GB
  ubuntu:  5 个 Ubuntu LTS Docker 镜像 (20.04~26.10), ~200MB
  sqldump: Wikipedia enwiki page.sql.gz (3 个连续月), ~6.6GB

用法:
  source /data/venv/bin/activate && python download_all_datasets.py

预计总大小: ~10GB, 数据盘 275GB 可用, 足够
去重潜力: 连续版本间 ~95% 重叠, Docker 共享基础层
"""

import os
import subprocess
import sys
import time
import gzip
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path("/data/datasets")
LOG_DIR = Path("/data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: str, desc: str = "", timeout: int = 600) -> bool:
    """Run a shell command with logging."""
    print(f"  [{desc}] {cmd[:120]}...")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            if err:
                print(f"  WARN: {err[:300]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"  WARN: timeout after {timeout}s")
        return False
    except Exception as e:
        print(f"  WARN: {e}")
        return False


# ============================================================
# 1. Linux Kernel (5 consecutive 6.1.x versions)
# ============================================================

def download_linux_kernel():
    """Download 5 consecutive Linux 6.1.x LTS kernel tarballs."""
    print("\n" + "=" * 60)
    print("[1/4] LINUX KERNEL (6.1.178 ~ 6.1.182)")
    print("=" * 60)
    print("  Each: ~20MB compressed, ~130MB extracted")
    print("  Dedup: ~99% overlap between consecutive patch versions")
    
    d = BASE / "linux"
    d.mkdir(parents=True, exist_ok=True)
    
    # Use Aliyun mirror (much faster from China)
    KERNEL_BASE = "https://mirrors.aliyun.com/linux-kernel/v6.x/linux-6.1.{}.tar.xz"
    KERNEL_FALLBACK = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.{}.tar.xz"
    VERSIONS = [178, 179, 180, 181, 182]
    
    for ver in VERSIONS:
        fname = f"linux-6.1.{ver}.tar.xz"
        fpath = d / fname
        
        if fpath.exists() and fpath.stat().st_size > 1000000:
            print(f"  SKIP {fname} (already exists, {fpath.stat().st_size/1e6:.1f} MB)")
            continue
        
            url = KERNEL_BASE.format(ver)
            print(f"  Downloading {fname} from Aliyun mirror...")
            ok = run(
                f"wget -q --show-progress --timeout=30 -P {d} '{url}'",
                f"kernel 6.1.{ver}",
                timeout=120,
            )
            
            if not ok or (fpath.exists() and fpath.stat().st_size < 1000000):
                # Fallback to kernel.org
                url = KERNEL_FALLBACK.format(ver)
                print(f"  Retry with kernel.org mirror...")
                run(f"wget -q --show-progress --timeout=120 -P {d} '{url}'",
                    f"kernel 6.1.{ver} (fallback)", timeout=300)
        
        if fpath.exists():
            print(f"  DONE: {fname} ({fpath.stat().st_size/1e6:.1f} MB)")
        else:
            print(f"  FAIL: {fname} not downloaded")
    
    _show_dir_stats(d, "linux kernel")


# ============================================================
# 2. PHP Docker Images
# ============================================================

def download_php_docker():
    """Pull PHP Docker images and save them as tar files."""
    print("\n" + "=" * 60)
    print("[2/4] PHP DOCKER IMAGES")
    print("=" * 60)
    print("  3 PHP versions x 3 variants = 9 images")
    print("  Docker layers are shared across variants → natural dedup")
    
    d = BASE / "php"
    d.mkdir(parents=True, exist_ok=True)
    
    # 3 PHP versions, 3 variants each
    TAGS = [
        # version, variants
        ("php:8.2.33-cli-bookworm", "php_8.2.33_cli_bookworm.tar"),
        ("php:8.2.33-fpm-bookworm", "php_8.2.33_fpm_bookworm.tar"),
        ("php:8.2.33-apache-bookworm", "php_8.2.33_apache_bookworm.tar"),
        ("php:8.3.33-cli-bookworm", "php_8.3.33_cli_bookworm.tar"),
        ("php:8.3.33-fpm-bookworm", "php_8.3.33_fpm_bookworm.tar"),
        ("php:8.3.33-apache-bookworm", "php_8.3.33_apache_bookworm.tar"),
        ("php:8.4.24-cli-bookworm", "php_8.4.24_cli_bookworm.tar"),
        ("php:8.4.24-fpm-bookworm", "php_8.4.24_fpm_bookworm.tar"),
        ("php:8.4.24-apache-bookworm", "php_8.4.24_apache_bookworm.tar"),
    ]
    
    for full_tag, save_name in TAGS:
        fpath = d / save_name
        
        if fpath.exists() and fpath.stat().st_size > 1000000:
            print(f"  SKIP {full_tag} (already saved)")
            continue
        
        print(f"  Pulling {full_tag}...")
        if not run(f"docker pull {full_tag}", full_tag, timeout=600):
            # Try without -bookworm suffix (default)
            short_tag = full_tag.replace("-bookworm", "")
            if short_tag != full_tag:
                print(f"  Retry with {short_tag}...")
                if run(f"docker pull {short_tag}", short_tag, timeout=600):
                    full_tag = short_tag
        
        print(f"  Saving {full_tag} -> {save_name}...")
        run(f"docker save {full_tag} -o {fpath}", f"save {full_tag}", timeout=120)
        
        # Remove image to free disk space
        run(f"docker rmi {full_tag} 2>/dev/null", f"rmi {full_tag}")
        
        if fpath.exists():
            print(f"  DONE: {save_name} ({fpath.stat().st_size/1e6:.1f} MB)")
    
    _show_dir_stats(d, "PHP Docker")


# ============================================================
# 3. Ubuntu Docker Images
# ============================================================

def download_ubuntu_docker():
    """Pull Ubuntu LTS Docker images."""
    print("\n" + "=" * 60)
    print("[3/4] UBUNTU DOCKER IMAGES")
    print("=" * 60)
    print("  5 Ubuntu LTS versions (20.04 ~ 26.10)")
    
    d = BASE / "ubuntu"
    d.mkdir(parents=True, exist_ok=True)
    
    TAGS = [
        ("ubuntu:20.04", "ubuntu_20.04.tar"),
        ("ubuntu:22.04", "ubuntu_22.04.tar"),
        ("ubuntu:24.04", "ubuntu_24.04.tar"),
        ("ubuntu:26.04", "ubuntu_26.04.tar"),
        ("ubuntu:26.10", "ubuntu_26.10.tar"),
    ]
    
    for full_tag, save_name in TAGS:
        fpath = d / save_name
        
        if fpath.exists() and fpath.stat().st_size > 1000000:
            print(f"  SKIP {full_tag} (already saved)")
            continue
        
        print(f"  Pulling {full_tag}...")
        run(f"docker pull {full_tag}", full_tag, timeout=300)
        
        print(f"  Saving {full_tag} -> {save_name}...")
        run(f"docker save {full_tag} -o {fpath}", f"save {full_tag}", timeout=120)
        run(f"docker rmi {full_tag} 2>/dev/null", f"rmi {full_tag}")
        
        if fpath.exists():
            print(f"  DONE: {save_name} ({fpath.stat().st_size/1e6:.1f} MB)")
    
    _show_dir_stats(d, "Ubuntu Docker")


# ============================================================
# 4. Wikipedia SQL Dumps (sqldump)
# ============================================================

def download_wikipedia_sql():
    """
    Download Wikipedia page.sql.gz for 3 consecutive months.
    
    These are SQL table dumps (page metadata, NOT full wiki text).
    Each ~2.2GB compressed → ~3 months = ~6.6GB total.
    Consecutive months have ~95% identical rows → excellent dedup potential.
    
    Alternative: categorylinks.sql.gz (~33MB each, ~95% overlap)
    Using the smaller 'category' dumps first, fall back to page.sql.gz
    """
    print("\n" + "=" * 60)
    print("[4/4] WIKIPEDIA SQL DUMPS (sqldump)")
    print("=" * 60)
    print("  Monthly enwiki SQL dumps (3 consecutive months)")
    print("  ~95% overlap between consecutive months → excellent dedup")
    
    d = BASE / "sqldump"
    d.mkdir(parents=True, exist_ok=True)
    
    # Try smaller tables first (category, categorylinks) which still have high overlap
    # Then medium-sized tables (page, pagelinks)
    
    # Priority: smaller files with high dedup potential
    TABLES = [
        # (table_name, typical_size)
        ("category.sql.gz", "33 MB"),
        ("categorylinks.sql.gz", "2.3 GB"),
    ]
    
    # Use 3 consecutive months (last 3 available: May, Jun, Jul 2026)
    MONTHS = [
        ("20260501", "2026-05"),
        ("20260601", "2026-06"),
        ("20260701", "2026-07"),
    ]
    
    BASE_URL = "https://dumps.wikimedia.org/enwiki/{date}/enwiki-{date}-{table}"
    
    for table, _size in TABLES:
        print(f"\n  Table: {table} (~{_size}/month)")
        
        for date, label in MONTHS:
            fname = f"enwiki-{date}-{table}"
            fpath = d / fname
            
            if fpath.exists() and fpath.stat().st_size > 100:
                print(f"  SKIP {fname} (already exists, {fpath.stat().st_size/1e6:.1f} MB)")
                continue
            
            url = BASE_URL.format(date=date, table=table)
            print(f"  Downloading {label} {table}...")
            
            ok = run(
                f"wget -q --show-progress --timeout=600 -P {d} '{url}'",
                f"wiki {label} {table}",
                timeout=900,
            )
            
            if not ok:
                print(f"  Retry with curl...")
                run(f"curl -L --max-time 900 -o {fpath} '{url}'", f"wiki {label} {table} (curl)")
            
            if fpath.exists():
                print(f"  DONE: {fname} ({fpath.stat().st_size/1e6:.1f} MB)")
            
            time.sleep(1)  # Be nice to the server
    
    _show_dir_stats(d, "Wikipedia SQL")


# ============================================================
# Helpers
# ============================================================

def _show_dir_stats(d: Path, label: str):
    """Show directory statistics."""
    if d.exists():
        files = list(d.glob("*"))
        total = sum(f.stat().st_size for f in files if f.is_file())
        count = sum(1 for f in files if f.is_file())
        print(f"  [{label}] {count} files, {total/1e6:.1f} MB / {total/1e9:.2f} GB")


def _count_files_recursive(d: Path) -> tuple:
    """Count total files and size recursively."""
    total = 0
    count = 0
    if d.exists():
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
                count += 1
    return count, total


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("  DATASET DOWNLOAD SCRIPT")
    print("  Server: " + os.uname().nodename)
    print("  Disk:   " + _get_disk_info())
    print("=" * 70)
    
    start_time = time.time()
    
    # Check disk space
    disk = shutil.disk_usage("/data")
    free_gb = disk.free / 1e9
    print(f"  Free space: {free_gb:.1f} GB")
    
    if free_gb < 50:
        print(f"  WARNING: Only {free_gb:.1f} GB free. Some downloads may fail.")
        print(f"  Continue? Adjusting to download fewer datasets.")
    
    # Download all four datasets
    download_linux_kernel()
    download_php_docker()
    download_ubuntu_docker()
    download_wikipedia_sql()
    
    elapsed = time.time() - start_time
    
    # ================================================================
    # Summary
    # ================================================================
    print("\n" + "=" * 70)
    print("  DOWNLOAD COMPLETE")
    print("  Elapsed: %.1f min" % (elapsed / 60))
    print("=" * 70)
    
    total_size = 0
    total_files_all = 0
    
    for subdir in ["linux", "php", "ubuntu", "sqldump"]:
        d = BASE / subdir
        if d.exists():
            n, sz = _count_files_recursive(d)
            total_files_all += n
            total_size += sz
            print(f"  {subdir:>10s}: {n:>6d} files, {sz/1e9:>8.2f} GB")
        else:
            print(f"  {subdir:>10s}: (empty)")
    
    print(f"  {'TOTAL':>10s}: {total_files_all:>6d} files, {total_size/1e9:>8.2f} GB")
    print("=" * 70)


def _get_disk_info() -> str:
    try:
        disk = shutil.disk_usage("/data")
        return f"{disk.free/1e9:.1f} GB free / {disk.total/1e9:.1f} GB total"
    except:
        return "unknown"


if __name__ == "__main__":
    main()
