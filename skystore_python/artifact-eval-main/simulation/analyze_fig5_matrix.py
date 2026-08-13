#!/usr/bin/env python3
"""Analyze matrix + Fig.5-style plots with dedup_aware as denominator."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_fig5_matrix"
DENOM = "dedup_aware_tevict_ranges"

# display order (paper-like + ours)
POLICY_ORDER = [
    "always_evict",
    "always_store",
    "teven",
    "tevict_ranges",
    "optimal",
    "dedup_aware_tevict_ranges",
]
# Paper Fig.5 puts baselines on X (denominator omitted). Ours is denominator.
BASELINE_ORDER = [
    "always_evict",
    "always_store",
    "teven",
    "tevict_ranges",
    "optimal",
]
LABELS = {
    "always_evict": "Always Evict",
    "always_store": "Always Store",
    "teven": "Teven",
    "tevict_ranges": "SkyStore",
    "optimal": "Optimal",
    "dedup_aware_tevict_ranges": "DedupAware\n(ours)",
}
# Paper legend order
TRACE_ORDER = ["T15", "T29", "T65", "T78", "T79"]
# Paper Fig.5: blue gradient light→dark for T15…T79
TRACE_COLORS = {
    "T15": "#BDD7EE",
    "T29": "#9DC3E6",
    "T65": "#5B9BD5",
    "T78": "#2E75B6",
    "T79": "#1F4E79",
}
COLORS = {
    "always_evict": "#4C78A8",
    "always_store": "#F58518",
    "teven": "#54A24B",
    "tevict_ranges": "#E45756",
    "optimal": "#B279A2",
    "dedup_aware_tevict_ranges": "#72B7B2",
}


def load_summary():
    raw = json.loads((OUT / "summary_all.json").read_text(encoding="utf-8"))
    cells = {}
    for r in raw:
        if not r.get("ok"):
            continue
        cost = r.get("cost") or {}
        if "total" not in cost:
            continue
        key = (r["setup"], r["tid"], r["policy"])
        cells[key] = {
            "total": float(cost["total"]),
            "storage": float(cost.get("storage") or 0),
            "transfer": float(cost.get("transfer") or 0),
            "request": float(cost.get("request") or 0),
            "wall_s": r.get("wall_s"),
            "file": r.get("file"),
        }
    return cells


def build_ratios(cells, setup: str):
    ratios = {p: [] for p in POLICY_ORDER}
    abs_costs = {p: [] for p in POLICY_ORDER}
    for tid in TRACE_ORDER:
        den = cells.get((setup, tid, DENOM))
        if not den or den["total"] <= 0:
            for p in POLICY_ORDER:
                ratios[p].append(np.nan)
                abs_costs[p].append(np.nan)
            continue
        for p in POLICY_ORDER:
            c = cells.get((setup, tid, p))
            if not c:
                ratios[p].append(np.nan)
                abs_costs[p].append(np.nan)
            else:
                ratios[p].append(c["total"] / den["total"])
                abs_costs[p].append(c["total"])
    return ratios, abs_costs


def _fmt_ratio_label(v: float) -> str:
    """Paper-style labels: 73x, 1.3x, 0.97x."""
    if v != v or v <= 0:
        return ""
    if v >= 10:
        return f"{v:.0f}x"
    if v >= 2:
        return f"{v:.0f}x" if abs(v - round(v)) < 0.05 else f"{v:.1f}x"
    if v >= 1:
        return f"{v:.1f}x"
    return f"{v:.2f}x"


def plot_fig5(setup: str, ratios: dict, out_png: Path):
    """
    Paper Fig.5 style:
      - X = baselines (denominator policy omitted)
      - grouped bars = IBM traces (blue gradient)
      - Y = cost / DedupAware, log2 scale
      - red ratio labels on bars
    """
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "DejaVu Serif", "Times", "serif"],
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 9,
        }
    )
    policies = [p for p in BASELINE_ORDER if p != DENOM]
    x = np.arange(len(policies))
    n = len(TRACE_ORDER)
    width = 0.82 / n
    fig, ax = plt.subplots(figsize=(10.5, 4.2))

    for i, tid in enumerate(TRACE_ORDER):
        vals = []
        for p in policies:
            # ratios[p] aligned with TRACE_ORDER
            idx = TRACE_ORDER.index(tid)
            v = ratios[p][idx]
            vals.append(v if v == v and v > 0 else np.nan)
        offsets = x + (i - n / 2) * width + width / 2
        bars = ax.bar(
            offsets,
            vals,
            width,
            label=tid,
            color=TRACE_COLORS[tid],
            edgecolor="black",
            linewidth=0.35,
            zorder=3,
        )
        for bar, v in zip(bars, vals):
            if v != v or v <= 0:
                continue
            # paper puts red labels above bars
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v * (1.08 if v >= 1 else 1.15),
                _fmt_ratio_label(v),
                ha="center",
                va="bottom",
                color="#C00000",
                fontsize=7.5,
                fontweight="bold",
                clip_on=False,
            )

    ax.set_yscale("log", base=2)
    ax.axhline(1.0, color="black", linestyle="-", linewidth=0.8, alpha=0.7, zorder=2)
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[p] for p in policies])
    ax.set_ylabel("Costs v.s. DedupAware")
    ax.set_xlabel("")
    # paper-like y ticks
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda y, _: f"{y:g}" if y >= 1 else f"{y:.2g}")
    )
    ax.grid(axis="y", which="major", linestyle=":", linewidth=0.5, alpha=0.6, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    # legend on top like paper (trace shades)
    leg = ax.legend(
        title="",
        ncol=len(TRACE_ORDER),
        loc="upper center",
        bbox_to_anchor=(0.5, 1.18),
        frameon=True,
        edgecolor="black",
        fancybox=False,
        borderpad=0.4,
        handlelength=1.2,
        columnspacing=1.0,
    )
    setup_name = "2-Region" if setup == "2reg" else "9-Region"
    fig.suptitle(
        f"Figure 5-style: {setup_name}, Fixed Base (FB): Ratio between baseline cost vs. DedupAware (ours).",
        y=1.02,
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    # restore default for other plots
    plt.rcParams.update(plt.rcParamsDefault)
    matplotlib.use("Agg")



def plot_components(cells, setup: str, out_png: Path):
    """Stacked storage vs transfer for each policy on each trace (absolute $)."""
    policies = POLICY_ORDER
    fig, axes = plt.subplots(1, len(TRACE_ORDER), figsize=(16, 4.5), sharey=True)
    for ax, tid in zip(axes, TRACE_ORDER):
        stor = []
        xfer = []
        labels = []
        for p in policies:
            c = cells.get((setup, tid, p))
            labels.append(LABELS[p].replace("\n", " "))
            if not c:
                stor.append(0)
                xfer.append(0)
            else:
                stor.append(c["storage"])
                xfer.append(c["transfer"])
        x = np.arange(len(policies))
        ax.bar(x, stor, label="storage", color="#4C78A8")
        ax.bar(x, xfer, bottom=stor, label="transfer", color="#F58518")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=75, ha="right", fontsize=7)
        ax.set_title(tid)
        if tid == TRACE_ORDER[0]:
            ax.set_ylabel("Cost ($)")
            ax.legend(fontsize=7)
    fig.suptitle(f"{setup}: storage vs transfer (absolute $)")
    fig.tight_layout()
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def write_analysis(cells: dict):
    lines = []
    lines.append("# Fig.5-style Matrix Analysis (DedupAware as denominator)\n\n")
    lines.append(
        "- Denominator: **dedup_aware_tevict_ranges** (ours)\n"
        "- Paper Fig.5 used SkyStore(tevict_ranges) as denominator; here ratios are "
        "`cost(policy) / cost(DedupAware)`.\n"
        "- Costs use **total without request** (paper ignores PUT/GET op fees).\n"
        "- 2reg: typeE Fixed-Base; 9reg: typeA Fixed-Base multi-cloud.\n\n"
    )

    for setup in ("2reg", "9reg"):
        lines.append(f"## {setup}\n\n")
        # absolute table
        lines.append("### Absolute cost ($ without request)\n\n")
        header = "| Trace | " + " | ".join(LABELS[p].replace("\n", " ") for p in POLICY_ORDER) + " |\n"
        lines.append(header)
        lines.append("|" + "---|" * (len(POLICY_ORDER) + 1) + "\n")
        for tid in TRACE_ORDER:
            row = [tid]
            for p in POLICY_ORDER:
                c = cells.get((setup, tid, p))
                row.append(f"{c['total']:.3f}" if c else "—")
            lines.append("| " + " | ".join(row) + " |\n")
        lines.append("\n")

        lines.append("### Ratio vs DedupAware (ours=1.0)\n\n")
        lines.append(header)
        lines.append("|" + "---|" * (len(POLICY_ORDER) + 1) + "\n")
        ratios, _ = build_ratios(cells, setup)
        for i, tid in enumerate(TRACE_ORDER):
            row = [tid]
            for p in POLICY_ORDER:
                v = ratios[p][i]
                row.append("—" if v != v else f"{v:.2f}×")
            lines.append("| " + " | ".join(row) + " |\n")
        lines.append("\n")

        def pct(part, tot):
            return 0.0 if tot <= 0 else 100.0 * part / tot

        lines.append("### Component split (storage % / transfer %) for every policy\n\n")
        lines.append(
            "| Trace | Policy | Total$ | Storage$ | Transfer$ | Stor% | Xfer% | vs Ours |\n"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|\n")
        for tid in TRACE_ORDER:
            ours = cells.get((setup, tid, DENOM))
            for p in POLICY_ORDER:
                c = cells.get((setup, tid, p))
                if not c:
                    continue
                ratio = "—" if not ours or ours["total"] <= 0 else f"{c['total']/ours['total']:.2f}×"
                lines.append(
                    f"| {tid} | {LABELS[p].replace(chr(10), ' ')} | {c['total']:.3f} | "
                    f"{c['storage']:.3f} | {c['transfer']:.3f} | "
                    f"{pct(c['storage'], c['total']):.1f}% | "
                    f"{pct(c['transfer'], c['total']):.1f}% | {ratio} |\n"
                )
        lines.append("\n")

        lines.append("### Delta vs DedupAware (where the $ goes)\n\n")
        lines.append(
            "| Trace | Policy | ΔTotal$ | ΔStorage$ | ΔTransfer$ | Driver |\n"
        )
        lines.append("|---|---|---:|---:|---:|---|\n")
        for tid in TRACE_ORDER:
            ours = cells.get((setup, tid, DENOM))
            if not ours:
                continue
            for p in POLICY_ORDER:
                if p == DENOM:
                    continue
                c = cells.get((setup, tid, p))
                if not c:
                    continue
                d_tot = c["total"] - ours["total"]
                d_s = c["storage"] - ours["storage"]
                d_x = c["transfer"] - ours["transfer"]
                if abs(d_s) >= abs(d_x):
                    driver = "storage-heavy gap" if d_s > 0 else "storage savings lost elsewhere"
                else:
                    driver = "transfer-heavy gap" if d_x > 0 else "transfer savings lost elsewhere"
                if d_tot < 0:
                    driver = "beats ours (" + driver + ")"
                lines.append(
                    f"| {tid} | {LABELS[p].replace(chr(10), ' ')} | {d_tot:+.3f} | "
                    f"{d_s:+.3f} | {d_x:+.3f} | {driver} |\n"
                )
        lines.append("\n")

        lines.append("### Why costs change (interpretation)\n\n")
        lines.append(
            "1. **Always Evict**: almost no cache → transfer dominates; ratio ≫1 on "
            "hot/read-heavy traces (T65/T78) because every remote GET pays full egress.\n"
            "2. **Always Store**: keeps replicas forever → storage dominates on long "
            "tails (T79); can beat SkyStore slightly on very hot traces but loses when "
            "objects go cold.\n"
            "3. **Teven**: fixed break-even TTL; safer than Always* but cannot adapt to "
            "burstiness → mid-range ratios.\n"
            "4. **SkyStore (tevict_ranges)**: adaptive TTL from inter-arrival histogram; "
            "usually best among author baselines; still stores full objects on cache hits.\n"
            "5. **Optimal**: clairvoyant T_next vs T_even; lower bound / oracle (not deployable).\n"
            "6. **DedupAware (ours)**: same adaptive TTL family, but HOT vs DEDUP using "
            "`τ=(1-L)×T_even`; DEDUP pays missing-chunk transfer and stores unique chunks + "
            "manifest → lower storage and often lower transfer when locality exists. "
            "Savings vs SkyStore ≈ reduction in full-object replicas / full egress.\n"
            "7. **Read the Δ table**: if ΔTransfer dominates, the gap is cross-region GET "
            "volume; if ΔStorage dominates, the gap is replica/chunk footprint over time.\n\n"
        )

    lines.append("## Figures\n\n")
    lines.append("- `fig5_2reg_ratios.png`, `fig5_9reg_ratios.png` — ratio bars\n")
    lines.append("- `fig5_2reg_components.png`, `fig5_9reg_components.png` — stor/xfer\n")
    (OUT / "ANALYSIS.md").write_text("".join(lines), encoding="utf-8")
    # also copy to experiment_logs
    dest = Path(r"D:\JavaProjects\Skystore1212\experiment_logs\fig5_dedup_aware_analysis_20260812.md")
    try:
        dest.write_text("".join(lines), encoding="utf-8")
    except Exception:
        pass


def main():
    cells = load_summary()
    for setup in ("2reg", "9reg"):
        ratios, _ = build_ratios(cells, setup)
        plot_fig5(setup, ratios, OUT / f"fig5_{setup}_ratios.png")
        plot_components(cells, setup, OUT / f"fig5_{setup}_components.png")
    write_analysis(cells)
    print("wrote plots + ANALYSIS.md under", OUT)


if __name__ == "__main__":
    main()
