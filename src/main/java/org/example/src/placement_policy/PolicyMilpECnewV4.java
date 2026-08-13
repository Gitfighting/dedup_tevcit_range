package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * MILP EC V4: V3 + delta effective bytes (size × r) and §7.4 read recovery matrix.
 */
public class PolicyMilpECnewV4 extends PolicyMilpECnewV3 {

    public PolicyMilpECnewV4(Config config, DiGraph totalGraph,
            Map<String, LogicalObject> logicalObjects,
            RegionManager regionManager) {
        super(config, totalGraph, logicalObjects, regionManager);
    }

    @Override
    protected double effectiveSizeRatio(LogicalObject template) {
        if (!Boolean.TRUE.equals(config.deltaEnabled) || template == null) {
            return 1.0;
        }
        if (template.isBase || template.deltaRatio >= 0.999999) {
            return 1.0;
        }
        return Math.max(0.0, Math.min(1.0, template.deltaRatio));
    }

    private boolean isDeltaMember(LogicalObject lo) {
        return lo != null && Boolean.TRUE.equals(config.deltaEnabled)
                && !lo.isBase && lo.deltaRatio < 0.999999;
    }

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            LogicalObject lo = logicalObjects.get(req.objKey);
            if (isDeltaMember(lo) && !Boolean.TRUE.equals(config.ecOnPatch)) {
                return Collections.singletonList(req.issueRegion);
            }
        }
        return super.place(req, config);
    }

    @Override
    public Map<String, BigDecimal> getPlacementWithSizes(Request req, Config config) {
        if ("read".equals(req.op)) {
            return super.getPlacementWithSizes(req, config);
        }
        if (!"write".equals(req.op)) {
            return null;
        }
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (isDeltaMember(lo) && !Boolean.TRUE.equals(config.ecOnPatch)) {
            BigDecimal eff = req.size.multiply(BigDecimal.valueOf(effectiveSizeRatio(lo)));
            return Collections.singletonMap(req.issueRegion, eff);
        }
        Map<String, BigDecimal> sized = super.getPlacementWithSizes(req, config);
        if (sized == null || lo == null || effectiveSizeRatio(lo) >= 0.999999) {
            return sized;
        }
        double ratio = effectiveSizeRatio(lo);
        Map<String, BigDecimal> scaled = new HashMap<>();
        for (Map.Entry<String, BigDecimal> e : sized.entrySet()) {
            scaled.put(e.getKey(), e.getValue().multiply(BigDecimal.valueOf(ratio)));
        }
        return scaled;
    }

    /**
     * Fallback read-transfer sizing (§7.4) used only when the transfer policy cannot build an explicit
     * delta read path (e.g. base unplaced). The primary, edge-accurate accounting lives in
     * {@code CheapestTransferV3.buildDeltaReadPath}; this keeps the single-graph fallback honest by
     * charging patch (size×r) plus full base when the base is not a local full replica.
     */
    @Override
    public BigDecimal getReadTransferSize(Request req, Config config) {
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (!isDeltaMember(lo)) {
            return super.getReadTransferSize(req, config);
        }
        String reader = req.issueRegion;
        BigDecimal fullSize = lo.getSize() == null ? req.size : lo.getSize();
        double r = effectiveSizeRatio(lo);
        BigDecimal patchSize = fullSize.multiply(BigDecimal.valueOf(r));

        BigDecimal patchXfer = localFullReplicaReady(lo, reader, patchSize, req.timestamp)
                ? BigDecimal.ZERO
                : patchSize;

        LogicalObject baseLo = lo.baseKey != null ? logicalObjects.get(lo.baseKey) : null;
        BigDecimal baseXfer = BigDecimal.ZERO;
        // §7.4: delta decode needs full base; EC shards alone do not satisfy local base.
        if (baseLo != null && !localFullReplicaReady(baseLo, reader, fullSize, req.timestamp)) {
            baseXfer = fullSize;
        }
        return patchXfer.add(baseXfer);
    }

    /** True only when a full replica (size >= fullSize) is ready and unexpired at {@code region}. */
    private boolean localFullReplicaReady(
            LogicalObject lo, String region, BigDecimal fullSize, Instant at) {
        return hasReadyFullReplicaAt(lo, region, at);
    }
}
