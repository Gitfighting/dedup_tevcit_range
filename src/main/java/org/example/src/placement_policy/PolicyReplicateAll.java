package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * ReplicateAll策略：将所有对象复制到所有区域
 * Python: class ReplicateAll(PlacementPolicy)
 */
public class PolicyReplicateAll extends PlacementPolicy {
    public PolicyReplicateAll(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
    }

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            if (config.regions != null && !config.regions.isEmpty()) {
                return config.regions;
            } else {
                return new java.util.ArrayList<>(totalGraph.getNodes());
            }
        } else {
            return Collections.emptyList();
        }
    }
}
