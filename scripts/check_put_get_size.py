"""Check if same obj_key always has the same size across PUT and GET."""
import csv
from collections import defaultdict
import sys

traces_dir = "/data/skystore/traces"
traces = [
    "IBMObjectStoreTrace015Part0.typeE.mc-aug.csv",
    "IBMObjectStoreTrace029Part0.typeE.mc-aug.csv",
    "IBMObjectStoreTrace065Part0.typeE.mc-aug.csv",
    "IBMObjectStoreTrace078Part0.typeE.mc-aug.csv",
    "IBMObjectStoreTrace079Part0.typeE.mc-aug.csv",
]

for tname in traces:
    path = f"{traces_dir}/{tname}"
    obj_sizes = {}
    
    print(f"\n{'='*60}")
    print(f"Trace: {tname}")
    
    with open(path) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            key = row["obj_key"]
            size = int(row["size"])
            op = row["op"]
            
            if key not in obj_sizes:
                obj_sizes[key] = []
            obj_sizes[key].append((size, op))
    
    # Analyze
    total_keys = len(obj_sizes)
    inconsistent = 0
    examples = []
    
    for key, entries in obj_sizes.items():
        sizes = set(s for s, _ in entries)
        if len(sizes) > 1:
            inconsistent += 1
            if len(examples) < 5:
                # Get all unique (size, op) pairs
                size_counts = defaultdict(int)
                for s, op in entries:
                    size_counts[(s, op)] += 1
                examples.append((key, size_counts))
    
    # Also check: does PUT happen before GET for the same key?
    put_first = 0
    get_first = 0
    both_ops = 0
    for key, entries in obj_sizes.items():
        has_put = any(op == "PUT" for _, op in entries)
        has_get = any(op == "GET" for _, op in entries)
        if has_put and has_get:
            both_ops += 1
            first_op = entries[0][1]
            if first_op == "PUT":
                put_first += 1
            else:
                get_first += 1
    
    pct = inconsistent / max(total_keys, 1) * 100
    print(f"  Unique keys: {total_keys:,}")
    print(f"  Consistent size: {total_keys - inconsistent:,} ({100-pct:.1f}%)")
    print(f"  Inconsistent size: {inconsistent:,} ({pct:.1f}%)")
    print(f"  Keys with both PUT+GET: {both_ops:,}")
    print(f"    PUT first: {put_first:,}  GET first: {get_first:,}")
    
    if examples:
        print(f"  Examples of inconsistent sizes:")
        for key, size_counts in examples[:5]:
            print(f"    {key}")
            for (size, op), count in sorted(size_counts.items()):
                print(f"      size={size:,}  op={op}  count={count}")
    
    # Size distribution within each key
    size_spread = []
    for key, entries in obj_sizes.items():
        sizes = [s for s, _ in entries]
        min_s = min(sizes)
        max_s = max(sizes)
        if max_s > min_s:
            ratio = max_s / min_s
            size_spread.append(ratio)
    
    if size_spread:
        print(f"  Size ratio (max/min) stats for inconsistent keys:")
        size_spread.sort()
        print(f"    count={len(size_spread)} min={size_spread[0]:.2f}x median={size_spread[len(size_spread)//2]:.2f}x max={size_spread[-1]:.2f}x")
        # Count how many are within 10% difference
        near = sum(1 for r in size_spread if r < 1.1)
        print(f"    within 10%: {near}/{len(size_spread)} ({near/max(len(size_spread),1)*100:.1f}%)")
        exact = sum(1 for r in size_spread if r < 1.0001)
        print(f"    near-exact: {exact}/{len(size_spread)} ({exact/max(len(size_spread),1)*100:.1f}%)")

print("\nDone.")
