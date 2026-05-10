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
 * AlwaysStore策略：总是在本地存储
 * 写入本地，读取时如果本地没有数据则拉取
 * Python: class AlwaysStore(PlacementPolicy)
 */
public class PolicyAlwaysStore extends PlacementPolicy {
    public PolicyAlwaysStore(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
    }

    @Override
    public List<String> place(Request req, Config config) {
        // Write local, and pull on read if data is not available locally
        return Collections.singletonList(req.issueRegion);
    }
}
