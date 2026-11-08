#!/bin/bash
# ===========================================================================
# run_experiment.sh —— 一键执行完整实验流水线
# 用法: chmod +x run_experiment.sh && ./run_experiment.sh
# ===========================================================================
set -e

source /data/venv/bin/activate

echo "==============================================="
echo " SkyStore CDC Dedup Experiment Pipeline"
echo "==============================================="

# Step 1: 下载数据集
echo ""
echo "[1/5] Downloading datasets..."
python /data/scripts/download_datasets.py 2>&1 | tee /data/logs/download.log

# Step 2: 构建文件池
echo ""
echo "[2/5] Building file pool..."
python /data/scripts/build_file_pool.py 2>&1 | tee /data/logs/file_pool.log

# Step 3: 生成映射表
echo ""
echo "[3/5] Building object-to-file mappings..."
python /data/scripts/build_mapping.py 2>&1 | tee /data/logs/mapping.log

# Step 4: 回放迹（以 T15_typeA 为例做 pilot）
echo ""
echo "[4/5] Running pilot experiment (T15_typeA, dedup=off)..."
python /data/scripts/trace_replayer.py \
    --trace /data/traces/typeA/IBMObjectStoreTrace015Part0.typeA.mc.aug \
    --mapping /data/mappings/T15_typeA_mapping.csv \
    --dedup off \
    --speedup 1000 \
    --mode local \
    2>&1 | tee /data/logs/pilot_T15_dedup_off.log

echo ""
echo "[5/5] Running pilot experiment (T15_typeA, dedup=on)..."
python /data/scripts/trace_replayer.py \
    --trace /data/traces/typeA/IBMObjectStoreTrace015Part0.typeA.mc.aug \
    --mapping /data/mappings/T15_typeA_mapping.csv \
    --dedup on \
    --speedup 1000 \
    --mode local \
    2>&1 | tee /data/logs/pilot_T15_dedup_on.log

echo ""
echo "==============================================="
echo " Pilot complete. Check /data/logs/ and /data/results/"
echo "==============================================="
