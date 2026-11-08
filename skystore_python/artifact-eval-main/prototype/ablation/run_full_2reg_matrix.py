#!/usr/bin/env python3
"""
Full ONLINE 2reg (typeE) matrix runner — executes ON ECS.

Tags (small → large): T079, T065, T015, T078, T029
Policies: always_evict, always_store, t_even, skystore

Usage on server:
  nohup /data/venv/bin/python ablation/run_full_2reg_matrix.py \
    > /data/ablation_exp/logs/matrix_2reg.log 2>&1 &
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROTO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTO))

TAGS = ["T065"]  # user: run T065 full first; other traces later
POLICIES = [
    "always_evict",
    "always_store",
    "t_even",
    "skystore",
]

BASE = Path("/data/ablation_exp")
LOG = BASE / "logs"
STATUS = BASE / "results" / "matrix_2reg_status.json"
PY = os.environ.get("ABLATION_PYTHON", "/data/venv/bin/python")

# Offline-measured logical footprints (typeE 2reg); used for disk admission.
EST_LOGICAL_GB = {
    "T065": 4.5,
    "T029": 9.7,
    "T078": 37.3,
    "T015": 402.5,
    "T079": 580.6,
}
MIN_FREE_GB = float(os.environ.get("MATRIX_MIN_FREE_GB", "20"))


def _free_gb(path: str = "/data") -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / (1024 ** 3)


def _admit(tag: str) -> bool:
    need = EST_LOGICAL_GB.get(tag, 0) * 1.15  # headroom for replicas/index
    free = _free_gb("/data")
    ok = free - need >= MIN_FREE_GB
    print(f"[disk] tag={tag} need~{need:.1f}G free={free:.1f}G admit={ok}", flush=True)
    return ok


def _purge_cache() -> None:
    import shutil

    cache = Path(os.environ.get("S3_LOCAL_CACHE", "/data/ablation_exp/s3-local-cache"))
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True, exist_ok=True)
    for p in (Path("/tmp/dedup_chunks"), Path("/tmp/dedup_index")):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)



def _load_status() -> dict:
    if STATUS.is_file():
        return json.loads(STATUS.read_text())
    return {"cells": {}, "started_at": datetime.now(timezone.utc).isoformat()}


def _save_status(st: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS.write_text(json.dumps(st, indent=2), encoding="utf-8")


def _run(cmd: list[str] | str, env: dict | None = None, timeout: int | None = None) -> int:
    print(f">> {cmd if isinstance(cmd, str) else ' '.join(cmd)}", flush=True)
    r = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        cwd=str(PROTO),
        env=env,
        timeout=timeout,
    )
    return r.returncode


def launch(policy: str, tag: str) -> None:
    env = os.environ.copy()
    env["ABLATION_PYTHON"] = PY
    env["PATH"] = f"{Path.home()}/.cargo/bin:" + env.get("PATH", "")
    env["AWS_ACCESS_KEY_ID"] = "minioadmin"
    env["AWS_SECRET_ACCESS_KEY"] = "minioadmin"
    env["AWS_DEFAULT_REGION"] = "us-east-1"
    env["AWS_REGION"] = "us-east-1"
    num = tag[1:]
    env["TRACE_FILE_PATH"] = (
        f"/data/skystore/traces/IBMObjectStoreTrace{num}Part0.typeE.mc-aug.csv"
    )
    # bridge
    _run(
        [
            PY, "-c",
            "import sys; sys.path.insert(0,'.'); from pathlib import Path; "
            "from ablation.trace_bridge import prepare_trace_bridge; "
            f"prepare_trace_bridge(Path('store-server'), Path('{env['TRACE_FILE_PATH']}'))",
        ],
        env=env,
    )
    rc = _run(
        [PY, "ablation/launch_local_stack.py", "--policy", policy, "--skip-deps"],
        env=env,
        timeout=900,
    )
    if rc != 0:
        raise RuntimeError(f"launch failed rc={rc}")


def run_cell(policy: str, tag: str) -> Path:
    env = os.environ.copy()
    env["PATH"] = f"{Path.home()}/.cargo/bin:" + env.get("PATH", "")
    env["AWS_ACCESS_KEY_ID"] = "minioadmin"
    env["AWS_SECRET_ACCESS_KEY"] = "minioadmin"
    env["AWS_DEFAULT_REGION"] = "us-east-1"
    env["AWS_REGION"] = "us-east-1"
    # full trace
    log = LOG / f"cell_{tag}_{policy}_full.log"
    with open(log, "w") as lf:
        p = subprocess.Popen(
            [
                PY, "ablation/run_online_cell.py",
                "--tag", tag, "--policy", policy, "--max-ops", "0",
            ],
            cwd=str(PROTO),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )
        rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"cell failed rc={rc} log={log}")
    return BASE / "results" / f"{tag}_typeE_{policy}.json"


def main() -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    only_tag = os.environ.get("MATRIX_ONLY_TAG", "")
    only_pol = os.environ.get("MATRIX_ONLY_POLICY", "")
    tags = [only_tag] if only_tag else TAGS
    policies = [only_pol] if only_pol else POLICIES

    st = _load_status()
    print(f"[matrix] start tags={tags} policies={policies}", flush=True)

    for tag in tags:
        if not _admit(tag):
            for policy in policies:
                key = f"{tag}:{policy}"
                st["cells"][key] = {
                    "status": "skipped_disk",
                    "reason": f"insufficient /data for ~{EST_LOGICAL_GB.get(tag)}GB",
                }
            _save_status(st)
            continue
        for policy in policies:
            key = f"{tag}:{policy}"
            cell = st["cells"].get(key, {})
            if cell.get("status") == "done":
                print(f"[matrix] SKIP done {key}", flush=True)
                continue
            st["cells"][key] = {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_status(st)
            t0 = time.time()
            try:
                _purge_cache()
                launch(policy, tag)
                out = run_cell(policy, tag)
                st["cells"][key] = {
                    "status": "done",
                    "result": str(out),
                    "wall_s": round(time.time() - t0, 1),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as ex:
                st["cells"][key] = {
                    "status": "error",
                    "error": str(ex),
                    "trace": traceback.format_exc()[-2000:],
                    "wall_s": round(time.time() - t0, 1),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                print(f"[matrix] ERROR {key}: {ex}", flush=True)
            _save_status(st)
            # stop stack between cells to free /tmp
            _run([PY, "ablation/launch_local_stack.py", "--stop"])

    print("[matrix] ALL DONE", flush=True)
    _save_status(st)


if __name__ == "__main__":
    main()
