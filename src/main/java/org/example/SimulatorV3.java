package org.example;

import org.example.src.model.Request;

import java.math.BigDecimal;
import java.util.Map;

/**
 * SimulatorV3 扩展 SimulatorV2，实现 EC 分片的完整逻辑。
 * 仅覆盖 getPlacementSizesForWrite，将各 region 的数据量委托给 placementPolicy.getPlacementWithSizes。
 */
public class SimulatorV3 extends SimulatorV2 {

    public SimulatorV3(String configPath,
            String tracePath,
            int numVms,
            boolean setBaseRegion,
            int days,
            boolean versionEnable,
            boolean storeDecision) {
        super(configPath, tracePath, numVms, setBaseRegion, days, versionEnable, storeDecision);
    }

    public SimulatorV3(String configPath,
            String tracePath,
            int numVms,
            boolean setBaseRegion,
            int days,
            boolean versionEnable) {
        super(configPath, tracePath, numVms, setBaseRegion, days, versionEnable);
    }

    @Override
    protected Map<String, BigDecimal> getPlacementSizesForWrite(Request req) {
        return placementPolicy.getPlacementWithSizes(req, config);
    }

    @Override
    protected BigDecimal getReadTransferSize(Request req) {
        return placementPolicy.getReadTransferSize(req, config);
    }

    @Override
    protected void onTtlComputed(Request request) {
        // Keep V3 MILP timing centralized in onRequestReady.
    }

    @Override
    protected void onRequestReady(Request request) {
        if ("milp_ec_new_v3".equals(String.valueOf(config.placementPolicy))
                && request != null
                && placementPolicy instanceof org.example.src.placement_policy.PolicyMilpECnewV3) {
            ((org.example.src.placement_policy.PolicyMilpECnewV3) placementPolicy)
                    .runMilpForIssueRegionIfDue(request.issueRegion, request.timestamp, request);
        }
    }
}
