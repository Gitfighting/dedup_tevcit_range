#!/usr/bin/env python3
"""
build_mapping.py —— 为每条 IBM 迹生成 object_id → file_path 映射表

输入: /data/file_pool.csv  +  IBM 迹文件
输出: /data/mappings/TXX_mapping.csv

用法:
  python build_mapping.py                           # 处理全部5条迹
  python build_mapping.py --trace T15               # 仅处理 T15
"""

import argparse, bisect, csv, os, time
from collections import defaultdict
from pathlib import Path

DATA_ROOT = os.environ.get("DATA_ROOT", "/data")
POOL_PATH = Path(DATA_ROOT) / "file_pool.csv"
MAPPINGS_DIR = Path(DATA_ROOT) / "mappings"


def load_file_pool(pool_path: Path) -> list:
    """加载文件池."""
    pool = []
    with open(pool_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sz = int(row['size_bytes'])
            pool.append((row['file_path'], sz, row['dataset'], row['tier']))
    pool.sort(key=lambda x: x[1])
    return pool


def load_trace_raw(trace_name: str, project_dir: Path) -> list:
    """从原始迹 CSV 中提取所有唯一 PUT 对象."""
    # Try multiple possible paths
    num = trace_name.replace("T", "").zfill(3)
    candidates = [
        project_dir / f"Data/2reg/2reg/IBMObjectStoreTrace{num}Part0.typeE.mc",
        # Fallback: pickle format
    ]
    
    trace_path = None
    for p in candidates:
        if p.exists():
            trace_path = p
            break

    if trace_path is None:
        # Try pickle
        import pickle
        pickle_dir = project_dir / "skystore_python/artifact-eval-main/simulation/SNIA_traces/traces/raw_traces"
        pickle_path = pickle_dir / f"IBMObjectStoreTrace{num}Part0.pickle"
        if pickle_path.exists():
            with open(pickle_path, 'rb') as f:
                data = pickle.load(f)
            objects = []
            seen = set()
            for obj_key, ops in data.items():
                for op in ops:
                    if op[1] == "REST.PUT.OBJECT":
                        try:
                            sz = int(op[2])
                        except (ValueError, TypeError):
                            sz = 0
                        if obj_key not in seen:
                            objects.append((obj_key, sz))
                            seen.add(obj_key)
            print(f"  Read {len(objects)} unique PUT objects from pickle")
            return objects
        else:
            # Last resort: read sample trace
            sample_path = project_dir / "skystore_python/artifact-eval-main/simulation/SNIA_traces/IBMObjectStoreSample"
            if sample_path.exists():
                print(f"  WARN: No trace file for {trace_name}. Using sample trace.")
                trace_path = sample_path

    if trace_path is None:
        print(f"  ERROR: No trace file for {trace_name}")
        return []

    # Read CSV
    objects = []
    seen = set()
    with open(trace_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) < 4 or parts[1] != "REST.PUT.OBJECT":
                continue
            obj_key = parts[2]
            try:
                sz = int(parts[3])
            except (ValueError, IndexError):
                sz = 0
            if obj_key not in seen:
                objects.append((obj_key, sz))
                seen.add(obj_key)

    print(f"  Read {len(objects)} unique PUT objects from {trace_path.name}")
    return objects


def map_trace(trace_name: str, pool: list, project_dir: Path) -> str:
    """为一条迹生成映射表.

    算法：对每个 IBM 对象，在文件池中二分查找大小最接近的未分配文件。
    池耗尽时循环复用。
    """
    print(f"\n{'='*50}")
    print(f"Mapping: {trace_name}")
    print(f"{'='*50}")

    objects = load_trace_raw(trace_name, project_dir)
    if not objects:
        return ""

    # 预计算大小数组（一次性，不在循环内重建）
    pool_sizes = [p[1] for p in pool]
    pool_n = len(pool)

    used = set()
    mappings = []
    deltas = []

    for obj_key, trace_size in objects:
        if trace_size <= 0:
            continue

        # 二分定位
        idx = bisect.bisect_left(pool_sizes, trace_size)

        # 双向螺旋搜索：每次向左和向右各走一步，取未使用且更接近的
        best_idx = -1
        best_dist = float('inf')
        max_radius = max(500, pool_n // 100)

        # offset=0 时只检查 idx（左右对称点相同）
        left, right = idx, idx
        for offset in range(max_radius):
            if left >= 0 and left not in used:
                dist = abs(pool_sizes[left] - trace_size)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = left
            if right < pool_n and right != left and right not in used:
                dist = abs(pool_sizes[right] - trace_size)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = right
            # 如果已经找到且左右都越界，提前终止
            if best_idx >= 0 and left < 0 and right >= pool_n:
                break
            if best_idx >= 0 and best_dist == 0:
                break  # 精确命中，无需继续
            left -= 1
            right += 1
            if left < 0 and right >= pool_n and best_idx >= 0:
                break

        # 池耗尽：清空 used，从头循环
        if best_idx < 0:
            used.clear()
            best_idx = min(bisect.bisect_left(pool_sizes, trace_size), pool_n - 1)

        used.add(best_idx)
        pe = pool[best_idx]
        d = abs(trace_size - pe[1]) / max(trace_size, 1)
        deltas.append(d)
        mappings.append((obj_key, pe[0], trace_size, pe[1], f"{d:.4f}", pe[2], pe[3]))

        # 进度条（每 50000 个对象输出一次）
        if len(mappings) % 50000 == 0:
            avg_sofar = sum(deltas) / len(deltas)
            print(f"  [{len(mappings):>8d}] avg_delta={avg_sofar:.1%}  used={len(used):,}/{pool_n:,}")

    MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)
    out = MAPPINGS_DIR / f"{trace_name}_mapping.csv"
    with open(out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["obj_key", "file_path", "trace_size", "pool_size",
                     "delta_pct", "dataset", "tier"])
        w.writerows(mappings)

    avg = sum(deltas)/len(deltas) if deltas else 0
    med = sorted(deltas)[len(deltas)//2] if deltas else 0
    print(f"  Objects: {len(mappings):,}  Avg delta: {avg:.1%}  Med delta: {med:.1%}")
    return str(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", choices=["T15","T29","T65","T78","T79"])
    parser.add_argument("--project-dir", default=f"{DATA_ROOT}/skystore-dedup")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 50)
    print(f"BUILD MAPPINGS  [{time.strftime('%H:%M:%S')}]")
    print("=" * 50)

    if not POOL_PATH.exists():
        print(f"ERROR: {POOL_PATH} not found. Run build_file_pool.py first.")
        return
    pool = load_file_pool(POOL_PATH)
    print(f"Pool: {len(pool):,} files")

    traces = [args.trace] if args.trace else ["T15","T29","T65","T78","T79"]
    for t in traces:
        map_trace(t, pool, Path(args.project_dir))

    print(f"\nDone in {time.time()-t0:.0f}s. Output: {MAPPINGS_DIR}")


if __name__ == "__main__":
    main()
