"""
Chunk-level cost replay for simulation (OCP).

Two policies share this engine:

  dedup_aware_tevict_ranges
      Copied adaptive TTL + HOT vs DEDUP threshold (τ=(1-L)×T_even).
      DEDUP holds teven/2 (prototype parity).

  tevict_ranges_dedup
      Author TevictRangesV2 TTL unchanged.
      Origin PUT bills full logical size (same as SkyStore primary copy).
      Cross-region cache bills unique missing chunks + manifest.

Does not modify simulator_v2.py core loop.
"""
from __future__ import annotations

import csv
import heapq
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import networkx as nx

from src.dedup.chunk_store import ChunkStore
from src.dedup.region_state import (
    RegionChunkState,
    add_chunks,
    drop_dedup_refs,
    locality_for_object,
    missing_bytes,
    settle,
)
from src.model.config import Config
from src.model.request import Request
from src.placement_policy.policy_dedup_aware_tevict_ranges import (
    ObjFormat,
    PolicyDedupAwareTevictRanges,
)
from src.placement_policy.policy_tevict_ranges_dedup import PolicyTevictRangesDedup
from src.utils.helpers import make_nx_graph

GB = float(2**30)


def _parse_ts_ms(ts: str) -> float:
    # traces use integer ms since epoch-ish start, or plain int ms
    return float(ts) / 1000.0


def _load_ops(trace: Path, max_ops: int = 0):
    rows = []
    with open(trace, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            if max_ops and len(rows) >= max_ops:
                break
    return rows


def _make_request(row: dict) -> Request:
    ts = datetime.fromtimestamp(_parse_ts_ms(row["timestamp"]))
    op = row["op"].upper()
    req_op = "write" if op == "PUT" else "read"
    size = float(row["size"])
    kwargs = {
        "timestamp": ts,
        "op": req_op,
        "issue_region": row["issue_region"],
        "obj_key": row["obj_key"],
        "size": size,
    }
    ttn = row.get("time_to_next_access", "-1")
    if ttn not in (None, "", "-1"):
        # field is absolute ms on the same epoch as timestamp (sim convention)
        kwargs["next_access_timestamp"] = datetime.fromtimestamp(float(ttn) / 1000.0)
    ttnr = row.get("time_to_next_access_same_reg", "-1")
    if ttnr not in (None, "", "-1"):
        kwargs["next_access_same_reg_timestamp"] = datetime.fromtimestamp(
            float(ttnr) / 1000.0
        )
    return Request(**kwargs)


def run_dedup_aware_replay(
    trace: Path,
    chunk_dir: Path,
    config: Config,
    max_ops: int = 0,
    profiles_dir: Optional[Path] = None,
) -> dict:
    return run_chunk_cost_replay(
        trace,
        chunk_dir,
        config,
        max_ops=max_ops,
        profiles_dir=profiles_dir,
        policy_name="dedup_aware_tevict_ranges",
    )


def run_tevict_ranges_dedup_replay(
    trace: Path,
    chunk_dir: Path,
    config: Config,
    max_ops: int = 0,
    profiles_dir: Optional[Path] = None,
) -> dict:
    return run_chunk_cost_replay(
        trace,
        chunk_dir,
        config,
        max_ops=max_ops,
        profiles_dir=profiles_dir,
        policy_name="tevict_ranges_dedup",
    )


def run_chunk_cost_replay(
    trace: Path,
    chunk_dir: Path,
    config: Config,
    max_ops: int = 0,
    profiles_dir: Optional[Path] = None,
    policy_name: str = "dedup_aware_tevict_ranges",
) -> dict:
    t0 = time.time()
    store = ChunkStore.load(chunk_dir)
    rows = _load_ops(trace, max_ops=max_ops)
    regions = sorted({r["issue_region"] for r in rows})
    if config.regions:
        for r in config.regions:
            if r not in regions:
                regions.append(r)

    # Same pricing graph as SimulatorV2 (src/utils/profiles)
    total_graph: nx.DiGraph = make_nx_graph()
    keep = [r for r in regions if r in total_graph.nodes]
    if len(keep) < 2:
        raise RuntimeError(f"need >=2 priced regions, got {keep}")

    ttl_dedup = policy_name == "tevict_ranges_dedup"
    if ttl_dedup:
        policy = PolicyTevictRangesDedup(
            config=config,
            total_graph=total_graph,
            objects={},
            regionManager=None,
            chunk_store=store,
        )
    else:
        policy = PolicyDedupAwareTevictRanges(
            config=config,
            total_graph=total_graph,
            objects={},
            regionManager=None,
            chunk_store=store,
        )

    st: Dict[str, RegionChunkState] = {
        r: RegionChunkState(store.n_unique) for r in keep
    }
    cost = dict(storage=0.0, transfer=0.0)
    ctr = defaultdict(int)
    bytes_moved = 0.0
    heaps: Dict[str, list] = {r: [] for r in keep}

    def rate_gbh(r: str) -> float:
        return float(total_graph.nodes[r]["priceStorage"]) * 3 / 24.0

    def egress(src: str, dst: str) -> float:
        return float(total_graph[src][dst]["cost"])

    def expire(r: str, key: str, now: float):
        s = st[r]
        settle(s, now, GB)
        meta = s.cached.pop(key, None)
        if meta is None:
            return
        _exp, _ttl, fmt = meta
        if fmt == ObjFormat.DEDUP:
            drop_dedup_refs(store, s, key)
            s.cur_bytes -= store.manifest_bytes(key)
        else:
            s.cur_bytes -= store.logical_size(key)
        ctr["evictions"] += 1

    def sweep(r: str, now: float):
        s = st[r]
        settle(s, now, GB)
        h = heaps[r]
        while h and h[0][0] <= now:
            exp, key = heapq.heappop(h)
            cur = s.cached.get(key)
            if cur is not None and cur[0] <= now and abs(cur[0] - exp) < 1e-9:
                expire(r, key, now)

    # Find a default base/src for first PUT region per object
    obj_base: Dict[str, str] = {}

    for row in rows:
        req = _make_request(row)
        now = req.timestamp.timestamp()
        reg = req.issue_region
        if reg not in st:
            ctr["skip_region"] += 1
            continue
        key = req.obj_key
        if not store.has(key):
            ctr["unknown_obj"] += 1
            continue

        for r in keep:
            sweep(r, now)

        s = st[reg]
        settle(s, now, GB)

        # histogram learning (author Phase-1 input)
        policy.update_past_requests(req, reg)

        if req.op == "write":
            ctr["put"] += 1
            if key not in s.pinned_objs:
                s.pinned_objs.add(key)
                obj_base[key] = reg
                if ttl_dedup:
                    # Origin is a normal full object (same as SkyStore PUT).
                    # Chunk pin is only for locality / missing-chunk transfer.
                    add_chunks(store, s, key, pin=True, bill=False)
                    s.cur_bytes += store.logical_size(key)
                    ctr["base_logical_bytes"] += store.logical_size(key)
                else:
                    add_chunks(store, s, key, pin=True)
            continue

        # GET
        ctr["get"] += 1
        if key in s.cached or key in s.pinned_objs:
            ctr["hit"] += 1
            if key in s.cached:
                src = obj_base.get(key, keep[0] if keep[0] != reg else keep[-1])
                if src == reg:
                    src = next(x for x in keep if x != reg)
                cur_fmt = s.cached[key][2]
                ttl, fmt = policy.resolve_cache(req, src, reg, L=s.L)
                refresh = ttl > 0 and (
                    (ttl_dedup and cur_fmt == ObjFormat.DEDUP)
                    or (not ttl_dedup and fmt == ObjFormat.HOT and cur_fmt == ObjFormat.HOT)
                )
                if refresh:
                    exp = now + ttl
                    s.cached[key] = (exp, ttl, cur_fmt)
                    heapq.heappush(heaps[reg], (exp, key))
            continue

        # miss: pull from cheapest other region that has content
        src_cands = [
            r
            for r in keep
            if r != reg
            and (key in st[r].pinned_objs or key in st[r].cached)
        ]
        if not src_cands:
            # fall back to base if known
            if key in obj_base and obj_base[key] != reg:
                src_cands = [obj_base[key]]
            else:
                ctr["miss_no_src"] += 1
                continue
        src = min(src_cands, key=lambda x: egress(x, reg))

        L_obj = locality_for_object(store, s, key)
        setter = getattr(policy, "set_region_L", None)
        if setter is not None:
            setter(reg, s.L)
        ttl, fmt = policy.resolve_cache(req, src, reg, L=L_obj)

        if (not ttl_dedup) and fmt == ObjFormat.HOT:
            xfer = store.logical_size(key)
            cost["transfer"] += egress(src, reg) * (xfer / GB)
            bytes_moved += xfer
            s.cur_bytes += store.logical_size(key)
            if ttl > 0:
                exp = now + ttl
                s.cached[key] = (exp, ttl, fmt)
                heapq.heappush(heaps[reg], (exp, key))
            else:
                s.cur_bytes -= store.logical_size(key)
            ctr["cache_hot"] += 1
        else:
            xfer = missing_bytes(store, s, key)
            cost["transfer"] += egress(src, reg) * (xfer / GB)
            bytes_moved += xfer
            if ttl_dedup:
                if ttl > 0:
                    add_chunks(store, s, key, pin=False)
                    s.cur_bytes += store.manifest_bytes(key)
                    exp = now + ttl
                    s.cached[key] = (exp, ttl, ObjFormat.DEDUP)
                    heapq.heappush(heaps[reg], (exp, key))
                    ctr["cache_dedup"] += 1
                else:
                    ctr["no_cache"] += 1
            else:
                add_chunks(store, s, key, pin=False)
                hold = policy.compute_teven_seconds(src, reg) / 2.0
                exp = now + max(hold, 1.0)
                s.cached[key] = (exp, hold, fmt)
                s.cur_bytes += store.manifest_bytes(key)
                heapq.heappush(heaps[reg], (exp, key))
                ctr["cache_dedup"] += 1

    # settle remaining storage
    end = max((_make_request(r).timestamp.timestamp() for r in rows), default=0.0)
    for r in keep:
        sweep(r, end + 1)
        settle(st[r], end + 1, GB)
        cost["storage"] += st[r].gbh * rate_gbh(r)

    wall = time.time() - t0
    total = cost["storage"] + cost["transfer"]
    extra = {}
    if ttl_dedup:
        extra["policy_keep_decisions"] = policy.stats_keep
        extra["policy_skip_decisions"] = policy.stats_skip
    else:
        extra["policy_hot_decisions"] = policy.stats_hot
        extra["policy_dedup_decisions"] = policy.stats_dedup
    return {
        "ok": True,
        "policy": policy_name,
        "trace": str(trace),
        "chunk_dir": str(chunk_dir),
        "max_ops": max_ops or len(rows),
        "ops": len(rows),
        "regions": keep,
        "total cost without request ($)": round(total, 9),
        "total storage cost ($)": round(cost["storage"], 9),
        "total transfer cost ($)": round(cost["transfer"], 9),
        "bytes_transferred_gb": round(bytes_moved / GB, 6),
        "stats": dict(ctr),
        **extra,
        "region_L": {r: round(st[r].L, 4) for r in keep},
        "wall_s": round(wall, 2),
        "zero_error_manifest_note": (
            "chunk store built by chunk_objects.py from "
            "/data/manifests/*.mc-aug.manifest.csv.gz (remap_zero exact/trim/pad/extent)"
        ),
    }


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--config", default="config/tevictranges_2reg_dedup.yaml")
    ap.add_argument("--max-ops", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from src.utils.helpers import load_config

    cfg = load_config(args.config)
    name = cfg.placement_policy or "dedup_aware_tevict_ranges"
    runner = (
        run_tevict_ranges_dedup_replay
        if name == "tevict_ranges_dedup"
        else run_dedup_aware_replay
    )
    result = runner(
        Path(args.trace),
        Path(args.chunks),
        cfg,
        max_ops=args.max_ops,
    )
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
