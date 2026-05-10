package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * TevictV2策略：基于请求直方图的自适应TTL驱逐策略
 * Python: class TevictV2(PlacementPolicy)
 */
public class PolicyTevictNew extends PlacementPolicy {
    private final long windowSize;
    private final double avgNetworkCost;
    private final double avgStorageCost;
    private final double minNetworkCost;

    private final Map<String, Map<String, List<Double>>> pastRequests = new HashMap<>();
    private final Map<String, Map<Integer, Double>> hist = new HashMap<>();
    private final Map<String, Map<Integer, Double>> lastHist = new HashMap<>();
    private final Map<String, Integer> numRequests = new HashMap<>();

    private final Map<String, Map<String, List<Double>>> nextPastRequests = new HashMap<>();
    private final Map<String, Map<Integer, Double>> nextHist = new HashMap<>();
    private final Map<String, Map<Integer, Double>> nextLastHist = new HashMap<>();
    private final Map<String, Integer> nextNumRequests = new HashMap<>();

    private LocalDateTime lastUpdated;
    private final Map<String, List<Double>> ttls = new HashMap<>();
    private LocalDateTime timePassed;
    private final Map<String, List<LocalDateTime>> seenDays = new HashMap<>();
    private final Map<String, String> removeImmediately = new HashMap<>();
    private final List<String> regions;
    private final Map<RegionPair, List<Double>> regionPairsTtl = new HashMap<>();
    private final int k = 12;
    private int c = 0;

    public PolicyTevictNew(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.windowSize = c.windowSize != null ? c.windowSize : -1;
        this.avgNetworkCost = getAvgNetworkCost(g);
        this.avgStorageCost = getAvgStorageCost(g);
        this.minNetworkCost = getMinNetworkCost(g);
        if (c.regions != null && !c.regions.isEmpty()) {
            this.regions = c.regions;
        } else {
            this.regions = new ArrayList<>(g.getNodes());
        }

        if (windowSize != -1) {
            this.lastUpdated = null;
        } else {
            this.lastUpdated = LocalDateTime.of(1970, 1, 1, 0, 0);
        }

        for (String region : regions) {
            for (String region2 : regions) {
                regionPairsTtl.put(new RegionPair(region, region2), new ArrayList<>());
            }
        }
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

    private double getAvgStorageCost(DiGraph graph) {
        double sum = 0.0;
        int count = 0;
        for (String node : graph.getNodes()) {
            Object price = graph.getNodeAttrs(node).get("priceStorage");
            if (price instanceof Number) {
                sum += ((Number) price).doubleValue();
                count++;
            }
        }
        return count > 0 ? sum / count : 0.0;
    }

    private double getMinNetworkCost(DiGraph graph) {
        double min = Double.MAX_VALUE;
        for (String src : graph.getNodes()) {
            for (String dst : graph.getSuccessors(src)) {
                Object cost = graph.getEdgeAttrs(src, dst).get("cost");
                if (cost instanceof Number) {
                    min = Math.min(min, ((Number) cost).doubleValue());
                }
            }
        }
        return min < Double.MAX_VALUE ? min : 0.0;
    }

    private boolean isNextWindow(LocalDateTime oldDatetime, LocalDateTime newDatetime) {
        long hoursDiff = ChronoUnit.HOURS.between(oldDatetime, newDatetime);
        return hoursDiff > windowSize;
    }

    private boolean isLargeTimeGap(LocalDateTime oldDatetime, LocalDateTime newDatetime) {
        long hoursDiff = Math.abs(ChronoUnit.HOURS.between(oldDatetime, newDatetime));
        return hoursDiff > windowSize * 2;
    }

    private LocalDateTime roundToNextHour(LocalDateTime dt) {
        if (dt.getMinute() != 0 || dt.getSecond() != 0 || dt.getNano() != 0) {
            dt = dt.plusHours(1).withMinute(0).withSecond(0).withNano(0);
        } else {
            dt = dt.plusHours(1);
        }
        return dt;
    }

    private long roundToNextHourSeconds(long dt) {
        return (dt / 3600 + 1) * 3600;
    }

    public void updatePastRequests(Request request, String placeRegion) {
        c++;
        LocalDateTime requestTime = LocalDateTime.ofInstant(request.timestamp, java.time.ZoneOffset.UTC);
        if (windowSize != -1) {
            if (lastUpdated == null || isLargeTimeGap(lastUpdated, requestTime)) {
                lastUpdated = requestTime.withMinute(0).withSecond(0).withNano(0);
                pastRequests.clear();
                hist.clear();
                numRequests.clear();
                lastHist.clear();
                nextLastHist.clear();
                nextPastRequests.clear();
                nextHist.clear();
                nextNumRequests.clear();
            } else if (isNextWindow(lastUpdated, requestTime)) {
                lastUpdated = lastUpdated.plusHours(windowSize);
                pastRequests.clear();
                pastRequests.putAll(nextPastRequests);
                hist.clear();
                hist.putAll(nextHist);
                lastHist.clear();
                lastHist.putAll(nextLastHist);
                numRequests.clear();
                numRequests.putAll(nextNumRequests);
                nextPastRequests.clear();
                nextHist.clear();
                nextNumRequests.clear();
                nextLastHist.clear();
            }
        }

        nextNumRequests.merge(placeRegion, 1, Integer::sum);
        nextPastRequests.putIfAbsent(placeRegion, new HashMap<>());

        long reqToSec = ChronoUnit.SECONDS.between(LocalDateTime.of(1970, 1, 1, 0, 0), requestTime);

        if (nextPastRequests.get(placeRegion).containsKey(request.objKey)) {
            List<Double> timestamps = nextPastRequests.get(placeRegion).get(request.objKey);
            long tnext = (long) (reqToSec - timestamps.get(timestamps.size() - 1));
            if (tnext == 0)
                tnext = 1;

            long currentWindow = (long) Math
                    .ceil((roundToNextHourSeconds((long) timestamps.get(timestamps.size() - 1).doubleValue())
                            - timestamps.get(timestamps.size() - 1)) / 3600.0);

            if (nextLastHist.containsKey(placeRegion)
                    && nextLastHist.get(placeRegion).containsKey((int) currentWindow)) {
                nextLastHist.get(placeRegion).merge((int) currentWindow, -request.size.doubleValue() / 1073741824.0,
                        Double::sum);
            }

            nextHist.putIfAbsent(placeRegion, new HashMap<>());
            int tnextHours = (int) Math.ceil(tnext / 3600.0);
            nextHist.get(placeRegion).merge(tnextHours, request.size.doubleValue() / 1073741824.0, Double::sum);
            timestamps.add((double) reqToSec);

            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            long tendWindow = ChronoUnit.SECONDS.between(requestTime, roundToNextHour(requestTime)) / 3600;
            nextLastHist.get(placeRegion).merge((int) tendWindow, request.size.doubleValue() / 1073741824.0,
                    Double::sum);
        } else {
            nextHist.putIfAbsent(placeRegion, new HashMap<>());
            nextPastRequests.get(placeRegion).put(request.objKey,
                    new ArrayList<>(Collections.singletonList((double) reqToSec)));
            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            long tendWindow = ChronoUnit.SECONDS.between(requestTime, roundToNextHour(requestTime)) / 3600;
            nextLastHist.get(placeRegion).merge((int) tendWindow, request.size.doubleValue() / 1073741824.0,
                    Double::sum);
        }

        timePassed = requestTime;
    }

    /**
     * Calculate eviction cost for different TTL values
     * Python: calc_evict_cost(self, region, teven_hours, net_cost,
     * storage_cost_hour)
     */
    public double[] calcEvictCost(String region, double tevenHours, double netCost, double storageCostHour) {
        Map<Integer, Double> X;
        Map<Integer, Double> lastX;

        if (windowSize == -1) {
            if (!nextHist.containsKey(region) || !nextLastHist.containsKey(region)) {
                return new double[] { -1, -1 };
            }
            X = nextHist.get(region);
            lastX = nextLastHist.get(region);
        } else {
            if (!hist.containsKey(region) || !lastHist.containsKey(region)) {
                return new double[] { -1, -1 };
            }
            X = hist.get(region);
            lastX = lastHist.get(region);
        }

        Map<Integer, Double> costHist = new HashMap<>();
        int maxKey = X.keySet().isEmpty() ? 1 : X.keySet().stream().max(Integer::compareTo).get();

        int ret = 0;
        int loopLimit = Math.min(maxKey + 1, (int) Math.ceil(tevenHours));

        for (int c = 0; c < loopLimit; c++) {
            costHist.put(c, 0.0);
            int tevict = c;

            for (Integer i : X.keySet()) {
                if (i <= tevict) {
                    assert i >= 1;
                    costHist.put(c, costHist.get(c) + X.get(i) * ((i - 1) + 0.6) * storageCostHour);
                } else {
                    costHist.put(c, costHist.get(c) + X.get(i) * (tevict * storageCostHour + netCost));
                }

                costHist.put(c, costHist.get(c) + lastX.getOrDefault(i, 0.0) * (tevict * storageCostHour));
            }

            if (costHist.get(c) <= costHist.get(ret)) {
                ret = c;
            }
        }

        return new double[] { ret, costHist.get(ret) };
    }

    /**
     * Get TTL for object transfer from src to dst
     * Python: get_tevict(self, src, dst, timestamp)
     */
    public double getTevict(String src, String dst, LocalDateTime timestamp) {
        if (seenDays.containsKey(dst) && !seenDays.get(dst).isEmpty() &&
                timestamp.isBefore(seenDays.get(dst).get(seenDays.get(dst).size() - 1).plusHours(k))) {
            List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
            if (!ttlList.isEmpty()) {
                return ttlList.get(ttlList.size() - 1);
            }
        }

        // Calculate TTL for all region pairs
        for (String region : regions) {
            for (String region2 : regions) {
                if (!region.equals(region2)) {
                    Map<String, Object> edgeAttrs = totalGraph.getEdgeAttrs(region, region2);
                    double netCost = edgeAttrs.get("cost") instanceof Number
                            ? ((Number) edgeAttrs.get("cost")).doubleValue()
                            : 0.0;

                    Map<String, Object> nodeAttrs = totalGraph.getNodeAttrs(region2);
                    double storage = nodeAttrs.get("priceStorage") instanceof Number
                            ? ((Number) nodeAttrs.get("priceStorage")).doubleValue() * 3
                            : 0.0;

                    double storageCostPerHour = storage / 24.0;
                    double teven = netCost / storage * 60 * 60 * 24;

                    double[] evictResult = calcEvictCost(region2, teven / 3600.0, netCost, storageCostPerHour);
                    double bestTtl = evictResult[0];

                    double calculatedTtl = findMin(bestTtl, teven, region2, timestamp);
                    regionPairsTtl.get(new RegionPair(region, region2)).add(calculatedTtl);
                }
            }
        }

        List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
        return ttlList.isEmpty() ? 0.0 : ttlList.get(ttlList.size() - 1);
    }

    /**
     * Find minimum TTL based on past data or default to teven/2
     * Python: find_min(self, y, teven, region, timestamp)
     */
    public double findMin(double y, double teven, String region, LocalDateTime timestamp) {
        boolean noPastData = false;
        double ttl;
        int numReqs = 0;

        if (windowSize == -1) {
            if (!nextNumRequests.containsKey(region)) {
                noPastData = true;
                ttl = teven / 3600.0 / 2.0;
            } else {
                numReqs = nextNumRequests.get(region);
            }
        } else {
            if (!numRequests.containsKey(region)) {
                noPastData = true;
                ttl = teven / 3600.0 / 2.0;
            } else {
                numReqs = numRequests.get(region);
            }
        }

        if (!noPastData) {
            if (y == -1 || lastUpdated == null || numReqs < 1000) {
                ttl = teven / 3600.0 / 2.0;
            } else {
                ttl = y;
            }
        } else {
            ttl = teven / 3600.0 / 2.0;
        }

        if (!seenDays.containsKey(region)) {
            seenDays.put(region, new ArrayList<>());
            ttls.put(region, new ArrayList<>());
        }

        List<LocalDateTime> regionSeenDays = seenDays.get(region);
        if (regionSeenDays.isEmpty() ||
                !timestamp.isBefore(regionSeenDays.get(regionSeenDays.size() - 1).plusHours(k))) {
            regionSeenDays.add(timestamp);
            ttls.get(region).add(ttl);
        }

        return ttl * 3600;
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
            // read case
            if (!logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                placeRegions.add(issueRegion);
            }
        }

        return placeRegions.stream().distinct().collect(Collectors.toList());
    }

    private static class RegionPair {
        final String region1;
        final String region2;

        RegionPair(String region1, String region2) {
            this.region1 = region1;
            this.region2 = region2;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o)
                return true;
            if (o == null || getClass() != o.getClass())
                return false;
            RegionPair that = (RegionPair) o;
            return Objects.equals(region1, that.region1) && Objects.equals(region2, that.region2);
        }

        @Override
        public int hashCode() {
            return Objects.hash(region1, region2);
        }
    }
}
