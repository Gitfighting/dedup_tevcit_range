"""
ChunkTevict cost replay.

TevictRanges histogram; lease is per unique chunk.
GET miss: pay cheapest holder; TTL = min live get_tevict (source uid
must cover now+T). GET hit: same min among live holders.

FB vs FP (config.fixed_base_region, same meaning as SimulatorV2 --setbase):

  FB: PUT region is pinned forever. Last-copy never moves.
  FP PUT of a new local copy: no other holder ⇒ author's median T_even
  (N / (S·3) · 86400 s, same formula as calculate_ttl with empty region_list);
  other holders ⇒ min_live get_tevict. Existing local copies are not
  refreshed on PUT.

  FP last copy (author expire_immediate): do not drop the uid. Freeze
  exp=inf. A GET hit clears the freeze and sets calculate_ttl. When a
  second copy is created (GET miss transfer or FP PUT), the frozen copy's
  TTL becomes now + transfer_time, where transfer_time is the transfer
  graph RTT in seconds (author transfer_time_millis/1000), not size/throughput.
  The frozen copy is not deleted until that expiry.

Latency matches SimulatorV2._update_transfer_metric: graph avg_rtt
(ms), not client wall time and not size/throughput. Same-region is the
self-loop (1 ms). GET miss from several holders: max RTT (parallel).

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
from typing import Dict, List, Optional

import numpy as np

from src.dedup.chunk_store import ChunkStore
from src.dedup.region_state import (
    RegionChunkState,
    cheapest_src_for_uids,
    ensure_present,
    expire_chunk_lease,
    holds_uids,
    lease_covers,
    set_chunk_lease,
    settle,
    uid_holder_count,
)
from src.model.config import Config
from src.model.request import Request
from src.placement_policy.policy_chunk_tevict import PolicyChunkTevict
from src.utils.helpers import make_nx_graph

GB = float(2**30)


def _latency_summary(xs: List[float]) -> dict:
    if not xs:
        return {"n": 0, "avg": 0.0, "p50": 0.0, "p90": 0.0, "p99": 0.0}
    a = np.asarray(xs, dtype=np.float64)
    return {
        "n": int(a.size),
        "avg": round(float(a.mean()), 4),
        "p50": round(float(np.percentile(a, 50)), 4),
        "p90": round(float(np.percentile(a, 90)), 4),
        "p99": round(float(np.percentile(a, 99)), 4),
    }


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


def run_chunk_tevict_replay(
    trace: Path,
    chunk_dir: Path,
    config: Config,
    max_ops: int = 0,
    profiles_dir: Optional[Path] = None,
) -> dict:
    """ChunkTevict replay."""
    t0 = time.time()
    store = ChunkStore.load(chunk_dir)
    rows = _load_ops(trace, max_ops=max_ops)
    regions = sorted({r["issue_region"] for r in rows})
    if config.regions:
        for r in config.regions:
            if r not in regions:
                regions.append(r)

    total_graph = make_nx_graph()
    keep = [r for r in regions if r in total_graph.nodes]
    if len(keep) < 2:
        raise RuntimeError(f"need >=2 priced regions, got {keep}")

    fixed_base = bool(getattr(config, "fixed_base_region", False))
    policy = PolicyChunkTevict(
        config=config,
        total_graph=total_graph,
        objects={},
        regionManager=None,
        chunk_store=store,
    )

    st: Dict[str, RegionChunkState] = {
        r: RegionChunkState(store.n_unique) for r in keep
    }
    for s in st.values():
        s.enable_chunk_clock()

    cost = dict(storage=0.0, transfer=0.0)
    ctr = defaultdict(int)
    bytes_moved = 0.0
    heaps: Dict[str, list] = {r: [] for r in keep}
    read_lats: List[float] = []
    write_lats: List[float] = []

    def rate_gbh(r: str) -> float:
        return float(total_graph.nodes[r]["priceStorage"]) * 3 / 24.0

    def price_pair(src: str, dst: str):
        e = total_graph[src][dst]
        return float(e["cost"]), float(e.get("latency", 0.0))

    def edge_rtt_ms(src: str, dst: str) -> float:
        if src is None or dst is None:
            return 0.0
        if total_graph.has_edge(src, dst):
            return float(total_graph[src][dst].get("latency", 1.0 if src == dst else 0.0))
        return 1.0 if src == dst else 0.0

    def pair_ttl(src: str, dst: str, ts: datetime, cache: dict) -> float:
        k = (src, dst)
        if k not in cache:
            cache[k] = float(policy.get_tevict_seconds(src, dst, ts))
        return cache[k]

    def min_live_ttl(uids: np.ndarray, now: float, ts: datetime, cache: dict) -> np.ndarray:
        """Author min_i get_tevict with source-liveness, per uid.

        First holder of the uid is always taken. An immortal holder (FB pin /
        FP freeze, exp=inf) may replace. A smaller T is taken only if that
        source's uid lease covers now+T.
        No other holder ⇒ median T_even.
        """
        n = len(uids)
        best = np.full(n, np.inf)
        seen = np.zeros(n, dtype=bool)
        others = [x for x in keep if x != reg]
        for r in others:
            sr = st[r]
            has = holds_uids(sr, uids)
            if not np.any(has):
                continue
            t = pair_ttl(r, reg, ts, cache)
            covers = lease_covers(sr, uids, now, t)
            exp = (
                sr.chunk_exp[uids]
                if sr.chunk_exp is not None
                else np.full(n, np.nan)
            )
            immortal = (sr.pinned[uids] > 0) | np.isinf(exp)
            take = has & ((~seen) | immortal | ((t < best) & covers))
            best[take] = t
            seen |= has
        best[~seen] = policy.median_teven_seconds(reg)
        return best

    def sweep(r: str, now: float, final: bool = False) -> None:
        s = st[r]
        settle(s, now, GB)
        h = heaps[r]
        while h and h[0][0] <= now:
            exp, uid, gen = heapq.heappop(h)
            if s.chunk_gen is None or int(s.chunk_gen[uid]) != gen:
                continue
            if s.pinned[uid] > 0:
                continue
            if not final and not fixed_base and uid_holder_count(st, uid) <= 1:
                s.chunk_exp[uid] = np.inf
                s.pending_uids.add(uid)
                ctr["last_copy_hold"] += 1
                continue
            if expire_chunk_lease(store, s, uid, bill=True):
                ctr["chunk_evictions"] += 1
                s.pending_uids.discard(uid)

    def release_pending(uid: int, now: float, keep_region: str, delay_s: float) -> None:
        """Author: last copy expire_immediate → set_ttl(now - start + transfer_s).

        That is expiry = now + transfer_s (graph RTT, not throughput).
        Do not drop the frozen uid immediately.
        """
        if fixed_base:
            return
        exp = now + max(float(delay_s), 0.0)
        for r in keep:
            if r == keep_region:
                continue
            sr = st[r]
            if uid in sr.pending_uids:
                sr.pending_uids.discard(uid)
                set_chunk_lease(sr, np.array([uid], dtype=np.int64), exp, heaps[r])
                ctr["pending_release"] += 1

    for row in rows:
        req = _make_request(row)
        now = req.timestamp.timestamp()
        ts = req.timestamp
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
        # Ordered uid list = reconstruct recipe. Billing/TTL use unique uids
        # (intra-object repeats must not double-count storage or transfer).
        ordered = store.uids(key)
        uids = np.unique(np.asarray(ordered))

        if req.op == "read":
            policy.update_past_requests(req, reg)

        if req.op == "write":
            ctr["put"] += 1
            if len(uids) == 0:
                continue
            present = holds_uids(s, uids)
            hit = uids[present]
            miss = uids[~present]
            if fixed_base:
                ensure_present(store, s, uids, pin=True, bill=True)
                if s.chunk_exp is not None:
                    s.chunk_exp[uids] = np.inf
            else:
                # Author calculate_ttl (FP): new copy only.
                # no other replica → median T_even; else min get_tevict from holders.
                # Already present: skip (do not refresh).
                if len(miss) > 0:
                    ensure_present(store, s, miss, pin=False, bill=True)
                    srcs, _ = cheapest_src_for_uids(st, reg, miss, keep, price_pair)
                    ttl_cache: dict = {}
                    med = policy.median_teven_seconds(reg)
                    has_src = np.array([x is not None for x in srcs], dtype=bool)
                    if np.any(~has_src):
                        set_chunk_lease(
                            s, miss[~has_src], now + max(med, 0.0), heaps[reg]
                        )
                        ctr["put_ttl_median_teven"] += 1
                    if np.any(has_src):
                        pull = miss[has_src]
                        ttls = min_live_ttl(pull, now, ts, ttl_cache)
                        for tval in np.unique(ttls):
                            set_chunk_lease(
                                s,
                                pull[ttls == tval],
                                now + max(float(tval), 0.0),
                                heaps[reg],
                            )
                        ctr["put_ttl_tevict"] += 1
                    for uid in miss.tolist():
                        # FP PUT write graph is local: author RTT = self-loop (1 ms).
                        release_pending(
                            int(uid), now, reg, edge_rtt_ms(reg, reg) / 1000.0
                        )
            ctr["put_hit_chunks"] += int(len(hit))
            ctr["put_miss_chunks"] += int(len(miss))
            # PUT is write-local (author): self-loop RTT, same as SimulatorV2.
            write_lats.append(edge_rtt_ms(reg, reg))
            continue

        ctr["get"] += 1
        if len(uids) == 0:
            continue
        present = holds_uids(s, uids)
        hit = uids[present]
        need = uids[~present]
        ctr["get_hit_chunks"] += int(len(hit))
        ctr["get_miss_chunks"] += int(len(need))
        ttl_cache: dict = {}

        if len(hit) > 0:
            timed = hit[s.pinned[hit] == 0]
            if len(timed) > 0:
                best = min_live_ttl(timed, now, ts, ttl_cache)
                for tval in np.unique(best):
                    set_chunk_lease(
                        s, timed[best == tval], now + max(float(tval), 0.0), heaps[reg]
                    )
                # Author GET hit: expire_immediate = False, then set_ttl.
                # Last-copy freeze is lifted; do not treat this copy as pending.
                for uid in timed.tolist():
                    if uid in s.pending_uids:
                        s.pending_uids.discard(int(uid))

        if len(need) == 0:
            ctr["hit"] += 1
            read_lats.append(edge_rtt_ms(reg, reg))
            continue

        srcs, unit_c = cheapest_src_for_uids(st, reg, need, keep, price_pair)
        ok = np.array([x is not None for x in srcs], dtype=bool)
        if not np.any(ok):
            ctr["miss_no_src"] += 1
            continue
        if not np.all(ok):
            # Cannot restore the object: some unique chunks have no holder.
            ctr["partial_no_src"] += 1
        pull = need[ok]
        srcs = srcs[ok]
        unit_c = unit_c[ok]
        nbytes = store.lens_u[pull].astype(np.float64)
        cost["transfer"] += float(np.dot(nbytes / GB, unit_c))
        bytes_moved += int(nbytes.sum())
        ensure_present(store, s, pull, pin=False, bill=True)
        # Parallel fetch: GET waits on the slowest paid edge (author uses max RTT).
        rtts = [edge_rtt_ms(str(src), reg) for src in srcs]
        if len(hit) > 0:
            rtts.append(edge_rtt_ms(reg, reg))
        read_lats.append(max(rtts) if rtts else edge_rtt_ms(reg, reg))

        ttls = min_live_ttl(pull, now, ts, ttl_cache)
        for tval in np.unique(ttls):
            set_chunk_lease(
                s, pull[ttls == tval], now + max(float(tval), 0.0), heaps[reg]
            )
        if not fixed_base:
            # Author: one transfer_time_millis = max edge RTT on this miss.
            delay_s = (max(rtts) if rtts else 1.0) / 1000.0
            for uid in pull.tolist():
                release_pending(int(uid), now, reg, delay_s)
        ctr["cache_chunk"] += 1

    end = max((_make_request(r).timestamp.timestamp() for r in rows), default=0.0)
    for r in keep:
        sweep(r, end + 1, final=True)
        settle(st[r], end + 1, GB)
        cost["storage"] += st[r].gbh * rate_gbh(r)

    wall = time.time() - t0
    total = cost["storage"] + cost["transfer"]
    lat_read = _latency_summary(read_lats)
    lat_write = _latency_summary(write_lats)
    return {
        "ok": True,
        "policy": "chunk_tevict",
        "fixed_base_region": fixed_base,
        "trace": str(trace),
        "chunk_dir": str(chunk_dir),
        "max_ops": max_ops or len(rows),
        "ops": len(rows),
        "regions": keep,
        "total cost without request ($)": round(total, 9),
        "total storage cost ($)": round(cost["storage"], 9),
        "total transfer cost ($)": round(cost["transfer"], 9),
        "bytes_transferred_gb": round(bytes_moved / GB, 6),
        "avg read latency (ms)": lat_read["avg"],
        "avg write latency (ms)": lat_write["avg"],
        "latency": {"read": lat_read, "write": lat_write},
        "stats": dict(ctr),
        "wall_s": round(wall, 2),
    }


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--config", default="config/chunk_tevict_2reg_fb.yaml")
    ap.add_argument("--max-ops", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from src.utils.helpers import load_config

    cfg = load_config(args.config)
    if (cfg.placement_policy or "chunk_tevict") != "chunk_tevict":
        raise SystemExit(f"unknown policy {cfg.placement_policy}")
    result = run_chunk_tevict_replay(
        Path(args.trace),
        Path(args.chunks),
        cfg,
        max_ops=args.max_ops,
    )
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
