#!/usr/bin/env python3
"""
组合矩阵 E00–E12 批量实验驱动（PolicyMilpECnewV4_Design.md §9.5）。

依赖: 阶段 2c Gurobi 触发修复 + 阶段 3 PolicyMilpECnewV4 实现完成后方可全量跑。
当前为计划占位：解析配置、列出任务、写出 matrix_manifest.json。

用法:
  python scripts/run_experiment_matrix.py --config config/experiment_matrix.yaml --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def main() -> None:
    ap = argparse.ArgumentParser(description="E00–E12 实验矩阵")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "experiment_matrix.yaml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if yaml is None:
        raise SystemExit("需要 PyYAML")
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    traces = cfg.get("traces", [])
    policies = cfg.get("policies", [])
    tasks = []
    for tr in traces:
        for pol in policies:
            tasks.append({"trace": tr, "policy_id": pol["id"], "policy": pol})
    out_dir = ROOT / cfg.get("output_dir", "results/experiments")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "matrix_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "planned",
                "blocked_on": ["§9.3 Gurobi hist trigger", "§9.4 PolicyMilpECnewV4"],
                "task_count": len(tasks),
                "tasks": tasks,
                "hand_calc_compass": cfg.get("hand_calc_compass"),
                "trace_characteristics": cfg.get("trace_characteristics"),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[matrix] {len(tasks)} tasks (dry_run={args.dry_run})")
    print(f"[matrix] manifest -> {manifest.relative_to(ROOT)}")
    if args.dry_run:
        for t in tasks[:10]:
            print(f"  {t['policy_id']} x {t['trace']}")
        if len(tasks) > 10:
            print(f"  ... +{len(tasks)-10} more")


if __name__ == "__main__":
    main()
