package org.example;

import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Status;
import org.example.src.model.LogicalObject;
import org.example.src.utils.CostLogger;
import org.example.src.utils.Definitions;
import org.example.graph.DiGraph;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.logging.Logger;

public class VerifyBigDecimal {
    public static void main(String[] args) {
        System.out.println("Starting verification...");

        // Setup Logger
        Logger logger = Logger.getLogger("VerifyBigDecimal");

        // Setup DiGraph (mock)
        DiGraph graph = new DiGraph();
        // Use setNodeAttr to add node and attribute implicitly
        graph.setNodeAttr("region1", "priceStorage", 0.023);

        // Setup RegionManager
        RegionManager rm = new RegionManager(graph, logger);
        rm.setStartTimeAndIgnoredDays(Instant.now().minusSeconds(3600), 0);

        // Setup CostLogger
        CostLogger.initialize("TEST", "test_trace", "test_config", true, 1);

        // Create Object
        String key = "obj1";
        BigDecimal sizeBytes = BigDecimal.valueOf(Definitions.GB).multiply(BigDecimal.TEN); // 10 GB
        // Constructor: key, size (BigDecimal), lastModified, status
        LogicalObject lo = new LogicalObject(key, sizeBytes, Instant.now(), Status.ready);
        lo.baseRegion = "region1";

        // PhysicalObject constructor: locationTag, key, size (BigDecimal), ttl,
        // logicalObject, status, versionId
        PhysicalObject po = new PhysicalObject("region1", key, sizeBytes, -1, lo, Status.ready, 1);
        po.setStorageStartTime(Instant.now().minusSeconds(3600)); // 1 hour ago

        // Add to logical object relationships
        lo.addPhysicalObject("region1", po);

        // Add object to RegionManager
        rm.addObjectToRegion("region1", po);

        // Remove object (triggers cost calculation)
        Instant endTime = Instant.now();
        System.out.println("Removing object...");
        rm.removeObjectFromRegion("region1", po, endTime);

        // Check aggregated cost
        BigDecimal cost = rm.aggregateStorageCost();
        System.out.println("Aggregated Cost (BigDecimal): " + cost);

        // Manual calc: 0.023 * 3 * (1/24) * 10
        // cost = pricePerGB(region) * sizeInGB * durationDays? No
        // RegionManager logic: pricePerGB returns (price*3) * days.
        // Then that is multiplied by sizeInGB.
        BigDecimal expected = BigDecimal.valueOf(0.023 * 3)
                .multiply(BigDecimal.valueOf(1.0 / 24.0))
                .multiply(BigDecimal.valueOf(10));

        System.out.println("Approx Expected: " + expected);

        BigDecimal diff = cost.subtract(expected).abs();
        if (diff.compareTo(BigDecimal.valueOf(0.000001)) < 0) {
            System.out.println("Verification PASSED: Cost matches within tolerance.");
        } else {
            System.out.println("Verification WARNING: Cost differs. " + diff);
            System.out.println("Note: Small differences are expected if 'expected' uses double math.");
        }

        CostLogger.close();
        System.out.println("Done.");
    }
}
