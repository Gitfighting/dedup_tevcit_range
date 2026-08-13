#!/usr/bin/env python3
"""E15: Full SkyStore matrix — 3/6/9-reg × 5 traces × 4 types @ p=0.10."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def load_yaml(path: Path) -> dict:
    if yaml is None:
        raise SystemExit("pip install pyyaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def p_tag(frac: float) -> str:
    return f"p{int(round(frac * 1000)):03d}"


def synth_trace_path(synth_root: str, wtype: str, tnum: str, frac: float) -> Path:
    return ROOT / synth_root / f"type{wtype}" / (
        f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.{p_tag(frac)}.delta.mc.aug"
    )


def alt_tag(tag: str) -> Optional[str]:
    """Reuse E14 3reg results: e15_tevict_3reg_T15_typeA_p100 → e14_tevict_T15_typeA_p100."""
    import re

    m = re.match(r"e15_(tevict|v4_delta)_3reg_(T\d+_type[A-E]_p100)", tag)
    if m:
        return f"e14_{m.group(1)}_{m.group(2)}"
    return None


def latest_result(tag: str) -> Optional[Path]:
    for t in [tag, alt_tag(tag) or ""]:
        if not t:
            continue
        m = sorted(ROOT.glob(f"simulation_results_{t}_*.json"), key=lambda p: p.stat().st_mtime)
        if m:
            return m[-1]
    return None


def milp_stats(tag: str) -> Dict[str, float]:
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


def run_sim(tag: str, cfg: str, trace_rel: str, simv: int, vm: int, heap: str) -> bool:
    if latest_result(tag):
        print(f"[exists] {tag}")
        return True
    args = f"--config {cfg} --trace {trace_rel} --vm {vm} --setbase false --simversion {simv} --tag {tag}"
    env = f"set MAVEN_OPTS=-Xmx{heap}&& " if sys.platform == "win32" else f"MAVEN_OPTS=-Xmx{heap} "
    cmd = f'{env}mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag}")
    ok = subprocess.run(cmd, cwd=ROOT, shell=True).returncode == 0
    if not ok:
        print(f"[FAIL] {tag}", file=sys.stderr)
        return False
    milp = ROOT / "results" / "milp_solve_log.jsonl"
    arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not arch.exists() and milp.exists():
        milp.replace(arch)
    return True


def build_tasks(cfg: dict) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    frac = float(cfg["primary_p"])
    skip = set(cfg.get("skip_traces") or [])
    for rc in cfg["region_configs"]:
        for tid, tnum in zip(cfg["trace_ids"], cfg["trace_numbers"]):
            if tid in skip:
                continue
            for wtype in cfg["workload_types"]:
                tp = synth_trace_path(rc["synth_root"], wtype, tnum, frac)
                if not tp.is_file():
                    print(f"[warn] missing {tp.relative_to(ROOT)}", file=sys.stderr)
                    continue
                rel = str(tp.relative_to(ROOT)).replace("\\", "/")
                for policy, pcfg, simv in [
                    ("tevict", rc["tevict"], 1),
                    ("v4_delta", rc["v4"], int(cfg.get("simversion", 3))),
                ]:
                    tag = f"e15_{policy}_{rc['id']}_{tid}_type{wtype}_p100"
                    tasks.append(
                        {
                            "reg": rc["id"],
                            "trace_id": tid,
                            "workload_type": wtype,
                            "policy": policy,
                            "config": pcfg,
                            "simversion": simv,
                            "trace_rel": rel,
                            "tag": tag,
                            "cluster_fraction": frac,
                        }
                    )
    if cfg.get("run_2reg_typeE"):
        r2 = cfg["region_2reg"]
        for tid, tnum in zip(cfg["trace_ids"], cfg["trace_numbers"]):
            if tid in skip:
                continue
            raw = ROOT / r2["data_root"] / f"IBMObjectStoreTrace{tnum}Part0.typeE.mc-aug"
            if not raw.is_file():
                alt = raw.with_suffix(".mc.aug")
                raw = alt if alt.is_file() else raw
            if not raw.is_file():
                continue
            rel = str(raw.relative_to(ROOT)).replace("\\", "/")
            for policy, pcfg, simv in [
                ("tevict", r2["tevict"], 1),
                ("v4_delta", r2["v4"], int(cfg.get("simversion", 3))),
            ]:
                tag = f"e15_{policy}_2reg_{tid}_typeE_raw"
                tasks.append(
                    {
                        "reg": "2reg",
                        "trace_id": tid,
                        "workload_type": "E",
                        "policy": policy,
                        "config": pcfg,
                        "simversion": simv,
                        "trace_rel": rel,
                        "tag": tag,
                        "cluster_fraction": 0.0,
                        "note": "no delta synth; raw typeE only",
                    }
                )
    return tasks


def row_from_task(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    jp = latest_result(t["tag"])
    if not jp:
        return None
    j = json.loads(jp.read_text(encoding="utf-8"))
    total = float(j["total cost ($)"])
    storage = float(j["total storage cost ($)"])
    transfer = float(j["total transfer cost ($)"])
    ms = milp_stats(t["tag"])
    return {
        **{k: t[k] for k in ("reg", "trace_id", "workload_type", "policy", "tag", "trace_rel", "cluster_fraction")},
        "total_cost": total,
        "storage_cost": storage,
        "transfer_cost": transfer,
        "storage_share_pct": (storage / total * 100) if total else 0,
        "transfer_share_pct": (transfer / total * 100) if total else 0,
        **ms,
        "json": jp.name,
    }


def add_vs_tevict(rows: List[Dict[str, Any]]) -> None:
    tev: Dict[str, float] = {}
    for r in rows:
        if r["policy"] == "tevict":
            tev[f"{r['reg']}|{r['trace_id']}|{r['workload_type']}"] = float(r["total_cost"])
    for r in rows:
        k = f"{r['reg']}|{r['trace_id']}|{r['workload_type']}"
        t = tev.get(k)
        if t:
            r["vs_tevict_pct"] = (float(r["total_cost"]) - t) / t * 100
            if r["policy"] == "v4_delta":
                tev_s = next(x for x in rows if x["policy"] == "tevict" and f"{x['reg']}|{x['trace_id']}|{x['workload_type']}" == k)
                r["delta_storage"] = float(r["storage_cost"]) - float(tev_s["storage_cost"])
                r["delta_transfer"] = float(r["transfer_cost"]) - float(tev_s["transfer_cost"])
                r["delta_total"] = float(r["total_cost"]) - float(tev_s["total_cost"])
        else:
            r["vs_tevict_pct"] = 0.0


def phase_synth() -> None:
    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_phase2_synth_batch.py"),
            "--config",
            str(ROOT / "config" / "phase2_synth_e15_6reg_9reg.yaml"),
        ],
        cwd=ROOT,
    )


def phase_run(cfg: dict, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    heap = cfg.get("maven_heap", "12g")
    vm = int(cfg.get("vm", 5))
    rows: List[Dict[str, Any]] = []
    milp = ROOT / "results" / "milp_solve_log.jsonl"
    for i, t in enumerate(tasks, 1):
        print(f"[task {i}/{len(tasks)}] {t['tag']}")
        if milp.exists():
            milp.unlink()
        if not run_sim(t["tag"], t["config"], t["trace_rel"], t["simversion"], vm, heap):
            continue
        row = row_from_task(t)
        if row:
            rows.append(row)
    add_vs_tevict(rows)
    return rows


def merge_prior(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pull in completed e14_3reg runs with e15 tag alias."""
    alias_map = []
    for p in ROOT.glob("simulation_results_e14_*_p100.json"):
        name = p.name
        if "e14_tevict_" in name:
            body = name.replace("simulation_results_e14_tevict_", "").split("_2026")[0]
            alias_map.append((f"e14_tevict_{body}", f"e15_tevict_3reg_{body}"))
        elif "e14_v4_delta_" in name:
            body = name.replace("simulation_results_e14_v4_delta_", "").split("_2026")[0]
            alias_map.append((f"e14_v4_delta_{body}", f"e15_v4_delta_3reg_{body}"))
    existing_tags = {r["tag"] for r in rows}
    # e14 tags already in rows if re-run; skip merge complexity
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "reg", "trace_id", "workload_type", "policy", "cluster_fraction",
        "total_cost", "storage_cost", "transfer_cost",
        "storage_share_pct", "transfer_share_pct",
        "vs_tevict_pct", "delta_total", "delta_storage", "delta_transfer",
        "ec_rate", "milp_ticks", "tag", "trace_rel", "json",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["reg"], x["trace_id"], x["workload_type"], x["policy"])))


def phase_analyze(rows: List[Dict[str, Any]], cfg: dict) -> None:
    script = ROOT / "scripts" / "analyze_e15_full_matrix.py"
    out = ROOT / "results" / "E15_full_matrix_summary.csv"
    write_csv(out, rows)
    subprocess.check_call([sys.executable, str(script), str(out)], cwd=ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "experiment_e15_full_skystore_matrix.yaml")
    ap.add_argument("--phase", choices=["all", "synth", "run", "analyze"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    tasks = build_tasks(cfg)
    print(f"tasks={len(tasks)} (expect up to 120 for 3/6/9-reg + optional 2reg typeE)")

    if args.dry_run:
        for t in tasks[:10]:
            print(t["tag"])
        return

    if args.phase in ("all", "synth"):
        phase_synth()

    rows: List[Dict[str, Any]] = []
    if args.phase in ("all", "run"):
        rows = phase_run(cfg, tasks)

    if args.phase in ("all", "analyze") and rows:
        phase_analyze(rows, cfg)
    elif args.phase == "analyze":
        csv_path = ROOT / "results" / "E15_full_matrix_summary.csv"
        if csv_path.exists():
            subprocess.check_call(
                [sys.executable, str(ROOT / "scripts" / "analyze_e15_full_matrix.py"), str(csv_path)],
                cwd=ROOT,
            )


if __name__ == "__main__":
    main()
