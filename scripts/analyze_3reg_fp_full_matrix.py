#!/usr/bin/env python3
"""
3reg FP 全矩阵结果分析 + 原因解读。

读取 results/3reg_fp_full_matrix_summary.csv 与各 simulation JSON，
输出透视表、排名、成本结构分解与相对 Tevict 的原因说明。

用法:
  python scripts/analyze_3reg_fp_full_matrix.py
  python scripts/analyze_3reg_fp_full_matrix.py --partial   # 矩阵未跑完也可分析已有结果
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results" / "3reg_fp_full_matrix_summary.csv"
OUT_MD = ROOT / "results" / "3reg_fp_full_matrix_ANALYSIS.md"
OUT_CSV = ROOT / "results" / "3reg_fp_full_matrix_rankings.csv"

REF = "tevict"

POLICY_NOTES = {
    "always_evict": "每读后立即驱逐副本 → 存储低、跨区读传输高。",
    "always_store": "读时拉取并永久保留 (pull_on_read) → 存储膨胀，读传输中等。",
    "teven": "T_even 固定 TTL 驱逐 → SkyStore 经典启发式之一。",
    "dynamicttl": "TTL-CC：按访问间隔动态 TTL → 热对象留久、冷对象早删。",
    "ewma": "EWMA 预测下次访问 → 自适应保留时长。",
    "tevict": "TevictRanges：配对边直方图 + FP 语义 → SkyStore 主基线。",
    "milp_ec_new": "MILP v1：副本 vs EC，无 delta；早期 EC 系数模型。",
    "milp_ec_new_v3": "MILP v3：FB/FP + EC(2+1)；热 trace 上传输项常压过 EC 存储收益。",
    "milp_ec_new_v4": "MILP v4：delta 成员 size×r + 读时 patch/base 恢复；主方案。",
}


def load_rows() -> List[Dict[str, Any]]:
    if not SUMMARY.is_file():
        return scan_json_tags()
    rows = []
    with SUMMARY.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({k: _num(v) for k, v in r.items()})
    return rows


def parse_fp3reg_tag(tag: str) -> Optional[Tuple[str, str, str]]:
    """fp3reg_{policy}_{Txx}_type{W} -> policy_id, trace_id, workload_type."""
    if not tag.startswith("fp3reg_"):
        return None
    body = tag[len("fp3reg_") :]
    for tid in ("T15", "T29", "T65", "T78", "T79"):
        needle = f"_{tid}_type"
        if needle in body:
            i = body.index(needle)
            policy_id = body[:i]
            wtype = body[i + len(needle) :]
            return policy_id, tid, wtype
    return None


def scan_json_tags() -> List[Dict[str, Any]]:
    """从 simulation_results_fp3reg_* 重建部分 summary（矩阵中断时）。"""
    rows: List[Dict[str, Any]] = []
    for jp in sorted(ROOT.glob("simulation_results_fp3reg_*.json")):
        stem = jp.stem  # simulation_results_fp3reg_...
        tag = stem.replace("simulation_results_", "").rsplit("_", 2)[0]
        parsed = parse_fp3reg_tag(tag)
        if not parsed:
            continue
        policy_id, tid, wtype = parsed
        j = json.loads(jp.read_text(encoding="utf-8"))
        rows.append(
            {
                "policy_id": policy_id,
                "trace_id": tid,
                "workload_type": wtype,
                "total_cost": float(j["total cost ($)"]),
                "storage_cost": float(j["total storage cost ($)"]),
                "transfer_cost": float(j["total transfer cost ($)"]),
                "total_requests": int(j["total requests"]),
                "tag": tag,
            }
        )
    return rows


def _num(v: Any) -> Any:
    if v is None or v == "":
        return v
    try:
        if "." in str(v):
            return float(v)
        return int(v)
    except ValueError:
        return v


def pct(part: float, whole: float) -> float:
    return (part / whole * 100.0) if whole > 0 else 0.0


def diagnose(row: Dict[str, Any], ref: Optional[Dict[str, Any]]) -> str:
    """相对 Tevict 的单格原因一句话。"""
    pid = row["policy_id"]
    tc = float(row["total_cost"])
    st = float(row["storage_cost"])
    tr = float(row["transfer_cost"])
    st_pct = pct(st, tc)
    tr_pct = pct(tr, tc)

    if ref:
        ref_tc = float(ref["total_cost"])
        delta = (tc - ref_tc) / ref_tc * 100.0 if ref_tc else 0.0
        ref_st_pct = pct(float(ref["storage_cost"]), ref_tc)
        ref_tr_pct = pct(float(ref["transfer_cost"]), ref_tc)
    else:
        delta = 0.0
        ref_st_pct = ref_tr_pct = 0.0

    if pid == REF:
        return f"参考基线；存储 {st_pct:.0f}% / 传输 {tr_pct:.0f}%。"

    if delta < -1.0:
        if pid == "milp_ec_new_v4":
            return (
                f"总成本低于 Tevict {abs(delta):.1f}%；"
                f"delta 缩小有效字节 + MILP 布局；存储 {st_pct:.0f}% / 传输 {tr_pct:.0f}%。"
            )
        if st_pct < ref_st_pct - 5:
            return f"比 Tevict 省 {abs(delta):.1f}%，主因存储占比更低（{st_pct:.0f}% vs {ref_st_pct:.0f}%）。"
        if tr_pct < ref_tr_pct - 5:
            return f"比 Tevict 省 {abs(delta):.1f}%，主因传输占比更低（{tr_pct:.0f}% vs {ref_tr_pct:.0f}%）。"
        return f"比 Tevict 省 {abs(delta):.1f}%；存储 {st_pct:.0f}% / 传输 {tr_pct:.0f}%。"

    if delta > 1.0:
        if pid in ("always_store", "replicate_all"):
            return f"比 Tevict 贵 {delta:.1f}%；多副本/永久保留 → 存储项主导（{st_pct:.0f}%）。"
        if pid in ("milp_ec_new_v3", "milp_ec_new") and tr_pct > 55:
            return (
                f"比 Tevict 贵 {delta:.1f}%；MILP 选 EC 时读/写跨区传片 → 传输 {tr_pct:.0f}% "
                f"（设计 §7.8：热 trace 上 EC 难赢副本）。"
            )
        if pid == "milp_ec_new_v4" and tr_pct > ref_tr_pct + 5:
            return (
                f"比 Tevict 贵 {delta:.1f}%；读 delta 成员需拉 base → 读恢复传输偏高（{tr_pct:.0f}%）。"
            )
        if pid == "always_evict":
            return f"比 Tevict 贵 {delta:.1f}%；频繁驱逐后再拉取 → 传输 {tr_pct:.0f}%。"
        return f"比 Tevict 贵 {delta:.1f}%；存储 {st_pct:.0f}% / 传输 {tr_pct:.0f}%。"

    return f"与 Tevict 接近（{delta:+.1f}%）；存储 {st_pct:.0f}% / 传输 {tr_pct:.0f}%。"


def build_rankings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_cell: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = f"{r['trace_id']}|{r.get('workload_type', r.get('workload', ''))}"
        by_cell[key].append(r)

    rankings: List[Dict[str, Any]] = []
    for cell, rs in sorted(by_cell.items()):
        tid, wtype = cell.split("|")
        rs_sorted = sorted(rs, key=lambda x: float(x["total_cost"]))
        ref = next((x for x in rs if x["policy_id"] == REF), None)
        for rank, r in enumerate(rs_sorted, 1):
            tc = float(r["total_cost"])
            ref_tc = float(ref["total_cost"]) if ref else tc
            rankings.append(
                {
                    "trace_id": tid,
                    "workload_type": wtype,
                    "rank": rank,
                    "policy_id": r["policy_id"],
                    "total_cost": tc,
                    "storage_pct": round(pct(float(r["storage_cost"]), tc), 1),
                    "transfer_pct": round(pct(float(r["transfer_cost"]), tc), 1),
                    "vs_tevict_pct": round((tc - ref_tc) / ref_tc * 100.0, 2) if ref_tc else 0.0,
                    "winner": rank == 1,
                    "reason": diagnose(r, ref),
                }
            )
    return rankings


def write_report(rows: List[Dict[str, Any]], rankings: List[Dict[str, Any]]) -> None:
    lines = [
        "# 3reg FP 全策略对比 — 结果与原因分析",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## 实验设置",
        "",
        "- **模式**: FP (`setbase=false`)",
        "- **参考基线**: TevictRanges (`tevict`)",
        "- **成本**: 总成本 = 存储 + 跨区传输（仿真器 Tracker）",
        "",
        "## 策略说明",
        "",
    ]
    for pid, note in POLICY_NOTES.items():
        lines.append(f"- **{pid}**: {note}")

    lines += ["", "## 各 (trace, type) 成本排名", ""]
    by_cell: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rankings:
        by_cell[f"{r['trace_id']}|{r['workload_type']}"].append(r)

    for cell in sorted(by_cell.keys()):
        tid, wtype = cell.split("|")
        lines.append(f"### {tid} / type{wtype}")
        lines.append("")
        lines.append("| 排名 | 策略 | 总成本 | vs Tevict | 存储% | 传输% | 原因 |")
        lines.append("|-----:|------|-------:|----------:|------:|------:|------|")
        for r in sorted(by_cell[cell], key=lambda x: x["rank"]):
            lines.append(
                f"| {r['rank']} | `{r['policy_id']}` | {r['total_cost']:.2f} | "
                f"{r['vs_tevict_pct']:+.1f}% | {r['storage_pct']:.0f}% | {r['transfer_pct']:.0f}% | {r['reason']} |"
            )
        lines.append("")

    # V4 summary across cells
    v4_rows = [r for r in rankings if r["policy_id"] == "milp_ec_new_v4"]
    if v4_rows:
        wins = sum(1 for r in v4_rows if r["winner"])
        beats = sum(1 for r in v4_rows if float(r["vs_tevict_pct"]) < -1.0)
        worse = sum(1 for r in v4_rows if float(r["vs_tevict_pct"]) > 1.0)
        avg_vs = sum(float(r["vs_tevict_pct"]) for r in v4_rows) / len(v4_rows)
        lines += [
            "## V4 (milp_ec_new_v4) 汇总",
            "",
            f"- 单元格数: {len(v4_rows)}",
            f"- 最便宜策略次数: {wins}",
            f"- 低于 Tevict (>1%): {beats}",
            f"- 高于 Tevict (>1%): {worse}",
            f"- 平均 vs Tevict: {avg_vs:+.1f}%",
            "",
            "### 机制解读（与设计文档一致）",
            "",
            "1. **存储项**：V4 对 delta 成员按 `size×r` 计费 → 相似簇内 patch 存储远小于全量对象。",
            "2. **传输项**：读 delta 时若 base 不在读区，需额外拉整份 base → 可能抵消 delta 收益（L4 布局）。",
            "3. **MILP+EC**：纯 V3 在热 trace 上常因「读分片重建」传输过高而选副本；V4 缩小有效字节后才可能翻盘。",
            "4. **FP 模式**：base 可迁移，与 FB 结果不可直接比。",
            "",
        ]

    lines += [
        "## 读结果时的注意",
        "",
        "- V4 使用 **synth p100** trace（含 delta 列）；基线用 **raw** trace — 访问行相同，基线忽略 delta 列。",
        "- 若 V4 全面贵于 Tevict，优先查 **传输占比** 与 **base 读恢复**，而非仅看 r。",
        "",
    ]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"[write] {OUT_MD}")

    if rankings:
        fields = list(rankings[0].keys())
        with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rankings)
        print(f"[write] {OUT_CSV}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partial", action="store_true", help="Analyze from JSON even if summary incomplete")
    args = ap.parse_args()

    rows = load_rows()
    if not rows and args.partial:
        rows = scan_json_tags()
    if not rows:
        raise SystemExit(f"No data in {SUMMARY} or fp3reg JSON files")

    rankings = build_rankings(rows)
    write_report(rows, rankings)


if __name__ == "__main__":
    main()
