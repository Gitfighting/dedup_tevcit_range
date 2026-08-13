#!/usr/bin/env python3
"""
3reg FP 全策略 SkyStore 对比矩阵。

对比策略:
  always_evict, always_store (pull_on_read), teven, dynamicttl (TTL-CC), ewma,
  tevict (SkyStore), milp_ec_new, milp_ec_new_v3, milp_ec_new_v4

模式: FP (--setbase false)

用法:
  python scripts/run_3reg_fp_full_matrix.py --dry-run
  python scripts/run_3reg_fp_full_matrix.py --phase run
  python scripts/run_3reg_fp_full_matrix.py --phase run --trace T79 --workload A
  python scripts/run_3reg_fp_full_matrix.py --phase run --policy tevict
  python scripts/run_3reg_fp_full_matrix.py --phase analyze
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


def raw_trace_path(cfg: dict, tnum: str, wtype: str) -> Path:
    return (
        ROOT
        / cfg["raw_trace_root"]
        / f"type{wtype}"
        / f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.mc.aug"
    )


def synth_trace_path(cfg: dict, tnum: str, wtype: str, frac: float) -> Path:
    tag = p_tag(frac)
    return (
        ROOT
        / cfg["synth_trace_root"]
        / f"type{wtype}"
        / f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.{tag}.delta.mc.aug"
    )


def resolve_synth_trace(cfg: dict, tnum: str, wtype: str) -> Optional[Path]:
    """Prefer primary_p synth trace; fall back to other fractions in config."""
    fracs = [float(cfg.get("primary_p", 0.10))]
    for f in cfg.get("synth_fallback_fractions") or [0.05]:
        if abs(f - fracs[0]) > 1e-9:
            fracs.append(float(f))
    for frac in fracs:
        tp = synth_trace_path(cfg, tnum, wtype, frac)
        if tp.is_file():
            return tp
    return None


def resolve_trace(cfg: dict, tid: str, tnum: str, wtype: str, trace_source: str) -> Optional[Path]:
    if trace_source == "synth":
        return resolve_synth_trace(cfg, tnum, wtype)
    tp = raw_trace_path(cfg, tnum, wtype)
    return tp if tp.is_file() else None


def build_tasks(
    cfg: dict,
    trace_filter: Optional[str],
    workload_filter: Optional[str],
    policy_filter: Optional[str],
) -> List[Dict[str, Any]]:
    skip = set(cfg.get("skip_traces") or [])
    tasks: List[Dict[str, Any]] = []
    for tid, tnum in zip(cfg["trace_ids"], cfg["trace_numbers"]):
        if tid in skip:
            continue
        if trace_filter and tid != trace_filter:
            continue
        for wtype in cfg["workload_types"]:
            if workload_filter and wtype.upper() != workload_filter.upper():
                continue
            for pol in cfg["policies"]:
                if policy_filter and pol["id"] != policy_filter:
                    continue
                tp = resolve_trace(cfg, tid, tnum, wtype, pol.get("trace_source", "raw"))
                if tp is None:
                    print(
                        f"[warn] missing trace for {pol['id']} {tid} type{wtype}: "
                        f"{pol.get('trace_source', 'raw')}",
                        file=sys.stderr,
                    )
                    continue
                rel = str(tp.relative_to(ROOT)).replace("\\", "/")
                tag = f"fp3reg_{pol['id']}_{tid}_type{wtype}"
                tasks.append(
                    {
                        "policy_id": pol["id"],
                        "policy_label": pol.get("label", pol["id"]),
                        "category": pol.get("category", ""),
                        "config": pol["config"],
                        "simversion": int(pol.get("simversion", 1)),
                        "trace_source": pol.get("trace_source", "raw"),
                        "trace_id": tid,
                        "trace_number": tnum,
                        "workload_type": wtype,
                        "trace_rel": rel,
                        "tag": tag,
                    }
                )
    return tasks


def latest_result(tag: str) -> Optional[Path]:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def run_sim(task: dict, cfg: dict, skip_existing: bool, force: bool) -> bool:
    tag = task["tag"]
    if skip_existing and not force and latest_result(tag):
        print(f"[skip] {tag} (result exists)")
        return True

    heap = cfg.get("maven_heap_t29", "16g") if task["trace_id"] == "T29" else cfg.get("maven_heap", "12g")
    vm = int(cfg.get("vm", 5))
    setbase = "true" if cfg.get("setbase") else "false"
    if cfg.get("setbase") is False:
        setbase = "false"
    simv = task["simversion"]
    args = (
        f"--config {task['config']} --trace {task['trace_rel']} --vm {vm} "
        f"--setbase {setbase} --simversion {simv} --tag {tag}"
    )
    milp_shared = ROOT / "results" / "milp_solve_log.jsonl"
    milp_arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if milp_shared.exists():
        if milp_arch.exists():
            milp_shared.unlink()
        else:
            milp_shared.replace(milp_arch)

    env = f"set MAVEN_OPTS=-Xmx{heap}&& " if sys.platform == "win32" else f"MAVEN_OPTS=-Xmx{heap} "
    cmd = f'{env}mvn -q -o exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag} simv={simv} trace={task['trace_source']}")
    proc = subprocess.run(cmd, cwd=ROOT, shell=True)
    if proc.returncode != 0:
        print(f"[FAIL] {tag}", file=sys.stderr)
        return False
    if milp_shared.exists() and not milp_arch.exists():
        milp_shared.replace(milp_arch)
    return True


def row_from_task(task: dict, cfg: dict) -> Optional[Dict[str, Any]]:
    jp = latest_result(task["tag"])
    if not jp:
        return None
    j = json.loads(jp.read_text(encoding="utf-8"))
    milp_path = ROOT / "results" / f"milp_solve_log_{task['tag']}.jsonl"
    ticks = ec = 0
    if milp_path.exists():
        for line in milp_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ticks += 1
            if json.loads(line).get("mode") == 1:
                ec += 1
    return {
        "mode": "FP",
        "policy_id": task["policy_id"],
        "policy_label": task["policy_label"],
        "category": task["category"],
        "trace_id": task["trace_id"],
        "workload_type": task["workload_type"],
        "trace_source": task["trace_source"],
        "cluster_fraction": float(cfg.get("primary_p", 0.10)),
        "total_cost": float(j["total cost ($)"]),
        "storage_cost": float(j["total storage cost ($)"]),
        "transfer_cost": float(j["total transfer cost ($)"]),
        "total_requests": int(j["total requests"]),
        "milp_ticks": ticks,
        "ec_rate": (ec / ticks) if ticks else "",
        "tag": task["tag"],
        "trace": task["trace_rel"],
        "json": jp.name,
        "config": task["config"],
    }


def add_vs_reference(rows: List[Dict[str, Any]], ref_policy: str) -> None:
    ref: Dict[str, float] = {}
    for r in rows:
        if r["policy_id"] == ref_policy:
            ref[f"{r['trace_id']}|{r['workload_type']}"] = float(r["total_cost"])
    for r in rows:
        k = f"{r['trace_id']}|{r['workload_type']}"
        base = ref.get(k)
        if base and base > 0:
            r["vs_reference_pct"] = (float(r["total_cost"]) - base) / base * 100.0
        else:
            r["vs_reference_pct"] = ""


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def phase_synth(cfg: dict) -> None:
    synth_cfg = cfg.get("synth_batch_config", "config/phase2_synth_batch.yaml")
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_phase2_synth_batch.py"),
        "--config",
        str(ROOT / synth_cfg),
    ]
    print(f"[synth] generating delta traces via {synth_cfg}")
    subprocess.check_call(cmd, cwd=ROOT)


def phase_run(cfg: dict, args: argparse.Namespace) -> List[Dict[str, Any]]:
    tasks = build_tasks(cfg, args.trace, args.workload, args.policy)
    print(f"[run] {len(tasks)} tasks (FP, 3reg)")
    if args.dry_run:
        for t in tasks[:20]:
            print(f"  {t['tag']} -> {t['config']}")
        if len(tasks) > 20:
            print(f"  ... and {len(tasks) - 20} more")
        return []

    ok = fail = skip = 0
    rows: List[Dict[str, Any]] = []
    for i, task in enumerate(tasks, 1):
        existed = latest_result(task["tag"]) is not None
        print(f"[{i}/{len(tasks)}] {task['tag']}")
        if existed and args.skip_existing and not args.force:
            skip += 1
        elif run_sim(task, cfg, args.skip_existing, args.force):
            ok += 1
        else:
            fail += 1
            continue
        row = row_from_task(task, cfg)
        if row:
            rows.append(row)

    print(f"[done] ok={ok} skip={skip} fail={fail}")
    if fail:
        print("[warn] some runs failed; analyze will use available results", file=sys.stderr)

    ref = cfg.get("reference_policy", "tevict")
    add_vs_reference(rows, ref)
    fields = [
        "mode", "policy_id", "policy_label", "category", "trace_id", "workload_type",
        "trace_source", "cluster_fraction", "total_cost", "storage_cost", "transfer_cost",
        "total_requests", "vs_reference_pct", "ec_rate", "milp_ticks", "tag", "trace", "json", "config",
    ]
    out = ROOT / "results" / "3reg_fp_full_matrix_summary.csv"
    write_csv(out, rows, fields)
    print(f"[write] {out}")
    return rows


def phase_analyze(cfg: dict) -> None:
    summary = ROOT / "results" / "3reg_fp_full_matrix_summary.csv"
    if not summary.is_file():
        print("[warn] summary missing; analyze script will scan JSON", file=sys.stderr)
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts" / "analyze_3reg_fp_full_matrix.py"), "--partial"],
        cwd=ROOT,
    )
    # 保留简要 REPORT（兼容旧路径）
    ref = cfg.get("reference_policy", "tevict")
    if summary.is_file():
        rows = list(csv.DictReader(summary.open(encoding="utf-8")))
        md = ROOT / "results" / "3reg_fp_full_matrix_REPORT.md"
        lines = [
            "# 3reg FP 全策略对比矩阵（摘要）",
            "",
            f"详见原因分析: `results/3reg_fp_full_matrix_ANALYSIS.md`",
            "",
            f"参考基线: `{ref}`",
            f"行数: {len(rows)}",
        ]
        md.write_text("\n".join(lines), encoding="utf-8")
        print(f"[write] {md}")


def main() -> None:
    ap = argparse.ArgumentParser(description="3reg FP full SkyStore policy matrix")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "experiment_3reg_fp_full.yaml")
    ap.add_argument("--phase", choices=["synth", "run", "analyze", "all"], default="run")
    ap.add_argument("--trace", help="Only T15/T29/T65/T78/T79")
    ap.add_argument("--workload", help="Only A/B/C/D")
    ap.add_argument("--policy", help="Only one policy id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--force", action="store_true", help="Re-run even if JSON exists")
    args = ap.parse_args()

    cfg = load_yaml(args.config)

    if args.phase in ("synth", "all"):
        phase_synth(cfg)

    if args.phase in ("run", "all"):
        phase_run(cfg, args)
    if args.phase in ("analyze", "all"):
        phase_analyze(cfg)


if __name__ == "__main__":
    main()
