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
 * Pure MILP builder/solver for EC(2+1) vs replicate.
 */
public final class GurobiEc21MilpBuilder {
    private static final AtomicBoolean DEBUG_LP_WRITTEN = new AtomicBoolean(false);

    private GurobiEc21MilpBuilder() {
    }

    public static LayoutDecisionV3 solve(
            GRBEnv env,
            MilpCoefficientBundle coeff,
            int baseIndex,
            List<String> regions,
            String issueRegion,
            Instant now,
            double timeLimitSec,
            double mipGap,
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
            GRBVar[][] y = new GRBVar[n][3];
            for (int r = 0; r < n; r++) {
                x[r] = model.addVar(0, 1, 0, GRB.BINARY, "x_" + r);
                for (int k = 0; k < 3; k++) {
                    y[r][k] = model.addVar(0, 1, 0, GRB.BINARY, "y_" + r + "_" + k);
                }
            }

            // x_r <= 1-z ; y_rk <= z
            for (int r = 0; r < n; r++) {
                GRBLinExpr c1 = new GRBLinExpr();
                c1.addTerm(1.0, x[r]);
                c1.addTerm(1.0, z);
                model.addConstr(c1, GRB.LESS_EQUAL, 1.0, "x_le_1mz_" + r);
                for (int k = 0; k < 3; k++) {
                    GRBLinExpr c2 = new GRBLinExpr();
                    c2.addTerm(1.0, y[r][k]);
                    c2.addTerm(-1.0, z);
                    model.addConstr(c2, GRB.LESS_EQUAL, 0.0, "y_le_z_" + r + "_" + k);
                }
            }

            // sum x >= 1-z, x_base >= 1-z
            GRBLinExpr sx = new GRBLinExpr();
            for (int r = 0; r < n; r++) {
                sx.addTerm(1.0, x[r]);
            }
            sx.addTerm(1.0, z);
            model.addConstr(sx, GRB.GREATER_EQUAL, 1.0, "sum_x_ge_1mz");
            if (baseIndex >= 0 && baseIndex < n) {
                GRBLinExpr xb = new GRBLinExpr();
                xb.addTerm(1.0, x[baseIndex]);
                xb.addTerm(1.0, z);
                model.addConstr(xb, GRB.GREATER_EQUAL, 1.0, "x_base_ge_1mz");
                // FB + 公平对比：replica 侧除 base 外至多再选 1 个整副本区（与「base+1 副本 vs base+2 片」叙事一致）
                GRBLinExpr extraRep = new GRBLinExpr();
                for (int r = 0; r < n; r++) {
                    if (r != baseIndex) {
                        extraRep.addTerm(1.0, x[r]);
                    }
                }
                model.addConstr(extraRep, GRB.LESS_EQUAL, 1.0, "at_most_one_extra_full_replica");
            }

            // sum_r y_rk = z, sum_k y_rk <= z, sum_k y_rk <= 1, x_r + sum_k y_rk <= 1
            for (int k = 0; k < 3; k++) {
                GRBLinExpr sy = new GRBLinExpr();
                for (int r = 0; r < n; r++) {
                    sy.addTerm(1.0, y[r][k]);
                }
                sy.addTerm(-1.0, z);
                model.addConstr(sy, GRB.EQUAL, 0.0, "sum_y_eq_z_k" + k);
            }
            for (int r = 0; r < n; r++) {
                GRBLinExpr row = new GRBLinExpr();
                for (int k = 0; k < 3; k++) {
                    row.addTerm(1.0, y[r][k]);
                }
                GRBLinExpr rowLeZ = new GRBLinExpr();
                rowLeZ.multAdd(1.0, row);
                rowLeZ.addTerm(-1.0, z);
                model.addConstr(rowLeZ, GRB.LESS_EQUAL, 0.0, "sum_y_le_z_r" + r);
                model.addConstr(row, GRB.LESS_EQUAL, 1.0, "sum_y_le_1_r" + r);

                GRBLinExpr disjoint = new GRBLinExpr();
                disjoint.addTerm(1.0, x[r]);
                disjoint.multAdd(1.0, row);
                model.addConstr(disjoint, GRB.LESS_EQUAL, 1.0, "x_plus_y_le_1_r" + r);
            }

            // Objective: sum sRep*x + sum sEc*y + readBaseConst + sum readRepDelta[r]*x[r] + z*readEcZCoeff
            // （readEcZCoeff = readEcFull − readBase；z=1 时 x≡0，读项为 readBase+readEcZCoeff = readEcFull）
            GRBLinExpr obj = new GRBLinExpr();
            for (int r = 0; r < n; r++) {
                obj.addTerm(coeff.sRep[r], x[r]);
                if (coeff.readRepDelta != null && r < coeff.readRepDelta.length) {
                    obj.addTerm(coeff.readRepDelta[r], x[r]);
                }
                for (int k = 0; k < 3; k++) {
                    obj.addTerm(coeff.sEc[r][k], y[r][k]);
                }
            }
            obj.addConstant(coeff.readBaseConst + coeff.tRep);
            obj.addTerm(coeff.readEcZCoeff + (coeff.tEc - coeff.tRep), z);
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
            boolean[][] yVal = new boolean[n][3];
            if (ok) {
                for (int r = 0; r < n; r++) {
                    xVal[r] = x[r].get(GRB.DoubleAttr.X) >= 0.5;
                    for (int k = 0; k < 3; k++) {
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
