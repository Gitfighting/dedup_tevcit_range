#!/usr/bin/env python3
"""
验证 PolicyMilpECnewV3 / GurobiEc21MilpBuilder 的 z 分支：
  z=0 → 整副本（至多一个额外 full replica）
  z=1 → EC(2+1) 两片落在两个非 base 区

在 FB（fixed base）设定下，与 Java 一致：比较
  min_{r!=base} (sRep[r])  vs  min_{r1,r2} (sEc[r1][0]+sEc[r2][1])
其中 sRep/sEc = storage(×3 仿真月) + placement egress，不含 API。

用法（仓库根目录）：
  python scripts/verify_milp_replica_vs_ec.py
  python scripts/verify_milp_replica_vs_ec.py --config config/milp_ec_new_v3_6reg.yaml
  python scripts/verify_milp_replica_vs_ec.py --gurobi   # 若已安装 gurobipy，与穷举对照
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGS = [
    ROOT / "config" / "milp_ec_new_v3_3reg.yaml",
    ROOT / "config" / "milp_ec_new_v3_6reg.yaml",
    ROOT / "config" / "milp_ec_new_v3_9reg.yaml",
]

TRACE_DAYS = 7.0
SIM_DAYS_PER_TRACE_DAY = 90.0 / 7.0
DEFAULT_HORIZON_SIM_DAYS = TRACE_DAYS * SIM_DAYS_PER_TRACE_DAY
EXTRA_TTL_SIM_DAYS = 30.0  # 边际冗余 TTL（与手算脚本 B 场景一致）


@dataclass
class MilpBranchChoice:
    z: int  # 0=replica, 1=ec
    winner: str
    rep_cost: float
    ec_cost: float
    rep_region: Optional[str]
    ec_regions: Optional[Tuple[str, str]]
    delta: float


def _load_handcalc():
    path = ROOT / "scripts" / "handcalc_3reg_storage_egress.py"
    name = "handcalc_3reg_storage_egress"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def ttl_cold_sec(src: str, dst: str, S: Dict[str, float], egress: Dict[Tuple[str, str], float]) -> float:
    """与 PolicyMilpECnewV3.findMin 冷启动一致：teven/2 秒，teven = N/S_dst * 86400。"""
    n = egress.get((src, dst), 0.09)
    s = S[dst]
    teven = (n / s) * 86400.0
    return teven / 2.0


def s_rep(
    issue: str,
    r: str,
    v_gb: float,
    ttl_sec: float,
    S: Dict[str, float],
    egress: Dict[Tuple[str, str], float],
    hc,
    delta_xfer_frac: float = 1.0,
) -> float:
    ttl_sim_days = ttl_sec / 86400.0
    stor = hc.storage_cost(S[r], v_gb, ttl_sim_days)
    xfer_v = v_gb * max(0.0, min(1.0, delta_xfer_frac))
    xfer = hc.egress_cost(egress, issue, r, xfer_v)
    return stor + xfer


def s_ec_shard(
    issue: str,
    r: str,
    v_gb: float,
    ttl_sec: float,
    S: Dict[str, float],
    egress: Dict[Tuple[str, str], float],
    hc,
) -> float:
    half = v_gb / 2.0
    ttl_sim_days = ttl_sec / 86400.0
    stor = hc.storage_cost(S[r], half, ttl_sim_days)
    xfer = hc.egress_cost(egress, issue, r, half)
    return stor + xfer


def milp_z_fb_fixed_base(
    issue: str,
    base: str,
    v_gb: float,
    ttl_by_region: Dict[str, float],
    regions: List[str],
    S: Dict[str, float],
    egress: Dict[Tuple[str, str], float],
    hc,
    delta_xfer_frac: float = 1.0,
) -> MilpBranchChoice:
    """FB：base 成本为常数项，z 只在边际 replica vs EC 之间选。"""
    best_rep = None
    best_rep_r = None
    for r in regions:
        if r == base:
            continue
        t = ttl_by_region.get(r, ttl_by_region.get("_default", 0.0))
        c = s_rep(issue, r, v_gb, t, S, egress, hc, delta_xfer_frac)
        if best_rep is None or c < best_rep:
            best_rep, best_rep_r = c, r

    best_ec = None
    best_ec_pair = None
    others = [r for r in regions if r != base]
    for r1, r2 in combinations(others, 2):
        t1 = ttl_by_region.get(r1, ttl_by_region.get("_default", 0.0))
        t2 = ttl_by_region.get(r2, ttl_by_region.get("_default", 0.0))
        c = s_ec_shard(issue, r1, v_gb, t1, S, egress, hc) + s_ec_shard(
            issue, r2, v_gb, t2, S, egress, hc
        )
        if best_ec is None or c < best_ec:
            best_ec, best_ec_pair = c, (r1, r2)

    if best_rep is None:
        best_rep = float("inf")
    if best_ec is None:
        best_ec = float("inf")

    if best_ec < best_rep - 1e-12:
        z, w = 1, "EC"
    else:
        z, w = 0, "REPLICA"
    return MilpBranchChoice(
        z=z,
        winner=w,
        rep_cost=best_rep,
        ec_cost=best_ec,
        rep_region=best_rep_r,
        ec_regions=best_ec_pair,
        delta=abs(best_rep - best_ec),
    )


def ttl_snapshot_for_issue(
    issue: str,
    regions: List[str],
    S: Dict[str, float],
    egress: Dict[Tuple[str, str], float],
    mode: str,
    warm_factor: float = 1.0,
) -> Dict[str, float]:
    """模拟 buildTtlSnapshotForIssueRegion：每区一条 (r -> issue) 的 TTL。"""
    out: Dict[str, float] = {}
    for r in regions:
        cold = ttl_cold_sec(r, issue, S, egress)
        if mode == "cold":
            out[r] = cold
        elif mode == "warm":
            out[r] = cold * warm_factor
        else:
            out[r] = cold
    return out


def trace_median_gb(trace_path: Path) -> Optional[float]:
    if not trace_path.is_file():
        return None
    sizes: List[float] = []
    with trace_path.open(newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("op", "").upper() != "PUT":
                continue
            try:
                sizes.append(int(row["size"]) / 1e9)
            except (KeyError, ValueError):
                pass
    return median(sizes) if sizes else None


def try_gurobi_verify(
    issue: str,
    base: str,
    v_gb: float,
    ttl_by_region: Dict[str, float],
    regions: List[str],
    S: Dict[str, float],
    egress: Dict[Tuple[str, str], float],
    hc,
) -> Optional[int]:
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError:
        return None

    n = len(regions)
    base_idx = regions.index(base)
    half = v_gb / 2.0
    m = gp.Model("milp_z_verify")
    m.Params.OutputFlag = 0
    z = m.addVar(vtype=GRB.BINARY, name="z")
    x = m.addVars(n, vtype=GRB.BINARY, name="x")
    y = m.addVars(n, 2, vtype=GRB.BINARY, name="y")

    m.addConstr(x[base_idx] == 0, "fb_base_x0")
    for r in range(n):
        if r != base_idx:
            m.addConstr(x[r] + z <= 1)
        for k in range(2):
            m.addConstr(y[r, k] <= z)
    for k in range(2):
        m.addConstr(gp.quicksum(y[r, k] for r in range(n)) == z)
    for r in range(n):
        m.addConstr(gp.quicksum(y[r, k] for k in range(2)) <= 1)
    extra = gp.quicksum(x[r] for r in range(n) if r != base_idx)
    m.addConstr(extra <= 1)

    obj = gp.LinExpr()
    for r in range(n):
        if r == base_idx:
            continue
        t = ttl_by_region.get(regions[r], ttl_by_region.get("_default", 0.0))
        ttl_days = t / 86400.0
        srep = hc.storage_cost(S[regions[r]], v_gb, ttl_days) + hc.egress_cost(
            egress, issue, regions[r], v_gb
        )
        obj += srep * x[r]
        sec = hc.storage_cost(S[regions[r]], half, ttl_days) + hc.egress_cost(
            egress, issue, regions[r], half
        )
        for k in range(2):
            obj += sec * y[r, k]
    m.setObjective(obj, GRB.MINIMIZE)
    m.optimize()
    if m.Status not in (GRB.OPTIMAL, GRB.SUBOPTIMAL):
        return None
    return 1 if z.X >= 0.5 else 0


def load_delta_frac_from_manifest(manifest: Path) -> Optional[float]:
    """读取 delta_encode_files.py 输出的 CSV，返回 delta_ratio_new 中位数。"""
    if not manifest.is_file():
        return None
    ratios: List[float] = []
    with manifest.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ratios.append(float(row["delta_ratio_new"]))
            except (KeyError, ValueError):
                pass
    if not ratios:
        return None
    ratios.sort()
    return ratios[len(ratios) // 2]


def run_grid(
    label: str,
    yaml_path: Path,
    hc,
    v_list: List[float],
    ttl_modes: List[Tuple[str, float]],
    delta_xfer_frac: float = 1.0,
) -> Tuple[int, int, int]:
    prof = hc.profiles_dir()
    regions = hc.load_regions_from_yaml(yaml_path)
    S = hc.load_price_storage(regions, prof)
    egress = hc.load_egress(regions, prof)

    rep_wins = ec_wins = total = 0
    ec_examples: List[str] = []

    print()
    print("=" * 72)
    print(f"{label}  ({yaml_path.name}, n={len(regions)} 区, delta_xfer_frac={delta_xfer_frac})")
    print("=" * 72)

    for issue in regions:
        for base in regions:
            for mode_name, warm_fac in ttl_modes:
                ttl_map = ttl_snapshot_for_issue(issue, regions, S, egress, mode_name, warm_fac)
                for v in v_list:
                    ch = milp_z_fb_fixed_base(
                        issue, base, v, ttl_map, regions, S, egress, hc, delta_xfer_frac
                    )
                    total += 1
                    if ch.z == 0:
                        rep_wins += 1
                    else:
                        ec_wins += 1
                        if len(ec_examples) < 5:
                            ec_examples.append(
                                f"  issue={issue.split(':')[1]} base={base.split(':')[1]} "
                                f"V={v:g} TTL={mode_name} rep=${ch.rep_cost:.4f} ec=${ch.ec_cost:.4f} "
                                f"EC@{ch.ec_regions}"
                            )

    print(f"  网格总数: {total}")
    print(f"  z=0 整副本: {rep_wins} ({100.0 * rep_wins / total:.1f}%)")
    print(f"  z=1 EC:     {ec_wins} ({100.0 * ec_wins / total:.1f}%)")
    if ec_examples:
        print("  EC 胜出示例（最多 5 条）:")
        for line in ec_examples:
            print(line)
    else:
        print("  本网格下 EC 未胜出。")
    return rep_wins, ec_wins, total


def run_trace_rows(
    label: str,
    data_dir: Path,
    issue: str,
    base: str,
    regions: List[str],
    S: Dict[str, float],
    egress: Dict[Tuple[str, str], float],
    hc,
    delta_xfer_frac: float = 1.0,
) -> None:
    traces = ["015", "029", "065", "078", "079"]
    print()
    print("=" * 72)
    print(f"{label} trace 中位对象（issue=base={issue.split(':')[1]}，TTL=cold + 30sim-day 边际）")
    print("=" * 72)
    print(f"{'Trace':<6} {'V_med GB':>12} {'z':>4} {'胜者':>8} {'Δ$':>10} {'最优副本区':>16}")
    for t in traces:
        p = data_dir / f"IBMObjectStoreTrace{t}Part0.typeA.mc.aug"
        v = trace_median_gb(p)
        if v is None or v <= 0:
            print(f"T{t:<5}  (无数据)")
            continue
        # 边际 TTL：30 sim-days（与手算脚本场景 B 一致）
        ttl_marg = {r: EXTRA_TTL_SIM_DAYS * 86400.0 for r in regions}
        ch = milp_z_fb_fixed_base(
            issue, base, v, ttl_marg, regions, S, egress, hc, delta_xfer_frac
        )
        rep_r = ch.rep_region.split(":")[1] if ch.rep_region else "-"
        print(
            f"T{t:<5} {v:>12.6f} {ch.z:>4} {ch.winner:>8} {ch.delta:>10.6f} {rep_r:>16}"
        )


def run_gurobi_crosscheck(hc, yaml_path: Path) -> None:
    try:
        import gurobipy  # noqa: F401
    except ImportError:
        print("\n(gurobipy 未安装，跳过 Gurobi 对照)")
        return

    prof = hc.profiles_dir()
    regions = hc.load_regions_from_yaml(yaml_path)
    S = hc.load_price_storage(regions, prof)
    egress = hc.load_egress(regions, prof)
    issue = base = regions[0]
    ttl_map = ttl_snapshot_for_issue(issue, regions, S, egress, "cold")
    mismatch = 0
    checked = 0
    for v in (0.001, 0.1, 1.0, 10.0):
        ch = milp_z_fb_fixed_base(issue, base, v, ttl_map, regions, S, egress, hc)
        gz = try_gurobi_verify(issue, base, v, ttl_map, regions, S, egress, hc)
        if gz is None:
            continue
        checked += 1
        if gz != ch.z:
            mismatch += 1
    print()
    print("=" * 72)
    print(f"Gurobi 对照 ({yaml_path.name}, issue=base={issue})")
    print("=" * 72)
    print(f"  检查 {checked} 组 V，穷举/z 分支不一致: {mismatch}")


def main() -> None:
    parser = argparse.ArgumentParser(description="验证 MILP z=0 整副本是否占主导")
    parser.add_argument(
        "--config",
        action="append",
        type=Path,
        default=None,
        help="YAML（可多次指定）；默认 3/6/9reg",
    )
    parser.add_argument("--gurobi", action="store_true", help="用 gurobipy 做小规模对照")
    parser.add_argument(
        "--delta-frac",
        type=float,
        default=None,
        help="replica 分支 placement egress 按 V×frac 计费（模拟真实 delta）；默认 1.0",
    )
    parser.add_argument(
        "--delta-manifest",
        type=Path,
        default=None,
        help="delta_encode_files.py 输出的 CSV；用其中 delta_ratio_new 中位数作为 --delta-frac",
    )
    parser.add_argument(
        "--sweep-delta-frac",
        action="store_true",
        help="额外扫描 frac=1,0.2,0.1,0.05,0.02 下 z=0 占比（3reg）",
    )
    args = parser.parse_args()

    hc = _load_handcalc()
    configs = args.config or DEFAULT_CONFIGS

    delta_frac = 1.0 if args.delta_frac is None else args.delta_frac
    if args.delta_manifest:
        m = load_delta_frac_from_manifest(args.delta_manifest)
        if m is not None:
            delta_frac = m
            print(f"从 manifest 读取 delta_xfer_frac 中位数: {delta_frac:.4f}")

    v_grid = [0.0001, 0.001, 0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 100.0]
    ttl_modes = [
        ("cold_teven/2", 1.0),
        ("warm_2x_cold", 2.0),
        ("warm_4x_cold", 4.0),
        ("warm_8x_cold", 8.0),
    ]

    print("MILP z 分支验证（FB fixed base，storage+egress，无 API）")
    print("  判定: min 边际 replica 成本 < min 边际 EC 成本  => z=0，否则 z=1")
    print(f"  replica 传输按对象大小 × {delta_frac:.4f} 计费（EC 仍满半片 egress）")
    print("  与 GurobiEc21MilpBuilder（FB 下 base 为常数项）目标一致。")

    grand_rep = grand_ec = grand_total = 0
    for cfg in configs:
        if not cfg.is_file():
            print(f"跳过缺失配置: {cfg}")
            continue
        rw, ew, tot = run_grid(
            f"配置 {cfg.stem}", cfg, hc, v_grid, ttl_modes, delta_frac
        )
        grand_rep += rw
        grand_ec += ew
        grand_total += tot

    print()
    print("=" * 72)
    print(f"合计 {len([c for c in configs if c.is_file()])} 套配置")
    print("=" * 72)
    print(f"  总场景: {grand_total}")
    print(f"  z=0 整副本: {grand_rep} ({100.0 * grand_rep / max(1, grand_total):.1f}%)")
    print(f"  z=1 EC:     {grand_ec} ({100.0 * grand_ec / max(1, grand_total):.1f}%)")

    # 3reg trace
    hc_mod = hc
    prof = hc_mod.profiles_dir()
    r3 = hc_mod.load_regions_from_yaml(ROOT / "config" / "milp_ec_new_v3_3reg.yaml")
    S3 = hc_mod.load_price_storage(r3, prof)
    e3 = hc_mod.load_egress(r3, prof)
    run_trace_rows(
        "3reg",
        ROOT / "Data" / "3reg" / "3reg" / "typeA",
        "aws:us-east-1",
        "aws:us-east-1",
        r3,
        S3,
        e3,
        hc_mod,
        delta_frac,
    )

    if args.sweep_delta_frac:
        print()
        print("=" * 72)
        print("delta_xfer_frac 敏感性（仅 3reg，issue=base=aws:us-east-1，V=1GB，TTL=30d）")
        print("=" * 72)
        cfg = ROOT / "config" / "milp_ec_new_v3_3reg.yaml"
        prof = hc.profiles_dir()
        regions = hc.load_regions_from_yaml(cfg)
        S = hc.load_price_storage(regions, prof)
        egress = hc.load_egress(regions, prof)
        ttl_marg = {r: EXTRA_TTL_SIM_DAYS * 86400.0 for r in regions}
        for frac in (1.0, 0.2, 0.1, 0.05, 0.02, 0.01):
            ch = milp_z_fb_fixed_base(
                "aws:us-east-1",
                "aws:us-east-1",
                1.0,
                ttl_marg,
                regions,
                S,
                egress,
                hc,
                frac,
            )
            print(f"  frac={frac:g} -> z={ch.z} ({ch.winner}) rep=${ch.rep_cost:.4f} ec=${ch.ec_cost:.4f}")

    # 结论
    print()
    print("=" * 72)
    print("结论（脚本层）")
    print("=" * 72)
    print(
        "  在 AWS/Azure/GCP 价目 + 跨区 egress 下，FB 边际 MILP 的 z 决策几乎总是 REPLICA：\n"
        "  EC 仅在存储单价极低且 egress 相对更高时，理论上才可能赢；\n"
        "  当前 3/6/9 区配置网格中若 EC 胜率为 0%，则「大部分情况选整副本」成立。\n"
        "  全 trace 仿真里 MILP 仍可能改变传输路径（CheapestTransferV3），总账单差异\n"
        "  不一定等于 z=0 —— 本脚本只验证 placement 分支 z，不替代仿真。"
    )

    if args.gurobi:
        run_gurobi_crosscheck(hc, ROOT / "config" / "milp_ec_new_v3_3reg.yaml")

    print("\n完成。")


if __name__ == "__main__":
    main()
