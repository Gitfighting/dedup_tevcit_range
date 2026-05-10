package org.example.src.placement_policy;

import com.gurobi.gurobi.*;
import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.utils.Definitions;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.*;

/**
 * MILP-EC 策略：RS(2,1) Erasure Coding 与 Replica 的动态自适应混合放置。
 *
 * 对象写入或读重建后，通过 MILP 决定"放副本"还是"RS(2,1) 编码为 3 个 shard 分发"。
 * MILP 在滚动窗口内按对象分组批量求解，目标是最小化存储 + 传输总成本。
 *
 * 求解方式：
 *   按对象分组，每个对象的事件按时间排序后构建子 MILP。
 *   同一对象的连续事件通过"前驱链"建立布局耦合：
 *     - 写事件决定初始布局 (replica 或 EC shard)
 *     - 后续读事件的拉取代价通过路由变量与前驱决策耦合
 *     - 读重建后的存储决策 (保留 replica / 重编码 EC) 影响后续事件
 */
public class PolicyMilpEC extends PolicyTevictRangesNew {

    // ── RS(2,1) 固定参数 ──
    private static final int EC_K = 2;
    private static final int EC_M = 1;
    private static final int EC_N = EC_K + EC_M; // 3

    private static final int MAX_WINDOW_EVENTS = 5000;

    // =====================================================================
    // 内部类型
    // =====================================================================

    public enum EventType {
        WRITE,
        READ_RECONSTRUCT
    }

    public static class MilpEvent {
        final EventType type;
        final String issueRegion;
        final String objKey;
        final double sizeGB;
        final Instant timestamp;
        final long timestampSeconds;

        int decision = 0;
        boolean[] sigma;
        double ecUnifiedTtlHours;

        MilpEvent(EventType type, String issueRegion, String objKey,
                  BigDecimal sizeBytes, Instant timestamp) {
            this.type = type;
            this.issueRegion = issueRegion;
            this.objKey = objKey;
            this.sizeGB = sizeBytes.doubleValue() / Definitions.GB;
            this.timestamp = timestamp;
            this.timestampSeconds = timestamp.getEpochSecond();
        }
    }

    public static class StorageEntry {
        public final boolean isReplica;
        public final long expireAtSeconds;
        public final double sizeGB;

        StorageEntry(boolean isReplica, long expireAtSeconds, double sizeGB) {
            this.isReplica = isReplica;
            this.expireAtSeconds = expireAtSeconds;
            this.sizeGB = sizeGB;
        }
    }

    /** MILP 内部存储状态跟踪，独立于 SimulatorV2 的 logicalObjects */
    public static class MilpStorageState {
        private final Map<String, Map<String, StorageEntry>> state = new HashMap<>();

        public void placeReplica(String objKey, String region,
                                 long startSec, double ttlSec, double sizeGB) {
            long expire = (ttlSec <= 0) ? -1L : startSec + (long) ttlSec;
            state.computeIfAbsent(objKey, k -> new HashMap<>())
                 .put(region, new StorageEntry(true, expire, sizeGB));
        }

        public void placeECShards(String objKey, List<String> shardRegions,
                                   long startSec, double ttlSec, double sizeGB) {
            long expire = (ttlSec <= 0) ? -1L : startSec + (long) ttlSec;
            Map<String, StorageEntry> m = state.computeIfAbsent(objKey, k -> new HashMap<>());
            for (String r : shardRegions) {
                m.put(r, new StorageEntry(false, expire, sizeGB / EC_K));
            }
        }

        public boolean hasReplica(String objKey, String region, long nowSec) {
            StorageEntry e = getEntry(objKey, region);
            return e != null && e.isReplica && !expired(e, nowSec);
        }

        public Map<String, StorageEntry> getShardRegions(String objKey, long nowSec) {
            Map<String, StorageEntry> result = new HashMap<>();
            Map<String, StorageEntry> m = state.get(objKey);
            if (m != null) {
                for (Map.Entry<String, StorageEntry> entry : m.entrySet()) {
                    if (!entry.getValue().isReplica && !expired(entry.getValue(), nowSec))
                        result.put(entry.getKey(), entry.getValue());
                }
            }
            return result;
        }

        public Map<String, StorageEntry> getReplicaRegions(String objKey, long nowSec) {
            Map<String, StorageEntry> result = new HashMap<>();
            Map<String, StorageEntry> m = state.get(objKey);
            if (m != null) {
                for (Map.Entry<String, StorageEntry> entry : m.entrySet()) {
                    if (entry.getValue().isReplica && !expired(entry.getValue(), nowSec))
                        result.put(entry.getKey(), entry.getValue());
                }
            }
            return result;
        }

        public void evictExpired(long nowSec) {
            for (Map<String, StorageEntry> m : state.values()) {
                m.entrySet().removeIf(e -> expired(e.getValue(), nowSec));
            }
            state.entrySet().removeIf(e -> e.getValue().isEmpty());
        }

        private StorageEntry getEntry(String objKey, String region) {
            Map<String, StorageEntry> m = state.get(objKey);
            return m != null ? m.get(region) : null;
        }

        private boolean expired(StorageEntry e, long nowSec) {
            return e.expireAtSeconds != -1L && nowSec >= e.expireAtSeconds;
        }
    }

    /** Gurobi 变量容器 */
    private static class EventVars {
        GRBVar d;          // 0=replica, 1=EC
        GRBVar[] sigma;    // σ[i]: region i stores shard
        GRBVar hatTau;     // τ̂: unified EC TTL (hours)
        GRBVar[] z;        // z[i] = σ[i]*τ̂ (linearization)
        GRBVar[] route;    // 读路由变量 (仅 READ 事件且存在前驱时创建)
    }

    // =====================================================================
    // 价格读取
    // =====================================================================

    private double regionStoragePrice(String region) {
        Object p = totalGraph.getNodeAttrs(region).get("priceStorage");
        if (p instanceof Number && ((Number) p).doubleValue() > 0) {
            return ((Number) p).doubleValue();
        }
        throw new IllegalStateException(
            "[MILP-EC] priceStorage unavailable for '" + region + "': " + p);
    }

    private double regionTransCost(String src, String dst) {
        Object c = totalGraph.getEdgeAttrs(src, dst).get("cost");
        if (!(c instanceof Number)) {
            throw new IllegalStateException(
                "[MILP-EC] transfer cost unavailable for '" + src + "'->'" + dst + "': " + c);
        }
        return ((Number) c).doubleValue();
    }

    // =====================================================================
    // 状态字段
    // =====================================================================

    private final MilpStorageState milpState = new MilpStorageState();
    private final List<MilpEvent> eventWindow = new ArrayList<>();
    private long windowStartHours = -1;
    private final long windowSizeHours;
    private final Map<String, Double> milpEcTtlByRegion = new HashMap<>();

    // =====================================================================
    // 构造器
    // =====================================================================

    public PolicyMilpEC(Config config, DiGraph totalGraph,
                        Map<String, LogicalObject> logicalObjects,
                        RegionManager regionManager) {
        super(config, totalGraph, logicalObjects, regionManager);
        long ws = config.windowSize != null ? config.windowSize.longValue() : 24L;
        this.windowSizeHours = ws > 0 ? ws : 24L;
    }

    // =====================================================================
    // Override: updatePastRequests (SimulatorV2 在 read 时调用)
    // =====================================================================

    @Override
    public void updatePastRequests(Request request, String placeRegion) {
        super.updatePastRequests(request, placeRegion);

        if ("read".equals(request.op)) {
            long nowSec = request.timestamp.getEpochSecond();
            boolean localRep = milpState.hasReplica(request.objKey, placeRegion, nowSec);
            boolean localShard = milpState.getShardRegions(request.objKey, nowSec)
                                          .containsKey(placeRegion);
            if (!localRep && !localShard) {
                eventWindow.add(new MilpEvent(EventType.READ_RECONSTRUCT,
                        request.issueRegion, request.objKey,
                        request.size, request.timestamp));
            }
        }

        checkAndSolveWindow(request.timestamp);
    }

    // =====================================================================
    // Override: place (SimulatorV2 在 read/write 时均调用)
    // =====================================================================

    @Override
    public List<String> place(Request req, Config config) {
        if ("write".equals(req.op)) {
            long reqH = req.timestamp.getEpochSecond() / 3600;
            if (windowStartHours == -1) windowStartHours = reqH;
            eventWindow.add(new MilpEvent(EventType.WRITE, req.issueRegion,
                    req.objKey, req.size, req.timestamp));
            checkAndSolveWindow(req.timestamp);
        }
        return Collections.singletonList(req.issueRegion);
    }

    // =====================================================================
    // Override: getTevict
    // =====================================================================

    @Override
    public double getTevict(String src, String dst, java.time.Instant timestampInstant) {
        // milpEcTtlByRegion 存储单位是小时，父类 getTevict 返回秒，统一转换
        Double ttlHours = milpEcTtlByRegion.get(src);
        if (ttlHours != null && ttlHours > 0) {
            return ttlHours * 3600.0;
        }
        return super.getTevict(src, dst, timestampInstant);
    }

    // =====================================================================
    // 窗口管理
    // =====================================================================

    /**
     * 强制求解当前积压的所有事件。
     * SimulatorV2 在模拟结束后可调用此方法，确保最后一个不完整窗口的事件也被处理。
     */
    public void flushPendingEvents(Instant now) {
        if (!eventWindow.isEmpty()) {
            solveWindowBatch(now);
            windowStartHours = now.getEpochSecond() / 3600;
        }
    }

    private void checkAndSolveWindow(Instant now) {
        long reqH = now.getEpochSecond() / 3600;
        if (windowStartHours == -1) windowStartHours = reqH;

        boolean windowFull = reqH - windowStartHours >= windowSizeHours;
        boolean tooManyEvents = eventWindow.size() >= MAX_WINDOW_EVENTS;

        if (windowFull || tooManyEvents) {
            solveWindowBatch(now);
            windowStartHours = reqH;
        }
    }

    // =====================================================================
    // 批量求解入口
    // =====================================================================

    private void solveWindowBatch(Instant now) {
        if (eventWindow.isEmpty()) return;

        long nowSec = now.getEpochSecond();
        milpState.evictExpired(nowSec);

        int n = regions.size();
        if (n < EC_N) {
            fallbackAllReplica(eventWindow);
            eventWindow.clear();
            return;
        }

        // 价格矩阵
        double[] sp = new double[n];
        double[][] tc = new double[n][n];
        for (int i = 0; i < n; i++) {
            sp[i] = regionStoragePrice(regions.get(i));
            for (int j = 0; j < n; j++) {
                if (i != j) tc[i][j] = regionTransCost(regions.get(i), regions.get(j));
            }
        }

        // 各 region 的最优 TTL τ*（小时）
        // 优先使用基类 calcEvictCost() 的直方图方法；无数据时退化为 teven/2
        double[] tauH = new double[n];
        for (int i = 0; i < n; i++) {
            double tevenSec = (avgNetworkCost / sp[i]) * 86400.0;
            double tevenHours = tevenSec / 3600.0;
            double histTtlHours = calcEvictCost(regions.get(i), tevenHours, avgNetworkCost);
            tauH[i] = (histTtlHours > 0) ? histTtlHours : (tevenHours / 2.0);
            tauH[i] = Math.max(tauH[i], 1.0);
        }

        // 按对象分组
        Map<String, List<MilpEvent>> byObj = new LinkedHashMap<>();
        for (MilpEvent ev : eventWindow) {
            byObj.computeIfAbsent(ev.objKey, k -> new ArrayList<>()).add(ev);
        }

        // 创建 Gurobi 环境（全窗口复用）
        GRBEnv env = null;
        try {
            env = new GRBEnv(true);
            env.set("OutputFlag", "0");
            env.start();

            for (Map.Entry<String, List<MilpEvent>> entry : byObj.entrySet()) {
                solveObjectMilp(env, entry.getKey(), entry.getValue(),
                        n, sp, tc, tauH, nowSec);
            }
        } catch (GRBException ex) {
            System.err.println("[MILP-EC] Gurobi env error: " + ex.getMessage());
            fallbackAllReplica(eventWindow);
        } finally {
            if (env != null) {
                try { env.dispose(); } catch (GRBException ignored) {}
            }
        }

        eventWindow.clear();
    }

    // =====================================================================
    // 单对象子 MILP
    // =====================================================================

    private void solveObjectMilp(GRBEnv env, String objKey, List<MilpEvent> events,
                                 int n, double[] sp, double[][] tc,
                                 double[] tauH, long nowSec) {

        events.sort(Comparator.comparingLong(e -> e.timestampSeconds));

        double maxTauH = 0;
        for (double t : tauH) maxTauH = Math.max(maxTauH, t);
        if (maxTauH <= 0) maxTauH = 24.0;
        final double BIG_M = maxTauH + 1.0;

        // carry-over：窗口开始前各 region 是否持有数据
        boolean[] coRep = new boolean[n];
        boolean[] coShard = new boolean[n];
        {
            Map<String, StorageEntry> reps = milpState.getReplicaRegions(objKey, nowSec);
            Map<String, StorageEntry> shds = milpState.getShardRegions(objKey, nowSec);
            for (int i = 0; i < n; i++) {
                coRep[i] = reps.containsKey(regions.get(i));
                coShard[i] = shds.containsKey(regions.get(i));
            }
        }

        GRBModel model = null;
        try {
            model = new GRBModel(env);
            model.set(GRB.DoubleParam.MIPGap, 0.05);
            model.set(GRB.DoubleParam.TimeLimit, Math.max(10.0, events.size() * 0.5));

            GRBLinExpr obj = new GRBLinExpr();
            List<EventVars> varsList = new ArrayList<>();
            int wi = 0, qi = 0;
            int lastDecIdx = -1; // 前驱事件索引 (-1 = 使用 carry-over)

            for (int e = 0; e < events.size(); e++) {
                MilpEvent ev = events.get(e);
                EventVars v = new EventVars();
                varsList.add(v);

                String tag = (ev.type == EventType.WRITE) ? "w" + (wi++) : "q" + (qi++);
                String px = tag + "_";
                int rIdx = regions.indexOf(ev.issueRegion);
                double S = ev.sizeGB;

                // ── 决策变量 ──

                v.d = model.addVar(0, 1, 0, GRB.BINARY, "d_" + tag);

                v.sigma = new GRBVar[n];
                for (int i = 0; i < n; i++)
                    v.sigma[i] = model.addVar(0, 1, 0, GRB.BINARY, "s_" + tag + "_" + i);

                v.hatTau = model.addVar(0, maxTauH, 0, GRB.CONTINUOUS, "ht_" + tag);

                v.z = new GRBVar[n];
                for (int i = 0; i < n; i++)
                    v.z[i] = model.addVar(0, maxTauH, 0, GRB.CONTINUOUS, "z_" + tag + "_" + i);

                // ── 约束 ──

                // [C3/C7] Σσ[i] = EC_N * d  (EC 时恰好 3 个 shard，replica 时 0 个)
                {
                    GRBLinExpr lhs = new GRBLinExpr();
                    for (int i = 0; i < n; i++) lhs.addTerm(1.0, v.sigma[i]);
                    GRBLinExpr rhs = new GRBLinExpr();
                    rhs.addTerm((double) EC_N, v.d);
                    model.addConstr(lhs, GRB.EQUAL, rhs, px + "sigSum");
                }

                // [C3] 发起方自留一个 shard：σ[rIdx] >= d
                if (rIdx >= 0) {
                    model.addConstr(v.sigma[rIdx], GRB.GREATER_EQUAL, v.d, px + "local");
                }

                // [C6] τ̂ >= τ*[i] * σ[i]（仅保留下界）
                // 当 d=1 时目标函数中 EC 存储成本 ∝ hatTau，优化器自然将 hatTau 压到
                // max_{sigma[i]=1} tauH[i]，无需额外上界约束。
                // 错误的 Big-M 上界（hatTau <= tauH[i] + BIG_M*(1-sigma[i])）在选中多个
                // 不同 tau 的 region 时会导致"下界 > 上界"，使 MILP 不可行。
                for (int i = 0; i < n; i++) {
                    double ti = tauH[i];
                    GRBLinExpr lb = new GRBLinExpr();
                    lb.addTerm(ti, v.sigma[i]);
                    model.addConstr(v.hatTau, GRB.GREATER_EQUAL, lb, px + "htLB" + i);
                }

                // [线性化] z[i] = σ[i] * τ̂（McCormick envelopes）
                for (int i = 0; i < n; i++) {
                    // z[i] <= BIG_M * σ[i]
                    GRBLinExpr zub1 = new GRBLinExpr();
                    zub1.addTerm(BIG_M, v.sigma[i]);
                    model.addConstr(v.z[i], GRB.LESS_EQUAL, zub1, px + "zU1_" + i);

                    // z[i] <= τ̂
                    model.addConstr(v.z[i], GRB.LESS_EQUAL, v.hatTau, px + "zU2_" + i);

                    // z[i] >= τ̂ - BIG_M*(1-σ[i])
                    GRBLinExpr zlb = new GRBLinExpr();
                    zlb.addTerm(1.0, v.hatTau);
                    zlb.addTerm(BIG_M, v.sigma[i]);
                    zlb.addConstant(-BIG_M);
                    model.addConstr(v.z[i], GRB.GREATER_EQUAL, zlb, px + "zL_" + i);
                }

                // ── 目标函数：存储成本 ──

                double tauR = (rIdx >= 0) ? tauH[rIdx] : 24.0;
                double spR = (rIdx >= 0) ? sp[rIdx] : regionStoragePrice(ev.issueRegion);

                // 副本存储：(1-d) * S * sp[r] * τ*[r] / 720
                double repStore = S * spR * (tauR / 720.0);
                obj.addConstant(repStore);
                obj.addTerm(-repStore, v.d);

                // EC 存储：Σ_i (S/K) * sp[i] / 720 * z[i]
                for (int i = 0; i < n; i++) {
                    double coeff = (S / EC_K) * sp[i] / 720.0;
                    obj.addTerm(coeff, v.z[i]);
                }

                // ── 目标函数：EC 分发传输成本 C_trans_write_EC / C_trans_post_EC ──
                // 设计中传输成本分三项：
                //   (1) Write 时 EC 分发：C_trans_write_EC
                //   (2) Read 拉取：C_trans_read（见下方 addCoupledReadCost / addCarryOverReadCost）
                //   (3) Read 重建后重新 EC 分发：C_trans_post_EC
                // 这里对 WRITE 和 READ_RECONSTRUCT 均需计入 EC 分发成本，
                // 两者是独立操作（先拉入 rIdx，再推出 shard），不构成重复计费。
                if (rIdx >= 0) {
                    for (int i = 0; i < n; i++) {
                        if (i != rIdx) {
                            obj.addTerm((S / EC_K) * tc[rIdx][i], v.sigma[i]);
                        }
                    }
                }

                // ── 目标函数：读取拉取成本（仅 READ_RECONSTRUCT）──

                int predIdx = lastDecIdx;
                if (ev.type == EventType.READ_RECONSTRUCT && rIdx >= 0) {
                    // 检查前驱是否因 TTL 过期而不可用：
                    // 若事件间时间差 > maxTauH（最大可能 TTL），前驱布局已失效
                    boolean predExpired = false;
                    if (predIdx >= 0) {
                        long gapSec = ev.timestampSeconds - events.get(predIdx).timestampSeconds;
                        double gapHours = gapSec / 3600.0;
                        if (gapHours > maxTauH) {
                            predExpired = true;
                        }
                    }

                    if (predIdx >= 0 && !predExpired) {
                        addCoupledReadCost(model, obj, v, varsList.get(predIdx),
                                events.get(predIdx), n, S, rIdx, tc, px);
                    } else {
                        addCarryOverReadCost(obj, S, rIdx, coRep, coShard, n, tc);
                    }
                }

                // 当前事件成为后续事件的前驱
                lastDecIdx = e;
            }

            model.setObjective(obj, GRB.MINIMIZE);
            model.optimize();

            int status = model.get(GRB.IntAttr.Status);
            if (status == GRB.Status.OPTIMAL || status == GRB.Status.SUBOPTIMAL) {
                extractSolution(objKey, events, varsList, n, tauH);
            } else {
                System.err.println("[MILP-EC] obj='" + objKey + "' status="
                        + status + " -> replica fallback");
                fallbackObjectReplica(objKey, events, tauH);
            }
        } catch (GRBException ex) {
            System.err.println("[MILP-EC] Gurobi error for '" + objKey + "': " + ex.getMessage());
            fallbackObjectReplica(objKey, events, tauH);
        } finally {
            if (model != null) {
                try { model.dispose(); } catch (Exception ignored) {}
            }
        }
    }

    // =====================================================================
    // 读取拉取成本：与前驱决策耦合
    //
    // 前驱选 replica (d_pred=0) → 拉完整对象: S * tc[predR → rIdx]
    // 前驱选 EC     (d_pred=1) → 用路由变量从 K=2 个 shard region 拉取
    // =====================================================================

    private void addCoupledReadCost(GRBModel model, GRBLinExpr obj,
                                    EventVars v, EventVars predV, MilpEvent predEv,
                                    int n, double S, int rIdx,
                                    double[][] tc, String px) throws GRBException {

        int predRIdx = regions.indexOf(predEv.issueRegion);

        // 路由变量：route[i]=1 表示从 region i 拉取一个 shard
        v.route = new GRBVar[n];
        for (int i = 0; i < n; i++) {
            v.route[i] = model.addVar(0, 1, 0, GRB.BINARY, px + "vr" + i);
        }

        // Σ route[i] = EC_K * d_pred（EC 时需 K=2 个 shard；replica 时 route 全 0）
        {
            GRBLinExpr lhs = new GRBLinExpr();
            for (int i = 0; i < n; i++) lhs.addTerm(1.0, v.route[i]);
            GRBLinExpr rhs = new GRBLinExpr();
            rhs.addTerm((double) EC_K, predV.d);
            model.addConstr(lhs, GRB.EQUAL, rhs, px + "rtSum");
        }

        // route[i] <= σ_pred[i]（只能从前驱的 shard 所在 region 拉取）
        for (int i = 0; i < n; i++) {
            model.addConstr(v.route[i], GRB.LESS_EQUAL, predV.sigma[i], px + "rtLE" + i);
        }

        // EC 拉取成本：Σ_{i≠rIdx} route[i] * (S/K) * tc[i→rIdx]
        // 本地 shard (i==rIdx) 的传输成本为 0，优化器会优先选用
        for (int i = 0; i < n; i++) {
            if (i != rIdx) {
                obj.addTerm((S / EC_K) * tc[i][rIdx], v.route[i]);
            }
        }

        // Replica 拉取成本：(1-d_pred) * S * tc[predR→rIdx]
        if (predRIdx >= 0 && predRIdx != rIdx) {
            double repFetch = S * tc[predRIdx][rIdx];
            obj.addConstant(repFetch);
            obj.addTerm(-repFetch, predV.d);
        }
    }

    // =====================================================================
    // 读取拉取成本：来自 carry-over 状态（常量）
    //
    // 按四种路径选最便宜：
    //   1. 本地有 replica → 0
    //   2. 本地有 shard + 远程 shard → 拉 1 片
    //   3. 仅远程 shard → 拉 K 片
    //   4. 仅远程 replica → 拉完整对象
    // =====================================================================

    private void addCarryOverReadCost(GRBLinExpr obj, double S, int rIdx,
                                      boolean[] coRep, boolean[] coShard,
                                      int n, double[][] tc) {
        if (coRep[rIdx]) return;

        int nShard = 0;
        for (int i = 0; i < n; i++) {
            if (coShard[i]) nShard++;
        }

        double fetchCost = Double.MAX_VALUE;

        // 路径 2：本地有 shard，再拉 1 个最便宜的远程 shard
        if (coShard[rIdx] && nShard >= EC_K) {
            double minTC = Double.MAX_VALUE;
            for (int j = 0; j < n; j++) {
                if (j != rIdx && coShard[j]) minTC = Math.min(minTC, tc[j][rIdx]);
            }
            if (minTC < Double.MAX_VALUE) {
                fetchCost = Math.min(fetchCost, (S / EC_K) * minTC);
            }
        }

        // 路径 3：无本地数据，拉 K 个最便宜的远程 shard
        if (!coShard[rIdx] && nShard >= EC_K) {
            List<Double> costs = new ArrayList<>();
            for (int j = 0; j < n; j++) {
                if (coShard[j]) costs.add(tc[j][rIdx]);
            }
            Collections.sort(costs);
            double total = 0;
            for (int c = 0; c < Math.min(EC_K, costs.size()); c++) {
                total += costs.get(c);
            }
            fetchCost = Math.min(fetchCost, (S / EC_K) * total);
        }

        // 路径 4：从最便宜的远程 replica 拉完整对象
        for (int j = 0; j < n; j++) {
            if (j != rIdx && coRep[j]) {
                fetchCost = Math.min(fetchCost, S * tc[j][rIdx]);
            }
        }

        if (fetchCost < Double.MAX_VALUE) {
            obj.addConstant(fetchCost);
        } else {
            // [C4 soft] 无任何可用数据——加大罚项，促使 MILP 在前序事件中保留足够持久的布局
            double maxTC = 0;
            for (int j = 0; j < n; j++) {
                if (j != rIdx) maxTC = Math.max(maxTC, tc[j][rIdx]);
            }
            obj.addConstant(S * maxTC * 2.0);
        }
    }

    // =====================================================================
    // 提取 MILP 解并更新内部状态
    // =====================================================================

    private void extractSolution(String objKey, List<MilpEvent> events,
                                 List<EventVars> varsList, int n, double[] tauH)
            throws GRBException {

        for (int e = 0; e < events.size(); e++) {
            MilpEvent ev = events.get(e);
            EventVars v = varsList.get(e);

            ev.decision = (int) Math.round(v.d.get(GRB.DoubleAttr.X));
            int rIdx = regions.indexOf(ev.issueRegion);
            double defaultTauH = (rIdx >= 0) ? tauH[rIdx] : 24.0;

            if (ev.decision == 0) {
                double tSec = defaultTauH * 3600.0;
                milpState.placeReplica(objKey, ev.issueRegion,
                        ev.timestampSeconds, tSec, ev.sizeGB);
            } else {
                ev.sigma = new boolean[n];
                List<String> shards = new ArrayList<>();
                for (int i = 0; i < n; i++) {
                    if (Math.round(v.sigma[i].get(GRB.DoubleAttr.X)) == 1) {
                        ev.sigma[i] = true;
                        shards.add(regions.get(i));
                    }
                }
                double ht = v.hatTau.get(GRB.DoubleAttr.X);
                if (ht <= 0) ht = defaultTauH;
                ev.ecUnifiedTtlHours = ht;

                // 缓存 EC 统一 TTL，供 getTevict() 返回给 SimulatorV2
                milpEcTtlByRegion.put(ev.issueRegion, ht);
                milpState.placeECShards(objKey, shards,
                        ev.timestampSeconds, ht * 3600.0, ev.sizeGB);
            }
        }
    }

    // =====================================================================
    // Fallback
    // =====================================================================

    private void fallbackObjectReplica(String objKey, List<MilpEvent> events, double[] tauH) {
        for (MilpEvent ev : events) {
            int rIdx = regions.indexOf(ev.issueRegion);
            double tH = (rIdx >= 0) ? tauH[rIdx] : 24.0;
            milpState.placeReplica(objKey, ev.issueRegion,
                    ev.timestampSeconds, tH * 3600.0, ev.sizeGB);
        }
    }

    private void fallbackAllReplica(List<MilpEvent> events) {
        for (MilpEvent ev : events) {
            double tH = computeTauHours(ev.issueRegion);
            milpState.placeReplica(ev.objKey, ev.issueRegion,
                    ev.timestampSeconds, tH * 3600.0, ev.sizeGB);
        }
    }

    private double computeTauHours(String region) {
        double s = regionStoragePrice(region);
        double tevenSec = (avgNetworkCost / s) * 86400.0;
        double tH = tevenSec / 3600.0 / 2.0;
        return Math.max(tH, 1.0);
    }
}
