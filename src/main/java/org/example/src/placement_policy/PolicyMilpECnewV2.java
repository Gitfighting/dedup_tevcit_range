package org.example.src.placement_policy;

import com.gurobi.gurobi.*;
import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.utils.Definitions;
import org.example.src.utils.Logger;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;

/**
 * PolicyMilpECnewV2：EC(2+0) 策略（策略隔离，不继承 PolicyTevictRangesNew）
 *
 * 关键词：TTL, 直方图, 副本, EC(2+0), MILP
 *
 * 直方图 TTL 计算：本类内完整实现与 PolicyTevictRangesNew 相同的算法（范围分组、calcEvictCost、findMin）。
 * 与 PolicyMilpECnew 差异：
 *   - EC(2+0)：2 数据片，0 校验片（需 2 片重构）
 *   - 副本 TTL：直方图 + calcEvictCost（本类实现）
 *   - EC 片 TTL：与 local region 一致（使用 tauH，非 MILP hatTau）
 *   - 驱逐规则：一片到期全部删除；删除前检查，仅删冗余
 */
public class PolicyMilpECnewV2 extends PlacementPolicy {

    // ── EC(2+0) 参数 ──
    private static final int EC_K = 2;
    private static final int EC_M = 0;
    private static final int EC_N = EC_K + EC_M; // 2

    // =====================================================================
    // 直方图 TTL 模型（与 PolicyTevictRangesNew 相同算法，本类内实现）
    // =====================================================================

    private final long windowSize;
    private final double avgNetworkCost;
    private final double minNetworkCost;
    private final List<String> regions;
    private final double[] ranges;

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
    private final Map<RegionPair, List<Double>> regionPairsTtl = new HashMap<>();

    // =====================================================================
    // MILP 相关
    // =====================================================================

    public static class RegionStats {
        double writeSizeGB = 0;
        double readSizeGB  = 0;
        void addWrite(double sizeGB) { writeSizeGB += sizeGB; }
        void addRead (double sizeGB) { readSizeGB  += sizeGB; }
        boolean empty() { return writeSizeGB <= 0 && readSizeGB <= 0; }
    }

    public static class RegionDecision {
        public final int mode;       // 0=副本, 1=EC
        public final boolean[] shardAt;
        public final double ttlHours;
        public final long decidedAtSec;

        RegionDecision(int mode, boolean[] shardAt, double ttlHours, long decidedAtSec) {
            this.mode = mode;
            this.shardAt = shardAt;
            this.ttlHours = ttlHours;
            this.decidedAtSec = decidedAtSec;
        }

        boolean expired(long nowSec) {
            return ttlHours > 0 && nowSec >= decidedAtSec + (long)(ttlHours * 3600.0);
        }

        boolean hasLocalData(int regionIdx) {
            if (mode == 0) return true;
            return shardAt != null && regionIdx >= 0 && regionIdx < shardAt.length && shardAt[regionIdx];
        }
    }

    private final Map<String, RegionStats> windowStats = new LinkedHashMap<>();
    private final Map<String, RegionDecision> decisions = new HashMap<>();
    /** EC TTL 缓存（与 local region 一致，使用 tauH） */
    private final Map<String, Double> milpEcTtlByRegion = new HashMap<>();

    private long windowStartHours = -1;
    private final long windowSizeHours;
    private int windowEventCount = 0;
    private static final int WINDOW_EVENT_THRESHOLD = 500;

    private GRBEnv persistentEnv;
    private int reqCount = 0;
    private long firstReqMs = -1;
    private int windowCount = 0;

    // =====================================================================
    // 构造器
    // =====================================================================

    public PolicyMilpECnewV2(Config config, DiGraph totalGraph,
                             Map<String, LogicalObject> logicalObjects,
                             RegionManager regionManager) {
        super(config, totalGraph, logicalObjects, regionManager);
        this.windowSize = config.windowSize != null ? config.windowSize.longValue() : -1;
        this.avgNetworkCost = getAvgNetworkCost(totalGraph);
        this.minNetworkCost = getMinNetworkCost(totalGraph);
        if (config.regions != null && !config.regions.isEmpty()) {
            this.regions = config.regions;
        } else {
            this.regions = new ArrayList<>(totalGraph.getNodes());
        }
        long ws = config.windowSize != null ? config.windowSize.longValue() : 24L;
        this.windowSizeHours = ws > 0 ? ws : 24L;

        // 初始化 ranges（2% 指数增长，与 PolicyTevictRangesNew 一致）
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
        this.ranges = rangesList.stream().mapToDouble(Double::doubleValue).toArray();

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

    // =====================================================================
    // 直方图辅助方法
    // =====================================================================

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

    /** 直方图累积的数据量（GB）。EC 时每 region 存 shard=size/K。 */
    private double getHistogramSizeGB(Request request, String placeRegion) {
        LogicalObject lo = logicalObjects.get(request.objKey);
        if (lo == null || lo.physicalObjects == null) {
            return request.size.doubleValue() / Definitions.GB;
        }
        if (lo.physicalObjects.size() == EC_N) {
            return request.size.doubleValue() / Definitions.GB / EC_K;
        }
        return request.size.doubleValue() / Definitions.GB;
    }

    /**
     * 直方图累积（与 PolicyTevictRangesNew 相同算法）
     */
    private void updateHistogram(Request request, String placeRegion) {
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

        double sizeGB = getHistogramSizeGB(request, placeRegion);
        double reqToSec = Duration.between(LocalDateTime.of(1970, 1, 1, 0, 0), requestTime).toNanos() / 1_000_000_000.0;

        if (nextPastRequests.get(placeRegion).containsKey(request.objKey)) {
            List<Double> timestamps = nextPastRequests.get(placeRegion).get(request.objKey);
            double tnext = reqToSec - timestamps.get(timestamps.size() - 1);
            if (tnext == 0) tnext = 1.0;

            double lastTimestamp = timestamps.get(timestamps.size() - 1);
            long currentWindow = (long) Math.ceil((roundToNextHourSeconds(lastTimestamp) - lastTimestamp) / 3600.0);

            if (nextLastHist.containsKey(placeRegion)
                    && nextLastHist.get(placeRegion).containsKey((int) currentWindow)) {
                nextLastHist.get(placeRegion).merge((int) currentWindow, -sizeGB, Double::sum);
            }

            nextHist.putIfAbsent(placeRegion, new HashMap<>());
            int nextIndexInRange = findNextIndexInRange(tnext);
            nextHist.get(placeRegion).merge(nextIndexInRange, sizeGB, Double::sum);
            timestamps.add(reqToSec);

            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            long tendWindow = (long) Math.ceil(
                    Duration.between(requestTime, roundToNextHour(requestTime)).toNanos() / 1_000_000_000.0 / 3600.0);
            nextLastHist.get(placeRegion).merge((int) tendWindow, sizeGB, Double::sum);
        } else {
            nextHist.putIfAbsent(placeRegion, new HashMap<>());
            nextPastRequests.get(placeRegion).put(request.objKey,
                    new ArrayList<>(Collections.singletonList(reqToSec)));
            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            long tendWindow = (long) Math.ceil(
                    Duration.between(requestTime, roundToNextHour(requestTime)).toNanos() / 1_000_000_000.0 / 3600.0);
            nextLastHist.get(placeRegion).merge((int) tendWindow, sizeGB, Double::sum);
        }

        timePassed = requestTime;
    }

    /**
     * 基于直方图计算驱逐成本最优 TTL（与 PolicyTevictRangesNew 相同算法）
     */
    private double calcEvictCost(String region, double tevenHours, double netCost) {
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

        double storageCostHour = (tevenHours > 0) ? (netCost / tevenHours) : avgNetworkCost;

        Map<Integer, Double> costHist = new HashMap<>();
        int ret = 0;

        for (int c = 0; c < ranges.length; c++) {
            costHist.put(c, 0.0);
            double tevict = ranges[c];

            for (int i = 0; i < ranges.length; i++) {
                if (X.getOrDefault(i, 0.0) == 0) continue;
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
                if (lastX.getOrDefault(i, 0.0) == 0) continue;
                costHist.put(c, costHist.get(c) + lastX.get(i) * (tevict * storageCostHour));
            }

            if (costHist.get(c) <= costHist.get(ret)) {
                ret = c;
            }
        }

        return ranges[ret];
    }

    /**
     * 基于历史数据或 teven/2 确定最小 TTL（与 PolicyTevictRangesNew 相同算法）
     */
    private double findMin(double y, double teven, String region, LocalDateTime timestamp) {
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

    /**
     * 副本 TTL：遍历 region 对，计算直方图 TTL（与 PolicyTevictRangesNew 相同逻辑）
     */
    private double getReplicaTevict(String src, String dst, Instant timestampInstant) {
        LocalDateTime timestamp = LocalDateTime.ofInstant(timestampInstant, java.time.ZoneOffset.UTC);

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
                                "[MILP-ECnewV2] transfer cost unavailable for '" + region + "'->'" + region2 + "': " + costObj);
                    double netCost = ((Number) costObj).doubleValue();

                    Map<String, Object> nodeAttrs = totalGraph.getNodeAttrs(region2);
                    Object priceObj = nodeAttrs.get("priceStorage");
                    if (!(priceObj instanceof Number) || ((Number) priceObj).doubleValue() <= 0)
                        throw new IllegalStateException(
                                "[MILP-ECnewV2] priceStorage unavailable for '" + region2 + "': " + priceObj);
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

    // =====================================================================
    // place / getPlacementWithSizes / getReadTransferSize
    // =====================================================================

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            reqCount++;
            if (firstReqMs == -1) firstReqMs = System.currentTimeMillis();
            if (reqCount % 10000 == 0) {
                long elapsed = System.currentTimeMillis() - firstReqMs;
                Logger.info(String.format("[MILP-ECnewV2] progress: %d reqs, windows=%d, decisions=%d",
                        reqCount, windowCount, decisions.size()));
            }
            long reqH = req.timestamp.getEpochSecond() / 3600;
            if (windowStartHours == -1) windowStartHours = reqH;
            double sizeGB = req.size.doubleValue() / Definitions.GB;
            windowStats.computeIfAbsent(req.issueRegion, k -> new RegionStats()).addWrite(sizeGB);
            windowEventCount++;
            checkAndSolveWindow(req.timestamp);
        }
        if ("write".equals(req.op)) {
            RegionDecision dec = decisions.get(req.issueRegion);
            long nowSec = req.timestamp.getEpochSecond();
            if (dec != null && !dec.expired(nowSec) && dec.mode == 1 && dec.shardAt != null) {
                List<String> shardRegions = new ArrayList<>();
                for (int i = 0; i < dec.shardAt.length && i < regions.size(); i++) {
                    if (dec.shardAt[i]) shardRegions.add(regions.get(i));
                }
                if (shardRegions.size() == EC_N) return shardRegions;
            }
        }
        return Collections.singletonList(req.issueRegion);
    }

    @Override
    public Map<String, BigDecimal> getPlacementWithSizes(Request req, Config config) {
        if (!"write".equals(req.op)) return null;
        RegionDecision dec = decisions.get(req.issueRegion);
        long nowSec = req.timestamp.getEpochSecond();
        if (dec != null && !dec.expired(nowSec) && dec.mode == 1 && dec.shardAt != null) {
            List<String> shardRegions = new ArrayList<>();
            for (int i = 0; i < dec.shardAt.length && i < regions.size(); i++) {
                if (dec.shardAt[i]) shardRegions.add(regions.get(i));
            }
            if (shardRegions.size() == EC_N) {
                BigDecimal shardSize = req.size.divide(BigDecimal.valueOf(EC_K), 20, RoundingMode.HALF_UP);
                Map<String, BigDecimal> result = new LinkedHashMap<>();
                for (String r : shardRegions) result.put(r, shardSize);
                return result;
            }
        }
        return null;
    }

    @Override
    public BigDecimal getReadTransferSize(Request req, Config config) {
        if (!"read".equals(req.op)) return null;
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (lo == null || lo.physicalObjects == null) return null;
        if (lo.physicalObjects.size() == EC_N) {
            return req.size.divide(BigDecimal.valueOf(EC_K), 20, RoundingMode.HALF_UP);
        }
        return null;
    }

    // =====================================================================
    // updatePastRequests
    // =====================================================================

    @Override
    public void updatePastRequests(Request request, String placeRegion) {
        updateHistogram(request, placeRegion);
        reqCount++;
        if ("read".equals(request.op)) {
            long nowSec = request.timestamp.getEpochSecond();
            int rIdx = regions.indexOf(request.issueRegion);
            RegionDecision dec = decisions.get(request.issueRegion);
            boolean hasLocal = dec != null && !dec.expired(nowSec) && dec.hasLocalData(rIdx);
            if (!hasLocal) {
                double sizeGB = request.size.doubleValue() / Definitions.GB;
                windowStats.computeIfAbsent(request.issueRegion, k -> new RegionStats()).addRead(sizeGB);
            }
            windowEventCount++;
        }
        checkAndSolveWindow(request.timestamp);
    }

    // =====================================================================
    // getTevict — 副本用直方图，EC 用 local region TTL
    // =====================================================================

    @Override
    public double getTevict(String src, String dst, Instant timestampInstant) {
        Double ttlHours = milpEcTtlByRegion.get(dst);
        if (ttlHours != null && ttlHours > 0) {
            return ttlHours * 3600.0;
        }
        if (src.equals(dst)) {
            double sp = regionStoragePrice(src);
            double tevenSec = (avgNetworkCost / sp) * 86400.0;
            return tevenSec / 2.0;
        }
        return getReplicaTevict(src, dst, timestampInstant);
    }

    // =====================================================================
    // canEvict / getLinkedEvictionObjects
    // =====================================================================

    public boolean canEvict(org.example.src.model.PhysicalObject obj, LogicalObject logicalObject,
            Map<String, org.example.src.model.PhysicalObject> allPhysObjs, Instant timestamp) {
        if (logicalObject == null || allPhysObjs == null) return true;
        BigDecimal fullSize = logicalObject.getSize();
        if (fullSize == null) return true;

        List<String> replicaRegions = new ArrayList<>();
        List<String> ecShardRegions = new ArrayList<>();
        for (Map.Entry<String, org.example.src.model.PhysicalObject> e : allPhysObjs.entrySet()) {
            org.example.src.model.PhysicalObject p = e.getValue();
            if (p.getKey().equals(obj.getKey()) && !p.getStorageStartTime().isAfter(timestamp)) {
                if (p.getSize().compareTo(fullSize) >= 0) {
                    replicaRegions.add(e.getKey());
                } else {
                    ecShardRegions.add(e.getKey());
                }
            }
        }
        boolean hasReplica = !replicaRegions.isEmpty();
        boolean hasEC = ecShardRegions.size() == EC_N;

        if (!hasReplica && !hasEC) return true;
        if (hasReplica && hasEC) return true;
        return false;
    }

    public List<org.example.src.model.PhysicalObject> getLinkedEvictionObjects(
            org.example.src.model.PhysicalObject obj, LogicalObject logicalObject,
            Map<String, org.example.src.model.PhysicalObject> allPhysObjs, Instant timestamp) {
        if (logicalObject == null || allPhysObjs == null) return Collections.singletonList(obj);

        BigDecimal fullSize = logicalObject.getSize();
        if (fullSize == null) return Collections.singletonList(obj);

        List<org.example.src.model.PhysicalObject> replicas = new ArrayList<>();
        List<org.example.src.model.PhysicalObject> ecShards = new ArrayList<>();
        for (org.example.src.model.PhysicalObject p : allPhysObjs.values()) {
            if (!p.getKey().equals(obj.getKey()) || p.getStorageStartTime().isAfter(timestamp)) continue;
            if (p.getSize().compareTo(fullSize) >= 0) replicas.add(p);
            else ecShards.add(p);
        }

        boolean isReplica = obj.getSize().compareTo(fullSize) >= 0;
        boolean hasReplica = !replicas.isEmpty();
        boolean hasEC = ecShards.size() == EC_N;

        if (isReplica) {
            if (hasEC) return Collections.singletonList(obj);
            return Collections.emptyList();
        }
        if (ecShards.contains(obj)) {
            if (hasReplica) return new ArrayList<>(ecShards);
            return Collections.emptyList();
        }
        return Collections.singletonList(obj);
    }

    // =====================================================================
    // 窗口求解
    // =====================================================================

    private void checkAndSolveWindow(Instant now) {
        long reqH = now.getEpochSecond() / 3600;
        if (windowStartHours == -1) windowStartHours = reqH;
        boolean windowFull = reqH - windowStartHours >= windowSizeHours;
        boolean tooManyRegions = windowStats.size() > regions.size() * 2;
        boolean enoughEvents = windowEventCount >= WINDOW_EVENT_THRESHOLD;
        if (windowFull || tooManyRegions || enoughEvents) {
            solveWindowBatch(now);
            windowStartHours = reqH;
            windowEventCount = 0;
        }
    }

    private void solveWindowBatch(Instant now) {
        if (windowStats.isEmpty()) return;
        long nowSec = now.getEpochSecond();
        decisions.entrySet().removeIf(e -> e.getValue().expired(nowSec));

        int n = regions.size();
        if (n < EC_N) {
            windowStats.clear();
            return;
        }

        double[] sp = new double[n];
        double[][] tc = new double[n][n];
        for (int i = 0; i < n; i++) {
            sp[i] = regionStoragePrice(regions.get(i));
            for (int j = 0; j < n; j++) {
                if (i != j) tc[i][j] = regionTransCost(regions.get(i), regions.get(j));
            }
        }

        double[] tauH = new double[n];
        for (int i = 0; i < n; i++) {
            double tevenSec = (avgNetworkCost / sp[i]) * 86400.0;
            double tevenHours = tevenSec / 3600.0;
            double histTtlH = calcEvictCost(regions.get(i), tevenHours, avgNetworkCost);
            tauH[i] = (histTtlH > 0) ? histTtlH : (tevenHours / 2.0);
            tauH[i] = Math.max(tauH[i], 1.0);
            milpEcTtlByRegion.put(regions.get(i), tauH[i]);
        }

        try {
            GRBEnv env = getOrCreateEnv();
            windowCount++;
            for (Map.Entry<String, RegionStats> entry : windowStats.entrySet()) {
                if (!entry.getValue().empty()) {
                    solveRegionMilp(env, entry.getKey(), entry.getValue(), n, sp, tc, tauH, nowSec);
                }
            }
        } catch (GRBException ex) {
            Logger.info("[MILP-ECnewV2] Gurobi error: " + ex.getMessage());
            if (persistentEnv != null) {
                try { persistentEnv.dispose(); } catch (GRBException ignored) {}
                persistentEnv = null;
            }
        }
        windowStats.clear();
    }

    private void solveRegionMilp(GRBEnv env, String regionKey, RegionStats stats,
                                int n, double[] sp, double[][] tc, double[] tauH, long nowSec) {
        int rIdx = regions.indexOf(regionKey);
        if (rIdx < 0) return;

        double sWrites = stats.writeSizeGB;
        double sReads = stats.readSizeGB;

        double maxTauH = 0;
        for (double t : tauH) maxTauH = Math.max(maxTauH, t);
        if (maxTauH <= 0) maxTauH = 24.0;
        final double BIG_M = maxTauH + 1.0;

        GRBModel model = null;
        try {
            model = new GRBModel(env);
            model.set(GRB.DoubleParam.MIPGap, 0.05);
            model.set(GRB.DoubleParam.TimeLimit, 2.0);

            GRBVar d = model.addVar(0, 1, 0, GRB.BINARY, "d");
            GRBVar hatTau = model.addVar(0, maxTauH, 0, GRB.CONTINUOUS, "ht");

            GRBVar[] sigma = new GRBVar[n];
            GRBVar[] z = new GRBVar[n];
            GRBVar[] vread = new GRBVar[n];
            for (int i = 0; i < n; i++) {
                sigma[i] = model.addVar(0, 1, 0, GRB.BINARY, "s" + i);
                z[i] = model.addVar(0, maxTauH, 0, GRB.CONTINUOUS, "z" + i);
                if (i != rIdx) vread[i] = model.addVar(0, 1, 0, GRB.CONTINUOUS, "vr" + i);
            }

            GRBLinExpr obj = new GRBLinExpr();

            GRBLinExpr lhs = new GRBLinExpr();
            for (int i = 0; i < n; i++) lhs.addTerm(1.0, sigma[i]);
            GRBLinExpr rhs = new GRBLinExpr();
            rhs.addTerm((double) EC_N, d);
            model.addConstr(lhs, GRB.EQUAL, rhs, "sigSum");

            model.addConstr(sigma[rIdx], GRB.GREATER_EQUAL, d, "local");

            String issueProvider = regionKey.split(":")[0];
            int sameProviderCount = 0;
            for (int i = 0; i < n; i++) {
                if (regions.get(i).startsWith(issueProvider + ":")) sameProviderCount++;
            }
            if (sameProviderCount >= EC_N) {
                for (int i = 0; i < n; i++) {
                    if (!regions.get(i).startsWith(issueProvider + ":")) {
                        GRBLinExpr ub = new GRBLinExpr();
                        ub.addConstant(1.0);
                        ub.addTerm(-1.0, d);
                        model.addConstr(sigma[i], GRB.LESS_EQUAL, ub, "prov" + i);
                    }
                }
            }

            for (int i = 0; i < n; i++) {
                double ti = tauH[i];
                GRBLinExpr lb = new GRBLinExpr();
                lb.addTerm(ti, sigma[i]);
                model.addConstr(hatTau, GRB.GREATER_EQUAL, lb, "htLB" + i);
            }

            for (int i = 0; i < n; i++) {
                GRBLinExpr zub1 = new GRBLinExpr();
                zub1.addTerm(BIG_M, sigma[i]);
                model.addConstr(z[i], GRB.LESS_EQUAL, zub1, "zU1_" + i);
                model.addConstr(z[i], GRB.LESS_EQUAL, hatTau, "zU2_" + i);
                GRBLinExpr zlb = new GRBLinExpr();
                zlb.addTerm(1.0, hatTau);
                zlb.addTerm(BIG_M, sigma[i]);
                zlb.addConstant(-BIG_M);
                model.addConstr(z[i], GRB.GREATER_EQUAL, zlb, "zL_" + i);
            }

            if (sReads > 0) {
                GRBLinExpr lh = new GRBLinExpr();
                for (int i = 0; i < n; i++) if (i != rIdx) lh.addTerm(1.0, vread[i]);
                GRBLinExpr rh = new GRBLinExpr();
                rh.addTerm((double) (EC_K - 1), d);
                model.addConstr(lh, GRB.EQUAL, rh, "readSum");
                for (int i = 0; i < n; i++)
                    if (i != rIdx) model.addConstr(vread[i], GRB.LESS_EQUAL, sigma[i], "vrLE" + i);
            }

            if (sWrites > 0) {
                double repStore = sWrites * sp[rIdx] * (tauH[rIdx] / 720.0);
                obj.addConstant(repStore);
                obj.addTerm(-repStore, d);
                for (int i = 0; i < n; i++)
                    obj.addTerm((sWrites / EC_K) * sp[i] / 720.0, z[i]);
                for (int i = 0; i < n; i++)
                    if (i != rIdx) obj.addTerm((sWrites / EC_K) * tc[rIdx][i], sigma[i]);
            }
            if (sReads > 0) {
                double repReadCost = sReads * avgNetworkCost;
                obj.addConstant(repReadCost);
                obj.addTerm(-repReadCost, d);
                for (int i = 0; i < n; i++)
                    if (i != rIdx) obj.addTerm((sReads / EC_K) * tc[i][rIdx], vread[i]);
            }

            model.setObjective(obj, GRB.MINIMIZE);
            model.optimize();

            int status = model.get(GRB.IntAttr.Status);
            if (status == GRB.Status.OPTIMAL || status == GRB.Status.SUBOPTIMAL) {
                int dec = (int) Math.round(d.get(GRB.DoubleAttr.X));
                boolean[] sigV = new boolean[n];
                if (dec == 1) {
                    sigV[rIdx] = true;
                    int otherIdx = pickMinCostRegionForEcShard(rIdx, n, tc, regionKey);
                    sigV[otherIdx] = true;
                } else {
                    for (int i = 0; i < n; i++)
                        sigV[i] = Math.round(sigma[i].get(GRB.DoubleAttr.X)) == 1;
                }
                double ht = hatTau.get(GRB.DoubleAttr.X);
                if (ht <= 0) ht = tauH[rIdx];

                decisions.put(regionKey, new RegionDecision(dec, sigV, ht, nowSec));
                if (dec == 1) {
                    Logger.info(String.format("[MILP-ECnewV2] region=%-25s EC(2+0) ttl=%.1fh shards=%s",
                            regionKey, tauH[rIdx], shardList(sigV)));
                } else {
                    Logger.info(String.format("[MILP-ECnewV2] region=%-25s REP ttl=%.1fh", regionKey, tauH[rIdx]));
                }
            }
        } catch (GRBException ex) {
            Logger.info("[MILP-ECnewV2] region='" + regionKey + "' Gurobi error: " + ex.getMessage());
        } finally {
            if (model != null) {
                try { model.dispose(); } catch (Exception ignored) {}
            }
        }
    }

    private GRBEnv getOrCreateEnv() throws GRBException {
        if (persistentEnv == null) {
            persistentEnv = new GRBEnv(true);
            persistentEnv.set("OutputFlag", "0");
            persistentEnv.start();
        }
        return persistentEnv;
    }

    public void flushPendingEvents(Instant now) {
        if (!windowStats.isEmpty()) {
            solveWindowBatch(now);
            windowStartHours = now.getEpochSecond() / 3600;
        }
        if (persistentEnv != null) {
            try { persistentEnv.dispose(); } catch (GRBException ignored) {}
            persistentEnv = null;
        }
    }

    /**
     * EC 另一片 region：按 graph（cost.csv）选运费最低的 region，不用 Gurobi。
     */
    private int pickMinCostRegionForEcShard(int rIdx, int n, double[][] tc, String regionKey) {
        String issueProvider = regionKey.split(":")[0];
        int sameProviderCount = 0;
        for (int i = 0; i < n; i++) {
            if (regions.get(i).startsWith(issueProvider + ":")) sameProviderCount++;
        }
        int best = -1;
        double bestCost = Double.MAX_VALUE;
        for (int i = 0; i < n; i++) {
            if (i == rIdx) continue;
            if (sameProviderCount >= EC_N && !regions.get(i).startsWith(issueProvider + ":"))
                continue;
            double c = tc[rIdx][i];
            if (c < bestCost) {
                bestCost = c;
                best = i;
            }
        }
        if (best < 0) best = rIdx == 0 ? 1 : 0;
        return best;
    }

    private String shardList(boolean[] sigV) {
        List<String> r = new ArrayList<>();
        for (int i = 0; i < sigV.length && i < regions.size(); i++)
            if (sigV[i]) r.add(regions.get(i));
        return r.toString();
    }

    private double regionStoragePrice(String region) {
        Object p = totalGraph.getNodeAttrs(region).get("priceStorage");
        if (p instanceof Number && ((Number) p).doubleValue() > 0)
            return ((Number) p).doubleValue();
        throw new IllegalStateException("[MILP-ECnewV2] priceStorage unavailable for '" + region + "'");
    }

    private double regionTransCost(String src, String dst) {
        Object c = totalGraph.getEdgeAttrs(src, dst).get("cost");
        if (!(c instanceof Number))
            throw new IllegalStateException("[MILP-ECnewV2] transfer cost unavailable '" + src + "'->'" + dst + "'");
        return ((Number) c).doubleValue();
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
            if (this == o) return true;
            if (o == null || getClass() != o.getClass()) return false;
            RegionPair that = (RegionPair) o;
            return Objects.equals(region1, that.region1) && Objects.equals(region2, that.region2);
        }

        @Override
        public int hashCode() {
            return Objects.hash(region1, region2);
        }
    }
}
