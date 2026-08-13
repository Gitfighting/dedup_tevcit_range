#!/usr/bin/env python3
"""
Online full-stack cell runner (runs ON the ECS host).

Pipeline:
  CreateBucket(logical) → replay .mc-aug with REAL bodies
  → POST /clean_out_remaining → save JSON under /data/ablation_exp/results

Assumes launch_local_stack.py already started store-server/sky-s3/s3s-fs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROTO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTO))

from ablation.body_materialize import load_manifest, resolve_manifest  # noqa: E402
from ablation.replay_mc_aug import replay  # noqa: E402

STORE = os.environ.get("STORE_URL", "http://127.0.0.1:3000")
ENDPOINT = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:8002")
RESULTS = Path("/data/ablation_exp/results")
BODIES = Path("/data/ablation_exp/bodies")


def http_json(method: str, url: str, body: dict | None = None, timeout: int = 120):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode().strip()
        if not raw:
            return {"ok": True, "status": getattr(resp, "status", 200)}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": True, "raw": raw[:500]}


def wait_store(timeout: int = 90) -> None:
    t0 = time.time()
    last_err = ""
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"{STORE}/docs", timeout=3)
            print(f"[cell] store ready ({STORE})", flush=True)
            return
        except Exception as ex:
            last_err = str(ex)
            time.sleep(1)
    raise RuntimeError(f"store-server not ready ({STORE}): {last_err}")


def wait_sky_s3(timeout: int = 60) -> None:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        try:
            import socket

            s = socket.create_connection(("127.0.0.1", 8002), timeout=2)
            s.close()
            print("[cell] sky-s3 :8002 listening", flush=True)
            return
        except Exception as ex:
            last = str(ex)
            time.sleep(1)
    raise RuntimeError(f"sky-s3 not listening: {last}")


def ensure_logical_bucket(logical: str = "default-skybucket") -> None:
    """
    Author path: ONE CreateBucket for the logical name.
    Do NOT also register_buckets with the same physical names —
    that UNIQUE-conflicts and sky-s3 unwrap()-panics.
    """
    env = os.environ.copy()
    env.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
    env.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")
    env.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    last = ""
    for attempt in range(1, 8):
        r = subprocess.run(
            [
                "aws", "s3", "mb", f"s3://{logical}",
                "--endpoint-url", ENDPOINT, "--no-verify-ssl",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        err = (r.stderr or "") + (r.stdout or "")
        last = err
        print(
            f"[cell] CreateBucket {logical} try={attempt}: rc={r.returncode} {err[:240]}",
            flush=True,
        )
        if r.returncode == 0 or "already" in err.lower() or "BucketAlready" in err:
            return
        time.sleep(2 * attempt)
    raise RuntimeError(f"CreateBucket failed: {last[:500]}")


def clean_out(timestamp: str | None = None) -> dict:
    # ISO timestamp required by store-server schemas
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return http_json(
        "POST",
        f"{STORE}/clean_out_remaining",
        {"timestamp": ts},
        timeout=600,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="T065")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--max-ops", type=int, default=0, help="0=full trace")
    ap.add_argument("--bucket", default="default-skybucket")
    ap.add_argument("--skip-register", action="store_true")
    args = ap.parse_args()

    tag = args.tag.upper()
    num = tag[1:] if tag.startswith("T") else tag
    trace = Path(
        f"/data/skystore/traces/IBMObjectStoreTrace{num}Part0.typeE.mc-aug.csv"
    )
    if not trace.is_file():
        raise SystemExit(f"missing trace {trace}")

    man = resolve_manifest(tag)
    recipes = load_manifest(man)
    body_dir = BODIES / tag
    RESULTS.mkdir(parents=True, exist_ok=True)

    wait_store()
    wait_sky_s3()
    if not args.skip_register:
        ensure_logical_bucket(args.bucket)

    print(
        f"[cell] ONLINE replay tag={tag} policy={args.policy} "
        f"max_ops={args.max_ops} recipes={len(recipes)}",
        flush=True,
    )
    from pathlib import Path as P

    stats = replay(
        trace,
        ENDPOINT,
        args.bucket,
        body_dir,
        recipes,
        P("/data/file_pool"),
        max_ops=args.max_ops,
        use_boto3=True,
    )
    print(f"[cell] replay done {stats}", flush=True)

    cost = clean_out()
    out = {
        "mode": "online_filesystem",
        "tag": tag,
        "policy": args.policy,
        "trace": str(trace),
        "manifest": str(man),
        "max_ops": args.max_ops,
        "replay": stats,
        "clean_out_remaining": cost,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    suffix = f"_max{args.max_ops}" if args.max_ops else ""
    path = RESULTS / f"{tag}_typeE_{args.policy}{suffix}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[cell] wrote {path}", flush=True)
    print(json.dumps({"replay": stats, "cost_keys": list(cost.keys())}, indent=2))


if __name__ == "__main__":
    main()
