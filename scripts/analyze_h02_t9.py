#!/usr/bin/env python3
"""Summarize t9 EC/replica boundary runs vs tevict baseline."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
TAGS = [
    ("tev_fp_tA_t8c", "tevict_ranges (baseline)"),
    ("v4_fp_tA_t8c", "V4 default (EC+replica)"),
    ("v4_fp_tA_t9_noec", "V4 replica-only (allow_ec=false)"),
    ("v4_fp_tA_t9_forceec", "V4 EC-only (force_ec_only=true)"),
]


def load_by_tag(tag: str) -> dict | None:
    files = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def main() -> None:
    lines = [
        "# H02 / t9: EC vs replica boundary (T79 typeA FP)",
        "",
        "Isolate whether the +$7 storage gap vs tevict comes from MILP choosing EC layout vs replica layout.",
        "",
        "| tag | policy | total ($) | storage ($) | transfer ($) | vs tevict total |",
        "|-----|--------|----------:|------------:|-------------:|----------------:|",
    ]
    tev = load_by_tag("tev_fp_tA_t8c")
    tev_total = float(tev["total cost ($)"]) if tev else None

    rows = []
    for tag, label in TAGS:
        data = load_by_tag(tag)
        if not data:
            rows.append((tag, label, None))
            continue
        total = float(data["total cost ($)"])
        storage = float(data["total storage cost ($)"])
        transfer = float(data["total transfer cost ($)"])
        delta = total - tev_total if tev_total is not None else None
        rows.append((tag, label, total, storage, transfer, delta))

    for row in rows:
        if row[2] is None:
            lines.append(f"| {row[0]} | {row[1]} | — | — | — | — |")
        else:
            _, label, total, storage, transfer, delta = row
            lines.append(
                f"| {row[0]} | {label} | {total:.2f} | {storage:.2f} | {transfer:.2f} | {delta:+.2f} |"
            )

    lines += [
        "",
        "## Interpretation",
        "",
        "- **replica-only** closest to tevict → gap driven by EC shard storage / multi-region layout.",
        "- **EC-only** vs default → quantifies replica-branch contribution.",
        "- If all variants stay ~+$6–7 vs tevict → gap is read-miss replica caching / TTL refresh, not EC vs replica MILP branch.",
    ]

    out = RESULTS / "H02_t9_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
