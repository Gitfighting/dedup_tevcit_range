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
 * LRU策略：写本地，读时拉取，超过缓存阈值时使用LRU驱逐
 * Python: class LRU(PlacementPolicy)
 */
public class PolicyLru extends PlacementPolicy {
    private final long cacheSize;

    public PolicyLru(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.cacheSize = c.cacheSize != null ? c.cacheSize : 0;
    }

    @Override
    public List<String> place(Request req, Config config) {
        String placeRegion = req.issueRegion;

        if ("read".equals(req.op) && !regionManager.hasObjectInRegion(placeRegion, req.objKey)) {
            if (req.size.add(regionManager.getRegionObjectSize(placeRegion))
                    .compareTo(java.math.BigDecimal.valueOf(cacheSize)) > 0) {
                // Evict until the size is less than the cache size
                regionManager.evictLru(placeRegion, req.size, cacheSize, req.timestamp);
            }
        }

        return Collections.singletonList(req.issueRegion);
    }
}
