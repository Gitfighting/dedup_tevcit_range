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
 * AlwaysEvict策略：写本地，但读取时不拉取
 * Python: class AlwaysEvict(PlacementPolicy)
 */
public class PolicyAlwaysEvict extends PlacementPolicy {
    public PolicyAlwaysEvict(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
    }

    @Override
    public List<String> place(Request req, Config config) {
        // Write local, and do not pull on read if data is not available locally
        if ("write".equals(req.op)) {
            return Collections.singletonList(req.issueRegion);
        } else {
            return Collections.emptyList();
        }
    }
}
