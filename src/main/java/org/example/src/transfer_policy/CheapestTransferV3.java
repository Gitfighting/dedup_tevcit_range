package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.model.Status;
import org.example.src.placement_policy.PlacementPolicy;
import org.example.src.utils.Logger;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * V3-specific transfer policy extension point.
 * Keeps shared CheapestTransferV2 untouched; V3 behavior is overridden here.
 */
public class CheapestTransferV3 extends CheapestTransferV2 {
    private static final int EC_K = 2;
    private final RegionManager regionManager;
    private final PlacementPolicy placementPolicy;

    private static final class ReadPlan {
        final String name;
        final double totalCost;
        final String src1;
        final String src2;

        ReadPlan(String name, double totalCost, String src1, String src2) {
            this.name = name;
            this.totalCost = totalCost;
            this.src1 = src1;
            this.src2 = src2;
        }
    }

    public CheapestTransferV3(Config config,
            DiGraph totalGraph,
            Map<String, LogicalObject> objectDict,
            RegionManager regionManager,
            PlacementPolicy placementPolicy) {
        super(config, totalGraph, objectDict, regionManager, placementPolicy);
        this.regionManager = regionManager;
        this.placementPolicy = placementPolicy;
    }

    @Override
    public ReadPathResult readTransferPath(Request req) {
        String dst = req.issueRegion;
        if (!objects.containsKey(req.objKey)) {
            throw new IllegalStateException("Object not found for read: " + req.objKey);
        }
        LogicalObject lo = objects.get(req.objKey);

        BigDecimal fullSize = lo.getSize() == null ? req.size : lo.getSize();

        Map<String, PhysicalObject> newPhysObjs = new HashMap<>();
        Map<String, PhysicalObject> physObjs = lo.physicalObjects;
        int inactive = 0;
        List<Map.Entry<String, PhysicalObject>> expiredReplicas = new ArrayList<>();
        List<Map.Entry<String, PhysicalObject>> expiredShards = new ArrayList<>();
        List<Map.Entry<String, PhysicalObject>> activeShards = new ArrayList<>();
        List<Map.Entry<String, PhysicalObject>> activeReplicas = new ArrayList<>();

        for (Map.Entry<String, PhysicalObject> entry : physObjs.entrySet()) {
            String region = entry.getKey();
            PhysicalObject obj = entry.getValue();
            if (req.timestamp.isBefore(obj.getStorageStartTime())) {
                newPhysObjs.put(region, obj);
                inactive += 1;
            } else {
                boolean isReplica = obj.getSize() != null && obj.getSize().compareTo(fullSize) >= 0;
                if (obj.getTtl() == Integer.MAX_VALUE || !obj.isExpired(req.timestamp)) {
                    newPhysObjs.put(region, obj);
                    if (isReplica) activeReplicas.add(entry);
                    else activeShards.add(entry);
                } else {
                    boolean fbBaseReplicaNoEvict = isReplica && Boolean.TRUE.equals(config.fixedBaseRegion)
                            && lo.baseRegion != null && region.equals(lo.baseRegion);
                    if (fbBaseReplicaNoEvict) {
                        newPhysObjs.put(region, obj);
                        activeReplicas.add(entry);
                    } else {
                        if (isReplica) expiredReplicas.add(entry);
                        else expiredShards.add(entry);
                    }
                }
            }
        }

        int totalShards = activeShards.size() + expiredShards.size();
        boolean evictAllShards = !expiredShards.isEmpty() && totalShards >= 2;
        if (evictAllShards) {
            for (Map.Entry<String, PhysicalObject> e : expiredShards) {
                PhysicalObject obj = e.getValue();
                Instant evictTime = obj.getStorageStartTime().plus(obj.getTtl(), ChronoUnit.SECONDS);
                regionManager.removeObjectFromRegion(obj.getLocationTag(), obj, evictTime);
                if ("dynamicttl".equals(config.placementPolicy) && placementPolicy != null) {
                    placementPolicy.updateGlobalTtl(obj.getTtl(), obj.getKey(), e.getKey(), dst);
                }
            }
            for (Map.Entry<String, PhysicalObject> e : activeShards) {
                regionManager.removeObjectFromRegion(e.getValue().getLocationTag(), e.getValue(), req.timestamp);
                newPhysObjs.remove(e.getKey());
            }
        } else {
            PhysicalObject toRestore = null;
            Instant toRestoreEvictTime = null;
            boolean toRestoreWasRemoved = false;
            for (Map.Entry<String, PhysicalObject> e : expiredShards) {
                PhysicalObject obj = e.getValue();
                Instant evictTime = obj.getStorageStartTime().plus(obj.getTtl(), ChronoUnit.SECONDS);
                regionManager.removeObjectFromRegion(obj.getLocationTag(), obj, evictTime);
                if ("dynamicttl".equals(config.placementPolicy) && placementPolicy != null) {
                    placementPolicy.updateGlobalTtl(obj.getTtl(), obj.getKey(), e.getKey(), dst);
                }
                if (toRestore == null || evictTime.isAfter(toRestoreEvictTime)) {
                    toRestore = obj;
                    toRestoreEvictTime = evictTime;
                    toRestoreWasRemoved = true;
                }
            }
            for (Map.Entry<String, PhysicalObject> e : expiredReplicas) {
                PhysicalObject obj = e.getValue();
                boolean isReplica = obj.getSize() != null && obj.getSize().compareTo(fullSize) >= 0;
                if (isReplica && Boolean.TRUE.equals(config.fixedBaseRegion)
                        && lo.baseRegion != null && e.getKey().equals(lo.baseRegion)) {
                    newPhysObjs.put(e.getKey(), obj);
                    activeReplicas.add(e);
                    continue;
                }
                Instant evictTime = obj.getStorageStartTime().plus(obj.getTtl(), ChronoUnit.SECONDS);
                // Align with CheapestTransferV2: always bill [storageStartTime, evictTime] on expiry before any
                // last-copy restore, so replica-mode storage accounting matches tevict_ranges_new exactly.
                regionManager.removeObjectFromRegion(obj.getLocationTag(), obj, evictTime);
                if ("dynamicttl".equals(config.placementPolicy) && placementPolicy != null) {
                    placementPolicy.updateGlobalTtl(obj.getTtl(), obj.getKey(), e.getKey(), dst);
                }
                if (toRestore == null || evictTime.isAfter(toRestoreEvictTime)) {
                    toRestore = obj;
                    toRestoreEvictTime = evictTime;
                    toRestoreWasRemoved = true;
                }
            }
            if (newPhysObjs.size() - inactive <= 0 && toRestore != null) {
                boolean toRestoreIsReplica = toRestore.getSize() != null && toRestore.getSize().compareTo(fullSize) >= 0;
                if (toRestoreIsReplica) {
                    newPhysObjs.put(toRestore.getLocationTag(), toRestore);
                    toRestore.setStorageStartTime(toRestoreEvictTime);
                    if (toRestoreWasRemoved) {
                        regionManager.addObjectToRegion(toRestore.getLocationTag(), toRestore);
                    }
                    toRestore.expireImmediate = true;
                    toRestore.setTtl(-1);
                }
            }
        }

        lo.physicalObjects = newPhysObjs;

        // V4: delta-member read recovery (§7.4) after expiry reconciliation so FB base / shard state is current.
        if (isDeltaMemberRead(lo)) {
            ReadPathResult deltaPath = buildDeltaReadPath(req, lo, dst);
            if (deltaPath != null) {
                return deltaPath;
            }
        }

        List<String> replicaRegions = new ArrayList<>();
        List<String> shardRegions = new ArrayList<>();
        collectReplicaAndShardRegions(req, lo, fullSize, newPhysObjs, replicaRegions, shardRegions);

        boolean isEC = shardRegions.size() >= 2;
        BigDecimal shardSize = fullSize.divide(BigDecimal.valueOf(EC_K), 20, RoundingMode.HALF_UP);
        if (replicaRegions.contains(dst)) {
            return buildHitResult(dst, req);
        }


        
        if (isEC && shardRegions.contains(dst)) {
            List<String> remoteShardCandidates = new ArrayList<>();
            for (String r : shardRegions) {
                if (!r.equals(dst)) {
                    remoteShardCandidates.add(r);
                }
            }
            String remoteShard = remoteShardCandidates.isEmpty() ? null : minCostRegion(remoteShardCandidates, dst);
            String bestReplica = replicaRegions.isEmpty() ? null : minCostRegion(replicaRegions, dst);
            ReadPlan p = chooseMinCostReadPlan(req,
                    remoteShard == null ? null : new ReadPlan("PlanB:fetch-1-shard",
                            edgeCost(remoteShard, dst) * shardSize.doubleValue(),
                            remoteShard, null),
                    bestReplica == null ? null : new ReadPlan("PlanC:fetch-full-replica",
                            edgeCost(bestReplica, dst) * fullSize.doubleValue(),
                            bestReplica, null));
            if ("PlanC:fetch-full-replica".equals(p.name)) {
                return buildSinglePath(p.src1, dst, req, fullSize, true);
            }
            return buildSinglePath(p.src1, dst, req, shardSize, true);
        }

        if (!replicaRegions.isEmpty() && !isEC) {
            String best = minCostRegion(replicaRegions, dst);
            return buildSinglePath(best, dst, req, fullSize, true);
        }

        if (isEC) {
            // 互补片强约束：EC(K=2) 重建必须拿到两个「不同 slot」的片，不能两片同类。
            List<String> compPair = pickComplementaryShardPair(shardRegions, dst, lo);
            if (replicaRegions.isEmpty()) {
                if (compPair == null) {
                    // 无互补片对、也无副本：判定为不可恢复，strict-fail（科研正确性优先）。
                    throw new IllegalStateException(
                            "EC read infeasible: no complementary shard pair and no replica for " + req.objKey);
                }
                return buildTwoShardPath(compPair.get(0), compPair.get(1), dst, req, shardSize);
            }
            String bestReplica = minCostRegion(replicaRegions, dst);
            ReadPlan replicaPlan = new ReadPlan("PlanD:fetch-full-replica",
                    edgeCost(bestReplica, dst) * fullSize.doubleValue(),
                    bestReplica, null);
            // 只有存在互补片对时才把「取两片」作为候选；否则唯一可行方案是副本。
            ReadPlan shardPlan = compPair == null ? null
                    : new ReadPlan("PlanE:fetch-k-shards",
                            edgeCost(compPair.get(0), dst) * shardSize.doubleValue()
                                    + edgeCost(compPair.get(1), dst) * shardSize.doubleValue(),
                            compPair.get(0), compPair.get(1));
            ReadPlan p = chooseMinCostReadPlan(req, replicaPlan, shardPlan);
            if ("PlanD:fetch-full-replica".equals(p.name)) {
                return buildSinglePath(p.src1, dst, req, fullSize, true);
            }
            return buildTwoShardPath(p.src1, p.src2, dst, req, shardSize);
        }

        if (shardRegions.size() == 1 && !replicaRegions.isEmpty()) {
            String best = minCostRegion(replicaRegions, dst);
            return buildSinglePath(best, dst, req, fullSize, true);
        }
        if (!replicaRegions.isEmpty()) {
            String best = minCostRegion(replicaRegions, dst);
            return buildSinglePath(best, dst, req, fullSize, true);
        }
        if (shardRegions.size() == 1) {
            // Degrade gracefully to baseline source selection when EC reconstruction is infeasible.
            lo.physicalObjects = physObjs;
            return super.readTransferPath(req);
        }
        // Fallback to baseline behavior if V3 partitioning leaves no feasible source.
        lo.physicalObjects = physObjs;
        return super.readTransferPath(req);
    }

    /**
     * V3-local replica/shard partitioning.
     * Keeps EC behavior isolated in V3 so V2 can stay Python-baseline.
     */
    private void collectReplicaAndShardRegions(Request req,
            LogicalObject lo,
            BigDecimal fullSize,
            Map<String, PhysicalObject> newPhysObjs,
            List<String> replicaRegions,
            List<String> shardRegions) {
        replicaRegions.clear();
        shardRegions.clear();
        for (Map.Entry<String, PhysicalObject> e : newPhysObjs.entrySet()) {
            PhysicalObject obj = e.getValue();
            if (req.timestamp.isBefore(obj.getStorageStartTime())) {
                continue;
            }
            boolean expired = obj.getTtl() != Integer.MAX_VALUE && obj.isExpired(req.timestamp);
            if (expired) {
                boolean fullReplica = obj.getSize() != null && obj.getSize().compareTo(fullSize) >= 0;
                boolean fbExpiredBaseFullReplica = Boolean.TRUE.equals(config.fixedBaseRegion)
                        && lo.baseRegion != null && e.getKey().equals(lo.baseRegion) && fullReplica;
                if (!fbExpiredBaseFullReplica) {
                    continue;
                }
            }
            if (obj.getSize() != null && obj.getSize().compareTo(fullSize) >= 0) {
                replicaRegions.add(e.getKey());
            } else {
                shardRegions.add(e.getKey());
            }
        }
    }

    private ReadPathResult buildHitResult(String dst, Request req) {
        DiGraph G = new DiGraph();
        G.addEdge(dst, dst, req.objKey, 0.0, 1, new int[] { 0 }, 1e12, 0.0, 0.0);
        G.setNodeAttr(dst, "src", true);
        G.setNodeAttr(dst, "dst", true);
        return new ReadPathResult(dst, G);
    }

    private ReadPathResult buildSinglePath(String src, String dst, Request req, BigDecimal size, boolean checkStatus) {
        if (checkStatus) {
            PhysicalObject po = objects.get(req.objKey).physicalObjects.get(src);
            if (po != null && po.getStatus() != Status.ready) {
                throw new IllegalStateException("Source not ready");
            }
        }
        DiGraph G = new DiGraph();
        Number throughput = (Number) totalGraph.getEdgeAttr(src, dst, "throughput");
        Number cost = (Number) totalGraph.getEdgeAttr(src, dst, "cost");
        Number latency = (Number) totalGraph.getEdgeAttr(src, dst, "latency");
        G.addEdge(src, dst, req.objKey, size, 1, new int[] { 0 }, throughput, cost, latency);
        G.setNodeAttr(src, "src", true);
        G.setNodeAttr(dst, "dst", true);
        return new ReadPathResult(src, G);
    }

    private ReadPathResult buildTwoShardPath(String r1, String r2, String dst, Request req, BigDecimal shardSize) {
        if (objects.get(req.objKey).physicalObjects.get(r1).getStatus() != Status.ready
                || objects.get(req.objKey).physicalObjects.get(r2).getStatus() != Status.ready) {
            throw new IllegalStateException("Source not ready");
        }
        List<DiGraph> graphs = new ArrayList<>();
        List<BigDecimal> sizes = new ArrayList<>();
        for (String src : new String[] { r1, r2 }) {
            DiGraph G = new DiGraph();
            Number throughput = (Number) totalGraph.getEdgeAttr(src, dst, "throughput");
            Number cost = (Number) totalGraph.getEdgeAttr(src, dst, "cost");
            Number latency = (Number) totalGraph.getEdgeAttr(src, dst, "latency");
            G.addEdge(src, dst, req.objKey, shardSize, 1, new int[] { 0 }, throughput, cost, latency);
            G.setNodeAttr(src, "src", true);
            G.setNodeAttr(dst, "dst", true);
            graphs.add(G);
            sizes.add(shardSize);
        }
        return new ReadPathResult(r1, graphs, sizes);
    }

    private String minCostRegion(List<String> regions, String dst) {
        if (regions == null || regions.isEmpty()) {
            throw new IllegalArgumentException("regions must not be empty");
        }
        String best = regions.get(0);
        for (int i = 1; i < regions.size(); i++) {
            best = minBy(best, regions.get(i), dst);
        }
        return best;
    }

    private List<String> pickTwoCheapestRegions(List<String> regions, String dst) {
        if (regions == null || regions.size() < 2) {
            throw new IllegalStateException("Need at least 2 shard regions");
        }
        String first = regions.get(0);
        String second = regions.get(1);
        if (!first.equals(minBy(first, second, dst))) {
            String tmp = first;
            first = second;
            second = tmp;
        }
        for (int i = 2; i < regions.size(); i++) {
            String r = regions.get(i);
            if (r.equals(minBy(r, first, dst))) {
                second = first;
                first = r;
            } else if (r.equals(minBy(r, second, dst))) {
                second = r;
            }
        }
        List<String> out = new ArrayList<>();
        out.add(first);
        out.add(second);
        return out;
    }

    /** 返回某分片所在 region 的 EC 片位身份；未知返回 {@code null}。 */
    private String slotOf(LogicalObject lo, String region) {
        PhysicalObject po = lo.physicalObjects.get(region);
        return po == null ? null : po.getShardSlot();
    }

    /**
     * 选出成本最低的「互补片对」——两片 slot 不同且均非 REPLICA，满足 EC(K=2) 可恢复。
     *
     * <p>身份缺失时的向后兼容：若所有候选分片都没有 slot 身份（{@code null}），退回旧的
     * 「按成本取两片」近似（假定互补）。一旦有任何分片带身份，就启用强约束：只接受 slot
     * 互异的片对，找不到则返回 {@code null}（交由上层 fallback 到副本或 strict-fail）。
     *
     * @return 两个 region 的列表（成本升序无关，按组合边成本最小选），无可行对返回 {@code null}
     */
    private List<String> pickComplementaryShardPair(List<String> shardRegions, String dst, LogicalObject lo) {
        if (shardRegions == null || shardRegions.size() < 2) {
            return null;
        }
        boolean allSlotsKnown = true;
        for (String r : shardRegions) {
            if (slotOf(lo, r) == null) {
                allSlotsKnown = false;
                break;
            }
        }
        if (!allSlotsKnown) {
            List<String> strict = pickStrictComplementaryPair(shardRegions, dst, lo);
            return strict != null ? strict : pickTwoCheapestRegions(shardRegions, dst);
        }
        return pickStrictComplementaryPair(shardRegions, dst, lo);
    }

    private List<String> pickStrictComplementaryPair(List<String> shardRegions, String dst, LogicalObject lo) {
        String bestA = null;
        String bestB = null;
        double bestCost = Double.MAX_VALUE;
        for (int i = 0; i < shardRegions.size(); i++) {
            for (int j = i + 1; j < shardRegions.size(); j++) {
                String ri = shardRegions.get(i);
                String rj = shardRegions.get(j);
                String si = slotOf(lo, ri);
                String sj = slotOf(lo, rj);
                if (si == null || sj == null) {
                    continue; // 身份不全，不能保证互补
                }
                if ("REPLICA".equals(si) || "REPLICA".equals(sj)) {
                    continue; // 副本不参与「取两片」组合
                }
                if (si.equals(sj)) {
                    continue; // 同类片不可恢复
                }
                double c = edgeCost(ri, dst) + edgeCost(rj, dst);
                if (c < bestCost) {
                    bestCost = c;
                    bestA = ri;
                    bestB = rj;
                }
            }
        }
        if (bestA == null) {
            return null;
        }
        List<String> out = new ArrayList<>();
        out.add(bestA);
        out.add(bestB);
        return out;
    }

    private String minBy(String a, String b, String dst) {
        double ca = edgeCost(a, dst), cb = edgeCost(b, dst);
        if (Double.compare(ca, cb) != 0) {
            return ca <= cb ? a : b;
        }
        return latency(a, dst) <= latency(b, dst) ? a : b;
    }

    private ReadPlan chooseMinCostReadPlan(Request req, ReadPlan a, ReadPlan b) {
        if (a == null && b == null) {
            throw new IllegalStateException("No feasible read plan for " + req.objKey);
        }
        if (a == null) {
            Logger.info(String.format("[READ-PLAN-V3] key=%s choose=%s cost=%.9f", req.objKey, b.name, b.totalCost));
            return b;
        }
        if (b == null) {
            Logger.info(String.format("[READ-PLAN-V3] key=%s choose=%s cost=%.9f", req.objKey, a.name, a.totalCost));
            return a;
        }
        ReadPlan chosen = a.totalCost <= b.totalCost ? a : b;
        Logger.info(String.format("[READ-PLAN-V3] key=%s choose=%s cost=%.9f (A=%s:%.9f,B=%s:%.9f)",
                req.objKey, chosen.name, chosen.totalCost, a.name, a.totalCost, b.name, b.totalCost));
        return chosen;
    }

    private double edgeCost(String src, String dst) {
        Object v = totalGraph.getEdgeAttr(src, dst, "cost");
        return v instanceof Number ? ((Number) v).doubleValue() : Double.MAX_VALUE;
    }

    private double latency(String src, String dst) {
        Object v = totalGraph.getEdgeAttr(src, dst, "latency");
        return v instanceof Number ? ((Number) v).doubleValue() : Double.MAX_VALUE;
    }

    // ===== V4 delta-member read recovery (§7.4) =====

    private boolean isDeltaMemberRead(LogicalObject lo) {
        return Boolean.TRUE.equals(config.deltaEnabled)
                && lo != null && !lo.isBase && lo.baseKey != null && lo.deltaRatio < 0.999999;
    }

    /**
     * Read path for a delta member: a patch hop (size×r; zero-cost when the patch is local) plus, when the
     * base is not locally recoverable, a base hop (full base via replica, or two base shards). Each hop is a
     * real {@code src→dst} edge so the simulator charges {@code edgePrice × bytes} per hop (no zero-priced
     * hit masking remote-base cost). Returns {@code null} when the base is unknown or the patch is unplaced,
     * so the caller falls back to baseline object handling.
     */
    private ReadPathResult buildDeltaReadPath(Request req, LogicalObject lo, String dst) {
        LogicalObject baseLo = objects.get(lo.baseKey);
        if (baseLo == null) {
            return null;
        }
        BigDecimal targetSize = lo.getSize() == null ? req.size : lo.getSize();
        double r = Math.max(0.0, Math.min(1.0, lo.deltaRatio));
        BigDecimal patchSize = targetSize.multiply(BigDecimal.valueOf(r));
        BigDecimal baseSize = baseLo.getSize() == null ? targetSize : baseLo.getSize();

        List<DiGraph> graphs = new ArrayList<>();
        List<BigDecimal> sizes = new ArrayList<>();
        String firstSrc = dst;

        // ---- patch hop ----
        if (readyFullAt(lo, dst, req.timestamp, patchSize)) {
            graphs.add(localGraph(dst, req.objKey));
            sizes.add(BigDecimal.ZERO);
        } else {
            String patchSrc = cheapestReadyRegion(lo, dst, req.timestamp);
            if (patchSrc == null) {
                return null; // patch not placed anywhere → baseline fallback
            }
            graphs.add(hopGraph(patchSrc, dst, req.objKey, patchSize));
            sizes.add(patchSize);
            firstSrc = patchSrc;
        }

        // ---- base hop (only when base not locally recoverable) ----
        if (!readyFullAt(baseLo, dst, req.timestamp, baseSize)) {
            if (!appendBaseRecovery(baseLo, dst, req.objKey, baseSize, req.timestamp, graphs, sizes)) {
                return null; // base unrecoverable → baseline fallback
            }
            if (dst.equals(firstSrc)) {
                firstSrc = graphSrc(graphs.get(graphs.size() - 1), dst);
            }
        }
        return new ReadPathResult(firstSrc, graphs, sizes);
    }

    /** Append a base-recovery hop: full base via cheapest replica, else two cheapest base shards. */
    private boolean appendBaseRecovery(LogicalObject baseLo, String dst, String objKey,
            BigDecimal baseSize, Instant ts, List<DiGraph> graphs, List<BigDecimal> sizes) {
        List<String> replicaRegions = new ArrayList<>();
        List<String> shardRegions = new ArrayList<>();
        for (Map.Entry<String, PhysicalObject> e : baseLo.physicalObjects.entrySet()) {
            if (e.getKey().equals(dst)) {
                continue;
            }
            PhysicalObject po = e.getValue();
            if (!readyNotExpired(po, ts)) {
                continue;
            }
            if (po.getSize() != null && po.getSize().compareTo(baseSize) >= 0) {
                replicaRegions.add(e.getKey());
            } else {
                shardRegions.add(e.getKey());
            }
        }
        if (!replicaRegions.isEmpty()) {
            String src = minCostRegion(replicaRegions, dst);
            graphs.add(hopGraph(src, dst, objKey, baseSize));
            sizes.add(baseSize);
            return true;
        }
        if (shardRegions.size() >= 2) {
            BigDecimal shardSize = baseSize.divide(BigDecimal.valueOf(EC_K), 20, RoundingMode.HALF_UP);
            List<String> pair = pickComplementaryShardPair(shardRegions, dst, baseLo);
            if (pair == null) {
                return false;
            }
            for (String s : pair) {
                graphs.add(hopGraph(s, dst, objKey, shardSize));
                sizes.add(shardSize);
            }
            return true;
        }
        return false;
    }

    private boolean readyNotExpired(PhysicalObject po, Instant ts) {
        if (po == null || !po.ready(ts)) {
            return false;
        }
        return po.getTtl() == Integer.MAX_VALUE || !po.isExpired(ts);
    }

    private boolean readyFullAt(LogicalObject lo, String region, Instant ts, BigDecimal fullSize) {
        PhysicalObject po = lo.physicalObjects.get(region);
        return readyNotExpired(po, ts) && po.getSize() != null && po.getSize().compareTo(fullSize) >= 0;
    }

    private String cheapestReadyRegion(LogicalObject lo, String dst, Instant ts) {
        List<String> cand = new ArrayList<>();
        for (Map.Entry<String, PhysicalObject> e : lo.physicalObjects.entrySet()) {
            if (e.getKey().equals(dst)) {
                continue;
            }
            if (readyNotExpired(e.getValue(), ts)) {
                cand.add(e.getKey());
            }
        }
        return cand.isEmpty() ? null : minCostRegion(cand, dst);
    }

    private DiGraph localGraph(String dst, String objKey) {
        DiGraph g = new DiGraph();
        g.addEdge(dst, dst, objKey, 0.0, 1, new int[] { 0 }, 1e12, 0.0, 0.0);
        g.setNodeAttr(dst, "src", true);
        g.setNodeAttr(dst, "dst", true);
        return g;
    }

    private DiGraph hopGraph(String src, String dst, String objKey, BigDecimal size) {
        DiGraph g = new DiGraph();
        Number throughput = (Number) totalGraph.getEdgeAttr(src, dst, "throughput");
        Number cost = (Number) totalGraph.getEdgeAttr(src, dst, "cost");
        Number latency = (Number) totalGraph.getEdgeAttr(src, dst, "latency");
        g.addEdge(src, dst, objKey, size, 1, new int[] { 0 }, throughput, cost, latency);
        g.setNodeAttr(src, "src", true);
        g.setNodeAttr(dst, "dst", true);
        return g;
    }

    private String graphSrc(DiGraph g, String fallback) {
        for (String n : g.getNodes()) {
            if (Boolean.TRUE.equals(g.getNodeAttrs(n).get("src"))) {
                return n;
            }
        }
        return fallback;
    }
}

