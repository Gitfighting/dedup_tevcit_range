#!/usr/bin/env python3
"""
OCP local-stack launcher for ablation (does NOT edit skystore_cli.py).

Fixes:
  - sky-s3 path -> s3-proxy/target/release/sky-s3
  - PATH includes ~/.cargo/bin for s3s-fs
  - uses venv + requirements-ablation.txt
  - optional PUT_POLICY / TRACE via env

Usage (on server)::

  cd /data/ablation_exp/prototype
  python3 ablation/launch_local_stack.py --policy skystore
  python3 ablation/launch_local_stack.py --policy skystore --stop
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROTO = Path(__file__).resolve().parents[1]
SS = PROTO / "store-server"
SKY_S3 = PROTO / "s3-proxy" / "target" / "release" / "sky-s3"
CFG = PROTO / "ablation" / "configs" / "init_2reg.json"
LOG = Path(os.environ.get("ABLATION_LOG_DIR", "/data/ablation_exp/logs"))
PID_DIR = LOG / "pids"
CACHE = Path(os.environ.get("S3_LOCAL_CACHE", "/data/ablation_exp/s3-local-cache"))


def _env_base(policy: str, client_region: str, init_regions: str) -> dict:
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path = os.environ.get("PATH", "")
    if cargo_bin not in path.split(":"):
        path = cargo_bin + ":" + path
    stubs = str(PROTO / "ablation" / "vendor_stubs")
    py_path = os.pathsep.join(
        [
            stubs,
            str(PROTO),
            str(SS),
            os.environ.get("PYTHONPATH", ""),
        ]
    )
    # Derive AWS region from client tag for local S3 SDK (sky-s3 localhost path)
    aws_region = "us-east-1"
    if ":" in client_region:
        aws_region = client_region.split(":", 1)[1]
    env = {
        **os.environ,
        "PATH": path,
        "PYTHONPATH": py_path,
        "INIT_REGIONS": init_regions,
        "CLIENT_FROM_REGION": client_region,
        "PUT_POLICY": policy,
        "GET_POLICY": os.environ.get("GET_POLICY", "cheapest"),
        "LOCAL": "true",
        "LOCAL_SERVER": "true",
        "SERVER_ADDR": "localhost",
        "SKYSTORE_BUCKET_PREFIX": "ablation",
        "VERSION_ENABLE": os.environ.get("VERSION_ENABLE", "NULL"),
        "RUST_LOG": "INFO",
        "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
        "AWS_SECRET_ACCESS_KEY": os.environ.get(
            "AWS_SECRET_ACCESS_KEY", "minioadmin"
        ),
        "AWS_DEFAULT_REGION": aws_region,
        "AWS_REGION": aws_region,
    }
    if os.environ.get("TRACE_FILE_PATH"):
        env["TRACE_FILE_PATH"] = os.environ["TRACE_FILE_PATH"]
    return env


def stop_stack() -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    # Prefer pid files — avoid pkill -f matching this launcher shell.
    if PID_DIR.is_dir():
        for p in PID_DIR.glob("*.pid"):
            try:
                pid = int(p.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            try:
                p.unlink()
            except Exception:
                pass
    # Narrow process kills by binary / wrapper name
    subprocess.run("killall -q sky-s3 2>/dev/null || true", shell=True, check=False)
    subprocess.run("killall -q s3s-fs 2>/dev/null || true", shell=True, check=False)
    # Kill OCP uvicorn wrapper (process cmdline is python ..._run_uvicorn_ablation.py)
    subprocess.run(
        "pgrep -af '_run_uvicorn_ablation|_run_uvicorn_skystore' "
        "| awk '{print $1}' | xargs -r kill 2>/dev/null || true",
        shell=True,
        check=False,
    )
    time.sleep(2)
    print("stack stopped (best-effort)", flush=True)


def ensure_profiles() -> None:
    """OCP: ensure author transfer/placement CSVs exist (copy from skystore tree)."""
    dest = SS / "operations" / "policy" / "utils" / "profiles"
    dest.mkdir(parents=True, exist_ok=True)
    candidates = [
        Path("/data/skystore/prototype/store-server/operations/policy/utils/profiles"),
        Path("/data/dedup_exp/profiles"),
    ]
    needed = ["storage.csv", "cost.csv", "throughput.csv", "manual.csv"]
    for name in needed:
        target = dest / name
        if target.is_file():
            continue
        for src_dir in candidates:
            src = src_dir / name
            if src.is_file():
                import shutil

                shutil.copy2(src, target)
                print(f"copied profile {name} from {src}", flush=True)
                break
        if not target.is_file() and name == "manual.csv":
            target.write_text("src,dst,cost\n", encoding="utf-8")
            print(f"wrote stub {target}", flush=True)


def ensure_deps() -> None:
    py = os.environ.get("ABLATION_PYTHON", sys.executable)
    req = SS / "requirements.txt"
    req_ab = SS / "requirements-ablation.txt"
    # 1) OCP ablation deps first (hard required for app import + dedup)
    if req_ab.is_file():
        cmd_ab = f"{py} -m pip install -q -r {req_ab}"
        print(f"+ {cmd_ab}", flush=True)
        subprocess.run(cmd_ab, shell=True, check=True)
    # 2) Author requirements without source-build psycopg2 (use binary)
    pkgs = []
    if req.is_file():
        for line in req.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.split("==")[0].split(">=")[0].split("[")[0].strip() in (
                "psycopg2",
                "UltraDict",
            ):
                continue
            pkgs.append(s)
    pkgs.append("psycopg2-binary")
    # UltraDict optional
    cmd = f"{py} -m pip install -q " + " ".join(f"'{p}'" for p in pkgs)
    print("+ install author pkgs (psycopg2 -> binary)", flush=True)
    subprocess.run(cmd, shell=True, check=False)
    # UltraDict: do NOT install for local ablation.
    # Author db.py has a bug when UltraDict is present (thread may be undefined);
    # Phase-A smoke used the UltraDict-absent SQLite path successfully.
    print("+ skip UltraDict (force SQLite local path)", flush=True)
    # 3) verify critical imports
    subprocess.run(
        f"{py} -c \"from dotenv import load_dotenv; import blake3, lmdb; "
        f"import fastapi, uvicorn, sqlalchemy, sqlalchemy_repr; print('ablation_deps_ok')\"",
        shell=True,
        check=True,
    )


def start_stack(policy: str) -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    ensure_profiles()

    with open(CFG) as f:
        cfg = json.load(f)
    init_regions = ",".join(cfg["init_regions"])
    client = cfg["client_from_region"]
    env = _env_base(policy, client, init_regions)

    if not SKY_S3.is_file():
        raise FileNotFoundError(f"sky-s3 missing: {SKY_S3}")

    s3s = Path.home() / ".cargo" / "bin" / "s3s-fs"
    if not s3s.is_file():
        # fall back to PATH
        s3s = Path("s3s-fs")

    py = os.environ.get("ABLATION_PYTHON", sys.executable)

    # s3s-fs
    s3s_log = open(LOG / "s3s_fs.log", "w")
    s3s_cmd = [
        str(s3s) if s3s.name != "s3s-fs" or s3s.is_file() else "s3s-fs",
        "--host",
        "localhost",
        "--port",
        "8014",
        "--access-key",
        env["AWS_ACCESS_KEY_ID"],
        "--secret-key",
        env["AWS_SECRET_ACCESS_KEY"],
        "--domain",
        "localhost:8014",
        str(CACHE),
    ]
    # resolve s3s-fs binary
    if (Path.home() / ".cargo" / "bin" / "s3s-fs").is_file():
        s3s_cmd[0] = str(Path.home() / ".cargo" / "bin" / "s3s-fs")
    print("+", " ".join(s3s_cmd), flush=True)
    # start_new_session: survive SSH/orchestrator shell exit (no SIGHUP)
    p_s3s = subprocess.Popen(
        s3s_cmd, env=env, stdout=s3s_log, stderr=s3s_log, start_new_session=True
    )
    (PID_DIR / "s3s-fs.pid").write_text(str(p_s3s.pid))

    # store-server via OCP wrapper (skyplane stub + priceStorage patch)
    db = SS / "skystore.db"
    if db.exists():
        db.unlink()
    wrapper = PROTO / "ablation" / "_run_uvicorn_ablation.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROTO / 'ablation' / 'vendor_stubs')!r})\n"
        f"sys.path.insert(0, {str(PROTO)!r})\n"
        f"sys.path.insert(0, {str(SS)!r})\n"
        "import ablation.fix_price_storage  # noqa: F401\n"
        "import uvicorn\n"
        "uvicorn.run('app:app', host='127.0.0.1', port=3000, workers=1, log_level='info')\n"
    )
    ss_log = open(LOG / "store_server.log", "w")
    ss_cmd = [py, "-u", str(wrapper)]
    print("+", " ".join(ss_cmd), flush=True)
    p_ss = subprocess.Popen(
        ss_cmd,
        cwd=str(SS),
        env=env,
        stdout=ss_log,
        stderr=ss_log,
        start_new_session=True,
    )
    (PID_DIR / "store-server.pid").write_text(str(p_ss.pid))

    # Wait for store-server BEFORE starting sky-s3 (sky-s3 panics if /healthz down)
    import urllib.request

    ok = False
    for _ in range(90):
        if p_ss.poll() is not None:
            break
        try:
            with urllib.request.urlopen("http://127.0.0.1:3000/docs", timeout=2) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            time.sleep(2)
    if not ok:
        raise RuntimeError(
            "store-server failed before sky-s3 start; see " + str(LOG / "store_server.log")
        )

    # sky-s3
    sky_log = open(LOG / "sky_s3.log", "w")
    print("+", SKY_S3, flush=True)
    p_sky = subprocess.Popen(
        [str(SKY_S3)], env=env, stdout=sky_log, stderr=sky_log, start_new_session=True
    )
    (PID_DIR / "sky-s3.pid").write_text(str(p_sky.pid))
    # wait until :8002 accepts connections
    import socket

    ready = False
    for _ in range(60):
        if p_sky.poll() is not None:
            break
        try:
            s = socket.create_connection(("127.0.0.1", 8002), timeout=1)
            s.close()
            ready = True
            break
        except OSError:
            time.sleep(1)
    if not ready:
        raise RuntimeError("sky-s3 failed to listen on :8002; see " + str(LOG / "sky_s3.log"))
    time.sleep(1)

    print(
        json.dumps(
            {
                "policy": policy,
                "store_server_health": ok,
                "sky_s3": str(SKY_S3),
                "pids": {
                    "s3s-fs": p_s3s.pid,
                    "store-server": p_ss.pid,
                    "sky-s3": p_sky.pid,
                },
            },
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--policy",
        default="skystore",
        choices=[
            "always_evict",
            "always_store",
            "t_even",
            "skystore",
        ],
    )
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--skip-deps", action="store_true")
    args = ap.parse_args()

    if args.stop:
        stop_stack()
        return
    stop_stack()
    if not args.skip_deps:
        ensure_deps()
    start_stack(args.policy)


if __name__ == "__main__":
    main()
