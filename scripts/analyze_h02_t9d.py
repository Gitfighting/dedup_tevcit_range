#!/usr/bin/env python3
"""Diff hot-key REMAINING-DIAG / TTL-WATCH lines from paired V4 vs tevict watch runs."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOT = "97edc66052b9d223"
LOG_DIR = ROOT / "logs"

DIAG_RE = re.compile(
    r"\[REMAINING-DIAG\] Region=(\S+) Obj=\S+ copies=(\d+) physTtl=(-?\d+) path=(\S+) wallH=([\d.]+) billedH=([\d.]+)"
)
TTL_RE = re.compile(
    r"\[TTL-WATCH\] path=(\S+) ts=(\S+) op=(\S+) issue=(\S+) dst=(\S+) key=\S+ region_list=(\[.*?\]) read_r=(\S+) set_ttl=([\d.]+) applied_ttl=(-?\d+)"
)
REM_RE = re.compile(
    r"\[REMAINING\] Region=(\S+) Obj=\S+ Size=([\d.]+) GB Duration=([\d.]+) Hrs Cost=\$([\d.]+)"
)


def load_lines(tag: str) -> list[str]:
    files = sorted(LOG_DIR.glob(f"cost_calculation*{tag}*.log"), reverse=True)
    if not files:
        return []
    return files[0].read_text(encoding="utf-8", errors="replace").splitlines()


def extract(tag: str) -> dict:
    lines = [ln for ln in load_lines(tag) if HOT in ln]
    diags = [DIAG_RE.search(ln) for ln in lines if "[REMAINING-DIAG]" in ln]
    diags = [m.groups() for m in diags if m]
    rems = [REM_RE.search(ln) for ln in lines if "[REMAINING]" in ln and "DIAG" not in ln]
    rems = [m.groups() for m in rems if m]
    ttls = [TTL_RE.search(ln) for ln in lines if "[TTL-WATCH]" in ln]
    ttls = [m.groups() for m in ttls if m]
    return {"diags": diags, "rems": rems, "ttls": ttls, "raw_count": len(lines)}


def main() -> None:
    v4 = extract("hotkey_v4_t9d")
    tev = extract("hotkey_tev_t9d")
    out = ROOT / "results" / "H02_t9d_report.md"
    lines = [
        "# H02 / t9d: Hot-key lifecycle diff (`97edc660`)",
        "",
        "Compare `[REMAINING-DIAG]` (copy count / ttl-cap path) and `[TTL-WATCH]` between V4 and tevict.",
        "",
        "## Log coverage",
        "",
        f"| run | hot-key lines | TTL-WATCH | REMAINING-DIAG |",
        f"|-----|--------------:|----------:|---------------:|",
        f"| V4 | {v4['raw_count']} | {len(v4['ttls'])} | {len(v4['diags'])} |",
        f"| tevict | {tev['raw_count']} | {len(tev['ttls'])} | {len(tev['diags'])} |",
        "",
    ]
    for label, data in [("V4", v4), ("tevict", tev)]:
        lines.append(f"## {label} REMAINING @ gcp")
        lines.append("")
        gcp = [d for d in data["diags"] if d[0].startswith("gcp")]
        if not gcp:
            lines.append("_No REMAINING-DIAG (run `hotkey_*_t9d` with `-Dskystore.watchObjPrefix=97edc660`)_")
        else:
            for region, copies, ttl, path, wall, billed in gcp:
                lines.append(
                    f"- `{region}` copies=**{copies}** physTtl=**{ttl}** path=**{path}** "
                    f"wall={wall}h billed={billed}h"
                )
        lines.append("")

    if v4["ttls"] or tev["ttls"]:
        lines += ["## First TTL-WATCH divergence", ""]
        n = min(len(v4["ttls"]), len(tev["ttls"]), 20)
        for i in range(n):
            a, b = v4["ttls"][i], tev["ttls"][i]
            mark = " **DIFF**" if a != b else ""
            lines.append(f"{i+1}. V4: `{a[0]}` issue={a[3]} read_r={a[6]} ttl={a[8]}{mark}")
            lines.append(f"   tev: `{b[0]}` issue={b[3]} read_r={b[6]} ttl={b[8]}")
        lines.append("")

    lines += [
        "## Hypothesis",
        "",
        "If tevict shows `path=ttl-cap` with `copies>1` while V4 shows `path=full`, the +12h gcp gap is",
        "**REMAINING accounting** (multi-copy ttl cap), not histogram TTL formula drift.",
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
