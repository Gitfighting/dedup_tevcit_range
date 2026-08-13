#!/usr/bin/env python3
"""
E14: SkyStore-aligned experiment matrix.

  Phase synth     — generate missing extended-p traces (T79 typeA)
  Phase matrix    — 5 traces × 4 types × (V4 + tevict) @ p=0.10
  Phase saturation — T79 typeA × 11 p levels × (V4 + tevict)

Output: results/E14_skystore_matrix_summary.csv
        results/E14_saturation_ladder_summary.csv
        results/E14_skystore_matrix_report.md
"""

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


def trace_path(cfg: dict, tid: str, tnum: str, wtype: str, frac: float) -> Path:
    rel = (
        f"{cfg['synth_root']}/type{wtype}/"
        f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.{p_tag(frac)}.delta.mc.aug"
    )
    return ROOT / rel.replace("\\", "/")


def trace_meta(trace: Path) -> Dict[str, Any]:
    sp = Path(str(trace) + ".summary.json")
    return json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}


def latest_result(tag: str) -> Optional[Path]:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def run_sim(tag: str, pcfg: str, trace_rel: str, simv: int, vm: int, heap: str) -> bool:
    if latest_result(tag):
        print(f"[exists] {tag}")
        return True
    args = f"--config {pcfg} --trace {trace_rel} --vm {vm} --setbase false --simversion {simv} --tag {tag}"
    env = f"set MAVEN_OPTS=-Xmx{heap}&& " if sys.platform == "win32" else f"MAVEN_OPTS=-Xmx{heap} "
    cmd = f'{env}mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag}")
    proc = subprocess.run(cmd, cwd=ROOT, shell=True)
    if proc.returncode != 0:
        print(f"[FAIL] {tag}", file=sys.stderr)
        return False
    milp = ROOT / "results" / "milp_solve_log.jsonl"
    arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not arch.exists() and milp.exists():
        milp.replace(arch)
    return True


def row_from_tag(
    experiment: str,
    policy: str,
    tid: str,
    wtype: str,
    frac: float,
    trace_rel: str,
    tag: str,
    meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    jp = latest_result(tag)
    if not jp:
        return None
    j = json.loads(jp.read_text(encoding="utf-8"))
    milp_path = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    ticks = ec = 0
    if milp_path.exists():
        for line in milp_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ticks += 1
            if json.loads(line).get("mode") == 1:
                ec += 1
    return {
        "experiment": experiment,
        "policy": policy,
        "trace_id": tid,
        "workload_type": wtype,
        "cluster_fraction": frac,
        "p_tag": p_tag(frac),
        "delta_member_keys": meta.get("delta_member_count"),
        "total_cost": float(j["total cost ($)"]),
        "storage_cost": float(j["total storage cost ($)"]),
        "transfer_cost": float(j["total transfer cost ($)"]),
        "total_requests": int(j["total requests"]),
        "milp_ticks": ticks,
        "ec_rate": (ec / ticks) if ticks else 0.0,
        "tag": tag,
        "trace": trace_rel,
        "json": jp.name,
    }


def add_vs_tevict(rows: List[Dict[str, Any]], key_fn) -> None:
    tev: Dict[str, float] = {}
    for r in rows:
        if r["policy"] == "tevict":
            tev[key_fn(r)] = float(r["total_cost"])
    for r in rows:
        k = key_fn(r)
        t = tev.get(k)
        r["vs_tevict_pct"] = ((float(r["total_cost"]) - t) / t * 100.0) if t else 0.0


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def phase_synth(cfg: dict) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_phase2_synth_batch.py"),
        "--config",
        str(ROOT / "config" / "phase2_synth_extended_p.yaml"),
    ]
    print("[phase synth] extended p traces for T79 typeA")
    subprocess.check_call(cmd, cwd=ROOT)


def build_matrix_tasks(cfg: dict) -> List[Tuple[str, str, str, str, float, str]]:
    """tid, tnum, wtype, policy, frac, trace_rel"""
    tasks: List[Tuple[str, str, str, str, float, str]] = []
    frac = float(cfg["primary_p"])
    skip = set(cfg.get("skip_traces") or [])
    for tid, tnum in zip(cfg["trace_ids"], cfg["trace_numbers"]):
        if tid in skip:
            continue
        for wtype in cfg["workload_types"]:
            tp = trace_path(cfg, tid, tnum, wtype, frac)
            if not tp.is_file():
                print(f"[warn] missing trace: {tp.relative_to(ROOT)}", file=sys.stderr)
                continue
            rel = str(tp.relative_to(ROOT)).replace("\\", "/")
            for policy, pcfg, simv in [
                ("tevict", cfg["tevict_config"], 1),
                ("v4_delta", cfg["v4_config"], int(cfg.get("simversion", 3))),
            ]:
                tasks.append((tid, tnum, wtype, policy, frac, rel))
    return tasks


def build_saturation_tasks(cfg: dict) -> List[Tuple[str, str, str, str, float, str]]:
    tid = cfg["saturation_trace"]
    tnum = cfg["trace_numbers"][cfg["trace_ids"].index(tid)]
    wtype = cfg["saturation_type"]
    tasks: List[Tuple[str, str, str, str, float, str]] = []
    for frac in cfg["saturation_fractions"]:
        tp = trace_path(cfg, tid, tnum, wtype, frac)
        if not tp.is_file():
            print(f"[warn] missing saturation trace p={frac}: {tp}", file=sys.stderr)
            continue
        rel = str(tp.relative_to(ROOT)).replace("\\", "/")
        for policy in ("tevict", "v4_delta"):
            tasks.append((tid, tnum, wtype, policy, frac, rel))
    return tasks


def run_tasks(cfg: dict, tasks: List[Tuple], experiment: str) -> List[Dict[str, Any]]:
    heap = cfg.get("maven_heap", "8g")
    vm = int(cfg.get("vm", 5))
    rows: List[Dict[str, Any]] = []
    for tid, tnum, wtype, policy, frac, rel in tasks:
        tag = f"e14_{policy}_{tid}_type{wtype}_{p_tag(frac)}"
        pcfg = cfg["tevict_config"] if policy == "tevict" else cfg["v4_config"]
        simv = 1 if policy == "tevict" else int(cfg.get("simversion", 3))
        milp = ROOT / "results" / "milp_solve_log.jsonl"
        if milp.exists():
            milp.unlink()
        if not run_sim(tag, pcfg, rel, simv, vm, heap):
            continue
        meta = trace_meta(ROOT / rel)
        row = row_from_tag(experiment, policy, tid, wtype, frac, rel, tag, meta)
        if row:
            rows.append(row)
    return rows


def write_report(cfg: dict, matrix_rows: List[Dict], sat_rows: List[Dict]) -> None:
    md = ROOT / "results" / "E14_skystore_matrix_report.md"
    lines = [
        "# E14 SkyStore 对齐实验矩阵",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## 实验设计",
        "",
        "- **Trace 小类（5）**：T15, T29, T65, T78, T79（SkyStore 论文；Trace065→T65, Trace078→T78）",
        "- **访问大类（4）**：typeA, typeB, typeC, typeD",
        "- **主矩阵**：@ $p{=}0.10$，V4 vs tevict",
        f"- **p 阶梯（{len(cfg['saturation_fractions'])} 档）**："
        + ", ".join(f"{x:.2f}" for x in cfg["saturation_fractions"])
        + "（T79 typeA）",
        "",
        "## 主矩阵 @ p=0.10（V4 vs tevict %）",
        "",
        "| trace | typeA | typeB | typeC | typeD |",
        "|-------|------:|------:|------:|------:|",
    ]
    for tid in cfg["trace_ids"]:
        cells = []
        for wtype in cfg["workload_types"]:
            v4 = next(
                (
                    x
                    for x in matrix_rows
                    if x["trace_id"] == tid
                    and x["workload_type"] == wtype
                    and x["policy"] == "v4_delta"
                ),
                None,
            )
            cells.append(f"{float(v4['vs_tevict_pct']):+.1f}%" if v4 else "—")
        lines.append(f"| {tid} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## T79 typeA p 阶梯",
        "",
        "| p | members | V4 | tevict | vs tevict |",
        "|--:|--------:|---:|-------:|----------:|",
    ]
    for frac in cfg["saturation_fractions"]:
        v4 = next(
            (x for x in sat_rows if x["policy"] == "v4_delta" and abs(x["cluster_fraction"] - frac) < 1e-6),
            None,
        )
        tev = next(
            (x for x in sat_rows if x["policy"] == "tevict" and abs(x["cluster_fraction"] - frac) < 1e-6),
            None,
        )
        if v4 and tev:
            lines.append(
                f"| {frac:.2f} | {v4.get('delta_member_keys', '?')} | "
                f"{float(v4['total_cost']):.2f} | {float(tev['total_cost']):.2f} | "
                f"{float(v4.get('vs_tevict_pct', 0)):+.1f}% |"
            )
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {md}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "experiment_e14_skystore_matrix.yaml")
    ap.add_argument(
        "--phase",
        choices=["all", "synth", "matrix", "saturation"],
        default="all",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_yaml(args.config)

    if args.dry_run:
        mt = build_matrix_tasks(cfg)
        st = build_saturation_tasks(cfg)
        print(f"matrix tasks: {len(mt)}, saturation tasks: {len(st)}")
        return

    if args.phase in ("all", "synth"):
        phase_synth(cfg)

    matrix_rows: List[Dict[str, Any]] = []
    sat_rows: List[Dict[str, Any]] = []

    if args.phase in ("all", "matrix"):
        mt = build_matrix_tasks(cfg)
        print(f"[matrix] {len(mt)} simulation tasks @ p={cfg['primary_p']}")
        matrix_rows = run_tasks(cfg, mt, "skystore_matrix")
        add_vs_tevict(matrix_rows, lambda r: f"{r['trace_id']}|{r['workload_type']}")
        fields = [
            "experiment", "policy", "trace_id", "workload_type", "cluster_fraction", "p_tag",
            "delta_member_keys", "total_cost", "storage_cost", "transfer_cost",
            "vs_tevict_pct", "ec_rate", "tag", "trace",
        ]
        write_csv(ROOT / "results" / "E14_skystore_matrix_summary.csv", matrix_rows, fields)

    if args.phase in ("all", "saturation"):
        st = build_saturation_tasks(cfg)
        print(f"[saturation] {len(st)} simulation tasks, {len(cfg['saturation_fractions'])} p levels")
        sat_rows = run_tasks(cfg, st, "saturation_ladder")
        add_vs_tevict(sat_rows, lambda r: f"{r['p_tag']}")
        fields = [
            "experiment", "policy", "trace_id", "workload_type", "cluster_fraction", "p_tag",
            "delta_member_keys", "total_cost", "storage_cost", "transfer_cost",
            "vs_tevict_pct", "ec_rate", "tag",
        ]
        write_csv(ROOT / "results" / "E14_saturation_ladder_summary.csv", sat_rows, fields)

    if matrix_rows or sat_rows:
        write_report(cfg, matrix_rows, sat_rows)


if __name__ == "__main__":
    main()
