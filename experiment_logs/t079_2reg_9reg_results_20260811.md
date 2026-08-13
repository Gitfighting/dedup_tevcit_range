# T079 DedupAwareTevict experiment results — 2026-08-11

Timestamp: 2026-08-11 16:11 CST (server).

## Pipeline
1. Zero-error manifest → FastCDC (2K/8K/64K) + BLAKE3 → 69.9M chunks / 12,582 objects in 56.3 min.
2. Prep: 8.69M unique chunks, **84.6 GB unique bytes** (logical PUT base ≈ 580.6 GB).
3. Replay: Tevict-style TTL (`teven/2`), baseline object-replica vs chunk-level dedup. 10 jobs, all OK.

## Cost (USD, full T079 trace)

| Variant | Policy | Storage | Transfer | Total | Xfer GB | Base stored GB |
|---|---|---:|---:|---:|---:|---:|
| typeE 2reg | baseline | 308.24 | 46.76 | **355.13** | 2338 | 580.6 |
| typeE 2reg | dedup | 43.41 | 7.85 | **51.40** | 393 | 78.8 |
| typeA 9reg | baseline | 351.22 | 147.81 | **499.17** | 2331 | 580.6 |
| typeA 9reg | dedup | 111.40 | 21.83 | **133.36** | 346 | 210.9 |
| typeB 9reg | baseline | 313.43 | 66.31 | **379.86** | 911 | 580.6 |
| typeB 9reg | dedup | 104.59 | 14.11 | **118.82** | 184 | 211.8 |
| typeC 9reg | baseline | 314.00 | 67.22 | **381.34** | 1081 | 580.6 |
| typeC 9reg | dedup | 103.55 | 6.91 | **110.58** | 121 | 217.1 |
| typeD 9reg | baseline | 347.85 | 148.54 | **496.53** | 2333 | 580.6 |
| typeD 9reg | dedup | 108.14 | 21.71 | **129.99** | 378 | 207.9 |

## Savings (dedup vs baseline)

| Variant | Baseline $ | Dedup $ | Save | % |
|---|---:|---:|---:|---:|
| **2reg (typeE)** | 355.13 | 51.40 | **303.73** | **85.5%** |
| 9reg typeA | 499.17 | 133.36 | 365.81 | 73.3% |
| 9reg typeB | 379.86 | 118.82 | 261.04 | 68.7% |
| 9reg typeC | 381.34 | 110.58 | 270.76 | 71.0% |
| 9reg typeD | 496.53 | 129.99 | 366.53 | 73.8% |

Object-level hit/miss counts are identical across policies (same TTL logic); savings come from chunk-level storage footprint and partial-chunk transfer on remote GET.

Raw JSON: `experiment_logs/results_T079/`.
