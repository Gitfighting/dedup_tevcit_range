package org.example.src.model;

import org.example.src.utils.ErasureCodingUtils;
import org.example.src.utils.CostLogger;
import org.example.graph.DiGraph;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.*;

/**
 * ECManager handles Erasure Coding transitions and operations.
 * Manages the lifecycle of converting replicas to EC and vice versa.
 */
public class ECManager {

    private final int k; // Data chunks
    private final int m; // Parity chunks (l + r for LRC)
    private final DiGraph totalGraph; // Network topology
    private final List<String> regions; // Available regions
    private final Map<String, double[]> regionPricing; // region -> [storagePrice, egressPrice]
    private final Map<String, String> cloudProviders; // region -> cloud name

    public ECManager(int k, int m, DiGraph totalGraph, List<String> regions) {
        this.k = k;
        this.m = m;
        this.totalGraph = totalGraph;
        this.regions = regions;
        this.regionPricing = new HashMap<>();
        this.cloudProviders = new HashMap<>();

        // Initialize pricing from graph
        for (String region : regions) {
            Map<String, Object> attrs = totalGraph.getNodeAttrs(region);

            // Extract storage price from node attributes
            if (attrs.get("priceStorage") == null) {
                throw new IllegalStateException("Missing priceStorage for region: " + region);
            }
            double storagePrice = ((Number) attrs.get("priceStorage")).doubleValue();

            // Extract egress price from outgoing edges (average of all edge costs from this
            // region)
            double egressPrice = totalGraph.getSuccessors(region).stream()
                    .mapToDouble(dest -> {
                        Object cost = totalGraph.getEdgeAttrs(region, dest).get("cost");
                        return cost != null ? ((Number) cost).doubleValue() : 0.0;
                    })
                    .average()
                    .orElseThrow(() -> new IllegalStateException("No egress pricing data for region: " + region));

            regionPricing.put(region, new double[] { storagePrice, egressPrice });

            // Extract cloud provider from region name (e.g., "aws:us-east-1" -> "aws")
            String[] parts = region.split(":");
            cloudProviders.put(region, parts.length > 0 ? parts[0] : "unknown");
        }
    }

    /**
     * Evaluates whether an object should transition to EC.
     * Uses idle time (time since last access) instead of access probability.
     */
    public boolean shouldTransitionToEC(LogicalObjectV2 obj, java.time.Instant currentTime) {
        int replicaCount = obj.getReplicaCount();
        BigDecimal objectSize = obj.getSize();

        // Calculate idle time (seconds since last access) using Duration for precision
        double idleTimeSeconds = Duration.between(obj.getLastAccessTimeInstant(), currentTime).getSeconds();

        // Calculate replica-weighted pricing based on actual replica distribution
        if (regionPricing.isEmpty()) {
            throw new IllegalStateException("Region pricing data is not initialized");
        }

        // Get regions where replicas currently exist
        Set<String> replicaRegions = obj.physicalObjects.keySet();

        // Simulate EC chunk placement using Algorithm 5
        List<String> ecTargetRegions = ErasureCodingUtils.selectChunkPlacements(
                k, m, this.totalGraph, this.regions, replicaRegions);

        // Call new Worst-Case Bound EC decision model
        boolean shouldTransition = ErasureCodingUtils.shouldTransitionToEC(
                replicaRegions, // Dominant region set
                objectSize, // Object size
                regionPricing, // Region pricing map
                idleTimeSeconds, // Idle time
                k, m, // EC parameters
                ecTargetRegions, // Simulated EC targets
                this.totalGraph); // Network topology

        // Detailed logging for analysis and tuning
        if (shouldTransition) {
            CostLogger.log(String.format(
                    "[EC-DECISION] Object=%s, Size=%.2fMB, Replicas=%d in %s, IdleHours=%.2f "
                            + "| EC_TARGETS=%s | EC_PARAMS: RS(%d+%d) | DECISION: TRANSITION",
                    obj.getKey(),
                    objectSize.doubleValue() / (1024.0 * 1024.0),
                    replicaCount,
                    replicaRegions,
                    idleTimeSeconds / 3600.0,
                    ecTargetRegions,
                    k,
                    m));
        }

        return shouldTransition;
    }

    /**
     * Simulates EC encoding by creating chunk metadata.
     * In real implementation, this would perform actual encoding.
     * 
     * @return Map of region -> List of chunk indices to place in that region
     */
    public Map<String, List<Integer>> planECPlacement(
            LogicalObjectV2 obj,
            Map<String, Double> regionAccessPatterns) {

        int numChunks = k + m;

        // Get replica regions from object's physical objects map
        Set<String> replicaRegions = new HashSet<>(obj.physicalObjects.keySet());

        // Use Algorithm 5 for chunk placement
        List<String> selectedRegions = ErasureCodingUtils.selectChunkPlacements(
                k, m, this.totalGraph, this.regions, replicaRegions);

        // Assign chunks to regions
        Map<String, List<Integer>> placement = new HashMap<>();
        for (int i = 0; i < numChunks && i < selectedRegions.size(); i++) {
            String region = selectedRegions.get(i);
            placement.putIfAbsent(region, new ArrayList<>());
            placement.get(region).add(i);
        }

        return placement;
    }

    /**
     * Creates PhysicalObjectV2 instances for EC chunks.
     * 
     * @param obj       The logical object
     * @param placement Map of region -> chunk indices
     * @param ecGroupId UUID for this EC group
     * @return Map of region -> List of PhysicalObjectV2 chunks
     */
    public Map<String, List<PhysicalObjectV2>> createECChunks(
            LogicalObjectV2 obj,
            Map<String, List<Integer>> placement,
            String ecGroupId,
            int ttl) {

        Map<String, List<PhysicalObjectV2>> chunks = new HashMap<>();
        BigDecimal chunkSize = obj.getShardSize();

        for (Map.Entry<String, List<Integer>> entry : placement.entrySet()) {
            String region = entry.getKey();
            List<Integer> indices = entry.getValue();
            List<PhysicalObjectV2> regionChunks = new ArrayList<>();

            for (int idx : indices) {
                PhysicalObjectV2.ChunkType type;
                if (idx < k) {
                    type = PhysicalObjectV2.ChunkType.DATA_CHUNK;
                } else if (idx < k + (m - 1)) {
                    type = PhysicalObjectV2.ChunkType.LOCAL_PARITY;
                } else {
                    type = PhysicalObjectV2.ChunkType.GLOBAL_PARITY;
                }

                PhysicalObjectV2 chunk = new PhysicalObjectV2(
                        region,
                        obj.getKey() + "_chunk_" + idx,
                        chunkSize,
                        ttl,
                        obj,
                        Status.ready,
                        obj.getLatestVersionId(),
                        type,
                        idx);
                chunk.setECGroupId(ecGroupId);
                regionChunks.add(chunk);
            }

            chunks.put(region, regionChunks);
        }

        return chunks;
    }

    /**
     * Calculates transition cost for converting replicas to EC.
     */
    public double calculateTransitionCost(LogicalObjectV2 obj, Map<String, List<Integer>> placement) {
        double objectSizeGB = obj.getSize().doubleValue() / (1024.0 * 1024.0 * 1024.0);

        // Cost to gather data (assume from one region)
        double gatherCost = 0; // Assume we use existing replica, no transfer

        // Cost to distribute chunks
        double avgEgress = regionPricing.values().stream()
                .mapToDouble(p -> p[1]).average().orElse(0.02);
        double distributeCost = (objectSizeGB * (k + m) / k) * avgEgress;

        return gatherCost + distributeCost;
    }

    // Getters for EC parameters
    public int getK() {
        return k;
    }

    public int getM() {
        return m;
    }
}
