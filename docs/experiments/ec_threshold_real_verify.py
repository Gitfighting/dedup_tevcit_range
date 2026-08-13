"""
EC-Threshold 真参数验证脚本

与 ec_threshold_synth_verify.py 同样的理论（docs/EC_Threshold_Theory.md §5），
但参数从工程真数据读取：

  - Region 列表    : config/milp_ec_new_v3_9reg.yaml
  - 存储单价 p_r   : skystore_python/.../profiles/storage.csv（与 Helpers.java 完全对齐：
                     Group="storage" 且 Tier ∈ {"General Purpose","Hot"}）
  - 跨区运费 c_ij  : skystore_python/.../profiles/cost.csv

用法:
  cd docs/experiments
  python ec_threshold_real_verify.py [--base aws:us-east-1] [--window-hours 24] [--K 2]

输出:
  - 控制台：每个区的 p_r、base 到各区的 c_{b,r}、平均 c̄、解析 λ*
  - 图   : ec_threshold_real_curve.png
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, List, Tuple

import numpy as np


# ------------------------------------------------------------
# 路径
# ------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))

YAML_PATH = os.path.join(ROOT, "config", "milp_ec_new_v3_9reg.yaml")
PROFILES_DIR = os.path.join(
    ROOT,
    "skystore_python",
    "artifact-eval-main",
    "simulation",
    "src",
    "utils",
    "profiles",
)
STORAGE_CSV = os.path.join(PROFILES_DIR, "storage.csv")
GCP_STORAGE_CSV = os.path.join(PROFILES_DIR, "gcp_storage_pricing.csv")
COST_CSV = os.path.join(PROFILES_DIR, "cost.csv")


# ------------------------------------------------------------
# 解析 yaml region 列表（不引依赖：手工解析最简 yaml）
# ------------------------------------------------------------
def load_regions_from_yaml(yaml_path: str) -> List[str]:
    regions: List[str] = []
    in_regions = False
    with open(yaml_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped.startswith("regions:"):
                in_regions = True
                continue
            if in_regions:
                if stripped.startswith("- "):
                    regions.append(stripped[2:].strip())
                else:
                    in_regions = False
    if not regions:
        raise RuntimeError(f"No regions parsed from {yaml_path}")
    return regions


# ------------------------------------------------------------
# 加载 priceStorage（与 Helpers.java 完全一致的 Tier 选择）
# ------------------------------------------------------------
def load_storage_prices_per_month(regions: List[str]) -> Dict[str, float]:
    """返回 region -> $/GB·月。与 Java Helpers.java 第 216 行规则一致："""
    prices: Dict[str, float] = {}

    # 1) 主 storage.csv：Group=storage 且 Tier ∈ {"General Purpose","Hot"}
    with open(STORAGE_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            region = f"{row['Vendor']}:{row['Region']}"
            if region not in regions:
                continue
            group = row.get("Group", "")
            tier = row.get("Tier", "")
            if group == "storage" and tier in ("General Purpose", "Hot"):
                # 与 Helpers.java 第 218 行行为一致：每条匹配都覆盖，
                # 最终保留 csv 里最后一行匹配（即同一 region 多档时取最后一档）
                prices[region] = float(row["PricePerUnit"])

    # 2) GCP fallback（gcp_storage_pricing.csv），仅当主 csv 没给出
    if os.path.exists(GCP_STORAGE_CSV):
        with open(GCP_STORAGE_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                region = f"{row['Vendor']}:{row['Region']}"
                if region in regions and region not in prices:
                    val = float(row["PricePerUnit"])
                    if val > 0:
                        prices[region] = val

    missing = [r for r in regions if r not in prices]
    if missing:
        raise RuntimeError(f"Missing priceStorage for regions: {missing}")
    return prices


# ------------------------------------------------------------
# 加载跨区运费 c_{i,j}（仅返回我们关心的 region 集合内的边）
# ------------------------------------------------------------
def load_costs(regions: List[str]) -> Dict[Tuple[str, str], float]:
    region_set = set(regions)
    costs: Dict[Tuple[str, str], float] = {}
    with open(COST_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            src = row["src"]
            dst = row["dest"]
            if src in region_set and dst in region_set:
                costs[(src, dst)] = float(row["cost"])
    return costs


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=None,
                        help="base region (default: yaml 中第一个 region)")
    parser.add_argument("--window-hours", type=float, default=24.0,
                        help="规划窗 W，单位小时")
    parser.add_argument("--K", type=int, default=2,
                        help="EC 数据片数 (EC(K, R-K))")
    parser.add_argument("--size-gb", type=float, default=1.0,
                        help="对象大小 (GB)")
    args = parser.parse_args()

    regions = load_regions_from_yaml(YAML_PATH)
    base = args.base or regions[0]
    if base not in regions:
        raise RuntimeError(f"--base {base} 不在 yaml regions 列表里：{regions}")

    K = args.K
    W = args.window_hours * 3600.0  # 秒
    S = args.size_gb

    # 单价: $/GB·月 → $/GB·sec  (与 Java 的 priceStorage*3 折算无关，保持原始月价)
    price_month = load_storage_prices_per_month(regions)
    SEC_PER_MONTH = 30.0 * 24.0 * 3600.0  # 论文常用约定
    price_sec = {r: price_month[r] / SEC_PER_MONTH for r in regions}

    cost_map = load_costs(regions)

    # base 到其余区的运费 c_{b,r}
    others = [r for r in regions if r != base]
    c_b_to_r: Dict[str, float] = {}
    missing_edges = []
    for r in others:
        if (base, r) not in cost_map:
            missing_edges.append((base, r))
        else:
            c_b_to_r[r] = cost_map[(base, r)]
    if missing_edges:
        raise RuntimeError(f"cost.csv 缺少边：{missing_edges}")

    # 平均跨区运费 c̄: 取所有 i!=j 边的平均（与 Java avgNetworkCost 一致逻辑）
    edge_vals = [v for (i, j), v in cost_map.items() if i != j]
    if not edge_vals:
        raise RuntimeError("cost.csv 中没有 i!=j 的边")
    c_bar = float(np.mean(edge_vals))

    # ---------- 解析 λ* ----------
    p_r_array = np.array([price_sec[r] for r in regions])
    sum_p_r = float(p_r_array.sum())
    sum_c_b_r = float(sum(c_b_to_r.values()))
    lam_star = (W * sum_p_r + sum_c_b_r) / c_bar

    # ---------- 报表 ----------
    print("=" * 70)
    print("EC-Threshold 真参数验证")
    print("=" * 70)
    print(f"base region : {base}")
    print(f"K           : {K}")
    print(f"S (object)  : {S} GB")
    print(f"W (window)  : {W:.0f} sec ({args.window_hours} h)")
    print()
    print("9 region priceStorage  (Tier=General Purpose / Hot)")
    print("-" * 70)
    print(f"  {'region':<28} {'$/GB·month':>14} {'$/GB·sec':>16}")
    for r in regions:
        print(f"  {r:<28} {price_month[r]:>14.6f} {price_sec[r]:>16.4e}")
    print(f"  Σ p_r       = {sum_p_r:.4e}  $/GB·sec")
    print()
    print(f"base→其它 8 区 cost  (base = {base})")
    print("-" * 70)
    for r in others:
        print(f"  {r:<28} c_b,r = {c_b_to_r[r]:.4f}  $/GB")
    print(f"  Σ c_b,r     = {sum_c_b_r:.4f}  $/GB")
    print()
    print(f"全图平均跨区运费 c̄ = {c_bar:.4f} $/GB")
    print()
    print(f"==> λ*  =  ( W·Σp_r  +  Σc_b,r ) / c̄")
    print(f"        =  ( {W:.0f} · {sum_p_r:.4e}  +  {sum_c_b_r:.4f} ) / {c_bar:.4f}")
    print(f"        =  {lam_star:.4f}  (次 / 窗)")
    print()
    print(f"含义: 该对象在 {args.window_hours:g} h 内读取次数 < {lam_star:.2f}")
    print("       → EC 比 replica 便宜（按定理 1）。")

    # ---------- 画图 ----------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        out_path = os.path.join(HERE, "ec_threshold_real_curve.png")

        # 两条直线：常数项 vs 读侧线性项
        x_max = 2.0 * lam_star if lam_star > 0 else 10.0
        lam_grid = np.linspace(0.0, x_max, 4001)

        rep_const = (1.0 - 1.0 / K) * S * (W * sum_p_r + sum_c_b_r)
        ec_slope = (K - 1) / K * c_bar * S
        rep_line = np.full_like(lam_grid, rep_const)
        ec_line = ec_slope * lam_grid

        plt.figure(figsize=(7.5, 5.0))
        plt.plot(lam_grid, rep_line,
                 label=r"$\Delta_{store}+\Delta_{write}$ (constant)")
        plt.plot(lam_grid, ec_line,
                 label=r"$\Delta_{read}(\lambda)$")
        plt.axvline(lam_star, linestyle="--", color="red",
                    label=fr"$\lambda^*={lam_star:.2f}$")
        plt.xlabel(r"reads per window  $\lambda$")
        plt.ylabel("dollar amount ($)")
        plt.title(
            f"EC-Threshold (real prices, base={base}, "
            f"W={args.window_hours:g} h, K={K})")
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_path, dpi=120)
        print()
        print(f"图已保存: {out_path}")
    except ImportError:
        print("\n(matplotlib 未安装，跳过画图)")

    print()
    print("[OK] 真参数 λ* 已计算完毕。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
