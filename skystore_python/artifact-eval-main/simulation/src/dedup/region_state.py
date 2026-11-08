"""Per-region unique-chunk presence (refcount + pin) for cost accounting.

Pin is a per-chunk counter so FP last-copy freeze can hold a uid without
sharing a pointer to another region.

FB: origin PUT pins forever (engine never expires pinned uids).
FP: pin is unused on PUT; last global copy of a uid is frozen (exp=inf)
until a GET hit sets calculate_ttl, or a second copy appears and the
frozen lease is set to now + transfer RTT (author expire_immediate).
"""
from __future__ import annotations

import heapq
from typing import Dict, Set, Tuple

import numpy as np

from src.dedup.chunk_store import ChunkStore


class RegionChunkState:
    def __init__(self, n_unique: int):
        self.ref = np.zeros(n_unique, dtype=np.uint32)
        self.pinned = np.zeros(n_unique, dtype=np.uint32)
        self.cur_bytes = 0.0
        self.last_t = 0.0
        self.gbh = 0.0
        # chunk_tevict: per-uid lease (NaN = none, inf = immortal / freeze)
        self.chunk_exp: np.ndarray | None = None
        self.chunk_gen: np.ndarray | None = None
        self.pending_uids: Set[int] = set()

    def enable_chunk_clock(self) -> None:
        n = int(len(self.ref))
        self.chunk_exp = np.full(n, np.nan, dtype=np.float64)
        self.chunk_gen = np.zeros(n, dtype=np.uint32)


def settle(state: RegionChunkState, now_s: float, gb: float) -> None:
    if now_s > state.last_t:
        state.gbh += (state.cur_bytes / gb) * ((now_s - state.last_t) / 3600.0)
        state.last_t = now_s


def holds_uids(state: RegionChunkState, uids: np.ndarray) -> np.ndarray:
    return (state.pinned[uids] > 0) | (state.ref[uids] > 0)


def ensure_present(
    store: ChunkStore,
    state: RegionChunkState,
    uids: np.ndarray,
    pin: bool = False,
    bill: bool = True,
) -> int:
    """Make uids present in this region. Bill only bytes that were absent.

    pin=True: FB immortal (pinned stays until never). Timed copies use ref=1.
    """
    if len(uids) == 0:
        return 0
    fresh = uids[(state.pinned[uids] == 0) & (state.ref[uids] == 0)]
    if pin:
        state.pinned[uids] = np.maximum(state.pinned[uids], 1)
    else:
        state.ref[uids] = np.maximum(state.ref[uids], 1)
    nb = int(store.lens_u[fresh].sum())
    if bill:
        state.cur_bytes += nb
    return nb


def set_chunk_lease(state: RegionChunkState, uids: np.ndarray, exp: float, heap: list) -> int:
    """Assign a timed lease to non-pinned uids. Pinned (FB) copies stay immortal."""
    if state.chunk_exp is None or len(uids) == 0:
        return 0
    timed = uids[state.pinned[uids] == 0]
    if len(timed) == 0:
        return 0
    state.chunk_gen[timed] += 1
    state.chunk_exp[timed] = exp
    gens = state.chunk_gen[timed]
    for i in range(len(timed)):
        heapq.heappush(heap, (float(exp), int(timed[i]), int(gens[i])))
    return int(len(timed))


def expire_chunk_lease(
    store: ChunkStore,
    state: RegionChunkState,
    uid: int,
    bill: bool = True,
) -> bool:
    """Drop the timed copy of one uid. FB pin is never dropped here."""
    if state.pinned[uid] > 0:
        return False
    if state.ref[uid] == 0:
        return False
    state.ref[uid] = 0
    if state.chunk_exp is not None:
        state.chunk_exp[uid] = np.nan
    if bill:
        state.cur_bytes -= int(store.lens_u[uid])
    return True


def lease_covers(
    state: RegionChunkState,
    uids: np.ndarray,
    now_s: float,
    ttl_s: float,
) -> np.ndarray:
    """Author: now + T_evict(src→dst) must not exceed the source copy's expiry.

    Chunk translation: the uid's lease in that region, not the object replica.
    FB pin and FP last-copy freeze (exp=inf) are immortal, like ttl == -1.
    """
    if len(uids) == 0:
        return np.zeros(0, dtype=bool)
    pin = state.pinned[uids] > 0
    if state.chunk_exp is None:
        return pin
    exp = state.chunk_exp[uids]
    immortal = pin | np.isinf(exp)
    return immortal | (np.isfinite(exp) & (exp >= (now_s + ttl_s)))


def uid_holder_count(states: Dict[str, RegionChunkState], uid: int) -> int:
    n = 0
    for s in states.values():
        if s.pinned[uid] > 0 or s.ref[uid] > 0:
            n += 1
    return n


def cheapest_src_for_uids(
    states: Dict[str, RegionChunkState],
    dst: str,
    uids: np.ndarray,
    keep: list,
    price_fn,
) -> Tuple[np.ndarray, np.ndarray]:
    """For each missing uid, cheapest holder region name and unit cost.

    Returns (src_names object array, unit_cost). src_names[i] is None if none.
    """
    n = len(uids)
    best_c = np.full(n, np.inf)
    best_l = np.full(n, np.inf)
    best_src = np.empty(n, dtype=object)
    others = [r for r in keep if r != dst]
    for r in others:
        sr = states[r]
        has = holds_uids(sr, uids)
        if not np.any(has):
            continue
        cost, lat = price_fn(r, dst)
        c = float(cost)
        l = float(lat)
        better = has & ((c < best_c) | ((c == best_c) & (l < best_l)))
        best_c[better] = c
        best_l[better] = l
        best_src[better] = r
    return best_src, best_c
