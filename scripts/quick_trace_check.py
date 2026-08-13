import csv

traces = [
    "/data/skystore/traces/IBMObjectStoreTrace015Part0.typeE.mc-aug.csv",
    "/data/skystore/traces/IBMObjectStoreTrace078Part0.typeE.mc-aug.csv",
    "/data/skystore/traces/IBMObjectStoreTrace079Part0.typeE.mc-aug.csv",
]

for path in traces:
    obj_sizes = {}
    obj_ops = {}
    
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            k = row["obj_key"]
            s = int(row["size"])
            op = row["op"]
            obj_sizes.setdefault(k, set()).add(s)
            obj_ops.setdefault(k, {"PUT": 0, "GET": 0})
            if op in obj_ops[k]:
                obj_ops[k][op] += 1

    multi = {k: v for k, v in obj_sizes.items() if len(v) > 1}
    both = [k for k, v in obj_ops.items() if v["PUT"] > 0 and v["GET"] > 0]
    put_only = [k for k, v in obj_ops.items() if v["PUT"] > 0 and v["GET"] == 0]
    get_only = [k for k, v in obj_ops.items() if v["PUT"] == 0 and v["GET"] > 0]
    
    fname = path.split("/")[-1]
    pct = len(multi) / max(len(obj_sizes), 1) * 100
    print(f"{fname}: {len(obj_sizes)} keys, inconsistent={len(multi)} ({pct:.1f}%)")
    print(f"  Both PUT+GET: {len(both)}, PUT only: {len(put_only)}, GET only: {len(get_only)}")
    for k in list(multi.keys())[:3]:
        print(f"  INCONSISTENT: {k}: sizes={obj_sizes[k]}")
    print()
