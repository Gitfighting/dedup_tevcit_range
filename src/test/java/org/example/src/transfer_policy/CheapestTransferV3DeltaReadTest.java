package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.model.Status;
import org.example.src.placement_policy.PolicyMilpECnewV4;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Pins the §7.4 read-recovery fix: a delta member whose patch is local but whose base is remote must
 * charge a real base hop (base bytes on the base→reader edge), not a zero-cost local hit.
 */
class CheapestTransferV3DeltaReadTest {

	private static final String ISSUE = "aws:r0";
	private static final String BASE_REGION = "aws:r1";
	private static final Instant T0 = Instant.parse("2026-01-01T00:00:00Z");
	private static final Instant T_READ = Instant.parse("2026-01-01T01:00:00Z");

	@Test
	void deltaReadChargesBaseHopWhenPatchLocalButBaseRemote() {
		Harness h = new Harness();
		BigDecimal full = new BigDecimal("1024");
		BigDecimal patch = new BigDecimal("512"); // r = 0.5

		// Delta member: patch replica local at the reader.
		LogicalObject member = h.deltaMember("obj-d", full, "obj-base", 0.5);
		member.physicalObjects.put(ISSUE, replica("obj-d", ISSUE, patch));

		// Base: full replica only in the remote region.
		LogicalObject base = h.baseObject("obj-base", full);
		base.physicalObjects.put(BASE_REGION, replica("obj-base", BASE_REGION, full));

		TransferPolicy.ReadPathResult rp = h.read("obj-d", full);

		assertEquals(2, rp.graphs.size(), "Expected patch hop + base hop");
		assertEquals(2, rp.segmentSizes.size(), "Both hops must carry explicit sizes");
		assertEquals(0, rp.segmentSizes.get(0).compareTo(BigDecimal.ZERO), "Local patch hop must be free");
		assertEquals(0, rp.segmentSizes.get(1).compareTo(full), "Remote base hop must charge full base bytes");
		assertTrue(hasSource(rp, BASE_REGION), "Base hop must originate at the remote base region");
	}

	@Test
	void deltaReadHasNoBaseHopWhenBaseAlsoLocal() {
		Harness h = new Harness();
		BigDecimal full = new BigDecimal("1024");
		BigDecimal patch = new BigDecimal("512");

		LogicalObject member = h.deltaMember("obj-d", full, "obj-base", 0.5);
		member.physicalObjects.put(ISSUE, replica("obj-d", ISSUE, patch));

		LogicalObject base = h.baseObject("obj-base", full);
		base.physicalObjects.put(ISSUE, replica("obj-base", ISSUE, full));

		TransferPolicy.ReadPathResult rp = h.read("obj-d", full);

		assertEquals(1, rp.graphs.size(), "Base local → no base hop");
		assertEquals(0, rp.segmentSizes.get(0).compareTo(BigDecimal.ZERO), "Fully local delta read must be free");
	}

	// ---- harness ----

	private static final class Harness {
		final DiGraph graph = new DiGraph();
		final Config config;
		final Map<String, LogicalObject> objects = new HashMap<>();
		final CheapestTransferV3 transfer;

		Harness() {
			config = buildConfig(ISSUE, BASE_REGION);
			seedNodePricing(graph, ISSUE, BASE_REGION);
			addLink(graph, BASE_REGION, ISSUE, 0.09);
			addLink(graph, ISSUE, BASE_REGION, 0.09);
			RegionManager rm = new RegionManager(graph, Logger.getLogger("test"));
			PolicyMilpECnewV4 policy = new PolicyMilpECnewV4(config, graph, objects, rm);
			transfer = new CheapestTransferV3(config, graph, objects, rm, policy);
		}

		LogicalObject deltaMember(String key, BigDecimal size, String baseKey, double r) {
			LogicalObject lo = new LogicalObject(key, size, T0, Status.ready);
			lo.isBase = false;
			lo.baseKey = baseKey;
			lo.deltaRatio = r;
			objects.put(key, lo);
			return lo;
		}

		LogicalObject baseObject(String key, BigDecimal size) {
			LogicalObject lo = new LogicalObject(key, size, T0, Status.ready);
			lo.isBase = true;
			lo.deltaRatio = 1.0;
			objects.put(key, lo);
			return lo;
		}

		TransferPolicy.ReadPathResult read(String key, BigDecimal size) {
			Request req = new Request();
			req.timestamp = T_READ;
			req.op = "read";
			req.issueRegion = ISSUE;
			req.objKey = key;
			req.size = size;
			return transfer.readTransferPath(req);
		}
	}

	private static boolean hasSource(TransferPolicy.ReadPathResult rp, String region) {
		for (DiGraph g : rp.graphs) {
			if (!g.edgesWithData().isEmpty() && region.equals(g.edgesWithData().get(0).src)) {
				return true;
			}
		}
		return false;
	}

	private static Config buildConfig(String... regions) {
		Config c = new Config();
		c.placementPolicy = "milp_ec_new_v4";
		c.transferPolicy = "cheapest";
		c.windowSize = -1;
		c.fixedBaseRegion = false;
		c.deltaEnabled = true;
		c.ecOnPatch = false;
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

	private static PhysicalObject replica(String key, String region, BigDecimal size) {
		PhysicalObject po = new PhysicalObject(region, key, size, -1, null, Status.ready, 0);
		po.setStorageStartTime(T0);
		po.setShardSlot("REPLICA");
		return po;
	}
}
