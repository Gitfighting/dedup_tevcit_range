"""TevictRangesDedup — author TevictRangesV2 TTL + chunk-level billing.

Ablation to isolate dedup (not the HOT/DEDUP threshold in DedupAware):

  - TTL / placement decisions: delegated to TevictRangesV2
    (policy_tevict_ranges_new.py, untouched).
  - Origin PUT: full logical object (same storage as SkyStore primary).
  - Remote cache (ttl > 0): unique missing chunks + manifest.
    ttl == 0 → pay missing-chunk GET, do not cache.

Author file is not subclassed or edited (OCP).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import networkx as nx

from src.dedup.chunk_store import ChunkStore
from src.model.config import Config
from src.model.object import LogicalObject
from src.model.request import Request
from src.placement_policy.policy import PlacementPolicy
from src.placement_policy.policy_dedup_aware_tevict_ranges import ObjFormat
from src.placement_policy.policy_tevict_ranges_new import TevictRangesV2


class PolicyTevictRangesDedup(PlacementPolicy):
    """Compose TevictRangesV2 with the chunk-store cost component."""

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
        self.stats_keep = 0
        self.stats_skip = 0
        super().__init__()

    def set_chunk_store(self, store: ChunkStore) -> None:
        self.chunk_store = store

    def name(self) -> str:
        return "tevict_ranges_dedup"

    def place(self, req: Request, config: Config = None) -> List[str]:
        return self._ttl.place(req, config)

    def update_past_requests(self, request: Request, place_region: str):
        return self._ttl.update_past_requests(request, place_region)

    def get_tevict(self, src, dst, timestamp):
        return self._ttl.get_tevict(src, dst, timestamp)

    def resolve_cache(
        self,
        req: Request,
        src: str,
        dst: str,
        L: Optional[float] = None,
    ) -> Tuple[float, str]:
        """SkyStore adaptive TTL, always DEDUP format (chunk accounting)."""
        ttl = float(self._ttl.get_tevict(src, dst, req.timestamp))
        if ttl <= 0:
            self.stats_skip += 1
            return 0.0, ObjFormat.DEDUP
        self.stats_keep += 1
        return ttl, ObjFormat.DEDUP
