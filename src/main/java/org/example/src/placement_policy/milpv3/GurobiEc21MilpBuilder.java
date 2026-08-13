package org.example.src.placement_policy.milpv3;

import com.gurobi.gurobi.GRB;
import com.gurobi.gurobi.GRBEnv;
import com.gurobi.gurobi.GRBException;
import com.gurobi.gurobi.GRBModel;
import com.gurobi.gurobi.GRBLinExpr;
import com.gurobi.gurobi.GRBVar;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Pure MILP builder/solver: FB base replica (handled outside {@code x} when applicable) vs replica layout vs EC with
 * exactly two stripe placements ({@value #NUM_EC_SHARD_SLOTS}), matching **base + 2 shards** (no third parity stripe variable).
 */
public final class GurobiEc21MilpBuilder {
    private static final AtomicBoolean DEBUG_LP_WRITTEN = new AtomicBoolean(false);
    /** EC branch places this many shards via {@code y[r][k]} ({@value #NUM_EC_SHARD_SLOTS} data shards; FB base replica modeled separately). */
    public static final int NUM_EC_SHARD_SLOTS = 2;

    private GurobiEc21MilpBuilder() {
    }

    public static LayoutDecisionV3 solve(//层的决定V3解决方案
            GRBEnv env,//ggorubi的环境
            MilpCoefficientBundle coeff,//系数
            int baseIndex,//基础索引
            List<String> regions,//区域
            String issueRegion,//触发区域
            Instant now,//时间
            double timeLimitSec,//时间限制
            double mipGap,//允许的MIP间隙
            boolean strictFail) throws GRBException {
        return solve(env, coeff, baseIndex, regions, issueRegion, now, timeLimitSec, mipGap, strictFail, null, true,
                false);
    }

    /**
     * @param debugLpPath if non-blank, the first successful model build in this JVM is written (LP format) before
     *                    optimize — useful to inspect why z (EC) vs replica wins.
     */
    public static LayoutDecisionV3 solve(
            GRBEnv env,
            MilpCoefficientBundle coeff,
            int baseIndex,
            List<String> regions,
            String issueRegion,
            Instant now,
            double timeLimitSec,
            double mipGap,
            boolean strictFail,
            String debugLpPath) throws GRBException {
        return solve(env, coeff, baseIndex, regions, issueRegion, now, timeLimitSec, mipGap, strictFail, debugLpPath,
                true, false);
    }

    /**
     * @param allowEc when {@code false}, adds {@code z=0} so the solver picks among replica layouts only (same
     *                objective coefficients as the full model).
     */
    public static LayoutDecisionV3 solve(
            GRBEnv env,
            MilpCoefficientBundle coeff,
            int baseIndex,
            List<String> regions,
            String issueRegion,
            Instant now,
            double timeLimitSec,
            double mipGap,
            boolean strictFail,
            String debugLpPath,
            boolean allowEc) throws GRBException {
        return solve(env, coeff, baseIndex, regions, issueRegion, now, timeLimitSec, mipGap, strictFail, debugLpPath,
                allowEc, false);
    }

    /**
     * @param forceEcOnly when {@code true}, adds {@code z=1} so the solver only optimizes EC stripe placement (no
     *                    replica branch). Mutually exclusive with {@code allowEc == false}.
     */
    public static LayoutDecisionV3 solve(
            GRBEnv env,
            MilpCoefficientBundle coeff,
            int baseIndex,
            List<String> regions,
            String issueRegion,
            Instant now,
            double timeLimitSec,
            double mipGap,
            boolean strictFail,
            String debugLpPath,
            boolean allowEc,
            boolean forceEcOnly) throws GRBException {
        int n = regions.size();
        GRBModel model = new GRBModel(env);
        try {
            if (timeLimitSec > 0) {
                model.set(GRB.DoubleParam.TimeLimit, timeLimitSec);
            }
            if (mipGap > 0) {
                model.set(GRB.DoubleParam.MIPGap, mipGap);
            }

            GRBVar z = model.addVar(0, 1, 0, GRB.BINARY, "z");
            GRBVar[] x = new GRBVar[n];
            GRBVar[][] y = new GRBVar[n][NUM_EC_SHARD_SLOTS];
            for (int r = 0; r < n; r++) {
                x[r] = model.addVar(0, 1, 0, GRB.BINARY, "x_" + r);
                for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                    y[r][k] = model.addVar(0, 1, 0, GRB.BINARY, "y_" + r + "_" + k);
                }
            }

            boolean fbFixedBase = coeff.fixedBaseFb && baseIndex >= 0 && baseIndex < n;
            if (fbFixedBase) {
                model.addConstr(x[baseIndex], GRB.EQUAL, 0.0, "fb_fixed_base_x0");
            }

            // Replica and EC are mutually exclusive per region. FB skips x+z at base (mandatory replica is off-variable).
            for (int r = 0; r < n; r++) {
                if (!fbFixedBase || r != baseIndex) {
                    GRBLinExpr c1 = new GRBLinExpr();
                    c1.addTerm(1.0, x[r]);
                    c1.addTerm(1.0, z);
                    model.addConstr(c1, GRB.LESS_EQUAL, 1.0, "x_le_1mz_" + r);
                }
                for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                    GRBLinExpr c2 = new GRBLinExpr();
                    c2.addTerm(1.0, y[r][k]);
                    c2.addTerm(-1.0, z);
                    model.addConstr(c2, GRB.LESS_EQUAL, 0.0, "y_le_z_" + r + "_" + k);
                }
            }

            // Coverage: FP (tevict-aligned) picks one replica region OR EC; FB keeps mandatory base replica in objective constant.
            if (!fbFixedBase) {
                GRBLinExpr sx = new GRBLinExpr();
                for (int r = 0; r < n; r++) {
                    sx.addTerm(1.0, x[r]);
                }
                sx.addTerm(1.0, z);
                model.addConstr(sx, GRB.GREATER_EQUAL, 1.0, "sum_x_ge_1mz");
                GRBLinExpr repOnly = new GRBLinExpr();
                for (int r = 0; r < n; r++) {
                    repOnly.addTerm(1.0, x[r]);
                }
                model.addConstr(repOnly, GRB.LESS_EQUAL, 1.0, "fp_at_most_one_replica");
            } else if (baseIndex >= 0 && baseIndex < n) {
                GRBLinExpr extraRep = new GRBLinExpr();
                for (int r = 0; r < n; r++) {
                    if (r != baseIndex) {
                        extraRep.addTerm(1.0, x[r]);
                    }
                }
                model.addConstr(extraRep, GRB.LESS_EQUAL, 1.0, "at_most_one_extra_full_replica");
            }

            // sum_r y_r,k = z for each k; per-region sum_k y_r,k <= z and <= 1 (at most one stripe per region).
            // No coupling between x and y: any region may host a shard regardless of full replica there (still bounded by x[r]+z below).
            for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                GRBLinExpr sy = new GRBLinExpr();
                for (int r = 0; r < n; r++) {
                    sy.addTerm(1.0, y[r][k]);
                }
                sy.addTerm(-1.0, z);
                model.addConstr(sy, GRB.EQUAL, 0.0, "sum_y_eq_z_k" + k);
            }
            for (int r = 0; r < n; r++) {
                GRBLinExpr row = new GRBLinExpr();
                for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                    row.addTerm(1.0, y[r][k]);
                }
                GRBLinExpr rowLeZ = new GRBLinExpr();
                rowLeZ.multAdd(1.0, row);
                rowLeZ.addTerm(-1.0, z);
                model.addConstr(rowLeZ, GRB.LESS_EQUAL, 0.0, "sum_y_le_z_r" + r);
                model.addConstr(row, GRB.LESS_EQUAL, 1.0, "sum_y_le_1_r" + r);
            }

            int issueIdx = issueRegion != null ? regions.indexOf(issueRegion) : -1;
            if (fbFixedBase && issueIdx >= 0 && issueIdx < n) {
                GRBLinExpr localShards = new GRBLinExpr();
                for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                    localShards.addTerm(1.0, y[issueIdx][k]);
                }
                model.addConstr(localShards, GRB.GREATER_EQUAL, z, "ec_local_shard_at_issue");
            }

            // minimize constant(FB base replica if any) + Σ_r sRep[r]*x[r] + Σ_{r,k} sEc[r][k]*y[r][k]
            // + read-miss local full-replica cache (w = 1 when z=1, or z=0 without x at issue)
            GRBLinExpr obj = new GRBLinExpr();
            obj.addConstant(coeff.fbBaseReplicaObjectiveConstant);
            for (int r = 0; r < n; r++) {
                obj.addTerm(coeff.sRep[r], x[r]);
                for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                    obj.addTerm(coeff.sEc[r][k], y[r][k]);
                }
            }
            if (issueIdx >= 0 && issueIdx < n && coeff.localReadCacheCost > 0.0) {
                GRBVar readMissLocal = model.addVar(0, 1, 0, GRB.BINARY, "read_miss_local_cache");
                GRBLinExpr wGeZ = new GRBLinExpr();
                wGeZ.addTerm(1.0, readMissLocal);
                wGeZ.addTerm(-1.0, z);
                model.addConstr(wGeZ, GRB.GREATER_EQUAL, 0.0, "read_miss_local_ge_z");
                GRBLinExpr wGeOneMinusX = new GRBLinExpr();
                wGeOneMinusX.addTerm(1.0, readMissLocal);
                wGeOneMinusX.addTerm(1.0, x[issueIdx]);
                model.addConstr(wGeOneMinusX, GRB.GREATER_EQUAL, 1.0, "read_miss_local_ge_1mx_issue");
                obj.addTerm(coeff.localReadCacheCost, readMissLocal);
            }
            model.setObjective(obj, GRB.MINIMIZE);

            if (forceEcOnly && !allowEc) {
                throw new GRBException("Invalid MILP flags: forceEcOnly=true requires allowEc=true");
            }
            if (forceEcOnly) {
                model.addConstr(z, GRB.EQUAL, 1.0, "experiment_force_ec_z1");
            } else if (!allowEc) {
                model.addConstr(z, GRB.EQUAL, 0.0, "ablation_force_replica_z0");
            }

            if (debugLpPath != null) {
                String debugLpTrimmed = debugLpPath.trim();
                if (!debugLpTrimmed.isEmpty() && DEBUG_LP_WRITTEN.compareAndSet(false, true)) {
                try {
                    model.write(debugLpTrimmed);
                } catch (GRBException ex) {
                    DEBUG_LP_WRITTEN.set(false);
                    throw ex;
                }
                }
            }

            model.optimize();
            int status = model.get(GRB.IntAttr.Status);
            String statusName = Integer.toString(status);
            boolean ok = status == GRB.OPTIMAL || status == GRB.SUBOPTIMAL;
            if (!ok && strictFail) {
                throw new GRBException("MILP solve failed with status=" + status);
            }

            boolean zVal = ok && z.get(GRB.DoubleAttr.X) >= 0.5;
            boolean[] xVal = new boolean[n];
            boolean[][] yVal = new boolean[n][NUM_EC_SHARD_SLOTS];
            if (ok) {
                for (int r = 0; r < n; r++) {
                    xVal[r] = x[r].get(GRB.DoubleAttr.X) >= 0.5;
                    for (int k = 0; k < NUM_EC_SHARD_SLOTS; k++) {
                        yVal[r][k] = y[r][k].get(GRB.DoubleAttr.X) >= 0.5;
                    }
                }
            }
            double objVal = ok ? model.get(GRB.DoubleAttr.ObjVal) : Double.NaN;
            return new LayoutDecisionV3(
                    zVal ? 1 : 0,
                    zVal,
                    xVal,
                    yVal,
                    baseIndex,
                    issueRegion,
                    now,
                    objVal,
                    coeff.ttlSec,
                    statusName);
        } finally {
            model.dispose();
        }
    }
}
