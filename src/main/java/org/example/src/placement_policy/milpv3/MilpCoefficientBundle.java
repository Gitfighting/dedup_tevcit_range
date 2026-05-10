package org.example.src.placement_policy.milpv3;

/**
 * Input coefficient bundle for one MILP solve.
 */
public class MilpCoefficientBundle {
    public final double[] sRep;
    public final double[][] sEc;
    /** Legacy scalar read terms (kept 0 when using histogram read block below). */
    public final double tRep;
    public final double tEc;
    public final double[] ttlSec;

    /**
     * 直方图加权读项，目标中为：
     * readBaseConst + Σ_r readRepDelta[r]·x[r] + z·readEcZCoeff，
     * readEcZCoeff = readEcFullConst − readBaseConst。
     */
    public final double readBaseConst;
    public final double[] readRepDelta;
    public final double readEcZCoeff;
    public final double readEcFullConst;

    /** Filled by PolicyMilpECnewV3: predicted read/write counts for the planning horizon. */
    public double nrPred = 1.0;
    public double nwPred = 1.0;

    public MilpCoefficientBundle(double[] sRep, double[][] sEc, double tRep, double tEc, double[] ttlSec,
            double readBaseConst, double[] readRepDelta, double readEcZCoeff, double readEcFullConst) {
        this.sRep = sRep;
        this.sEc = sEc;
        this.tRep = tRep;
        this.tEc = tEc;
        this.ttlSec = ttlSec;
        this.readBaseConst = readBaseConst;
        this.readRepDelta = readRepDelta;
        this.readEcZCoeff = readEcZCoeff;
        this.readEcFullConst = readEcFullConst;
    }

    /** Legacy bundle without histogram read decomposition (read Rep/EC deltas zeroed). */
    public MilpCoefficientBundle(double[] sRep, double[][] sEc, double tRep, double tEc, double[] ttlSec) {
        this(sRep, sEc, tRep, tEc, ttlSec, 0.0, new double[sRep.length], 0.0, 0.0);
    }
}
