package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.model.Status;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Read miss must cache a full replica at {@code issueRegion}; shards/patches must not suppress placement. */
class PolicyMilpECnewV3ReadCachePlacementTest {

    private static final Instant T0 = Instant.parse("2026-01-01T00:00:00Z");
    private static final String ISSUE = "aws:us-east-1";
    private static final String REMOTE = "gcp:us-west1-a";

    private PolicyMilpECnewV3 policy;
    private PolicyMilpECnewV4 v4Policy;
    private Map<String, LogicalObject> objects;
    private Config config;

    @BeforeEach
    void setUp() {
        config = new Config();
        config.placementPolicy = "milp_ec_new_v3";
        config.regions = List.of(ISSUE, REMOTE, "azure:westeurope");
        config.milpStrictFail = true;
        config.milpMinRequests = Integer.MAX_VALUE;
        config.deltaEnabled = true;
        DiGraph graph = new DiGraph();
        for (String r : config.regions) {
            graph.setNodeAttr(r, "priceStorage", 0.02);
        }
        objects = new HashMap<>();
        RegionManager rm = new RegionManager(graph, Logger.getLogger("test"));
        policy = new PolicyMilpECnewV3(config, graph, objects, rm);
        v4Policy = new PolicyMilpECnewV4(config, graph, objects, rm);
    }

    @Test
    void readMissWithOnlyLocalShardRequestsFullReplicaPlacement() {
        BigDecimal full = new BigDecimal("1024");
        LogicalObject lo = baseObject("obj-a", full);
        lo.physicalObjects.put(ISSUE, shard(ISSUE, full));
        lo.physicalObjects.put(REMOTE, shard(REMOTE, full));

        Request req = readReq("obj-a", full, ISSUE);
        List<String> regions = policy.place(req, config);
        Map<String, BigDecimal> sized = policy.getPlacementWithSizes(req, config);

        assertEquals(List.of(ISSUE), regions);
        assertEquals(full, sized.get(ISSUE));
    }

    @Test
    void readHitWhenFullReplicaAlreadyLocal() {
        BigDecimal full = new BigDecimal("1024");
        LogicalObject lo = baseObject("obj-b", full);
        lo.physicalObjects.put(ISSUE, replica(ISSUE, full));

        Request req = readReq("obj-b", full, ISSUE);
        assertTrue(policy.place(req, config).isEmpty());
        assertEquals(null, policy.getPlacementWithSizes(req, config));
    }

    @Test
    void readMissWithLocalDeltaPatchRequestsFullReplicaPlacement() {
        BigDecimal full = new BigDecimal("2048");
        BigDecimal patch = new BigDecimal("512");
        PolicyMilpECnewV4 v4 = v4Policy;
        LogicalObject member = baseObject("obj-d", full);
        member.isBase = false;
        member.baseKey = "obj-base";
        member.deltaRatio = 0.25;
        member.physicalObjects.put(ISSUE, replica(ISSUE, patch));

        Request req = readReq("obj-d", full, ISSUE);
        List<String> regions = v4.place(req, config);
        Map<String, BigDecimal> sized = v4.getPlacementWithSizes(req, config);

        assertEquals(List.of(ISSUE), regions);
        assertEquals(full, sized.get(ISSUE));
    }

    private LogicalObject baseObject(String key, BigDecimal size) {
        LogicalObject lo = new LogicalObject(key, size, T0, Status.ready);
        objects.put(key, lo);
        return lo;
    }

    private static Request readReq(String key, BigDecimal size, String issue) {
        Request req = new Request();
        req.op = "read";
        req.objKey = key;
        req.size = size;
        req.issueRegion = issue;
        req.timestamp = T0.plusSeconds(3600);
        return req;
    }

    private static PhysicalObject replica(String region, BigDecimal size) {
        PhysicalObject po = new PhysicalObject(region, "k", size, -1, null, Status.ready, 0);
        po.setStorageStartTime(T0);
        po.setShardSlot("REPLICA");
        return po;
    }

    private static PhysicalObject shard(String region, BigDecimal full) {
        PhysicalObject po = new PhysicalObject(region, "k", full.divide(BigDecimal.valueOf(2)), -1, null,
                Status.ready, 0);
        po.setStorageStartTime(T0);
        po.setShardSlot("S0");
        return po;
    }
}
