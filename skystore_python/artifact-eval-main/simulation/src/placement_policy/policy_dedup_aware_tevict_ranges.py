"""
DedupAwareTevictRanges — independent simulation policy (OCP).

Mirrors prototype DedupAwareTevict design:
  - Does NOT subclass / modify author TevictRangesV2.
  - Copies the full adaptive-TTL suite (histogram, ranges, get_tevict, …)
    so this file is a standalone strategy.
  - Adds one extra decision layer: HOT vs DEDUP storage format.

    Phase 1: adaptive TTL (same logic as TevictRangesV2 / SkyStore)
    Phase 2: if adaptive_ttl > 0:
        tau_effective = (1 - L) * T_even
        time_to_next <= tau_effective -> HOT  (return adaptive_ttl, full replica)
        time_to_next >  tau_effective -> DEDUP (return 0, chunk refs only)

Author file policy_tevict_ranges_new.py is left untouched.
"""
from __future__ import annotations

import datetime
import math
from collections import OrderedDict
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import networkx as nx

from src.dedup.chunk_store import ChunkStore
from src.model.config import Config
from src.model.object import LogicalObject
from src.model.request import Request
from src.placement_policy.policy import PlacementPolicy
from src.utils.helpers import get_avg_network_cost, get_min_network_cost


class ObjFormat:
    HOT = "hot"
    DEDUP = "dedup"


class PolicyDedupAwareTevictRanges(PlacementPolicy):
    """Independent policy: copied TevictRanges TTL + dedup-aware format."""

    def __init__(
        self,
        config: Config,
        total_graph: nx.DiGraph,
        objects: Dict[str, LogicalObject],
        regionManager=None,
        chunk_store: Optional[ChunkStore] = None,
    ) -> None:
        # ---- copied TevictRangesV2 state ----
        self.config = config
        self.window_size = self.config.window_size
        self.total_graph = total_graph
        self.objects = objects if objects is not None else {}
        self.region_manager = regionManager

        self.avgNetworkCost = get_avg_network_cost(self.total_graph)
        self.minNetworkCost = get_min_network_cost(self.total_graph)

        self.past_requests = {}
        self.hist = {}
        self.last_hist = {}
        self.num_requests = {}

        self.next_past_requests = {}
        self.next_hist = {}
        self.next_last_hist = {}
        self.next_num_requests = {}

        if self.window_size != -1:
            self.last_updated = None
        else:
            self.last_updated = datetime.datetime.fromtimestamp(0)

        self.ttls = {}
        self.time_passed = None
        self.seen_days = {}

        self.remove_immediately = {}
        self.regions = list(self.config.regions or [])
        self.region_pairs_ttl = {}
        for region in self.regions:
            for region2 in self.regions:
                self.region_pairs_ttl[(region, region2)] = []

        # ---- dedup-aware extension (prototype parity) ----
        self.chunk_store = chunk_store
        self._obj_format: Dict[str, str] = {}
        self._region_L: Dict[str, float] = {}
        self.stats_hot = 0
        self.stats_dedup = 0

        super().__init__()

    # ------------------------------------------------------------------
    # Dedup-aware helpers (prototype Phase-2)
    # ------------------------------------------------------------------

    def set_chunk_store(self, store: ChunkStore) -> None:
        self.chunk_store = store

    def set_region_L(self, region: str, L: float) -> None:
        self._region_L[region] = max(0.0, min(1.0, float(L)))

    def get_region_L(self, region: str) -> float:
        return float(self._region_L.get(region, 0.0))

    def get_obj_format(self, key: str) -> str:
        return self._obj_format.get(key, ObjFormat.HOT)

    def compute_teven_seconds(self, src: str, dst: str) -> float:
        net = float(self.total_graph[src][dst]["cost"])
        storage = float(self.total_graph.nodes[dst]["priceStorage"])
        return net / (storage * 3) * 86400.0

    def time_to_next_seconds(self, req: Request) -> Optional[float]:
        ttn = req.next_access_same_reg_timestamp
        if ttn is not None and ttn != datetime.datetime.max:
            return max((ttn - req.timestamp).total_seconds(), 0.0)
        ttn2 = req.next_access_timestamp
        if ttn2 is not None and ttn2 != datetime.datetime.max:
            return max((ttn2 - req.timestamp).total_seconds(), 0.0)
        return None

    def decide_format(
        self,
        req: Request,
        src: str,
        dst: str,
        L: Optional[float] = None,
    ) -> str:
        if L is None:
            L = self.get_region_L(dst)
        teven = self.compute_teven_seconds(src, dst)
        tau = (1.0 - L) * teven
        ttn = self.time_to_next_seconds(req)
        if ttn is None or ttn <= 0:
            # prototype: unknown next access -> no HOT keep
            fmt = ObjFormat.DEDUP
        elif ttn <= tau:
            fmt = ObjFormat.HOT
        else:
            fmt = ObjFormat.DEDUP
        self._obj_format[req.obj_key] = fmt
        if fmt == ObjFormat.HOT:
            self.stats_hot += 1
        else:
            self.stats_dedup += 1
        return fmt

    def resolve_cache(
        self,
        req: Request,
        src: str,
        dst: str,
        L: Optional[float] = None,
    ) -> Tuple[float, str]:
        """
        Prototype get_ttl parity:
          Phase1 adaptive TTL; if <=0 -> (0, DEDUP)
          Phase2 HOT -> (adaptive_ttl, HOT); DEDUP -> (0, DEDUP)
        """
        adaptive_ttl = float(self.get_tevict(src, dst, req.timestamp))
        if adaptive_ttl <= 0:
            self._obj_format[req.obj_key] = ObjFormat.DEDUP
            self.stats_dedup += 1
            return 0.0, ObjFormat.DEDUP
        fmt = self.decide_format(req, src, dst, L=L)
        if fmt == ObjFormat.DEDUP:
            return 0.0, fmt
        return adaptive_ttl, fmt

    def name(self) -> str:
        return "dedup_aware_tevict_ranges"

    # ------------------------------------------------------------------
    # Copied TevictRangesV2 TTL suite (independent strategy body)
    # ------------------------------------------------------------------

    def is_next_window(self, old_datetime, new_datetime):
        time_difference = new_datetime - old_datetime
        return time_difference > timedelta(hours=self.window_size)

    def is_large_time_gap(self, old_datetime, new_datetime):
        time_difference = abs(new_datetime - old_datetime)
        return time_difference > timedelta(hours=self.window_size * 2)

    def round_to_next_hour(self, dt: datetime.datetime):
        if dt.minute != 0 or dt.second != 0 or dt.microsecond != 0:
            dt += timedelta(hours=1)
            dt = dt.replace(minute=0, second=0, microsecond=0)
        else:
            dt += timedelta(hours=1)
        return dt

    def round_to_next_hour_seconds(self, dt: int, ranges):
        return (dt // 3600 + 1) * 3600

    def find_next_index_in_range(self, tnext: int, ranges):
        tnext_in_hours = tnext / 3600
        for i in range(0, len(ranges), 1):
            if tnext_in_hours <= ranges[i]:
                return i
        return len(ranges) - 1

    def update_past_requests(self, request: Request, place_region: str):
        KB = 1024
        MB = 1024 * KB
        GB = 1024 * MB
        SEC = 1000
        MIN = 60 * SEC
        HOUR = 60 * MIN
        MIN_IN_HOUR = 0.01666
        s = MIN_IN_HOUR * HOUR
        ranges_2per = [0] + [s * (1 + (2 / 100)) ** i for i in range(700)]
        ranges = [x / HOUR for x in ranges_2per]

        if self.window_size != -1:
            if self.last_updated is None or self.is_large_time_gap(
                self.last_updated, request.timestamp
            ):
                self.last_updated = request.timestamp.replace(
                    minute=0, second=0, microsecond=0
                )
                self.past_requests = {}
                self.hist = {}
                self.num_requests = {}
                self.last_hist = {}
                self.next_last_hist = {}
                self.next_past_requests = {}
                self.next_hist = {}
                self.next_num_requests = {}
            elif self.is_next_window(self.last_updated, request.timestamp):
                self.last_updated = self.last_updated + timedelta(
                    hours=self.window_size
                )
                self.past_requests = self.next_past_requests
                self.hist = self.next_hist
                self.last_hist = self.next_last_hist
                self.num_requests = self.next_num_requests
                self.next_past_requests = {}
                self.next_hist = {}
                self.next_num_requests = {}
                self.next_last_hist = {}

        self.next_num_requests[place_region] = (
            self.next_num_requests.get(place_region, 0) + 1
        )
        if place_region not in self.next_past_requests:
            self.next_past_requests[place_region] = {}
        if request.obj_key in self.next_past_requests[place_region]:
            req_to_sec = (
                request.timestamp - datetime.datetime.fromtimestamp(0)
            ).total_seconds()
            tnext = (
                req_to_sec - self.next_past_requests[place_region][request.obj_key][-1]
            )
            if tnext == 0:
                tnext = 1

            curentWindow = int(
                math.ceil(
                    (
                        self.round_to_next_hour_seconds(
                            self.next_past_requests[place_region][request.obj_key][-1],
                            ranges,
                        )
                        - self.next_past_requests[place_region][request.obj_key][-1]
                    )
                    / 3600
                )
            )

            if (
                place_region in self.next_last_hist
                and curentWindow in self.next_last_hist[place_region]
            ):
                self.next_last_hist[place_region][curentWindow] -= request.size / GB

            next_index_in_range = self.find_next_index_in_range(tnext, ranges)
            if next_index_in_range not in self.next_hist[place_region]:
                self.next_hist[place_region][next_index_in_range] = 0
            self.next_hist[place_region][next_index_in_range] += request.size / GB
            self.next_past_requests[place_region][request.obj_key].append(
                (request.timestamp - datetime.datetime.fromtimestamp(0)).total_seconds()
            )

            if place_region not in self.next_last_hist:
                self.next_last_hist[place_region] = {}
            tendWindow = int(
                math.ceil(
                    (
                        self.round_to_next_hour(request.timestamp) - request.timestamp
                    ).total_seconds()
                    / 3600
                )
            )
            self.next_last_hist[place_region][tendWindow] = (
                self.next_last_hist[place_region].get(tendWindow, 0) + request.size / GB
            )
        else:
            if place_region not in self.next_hist:
                self.next_hist[place_region] = {}
            self.next_past_requests[place_region][request.obj_key] = []
            self.next_past_requests[place_region][request.obj_key].append(
                (request.timestamp - datetime.datetime.fromtimestamp(0)).total_seconds()
            )
            if place_region not in self.next_last_hist:
                self.next_last_hist[place_region] = {}
            tendWindow = int(
                math.ceil(
                    (
                        self.round_to_next_hour(request.timestamp) - request.timestamp
                    ).total_seconds()
                    / 3600
                )
            )
            self.next_last_hist[place_region][tendWindow] = (
                self.next_last_hist[place_region].get(tendWindow, 0) + request.size / GB
            )

        self.time_passed = request.timestamp

    def calc_evict_cost(self, region, teven_hours, net_cost):
        if self.window_size == -1:
            if region not in self.next_hist or region not in self.next_last_hist:
                return -1
            X = self.next_hist[region]
            last_X = self.next_last_hist[region]
        else:
            if region not in self.hist or region not in self.last_hist:
                return -1
            X = self.hist[region]
            last_X = self.last_hist[region]

        if teven_hours == 0:
            storage_cost_hour = self.avgNetworkCost / teven_hours
        else:
            storage_cost_hour = net_cost / teven_hours

        KB = 1024
        MB = 1024 * KB
        GB = 1024 * MB
        SEC = 1000
        MIN = 60 * SEC
        HOUR = 60 * MIN
        MIN_IN_HOUR = 0.01666
        s = MIN_IN_HOUR * HOUR
        ranges_2per = [0] + [s * (1 + (2 / 100)) ** i for i in range(700)]
        ranges = [x / HOUR for x in ranges_2per]

        cost_hist = {}
        ret = 0
        for c in range(len(ranges)):
            c = int(c)
            if c not in cost_hist:
                cost_hist[c] = 0
            tevict = ranges[c]
            for i in range(len(ranges)):
                if (X.get(i, 0)) == 0:
                    continue
                if tevict == 0:
                    cost_hist[c] += float(X.get(i, 0)) * (net_cost)
                    continue
                if ranges[i] <= tevict:
                    cost_hist[c] += (
                        float(X.get(i, 0))
                        * ((ranges[i] + int(i != 0) * (ranges[i - 1])) / 2)
                        * storage_cost_hour
                    )
                else:
                    cost_hist[c] += float(X.get(i, 0)) * (
                        (tevict * storage_cost_hour) + net_cost
                    )
            for i in range(len(ranges)):
                if (last_X.get(i, 0)) == 0:
                    continue
                cost_hist[c] += float(last_X.get(i, 0)) * (tevict * storage_cost_hour)

            if cost_hist[c] <= cost_hist[ret]:
                ret = c

        Y = OrderedDict()
        for i in sorted(X.keys()):
            Y[i] = X.get(i, 0)
        return ranges[ret]

    def get_tevict(self, src, dst, timestamp):
        if dst in self.seen_days and timestamp < self.seen_days[dst][-1] + timedelta(
            hours=12
        ):
            return self.region_pairs_ttl[(src, dst)][-1]
        for region in self.regions:
            for region2 in self.regions:
                if region != region2:
                    net_cost = self.total_graph[region][region2]["cost"]
                    storage = self.total_graph.nodes[region2]["priceStorage"]
                    teven = net_cost / storage * 60 * 60 * 24
                    calculated_cost = self.calc_evict_cost(
                        region2, teven / 3600, net_cost
                    )
                    self.region_pairs_ttl[(region, region2)].append(
                        self.find_min(calculated_cost, teven, region2, timestamp)
                    )
        return self.region_pairs_ttl[(src, dst)][-1]

    def find_min(self, y, teven, region, timestamp):
        noPastData = False
        if self.window_size == -1:
            if region not in self.next_num_requests:
                noPastData = True
                ttl = teven / 3600 / 2
            else:
                numRequests = self.next_num_requests[region]
        else:
            if region not in self.num_requests:
                noPastData = True
                ttl = teven / 3600 / 2
            else:
                numRequests = self.num_requests[region]

        if not noPastData:
            if y == -1 or self.last_updated is None or numRequests < 1000:
                ttl = teven / 3600 / 2
            else:
                ttl = y

        if region not in self.seen_days:
            self.seen_days[region] = []
            self.ttls[region] = []
        if len(self.seen_days[region]) == 0 or timestamp >= self.seen_days[region][
            -1
        ] + timedelta(hours=12):
            self.seen_days[region].append(timestamp)
            self.ttls[region].append(ttl)

        return ttl * 3600

    def place(self, req: Request, config: Config = None) -> List[str]:
        key, issue_region, op = req.obj_key, req.issue_region, req.op
        place_regions = []
        if op == "write":
            place_regions.append(issue_region)
        else:
            if key not in self.objects or issue_region not in self.objects[key].physical_objects:
                place_regions.append(issue_region)
        return list(set(place_regions))
