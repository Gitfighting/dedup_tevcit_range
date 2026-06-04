package org.example;

import org.example.src.model.Request;

import java.math.BigDecimal;
import java.util.Map;

/**
 * {@link SimulatorV2} 的扩展：在仿真主循环中接入「按 region 的写入体积」与「读路径分段体积」，供 **base + 2 EC shard**（{@link org.example.src.placement_policy.milpv3.GurobiEc21MilpBuilder#NUM_EC_SHARD_SLOTS}）等策略使用。
 * <ul>
 *   <li>写：{@link #getPlacementSizesForWrite} → {@link org.example.src.placement_policy.PlacementPolicy#getPlacementWithSizes}；
 *       {@code null} 时 V2 回退为整对象 {@link Request#size}。</li>
 *   <li>读：{@link #getReadTransferSize} → {@link org.example.src.placement_policy.PlacementPolicy#getReadTransferSize}；
 *       若传输路径带有 {@code segmentSizes} 则优先用之；否则 {@code null} 回退为 {@link Request#size}（见 V2
 *       {@code _updateTransferMetric}）。</li>
 *   <li>{@code milp_ec_new_v3}：写路径 {@link org.example.SimulatorV2#calculateTtl} 上，配对 TTL 直方图每 12h 滚动时
 *       {@link org.example.src.placement_policy.PolicyMilpECnewV3#syncMilpIfHistogramTtlAdvanced} 刷新 TTL 并求解 MILP；
 *       读路径仅更新直方图/可能刷新 TTL（{@link #getTevict}），不触发 MILP。</li>
 * </ul>
 */
public class SimulatorV3 extends SimulatorV2 {

    public SimulatorV3(String configPath,
            String tracePath,
            int numVms,
            boolean setBaseRegion,
            int days,
            boolean versionEnable,
            boolean storeDecision) {
        super(configPath, tracePath, numVms, setBaseRegion, days, versionEnable, storeDecision);
    }

    public SimulatorV3(String configPath,
            String tracePath,
            int numVms,
            boolean setBaseRegion,
            int days,
            boolean versionEnable) {
        super(configPath, tracePath, numVms, setBaseRegion, days, versionEnable);
    }

    @Override
    protected Map<String, BigDecimal> getPlacementSizesForWrite(Request req) {
        return placementPolicy.getPlacementWithSizes(req, config);
    }

    @Override
    protected BigDecimal getReadTransferSize(Request req) {
        return placementPolicy.getReadTransferSize(req, config);
    }
}
