# Fig.5-style Matrix Analysis (DedupAware as denominator)

- Denominator: **dedup_aware_tevict_ranges** (ours)
- Paper Fig.5 used SkyStore(tevict_ranges) as denominator; here ratios are `cost(policy) / cost(DedupAware)`.
- Costs use **total without request** (paper ignores PUT/GET op fees).
- 2reg: typeE Fixed-Base; 9reg: typeA Fixed-Base multi-cloud.

## 2reg

### Absolute cost ($ without request)

| Trace | Always Evict | Always Store | Teven | SkyStore | Optimal | DedupAware (ours) |
|---|---|---|---|---|---|---|
| T15 | 92.788 | 141.775 | 95.953 | 92.016 | 90.897 | 26.083 |
| T29 | 4.602 | 7.151 | 5.025 | 4.621 | 4.593 | 0.221 |
| T65 | 55.056 | 4.389 | 6.315 | 4.385 | 4.560 | 3.205 |
| T78 | 62.907 | 24.023 | 17.807 | 16.793 | 16.297 | 4.046 |
| T79 | 356.372 | 540.173 | 340.834 | 337.018 | 323.478 | 76.292 |

### Ratio vs DedupAware (ours=1.0)

| Trace | Always Evict | Always Store | Teven | SkyStore | Optimal | DedupAware (ours) |
|---|---|---|---|---|---|---|
| T15 | 3.56× | 5.44× | 3.68× | 3.53× | 3.48× | 1.00× |
| T29 | 20.79× | 32.31× | 22.70× | 20.88× | 20.75× | 1.00× |
| T65 | 17.18× | 1.37× | 1.97× | 1.37× | 1.42× | 1.00× |
| T78 | 15.55× | 5.94× | 4.40× | 4.15× | 4.03× | 1.00× |
| T79 | 4.67× | 7.08× | 4.47× | 4.42× | 4.24× | 1.00× |

### Component split (storage % / transfer %) for every policy

| Trace | Policy | Total$ | Storage$ | Transfer$ | Stor% | Xfer% | vs Ours |
|---|---|---:|---:|---:|---:|---:|---:|
| T15 | Always Evict | 92.788 | 85.640 | 7.148 | 92.3% | 7.7% | 3.56× |
| T15 | Always Store | 141.775 | 136.631 | 5.144 | 96.4% | 3.6% | 5.44× |
| T15 | Teven | 95.953 | 90.806 | 5.147 | 94.6% | 5.4% | 3.68× |
| T15 | SkyStore | 92.016 | 86.714 | 5.302 | 94.2% | 5.8% | 3.53× |
| T15 | Optimal | 90.897 | 85.750 | 5.147 | 94.3% | 5.7% | 3.48× |
| T15 | DedupAware (ours) | 26.083 | 22.904 | 3.179 | 87.8% | 12.2% | 1.00× |
| T29 | Always Evict | 4.602 | 4.145 | 0.457 | 90.1% | 9.9% | 20.79× |
| T29 | Always Store | 7.151 | 6.957 | 0.194 | 97.3% | 2.7% | 32.31× |
| T29 | Teven | 5.025 | 4.581 | 0.444 | 91.2% | 8.8% | 22.70× |
| T29 | SkyStore | 4.621 | 4.175 | 0.446 | 90.4% | 9.6% | 20.88× |
| T29 | Optimal | 4.593 | 4.149 | 0.444 | 90.3% | 9.7% | 20.75× |
| T29 | DedupAware (ours) | 0.221 | 0.188 | 0.033 | 84.9% | 15.1% | 1.00× |
| T65 | Always Evict | 55.056 | 2.160 | 52.896 | 3.9% | 96.1% | 17.18× |
| T65 | Always Store | 4.389 | 4.282 | 0.107 | 97.6% | 2.4% | 1.37× |
| T65 | Teven | 6.315 | 4.024 | 2.292 | 63.7% | 36.3% | 1.97× |
| T65 | SkyStore | 4.385 | 4.245 | 0.141 | 96.8% | 3.2% | 1.37× |
| T65 | Optimal | 4.560 | 2.268 | 2.292 | 49.7% | 50.3% | 1.42× |
| T65 | DedupAware (ours) | 3.205 | 3.083 | 0.123 | 96.2% | 3.8% | 1.00× |
| T78 | Always Evict | 62.907 | 13.978 | 48.929 | 22.2% | 77.8% | 15.55× |
| T78 | Always Store | 24.023 | 23.440 | 0.583 | 97.6% | 2.4% | 5.94× |
| T78 | Teven | 17.807 | 15.921 | 1.886 | 89.4% | 10.6% | 4.40× |
| T78 | SkyStore | 16.793 | 14.712 | 2.081 | 87.6% | 12.4% | 4.15× |
| T78 | Optimal | 16.297 | 14.448 | 1.849 | 88.7% | 11.3% | 4.03× |
| T78 | DedupAware (ours) | 4.046 | 2.742 | 1.305 | 67.8% | 32.2% | 1.00× |
| T79 | Always Evict | 356.372 | 279.550 | 76.822 | 78.4% | 21.6% | 4.67× |
| T79 | Always Store | 540.173 | 528.562 | 11.610 | 97.9% | 2.1% | 7.08× |
| T79 | Teven | 340.834 | 323.020 | 17.814 | 94.8% | 5.2% | 4.47× |
| T79 | SkyStore | 337.018 | 309.118 | 27.899 | 91.7% | 8.3% | 4.42× |
| T79 | Optimal | 323.478 | 305.664 | 17.814 | 94.5% | 5.5% | 4.24× |
| T79 | DedupAware (ours) | 76.292 | 52.991 | 23.301 | 69.5% | 30.5% | 1.00× |

### Delta vs DedupAware (where the $ goes)

| Trace | Policy | ΔTotal$ | ΔStorage$ | ΔTransfer$ | Driver |
|---|---|---:|---:|---:|---|
| T15 | Always Evict | +66.705 | +62.736 | +3.969 | storage-heavy gap |
| T15 | Always Store | +115.692 | +113.727 | +1.965 | storage-heavy gap |
| T15 | Teven | +69.870 | +67.903 | +1.968 | storage-heavy gap |
| T15 | SkyStore | +65.933 | +63.810 | +2.123 | storage-heavy gap |
| T15 | Optimal | +64.814 | +62.846 | +1.968 | storage-heavy gap |
| T29 | Always Evict | +4.381 | +3.957 | +0.423 | storage-heavy gap |
| T29 | Always Store | +6.929 | +6.769 | +0.161 | storage-heavy gap |
| T29 | Teven | +4.804 | +4.393 | +0.411 | storage-heavy gap |
| T29 | SkyStore | +4.400 | +3.987 | +0.412 | storage-heavy gap |
| T29 | Optimal | +4.372 | +3.961 | +0.411 | storage-heavy gap |
| T65 | Always Evict | +51.850 | -0.922 | +52.773 | transfer-heavy gap |
| T65 | Always Store | +1.184 | +1.199 | -0.015 | storage-heavy gap |
| T65 | Teven | +3.110 | +0.941 | +2.169 | transfer-heavy gap |
| T65 | SkyStore | +1.180 | +1.162 | +0.018 | storage-heavy gap |
| T65 | Optimal | +1.355 | -0.814 | +2.169 | transfer-heavy gap |
| T78 | Always Evict | +58.861 | +11.237 | +47.624 | transfer-heavy gap |
| T78 | Always Store | +19.976 | +20.698 | -0.722 | storage-heavy gap |
| T78 | Teven | +13.761 | +13.179 | +0.582 | storage-heavy gap |
| T78 | SkyStore | +12.747 | +11.970 | +0.776 | storage-heavy gap |
| T78 | Optimal | +12.250 | +11.706 | +0.544 | storage-heavy gap |
| T79 | Always Evict | +280.080 | +226.559 | +53.521 | storage-heavy gap |
| T79 | Always Store | +463.881 | +475.571 | -11.691 | storage-heavy gap |
| T79 | Teven | +264.542 | +270.029 | -5.487 | storage-heavy gap |
| T79 | SkyStore | +260.726 | +256.127 | +4.598 | storage-heavy gap |
| T79 | Optimal | +247.186 | +252.673 | -5.487 | storage-heavy gap |

### Why costs change (interpretation)

1. **Always Evict**: almost no cache → transfer dominates; ratio ≫1 on hot/read-heavy traces (T65/T78) because every remote GET pays full egress.
2. **Always Store**: keeps replicas forever → storage dominates on long tails (T79); can beat SkyStore slightly on very hot traces but loses when objects go cold.
3. **Teven**: fixed break-even TTL; safer than Always* but cannot adapt to burstiness → mid-range ratios.
4. **SkyStore (tevict_ranges)**: adaptive TTL from inter-arrival histogram; usually best among author baselines; still stores full objects on cache hits.
5. **Optimal**: clairvoyant T_next vs T_even; lower bound / oracle (not deployable).
6. **DedupAware (ours)**: same adaptive TTL family, but HOT vs DEDUP using `τ=(1-L)×T_even`; DEDUP pays missing-chunk transfer and stores unique chunks + manifest → lower storage and often lower transfer when locality exists. Savings vs SkyStore ≈ reduction in full-object replicas / full egress.
7. **Read the Δ table**: if ΔTransfer dominates, the gap is cross-region GET volume; if ΔStorage dominates, the gap is replica/chunk footprint over time.

## 9reg

### Absolute cost ($ without request)

| Trace | Always Evict | Always Store | Teven | SkyStore | Optimal | DedupAware (ours) |
|---|---|---|---|---|---|---|
| T15 | 106.632 | 161.552 | 124.172 | — | 105.442 | 61.583 |
| T29 | — | — | — | — | — | 0.910 |
| T65 | 210.939 | 19.267 | 29.592 | — | 16.065 | 16.379 |
| T78 | — | — | — | — | — | 18.680 |
| T79 | — | — | — | — | — | 156.575 |

### Ratio vs DedupAware (ours=1.0)

| Trace | Always Evict | Always Store | Teven | SkyStore | Optimal | DedupAware (ours) |
|---|---|---|---|---|---|---|
| T15 | 1.73× | 2.62× | 2.02× | — | 1.71× | 1.00× |
| T29 | — | — | — | — | — | 1.00× |
| T65 | 12.88× | 1.18× | 1.81× | — | 0.98× | 1.00× |
| T78 | — | — | — | — | — | 1.00× |
| T79 | — | — | — | — | — | 1.00× |

### Component split (storage % / transfer %) for every policy

| Trace | Policy | Total$ | Storage$ | Transfer$ | Stor% | Xfer% | vs Ours |
|---|---|---:|---:|---:|---:|---:|---:|
| T15 | Always Evict | 106.632 | 79.906 | 26.726 | 74.9% | 25.1% | 1.73× |
| T15 | Always Store | 161.552 | 137.330 | 24.221 | 85.0% | 15.0% | 2.62× |
| T15 | Teven | 124.172 | 99.947 | 24.226 | 80.5% | 19.5% | 2.02× |
| T15 | Optimal | 105.442 | 79.930 | 25.512 | 75.8% | 24.2% | 1.71× |
| T15 | DedupAware (ours) | 61.583 | 48.507 | 13.076 | 78.8% | 21.2% | 1.00× |
| T29 | DedupAware (ours) | 0.910 | 0.837 | 0.073 | 91.9% | 8.1% | 1.00× |
| T65 | Always Evict | 210.939 | 1.933 | 209.006 | 0.9% | 99.1% | 12.88× |
| T65 | Always Store | 19.267 | 17.465 | 1.802 | 90.6% | 9.4% | 1.18× |
| T65 | Teven | 29.592 | 13.603 | 15.989 | 46.0% | 54.0% | 1.81× |
| T65 | Optimal | 16.065 | 8.877 | 7.187 | 55.3% | 44.7% | 0.98× |
| T65 | DedupAware (ours) | 16.379 | 13.480 | 2.898 | 82.3% | 17.7% | 1.00× |
| T78 | DedupAware (ours) | 18.680 | 10.338 | 8.342 | 55.3% | 44.7% | 1.00× |
| T79 | DedupAware (ours) | 156.575 | 113.343 | 43.232 | 72.4% | 27.6% | 1.00× |

### Delta vs DedupAware (where the $ goes)

| Trace | Policy | ΔTotal$ | ΔStorage$ | ΔTransfer$ | Driver |
|---|---|---:|---:|---:|---|
| T15 | Always Evict | +45.049 | +31.399 | +13.650 | storage-heavy gap |
| T15 | Always Store | +99.969 | +88.824 | +11.145 | storage-heavy gap |
| T15 | Teven | +62.589 | +51.440 | +11.150 | storage-heavy gap |
| T15 | Optimal | +43.860 | +31.424 | +12.436 | storage-heavy gap |
| T65 | Always Evict | +194.560 | -11.547 | +206.107 | transfer-heavy gap |
| T65 | Always Store | +2.888 | +3.984 | -1.097 | storage-heavy gap |
| T65 | Teven | +13.213 | +0.123 | +13.090 | transfer-heavy gap |
| T65 | Optimal | -0.314 | -4.603 | +4.289 | beats ours (storage savings lost elsewhere) |

### Why costs change (interpretation)

1. **Always Evict**: almost no cache → transfer dominates; ratio ≫1 on hot/read-heavy traces (T65/T78) because every remote GET pays full egress.
2. **Always Store**: keeps replicas forever → storage dominates on long tails (T79); can beat SkyStore slightly on very hot traces but loses when objects go cold.
3. **Teven**: fixed break-even TTL; safer than Always* but cannot adapt to burstiness → mid-range ratios.
4. **SkyStore (tevict_ranges)**: adaptive TTL from inter-arrival histogram; usually best among author baselines; still stores full objects on cache hits.
5. **Optimal**: clairvoyant T_next vs T_even; lower bound / oracle (not deployable).
6. **DedupAware (ours)**: same adaptive TTL family, but HOT vs DEDUP using `τ=(1-L)×T_even`; DEDUP pays missing-chunk transfer and stores unique chunks + manifest → lower storage and often lower transfer when locality exists. Savings vs SkyStore ≈ reduction in full-object replicas / full egress.
7. **Read the Δ table**: if ΔTransfer dominates, the gap is cross-region GET volume; if ΔStorage dominates, the gap is replica/chunk footprint over time.

## Figures

- `fig5_2reg_ratios.png`, `fig5_9reg_ratios.png` — ratio bars
- `fig5_2reg_components.png`, `fig5_9reg_components.png` — stor/xfer
