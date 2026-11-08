#!/usr/bin/env python3
"""
trace_replayer.py —— IBM 迹回放引擎

用法:
  source /data/venv/bin/activate && python trace_replayer.py \
    --trace /data/traces/typeA/IBMObjectStoreTrace015Part0.typeA.mc.aug \
    --mapping /data/mappings/T15_typeA_mapping.csv \
    --dedup on \
    --speedup 1000

回放 IBM 迹 + 真实文件映射 → PUT/GET → s3-proxy (带 CDC 去重)
"""

import csv, time, sys, argparse, json
from pathlib import Path
from collections import defaultdict

# 如果没有真实的 s3-proxy，用本地文件系统模拟去重（用于初步测试）
LOCAL_MODE = False  # 设为 True 可离线测试
LOCAL_CHUNK_STORE = Path("/data/chunks")
LOCAL_MANIFEST_FILE = Path("/data/results/manifest.jsonl")


def load_mapping(path):
    mapping = {}
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            mapping[row['obj_key']] = row['file_path']
    return mapping


def local_put(obj_key, file_path, chunk_store, manifests, stats):
    """本地模拟 PUT：读取真实文件，记录大小"""
    full_path = Path("/data/datasets") / file_path
    if not full_path.exists():
        stats['missing_files'] += 1
        return

    try:
        size = full_path.stat().st_size
    except OSError:
        stats['missing_files'] += 1
        return

    stats['puts'] += 1
    stats['put_bytes'] += size
    stats['unique_files'].add(file_path)

    # 记录 manifest（简化版，不需要真实 CDC）
    manifests[obj_key] = {
        'file_path': file_path,
        'size': size,
    }


def local_get(obj_key, manifests, stats):
    """本地模拟 GET"""
    if obj_key in manifests:
        size = manifests[obj_key]['size']
        stats['gets'] += 1
        stats['get_bytes'] += size
    else:
        stats['misses'] += 1


def local_replay(trace_path, mapping, speedup=1000):
    """本地模拟回放（无 s3-proxy，纯统计）"""
    stats = {
        'puts': 0, 'gets': 0, 'misses': 0,
        'put_bytes': 0, 'get_bytes': 0,
        'unique_files': set(), 'missing_files': 0,
        'lines': 0,
    }
    manifests = {}
    t0 = None
    trace_start = None

    with open(trace_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = int(row.get('timestamp', 0))
            except (ValueError, TypeError):
                ts = 0

            if t0 is None:
                t0 = time.time()
                trace_start = ts

            # 时间对齐
            if speedup > 0 and ts > 0:
                elapsed_trace = (ts - trace_start) / 1000.0
                elapsed_real = time.time() - t0
                sleep_time = elapsed_trace / speedup - elapsed_real
                if sleep_time > 0:
                    time.sleep(min(sleep_time, 10))

            op = row.get('op', '')
            obj_key = row.get('obj_key', '')
            stats['lines'] += 1

            if op == 'PUT':
                fp = mapping.get(obj_key)
                if fp:
                    local_put(obj_key, fp, LOCAL_CHUNK_STORE, manifests, stats)
            elif op == 'GET':
                local_get(obj_key, manifests, stats)

            if stats['lines'] % 500000 == 0:
                elapsed = time.time() - t0 if t0 else 0
                print(f"  [{stats['lines']:,}] PUT={stats['puts']:,} GET={stats['gets']:,} "
                      f"elapsed={elapsed:.0f}s")

    elapsed = time.time() - t0 if t0 else 0

    print(f"\n=== Replay Complete ===")
    print(f"  Lines:    {stats['lines']:,}")
    print(f"  PUT:      {stats['puts']:,}  ({stats['put_bytes']/1e9:.2f} GB)")
    print(f"  GET:      {stats['gets']:,}  ({stats['get_bytes']/1e9:.2f} GB)")
    print(f"  Misses:   {stats['misses']:,}")
    print(f"  Unique files uploaded: {len(stats['unique_files'])}")
    print(f"  Missing files: {stats['missing_files']}")
    print(f"  Duration:  {elapsed:.1f}s")
    return stats


def main():
    parser = argparse.ArgumentParser(description='IBM Trace Replayer')
    parser.add_argument('--trace', required=True, help='Trace file (*.mc.aug)')
    parser.add_argument('--mapping', required=True, help='Mapping CSV')
    parser.add_argument('--dedup', choices=['on', 'off'], default='on')
    parser.add_argument('--speedup', type=float, default=1000, help='Speedup factor')
    parser.add_argument('--s3-endpoint', default='http://localhost:8002')
    parser.add_argument('--mode', choices=['local', 's3'], default='local',
                        help='local=dry run stats, s3=real s3-proxy')
    args = parser.parse_args()

    print("=" * 60)
    print(f"Trace Replayer (dedup={args.dedup}, speedup={args.speedup}x)")
    print(f"  Trace:   {args.trace}")
    print(f"  Mapping: {args.mapping}")
    print("=" * 60)

    mapping = load_mapping(args.mapping)
    print(f"\nLoaded {len(mapping):,} mappings")

    if args.mode == 'local':
        stats = local_replay(args.trace, mapping, args.speedup)
        # 保存统计
        out_path = Path("/data/results") / f"{Path(args.trace).stem}_stats.json"
        out = {k: (len(v) if isinstance(v, set) else v) for k, v in stats.items()}
        with open(out_path, 'w') as f:
            json.dump(out, f, indent=2)
        print(f"\nStats saved: {out_path}")
    else:
        # TODO: 真实 s3-proxy 模式
        print("s3 mode not yet implemented. Use --mode=local for dry run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
