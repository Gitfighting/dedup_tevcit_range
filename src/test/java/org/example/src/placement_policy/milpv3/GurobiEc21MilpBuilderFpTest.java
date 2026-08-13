package org.example.src.placement_policy.milpv3;

import com.gurobi.gurobi.GRBEnv;
import com.gurobi.gurobi.GRBException;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** FP MILP must allow tevict-like single replica at issue instead of forcing base-region copy or EC+base. */
class GurobiEc21MilpBuilderFpTest {

    private static final List<String> REGIONS = List.of(
            "aws:us-east-1", "azure:westeurope", "gcp:us-west1-a");

    @Test
    void fpPrefersReplicaAtIssueOverEcWhenRemoteTransferIsExpensive() throws GRBException {
        GRBEnv env;
        try {
            env = new GRBEnv(true);
            env.set("OutputFlag", "0");
            env.start();
        } catch (GRBException e) {
            Assumptions.assumeTrue(false, "Gurobi not available: " + e.getMessage());
            return;
        }
        try {
            int n = REGIONS.size();
            double[] sRep = {10.0, 8.0, 1.0};
            double[][] sEc = {
                    {4.0, 4.0},
                    {4.0, 4.0},
                    {2.0, 2.0}
            };
            double[] ttlSec = {3600.0, 3600.0, 3600.0};
            double localReadCache = 5.0;
            MilpCoefficientBundle coeff = new MilpCoefficientBundle(
                    sRep, sEc, ttlSec, false, 0.0, localReadCache);

            LayoutDecisionV3 dec = GurobiEc21MilpBuilder.solve(
                    env, coeff, 0, REGIONS, "gcp:us-west1-a", Instant.EPOCH,
                    5.0, 0.0, true, null, true, false);

            assertFalse(dec.z, "EC should lose when local replica at issue is cheapest");
            assertEquals(0, dec.mode);
            assertTrue(dec.x[2], "FP should place the single replica at issue region");
            assertFalse(dec.x[0], "FP must not force a base-region full replica");
        } finally {
            env.dispose();
        }
    }
}
