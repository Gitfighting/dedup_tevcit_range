package org.example.src.transfer_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.Request;

import java.math.BigDecimal;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 传输策略基类，等效于 Python 的 TransferPolicy。
 */
public abstract class TransferPolicy {
    protected final Config config;
    protected final DiGraph totalGraph;
    protected final Map<String, LogicalObject> objects;
    protected final int numVms;

    public TransferPolicy(Config config, DiGraph totalGraph, Map<String, LogicalObject> objectDict, int numVms) {
        this.config = config;
        this.totalGraph = totalGraph;
        this.objects = objectDict;
        this.numVms = numVms;
    }

    /**
     * 读取传输路径，返回源区域与路径图。
     */
    public abstract ReadPathResult readTransferPath(Request req);

    /**
     * 写入传输路径，返回路径图。
     */
    public abstract DiGraph writeTransferPath(Request req, String dst);

    /** 读取路径结果封装 */
    public static class ReadPathResult {
        public final String src;
        /** Legacy single-graph field; first segment when {@link #graphs} has multiple entries. */
        public final DiGraph graph;
        /** Non-null; replica-style reads use a singleton list. */
        public final List<DiGraph> graphs;
        /**
         * Non-null; empty means the simulator falls back to its own size resolution per segment index.
         */
        public final List<BigDecimal> segmentSizes;

        public ReadPathResult(String src, DiGraph graph) {
            this.src = src;
            this.graph = graph;
            this.graphs = Collections.singletonList(graph);
            this.segmentSizes = Collections.emptyList();
        }

        public ReadPathResult(String src, List<DiGraph> graphs, List<BigDecimal> segmentSizes) {
            this.src = src;
            List<DiGraph> g = graphs != null ? graphs : Collections.emptyList();
            this.graphs = g;
            this.graph = g.isEmpty() ? null : g.get(0);
            this.segmentSizes = segmentSizes != null ? segmentSizes : Collections.emptyList();
        }
    }
}

