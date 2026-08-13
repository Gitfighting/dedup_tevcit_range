package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.time.Duration;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * TTL-based GET eviction policy using inter-arrival time.
 *
 * <p>On GET: computes the time interval between this access and the next access
 * (time_to_next_access). If this interval is less than TTL, the object is treated
 * as "hot" and a physical replica is stored locally. Otherwise it is "cold" and
 * only a reference is kept (the object is evicted after serving the read).</p>
 *
 * <p>This is a placement-time decision policy that complements the traditional
 * eviction-time TTL policies. It answers: "Should we store a local copy of this
 * object after serving a GET, given what we know about when it will next be accessed?"</p>
 *
 * <p>TTL source (priority order):
 * <ol>
 *   <li>{@code config.cacheTtl} (hours) — explicit experiment control</li>
 *   <li>Break-even TTL computed from graph network/storage costs</li>
 * </ol></p>
 */
public class PolicyTevictGetTtl extends PlacementPolicy {
    private final long ttlSeconds;

    public PolicyTevictGetTtl(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);

        if (c.cacheTtl != null && c.cacheTtl > 0) {
            this.ttlSeconds = c.cacheTtl * 3600L;
        } else {
            this.ttlSeconds = computeBreakEvenTtlSeconds(g);
        }
    }

    /**
     * Compute break-even TTL in seconds from average network and storage costs.
     * Formula: ttl_seconds = (avg_network_cost / avg_storage_cost) * 3600 * 24 / 3
     */
    private long computeBreakEvenTtlSeconds(DiGraph g) {
        double sumNetCost = 0.0;
        int netCount = 0;
        double sumStorage = 0.0;
        int storCount = 0;

        for (String node : g.getNodes()) {
            Map<String, Object> nodeAttrs = g.getNodeAttrs(node);
            Object priceObj = nodeAttrs.get("priceStorage");
            if (priceObj instanceof Number && ((Number) priceObj).doubleValue() > 0) {
                sumStorage += ((Number) priceObj).doubleValue();
                storCount++;
            }

            for (String dst : g.getSuccessors(node)) {
                Map<String, Object> edgeAttrs = g.getEdgeAttrs(node, dst);
                Object costObj = edgeAttrs.get("cost");
                if (costObj instanceof Number) {
                    sumNetCost += ((Number) costObj).doubleValue();
                    netCount++;
                }
            }
        }

        if (netCount == 0 || storCount == 0 || sumStorage <= 0) {
            return 86400L; // default: 24 hours
        }

        double avgNetCost = sumNetCost / netCount;
        double avgStorage = sumStorage / storCount;

        // Same formula as Teven / fixedttl: ttl_hours = (net_cost / storage) * 24 / 3
        double ttlHours = (avgNetCost / avgStorage) * 24.0 / 3.0;
        return (long) (ttlHours * 3600.0);
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        String issueRegion = req.issueRegion;
        String op = req.op;

        if ("write".equals(op)) {
            return Collections.singletonList(issueRegion);
        }

        // GET operation: TTL-based decision using time_to_next_access
        if (req.nextAccessTimestamp != null) {
            long timeToNextAccess = Duration.between(req.timestamp, req.nextAccessTimestamp).getSeconds();

            if (timeToNextAccess > 0 && timeToNextAccess < ttlSeconds) {
                if (!logicalObjects.containsKey(key)
                        || !logicalObjects.get(key).physicalObjects.containsKey(issueRegion)) {
                    return Collections.singletonList(issueRegion);
                }
                return Collections.emptyList();
            }
        }

        return Collections.emptyList();
    }
}
