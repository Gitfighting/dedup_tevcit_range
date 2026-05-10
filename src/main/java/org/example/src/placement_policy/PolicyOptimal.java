package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.util.*;
import java.util.stream.Collectors;
import org.example.src.model.Status;

/**
 * Optimal策略：基于未来访问模式的最优缓存策略
 * Python: class Optimal(PlacementPolicy)
 */
public class PolicyOptimal extends PlacementPolicy {
    private final Map<String, Map<String, Integer>> curReq = new HashMap<>();
    private final Map<String, Map<String, List<Long>>> requests = new HashMap<>(); // Needs to be populated externally
                                                                                   // for Oracle behavior
    private final double avgNetworkCost;

    public PolicyOptimal(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
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

            boolean hasReads = curReq.containsKey(issueRegion)
                    && curReq.get(issueRegion).containsKey(key)
                    && requests.containsKey(issueRegion) // Safety check not in Python but good for Java
                    && requests.get(issueRegion).containsKey(key)
                    && curReq.get(issueRegion).get(key) <= requests.get(issueRegion).get(key).size() - 1;

            if (isBaseRegion || noCopies || hasReads) {
                placeRegions.add(issueRegion);
            }
        } else {
            // read case
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                if (worthStoring(issueRegion, key)) {
                    placeRegions.add(issueRegion);
                }
            }

            curReq.computeIfAbsent(issueRegion, k -> new HashMap<>())
                    .merge(key, 1, Integer::sum);
        }

        return placeRegions.stream().distinct().collect(Collectors.toList());
    }

    private boolean worthStoring(String issueRegion, String key) {
        List<String> regionList = logicalObjects.get(key).physicalObjects.values().stream()
                .filter(obj -> !obj.getLocationTag().equals(issueRegion) && obj.getStatus() == Status.ready)
                .map(PhysicalObject::getLocationTag)
                .collect(Collectors.toList());

        double netCost;
        if (regionList.isEmpty()) {
            // check that this object is located in issue_region
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

        if (curReq.containsKey(issueRegion)
                && curReq.get(issueRegion).containsKey(key)
                && requests.containsKey(issueRegion)
                && requests.get(issueRegion).containsKey(key)) {

            int curIdx = curReq.get(issueRegion).get(key);
            List<Long> reqList = requests.get(issueRegion).get(key);

            if (curIdx < reqList.size() - 1) {
                long timeDiff = reqList.get(curIdx + 1) - reqList.get(curIdx);
                // Compare with double teven
                return timeDiff <= teven;
            }
        }

        return false;
    }
}
