#!/bin/bash
# Minimal dataset: 5 consecutive Linux kernel versions
set -e
DATA=/data/datasets/linux
mkdir -p $DATA
LOG=/data/logs/mini_download.log
echo "=== $(date) Starting mini download ===" | tee -a $LOG
BASE="https://cdn.kernel.org/pub/linux/kernel/v6.1/"
for ver in 100 101 102 103 104; do
    fname="linux-6.1.${ver}.tar.xz"
    fpath="$DATA/$fname"
    if [ -f "$fpath" ]; then
        echo "  SKIP $fname (exists)" | tee -a $LOG
        continue
    fi
    echo "  Downloading $fname ..." | tee -a $LOG
    wget -q --show-progress -P $DATA "$BASE$fname" 2>&1 | tee -a $LOG
    echo "  Done: $fname" | tee -a $LOG
done
echo "=== $(date) Download complete ===" | tee -a $LOG
ls -lh $DATA/ | tee -a $LOG
du -sh $DATA/ | tee -a $LOG
