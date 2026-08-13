import csv
path = "/data/skystore/traces/IBMObjectStoreTrace065Part0.typeE.mc-aug.csv"
obj_sizes = {}
with open(path) as f:
    r = csv.DictReader(f)
    for row in r:
        k = row["obj_key"]
        s = int(row["size"])
        obj_sizes.setdefault(k, set()).add(s)

multi = {k: v for k, v in obj_sizes.items() if len(v) > 1}
print(f"T65: {len(obj_sizes)} keys, inconsistent={len(multi)}")
for k in list(multi.keys())[:8]:
    print(f"  {k}: {obj_sizes[k]}")

# Also check op counts
obj_ops = {}
with open(path) as f:
    r = csv.DictReader(f)
    for row in r:
        k = row["obj_key"]
        op = row["op"]
        if k not in obj_ops:
            obj_ops[k] = {"PUT": 0, "GET": 0}
        if op in obj_ops[k]:
            obj_ops[k][op] += 1

both = [k for k, v in obj_ops.items() if v["PUT"] > 0 and v["GET"] > 0]
put_only = [k for k, v in obj_ops.items() if v["PUT"] > 0 and v["GET"] == 0]
get_only = [k for k, v in obj_ops.items() if v["PUT"] == 0 and v["GET"] > 0]
print(f"Both PUT+GET: {len(both)}, PUT only: {len(put_only)}, GET only: {len(get_only)}")
