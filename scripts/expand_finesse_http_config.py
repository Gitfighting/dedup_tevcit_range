#!/usr/bin/env python3
"""生成/更新 config/finesse_http_datasets.json（HTTP Range 对象列表）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "finesse_http_datasets.json"

KERNEL_BASE = "https://cdn.kernel.org/pub/linux/kernel/v6.x"
# 默认 4 个连续版即可标定 D_r；16 个适合过夜批跑（HTTP 全文仍须过网，慢时不适合迭代）
KERNEL_VERSIONS = list(range(100, 104))  # 6.1.100 .. 6.1.103

# FSL macOS 2012：每周一个快照（论文 macOS 工作负载同类）
# macOS FSL 为变粒度论文工作负载，非 Finesse fast19 六数据集；默认 4 个作连通性抽检
MACOS_SNAPSHOTS = [
    "macos-2012-01-05-005505.tar.bz2",
    "macos-2012-01-12-012618.tar.bz2",
    "macos-2012-01-19-002730.tar.bz2",
    "macos-2012-01-26-015206.tar.bz2",
]
MACOS_BASE = "https://tracer.filesystems.org/traces/macos/2012"

SQLDUMP_OBJECTS = [
    ("thinkmobile_20200505.sql", "file:sqldump/thinkmobile_20200505.sql"),
    ("thinkmobile_20200730.sql", "file:sqldump/thinkmobile_20200730.sql"),
    ("thinkmobile_dev_20200722.sql", "file:sqldump/thinkmobile_dev_20200722.sql"),
    ("thinkmobile_dev_20200803.sql", "file:sqldump/thinkmobile_dev_20200803.sql"),
]


def main() -> None:
    cfg = {
        "datasets": {
            "kernel": {
                "description": (
                    f"Linux kernel 连续补丁版 {KERNEL_VERSIONS[0]}..{KERNEL_VERSIONS[-1]} "
                    "(cdn.kernel.org)。压缩 tar.xz；Range 在线读不落盘。"
                    "注意：压缩包上 r 偏大/难成簇，论文级 D_r 以 sqldump 未压缩快照为主。"
                ),
                "compressed": True,
                "objects": [
                    {
                        "name": f"linux-6.1.{v}.tar.xz",
                        "url": f"{KERNEL_BASE}/linux-6.1.{v}.tar.xz",
                    }
                    for v in KERNEL_VERSIONS
                ],
            },
            "sqldump": {
                "description": (
                    "SQL Dump 未压缩 .sql（本地 file:）。"
                    "有效 D_r 的核心来源；若有更多 mysqldump 可追加到 sqldump/ 或改为 https:// URL。"
                ),
                "compressed": False,
                "objects": [
                    {"name": name, "url": url} for name, url in SQLDUMP_OBJECTS
                ],
            },
            "macos": {
                "description": (
                    f"FSL macOS 2012 快照 {len(MACOS_SNAPSHOTS)} 个（每周）。"
                    "tar.bz2 为 fs-hasher 归档；Range 可读。"
                    "与论文原始 macOS 字节实验有差异，作多工作负载对照。"
                ),
                "compressed": True,
                "objects": [
                    {
                        "name": name,
                        "url": f"{MACOS_BASE}/{name}",
                    }
                    for name in MACOS_SNAPSHOTS
                ],
            },
        }
    }
    OUT.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[expand] {OUT}")
    print(
        f"  kernel={len(KERNEL_VERSIONS)}  sqldump={len(SQLDUMP_OBJECTS)}  "
        f"macos={len(MACOS_SNAPSHOTS)}"
    )


if __name__ == "__main__":
    main()
