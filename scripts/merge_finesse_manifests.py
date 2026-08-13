#!/usr/bin/env python3
"""
合并多个 Finesse / delta 测量 manifest，产出统一 D_r 池供阶段 2 合成脚本抽样。

支持输入：
  - FinesseRunner 格式：dataset,logical_key,is_base,base_key,size,delta_ratio,...
  - delta_encode_files 格式：logical_key,old_path,new_path,...,delta_ratio_new,...

用法：
  python scripts/merge_finesse_manifests.py \\
    --finesse results/finesse_sqldump_manifest.csv \\
    --delta-stats results/delta_stats_kernel.csv --dataset kernel \\
    --out results/finesse_merged_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

ROOT = Path(__file__).resolve().parents[1]

OUT_FIELDS = [
    "dataset",
    "logical_key",
    "is_base",
    "base_key",
    "size",
    "delta_ratio",
    "shared_sf",
    "copied_chunks",
    "added_chunks",
    "source",
]


@dataclass
class DrStats:
    count: int = 0
    min_r: float = 0.0
    p50: float = 0.0
    max_r: float = 0.0
    mean_r: float = 0.0

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "min": self.min_r,
            "p50": self.p50,
            "max": self.max_r,
            "mean": self.mean_r,
        }


def percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def dr_stats(values: Iterable[float]) -> DrStats:
    arr = sorted(float(v) for v in values)
    if not arr:
        return DrStats()
    return DrStats(
        count=len(arr),
        min_r=arr[0],
        p50=percentile(arr, 50),
        max_r=arr[-1],
        mean_r=sum(arr) / len(arr),
    )


def load_finesse_manifest(path: Path, default_dataset: str) -> List[dict]:
    rows: List[dict] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("logical_key") or row.get("obj_key") or "").strip()
            if not key:
                continue
            is_base = (row.get("is_base") or "1").strip() in ("1", "true", "True")
            rows.append(
                {
                    "dataset": row.get("dataset") or default_dataset,
                    "logical_key": key,
                    "is_base": "1" if is_base else "0",
                    "base_key": (row.get("base_key") or "").strip(),
                    "size": row.get("size") or "0",
                    "delta_ratio": row.get("delta_ratio") or "1.0",
                    "shared_sf": row.get("shared_sf") or "0",
                    "copied_chunks": row.get("copied_chunks") or "0",
                    "added_chunks": row.get("added_chunks") or "0",
                    "source": f"finesse:{path.name}",
                }
            )
    return rows


def load_delta_stats(path: Path, dataset: str) -> List[dict]:
    """把 delta_encode_files 输出转为 Finesse manifest 行（相邻版本对）。"""
    rows: List[dict] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            old_name = Path(row.get("old_path") or "").name
            new_name = Path(row.get("new_path") or "").name
            base_key = old_name or (row.get("logical_key") or "")
            member_key = new_name or (row.get("logical_key") or "")
            ratio = row.get("delta_ratio_new") or row.get("delta_ratio") or "1.0"
            new_bytes = row.get("new_bytes") or "0"
            rows.append(
                {
                    "dataset": dataset,
                    "logical_key": base_key,
                    "is_base": "1",
                    "base_key": "",
                    "size": row.get("old_bytes") or new_bytes,
                    "delta_ratio": "1.0",
                    "shared_sf": "0",
                    "copied_chunks": "0",
                    "added_chunks": "0",
                    "source": f"delta_stats:{path.name}",
                }
            )
            rows.append(
                {
                    "dataset": dataset,
                    "logical_key": member_key,
                    "is_base": "0",
                    "base_key": base_key,
                    "size": new_bytes,
                    "delta_ratio": ratio,
                    "shared_sf": "0",
                    "copied_chunks": "0",
                    "added_chunks": "0",
                    "source": f"delta_stats:{path.name}",
                }
            )
    return rows


def merge(all_rows: List[dict]) -> List[dict]:
    """按 (dataset, logical_key) 去重，后者覆盖前者。"""
    seen: dict = {}
    order: List[tuple] = []
    for r in all_rows:
        k = (r["dataset"], r["logical_key"])
        if k not in seen:
            order.append(k)
        seen[k] = r
    return [seen[k] for k in order]


def main() -> None:
    p = argparse.ArgumentParser(description="合并 Finesse / delta_stats manifest")
    p.add_argument("--finesse", action="append", default=[], help="FinesseRunner manifest，可多次指定")
    p.add_argument("--delta-stats", action="append", default=[], help="delta_encode_files 输出 CSV")
    p.add_argument("--dataset", default="kernel", help="delta-stats 行的 dataset 标签")
    p.add_argument("--out", type=Path, default=ROOT / "results" / "finesse_merged_manifest.csv")
    p.add_argument("--summary", type=Path, help="摘要 JSON（默认 <out>.summary.json）")
    args = p.parse_args()

    combined: List[dict] = []
    per_dataset_dr: dict = {}

    for fp in args.finesse:
        path = Path(fp)
        ds = path.stem.replace("finesse_", "").replace("_manifest", "")
        rows = load_finesse_manifest(path, ds)
        combined.extend(rows)
        vals = [float(r["delta_ratio"]) for r in rows if r["is_base"] == "0"]
        per_dataset_dr[ds] = dr_stats(vals).to_dict()

    for fp in args.delta_stats:
        path = Path(fp)
        rows = load_delta_stats(path, args.dataset)
        combined.extend(rows)
        vals = [float(r["delta_ratio"]) for r in rows if r["is_base"] == "0"]
        per_dataset_dr[args.dataset] = dr_stats(vals).to_dict()

    if not combined:
        raise SystemExit("未提供任何 manifest 输入")

    merged = merge(combined)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, lineterminator="\n")
        w.writeheader()
        for r in merged:
            w.writerow(r)

    all_dr = [float(r["delta_ratio"]) for r in merged if r["is_base"] == "0"]
    summary = {
        "output": str(args.out),
        "object_count": len(merged),
        "delta_member_count": len(all_dr),
        "per_dataset_dr": per_dataset_dr,
        "merged_dr": dr_stats(all_dr).to_dict(),
    }
    summary_path = args.summary or args.out.with_suffix(args.out.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[merge] 写入 {args.out} ({len(merged)} 对象)")
    print(f"[merge] 摘要 {summary_path}")
    if all_dr:
        d = summary["merged_dr"]
        print(f"  合并 D_r: n={d['count']} p50={d['p50']:.6f} mean={d['mean']:.6f}")


if __name__ == "__main__":
    main()
