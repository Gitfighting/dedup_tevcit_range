#!/bin/bash
# download_big.sh - Download large files for file pool coverage
# Covers: MB-range (kernel tarballs), GB-range (ISOs)
# Total: ~10GB additional

set -e

LOG=/data/logs/big_download.log
echo "=== $(date) START BIG FILE DOWNLOAD ===" | tee -a $LOG

MIRROR="https://mirrors.aliyun.com/linux-kernel/v6.x"
mkdir -p /data/datasets/linux_big

# ------------------------------------------------------------
# 1. MORE KERNEL VERSIONS - 3 more LTS series x 5 versions each
#    These are real compressed tarballs (~140MB each)
#    - Great for matching T15/T65/T78 P90+ sizes
#    - Different series have different code → more file variety
# ------------------------------------------------------------
echo "[1] More Linux Kernel Series" | tee -a $LOG

# 6.6.x (5 versions: 145-149)
for ver in 145 146 147 148 149; do
    f="/data/datasets/linux_big/linux-6.6.${ver}.tar.xz"
    if [ -s "$f" ]; then
        echo "  SKIP linux-6.6.${ver}" | tee -a $LOG
        continue
    fi
    echo "  Downloading linux-6.6.${ver}..." | tee -a $LOG
    wget -q --show-progress -O "$f" "$MIRROR/linux-6.6.${ver}.tar.xz" 2>&1 | tail -1
    echo "  DONE: $(ls -lh $f | awk '{print $5}')" | tee -a $LOG
done

# 6.12.x (5 versions: 98-102)
for ver in 98 99 100 101 102; do
    f="/data/datasets/linux_big/linux-6.12.${ver}.tar.xz"
    if [ -s "$f" ]; then
        echo "  SKIP linux-6.12.${ver}" | tee -a $LOG
        continue
    fi
    echo "  Downloading linux-6.12.${ver}..." | tee -a $LOG
    wget -q --show-progress -O "$f" "$MIRROR/linux-6.12.${ver}.tar.xz" 2>&1 | tail -1
    echo "  DONE: $(ls -lh $f | awk '{print $5}')" | tee -a $LOG
done

echo "  Kernel series done" | tee -a $LOG

# ------------------------------------------------------------
# 2. UBUNTU ISO IMAGES - Matches T79 GB-scale objects
#    Each ~1.8GB, perfect for large object mapping
# ------------------------------------------------------------
echo "[2] Ubuntu Server ISOs (~2GB each)" | tee -a $LOG
mkdir -p /data/datasets/iso

# Use -L to follow redirect
for ver in 22.04.5 24.04.2; do
    f="/data/datasets/iso/ubuntu-${ver}-live-server-amd64.iso"
    if [ -s "$f" ]; then
        echo "  SKIP ubuntu-${ver}" | tee -a $LOG
        continue
    fi
    url="https://mirrors.aliyun.com/ubuntu-releases/${ver%.*}/ubuntu-${ver}-live-server-amd64.iso"
    echo "  Downloading ubuntu-${ver} (will take a while)..." | tee -a $LOG
    wget -q --show-progress -L -O "$f" "$url" 2>&1 | tail -1
    echo "  DONE: $(ls -lh $f | awk '{print $5}')" | tee -a $LOG
done

# ------------------------------------------------------------
# 3. DEBIAN NETINST ISO - Medium size (~600MB)
# ------------------------------------------------------------
echo "[3] Debian netinst ISO (~600MB)" | tee -a $LOG
f="/data/datasets/iso/debian-12.10.0-amd64-netinst.iso"
if [ ! -s "$f" ]; then
    echo "  Downloading debian-12.10..." | tee -a $LOG
    wget -q --show-progress -L -O "$f" \
        "https://mirrors.aliyun.com/debian-cd/current/amd64/iso-cd/debian-12.10.0-amd64-netinst.iso" 2>&1 | tail -1
    echo "  DONE: $(ls -lh $f | awk '{print $5}')" | tee -a $LOG
else
    echo "  SKIP debian-12.10" | tee -a $LOG
fi

# ------------------------------------------------------------
# SUMMARY
# ------------------------------------------------------------
echo "" | tee -a $LOG
echo "=== $(date) BIG FILES COMPLETE ===" | tee -a $LOG
echo "Kernel big:" | tee -a $LOG
du -sh /data/datasets/linux_big/ | tee -a $LOG
echo "ISOs:" | tee -a $LOG
du -sh /data/datasets/iso/ | tee -a $LOG
echo "TOTAL datasets:" | tee -a $LOG
du -sh /data/datasets/ | tee -a $LOG
