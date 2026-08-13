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
 *       {@code _updateTransferMetric}）。读 miss 落盘走 {@link #getPlacementSizesForRead}，必须为 logical 全对象大小。</li>
 *   <li>{@code milp_ec_new_v3/v4}：配对 TTL 直方图刷新与 {@link org.example.src.placement_policy.PolicyTevictRangesNew} 一致（{@link org.example.src.placement_policy.PolicyMilpECnewV3#getTevict}）；MILP 在刷新后由 pending 队列触发，读路径不单独滚桶。</li>
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
