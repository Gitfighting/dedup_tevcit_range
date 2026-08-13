#!/usr/bin/env python3
"""
阶段 2 批量合成驱动：按 YAML 配置对多条 IBM trace 追加 delta 列。

设计依据：PolicyMilpECnewV4_Design.md §5.2 / §10

用法：
  # 先合并 manifest（sqldump + 可选 kernel）
  python scripts/merge_finesse_manifests.py \\
    --finesse results/finesse_sqldump_manifest.csv \\
    --out results/finesse_merged_manifest.csv

  # 批量合成
  python scripts/run_phase2_synth_batch.py --config config/phase2_synth_batch.yaml

  # 只列任务、不执行
  python scripts/run_phase2_synth_batch.py --config config/phase2_synth_batch.yaml --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text)
    # 极简 fallback：仅支持当前仓库 yaml 结构时可用 pip install pyyaml
    raise SystemExit("需要 PyYAML: pip install pyyaml")


def resolve_traces(globs: List[str]) -> List[Path]:
    found: List[Path] = []
    for pattern in globs:
        for p in sorted(glob(str(ROOT / pattern), recursive=True)):
            path = Path(p)
            if path.is_file() and (
                path.suffix == ".aug" or path.name.endswith(".mc.aug")
            ):
                found.append(path)
    # 去重保序
    seen = set()
    out: List[Path] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def output_path_for(
    trace: Path,
    output_root: Path,
    cluster_fraction: float,
) -> Path:
    """
    Data/3reg/3reg/typeA/IBM...typeA.mc.aug
    -> results/synth/3reg/typeA/IBM...typeA.p005.delta.mc.aug
    """
    rel = trace.relative_to(ROOT)
    parts = rel.parts
    reg = "unknown"
    wtype = "unknown"
    for i, part in enumerate(parts):
        if part.endswith("reg") and part[:-3].isdigit():
            reg = part
        if part.lower().startswith("type") and len(part) == 5:
            wtype = part
    p_tag = f"p{int(cluster_fraction * 1000):03d}"
    stem = trace.name.replace(".mc.aug", f".{p_tag}.delta.mc.aug")
    return output_root / reg / wtype / stem


def run_one(
    mode: str,
    trace: Path,
    manifest: Path,
    out: Path,
    cluster_fraction: float,
    seed: int,
    verify_baseline: bool,
) -> dict:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "synth_multicloud_delta_trace.py"),
        mode,
        "--trace",
        str(trace),
        "--manifest",
        str(manifest),
        "--out",
        str(out),
        "--cluster-fraction",
        str(cluster_fraction),
        "--seed",
        str(seed),
        "--summary",
        str(out.with_suffix(out.suffix + ".summary.json")),
    ]
    if verify_baseline:
        cmd.append("--verify-baseline")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    elapsed = time.time() - t0
    return {
        "trace": str(trace.relative_to(ROOT)),
        "output": str(out.relative_to(ROOT)),
        "ok": proc.returncode == 0,
        "elapsed_sec": round(elapsed, 2),
        "stdout_tail": proc.stdout.strip()[-500:] if proc.stdout else "",
        "stderr_tail": proc.stderr.strip()[-500:] if proc.stderr else "",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="阶段 2 批量 trace 合成")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "phase2_synth_batch.yaml")
    ap.add_argument("--dry-run", action="store_true", help="只列出任务")
    ap.add_argument("--limit", type=int, help="最多处理 N 个 trace（调试）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    manifest = ROOT / cfg["manifest"]
    if not manifest.is_file():
        raise SystemExit(
            f"manifest 不存在: {manifest}\n"
            "请先运行: python scripts/merge_finesse_manifests.py "
            "--finesse results/finesse_sqldump_manifest.csv "
            "--out results/finesse_merged_manifest.csv"
        )

    traces = resolve_traces(cfg.get("trace_globs", []))
    if not traces:
        raise SystemExit("trace_globs 未匹配到任何 .mc.aug 文件")

    fractions: List[float] = cfg.get("cluster_fractions", [0.05])
    output_root = ROOT / cfg.get("output_root", "results/synth")
    mode = cfg.get("mode", "augment")
    seed = int(cfg.get("seed", 42))
    verify = bool(cfg.get("verify_baseline", True))
    skip_existing = bool(cfg.get("skip_existing", True))

    tasks: List[dict] = []
    for trace in traces:
        for frac in fractions:
            out = output_path_for(trace, output_root, frac)
            tasks.append(
                {
                    "trace": trace,
                    "out": out,
                    "fraction": frac,
                    "skip": skip_existing and out.is_file(),
                }
            )

    if args.limit:
        tasks = tasks[: args.limit]

    print(f"[batch] manifest={manifest.relative_to(ROOT)}")
    print(f"[batch] 任务数={len(tasks)} (traces={len(traces)}, fractions={fractions})")

    if args.dry_run:
        for t in tasks:
            status = "SKIP" if t["skip"] else "RUN"
            print(
                f"  [{status}] p={t['fraction']:.3f} "
                f"{t['trace'].relative_to(ROOT)} -> {t['out'].relative_to(ROOT)}"
            )
        return

    results: List[dict] = []
    ok_count = 0
    skip_count = 0
    for i, t in enumerate(tasks, 1):
        if t["skip"]:
            skip_count += 1
            results.append(
                {
                    "trace": str(t["trace"].relative_to(ROOT)),
                    "output": str(t["out"].relative_to(ROOT)),
                    "ok": True,
                    "skipped": True,
                }
            )
            continue
        print(
            f"[batch] ({i}/{len(tasks)}) p={t['fraction']:.3f} "
            f"{t['trace'].name} ...",
            flush=True,
        )
        t["out"].parent.mkdir(parents=True, exist_ok=True)
        r = run_one(
            mode,
            t["trace"],
            manifest,
            t["out"],
            t["fraction"],
            seed,
            verify,
        )
        r["skipped"] = False
        r["cluster_fraction"] = t["fraction"]
        results.append(r)
        if r["ok"]:
            ok_count += 1
            print(f"  OK ({r['elapsed_sec']}s) -> {r['output']}")
        else:
            print(f"  FAIL: {r['stderr_tail']}", file=sys.stderr)

    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(ROOT.resolve()))
        except ValueError:
            return str(p)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": rel(args.config),
        "manifest": rel(manifest),
        "mode": mode,
        "seed": seed,
        "tasks_total": len(tasks),
        "ok": ok_count,
        "skipped": skip_count,
        "failed": len(tasks) - ok_count - skip_count,
        "results": results,
    }
    report_path = output_root / "batch_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[batch] 完成: ok={ok_count} skipped={skip_count} "
          f"failed={report['failed']}")
    print(f"[batch] 报告: {report_path.relative_to(ROOT)}")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
