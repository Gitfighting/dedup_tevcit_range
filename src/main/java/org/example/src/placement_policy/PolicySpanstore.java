package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.*;
import java.util.stream.Collectors;

/**
 * SPANStore策略：基于访问集合和ILP优化的放置策略
 * Python: class SPANStore(PlacementPolicy)
 * 注意：此实现简化了oracle相关逻辑，仅保留核心放置决策逻辑
 */
public class PolicySpanstore extends PlacementPolicy {
    private final String policy;
    private final Map<String, Double> objectSizes = new HashMap<>();
    private final Map<String, Set<String>> accessSets = new HashMap<>();
    private final Map<Set<String>, List<String>> accessSetsObjects = new HashMap<>();
    private final Map<Set<String>, List<String>> placementDecisions = new HashMap<>();
    private final Map<String, String> getDecisions = new HashMap<>();
    private final Map<String, String> pastGetDecisions = new HashMap<>();
    private final String tracePath;

    public PolicySpanstore(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
        this.policy = c.policy != null ? c.policy : "spanstore";
        this.tracePath = c.tracePath;
        if (tracePath != null) {
            updateAccessSets(tracePath);
        }
    }

    private void updateAccessSets(String tracePath) {
        Map<String, Set<String>> regionToObjects = new HashMap<>();
        Map<String, Integer> versions = new HashMap<>();

        try (BufferedReader br = new BufferedReader(new FileReader(tracePath))) {
            String line = br.readLine(); // skip header
            while ((line = br.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length < 4)
                    continue;

                String region = parts[0];
                String objKey = parts[1];
                double size = Double.parseDouble(parts[2]);
                String op = parts[3];

                String normalizedOp = null;
                if (op.equals("GET") || op.equals("REST.GET.OBJECT")) {
                    normalizedOp = "read";
                } else if (op.equals("PUT") || op.equals("REST.PUT.OBJECT")) {
                    normalizedOp = "write";
                }

                if (normalizedOp == null)
                    continue;

                if ("write".equals(normalizedOp)) {
                    versions.put(objKey, versions.getOrDefault(objKey, -1) + 1);
                }

                String versionedKey = objKey + "-v" + versions.getOrDefault(objKey, 0);
                objectSizes.put(versionedKey, size);
                regionToObjects.computeIfAbsent(region, k -> new HashSet<>()).add(versionedKey);
            }
        } catch (IOException e) {
            e.printStackTrace();
        }

        // Build access sets
        Map<String, Set<String>> tempAccessSets = new HashMap<>();
        for (Map.Entry<String, Set<String>> entry : regionToObjects.entrySet()) {
            String region = entry.getKey();
            for (String obj : entry.getValue()) {
                tempAccessSets.computeIfAbsent(obj, k -> new HashSet<>()).add(region);
            }
        }

        for (Map.Entry<String, Set<String>> entry : tempAccessSets.entrySet()) {
            accessSets.put(entry.getKey(), entry.getValue());
        }

        // Invert
        for (Map.Entry<String, Set<String>> entry : accessSets.entrySet()) {
            String objKey = entry.getKey();
            Set<String> regions = entry.getValue();
            accessSetsObjects.computeIfAbsent(regions, k -> new ArrayList<>()).add(objKey);
        }
    }

    private AggregateResult spanstoreAggregate(List<Request> requests, Set<String> accessSet) {
        List<String> objectsInAccessSet = accessSetsObjects.getOrDefault(accessSet, Collections.emptyList());

        Map<String, Integer> putCounts = new HashMap<>();
        Map<String, Integer> getCounts = new HashMap<>();
        Map<String, Double> ingressCounts = new HashMap<>();
        Map<String, Double> egressCounts = new HashMap<>();

        for (Request request : requests) {
            if (objectsInAccessSet.contains(request.objKey)) {
                String region = request.issueRegion;
                if ("write".equals(request.op)) {
                    putCounts.merge(region, 1, Integer::sum);
                    ingressCounts.merge(region, request.size.doubleValue(), Double::sum);
                } else if ("read".equals(request.op)) {
                    getCounts.merge(region, 1, Integer::sum);
                    egressCounts.merge(region, request.size.doubleValue(), Double::sum);
                }
            }
        }

        double totalSize = objectsInAccessSet.stream()
                .mapToDouble(objKey -> objectSizes.getOrDefault(objKey, 0.0))
                .sum();

        // Ensure all regions in access set have entries
        for (String region : accessSet) {
            putCounts.putIfAbsent(region, 0);
            getCounts.putIfAbsent(region, 0);
            ingressCounts.putIfAbsent(region, 0.0);
            egressCounts.putIfAbsent(region, 0.0);
        }

        return new AggregateResult(totalSize, putCounts, getCounts, ingressCounts, egressCounts);
    }

    public void generateDecisions(List<Request> requests) {
        placementDecisions.clear();
        if (!getDecisions.isEmpty()) {
            pastGetDecisions.clear();
            pastGetDecisions.putAll(getDecisions);
        }
        getDecisions.clear();

        for (Set<String> accessSet : accessSetsObjects.keySet()) {
            AggregateResult agg = spanstoreAggregate(requests, accessSet);

            // Simplified placement: choose regions with highest activity
            List<String> topRegions = agg
                    .getTopRegions(config.replicationFactorMin != null ? config.replicationFactorMin : 3);
            placementDecisions.put(accessSet, topRegions);

            // Map each region to closest placement
            for (String region : accessSet) {
                String closest = findClosestRegion(region, topRegions);
                getDecisions.put(region, closest);
            }
        }
    }

    private String findClosestRegion(String source, List<String> targets) {
        if (targets.isEmpty())
            return source;
        if (targets.contains(source))
            return source;

        return targets.stream()
                .min(Comparator.comparing(target -> {
                    Map<String, Object> attrs = totalGraph.getEdgeAttrs(source, target);
                    if (attrs == null)
                        return Double.MAX_VALUE;
                    Object cost = attrs.get("cost");
                    return cost instanceof Number ? ((Number) cost).doubleValue() : Double.MAX_VALUE;
                }))
                .orElse(targets.get(0));
    }

    @Override
    public List<String> place(Request req, Config config) {
        String key = req.objKey;
        Set<String> accessSet = accessSets.get(key);
        if (accessSet != null) {
            List<String> regions = placementDecisions.get(accessSet);
            if (regions != null) {
                return regions;
            }
        }
        return Collections.singletonList(req.issueRegion);
    }

    private static class AggregateResult {
        final double size;
        final Map<String, Integer> put;
        final Map<String, Integer> get;
        final Map<String, Double> ingress;
        final Map<String, Double> egress;

        AggregateResult(double size, Map<String, Integer> put, Map<String, Integer> get,
                Map<String, Double> ingress, Map<String, Double> egress) {
            this.size = size;
            this.put = put;
            this.get = get;
            this.ingress = ingress;
            this.egress = egress;
        }

        List<String> getTopRegions(int count) {
            Map<String, Integer> activity = new HashMap<>();
            put.forEach((r, v) -> activity.merge(r, v, Integer::sum));
            get.forEach((r, v) -> activity.merge(r, v, Integer::sum));

            return activity.entrySet().stream()
                    .sorted(Map.Entry.<String, Integer>comparingByValue().reversed())
                    .limit(count)
                    .map(Map.Entry::getKey)
                    .collect(Collectors.toList());
        }
    }
}
