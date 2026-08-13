#!/usr/bin/env python3
"""
build_datasets.py —— 下载全部真实文件数据集到云服务器

下载内容:
  Linux kernel 6.1.x: 30 个连续版本 (6.1.90 ~ 6.1.119), ~4 GB
  PHP Docker images:  15 个镜像, ~2.5 GB
  Ubuntu Docker images: 6 个镜像, ~200 MB
  sqldump:  从公共镜像下载 (Wikipedia dumps), ~10 GB (需手动指定URL)

用法:
  python build_datasets.py                          # 下载全部
  python build_datasets.py --skip-sqldump            # 跳过 sqldump
  python build_datasets.py --kernel-only             # 仅下载内核
  python build_datasets.py --kernel-version 90 119   # 指定内核版本范围
"""

import argparse
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DATA_ROOT = os.environ.get("DATA_ROOT", "/data")
DATASETS = Path(DATA_ROOT) / "datasets"

# ============= Config =============

KERNEL_VERSIONS = list(range(90, 120))  # 6.1.90 ~ 6.1.119 共30版本
KERNEL_URL = "https://cdn.kernel.org/pub/linux/kernel/v6.1/linux-6.1.{}.tar.xz"

PHP_TAGS = [
    # Debian-based (trixie/bookworm, ~170 MB each)
    "php:8.2.32-cli", "php:8.2.32-fpm", "php:8.2.32-apache",
    "php:8.3.32-cli", "php:8.3.32-fpm", "php:8.3.32-apache",
    # Alpine-based (~35 MB each)
    "php:8.2.32-cli-alpine", "php:8.2.32-fpm-alpine",
    "php:8.3.32-cli-alpine", "php:8.3.32-fpm-alpine",
    # PHP 8.4
    "php:8.4-cli", "php:8.4-fpm", "php:8.4-apache",
    "php:8.4-cli-alpine", "php:8.4-fpm-alpine",
]

UBUNTU_TAGS = [
    "ubuntu:22.04", "ubuntu:24.04", "ubuntu:26.04",
    "ubuntu:26.10", "ubuntu:latest", "ubuntu:20.04",
]


def run(cmd: str, desc: str = ""):
    """Run a shell command with logging."""
    prefix = f"[{desc}] " if desc else ""
    print(f"  {prefix}$ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  WARN: {result.stderr.strip()[:200]}")
    return result


# ============= Linux Kernel =============

def download_kernel(start_ver: int, end_ver: int):
    """Download Linux kernel 6.1.x tar.xz files."""
    kernel_dir = DATASETS / "linux"
    kernel_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Downloading Linux Kernel 6.1.{start_ver} ~ 6.1.{end_ver}")
    print(f"Destination: {kernel_dir}")
    print(f"{'='*60}")

    for ver in range(start_ver, end_ver + 1):
        fname = f"linux-6.1.{ver}.tar.xz"
        fpath = kernel_dir / fname
        if fpath.exists():
            sz = fpath.stat().st_size
            print(f"  [SKIP] {fname} ({sz/1e6:.0f} MB, already exists)")
            continue

        url = KERNEL_URL.format(ver)
        print(f"  [DOWNLOAD] {fname} ...")
        result = run(f"wget -q --show-progress -O {fpath} {url}", fname)
        if result.returncode != 0:
            print(f"  FAILED for {ver}, stopping kernel downloads.")
            break

    # Count
    downloaded = list(kernel_dir.glob("*.tar.xz"))
    total_mb = sum(f.stat().st_size for f in downloaded) / 1e6
    print(f"\n  Kernel: {len(downloaded)} files, {total_mb:.0f} MB total")


# ============= Docker Images =============

def download_docker_images(tags: list, dataset_name: str):
    """pull + save docker images, then extract layers."""
    out_dir = DATASETS / dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Downloading {len(tags)} Docker images → {out_dir}")
    print(f"{'='*60}")

    for tag in tags:
        safe_name = tag.replace(":", "_").replace("/", "_")
        tar_path = out_dir / f"{safe_name}.tar"
        extracted_dir = out_dir / f"{safe_name}_extracted"

        if extracted_dir.exists() and any(extracted_dir.iterdir()):
            # Count files in extracted dir
            file_count = sum(1 for _ in extracted_dir.rglob("*") if _.is_file())
            print(f"  [SKIP] {tag} ({file_count} files extracted)")
            continue

        # Pull
        print(f"  [PULL] {tag} ...")
        run(f"docker pull {tag}", tag)

        # Save to tar
        print(f"  [SAVE] {safe_name}.tar ...")
        run(f"docker save {tag} -o {tar_path}", tag)
        sz = tar_path.stat().st_size if tar_path.exists() else 0
        print(f"    -> {sz/1e6:.0f} MB")

        # Extract layers
        print(f"  [EXTRACT] layers ...")
        extracted_dir.mkdir(exist_ok=True)
        run(f"tar -xf {tar_path} -C {extracted_dir}", tag)

        # Extract each layer.tar.gz
        for layer_tar in sorted(extracted_dir.glob("*/layer.tar")):
            layer_name = layer_tar.parent.name
            layer_out = extracted_dir / f"layer_{layer_name}"
            layer_out.mkdir(exist_ok=True)
            run(f"tar -xf {layer_tar} -C {layer_out} 2>/dev/null", f"layer {layer_name[:20]}")
            # Remove layer.tar to save space
            layer_tar.unlink(missing_ok=True)

        # Remove the docker tar to save space
        tar_path.unlink(missing_ok=True)

    # Stats
    for d in out_dir.iterdir():
        if d.is_dir() and d.name.endswith("_extracted"):
            count = sum(1 for _ in d.rglob("*") if _.is_file())
            print(f"  {d.name}: {count} files")


# ============= SQL Dump (skeleton) =============

def download_sqldump(urls: list = None):
    """Download SQL dumps from external URLs.
    
    用户需要提供具体的下载URL。
    推荐: Wikipedia dumps (dumps.wikimedia.org) 或 StackOverflow (archive.org)
    """
    sqldump_dir = DATASETS / "sqldump"
    sqldump_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("SQL Dump Dataset")
    print(f"{'='*60}")

    if not urls:
        print("""
  NOTE: sqldump requires manual URL configuration.
  
  Recommended sources:
  1. Wikipedia weekly dumps:
     https://dumps.wikimedia.org/enwiki/latest/
     Files: enwiki-latest-page.sql.gz (~2 GB), etc.
     
  2. StackOverflow data dump (archive.org):
     https://archive.org/details/stackexchange
     
  Set URLs in the script or pass via --sqldump-url.
  
  Skipping sqldump for now.
""")
        return

    for i, url in enumerate(urls):
        fname = url.split("/")[-1] or f"dump_{i:04d}.sql.gz"
        fpath = sqldump_dir / fname
        if fpath.exists():
            print(f"  [SKIP] {fname}")
            continue
        print(f"  [DOWNLOAD] {fname} ...")
        run(f"wget -q --show-progress -O {fpath} {url}", fname)


# ============= Main =============

def main():
    parser = argparse.ArgumentParser(description="Download real-file datasets")
    parser.add_argument("--skip-sqldump", action="store_true")
    parser.add_argument("--kernel-only", action="store_true")
    parser.add_argument("--kernel-version", nargs=2, type=int, default=[90, 119],
                        help="Start and end kernel version (default: 90 119)")
    parser.add_argument("--sqldump-url", nargs="*",
                        help="SQL dump download URLs")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("BUILD DATASETS")
    print(f"DATA_ROOT={DATA_ROOT}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Kernel
    download_kernel(args.kernel_version[0], args.kernel_version[1])

    if not args.kernel_only:
        # 2. PHP Docker
        download_docker_images(PHP_TAGS, "php")

        # 3. Ubuntu Docker
        download_docker_images(UBUNTU_TAGS, "ubuntu")

        # 4. SQL dump
        if not args.skip_sqldump:
            download_sqldump(args.sqldump_url)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"Done! Total time: {elapsed/60:.0f} min")
    print(f"Datasets: {DATASETS}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
