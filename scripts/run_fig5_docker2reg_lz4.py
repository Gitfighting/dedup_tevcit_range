#!/usr/bin/env python3
"""2-reg Docker Figure-5-style ratios: baselines vs SkyStore vs SkyStore+LZ4.

Single-variable ablation: PolicyTevictRangesLz4 shares placement/TTL/hist with
tevict_ranges; only transfer+storage physical sizes are LZ4-scaled.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "target" / "Skystore1212-1.0-SNAPSHOT.jar"
TRACE = "Data/docker_image/docker_image_2reg_lz4.mc.aug"

# Paper Fig.5-style policies + LZ4 ablation sibling of SkyStore.
POLICIES = {
    "always_evict": "config/alwaysevict_2reg.yaml",
    "always_store": "config/alwaysstore_2reg.yaml",
    "teven": "config/teven_2reg.yaml",
    "tevict_ranges": "config/tevictranges_2reg.yaml",
    "tevict_ranges_lz4": "config/tevictranges_lz4_2reg.yaml",
}
LABELS = {
    "always_evict": "Always Evict",
    "always_store": "Always Store",
    "teven": "Teven",
    "tevict_ranges": "SkyStore",
    "tevict_ranges_lz4": "SkyStore+LZ4",
}
REF = "tevict_ranges"
TAG_PREFIX = "fig5_docker2reg"


def latest(tag: str) -> Path | None:
    pattern = re.compile(rf"^simulation_results_{re.escape(tag)}_\d{{8}}_\d{{6}}\.json$")
    files = sorted(
        (p for p in ROOT.glob(f"simulation_results_{tag}_*.json") if pattern.match(p.name)),
        key=lambda p: p.stat().st_mtime,
    )
    return files[-1] if files else None


def run_one(name: str, config: str, force: bool) -> Path:
    tag = f"{TAG_PREFIX}_{name}"
    existing = latest(tag)
    if existing and not force:
        print(f"[skip] {tag} -> {existing.name}")
        return existing
    cmd = [
        "java", "-Xms2G", "-Xmx6G", "-jar", str(JAR),
        "--config", config, "--trace", TRACE, "--vm", "5",
        "--setbase", "true", "--simversion", "1", "--tag", tag,
    ]
    print(f"[run] {tag}")
    subprocess.run(cmd, cwd=ROOT, check=True)
    out = latest(tag)
    if out is None:
        raise SystemExit(f"missing result for {tag}")
    return out


def load_cost(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "total": float(data["total cost ($)"]),
        "storage": float(data["total storage cost ($)"]),
        "transfer": float(data["total transfer cost ($)"]),
        "wire_ratio": float(data.get("wire/logical transfer ratio", 1.0)),
        "policy": data.get("config_policy"),
        "result": path.name,
    }


def plot_ratios(rows: list[dict], out_png: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[warn] matplotlib missing; skip PNG")
        return

    order = ["always_evict", "always_store", "teven", "tevict_ranges", "tevict_ranges_lz4"]
    by_name = {r["policy_id"]: r for r in rows}
    labels = [LABELS[p] for p in order if p in by_name]
    ratios = [by_name[p]["vs_skystore"] for p in order if p in by_name]
    colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2", "#E45756"]

    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    bars = ax.bar(labels, ratios, color=colors[: len(labels)], width=0.65)
    ax.axhline(1.0, color="#333333", linestyle="--", linewidth=1.0, label="SkyStore (=1)")
    ax.set_ylabel("Total cost / SkyStore")
    ax.set_title("2-reg Docker: Figure-5-style cost ratios (+ LZ4 ablation)")
    ax.set_ylim(0, max(ratios) * 1.15 if ratios else 1)
    for bar, val in zip(bars, ratios):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{val:.2f}x", ha="center", va="bottom", fontsize=9)
    ax.legend(loc="upper right", frameon=False)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=160)
    plt.close(fig)
    print(f"Wrote plot: {out_png}")


def write_analysis(rows: list[dict], path: Path) -> None:
    by = {r["policy_id"]: r for r in rows}
    sky = by["tevict_ranges"]
    lz4 = by["tevict_ranges_lz4"]
    lines = [
        "# 2-reg Docker: SkyStore vs SkyStore+LZ4 (single-variable)",
        "",
        "## Setup",
        "- Placement policy family: identical TevictRanges TTL / histogram / store-or-evict.",
        "- Only difference for `tevict_ranges_lz4`: physical transfer + stored bytes scaled by LZ4.",
        "- Logical sizes unchanged (histograms / request-size metrics stay comparable).",
        f"- Measured mean wire/logical ratio on this trace: **{lz4['wire_ratio']:.4f}**.",
        "",
        "## Costs ($)",
        "",
        "| Policy | Total | Storage | Transfer | vs SkyStore |",
        "|---|---:|---:|---:|---:|",
    ]
    for p in ["always_evict", "always_store", "teven", "tevict_ranges", "tevict_ranges_lz4"]:
        r = by[p]
        lines.append(
            f"| {LABELS[p]} | {r['total']:.4f} | {r['storage']:.4f} | "
            f"{r['transfer']:.4f} | {r['vs_skystore']:.2f}x |"
        )
    stor_drop = 1.0 - lz4["storage"] / sky["storage"] if sky["storage"] else 0
    xfer_drop = 1.0 - lz4["transfer"] / sky["transfer"] if sky["transfer"] else 0
    tot_drop = 1.0 - lz4["total"] / sky["total"] if sky["total"] else 0
    lines += [
        "",
        "## Why LZ4 beats SkyStore",
        "",
        f"- Same placement decisions → same when/where to keep replicas.",
        f"- Storage GB billed drops ≈ **{stor_drop:.1%}** "
        f"(${sky['storage']:.4f} → ${lz4['storage']:.4f}).",
        f"- Transfer GB billed drops ≈ **{xfer_drop:.1%}** "
        f"(${sky['transfer']:.4f} → ${lz4['transfer']:.4f}), matching wire ratio ~{lz4['wire_ratio']:.3f}.",
        f"- Total cost drops ≈ **{tot_drop:.1%}** "
        f"(${sky['total']:.4f} → ${lz4['total']:.4f}).",
        "- Dominated by storage on this workload; compressing replicas amplifies SkyStore's keep benefit.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote analysis: {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-run", action="store_true", help="only aggregate existing tags")
    args = ap.parse_args()

    if not args.skip_run:
        if not JAR.is_file():
            raise SystemExit("Build jar first: mvn -q -DskipTests package")
        for name, cfg in POLICIES.items():
            run_one(name, cfg, args.force)

    rows = []
    ref_total = None
    loaded = {}
    for name in POLICIES:
        path = latest(f"{TAG_PREFIX}_{name}")
        if path is None:
            # fallback to earlier docker_lz4_2reg_* tags
            path = latest(f"docker_lz4_2reg_{name}")
        if path is None:
            print(f"[missing] {name}")
            continue
        costs = load_cost(path)
        loaded[name] = costs
        if name == REF:
            ref_total = costs["total"]

    if ref_total is None or ref_total <= 0:
        raise SystemExit("missing SkyStore (tevict_ranges) result")

    for name, costs in loaded.items():
        rows.append({
            "policy_id": name,
            "label": LABELS[name],
            "total": costs["total"],
            "storage": costs["storage"],
            "transfer": costs["transfer"],
            "wire_ratio": costs["wire_ratio"],
            "vs_skystore": costs["total"] / ref_total,
            "config_policy": costs["policy"],
            "result": costs["result"],
        })

    out_csv = ROOT / "results" / "fig5_docker2reg_ratios.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {out_csv}")

    plot_ratios(rows, ROOT / "results" / "fig5_docker2reg_ratios.png")
    write_analysis(rows, ROOT / "results" / "fig5_docker2reg_ANALYSIS.md")

    print("\n=== cost / SkyStore ===")
    for r in sorted(rows, key=lambda x: -x["vs_skystore"]):
        print(f"{r['label']:16s} {r['vs_skystore']:.3f}x  total=${r['total']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
