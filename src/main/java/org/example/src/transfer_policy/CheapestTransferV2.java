package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.model.Status;
import org.example.src.placement_policy.PlacementPolicy;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HashMap;
import java.util.Map;

/**
 * 最便宜传输策略（V2），等效于 Python CheapestTransferV2。
 * 选择网络成本最低的副本进行读取，并处理过期对象的清理。
 */
public class CheapestTransferV2 extends TransferPolicy {
    private final RegionManager regionManager;
    private final PlacementPolicy placementPolicy; // 可为 null

    public CheapestTransferV2(Config config,
            DiGraph totalGraph,
            Map<String, LogicalObject> objectDict,
            RegionManager regionManager,
            PlacementPolicy placementPolicy) {
        super(config, totalGraph, objectDict, 2);
        this.regionManager = regionManager;
        this.placementPolicy = placementPolicy;
    }

    @Override
    public ReadPathResult readTransferPath(Request req) {
        String dst = req.issueRegion;
        String src;

        if (objects.containsKey(req.objKey)) {
            Map<String, PhysicalObject> newPhysObjs = new HashMap<>();
            Map<String, PhysicalObject> physObjs = objects.get(req.objKey).physicalObjects;
            src = null;
            PhysicalObject toRemove = null;
            Instant toRemEvictTime = null;
            int inactive = 0;

            for (Map.Entry<String, PhysicalObject> entry : physObjs.entrySet()) {
                String region = entry.getKey();
                PhysicalObject obj = entry.getValue();

                if (req.timestamp.isBefore(obj.getStorageStartTime())) {
                    newPhysObjs.put(region, obj);
                    inactive += 1;
                } else {
                    if (obj.getTtl() == Integer.MAX_VALUE || !obj.isExpired(req.timestamp)) {
                        newPhysObjs.put(region, obj);
                        if (src == null)
                            src = obj.getLocationTag();
                        else {
                            String finalSrc = src;
                            src = minBy(src, obj.getLocationTag(), (x) -> new double[] {
                                    toD(totalGraph.getEdgeAttr(x, dst, "cost")),
                                    toD(totalGraph.getEdgeAttr(x, dst, "latency"))
                            });
                        }
                    } else {
                        Instant evictTime = obj.getStorageStartTime().plus(obj.getTtl(), ChronoUnit.SECONDS);
                        if (toRemove == null || evictTime.isAfter(toRemEvictTime)) {
                            toRemove = obj;
                            toRemEvictTime = evictTime;
                        }
                        regionManager.removeObjectFromRegion(obj.getLocationTag(), obj, evictTime);
                        if ("dynamicttl".equals(config.placementPolicy) && placementPolicy != null) {
                            placementPolicy.updateGlobalTtl(obj.getTtl(), obj.getKey(), region, dst);
                        }
                    }
                }
            }

            if (newPhysObjs.size() - inactive <= 0) {
                newPhysObjs.put(toRemove.getLocationTag(), toRemove);
                toRemove.setStorageStartTime(toRemEvictTime);
                regionManager.addObjectToRegion(toRemove.getLocationTag(), toRemove);
                toRemove.expireImmediate = true;
                toRemove.setTtl(-1);
                src = toRemove.getLocationTag();
            }

            objects.get(req.objKey).physicalObjects = newPhysObjs;
            if (src == null)
                throw new IllegalStateException("src is null");
        } else {
            String base = objects.get(req.objKey).baseRegion;
            if (base == null)
                throw new IllegalStateException("base_region is null");
            src = base;
        }

        if (objects.get(req.objKey).physicalObjects.get(src).getStatus() != Status.ready)
            throw new IllegalStateException("Source not ready");

        DiGraph G = new DiGraph();
        Number throughput = (Number) totalGraph.getEdgeAttr(src, dst, "throughput");
        Number cost = (Number) totalGraph.getEdgeAttr(src, dst, "cost");
        Number latency = (Number) totalGraph.getEdgeAttr(src, dst, "latency");
        G.addEdge(src, dst, req.objKey, req.size, 1, new int[] { 0 }, throughput, cost, latency);
        G.setNodeAttr(src, "src", true);
        G.setNodeAttr(dst, "dst", true);
        return new ReadPathResult(src, G);
    }

    @Override
    public DiGraph writeTransferPath(Request req, String dst) {
        String src = req.issueRegion;
        DiGraph G = new DiGraph();
        Number throughput = (Number) totalGraph.getEdgeAttr(src, dst, "throughput");
        Number cost = (Number) totalGraph.getEdgeAttr(src, dst, "cost");
        Number latency = (Number) totalGraph.getEdgeAttr(src, dst, "latency");
        G.addEdge(src, dst, req.objKey, req.size, 1, new int[] { 0 }, throughput, cost, latency);
        G.setNodeAttr(src, "src", true);
        G.setNodeAttr(dst, "dst", true);
        return G;
    }

    private static String minBy(String a, String b, java.util.function.Function<String, double[]> key) {
        double[] ka = key.apply(a);
        double[] kb = key.apply(b);
        int cmp = Double.compare(ka[0], kb[0]);
        if (cmp != 0)
            return cmp <= 0 ? a : b;
        return Double.compare(ka[1], kb[1]) <= 0 ? a : b;
    }

    private static double toD(Object o) {
        return o instanceof Number ? ((Number) o).doubleValue() : 0.0;
    }
}
