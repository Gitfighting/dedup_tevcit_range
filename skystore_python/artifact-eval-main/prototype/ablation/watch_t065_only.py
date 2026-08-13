#!/usr/bin/env python3
"""
Watchdog: when current T065 cell finishes, ensure remaining work is T065-only
(restart matrix with TAGS=[T065] if the in-memory process still has old TAGS).
Runs detached on ECS.
"""
import json
import os
import subprocess
import time
from pathlib import Path

STATUS = Path("/data/ablation_exp/results/matrix_2reg_status.json")
LOG = Path("/data/ablation_exp/logs/t065_watchdog.log")
PY = "/data/venv/bin/python"
PROTO = "/data/ablation_exp/prototype"


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def status() -> dict:
    if STATUS.is_file():
        return json.loads(STATUS.read_text())
    return {}


def matrix_alive() -> bool:
    r = subprocess.run(
        "pgrep -f run_full_2reg_matrix", shell=True, capture_output=True
    )
    return r.returncode == 0


def main() -> None:
    log("watchdog start")
    # wait until always_evict done
    while True:
        st = status().get("cells", {})
        ae = st.get("T065:always_evict", {})
        if ae.get("status") == "done":
            log(f"always_evict done wall={ae.get('wall_s')}")
            break
        if ae.get("status") == "error":
            log(f"always_evict ERROR {ae}")
            break
        if not matrix_alive():
            log("matrix died unexpectedly")
            break
        time.sleep(60)

    # After full T065×5, old in-memory TAGS may continue to T029 — kill & restart T065-only
    # Wait until either all T065 policies terminal, or matrix tries something else.
    want = [
        "always_evict",
        "always_store",
        "t_even",
        "skystore",
        "dedup_aware_tevict",
    ]
    while True:
        st = status().get("cells", {})
        t065 = {p: st.get(f"T065:{p}", {}).get("status") for p in want}
        log(f"T065 states {t065}")
        if all(s in ("done", "error") for s in t065.values()):
            log("all T065 policies finished — stop matrix to avoid other tags")
            subprocess.run("pkill -f run_online_cell || true", shell=True)
            subprocess.run("pkill -f run_full_2reg_matrix || true", shell=True)
            subprocess.run(
                f"cd {PROTO} && {PY} ablation/launch_local_stack.py --stop || true",
                shell=True,
            )
            break
        # if a non-T065 cell appears as running, kill and restart T065-only
        for k, v in st.items():
            if not k.startswith("T065:") and v.get("status") == "running":
                log(f"non-T065 running {k} — restart T065-only")
                subprocess.run("pkill -9 -f run_online_cell || true", shell=True)
                subprocess.run("pkill -9 -f run_full_2reg_matrix || true", shell=True)
                time.sleep(3)
                # ensure TAGS patched
                subprocess.run(
                    "setsid /data/ablation_exp/start_matrix_locked.sh </dev/null >/dev/null 2>&1 &",
                    shell=True,
                )
                break
        if not matrix_alive():
            # restart if T065 incomplete
            if not all(s in ("done", "error") for s in t065.values()):
                log("matrix dead with T065 incomplete — restart")
                subprocess.run(
                    "setsid /data/ablation_exp/start_matrix_locked.sh </dev/null >/dev/null 2>&1 &",
                    shell=True,
                )
            else:
                break
        time.sleep(90)
    log("watchdog exit")


if __name__ == "__main__":
    main()
