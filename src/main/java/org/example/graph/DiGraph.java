package org.example.graph;

import java.util.*;

/**
 * 有向图的简单实现，提供与 Python networkx.DiGraph 等效的核心能力：
 * - 添加边并附带属性（throughput、cost、latency、obj_key、size 等）
 * - 访问节点属性（例如 priceStorage、src/dst 标记）
 * - 查询边是否存在与获取属性
 * - 遍历边集合（包含属性）
 */
public class DiGraph {
    /** 节点属性：node -> attr map */
    private final Map<String, Map<String, Object>> nodes = new HashMap<>();
    /** 边属性：src -> dst -> attr map */
    private final Map<String, Map<String, Map<String, Object>>> edges = new HashMap<>();

    public void addEdge(String src, String dst, Map<String, Object> attrs) {
        nodes.computeIfAbsent(src, k -> new HashMap<>());
        nodes.computeIfAbsent(dst, k -> new HashMap<>());
        edges.computeIfAbsent(src, k -> new HashMap<>())
             .put(dst, new HashMap<>(attrs));
    }

    public void addEdge(String src,
                        String dst,
                        String objKey,
                        Number size,
                        int numPartitions,
                        int[] partitions,
                        Number throughput,
                        Number cost,
                        Number latency) {
        Map<String, Object> attrs = new HashMap<>();
        attrs.put("obj_key", objKey);
        attrs.put("size", size);
        attrs.put("num_partitions", numPartitions);
        attrs.put("partitions", partitions);
        attrs.put("throughput", throughput);
        attrs.put("cost", cost);
        attrs.put("latency", latency);
        addEdge(src, dst, attrs);
    }

    public boolean hasEdge(String src, String dst) {
        return edges.containsKey(src) && edges.get(src).containsKey(dst);
    }

    public Map<String, Object> getEdgeAttrs(String src, String dst) {
        if (!hasEdge(src, dst)) return Collections.emptyMap();
        return edges.get(src).get(dst);
    }

    public Object getEdgeAttr(String src, String dst, String key) {
        return getEdgeAttrs(src, dst).get(key);
    }

    public Map<String, Object> getNodeAttrs(String node) {
        return nodes.computeIfAbsent(node, k -> new HashMap<>());
    }

    public Object getNodeAttr(String node, String key) {
        return getNodeAttrs(node).get(key);
    }

    public void setNodeAttr(String node, String key, Object value) {
        getNodeAttrs(node).put(key, value);
    }

    public boolean containsNode(String node) {
        return nodes.containsKey(node);
    }

    public int nodeCount() { return nodes.size(); }

    public Set<String> getNodes() {
        return nodes.keySet();
    }

    public List<String> getSuccessors(String node) {
        Map<String, Map<String, Object>> successors = edges.get(node);
        if (successors == null) return Collections.emptyList();
        return new ArrayList<>(successors.keySet());
    }

    public List<Edge> edgesWithData() {
        List<Edge> list = new ArrayList<>();
        for (Map.Entry<String, Map<String, Map<String, Object>>> e1 : edges.entrySet()) {
            String src = e1.getKey();
            for (Map.Entry<String, Map<String, Object>> e2 : e1.getValue().entrySet()) {
                String dst = e2.getKey();
                list.add(new Edge(src, dst, e2.getValue()));
            }
        }
        return list;
    }

    public static class Edge {
        public final String src;
        public final String dst;
        public final Map<String, Object> attrs;

        public Edge(String src, String dst, Map<String, Object> attrs) {
            this.src = src;
            this.dst = dst;
            this.attrs = attrs;
        }
    }
}

