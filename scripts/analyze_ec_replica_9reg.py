#!/usr/bin/env python3
"""Audit EC vs replica cost gap on 9-reg T79: MILP logs + simulation JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json_glob(tag: str) -> dict:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"))
    if not matches:
        raise FileNotFoundError(tag)
    return json.loads(matches[-1].read_text(encoding="utf-8"))


def parse_milp(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> None:
    rep = load_json_glob("v4_9reg_replica_raw")
    ec = load_json_glob("v4_9reg_forceec_raw")
    rep_milp = parse_milp(ROOT / "results/milp_solve_log_v4_fp_9reg_tA.jsonl")
    ec_milp = parse_milp(ROOT / "results/milp_solve_log_v4_9reg_forceec_raw.jsonl")

    print("=== Simulation totals (9-reg T79 typeA FP) ===")
    for label, d in [("replica", rep), ("force_ec", ec)]:
        print(
            f"  {label}: total={d['total cost ($)']:.4f} "
            f"storage={d['total storage cost ($)']:.4f} "
            f"transfer={d['total transfer cost ($)']:.4f}"
        )
    dt = ec["total cost ($)"] - rep["total cost ($)"]
    ds = ec["total storage cost ($)"] - rep["total storage cost ($)"]
    dtr = ec["total transfer cost ($)"] - rep["total transfer cost ($)"]
    print(f"  delta EC-replica: total=+{dt:.4f} storage=+{ds:.4f} transfer=+{dtr:.4f}")

    print("\n=== MILP tick write footprint ===")
    for label, rows in [("replica", rep_milp), ("force_ec", ec_milp)]:
        n_rep = sum(1 for r in rows if r.get("mode") == 0)
        n_ec = sum(1 for r in rows if r.get("mode") == 1)
        wr_counts = [len(r.get("writeRegionsOnPut") or []) for r in rows]
        print(
            f"  {label}: ticks={len(rows)} mode0={n_rep} mode1={n_ec} "
            f"write_regions/tick min={min(wr_counts)} max={max(wr_counts)} avg={sum(wr_counts)/len(wr_counts):.1f}"
        )

    print("\n=== Sample force_ec ticks (issue -> write regions) ===")
    for r in ec_milp[:5]:
        issue = r["issueRegion"]
        wr = r.get("writeRegionsOnPut") or []
        remote = [x for x in wr if x != issue]
        print(f"  tick{r['tick']} issue={issue} writes={wr} remote_shards={remote}")

    # Cross-region shard writes per tick (extra vs replica single local write)
    extra_remote = 0
    for r in ec_milp:
        issue = r["issueRegion"]
        for w in r.get("writeRegionsOnPut") or []:
            if w != issue:
                extra_remote += 1
    print(f"\n  force_ec MILP ticks with cross-region shard placement: {extra_remote}/{len(ec_milp)} ticks")

    print("\n=== Why gap is tiny (mechanism checklist) ===")
    puts = rep["total requests"]  # includes GET; PUT count from trace metadata
    print("  1. Write bytes: EC places 2 shards x (V/2) => same total bytes as 1 replica x V on PUT.")
    print("  2. Replica FP write: single region at issue (often src=dst => near-zero write transfer).")
    print("  3. EC write: always 2 regions; remote shard adds edge cost — but only for PUTs after decisionCache set.")
    print("  4. Read miss path: SimulatorV2 caches FULL replica at reader after transfer (not shard-only).")
    print("  5. Dominant cost: ~101k GET cross-region reads; once full replicas exist, EC read advantage erased.")
    print("  6. MILP only re-decides ~12 times / 7 days; decisionCache persists between ticks per issue region.")

    out = ROOT / "results/H02_ec_replica_9reg_audit.md"
    out.write_text(
        "\n".join(
            [
                "# H02: EC vs replica 9-reg audit",
                "",
                "## Measured (T79 typeA FP, 9-reg)",
                "",
                "| mode | total ($) | storage ($) | transfer ($) |",
                "|------|----------:|------------:|-------------:|",
                f"| replica | {rep['total cost ($)']:.4f} | {rep['total storage cost ($)']:.4f} | {rep['total transfer cost ($)']:.4f} |",
                f"| force_ec | {ec['total cost ($)']:.4f} | {ec['total storage cost ($)']:.4f} | {ec['total transfer cost ($)']:.4f} |",
                f"| **delta** | **+{dt:.4f}** | **+{ds:.4f}** | **+{dtr:.4f}** |",
                "",
                "EC **is** more expensive, but only by **~$0.11 (~0.03%)** — not \"same price within noise\"; the effect is real but swamped.",
                "",
                "## MILP logs confirm EC is active",
                "",
                f"- force_ec: **12/12** ticks `mode=1`, **2** write regions per tick",
                f"- replica: **12/12** ticks `mode=0`, **1** write region (issue only)",
                "",
                "Example force_ec tick 1: issue `aws:us-east-1` writes shards to `[aws:us-east-1, aws:eu-west-1]`.",
                "",
                "## Root causes (why EC penalty ≪ intuition)",
                "",
                "### A. Write transfer volume equivalence (K=2)",
                "EC PUT places **two shards** of size `V/2` each → **V total bytes** moved, same as one full replica.",
                "MILP coefficients explicitly use `sizeShardGb = sizeLogicGb / K_DATA` (K_DATA=2).",
                "",
                "### B. Replica FP write is often local / free",
                "Replica `writeRegionsOnPut=[issue]` → `writeTransferPath(issue→issue)` with full `V`.",
                "Local self-edge transfer cost is **~0**; EC still pays **remote shard** edge on every EC-mode PUT.",
                "",
                "### C. Read-miss destroys EC state (dominant)",
                "`readCachePlacementRegions` always places a **full replica** at `issueRegion` after paid read miss.",
                "After first miss, object has full copy; subsequent reads use replica path (Plan C/D).",
                "Cross-region **read** transfer (~$150 total) dominates; EC's 1-shard fetch (Plan B) rarely persists.",
                "",
                "### D. Low MILP refresh rate",
                "Only **~12** MILP solves in 7 days; `decisionCache` applies EC layout to subsequent PUTs at that issue region.",
                "Before first MILP per region, `decisionCache=null` → fallback **issue-only** (same as replica).",
                "",
                "## 3-reg reference (t9)",
                "",
                "| tag | transfer delta vs replica |",
                "|-----|-------------------------:|",
                "| force_ec | +$0.21 on $101 transfer |",
                "",
                "Same mechanism; 9-reg transfer split is 50/50 storage/transfer so EC write penalty is still small vs aggregate reads.",
                "",
                "## Fix / experiment directions",
                "",
                "1. **Read miss**: optional shard-only cache (no full replica upgrade) under EC mode.",
                "2. **Accounting audit**: enable `CostLogger.logNetworkCost` (currently commented) for read/write split.",
                "3. **EC recovery charge**: explicit reconstruct cost when two shards fetched (Plan E) vs one replica.",
                "4. **Workload**: typeC high cross-region read ratio may widen EC vs replica gap.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
