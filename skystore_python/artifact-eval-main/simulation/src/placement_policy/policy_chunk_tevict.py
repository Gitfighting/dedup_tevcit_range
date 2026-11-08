"""ChunkTevict — TevictRanges interval histogram, clock on unique chunks.

Author intent kept:
  dst Tnext/Last learns when this site will ask again;
  a copy's TTL trades storage against paying edge N on the next miss;
    GET miss from A to B ⇒ pay cheapest holder; TTL = min live get_tevict
    (source uid lease must cover now+T, else that edge is not a real N);
    GET hit refresh = same min among live holders; none ⇒ median T_even (/3).

Adaptation (unit = unique chunk in a region):
  transfer still picks the cheapest holder per missing uid;
  TTL is min_live get_tevict over holders of that uid (not the paid edge);
  hit chunks refresh independently; a shared uid has one clock in a region;
  histogram size(o) = sum of this object's unique chunk lengths (hit+miss);
  PUT has no transfer; FB pin is immortal.
  FP PUT of a *new* local copy: no other holder ⇒ author's median T_even
  (same N/(S·3)·86400 as calculate_ttl with no other replica);
  other holders exist ⇒ same min_live get_tevict as GET miss.
  Already-present chunks are not refreshed on PUT (author skips if already stored).
  FP last copy: freeze (author expire_immediate / ttl=-1); second copy sets
  expiry to now + transfer RTT (author transfer_time_millis/1000).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import networkx as nx

from src.dedup.chunk_store import ChunkStore
from src.model.config import Config
from src.model.object import LogicalObject
from src.model.request import Request
from src.placement_policy.policy import PlacementPolicy
from src.placement_policy.policy_tevict_ranges_new import TevictRangesV2
from src.utils.helpers import get_median_network_cost


class PolicyChunkTevict(PlacementPolicy):
    def __init__(
        self,
        config: Config,
        total_graph: nx.DiGraph,
        objects: Dict[str, LogicalObject],
        regionManager=None,
        chunk_store: Optional[ChunkStore] = None,
    ) -> None:
        self.config = config
        self.total_graph = total_graph
        self.chunk_store = chunk_store
        self._ttl = TevictRangesV2(
            config=config,
            total_graph=total_graph,
            objects=objects if objects is not None else {},
            regionManager=regionManager,
        )
        super().__init__()

    def set_chunk_store(self, store: ChunkStore) -> None:
        self.chunk_store = store

    def name(self) -> str:
        return "chunk_tevict"

    def place(self, req: Request, config: Config = None) -> List[str]:
        return self._ttl.place(req, config)

    def _hist_request(self, request: Request) -> Request:
        if self.chunk_store is None or not self.chunk_store.has(request.obj_key):
            return request
        w = float(self.chunk_store.unique_bytes(request.obj_key))
        if w <= 0:
            w = float(request.size)
        return request.model_copy(update={"size": w})

    def update_past_requests(self, request: Request, place_region: str):
        return self._ttl.update_past_requests(self._hist_request(request), place_region)

    def get_tevict(self, src, dst, timestamp) -> float:
        """Author TevictRangesV2.get_tevict: seconds (find_min returns ttl_hours * 3600)."""
        return float(self._ttl.get_tevict(src, dst, timestamp))

    def get_tevict_seconds(self, src, dst, timestamp) -> float:
        return self.get_tevict(src, dst, timestamp)

    def median_teven_seconds(self, dst: str) -> float:
        net = float(get_median_network_cost(self.total_graph) or 0.0)
        storage = float(self.total_graph.nodes[dst]["priceStorage"])
        if storage <= 0 or net <= 0:
            return 0.0
        return net / (storage * 3) * 86400.0
