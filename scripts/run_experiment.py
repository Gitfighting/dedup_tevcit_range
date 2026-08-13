#!/usr/bin/env python3
"""
run_experiment.py —— 迹回放引擎

读取 IBM 迹 + 映射表 → 用 boto3 通过 sky-s3 (port 8002) 执行 PUT/GET → 计时统计

用法:
  python run_experiment.py --trace T15 --dedup on
  python run_experiment.py --trace T15 --dedup off
  python run_experiment.py --trace T15 --dedup on --speedup 10  # 10倍速回放
  python run_experiment.py --all --dedup on   # 跑全部5条迹
"""

import argparse, csv, json, os, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path

DATA_ROOT = os.environ.get("DATA_ROOT", "/data")
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", f"{DATA_ROOT}/skystore-dedup"))
MAPPINGS_DIR = Path(DATA_ROOT) / "mappings"
RESULTS_DIR = Path(DATA_ROOT) / "results"
LOGS_DIR = Path(DATA_ROOT) / "logs"

S3_ENDPOINT = "http://127.0.0.1:8002"
S3_ACCESS_KEY = "dummy_key"
S3_SECRET_KEY = "dummy_secret"
BUCKET = "skystore-dedup-bucket"

import boto3
from botocore.config import Config as BotoConfig


def load_mapping(trace_name: str) -> dict:
    """加载 object_key → file_path 映射."""
    path = MAPPINGS_DIR / f"{trace_name}_mapping.csv"
    if not path.exists():
        print(f"WARN: No mapping for {trace_name}, using sample.")
        path = MAPPINGS_DIR / "T15_mapping.csv"  # fallback
    mapping = {}
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            mapping[row['obj_key']] = row['file_path']
    return mapping


def get_file_bytes(file_path: str) -> bytes:
    """获取真实文件的字节内容."""
    # file_path 格式: "linux/6.1.90/net/ipv4/tcp.c" 或 "linux_archives/linux-6.1.90.tar.xz"
    parts = file_path.split("/", 1)
    if len(parts) < 2:
        return b""

    dataset = parts[0]
    subpath = parts[1]

    # Determine actual path on disk
    base = Path(DATA_ROOT) / "datasets"

    if dataset.endswith("_archives"):
        ds = dataset.replace("_archives", "")
        full = base / ds / subpath.split("/")[-1]
    elif dataset.endswith("_rootfs"):
        ds = dataset.replace("_rootfs", "")
        # subpath is like "php_8.2.32-cli"
        parts2 = subpath.split("/", 1)
        extracted_dir = parts2[0]
        remainder = parts2[1] if len(parts2) > 1 else ""
        full = base / ds / f"{extracted_dir}_extracted" / remainder
    elif dataset == "sqldump":
        full = base / "sqldump" / subpath.split("/")[-1]
    else:
        # linux/6.1.90/net/ipv4/tcp.c
        # Need to extract from tar on the fly
        parts2 = subpath.split("/", 1)
        ver = parts2[0]
        inner = parts2[1] if len(parts2) > 1 else ""
        tar_path = base / "linux" / f"linux-{ver}.tar.xz"
        if not tar_path.exists():
            return b""
        import tarfile, io
        try:
            with tarfile.open(tar_path, 'r:xz') as tf:
                member = tf.getmember(inner)
                fh = tf.extractfile(member)
                if fh:
                    return fh.read()
        except (KeyError, tarfile.TarError):
            pass
        return b""

    if full.exists():
        return full.read_bytes()
    return b""


def replay_trace(trace_name: str, dedup_enabled: bool, speedup: float = 1.0):
    """回放一条迹."""
    log_path = LOGS_DIR / f"replay_{trace_name}_dedup-{dedup_enabled}.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Find trace file
    num = trace_name.replace("T", "").zfill(3)
    trace_candidates = [
        PROJECT_DIR / f"Data/2reg/2reg/IBMObjectStoreTrace{num}Part0.typeE.mc",
        PROJECT_DIR / f"skystore_python/artifact-eval-main/simulation/SNIA_traces/IBMObjectStoreSample",
    ]
    trace_path = None
    for p in trace_candidates:
        if p.exists():
            trace_path = p
            break
    if trace_path is None:
        print(f"  ERROR: No trace file for {trace_name}")
        return

    mapping = load_mapping(trace_name)
    print(f"  Mapping: {len(mapping)} objects")

    s3 = boto3.client('s3', endpoint_url=S3_ENDPOINT,
                      aws_access_key_id=S3_ACCESS_KEY,
                      aws_secret_access_key=S3_SECRET_KEY,
                      region_name='us-east-1', use_ssl=False,
                      config=BotoConfig(connect_timeout=30, read_timeout=120,
                                       retries={'max_attempts': 2}))

    t0 = time.time()
    trace_t0 = None
    line_count = 0
    put_count = get_count = put_bytes = get_bytes = 0
    errors = 0

    # Create bucket if needed
    try:
        s3.create_bucket(Bucket=BUCKET)
    except Exception:
        pass  # already exists

    with open(trace_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue

            try:
                ts_ms = int(parts[0])
            except (ValueError, IndexError):
                continue
            op = parts[1]
            obj_key = parts[2]

            if trace_t0 is None:
                trace_t0 = ts_ms

            # Time alignment
            if speedup < 999:
                elapsed_trace = (ts_ms - trace_t0) / 1000
                elapsed_real = time.time() - t0
                wait = elapsed_trace / speedup - elapsed_real
                if wait > 0:
                    time.sleep(wait)

            try:
                if op == "REST.PUT.OBJECT":
                    fpath = mapping.get(obj_key)
                    if not fpath:
                        continue
                    data = get_file_bytes(fpath)
                    if not data:
                        continue
                    s3.put_object(Bucket=BUCKET, Key=obj_key, Body=data)
                    put_count += 1
                    put_bytes += len(data)

                elif op == "REST.GET.OBJECT":
                    resp = s3.get_object(Bucket=BUCKET, Key=obj_key)
                    body = resp['Body'].read()
                    get_count += 1
                    get_bytes += len(body)

                # HEAD and other ops: skip for now (only PUT/GET matter for dedup)
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  ERR [{obj_key}] {e}")

            line_count += 1
            if line_count % 5000 == 0:
                elapsed = time.time() - t0
                print(f"  [{line_count:>8d}] PUT={put_count} GET={get_count} "
                      f"W={put_bytes/1e6:.1f}MB R={get_bytes/1e6:.1f}MB "
                      f"err={errors} t={elapsed:.0f}s")

    elapsed = time.time() - t0
    result = {
        "trace": trace_name,
        "dedup_enabled": dedup_enabled,
        "speedup": speedup,
        "lines_processed": line_count,
        "puts": put_count,
        "gets": get_count,
        "bytes_written": put_bytes,
        "bytes_read": get_bytes,
        "errors": errors,
        "elapsed_s": round(elapsed, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Save result
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"replay_{trace_name}_dedup-{dedup_enabled}.json"
    with open(result_path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"  Done: {line_count} lines in {elapsed:.0f}s "
          f"(speedup={speedup}x, errors={errors})")
    print(f"  PUT={put_count} ({put_bytes/1e6:.0f}MB) / GET={get_count} ({get_bytes/1e6:.0f}MB)")
    print(f"  Result: {result_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="IBM Trace Replay Experiment")
    parser.add_argument("--trace", choices=["T15","T29","T65","T78","T79"])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dedup", choices=["on","off"], default="on")
    parser.add_argument("--speedup", type=float, default=1.0,
                        help="Replay speed (1=real-time, 10=10x, 0=max)")
    args = parser.parse_args()

    dedup = args.dedup == "on"
    print("=" * 60)
    print(f"TRACE REPLAY - dedup={'ON' if dedup else 'OFF'}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    traces = ["T15","T29","T65","T78","T79"] if args.all else [args.trace]

    for t in traces:
        print(f"\n--- {t} ---")
        try:
            replay_trace(t, dedup, args.speedup)
        except Exception as e:
            print(f"  FAILED: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print("All replays done.")
    print(f"Results: {RESULTS_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
