#!/usr/bin/env python3
"""
Replay IBM .mc-aug traces against local SkyStore S3 proxy (online ablation).

Uses real file bodies from zero-error manifests + /data/file_pool.
Default transport: boto3 (fast). Optional --aws-cli for debugging.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ablation.body_materialize import (
    DEFAULT_POOL,
    load_manifest,
    materialize_object,
    payload_buffers,
    resolve_manifest,
    safe_name,
)


def _boto3_client(endpoint: str):
    import boto3
    from botocore.client import Config

    # sky-s3/s3s: HeadObject works but GetObject 404s if botocore sends
    # x-amz-checksum-mode=ENABLED (default in recent botocore).
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get(
            "AWS_SECRET_ACCESS_KEY", "minioadmin"
        ),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _stream_body(row: dict, pool: Path, size: int):
    """Spooled file with exact payload bytes (avoid full RAM for large objs)."""
    sp = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    written = 0
    for buf in payload_buffers(row, pool):
        sp.write(buf)
        written += len(buf)
    if written != size:
        sp.close()
        raise IOError(f"wrote {written} != {size}")
    sp.seek(0)
    return sp


def replay(
    trace: Path,
    endpoint: str,
    bucket: str,
    body_dir: Path,
    recipes: dict,
    pool: Path,
    max_ops: int = 0,
    sleep_scale: float = 0.0,
    use_boto3: bool = True,
) -> dict:
    body_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "put": 0,
        "get": 0,
        "skip": 0,
        "error": 0,
        "body_miss": 0,
        "bytes_put": 0,
        "bytes_get": 0,
        "transport": "boto3" if use_boto3 else "aws-cli",
    }
    client = _boto3_client(endpoint) if use_boto3 else None
    t0 = time.time()
    with open(trace, newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        has_header = "timestamp" in sample.splitlines()[0].lower()
        reader = csv.DictReader(f) if has_header else csv.reader(f)
        prev_ts = None
        n = 0
        for row in reader:
            if max_ops and n >= max_ops:
                break
            if has_header:
                ts = int(float(row["timestamp"]))
                op = row["op"].upper()
                key = row.get("obj_key") or row.get("data_id")
                size = int(float(row["size"]))
            else:
                ts = int(float(row[0]))
                op = str(row[1]).upper()
                key = row[3]
                size = int(float(row[4]))
            if sleep_scale > 0 and prev_ts is not None and ts > prev_ts:
                time.sleep(min((ts - prev_ts) * sleep_scale, 0.05))
            prev_ts = ts

            if op == "PUT":
                recipe = recipes.get(key)
                if recipe is None:
                    stats["body_miss"] += 1
                    stats["error"] += 1
                    n += 1
                    continue
                try:
                    if use_boto3:
                        body = _stream_body(recipe, pool, size)
                        try:
                            client.put_object(
                                Bucket=bucket, Key=key, Body=body
                            )
                        finally:
                            body.close()
                    else:
                        path = materialize_object(
                            key, size, body_dir, recipes, pool
                        )
                        env = os.environ.copy()
                        env.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
                        env.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")
                        r = subprocess.run(
                            [
                                "aws", "s3api", "put-object",
                                "--endpoint-url", endpoint, "--no-verify-ssl",
                                "--bucket", bucket, "--key", key,
                                "--body", str(path),
                            ],
                            env=env,
                            capture_output=True,
                            text=True,
                        )
                        if r.returncode != 0:
                            raise RuntimeError(r.stderr[:300])
                    stats["put"] += 1
                    stats["bytes_put"] += size
                except Exception as ex:
                    stats["error"] += 1
                    if stats["error"] <= 8:
                        print(f"[put] {key}: {ex}", file=sys.stderr, flush=True)
            elif op == "GET":
                try:
                    if use_boto3:
                        resp = client.get_object(Bucket=bucket, Key=key)
                        data = resp["Body"].read()
                        stats["bytes_get"] += len(data)
                    else:
                        out = body_dir / f"get_{safe_name(key)}.out"
                        env = os.environ.copy()
                        env.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
                        env.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")
                        r = subprocess.run(
                            [
                                "aws", "s3api", "get-object",
                                "--endpoint-url", endpoint, "--no-verify-ssl",
                                "--bucket", bucket, "--key", key, str(out),
                            ],
                            env=env,
                            capture_output=True,
                            text=True,
                        )
                        if r.returncode != 0:
                            raise RuntimeError(r.stderr[:200])
                        try:
                            out.unlink(missing_ok=True)
                        except OSError:
                            pass
                    stats["get"] += 1
                except Exception:
                    stats["skip"] += 1
            else:
                stats["skip"] += 1
            n += 1
            if n % 2000 == 0:
                rate = n / max(time.time() - t0, 1e-6)
                print(
                    f"[replay] {n} ops {stats} {rate:.1f} ops/s",
                    flush=True,
                )
    stats["ops"] = n
    stats["wall_s"] = round(time.time() - t0, 1)
    stats["ops_per_s"] = round(n / max(stats["wall_s"], 1e-6), 2)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--endpoint", default="http://127.0.0.1:8002")
    ap.add_argument("--bucket", default="default-skybucket")
    ap.add_argument("--body-dir", default="/data/ablation_exp/bodies/tmp")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--pool", default=str(DEFAULT_POOL))
    ap.add_argument("--tag", default="T065")
    ap.add_argument("--max-ops", type=int, default=0)
    ap.add_argument("--sleep-scale", type=float, default=0.0)
    ap.add_argument("--aws-cli", action="store_true")
    args = ap.parse_args()

    man = Path(args.manifest) if args.manifest else resolve_manifest(args.tag)
    print(f"[replay] manifest={man}", flush=True)
    recipes = load_manifest(man)
    print(
        f"[replay] recipes={len(recipes)} endpoint={args.endpoint} "
        f"boto3={not args.aws_cli}",
        flush=True,
    )

    stats = replay(
        Path(args.trace),
        args.endpoint,
        args.bucket,
        Path(args.body_dir),
        recipes,
        Path(args.pool),
        args.max_ops,
        args.sleep_scale,
        use_boto3=not args.aws_cli,
    )
    print(stats)


if __name__ == "__main__":
    main()
