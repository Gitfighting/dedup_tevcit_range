"""
OCP safety net for author get_avg_storage_cost.

Preferred fix is data: merge GCP into storage.csv via
scripts/cloud/merge_gcp_into_storage_csv.py so helpers.py attaches
priceStorage to all throughput nodes.

This patch only fills remaining holes (should be 0 after merge).
Does NOT edit helpers.py / policy_skystore.py.
"""
from __future__ import annotations

import statistics

import operations.policy.utils.helpers as helpers

_orig = helpers.get_avg_storage_cost


def get_avg_storage_cost_safe(G):
    missing = [n for n in G.nodes if "priceStorage" not in G.nodes[n]]
    if missing:
        known = [
            G.nodes[n]["priceStorage"]
            for n in G.nodes
            if "priceStorage" in G.nodes[n]
        ]
        fill = statistics.median(known) if known else 0.023
        for n in missing:
            G.nodes[n]["priceStorage"] = fill
        print(
            f"[ablation.fix_price_storage] WARNING filled {len(missing)} "
            f"nodes with {fill} (run merge_gcp_into_storage_csv.py)",
            flush=True,
        )
    else:
        print(
            f"[ablation.fix_price_storage] OK all {len(G.nodes)} nodes "
            f"have priceStorage",
            flush=True,
        )
    return _orig(G)


helpers.get_avg_storage_cost = get_avg_storage_cost_safe
print("[ablation.fix_price_storage] patched get_avg_storage_cost", flush=True)
