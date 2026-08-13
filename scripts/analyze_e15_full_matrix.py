#!/usr/bin/env python3
"""Analyze E15 full SkyStore matrix: 20-cell averages per reg, storage/transfer balance."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]


def load_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def f(x: Any) -> float:
    return float(x) if x not in ("", None) else 0.0


def main() -> None:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results" / "E15_full_matrix_summary.csv"
    rows = load_rows(csv_path)
    v4 = [r for r in rows if r["policy"] == "v4_delta" and r.get("workload_type") in ("A", "B", "C", "D")]
    tev = [r for r in rows if r["policy"] == "tevict" and r.get("workload_type") in ("A", "B", "C", "D")]

    by_reg_v4: Dict[str, List[Dict]] = defaultdict(list)
    for r in v4:
        by_reg_v4[r["reg"]].append(r)

    lines = [
        "# E15 全矩阵分析：SkyStore 对齐（3/6/9-reg × 20 格）",
        "",
        "## 1. 实验规模",
        "",
        "- **每 reg 20 格** = 5 trace（T15/T29/T65/T78/T79）× 4 type（A–D）",
        "- **3/6/9-reg** 各跑 tevict（SkyStore 基线）与 V4（相似检测+差分+EC 可选）",
        "- **2-reg**：仓库仅 **typeE** 原始 trace，无 A–D 增列 trace，不作 20 格主表",
        "",
        f"- 本 CSV 记录数：{len(rows)}（V4 主矩阵格：{len(v4)}）",
        "",
        "## 2. 每 reg 20 格平均（V4 vs tevict）",
        "",
        "| reg | 平均 total 节省% | 平均 Δstorage ($) | 平均 Δtransfer ($) | 平均 storage占比(V4) | 平均 transfer占比(V4) | ec_rate>0 格数 |",
        "|-----|----------------:|------------------:|-------------------:|---------------------:|----------------------:|---------------:|",
    ]

    for reg in sorted(by_reg_v4.keys(), key=lambda x: int(x.replace("reg", ""))):
        cells = by_reg_v4[reg]
        if not cells:
            continue
        vs = [f(c.get("vs_tevict_pct")) for c in cells]
        ds = [f(c.get("delta_storage")) for c in cells]
        dt = [f(c.get("delta_transfer")) for c in cells]
        ss = [f(c.get("storage_share_pct")) for c in cells]
        ts = [f(c.get("transfer_share_pct")) for c in cells]
        ec_n = sum(1 for c in cells if f(c.get("ec_rate")) > 0)
        lines.append(
            f"| {reg} | {mean(vs):+.2f}% | {mean(ds):+.2f} | {mean(dt):+.2f} | "
            f"{mean(ss):.1f}% | {mean(ts):.1f}% | {ec_n}/{len(cells)} |"
        )

    lines += [
        "",
        "## 3. 分 trace 平均（跨 reg × type，V4 vs tevict %）",
        "",
        "| trace | 平均 vs tevict | 格数 |",
        "|-------|---------------:|-----:|",
    ]
    by_trace: Dict[str, List[float]] = defaultdict(list)
    for r in v4:
        by_trace[r["trace_id"]].append(f(r.get("vs_tevict_pct")))
    for tid in ["T15", "T29", "T65", "T78", "T79"]:
        if tid in by_trace:
            lines.append(f"| {tid} | {mean(by_trace[tid]):+.2f}% | {len(by_trace[tid])} |")

    lines += [
        "",
        "## 4. 分 type 平均（跨 reg × trace）",
        "",
        "| type | 平均 vs tevict |",
        "|------|---------------:|",
    ]
    by_type: Dict[str, List[float]] = defaultdict(list)
    for r in v4:
        by_type[r["workload_type"]].append(f(r.get("vs_tevict_pct")))
    for w in ["A", "B", "C", "D"]:
        if w in by_type:
            lines.append(f"| type{w} | {mean(by_type[w]):+.2f}% |")

    lines += [
        "",
        "## 5. 方法作用与 incremental 叙事（存储–运费权衡）",
        "",
        "### 5.1 SkyStore（tevict）在做什么",
        "",
        "SkyStore 把对象视为**按 key/版本独立**的实体，优化 **TTL + 整副本放置**：",
        "读 miss 时 **replicate-on-read** 拉到本地再存一份，用**存储费**换**少付 egress**——",
        "因云价目表上 **跨区域运费通常高于存储单价**，原作者刻意**减少重复拉取**。",
        "",
        "### 5.2 相似检测 + 差分编码（本文主线）",
        "",
        "SkyStore **未建模上传序列中的 resemblance**。本文用 Finesse manifest 标注相似 member，",
        "对 member 按 **r·V** 计存储/传输：",
        "- **效果**：多数格 **Δtransfer 为负**（少搬字节），**Δstorage 略降或略升**；",
        "  T78/T79 等写多读少 trace 上 total 节省更明显。",
        "- **代价**：读 member 可能需 **base+delta 两次拉取**，在运费贵时抵消部分收益——",
        "  这是 **incremental 张力**，不是 SkyStore 原设计的 free lunch。",
        "",
        "### 5.3 EC（MILP 可选分支）",
        "",
        "EC 用 **更低存储占用（V/k）** 换 **更多分片放置与读恢复时的传输/计算**；",
        "在**容错/持久性**上价值大，但在当前 IBM 价目表 + FP 下 **MILP 几乎不选 EC**（ec_rate≈0）。",
        "单独 force EC 略增 total——与「运费贵、读恢复贵」一致。",
        "EC 与 δ **无正向叠加**：δ 已缩小体积，EC 存储优势进一步被 egress 抵消。",
        "",
        "### 5.4 聪明之处（论文论点）",
        "",
        "1. **语义补洞**：在 SkyStore 成本框架内显式计入 **upload-time 相似冗余**。",
        "2. **可分解**：20 格/reg 平均 + storage/transfer 分项，说明钱从哪省、从哪花。",
        "3. **诚实 incremental**：不替代 SkyStore 的 TTL 智慧，而是在其之上 **小幅** 降账单；",
        "   δ 与 EC 都要在 **存储–运费** 天平上重新证明划算。",
        "",
        "## 6. 主矩阵明细（V4 vs tevict %）",
        "",
    ]

    for reg in sorted(by_reg_v4.keys(), key=lambda x: int(x.replace("reg", ""))):
        lines.append(f"### {reg}")
        lines.append("")
        lines.append("| trace | typeA | typeB | typeC | typeD |")
        lines.append("|-------|------:|------:|------:|------:|")
        for tid in ["T15", "T29", "T65", "T78", "T79"]:
            cells = []
            for w in ["A", "B", "C", "D"]:
                hit = next(
                    (x for x in by_reg_v4[reg] if x["trace_id"] == tid and x["workload_type"] == w),
                    None,
                )
                cells.append(f"{f(hit.get('vs_tevict_pct')):+.1f}%" if hit else "—")
            lines.append(f"| {tid} | " + " | ".join(cells) + " |")
        lines.append("")

    out = ROOT / "results" / "E15_full_matrix_ANALYSIS.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
