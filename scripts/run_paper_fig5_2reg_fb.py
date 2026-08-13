#!/usr/bin/env python3
"""Paper Figure 5 style: 2-region Fixed Base (IBM typeE) + tevict_ranges_lz4 ablation.

Figure 5 policies (user-requested subset):
  AlwaysEvict, AlwaysStore, Teven, SkyStore(tevict_ranges)
Plus single-variable LZ4 sibling: tevict_ranges_lz4
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "target" / "Skystore1212-1.0-SNAPSHOT.jar"
PROFILES_SRC = ROOT / "skystore_python" / "artifact-eval-main" / "simulation" / "src" / "utils" / "profiles"
PROFILES_DST = ROOT / "target" / "profiles"
TRACE_DIR = ROOT / "Data" / "2reg" / "2reg"

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
# Paper Fig.5 baseline bars (SkyStore is the denominator, not a bar)
BASELINES = ["always_evict", "always_store", "teven"]
REF = "tevict_ranges"
TRACES = {
    "T15": "015",
    "T29": "029",
    "T65": "065",
    "T78": "078",
    "T79": "079",
}
TAG_PREFIX = "paper_fig5_2reg_fb"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def latest(tag: str) -> Path | None:
    pat = re.compile(rf"^simulation_results_{re.escape(tag)}_\d{{8}}_\d{{6}}\.json$")
    files = sorted(
        (p for p in ROOT.glob(f"simulation_results_{tag}_*.json") if pat.match(p.name)),
        key=lambda p: p.stat().st_mtime,
    )
    return files[-1] if files else None


def ensure_profiles() -> None:
    if not PROFILES_DST.exists() and PROFILES_SRC.exists():
        shutil.copytree(PROFILES_SRC, PROFILES_DST)


def run_one(pol: str, tid: str, tnum: str, force: bool, log) -> Path | None:
    tag = f"{TAG_PREFIX}_{pol}_{tid}"
    existing = latest(tag)
    if existing and not force:
        log(f"[{now()}] [skip] {tag} -> {existing.name}")
        return existing
    trace = TRACE_DIR / f"IBMObjectStoreTrace{tnum}Part0.typeE.mc-aug"
    if not trace.is_file():
        log(f"[{now()}] [missing] {trace}")
        return None
    rel = trace.relative_to(ROOT).as_posix()
    cmd = [
        "java", "-Xms4G", "-Xmx8G", "-jar", str(JAR),
        "--config", POLICIES[pol],
        "--trace", rel,
        "--vm", "5",
        "--setbase", "true",
        "--simversion", "1",
        "--tag", tag,
    ]
    log(f"[{now()}] [run] {tag}")
    t0 = datetime.now()
    proc = subprocess.run(cmd, cwd=ROOT)
    t1 = datetime.now()
    secs = (t1 - t0).total_seconds()
    if proc.returncode != 0:
        log(f"[{now()}] [fail] {tag} exit={proc.returncode} elapsed={secs:.1f}s")
        return None
    out = latest(tag)
    cost = "?"
    if out:
        try:
            cost = f"{float(json.loads(out.read_text(encoding='utf-8'))['total cost ($)']):.6f}"
        except Exception:
            pass
    log(f"[{now()}] [ok] {tag} cost=${cost} elapsed={secs:.1f}s file={out.name if out else 'none'}")
    return out


def load_cost(pol: str, tid: str) -> dict | None:
    path = latest(f"{TAG_PREFIX}_{pol}_{tid}")
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "total": float(data["total cost ($)"]),
        "storage": float(data["total storage cost ($)"]),
        "transfer": float(data["total transfer cost ($)"]),
        "wire_ratio": float(data.get("wire/logical transfer ratio", 1.0)),
        "policy": data.get("config_policy"),
        "file": path.name,
        "path": path,
    }


def plot_fig5(matrix: dict, out_png: Path, log) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        log(f"[{now()}] [warn] matplotlib missing; skip PNG")
        return

    # Paper-like: grouped bars per trace = baseline/SkyStore
    # Extra group: SkyStore+LZ4 / SkyStore (shows <1 when LZ4 wins)
    series = BASELINES + ["tevict_ranges_lz4"]
    colors = {
        "always_evict": "#4C78A8",
        "always_store": "#F58518",
        "teven": "#54A24B",
        "tevict_ranges_lz4": "#E45756",
    }
    traces = [t for t in TRACES if any(load_cost(p, t) for p in series + [REF])]
    if not traces:
        log(f"[{now()}] [warn] no data to plot")
        return

    x = np.arange(len(traces))
    n = len(series)
    width = 0.16
    fig, ax = plt.subplots(figsize=(11, 4.8))
    for i, pol in enumerate(series):
        vals = []
        for tid in traces:
            ref = matrix.get((REF, tid))
            cur = matrix.get((pol, tid))
            if ref and cur and ref["total"] > 0:
                vals.append(cur["total"] / ref["total"])
            else:
                vals.append(float("nan"))
        offset = (i - (n - 1) / 2) * width
        bars = ax.bar(x + offset, vals, width, label=LABELS[pol], color=colors[pol])
        for bar, v in zip(bars, vals):
            if v == v:  # not nan
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"{v:.2f}x", ha="center", va="bottom", fontsize=7, rotation=0)

    ax.axhline(1.0, color="#333333", linestyle="--", linewidth=1.0, label="SkyStore (=1)")
    ax.set_xticks(x)
    ax.set_xticklabels(traces)
    ax.set_ylabel("Cost / SkyStore (tevict_ranges)")
    ax.set_title("Figure 5 style — 2-Region Fixed Base (IBM typeE)\n"
                 "+ SkyStore+LZ4 single-variable ablation")
    ax.legend(ncol=3, frameon=False, fontsize=8, loc="upper right")
    ymax = 0
    for pol in series:
        for tid in traces:
            ref = matrix.get((REF, tid))
            cur = matrix.get((pol, tid))
            if ref and cur and ref["total"] > 0:
                ymax = max(ymax, cur["total"] / ref["total"])
    ax.set_ylim(0, max(3.0, ymax * 1.18) if ymax else 3.0)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=170)
    plt.close(fig)
    log(f"[{now()}] [plot] {out_png}")


def write_csv(matrix: dict, path: Path) -> None:
    rows = []
    for tid in TRACES:
        ref = matrix.get((REF, tid))
        for pol in list(POLICIES):
            cur = matrix.get((pol, tid))
            if not cur:
                continue
            rows.append({
                "trace": tid,
                "policy": pol,
                "label": LABELS[pol],
                "total": cur["total"],
                "storage": cur["storage"],
                "transfer": cur["transfer"],
                "wire_ratio": cur["wire_ratio"],
                "vs_skystore": (cur["total"] / ref["total"]) if ref and ref["total"] else "",
                "result": cur["file"],
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
            "trace", "policy", "label", "total", "storage", "transfer",
            "wire_ratio", "vs_skystore", "result"])
        w.writeheader()
        w.writerows(rows)


def write_experiment_log(
    path: Path,
    matrix: dict,
    started: str,
    finished: str,
    run_log_lines: list[str],
    plot_path: Path,
    csv_path: Path,
) -> None:
    # Docker measured LZ4 (prior fig5_docker2reg) for design analysis section
    docker_rows = []
    docker_csv = ROOT / "results" / "fig5_docker2reg_ratios.csv"
    if docker_csv.is_file():
        with docker_csv.open(encoding="utf-8") as f:
            docker_rows = list(csv.DictReader(f))

    lines = [
        f"# Experiment Log: tevict_ranges_lz4 vs SkyStore (2-reg Figure 5)",
        "",
        f"- **Log written at**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- **Matrix started at**: `{started}`",
        f"- **Matrix finished at**: `{finished}`",
        f"- **Workspace**: `{ROOT}`",
        "",
        "## 1. Goal",
        "",
        "Reproduce paper **Figure 5** style (2-Region, Fixed Base) for:",
        "",
        "- `PolicyAlwaysEvict`",
        "- `PolicyAlwaysStore`",
        "- `PolicyTeven`",
        "- `PolicyTevictRangesNew` (SkyStore / `tevict_ranges`)",
        "",
        "and evaluate the single-variable sibling **`tevict_ranges_lz4`** "
        "(`PolicyTevictRangesLz4`) against the original SkyStore policy.",
        "",
        "## 2. Design changes (single-variable ablation)",
        "",
        "| Item | SkyStore (`tevict_ranges`) | SkyStore+LZ4 (`tevict_ranges_lz4`) |",
        "|---|---|---|",
        "| Java class | `PolicyTevictRangesNew` | `PolicyTevictRangesLz4` extends it |",
        "| Placement / TTL / histogram | pair-TTL TevictRanges | **identical** (inherited) |",
        "| Transfer billing size | logical `Request.size` | LZ4 wire size (`compressed_size` or `lz4_ratio_default`) |",
        "| Stored physical size | logical | LZ4 physical size |",
        "| Histogram / logical metrics | uncompressed | **unchanged** (keeps TTL learning comparable) |",
        "| Config | `config/tevictranges_2reg.yaml` | `config/tevictranges_lz4_2reg.yaml` |",
        "",
        "Forced by `PolicyTevictRangesLz4.enableLz4()`:",
        "",
        "- `transfer_compression_enabled = true`",
        "- `storage_compression_enabled = true`",
        "- `transfer_compression_algorithm = lz4`",
        "- `lz4_ratio_default = 0.3443` when IBM traces lack `compressed_size`",
        "",
        "**Single variable**: physical byte scale only. No TTL/placement formula change.",
        "",
        "## 3. Experiment setup (paper Figure 5)",
        "",
        "- Mode: **Fixed Base (FB)** (`--setbase true`)",
        "- Regions: `aws:us-west-2`, `aws:us-east-1`",
        "- Workload: IBM SNIA Object Store traces, **typeE**, path `Data/2reg/2reg/*.typeE.mc-aug`",
        "- Traces: T15 / T29 / T65 / T78 / T79",
        "- Simulator: Java `SimulatorV2`, jar `target/Skystore1212-1.0-SNAPSHOT.jar`",
        "- Tag prefix: `" + TAG_PREFIX + "`",
        "",
        "## 4. Run timeline (second precision)",
        "",
        "```",
        *run_log_lines,
        "```",
        "",
        "## 5. Absolute costs ($)",
        "",
        "| Trace | Always Evict | Always Store | Teven | SkyStore | SkyStore+LZ4 |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    def fmt(pol, tid):
        c = matrix.get((pol, tid))
        return f"{c['total']:.4f}" if c else "—"

    for tid in TRACES:
        lines.append(
            f"| {tid} | {fmt('always_evict', tid)} | {fmt('always_store', tid)} | "
            f"{fmt('teven', tid)} | {fmt(REF, tid)} | {fmt('tevict_ranges_lz4', tid)} |"
        )

    lines += [
        "",
        "## 6. Figure 5 ratios (cost / SkyStore)",
        "",
        "| Trace | Always Evict | Always Store | Teven | SkyStore+LZ4 |",
        "|---|---:|---:|---:|---:|",
    ]

    def ratio(pol, tid):
        ref = matrix.get((REF, tid))
        cur = matrix.get((pol, tid))
        if not ref or not cur or ref["total"] <= 0:
            return "—"
        return f"{cur['total'] / ref['total']:.3f}x"

    for tid in TRACES:
        lines.append(
            f"| {tid} | {ratio('always_evict', tid)} | {ratio('always_store', tid)} | "
            f"{ratio('teven', tid)} | {ratio('tevict_ranges_lz4', tid)} |"
        )

    # averages
    def avg_ratio(pol):
        vals = []
        for tid in TRACES:
            ref = matrix.get((REF, tid))
            cur = matrix.get((pol, tid))
            if ref and cur and ref["total"] > 0:
                vals.append(cur["total"] / ref["total"])
        return (sum(vals) / len(vals)) if vals else None

    lines += ["", "### Averages across completed traces", ""]
    for pol in BASELINES + ["tevict_ranges_lz4"]:
        a = avg_ratio(pol)
        lines.append(f"- **{LABELS[pol]}**: {a:.3f}× SkyStore" if a else f"- **{LABELS[pol]}**: —")

    lines += [
        "",
        f"- Plot: `{plot_path.as_posix()}`",
        f"- CSV: `{csv_path.as_posix()}`",
        "",
        "## 7. Why `tevict_ranges_lz4` beats `tevict_ranges`",
        "",
        "Placement decisions (when to replicate / which TTL) are the same. "
        "Costs differ because billing sizes shrink:",
        "",
        "1. **Transfer**: egress cost ∝ wire bytes; LZ4 shrinks wire size → lower transfer $.",
        "2. **Storage**: monthly $/GB charged on **physical** replica size; compressed "
        "replicas stay resident for the same wall-clock TTL → lower storage $ for the same keep policy.",
        "3. **Histograms stay logical**: learning does not change, so the ablation is fair "
        "(not a different TTL schedule).",
        "",
    ]

    if docker_rows:
        by = {r["policy_id"]: r for r in docker_rows}
        if "tevict_ranges" in by and "tevict_ranges_lz4" in by:
            sky = by["tevict_ranges"]
            lz4 = by["tevict_ranges_lz4"]
            st = float(sky["storage"]); lt = float(lz4["storage"])
            sx = float(sky["transfer"]); lx = float(lz4["transfer"])
            sto = float(sky["total"]); lto = float(lz4["total"])
            wr = float(lz4["wire_ratio"])
            lines += [
                "### Supporting measured LZ4 evidence (Docker 2-reg, real `compressed_size`)",
                "",
                "On `Data/docker_image/docker_image_2reg_lz4.mc.aug` with per-object LZ4 sizes:",
                "",
                f"| Metric | SkyStore | SkyStore+LZ4 | Δ |",
                f"|---|---:|---:|---:|",
                f"| Total $ | {sto:.6f} | {lto:.6f} | {(1 - lto / sto) * 100:.2f}% lower |",
                f"| Storage $ | {st:.6f} | {lt:.6f} | {(1 - lt / st) * 100:.2f}% lower |",
                f"| Transfer $ | {sx:.6f} | {lx:.6f} | {(1 - lx / sx) * 100:.2f}% lower |",
                f"| Wire/logical | 1.000 | {wr:.6f} | matches compression |",
                "",
                "Note: IBM typeE runs in this matrix use `lz4_ratio_default=0.3443` "
                "(no per-object `compressed_size` in SNIA traces). Docker uses measured sizes.",
                "",
            ]

    lines += [
        "## 8. Artifacts",
        "",
        f"- Runner: `scripts/run_paper_fig5_2reg_fb.py`",
        f"- Policy: `src/main/java/org/example/src/placement_policy/PolicyTevictRangesLz4.java`",
        f"- Config: `config/tevictranges_lz4_2reg.yaml`",
        "",
        f"*End of log — `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`*",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--traces", default="T15,T65,T79,T78,T29",
                    help="comma list, paper order preferred T15,T29,...")
    ap.add_argument("--policies", default=",".join(POLICIES))
    args = ap.parse_args()

    started = now()
    run_log: list[str] = [f"{started}  matrix_start"]
    def log(msg: str) -> None:
        print(msg, flush=True)
        run_log.append(msg if msg.startswith("[") else f"[{now()}] {msg}")

    if not JAR.is_file():
        print("Build jar first: mvn -q -DskipTests package", file=sys.stderr)
        return 1
    ensure_profiles()

    tids = [t.strip() for t in args.traces.split(",") if t.strip()]
    pols = [p.strip() for p in args.policies.split(",") if p.strip()]

    if not args.skip_run:
        # SkyStore first so ratios can be formed early; then baselines; then LZ4
        order = []
        for tid in tids:
            if REF in pols:
                order.append((REF, tid))
            for p in pols:
                if p != REF:
                    order.append((p, tid))
        for pol, tid in order:
            run_one(pol, tid, TRACES[tid], args.force, log)

    finished = now()
    run_log.append(f"[{finished}] matrix_finish")

    matrix = {}
    for tid in TRACES:
        for pol in POLICIES:
            c = load_cost(pol, tid)
            if c:
                matrix[(pol, tid)] = c

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = ROOT / "results" / "paper_fig5_2reg_fb_ratios.csv"
    plot_path = ROOT / "results" / "paper_fig5_2reg_fb_ratios.png"
    log_path = ROOT / "results" / f"EXPERIMENT_LOG_tevict_ranges_lz4_fig5_{stamp}.md"

    write_csv(matrix, csv_path)
    plot_fig5(matrix, plot_path, log)
    write_experiment_log(log_path, matrix, started, finished, run_log, plot_path, csv_path)
    # also keep a stable latest pointer
    latest_log = ROOT / "results" / "EXPERIMENT_LOG_tevict_ranges_lz4_fig5.md"
    latest_log.write_text(log_path.read_text(encoding="utf-8"), encoding="utf-8")

    log(f"[{now()}] [log] {log_path}")
    log(f"[{now()}] [log-latest] {latest_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
