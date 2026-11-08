# 2reg Fig.5 — ChunkTevict = 1

- Mode: **FB**, typeE, traces T15 / T65 / T78 / T79 (no T29).
- Denominator: **chunk_tevict**.
- Baselines: Always Evict, Always Store, Teven, SkyStore (`tevict_ranges`), Optimal.
- Excluded: old DedupAware / tevict_ranges_dedup (code was wrong).
- Baseline numbers: `experiment_logs/fig5_matrix_20260812`.
- ChunkTevict: `experiment_logs/chunk_tevict_20260815` (2026-08-15 rerun).

## Absolute cost ($ without request)

| Trace | Always Evict | Always Store | Teven | SkyStore | Optimal | ChunkTevict |
|---|---:|---:|---:|---:|---:|---:|
| T15 | 92.788 | 141.775 | 95.953 | 92.016 | 90.897 | 25.219 |
| T65 | 55.056 | 4.389 | 6.315 | 4.385 | 4.560 | 2.010 |
| T78 | 62.907 | 24.023 | 17.807 | 16.793 | 16.297 | 2.504 |
| T79 | 356.372 | 540.173 | 340.834 | 337.018 | 323.478 | 51.917 |

## Ratio vs ChunkTevict (=1.0)

| Trace | Always Evict | Always Store | Teven | SkyStore | Optimal | ChunkTevict |
|---|---:|---:|---:|---:|---:|---:|
| T15 | 3.68× | 5.62× | 3.80× | 3.65× | 3.60× | 1.00× |
| T65 | 27.39× | 2.18× | 3.14× | 2.18× | 2.27× | 1.00× |
| T78 | 25.12× | 9.59× | 7.11× | 6.71× | 6.51× | 1.00× |
| T79 | 6.86× | 10.40× | 6.56× | 6.49× | 6.23× | 1.00× |

## Storage / transfer split

| Trace | Policy | Total$ | Storage$ | Transfer$ | Stor% | Xfer% | vs ChunkTevict |
|---|---|---:|---:|---:|---:|---:|---:|
| T15 | Always Evict | 92.788 | 85.640 | 7.148 | 92.3% | 7.7% | 3.68× |
| T15 | Always Store | 141.775 | 136.631 | 5.144 | 96.4% | 3.6% | 5.62× |
| T15 | Teven | 95.953 | 90.806 | 5.147 | 94.6% | 5.4% | 3.80× |
| T15 | SkyStore | 92.016 | 86.714 | 5.302 | 94.2% | 5.8% | 3.65× |
| T15 | Optimal | 90.897 | 85.750 | 5.147 | 94.3% | 5.7% | 3.60× |
| T15 | ChunkTevict | 25.219 | 22.118 | 3.101 | 87.7% | 12.3% | 1.00× |
| T65 | Always Evict | 55.056 | 2.160 | 52.896 | 3.9% | 96.1% | 27.39× |
| T65 | Always Store | 4.389 | 4.282 | 0.107 | 97.6% | 2.4% | 2.18× |
| T65 | Teven | 6.315 | 4.024 | 2.292 | 63.7% | 36.3% | 3.14× |
| T65 | SkyStore | 4.385 | 4.245 | 0.141 | 96.8% | 3.2% | 2.18× |
| T65 | Optimal | 4.560 | 2.268 | 2.292 | 49.7% | 50.3% | 2.27× |
| T65 | ChunkTevict | 2.010 | 1.953 | 0.057 | 97.2% | 2.8% | 1.00× |
| T78 | Always Evict | 62.907 | 13.978 | 48.929 | 22.2% | 77.8% | 25.12× |
| T78 | Always Store | 24.023 | 23.440 | 0.583 | 97.6% | 2.4% | 9.59× |
| T78 | Teven | 17.807 | 15.921 | 1.886 | 89.4% | 10.6% | 7.11× |
| T78 | SkyStore | 16.793 | 14.712 | 2.081 | 87.6% | 12.4% | 6.71× |
| T78 | Optimal | 16.297 | 14.448 | 1.849 | 88.7% | 11.3% | 6.51× |
| T78 | ChunkTevict | 2.504 | 2.169 | 0.335 | 86.6% | 13.4% | 1.00× |
| T79 | Always Evict | 356.372 | 279.550 | 76.822 | 78.4% | 21.6% | 6.86× |
| T79 | Always Store | 540.173 | 528.562 | 11.610 | 97.9% | 2.1% | 10.40× |
| T79 | Teven | 340.834 | 323.020 | 17.814 | 94.8% | 5.2% | 6.56× |
| T79 | SkyStore | 337.018 | 309.118 | 27.899 | 91.7% | 8.3% | 6.49× |
| T79 | Optimal | 323.478 | 305.664 | 17.814 | 94.5% | 5.5% | 6.23× |
| T79 | ChunkTevict | 51.917 | 45.016 | 6.901 | 86.7% | 13.3% | 1.00× |
