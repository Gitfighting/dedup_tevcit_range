package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;
import org.example.src.model.Status;

import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;

/**
 * EWMA策略：指数加权移动平均预测策略
 * Python: class EWMA(PlacementPolicy)
 */
public class PolicyEwma extends PlacementPolicy {
    // Key: region + ":" + objKey, Value: [Tcur, Tprev]
    private final Map<String, double[]> tnextVars = new HashMap<>();
    // Key: region + ":" + objKey, Value: Last Request Timestamp
    private final Map<String, Instant> lastRequest = new HashMap<>();

    private final double initialTPrev = 0.0;
    private final double alpha = 0.5;
    private final int factor = 30;
    private final double avgNetworkCost;

    public PolicyEwma(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
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

    private void addRequestToQueue(String region, String objKey, Instant timestamp) {
        String key = region + ":" + objKey;
        if (!tnextVars.containsKey(key)) {
            tnextVars.put(key, new double[] { 0.0, initialTPrev });
        }

        double tcur = tnextVars.get(key)[0];
        double tprev = tnextVars.get(key)[1];
        Instant lastReq = lastRequest.get(key);
        if (lastReq == null) {
            lastReq = Instant.ofEpochMilli(0); // Match Python's datetime.fromtimestamp(0)
        }

        // FIX: Use nanosecond precision to match Python's total_seconds() float return
        double newTcur = (timestamp.toEpochMilli() - lastReq.toEpochMilli()) / 1000.0;
        double newTprev = alpha * tcur + (1 - alpha) * tprev;

        tnextVars.put(key, new double[] { newTcur, newTprev });
        lastRequest.put(key, timestamp);
    }

    @Override
    public double estimateArrivalRecency(String region, String objKey) {
        String key = region + ":" + objKey;
        if (!tnextVars.containsKey(key)) {
            return initialTPrev;
        }
        double tcur = tnextVars.get(key)[0];
        double tprev = tnextVars.get(key)[1];
        return alpha * tcur + (1 - alpha) * tprev;
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;
        List<String> placeRegions = new ArrayList<>();

        if ("write".equals(op)) {
            placeRegions.add(issueRegion);
        } else {
            List<String> regionList = logicalObjects.get(key).physicalObjects.values().stream()
                    .filter(obj -> !obj.getLocationTag().equals(issueRegion) && obj.getStatus() == Status.ready)
                    .map(PhysicalObject::getLocationTag)
                    .collect(Collectors.toList());

            double netCost;
            if (regionList.isEmpty()) {
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

            Object priceObj = totalGraph.getNodeAttrs(issueRegion).get("priceStorage");
            double price = priceObj instanceof Number ? ((Number) priceObj).doubleValue() : 0.0;

            // Match Python: net_cost / price * 60 * 60 * 24 / 3
            double teven = (netCost / price * 60 * 60 * 24 / 3.0);

            addRequestToQueue(issueRegion, key, req.timestamp);
            double estimatedRecency = estimateArrivalRecency(issueRegion, key);

            // Match Python: estimated_recency * self.factor <= teven
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)
                    && estimatedRecency * factor <= teven) {
                placeRegions.add(issueRegion);
            }
        }

        return placeRegions;
    }
}
