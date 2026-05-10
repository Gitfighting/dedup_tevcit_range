package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * TevictRangesV2策略：使用范围分组的自适应TTL驱逐策略
 * Python: class TevictRangesV2(PlacementPolicy)
 */
public class PolicyTevictRangesNew extends PlacementPolicy {
    private final long windowSize;
    protected final double avgNetworkCost;
    protected final double minNetworkCost;

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
    protected final List<String> regions;
    private final Map<RegionPair, List<Double>> regionPairsTtl = new HashMap<>();
    private int debugCount = 0;
    private final double[] ranges;

    public PolicyTevictRangesNew(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.windowSize = c.windowSize != null ? c.windowSize : -1;
        this.avgNetworkCost = getAvgNetworkCost(totalGraph);
        this.minNetworkCost = getMinNetworkCost(totalGraph);
        if (c.regions != null && !c.regions.isEmpty()) {
            this.regions = c.regions;
        } else {
            this.regions = new ArrayList<>(g.getNodes());
        }
        System.out.println("[DEBUG] PolicyTevictRangesNew regions: " + regions);

        // Initialize ranges (2% exponential growth)
        double SEC = 1000;
        double MIN = 60 * SEC;
        double HOUR = 60 * MIN;
        double MIN_IN_HOUR = 0.01666;
        double s = MIN_IN_HOUR * HOUR;

        List<Double> rangesList = new ArrayList<>();
        rangesList.add(0.0);
        for (int i = 0; i < 700; i++) {
            rangesList.add(s * Math.pow(1 + 0.02, i) / HOUR);
        }
        ranges = rangesList.stream().mapToDouble(Double::doubleValue).toArray();

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

    private double roundToNextHourSeconds(double dt) {
        return (Math.floor(dt / 3600.0) + 1) * 3600.0;
    }

    private int findNextIndexInRange(double tnext) {
        double tnextInHours = tnext / 3600.0;
        for (int i = 0; i < ranges.length; i++) {
            if (tnextInHours <= ranges[i]) {
                return i;
            }
        }
        return ranges.length - 1;
    }

    public void updatePastRequests(Request request, String placeRegion) {
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

        // FIX: Use double precision for seconds to match Python
        double reqToSec = Duration.between(LocalDateTime.of(1970, 1, 1, 0, 0), requestTime).toNanos() / 1_000_000_000.0;

        if (nextPastRequests.get(placeRegion).containsKey(request.objKey)) {
            List<Double> timestamps = nextPastRequests.get(placeRegion).get(request.objKey);
            // FIX: tnext should be double to preserve precision
            double tnext = reqToSec - timestamps.get(timestamps.size() - 1);
            if (tnext == 0)
                tnext = 1.0;

            // FIX: Preserve floating-point precision in timestamp calculations
            double lastTimestamp = timestamps.get(timestamps.size() - 1);
            long currentWindow = (long) Math.ceil((roundToNextHourSeconds(lastTimestamp) - lastTimestamp) / 3600.0);

            if (nextLastHist.containsKey(placeRegion)
                    && nextLastHist.get(placeRegion).containsKey((int) currentWindow)) {
                nextLastHist.get(placeRegion).merge((int) currentWindow, -request.size.doubleValue() / 1073741824.0,
                        Double::sum);
            }

            nextHist.putIfAbsent(placeRegion, new HashMap<>());
            int nextIndexInRange = findNextIndexInRange(tnext);
            nextHist.get(placeRegion).merge(nextIndexInRange, request.size.doubleValue() / 1073741824.0, Double::sum);
            timestamps.add(reqToSec);

            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            // FIX: tendWindow calculation must use floating point division before ceil
            long tendWindow = (long) Math.ceil(
                    Duration.between(requestTime, roundToNextHour(requestTime)).toNanos() / 1_000_000_000.0 / 3600.0);
            nextLastHist.get(placeRegion).merge((int) tendWindow, request.size.doubleValue() / 1073741824.0,
                    Double::sum);
        } else {
            nextHist.putIfAbsent(placeRegion, new HashMap<>());
            nextPastRequests.get(placeRegion).put(request.objKey,
                    new ArrayList<>(Collections.singletonList(reqToSec)));
            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            // FIX: tendWindow calculation must use floating point division before ceil
            long tendWindow = (long) Math.ceil(
                    Duration.between(requestTime, roundToNextHour(requestTime)).toNanos() / 1_000_000_000.0 / 3600.0);
            nextLastHist.get(placeRegion).merge((int) tendWindow, request.size.doubleValue() / 1073741824.0,
                    Double::sum);
        }

        timePassed = requestTime;
    }

    /**
     * Calculate eviction cost for different TTL values using range-based histogram
     * Python: calc_evict_cost(self, region, teven_hours, net_cost)
     */
    public double calcEvictCost(String region, double tevenHours, double netCost) {
        Map<Integer, Double> X;
        Map<Integer, Double> lastX;

        if (windowSize == -1) {
            if (!nextHist.containsKey(region) || !nextLastHist.containsKey(region)) {
                return -1;
            }
            X = nextHist.get(region);
            lastX = nextLastHist.get(region);
        } else {
            if (!hist.containsKey(region) || !lastHist.containsKey(region)) {
                return -1;
            }
            X = hist.get(region);
            lastX = lastHist.get(region);
        }

        double storageCostHour;
        if (tevenHours == 0) {
            storageCostHour = avgNetworkCost / tevenHours;
        } else {
            storageCostHour = netCost / tevenHours;
        }

        Map<Integer, Double> costHist = new HashMap<>();

        int ret = 0;

        for (int c = 0; c < ranges.length; c++) {
            costHist.put(c, 0.0);
            double tevict = ranges[c];

            for (int i = 0; i < ranges.length; i++) {
                if (X.getOrDefault(i, 0.0) == 0) {
                    continue;
                }
                if (tevict == 0) {
                    costHist.put(c, costHist.get(c) + X.get(i) * netCost);
                    continue;
                }
                if (ranges[i] <= tevict) {
                    double avgRange = (ranges[i] + (i != 0 ? ranges[i - 1] : 0)) / 2.0;
                    costHist.put(c, costHist.get(c) + X.get(i) * avgRange * storageCostHour);
                } else {
                    costHist.put(c, costHist.get(c) + X.get(i) * (tevict * storageCostHour + netCost));
                }
            }

            for (int i = 0; i < ranges.length; i++) {
                if (lastX.getOrDefault(i, 0.0) == 0) {
                    continue;
                }
                costHist.put(c, costHist.get(c) + lastX.get(i) * (tevict * storageCostHour));
            }

            if (costHist.get(c) <= costHist.get(ret)) {
                ret = c;
            }
        }

        return ranges[ret];
    }

    /**
     */
    @Override
    public double getTevict(String src, String dst, java.time.Instant timestampInstant) {
        LocalDateTime timestamp = LocalDateTime.ofInstant(timestampInstant, java.time.ZoneOffset.UTC);
        if (debugCount < 100) { // Debugging
            System.out.println("[DEBUG] getTevict STARTED for src=" + src + " dst=" + dst);
            debugCount++;
        }
        if (seenDays.containsKey(dst) && !seenDays.get(dst).isEmpty() &&
                timestamp.isBefore(seenDays.get(dst).get(seenDays.get(dst).size() - 1).plusHours(12))) {
            List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
            if (!ttlList.isEmpty()) {
                return ttlList.get(ttlList.size() - 1);
            }
        }

        for (String region : regions) {
            for (String region2 : regions) {
                if (!region.equals(region2)) {
                    Map<String, Object> edgeAttrs = totalGraph.getEdgeAttrs(region, region2);
                    Object costObj = edgeAttrs.get("cost");
                    if (!(costObj instanceof Number))
                        throw new IllegalStateException(
                            "[TevictRanges] transfer cost unavailable for '" + region + "'->'" + region2 + "': " + costObj);
                    double netCost = ((Number) costObj).doubleValue();

                    Map<String, Object> nodeAttrs = totalGraph.getNodeAttrs(region2);
                    Object priceObj = nodeAttrs.get("priceStorage");
                    if (!(priceObj instanceof Number) || ((Number) priceObj).doubleValue() <= 0)
                        throw new IllegalStateException(
                            "[TevictRanges] priceStorage unavailable for '" + region2 + "': " + priceObj);
                    double storage = ((Number) priceObj).doubleValue();

                    double teven = netCost / storage * 60 * 60 * 24;

                    double calculatedCost = calcEvictCost(region2, teven / 3600.0, netCost);

                    double calculatedTtl = findMin(calculatedCost, teven, region2, timestamp);
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
                !timestamp.isBefore(regionSeenDays.get(regionSeenDays.size() - 1).plusHours(12))) {
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
