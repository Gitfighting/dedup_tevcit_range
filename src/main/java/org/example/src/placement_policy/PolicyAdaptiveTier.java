package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;
import org.example.src.utils.CostLogger;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * PolicyAdaptiveTier: Adaptive Tiering with Object Hotness and Hybrid
 * Redundancy
 * 
 * Combines PolicyTevictRangesNew's histogram-based TTL with:
 * - Object-level dual-timescale EWMA hotness modeling
 * - Global hotness-based grouping (HOT/WARM/COLD)
 * - Hybrid redundancy strategy (Replica for hot, EC for cold)
 */
public class PolicyAdaptiveTier extends PlacementPolicy {
    private final long windowSize;
    private final double avgNetworkCost;
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
    private final List<String> regions;
    private final Map<RegionPair, List<Double>> regionPairsTtl = new HashMap<>();
    private int debugCount = 0;
    private final double[] ranges;

    public PolicyAdaptiveTier(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);

        // Initialize PolicyTevictRangesNew fields
        this.windowSize = c.windowSize != null ? c.windowSize : -1;
        this.avgNetworkCost = getAvgNetworkCost(totalGraph);
        this.minNetworkCost = getMinNetworkCost(totalGraph);
        if (c.regions != null && !c.regions.isEmpty()) {
            this.regions = c.regions;
        } else {
            this.regions = new ArrayList<>(g.getNodes());
        }
        System.out.println("[DEBUG] PolicyAdaptiveTier regions: " + regions);

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

        // Initialize EC configuration
        this.ecEnabled = c.ecEnabled != null ? c.ecEnabled : true;
        int k = c.ecK != null ? c.ecK : 6;
        int m = c.ecM != null ? c.ecM : 3;

        this.ecManager = new ECManager(k, m, totalGraph,
                c.regions != null ? c.regions : new ArrayList<>(totalGraph.getNodes()));

        this.lastECCheck = new HashMap<>();

        String initMessage = "[PolicyAdaptiveTier] Initialized with EC(" + k + "+" + m + "), enabled=" + ecEnabled;
        System.out.println(initMessage);
        CostLogger.log(initMessage);
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
                            "[AdaptiveTier] transfer cost unavailable for '" + region + "'->'" + region2 + "': " + costObj);
                    double netCost = ((Number) costObj).doubleValue();

                    Map<String, Object> nodeAttrs = totalGraph.getNodeAttrs(region2);
                    Object priceObj = nodeAttrs.get("priceStorage");
                    if (!(priceObj instanceof Number) || ((Number) priceObj).doubleValue() <= 0)
                        throw new IllegalStateException(
                            "[AdaptiveTier] priceStorage unavailable for '" + region2 + "': " + priceObj);
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

    // Object grouping enum
    public enum ObjectGroup {
        HOT, // hotness > 0.7
        WARM, // hotness 0.3-0.7
        COLD // hotness < 0.3
    }

    // EC Manager
    private final ECManager ecManager;
    private final boolean ecEnabled;

    // Debug logging
    private static int debugLogCount = 0;
    private static final int DEBUG_LOG_LIMIT = 100;

    // EC check throttling
    private final Map<String, Instant> lastECCheck;
    private final long EC_CHECK_INTERVAL = 3600; // 1 hour in seconds

    @Override
    public List<String> place(Request req, Config config) {
        // Step 1: PolicyTevictRangesNew base logic (histogram TTL)
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

        // Step 2: Get or create LogicalObjectV2
        LogicalObject baseObj = logicalObjects.get(req.objKey);
        LogicalObjectV2 obj = null;

        if (baseObj instanceof LogicalObjectV2) {
            obj = (LogicalObjectV2) baseObj;
        } else if (baseObj != null) {
            // Convert to V2
            obj = new LogicalObjectV2(
                    baseObj.getKey(),
                    baseObj.getSize(),
                    baseObj.getLastModified(),
                    baseObj.getStatus());
            // Copy physical objects
            for (Map.Entry<String, PhysicalObject> entry : baseObj.physicalObjects.entrySet()) {
                obj.physicalObjects.put(entry.getKey(), entry.getValue());
            }
            logicalObjects.put(req.objKey, obj);
        }

        if (obj != null) {
            // Step 3: Update object hotness
            obj.recordAccess(req);

            // Step 4: Classify object globally
            ObjectGroup group = classifyObjectGlobally(obj, req.timestamp);

            // Step 5: EC decision (only for COLD group)
            if (ecEnabled && group == ObjectGroup.COLD && shouldConsiderEC(obj, req.timestamp)) {
                checkAndTransitionToEC(obj, req.timestamp);
            }
            // ecEnabled = true (假设) config.ecEnabled
            // group = COLD ✓
            // 进入 shouldConsiderEC() 检查
        }
        return placeRegions.stream().distinct().collect(Collectors.toList());
        // List<String> 仅是list，无区别，被简化了
    }

    /**
     * Classify object into global group based on hotness
     */
    private ObjectGroup classifyObjectGlobally(LogicalObjectV2 obj, Instant currentTime) {
        double globalHotness = calculateGlobalHotness(obj, currentTime);

        if (globalHotness > 0.7) {
            return ObjectGroup.HOT;
        } else if (globalHotness > 0.3) {
            return ObjectGroup.WARM;
        } else {
            return ObjectGroup.COLD;
        }
    }

    /**
     * Calculate global hotness using dual-timescale EWMA
     * 
     * Fast EWMA (α=0.3, decay=0.0001/s): detects burst traffic
     * Slow EWMA (α=0.05, decay=0.00001/s): captures long-term popularity
     * Global hotness: max(fast, slow)
     * 
     * Advantages:
     * - No manual thresholds or heuristics
     * - Time dimension preserved through exponential decay
     * - Naturally handles both burst and sustained access
     * - Easily extendable to per-region tracking
     */
    private double calculateGlobalHotness(LogicalObjectV2 obj, Instant currentTime) {
        double fastHotness = obj.getFastHotness(currentTime);
        double slowHotness = obj.getSlowHotness(currentTime);

        // Conservative: if either signal indicates hotness, treat as hot
        return Math.max(fastHotness, slowHotness);
    }

    /**
     * Check if should consider EC transition
     */
    private boolean shouldConsiderEC(LogicalObjectV2 obj, Instant currentTime) {
        String key = obj.getKey();

        // Throttle checks
        Instant lastCheck = lastECCheck.get(key);
        if (lastCheck != null &&
                currentTime.getEpochSecond() - lastCheck.getEpochSecond() < EC_CHECK_INTERVAL) {
            return false;
        }
        lastECCheck.put(key, currentTime);

        // Must have enough replicas 副本数量 >= 3
        if (obj.getReplicaCount() < 3) {
            return false;
        }

        // Must not be in EC state already 未处于EC状态
        if (obj.isInECState()) {
            return false;
        }

        // Must be old enough 对象年龄 >= 24小时
        if (obj.getAgeHours(currentTime) < 24) {
            return false;
        }

        // Must be idle enough 空闲时间 >= 12小时
        if (obj.getHoursSinceLastAccess(currentTime) < 12) {
            return false;
        }

        return true;
    }

    /**
     * Check and execute EC transition if cost-effective
     */
    private void checkAndTransitionToEC(LogicalObjectV2 obj, Instant currentTime) {
        String key = obj.getKey();

        if (debugLogCount < DEBUG_LOG_LIMIT) {
            CostLogger.log(String.format("[EC-CHECK #%d] Object=%s, Replicas=%d, Hotness=%.4f",
                    debugLogCount + 1, key, obj.getReplicaCount(),
                    obj.getCurrentHotness(currentTime)));
            debugLogCount++;
        }

        // Ask ECManager if transition is cost-effective
        if (ecManager.shouldTransitionToEC(obj, currentTime)) {
            if (debugLogCount < DEBUG_LOG_LIMIT) {
                long idleSeconds = Duration.between(obj.getLastAccessTimeInstant(), currentTime).getSeconds();
                CostLogger.log(String.format("[EC-DECISION] ✓ TRANSITION object=%s (replicas=%d, idleTime=%d sec)",
                        key, obj.getReplicaCount(), idleSeconds));
                debugLogCount++;
            }

            // Execute EC transition (from PolicyHyStoreEC)
            executeECTransition(obj);
        } else {
            if (debugLogCount < DEBUG_LOG_LIMIT) {
                CostLogger.log(String.format("[EC-DECISION] ✗ SKIP object=%s (not cost-effective)", key));
                debugLogCount++;
            }
        }
    }

    /**
     * Execute EC transition with unified TTL management
     */
    private void executeECTransition(LogicalObjectV2 obj) {
        // Build access pattern map
        Map<String, Double> accessPatterns = new HashMap<>();
        for (String region : config.regions != null ? config.regions : totalGraph.getNodes()) {
            accessPatterns.put(region, 0.1); // Uniform for now
        }

        // Step 1: 规划块放置 Plan EC placement
        Map<String, List<Integer>> placement = ecManager.planECPlacement(obj, accessPatterns);

        // Step 2: 生成 EC group ID Generate EC group ID
        String ecGroupId = UUID.randomUUID().toString();

        // Step 3: 计算统一TTL（取所有副本中的最大值）
        // 使用最大TTL确保EC块至少和最长存活的副本一样久
        int ttl = obj.physicalObjects.values().stream()
                .mapToInt(PhysicalObject::getTtl)
                .max()
                .orElse(-1); // 如果没有副本，默认为-1（无限）

        // Step 4: 创建 EC 块 Create EC chunks
        Map<String, List<PhysicalObjectV2>> chunks = ecManager.createECChunks(obj, placement, ecGroupId, ttl);

        // Step 5: 更新 LogicalObject 状态 Update LogicalObject state
        obj.convertToLRC(ecManager.getK(), 0, ecManager.getM());
        obj.setECGroupId(ecGroupId);

        // Step 6: 删除旧副本 Remove old replicas and add EC chunks
        List<String> oldRegions = new ArrayList<>(obj.physicalObjects.keySet());
        for (String region : oldRegions) {
            obj.physicalObjects.remove(region);
        }
        // Step 7: 添加 EC chunks
        for (Map.Entry<String, List<PhysicalObjectV2>> entry : chunks.entrySet()) {
            String region = entry.getKey();
            for (PhysicalObjectV2 chunk : entry.getValue()) {
                obj.addPhysicalObject(region + "_" + chunk.getChunkIndex(), chunk);
            }
        }

        CostLogger.log(String.format("[EC-TRANSITION] ✓ Converted %s to EC (RS(%d+%d)), unifiedTTL=%d",
                obj.getKey(), ecManager.getK(), ecManager.getM(), ttl));
    }
}
