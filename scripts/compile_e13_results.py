#!/usr/bin/env python3
"""Compile E13 results from simulation_results_e13_*.json without re-running sims."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]


def milp_stats(tag: str) -> Dict[str, Any]:
    path = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not path.exists():
        return {"milp_ticks": 0, "ec_ticks": 0, "ec_rate": 0.0}
    ticks = ec = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ticks += 1
        if json.loads(line).get("mode") == 1:
            ec += 1
    return {"milp_ticks": ticks, "ec_ticks": ec, "ec_rate": (ec / ticks) if ticks else 0.0}


def main() -> None:
    rows: List[Dict[str, Any]] = []
    pat = re.compile(r"simulation_results_(e13_[^_]+(?:_[^_]+)*?)_\d{8}_\d{6}\.json")
    for jp in sorted(ROOT.glob("simulation_results_e13_*.json")):
        m = pat.match(jp.name)
        if not m:
            continue
        tag = m.group(1)
        j = json.loads(jp.read_text(encoding="utf-8"))
        # infer fields from tag: e13_{policy}_{trace_id...}
        parts = tag.split("_", 2)  # e13, policy..., rest
        body = tag[len("e13_") :]
        if body.startswith("tevict_"):
            policy = "tevict"
            trace_id = body[len("tevict_") :]
        elif body.startswith("v4_delta_ec_"):
            policy = "v4_delta_ec"
            trace_id = body[len("v4_delta_ec_") :]
        else:
            continue
        experiment = "saturation" if "typeA_p" in trace_id else "cross_trace"
        rows.append(
            {
                "experiment": experiment,
                "policy": policy,
                "trace_id": trace_id,
                "total_cost": j["total cost ($)"],
                "storage_cost": j["total storage cost ($)"],
                "transfer_cost": j["total transfer cost ($)"],
                **milp_stats(tag),
                "tag": tag,
                "json": jp.name,
            }
        )

    tev_map = {}
    for r in rows:
        if r["policy"] == "tevict":
            tev_map[f"{r['experiment']}|{r['trace_id']}"] = float(r["total_cost"])
    for r in rows:
        key = f"{r['experiment']}|{r['trace_id']}"
        tev = tev_map.get(key)
        r["vs_tevict_pct"] = ((float(r["total_cost"]) - tev) / tev * 100.0) if tev else 0.0

    out = ROOT / "results" / "E13_delta_saturation_summary.csv"
    fields = [
        "experiment",
        "policy",
        "trace_id",
        "total_cost",
        "storage_cost",
        "transfer_cost",
        "vs_tevict_pct",
        "milp_ticks",
        "ec_rate",
        "tag",
        "json",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["experiment"], x["trace_id"], x["policy"])))
    print(f"Wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
