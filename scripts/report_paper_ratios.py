#!/usr/bin/env python3
"""Report baseline/SkyStore cost ratios for fb3reg new runs."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ["always_evict", "always_store", "teven", "ewma", "optimal"]
REF = "tevict"
TRACES = ["T15", "T65", "T78", "T79"]
TYPES = ["A", "B", "C", "D"]


def load_cost(pol: str, tid: str, wtype: str) -> float | None:
    tag = f"fb3reg_{pol}_{tid}_type{wtype}"
    files = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    return float(json.loads(files[-1].read_text(encoding="utf-8"))["total cost ($)"])


def ratio(num: float | None, den: float | None) -> str:
    if num is None or den is None or den <= 0:
        return "—"
    return f"{num/den:.2f}"


def main() -> None:
    # per cell
    print("=== Java fb3reg 新跑: baseline cost / SkyStore (tevict) ===")
    print("trace,type," + ",".join(POLICIES))
    by_type = defaultdict(lambda: defaultdict(list))
    for tid in TRACES:
        for wtype in TYPES:
            ref = load_cost(REF, tid, wtype)
            vals = [ratio(load_cost(p, tid, wtype), ref) for p in POLICIES]
            print(f"{tid},{wtype}," + ",".join(vals))
            if ref and ref > 0:
                for p in POLICIES:
                    c = load_cost(p, tid, wtype)
                    if c is not None:
                        by_type[wtype][p].append(c / ref)

    print("\n=== Java fb3reg 新跑: 按 type 对 trace 平均 (Table 4 口径) ===")
    print("type," + ",".join(POLICIES))
    for wtype in TYPES:
        row = [f"{mean(by_type[wtype][p]):.2f}" if by_type[wtype][p] else "—" for p in POLICIES]
        print(f"{wtype}," + ",".join(row))

    all_p = defaultdict(list)
    for wtype in TYPES:
        for p in POLICIES:
            all_p[p].extend(by_type[wtype][p])
    print("\n=== Java fb3reg 新跑: 全格平均 ===")
    for p in POLICIES:
        print(f"{p}: {mean(all_p[p]):.2f}x" if all_p[p] else f"{p}: —")


if __name__ == "__main__":
    main()
