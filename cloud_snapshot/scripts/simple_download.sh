#!/bin/bash
# simple_download.sh - Download all 4 datasets
# Run: nohup bash /data/scripts/simple_download.sh > /data/logs/simple_download.log 2>&1 &
set -e

LOG=/data/logs/simple_download.log
echo "=== $(date) START ===" | tee -a $LOG

# 1. Linux Kernel (Aliyun mirror)
echo "[1/4] Linux Kernel (5 versions, ~130MB each)" | tee -a $LOG
mkdir -p /data/datasets/linux
MIRROR="https://mirrors.aliyun.com/linux-kernel/v6.x"
for ver in 178 179 180 181 182; do
    f="/data/datasets/linux/linux-6.1.${ver}.tar.xz"
    if [ -s "$f" ]; then
        echo "  SKIP 6.1.${ver} (exists: $(ls -lh $f | awk '{print $5}'))" | tee -a $LOG
        continue
    fi
    echo "  Downloading 6.1.${ver} from Aliyun..." | tee -a $LOG
    wget -q --show-progress -O "$f" "$MIRROR/linux-6.1.${ver}.tar.xz" 2>&1 | tee -a $LOG
    SIZE=$(ls -lh $f 2>/dev/null | awk '{print $5}')
    echo "  DONE 6.1.${ver}: $SIZE" | tee -a $LOG
done
echo "  Linux done: $(du -sh /data/datasets/linux/ | awk '{print $1}')" | tee -a $LOG

# 2. PHP Docker
echo "[2/4] PHP Docker (9 images)" | tee -a $LOG
mkdir -p /data/datasets/php
for tag in php:8.2.33-cli php:8.2.33-fpm php:8.2.33-apache \
           php:8.3.33-cli php:8.3.33-fpm php:8.3.33-apache \
           php:8.4.24-cli php:8.4.24-fpm php:8.4.24-apache; do
    out="/data/datasets/php/$(echo $tag | tr ':' '_').tar"
    if [ -s "$out" ]; then
        echo "  SKIP $tag (exists)" | tee -a $LOG
        continue
    fi
    echo "  Pulling $tag..." | tee -a $LOG
    docker pull "$tag" 2>&1 | tail -3 | tee -a $LOG
    docker save "$tag" -o "$out"
    docker rmi "$tag" 2>/dev/null
    echo "  Saved: $(ls -lh $out | awk '{print $5}')" | tee -a $LOG
done
echo "  PHP done: $(du -sh /data/datasets/php/ | awk '{print $1}')" | tee -a $LOG

# 3. Ubuntu Docker
echo "[3/4] Ubuntu Docker (5 images)" | tee -a $LOG
mkdir -p /data/datasets/ubuntu
for tag in ubuntu:20.04 ubuntu:22.04 ubuntu:24.04 ubuntu:26.04 ubuntu:26.10; do
    out="/data/datasets/ubuntu/$(echo $tag | tr ':' '_').tar"
    if [ -s "$out" ]; then
        echo "  SKIP $tag (exists)" | tee -a $LOG
        continue
    fi
    echo "  Pulling $tag..." | tee -a $LOG
    docker pull "$tag" 2>&1 | tail -3 | tee -a $LOG
    docker save "$tag" -o "$out"
    docker rmi "$tag" 2>/dev/null
    echo "  Saved: $(ls -lh $out | awk '{print $5}')" | tee -a $LOG
done
echo "  Ubuntu done: $(du -sh /data/datasets/ubuntu/ | awk '{print $1}')" | tee -a $LOG

# 4. Wikipedia SQL (small tables only - category + categorylinks)
echo "[4/4] Wikipedia SQL (6 files, ~3 months x 2 tables)" | tee -a $LOG
mkdir -p /data/datasets/sqldump
BASE="https://dumps.wikimedia.org/enwiki"
for date in 20260501 20260601 20260701; do
    for tbl in category.sql.gz categorylinks.sql.gz; do
        f="/data/datasets/sqldump/enwiki-${date}-${tbl}"
        if [ -s "$f" ]; then
            echo "  SKIP ${date}/${tbl} (exists: $(ls -lh $f | awk '{print $5}'))" | tee -a $LOG
            continue
        fi
        echo "  Downloading ${date}/${tbl}..." | tee -a $LOG
        wget -q --show-progress -O "$f" "${BASE}/${date}/enwiki-${date}-${tbl}" 2>&1 | tee -a $LOG
        SIZE=$(ls -lh $f 2>/dev/null | awk '{print $5}')
        echo "  DONE ${date}/${tbl}: $SIZE" | tee -a $LOG
    done
done
echo "  SQL done: $(du -sh /data/datasets/sqldump/ | awk '{print $1}')" | tee -a $LOG

# Summary
echo "" | tee -a $LOG
echo "=== $(date) ALL DOWNLOADS COMPLETE ===" | tee -a $LOG
for d in linux php ubuntu sqldump; do
    if [ -d "/data/datasets/$d" ]; then
        echo "  $d: $(du -sh /data/datasets/$d/ | awk '{print $1}') / $(ls /data/datasets/$d/ | wc -l) files" | tee -a $LOG
    fi
done
echo "  TOTAL: $(du -sh /data/datasets/ | awk '{print $1}')" | tee -a $LOG
