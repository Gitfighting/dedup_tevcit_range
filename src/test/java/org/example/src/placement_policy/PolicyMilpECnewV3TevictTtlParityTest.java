package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** Pair-TTL {@link PolicyMilpECnewV3#getTevict} must match tevict_ranges early-return semantics. */
class PolicyMilpECnewV3TevictTtlParityTest {

    private PolicyMilpECnewV3 policy;
    private PolicyTevictRangesNew tevict;

    @BeforeEach
    void setUp() {
        DiGraph g = buildThreeRegGraph();
        Config config = new Config();
        config.regions = List.of("aws:us-east-1", "gcp:us-west1-a", "azure:westeurope");
        config.windowSize = -1;
        config.milpStrictFail = true;
        // TTL parity tests must not invoke Gurobi or require trace objects in the map.
        config.milpMinRequests = Integer.MAX_VALUE;
        Map<String, LogicalObject> objects = new HashMap<>();
        RegionManager rm = new RegionManager(g, Logger.getLogger("test"));
        policy = new PolicyMilpECnewV3(config, g, objects, rm);
        tevict = new PolicyTevictRangesNew(config, g, objects, rm);
    }

    @Test
    void getTevictWithinTwelveHourWindowIsStable() {
        Instant t0 = Instant.parse("2020-01-01T00:00:00Z");
        String src = "gcp:us-west1-a";
        String dst = "aws:us-east-1";
        double first = policy.getTevict(src, dst, t0);
        double second = policy.getTevict(src, dst, t0.plusSeconds(3600));
        assertEquals(first, second, 1e-9);
        double tevFirst = tevict.getTevict(src, dst, t0);
        double tevSecond = tevict.getTevict(src, dst, t0.plusSeconds(3600));
        assertEquals(tevFirst, tevSecond, 1e-9);
    }

    @Test
    void warmHistogramRequestsMatchBetweenPolicies() {
        Instant t = Instant.parse("2020-01-02T06:00:00Z");
        for (int i = 0; i < 1005; i++) {
            Request req = new Request();
            req.op = "write";
            req.objKey = "key-" + i;
            req.size = BigDecimal.valueOf(1_000_000);
            req.issueRegion = "aws:us-east-1";
            req.timestamp = t.plusSeconds(i * 30L);
            policy.updatePastRequests(req, req.issueRegion);
            tevict.updatePastRequests(req, req.issueRegion);
        }
        String src = "gcp:us-west1-a";
        String dst = "aws:us-east-1";
        double milpTtl = policy.getTevict(src, dst, t.plusSeconds(1005 * 30L));
        double tevTtl = tevict.getTevict(src, dst, t.plusSeconds(1005 * 30L));
        assertEquals(tevTtl, milpTtl, 1e-6);
    }

    private static DiGraph buildThreeRegGraph() {
        DiGraph g = new DiGraph();
        List<String> regs = List.of("aws:us-east-1", "gcp:us-west1-a", "azure:westeurope");
        for (String r : regs) {
            g.setNodeAttr(r, "priceStorage", 0.023);
        }
        double[][] costs = {
                {0, 0.02, 0.03},
                {0.02, 0, 0.025},
                {0.03, 0.025, 0}
        };
        for (int i = 0; i < regs.size(); i++) {
            for (int j = 0; j < regs.size(); j++) {
                if (i != j) {
                    Map<String, Object> attrs = new HashMap<>();
                    attrs.put("obj_key", "seed");
                    attrs.put("size", BigDecimal.ONE);
                    attrs.put("num_partitions", 1);
                    attrs.put("partitions", new int[] { 0 });
                    attrs.put("throughput", 1e9);
                    attrs.put("cost", costs[i][j]);
                    attrs.put("latency", 1.0);
                    g.addEdge(regs.get(i), regs.get(j), attrs);
                }
            }
        }
        return g;
    }
}
