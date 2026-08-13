# Fig.5 matrix status (interrupted by ECS SSH hang)

Date: 2026-08-12 ~ 2026-08-13

## Goal
- 2reg + 9reg × 6 policies × T15/T29/T65/T78/T79 (60 cells)
- Policies: always_evict, always_store, teven, tevict_ranges, optimal, dedup_aware_tevict_ranges
- Ratio denominator: **dedup_aware_tevict_ranges (ours)**
- Cost metric: total without request ($)

## Progress before SSH died
- Smoke OK: 2reg always_evict T79 = $356.37
- Matrix running with 8 workers under `/data/sim_exp/python_simulation`
- Last observed: **ok≈18, fail=1, jsons≈18**, still running T29/tevict jobs
- Sample completed (2reg):
  - always_evict T65 $55.06, T79 $356.37, T15 $92.79, T78 $62.91
  - always_store T65 $4.39, T79 $540.17, T15 $141.78, T78 $24.02
  - teven T65 $6.32, T79 $340.83, T15 $95.95, T78 $17.81
  - tevict_ranges T65 $4.39, T15 $92.02
  - optimal T65 $4.56, T15 $90.90, T79 $323.48

## Blocker
- ECS `118.31.175.106` pings OK but **sshd banner exchange times out for >1.5h**
- Likely OOM / thrash from 8× parallel T29 sims (+ online ablation)
- Need **Aliyun console reboot / VNC** to restore SSH

## After reboot (resume)
```bash
cd /data/sim_exp/python_simulation
# resume unfinished only (no --force), lean workers
nohup /data/venv/bin/python -u run_fig5_matrix_parallel.py --workers 3 \
  >> results_fig5_matrix/nohup_resume.log 2>&1 &
# when done:
/data/venv/bin/python -u analyze_fig5_matrix.py
```

Local watcher: `scripts/cloud/recover_fig5_watch.py` (still polling).
Artifacts target: `experiment_logs/fig5_matrix_20260812/`
