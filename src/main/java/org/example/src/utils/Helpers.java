package org.example.src.utils;

import org.example.graph.DiGraph;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 工具函数，等效于 Python src.utils.helpers
 */
public final class Helpers {

    /**
     * 将字符串进行精炼处理，将前4个'-'分隔的部分转换为 "part0:part1-part2-part3" 格式
     * Python: parts = s.split("-")[:4]; refined = parts[0] + ":" +
     * "-".join(parts[1:])
     */
    public static String refineString(String s) {
        String[] parts = s.split("-", 4);
        if (parts.length == 0)
            return s;
        if (parts.length == 1)
            return parts[0] + ":";
        return parts[0] + ":" + String.join("-", Arrays.copyOfRange(parts, 1, parts.length));
    }

    /**
     * 将第一个冒号替换为连字符
     * Python: return s.replace(":", "-", 1)
     */
    public static String convertHyphenToColon(String s) {
        int idx = s.indexOf(":");
        if (idx >= 0) {
            return s.substring(0, idx) + "-" + s.substring(idx + 1);
        }
        return s;
    }

    /**
     * 获取最小网络成本
     * Python: get_min_network_cost(G)
     */
    public static Double getMinNetworkCost(DiGraph G) {
        List<Double> costs = new ArrayList<>();
        for (DiGraph.Edge edge : G.edgesWithData()) {
            Object cost = edge.attrs.get("cost");
            if (cost instanceof Number) {
                double c = ((Number) cost).doubleValue();
                if (c > 0)
                    costs.add(c);
            }
        }
        return costs.isEmpty() ? null : Collections.min(costs);
    }

    /**
     * 获取中位数网络成本
     * Python: get_median_network_cost(G)
     */
    public static Double getMedianNetworkCost(DiGraph G) {
        List<Double> costs = new ArrayList<>();
        for (DiGraph.Edge edge : G.edgesWithData()) {
            Object cost = edge.attrs.get("cost");
            if (cost instanceof Number) {
                double c = ((Number) cost).doubleValue();
                if (c > 0)
                    costs.add(c);
            }
        }
        if (costs.isEmpty())
            return null;
        Collections.sort(costs);
        int size = costs.size();
        if (size % 2 == 0) {
            return (costs.get(size / 2 - 1) + costs.get(size / 2)) / 2.0;
        } else {
            return costs.get(size / 2);
        }
    }

    /**
     * 获取平均网络成本
     * Python: get_avg_network_cost(G)
     */
    public static double getAvgNetworkCost(DiGraph G) {
        List<Double> costs = new ArrayList<>();
        for (DiGraph.Edge edge : G.edgesWithData()) {
            Object cost = edge.attrs.get("cost");
            if (cost instanceof Number) {
                double c = ((Number) cost).doubleValue();
                if (c > 0)
                    costs.add(c);
            }
        }
        if (costs.isEmpty())
            return 0.0;
        return costs.stream().mapToDouble(Double::doubleValue).average().orElse(0.0);
    }

    /**
     * 获取平均存储成本
     * Python: get_avg_storage_cost(G)
     */
    public static double getAvgStorageCost(DiGraph G) {
        List<Double> storages = new ArrayList<>();
        Set<String> nodes = G.getNodes();
        for (String node : nodes) {
            Object price = G.getNodeAttrs(node).get("priceStorage");
            if (price instanceof Number) {
                storages.add(((Number) price).doubleValue());
            }
        }
        if (storages.isEmpty())
            return 0.0;
        return storages.stream().mapToDouble(Double::doubleValue).average().orElse(0.0);
    }

    /**
     * 获取完整路径（将相对路径转换为绝对路径）
     * Python: get_full_path(relative_path)
     */
    public static String getFullPath(String relativePath) {
        Path base = Paths.get("").toAbsolutePath();
        return base.resolve(relativePath).toString();
    }

    /**
     * 构建网络拓扑图，从CSV文件加载成本、吞吐量、延迟和存储价格数据
     * Python: make_nx_graph(cost_path, throughput_path, latency_path,
     * storage_cost_path, num_vms)
     */
    public static DiGraph makeNxGraph(String costPath,
            String throughputPath,
            String latencyPath,
            String storageCostPath,
            int numVms) throws IOException {
        String path = new File(Helpers.class.getProtectionDomain().getCodeSource().getLocation().getPath()).getParent();

        File costFile = costPath == null ? Paths.get(path, "profiles", "cost.csv").toFile() : new File(costPath);
        File throughputFile = throughputPath == null ? Paths.get(path, "profiles", "throughput.csv").toFile()
                : new File(throughputPath);
        File latencyFile = latencyPath == null ? Paths.get(path, "profiles", "complete_latency.csv").toFile()
                : new File(latencyPath);
        File storageFile = storageCostPath == null ? Paths.get(path, "profiles", "storage.csv").toFile()
                : new File(storageCostPath);

        DiGraph G = new DiGraph();

        // Step 1: 加载throughput并创建边（初始cost为null）
        // throughput.csv: src_region,dst_region,throughput_sent
        try (BufferedReader br = new BufferedReader(new FileReader(throughputFile))) {
            String header = br.readLine();
            Map<String, Integer> idx = headerIndex(header);
            String line;
            while ((line = br.readLine()) != null) {
                String[] cols = splitCsv(line);
                String srcRegion = cols[idx.get("src_region")];
                String dstRegion = cols[idx.get("dst_region")];
                if (srcRegion.equals(dstRegion))
                    continue;
                double throughput = parseDouble(cols[idx.get("throughput_sent")]);
                // 创建边时cost先设为null，throughput除以GB
                G.addEdge(srcRegion, dstRegion, null, 0, 1, new int[] { 0 },
                        numVms * throughput / Definitions.GB, null, 1);
            }
        }

        // Step 2: 加载cost并更新已存在的边
        // cost.csv: src,dest,cost
        List<String> noCostPairs = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new FileReader(costFile))) {
            String header = br.readLine();
            Map<String, Integer> idx = headerIndex(header);
            String line;
            while ((line = br.readLine()) != null) {
                String[] cols = splitCsv(line);
                String src = cols[idx.get("src")];
                String dest = cols[idx.get("dest")];
                if (G.hasEdge(src, dest)) {
                    Map<String, Object> attrs = G.getEdgeAttrs(src, dest);
                    attrs.put("cost", parseDouble(cols[idx.get("cost")]));
                    attrs.put("latency", 1);
                }
            }
        }

        // 检查哪些边没有cost数据
        for (DiGraph.Edge edge : G.edgesWithData()) {
            if (edge.attrs.get("cost") == null) {
                noCostPairs.add("(" + edge.src + ", " + edge.dst + ")");
            }
        }
        if (!noCostPairs.isEmpty()) {
            System.out.println("Unable to get egress costs for: " + noCostPairs);
        }

        // Step 3: 加载storage成本
        // storage.csv: Vendor,Region,Group,Tier,PricePerUnit
        Set<String> noStorageCost = new HashSet<>();
        try (BufferedReader br = new BufferedReader(new FileReader(storageFile))) {
            String header = br.readLine();
            Map<String, Integer> idx = headerIndex(header);
            String line;
            while ((line = br.readLine()) != null) {
                String[] cols = splitCsv(line);
                String region = cols[idx.get("Vendor")] + ":" + cols[idx.get("Region")];
                String group = cols[idx.get("Group")];
                String tier = cols[idx.get("Tier")];
                if (G.getNodes().contains(region)) {
                    if ("storage".equals(group) && ("General Purpose".equals(tier) || "Hot".equals(tier))) {
                        double price = parseDouble(cols[idx.get("PricePerUnit")]);
                        G.setNodeAttr(region, "priceStorage", price);
                    } else if ("put request".equals(group)) {
                        // Load PUT request pricing (matching Python line 158-161)
                        if (G.getNodeAttr(region, "pricePut") == null) {
                            double price = parseDouble(cols[idx.get("PricePerUnit")]);
                            G.setNodeAttr(region, "pricePut", price);
                        }
                    } else if ("get request".equals(group)) {
                        // Load GET request pricing (matching Python line 162-165)
                        if (G.getNodeAttr(region, "priceGet") == null) {
                            double price = parseDouble(cols[idx.get("PricePerUnit")]);
                            G.setNodeAttr(region, "priceGet", price);
                        }
                    }
                } else {
                    noStorageCost.add(region);
                }
            }
        }

        // Step 3b: 加载 GCP 存储价格（gcp_storage_pricing.csv）
        // 格式: Vendor,Region,Name,Tier,Unit,PricePerUnit
        File gcpStorageFile = Paths.get(path, "profiles", "gcp_storage_pricing.csv").toFile();
        if (gcpStorageFile.exists()) {
            try (BufferedReader br = new BufferedReader(new FileReader(gcpStorageFile))) {
                String header = br.readLine();
                Map<String, Integer> idx = headerIndex(header);
                String line;
                while ((line = br.readLine()) != null) {
                    String[] cols = splitCsv(line);
                    String region = cols[idx.get("Vendor")] + ":" + cols[idx.get("Region")];
                    if (G.getNodes().contains(region) && G.getNodeAttr(region, "priceStorage") == null) {
                        double price = parseDouble(cols[idx.get("PricePerUnit")]);
                        if (price > 0) {
                            G.setNodeAttr(region, "priceStorage", price);
                        }
                    }
                }
            }
        }

        // Step 4: 加载latency并更新边（仅PREMIUM tier）
        // complete_latency.csv: src_region,dst_region,src_tier,dst_tier,avg_rtt
        try (BufferedReader br = new BufferedReader(new FileReader(latencyFile))) {
            String header = br.readLine();
            Map<String, Integer> idx = headerIndex(header);
            String line;
            while ((line = br.readLine()) != null) {
                String[] cols = splitCsv(line);
                String srcRegion = cols[idx.get("src_region")];
                String dstRegion = cols[idx.get("dst_region")];
                String srcTier = cols[idx.get("src_tier")];
                String dstTier = cols[idx.get("dst_tier")];
                if (G.hasEdge(srcRegion, dstRegion)
                        && "PREMIUM".equals(dstTier)
                        && "PREMIUM".equals(srcTier)) {
                    Map<String, Object> attrs = G.getEdgeAttrs(srcRegion, dstRegion);
                    attrs.put("latency", parseDouble(cols[idx.get("avg_rtt")]));
                }
            }
        }

        // Step 5: 为每个节点添加自环边（表示本地读取），如果不存在的话
        // 根据云提供商设置入口吞吐量限制
        Set<String> nodes = new HashSet<>(G.getNodes());
        for (String node : nodes) {
            if (!G.hasEdge(node, node)) {
                double ingressLimit;
                if (node.startsWith("aws")) {
                    ingressLimit = Definitions.AWS_INSTANCE_THROUGHPUT_LIMIT[1];
                } else if (node.startsWith("gcp")) {
                    ingressLimit = Definitions.GCP_INSTANCE_THROUGHPUT_LIMIT[1];
                } else {
                    ingressLimit = Definitions.AZURE_INSTANCE_THROUGHPUT_LIMIT[1];
                }
                // 自环边：cost=0, latency=1
                // Note: throughput NOT multiplied by numVms to match Python behavior
                G.addEdge(node, node, null, 0, 1, new int[] { 0 }, ingressLimit, 0.0, 1.0);
            }
        }

        System.out.println("Number of nodes: " + G.nodeCount());
        return G;
    }

    /**
     * 解析CSV表头，返回列名到索引的映射
     */
    private static Map<String, Integer> headerIndex(String headerLine) {
        String[] cols = splitCsv(headerLine);
        Map<String, Integer> idx = new HashMap<>();
        for (int i = 0; i < cols.length; i++) {
            idx.put(cols[i].trim(), i);
        }
        return idx;
    }

    /**
     * 简单CSV分割（不处理引号内逗号的复杂情况）
     */
    private static String[] splitCsv(String line) {
        return line.split(",");
    }

    /**
     * 安全解析double值
     */
    private static double parseDouble(String s) {
        try {
            return Double.parseDouble(s.trim());
        } catch (Exception e) {
            return 0.0;
        }
    }

    private Helpers() {
    }
}
