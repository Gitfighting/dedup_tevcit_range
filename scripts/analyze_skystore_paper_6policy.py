#!/usr/bin/env python3
"""Analyze 6-policy Java FP results vs SkyStore paper trends."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ["always_evict", "always_store", "teven", "ewma", "tevict", "optimal"]
LABELS = {
    "always_evict": "Always Evict",
    "always_store": "Always Store",
    "teven": "T_even",
    "ewma": "EWMA",
    "tevict": "SkyStore (tevict_ranges)",
    "optimal": "CGP/Optimal",
}
TRACES = ["T15", "T65", "T78", "T79"]
TYPES = ["A", "B", "C", "D"]
REF = "tevict"


def load_rows() -> list[dict]:
    rows = []
    for pol in POLICIES:
        for tid in TRACES:
            for wtype in TYPES:
                tag = f"fp3reg_{pol}_{tid}_type{wtype}"
                matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
                if not matches:
                    continue
                j = json.loads(matches[-1].read_text(encoding="utf-8"))
                tc = float(j["total cost ($)"])
                st = float(j["total storage cost ($)"])
                tr = float(j["total transfer cost ($)"])
                rows.append({
                    "policy_id": pol,
                    "trace_id": tid,
                    "workload_type": wtype,
                    "total_cost": tc,
                    "storage_cost": st,
                    "transfer_cost": tr,
                    "storage_pct": 100 * st / tc if tc else 0,
                    "transfer_pct": 100 * tr / tc if tc else 0,
                    "json": matches[-1].name,
                })
    return rows


def add_ratios(rows: list[dict]) -> None:
    ref = {}
    for r in rows:
        if r["policy_id"] == REF:
            ref[(r["trace_id"], r["workload_type"])] = r["total_cost"]
    for r in rows:
        base = ref.get((r["trace_id"], r["workload_type"]))
        r["vs_skystore_x"] = (r["total_cost"] / base) if base and base > 0 else None


def paper_expected_order() -> list[str]:
    # cheapest -> most expensive (typical paper trend, SkyStore near optimal)
    return ["optimal", "tevict", "teven", "ewma", "always_store", "always_evict"]


def main() -> None:
    rows = load_rows()
    add_ratios(rows)
    out_csv = ROOT / "results" / "skystore_paper_6policy_fp3reg.csv"
    fields = [
        "policy_id", "trace_id", "workload_type", "total_cost", "storage_cost",
        "transfer_cost", "storage_pct", "transfer_pct", "vs_skystore_x", "json",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # per-cell ranking
    cells = defaultdict(list)
    for r in rows:
        cells[(r["trace_id"], r["workload_type"])].append(r)
    rank_rows = []
    trend_match = 0
    trend_total = 0
    for key, rs in sorted(cells.items()):
        rs_sorted = sorted(rs, key=lambda x: x["total_cost"])
        order = [x["policy_id"] for x in rs_sorted]
        if len(order) >= 4:
            trend_total += 1
            # paper: tevict beats teven/ewma/always_* ; optimal <= tevict
            ok = (
                order.index("tevict") < order.index("teven")
                and order.index("tevict") < order.index("always_evict")
                and order.index("tevict") < order.index("always_store")
                and ("optimal" not in order or order.index("optimal") <= order.index("tevict"))
            )
            if ok:
                trend_match += 1
        for i, r in enumerate(rs_sorted, 1):
            rank_rows.append({**r, "rank": i, "cell": f"{key[0]}/{key[1]}"})

    md = ROOT / "results" / "skystore_paper_6policy_fp3reg_ANALYSIS.md"
    lines = [
        "# SkyStore 论文六策略 Java 复现（3reg FP）",
        "",
        f"- 数据格数（有结果的 trace×type）: **{len(cells)}** / 16",
        f"- 论文核心趋势匹配（tevict 优于 teven/always_*，optimal ≤ tevict）: **{trend_match}/{trend_total}** 格",
        "",
        "## 论文预期（Section 6, Table 4–5）",
        "",
        "1. **SkyStore (tevict)** 总成本低于 Always Evict、Always Store、T_even、EWMA。",
        "2. **CGP/Optimal** 为下界，SkyStore 平均在其 ~15% 以内（2-region Table 3）。",
        "3. Always Evict：传输极高；Always Store：存储极高；T_even/EWMA 居中但差于自适应 TTL。",
        "",
        "## 各格成本排名（$ 越低越好）",
        "",
    ]
    for key in sorted(cells):
        rs = sorted(cells[key], key=lambda x: x["total_cost"])
        lines.append(f"### {key[0]} / type{key[1]}")
        lines.append("")
        lines.append("| 排名 | 策略 | 总成本 | vs SkyStore | 存储% | 传输% |")
        lines.append("|-----:|------|-------:|------------:|------:|------:|")
        for i, r in enumerate(rs, 1):
            vs = f"{r['vs_skystore_x']:.2f}x" if r.get("vs_skystore_x") else "—"
            lines.append(
                f"| {i} | {LABELS[r['policy_id']]} | {r['total_cost']:.2f} | {vs} | "
                f"{r['storage_pct']:.0f}% | {r['transfer_pct']:.0f}% |"
            )
        lines.append("")

    # aggregate vs skystore by policy
    lines.append("## 相对 SkyStore 的平均倍数（全格平均）")
    lines.append("")
    lines.append("| 策略 | 平均 cost/SkyStore |")
    lines.append("|------|-------------------:|")
    by_pol = defaultdict(list)
    for r in rows:
        if r.get("vs_skystore_x") and r["policy_id"] != REF:
            by_pol[r["policy_id"]].append(r["vs_skystore_x"])
    for pol in POLICIES:
        if pol == REF:
            continue
        xs = by_pol.get(pol, [])
        if xs:
            lines.append(f"| {LABELS[pol]} | {mean(xs):.2f}x |")
        else:
            lines.append(f"| {LABELS[pol]} | — |")

    lines.append("")
    lines.append("## T_even vs tevict_ranges（/3 口径差异）")
    lines.append("")
    lines.append("- **teven**：`SimulatorV2` 用 `N/S × 86400 / 3`，与 `RegionManager` 存储 ×3 计费一致。")
    lines.append("- **tevict_ranges**：`getTevict` 内 `teven = N/S × 86400`（无 /3），但直方图 `calcEvictCost` 内部自洽。")
    lines.append("- 若 Java 趋势与论文一致，说明 artifact 按作者实现复现；teven/tevict 绝对 TTL 差异不一定改变相对排名。")
    lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_csv}")
    print(f"Wrote {md}")
    print(f"Trend match: {trend_match}/{trend_total}")


if __name__ == "__main__":
    main()
