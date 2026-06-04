package org.example.src.placement_policy.milpv3;

/**
 * Coefficients for one MILP solve: storage + placement transfer (edge {@code cost} × GB) only.
 */
public class MilpCoefficientBundle {
    /** Replica branch linear cost per region {@code r} (coefficient of {@code x[r]}). */
    public final double[] sRep;
    /** EC branch linear cost per region {@code r} and stripe slot {@code k} (coefficient of {@code y[r][k]}). */
    public final double[][] sEc;
    /** TTL snapshot (seconds per region index) used when building {@code sRep}/{@code sEc}; not a decision variable. */
    public final double[] ttlSec;

    /**
     * When true, {@link org.example.src.placement_policy.milpv3.GurobiEc21MilpBuilder} fixes {@code x[base]=0} and adds
     * {@link #fbBaseReplicaObjectiveConstant} to the objective (FB whole-object replica at base is mandatory and priced outside {@code x}).
     */
    public final boolean fixedBaseFb;
    /** Always-on FB term: base full-object storage (dataset horizon) + one-time placement edge from issue region to base. */
    public final double fbBaseReplicaObjectiveConstant;

    public MilpCoefficientBundle(double[] sRep, double[][] sEc, double[] ttlSec,
            boolean fixedBaseFb, double fbBaseReplicaObjectiveConstant) {
        this.sRep = sRep;
        this.sEc = sEc;
        this.ttlSec = ttlSec;
        this.fixedBaseFb = fixedBaseFb;
        this.fbBaseReplicaObjectiveConstant = fbBaseReplicaObjectiveConstant;
    }
}
