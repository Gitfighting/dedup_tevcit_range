#!/usr/bin/env python3
"""Run delta x EC 2x2 ablation + tevict; emit SkyStore-compatible + incremental metrics."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def load_yaml(path: Path) -> dict:
    if yaml is None:
        raise SystemExit("pip install pyyaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def latest_result(tag: str) -> Path:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(tag)
    return matches[-1]


def run_sim(tag: str, cfg: str, trace: str, simv: int, vm: int) -> None:
    args = f"--config {cfg} --trace {trace} --vm {vm} --setbase false --simversion {simv} --tag {tag}"
    cmd = f'mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag}")
    proc = subprocess.run(cmd, cwd=ROOT, shell=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sim failed: {tag}")


def milp_stats(tag: str) -> Dict[str, Any]:
    path = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not path.exists():
        shared = ROOT / "results" / "milp_solve_log.jsonl"
        if shared.exists():
            path = shared
        else:
            return {"milp_ticks": 0, "ec_ticks": 0, "ec_rate": 0.0}
    ticks = ec = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ticks += 1
        o = json.loads(line)
        if o.get("mode") == 1:
            ec += 1
    return {
        "milp_ticks": ticks,
        "ec_ticks": ec,
        "ec_rate": (ec / ticks) if ticks else 0.0,
    }


def trace_summary(trace: Path) -> Dict[str, Any]:
    sp = trace.with_suffix(trace.suffix + ".summary.json")
    if not sp.exists():
        alt = Path(str(trace) + ".summary.json")
        sp = alt if alt.exists() else sp
    if sp.exists():
        return json.loads(sp.read_text(encoding="utf-8"))
    return {}


def incremental_metrics(row: Dict[str, Any], tev_total: float) -> Dict[str, Any]:
    total = float(row["total_cost"])
    storage = float(row["storage_cost"])
    transfer = float(row["transfer_cost"])
    agg_gb = float(row.get("aggregate_size_gb") or 0)
    nreq = int(row.get("total_requests") or 0)
    out: Dict[str, Any] = {
        "vs_tevict_pct": ((total - tev_total) / tev_total * 100.0) if tev_total else 0.0,
        "storage_share_pct": (storage / total * 100.0) if total else 0.0,
        "transfer_share_pct": (transfer / total * 100.0) if total else 0.0,
        "cost_per_request_usd": total / nreq if nreq else 0.0,
        "storage_per_agg_gb_usd": storage / agg_gb if agg_gb else 0.0,
        "transfer_per_agg_gb_usd": transfer / agg_gb if agg_gb else 0.0,
    }
    return out


def main() -> None:
    cfg_path = ROOT / "config" / "experiment_delta_ec_2x2.yaml"
    if len(sys.argv) > 1:
        cfg_path = Path(sys.argv[1])
    cfg = load_yaml(cfg_path)
    trace_rel = cfg["trace"].replace("\\", "/")
    trace = ROOT / trace_rel
    vm = int(cfg.get("vm", 5))
    simv = int(cfg.get("simversion", 3))
    ts = trace_summary(trace)

    rows: List[Dict[str, Any]] = []

    # tevict SkyStore baseline
    tev_tag = f"2x2_tev_{cfg['trace_id']}"
    milp_shared = ROOT / "results" / "milp_solve_log.jsonl"
    if milp_shared.exists():
        milp_shared.unlink()
    run_sim(tev_tag, cfg["tevict_config"], trace_rel, 1, vm)
    tev_j = json.loads(latest_result(tev_tag).read_text(encoding="utf-8"))
    tev_total = float(tev_j["total cost ($)"])
    rows.append(
        {
            "corner": "tevict",
            "label": "SkyStore tevict (no δ, no MILP EC)",
            "delta": False,
            "ec": False,
            "tag": tev_tag,
            "total_cost": tev_total,
            "storage_cost": tev_j["total storage cost ($)"],
            "transfer_cost": tev_j["total transfer cost ($)"],
            "total_requests": tev_j["total requests"],
            "aggregate_size_gb": tev_j.get("aggregate size (GB)"),
            **milp_stats(tev_tag),
        }
    )

    for corner in cfg["corners"]:
        tag = f"2x2_{corner['id']}_{cfg['trace_id']}"
        if milp_shared.exists():
            milp_shared.unlink()
        run_sim(tag, corner["config"], trace_rel, simv, vm)
        arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
        if not arch.exists() and milp_shared.exists():
            milp_shared.replace(arch)
        j = json.loads(latest_result(tag).read_text(encoding="utf-8"))
        cid = corner["id"]
        rows.append(
            {
                "corner": cid,
                "label": corner["label"],
                "delta": cid.startswith("delta"),
                "ec": cid.endswith("_ec"),
                "tag": tag,
                "total_cost": j["total cost ($)"],
                "storage_cost": j["total storage cost ($)"],
                "transfer_cost": j["total transfer cost ($)"],
                "total_requests": j["total requests"],
                "aggregate_size_gb": j.get("aggregate size (GB)"),
                **milp_stats(tag),
            }
        )

    for r in rows:
        r.update(incremental_metrics(r, tev_total))

    # Factor attribution vs nodelta_rep baseline corner if present
    base = next((x for x in rows if x["corner"] == "nodelta_rep"), None)
    if base:
        btot = float(base["total_cost"])
        for r in rows:
            r["vs_nodelta_rep_pct"] = ((float(r["total_cost"]) - btot) / btot * 100.0) if btot else 0.0

    if ts:
        for r in rows:
            r["delta_member_keys"] = ts.get("delta_member_count")
            r["cluster_fraction"] = ts.get("cluster_fraction")

    out_csv = ROOT / "results" / f"delta_ec_2x2_{cfg['trace_id']}_summary.csv"
    fields = [
        "corner",
        "label",
        "delta",
        "ec",
        "total_cost",
        "storage_cost",
        "transfer_cost",
        "vs_tevict_pct",
        "vs_nodelta_rep_pct",
        "storage_share_pct",
        "transfer_share_pct",
        "cost_per_request_usd",
        "storage_per_agg_gb_usd",
        "transfer_per_agg_gb_usd",
        "milp_ticks",
        "ec_ticks",
        "ec_rate",
        "delta_member_keys",
        "cluster_fraction",
        "tag",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    md = ROOT / "results" / f"delta_ec_2x2_{cfg['trace_id']}_report.md"
    lines = [
        "# Delta × EC 2×2 ablation (SkyStore-compatible trace)",
        "",
        f"Trace: `{trace_rel}` ({ts.get('input_rows', '?')} req, "
        f"member keys {ts.get('delta_member_count', '?')}, p={ts.get('cluster_fraction', '?')})",
        "",
        "## SkyStore metrics (USD, 7-day window)",
        "",
        "| corner | δ | EC | total | storage | transfer | vs tevict |",
        "|--------|---|----|------:|--------:|---------:|----------:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['corner']} | {'✓' if r.get('delta') else '·'} | "
            f"{'✓' if r.get('ec') else '·'} | {float(r['total_cost']):.2f} | "
            f"{float(r['storage_cost']):.2f} | {float(r['transfer_cost']):.2f} | "
            f"{float(r.get('vs_tevict_pct', 0)):+.1f}% |"
        )
    lines += [
        "",
        "## Incremental metrics",
        "",
        "| corner | $/req | storage/agg GB | transfer/agg GB | MILP EC rate |",
        "|--------|------:|---------------:|----------------:|-------------:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['corner']} | {float(r.get('cost_per_request_usd', 0)):.6f} | "
            f"{float(r.get('storage_per_agg_gb_usd', 0)):.6f} | "
            f"{float(r.get('transfer_per_agg_gb_usd', 0)):.6f} | "
            f"{float(r.get('ec_rate', 0))*100:.0f}% |"
        )
    lines += [
        "",
        "### Attribution (incremental paper)",
        "",
        "- **δ-only effect**: `delta_rep` − `nodelta_rep` (storage + transfer split).",
        "- **EC-only effect**: `nodelta_ec` − `nodelta_rep`.",
        "- **Combined**: `delta_ec` − `nodelta_rep`; interaction = combined − δ-only − EC-only.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_csv} and {md}")


if __name__ == "__main__":
    main()
