package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * IndividualTTL策略：基于请求时间窗口的个性化TTL策略
 * Python: class IndividualTTL(PlacementPolicy)
 */
public class PolicyKeep extends PlacementPolicy {
    private final Map<String, Map<String, List<Long>>> requests = new HashMap<>(); // Unused in Python? Keeping
                                                                                   // structure but using requestTimes
                                                                                   // for logic.
    private final double avgNetworkCost;
    private final int factor = 1;
    private final long windowSize;
    // Map (Region, ObjKey) -> timestamps
    private final Map<RegionObjectKey, Deque<LocalDateTime>> requestTimes = new HashMap<>();

    public PolicyKeep(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.avgNetworkCost = getAvgNetworkCost(g);
        // Python: self.window_size = self.config.window_size (default handling implies
        // 1 day if not set in config, or handled elsewhere)
        this.windowSize = c.windowSize != null ? c.windowSize : 86400; // default 1 day in seconds
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

    private void addRequestToQueue(String region, String objKey, LocalDateTime timestamp) {
        RegionObjectKey key = new RegionObjectKey(region, objKey);
        requestTimes.putIfAbsent(key, new ArrayDeque<>());

        Deque<LocalDateTime> requestQueue = requestTimes.get(key);
        requestQueue.add(timestamp);

        // Remove oldest requests if window size is exceeded
        while (!requestQueue.isEmpty() &&
                ChronoUnit.SECONDS.between(requestQueue.peekFirst(), timestamp) > windowSize) {
            requestQueue.pollFirst();
        }
    }

    @Override
    public double estimateArrivalRecency(String region, String objKey) {
        RegionObjectKey key = new RegionObjectKey(region, objKey);
        if (!requestTimes.containsKey(key)) {
            return 0;
        }

        Deque<LocalDateTime> requestQueue = requestTimes.get(key);
        if (requestQueue.isEmpty()) {
            return 0;
        }

        // Python logic: sum(diffs) / len(queue)
        // Note: Python's implementation divides by len(queue), not len-1. We strictly
        // mimic this.
        List<LocalDateTime> times = new ArrayList<>(requestQueue);
        double sum = 0;
        for (int i = 1; i < times.size(); i++) {
            sum += ChronoUnit.SECONDS.between(times.get(i - 1), times.get(i));
        }
        return sum / times.size();
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;
        List<String> placeRegions = new ArrayList<>();
        LocalDateTime requestTime = LocalDateTime.ofInstant(req.timestamp, java.time.ZoneOffset.UTC);

        if ("write".equals(op)) {
            placeRegions.add(issueRegion);
            addRequestToQueue(issueRegion, key, requestTime);
        } else {
            // read case
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

            Object priceStorage = totalGraph.getNodeAttrs(issueRegion).get("priceStorage");
            double price = priceStorage instanceof Number ? ((Number) priceStorage).doubleValue() : 0.0;

            // Match Python: net_cost / (price * 3) * 60 * 60 * 24
            // Ensure double precision
            double teven = (netCost / (price * 3.0) * 60 * 60 * 24);

            addRequestToQueue(issueRegion, key, requestTime);
            double estimatedRecency = estimateArrivalRecency(issueRegion, key);

            if ("read".equals(op)) {
                if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion) &&
                        estimatedRecency <= teven) {
                    placeRegions.add(issueRegion);
                }
            }
        }

        return placeRegions.stream().distinct().collect(Collectors.toList());
    }

    private static class RegionObjectKey {
        final String region;
        final String objKey;

        RegionObjectKey(String region, String objKey) {
            this.region = region;
            this.objKey = objKey;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o)
                return true;
            if (o == null || getClass() != o.getClass())
                return false;
            RegionObjectKey that = (RegionObjectKey) o;
            return Objects.equals(region, that.region) && Objects.equals(objKey, that.objKey);
        }

        @Override
        public int hashCode() {
            return Objects.hash(region, objKey);
        }
    }
}
