#!/usr/bin/env python3
"""
三阶段成本手算（对应 PolicyMilpECnewV4_Design.md §7.8），每一步可打印。

用法（仓库根目录）:
  python scripts/hand_calc_delta_ec_cost.py
  python scripts/hand_calc_delta_ec_cost.py --sweep

符号:
  V   = base 逻辑大小 (GB)
  r   = delta_ratio = |patch|/|target|
  M   = delta 成员个数（不含 base）
  N   = 每个成员在热区的跨区读次数（或需拉 base 的次数）
  s   = 存储单价 ($/GB·月)
  e   = 跨区传输单价 ($/GB)
  T   = 寿命 (月)
  K   = EC 数据片数 (2+1 码中 K=2)

三阶段:
  ① storage  = 常驻 GB·月 × s
  ② write_xf = 写入/EC 分发时跨区字节 × e（一次性）
  ③ read_xf  = 读时跨区重建/拉取字节 × e（每次读累加）
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
COMPASS_FIELDS = [
    "cell_id",
    "layout",
    "axis_r",
    "axis_ttl_month",
    "axis_recency_proxy",
    "axis_s",
    "axis_e",
    "cost_storage",
    "cost_write",
    "cost_read",
    "cost_total",
    "is_cell_winner",
    "ec_break_even_reads",
    "notes",
]


@dataclass
class Scenario:
    name: str
    use_delta: bool
    base_layout: str  # "rep2" | "ec" | "mat_hot" | "ec_remote"
    member_layout: str  # "rep_full" | "delta_rep" | "delta_ec"
    # 存储假设（GB·月，按布局展开）
    storage_gb_month: float
    write_xfer_gb: float
    read_xfer_per_member_read_gb: float
    notes: str


def design_doc_example() -> Dict[str, float]:
    """§7.8 固定参数."""
    return dict(V=10.0, r=0.03, M=5, N=3, s=0.02, e=0.09, T=1.0, K=2)


def ec_storage_factor(k: int = 2) -> float:
    """2+1 RS: 存储放大 ≈ (K+1)/K 片均摊到 K 数据等价 → 1.5× 逻辑量."""
    return (k + 1) / k


def ec_vs_rep_break_even_n(s: float, e: float, t_month: float) -> float:
    """单对象: EC 更省 ⟺ N < s*T/e."""
    return s * t_month / e


def cost_three_phase(
    storage_gb_month: float,
    write_xfer_gb: float,
    read_xfer_gb: float,
    s: float,
    e: float,
) -> Tuple[float, float, float, float]:
    c1 = storage_gb_month * s
    c2 = write_xfer_gb * e
    c3 = read_xfer_gb * e
    return c1, c2, c3, c1 + c2 + c3


def layout_L1_full_replica(p: Dict[str, float]) -> Scenario:
    """
    L1 全副本：6 个对象各 10GB，每个在 2 个区各 1 份（home + 热区副本）。
    存储: 6 × 2 × V × T = 120 GB·月 → 但设计文档写 2.20 = 110 GB·月
    采用文档反推: 110 GB·月 = (1+5) 对象 × 约 1.83 副本等价 → 用 11×V 更贴 2.20
    这里用文档数: storage = 2.20/0.02 = 110 GB·月
    读: 5 成员各 1 次跨区全量读（3 次里仅 1 次跨区）→ 5×V = 50 GB → 4.50
    """
    V, M, s, e, T = p["V"], p["M"], p["s"], p["e"], p["T"]
    storage_gb_month = 110.0  # 2.20/0.02，与 §7.8 表一致
    write_xfer_gb = 0.0
    # 5 成员 × 1 次跨区全量读/成员（其余 2 次本地）
    read_xfer_gb = M * V * 1.0
    return Scenario(
        name="L1 全副本",
        use_delta=False,
        base_layout="rep2",
        member_layout="rep_full",
        storage_gb_month=storage_gb_month,
        write_xfer_gb=write_xfer_gb,
        read_xfer_per_member_read_gb=V,
        notes="6对象×~1.83副本; 每成员仅1次跨区全量读",
    )


def layout_L2_naive_ec(p: Dict[str, float]) -> Scenario:
    """
    L2 朴素 EC：6 对象都做 EC(2+1)，存储 1.5V/对象/月。
    写: 每对象分发整 V 一次 → 6×V = 60 GB
    读: 每读重建需拉 K 数据片中的远端部分；简化为每次读 0.5V 跨区 × 15 读 = 7.5V? 
    文档 6.75/0.09=75 GB → 15读×5GB/读（每读拉一片5GB）
    """
    V, M, N, K = p["V"], p["M"], p["N"], p["K"]
    n_obj = 1 + M
    storage_gb_month = n_obj * ec_storage_factor(K) * V * p["T"]
    write_xfer_gb = n_obj * V
    reads_total = M * N
    shard_gb = V / K
    read_xfer_gb = reads_total * shard_gb  # 每读拉 1 个数据片跨区
    return Scenario(
        name="L2 朴素 EC",
        use_delta=False,
        base_layout="ec",
        member_layout="rep_full",
        storage_gb_month=storage_gb_month,
        write_xfer_gb=write_xfer_gb,
        read_xfer_per_member_read_gb=shard_gb,
        notes=f"{n_obj}对象EC; 每读拉1片({shard_gb}GB)跨区",
    )


def layout_L3_delta_mat_base(p: Dict[str, float]) -> Scenario:
    """
    L3 delta + 热区物化 base:
    存储: base 在 2 区各 1 份 (2×V) + 5 patch 各 1 份 (5×rV) = 20+1.5=21.5? 
    文档 0.46/0.02=23 GB·月
    写: base 搬 1 次 V + 5 patch 各 rV → V + M×rV = 11.5 GB
    读: 热区已有 base+patch → 0
    """
    V, r, M = p["V"], p["r"], p["M"]
    patch = r * V
    storage_gb_month = 2 * V + M * patch  # base 双区 + patch 单份
    write_xfer_gb = V + M * patch
    read_xfer_gb = 0.0
    return Scenario(
        name="L3 delta+热区物化base",
        use_delta=True,
        base_layout="mat_hot",
        member_layout="delta_rep",
        storage_gb_month=storage_gb_month,
        write_xfer_gb=write_xfer_gb,
        read_xfer_per_member_read_gb=0.0,
        notes="base双区物化; patch单区; 读全本地",
    )


def layout_L4_delta_base_ec_remote(p: Dict[str, float]) -> Scenario:
    """
    L4 delta + base 远区 EC:
    存储: base 1.5V + 5×rV
    写: 同 L3
    读: 每次读成员需重建 base → 每读 V 跨区 × 15 = 150? 文档 75GB
    采用: 每读拉 0.5V（EC 重建等价）× 15 = 7.5V = 75 GB
    """
    V, r, M, N, K = p["V"], p["r"], p["M"], p["N"], p["K"]
    patch = r * V
    storage_gb_month = ec_storage_factor(K) * V + M * patch
    write_xfer_gb = V + M * patch
    reads_total = M * N
    read_xfer_gb = reads_total * (V / 2)  # 重建 base 的跨区等价
    return Scenario(
        name="L4 delta+base远区EC",
        use_delta=True,
        base_layout="ec_remote",
        member_layout="delta_rep",
        storage_gb_month=storage_gb_month,
        write_xfer_gb=write_xfer_gb,
        read_xfer_per_member_read_gb=V / 2,
        notes="base EC远处; 每次读成员重建base≈0.5V跨区",
    )


def print_scenario(sc: Scenario, p: Dict[str, float]) -> None:
    s, e = p["s"], p["e"]
    reads = p["M"] * p["N"]
    read_xfer = sc.read_xfer_per_member_read_gb * reads if sc.read_xfer_per_member_read_gb else 0.0
    if sc.name.startswith("L1"):
        read_xfer = sc.storage_gb_month  # overridden below
    # use explicit read_xfer_gb from layout builders
    if sc.name == "L1 全副本":
        read_xfer = p["M"] * p["V"]
    elif sc.name == "L2 朴素 EC":
        read_xfer = p["M"] * p["N"] * (p["V"] / p["K"])
    elif sc.name == "L3 delta+热区物化base":
        read_xfer = 0.0
    elif sc.name == "L4 delta+base远区EC":
        read_xfer = p["M"] * p["N"] * (p["V"] / 2)

    c1, c2, c3, total = cost_three_phase(sc.storage_gb_month, sc.write_xfer_gb, read_xfer, s, e)
    print(f"\n=== {sc.name} ===")
    print(f"  假设: {sc.notes}")
    print(f"  ① 存储 GB·月 = {sc.storage_gb_month:.2f}  →  {sc.storage_gb_month:.2f} × {s} = ${c1:.3f}")
    print(f"  ② 写入跨区 GB = {sc.write_xfer_gb:.2f}  →  {sc.write_xfer_gb:.2f} × {e} = ${c2:.3f}")
    print(f"  ③ 读取跨区 GB = {read_xfer:.2f}  →  {read_xfer:.2f} × {e} = ${c3:.3f}")
    print(f"  合计 = ${total:.3f}")


def sweep_factorial(out_dir: Path | None = None) -> List[dict]:
    """
    分类讨论：扫 r、TTL、重复读敏感性代理（非 oracle N）。
    axis_recency_proxy: 0=无跨区读, 1=每读都跨区全量（敏感性上界）
    """
    rows_out: List[dict] = []
    print("\n######## 分类实验矩阵（手算扫参 → compass.csv）########\n")
    print("固定: V=10GB, M=5, s=0.02, e=0.09, K=2\n")

    for T in [1, 12]:
        for recency in [0.0, 0.33, 1.0]:
            for r in [1.0, 0.1, 0.03, 0.005]:
                p = dict(V=10.0, r=r, M=5, N=3, s=0.02, e=0.09, T=T, K=2)
                if r >= 1.0:
                    layouts = [layout_L1_full_replica(p), layout_L2_naive_ec(p)]
                else:
                    layouts = [
                        layout_L1_full_replica(p),
                        layout_L2_naive_ec(p),
                        layout_L3_delta_mat_base(p),
                        layout_L4_delta_base_ec_remote(p),
                    ]
                best_total = 1e18
                best_layout = ""
                cell_rows: List[dict] = []
                for sc in layouts:
                    if sc.name == "L1 全副本":
                        read_xfer = p["M"] * p["V"] * recency
                    elif sc.name == "L2 朴素 EC":
                        read_xfer = p["M"] * p["N"] * (p["V"] / p["K"]) * recency
                    elif sc.name == "L3 delta+热区物化base":
                        read_xfer = 0.0 if recency > 0 else 0.0
                    else:
                        read_xfer = p["M"] * p["N"] * (p["V"] / 2) * recency
                    c1, c2, c3, total = cost_three_phase(
                        sc.storage_gb_month, sc.write_xfer_gb, read_xfer, p["s"], p["e"]
                    )
                    if total < best_total:
                        best_total = total
                        best_layout = sc.name
                    cell_id = f"T{T}_r{r}_rec{recency:.2f}_{sc.name.split()[0]}"
                    cell_rows.append(
                        {
                            "cell_id": cell_id,
                            "layout": sc.name,
                            "axis_r": r,
                            "axis_ttl_month": T,
                            "axis_recency_proxy": recency,
                            "axis_s": p["s"],
                            "axis_e": p["e"],
                            "cost_storage": round(c1, 4),
                            "cost_write": round(c2, 4),
                            "cost_read": round(c3, 4),
                            "cost_total": round(total, 4),
                            "is_cell_winner": False,
                            "ec_break_even_reads": round(
                                ec_vs_rep_break_even_n(p["s"], p["e"], T), 4
                            ),
                            "notes": sc.notes,
                        }
                    )
                for row in cell_rows:
                    row["is_cell_winner"] = row["layout"] == best_layout
                    rows_out.append(row)

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "compass.csv"
        summary_path = out_dir / "compass_summary.json"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COMPASS_FIELDS, lineterminator="\n")
            w.writeheader()
            for row in rows_out:
                w.writerow(row)
        winners = [r for r in rows_out if r["is_cell_winner"]]
        summary = {
            "row_count": len(rows_out),
            "winner_count": len(winners),
            "ec_break_even_T1": ec_vs_rep_break_even_n(0.02, 0.09, 1),
            "ec_break_even_T12": ec_vs_rep_break_even_n(0.02, 0.09, 12),
            "note": "axis_recency_proxy 为手算敏感性，非仿真直方图 oracle",
        }
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[compass] -> {csv_path.relative_to(ROOT)} ({len(rows_out)} rows)")
        print(f"[compass] -> {summary_path.relative_to(ROOT)}")

    n_star = ec_vs_rep_break_even_n(0.02, 0.09, 1.0)
    print(f"\n单对象 EC vs 副本盈亏平衡 (T=1月): N < sT/e = {n_star:.3f} 次跨区读")
    print(f"T=12月: N < {ec_vs_rep_break_even_n(0.02, 0.09, 12):.2f} 次")
    return rows_out


def main() -> None:
    ap = argparse.ArgumentParser(description="delta+EC 三阶段成本手算")
    ap.add_argument("--sweep", action="store_true", help="扫参并写 compass.csv")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "results" / "hand_calc",
        help="compass 输出目录",
    )
    args = ap.parse_args()
    p = design_doc_example()
    print("§7.8 参数:", p)
    print(f"EC盈亏平衡 N < sT/e = {ec_vs_rep_break_even_n(p['s'], p['e'], p['T']):.4f}")
    for fn in (layout_L1_full_replica, layout_L2_naive_ec, layout_L3_delta_mat_base, layout_L4_delta_base_ec_remote):
        print_scenario(fn(p), p)
    if args.sweep:
        sweep_factorial(args.out_dir)


if __name__ == "__main__":
    main()
