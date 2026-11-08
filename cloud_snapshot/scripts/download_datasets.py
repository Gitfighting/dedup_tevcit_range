#!/usr/bin/env python3
"""
download_datasets.py —— 下载全部数据集到云服务器
用法: source /data/venv/bin/activate && python download_datasets.py

下载内容:
  - Linux kernel 30 个版本 (6.1.90 ~ 6.1.119): ~4GB
  - PHP Docker 镜像 15 个: ~2GB
  - Ubuntu Docker 镜像 6 个: ~0.2GB
  - sqldump: 手动下载（需具体URL）
"""

import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path("/data/datasets")
KERNEL_URL = "https://cdn.kernel.org/pub/linux/kernel/v6.1/linux-6.1.{}.tar.xz"
PHP_TAGS = [
    "8.2.32-cli", "8.2.32-fpm", "8.2.32-apache",
    "8.3.32-cli", "8.3.32-fpm", "8.3.32-apache",
    "8.4.23-cli", "8.4.23-fpm", "8.4.23-apache",
    "8.2.32-cli-alpine", "8.2.32-fpm-alpine",
    "8.3.32-cli-alpine", "8.3.32-fpm-alpine",
    "8.4.23-cli-alpine", "8.4.23-fpm-alpine",
]
UBUNTU_TAGS = ["18.04", "20.04", "22.04", "24.04", "26.04", "latest"]


def run(cmd, desc=""):
    print(f"  [{desc}] {cmd[:100]}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  WARN: {result.stderr[:200]}")
    return result.returncode == 0


def download_kernel():
    print("\n=== Downloading Linux Kernel 30 versions ===")
    d = BASE / "linux"
    d.mkdir(parents=True, exist_ok=True)
    for ver in range(90, 120):
        fname = f"linux-6.1.{ver}.tar.xz"
        fpath = d / fname
        if fpath.exists():
            print(f"  SKIP {fname} (already exists)")
            continue
        url = KERNEL_URL.format(ver)
        ok = run(f"wget -q --show-progress -P {d} {url}", f"kernel 6.1.{ver}")
        if not ok:
            # 某些版本可能不存在，跳过后继续
            print(f"  SKIP 6.1.{ver} (download failed, may not exist)")

        # 每下载 5 个暂停 2 秒，避免被限速
        if ver % 5 == 0 and ver > 90:
            time.sleep(2)


def download_docker_images():
    print("\n=== Pulling Docker images ===")

    # PHP
    d = BASE / "php"
    d.mkdir(parents=True, exist_ok=True)
    for tag in PHP_TAGS:
        fpath = d / f"php_{tag.replace(':', '-')}.tar"
        if fpath.exists():
            print(f"  SKIP php:{tag}")
            continue
        full_tag = f"php:{tag}"
        run(f"docker pull {full_tag}", f"pull {full_tag}")
        run(f"docker save {full_tag} -o {fpath}", f"save {full_tag}")
        run(f"docker rmi {full_tag}", f"rmi {full_tag}")

    # Ubuntu
    d = BASE / "ubuntu"
    d.mkdir(parents=True, exist_ok=True)
    for tag in UBUNTU_TAGS:
        fpath = d / f"ubuntu_{tag.replace(':', '-')}.tar"
        if fpath.exists():
            print(f"  SKIP ubuntu:{tag}")
            continue
        full_tag = f"ubuntu:{tag}"
        run(f"docker pull {full_tag}", f"pull {full_tag}")
        run(f"docker save {full_tag} -o {fpath}", f"save {full_tag}")
        run(f"docker rmi {full_tag}", f"rmi {full_tag}")


def print_sqldump_instructions():
    print("""
=== SQLDUMP 下载说明 ===
sqldump 需要手动从公共镜像下载。以下两个可选:

方案 A: Wikipedia 每日转储
  https://dumps.wikimedia.org/backup-index.html
  选择 30 个连续日期的 pages-meta-history 或 stub dump

方案 B: StackOverflow 数据转储
  https://archive.org/details/stackexchange
  选择 30 个连续版本的 dump

下载后放到 /data/datasets/sqldump/ 目录下。
如果暂时不需要 sqldump，实验可只以内核 + PHP + Ubuntu 为数据集。
""")


def main():
    print("=" * 60)
    print("Dataset Download Script")
    print("=" * 60)

    download_kernel()
    download_docker_images()
    print_sqldump_instructions()

    # 统计
    print("\n=== Download complete ===")
    for subdir in ["linux", "php", "ubuntu", "sqldump"]:
        d = BASE / subdir
        if d.exists():
            total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            count = sum(1 for f in d.rglob("*") if f.is_file())
            print(f"  {subdir}: {count} files, {total/1e9:.1f} GB")


if __name__ == "__main__":
    main()
