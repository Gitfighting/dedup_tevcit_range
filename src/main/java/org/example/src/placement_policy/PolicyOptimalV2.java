package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

/**
 * OptimalV2策略：基于未来访问时间戳的最优缓存策略
 * Python: class OptimalV2(PlacementPolicy)
 */
public class PolicyOptimalV2 extends PlacementPolicy {
    private final double avgNetworkCost;
    private int notWorth = 0;
    private int worth = 0;

    public PolicyOptimalV2(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.avgNetworkCost = getAvgNetworkCost(g);
    }

    private double getAvgNetworkCost(DiGraph graph) {
        double sum = 0.0;
        int count = 0;
        for (String src : graph.getNodes()) {
            for (String dst : graph.getSuccessors(src)) {
                Object cost = graph.getEdgeAttrs(src, dst).get("cost");
                if (cost instanceof Number) {
                    sum += ((Number) cost).doubleValue();
                    count++;
                }
            }
        }
        return count > 0 ? sum / count : 0.0;
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;
        List<String> placeRegions = new ArrayList<>();

        if ("write".equals(op)) {
            LogicalObject logicalObj = logicalObjects.get(key);
            boolean isBaseRegion = issueRegion.equals(logicalObj.baseRegion);
            boolean noCopies = logicalObj.physicalObjects.isEmpty();
            boolean hasReads = req.nextAccessSameRegTimestamp != null &&
                    !req.nextAccessSameRegTimestamp.equals(LocalDateTime.MAX);

            if (isBaseRegion || noCopies || hasReads) {
                placeRegions.add(issueRegion);
            }
        } else {
            // read case
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                if (worthStoring(issueRegion, key, req)) {
                    placeRegions.add(issueRegion);
                }
            }
        }

        return placeRegions.stream().distinct().collect(Collectors.toList());
    }

    private boolean worthStoring(String issueRegion, String key, Request request) {
        List<String> regionList = logicalObjects.get(key).physicalObjects.values().stream()
                .filter(obj -> !obj.getLocationTag().equals(issueRegion) && obj.ready(request.timestamp))
                .map(PhysicalObject::getLocationTag)
                .collect(Collectors.toList());

        double netCost;
        if (regionList.isEmpty()) {
            // Check that this object is located in issue_region
            long countInIssue = logicalObjects.get(key).physicalObjects.values().stream()
                    .filter(obj -> obj.getLocationTag().equals(issueRegion))
                    .count();
            assert countInIssue == 1;
            netCost = avgNetworkCost;
        } else {
            String src = regionList.stream()
                    .min(Comparator.comparing((String x) -> {
                        Object cost = totalGraph.getEdgeAttrs(x, issueRegion).get("cost");
                        return cost instanceof Number ? ((Number) cost).doubleValue() : Double.MAX_VALUE;
                    }).thenComparing(x -> {
                        Object latency = totalGraph.getEdgeAttrs(x, issueRegion).get("latency");
                        return latency instanceof Number ? ((Number) latency).doubleValue() : Double.MAX_VALUE;
                    }))
                    .orElse(null);

            if (src != null) {
                Object costObj = totalGraph.getEdgeAttrs(src, issueRegion).get("cost");
                netCost = costObj instanceof Number ? ((Number) costObj).doubleValue() : avgNetworkCost;
            } else {
                netCost = avgNetworkCost;
            }
        }

        Object priceStorage = totalGraph.getNodeAttrs(issueRegion).get("priceStorage");
        double price = priceStorage instanceof Number ? ((Number) priceStorage).doubleValue() : 0.0;

        // Match Python: net_cost / price * 60 * 60 * 24 / 3
        // Use double precision
        double teven = (netCost / price * 60 * 60 * 24 / 3.0);

        if (request.nextAccessSameRegTimestamp != null) {
            // FIX: Use nanosecond precision to match Python's total_seconds() float return
            double timeDiff = java.time.Duration.between(request.timestamp, request.nextAccessSameRegTimestamp)
                    .toNanos() / 1_000_000_000.0;
            if (timeDiff <= teven) {
                worth++;
                return true;
            }
        }

        notWorth++;
        return false;
    }
}
