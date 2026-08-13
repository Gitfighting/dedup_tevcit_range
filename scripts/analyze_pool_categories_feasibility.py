"""Fast category rollup + exact-size feasibility (no full 18M walk if avoidable)."""
import os
import csv
import subprocess
import numpy as np
from collections import defaultdict

ROOT = "/data/file_pool"
TRACES = "/data/skystore/traces"


def category(name: str) -> str:
    n = name.lower()
    if n == "linux_kernel" or n.startswith("linux-") or n.startswith("linux_"):
        return "linux"
    if n.startswith("ubuntu") or n in (
        "bionic-base-amd64",
        "focal-base-amd64",
        "jammy-base-amd64",
        "noble-base-amd64",
    ):
        return "ubuntu"
    if n.startswith("php-") or n.startswith("php_"):
        return "php"
    if n.startswith("bitnami-") or n.startswith("deb-bitnami-"):
        return "bitnami_docker"
    if n == "deb_ova" or n.startswith("debian-") or n.startswith("deb-debian-"):
        return "debian_deb_ova"
    if n == "mapped_tars":
        return "mapped_tars"
    if "sql" in n or n.startswith("sqldump"):
        return "sqldump"
    return "other"


def main():
    print("=" * 70)
    print("1) TOP-LEVEL CATEGORY BREAKDOWN (du -sb each top entry)")
    print("=" * 70)
    stats = defaultdict(lambda: {"top": 0, "bytes": 0, "examples": []})
    names = sorted(os.listdir(ROOT))
    for i, name in enumerate(names):
        path = os.path.join(ROOT, name)
        cat = category(name)
        stats[cat]["top"] += 1
        if len(stats[cat]["examples"]) < 4:
            stats[cat]["examples"].append(name)
        try:
            out = subprocess.check_output(["du", "-sb", path], stderr=subprocess.DEVNULL, text=True)
            b = int(out.split()[0])
        except Exception:
            b = 0
        stats[cat]["bytes"] += b
        if (i + 1) % 50 == 0:
            print(f"  ... scanned {i+1}/{len(names)} top entries", flush=True)

    total = sum(v["bytes"] for v in stats.values())
    print(f"{'category':18s} {'tops':>6s} {'GB':>10s} {'pct':>7s}  examples")
    for cat, v in sorted(stats.items(), key=lambda x: -x[1]["bytes"]):
        gb = v["bytes"] / (1024**3)
        pct = 100.0 * v["bytes"] / max(total, 1)
        print(f"{cat:18s} {v['top']:6d} {gb:10.2f} {pct:6.1f}%  {', '.join(v['examples'])}")
    print(f"{'TOTAL':18s} {sum(v['top'] for v in stats.values()):6d} {total/(1024**3):10.2f}")

    print()
    print("=" * 70)
    print("2) POOL SIZE ARRAY + TRACE FEASIBILITY")
    print("=" * 70)
    pool = np.load("/data/pool_sizes.npy").astype(np.int64)
    pool_set = set(pool.tolist())
    pool_sorted = np.sort(pool)
    print(f"pool files(npy)={len(pool):,} unique_sizes={len(np.unique(pool)):,} "
          f"min={pool.min()} max={pool.max()} total_GB={pool.sum()/(1024**3):.2f}")

    print()
    print("Strict |F|==S is hard. Truncate/cycle always gives 0-byte size error.")
    print()

    for fname in sorted(os.listdir(TRACES)):
        if not fname.endswith(".csv"):
            continue
        path = os.path.join(TRACES, fname)
        puts = 0
        uniq = set()
        with open(path, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                if row.get("op") != "PUT":
                    continue
                puts += 1
                uniq.add(int(row["size"]))

        exact = sum(1 for s in uniq if s in pool_set)
        # nearest for all unique sizes
        within1 = within1k = within1m = 0
        dists = []
        for s in uniq:
            idx = int(np.searchsorted(pool_sorted, s))
            best = None
            if idx < len(pool_sorted):
                best = abs(int(pool_sorted[idx]) - s)
            if idx > 0:
                d = abs(int(pool_sorted[idx - 1]) - s)
                best = d if best is None else min(best, d)
            if best is None:
                continue
            dists.append(best)
            if best <= 1:
                within1 += 1
            if best <= 1024:
                within1k += 1
            if best <= 1024 * 1024:
                within1m += 1
        dists.sort()
        short = fname.replace("IBMObjectStoreTrace", "T").replace("Part0.typeE.mc-aug.csv", "")
        n = max(len(uniq), 1)
        print(f"{short}: PUT={puts:,} unique_PUT_sizes={len(uniq):,}")
        print(f"  strict exact |F|==S: {exact}/{len(uniq)} ({100*exact/n:.2f}%)")
        if dists:
            print(f"  nearest median/p90/max: {dists[len(dists)//2]} / {dists[int(len(dists)*0.9)]} / {dists[-1]} B")
            print(f"  nearest <=1B / <=1KB / <=1MB: {within1}/{within1k}/{within1m}")
        print(f"  truncate/cycle => size error ALWAYS 0B for all {puts:,} PUTs")
        print()
    print("Done.")


if __name__ == "__main__":
    main()
