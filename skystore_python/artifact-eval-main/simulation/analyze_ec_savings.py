
import os
import sys
import datetime
import math
import argparse
from typing import Dict, List, Tuple
from collections import defaultdict
from pydantic import BaseModel

# Add local source to path
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

# Mocking or Importing necessary components
try:
    from src.model.config import Config
    from src.model.request import Request
    from src.model.region_mgmt import RegionManager
    from src.placement_policy.policy_tevict_ranges_new import TevictRangesV2
    from src.utils.definitions import GB
except ImportError:
    # If imports fail (e.g. running outside of proper context), defining minimal mocks
    print("Warning: Could not import project modules. Using minimal mocks.")
    class Config:
        def __init__(self):
            self.window_size = -1
            self.regions = ["aws:us-east-1", "aws:us-west-2"]
    class Request(BaseModel):
        timestamp: datetime.datetime
        op: str
        issue_region: str
        obj_key: str
        size: float
    GB = 1024 * 1024 * 1024

import networkx as nx

def parse_trace_line(line: str) -> Request:
    # Format: timestamp,op,issue_region,obj_key,size,time_to_next_access,...
    parts = line.strip().split(',')
    
    # Timestamp is in relative seconds? Or MS? 
    # Sample: 0. Usually these are seconds or ms. 
    # Let's assume seconds for now as typical for these traces.
    ts = float(parts[0]) 
    dt = datetime.datetime.fromtimestamp(ts)
    
    op_str = parts[1].upper()
    op = "read" if op_str == "GET" or op_str == "REST.GET.OBJECT" or op_str == "READ" else "write"
    
    key = parts[3]
    size = float(parts[4])
    
    return Request(
        timestamp=dt,
        op=op,
        issue_region=parts[2],
        obj_key=key,
        size=size
    )

def main():
    parser = argparse.ArgumentParser(description="Estimate EC Savings")
    parser.add_argument("--trace", type=str, required=True, help="Path to trace file")
    # Using defaults for quick run
    args = parser.parse_args()

    # Setup Environment
    print(f"Loading trace: {args.trace}")
    
    # Minimal Graph
    g = nx.DiGraph()
    g.add_node("aws:us-east-1", priceStorage=0.023)
    g.add_node("aws:us-west-2", priceStorage=0.023)
    g.add_edge("aws:us-east-1", "aws:us-west-2", cost=0.02) # Transfer cost
    g.add_edge("aws:us-west-2", "aws:us-east-1", cost=0.02)

    config = Config(
        placement_policy="tevict_ranges",
        transfer_policy="cheapest",
        regions=["aws:us-east-1", "aws:us-west-2"],
        window_size=-1
    )
    # config.regions is already set in init if passed, or can be set here.
    # The init above sets it.
    
    # Policy
    policy = TevictRangesV2(config, g, {}, None)
    
    # Constants
    STORAGE_PRICE = 0.023 / (30 * 24 * 3600) * 3600 # per GB per hour? No, keeping unit consistent
    # Let's use hour as unit for ease, or second. 
    # Code uses: storage_cost_hour = netCost / tevenHours
    # Let's stick to the Policy's own cost units (which seem to be derivative of net_cost)
    
    # We will simulate "One Region Cache" scenario:
    # Source: Remote (Cost to fetch = X)
    # Cache: Local (Cost to store = Y per sec)
    # Binary: Keep or Evict
    # Tiered: Keep, EC, or Evict
    
    NET_COST = 0.02 # Cost to fetch on miss
    STORAGE_PER_GB_HOUR = 0.023 / (30 * 24) 
    
    # EC Parameters
    EC_STORAGE_FACTOR = 0.6  # 40% savings
    EC_ACCESS_FACTOR = 1.2   # 20% penalty on access (reconstruction/overhead)
    
    print("Starting Simulation...")
    
    req_count = 0
    total_binary_cost = 0.0
    total_ec_cost = 0.0
    
    # Histogram of reuse times (aggregated for analysis)
    # We can peek into policy.next_hist or policy.hist
    
    with open(args.trace, 'r') as f:
        header = f.readline() # Skip header
        for line in f:
            if not line.strip(): continue
            try:
                # Custom parsing based on file view (adjusting in next step if needed)
                req = parse_trace_line(line)
            except Exception:
                continue
                
            req_count += 1
            if req_count % 10000 == 0:
                print(f"Processed {req_count} requests...", end='\r')
            
            # Update Policy State
            # This updates the internal histograms
            policy.update_past_requests(req, "aws:us-west-2") # Assume we are caching in West-2
            
    print("\nSimulation Step 1 (Learning) Complete.")
    print("Analyzing Histograms to Estimate Costs...")
    
    # Now analyze the histograms in policy.next_hist (or hist)
    # The policy stores hist as: time_bucket_index -> GB sum
    
    # We essentially want to solve:
    # Cost_Binary = min_T ( Storage(0..T) + Miss(T..inf) )
    # Cost_EC = min_T1,T2 ( Storage_Hot(0..T1) + Storage_EC(T1..T2) + Miss(T2..inf) )
    
    # Merge histograms if multiple regions (we just used West-2)
    hist = policy.next_hist.get("aws:us-west-2", {})
    if not hist:
        hist = policy.next_last_hist.get("aws:us-west-2", {})
        
    if not hist:
        print("No history collected.")
        return

    # Convert buckets back to time
    # The policy uses 'ranges' array for buckets.
    # We need to access that.
    # Re-creating ranges as in the policy code
    SEC = 1000 # Wait, policy defines SEC=1000? 
    # Python code:
    # SEC = 1000
    # MIN = 60 * SEC 
    # ... this implies the unit is MILLISECONDS? 
    # BUT `req_to_sec` in update_past_requests uses `total_seconds()`.
    # Let's check `ranges` generation in the file view.
    # It says: ranges = [x / HOUR for x in ranges_2per] where ranges_2per uses SEC=1000.
    # If SEC=1000, then MIN=60000. HOUR=3600000.
    # ranges buckets are in HOURS?
    # `tnext = req_to_sec ...` (Seconds)
    # `find_next_index_in_range(tnext, ranges)` where ranges are in hours?
    # `tnext_in_hours = tnext / 3600`
    # So buckets are indeed HOURS.
    
    # Re-generate ranges
    KB = 1024
    MB = 1024 * KB
    GB = 1024 * MB
    # Note: These vars are local in the method in original code, duplicating here
    SEC = 1000 # This variable name is confusing in original code if unit is seconds. 
               # In original python: SEC = 1000. MIN = 60000. 
               # But `req_to_sec` uses datetime `total_seconds()`.
               # And `tnext_in_hours = tnext / 3600`.
               # This implies `tnext` is indeed Seconds.
               # Constructing ranges:
               # MIN_IN_HOUR = 0.01666
               # s = MIN_IN_HOUR * HOUR (where HOUR = 3,600,000?) -> s = 60,000 approx (1 min)
               # ranges_2per: s * ...
               # ranges = [x / HOUR ...] -> normalize back to hours.
    
    # Let's just copy the range generation logic EXACTLY
    SEC_CONST = 1000
    MIN_CONST = 60 * SEC_CONST
    HOUR_CONST = 60 * MIN_CONST
    MIN_IN_HOUR = 0.01666
    s = MIN_IN_HOUR * HOUR_CONST
    ranges_2per = [0] + [s * (1 + (2 / 100)) ** i for i in range(700)]
    ranges = [x / HOUR_CONST for x in ranges_2per] # This is in Hours
    
    total_gb_processed = sum(hist.values())
    print(f"Total GB in reuse histogram: {total_gb_processed:.2f}")

    # Calculate Optimal Binary Cost
    min_binary_cost = float('inf')
    best_T = 0
    
    # Run Scenarios
    scenarios = [
        ("Conservative (20% WAN Penalty)", 0.2 * NET_COST),
        ("Optimistic (Local CPU Cost only ~0.5% WAN)", 0.005 * NET_COST) # $0.0001
    ]

    for name, access_penalty in scenarios:
        min_ec_cost = float('inf')
        best_T1 = 0
        best_T2 = 0
        
        # O(N^3) Optimization
        for c in range(len(ranges)):
            T_evict = ranges[c]
            
            # Pruning: If T_evict is very small or very large, maybe skip? No, ranges are log-distributed.
            
            for k in range(c + 1):
                T_ec_trans = ranges[k]
                
                cost_ec = 0.0
                for i, gb in hist.items():
                    t_reuse = ranges[i]
                    
                    if t_reuse <= T_ec_trans:
                         # Hot Tier
                        cost_ec += gb * t_reuse * STORAGE_PER_GB_HOUR
                    elif t_reuse <= T_evict:
                        # EC Tier
                        # Cost = Hot Storage (T_ec) + EC Storage (duration) + AccessPenalty
                        cost_ec += gb * (T_ec_trans * STORAGE_PER_GB_HOUR + \
                                         (t_reuse - T_ec_trans) * STORAGE_PER_GB_HOUR * EC_STORAGE_FACTOR + \
                                         access_penalty)
                    else:
                        # Miss (Evicted)
                        # Cost = Hot Storage (T_ec) + EC Storage (T_evict - T_ec) + MissPenalty
                        cost_ec += gb * (T_ec_trans * STORAGE_PER_GB_HOUR + \
                                         (T_evict - T_ec_trans) * STORAGE_PER_GB_HOUR * EC_STORAGE_FACTOR + \
                                         NET_COST)
                
                if cost_ec < min_ec_cost:
                    min_ec_cost = cost_ec
                    best_T1 = T_ec_trans
                    best_T2 = T_evict

        print(f"\nScenario: {name}")
        print(f"  Optimal Tiered EC Policy:")
        print(f"    Transition Time: {best_T1:.4f} hours")
        print(f"    Eviction Time:   {best_T2:.4f} hours")
        print(f"    Total Cost Score: {min_ec_cost:.4f}")
        
        savings = (min_binary_cost - min_ec_cost) / min_binary_cost * 100
        print(f"    Estimated Savings: {savings:.2f}%")

if __name__ == "__main__":
    main()
