#!/usr/bin/env python3
"""
IBM / 增列 trace 的 Table-2 式特征画像（PolicyMilpECnewV4_Design.md §9.1）。

统计仅用 trace 已有字段（已完成请求），不预测下一条 op。
time_to_next_access 仅用于「访问间隔分布」画像，不注入策略。

用法:
  python scripts/trace_characteristics_report.py
  python scripts/trace_characteristics_report.py --config config/trace_characteristics.yaml
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

OUT_FIELDS = [
    "trace_path",
    "trace_label",
    "total_rows",
    "put_count",
    "get_count",
    "put_get_ratio",
    "distinct_keys",
    "has_delta_cols",
    "delta_member_rows",
    "delta_member_key_ratio",
    "delta_ratio_p50",
    "size_tiny_pct",
    "size_small_pct",
    "size_medium_pct",
    "size_large_pct",
    "get_one_hit_pct",
    "get_cold_pct",
    "get_warm_pct",
    "get_hot_pct",
    "get_super_hot_pct",
    "interval_lt_1d_pct",
    "interval_1d_1mo_pct",
    "interval_gt_1mo_pct",
    "top_issue_regions",
]


def load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        raise SystemExit("需要 PyYAML: pip install pyyaml")
    return yaml.safe_load(text)


def resolve_globs(cfg: dict) -> List[Path]:
    globs = cfg.get("trace_globs", [])
    paths: List[Path] = []
    for g in globs:
        p = ROOT / g
        if p.is_file():
            paths.append(p)
        else:
            # 允许简单 glob
            parent = p.parent
            if parent.exists():
                for f in sorted(parent.glob(p.name)):
                    if f.is_file():
                        paths.append(f)
    seen = set()
    out: List[Path] = []
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def bucket_label(size: int, buckets: Dict[str, List[int]]) -> str:
    for name, (lo, hi) in buckets.items():
        if lo <= size < hi:
            return name
    return "unknown"


def trace_label(path: Path) -> str:
    name = path.name
    for tag in ("015", "029", "065", "078", "079"):
        if tag in name:
            return f"T{tag}"
    return name[:32]


def analyze_trace(
    path: Path,
    size_buckets: Dict[str, List[int]],
    get_buckets: Dict[str, List[int]],
) -> dict:
    put = get = 0
    keys: set = set()
    key_gets: Counter = Counter()
    key_last_get_ts: Dict[str, int] = {}
    intervals: List[int] = []
    size_hist: Counter = Counter()
    get_freq_hist: Counter = Counter()
    interval_hist = Counter({"lt_1d": 0, "d1_1mo": 0, "gt_1mo": 0})
    region_gets: Counter = Counter()
    has_delta = False
    delta_rows = 0
    delta_ratios: List[float] = []

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        has_delta = "is_base" in fields and "delta_ratio" in fields
        for row in reader:
            op = (row.get("op") or "").strip().upper()
            key = (row.get("obj_key") or "").strip()
            if not key:
                continue
            keys.add(key)
            try:
                ts = int(float(row.get("timestamp") or 0))
            except ValueError:
                ts = 0
            try:
                size = int(float(row.get("size") or 0))
            except ValueError:
                size = 0
            region = (row.get("issue_region") or "").strip()

            if op == "PUT":
                put += 1
            elif op == "GET":
                get += 1
                key_gets[key] += 1
                region_gets[region] += 1
                if key in key_last_get_ts and ts > key_last_get_ts[key]:
                    dt = ts - key_last_get_ts[key]
                    intervals.append(dt)
                    if dt < 86400:
                        interval_hist["lt_1d"] += 1
                    elif dt < 2592000:
                        interval_hist["d1_1mo"] += 1
                    else:
                        interval_hist["gt_1mo"] += 1
                key_last_get_ts[key] = ts

            if op in ("PUT", "GET"):
                size_hist[bucket_label(size, size_buckets)] += 1

            if has_delta:
                is_base = (row.get("is_base") or "1").strip() in ("1", "true", "True")
                if not is_base:
                    delta_rows += 1
                    try:
                        delta_ratios.append(float(row.get("delta_ratio") or 1.0))
                    except ValueError:
                        pass

    for k, c in key_gets.items():
        label = "one_hit"
        for name, (lo, hi) in get_buckets.items():
            if lo <= c <= hi:
                label = name
                break
        get_freq_hist[label] += 1

    total_get_keys = sum(get_freq_hist.values()) or 1
    total_intervals = sum(interval_hist.values()) or 1
    total_ops = put + get or 1
    size_total = sum(size_hist.values()) or 1

    def pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 2)

    def pct_bucket(name: str) -> float:
        return pct(size_hist.get(name, 0), size_total)

    def pct_get_freq(name: str) -> float:
        return pct(get_freq_hist.get(name, 0), total_get_keys)

    delta_ratio_p50 = 0.0
    if delta_ratios:
        s = sorted(delta_ratios)
        delta_ratio_p50 = s[len(s) // 2]

    top_regions = ", ".join(f"{r}:{c}" for r, c in region_gets.most_common(5))

    return {
        "trace_path": str(path.relative_to(ROOT)),
        "trace_label": trace_label(path),
        "total_rows": put + get,
        "put_count": put,
        "get_count": get,
        "put_get_ratio": round(put / max(get, 1), 4),
        "distinct_keys": len(keys),
        "has_delta_cols": has_delta,
        "delta_member_rows": delta_rows,
        "delta_member_key_ratio": round(delta_rows / max(total_ops, 1), 6),
        "delta_ratio_p50": delta_ratio_p50,
        "size_tiny_pct": pct_bucket("tiny"),
        "size_small_pct": pct_bucket("small"),
        "size_medium_pct": pct_bucket("medium"),
        "size_large_pct": pct_bucket("large"),
        "get_one_hit_pct": pct_get_freq("one_hit"),
        "get_cold_pct": pct_get_freq("cold"),
        "get_warm_pct": pct_get_freq("warm"),
        "get_hot_pct": pct_get_freq("hot"),
        "get_super_hot_pct": pct_get_freq("super_hot"),
        "interval_lt_1d_pct": pct(interval_hist["lt_1d"], total_intervals),
        "interval_1d_1mo_pct": pct(interval_hist["d1_1mo"], total_intervals),
        "interval_gt_1mo_pct": pct(interval_hist["gt_1mo"], total_intervals),
        "top_issue_regions": top_regions,
        "_interval_count": len(intervals),
        "_get_keys_with_reads": total_get_keys,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Trace Table-2 特征画像")
    ap.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "trace_characteristics.yaml",
    )
    args = ap.parse_args()
    cfg = load_config(args.config)
    paths = resolve_globs(cfg)
    if not paths:
        raise SystemExit("未匹配到任何 trace 文件")

    size_buckets = {
        k: list(v) for k, v in cfg.get("size_buckets_bytes", {}).items()
    }
    get_buckets = {
        k: list(v) for k, v in cfg.get("get_freq_buckets", {}).items()
    }
    out_dir = ROOT / cfg.get("output_dir", "results/trace_characteristics")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "table2_report.csv"
    json_path = out_dir / "table2_report.json"

    rows: List[dict] = []
    for p in paths:
        print(f"[trace_char] {p.relative_to(ROOT)} ...", flush=True)
        rows.append(analyze_trace(p, size_buckets, get_buckets))

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    json_path.write_text(
        json.dumps({"traces": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[trace_char] CSV  -> {csv_path.relative_to(ROOT)} ({len(rows)} traces)")
    print(f"[trace_char] JSON -> {json_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
