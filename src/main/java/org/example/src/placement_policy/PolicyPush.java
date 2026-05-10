package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * PushonWrite策略：本地写入并异步推送到一组推送区域
 * Python: class PushonWrtie(PlacementPolicy)
 */
public class PolicyPush extends PlacementPolicy {
    private final List<String> pushRegions;

    public PolicyPush(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.pushRegions = c.regions != null ? new ArrayList<>(c.regions) : new ArrayList<>();
    }

    @Override
    public List<String> place(Request req, Config config) {
        List<String> result = new ArrayList<>();
        result.add(req.issueRegion);
        result.addAll(pushRegions);
        return result.stream().distinct().collect(Collectors.toList());
    }
}
