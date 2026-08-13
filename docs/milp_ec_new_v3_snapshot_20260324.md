# MILP EC New V3 Snapshot (2026-03-24)

## Scope
- Policy: `milp_ec_new_v3`
- Regions: 9-reg
- Base mode: `setbase=true`
- Goal: stabilize trigger timing, avoid request-level MILP explosion, and run T79 comparison.

## Key Runtime Config
- Config file: `config/milp_ec_new_v3_9reg_smoke.yaml`
- Effective key params:
  - `placement_policy: milp_ec_new_v3`
  - `transfer_policy: cheapest`
  - `window_size: -1`
  - `milp_time_limit_sec: 2.0`
  - `milp_mip_gap: 0.05`
  - `milp_strict_fail: true`
  - `milp_trigger_mode: HISTOGRAM_TICK` (reserved/extensible)

## Code Changes Included in This Snapshot
- `src/main/java/org/example/SimulatorV2.java`
  - added extension hooks (`onTtlComputed`, `onRequestReady`) for OCP-style policy-specific timing.
  - moved `onRequestReady` invocation to after logical object creation/update.
- `src/main/java/org/example/SimulatorV3.java`
  - V3-specific MILP trigger moved to `onRequestReady`.
- `src/main/java/org/example/src/placement_policy/PolicyMilpECnewV3.java`
  - independent TTL/hist path retained.
  - MILP trigger throttled by region+time bucket (1 hour) via `runMilpForIssueRegionIfDue`.
  - MILP coefficients consume TTL snapshot (avoid second `getTevict` pass during coefficient build).
  - fallback for missing node API prices:
    - `pricePut -> 5e-6`
    - `priceGet -> 4e-7`
- `src/main/java/org/example/src/transfer_policy/CheapestTransferV3.java`
  - robust fallback to baseline read path when shard state is infeasible (avoid hard abort in long-run full trace).

## Quick Eval (10k subset)
- V3 result: `simulation_results_quick_v3_t79_10k_20260324_154826.json`
- Baseline result: `simulation_results_quick_tr_t79_10k_20260324_154835.json`

## Formal Eval (full T79)
- V3 result: `simulation_results_formal_v3_9reg_T79_fix2_20260324_155248.json`
- Baseline result: `simulation_results_formal_tr_9reg_T79_20260324_155006.json`

### Formal Cost Comparison
- Total cost:
  - V3: `419.398758912`
  - Baseline: `498.140838625`
  - Delta: `-78.742079713` (~`-15.81%`)
- Transfer cost:
  - V3: `152.719241418`
  - Baseline: `125.533490231`
- Storage cost:
  - V3: `266.011090854`
  - Baseline: `372.016952074`
- Request cost:
  - V3: `0.66842664`
  - Baseline: `0.59039632`

## Notes
- Full-run path was stabilized after fixing two V3 read-path failure modes:
  - no valid source fallback
  - single-shard unreconstructable fallback
- MILP solve count can be inspected in `milp_ec_new_v3.log` (`Optimize a model` occurrences).
