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

class PolicyHyStoreECV2(TevictRangesV2):
    """
    HyStore-EC V2: Optimized Hybrid Storage with Erasure Coding
    
    Performance improvements over V1:
    - Removed O(n²) state traversal
    - Incremental counter updates
    - Reduced logging overhead
    """
    
    def __init__(
        self,
        config: Config,
        total_graph: nx.DiGraph,
        objects: Dict[str, LogicalObject],
        regionManager,
    ) -> None:
        super().__init__(config, total_graph, objects, regionManager)
        
        # State tracking: obj_key -> (state, access_count, last_transition_time)
        self.object_states: Dict[str, tuple] = {}
        
        # EC cost parameters
        self.rf_factor = 3.0      # Replication Factor
        self.ec_factor = 1.5      # EC overhead (LRC 6+3)
        
        # Transition thresholds
        self.access_threshold = 2
        self.time_window_hours = 24
        
        # Incremental statistics (avoid O(n) traversal)
        self.hot_count = 0
        self.cold_count = 0
        self.transitions_hot_to_cold = 0
        self.transitions_cold_to_hot = 0
        
        # Request counter for periodic logging
        self.request_count = 0
        
        logger.info(f"[HyStore-EC V2] Initialized: RF={self.rf_factor}, EC={self.ec_factor}")

    def place(self, request: Request, config: Config) -> List[str]:
        """
        Main placement logic with HOT/COLD state management
        """
        key = request.obj_key
        self.request_count += 1
        
        # Get or initialize state
        if key in self.object_states:
            current_state, access_count, last_transition = self.object_states[key]
        else:
            # New object: start HOT
            current_state = ObjectState.HOT
            access_count = 0
            last_transition = request.timestamp
            self.hot_count += 1
        
        # Update access count
        access_count += 1
        
        # Track state before potential change
        old_state = current_state
        
        # Decision logic
        if request.op in ["PUT", "REST.PUT.OBJECT"]:
            # Writes always go to HOT
            if current_state == ObjectState.COLD:
                current_state = ObjectState.HOT
                self.transitions_cold_to_hot += 1
                # Update counters
                self.cold_count -= 1
                self.hot_count += 1
                access_count = 1
                last_transition = request.timestamp
            
        elif request.op in ["GET", "REST.GET.OBJECT"]:
            time_since_transition = (request.timestamp - last_transition).total_seconds() / 3600
            
            if current_state == ObjectState.HOT:
                # Check transition to COLD
                if time_since_transition >= self.time_window_hours:
                    if access_count <= self.access_threshold:
                        current_state = ObjectState.COLD
                        self.transitions_hot_to_cold += 1
                        # Update counters
                        self.hot_count -= 1
                        self.cold_count += 1
                    
                    # Reset for next window
                    access_count = 1
                    last_transition = request.timestamp
            
            elif current_state == ObjectState.COLD:
                # Promote on read
                current_state = ObjectState.HOT
                self.transitions_cold_to_hot += 1
                # Update counters
                self.cold_count -= 1
                self.hot_count += 1
                access_count = 1
                last_transition = request.timestamp
        
        # Update state
        self.object_states[key] = (current_state, access_count, last_transition)
        
        # Periodic logging (every 10000 requests to avoid I/O overhead)
        if self.request_count % 10000 == 0:
            logger.info(
                f"[HyStore-EC V2] Stats @ {self.request_count} reqs: "
                f"HOT={self.hot_count}, COLD={self.cold_count}, "
                f"H→C={self.transitions_hot_to_cold}, C→H={self.transitions_cold_to_hot}"
            )
        
        # Return placement based on final state
        if current_state == ObjectState.HOT:
            return self.get_replica_placement(request)
        else:
            return self.get_ec_placement(request)

    def get_replica_placement(self, request):
        """
        HOT state: Full replication via parent TevictRanges logic
        """
        return super().place(request, self.config)

    def get_ec_placement(self, request):
        """
        COLD state: Single cheapest region (simulates EC)
        In FP mode: 3x (HOT) -> 1x (COLD) = 66% cost reduction
        """
        cheapest_region = min(
            self.regions,
            key=lambda r: self.total_graph.nodes[r].get("priceStorage", float('inf'))
        )
        return [cheapest_region]

    def update_past_requests(self, request, place_region):
        """
        Track request history for parent TevictRanges logic
        """
        super().update_past_requests(request, place_region)
