#!/usr/bin/env python3
"""
方式 B 多云合成：在 IBM/SNIA 多区域 trace 上追加 delta 列，供 V4 仿真消费。

设计依据：docs/PolicyMilpECnewV4_Design.md §5 / §6.1 / §9 阶段 2

核心原则（规格驱动）：
  1. **方案 I 相似簇**：不增删 trace 行，仅在末尾追加 is_base, base_key, delta_ratio。
  2. **对象层**：Finesse manifest 提供真实 size / 簇结构 / 逐对象 r（或 D_r 分布抽样）。
  3. **访问层**：沿用 IBM .aug trace 的时间线、op、issue_region（已含 Type A–D 区域映射）。
  4. **基线零影响**：旧策略忽略新列；增列不改变行数与其它字段。

模式：
  augment       在现有 trace 上分相似簇，delta 成员 r 从 manifest D_r 抽样（默认，可扩展）。
  manifest-map  把 manifest 的真实簇结构映射到 trace 的 obj_key（逐对象真实 r，不抽样）。
  synthesize    完整方式 B：IBM 访问时间线 + Finesse 对象池（size/簇/r 来自 manifest）。

用法（仓库根目录）：
  # 最常见：IBM trace + sqldump manifest，分簇并增列
  python scripts/synth_multicloud_delta_trace.py augment \\
    --trace Data/3reg/3reg/typeA/IBMObjectStoreTrace078Part0.typeA.mc.aug \\
    --manifest results/finesse_sqldump_manifest.csv \\
    --out results/trace078.delta.mc.aug \\
    --cluster-fraction 0.05 --seed 42

  # 把 manifest 真实簇直接映射到 trace key（适合小 manifest + 大 trace）
  python scripts/synth_multicloud_delta_trace.py manifest-map \\
    --trace Data/3reg/3reg/typeA/IBMObjectStoreTrace078Part0.typeA.mc.aug \\
    --manifest results/finesse_sqldump_manifest.csv \\
    --out results/trace078.manifest-map.mc.aug

  # 干跑：只处理前 5000 行验证
  python scripts/synth_multicloud_delta_trace.py augment ... --max-rows 5000 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

# 设计 §5.2：簇大小 2–8
DEFAULT_CLUSTER_SIZE_WEIGHTS: Dict[int, float] = {
    2: 0.30,
    3: 0.25,
    4: 0.20,
    5: 0.10,
    6: 0.08,
    7: 0.04,
    8: 0.03,
}

DELTA_COLS = ("is_base", "base_key", "delta_ratio")


@dataclass
class ObjectMeta:
    """一个逻辑对象的 delta 画像（trace 级）。"""

    key: str
    size: int = 0
    is_base: bool = True
    base_key: str = ""
    delta_ratio: float = 1.0
    manifest_key: str = ""  # 映射到的 manifest 逻辑名（若有）


@dataclass
class DrStats:
    count: int = 0
    min_r: float = 0.0
    p10: float = 0.0
    p50: float = 0.0
    p90: float = 0.0
    max_r: float = 0.0
    mean_r: float = 0.0

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "min": self.min_r,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "max": self.max_r,
            "mean": self.mean_r,
        }


@dataclass
class ManifestEntry:
    logical_key: str
    is_base: bool
    base_key: str
    size: int
    delta_ratio: float


@dataclass
class SynthSummary:
    mode: str
    seed: int
    input_trace: str
    manifest: str
    output_trace: str
    input_rows: int = 0
    output_rows: int = 0
    distinct_keys: int = 0
    clustered_keys: int = 0
    cluster_count: int = 0
    base_count: int = 0
    delta_member_count: int = 0
    dr_from_manifest: DrStats = field(default_factory=DrStats)
    dr_assigned: DrStats = field(default_factory=DrStats)
    cluster_fraction: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "mode": self.mode,
            "seed": self.seed,
            "input_trace": self.input_trace,
            "manifest": self.manifest,
            "output_trace": self.output_trace,
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "distinct_keys": self.distinct_keys,
            "clustered_keys": self.clustered_keys,
            "cluster_count": self.cluster_count,
            "base_count": self.base_count,
            "delta_member_count": self.delta_member_count,
            "cluster_fraction": self.cluster_fraction,
            "dr_from_manifest": self.dr_from_manifest.to_dict(),
            "dr_assigned": self.dr_assigned.to_dict(),
        }
        return d


def percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    frac = idx - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def compute_dr_stats(values: Iterable[float]) -> DrStats:
    arr = sorted(float(v) for v in values)
    if not arr:
        return DrStats()
    return DrStats(
        count=len(arr),
        min_r=arr[0],
        p10=percentile(arr, 10),
        p50=percentile(arr, 50),
        p90=percentile(arr, 90),
        max_r=arr[-1],
        mean_r=sum(arr) / len(arr),
    )


def load_manifest(path: Path) -> Tuple[List[ManifestEntry], DrStats]:
    entries: List[ManifestEntry] = []
    delta_rs: List[float] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get("logical_key") or row.get("obj_key") or ""
            if not key:
                continue
            is_base = (row.get("is_base") or "1").strip() in ("1", "true", "True")
            base_key = (row.get("base_key") or "").strip()
            size = int(float(row.get("size") or 0))
            delta_ratio = float(row.get("delta_ratio") or 1.0)
            entries.append(
                ManifestEntry(
                    logical_key=key,
                    is_base=is_base,
                    base_key=base_key,
                    size=size,
                    delta_ratio=delta_ratio,
                )
            )
            if not is_base:
                delta_rs.append(delta_ratio)
    return entries, compute_dr_stats(delta_rs)


def flatten_manifest_clusters(entries: List[ManifestEntry]) -> Dict[str, ManifestEntry]:
    """
    把 manifest 中可能存在的链式 base（v2 diff v1, v3 diff v2）压平为「单层 delta」：
    每个成员直接指向簇根 base（设计 §8.4）。
    """
    by_key = {e.logical_key: e for e in entries}

    def cluster_root(key: str) -> str:
        seen = set()
        cur = key
        while True:
            e = by_key.get(cur)
            if e is None or not e.base_key or e.base_key == cur:
                return cur
            if e.base_key in seen:
                return e.base_key
            seen.add(cur)
            cur = e.base_key

    flat: Dict[str, ManifestEntry] = {}
    for e in entries:
        root = cluster_root(e.logical_key)
        is_root = e.logical_key == root
        flat[e.logical_key] = ManifestEntry(
            logical_key=e.logical_key,
            is_base=is_root,
            base_key="" if is_root else root,
            size=e.size,
            delta_ratio=1.0 if is_root else e.delta_ratio,
        )
    return flat


def sample_cluster_size(rng: random.Random, weights: Dict[int, float]) -> int:
    sizes = sorted(weights.keys())
    probs = [weights[s] for s in sizes]
    return rng.choices(sizes, weights=probs, k=1)[0]


def form_clusters_augment(
    keys: List[str],
    key_sizes: Dict[str, int],
    cluster_fraction: float,
    size_weights: Dict[int, float],
    dr_pool: List[float],
    rng: random.Random,
) -> Dict[str, ObjectMeta]:
    """
    设计 §5.2：按比例 p 把 key 分入相似簇；每簇 1 base + 成员，成员 r 从 D_r 抽样。
    未入簇 key：is_base=1, delta_ratio=1.0。
    """
    meta: Dict[str, ObjectMeta] = {
        k: ObjectMeta(key=k, size=key_sizes.get(k, 0)) for k in keys
    }
    if not keys or cluster_fraction <= 0:
        return meta

    shuffled = keys[:]
    rng.shuffle(shuffled)
    target = max(2, int(len(keys) * cluster_fraction))
    if target < 2:
        return meta

    clusters: List[List[str]] = []
    i = 0
    assigned = 0
    while i < len(shuffled) and assigned < target:
        want = sample_cluster_size(rng, size_weights)
        want = max(2, min(want, target - assigned, len(shuffled) - i))
        if want < 2:
            break
        group = shuffled[i : i + want]
        if len(group) < 2:
            break
        clusters.append(group)
        assigned += len(group)
        i += want

    if not dr_pool:
        dr_pool = [0.01]  # 无 manifest 时的兜底（应警告）

    for group in clusters:
        base = max(group, key=lambda k: key_sizes.get(k, 0))
        meta[base] = ObjectMeta(
            key=base,
            size=key_sizes.get(base, 0),
            is_base=True,
            base_key="",
            delta_ratio=1.0,
        )
        for member in group:
            if member == base:
                continue
            meta[member] = ObjectMeta(
                key=member,
                size=key_sizes.get(member, 0),
                is_base=False,
                base_key=base,
                delta_ratio=rng.choice(dr_pool),
            )
    return meta


def _manifest_cluster_templates(
    manifest_flat: Dict[str, ManifestEntry],
) -> List[List[ManifestEntry]]:
    """按簇根分组；每组内 base 在前，成员在后（保持 manifest 真实 r）。"""
    by_root: Dict[str, List[ManifestEntry]] = defaultdict(list)
    for e in manifest_flat.values():
        root = e.logical_key if e.is_base or not e.base_key else e.base_key
        by_root[root].append(e)
    templates: List[List[ManifestEntry]] = []
    for root in sorted(by_root.keys()):
        group = by_root[root]
        group.sort(
            key=lambda x: (0 if x.is_base or not x.base_key else 1, x.logical_key),
        )
        templates.append(group)
    return templates


def build_meta_manifest_map(
    trace_keys: List[str],
    key_sizes: Dict[str, int],
    manifest_flat: Dict[str, ManifestEntry],
    rng: random.Random,
) -> Dict[str, ObjectMeta]:
    """
    把 manifest 的真实簇模板循环映射到 trace 的 distinct key。
    每个模板簇保持逐对象真实 r（不抽样）。
    """
    cluster_list = _manifest_cluster_templates(manifest_flat)
    if not cluster_list:
        raise ValueError("manifest 为空")
    rng.shuffle(cluster_list)

    meta: Dict[str, ObjectMeta] = {
        k: ObjectMeta(key=k, size=key_sizes.get(k, 0)) for k in trace_keys
    }
    c_idx = 0
    k_idx = 0
    while k_idx < len(trace_keys):
        template = cluster_list[c_idx % len(cluster_list)]
        c_idx += 1
        base_trace_key: Optional[str] = None
        for entry in template:
            if k_idx >= len(trace_keys):
                break
            tk = trace_keys[k_idx]
            k_idx += 1
            is_base = entry.is_base or not entry.base_key
            if is_base:
                base_trace_key = tk
                meta[tk] = ObjectMeta(
                    key=tk,
                    size=entry.size if entry.size > 0 else key_sizes.get(tk, 0),
                    is_base=True,
                    base_key="",
                    delta_ratio=1.0,
                    manifest_key=entry.logical_key,
                )
            else:
                meta[tk] = ObjectMeta(
                    key=tk,
                    size=entry.size if entry.size > 0 else key_sizes.get(tk, 0),
                    is_base=False,
                    base_key=base_trace_key or "",
                    delta_ratio=entry.delta_ratio,
                    manifest_key=entry.logical_key,
                )
    return meta


def build_meta_synthesize(
    trace_keys: List[str],
    key_sizes: Dict[str, int],
    manifest_flat: Dict[str, ManifestEntry],
    rng: random.Random,
) -> Dict[str, ObjectMeta]:
    """
    完整方式 B：每个 trace key 循环映射到 manifest 对象池，继承 size/簇/r。
    IBM trace 提供访问时间线；Finesse manifest 提供对象总体（可复用同一对象）。
    """
    pool = sorted(manifest_flat.keys())
    if not pool:
        raise ValueError("manifest 为空")

    manifest_to_trace: Dict[str, str] = {}
    meta: Dict[str, ObjectMeta] = {}

    for i, tk in enumerate(trace_keys):
        mk = pool[i % len(pool)]
        entry = manifest_flat[mk]
        is_base = entry.is_base or not entry.base_key
        meta[tk] = ObjectMeta(
            key=tk,
            size=entry.size if entry.size > 0 else key_sizes.get(tk, 0),
            is_base=is_base,
            base_key="",
            delta_ratio=1.0 if is_base else entry.delta_ratio,
            manifest_key=entry.logical_key,
        )
        manifest_to_trace.setdefault(entry.logical_key, tk)

    for tk, m in meta.items():
        if m.is_base:
            continue
        me = manifest_flat.get(m.manifest_key)
        if me is None or not me.base_key:
            m.is_base = True
            m.delta_ratio = 1.0
            continue
        trace_base = manifest_to_trace.get(me.base_key)
        if trace_base:
            m.base_key = trace_base
        else:
            m.is_base = True
            m.delta_ratio = 1.0
    return meta


def scan_trace_keys(
    trace_path: Path,
    max_rows: Optional[int] = None,
) -> Tuple[List[str], Dict[str, int], int]:
    """第一遍扫描：distinct obj_key 顺序 + 各 key 最大 size + 行数。"""
    keys_order: List[str] = []
    seen = set()
    key_sizes: Dict[str, int] = {}
    rows = 0
    with trace_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if "obj_key" not in (reader.fieldnames or []):
            raise ValueError(f"trace 缺少 obj_key 列: {trace_path}")
        for row in reader:
            rows += 1
            if max_rows and rows > max_rows:
                break
            k = (row.get("obj_key") or "").strip()
            if not k:
                continue
            if k not in seen:
                seen.add(k)
                keys_order.append(k)
            try:
                sz = int(float(row.get("size") or 0))
            except ValueError:
                sz = 0
            key_sizes[k] = max(key_sizes.get(k, 0), sz)
    return keys_order, key_sizes, rows


def write_augmented_trace(
    trace_path: Path,
    out_path: Path,
    meta: Dict[str, ObjectMeta],
    max_rows: Optional[int] = None,
) -> int:
    """第二遍流式写回：追加三列，其它列原样保留。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with trace_path.open("r", encoding="utf-8", newline="") as fin, out_path.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.DictReader(fin)
        fieldnames = list(reader.fieldnames or [])
        for col in DELTA_COLS:
            if col not in fieldnames:
                fieldnames.append(col)
        writer = csv.DictWriter(fout, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in reader:
            written += 1
            if max_rows and written > max_rows:
                break
            k = (row.get("obj_key") or "").strip()
            m = meta.get(k)
            if m is None:
                m = ObjectMeta(key=k)
            row["is_base"] = "1" if m.is_base else "0"
            row["base_key"] = m.base_key if m.base_key else ""
            row["delta_ratio"] = f"{m.delta_ratio:.8f}"
            # synthesize / manifest-map 可覆盖 size
            if m.size > 0 and m.manifest_key:
                row["size"] = str(m.size)
            writer.writerow(row)
    return written


def verify_baseline_columns(
    input_path: Path,
    output_path: Path,
    max_rows: Optional[int] = None,
) -> List[str]:
    """
    验收 §11.1「基线零影响」：增列 trace 的原始列必须与输入逐行一致。
    只比对输入表头中的列（不含新增的 is_base/base_key/delta_ratio）。
    """
    errors: List[str] = []
    with input_path.open("r", encoding="utf-8", newline="") as fin, output_path.open(
        "r", encoding="utf-8", newline=""
    ) as fout:
        rin = csv.DictReader(fin)
        rout = csv.DictReader(fout)
        base_cols = list(rin.fieldnames or [])
        row_num = 0
        for in_row, out_row in zip(rin, rout):
            row_num += 1
            if max_rows and row_num > max_rows:
                break
            for col in base_cols:
                if in_row.get(col) != out_row.get(col):
                    errors.append(
                        f"行 {row_num} 列 {col}: 输入={in_row.get(col)!r} 输出={out_row.get(col)!r}"
                    )
                    if len(errors) >= 20:
                        return errors
        # 检查行数（若未限 max_rows）
        if not max_rows:
            extra_in = sum(1 for _ in rin)
            extra_out = sum(1 for _ in rout)
            if extra_in or extra_out:
                errors.append(
                    f"行数不一致: 输入多读 {extra_in} 行, 输出多读 {extra_out} 行"
                )
    return errors


def validate_meta(meta: Dict[str, ObjectMeta]) -> List[str]:
    errors: List[str] = []
    for k, m in meta.items():
        if m.is_base:
            if m.delta_ratio != 1.0 and m.delta_ratio < 0.999999:
                errors.append(f"base {k} 的 delta_ratio 应为 1.0，实际 {m.delta_ratio}")
            if m.base_key:
                errors.append(f"base {k} 不应有 base_key")
        else:
            if not m.base_key:
                errors.append(f"delta 成员 {k} 缺少 base_key")
            elif m.base_key not in meta:
                errors.append(f"delta 成员 {k} 的 base_key={m.base_key} 不存在")
            elif not meta[m.base_key].is_base:
                errors.append(f"delta 成员 {k} 的 base {m.base_key} 不是 is_base")
            if m.delta_ratio <= 0 or m.delta_ratio > 1.0:
                errors.append(f"delta 成员 {k} 的 r 越界: {m.delta_ratio}")
    return errors


def run(args: argparse.Namespace) -> SynthSummary:
    trace_path = Path(args.trace)
    manifest_path = Path(args.manifest) if args.manifest else None
    out_path = Path(args.out)
    summary_path = Path(args.summary) if args.summary else out_path.with_suffix(
        out_path.suffix + ".summary.json"
    )

    rng = random.Random(args.seed)
    size_weights = DEFAULT_CLUSTER_SIZE_WEIGHTS
    if args.cluster_size_weights:
        size_weights = {
            int(k): float(v) for k, v in json.loads(args.cluster_size_weights).items()
        }

    entries: List[ManifestEntry] = []
    dr_manifest = DrStats()
    dr_pool: List[float] = []
    manifest_flat: Dict[str, ManifestEntry] = {}

    if manifest_path:
        entries, dr_manifest = load_manifest(manifest_path)
        manifest_flat = flatten_manifest_clusters(entries)
        dr_pool = [
            e.delta_ratio
            for e in manifest_flat.values()
            if not e.is_base and e.delta_ratio < 1.0
        ]
    elif args.mode in ("manifest-map", "synthesize"):
        raise SystemExit(f"模式 {args.mode} 需要 --manifest")

    keys_order, key_sizes, input_rows = scan_trace_keys(trace_path, args.max_rows)

    if args.mode == "augment":
        if not dr_pool and manifest_path:
            print(
                "警告: manifest 中无 delta 成员，将使用兜底 r=0.01",
                file=sys.stderr,
            )
        meta = form_clusters_augment(
            keys_order,
            key_sizes,
            args.cluster_fraction,
            size_weights,
            dr_pool,
            rng,
        )
    elif args.mode == "manifest-map":
        meta = build_meta_manifest_map(keys_order, key_sizes, manifest_flat, rng)
    elif args.mode == "synthesize":
        meta = build_meta_synthesize(keys_order, key_sizes, manifest_flat, rng)
    else:
        raise SystemExit(f"未知模式: {args.mode}")

    errors = validate_meta(meta)
    if errors:
        for e in errors[:20]:
            print(f"校验失败: {e}", file=sys.stderr)
        raise SystemExit(f"元数据校验失败 ({len(errors)} 项)")

    if args.dry_run:
        print(f"[dry-run] 模式={args.mode}, distinct_keys={len(keys_order)}, 校验通过")
        summary = SynthSummary(
            mode=args.mode,
            seed=args.seed,
            input_trace=str(trace_path),
            manifest=str(manifest_path) if manifest_path else "",
            output_trace=str(out_path),
            input_rows=input_rows,
            distinct_keys=len(keys_order),
            clustered_keys=sum(1 for m in meta.values() if not m.is_base or m.base_key),
            cluster_fraction=args.cluster_fraction,
            dr_from_manifest=dr_manifest,
        )
        summary.base_count = sum(1 for m in meta.values() if m.is_base)
        summary.delta_member_count = sum(1 for m in meta.values() if not m.is_base)
        summary.dr_assigned = compute_dr_stats(
            m.delta_ratio for m in meta.values() if not m.is_base
        )
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return summary

    output_rows = write_augmented_trace(trace_path, out_path, meta, args.max_rows)

    if getattr(args, "verify_baseline", False):
        verr = verify_baseline_columns(trace_path, out_path, args.max_rows)
        if verr:
            for e in verr[:10]:
                print(f"基线校验失败: {e}", file=sys.stderr)
            raise SystemExit(f"基线列校验失败 ({len(verr)} 项)")

    base_count = sum(1 for m in meta.values() if m.is_base)
    delta_count = sum(1 for m in meta.values() if not m.is_base)
    clustered = sum(1 for m in meta.values() if not m.is_base)
    # 粗估簇数：每个 base 至少对应 1 个成员或单独成簇
    bases_with_members = defaultdict(int)
    for m in meta.values():
        if not m.is_base and m.base_key:
            bases_with_members[m.base_key] += 1
    cluster_count = len(bases_with_members)

    summary = SynthSummary(
        mode=args.mode,
        seed=args.seed,
        input_trace=str(trace_path),
        manifest=str(manifest_path) if manifest_path else "",
        output_trace=str(out_path),
        input_rows=input_rows,
        output_rows=output_rows,
        distinct_keys=len(keys_order),
        clustered_keys=clustered,
        cluster_count=cluster_count,
        base_count=base_count,
        delta_member_count=delta_count,
        cluster_fraction=args.cluster_fraction,
        dr_from_manifest=dr_manifest,
        dr_assigned=compute_dr_stats(
            m.delta_ratio for m in meta.values() if not m.is_base
        ),
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[synth] 模式={args.mode}")
    print(f"  输入 trace: {trace_path} ({input_rows:,} 行, {len(keys_order):,} keys)")
    print(f"  输出 trace: {out_path} ({output_rows:,} 行)")
    print(f"  摘要: {summary_path}")
    print(
        f"  簇: {cluster_count:,} 个, delta 成员 {delta_count:,} / {len(keys_order):,} keys"
    )
    if summary.dr_from_manifest.count:
        d = summary.dr_from_manifest
        print(
            f"  manifest D_r: n={d.count} p50={d.p50:.6f} mean={d.mean_r:.6f}"
        )
    if summary.dr_assigned.count:
        d = summary.dr_assigned
        print(
            f"  赋值 D_r:   n={d.count} p50={d.p50:.6f} mean={d.mean_r:.6f}"
        )
    if input_rows != output_rows and not args.max_rows:
        print(
            f"  警告: 行数变化 {input_rows} -> {output_rows}",
            file=sys.stderr,
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="方式 B：在多云 trace 上追加 is_base/base_key/delta_ratio（PolicyMilpECnewV4 阶段 2）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "mode",
        choices=["augment", "manifest-map", "synthesize"],
        help="augment=分簇+D_r抽样; manifest-map=真实簇映射; synthesize=IBM时间线+Finesse对象池",
    )
    p.add_argument("--trace", required=True, help="输入 .mc.aug trace（已含 Type A–D 区域）")
    p.add_argument("--manifest", help="FinesseRunner 产出的 manifest.csv")
    p.add_argument("--out", required=True, help="输出增列 trace 路径")
    p.add_argument(
        "--summary",
        help="摘要 JSON 路径（默认 <out>.summary.json）",
    )
    p.add_argument(
        "--cluster-fraction",
        type=float,
        default=0.05,
        help="augment 模式：参与相似簇的 key 比例 p（默认 0.05）",
    )
    p.add_argument("--seed", type=int, default=42, help="随机种子（可复现）")
    p.add_argument(
        "--cluster-size-weights",
        help='簇大小权重 JSON，如 \'{"2":0.5,"3":0.3,"4":0.2}\'',
    )
    p.add_argument(
        "--max-rows",
        type=int,
        help="仅处理前 N 行（调试/干跑）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只构建元数据并校验，不写 trace",
    )
    p.add_argument(
        "--verify-baseline",
        action="store_true",
        help="写盘后校验原始列与输入逐行一致（§11.1 基线零影响）",
    )
    return p


def self_check() -> None:
    """规格自检：簇结构、单层 base、行数守恒、D_r 抽样可复现。"""
    import tempfile

    ok = True

    # 1) flatten 链式 manifest → 单层 base
    chained = [
        ManifestEntry("a", True, "", 100, 1.0),
        ManifestEntry("b", False, "a", 100, 0.01),
        ManifestEntry("c", False, "b", 100, 0.02),
    ]
    flat = flatten_manifest_clusters(chained)
    if flat["c"].base_key != "a":
        print("FAIL: flatten 未把 c.base_key 压到簇根 a")
        ok = False

    # 2) augment 可复现 + 校验
    keys = [f"k{i}" for i in range(100)]
    sizes = {k: 1000 + i for i, k in enumerate(keys)}
    dr = [0.01, 0.02, 0.03]
    m1 = form_clusters_augment(keys, sizes, 0.2, DEFAULT_CLUSTER_SIZE_WEIGHTS, dr, random.Random(7))
    m2 = form_clusters_augment(keys, sizes, 0.2, DEFAULT_CLUSTER_SIZE_WEIGHTS, dr, random.Random(7))
    if {k: (v.is_base, v.base_key, v.delta_ratio) for k, v in m1.items()} != {
        k: (v.is_base, v.base_key, v.delta_ratio) for k, v in m2.items()
    }:
        print("FAIL: augment 同 seed 不可复现")
        ok = False
    if validate_meta(m1):
        print("FAIL: augment 元数据校验失败")
        ok = False

    # 3) 行数守恒 + 列追加
    trace_csv = (
        "timestamp,op,issue_region,obj_key,size,time_to_next_access,time_to_next_access_same_reg\n"
        "0,PUT,aws:us-east-1,k0,100,1,1\n"
        "1,GET,aws:us-east-1,k0,100,2,2\n"
        "2,PUT,gcp:us-west1-a,k1,200,3,3\n"
    )
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        trace_f = td_path / "t.csv"
        out_f = td_path / "out.csv"
        trace_f.write_text(trace_csv, encoding="utf-8")
        meta = {
            "k0": ObjectMeta("k0", 100, True, "", 1.0),
            "k1": ObjectMeta("k1", 200, False, "k0", 0.01),
        }
        n = write_augmented_trace(trace_f, out_f, meta)
        if n != 3:
            print(f"FAIL: 期望 3 行，实际 {n}")
            ok = False
        lines = out_f.read_text(encoding="utf-8").strip().splitlines()
        hdr = lines[0].split(",")
        if list(DELTA_COLS) != hdr[-3:]:
            print(f"FAIL: 末尾列应为 {DELTA_COLS}，实际 {hdr[-3:]}")
            ok = False
        k1_line = next((ln for ln in lines if ",k1," in ln), "")
        if "0,k0,0.01000000" not in k1_line:
            print(f"FAIL: k1 行 delta 列不正确: {k1_line}")
            ok = False

    if ok:
        print("self-check: PASS (3/3)")
    else:
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        self_check()
        return
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
