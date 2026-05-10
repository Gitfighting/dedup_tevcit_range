package org.example.src.placement_policy;

import com.gurobi.gurobi.GRBEnv;
import com.gurobi.gurobi.GRBException;
import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.placement_policy.milpv3.GurobiEc21MilpBuilder;
import org.example.src.placement_policy.milpv3.LayoutDecisionV3;
import org.example.src.placement_policy.milpv3.MilpCoefficientBundle;
import org.example.src.placement_policy.milpv3.StorageCostHelper;
import org.example.src.utils.Definitions;
import org.example.src.utils.Logger;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * MILP EC new V3 policy.
 *
 * Independent strategy implementation:
 * - extends PlacementPolicy (no inheritance from PolicyTevictRangesNew),
 * - keeps TevictRanges histogram/TTL logic copied locally,
 * - adds MILP solve + decision landing.
 */
public class PolicyMilpECnewV3 extends PlacementPolicy {
    private enum MilpTriggerMode {
        HISTOGRAM_TICK,
        INDEPENDENT_WINDOW;

        static MilpTriggerMode fromConfig(String raw) {
            if (raw == null) {
                return HISTOGRAM_TICK;
            }
            try {
                return MilpTriggerMode.valueOf(raw.trim().toUpperCase());
            } catch (IllegalArgumentException ex) {
                throw new IllegalStateException("Invalid milpTriggerMode: " + raw);
            }
        }
    }

    private static final int K_DATA = 2; // EC(2+1)
    private static final int N_TOTAL = 3;
    private static final long MILP_TRIGGER_BUCKET_SECONDS = 3600L; // region-level cadence (1 hour)
    private static final double PY_FALLBACK_PRICE_PUT = 5e-6;
    private static final double PY_FALLBACK_PRICE_GET = 4e-7;

    // ===== MILP =====
    private final Map<String, LayoutDecisionV3> decisionCache = new HashMap<>();
    private final Map<String, Long> lastMilpBucketByRegion = new HashMap<>();
    private long milpTickSeq = 0L;
    private final double milpTimeLimitSec;
    private final double milpMipGap;
    private final boolean milpStrictFail;
    /** Experimental: MILP fixes z=1 (EC layout only; replica branch disabled). */
    private final boolean milpEcV3ForceEcOnly;
    /** When false, MILP uses z=0 (replica-only ablation). */
    private final boolean milpEcV3AllowEc;
    private GRBEnv persistentEnv;

    // ===== TevictRanges histogram/TTL (copied for V3 independence) =====
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
    private final Map<String, List<LocalDateTime>> seenDays = new HashMap<>();
    protected final List<String> regions;
    private final Map<RegionPair, List<Double>> regionPairsTtl = new HashMap<>();
    private final double[] ranges;
    private int debugCount = 0;

    public PolicyMilpECnewV3(Config config, DiGraph totalGraph,
            Map<String, LogicalObject> logicalObjects,
            RegionManager regionManager) {
        super(config, totalGraph, logicalObjects, regionManager);
        this.windowSize = config.windowSize != null ? config.windowSize : -1;
        this.avgNetworkCost = getAvgNetworkCost(totalGraph);
        this.minNetworkCost = getMinNetworkCost(totalGraph);
        if (config.regions != null && !config.regions.isEmpty()) {
            this.regions = config.regions;
        } else {
            this.regions = new ArrayList<>(totalGraph.getNodes());
        }

        List<Double> rangesList = new ArrayList<>();
        rangesList.add(0.0);
        double sec = 1000;
        double min = 60 * sec;
        double hour = 60 * min;
        double minInHour = 0.01666;
        double s = minInHour * hour;
        for (int i = 0; i < 700; i++) {
            rangesList.add(s * Math.pow(1 + 0.02, i) / hour);
        }
        ranges = rangesList.stream().mapToDouble(Double::doubleValue).toArray();
        this.lastUpdated = (windowSize != -1) ? null : LocalDateTime.of(1970, 1, 1, 0, 0);
        for (String region : regions) {
            for (String region2 : regions) {
                regionPairsTtl.put(new RegionPair(region, region2), new ArrayList<>());
            }
        }

        this.milpTimeLimitSec = config.milpTimeLimitSec != null ? config.milpTimeLimitSec : 2.0;
        this.milpMipGap = config.milpMipGap != null ? config.milpMipGap : 0.05;
        this.milpStrictFail = config.milpStrictFail == null || config.milpStrictFail;
        this.milpEcV3ForceEcOnly = Boolean.TRUE.equals(config.milpEcV3ForceEcOnly);
        this.milpEcV3AllowEc = config.milpEcV3AllowEc == null || config.milpEcV3AllowEc;
        if (!this.milpStrictFail) {
            throw new IllegalStateException("milp_ec_new_v3 requires milpStrictFail=true");
        }
        MilpTriggerMode.fromConfig(config.milpTriggerMode); // validate config enum
    }

    @Override
    public void updatePastRequests(Request req, String region) {
        // only update histogram states here; MILP trigger is invoked after TTL is computed in SimulatorV2.
        updateHistogramStates(req, region);
    }

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            LayoutDecisionV3 dec = decisionCache.get(req.issueRegion);
            if (dec != null && dec.mode == 1) {
                List<String> ecRegions = extractEcRegions(dec);
                if (ecRegions.size() != N_TOTAL) {
                    throw new IllegalStateException(
                            "[MILP-ECnewV3] EC mode requires " + N_TOTAL + " shard regions, got " + ecRegions.size());
                }
                return ecRegions;
            }
            return Collections.singletonList(req.issueRegion);
        }
        // read: place only on miss in issueRegion
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (lo == null || lo.physicalObjects == null || !lo.physicalObjects.containsKey(req.issueRegion)) {
            return Collections.singletonList(req.issueRegion);
        }
        return Collections.emptyList();
    }

    @Override
    public Map<String, BigDecimal> getPlacementWithSizes(Request req, Config config) {
        if (!"write".equals(req.op)) {
            return null;
        }
        LayoutDecisionV3 dec = decisionCache.get(req.issueRegion);
        List<String> regionsToPlace = place(req, config);
        if (regionsToPlace == null || regionsToPlace.isEmpty()) {
            return null;
        }
        Map<String, BigDecimal> sized = new HashMap<>();
        if (dec == null || dec.mode == 0) {
            for (String r : regionsToPlace) {
                sized.put(r, req.size);
            }
            return sized;
        }
        BigDecimal shardSize = req.size.divide(BigDecimal.valueOf(K_DATA), 20, RoundingMode.HALF_UP);
        for (String r : regionsToPlace) {
            sized.put(r, shardSize);
        }
        return sized;
    }

    @Override
    public BigDecimal getReadTransferSize(Request req, Config config) {
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (lo == null || lo.physicalObjects == null) {
            return null;
        }
        BigDecimal fullSize = lo.getSize() == null ? req.size : lo.getSize();
        int shardReady = 0;
        int replicaReady = 0;
        for (PhysicalObject po : lo.physicalObjects.values()) {
            if (req.timestamp.isBefore(po.getStorageStartTime())) {
                continue;
            }
            if (po.getTtl() != Integer.MAX_VALUE && po.isExpired(req.timestamp)) {
                continue;
            }
            boolean isReplica = po.getSize() != null && po.getSize().compareTo(fullSize) >= 0;
            if (isReplica) {
                replicaReady++;
            } else {
                shardReady++;
            }
        }
        // Only use shard-size fallback when state is effectively EC-only recoverable.
        if (replicaReady == 0 && shardReady >= K_DATA) {
            return req.size.divide(BigDecimal.valueOf(K_DATA), 20, RoundingMode.HALF_UP);
        }
        return null;
    }

    @Override
    public double getTevict(String src, String dst, Instant timestampInstant) {
        LocalDateTime timestamp = LocalDateTime.ofInstant(timestampInstant, java.time.ZoneOffset.UTC);
        if (debugCount < 100) {
            System.out.println("[DEBUG] getTevict STARTED for src=" + src + " dst=" + dst);
            debugCount++;
        }
        if (canReuseTtlWindow(dst, timestamp)) {
            List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
            if (ttlList != null && !ttlList.isEmpty()) {
                return ttlList.get(ttlList.size() - 1);
            }
        }
        refreshAllRegionPairTtls(timestamp);
        List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
        return (ttlList == null || ttlList.isEmpty()) ? 0.0 : ttlList.get(ttlList.size() - 1);
    }

    public void flushPendingEvents(Instant now) {
        // keep signature for Simulator cleanup compatibility
        if (persistentEnv != null) {
            try {
                persistentEnv.dispose();
            } catch (GRBException e) {
                if (milpStrictFail) {
                    throw new IllegalStateException("Failed disposing Gurobi env", e);
                }
            } finally {
                persistentEnv = null;
            }
        }
    }

    public void runMilpForIssueRegion(String issueRegion, Instant now, Request triggerReq) {//运行MILP，解决where和how much per region
        LogicalObject template = resolveTemplate(triggerReq);//获取模板逻辑对象
        double[] ttlSecSnapshot = buildTtlSnapshotForIssueRegion(issueRegion, now);
        MilpCoefficientBundle coeff = buildCoefficients(issueRegion, template, ttlSecSnapshot);
        long tick = ++milpTickSeq;

        ensureEnv();
        int baseIdx = resolveBaseRegionIndex(template);
        try {
            LayoutDecisionV3 dec = GurobiEc21MilpBuilder.solve(
                    persistentEnv,
                    coeff,
                    baseIdx,
                    regions,
                    issueRegion,
                    now,
                    milpTimeLimitSec,
                    milpMipGap,
                    milpStrictFail,
                    null,
                    milpEcV3AllowEc,
                    milpEcV3ForceEcOnly);
            decisionCache.put(issueRegion, dec);
            Logger.info(String.format(
                    "[MILP-ECnewV3] tick=%d issue=%s template=%s mode=%d status=%s obj=%.6f tRep=%.9f tEc=%.9f ttl=%s",
                    tick, issueRegion, triggerReq.objKey, dec.mode, dec.solverStatus, dec.objVal,
                    coeff.tRep, coeff.tEc, summarizeArray(coeff.ttlSec)));
        } catch (GRBException e) {
            throw new IllegalStateException("MILP solve failed for issueRegion=" + issueRegion, e);
        }
    }

    /**
     * Region-level throttled MILP trigger.
     * Same region is solved at most once per time bucket.
     */
    public void runMilpForIssueRegionIfDue(String issueRegion, Instant now, Request triggerReq) {
        if (issueRegion == null || now == null || triggerReq == null) {
            return;
        }
        long bucket = Math.floorDiv(now.getEpochSecond(), MILP_TRIGGER_BUCKET_SECONDS);
        Long last = lastMilpBucketByRegion.get(issueRegion);
        if (last != null && last == bucket) {
            return;
        }
        runMilpForIssueRegion(issueRegion, now, triggerReq);
        lastMilpBucketByRegion.put(issueRegion, bucket);
    }

    private LogicalObject resolveTemplate(Request req) {
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (lo != null) {
            return lo;
        }
        throw new IllegalStateException("Template logical object not found: " + req.objKey);
    }

    private int resolveBaseRegionIndex(LogicalObject lo) {//获取基底region索引
        if (lo != null && lo.baseRegion != null) {
            int idx = regions.indexOf(lo.baseRegion);
            if (idx >= 0) {
                return idx;
            }
        }
        if (config.storageRegion != null && !config.storageRegion.isEmpty()) {
            int idx = regions.indexOf(config.storageRegion);
            if (idx >= 0) {
                return idx;
            }
        }
        return regions.isEmpty() ? -1 : 0;
    }

    private MilpCoefficientBundle buildCoefficients(String issueRegion, LogicalObject template, double[] ttlSecSnapshot) {
        int n = regions.size();
        double[] ttlSec = new double[n];
        double[] sRep = new double[n];
        double[][] sEc = new double[n][N_TOTAL];

        double sizeLogicGb = template.getSize()
                .divide(BigDecimal.valueOf(Definitions.GB), 20, RoundingMode.HALF_UP).doubleValue();
        double sizeShardGb = sizeLogicGb / K_DATA;

        double nwPred = 1.0;//预测写请求次数（Nw_pred），当前写死为 1，用在写 API 项：nwPred * pricePut
        double nrPred = nwPred;//预测读请求次数（Nr_pred），当前直接等于 nwPred
        double getApi = getNodePriceRequired(issueRegion, "priceGet");

        for (int r = 0; r < n; r++) {
            String rr = regions.get(r);
            double ttl = (ttlSecSnapshot != null && r < ttlSecSnapshot.length) ? ttlSecSnapshot[r] : 0.0;
            ttlSec[r] = Math.max(0.0, ttl);

            double storRep = StorageCostHelper.storageCostGb(sizeLogicGb, rr, ttlSec[r], totalGraph);
            double storShard = StorageCostHelper.storageCostGb(sizeShardGb, rr, ttlSec[r], totalGraph);
            double putApi = getNodePriceRequired(rr, "pricePut");

            // network proxy terms
            double edgeRep = edgeCostRequired(issueRegion, rr) * sizeLogicGb;
            double edgeShard = edgeCostRequired(issueRegion, rr) * sizeShardGb;

            sRep[r] = storRep + edgeRep + (nwPred * putApi);
            for (int k = 0; k < N_TOTAL; k++) {
                sEc[r][k] = storShard + edgeShard + (nwPred * putApi);
            }
        }

        // single-point GET accounting in T terms (once per logical read)
        double tRep = nrPred * getApi;
        double tEc = nrPred * getApi;
        validateCoefficients(issueRegion, ttlSec, sRep, sEc, tRep, tEc);
        return new MilpCoefficientBundle(sRep, sEc, tRep, tEc, ttlSec);
    }

    /**
     * Build one TTL vector for MILP coefficients using a single timestamp window.
     * This avoids a second round of getTevict() calls inside MILP coefficient building.
     */
    private double[] buildTtlSnapshotForIssueRegion(String issueRegion, Instant now) {
        LocalDateTime ts = LocalDateTime.ofInstant(now, java.time.ZoneOffset.UTC);
        if (!canReuseTtlWindow(issueRegion, ts)) {
            refreshAllRegionPairTtls(ts);
        }
        double[] ttlSec = new double[regions.size()];
        for (int r = 0; r < regions.size(); r++) {
            List<Double> ttlList = regionPairsTtl.get(new RegionPair(regions.get(r), issueRegion));
            ttlSec[r] = (ttlList == null || ttlList.isEmpty()) ? 0.0 : Math.max(0.0, ttlList.get(ttlList.size() - 1));
        }
        return ttlSec;
    }

    private boolean canReuseTtlWindow(String dst, LocalDateTime timestamp) {
        return seenDays.containsKey(dst) && !seenDays.get(dst).isEmpty()
                && timestamp.isBefore(seenDays.get(dst).get(seenDays.get(dst).size() - 1).plusHours(12));
    }

    /**
     * Kept identical to TevictRanges calculation flow:
     * iterate all region pairs and append TTL for current timestamp window.
     */
    private void refreshAllRegionPairTtls(LocalDateTime timestamp) {
        for (String region : regions) {
            for (String region2 : regions) {
                if (!region.equals(region2)) {
                    Map<String, Object> edgeAttrs = totalGraph.getEdgeAttrs(region, region2);
                    Object costObj = edgeAttrs.get("cost");
                    if (!(costObj instanceof Number)) {
                        throw new IllegalStateException(
                                "[TevictRanges] transfer cost unavailable for '" + region + "'->'" + region2 + "': " + costObj);
                    }
                    double netCost = ((Number) costObj).doubleValue();

                    Map<String, Object> nodeAttrs = totalGraph.getNodeAttrs(region2);
                    Object priceObj = nodeAttrs.get("priceStorage");
                    if (!(priceObj instanceof Number) || ((Number) priceObj).doubleValue() <= 0) {
                        throw new IllegalStateException(
                                "[TevictRanges] priceStorage unavailable for '" + region2 + "': " + priceObj);
                    }
                    double storage = ((Number) priceObj).doubleValue();
                    double teven = netCost / storage * 60 * 60 * 24;
                    double calculatedCost = calcEvictCost(region2, teven / 3600.0, netCost);
                    double calculatedTtl = findMin(calculatedCost, teven, region2, timestamp);
                    regionPairsTtl.get(new RegionPair(region, region2)).add(calculatedTtl);
                }
            }
        }
    }

    private void ensureEnv() {//确保Gurobi环境
        if (persistentEnv != null) {//如果环境已经存在，则直接返回
            return;
        }
        try {
            persistentEnv = new GRBEnv(true);
            persistentEnv.set("LogFile", "milp_ec_new_v3.log");
            persistentEnv.start();
        } catch (GRBException e) {
            throw new IllegalStateException("Failed to initialize Gurobi env", e);
        }
    }

    private double getNodePriceRequired(String region, String key) {
        Object v = totalGraph.getNodeAttr(region, key);
        if (v instanceof Number) {
            return ((Number) v).doubleValue();
        }
        if ("pricePut".equals(key)) {
            return PY_FALLBACK_PRICE_PUT;
        }
        if ("priceGet".equals(key)) {
            return PY_FALLBACK_PRICE_GET;
        }
        throw new IllegalStateException("Missing numeric node attr '" + key + "' in region " + region);
    }

    private double edgeCostRequired(String src, String dst) {
        Object v = totalGraph.getEdgeAttr(src, dst, "cost");
        if (v instanceof Number) {
            return ((Number) v).doubleValue();
        }
        throw new IllegalStateException("Missing numeric edge cost from " + src + " to " + dst);
    }

    private void validateCoefficients(String issueRegion, double[] ttlSec, double[] sRep, double[][] sEc,
            double tRep, double tEc) {
        for (double v : ttlSec) {
            assertFinite(v, "ttlSec", issueRegion);
        }
        for (double v : sRep) {
            assertFinite(v, "sRep", issueRegion);
        }
        for (double[] row : sEc) {
            for (double v : row) {
                assertFinite(v, "sEc", issueRegion);
            }
        }
        assertFinite(tRep, "tRep", issueRegion);
        assertFinite(tEc, "tEc", issueRegion);
    }

    private void assertFinite(double v, String name, String issueRegion) {
        if (Double.isNaN(v) || Double.isInfinite(v)) {
            throw new IllegalStateException("Invalid coefficient " + name + " for issueRegion=" + issueRegion);
        }
    }

    private String summarizeArray(double[] arr) {
        if (arr == null || arr.length == 0) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        int m = Math.min(4, arr.length);
        for (int i = 0; i < m; i++) {
            if (i > 0) sb.append(", ");
            sb.append(String.format("%.2f", arr[i]));
        }
        if (arr.length > m) {
            sb.append(", ...");
        }
        sb.append("]");
        return sb.toString();
    }

    private List<String> extractEcRegions(LayoutDecisionV3 dec) {
        if (dec.y == null || dec.y.length == 0) {
            return Collections.emptyList();
        }
        Set<String> unique = new LinkedHashSet<>();
        for (int r = 0; r < dec.y.length && r < regions.size(); r++) {
            boolean assigned = false;
            for (int k = 0; k < dec.y[r].length; k++) {
                if (dec.y[r][k]) {
                    assigned = true;
                    break;
                }
            }
            if (assigned) {
                unique.add(regions.get(r));
            }
        }
        return new ArrayList<>(unique);
    }

    // ===== copied TevictRanges internals =====
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

    protected double getHistogramSizeGB(Request request, String placeRegion) {
        return request.size.doubleValue() / 1073741824.0;
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

    private void updateHistogramStates(Request request, String placeRegion) {
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
            if (tnext == 0) {
                tnext = 1.0;
            }
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
            nextPastRequests.get(placeRegion).put(request.objKey, new ArrayList<>(Collections.singletonList(reqToSec)));
            nextLastHist.putIfAbsent(placeRegion, new HashMap<>());
            long tendWindow = (long) Math.ceil(
                    Duration.between(requestTime, roundToNextHour(requestTime)).toNanos() / 1_000_000_000.0 / 3600.0);
            nextLastHist.get(placeRegion).merge((int) tendWindow, sizeGB, Double::sum);
        }
    }

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
        double storageCostHour = (tevenHours == 0) ? avgNetworkCost / tevenHours : netCost / tevenHours;
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
                ttl = 0.0;
            }
        } else {
            if (!numRequests.containsKey(region)) {
                noPastData = true;
                ttl = teven / 3600.0 / 2.0;
            } else {
                numReqs = numRequests.get(region);
                ttl = 0.0;
            }
        }
        if (!noPastData) {
            if (y == -1 || lastUpdated == null || numReqs < 1000) {
                ttl = teven / 3600.0 / 2.0;
            } else {
                ttl = y;
            }
        }
        if (!seenDays.containsKey(region)) {
            seenDays.put(region, new ArrayList<>());
            ttls.put(region, new ArrayList<>());
        }
        List<LocalDateTime> regionSeenDays = seenDays.get(region);
        if (regionSeenDays.isEmpty()
                || !timestamp.isBefore(regionSeenDays.get(regionSeenDays.size() - 1).plusHours(12))) {
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

