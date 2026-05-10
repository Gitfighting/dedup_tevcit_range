package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;

import java.util.*;

/**
 * Fixed_TTL策略：固定TTL缓存策略
 * Python: class Fixed_TTL(PlacementPolicy)
 */
public class PolicyFixedTtl extends PlacementPolicy {
    public PolicyFixedTtl(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;

        Set<String> placeRegions = new HashSet<>();

        if ("write".equals(op)) {
            placeRegions.add(issueRegion);
        } else {
            // read case
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                placeRegions.add(issueRegion);
            }
        }

        return new ArrayList<>(placeRegions);
    }
}
