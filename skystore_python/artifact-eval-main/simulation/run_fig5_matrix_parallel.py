#!/usr/bin/env python3
"""
Parallel Fig.5-style matrix.

Runs from simulation ROOT so get_full_path() works.
Each job gets its own config + trace symlink to avoid versioned-trace races.

Denominator for ratios: tevict_ranges (SkyStore).

Default placement mode is FB (`--setbase`). Pass `--fp` for free-placement;
FP result tags get a `_fp` suffix so they do not overwrite FB files.

Scheduling:
  - --workers targets ~85% of CPU (default)
  - --max-heavy limits concurrent T29 jobs (memory-heavy) to avoid OOM
  - 9reg supports workload types A/B/C/D via --types (B/C/D reserved until traces ready)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PY = os.environ.get("ABLATION_PYTHON", sys.executable)
OUT = ROOT / "results_fig5_matrix"
LOG = OUT / "matrix.log"
CFG_DIR = ROOT / "config" / "fig5_jobs"
TRACE_WORK = ROOT / "traces" / "fig5_work"

TAGS = {"T15": "015", "T29": "029", "T65": "065", "T78": "078", "T79": "079"}
HEAVY_TIDS = {"T29"}

# 2reg uses typeE; 9reg uses typeA/B/C/D
SETUP_REGIONS = {
    "2reg": ["aws:us-west-2", "aws:us-east-1"],
    "3reg": [
        "aws:us-east-1",
        "azure:westeurope",
        "gcp:us-west1-a",
    ],
    "6reg": [
        "aws:us-west-2",
        "azure:eastus",
        "azure:westeurope",
        "aws:us-east-1",
        "gcp:us-west1-a",
        "gcp:us-east1-b",
    ],
    "9reg": [
        "aws:us-east-1",
        "aws:us-west-1",
        "aws:eu-west-1",
        "azure:eastus",
        "azure:westus",
        "azure:westeurope",
        "gcp:us-east1-b",
        "gcp:us-west1-a",
        "gcp:europe-west1-b",
    ],
}

# SkyStore Fig.5 (2reg FB): Always Evict, Always Store, Teven, TTL-CC, EWMA, AWS Multi-Region.
# SkyStore Table 4 (3reg FB): same plus JuiceFS instead of AWS Multi-Region.
# tevict_ranges = object-level SkyStore (extra row/bar).
# dynamicttl = TTL-CC; ewma = EWMA (alpha=0.5); replicate_all = AWS-MR (2reg) / JuiceFS (3+reg).
# optimal (SkyStore Table 3 CGP) is off the default matrix; pass --policies ...optimal... to run it.
ALL_POLICIES = [
    "always_evict",
    "always_store",
    "teven",
    "dynamicttl",
    "ewma",
    "replicate_all",
    "tevict_ranges",
    "chunk_tevict",
]
FOLLOWUP_POLICIES = ["dynamicttl", "ewma", "replicate_all"]


def log(msg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def make_tag(
    setup: str, wtype: str, policy: str, tid: str, fixed_base: bool = True
) -> str:
    """Result tag. 2reg uses typeE; multi-reg typeA keeps legacy name (no type infix).

    FB tags stay unsuffixed so existing matrix files are reused. FP adds `_fp`.
    """
    if setup == "2reg":
        tag = f"fig5_2reg_{policy}_{tid}"
    elif wtype.upper() == "A" and setup in ("3reg", "6reg", "9reg"):
        tag = f"fig5_{setup}_{policy}_{tid}"
    else:
        tag = f"fig5_{setup}_type{wtype.upper()}_{policy}_{tid}"
    if not fixed_base:
        tag += "_fp"
    return tag


def find_trace(setup: str, num: str, wtype: str) -> Path | None:
    w = wtype.upper()
    if setup == "2reg":
        globs = [
            str(ROOT / "traces" / f"IBMObjectStoreTrace{num}Part0.typeE.mc-aug"),
            f"/data/sim_exp/python_simulation/traces/IBMObjectStoreTrace{num}Part0.typeE.mc-aug",
            f"/data/skystore/traces/IBMObjectStoreTrace{num}Part0.typeE.mc-aug.csv",
        ]
    else:
        prefix = {"3reg": "traces3", "6reg": "traces6", "9reg": "traces9"}[setup]
        globs = [
            str(ROOT / prefix / f"type{w}" / f"IBMObjectStoreTrace{num}Part0.type{w}.mc.aug"),
            f"/data/sim_exp/python_simulation/{prefix}/type{w}/IBMObjectStoreTrace{num}Part0.type{w}.mc.aug",
            str(ROOT / prefix / f"IBMObjectStoreTrace{num}Part0.type{w}.mc.aug"),
            f"/data/sim_exp/python_simulation/{prefix}/IBMObjectStoreTrace{num}Part0.type{w}.mc.aug",
        ]
    for pat in globs:
        p = Path(pat)
        if p.is_file() and p.stat().st_size > 1000:
            return p.resolve()
    return None


def extract_cost(path: Path) -> dict | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    row = data[-1] if isinstance(data, list) else data
    total = row.get("total cost without request ($)")
    if total is None:
        total = row.get("total cost ($)")
    return {
        "total": float(total),
        "storage": float(row.get("total storage cost ($)", 0) or 0),
        "transfer": float(row.get("total transfer cost ($)", 0) or 0),
        "request": float(row.get("total request cost ($)", 0) or 0),
        "raw": row,
    }


def cleanup_job_artifacts(tag: str, rel_trace: str) -> None:
    for p in (
        ROOT / f"simulation_results_{tag}.json",
        ROOT / f"{rel_trace}-versioned",
        ROOT / f"{rel_trace}-versioned.prototype",
        ROOT / f"{rel_trace}.prototype",
        TRACE_WORK / f"{tag}.mc-aug",
        TRACE_WORK / f"{tag}.mc-aug-versioned",
        TRACE_WORK / f"{tag}.mc-aug-versioned.prototype",
        CFG_DIR / f"{tag}.yaml",
    ):
        try:
            if p.is_symlink() or p.is_file():
                p.unlink()
        except OSError:
            pass


def run_one(job: dict) -> dict:
    setup, policy, tid, num = job["setup"], job["policy"], job["tid"], job["num"]
    wtype = job.get("wtype", "A" if setup == "9reg" else "E")
    fixed_base = bool(job.get("fixed_base", True))
    tag = make_tag(setup, wtype, policy, tid, fixed_base=fixed_base)
    t0 = time.time()
    result = {
        "setup": setup,
        "policy": policy,
        "tid": tid,
        "num": num,
        "wtype": wtype,
        "tag": tag,
        "fixed_base_region": fixed_base,
        "ok": False,
    }
    rel_trace = f"traces/fig5_work/{tag}.mc-aug"
    try:
        trace = find_trace(setup, num, wtype)
        if trace is None:
            result["error"] = f"missing trace {setup} type{wtype} {num}"
            return result
        result["trace"] = str(trace)
        out_json = OUT / f"simulation_results_{tag}.json"

        if policy == "chunk_tevict":
            sys.path.insert(0, str(ROOT))
            from src.dedup.aware_replay import run_chunk_tevict_replay
            from src.model.config import Config

            cfg = Config(
                placement_policy=policy,
                transfer_policy="cheapest",
                regions=list(SETUP_REGIONS[setup]),
                window_size=-1,
                fixed_base_region=fixed_base,
                chunk_store_dir=f"/data/dedup_exp/T{num}",
            )
            chunk_dir = Path(f"/data/dedup_exp/T{num}")
            if not (chunk_dir / "lens_u.npy").is_file():
                result["error"] = f"missing chunks {chunk_dir}"
                return result
            data = run_chunk_tevict_replay(trace, chunk_dir, cfg, max_ops=0)
            OUT.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps([data], indent=2), encoding="utf-8")
            result["ok"] = bool(data.get("ok"))
            result["cost"] = {
                "total": float(data["total cost without request ($)"]),
                "storage": float(data["total storage cost ($)"]),
                "transfer": float(data["total transfer cost ($)"]),
                "request": 0.0,
            }
            result["stats"] = data.get("stats")
            result["wall_s"] = round(time.time() - t0, 2)
            result["file"] = str(out_json)
            result["latency"] = data.get("latency")
            result["avg read latency (ms)"] = data.get("avg read latency (ms)")
            result["avg write latency (ms)"] = data.get("avg write latency (ms)")
            lat_r = (data.get("latency") or {}).get("read") or {}
            result["p90 read latency (ms)"] = lat_r.get("p90")
            result["p99 read latency (ms)"] = lat_r.get("p99")
            return result

        CFG_DIR.mkdir(parents=True, exist_ok=True)
        TRACE_WORK.mkdir(parents=True, exist_ok=True)

        regions = SETUP_REGIONS[setup]
        reg_yaml = "[" + ", ".join(f"'{r}'" for r in regions) + "]"
        place = {
            "always_evict": "always_evict",
            "always_store": "pull_on_read",
            "teven": "teven",
            "tevict_ranges": "tevict_ranges",
            "optimal": "optimal",
            "dynamicttl": "dynamicttl",
            "ewma": "ewma",
            "replicate_all": "replicate_all",
        }[policy]
        cfg_body = (
            f'placement_policy: "{place}"\n'
            f'transfer_policy: "cheapest"\n'
            f"regions: {reg_yaml}\n"
        )
        if place == "tevict_ranges":
            cfg_body += "window_size: -1\n"
        rel_cfg = f"config/fig5_jobs/{tag}.yaml"
        (ROOT / rel_cfg).write_text(cfg_body, encoding="utf-8")

        local_trace = ROOT / rel_trace
        if local_trace.exists() or local_trace.is_symlink():
            local_trace.unlink()
        os.symlink(trace, local_trace)
        for extra in (
            ROOT / f"{rel_trace}-versioned",
            ROOT / f"{rel_trace}-versioned.prototype",
            ROOT / f"simulation_results_{tag}.json",
        ):
            if extra.exists() or extra.is_symlink():
                try:
                    extra.unlink()
                except OSError:
                    pass

        cmd = [
            PY, "-u", "main.py",
            "--config", rel_cfg,
            "--trace", rel_trace,
            "--vm", "5",
            "--simversion", "1",
            "--tag", tag,
        ]
        if fixed_base:
            cmd.append("--setbase")
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=job.get("timeout", 7200)
        )
        produced = ROOT / f"simulation_results_{tag}.json"
        if proc.returncode != 0 or not produced.is_file():
            result["error"] = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-2000:]
            result["rc"] = proc.returncode
            result["wall_s"] = round(time.time() - t0, 2)
            return result

        OUT.mkdir(parents=True, exist_ok=True)
        shutil.copy(produced, out_json)
        cost = extract_cost(out_json)
        result["ok"] = True
        result["cost"] = {
            "total": float(cost["raw"].get("total cost without request ($)", cost["total"])),
            "storage": cost["storage"],
            "transfer": cost["transfer"],
            "request": cost["request"],
        }
        result["wall_s"] = round(time.time() - t0, 2)
        result["file"] = str(out_json)
        result["avg read latency (ms)"] = cost["raw"].get("avg read latency (ms)")
        result["avg write latency (ms)"] = cost["raw"].get("avg write latency (ms)")
        result["p90 read latency (ms)"] = cost["raw"].get("p90 read latency (ms)")
        result["p99 read latency (ms)"] = cost["raw"].get("p99 read latency (ms)")
        cleanup_job_artifacts(tag, rel_trace)
        return result
    except Exception:
        import traceback
        result["error"] = traceback.format_exc()[-2000:]
        result["wall_s"] = round(time.time() - t0, 2)
        return result


def build_jobs(
    setups, policies, tids, types_by_setup: dict[str, list[str]], fixed_base: bool = True
):
    jobs = []
    for setup in setups:
        wtypes = types_by_setup.get(setup, ["E"] if setup == "2reg" else ["A"])
        for wtype in wtypes:
            for policy in policies:
                for tid in tids:
                    jobs.append(
                        {
                            "setup": setup,
                            "wtype": wtype.upper(),
                            "policy": policy,
                            "tid": tid,
                            "num": TAGS[tid],
                            # T29 is long; T78 2reg ChunkTevict was ~2.0 h historically.
                            "timeout": 57600 if tid == "T29" else (14400 if tid == "T78" else 7200),
                            "heavy": tid in HEAVY_TIDS,
                            "fixed_base": fixed_base,
                        }
                    )
    jobs.sort(
        key=lambda j: (
            1 if j["heavy"] else 0,
            j["wtype"],
            j["tid"],
            j["setup"],
            j["policy"],
        )
    )
    return jobs


def default_workers() -> int:
    n = os.cpu_count() or 4
    # ~85% target: leave ~15% headroom (avoid saturating to 100%)
    return max(1, min(n - 1, int(round(n * 0.85))))


def run_pool(todo: list, workers: int, max_heavy: int, summary: list) -> None:
    pending = list(todo)
    inflight = {}
    heavy_n = 0

    def pick_next():
        nonlocal heavy_n
        if len(inflight) >= workers:
            return None
        for i, j in enumerate(pending):
            if j.get("heavy") and heavy_n >= max_heavy:
                continue
            return pending.pop(i)
        return None

    with ProcessPoolExecutor(max_workers=workers) as ex:
        while pending or inflight:
            while True:
                job = pick_next()
                if job is None:
                    break
                fut = ex.submit(run_one, job)
                inflight[fut] = job
                if job.get("heavy"):
                    heavy_n += 1
                log(
                    f"[start] {job['setup']} type{job['wtype']} {job['policy']} {job['tid']} "
                    f"inflight={len(inflight)} heavy={heavy_n}"
                )

            if not inflight:
                break

            done, _ = wait(inflight.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                job = inflight.pop(fut)
                if job.get("heavy"):
                    heavy_n = max(0, heavy_n - 1)
                r = fut.result()
                summary.append(r)
                wt = r.get("wtype", "?")
                if r.get("ok"):
                    log(
                        f"[ok] {r['setup']} type{wt} {r['policy']} {r['tid']} "
                        f"total=${r['cost']['total']:.4f} wall={r['wall_s']}s"
                    )
                else:
                    log(
                        f"[fail] {r['setup']} type{wt} {r['policy']} {r['tid']} "
                        f"{str(r.get('error', ''))[:400]}"
                    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=default_workers())
    ap.add_argument(
        "--max-heavy",
        type=int,
        default=2,
        help="Max concurrent T29 jobs (memory-heavy). Default 2 on 32GB.",
    )
    ap.add_argument("--setups", default="2reg,9reg")
    ap.add_argument("--policies", default=",".join(ALL_POLICIES))
    ap.add_argument("--traces", default="T15,T65,T29,T78,T79")
    # 9reg types: default A only; B/C/D reserved via --types A,B,C,D when ready
    ap.add_argument(
        "--types",
        default="",
        help="Workload types for 9reg, e.g. A or A,B,C,D. Empty => 2reg=E, 9reg=A.",
    )
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--fp",
        action="store_true",
        help="Free-placement (omit --setbase). Default is FB. FP result tags get _fp suffix.",
    )
    args = ap.parse_args()

    setups = [s.strip() for s in args.setups.split(",") if s.strip()]
    policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    tids = [t.strip() for t in args.traces.split(",") if t.strip()]
    type_list = [t.strip().upper() for t in args.types.split(",") if t.strip()]
    types_by_setup: dict[str, list[str]] = {}
    for s in setups:
        if s == "2reg":
            types_by_setup[s] = ["E"]
        else:
            types_by_setup[s] = type_list or ["A"]

    jobs = build_jobs(setups, policies, tids, types_by_setup, fixed_base=not args.fp)
    OUT.mkdir(parents=True, exist_ok=True)
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_WORK.mkdir(parents=True, exist_ok=True)
    mode = "FP" if args.fp else "FB"
    log(
        f"jobs={len(jobs)} workers={args.workers} max_heavy={args.max_heavy} "
        f"types={types_by_setup} mode={mode} (target ~85% CPU, T29 capped)"
    )

    todo = []
    summary = []
    for j in jobs:
        tag = make_tag(
            j["setup"], j["wtype"], j["policy"], j["tid"], fixed_base=j.get("fixed_base", True)
        )
        path = OUT / f"simulation_results_{tag}.json"
        if path.is_file() and not args.force:
            try:
                cost = extract_cost(path)
                if cost and cost["total"] == cost["total"]:
                    summary.append(
                        {
                            "setup": j["setup"],
                            "wtype": j["wtype"],
                            "policy": j["policy"],
                            "tid": j["tid"],
                            "ok": True,
                            "cost": {
                                "total": float(
                                    cost["raw"].get(
                                        "total cost without request ($)", cost["total"]
                                    )
                                ),
                                "storage": cost["storage"],
                                "transfer": cost["transfer"],
                                "request": cost["request"],
                            },
                            "skipped": True,
                            "file": str(path),
                        }
                    )
                    log(f"[skip] {tag}")
                    continue
            except Exception:
                pass
        todo.append(j)
    log(f"todo={len(todo)}")

    if todo:
        run_pool(todo, args.workers, args.max_heavy, summary)

    summary_path = OUT / "summary_all.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    ok_n = sum(1 for s in summary if s.get("ok"))
    log(f"[done] wrote {summary_path} ok={ok_n}/{len(summary)}")
    return 0 if ok_n == len(jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
