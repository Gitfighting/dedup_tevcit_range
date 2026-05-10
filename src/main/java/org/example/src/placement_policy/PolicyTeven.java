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
 * Teven策略：动态TTL策略
 * Python: class Teven(PlacementPolicy)
 */
public class PolicyTeven extends PlacementPolicy {
    public PolicyTeven(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;

        if ("write".equals(op)) {
            return Collections.singletonList(issueRegion);
        } else {
            // read case
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                return Collections.singletonList(issueRegion);
            }
        }

        return Collections.emptyList();
    }
}
