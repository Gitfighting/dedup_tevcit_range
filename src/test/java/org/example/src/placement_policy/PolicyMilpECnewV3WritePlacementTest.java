package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.RegionManager;
import org.example.src.placement_policy.milpv3.LayoutDecisionV3;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Verifies MILP {@code x}/{@code y} decisions are executed on PUT, not silently replaced by issue-only placement. */
class PolicyMilpECnewV3WritePlacementTest {

    private static final List<String> REGIONS = List.of(
            "aws:us-east-1", "azure:westeurope", "gcp:us-west1-a");

    private PolicyMilpECnewV3 policy;

    @BeforeEach
    void setUp() {
        Config config = new Config();
        config.placementPolicy = "milp_ec_new_v3";
        config.fixedBaseRegion = false;
        config.regions = REGIONS;
        DiGraph graph = new DiGraph();
        for (String r : REGIONS) {
            graph.setNodeAttr(r, "priceStorage", 0.02);
        }
        policy = new PolicyMilpECnewV3(config, graph, new HashMap<>(), new RegionManager(graph, Logger.getLogger("test")));
    }

    @Test
    void fpReplicaUsesMilpXNotIssueRegion() {
        LayoutDecisionV3 dec = replicaDecision(new boolean[] { true, false, false });
        List<String> regions = policy.regionsForWriteDecision(dec, "gcp:us-west1-a");
        assertEquals(List.of("aws:us-east-1"), regions);
    }

    @Test
    void fpReplicaAtIssueMatchesTevictPut() {
        LayoutDecisionV3 dec = replicaDecision(new boolean[] { false, false, true });
        List<String> regions = policy.regionsForWriteDecision(dec, "gcp:us-west1-a");
        assertEquals(List.of("gcp:us-west1-a"), regions);
    }

    @Test
    void fbReplicaUnionsIssueWithExtraReplica() {
        policy.config.fixedBaseRegion = true;
        LayoutDecisionV3 dec = replicaDecision(new boolean[] { false, true, false });
        List<String> regions = policy.regionsForWriteDecision(dec, "aws:us-east-1");
        assertTrue(regions.contains("aws:us-east-1"));
        assertTrue(regions.contains("azure:westeurope"));
        assertEquals(2, regions.size());
    }

    @Test
    void ecModeUsesShardRegionsFromY() {
        boolean[][] y = new boolean[3][2];
        y[1][0] = true;
        y[2][1] = true;
        LayoutDecisionV3 dec = ecDecision(y);
        List<String> regions = policy.regionsForWriteDecision(dec, "aws:us-east-1");
        assertEquals(List.of("azure:westeurope", "gcp:us-west1-a"), regions);
    }

    @Test
    void nullDecisionFallsBackToIssueRegion() {
        List<String> regions = policy.regionsForWriteDecision(null, "azure:westeurope");
        assertEquals(List.of("azure:westeurope"), regions);
    }

    private static LayoutDecisionV3 replicaDecision(boolean[] x) {
        return new LayoutDecisionV3(
                0, false, x, new boolean[3][2], 0, "gcp:us-west1-a",
                Instant.EPOCH, 0.0, new double[3], "2");
    }

    private static LayoutDecisionV3 ecDecision(boolean[][] y) {
        return new LayoutDecisionV3(
                1, true, new boolean[3], y, 0, "aws:us-east-1",
                Instant.EPOCH, 0.0, new double[3], "2");
    }
}
