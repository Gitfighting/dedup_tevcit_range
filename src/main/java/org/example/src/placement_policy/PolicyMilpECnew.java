package org.example.src.placement_policy;

import com.gurobi.gurobi.*;
import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.utils.Definitions;
import org.example.src.utils.Logger;

import java.time.Instant;
import java.util.*;

/**
 * MILP-EC Region-Level Strategy (New Version)
 *
 * 与已移除的 object-level MILP 版本的根本区别：
 *   旧版：事件 = per-object，状态 = per-object MilpStorageState
 *   新版：事件 = per-region RegionStats（聚合 sizeGB），状态 = per-region RegionDecision
 *
 * 每个窗口：
 *   1. 按 issueRegion 累计 (writeSizeGB, readSizeGB)
 *   2. 为每个活跃 region 解一个 MILP（最多 n_regions 次，固定上界）
 *   3. 将 RegionDecision（mode, shardAt[], ttlHours）缓存于 decisions map
 *   4. 后续请求查 RegionDecision 判断是否需要远程拉取
 *
 * MILP 变量（每 region 一组，共 ~1+9+1+9+8 = 28 个）：
 *   d        — 0=副本, 1=EC
 *   sigma[i] — region i 是否存放 shard
 *   hatTau   — EC 统一 TTL（小时）
 *   z[i]     — sigma[i]*hatTau 线性化（McCormick）
 *   vread[i] — 读路由：从 region i 拉取 shard 的比例（i ≠ rIdx）
 */
public class PolicyMilpECnew extends PolicyTevictRangesNew {

    // ── RS(2,1) 参数 ──
    private static final int EC_K = 2;
    private static final int EC_M = 1;
    private static final int EC_N = EC_K + EC_M; // 3

    // =====================================================================
    // 内部数据结构（全部 region 粒度，无 objKey）
    // =====================================================================

    /**
     * 当前窗口内某 region 的累积统计。
     * 不记录具体对象，只关心"这个 region 总共写了多少 GB、需要远程拉取多少 GB"。
     */
    public static class RegionStats {
        double writeSizeGB = 0;
        double readSizeGB  = 0;

        void addWrite(double sizeGB) { writeSizeGB += sizeGB; }
        void addRead (double sizeGB) { readSizeGB  += sizeGB; }
        boolean empty() { return writeSizeGB <= 0 && readSizeGB <= 0; }
    }

    /**
     * MILP 对某 region 的决策结果。
     * 替代旧版的 MilpStorageState，完全在 region 粒度记录。
     */
    public static class RegionDecision {
        /** 0 = replica，1 = EC */
        public final int mode;
        /** EC 模式下哪些 region 存放 shard（长度 = n_regions） */
        public final boolean[] shardAt;
        /** 统一 TTL（小时） */
        public final double ttlHours;
        /** 决策生效的时刻（epoch 秒） */
        public final long decidedAtSec;

        RegionDecision(int mode, boolean[] shardAt, double ttlHours, long decidedAtSec) {
            this.mode = mode;
            this.shardAt = shardAt;
            this.ttlHours = ttlHours;
            this.decidedAtSec = decidedAtSec;
        }

        /** 决策是否已过期 */
        boolean expired(long nowSec) {
            return ttlHours > 0 && nowSec >= decidedAtSec + (long)(ttlHours * 3600.0);
        }

        /**
         * 判断 regionIdx 是否有本地数据（不需要跨 region 拉取）：
         *   副本模式：该 region 自己就是写入方，副本在本地
         *   EC 模式：sigma[regionIdx] = true，本地有 shard
         */
        boolean hasLocalData(int regionIdx) {
            if (mode == 0) return true; // replica 始终本地
            return shardAt != null && regionIdx >= 0
                   && regionIdx < shardAt.length && shardAt[regionIdx];
        }
    }

    // =====================================================================
    // 状态字段
    // =====================================================================

    /** 当前窗口内各 region 的累积统计 */
    private final Map<String, RegionStats> windowStats = new LinkedHashMap<>();

    /** 最近一次 MILP 对各 region 的决策（有效期内） */
    private final Map<String, RegionDecision> decisions = new HashMap<>();

    /** EC TTL 缓存，供 getTevict() 返回给 SimulatorV2 */
    private final Map<String, Double> milpEcTtlByRegion = new HashMap<>();

    private long windowStartHours = -1;
    private final long windowSizeHours;

    /** 当前窗口内累积的事件数（write+read），达到阈值即触发求解 */
    private int windowEventCount = 0;
    private static final int WINDOW_EVENT_THRESHOLD = 500;

    /** 持久化 Gurobi 环境（整个模拟生命周期复用，避免反复创建/销毁） */
    private GRBEnv persistentEnv;
    private long totalSolveMs = 0;
    private int  totalSolveCount = 0;

    /** 进度计数 */
    private int reqCount = 0;
    private long firstReqMs = -1;
    private int windowCount = 0;

    // =====================================================================
    // 构造器
    // =====================================================================

    public PolicyMilpECnew(Config config, DiGraph totalGraph,
                           Map<String, LogicalObject> logicalObjects,
                           RegionManager regionManager) {
        super(config, totalGraph, logicalObjects, regionManager);
        long ws = config.windowSize != null ? config.windowSize.longValue() : 24L;
        this.windowSizeHours = ws > 0 ? ws : 24L;
    }

    // =====================================================================
    // Override: place — 写请求时，累计 region 写量
    // =====================================================================

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            reqCount++;
            if (firstReqMs == -1) firstReqMs = System.currentTimeMillis();

            if (reqCount % 10000 == 0) {
                long elapsed = System.currentTimeMillis() - firstReqMs;
                Logger.info(String.format("[MILP-ECnew] progress: %d reqs, %.1f s, %.0f req/s, windows=%d, decisions=%d",
                        reqCount, elapsed / 1000.0,
                        reqCount / (elapsed / 1000.0),
                        windowCount, decisions.size()));
            }

            long reqH = req.timestamp.getEpochSecond() / 3600;
            if (windowStartHours == -1) windowStartHours = reqH;

            double sizeGB = req.size.doubleValue() / Definitions.GB;
            windowStats.computeIfAbsent(req.issueRegion, k -> new RegionStats())
                       .addWrite(sizeGB);
            windowEventCount++;

            checkAndSolveWindow(req.timestamp);
        }
        return Collections.singletonList(req.issueRegion);
    }

    // =====================================================================
    // Override: updatePastRequests — 读请求时，判断是否需要远程拉取
    // =====================================================================

    @Override
    public void updatePastRequests(Request request, String placeRegion) {
        super.updatePastRequests(request, placeRegion);
        reqCount++;

        if ("read".equals(request.op)) {
            long nowSec = request.timestamp.getEpochSecond();
            int rIdx = regions.indexOf(request.issueRegion);

            // 查询该 region 的当前决策是否保证本地有数据
            RegionDecision dec = decisions.get(request.issueRegion);
            boolean hasLocal = dec != null
                               && !dec.expired(nowSec)
                               && dec.hasLocalData(rIdx);

            if (!hasLocal) {
                double sizeGB = request.size.doubleValue() / Definitions.GB;
                windowStats.computeIfAbsent(request.issueRegion, k -> new RegionStats())
                           .addRead(sizeGB);
            }
            windowEventCount++;
        }

        checkAndSolveWindow(request.timestamp);
    }

    // =====================================================================
    // Override: getTevict — 返回 MILP 算出的 EC TTL（秒）
    // =====================================================================

    @Override
    public double getTevict(String src, String dst, Instant timestampInstant) {
        Double ttlHours = milpEcTtlByRegion.get(src);
        if (ttlHours != null && ttlHours > 0) {
            return ttlHours * 3600.0;
        }
        // SimulatorV2 对 milp_ec_new 的 WRITE 会以 src==dst 调用 getTevict。
        // 父类的 regionPairsTtl 缓存从不填充自身配对（循环跳过 region.equals(region2)），
        // 导致每次调用都 cache miss 并重跑 72 对 calcEvictCost —— 这是致命性能陷阱。
        // 自身配对直接用本 region 的 teven/2 公式返回。
        if (src.equals(dst)) {
            double sp = regionStoragePrice(src);
            double tevenSec = (avgNetworkCost / sp) * 86400.0;
            return tevenSec / 2.0;
        }
        return super.getTevict(src, dst, timestampInstant);
    }

    // =====================================================================
    // 模拟结束时强制求解剩余窗口
    // =====================================================================

    public void flushPendingEvents(Instant now) {
        if (!windowStats.isEmpty()) {
            solveWindowBatch(now);
            windowStartHours = now.getEpochSecond() / 3600;
        }
        close();
    }

    // =====================================================================
    // 窗口触发判断
    // =====================================================================

    private void checkAndSolveWindow(Instant now) {
        long reqH = now.getEpochSecond() / 3600;
        if (windowStartHours == -1) windowStartHours = reqH;

        boolean windowFull      = reqH - windowStartHours >= windowSizeHours;
        boolean tooManyRegions  = windowStats.size() > regions.size() * 2;
        boolean enoughEvents    = windowEventCount >= WINDOW_EVENT_THRESHOLD;

        if (windowFull || tooManyRegions || enoughEvents) {
            solveWindowBatch(now);
            windowStartHours = reqH;
            windowEventCount = 0;
        }
    }

    // =====================================================================
    // Gurobi 环境管理：懒初始化，整个模拟生命周期复用
    // =====================================================================

    private GRBEnv getOrCreateEnv() throws GRBException {
        if (persistentEnv == null) {
            long t0 = System.currentTimeMillis();
            persistentEnv = new GRBEnv(true);
            persistentEnv.set("OutputFlag", "0");
            persistentEnv.start();
            Logger.info(String.format("[MILP-ECnew] GRBEnv created in %d ms",
                    System.currentTimeMillis() - t0));
        }
        return persistentEnv;
    }

    /** 模拟结束时释放 Gurobi 资源 */
    public void close() {
        if (persistentEnv != null) {
            try { persistentEnv.dispose(); } catch (GRBException ignored) {}
            persistentEnv = null;
        }
        long totalMs = firstReqMs > 0 ? System.currentTimeMillis() - firstReqMs : 0;
        String sep = "============================================================";
        Logger.info(sep);
        Logger.info("[MILP-ECnew] FINAL SUMMARY");
        Logger.info(String.format("  total requests   = %d", reqCount));
        Logger.info(String.format("  total wall time  = %.1f s", totalMs / 1000.0));
        Logger.info(String.format("  throughput       = %.0f req/s", reqCount > 0 ? reqCount / (totalMs / 1000.0) : 0));
        Logger.info(String.format("  windows solved   = %d", windowCount));
        Logger.info(String.format("  MILP solves      = %d  (total %d ms, avg %.1f ms)",
                totalSolveCount, totalSolveMs,
                totalSolveCount > 0 ? (double) totalSolveMs / totalSolveCount : 0));
        Logger.info(String.format("  active decisions = %d", decisions.size()));
        Logger.info(sep);
    }

    // =====================================================================
    // 批量求解入口：每个窗口最多 n_regions 次 MILP
    // =====================================================================

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
            double tevenSec   = (avgNetworkCost / sp[i]) * 86400.0;
            double tevenHours = tevenSec / 3600.0;
            double histTtlH   = calcEvictCost(regions.get(i), tevenHours, avgNetworkCost);
            tauH[i] = (histTtlH > 0) ? histTtlH : (tevenHours / 2.0);
            tauH[i] = Math.max(tauH[i], 1.0);
        }

        try {
            GRBEnv env = getOrCreateEnv();
            windowCount++;

            long windowT0 = System.currentTimeMillis();
            int solved = 0;
            for (Map.Entry<String, RegionStats> entry : windowStats.entrySet()) {
                if (!entry.getValue().empty()) {
                    solveRegionMilp(env, entry.getKey(), entry.getValue(),
                            n, sp, tc, tauH, nowSec);
                    solved++;
                }
            }
            long elapsed = System.currentTimeMillis() - windowT0;
            totalSolveMs += elapsed;
            totalSolveCount += solved;
            Logger.info(String.format("[MILP-ECnew] window solved %d regions in %d ms", solved, elapsed));

        } catch (GRBException ex) {
            Logger.info("[MILP-ECnew] Gurobi error: " + ex.getMessage());
            if (persistentEnv != null) {
                try { persistentEnv.dispose(); } catch (GRBException ignored) {}
                persistentEnv = null;
            }
        }

        windowStats.clear();
    }

    // =====================================================================
    // 单 region MILP
    //
    // 输入：该 region 在窗口内的聚合量 (writeSizeGB, readSizeGB)
    // 输出：RegionDecision 写入 decisions map
    //
    // 约束结构（9 region 时共 ~28 变量，~40 约束）：
    //   C1: Σ sigma[i] = 3*d                    (EC 时选 3 个 shard region)
    //   C2: sigma[rIdx] >= d                     (本地必须有 shard)
    //   C3: hatTau = max_{sigma[i]=1} tau*[i]    (Big-M 线性化)
    //   C4: z[i] = sigma[i]*hatTau               (McCormick 线性化)
    //   C5: Σ vread[i] = (K-1)*d,  vread[i]<=sigma[i]  (读路由)
    //
    // 目标函数：
    //   写副本存储 + EC 分片存储 + EC 分发传输 + EC 读取传输
    // =====================================================================

    private void solveRegionMilp(GRBEnv env, String regionKey, RegionStats stats,
                                  int n, double[] sp, double[][] tc, double[] tauH,
                                  long nowSec) {
        int rIdx = regions.indexOf(regionKey);
        if (rIdx < 0) return;

        double sWrites = stats.writeSizeGB;
        double sReads  = stats.readSizeGB;

        double maxTauH = 0;
        for (double t : tauH) maxTauH = Math.max(maxTauH, t);
        if (maxTauH <= 0) maxTauH = 24.0;
        final double BIG_M = maxTauH + 1.0;

        GRBModel model = null;
        try {
            model = new GRBModel(env);
            model.set(GRB.DoubleParam.MIPGap, 0.05);
            model.set(GRB.DoubleParam.TimeLimit, 2.0);

            // ── 决策变量 ──
            GRBVar d      = model.addVar(0, 1,       0, GRB.BINARY,     "d");
            GRBVar hatTau = model.addVar(0, maxTauH, 0, GRB.CONTINUOUS, "ht");

            GRBVar[] sigma = new GRBVar[n];
            GRBVar[] z     = new GRBVar[n];
            GRBVar[] vread = new GRBVar[n];
            for (int i = 0; i < n; i++) {
                sigma[i] = model.addVar(0, 1,       0, GRB.BINARY,     "s"  + i);
                z[i]     = model.addVar(0, maxTauH, 0, GRB.CONTINUOUS, "z"  + i);
                if (i != rIdx)
                    vread[i] = model.addVar(0, 1, 0, GRB.CONTINUOUS, "vr" + i);
            }

            GRBLinExpr obj = new GRBLinExpr();

            // ── C1: Σ sigma[i] = EC_N * d ──
            {
                GRBLinExpr lhs = new GRBLinExpr();
                for (int i = 0; i < n; i++) lhs.addTerm(1.0, sigma[i]);
                GRBLinExpr rhs = new GRBLinExpr();
                rhs.addTerm((double) EC_N, d);
                model.addConstr(lhs, GRB.EQUAL, rhs, "sigSum");
            }

            // ── C2: sigma[rIdx] >= d（本地 shard）──
            model.addConstr(sigma[rIdx], GRB.GREATER_EQUAL, d, "local");

            // ── C_PROVIDER: EC 分片限制在同一云厂商内 ──
            // sigma[i] <= 1-d 对所有跨厂商 region i（EC 模式时强制 sigma[i]=0）
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

            // ── C3: hatTau >= tau*[i] * sigma[i]（仅下界）──
            // 目标函数中 EC 存储成本 ∝ hatTau，最小化时优化器自然将 hatTau
            // 压至 max_{sigma[i]=1} tauH[i]，无需显式上界。
            for (int i = 0; i < n; i++) {
                double ti = tauH[i];
                GRBLinExpr lb = new GRBLinExpr();
                lb.addTerm(ti, sigma[i]);
                model.addConstr(hatTau, GRB.GREATER_EQUAL, lb, "htLB" + i);
            }

            // ── C4: z[i] = sigma[i] * hatTau（McCormick）──
            for (int i = 0; i < n; i++) {
                GRBLinExpr zub1 = new GRBLinExpr();
                zub1.addTerm(BIG_M, sigma[i]);
                model.addConstr(z[i], GRB.LESS_EQUAL, zub1,  "zU1_" + i);
                model.addConstr(z[i], GRB.LESS_EQUAL, hatTau, "zU2_" + i);

                GRBLinExpr zlb = new GRBLinExpr();
                zlb.addTerm(1.0, hatTau);
                zlb.addTerm(BIG_M, sigma[i]);
                zlb.addConstant(-BIG_M);
                model.addConstr(z[i], GRB.GREATER_EQUAL, zlb, "zL_" + i);
            }

            // ── C5: 读路由 Σ vread[i] = (EC_K-1)*d，vread[i]<=sigma[i] ──
            if (sReads > 0) {
                GRBLinExpr lhs = new GRBLinExpr();
                for (int i = 0; i < n; i++)
                    if (i != rIdx) lhs.addTerm(1.0, vread[i]);
                GRBLinExpr rhs = new GRBLinExpr();
                rhs.addTerm((double)(EC_K - 1), d);
                model.addConstr(lhs, GRB.EQUAL, rhs, "readSum");

                for (int i = 0; i < n; i++)
                    if (i != rIdx)
                        model.addConstr(vread[i], GRB.LESS_EQUAL, sigma[i], "vrLE" + i);
            }

            // ── 目标函数 ──

            // 写存储成本
            if (sWrites > 0) {
                // 副本存储：sWrites * sp[r] * tauH[r] / 720 * (1-d)
                double repStore = sWrites * sp[rIdx] * (tauH[rIdx] / 720.0);
                obj.addConstant(repStore);
                obj.addTerm(-repStore, d);

                // EC 分片存储：Σ_i (sWrites/K) * sp[i] / 720 * z[i]
                for (int i = 0; i < n; i++)
                    obj.addTerm((sWrites / EC_K) * sp[i] / 720.0, z[i]);

                // EC 分发传输：Σ_{i≠r} (sWrites/K) * tc[r→i] * sigma[i]
                for (int i = 0; i < n; i++)
                    if (i != rIdx)
                        obj.addTerm((sWrites / EC_K) * tc[rIdx][i], sigma[i]);
            }

            // 读取拉取成本
            if (sReads > 0) {
                // 副本模式（d=0）：需要从其他 region 拉取完整对象
                // 使用 avgNetworkCost 近似拉取单价：sReads * avgNetworkCost * (1-d)
                double repReadCost = sReads * avgNetworkCost;
                obj.addConstant(repReadCost);
                obj.addTerm(-repReadCost, d);

                // EC 模式（d=1）：从 vread 路由的远程 shard region 拉取 1 片
                for (int i = 0; i < n; i++)
                    if (i != rIdx)
                        obj.addTerm((sReads / EC_K) * tc[i][rIdx], vread[i]);
            }

            model.setObjective(obj, GRB.MINIMIZE);
            model.optimize();

            // ── 提取解并更新 RegionDecision ──
            int status = model.get(GRB.IntAttr.Status);
            if (status == GRB.Status.OPTIMAL || status == GRB.Status.SUBOPTIMAL) {
                int dec = (int) Math.round(d.get(GRB.DoubleAttr.X));

                boolean[] sigV = new boolean[n];
                for (int i = 0; i < n; i++)
                    sigV[i] = Math.round(sigma[i].get(GRB.DoubleAttr.X)) == 1;

                double ht = hatTau.get(GRB.DoubleAttr.X);
                if (ht <= 0) ht = tauH[rIdx];

                // 保存决策（有效期 = ht 小时，从当前 nowSec 起计）
                decisions.put(regionKey, new RegionDecision(dec, sigV, ht, nowSec));

                if (dec == 1) {
                    milpEcTtlByRegion.put(regionKey, ht);
                    Logger.info(String.format("[MILP-ECnew] region=%-25s EC  ttl=%.1fh shards=%s",
                            regionKey, ht, shardList(sigV)));
                } else {
                    Logger.info(String.format("[MILP-ECnew] region=%-25s REP ttl=%.1fh",
                            regionKey, tauH[rIdx]));
                }
            } else {
                Logger.info("[MILP-ECnew] region='" + regionKey
                        + "' infeasible/timeout status=" + status);
            }

        } catch (GRBException ex) {
            Logger.info("[MILP-ECnew] region='" + regionKey
                    + "' Gurobi error: " + ex.getMessage());
        } finally {
            if (model != null) {
                try { model.dispose(); } catch (Exception ignored) {}
            }
        }
    }

    // =====================================================================
    // 辅助方法
    // =====================================================================

    private String shardList(boolean[] sigV) {
        List<String> r = new ArrayList<>();
        for (int i = 0; i < sigV.length; i++)
            if (sigV[i]) r.add(regions.get(i));
        return r.toString();
    }

    private double regionStoragePrice(String region) {
        Object p = totalGraph.getNodeAttrs(region).get("priceStorage");
        if (p instanceof Number && ((Number) p).doubleValue() > 0)
            return ((Number) p).doubleValue();
        throw new IllegalStateException(
                "[MILP-ECnew] priceStorage unavailable for '" + region + "': " + p);
    }

    private double regionTransCost(String src, String dst) {
        Object c = totalGraph.getEdgeAttrs(src, dst).get("cost");
        if (!(c instanceof Number))
            throw new IllegalStateException(
                    "[MILP-ECnew] transfer cost unavailable '"
                    + src + "'->'" + dst + "': " + c);
        return ((Number) c).doubleValue();
    }
}
