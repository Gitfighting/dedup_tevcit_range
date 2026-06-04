package org.example.src.placement_policy.milpv3;

import org.example.graph.DiGraph;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;

/**
 * 闭式 EC 判定（Closed-form EC-Threshold rule）。
 *
 * <p>实现 docs/EC_Threshold_Theory.md 定理 1：在 FB + Tevict 设定下，
 * 当且仅当
 * <pre>
 *   λ &lt; λ* := ( W · Σ_{r∈A} p_r · 3 + Σ_{r∈A∖{b}} c_{b,r} ) / c̄
 * </pre>
 * EC(K, R-K) 比 R 副本的期望总成本严格更低（其中 p_r 为 priceStorage，
 * 与 StorageCostHelper 一致地乘 3 折算到"3 副本计费视角"；c̄ 是全图平均跨区运费）。
 *
 * <p>本类是<b>纯函数 + 无 Gurobi 依赖</b>，与 {@link GurobiEc21MilpBuilder}
 * 平行：调用方可在 {@code PolicyMilpECnewV3} 里通过 yaml 开关二选一。
 *
 * <p><b>与 V3 MILP 的对齐：</b>
 * <ul>
 *   <li>V3 EC 分支对 {@link GurobiEc21MilpBuilder#NUM_EC_SHARD_SLOTS} 条 stripe 决策：**base 整副本与 MILP 分叉**，两片数据 shard 落在 **两个非 base 区**（本规则按 seatCost 选最便宜的一对）。</li>
 *   <li>{@code R} 仅参与 λ* 公式：默认 R=3；将 R 调大（例如 R=9）相当于"激进 EC 上界"。</li>
 *   <li>p_r 用 priceStorage × 3（与 StorageCostHelper.pricePerGbForDuration 完全一致）。</li>
 * </ul>
 */
public final class EcThresholdRule {

    /** 默认 |A| = 3（参与 λ* 的候选集合大小）；EC 落地仍为 {@link GurobiEc21MilpBuilder#NUM_EC_SHARD_SLOTS} 个 shard 区。 */
    public static final int DEFAULT_R = 3;
    /** 默认对象大小（GB），仅作 λ* 量级日志使用，不影响判定（因 λ* 与 S 无关）。 */
    public static final double DEFAULT_SIZE_GB = 1.0;

    private EcThresholdRule() {
    }

    public static final class Inputs {
        public final List<String> regions;
        public final DiGraph graph;
        public final int baseRegionIndex;
        public final String issueRegion;
        /** 规划窗 W（秒）。建议与 V3 的 horizonSec 对齐。 */
        public final double windowSec;
        /** EC 数据片数，V3 中固定 K=2。 */
        public final int K;
        /** |A|：参与 λ* 求和的 region 数（默认 3）。 */
        public final int R;
        /** 全图平均跨区运费 c̄（avgNetworkCost）。 */
        public final double avgNetworkCost;
        /** 由调用方提供的窗内读次数预测 λ_pred。 */
        public final double lambdaPred;

        public Inputs(List<String> regions, DiGraph graph, int baseRegionIndex,
                String issueRegion, double windowSec, int K, int R,
                double avgNetworkCost, double lambdaPred) {
            this.regions = regions;
            this.graph = graph;
            this.baseRegionIndex = baseRegionIndex;
            this.issueRegion = issueRegion;
            this.windowSec = windowSec;
            this.K = K;
            this.R = R;
            this.avgNetworkCost = avgNetworkCost;
            this.lambdaPred = lambdaPred;
        }
    }

    /** 单个 region 在 A 中的"占座成本"= W·p_r·3 + c_{b,r}（仅用于排序选 A）。 */
    private static double seatCost(Inputs in, int regionIdx, int baseIdx) {
        String r = in.regions.get(regionIdx);
        double pPerGbMonth = readPriceStorageMonthlyTimes3(in.graph, r);
        double pPerGbSec = pPerGbMonth / SEC_PER_MONTH;
        String base = in.regions.get(baseIdx);
        double cbr = (regionIdx == baseIdx) ? 0.0 : edgeCost(in.graph, base, r);
        return in.windowSec * pPerGbSec + cbr;
    }

    /**
     * 选 A：base 必入；issueRegion 若不同于 base 则次入；剩余按 seatCost 升序选满 R 个。
     *
     * <p>返回 region 索引列表，长度恰为 R。
     */
    static List<Integer> selectA(Inputs in) {
        int n = in.regions.size();
        int baseIdx = in.baseRegionIndex;
        if (baseIdx < 0 || baseIdx >= n) {
            throw new IllegalStateException(
                    "[EcThresholdRule] invalid baseRegionIndex=" + baseIdx + " in regions.size=" + n);
        }
        int target = Math.max(1, Math.min(in.R, n));

        LinkedHashSet<Integer> a = new LinkedHashSet<>();
        a.add(baseIdx);

        int issueIdx = (in.issueRegion == null) ? -1 : in.regions.indexOf(in.issueRegion);
        if (issueIdx >= 0 && issueIdx != baseIdx && a.size() < target) {
            a.add(issueIdx);
        }

        if (a.size() < target) {
            List<Integer> candidates = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                if (!a.contains(i)) {
                    candidates.add(i);
                }
            }
            candidates.sort(Comparator.comparingDouble(i -> seatCost(in, i, baseIdx)));
            for (Integer c : candidates) {
                if (a.size() >= target) {
                    break;
                }
                a.add(c);
            }
        }
        return new ArrayList<>(a);
    }

    /**
     * 给定 A，计算 λ*（定理 1）。
     */
    public static double computeLambdaStar(Inputs in, List<Integer> a) {
        if (in.avgNetworkCost <= 0.0) {
            // 没有跨区运费就退化为：永远不该 EC（任何读都会让 EC 变贵但贵度=0），
            // 这里返回 0 让规则在调用方落到 replica 分支。
            return 0.0;
        }
        int baseIdx = in.baseRegionIndex;
        String base = in.regions.get(baseIdx);

        double sumP = 0.0;
        double sumCb = 0.0;
        for (Integer i : a) {
            String r = in.regions.get(i);
            double pPerGbMonth = readPriceStorageMonthlyTimes3(in.graph, r);
            sumP += pPerGbMonth / SEC_PER_MONTH;
            if (i != baseIdx) {
                sumCb += edgeCost(in.graph, base, r);
            }
        }
        return (in.windowSec * sumP + sumCb) / in.avgNetworkCost;
    }

    public static final class DecisionResult {
        public final LayoutDecisionV3 decision;
        public final double lambdaStar;
        public final double lambdaPred;
        public final List<Integer> aIndices;

        DecisionResult(LayoutDecisionV3 decision, double lambdaStar,
                double lambdaPred, List<Integer> aIndices) {
            this.decision = decision;
            this.lambdaStar = lambdaStar;
            this.lambdaPred = lambdaPred;
            this.aIndices = aIndices;
        }
    }

    /**
     * 主入口：按定理 1 给出 EC/Replica 决策，构造 {@link LayoutDecisionV3}（与 MILP 求解器
     * 输出兼容，可直接塞进 {@code PolicyMilpECnewV3.decisionCache}）。
     *
     * <p>EC 模式 layout：**两片 shard** 落在 **两个不同的非 base region**（按 seatCost 升序取前二），与
     * {@link GurobiEc21MilpBuilder#NUM_EC_SHARD_SLOTS} 一致。
     */
    public static DecisionResult decide(Inputs in, Instant decidedAt) {
        List<Integer> a = selectA(in);
        double lamStar = computeLambdaStar(in, a);
        boolean useEc = in.lambdaPred < lamStar;

        int n = in.regions.size();
        boolean[] x = new boolean[n];
        boolean[][] y = new boolean[n][GurobiEc21MilpBuilder.NUM_EC_SHARD_SLOTS];

        if (!useEc) {
            // Replica 模式：与 buildDefaultReplicaDecision 一致——x/y 全 0，
            // PolicyMilpECnewV3.place() 的 mode==0 分支直接返回 issueRegion 落地。
            LayoutDecisionV3 dec = new LayoutDecisionV3(
                    0, false, x, y,
                    in.baseRegionIndex, in.issueRegion, decidedAt,
                    /* objVal */ 0.0, /* ttlSecSnapshot */ new double[n],
                    "THRESHOLD_REPLICA");
            return new DecisionResult(dec, lamStar, in.lambdaPred, a);
        }

        int baseIdx = in.baseRegionIndex;
        List<Integer> shardCandidates = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            if (i != baseIdx) {
                shardCandidates.add(i);
            }
        }
        shardCandidates.sort(Comparator.comparingDouble(i -> seatCost(in, i, baseIdx)));
        if (shardCandidates.size() < GurobiEc21MilpBuilder.NUM_EC_SHARD_SLOTS) {
            throw new IllegalStateException(
                    "[EcThresholdRule] need at least " + GurobiEc21MilpBuilder.NUM_EC_SHARD_SLOTS
                            + " non-base regions for EC shards (n=" + n + ")");
        }
        y[shardCandidates.get(0)][0] = true;
        y[shardCandidates.get(1)][1] = true;

        LayoutDecisionV3 dec = new LayoutDecisionV3(
                1, true, x, y, baseIdx, in.issueRegion, decidedAt,
                0.0, new double[n], "THRESHOLD_EC");
        return new DecisionResult(dec, lamStar, in.lambdaPred, a);
    }

    // ------------------------------------------------------------
    // helpers
    // ------------------------------------------------------------

    private static final double SEC_PER_MONTH = 30.0 * 24.0 * 3600.0;

    /** 与 StorageCostHelper.pricePerGbForDuration 完全一致：priceStorage × 3。 */
    private static double readPriceStorageMonthlyTimes3(DiGraph g, String region) {
        Object v = g.getNodeAttrs(region).get("priceStorage");
        if (!(v instanceof Number)) {
            throw new IllegalStateException(
                    "[EcThresholdRule] priceStorage missing for region '" + region + "'");
        }
        return ((Number) v).doubleValue() * 3.0;
    }

    private static double edgeCost(DiGraph g, String src, String dst) {
        Object v = g.getEdgeAttr(src, dst, "cost");
        if (!(v instanceof Number)) {
            throw new IllegalStateException(
                    "[EcThresholdRule] missing edge cost from " + src + " to " + dst);
        }
        return ((Number) v).doubleValue();
    }
}
