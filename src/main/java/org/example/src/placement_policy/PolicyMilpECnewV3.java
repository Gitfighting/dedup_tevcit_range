package org.example.src.placement_policy;

import com.gurobi.gurobi.GRBEnv;
import com.gurobi.gurobi.GRBException;
import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.placement_policy.milpv3.*;
import org.example.src.utils.Definitions;
import org.example.src.utils.Logger;

import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;
/**
 * MILP EC new V3 policy.
 *
 * <p>Independent strategy implementation:
 * <ul>
 *   <li>extends {@link PlacementPolicy} (no inheritance from {@link PolicyTevictRangesNew}),</li>
 *   <li>keeps TevictRanges histogram/TTL logic copied locally,</li>
 *   <li>adds MILP solve + decision landing on write path.</li>
 * </ul>
 *
 * <p>Pair-TTL refresh follows {@link PolicyTevictRangesNew#getTevict}: 12h early return, full refresh
 * only on bucket roll. MILP runs after that refresh via a pending queue, not on the PUT
 * {@link org.example.SimulatorV2#calculateTtl} path. {@link #findMin} may still use teven/2 until
 * enough read samples exist; that only affects TTL values fed into coefficients, not whether Gurobi runs.
 */
public class PolicyMilpECnewV3 extends PlacementPolicy {

    /** Histogram admission threshold in {@link #findMin}; matches {@link PolicyTevictRangesNew}. */
    private static final int TEVICT_HISTOGRAM_MIN_REQUESTS = 1000;
    private static final int K_DATA = 2; // shard payload size = logical size / K_DATA (two data shards)
    /** MILP / placement: exactly this many EC shard regions (base replica + these shards). */
    private static final int N_EC_SHARDS = GurobiEc21MilpBuilder.NUM_EC_SHARD_SLOTS;
    /** Histogram pair-TTL list advances on this cadence (must match TevictRanges seenDays step below). */
    private static final int PAIR_TTL_BUCKET_HOURS = 12;

    // ===== MILP =====
    private final Map<String, LayoutDecisionV3> decisionCache = new HashMap<>();
    /** MILP deferred until {@link #getTevict} performs the same pair-TTL refresh as tevict_ranges. */
    private final Map<String, Request> pendingMilpAfterTtlRefresh = new HashMap<>();
    private long milpTickSeq = 0L;
    private long histVersion = 0L;
    private static final Path MILP_SOLVE_LOG = Paths.get("results", "milp_solve_log.jsonl");
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
    }

    @Override
    public void updatePastRequests(Request req, String region) {
        boolean histSwapped = updateHistogramStates(req, region);
        boolean admit = meetsMilpAdmission(req.issueRegion);
        LocalDateTime ts = LocalDateTime.ofInstant(req.timestamp, java.time.ZoneOffset.UTC);
        if (windowSize > 0) {
            if (histSwapped && admit) {
                runMilpForIssueRegion(req.issueRegion, req.timestamp, req);
            }
        } else if (admit && !canReuseTtlWindow(req.issueRegion, ts)) {
            pendingMilpAfterTtlRefresh.put(req.issueRegion, req);
        }
    }

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            return writePlacementRegions(req);
        }
        return readCachePlacementRegions(req);
    }

    /**
     * After a paid read transfer, cache a <em>full</em> replica at {@code issueRegion}. A local EC shard or
     * delta patch must not suppress placement.
     */
    protected List<String> readCachePlacementRegions(Request req) {
        LogicalObject lo = logicalObjects.get(req.objKey);
        if (lo == null || lo.physicalObjects == null) {
            return Collections.singletonList(req.issueRegion);
        }
        if (hasReadyFullReplicaAt(lo, req.issueRegion, req.timestamp)) {
            return Collections.emptyList();
        }
        return Collections.singletonList(req.issueRegion);
    }

    /** True when {@code region} holds an unexpired physical copy whose size is at least the logical object size. */
    protected boolean hasReadyFullReplicaAt(LogicalObject lo, String region, Instant at) {
        if (lo == null || lo.physicalObjects == null) {
            return false;
        }
        PhysicalObject po = lo.physicalObjects.get(region);
        if (po == null || po.getStorageStartTime() == null || at.isBefore(po.getStorageStartTime())) {
            return false;
        }
        if (po.getTtl() != Integer.MAX_VALUE && po.isExpired(at)) {
            return false;
        }
        BigDecimal fullSize = lo.getSize();
        return fullSize != null && po.getSize() != null && po.getSize().compareTo(fullSize) >= 0;
    }

    protected BigDecimal logicalFullSize(LogicalObject lo, Request req) {
        if (lo != null && lo.getSize() != null) {
            return lo.getSize();
        }
        return req.size;
    }

    /**
     * PUT placement from cached MILP {@code x}/{@code y}: replica regions or EC shard regions.
     * Falls back to {@code issueRegion} when no decision is cached yet.
     */
    protected List<String> writePlacementRegions(Request req) {
        return regionsForWriteDecision(decisionCache.get(req.issueRegion), req.issueRegion);
    }

    /**
     * Maps a MILP layout to simulator write regions. FB always includes {@code issueRegion} (local PUT);
     * FP uses {@code x[]} when {@code z=0}, or EC shard regions when {@code z=1}.
     */
    List<String> regionsForWriteDecision(LayoutDecisionV3 dec, String issueRegion) {
        if (dec == null) {
            return Collections.singletonList(issueRegion);
        }
        if (dec.mode == 1) {
            List<String> shards = extractEcRegions(dec);
            if (shards.size() != N_EC_SHARDS) {
                throw new IllegalStateException(
                        "[MILP-ECnewV3] EC mode requires " + N_EC_SHARDS + " shard regions, got " + shards.size());
            }
            return shards;
        }
        List<String> replicas = replicaRegionsFromDecision(dec);
        if (Boolean.TRUE.equals(config.fixedBaseRegion)) {
            return unionDistinct(issueRegion, replicas);
        }
        return replicas.isEmpty() ? Collections.singletonList(issueRegion) : replicas;
    }

    private static List<String> unionDistinct(String first, List<String> rest) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        out.add(first);
        if (rest != null) {
            out.addAll(rest);
        }
        return new ArrayList<>(out);
    }

    @Override
    public Map<String, BigDecimal> getPlacementWithSizes(Request req, Config config) {
        if ("read".equals(req.op)) {
            List<String> cacheRegions = readCachePlacementRegions(req);
            if (cacheRegions.isEmpty()) {
                return null;
            }
            LogicalObject lo = logicalObjects.get(req.objKey);
            BigDecimal fullSize = logicalFullSize(lo, req);
            Map<String, BigDecimal> sized = new HashMap<>();
            for (String region : cacheRegions) {
                sized.put(region, fullSize);
            }
            return sized;
        }
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

    /**
     * Pair-TTL lookup aligned with {@link PolicyTevictRangesNew#getTevict}: reuse cached slice inside the
     * {@value #PAIR_TTL_BUCKET_HOURS}h window; refresh all region pairs only when the window rolls.
     */
    @Override
    public double getTevict(String src, String dst, Instant timestampInstant) {
        LocalDateTime timestamp = LocalDateTime.ofInstant(timestampInstant, java.time.ZoneOffset.UTC);
        if (debugCount < 100) {
            System.out.println("[DEBUG] getTevict STARTED for src=" + src + " dst=" + dst);
            debugCount++;
        }
        if (seenDays.containsKey(dst) && !seenDays.get(dst).isEmpty()
                && timestamp.isBefore(seenDays.get(dst).get(seenDays.get(dst).size() - 1)
                        .plusHours(PAIR_TTL_BUCKET_HOURS))) {
            List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
            if (ttlList != null && !ttlList.isEmpty()) {
                return ttlList.get(ttlList.size() - 1);
            }
        }
        refreshAllRegionPairTtls(timestamp);
        drainPendingMilpAfterTtlRefresh();
        List<Double> ttlList = regionPairsTtl.get(new RegionPair(src, dst));
        return (ttlList == null || ttlList.isEmpty()) ? 0.0 : ttlList.get(ttlList.size() - 1);
    }

    private void drainPendingMilpAfterTtlRefresh() {
        if (pendingMilpAfterTtlRefresh.isEmpty()) {
            return;
        }
        for (Map.Entry<String, Request> e : new ArrayList<>(pendingMilpAfterTtlRefresh.entrySet())) {
            String issueRegion = e.getKey();
            Request pending = pendingMilpAfterTtlRefresh.remove(issueRegion);
            if (pending != null && meetsMilpAdmission(issueRegion)) {
                runMilpForIssueRegion(issueRegion, pending.timestamp, pending);
            }
        }
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
        MilpCoefficientBundle coeff = buildCoefficients(issueRegion, template, ttlSecSnapshot, now);
        long tick = ++milpTickSeq;//milp求解计数器

        ensureEnv();//确保Gurobi环境
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
            appendMilpSolveLog(tick, now, issueRegion, coeff, dec);
            Logger.info(String.format(
                    "[MILP-ECnewV3] tick=%d issue=%s template=%s mode=%d status=%s obj=%.6f ttlSec=%s",
                    tick, issueRegion, triggerReq.objKey, dec.mode, dec.solverStatus, dec.objVal,
                    summarizeArray(coeff.ttlSec)));
        } catch (GRBException e) {
            throw new IllegalStateException("MILP solve failed for issueRegion=" + issueRegion, e);
        }
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

    /**
     * Coefficients for {@link GurobiEc21MilpBuilder}: per-region replica cost {@code sRep[r]} and per (region, EC-slot)
     * cost {@code sEc[r][k]}. Each is only {@link StorageCostHelper#storageCostGb} plus {@code totalGraph} edge
     * attribute {@code cost} from {@code issueRegion} to the placement region, multiplied by placed size (full object
     * or shard).
     * <p>
     * FB ({@code fixedBaseRegion}): mandatory base full replica is priced as {@link MilpCoefficientBundle#fbBaseReplicaObjectiveConstant}
     * using storage duration from {@link LogicalObject#getPutInstant} (first PUT) to the accounting end instant (typically
     * {@link Config#milpDatasetEndEpochSec}, pre-filled from trace + tail in {@link org.example.SimulatorV2}), or
     * {@link Config#milpFbReplicaHorizonSec} as a plain duration when epoch end is unset — not pair TTL; {@code sRep[base]} is
     * zero and {@code x[base]} is fixed off in the MILP.
     * EC shard rows at base still use TTL-based {@code sEc}.
     */
    protected double effectiveSizeRatio(LogicalObject template) {
        return 1.0;
    }

    private MilpCoefficientBundle buildCoefficients(
            String issueRegion, LogicalObject template, double[] ttlSecSnapshot, Instant now) {
        int n = regions.size();
        double[] ttlSec = new double[n];
        double[] sRep = new double[n];
        double[][] sEc = new double[n][N_EC_SHARDS];

        double ratio = effectiveSizeRatio(template);
        double sizeLogicGb = template.getSize()
                .divide(BigDecimal.valueOf(Definitions.GB), 20, RoundingMode.HALF_UP).doubleValue() * ratio;
        double sizeShardGb = sizeLogicGb / K_DATA;

        int baseIdx = resolveBaseRegionIndex(template);
        boolean fixedBaseFb = Boolean.TRUE.equals(config.fixedBaseRegion) && baseIdx >= 0 && baseIdx < n;
        double fbConst = 0.0;
        int issueIdx = regions.indexOf(issueRegion);
        if (fixedBaseFb) {
            String baseRg = regions.get(baseIdx);
            Instant putStart = template.getPutInstant();
            if (putStart == null) {
                throw new IllegalStateException(
                        "[MILP-ECnewV3] template missing putInstant for FB MILP (obj=" + template.getKey() + ")");
            }
            double horizonSec;
            if (config.milpDatasetEndEpochSec != null) {
                Instant accountingEnd = Instant.ofEpochSecond(config.milpDatasetEndEpochSec);
                horizonSec = Math.max(0.0,
                        java.time.Duration.between(putStart, accountingEnd).toNanos() / 1_000_000_000.0);
            } else if (config.milpFbReplicaHorizonSec != null) {
                horizonSec = Math.max(0.0, config.milpFbReplicaHorizonSec);
            } else {
                throw new IllegalStateException(
                        "[MILP-ECnewV3] fixedBaseRegion MILP needs storage horizon: set milp_dataset_end_epoch_sec, or run "
                                + "via SimulatorV2 (auto-fills from trace last request + "
                                + org.example.SimulatorV2.TRACE_ACCOUNTING_TAIL_SECONDS
                                + "s tail), or set milp_fb_replica_horizon_sec.");
            }
            fbConst = StorageCostHelper.storageCostGb(sizeLogicGb, baseRg, horizonSec, totalGraph)
                    + edgeCostRequired(issueRegion, baseRg) * sizeLogicGb;
            if (issueIdx >= 0 && issueIdx < n && issueIdx != baseIdx) {
                double issueTtl = (ttlSecSnapshot != null && issueIdx < ttlSecSnapshot.length)
                        ? Math.max(0.0, ttlSecSnapshot[issueIdx]) : 0.0;
                fbConst += StorageCostHelper.storageCostGb(
                        sizeLogicGb, issueRegion, issueTtl, totalGraph);
            }
        }

        for (int r = 0; r < n; r++) {
            String rr = regions.get(r);
            double ttl = (ttlSecSnapshot != null && r < ttlSecSnapshot.length) ? ttlSecSnapshot[r] : 0.0;
            ttlSec[r] = Math.max(0.0, ttl);

            double storRep = StorageCostHelper.storageCostGb(sizeLogicGb, rr, ttlSec[r], totalGraph);
            double storShard = StorageCostHelper.storageCostGb(sizeShardGb, rr, ttlSec[r], totalGraph);

            double placementTransferCostRep = edgeCostRequired(issueRegion, rr) * sizeLogicGb;
            double placementTransferCostShard = edgeCostRequired(issueRegion, rr) * sizeShardGb;

            if (fixedBaseFb && r == baseIdx) {
                sRep[r] = 0.0;
            } else {
                sRep[r] = storRep + placementTransferCostRep;
            }
            for (int k = 0; k < N_EC_SHARDS; k++) {
                sEc[r][k] = storShard + placementTransferCostShard;
            }
        }

        validateCoefficients(issueRegion, ttlSec, sRep, sEc);
        double cacheTtlSec = 0.0;
        for (double t : ttlSec) {
            cacheTtlSec = Math.max(cacheTtlSec, t);
        }
        if (cacheTtlSec <= 0.0) {
            cacheTtlSec = PAIR_TTL_BUCKET_HOURS * 3600.0;
        }
        double localReadCache = fixedBaseFb ? 0.0 : StorageCostHelper.storageCostGb(
                sizeLogicGb, issueRegion, cacheTtlSec, totalGraph);
        return new MilpCoefficientBundle(sRep, sEc, ttlSec, fixedBaseFb, fbConst, localReadCache);
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
                && timestamp.isBefore(
                        seenDays.get(dst).get(seenDays.get(dst).size() - 1).plusHours(PAIR_TTL_BUCKET_HOURS));
    }

    /**
     * Kept identical to TevictRanges calculation flow:
     * iterate all region pairs and append TTL for current timestamp window.
     */
    private void refreshAllRegionPairTtls(LocalDateTime timestamp) {//刷新所有区域的TTL 对所有region对算一轮TTL
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

    private double edgeCostRequired(String src, String dst) {
        Object v = totalGraph.getEdgeAttr(src, dst, "cost");
        if (v instanceof Number) {
            return ((Number) v).doubleValue();
        }
        throw new IllegalStateException("Missing numeric edge cost from " + src + " to " + dst);
    }

    private void validateCoefficients(String issueRegion, double[] ttlSec, double[] sRep, double[][] sEc) {
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

    /** Full numeric JSON array (no truncation) for {@code milp_solve_log.jsonl} machine parsing. */
    private String jsonArray(double[] arr) {
        if (arr == null || arr.length == 0) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            if (i > 0) sb.append(',');
            sb.append(String.format("%.6f", arr[i]));
        }
        sb.append(']');
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

    private boolean meetsMilpAdmission(String region) {
        int min = milpMinRequestsThreshold();
        int count;
        if (windowSize == -1) {
            count = nextNumRequests.getOrDefault(region, 0);
        } else {
            count = numRequests.getOrDefault(region, 0);
        }
        return count >= min;
    }

    /** Shared with {@link #findMin} so MILP admission and TTL histogram use the same threshold. */
    protected int milpMinRequestsThreshold() {
        return config.milpMinRequests != null ? config.milpMinRequests : 1000;
    }

    private void appendMilpSolveLog(
            long tick, Instant now, String issueRegion, MilpCoefficientBundle coeff, LayoutDecisionV3 dec) {
        try {
            Files.createDirectories(MILP_SOLVE_LOG.getParent());
            String line = buildMilpSolveLogLine(tick, now, issueRegion, coeff, dec);
            Files.write(
                    MILP_SOLVE_LOG,
                    line.getBytes(StandardCharsets.UTF_8),
                    StandardOpenOption.CREATE,
                    StandardOpenOption.APPEND);
        } catch (IOException e) {
            Logger.info("[MILP-ECnewV3] milp_solve_log append failed: " + e.getMessage());
        }
    }

    /** JSON line for {@code milp_solve_log.jsonl}: solver outcome + concrete placement (x/y) + write-path regions. */
    private String buildMilpSolveLogLine(
            long tick, Instant now, String issueRegion, MilpCoefficientBundle coeff, LayoutDecisionV3 dec) {
        String baseRegion = (dec.baseRegionIndex >= 0 && dec.baseRegionIndex < regions.size())
                ? regions.get(dec.baseRegionIndex) : "";
        List<String> replicaRegions = replicaRegionsFromDecision(dec);
        List<String> ecShardRegions = extractEcRegions(dec);
        List<String> writeRegions = writeRegionsFromDecision(dec, issueRegion);
        String ecShardDetail = jsonEcShardDetail(dec);
        return String.format(
                "{\"tick\":%d,\"timestamp\":%d,\"issueRegion\":\"%s\",\"histVersion\":%d,"
                        + "\"fixedBaseFb\":%s,\"baseRegion\":\"%s\","
                        + "\"ttlSec\":%s,\"z\":%s,\"mode\":%d,"
                        + "\"replicaRegions\":%s,\"ecShardRegions\":%s,\"ecShardDetail\":%s,"
                        + "\"writeRegionsOnPut\":%s,\"localReadCacheCost\":%.8f,"
                        + "\"objVal\":%.8f,\"solverStatus\":\"%s\"}%n",
                tick,
                now.getEpochSecond(),
                issueRegion,
                histVersion,
                coeff.fixedBaseFb ? "true" : "false",
                baseRegion,
                jsonArray(coeff.ttlSec),
                dec.mode == 1 ? "true" : "false",
                dec.mode,
                jsonStringArray(replicaRegions),
                jsonStringArray(ecShardRegions),
                ecShardDetail,
                jsonStringArray(writeRegions),
                coeff.localReadCacheCost,
                dec.objVal,
                dec.solverStatus);
    }

    /** Full-replica regions from MILP {@code x[r]} (may differ from {@link #place} write path). */
    private List<String> replicaRegionsFromDecision(LayoutDecisionV3 dec) {
        List<String> out = new ArrayList<>();
        if (dec.x == null) {
            return out;
        }
        for (int r = 0; r < dec.x.length && r < regions.size(); r++) {
            if (dec.x[r]) {
                out.add(regions.get(r));
            }
        }
        return out;
    }

    /** Regions {@link #place} uses on PUT for this cached decision (execution semantics). */
    private List<String> writeRegionsFromDecision(LayoutDecisionV3 dec, String issueRegion) {
        return regionsForWriteDecision(dec, issueRegion);
    }

    /** Per-shard slot assignment: {@code [{\"region\":\"...\",\"slot\":0}, ...]}. */
    private String jsonEcShardDetail(LayoutDecisionV3 dec) {
        if (dec.y == null) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (int r = 0; r < dec.y.length && r < regions.size(); r++) {
            if (dec.y[r] == null) {
                continue;
            }
            for (int k = 0; k < dec.y[r].length; k++) {
                if (!dec.y[r][k]) {
                    continue;
                }
                if (!first) {
                    sb.append(',');
                }
                first = false;
                sb.append("{\"region\":\"").append(regions.get(r)).append("\",\"slot\":").append(k).append('}');
            }
        }
        sb.append(']');
        return sb.toString();
    }

    private String jsonStringArray(List<String> items) {
        if (items == null || items.isEmpty()) {
            return "[]";
        }
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < items.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append('"').append(items.get(i)).append('"');
        }
        sb.append(']');
        return sb.toString();
    }

    private boolean updateHistogramStates(Request request, String placeRegion) {
        boolean swapped = false;
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
                swapped = true;
                histVersion++;
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
        return swapped;
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
            if (y == -1 || lastUpdated == null || numReqs < TEVICT_HISTOGRAM_MIN_REQUESTS) {
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
                || !timestamp.isBefore(regionSeenDays.get(regionSeenDays.size() - 1)
                        .plusHours(PAIR_TTL_BUCKET_HOURS))) {
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

