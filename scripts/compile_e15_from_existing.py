#!/usr/bin/env python3
"""Merge E14/E15 simulation JSONs into E15_full_matrix_summary.csv (resume-safe)."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

PAT = re.compile(
    r"simulation_results_(e1[45]_[^_]+(?:_[^_]+)*?)_\d{8}_\d{6}\.json"
)


def parse_tag(tag: str) -> Optional[Dict[str, str]]:
    # e15_v4_delta_6reg_T79_typeA_p100
    m = re.match(r"e1[45]_(tevict|v4_delta)_((?:3|6|9)reg)_(T\d+)_type([A-E])", tag)
    if m:
        return {"policy": m.group(1), "reg": m.group(2), "trace_id": m.group(3), "workload_type": m.group(4)}
    # e14_tevict_T15_typeA_p100  (3reg implicit)
    m = re.match(r"e1[45]_(tevict|v4_delta)_(T\d+)_type([A-E])", tag)
    if m:
        return {"policy": m.group(1), "reg": "3reg", "trace_id": m.group(2), "workload_type": m.group(3)}
    m2 = re.match(r"e1[45]_(tevict|v4_delta)_2reg_(T\d+)_typeE", tag)
    if m2:
        return {"policy": m2.group(1), "reg": "2reg", "trace_id": m2.group(2), "workload_type": "E"}
    return None


def milp_stats(tag: str) -> Dict[str, float]:
    p = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not p.exists():
        return {"ec_rate": 0.0, "milp_ticks": 0}
    ticks = ec = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ticks += 1
        if json.loads(line).get("mode") == 1:
            ec += 1
    return {"ec_rate": (ec / ticks) if ticks else 0.0, "milp_ticks": ticks}


def main() -> None:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for jp in sorted(ROOT.glob("simulation_results_e1[45]_*.json")):
        m = PAT.match(jp.name)
        if not m:
            continue
        tag = m.group(1)
        if tag in seen:
            continue
        seen.add(tag)
        meta = parse_tag(tag)
        if not meta:
            continue
        j = json.loads(jp.read_text(encoding="utf-8"))
        total = float(j["total cost ($)"])
        storage = float(j["total storage cost ($)"])
        transfer = float(j["total transfer cost ($)"])
        rows.append(
            {
                **meta,
                "cluster_fraction": 0.1,
                "total_cost": total,
                "storage_cost": storage,
                "transfer_cost": transfer,
                "storage_share_pct": storage / total * 100 if total else 0,
                "transfer_share_pct": transfer / total * 100 if total else 0,
                **milp_stats(tag),
                "tag": tag,
                "json": jp.name,
            }
        )

    tev = {}
    for r in rows:
        if r["policy"] == "tevict":
            tev[f"{r['reg']}|{r['trace_id']}|{r['workload_type']}"] = float(r["total_cost"])
    for r in rows:
        k = f"{r['reg']}|{r['trace_id']}|{r['workload_type']}"
        t = tev.get(k)
        if t:
            r["vs_tevict_pct"] = (float(r["total_cost"]) - t) / t * 100
            if r["policy"] == "v4_delta":
                ts = next(x for x in rows if x["policy"] == "tevict" and f"{x['reg']}|{x['trace_id']}|{x['workload_type']}" == k)
                r["delta_storage"] = float(r["storage_cost"]) - float(ts["storage_cost"])
                r["delta_transfer"] = float(r["transfer_cost"]) - float(ts["transfer_cost"])
                r["delta_total"] = float(r["total_cost"]) - float(ts["total_cost"])

    out = ROOT / "results" / "E15_full_matrix_summary.csv"
    fields = [
        "reg", "trace_id", "workload_type", "policy", "cluster_fraction",
        "total_cost", "storage_cost", "transfer_cost", "storage_share_pct", "transfer_share_pct",
        "vs_tevict_pct", "delta_total", "delta_storage", "delta_transfer", "ec_rate", "tag", "json",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["reg"], x["trace_id"], x["workload_type"], x["policy"])))
    print(f"Wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
