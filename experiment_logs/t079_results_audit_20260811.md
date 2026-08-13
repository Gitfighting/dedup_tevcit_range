# T079 result audit — 2026-08-11 16:40

## Process status
All experiment processes exited. load ≈ 0. No `chunk_objects` / `orchestrate` / `replay_dedup` running.

## Integrity checks (server)
| Check | Result |
|---|---|
| Σ chunk lengths == Σ object sizes | **Exact match** (623.398 GB, Δ=0 B) |
| Objects with chunk_sum ≠ size | **0 / 12582** |
| Unique chunks / bytes | 8,691,500 / **84.602 GB** |
| Global content dedup ratio | **86.43%** (1 − 84.6/623.4) |
| Baseline vs dedup op counters | **Identical** (same TTL path) |
| 2reg transfer $ vs 2338.05×$0.02 | **$46.7610 vs reported $46.7611** |
| 2reg dedup transfer $ vs 392.61×$0.02 | **$7.8522 exact** |
| Storage ≈ ΣGB-h × ($0.023×3/24) | baseline $308.2359≈$308.2361; dedup $43.4114≈$43.4112 |

## Manifest composition
| action | objects | bytes |
|---|---:|---:|
| trim | 7109 | 385.40 GB |
| extent | 708 | 184.65 GB |
| pad | 4182 | 18.86 GB |
| exact | 583 | 34.50 GB |

Source files serving >1 object: 8 files → 114.79 GB (18.4% of logical). Most dedup is **cross-file CDC similarity**, not only extent carving.

## Conclusion
Arithmetic and event counts are consistent; large savings track the measured 86% content redundancy under chunk-level billing. Savings are methodology-sensitive to homogeneous linux/ubuntu pool (expected for DedupAwareTevict on this dataset).
