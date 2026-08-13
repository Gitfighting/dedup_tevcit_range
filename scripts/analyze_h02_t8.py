#!/usr/bin/env python3
"""H02/t8: reconcile MILP ttlSec snapshot vs simulation REMAINING (typeA FP t11)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ["aws:us-east-1", "azure:westeurope", "gcp:us-west1-a"]

MILP_RE = re.compile(
    r"tick=(\d+) issue=([^\s]+) template=\S+ mode=\d+ status=\d+ obj=[\d.]+ ttlSec=\[([^\]]+)\]"
)
REMAINING_RE = re.compile(
    r"\[REMAINING\] Region=(\S+) Obj=(\S+) Size=([\d.]+) GB Duration=([\d.]+) Hrs Cost=\$([\d.]+)"
)


def parse_milp_ticks(log_path: Path) -> list[dict]:
    ticks: list[dict] = []
    if not log_path.exists():
        return ticks
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = MILP_RE.search(line)
        if not m:
            continue
        ttl_parts = [float(x.strip()) for x in m.group(3).split(",")]
        ttl_map = {REGIONS[i]: ttl_parts[i] for i in range(min(len(REGIONS), len(ttl_parts)))}
        ticks.append(
            {
                "tick": int(m.group(1)),
                "issue": m.group(2),
                "ttl_sec": ttl_map,
                "ttl_hrs": {r: s / 3600.0 for r, s in ttl_map.items()},
            }
        )
    return ticks


def parse_remaining(log_path: Path, obj_suffix: str | None = None) -> list[dict]:
    rows: list[dict] = []
    if not log_path.exists():
        return rows
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = REMAINING_RE.search(line)
        if not m:
            continue
        obj = m.group(2)
        if obj_suffix and obj_suffix not in obj:
            continue
        rows.append(
            {
                "region": m.group(1),
                "obj": obj,
                "size_gb": float(m.group(3)),
                "duration_hrs": float(m.group(4)),
                "cost": float(m.group(5)),
            }
        )
    return rows


def load_sim(tag: str) -> dict:
    matches = sorted(ROOT.glob(f"simulation_results_{tag}_*.json"), key=lambda p: p.stat().st_mtime)
    if not matches:
        return {}
    return json.loads(matches[-1].read_text(encoding="utf-8"))


def main() -> None:
    v4_log = ROOT / "logs" / "cost_calculation_v4_fp_tA_t11_20260614_154516.log"
    tev_log = ROOT / "logs" / "cost_calculation_tev_fp_tA_t11_20260614_155316.log"
    out = ROOT / "results" / "H02_t8_report.md"

    v4_milp = parse_milp_ticks(v4_log)
    v4_sim = load_sim("v4_fp_tA_t11")
    tev_sim = load_sim("tev_fp_tA_t11")

    hot = "97edc66052b9d223"
    v4_rem = parse_remaining(v4_log, hot)
    tev_rem = parse_remaining(tev_log, hot)

    lines: list[str] = [
        "# H02 / t8: MILP ttlSec vs REMAINING (typeA FP t11)",
        "",
        "## Simulation totals",
        "",
        "| tag | total ($) | storage ($) | transfer ($) |",
        "|-----|----------:|------------:|-------------:|",
    ]
    for tag, sim in [("v4_fp_tA_t11", v4_sim), ("tev_fp_tA_t11", tev_sim)]:
        if sim:
            lines.append(
                f"| {tag} | {sim.get('total cost ($)', '?'):.2f} | "
                f"{sim.get('total storage cost ($)', '?'):.2f} | "
                f"{sim.get('total transfer cost ($)', '?'):.2f} |"
            )

    gap_storage = float(v4_sim.get("total storage cost ($)", 0)) - float(
        tev_sim.get("total storage cost ($)", 0)
    )
    lines += [
        "",
        f"**Storage gap (V4 − tevict): ${gap_storage:.2f}**",
        "",
        "## Hot key REMAINING (`97edc660` @ gcp)",
        "",
        "| policy | duration (h) | storage cost ($) |",
        "|--------|-------------:|-----------------:|",
    ]
    if v4_rem:
        r = v4_rem[0]
        lines.append(f"| V4 | {r['duration_hrs']:.2f} | {r['cost']:.6f} |")
    if tev_rem:
        r = tev_rem[0]
        lines.append(f"| tevict | {r['duration_hrs']:.2f} | {r['cost']:.6f} |")
    if v4_rem and tev_rem:
        dh = v4_rem[0]["duration_hrs"] - tev_rem[0]["duration_hrs"]
        lines.append("")
        lines.append(f"**Duration delta: {dh:.2f} h** (= exactly one 12h pair-TTL bucket if 12.00)")

    lines += [
        "",
        "## V4 MILP ticks (from cost log)",
        "",
        "| tick | issue | ttlSec (aws, azure, gcp) | gcp self ttl (h) |",
        "|-----:|-------|--------------------------|----------------:|",
    ]
    for t in v4_milp:
        sec = t["ttl_sec"]
        gcp_h = t["ttl_hrs"].get("gcp:us-west1-a", 0.0)
        sec_str = ", ".join(f"{sec.get(r, 0):.0f}" for r in REGIONS)
        lines.append(f"| {t['tick']} | {t['issue']} | {sec_str} | {gcp_h:.2f} |")

    if v4_milp:
        last_gcp = [t for t in v4_milp if t["issue"] == "gcp:us-west1-a"][-1]
        ttl_sec = last_gcp["ttl_hrs"]["gcp:us-west1-a"] * 3600
        lines += [
            "",
            "## Interpretation",
            "",
            f"- Final gcp-issue MILP snapshot: **{last_gcp['ttl_hrs']['gcp:us-west1-a']:.2f} h** "
            f"({ttl_sec:.0f} sec) for pair TTL fed to coefficients.",
            "- REMAINING duration is **wall-clock storage** to trace end, not raw pair TTL.",
            f"- Hot-key gap **{v4_rem[0]['duration_hrs'] - tev_rem[0]['duration_hrs']:.2f} h** suggests the copy at gcp "
            "was retained ~1 bucket longer **or** placed ~12h earlier in V4.",
            "- Likely drivers to fix next:",
            "  1. **Copy-state divergence**: V4 read-cache full-replica upgrade adds more gcp copies vs tevict.",
            "  2. **region_list in calculateTtl**: extra remote copies change `min(getTevict)` selection.",
            "  3. **Histogram refresh count**: verify `regionPairsTtl` append count matches tevict per 12h bucket.",
            "",
            "## Recommended follow-up (t8b)",
            "",
            "- Trace-replay unit test: feed identical read stream to PolicyMilpECnewV3 vs PolicyTevictRangesNew;",
            "  assert `getTevict(src,dst,t)` equal at each 12h boundary.",
            "- Log per-object **STORAGE-ADD** at gcp for `97edc660` in both runs (enable verbose for that key).",
        ]

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(f"MILP ticks parsed: {len(v4_milp)}")
    if v4_rem and tev_rem:
        print(f"Hot key duration delta: {v4_rem[0]['duration_hrs'] - tev_rem[0]['duration_hrs']:.2f} h")


if __name__ == "__main__":
    main()
