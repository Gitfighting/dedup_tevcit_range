#!/usr/bin/env python3
"""Run Phase-1 ablation experiments and append structured records to results/EXPERIMENT_LOG.csv."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
LOG_CSV = ROOT / "results" / "EXPERIMENT_LOG.csv"
LOG_MD = ROOT / "results" / "EXPERIMENT_LOG.md"

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

CSV_FIELDS = [
    "run_ts",
    "exp_id",
    "tag",
    "goal",
    "policy_config",
    "setbase",
    "trace_id",
    "trace_path",
    "total_cost",
    "storage_cost",
    "transfer_cost",
    "total_requests",
    "result_json",
    "notes",
]


def load_cfg(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise SystemExit("PyYAML required: pip install pyyaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def latest_result(tag: str) -> Path | None:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def run_one(exp_id: str, goal: str, trace: Dict[str, Any], policy_config: str, setbase: bool, vm: int, simversion: int, days: int, dry_run: bool) -> Dict[str, Any]:
    tag = f"{exp_id.lower()}_{trace['id']}"
    setbase_str = "true" if setbase else "false"
    trace_rel = trace["path"].replace("\\", "/")
    trace_path = str(ROOT / trace["path"])
    args = (
        f"--config {policy_config} --trace {trace_rel} --vm {vm} "
        f"--setbase {setbase_str} --simversion {simversion} --tag {tag}"
    )
    cmd_str = f'mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {exp_id} tag={tag}")
    print(f"      trace={trace_path}")
    print(f"      config={policy_config} setbase={setbase_str}")
    if dry_run:
        return {"exp_id": exp_id, "tag": tag, "dry_run": True}
    # Archive stale shared MILP log so tick counts reflect this run only.
    milp_shared = ROOT / "results" / "milp_solve_log.jsonl"
    milp_arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if milp_shared.exists():
        milp_shared.replace(milp_arch) if not milp_arch.exists() else milp_shared.unlink()
    print(f"      [expect ~8-12 min for T79 p100; MILP log -> results/milp_solve_log_{tag}.jsonl]")
    t0 = datetime.now(timezone.utc)
    proc = subprocess.run(cmd_str, cwd=ROOT, shell=True)
    elapsed_min = (datetime.now(timezone.utc) - t0).total_seconds() / 60.0
    print(f"      [finished in {elapsed_min:.1f} min, exit={proc.returncode}]")
    if proc.returncode != 0:
        raise RuntimeError(f"simulation failed for {tag}: exit {proc.returncode}")
    result_path = latest_result(tag)
    if result_path is None:
        raise RuntimeError(f"no simulation_results_{tag}_*.json found")
    if milp_shared.exists():
        milp_shared.replace(milp_arch)
    data = json.loads(result_path.read_text(encoding="utf-8"))
    row = {
        "run_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exp_id": exp_id,
        "tag": tag,
        "goal": goal,
        "policy_config": policy_config,
        "setbase": setbase_str,
        "trace_id": trace["id"],
        "trace_path": trace_path,
        "total_cost": data.get("total cost ($)"),
        "storage_cost": data.get("total storage cost ($)"),
        "transfer_cost": data.get("total transfer cost ($)"),
        "total_requests": data.get("total requests"),
        "result_json": result_path.name,
        "notes": "",
    }
    append_csv(row)
    return row


def append_csv(row: Dict[str, Any]) -> None:
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not LOG_CSV.exists()
    with LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase-1 experiment runner")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "experiment_phase1.yaml")
    ap.add_argument("--exp", help="Run only this experiment id (e.g. E01)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    trace_by_id = {t["id"]: t for t in cfg["traces"]}
    vm = int(cfg.get("vm", 5))
    simversion = int(cfg.get("simversion", 3))
    days = int(cfg.get("days", 0))

    for exp in cfg["experiments"]:
        if args.exp and exp["id"] != args.exp:
            continue
        for tid in exp["traces"]:
            trace = trace_by_id[tid]
            run_one(
                exp["id"],
                exp.get("goal", ""),
                trace,
                exp["policy_config"],
                bool(exp.get("setbase", False)),
                vm,
                simversion,
                days,
                args.dry_run,
            )


if __name__ == "__main__":
    main()
