"""Per-region unique-chunk presence (refcount + pin) for cost accounting."""
from __future__ import annotations

from typing import Dict, Set, Tuple

import numpy as np

from src.dedup.chunk_store import ChunkStore


class RegionChunkState:
    def __init__(self, n_unique: int):
        self.ref = np.zeros(n_unique, dtype=np.uint32)
        self.pinned = np.zeros(n_unique, dtype=bool)
        self.pinned_objs: Set[str] = set()
        self.cached: Dict[str, Tuple[float, float, str]] = {}
        # key -> (expiry_s, ttl_s, format)  format in {"hot","dedup"}
        self.cur_bytes = 0.0
        self.last_t = 0.0
        self.gbh = 0.0
        # cumulative locality stats
        self.local_hits = 0
        self.total_checks = 0

    @property
    def L(self) -> float:
        if self.total_checks <= 0:
            return 0.0
        return float(self.local_hits) / float(self.total_checks)


def settle(state: RegionChunkState, now_s: float, gb: float) -> None:
    if now_s > state.last_t:
        state.gbh += (state.cur_bytes / gb) * ((now_s - state.last_t) / 3600.0)
        state.last_t = now_s


def missing_bytes(store: ChunkStore, state: RegionChunkState, key: str) -> int:
    uids = store.uids(key)
    if len(uids) == 0:
        return 0
    present = state.pinned[uids] | (state.ref[uids] > 0)
    return int(store.lens_u[uids[~present]].sum())


def locality_for_object(store: ChunkStore, state: RegionChunkState, key: str) -> float:
    uids = store.uids(key)
    if len(uids) == 0:
        return 1.0
    present = state.pinned[uids] | (state.ref[uids] > 0)
    hits = int(present.sum())
    state.local_hits += hits
    state.total_checks += int(len(uids))
    return hits / float(len(uids))


def add_chunks(
    store: ChunkStore,
    state: RegionChunkState,
    key: str,
    pin: bool = False,
    bill: bool = True,
) -> int:
    """Mark chunks present; optionally add fresh unique bytes to the bill.

    bill=False: update pin/ref only (origin copy is billed as logical_size).
    """
    uids = store.uids(key)
    if len(uids) == 0:
        return 0
    if pin:
        fresh = uids[~state.pinned[uids] & (state.ref[uids] == 0)]
        state.pinned[uids] = True
    else:
        fresh = uids[~state.pinned[uids] & (state.ref[uids] == 0)]
        state.ref[uids] += 1
    nb = int(store.lens_u[fresh].sum())
    if bill:
        state.cur_bytes += nb
    return nb


def drop_dedup_refs(store: ChunkStore, state: RegionChunkState, key: str) -> None:
    uids = store.uids(key)
    if len(uids) == 0:
        return
    state.ref[uids] -= 1
    gone = uids[(state.ref[uids] == 0) & ~state.pinned[uids]]
    state.cur_bytes -= int(store.lens_u[gone].sum())
