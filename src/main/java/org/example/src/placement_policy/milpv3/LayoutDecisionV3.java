package org.example.src.placement_policy.milpv3;

import java.time.Instant;

/**
 * Immutable MILP layout decision snapshot for one aggregate key.
 */
public class LayoutDecisionV3 {//定义属性和构造函数，用于记录MILP决策的布局结果
    public final int mode; // 0=rep, 1=EC
    public final boolean z;
    public final boolean[] x;
    public final boolean[][] y;
    public final int baseRegionIndex;
    public final String issueRegion;
    public final Instant decidedAt;
    public final double objVal;
    public final double[] ttlSecSnapshot;
    public final String solverStatus;

    public LayoutDecisionV3(
            int mode,
            boolean z,
            boolean[] x,
            boolean[][] y,
            int baseRegionIndex,
            String issueRegion,
            Instant decidedAt,
            double objVal,
            double[] ttlSecSnapshot,
            String solverStatus) {
        this.mode = mode;
        this.z = z;
        this.x = x;
        this.y = y;
        this.baseRegionIndex = baseRegionIndex;
        this.issueRegion = issueRegion;
        this.decidedAt = decidedAt;
        this.objVal = objVal;
        this.ttlSecSnapshot = ttlSecSnapshot;
        this.solverStatus = solverStatus;
    }
}

