package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.Request;
import org.example.src.model.Status;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 最近传输策略，选择到目的地吞吐最高的副本。
 */
public class ClosestTransfer extends TransferPolicy {
    public ClosestTransfer(Config config, DiGraph totalGraph, Map<String, LogicalObject> objectDict) {
        super(config, totalGraph, objectDict, 2);
    }

    @Override
    public ReadPathResult readTransferPath(Request req) {
        String dst = req.issueRegion;
        String src;
        LogicalObject lo = objects.get(req.objKey);
        if (lo != null) {
            List<String> regionList = lo.physicalObjects.values().stream()
                    .filter(po -> po.getStatus() == Status.ready)
                    .map(po -> po.getLocationTag())
                    .collect(Collectors.toList());
            src = regionList.stream().max((a, b) -> {
                double ta = ((Number) totalGraph.getEdgeAttr(a, dst, "throughput")).doubleValue();
                double tb = ((Number) totalGraph.getEdgeAttr(b, dst, "throughput")).doubleValue();
                return Double.compare(ta, tb);
            }).orElseThrow(() -> new IllegalStateException("No ready regions"));
        } else {
            if (config.storageRegion == null || config.storageRegion.isEmpty())
                throw new IllegalStateException("storageRegion is empty");
            src = config.storageRegion;
        }

        if (objects.get(req.objKey).physicalObjects.get(src).getStatus() != Status.ready)
            throw new IllegalStateException("Source not ready");

        DiGraph G = new DiGraph();
        Number throughput = (Number) totalGraph.getEdgeAttr(src, dst, "throughput");
        Number cost = (Number) totalGraph.getEdgeAttr(src, dst, "cost");
        Number latency = (Number) totalGraph.getEdgeAttr(src, dst, "latency");
        G.addEdge(src, dst, req.objKey, req.size, 1, new int[]{0}, throughput, cost, latency);
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
        G.addEdge(src, dst, req.objKey, req.size, 1, new int[]{0}, throughput, cost, latency);
        G.setNodeAttr(src, "src", true);
        G.setNodeAttr(dst, "dst", true);
        return G;
    }
}

