#!/usr/bin/env python3
"""Fastest smoke: T065 dedup-aware replay (offline zero-error chunks)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.dedup.aware_replay import run_dedup_aware_replay
from src.utils.helpers import load_config


def main() -> int:
    cfg = load_config("config/tevictranges_2reg_dedup.yaml")
    cfg.placement_policy = "dedup_aware_tevict_ranges"
    # prefer env paths on ECS; fall back to local Data/
    trace = Path(
        "/data/sim_exp/python_simulation/traces/IBMObjectStoreTrace065Part0.typeE.mc-aug"
    )
    if not trace.is_file():
        trace = Path(
            r"D:\JavaProjects\Skystore1212\Data\2reg\2reg\IBMObjectStoreTrace065Part0.typeE.mc-aug"
        )
    chunks = Path("/data/dedup_exp/T065")
    out = ROOT / "results_t65" / "dedup_aware_smoke_T065.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # max_ops=0 => full T065 (fast: small chunk store ~9MB)
    result = run_dedup_aware_replay(trace, chunks, cfg, max_ops=0)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"[wrote] {out}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
