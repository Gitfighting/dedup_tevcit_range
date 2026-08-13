#!/usr/bin/env python3
"""
collect_stats.py —— 从 PostgreSQL + ChunkStore 收集成本统计

查询逻辑:
  去重 OFF: 存储 = SUM(logical_objects.size)
  去重 ON:  存储 = ChunkStore 唯一块总大小

用法:
  python collect_stats.py              # 收集当前数据库统计
  python collect_stats.py --all        # 汇总所有实验结果的 JSONs
"""

import argparse, csv, json, os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

DATA_ROOT = os.environ.get("DATA_ROOT", "/data")
RESULTS_DIR = Path(DATA_ROOT) / "results"

# PostgreSQL connection
DB_CONFIG = {
    'host': 'localhost', 'port': 5432,
    'dbname': 'skystore', 'user': 'ubuntu',
    'password': 'skystore',
}


def collect_from_db():
    """从 PostgreSQL 收集存储和传输统计."""
    try:
        import psycopg2
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        # Total logical objects
        cur.execute("SELECT COUNT(*), SUM(size) FROM logical_objects WHERE status = 'READY'")
        obj_count, logical_size = cur.fetchone()
        obj_count = obj_count or 0
        logical_size = logical_size or 0

        # Physical storage
        cur.execute("""
            SELECT COUNT(*), SUM(size)
            FROM physical_object_locators
            WHERE status = 'READY'
        """)
        phys_count, phys_size = cur.fetchone()
        phys_count = phys_count or 0
        phys_size = phys_size or 0

        # ChunkStore disk usage
        chunk_dir = Path(DATA_ROOT) / "chunks"
        chunk_count = 0
        chunk_bytes = 0
        if chunk_dir.exists():
            for f in chunk_dir.rglob("*"):
                if f.is_file():
                    chunk_count += 1
                    chunk_bytes += f.stat().st_size

        conn.close()

        stats = {
            "timestamp": datetime.now().isoformat(),
            "logical_objects": obj_count,
            "logical_size_bytes": logical_size,
            "physical_locators": phys_count,
            "physical_size_bytes": phys_size,
            "chunkstore_chunks": chunk_count,
            "chunkstore_bytes": chunk_bytes,
        }
        return stats
    except ImportError:
        print("  WARN: psycopg2 not installed, using ChunkStore only")
        chunk_dir = Path(DATA_ROOT) / "chunks"
        chunk_count = sum(1 for _ in chunk_dir.rglob("*") if _.is_file()) if chunk_dir.exists() else 0
        chunk_bytes = sum(_.stat().st_size for _ in chunk_dir.rglob("*") if _.is_file()) if chunk_dir.exists() else 0
        return {"chunkstore_chunks": chunk_count, "chunkstore_bytes": chunk_bytes}
    except Exception as e:
        print(f"  DB error: {e}")
        return {"error": str(e)}


def collect_all_results():
    """汇总 RESULTS_DIR 下所有 JSON 结果."""
    results = {}
    for jf in sorted(RESULTS_DIR.glob("replay_*.json")):
        with open(jf, 'r') as f:
            data = json.load(f)
        trace = data.get("trace", "?")
        dedup = data.get("dedup_enabled", False)
        key = f"{trace}_dedup-{'on' if dedup else 'off'}"
        results[key] = data

    return results


def print_summary(results: dict):
    """打印汇总表."""
    print(f"\n{'='*70}")
    print("EXPERIMENT SUMMARY")
    print(f"{'='*70}")
    print(f"{'Trace':<8s} {'Dedup':<6s} {'PUT/MB':>10s} {'GET/MB':>10s} {'Time':>8s} {'Errors':>7s}")
    print("-" * 70)

    rows = []
    for key in sorted(results.keys()):
        r = results[key]
        trace = r.get("trace", "?")
        dedup = "ON" if r.get("dedup_enabled") else "OFF"
        w_mb = r.get("bytes_written", 0) / 1e6
        r_mb = r.get("bytes_read", 0) / 1e6
        t = r.get("elapsed_s", 0)
        e = r.get("errors", 0)
        print(f"{trace:<8s} {dedup:<6s} {w_mb:>10.1f} {r_mb:>10.1f} {t:>8.0f}s {e:>7d}")
        rows.append((trace, dedup, w_mb, r_mb, t, e))

    # Write CSV
    csv_path = RESULTS_DIR / "summary.csv"
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["trace", "dedup", "put_mb", "get_mb", "elapsed_s", "errors"])
        w.writerows(rows)
    print(f"\n  CSV: {csv_path}")

    # Cost comparison
    print(f"\n{'='*70}")
    print("COST COMPARISON (from DB)")
    print(f"{'='*70}")
    db_stats = collect_from_db()
    if db_stats:
        for k, v in db_stats.items():
            print(f"  {k}: {v}")
        logical = db_stats.get("logical_size_bytes", 0) / 1e9
        chunk = db_stats.get("chunkstore_bytes", 0) / 1e9
        if logical > 0 and chunk > 0:
            saving = (1 - chunk / logical) * 100
            print(f"  Dedup saving: {saving:.1f}%")

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Summarize all replay results")
    args = parser.parse_args()

    if args.all:
        results = collect_all_results()
        if not results:
            print("No replay results found in", RESULTS_DIR)
            return
        print_summary(results)
    else:
        stats = collect_from_db()
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
