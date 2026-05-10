package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;

import java.math.BigDecimal;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 置放策略基类，占位并与 Python 的 PlacementPolicy 对齐。
 * Python: class PlacementPolicy: def place(self, req: Request, config: Config)
 * -> List[str]: pass
 */
public abstract class PlacementPolicy {
    protected final Config config;
    protected final DiGraph totalGraph;
    protected final Map<String, LogicalObject> logicalObjects;
    protected final RegionManager regionManager;

    public PlacementPolicy(Config config,
            DiGraph totalGraph,
            Map<String, LogicalObject> logicalObjects,
            RegionManager regionManager) {
        this.config = config;
        this.totalGraph = totalGraph;
        this.logicalObjects = logicalObjects;
        this.regionManager = regionManager;
    }

    /**
     * 置放决策接口，与 Python 的 place 方法对齐。
     * 返回应置放的区域列表。
     */
    public List<String> place(Request req, Config config) {
        return Collections.emptyList();
    }

    public void updateGlobalTtl(int ttl, String key, String region, String dst) {
    }

    public void onRead(Request req) {
    }

    public void onWrite(Request req, PhysicalObject newObj) {
    }

    // Methods required by SimulatorV2 (corresponding to Python usage)
    public void updatePastRequests(Request req, String region) {
    }

    public void objectHit(String key, String region) {
    }

    public double getTevict(String region, String issueRegion, java.time.Instant timestamp) {
        return 0.0;
    }

    public double getTtl(String region, double currentTtl) {
        return currentTtl;
    }

    public double estimateArrivalRecency(String region, String key) {
        return 0.0;
    }

    public double factor = 1.0;

    // For Tevict / TevictRanges
    public List<Double> ttls = Collections.emptyList();
    public Map<String, Double> regionPairsTtl = Collections.emptyMap();

    // SPANStore support
    public List<String> place(String objKey) {
        return Collections.emptyList();
    }

    /** Used by {@link org.example.SimulatorV3}; override in EC-aware policies. */
    public Map<String, BigDecimal> getPlacementWithSizes(Request req, Config cfg) {
        return null;
    }

    /** Used by {@link org.example.SimulatorV3}; override in EC-aware policies. */
    public BigDecimal getReadTransferSize(Request req, Config cfg) {
        return null;
    }

}
