package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;

import java.util.*;

/**
 * DynamicTTL策略：动态调整TTL的缓存策略
 * Python: class DynamicTTL(PlacementPolicy)
 */
public class PolicyDynamicTtl extends PlacementPolicy {
    private final Map<String, Integer> objectHits = new HashMap<>();
    private final Map<String, Double> globalTtls = new HashMap<>();
    private final double epsilon = 0.01;
    private final double avgNetworkCost;

    public PolicyDynamicTtl(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
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
    public void updateGlobalTtl(int duration, String objKey, String src, String dst) {
        int numHits = objectHits.getOrDefault(dst + ":" + objKey, 0);

        Object costObj = totalGraph.getEdgeAttrs(src, dst).get("cost");
        double netCost = costObj instanceof Number ? ((Number) costObj).doubleValue() : avgNetworkCost;
        if (netCost == 0)
            netCost = avgNetworkCost;

        Object priceObj = totalGraph.getNodeAttrs(dst).get("priceStorage");
        double storageCostPerHour = priceObj instanceof Number ? ((Number) priceObj).doubleValue() / 24 * 3 : 0.0;

        double durationInHour = duration / 3600.0;

        if (durationInHour != 0) {
            double deltaTtl = epsilon * (-durationInHour * storageCostPerHour + numHits / durationInHour * netCost)
                    * 3600;
            globalTtls.put(dst, Math.max(globalTtls.getOrDefault(dst, 0.0) + deltaTtl, 0.0));
        }

        objectHits.remove(src + ":" + objKey);
    }

    public void objectHit(String objKey, String region) {
        String key = region + ":" + objKey;
        objectHits.put(key, objectHits.getOrDefault(key, 0) + 1);
    }

    public double getTtl(String region, double teven) {
        if (!globalTtls.containsKey(region)) {
            globalTtls.put(region, teven); // Initialize with teven in seconds
        }
        return globalTtls.get(region); // Return in seconds
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;

        Set<String> placeRegions = new HashSet<>();

        if ("write".equals(op)) {
            placeRegions.add(issueRegion);
        } else {
            // read case
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                placeRegions.add(issueRegion);
            }
        }

        return new ArrayList<>(placeRegions);
    }
}
