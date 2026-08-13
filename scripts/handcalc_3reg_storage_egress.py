#!/usr/bin/env python3
"""
3reg 手算：存储 + 跨区域 egress（不含 PUT/GET API、不含编解码）

与 Java Simulator / PolicyMilpECnewV3 价目对齐：
  - 区域：config/milp_ec_new_v3_3reg.yaml
  - 存储：target/profiles/storage.csv（AWS/Azure General Purpose / Hot）
          + target/profiles/gcp_storage_pricing.csv（GCP）
  - 运费：target/profiles/cost.csv

存储计费（RegionManager.pricePerGB）：
  cost_stor = V_GB * priceStorage * 3 * T_sim_days
  （×3 = 多云实验「1 仿真日 ≈ 3 计费月」）

用法（仓库根目录）：
  python scripts/handcalc_3reg_storage_egress.py
  python scripts/handcalc_3reg_storage_egress.py --verbose
  python scripts/handcalc_3reg_storage_egress.py --v-gb 2 --ttl-days 30
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
YAML_3REG = ROOT / "config" / "milp_ec_new_v3_3reg.yaml"
PROFILES = ROOT / "target" / "profiles"
# 若 target/profiles 不存在，回退 artifact
FALLBACK_PROFILES = (
    ROOT
    / "skystore_python"
    / "artifact-eval-main"
    / "simulation"
    / "src"
    / "utils"
    / "profiles"
)

REGIONS_DEFAULT = [
    "aws:us-east-1",
    "azure:westeurope",
    "gcp:us-west1-a",
]

# SkyStore 多云：7 天 trace 拉伸为 90 仿真日
TRACE_DAYS = 7.0
SIM_DAYS_PER_TRACE_DAY = 90.0 / 7.0
DEFAULT_HORIZON_SIM_DAYS = TRACE_DAYS * SIM_DAYS_PER_TRACE_DAY  # 90

# 仿真结果参考（storage + transfer；不含 API）
SIM_REF_3REG = {
    "T15": {"tevict": (82.77, 57.67, 21.21), "milp": (85.16, 60.08, 21.20)},
    "T29": {"tevict": (29.01, 2.84, 1.50), "milp": (29.12, 2.95, 1.50)},
    "T65": {"tevict": (6.54, 5.46, 0.93), "milp": (12.03, 4.36, 7.52)},
    "T78": {"tevict": (28.56, 16.79, 10.00), "milp": (28.38, 15.02, 11.57)},
    "T79": {"tevict": (279.67, 197.70, 81.86), "milp": (303.02, 187.38, 115.53)},
}


@dataclass
class MarginalPlan:
    kind: str  # "replica" | "ec"
    regions: Tuple[str, ...]
    storage_usd: float
    egress_usd: float

    @property
    def total(self) -> float:
        return self.storage_usd + self.egress_usd


def profiles_dir() -> Path:
    if (PROFILES / "storage.csv").is_file():
        return PROFILES
    if (FALLBACK_PROFILES / "storage.csv").is_file():
        return FALLBACK_PROFILES
    raise FileNotFoundError(
        f"找不到 storage.csv：已尝试 {PROFILES} 与 {FALLBACK_PROFILES}"
    )


def load_regions_from_yaml(path: Path) -> List[str]:
    regions: List[str] = []
    in_block = False
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("regions:"):
                in_block = True
                continue
            if in_block:
                if s.startswith("- "):
                    regions.append(s[2:].strip().strip('"').strip("'"))
                elif s and not s.startswith("#"):
                    break
    return regions or REGIONS_DEFAULT


def load_price_storage(regions: List[str], prof: Path) -> Dict[str, float]:
    """与 Helpers.java 一致：storage + General Purpose/Hot；GCP 单独文件；后写覆盖先写。"""
    want = set(regions)
    S: Dict[str, float] = {}
    storage_csv = prof / "storage.csv"
    with storage_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            reg = f"{row['Vendor']}:{row['Region']}"
            if reg not in want:
                continue
            if row["Group"] == "storage" and row["Tier"] in ("General Purpose", "Hot"):
                S[reg] = float(row["PricePerUnit"])
    gcp_csv = prof / "gcp_storage_pricing.csv"
    if gcp_csv.is_file():
        with gcp_csv.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                reg = f"{row['Vendor']}:{row['Region']}"
                if reg in want:
                    S[reg] = float(row["PricePerUnit"])
    missing = [r for r in regions if r not in S]
    if missing:
        raise KeyError(f"缺少 priceStorage: {missing}")
    return S


def load_egress(regions: List[str], prof: Path) -> Dict[Tuple[str, str], float]:
    want = set(regions)
    cost: Dict[Tuple[str, str], float] = {}
    with (prof / "cost.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s, d = row["src"], row["dest"]
            if s in want and d in want and s != d:
                cost[(s, d)] = float(row["cost"])
    return cost


def storage_cost(S_month: float, v_gb: float, sim_days: float) -> float:
    return v_gb * S_month * 3.0 * sim_days


def egress_cost(cost: Dict[Tuple[str, str], float], src: str, dst: str, v_gb: float) -> float:
    return cost.get((src, dst), 0.09) * v_gb


def t_even_months(n_per_gb: float, s_month: float) -> float:
    if s_month <= 0:
        return float("inf")
    return n_per_gb / s_month


def best_replica(
    issue: str,
    base: str,
    v_gb: float,
    ttl_sim_days: float,
    regions: List[str],
    S: Dict[str, float],
    cost: Dict[Tuple[str, str], float],
) -> MarginalPlan:
    best: Optional[MarginalPlan] = None
    for r in regions:
        if r == base:
            continue
        st = storage_cost(S[r], v_gb, ttl_sim_days)
        eg = egress_cost(cost, issue, r, v_gb)
        plan = MarginalPlan("replica", (r,), st, eg)
        if best is None or plan.total < best.total:
            best = plan
    assert best is not None
    return best


def best_ec(
    issue: str,
    base: str,
    v_gb: float,
    ttl_sim_days: float,
    regions: List[str],
    S: Dict[str, float],
    cost: Dict[Tuple[str, str], float],
) -> MarginalPlan:
    others = [r for r in regions if r != base]
    best: Optional[MarginalPlan] = None
    half = v_gb / 2.0
    for r1, r2 in combinations(others, 2):
        st = storage_cost(S[r1], half, ttl_sim_days) + storage_cost(S[r2], half, ttl_sim_days)
        eg = egress_cost(cost, issue, r1, half) + egress_cost(cost, issue, r2, half)
        plan = MarginalPlan("ec", (r1, r2), st, eg)
        if best is None or plan.total < best.total:
            best = plan
    assert best is not None
    return best


def print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_price_table(regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float]) -> None:
    print_header("1. 3reg 价目表（仅 storage + egress）")
    print(f"{'区域':<22} {'priceStorage':>14}  {'单位':<10}")
    print("-" * 52)
    for r in regions:
        print(f"{r:<22} {S[r]:>14.5f}  $/GB·月")
    print()
    print("跨区域 egress ($/GB)，源 \\ 目的：")
    short = [r.split(":")[1][:10] for r in regions]
    print(f"{'':22} " + " ".join(f"{h:>10}" for h in short))
    for s in regions:
        row = []
        for d in regions:
            if s == d:
                row.append(f"{'0':>10}")
            else:
                row.append(f"{cost[(s, d)]:>10.4f}")
        print(f"{s:<22} " + " ".join(row))
    print()
    print("T_even = N / S_dst（月），示例边：")
    for s in regions:
        for d in regions:
            if s == d:
                continue
            n = cost[(s, d)]
            te = t_even_months(n, S[d])
            print(f"  {s.split(':')[1]:>12} -> {d.split(':')[1]:<12}  N={n:.4f}  S={S[d]:.5f}  T_even={te:.2f} mo")


def example_all_regions_one_day(
    regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float], issue: str
) -> None:
    print_header("2. 场景 A：3 区各有一份冗余 × 1 仿真日（仅存储）")
    print("假设：先前操作已在 3 个区各放置一份；驻留 1 仿真日；不计 placement egress。")
    for v in (0.001, 0.1, 1.0, 2.0, 10.0):
        rep = sum(storage_cost(S[r], v, 1.0) for r in regions)
        ec = sum(storage_cost(S[r], v / 2.0, 1.0) for r in regions)
        save = rep - ec
        edges = [cost[(issue, d)] for d in regions if d != issue]
        avg_n = sum(edges) / len(edges)
        be = save / avg_n if avg_n > 0 else float("inf")
        print(f"\n  V = {v:g} GB")
        print(f"    整副本（3×V）存储: ${rep:.6f}")
        print(f"    EC 半片（3×V/2）存储: ${ec:.6f}  节省 ${save:.6f} ({100*save/rep:.0f}%)")
        print(f"    若从 {issue} 多拉 1GB 分片抹平存储差：约 {be:.1f} 次 (avg egress=${avg_n:.4f}/GB)")


def example_fb_milp(
    regions: List[str],
    S: Dict[str, float],
    cost: Dict[Tuple[str, str], float],
    issue: str,
    base: str,
    v_gb: float,
    horizon: float,
    ttl: float,
    label: str,
) -> None:
    print(f"\n--- {label} ---")
    print(f"  issue={issue}  base={base}  V={v_gb} GB  base_horizon={horizon:.0f} sim-days  extra_TTL={ttl:.0f} sim-days")
    c_base = storage_cost(S[base], v_gb, horizon)
    rep = best_replica(issue, base, v_gb, ttl, regions, S, cost)
    ec = best_ec(issue, base, v_gb, ttl, regions, S, cost)
    winner = "EC" if ec.total < rep.total else "REPLICA"
    print(f"  Base 存储（两分支相同）: ${c_base:.4f}")
    print(f"  z=0 整副本 @ {rep.regions[0]}")
    print(f"       存储 ${rep.storage_usd:.4f} + egress ${rep.egress_usd:.4f} = 边际 ${rep.total:.4f}")
    print(f"       总计 ${c_base + rep.total:.4f}")
    print(f"  z=1 EC @ {ec.regions[0]} + {ec.regions[1]}")
    print(f"       存储 ${ec.storage_usd:.4f} + egress ${ec.egress_usd:.4f} = 边际 ${ec.total:.4f}")
    print(f"       总计 ${c_base + ec.total:.4f}")
    print(f"  MILP 边际胜者: {winner}  (Δ=${abs(rep.total - ec.total):.4f})")


def example_fb_suite(regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float]) -> None:
    print_header("3. 场景 B：FB + MILP 单对象（storage + placement egress）")
    H = DEFAULT_HORIZON_SIM_DAYS
    cases = [
        ("B1 aws base/issue, V=1GB", "aws:us-east-1", "aws:us-east-1", 1.0, H, 30.0),
        ("B2 aws base/issue, V=2GB", "aws:us-east-1", "aws:us-east-1", 2.0, H, 30.0),
        ("B3 aws base, TTL=7 sim-days", "aws:us-east-1", "aws:us-east-1", 1.0, H, 7.0),
        ("B4 aws base, TTL=90 sim-days", "aws:us-east-1", "aws:us-east-1", 1.0, H, 90.0),
        ("B5 gcp base/issue", "gcp:us-west1-a", "gcp:us-west1-a", 1.0, H, 30.0),
        ("B6 azure base/issue", "azure:westeurope", "azure:westeurope", 1.0, H, 30.0),
        ("B7 issue=aws, base=gcp (跨云 PUT)", "aws:us-east-1", "gcp:us-west1-a", 1.0, H, 30.0),
    ]
    for label, issue, base, v, h, ttl in cases:
        if base not in regions:
            continue
        example_fb_milp(regions, S, cost, issue, base, v, h, ttl, label)


def scan_v_ttl(regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float]) -> None:
    print_header("4. 扫描：何时 EC 边际优于整副本？（issue=base=aws:us-east-1）")
    issue = base = "aws:us-east-1"
    if issue not in regions:
        print("  (跳过：3reg 不含 aws:us-east-1)")
        return
    print(f"{'V(GB)':>8} {'TTL(sim-d)':>10} {'REP边际':>10} {'EC边际':>10} {'胜者':>8} {'Δ':>8}")
    ec_wins = 0
    total = 0
    for v in (0.01, 0.1, 0.5, 1, 2, 5, 10, 50, 100):
        for ttl in (1, 7, 30, 90, 180):
            rep = best_replica(issue, base, v, ttl, regions, S, cost)
            ec = best_ec(issue, base, v, ttl, regions, S, cost)
            w = "EC" if ec.total < rep.total else "REP"
            if w == "EC":
                ec_wins += 1
            total += 1
            if v in (1, 2, 10) and ttl in (30, 90):
                print(f"{v:>8g} {ttl:>10g} {rep.total:>10.4f} {ec.total:>10.4f} {w:>8} {abs(rep.total-ec.total):>8.4f}")
    print(f"\n  全网格 {total} 组：EC 胜 {ec_wins} 组，REPLICA 胜 {total - ec_wins} 组")


def enumerate_all_marginal(regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float]) -> None:
    print_header("5. 穷举边际方案（issue=base=aws:us-east-1, V=1GB, TTL=30 sim-days）")
    issue = base = "aws:us-east-1"
    v, ttl = 1.0, 30.0
    print("  [整副本候选]")
    rows = []
    for r in regions:
        if r == base:
            continue
        st = storage_cost(S[r], v, ttl)
        eg = egress_cost(cost, issue, r, v)
        rows.append((st + eg, r, st, eg))
    rows.sort()
    for tot, r, st, eg in rows:
        print(f"    {r:<22} stor=${st:.4f}  egress=${eg:.4f}  total=${tot:.4f}")
    print("  [EC 两片候选]")
    rows_ec = []
    others = [r for r in regions if r != base]
    half = v / 2.0
    for r1, r2 in combinations(others, 2):
        st = storage_cost(S[r1], half, ttl) + storage_cost(S[r2], half, ttl)
        eg = egress_cost(cost, issue, r1, half) + egress_cost(cost, issue, r2, half)
        rows_ec.append((st + eg, r1, r2, st, eg))
    rows_ec.sort()
    for tot, r1, r2, st, eg in rows_ec:
        print(f"    {r1} + {r2}")
        print(f"      stor=${st:.4f}  egress=${eg:.4f}  total=${tot:.4f}")


def trace_stats(trace_path: Path) -> Optional[dict]:
    if not trace_path.is_file():
        return None
    puts = gets = 0
    sizes_put: List[float] = []
    with trace_path.open(newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            op = row.get("op", "").upper()
            try:
                sz = int(row["size"]) / 1e9
            except (KeyError, ValueError):
                sz = 0.0
            if op == "PUT":
                puts += 1
                sizes_put.append(sz)
            elif op == "GET":
                gets += 1
    if not sizes_put:
        return None
    sizes_put.sort()
    p90 = sizes_put[int(0.9 * len(sizes_put))]
    return {
        "puts": puts,
        "gets": gets,
        "median_gb": median(sizes_put),
        "p90_gb": p90,
        "max_gb": max(sizes_put),
    }


def example_traces(
    regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float]
) -> None:
    print_header("6. IBM trace（3reg typeA）+ 单对象 FB 手算")
    data_root = ROOT / "Data" / "3reg" / "3reg" / "typeA"
    traces = ["015", "029", "065", "078", "079"]
    issue = base = "aws:us-east-1"
    H, ttl = DEFAULT_HORIZON_SIM_DAYS, 30.0
    print(f"{'Trace':<6} {'PUT':>8} {'GET':>10} {'中位GB':>12} {'p90 GB':>10} {'REP边际':>10} {'EC边际':>10} {'胜者':>6}")
    for t in traces:
        path = data_root / f"IBMObjectStoreTrace{t}Part0.typeA.mc.aug"
        st = trace_stats(path)
        if not st:
            print(f"  T{t}: 文件不存在 {path}")
            continue
        v = st["median_gb"]
        if v <= 0:
            v = 1e-6
        rep = best_replica(issue, base, v, ttl, regions, S, cost)
        ec = best_ec(issue, base, v, ttl, regions, S, cost)
        w = "EC" if ec.total < rep.total else "REP"
        print(
            f"T{t:<5} {st['puts']:>8} {st['gets']:>10} {st['median_gb']:>12.6f} "
            f"{st['p90_gb']:>10.4f} {rep.total:>10.6f} {ec.total:>10.6f} {w:>6}"
        )
    print("\n  全 trace 仿真参考（storage + transfer，不含 API）：")
    print(f"  {'Trace':<6} {'策略':<10} {'总成本':>10} {'存储':>10} {'传输':>10}")
    for t in traces:
        key = f"T{int(t)}"  # SIM_REF 键为 T15, T65 等
        if key not in SIM_REF_3REG:
            continue
        tot, stor, xfer = SIM_REF_3REG[key]["tevict"]
        print(f"  {key:<6} {'Tevict':<10} {tot:>10.2f} {stor:>10.2f} {xfer:>10.2f}")
        tot, stor, xfer = SIM_REF_3REG[key]["milp"]
        print(f"  {key:<6} {'MILP-V3':<10} {tot:>10.2f} {stor:>10.2f} {xfer:>10.2f}")


def example_two_region_teaching(regions: List[str], S: Dict[str, float], cost: Dict[Tuple[str, str], float]) -> None:
    print_header("7. 对照：SkyStore 同云两区 T_even 量级（tevenstorage / tevencost 示例边）")
    teven_stor = prof / "tevenstorage.csv" if (prof := profiles_dir()) else None
    teven_cost = prof / "tevencost.csv"
    if teven_cost.is_file():
        with teven_cost.open(newline="") as f:
            rows = list(csv.DictReader(f))
        if rows:
            row = rows[0]
            s_src, s_dst = row["src"], row["dest"]
            n = float(row["cost"])
            if s_dst in S:
                te = t_even_months(n, S[s_dst])
                print(f"  {s_src} -> {s_dst}: N={n:.4f}  (3reg 内 aws 区 S≈0.021)  T_even≈{n/0.021:.2f} mo")
    print("  3reg 跨云边 aws:us-east-1 -> azure:westeurope:")
    n = cost[("aws:us-east-1", "azure:westeurope")]
    print(f"    N={n:.4f}, S_dst={S['azure:westeurope']:.5f}  T_even={t_even_months(n, S['azure:westeurope']):.2f} mo")
    print("  => 3reg 跨云 T_even 远大于同云两区（~0.77mo），存储相对更「值得多存」。")


def main() -> None:
    parser = argparse.ArgumentParser(description="3reg storage+egress hand calculator")
    parser.add_argument("--yaml", type=Path, default=YAML_3REG, help="3reg 配置文件")
    parser.add_argument("--v-gb", type=float, default=None, help="仅打印指定 V 的 FB 详表")
    parser.add_argument("--ttl-days", type=float, default=30.0, help="额外冗余 TTL（仿真日）")
    parser.add_argument("--verbose", action="store_true", help="打印穷举表")
    args = parser.parse_args()

    prof = profiles_dir()
    regions = load_regions_from_yaml(args.yaml)
    S = load_price_storage(regions, prof)
    cost = load_egress(regions, prof)

    print("3reg 手算脚本（storage + egress only）")
    print(f"  配置: {args.yaml}")
    print(f"  价目: {prof}")
    print(f"  区域: {regions}")
    print(f"  仿真 horizon: {DEFAULT_HORIZON_SIM_DAYS:.0f} sim-days (= {TRACE_DAYS:.0f} trace-days × {SIM_DAYS_PER_TRACE_DAY:.0f})")

    print_price_table(regions, S, cost)
    example_all_regions_one_day(regions, S, cost, "aws:us-east-1")
    example_fb_suite(regions, S, cost)

    if args.v_gb is not None:
        example_fb_milp(
            regions, S, cost,
            "aws:us-east-1", "aws:us-east-1",
            args.v_gb, DEFAULT_HORIZON_SIM_DAYS, args.ttl_days,
            f"自定义 V={args.v_gb} GB TTL={args.ttl_days}",
        )

    scan_v_ttl(regions, S, cost)
    if args.verbose:
        enumerate_all_marginal(regions, S, cost)
    example_traces(regions, S, cost)
    example_two_region_teaching(regions, S, cost)

    print()
    print("完成。不计 PUT/GET API 与编解码。")


if __name__ == "__main__":
    main()
