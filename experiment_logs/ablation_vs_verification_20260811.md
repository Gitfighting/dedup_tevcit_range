# Ablation vs Verification — 2026-08-11

> **Server**: `118.31.175.106` (current ECS) — **not** the old `121.40.28.243`  
> **Related**: `dedup_aware_tevict_strategy_20260809.md` (G1–G8), `pipeline_status_and_t079_analysis_20260811.md`  
> **OCP**: `experiment_logs/ocp_prototype_extension_20260811.md` — **保留作者策略，只扩展**  
> **Scripts**: `ablation_prototype_plan.py`, `run_prototype_ablation.py`, `bootstrap_ablation_prototype.py`

---

## 1. Current cloud job = VERIFICATION (keep running)

| Item | Value |
|------|--------|
| **What** | Offline Stage B cost replay: **baseline vs dedup** |
| **Tool** | `scripts/cloud/replay_dedup.py` (via `master_pipeline.py` / orchestrator) |
| **TTL** | Fixed **T_even / 2** for both arms (Tevict cold-start default) |
| **Data** | `/data/dedup_exp/` (chunk stores, prep, results) |
| **Isolates** | Chunk-level storage + partial transfer billing vs full-object billing, **holding replica lifetimes identical** |
| **Does not run** | The five prototype placement policies; no HOT/DEDUP state machine |

**Action**: Leave the verification pipeline alone. Do not kill chunk/prep/replay processes, reuse its ports, or write into `/data/dedup_exp/` for ablation.

Status snapshot (see pipeline log docs): T079 ✅, T065 ✅; T078 / T015 / T029 progressing under the same verification design.

---

## 2. Next = PROTOTYPE ablation

| Item | Value |
|------|--------|
| **Code** | `skystore_python/artifact-eval-main/prototype/` |
| **Policies** | `always_evict`, `always_store`, `t_even`, `skystore`, `dedup_aware_tevict` |
| **Registry** | `prototype/store-server/operations/policy/placement_policy/get_placement.py` |
| **Knob** | `PUT_POLICY` (+ `skystore_cli.py init --put_policy`) |
| **Cost end** | `POST /clean_out_remaining` → storage / network (+ `stats` for dedup) |
| **Remote root** | `/data/ablation_exp/` (separate from verification) |

This is the live store-server + s3-proxy + local s3 path, not the offline `replay_dedup` counterfactual.

---

## 3. Map to G1–G8

From `experiment_logs/dedup_aware_tevict_strategy_20260809.md` §5.1:

| Group | Dedup | Placement policy | Role |
|-------|:-----:|------------------|------|
| **G1** | ❌ | `always_evict` | Pure evict baseline |
| **G2** | ❌ | `always_store` | Pure store baseline |
| **G3** | ❌ | `t_even` | Fixed break-even TTL |
| **G4** | ❌ | `skystore` | Adaptive TTL (SkyStore) |
| **G5** | ✅ | `always_evict` | Dedup, no cache |
| **G6** | ✅ | `always_store` | Dedup + always cache |
| **G7** | ✅ | `t_even` | Dedup + fixed T_even |
| **G8** | ✅ | `dedup_aware_tevict` | **Paper policy** (adaptive + HOT/DEDUP) |

**Practical note:** Today only `dedup_aware_tevict` triggers live `DedupEngine` on PUT. G1–G4 are “policy only.” G5–G7 need either a dedup toggle for the other policies or an explicit wiring change — until then, treat the **5-policy sweep** as G1–G4 + G8, and schedule G5–G7 after the dedup import path is shared.

Comparison axes (§5.2): dedup on/off (G1→G5, G2→G6), adaptive vs fixed TTL (G7→G8), synergy (G6→G8), vs SkyStore (G4→G8).

---

## 4. Prerequisites / gaps (from prototype exploration)

Fix or work around these before claiming full G1–G8 numbers:

| Gap | Why it matters |
|-----|----------------|
| **`TRACE_FILE_PATH`** | `skystore` / `dedup_aware_tevict` (and `always_store`) index timestamps from a trace at init. Default path under `experiment/trace/` is missing in-repo. Export `TRACE_FILE_PATH` to the matching `.mc-aug` (or converted `.prototype`) before start. |
| **`operations.dedup` import** | `put.py` does `from operations.dedup import DedupEngine`, but code lives at `prototype/dedup/`, not `store-server/operations/dedup/`. Need symlink/copy (or import fix) or G8 chunking fails at runtime. |
| **`blake3` / `lmdb`** | Used by dedup engine / index; **not** listed in `store-server/requirements.txt`. Install on the ablation venv. |
| **Replay driver** | `run_client.py` expects `send.py` (missing). Need a small `.mc-aug` → `aws s3api` driver against the local proxy (port separate from verification). |
| **`clean_out_remaining`** | Cost is finalized only after this POST. Ablation must call it and save JSON; latency `metrics.json` alone is not dollar cost. |

Also watch: s3-proxy pull-header name mismatches (`teven` vs `t_even`); incomplete network-cost accumulation for some non-dedup policies; single-machine local cache path (`/tmp/s3-local-cache`) assumes `s3s-fs`.

---

## 5. Suggested run order

Minimize risk and disk/CPU contention with verification:

1. **Small 2reg typeE first:** `T065`, `T078` × **5 policies**  
2. **Then** `T079` × 5 policies (2reg typeE)  
3. **Then 9reg** (typeA–D where applicable; usually skip T029 for 9reg)  
4. **Heavy last:** `T015`, then `T029` (largest / slowest)

Per cell: upload (once) → set `PUT_POLICY` + `TRACE_FILE_PATH` → init → register → replay → `clean_out_remaining` → collect JSON under `/data/ablation_exp/results/`.

Prefer dry-run via:

```bash
python scripts/cloud/run_prototype_ablation.py --tag T065 --policy always_evict --dry-run
python scripts/cloud/ablation_prototype_plan.py --checklist
```

---

## 6. Concurrency: do not kill verification

| Concern | Rule |
|---------|------|
| **Dirs** | Ablation → `/data/ablation_exp/`; verification → `/data/dedup_exp/` |
| **Ports** | If both run, give store-server / s3-proxy / s3s-fs **distinct** ports; never steal verification listeners |
| **Processes** | Do not stop `master_pipeline`, chunk workers, or `replay_dedup` jobs |
| **Disk** | Check `df -h /data` before large ablations; reclaim only ablation scratch, not verification chunk caches |
| **Old server** | Docs/scripts that still say `121.40.28.243` are stale for this work — use **`118.31.175.106`** |

`run_prototype_ablation.py` real mode is preflight-only (SSH disk + look for prototype dirs). It does **not** start or kill services yet.

---

## Quick links

| Artifact | Path |
|----------|------|
| Strategy / G1–G8 | `experiment_logs/dedup_aware_tevict_strategy_20260809.md` |
| Plan script | `scripts/cloud/ablation_prototype_plan.py` |
| Launcher stub | `scripts/cloud/run_prototype_ablation.py` |
| Verification replay | `scripts/cloud/replay_dedup.py` |
| Policy registry | `prototype/.../get_placement.py` |
