package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.*;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.*;
import java.util.stream.Collectors;

/**
 * SkyPIE策略：基于访问集合的优化放置策略
 * Python: class SkyPIE(PlacementPolicy)
 * 注意：此实现简化了oracle相关逻辑，仅保留核心放置决策逻辑
 */
public class PolicySkypie extends PlacementPolicy {
    private final Map<String, Double> objectSizes = new HashMap<>();
    private final Map<String, Set<String>> accessSets = new HashMap<>();
    private final Map<Set<String>, List<String>> accessSetsObjects = new HashMap<>();
    private final Map<String, List<String>> placementDecisions = new HashMap<>();
    private final Map<String, Map<String, String>> getDecisions = new HashMap<>();
    private final Map<String, Map<String, String>> pastGetDecisions = new HashMap<>();
    private final String tracePath;

    public PolicySkypie(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(c, g, lo, rm);
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

                String region = parts[0]; // issue_region
                String objKey = parts[1]; // obj_key
                double size = Double.parseDouble(parts[2]); // size
                String op = parts[3]; // op

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

        // Convert to sorted sets for consistency
        for (Map.Entry<String, Set<String>> entry : tempAccessSets.entrySet()) {
            accessSets.put(entry.getKey(), entry.getValue());
        }

        // Invert: access_sets_objects
        for (Map.Entry<String, Set<String>> entry : accessSets.entrySet()) {
            String objKey = entry.getKey();
            Set<String> regions = entry.getValue();
            accessSetsObjects.computeIfAbsent(regions, k -> new ArrayList<>()).add(objKey);
        }
    }

    public void generateDecisions(List<Request> requests) {
        placementDecisions.clear();
        if (!getDecisions.isEmpty()) {
            pastGetDecisions.clear();
            pastGetDecisions.putAll(getDecisions);
        }
        getDecisions.clear();

        // Aggregate workloads per object
        Map<String, WorkloadData> workloadsPerObject = aggregatePerObject(requests);

        // Simplified oracle logic: use basic placement strategy
        for (Map.Entry<String, WorkloadData> entry : workloadsPerObject.entrySet()) {
            String objKey = entry.getKey();
            WorkloadData workload = entry.getValue();

            // Simple strategy: place in regions with highest activity
            List<String> topRegions = workload.getTopRegions(3);
            placementDecisions.put(objKey, topRegions);

            // Map each region to closest placement region
            List<String> targetRegions = config.regions;
            if (targetRegions == null || targetRegions.isEmpty()) {
                targetRegions = new ArrayList<>(totalGraph.getNodes());
            }

            Map<String, String> appAssignments = new HashMap<>();
            for (String region : targetRegions) {
                String closest = findClosestRegion(region, topRegions);
                appAssignments.put(region, closest);
            }
            getDecisions.put(objKey, appAssignments);
        }
    }

    private Map<String, WorkloadData> aggregatePerObject(List<Request> requests) {
        Map<String, WorkloadData> result = new HashMap<>();

        for (Request request : requests) {
            String objKey = request.objKey;
            String issueRegion = request.issueRegion;
            double size = request.size.doubleValue();

            WorkloadData workload = result.computeIfAbsent(objKey, k -> new WorkloadData());
            workload.size = size;

            if ("write".equals(request.op)) {
                workload.put.merge(issueRegion, 1, Integer::sum);
                workload.ingress.merge(issueRegion, size, Double::sum);
            } else if ("read".equals(request.op)) {
                workload.get.merge(issueRegion, 1, Integer::sum);
                workload.egress.merge(issueRegion, size, Double::sum);
            }
        }

        return result;
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
        return placementDecisions.getOrDefault(key, Collections.singletonList(req.issueRegion));
    }

    private static class WorkloadData {
        double size = 0;
        Map<String, Integer> put = new HashMap<>();
        Map<String, Integer> get = new HashMap<>();
        Map<String, Double> ingress = new HashMap<>();
        Map<String, Double> egress = new HashMap<>();

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
