#!/usr/bin/env python3
"""
对真实文件版本对做差分编码测量（对齐 Finesse / VCDiff 思路：只传 delta 时的字节数）。

输出：每条版本的 |delta|、delta/new 比例、压缩后大小等，写入 CSV 供 MILP/trace 绑定。

依赖（任选其一）：
  pip install zstandard
  或系统安装 zstd 命令行（https://github.com/facebook/zstd）

用法（仓库根目录）：
  python scripts/delta_encode_files.py --old a.bin --new b.bin
  python scripts/delta_encode_files.py --pair-dir D:/datasets/versions
  python scripts/delta_encode_files.py --manifest manifests/pairs.csv --out results/delta_stats.csv

manifest CSV 表头：
  logical_key,old_path,new_path
  （路径可为相对仓库根或绝对路径）
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class DeltaResult:
    logical_key: str
    old_path: str
    new_path: str
    old_bytes: int
    new_bytes: int
    delta_bytes: int
    delta_ratio_new: float  # delta_bytes / new_bytes
    method: str


def _read_file(path: Path) -> bytes:
    return path.read_bytes()


def _delta_zstd_lib(old: bytes, new: bytes) -> Tuple[int, str]:
    import zstandard as zstd

    # 以 old 为字典压缩 new（近似「有基版本时的 delta」）
    dict_data = zstd.ZstdCompressionDict(old, k=0)
    comp = zstd.ZstdCompressor(dict_data=dict_data, level=9)
    out = comp.compress(new)
    return len(out), "zstd_dict_old"


def _delta_zstd_cli(old_path: Path, new_path: Path) -> Tuple[int, str]:
    zstd = shutil.which("zstd")
    if not zstd:
        raise FileNotFoundError("zstd CLI not found")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        patch = td_path / "out.zst"
        # zstd --patch-from=old new -f patch
        cmd = [zstd, f"--patch-from={old_path}", str(new_path), "-f", str(patch), "-q"]
        subprocess.run(cmd, check=True, capture_output=True)
        return patch.stat().st_size, "zstd_patch_from_cli"


def _delta_bsdiff(old: bytes, bnew: bytes) -> Tuple[int, str]:
    try:
        import bsdiff4

        patch = bsdiff4.diff(old, bnew)
        return len(patch), "bsdiff4"
    except ImportError:
        raise FileNotFoundError("bsdiff4 not installed")


def measure_pair(logical_key: str, old_path: Path, new_path: Path) -> DeltaResult:
    if not old_path.is_file():
        raise FileNotFoundError(old_path)
    if not new_path.is_file():
        raise FileNotFoundError(new_path)
    old_b = _read_file(old_path)
    new_b = _read_file(new_path)
    old_n, new_n = len(old_b), len(new_b)
    if new_n == 0:
        return DeltaResult(
            logical_key, str(old_path), str(new_path), old_n, new_n, 0, 0.0, "empty_new"
        )

    delta_n = new_n
    method = "fallback_full_new"
    for fn in (
        lambda: _delta_zstd_cli(old_path, new_path),
        lambda: _delta_zstd_lib(old_b, new_b),
        lambda: _delta_bsdiff(old_b, new_b),
    ):
        try:
            delta_n, method = fn()
            break
        except Exception:
            continue

    ratio = delta_n / new_n if new_n else 0.0
    return DeltaResult(
        logical_key=logical_key,
        old_path=str(old_path),
        new_path=str(new_path),
        old_bytes=old_n,
        new_bytes=new_n,
        delta_bytes=delta_n,
        delta_ratio_new=ratio,
        method=method,
    )


def pairs_from_dir(pair_dir: Path) -> List[Tuple[str, Path, Path]]:
    files = sorted([p for p in pair_dir.iterdir() if p.is_file()])
    out: List[Tuple[str, Path, Path]] = []
    for i in range(1, len(files)):
        out.append((files[i].stem, files[i - 1], files[i]))
    return out


def load_manifest(path: Path) -> List[Tuple[str, Path, Path]]:
    rows: List[Tuple[str, Path, Path]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get("logical_key") or row.get("key") or ""
            old_p = Path(row["old_path"])
            new_p = Path(row["new_path"])
            if not old_p.is_absolute():
                old_p = ROOT / old_p
            if not new_p.is_absolute():
                new_p = ROOT / new_p
            rows.append((key or f"{old_p.name}->{new_p.name}", old_p, new_p))
    return rows


def write_csv(results: List[DeltaResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "logical_key",
                "old_path",
                "new_path",
                "old_bytes",
                "new_bytes",
                "delta_bytes",
                "delta_ratio_new",
                "method",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.logical_key,
                    r.old_path,
                    r.new_path,
                    r.old_bytes,
                    r.new_bytes,
                    r.delta_bytes,
                    f"{r.delta_ratio_new:.6f}",
                    r.method,
                ]
            )


def summarize(results: List[DeltaResult]) -> None:
    if not results:
        print("无结果。")
        return
    ratios = sorted(r.delta_ratio_new for r in results)
    n = len(ratios)
    med = ratios[n // 2]
    p90 = ratios[int(0.9 * (n - 1))]
    print(f"  对数: {n}")
    print(f"  delta/new 中位数: {med:.4f}")
    print(f"  delta/new p90:    {p90:.4f}")
    print(f"  方法: { {r.method for r in results} }")


def main() -> None:
    parser = argparse.ArgumentParser(description="真实文件版本对差分编码测量")
    parser.add_argument("--old", type=Path, help="旧版本文件")
    parser.add_argument("--new", type=Path, help="新版本文件")
    parser.add_argument("--pair-dir", type=Path, help="目录内按文件名排序相邻成对")
    parser.add_argument("--manifest", type=Path, help="CSV: logical_key,old_path,new_path")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results" / "delta_stats.csv",
        help="输出 CSV",
    )
    args = parser.parse_args()

    pairs: List[Tuple[str, Path, Path]] = []
    if args.old and args.new:
        pairs.append(("pair0", args.old, args.new))
    elif args.pair_dir:
        pairs = pairs_from_dir(args.pair_dir)
    elif args.manifest:
        pairs = load_manifest(args.manifest)
    else:
        parser.error("需要 --old/--new、--pair-dir 或 --manifest")

    print("差分编码测量（真实字节）")
    results: List[DeltaResult] = []
    for key, old_p, new_p in pairs:
        try:
            r = measure_pair(key, old_p, new_p)
            results.append(r)
            print(
                f"  {key}: new={r.new_bytes} delta={r.delta_bytes} "
                f"ratio={r.delta_ratio_new:.4f} ({r.method})"
            )
        except Exception as e:
            print(f"  {key}: FAILED {e}", file=sys.stderr)

    if len(results) > 1 or (args.manifest or args.pair_dir):
        print()
        summarize(results)
        write_csv(results, args.out)
        print(f"\n已写入: {args.out}")
    print("\n提示: 将 delta_ratio_new 用于 verify_milp_replica_vs_ec.py --delta-frac <中位数>")


if __name__ == "__main__":
    main()
