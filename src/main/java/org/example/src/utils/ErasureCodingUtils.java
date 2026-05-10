package org.example.src.utils;

import java.math.BigDecimal;
import java.util.*;

/**
 * ErasureCodingUtils: Utility functions for Erasure Coding decisions and
 * operations
 * 
 * Provides:
 * - Cost-based EC transition decision
 * - Chunk placement strategy
 * - Transition cost calculation
 */
public class ErasureCodingUtils {

    /**
     * /**
     * Determine if object should transition to EC using Dominant Region +
     * Worst-Case Bound model
     * 
     * Decision Formula:
     * T_idle × ΔC_storage^LB > C_transition + N_min_reads × C_read^UB
     * 
     * Where:
     * - ΔC_storage^LB = (max_replica_cost - min_EC_cost) × Size
     * - C_transition = sum over EC chunks of min egress cost from any replica
     * region
     * - C_read^UB = worst-case reconstruction cost if read from most expensive
     * region
     * - N_min_reads = 1 (conservative assumption)
     * 
     * @param replicaRegions  Dominant region set (regions with replicas)
     * @param objectSize      Object size in bytes
     * @param regionPricing   Map of region -> [storagePrice, egressPrice]
     * @param idleTimeSeconds Time since last access (seconds)
     * @param k               Number of data chunks
     * @param m               Number of parity chunks
     * @param ecTargetRegions Simulated EC chunk placement regions
     * @param graph           Network topology for transfer cost calculation
     * @return true if EC transition is cost-effective under worst-case assumptions
     */
    public static boolean shouldTransitionToEC(
            Set<String> replicaRegions,
            BigDecimal objectSize,
            Map<String, double[]> regionPricing,
            double idleTimeSeconds,
            int k,
            int m,
            List<String> ecTargetRegions,
            org.example.graph.DiGraph graph) {

        // Convert size to GB
        double objectSizeGB = objectSize.doubleValue() / (1024.0 * 1024.0 * 1024.0);

        // Convert idle time to months (2,592,000 seconds/month)
        double idleMonths = idleTimeSeconds / 2592000.0;

        // Minimum idle threshold: 12 hours
        if (idleTimeSeconds < 43200) {
            return false;
        }

        // ==================== Step 2: Storage Saving Lower Bound ====================
        // ΔC_storage^LB = (max_replica_cost - min_EC_cost) × Size

        double maxReplicaStorageCost = Double.MIN_VALUE;
        double minECStorageCost = Double.MAX_VALUE;

        // Find max replica storage cost among dominant regions
        for (String region : replicaRegions) {
            double[] pricing = regionPricing.get(region);
            if (pricing != null) {
                double monthlyCost = pricing[0] * objectSizeGB; // $/month
                maxReplicaStorageCost = Math.max(maxReplicaStorageCost, monthlyCost);
            }
        }

        // Find min EC storage cost among dominant regions
        for (String region : replicaRegions) {
            double[] pricing = regionPricing.get(region);
            if (pricing != null) {
                double ecMonthlyCost = pricing[0] * objectSizeGB * ((double) (k + m) / k);
                minECStorageCost = Math.min(minECStorageCost, ecMonthlyCost);
            }
        }

        double deltaStorageLB = (maxReplicaStorageCost - minECStorageCost) * idleMonths;

        // ==================== Step 3: Transition Cost (Real) ====================
        // C_transition = Σ(min egress from any replica to each EC chunk)

        double transitionCost = 0.0;
        double chunkSizeGB = objectSizeGB / k; // Each chunk size

        for (String ecRegion : ecTargetRegions) {
            double minEgressCost = Double.MAX_VALUE;

            // Find cheapest transfer from any replica region to this EC region
            for (String replicaRegion : replicaRegions) {
                double egressCost = getEgressCost(graph, replicaRegion, ecRegion) * chunkSizeGB;
                minEgressCost = Math.min(minEgressCost, egressCost);
            }

            transitionCost += minEgressCost;
        }

        // ==================== Step 4: Read Penalty Upper Bound ====================
        // C_read^UB = max over replica regions of (cost to reconstruct k chunks)

        double maxReadCost = Double.MIN_VALUE;

        for (String readRegion : replicaRegions) {
            double reconstructCost = 0.0;

            // Need to fetch k data chunks to reconstruct
            for (int i = 0; i < k && i < ecTargetRegions.size(); i++) {
                String ecChunkRegion = ecTargetRegions.get(i);
                reconstructCost += getEgressCost(graph, ecChunkRegion, readRegion) * chunkSizeGB;
            }

            maxReadCost = Math.max(maxReadCost, reconstructCost);
        }

        int N_minReads = 1; // Conservative: assume at least 1 read
        double readPenaltyUB = N_minReads * maxReadCost;

        // ==================== Step 5: Final Decision ====================
        // Transition if: T_idle × ΔC_storage^LB > C_transition + C_read^UB

        double leftSide = deltaStorageLB;
        double rightSide = transitionCost + readPenaltyUB;

        return leftSide > rightSide;
    }

    /**
     * Get egress cost from source to destination region
     */
    private static double getEgressCost(org.example.graph.DiGraph graph, String src, String dst) {
        if (src.equals(dst)) {
            return 0.0; // No cost for same region
        }

        Map<String, Object> edgeAttrs = graph.getEdgeAttrs(src, dst);
        if (edgeAttrs != null) {
            Object cost = edgeAttrs.get("cost");
            if (cost != null) {
                return ((Number) cost).doubleValue();
            }
        }

        // Fallback: use source region's egress price
        Map<String, Object> srcAttrs = graph.getNodeAttrs(src);
        if (srcAttrs != null) {
            Object egress = srcAttrs.get("priceEgress");
            if (egress != null) {
                return ((Number) egress).doubleValue();
            }
        }

        return 0.02; // Default fallback
    }

    /**
     * Algorithm 5: EC Parameter Determination and Chunk Placement
     * 
     * Places chunks strategically:
     * - Data chunks: prioritize high access frequency regions
     * - Parity chunks: prioritize different clouds + low storage cost
     */
    public static List<String> selectChunkPlacements(
            int k,
            int m,
            org.example.graph.DiGraph graph,
            List<String> availableRegions,
            Set<String> replicaRegions) {

        // Step 1: Collect region information
        List<RegionScore> regionScores = new ArrayList<>();

        for (String region : availableRegions) {
            // Access frequency: higher if region has replicas 1.1 计算访问频率得分
            double accessFreq = replicaRegions.contains(region) ? 1.0 : 0.0;

            // Storage cost from graph node attributes 1.2 计算存储成本得分
            double storageCost = getStoragePrice(graph, region);

            // Cloud provider (e.g., "aws" from "aws:us-east-1") 1.3 提取云提供商名称
            String cloud = region.contains(":") ? region.split(":")[0] : "unknown";
            // 1.4 创建评分对象
            regionScores.add(new RegionScore(region, accessFreq, storageCost, cloud));
        }

        // Step 2: Place data chunks (prioritize high access frequency, then low cost)
        // 2.1 按访问频率降序排序，相同时按存储成本升序排序
        regionScores.sort((a, b) -> {
            // Priority 1: Higher access frequency
            int freqCompare = Double.compare(b.accessFreq, a.accessFreq);
            if (freqCompare != 0) {
                return freqCompare;
            }
            // Priority 2: Lower storage cost (when access freq is equal)
            return Double.compare(a.storageCost, b.storageCost);
        });

        List<String> selectedRegions = new ArrayList<>();
        Set<String> usedRegions = new HashSet<>();
        String primaryCloud = null;
        // 2.2 选择前k个Region作为数据块位置
        for (int i = 0; i < k && i < regionScores.size(); i++) {
            String region = regionScores.get(i).region;
            selectedRegions.add(region);
            usedRegions.add(region);
            // 记录第一个Region的云提供商
            if (i == 0) {
                primaryCloud = regionScores.get(i).cloud;
            }
        }

        // Step 3: Place parity chunks (prioritize different cloud + low cost)
        final String finalPrimaryCloud = primaryCloud;
        regionScores.sort((a, b) -> {
            // Priority 1: Different cloud from primary
            boolean aDiffCloud = !a.cloud.equals(finalPrimaryCloud);
            boolean bDiffCloud = !b.cloud.equals(finalPrimaryCloud);

            if (aDiffCloud != bDiffCloud) {
                return aDiffCloud ? -1 : 1;// 不同云的排在前面
            }

            // Priority 2: Lower storage cost
            return Double.compare(a.storageCost, b.storageCost);
        });
        // 3.2 逐个添加校验块位置
        for (RegionScore rs : regionScores) {
            if (usedRegions.contains(rs.region)) {
                continue; // 跳过已用于数据块的Region
            }

            selectedRegions.add(rs.region);
            usedRegions.add(rs.region);

            if (selectedRegions.size() >= k + m) {
                break;// 已选够k+m个
            }
        }

        return selectedRegions;
    }

    /**
     * Determine EC parameters (k, m) based on available region count
     */
    public static int[] determineECParameters(int numRegions) {
        if (numRegions == 3) {
            return new int[] { 2, 1 }; // RS(2+1)
        } else if (numRegions >= 6 && numRegions <= 8) {
            return new int[] { 4, 2 }; // RS(4+2)
        } else if (numRegions >= 9) {
            return new int[] { 6, 3 }; // RS(6+3)
        } else {
            return null; // EC not supported
        }
    }

    /**
     * Helper class for region scoring
     */
    private static class RegionScore {
        String region;
        double accessFreq;
        double storageCost;
        String cloud;

        RegionScore(String region, double accessFreq, double storageCost, String cloud) {
            this.region = region;
            this.accessFreq = accessFreq;
            this.storageCost = storageCost;
            this.cloud = cloud;
        }
    }

    private static double getStoragePrice(org.example.graph.DiGraph graph, String region) {
        Map<String, Object> attrs = graph.getNodeAttrs(region);
        Object price = attrs.get("priceStorage");
        return price != null ? ((Number) price).doubleValue() : 0.023;
    }

    /**
     * Calculate the cost of transitioning from replicas to EC
     * 
     * @param objectSizeGB   Object size in GB
     * @param avgEgressPrice Average egress price
     * @param k              Number of data chunks
     * @param m              Number of parity chunks
     * @return Transition cost in dollars
     */
    public static double calculateTransitionCost(
            double objectSizeGB,
            double avgEgressPrice,
            int k,
            int m) {

        // Cost to distribute EC chunks
        double ecStorageOverhead = (double) (k + m) / k;
        double distributeCost = objectSizeGB * avgEgressPrice * ecStorageOverhead;

        return distributeCost;
    }
}
