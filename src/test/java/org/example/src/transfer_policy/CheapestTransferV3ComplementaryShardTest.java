package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.model.Status;
import org.example.src.placement_policy.PolicyMilpECnewV3;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CheapestTransferV3ComplementaryShardTest {

	@Test
	void readPathShouldNotChooseTwoSameShardSlots() {
		String issue = "aws:r0";
		String a = "aws:r1";
		String b = "aws:r2";
		String c = "aws:r3";

		DiGraph graph = new DiGraph();
		Config config = buildConfig(issue, a, b, c);
		seedNodePricing(graph, issue, a, b, c);

		// Make A and B cheapest, C more expensive. If slot is ignored, A+B would be
		// chosen.
		addLink(graph, a, issue, 1.0);
		addLink(graph, b, issue, 1.0);
		addLink(graph, c, issue, 10.0);

		// Also provide links needed by write/hist paths to avoid missing attrs.
		addLink(graph, issue, a, 1.0);
		addLink(graph, issue, b, 1.0);
		addLink(graph, issue, c, 1.0);

		Map<String, LogicalObject> objects = new HashMap<>();
		String key = "obj-1";
		LogicalObject lo = new LogicalObject(key, new BigDecimal("1024"), Instant.parse("2026-01-01T00:00:00Z"),
				Status.ready);

		lo.physicalObjects.put(a, shard(key, a, "SHARD_1"));
		lo.physicalObjects.put(b, shard(key, b, "SHARD_1"));
		lo.physicalObjects.put(c, shard(key, c, "SHARD_2"));
		objects.put(key, lo);

		RegionManager rm = new RegionManager(graph, Logger.getLogger("test"));
		PolicyMilpECnewV3 policy = new PolicyMilpECnewV3(config, graph, objects, rm);
		CheapestTransferV3 transfer = new CheapestTransferV3(config, graph, objects, rm, policy);

		Request req = new Request();
		req.timestamp = Instant.parse("2026-01-01T01:00:00Z");
		req.op = "read";
		req.issueRegion = issue;
		req.objKey = key;
		req.size = new BigDecimal("1024");

		TransferPolicy.ReadPathResult rp = transfer.readTransferPath(req);
		List<String> srcs = new ArrayList<>();
		for (DiGraph g : rp.graphs) {
			if (!g.edgesWithData().isEmpty()) {
				srcs.add(g.edgesWithData().get(0).src);
			}
		}

		Set<String> srcSet = new HashSet<>(srcs);
		// Complementary rule should force one of {A,B} + C, never A+B.
		assertTrue(srcSet.contains(c), "Expected complementary SHARD_2 source to be selected");
		assertFalse(srcSet.contains(a) && srcSet.contains(b), "Must not pick two SHARD_1 sources together");
	}

	@Test
	void readPathShouldFallbackToReplicaWhenNoComplementaryPair() {
		String issue = "aws:r0";
		String a = "aws:r1";
		String b = "aws:r2";
		String replica = "aws:r3";

		DiGraph graph = new DiGraph();
		Config config = buildConfig(issue, a, b, replica);
		seedNodePricing(graph, issue, a, b, replica);

		addLink(graph, a, issue, 1.0);
		addLink(graph, b, issue, 1.0);
		addLink(graph, replica, issue, 2.0);

		addLink(graph, issue, a, 1.0);
		addLink(graph, issue, b, 1.0);
		addLink(graph, issue, replica, 1.0);

		Map<String, LogicalObject> objects = new HashMap<>();
		String key = "obj-2";
		BigDecimal fullSize = new BigDecimal("1024");
		LogicalObject lo = new LogicalObject(key, fullSize, Instant.parse("2026-01-01T00:00:00Z"), Status.ready);

		// No complementary pair: both shards are SHARD_1.
		lo.physicalObjects.put(a, shard(key, a, "SHARD_1"));
		lo.physicalObjects.put(b, shard(key, b, "SHARD_1"));
		lo.physicalObjects.put(replica, replica(key, replica, fullSize));
		objects.put(key, lo);

		RegionManager rm = new RegionManager(graph, Logger.getLogger("test"));
		PolicyMilpECnewV3 policy = new PolicyMilpECnewV3(config, graph, objects, rm);
		CheapestTransferV3 transfer = new CheapestTransferV3(config, graph, objects, rm, policy);

		Request req = new Request();
		req.timestamp = Instant.parse("2026-01-01T01:00:00Z");
		req.op = "read";
		req.issueRegion = issue;
		req.objKey = key;
		req.size = fullSize;

		TransferPolicy.ReadPathResult rp = transfer.readTransferPath(req);

		assertEquals(replica, rp.src, "Expected fallback to replica source when no complementary shard pair exists");
		assertEquals(1, rp.graphs.size(), "Replica fallback should use single transfer graph");
	}

	@Test
	void readPathShouldNotRecycleIntoSingleShardWithoutReplica() {
		String issue = "aws:r0";
		String shard = "aws:r1";
		String repA = "aws:r2";
		String repB = "aws:r3";

		DiGraph graph = new DiGraph();
		Config config = buildConfig(issue, shard, repA, repB);
		seedNodePricing(graph, issue, shard, repA, repB);

		addLink(graph, shard, issue, 1.0);
		addLink(graph, repA, issue, 2.0);
		addLink(graph, repB, issue, 3.0);
		addLink(graph, issue, shard, 1.0);
		addLink(graph, issue, repA, 1.0);
		addLink(graph, issue, repB, 1.0);

		Map<String, LogicalObject> objects = new HashMap<>();
		String key = "obj-3";
		BigDecimal fullSize = new BigDecimal("1024");
		LogicalObject lo = new LogicalObject(key, fullSize, Instant.parse("2026-01-01T00:00:00Z"), Status.ready);

		// One active shard remains, while replicas are expired.
		lo.physicalObjects.put(shard, shard(key, shard, "SHARD_1"));
		lo.physicalObjects.put(repA, expiredReplica(key, repA, fullSize));
		lo.physicalObjects.put(repB, expiredReplica(key, repB, fullSize));
		objects.put(key, lo);

		RegionManager rm = new RegionManager(graph, Logger.getLogger("test"));
		PolicyMilpECnewV3 policy = new PolicyMilpECnewV3(config, graph, objects, rm);
		CheapestTransferV3 transfer = new CheapestTransferV3(config, graph, objects, rm, policy);

		Request req = new Request();
		req.timestamp = Instant.parse("2026-01-01T01:00:00Z");
		req.op = "read";
		req.issueRegion = issue;
		req.objKey = key;
		req.size = fullSize;

		TransferPolicy.ReadPathResult rp = assertDoesNotThrow(() -> transfer.readTransferPath(req),
				"Recycle policy must not leave an unrecoverable single-shard/no-replica state");
		assertTrue(rp != null && rp.src != null, "Expected a feasible read source after cleanup");
	}

	private static Config buildConfig(String... regions) {
		Config c = new Config();
		c.placementPolicy = "milp_ec_new_v3";
		c.transferPolicy = "cheapest";
		c.windowSize = -1;
		c.fixedBaseRegion = false;
		c.regions = new ArrayList<>();
		for (String r : regions) {
			c.regions.add(r);
		}
		return c;
	}

	private static void seedNodePricing(DiGraph graph, String... regions) {
		for (String r : regions) {
			graph.setNodeAttr(r, "priceStorage", 0.02);
			graph.setNodeAttr(r, "pricePut", 0.000005);
			graph.setNodeAttr(r, "priceGet", 0.0000004);
		}
	}

	private static void addLink(DiGraph graph, String src, String dst, double cost) {
		Map<String, Object> attrs = new HashMap<>();
		attrs.put("obj_key", "seed");
		attrs.put("size", BigDecimal.ONE);
		attrs.put("num_partitions", 1);
		attrs.put("partitions", new int[] { 0 });
		attrs.put("throughput", 1e9);
		attrs.put("cost", cost);
		attrs.put("latency", 1.0);
		graph.addEdge(src, dst, attrs);
	}

	private static PhysicalObject shard(String key, String region, String slot) {
		LogicalObject owner = null;
		PhysicalObject po = new PhysicalObject(region, key, new BigDecimal("512"), -1, owner, Status.ready, 0);
		po.setStorageStartTime(Instant.parse("2026-01-01T00:00:00Z"));
		po.setShardSlot(slot);
		return po;
	}

	private static PhysicalObject replica(String key, String region, BigDecimal fullSize) {
		LogicalObject owner = null;
		PhysicalObject po = new PhysicalObject(region, key, fullSize, -1, owner, Status.ready, 0);
		po.setStorageStartTime(Instant.parse("2026-01-01T00:00:00Z"));
		po.setShardSlot("REPLICA");
		return po;
	}

	private static PhysicalObject expiredReplica(String key, String region, BigDecimal fullSize) {
		LogicalObject owner = null;
		PhysicalObject po = new PhysicalObject(region, key, fullSize, 60, owner, Status.ready, 0);
		po.setStorageStartTime(Instant.parse("2026-01-01T00:00:00Z"));
		po.setShardSlot("REPLICA");
		return po;
	}
}
