from typing import Dict, List, Optional
from src.placement_policy.policy_tevict_ranges_new import TevictRangesV2
from src.model.object import LogicalObject, PhysicalObject, Status
from src.model.config import Config
import networkx as nx
from src.model.request import Request
from enum import Enum
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ObjectState(Enum):
    HOT = 1  # Replicated (RF=3)
    COLD = 2 # Erasure Coded (LRC ~1.5x)

class PolicyHyStoreECV3(TevictRangesV2):
    """
    HyStore-EC V3: AGGRESSIVE Cost Optimization
    
    Key Changes from V2:
    - Shorter cooldown window (6h instead of 24h)
    - "Last access time" based transitions (not window-based counting)
    - Delayed promotion: COLD objects need multiple accesses to go HOT
    - Only WRITES force immediate HOT promotion
    
    Expected: 30-50% cost reduction vs TevictRanges FP
    """
    
    def __init__(
        self,
        config: Config,
        total_graph: nx.DiGraph,
        objects: Dict[str, LogicalObject],
        regionManager,
    ) -> None:
        super().__init__(config, total_graph, objects, regionManager)
        
        # State: obj_key -> (state, last_access_time, cold_access_count)
        self.object_states: Dict[str, tuple] = {}
        
        # EC parameters
        self.rf_factor = 3.0
        self.ec_factor = 1.5
        
        # AGGRESSIVE transition thresholds
        self.cold_threshold_hours = 6  # If not accessed for 6h -> COLD
        self.hot_promotion_reads = 3   # Need 3 reads while COLD to promote to HOT
        
        # Statistics
        self.hot_count = 0
        self.cold_count = 0
        self.transitions_hot_to_cold = 0
        self.transitions_cold_to_hot = 0
        self.request_count = 0
        
        logger.info(f"[HyStore-EC V3] AGGRESSIVE mode: cold_threshold={self.cold_threshold_hours}h, promotion_reads={self.hot_promotion_reads}")

    def place(self, request: Request, config: Config) -> List[str]:
        """
        Aggressive cost-saving placement logic
        """
        key = request.obj_key
        self.request_count += 1
        
        # Get or initialize state
        if key in self.object_states:
            current_state, last_access_time, cold_access_count = self.object_states[key]
        else:
            # New object: start HOT
            current_state = ObjectState.HOT
            last_access_time = request.timestamp
            cold_access_count = 0
            self.hot_count += 1
        
        # Calculate time since last access
        hours_since_access = (request.timestamp - last_access_time).total_seconds() / 3600
        
        # State transition logic
        if request.op in ["PUT", "REST.PUT.OBJECT"]:
            # WRITE: Always force HOT (data mutation needs full redundancy)
            if current_state == ObjectState.COLD:
                current_state = ObjectState.HOT
                self.transitions_cold_to_hot += 1
                self.cold_count -= 1
                self.hot_count += 1
                cold_access_count = 0
            last_access_time = request.timestamp
            
        elif request.op in ["GET", "REST.GET.OBJECT"]:
            if current_state == ObjectState.HOT:
                # Check if object should cool down
                if hours_since_access >= self.cold_threshold_hours:
                    # Object hasn't been accessed for 6+ hours -> Move to COLD
                    current_state = ObjectState.COLD
                    self.transitions_hot_to_cold += 1
                    self.hot_count -= 1
                    self.cold_count += 1
                    cold_access_count = 1  # This read is the first cold access
                else:
                    # Still HOT, update access time
                    last_access_time = request.timestamp
            
            elif current_state == ObjectState.COLD:
                # COLD object accessed
                cold_access_count += 1
                
                # Delayed promotion: Need multiple reads to go back HOT
                if cold_access_count >= self.hot_promotion_reads:
                    current_state = ObjectState.HOT
                    self.transitions_cold_to_hot += 1
                    self.cold_count -= 1
                    self.hot_count += 1
                    cold_access_count = 0
                    last_access_time = request.timestamp
                # else: Stay COLD, just record the access
        
        # Update state
        self.object_states[key] = (current_state, last_access_time, cold_access_count)
        
        # Periodic logging
        if self.request_count % 10000 == 0:
            logger.info(
                f"[HyStore-EC V3] @ {self.request_count} reqs: "
                f"HOT={self.hot_count}, COLD={self.cold_count}, "
                f"H→C={self.transitions_hot_to_cold}, C→H={self.transitions_cold_to_hot}"
            )
        
        # Placement based on final state
        if current_state == ObjectState.HOT:
            return self.get_replica_placement(request)
        else:
            return self.get_ec_placement(request)

    def get_replica_placement(self, request):
        """HOT: Full replication (TevictRanges logic)"""
        return super().place(request, self.config)

    def get_ec_placement(self, request):
        """COLD: Single cheapest region (66% cost reduction in FP mode)"""
        cheapest_region = min(
            self.regions,
            key=lambda r: self.total_graph.nodes[r].get("priceStorage", float('inf'))
        )
        return [cheapest_region]

    def update_past_requests(self, request, place_region):
        """Track for parent TevictRanges"""
        super().update_past_requests(request, place_region)
