# 9reg typeB/C/D 预留说明

## 当前
- **2reg**：typeE（已完成）
- **9reg**：正在跑 **typeA only**（不要杀）

## 已预留
- ECS 路径：
  - `/data/sim_exp/python_simulation/traces9/typeB/*.typeB.mc.aug`
  - `/data/sim_exp/python_simulation/traces9/typeC/*.typeC.mc.aug`
  - `/data/sim_exp/python_simulation/traces9/typeD/*.typeD.mc.aug`
- runner 已支持：`--types A,B,C,D`
- typeA 结果文件名保持兼容：`fig5_9reg_{policy}_{tid}.json`
- typeB/C/D 结果文件名：`fig5_9reg_type{B|C|D}_{policy}_{tid}.json`

## CPU 策略（约 85%）
- 8 核默认 workers = `round(8*0.85)=7`，且 `<= nproc-1`
- T29 仍 `max-heavy=2`，避免 OOM

## typeA 结束后启动 B/C/D（示例）
```bash
cd /data/sim_exp/python_simulation
nohup /data/venv/bin/python -u run_fig5_matrix_parallel.py \
  --setups 9reg --types B,C,D --workers 7 --max-heavy 2 \
  >> results_fig5_matrix/nohup_9reg_bcd.log 2>&1 &
```
或一次跑齐：`--types A,B,C,D`（A 已完成会 skip）

## 注意
- dedup_aware 的 chunk store 目前按 trace 号 `/data/dedup_exp/T0xx`；
  若 B/C/D 对象集合与 A 不同，可能需为各 type 单独准备 chunk（否则 dedup 格可能不准/失败）。
