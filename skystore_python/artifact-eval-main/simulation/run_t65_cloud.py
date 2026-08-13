#!/usr/bin/env python3
"""Run Python simulation (artifact-eval-main/simulation) for T65 typeE 2reg FB."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
TRACE = ROOT / "traces" / "IBMObjectStoreTrace065Part0.typeE.mc-aug"
OUT_DIR = ROOT / "results_t65"
LOG = OUT_DIR / "run_t65.log"

# Paper-style 2reg Fixed Base policies
JOBS = [
    ("always_evict", "config/alwaysevict.yaml"),
    ("always_store", "config/alwaysstore.yaml"),
    ("teven", "config/teven.yaml"),
    ("tevict_ranges", "config/tevictranges_2reg.yaml"),
]


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    if not TRACE.is_file():
        log(f"MISSING trace {TRACE}")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []
    for name, cfg in JOBS:
        tag = f"python_sim_fb_T65_{name}"
        out_json = ROOT / f"simulation_results_{tag}.json"
        if out_json.exists():
            out_json.unlink()
        cmd = [
            PY, "-u", "main.py",
            "--config", cfg,
            "--trace", str(TRACE),
            "--vm", "5",
            "--setbase",
            "--simversion", "1",
            "--tag", tag,
        ]
        log(f"[run] {name} -> {' '.join(cmd)}")
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        elapsed = time.time() - t0
        (OUT_DIR / f"{name}.stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
        (OUT_DIR / f"{name}.stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
        if proc.returncode != 0:
            log(f"[fail] {name} rc={proc.returncode} elapsed={elapsed:.1f}s")
            log((proc.stderr or proc.stdout or "")[-1500:])
            summary.append({"policy": name, "ok": False, "elapsed_s": elapsed})
            continue
        cost = None
        if out_json.is_file():
            try:
                data = json.loads(out_json.read_text(encoding="utf-8"))
                row = data[-1] if isinstance(data, list) else data
                cost = row.get("total cost ($)")
                # copy into results_t65
                dest = OUT_DIR / out_json.name
                dest.write_text(out_json.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception as ex:
                log(f"[warn] parse {out_json}: {ex}")
        log(f"[ok] {name} cost=${cost} elapsed={elapsed:.1f}s file={out_json.name}")
        summary.append(
            {
                "policy": name,
                "ok": True,
                "elapsed_s": round(elapsed, 2),
                "total_cost": cost,
                "file": out_json.name,
            }
        )
    summary_path = OUT_DIR / "summary_T65.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"[done] summary -> {summary_path}")
    ok = all(x.get("ok") for x in summary) and len(summary) == len(JOBS)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
