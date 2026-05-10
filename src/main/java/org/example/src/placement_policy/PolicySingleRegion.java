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
 * SingleRegionWrite策略：写入到配置中指定的同一区域
 * Python: class SingleRegionWrite(PlacementPolicy)
 */
public class PolicySingleRegion extends PlacementPolicy {
    public PolicySingleRegion(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
    }

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            return Collections.singletonList(config.storageRegion);
        } else {
            return Collections.emptyList();
        }
    }
}
