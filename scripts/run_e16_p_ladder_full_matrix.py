#!/usr/bin/env python3
"""E16: Full SkyStore matrix p ladder — 3/6/9-reg × 20 cells × 11 p levels."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
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


def alt_tags(tag: str) -> List[str]:
    """Reuse prior experiment results when tags differ only by prefix or p100 alias."""
    alts = [tag]
    m = re.match(r"e16_(tevict|v4)_(3reg|6reg|9reg)_(T\d+_type[A-D])_(p\d+)", tag)
    if not m:
        return alts
    policy, reg, body, pt = m.group(1), m.group(2), m.group(3), m.group(4)
    pol = "v4_delta" if policy == "v4" else "tevict"
    if pt == "p100":
        alts.append(f"e15_{pol}_{reg}_{body}_p100")
        if reg == "3reg":
            alts.append(f"e14_{pol}_{body}")
    if policy == "tevict" and pt == "p100":
        alts.append(f"e15_tevict_{reg}_{body}_p100")
        if reg == "3reg":
            alts.append(f"e14_tevict_{body}")
    return alts


def latest_result(tag: str) -> Optional[Path]:
    for t in alt_tags(tag):
        matches = sorted(ROOT.glob(f"simulation_results_{t}_*.json"), key=lambda p: p.stat().st_mtime)
        if matches:
            return matches[-1]
    return None


def milp_stats(tag: str) -> Dict[str, float]:
    for t in alt_tags(tag):
        path = ROOT / "results" / f"milp_solve_log_{t}.jsonl"
        if path.exists():
            ticks = ec = 0
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ticks += 1
                if json.loads(line).get("mode") == 1:
                    ec += 1
            return {"milp_ticks": ticks, "ec_ticks": ec, "ec_rate": (ec / ticks) if ticks else 0.0}
    return {"milp_ticks": 0, "ec_ticks": 0, "ec_rate": 0.0}


def cleanup_versioned_artifacts(trace_rel: str) -> None:
    """Remove *-versioned* intermediates to avoid disk exhaustion on large traces."""
    base = ROOT / trace_rel
    parent = base.parent
    stem = base.name
    if not parent.is_dir():
        return
    for p in parent.glob(f"{stem}-versioned*"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in parent.glob(f"{stem}-versioned*.prototype"):
        try:
            p.unlink()
        except OSError:
            pass


def run_sim(tag: str, cfg: str, trace_rel: str, simv: int, vm: int, heap: str) -> bool:
    if not trace_rel:
        if latest_result(tag):
            print(f"[exists] {tag}")
            return True
        print(f"[skip] no trace for {tag}", file=sys.stderr)
        return False
    if not (ROOT / trace_rel).is_file():
        print(f"[skip] missing trace for {tag}: {trace_rel}", file=sys.stderr)
        return False
    if latest_result(tag):
        print(f"[exists] {tag}")
        return True
    args = f"--config {cfg} --trace {trace_rel} --vm {vm} --setbase false --simversion {simv} --tag {tag}"
    env = f"set MAVEN_OPTS=-Xmx{heap}&& " if sys.platform == "win32" else f"MAVEN_OPTS=-Xmx{heap} "
    cmd = f'{env}mvn -q compile exec:java "-Dexec.mainClass=org.example.Main" "-Dexec.args={args}"'
    print(f"[run] {tag}")
    ok = subprocess.run(cmd, cwd=ROOT, shell=True).returncode == 0
    if not ok:
        print(f"[FAIL] {tag}", file=sys.stderr)
        return False
    cleanup_versioned_artifacts(trace_rel)
    milp = ROOT / "results" / "milp_solve_log.jsonl"
    arch = ROOT / "results" / f"milp_solve_log_{tag}.jsonl"
    if not arch.exists() and milp.exists():
        milp.replace(arch)
    return True


def raw_trace_path(reg_id: str, wtype: str, tnum: str) -> Path:
    return ROOT / "Data" / reg_id / reg_id / f"type{wtype}" / f"IBMObjectStoreTrace{tnum}Part0.type{wtype}.mc.aug"


def delete_p_synth_traces(cfg: dict, frac: float) -> int:
    """Delete synth artifacts for one p level (trace + summary + versioned)."""
    pt = p_tag(frac)
    removed = 0
    synth_root = ROOT / "results" / "synth"
    if not synth_root.is_dir():
        return 0
    for p in synth_root.rglob(f"*.{pt}.delta.mc.aug*"):
        try:
            p.unlink()
            removed += 1
            print(f"[delete] {p.relative_to(ROOT)}")
        except OSError as e:
            print(f"[delete fail] {p}: {e}", file=sys.stderr)
    return removed


def delete_synth_except_p(cfg: dict, keep_frac: Optional[float]) -> int:
    """Remove all synth p-traces except optionally one keep_frac."""
    total = 0
    for frac in cfg["fractions"]:
        if keep_frac is not None and abs(float(frac) - float(keep_frac)) < 1e-9:
            continue
        total += delete_p_synth_traces(cfg, frac)
    return total


def synth_one_p(cfg: dict, frac: float) -> None:
    """Synthesize delta traces for a single p across the full 3/6/9-reg matrix."""
    manifest = ROOT / cfg.get("manifest", "results/finesse_merged_manifest.csv")
    if not manifest.is_file():
        raise SystemExit(f"manifest missing: {manifest}")
    seed = int(cfg.get("synth_seed", 42))
    skip = set(cfg.get("skip_traces") or [])
    script = ROOT / "scripts" / "synth_multicloud_delta_trace.py"
    n_ok = n_skip = n_fail = 0
    for rc in cfg["region_configs"]:
        reg_id = rc["id"]
        for tid, tnum in zip(cfg["trace_ids"], cfg["trace_numbers"]):
            if tid in skip:
                continue
            for wtype in cfg["workload_types"]:
                raw = raw_trace_path(reg_id, wtype, tnum)
                out = synth_trace_path(rc["synth_root"], wtype, tnum, frac)
                if out.is_file():
                    n_skip += 1
                    continue
                if not raw.is_file():
                    print(f"[synth warn] missing raw {raw.relative_to(ROOT)}", file=sys.stderr)
                    n_fail += 1
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                summary = Path(str(out) + ".summary.json")
                cmd = [
                    sys.executable,
                    str(script),
                    "augment",
                    "--trace",
                    str(raw),
                    "--manifest",
                    str(manifest),
                    "--out",
                    str(out),
                    "--cluster-fraction",
                    str(frac),
                    "--seed",
                    str(seed),
                    "--summary",
                    str(summary),
                    "--verify-baseline",
                ]
                print(f"[synth] p={frac:.2f} {out.relative_to(ROOT)}")
                proc = subprocess.run(cmd, cwd=ROOT)
                if proc.returncode == 0:
                    n_ok += 1
                else:
                    n_fail += 1
                    raise RuntimeError(f"synth failed: {out}")
    print(f"[synth p={frac:.2f}] ok={n_ok} skip={n_skip} fail={n_fail}")


def build_tasks(cfg: dict, p_only: Optional[float] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (tevict_tasks, v4_tasks). tevict uses raw IBM trace (no synth)."""
    skip = set(cfg.get("skip_traces") or [])
    tevict_tasks: List[Dict[str, Any]] = []
    v4_tasks: List[Dict[str, Any]] = []
    fractions = cfg["fractions"]
    if p_only is not None:
        fractions = [p_only]

    for rc in cfg["region_configs"]:
        for tid, tnum in zip(cfg["trace_ids"], cfg["trace_numbers"]):
            if tid in skip:
                continue
            for wtype in cfg["workload_types"]:
                raw = raw_trace_path(rc["id"], wtype, tnum)
                if raw.is_file():
                    rel_raw = str(raw.relative_to(ROOT)).replace("\\", "/")
                else:
                    rel_raw = ""
                tev_tag = f"e16_tevict_{rc['id']}_{tid}_type{wtype}_p100"
                if rel_raw or latest_result(tev_tag):
                    tevict_tasks.append(
                        {
                            "reg": rc["id"],
                            "trace_id": tid,
                            "workload_type": wtype,
                            "policy": "tevict",
                            "config": rc["tevict"],
                            "simversion": 1,
                            "trace_rel": rel_raw,
                            "tag": tev_tag,
                            "cluster_fraction": float(cfg.get("tevict_reference_p", 0.10)),
                            "p_tag": "p100",
                        }
                    )
                for frac in fractions:
                    tp = synth_trace_path(rc["synth_root"], wtype, tnum, frac)
                    rel = str(tp.relative_to(ROOT)).replace("\\", "/")
                    pt = p_tag(frac)
                    v4_tag = f"e16_v4_{rc['id']}_{tid}_type{wtype}_{pt}"
                    v4_tasks.append(
                        {
                            "reg": rc["id"],
                            "trace_id": tid,
                            "workload_type": wtype,
                            "policy": "v4_delta",
                            "config": rc["v4"],
                            "simversion": int(cfg.get("simversion", 3)),
                            "trace_rel": rel,
                            "tag": v4_tag,
                            "cluster_fraction": frac,
                            "p_tag": pt,
                        }
                    )
    return tevict_tasks, v4_tasks


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
        **{k: t[k] for k in ("reg", "trace_id", "workload_type", "policy", "tag", "trace_rel", "cluster_fraction", "p_tag")},
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
    tev_s: Dict[str, Dict[str, float]] = {}
    for r in rows:
        if r["policy"] == "tevict":
            k = f"{r['reg']}|{r['trace_id']}|{r['workload_type']}"
            tev[k] = float(r["total_cost"])
            tev_s[k] = {
                "storage": float(r["storage_cost"]),
                "transfer": float(r["transfer_cost"]),
            }
    for r in rows:
        k = f"{r['reg']}|{r['trace_id']}|{r['workload_type']}"
        t = tev.get(k)
        if t and r["policy"] == "v4_delta":
            r["vs_tevict_pct"] = (float(r["total_cost"]) - t) / t * 100
            ts = tev_s[k]
            r["delta_total"] = float(r["total_cost"]) - t
            r["delta_storage"] = float(r["storage_cost"]) - ts["storage"]
            r["delta_transfer"] = float(r["transfer_cost"]) - ts["transfer"]
        elif r["policy"] == "tevict":
            r["vs_tevict_pct"] = 0.0
        else:
            r["vs_tevict_pct"] = None


def compile_all(cfg: dict) -> List[Dict[str, Any]]:
    tev_tasks, v4_tasks = build_tasks(cfg)
    rows: List[Dict[str, Any]] = []
    for t in tev_tasks + v4_tasks:
        row = row_from_task(t)
        if row:
            rows.append(row)
    add_vs_tevict(rows)
    return rows


def write_detail_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "reg", "trace_id", "workload_type", "policy", "cluster_fraction", "p_tag",
        "total_cost", "storage_cost", "transfer_cost",
        "storage_share_pct", "transfer_share_pct",
        "vs_tevict_pct", "delta_total", "delta_storage", "delta_transfer",
        "ec_rate", "milp_ticks", "tag", "trace_rel", "json",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["reg"], x.get("p_tag", ""), x["trace_id"], x["workload_type"], x["policy"])))


def fval(x: Any) -> float:
    return float(x) if x not in ("", None) else 0.0


def write_averages(path: Path, rows: List[Dict[str, Any]], cfg: dict) -> List[Dict[str, Any]]:
    v4 = [r for r in rows if r["policy"] == "v4_delta" and r.get("vs_tevict_pct") is not None]
    avg_rows: List[Dict[str, Any]] = []
    for rc in cfg["region_configs"]:
        reg = rc["id"]
        for frac in cfg["fractions"]:
            pt = p_tag(frac)
            cells = [r for r in v4 if r["reg"] == reg and r["p_tag"] == pt]
            if not cells:
                continue
            avg_rows.append(
                {
                    "reg": reg,
                    "cluster_fraction": frac,
                    "p_tag": pt,
                    "cells": len(cells),
                    "avg_vs_tevict_pct": mean(fval(c["vs_tevict_pct"]) for c in cells),
                    "avg_delta_total": mean(fval(c.get("delta_total")) for c in cells),
                    "avg_delta_storage": mean(fval(c.get("delta_storage")) for c in cells),
                    "avg_delta_transfer": mean(fval(c.get("delta_transfer")) for c in cells),
                    "avg_v4_total": mean(fval(c["total_cost"]) for c in cells),
                    "avg_tevict_total": mean(
                        fval(c["total_cost"]) - fval(c.get("delta_total")) for c in cells
                    ),
                    "ec_rate_gt0": sum(1 for c in cells if fval(c.get("ec_rate")) > 0),
                }
            )
    # pooled 3-reg × 20 = 60 cells per p
    for frac in cfg["fractions"]:
        pt = p_tag(frac)
        cells = [r for r in v4 if r["p_tag"] == pt]
        if not cells:
            continue
        avg_rows.append(
            {
                "reg": "ALL",
                "cluster_fraction": frac,
                "p_tag": pt,
                "cells": len(cells),
                "avg_vs_tevict_pct": mean(fval(c["vs_tevict_pct"]) for c in cells),
                "avg_delta_total": mean(fval(c.get("delta_total")) for c in cells),
                "avg_delta_storage": mean(fval(c.get("delta_storage")) for c in cells),
                "avg_delta_transfer": mean(fval(c.get("delta_transfer")) for c in cells),
                "avg_v4_total": mean(fval(c["total_cost"]) for c in cells),
                "avg_tevict_total": mean(
                    fval(c["total_cost"]) - fval(c.get("delta_total")) for c in cells
                ),
                "ec_rate_gt0": sum(1 for c in cells if fval(c.get("ec_rate")) > 0),
            }
        )
    fields = list(avg_rows[0].keys()) if avg_rows else []
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(avg_rows)
    return avg_rows


def write_analysis(
    path: Path,
    detail: List[Dict[str, Any]],
    averages: List[Dict[str, Any]],
    cfg: dict,
) -> None:
    v4 = [r for r in detail if r["policy"] == "v4_delta"]
    lines = [
        "# E16 全矩阵 p 阶梯分析",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## 设定",
        "",
        "- **3/6/9-reg** × **20 格**（5 trace × 4 type）× **11 档 p**",
        f"- p 阶梯: {', '.join(f'{x:.2f}' for x in cfg['fractions'])}",
        "- **r 分布不变**（`finesse_merged_manifest.csv` + seed=42）",
        "- tevict 基线固定（不读 delta 列），V4 按各 p 的增列 trace 计费",
        "",
        f"- 明细行数: {len(detail)}（V4 完成格: {len(v4)} / 期望 660）",
        "",
        "## 每 reg × p：20 格平均 vs tevict",
        "",
        "| reg | p | 格数 | 平均节省% | 平均 Δtotal | 平均 Δstorage | 平均 Δtransfer |",
        "|-----|--:|-----:|----------:|------------:|--------------:|---------------:|",
    ]
    for a in averages:
        if a["reg"] == "ALL":
            continue
        lines.append(
            f"| {a['reg']} | {a['cluster_fraction']:.2f} | {a['cells']} | "
            f"{a['avg_vs_tevict_pct']:+.2f}% | {a['avg_delta_total']:+.2f} | "
            f"{a['avg_delta_storage']:+.2f} | {a['avg_delta_transfer']:+.2f} |"
        )
    lines += [
        "",
        "## 三 reg 合并（每 p 最多 60 格平均）",
        "",
        "| p | 格数 | 平均节省% | 平均 Δtotal |",
        "|--:|-----:|----------:|------------:|",
    ]
    for a in averages:
        if a["reg"] != "ALL":
            continue
        lines.append(
            f"| {a['cluster_fraction']:.2f} | {a['cells']} | "
            f"{a['avg_vs_tevict_pct']:+.2f}% | {a['avg_delta_total']:+.2f} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def phase_synth(cfg: dict) -> None:
    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_phase2_synth_batch.py"),
            "--config",
            str(ROOT / cfg["synth_config"]),
        ],
        cwd=ROOT,
    )


def phase_run(cfg: dict, tev_tasks: List[Dict], v4_tasks: List[Dict], run_tevict: bool = True) -> None:
    heap = cfg.get("maven_heap", "12g")
    vm = int(cfg.get("vm", 5))
    milp = ROOT / "results" / "milp_solve_log.jsonl"
    tasks = (tev_tasks + v4_tasks) if run_tevict else v4_tasks
    for i, t in enumerate(tasks, 1):
        print(f"[task {i}/{len(tasks)}] {t['tag']}")
        if milp.exists():
            milp.unlink()
        run_sim(t["tag"], t["config"], t["trace_rel"], t["simversion"], vm, heap)


def phase_rolling(cfg: dict, single_p: Optional[float] = None) -> None:
    """
    Disk-friendly pipeline per p: delete other p synth → synth this p → run V4 → compile → delete this p synth.
    tevict uses raw IBM traces; results persist in simulation_results_*.json.
    """
    fractions = [single_p] if single_p is not None else cfg["fractions"]
    tev_all, _ = build_tasks(cfg, p_only=None)
    tev_pending = [t for t in tev_all if not latest_result(t["tag"])]
    ran_tevict = False

    for frac in fractions:
        frac = float(frac)
        print(f"\n{'='*60}\n[rolling] p={frac:.2f} ({p_tag(frac)})\n{'='*60}")
        delete_synth_except_p(cfg, keep_frac=None)
        synth_one_p(cfg, frac)
        if tev_pending and not ran_tevict:
            phase_run(cfg, tev_pending, [], run_tevict=True)
            ran_tevict = True
            tev_pending = []
        _, v4_tasks = build_tasks(cfg, p_only=frac)
        phase_run(cfg, [], v4_tasks, run_tevict=False)
        phase_compile(cfg)
        if cfg.get("rolling_delete_synth_after_run", True):
            delete_p_synth_traces(cfg, frac)
            print(f"[rolling] freed synth for p={frac:.2f}")


def phase_compile(cfg: dict) -> None:
    rows = compile_all(cfg)
    detail = ROOT / "results" / "E16_p_ladder_full_summary.csv"
    avg_path = ROOT / "results" / "E16_p_ladder_full_averages.csv"
    write_detail_csv(detail, rows)
    avgs = write_averages(avg_path, rows, cfg)
    write_analysis(ROOT / "results" / "E16_p_ladder_full_ANALYSIS.md", rows, avgs, cfg)
    print(f"Wrote {detail}, {avg_path}, E16_p_ladder_full_ANALYSIS.md")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "experiment_e16_p_ladder_full_matrix.yaml")
    ap.add_argument("--phase", choices=["all", "synth", "run", "analyze", "compile", "rolling", "cleanup-synth"], default="all")
    ap.add_argument("--p", type=float, help="Single p for --phase rolling (e.g. 0.05)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    tev_tasks, v4_tasks = build_tasks(cfg)
    print(f"tevict_tasks={len(tev_tasks)} v4_tasks={len(v4_tasks)} (expect 60 + 660)")

    if args.dry_run:
        for t in (tev_tasks[:3] + v4_tasks[:5]):
            print(t["tag"], t["trace_rel"])
        return

    if args.phase == "cleanup-synth":
        n = delete_synth_except_p(cfg, keep_frac=None)
        print(f"Removed {n} synth files (all p levels)")
        return

    if args.phase == "rolling":
        phase_rolling(cfg, single_p=args.p)
        return

    if args.phase in ("all", "synth"):
        phase_synth(cfg)

    if args.phase in ("all", "run"):
        phase_run(cfg, tev_tasks, v4_tasks)

    if args.phase in ("all", "analyze", "compile"):
        phase_compile(cfg)


if __name__ == "__main__":
    main()
