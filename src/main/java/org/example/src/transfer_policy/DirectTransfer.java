package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.Request;
import org.example.src.model.Status;

import java.util.Map;

/**
 * 直接传输策略，等效于 Python 的 DirectTransfer。
 */
public class DirectTransfer extends TransferPolicy {
    public DirectTransfer(Config config, DiGraph totalGraph, Map<String, LogicalObject> objectDict) {
        super(config, totalGraph, objectDict, 2);
    }

    @Override
    public ReadPathResult readTransferPath(Request req) {
        DiGraph G = new DiGraph();
        String src = config.storageRegion;
        String dst = req.issueRegion;

        LogicalObject lo = objects.get(req.objKey);
        if (lo == null) throw new IllegalStateException("Object not found: " + req.objKey);
        if (lo.physicalObjects.get(src).getStatus() != Status.ready) throw new IllegalStateException("Source not ready");

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

