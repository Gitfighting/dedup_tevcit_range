#!/usr/bin/env python3
"""E13: sweep cluster_fraction p (delta coverage) + cross-trace @ p=0.1."""

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
    env = "set MAVEN_OPTS=-Xmx8g&& " if sys.platform == "win32" else "MAVEN_OPTS=-Xmx8g "
    cmd = f'{env}mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag}")
    proc = subprocess.run(cmd, cwd=ROOT, shell=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sim failed: {tag}")


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


def trace_meta(trace: Path) -> Dict[str, Any]:
    sp = Path(str(trace) + ".summary.json")
    if sp.exists():
        return json.loads(sp.read_text(encoding="utf-8"))
    return {}


def row_from_sim(
    experiment: str,
    policy: str,
    trace_id: str,
    trace_rel: str,
    tag: str,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    j = json.loads(latest_result(tag).read_text(encoding="utf-8"))
    total = float(j["total cost ($)"])
    storage = float(j["total storage cost ($)"])
    transfer = float(j["total transfer cost ($)"])
    return {
        "experiment": experiment,
        "policy": policy,
        "trace_id": trace_id,
        "trace": trace_rel,
        "cluster_fraction": meta.get("cluster_fraction"),
        "delta_member_keys": meta.get("delta_member_count"),
        "dr_mean": (meta.get("dr_assigned") or {}).get("mean"),
        "total_cost": total,
        "storage_cost": storage,
        "transfer_cost": transfer,
        "total_requests": j["total requests"],
        "aggregate_size_gb": j.get("aggregate size (GB)"),
        **milp_stats(tag),
        "tag": tag,
    }


def main() -> None:
    cfg_path = ROOT / "config" / "experiment_delta_saturation.yaml"
    if len(sys.argv) > 1:
        cfg_path = Path(sys.argv[1])
    cfg = load_yaml(cfg_path)
    vm = int(cfg.get("vm", 5))
    simv = int(cfg.get("simversion", 3))
    rows: List[Dict[str, Any]] = []
    milp_shared = ROOT / "results" / "milp_solve_log.jsonl"

    for frac in cfg["fractions"]:
        p_tag = f"p{int(frac * 1000):03d}"
        trace_rel = cfg["trace_template"].format(p_tag=p_tag).replace("\\", "/")
        trace_id = f"{cfg['trace_id_prefix']}_{p_tag}"
        meta = trace_meta(ROOT / trace_rel)

        for policy, pcfg, psim in [
            ("tevict", cfg["tevict_config"], 1),
            ("v4_delta_ec", cfg["v4_config"], simv),
        ]:
            tag = f"e13_{policy}_{trace_id}"
            if milp_shared.exists():
                milp_shared.unlink()
            run_sim(tag, pcfg, trace_rel, psim, vm)
            arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
            if not arch.exists() and milp_shared.exists():
                milp_shared.replace(arch)
            rows.append(row_from_sim("saturation", policy, trace_id, trace_rel, tag, meta))

    for ct in cfg.get("cross_traces", []):
        trace_rel = ct["trace"].replace("\\", "/")
        trace_id = ct["id"]
        meta = trace_meta(ROOT / trace_rel)
        for policy, pcfg, psim in [
            ("tevict", cfg["tevict_config"], 1),
            ("v4_delta_ec", cfg["v4_config"], simv),
        ]:
            tag = f"e13_{policy}_{trace_id}_p100"
            if milp_shared.exists():
                milp_shared.unlink()
            run_sim(tag, pcfg, trace_rel, psim, vm)
            arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
            if not arch.exists() and milp_shared.exists():
                milp_shared.replace(arch)
            rows.append(row_from_sim("cross_trace", policy, trace_id, trace_rel, tag, meta))

    # vs tevict within same (experiment, trace_id)
    tev_map: Dict[str, float] = {}
    for r in rows:
        if r["policy"] == "tevict":
            tev_map[f"{r['experiment']}|{r['trace_id']}"] = float(r["total_cost"])
    for r in rows:
        key = f"{r['experiment']}|{r['trace_id']}"
        tev = tev_map.get(key)
        if tev:
            r["vs_tevict_pct"] = (float(r["total_cost"]) - tev) / tev * 100.0
        else:
            r["vs_tevict_pct"] = 0.0

    out = ROOT / "results" / "E13_delta_saturation_summary.csv"
    fields = [
        "experiment",
        "policy",
        "trace_id",
        "cluster_fraction",
        "delta_member_keys",
        "dr_mean",
        "total_cost",
        "storage_cost",
        "transfer_cost",
        "vs_tevict_pct",
        "milp_ticks",
        "ec_rate",
        "tag",
        "trace",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    md = ROOT / "results" / "E13_delta_saturation_report.md"
    lines = [
        "# E13 Delta coverage sweep + cross-trace",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Saturation (T79 typeA)",
        "",
        "| p | members | V4 total | tevict | vs tevict |",
        "|--:|--------:|---------:|-------:|----------:|",
    ]
    sat = [r for r in rows if r["experiment"] == "saturation"]
    for frac in cfg["fractions"]:
        p_tag = f"p{int(frac * 1000):03d}"
        tid = f"{cfg['trace_id_prefix']}_{p_tag}"
        v4 = next(x for x in sat if x["trace_id"] == tid and x["policy"] == "v4_delta_ec")
        tev = next(x for x in sat if x["trace_id"] == tid and x["policy"] == "tevict")
        lines.append(
            f"| {frac:.2f} | {v4.get('delta_member_keys')} | "
            f"{float(v4['total_cost']):.2f} | {float(tev['total_cost']):.2f} | "
            f"{float(v4.get('vs_tevict_pct', 0)):+.1f}% |"
        )
    lines += ["", "## Cross-trace @ p=0.1", "", "| trace | V4 | tevict | vs tevict |", "|-------|---:|-------:|----------:|"]
    cross = [r for r in rows if r["experiment"] == "cross_trace"]
    for ct in cfg.get("cross_traces", []):
        tid = ct["id"]
        v4 = next(x for x in cross if x["trace_id"] == tid and x["policy"] == "v4_delta_ec")
        tev = next(x for x in cross if x["trace_id"] == tid and x["policy"] == "tevict")
        lines.append(
            f"| {tid} | {float(v4['total_cost']):.2f} | {float(tev['total_cost']):.2f} | "
            f"{float(v4.get('vs_tevict_pct', 0)):+.1f}% |"
        )
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out} and {md}")


if __name__ == "__main__":
    main()
