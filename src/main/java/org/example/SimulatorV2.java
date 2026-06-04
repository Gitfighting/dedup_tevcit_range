package org.example;

import org.example.src.model.Config;
import org.example.src.utils.Helpers;
import java.io.*;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.math.BigDecimal;
import java.math.RoundingMode;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import org.example.src.model.*;
import org.example.src.placement_policy.*;
import org.example.src.transfer_policy.*;
import org.example.graph.DiGraph;
/**
 * Python simulator_v2.py 的前 1-174 行的 Java 1.8 等价实现。
 * 保留原始结构与逻辑，包括导入、日志初始化、versionate_trace 函数与构造逻辑初始化。
 */
public class SimulatorV2 {
    private static final Logger logger = Logger.getLogger(SimulatorV2.class.getName());

    /** Tail after last trace request used by {@link org.example.src.model.RegionManager#calculateRemainingStorageCosts}. */
    public static final long TRACE_ACCOUNTING_TAIL_SECONDS = 50L;

    // 版本化函数：versionate_trace(trace_path, out_path)
    public void versionateTrace(String tracePath, String outPath) {
        Map<String, Integer> versions = new HashMap<>();
        File in = new File(Helpers.getFullPath(tracePath));
        File out = new File(outPath);
        try (BufferedReader br = new BufferedReader(new FileReader(in));
                BufferedWriter bw = new BufferedWriter(new FileWriter(out))) {
            String header = br.readLine();
            if (header == null)
                return;
            bw.write(header);
            bw.newLine();
            String[] cols = header.split(",");
            String line;
            while ((line = br.readLine()) != null) {
                String[] vals = line.split(",");
                Map<String, String> row = new HashMap<>();
                for (int i = 0; i < cols.length && i < vals.length; i++)
                    row.put(cols[i], vals[i]);
                String op = row.get("op");
                String objKey = row.get("obj_key");
                if ("PUT".equals(op) || "REST.PUT.OBJECT".equals(op)) {
                    versions.put(objKey, versions.getOrDefault(objKey, 0) + 1);
                }
                int v = versions.getOrDefault(objKey, 0);
                String newVersionId = objKey + "-v" + v;
                row.put("obj_key", newVersionId);
                bw.write(joinRow(cols, row));
                bw.newLine();
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    private String joinRow(String[] cols, Map<String, String> row) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < cols.length; i++) {
            if (i > 0)
                sb.append(',');
            sb.append(row.getOrDefault(cols[i], ""));
        }
        return sb.toString();
    }

    // 类 SimulatorV2 的构造与字段初始化（对应 __init__ 前 1-174 行）
    protected Config config;
    private String tracePath;
    private boolean storeDecision;
    private boolean setBaseRegion;
    private boolean versionEnable;
    private int daysToIgnoreCost;
    private boolean ignoreCost;

    // 策略集合
    private final Set<String> evictionBasedPolicies = new HashSet<>(Arrays.asList(
            "teven", "tevict", "tevict_ranges", "tevict_window", "optimal", "fixedttl", "to_keep", "ewma",
            "dynamicttl", "adaptive_tier", "milp_ec_new", "milp_ec_new_v3"));//注册新策略
    private final Set<String> otherEvictionPolicies = new HashSet<>(Arrays.asList(
            "lru", "always_evict", "pull_on_read", "replicate_all"));
    private final Set<String> spanstorePolicies = new HashSet<>(Arrays.asList("spanstore", "oracle"));

    // 时间与状态字段
    private Instant placementDecisionGeneratedTime = null;
    private final Duration updateTimeInterval = Duration.ofHours(1);
    private final Map<String, List<String>> placeDecisions = new HashMap<>();
    private boolean isUpdatingPlacement = false;
    private int evict = 0;
    // 对齐 Python 命名
    private final List<Integer> ttl_log = new ArrayList<>();
    private int good_choices = 0;
    private int total_choices = 1;
    private final List<Double> runtime_throughputs = new ArrayList<>();
    private int hits = 0;
    private int misses = 0;
    private double hits_size = 0;
    private double misses_size = 0;
    private Tracker tracker;
    protected PlacementPolicy placementPolicy;
    private TransferPolicy transferPolicy;

    // Missing fields from Python
    private double minNetworkCost;
    private double avgNetworkCost;
    private double medianNetworkCost;
    private int numVms;

    public SimulatorV2(String configPath,
            String tracePath,
            int numVms,
            boolean setBaseRegion,
            int days,
            boolean versionEnable,
            boolean storeDecision) {
        logger.setLevel(Level.SEVERE);
        this.config = org.example.src.utils.ConfigLoader.loadConfig(configPath); // 从YAML加载配置
        // Python artifact behavior is the default mainline.
        this.config.pythonCompat = true;
        this.tracePath = tracePath;
        this._printConfigDetails();

        this.storeDecision = storeDecision;
        this.setBaseRegion = setBaseRegion;
        this.versionEnable = versionEnable;
        this.daysToIgnoreCost = days;
        this.ignoreCost = days != 0;

        /*
         * Fixed-Base (FB) vs Free-Placement / Moving-Base (FP, setBaseRegion=false):
         * - config.fixedBaseRegion mirrors Python set_base_region; assignBaseRegion on write only when setBaseRegion
         *   (see request loop below).
         * - Read-path refresh_ttl + optimal eviction: non-base cache copy may be evicted; FP keeps last copy via
         *   expireImmediate (initiate_data_transfer).
         * - calculateTtl: TTL for copies to base vs non-base differs when fixedBaseRegion (see calculateTtl).
         * Transfer policies should not re-implement this split except for FB edge cases (expired base replica row).
         */
        if (this.setBaseRegion) {
            System.out.println("Fixed Base Region is set");
            this.config.fixedBaseRegion = true;
        } else {
            System.out.println("Fixed Base Region is not set");
            this.config.fixedBaseRegion = false;
        }

        // Initialize network cost metrics
        try {
            this.totalGraph = Helpers.makeNxGraph(null, null, null, null, numVms);
        } catch (IOException e) {
            throw new RuntimeException("Failed to initialize graph", e);
        }
        this.minNetworkCost = Helpers.getMinNetworkCost(this.totalGraph);
        this.avgNetworkCost = Helpers.getAvgNetworkCost(this.totalGraph);
        this.medianNetworkCost = Helpers.getMedianNetworkCost(this.totalGraph);

        this.numVms = numVms;
        this.tracker = new org.example.src.model.Tracker();
        this.tracker.setDayToIgnore(this.daysToIgnoreCost);

        this.regionManager = new org.example.src.model.RegionManager(this.totalGraph, logger);


        this.placementPolicy = _select_placement_policy(String.valueOf(config.placementPolicy));
        if (!spanstorePolicies.contains(String.valueOf(config.placementPolicy))) {
            this.transferPolicy = _select_transfer_policy(String.valueOf(config.transferPolicy), this.placementPolicy);
        } else {
            this.transferPolicy = null;
        }
    }

    private List<Map<String, Object>> detailsLog = new ArrayList<>();
    private boolean enableJsonDetail = Boolean.getBoolean("skystore.detailedCostLogging"); // Re-use this flag or use
                                                                                          
    public org.example.src.model.Tracker getTracker() {
        return this.tracker;
    }

    public SimulatorV2(String configPath,
            String tracePath,
            int numVms,
            boolean setBaseRegion,
            int days,
            boolean versionEnable) {
        this(configPath, tracePath, numVms, setBaseRegion, days, versionEnable, false);
    }

    private void _printConfigDetails() {
        String configStr = "------ Config Details ------\n" +
                "storage_region: " + (config.storageRegion) + "\n\n" +
                "placement_policy: " + (config.placementPolicy) + "\n\n" +
                "transfer_policy: " + (config.transferPolicy) + "\n\n" +
                "python_compat: " + (config.pythonCompat != null && config.pythonCompat) + "\n\n" +
                "window_size: " + (config.windowSize) + "\n\n" +
                "ttl: " + (config.cacheTtl) + "\n" +
                "---------------------------\n";
        logger.info(configStr);
    }

    // 下面实现对应 Python 第 174-541 行的传输处理逻辑
    // 注意：列表推导式与字典操作采用 Java 集合遍历与 Map API 实现
    // 保留注释并翻译为中文注释

    // 依赖的模型与管理器（需在其他处完成初始化）
    private DiGraph totalGraph;
    private RegionManager regionManager;
    private final Map<String, LogicalObject> logicalObjects = new HashMap<>();

    /** 扩展点：写请求时各 region 的数据量（EC 分片等）。默认 null 表示每 region 用完整 request.size。 */
    protected Map<String, BigDecimal> getPlacementSizesForWrite(Request req) {
        return null;
    }

    /** 扩展点：读请求的传输数据量（EC 时只需拉 1 片）。默认 null 表示用完整 request.size。 */
    protected BigDecimal getReadTransferSize(Request req) {
        return null;
    }

    /**
     * Extension hook: called immediately after a TTL value is computed.
     * Baseline V2 keeps no extra behavior.
     */
    protected void onTtlComputed(Request request) {
        // no-op
    }

    /**
     * Extension hook: called once per parsed request before read/write dispatch.
     * Baseline V2 keeps no extra behavior.
     */
    protected void onRequestReady(Request request) {
        // no-op
    }

    /**
     * 计算并应用传输，返回每次传输的耗时（毫秒）。
     * 等价于 Python 代码片段中对 transfer_graph 的处理与 TTL 刷新、对象放置。
     *
     * @param request        请求对象
     * @param transferGraphs 传输图列表（每个图代表一次传输）
     * @param placeRegions   放置区域列表（与传输图索引对应）
     * @param ttl            是否刷新 TTL
     * @param op             操作类型（"read" 或 "write"）
     * @return 传输时间列表（毫秒）
     */
    public List<Long> initiate_data_transfer(
            Instant current_timestamp,
            org.example.src.model.Request request,
            List<org.example.graph.DiGraph> transfer_graphs,
            List<String> place_regions,
            boolean ttl,
            String op,
            Set<String> cache_regions) {

        // Update past requests for tevict-like policies (Python-aligned: read-only).
        String pp = String.valueOf(config.placementPolicy);
        boolean shouldUpdatePast = (("tevict_ranges".equals(pp)
                || "tevict".equals(pp)
                || "tevict_window".equals(pp)
                || "milp_ec_new_v3".equals(pp))
                && "read".equals(op));
        if (shouldUpdatePast) {
            placementPolicy.updatePastRequests(request, request.issueRegion);
        }

        Map<String, BigDecimal> placementSizes = "write".equals(op) ? getPlacementSizesForWrite(request) : null;
        List<Long> transfer_times = new ArrayList<>();
        for (int i = 0; i < transfer_graphs.size(); i++) {//每个传输图一轮
           DiGraph transfer_graph = transfer_graphs.get(i);

            // Extract src nodes (nodes with src=True)
            List<String> src = new ArrayList<>();
            for (String node : transfer_graph.getNodes()) {
                Object srcAttr = transfer_graph.getNodeAttr(node, "src");
                if (srcAttr != null && Boolean.TRUE.equals(srcAttr)) {
                    src.add(node);
                }
            }

            // Extract dst nodes (nodes with dst=True)
            List<String> dsts = new ArrayList<>();
            for (String node : transfer_graph.getNodes()) {
                Object dstAttr = transfer_graph.getNodeAttr(node, "dst");
                if (dstAttr != null && Boolean.TRUE.equals(dstAttr)) {
                    dsts.add(node);
                }
            }

            if ("read".equals(op)) {
                request.readFrom = String.join(",", src);
            }

            if (src.size() != 1) {
                throw new IllegalStateException(
                        "Graphs look like: " + transfer_graph.getNodes() + ", src: " + src + ", dsts: " + dsts);
            }

            long transfer_time_millis = 0;
            for (String dst : dsts) {
                Number latency = (Number) transfer_graph.getEdgeAttr(src.get(0), dst, "latency");
                if (latency != null) {
                    transfer_time_millis = Math.max(transfer_time_millis, latency.longValue());
                }
            }
            logger.fine("transfer time (ms): " + transfer_time_millis);

            String schedule_place_region = (place_regions.size() > i) ? place_regions.get(i) : "";
            String srcNode = transfer_graph.edgesWithData().isEmpty() ? request.issueRegion
                    : transfer_graph.edgesWithData().get(0).src;

            String key = request.objKey;
            Map<String, PhysicalObject> physical_objects = logicalObjects
                    .get(key).physicalObjects;
            LogicalObject logical_object = logicalObjects.get(request.objKey);
            String physical_object_region = srcNode;

            boolean refresh_ttl = (ttl
                    && physical_objects.containsKey(srcNode)
                    && !srcNode.equals(logical_object.baseRegion)
                    && srcNode.equals(request.issueRegion)
                    && "read".equals(op)); // Only refresh on reads from the same region

            // Refresh TTL for the object in the cache
            if (refresh_ttl) {
                PhysicalObject physical_object = physical_objects.get(srcNode);
                physical_object.expireImmediate = false;

                if ("dynamicttl".equals(String.valueOf(config.placementPolicy))) {
                    placementPolicy.objectHit(key, request.issueRegion);
                }

                // Decide store or evict based on the policy
                if ("optimal".equals(String.valueOf(config.placementPolicy))) {
                    List<String> region_list = new ArrayList<>();
                    for (org.example.src.model.PhysicalObject obj : logicalObjects.get(key).physicalObjects.values()) {
                        if (!obj.getLocationTag().equals(request.issueRegion) && obj.ready(request.timestamp)) {
                            region_list.add(obj.getLocationTag());
                        }
                    }

                    double net_cost;
                    if (region_list.isEmpty()) {
                        // check that this object is located in issue_region
                        long count = logicalObjects.get(key).physicalObjects.values().stream()
                                .filter(obj -> obj.getLocationTag().equals(request.issueRegion))
                                .count();
                        if (count != 1) {
                            throw new IllegalStateException("Expected exactly one object in issue_region");
                        }
                        net_cost = this.medianNetworkCost;
                    } else {
                        String src_2 = region_list.stream()
                                .min((x, y) -> {
                                    Number costX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion, "cost");
                                    Number costY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion, "cost");
                                    int cmp = Double.compare(costX.doubleValue(), costY.doubleValue());
                                    if (cmp != 0)
                                        return cmp;
                                    Number latX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion, "latency");
                                    Number latY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion, "latency");
                                    return Double.compare(latX.doubleValue(), latY.doubleValue());
                                })
                                .get();
                        net_cost = ((Number) totalGraph.getEdgeAttr(src_2, request.issueRegion, "cost")).doubleValue();
                    }

                    // calculate `teven`
                    Number priceStorage = (Number) totalGraph.getNodeAttr(request.issueRegion, "priceStorage");
                    double teven = (net_cost / priceStorage.doubleValue()) * 60 * 60 * 24 / 3;

                    // if object is the base region or
                    // (the request is not the last request and `tnext` (cur `timestamp` - prev
                    // `timestamp`) < `teven`), then keep it in cache
                    if (request.nextAccessSameRegTimestamp != null) {
                        long tnext = Duration.between(request.timestamp, request.nextAccessSameRegTimestamp)
                                .getSeconds();
                        if (tnext >= teven) {
                            logger.fine("remove from cache");
                            if (config.fixedBaseRegion) {
                                // fixed base region case - always evict
                                regionManager.removeObjectFromRegion(physical_object_region, physical_object,
                                        request.timestamp);
                                logicalObjects.get(key).physicalObjects.remove(physical_object_region);
                            } else {
                                // no base region - only remove if there are more than one object
                                if (logicalObjects.get(key).physicalObjects.size() > 1) {
                                    regionManager.removeObjectFromRegion(physical_object_region, physical_object,
                                            request.timestamp);
                                    logicalObjects.get(key).physicalObjects.remove(physical_object_region);
                                } else {
                                    physical_object.expireImmediate = true;
                                    physical_object.setTtl(-1);
                                }
                            }
                        }
                    }
                } else {
                    // everything else but optimal
                    if ("to_keep".equals(String.valueOf(config.placementPolicy))
                            || "ewma".equals(String.valueOf(config.placementPolicy))) {

                        // double estimated_recency = placementPolicy.estimate_arrival_recency(srcNode,
                        // key);
                        double estimated_recency = 0; // placeholder

                        List<String> region_list = new ArrayList<>();
                        for (org.example.src.model.PhysicalObject obj : logicalObjects.get(key).physicalObjects
                                .values()) {
                            if (!obj.getLocationTag().equals(request.issueRegion) && obj.ready(request.timestamp)) {
                                region_list.add(obj.getLocationTag());
                            }
                        }

                        double net_cost;
                        if (region_list.isEmpty()) {
                            net_cost = this.medianNetworkCost;
                        } else {
                            String src_2 = region_list.stream()
                                    .min((x, y) -> {
                                        Number costX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion, "cost");
                                        Number costY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion, "cost");
                                        int cmp = Double.compare(costX.doubleValue(), costY.doubleValue());
                                        if (cmp != 0)
                                            return cmp;
                                        Number latX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion,
                                                "latency");
                                        Number latY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion,
                                                "latency");
                                        return Double.compare(latX.doubleValue(), latY.doubleValue());
                                    })
                                    .get();
                            net_cost = ((Number) totalGraph.getEdgeAttr(src_2, request.issueRegion, "cost"))
                                    .doubleValue();
                        }

                        Number priceStorage = (Number) totalGraph.getNodeAttr(request.issueRegion, "priceStorage");
                        double teven = (net_cost / priceStorage.doubleValue()) * 60 * 60 * 24 / 3;

                        // double factor = placementPolicy.factor;
                        double factor = 1.0; // placeholder
                        if (estimated_recency * factor <= teven) {
                            // refresh case
                            int new_ttl = (int) Math.round(Math.min(estimated_recency * factor, teven));

                            long storageDuration = Duration
                                    .between(physical_object.getStorageStartTime(), request.timestamp).getSeconds();
                            physical_object.setTtl((int) (new_ttl + storageDuration));

                            if (request.nextAccessSameRegTimestamp != null) {
                                long timeToNext = Duration
                                        .between(request.timestamp, request.nextAccessSameRegTimestamp).getSeconds();
                                if (new_ttl >= timeToNext) {
                                    this.good_choices += 1;
                                }
                            }
                            this.total_choices += 1;
                            this.ttl_log.add(new_ttl);
                        } else {
                            if (request.nextAccessSameRegTimestamp != null) {
                                long timeToNext = Duration
                                        .between(request.timestamp, request.nextAccessSameRegTimestamp).getSeconds();
                                if (teven < timeToNext) {
                                    this.good_choices += 1;
                                }
                            }
                            this.total_choices += 1;
                        }

                    } else {
                        // Simulate a cache hit: Handle refresh TTL for other eviction policies (teven,
                        // tevict, fixedttl)
                        List<String> evictionPolicies = Arrays.asList("teven", "tevict", "tevict_ranges",
                                "tevict_window", "dynamicttl", "milp_ec_new_v3");
                        if (evictionPolicies.contains(String.valueOf(config.placementPolicy))) {
                            List<String> region_list = new ArrayList<>();
                            for (org.example.src.model.PhysicalObject obj : logicalObjects.get(key).physicalObjects
                                    .values()) {
                                if (!obj.getLocationTag().equals(request.issueRegion) && obj.ready(request.timestamp)) {
                                    region_list.add(obj.getLocationTag());
                                }
                            }

                            double net_cost;
                            if (region_list.isEmpty()) {
                                net_cost = this.medianNetworkCost;
                            } else {
                                String src_2 = region_list.stream()
                                        .min((x, y) -> {
                                            Number costX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion,
                                                    "cost");
                                            Number costY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion,
                                                    "cost");
                                            int cmp = Double.compare(costX.doubleValue(), costY.doubleValue());
                                            if (cmp != 0)
                                                return cmp;
                                            Number latX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion,
                                                    "latency");
                                            Number latY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion,
                                                    "latency");
                                            return Double.compare(latX.doubleValue(), latY.doubleValue());
                                        })
                                        .get();
                                net_cost = ((Number) totalGraph.getEdgeAttr(src_2, request.issueRegion, "cost"))
                                        .doubleValue();
                            }

                            Number priceStorageNum = (Number) totalGraph.getNodeAttr(request.issueRegion,
                                    "priceStorage");
                            if (priceStorageNum == null || priceStorageNum.doubleValue() <= 0)
                                throw new IllegalStateException(
                                    "[SimulatorV2] priceStorage unavailable for '" + request.issueRegion + "': " + priceStorageNum);
                            double priceStorageVal = priceStorageNum.doubleValue();

                            double ttlValue = (net_cost / priceStorageVal) * 60 * 60 * 24 / 3;

                            if ("tevict".equals(String.valueOf(config.placementPolicy))
                                    || "tevict_ranges".equals(String.valueOf(config.placementPolicy))
                                    || "tevict_window".equals(String.valueOf(config.placementPolicy))
                                    || "milp_ec_new_v3".equals(String.valueOf(config.placementPolicy))) {
                                if (!region_list.isEmpty()) {
                                    String read_region = null;
                                    Double set_ttl = null;
                                    for (String read_r : region_list) {
                                        double temp_ttl = placementPolicy.getTevict(read_r, request.issueRegion,
                                                request.timestamp);
                                        org.example.src.model.PhysicalObject phys = logicalObjects
                                                .get(request.objKey).physicalObjects.get(read_r);
                                        if (read_region == null
                                                || phys.getTtl() == -1
                                                || (temp_ttl < set_ttl
                                                        && request.timestamp.plus(Duration.ofSeconds((long) temp_ttl))
                                                                .isBefore(phys.getStorageStartTime()
                                                                        .plus(Duration.ofSeconds(phys.getTtl()))))) {
                                            read_region = read_r;
                                            set_ttl = temp_ttl;
                                        }
                                    }
                                    ttlValue = set_ttl;
                                } else if ("milp_ec_new_v3".equals(String.valueOf(config.placementPolicy))) {
                                    double ecTtl = placementPolicy.getTevict(physical_object_region,
                                            request.issueRegion, request.timestamp);
                                    if (ecTtl > 0) {
                                        ttlValue = ecTtl;
                                    }
                                }
                            } else if ("dynamicttl".equals(String.valueOf(config.placementPolicy))) {
                                ttlValue = placementPolicy.getTtl(request.issueRegion, ttlValue);
                            }

                            // Extension hook right after TTL computed.
                            onTtlComputed(request);

                            int ttl_rounded = (int) Math.round(ttlValue);
                            long storageDuration = Duration
                                    .between(physical_object.getStorageStartTime(), request.timestamp).getSeconds();
                            physical_object.setTtl((int) (ttl_rounded + storageDuration));
                        }
                    }
                }
            }

            // Schedule the completion of the transfer after the transfer time
            if (!schedule_place_region.isEmpty() && cache_regions.contains(schedule_place_region)) {
                // Handle overwrite for WRITE operations: if object exists, remove it to
                // finalize cost and allow new version
                if ("write".equals(op)
                        && logicalObjects.get(request.objKey).physicalObjects.containsKey(schedule_place_region)) {
                    org.example.src.model.PhysicalObject oldObj = logicalObjects.get(request.objKey).physicalObjects
                            .get(schedule_place_region);
                    // Remove old object at the time the new one would start (or roughly now)
                    regionManager.removeObjectFromRegion(schedule_place_region, oldObj,
                            current_timestamp.plusMillis(transfer_time_millis));
                    logicalObjects.get(request.objKey).physicalObjects.remove(schedule_place_region);
                }

                if (!logicalObjects.get(request.objKey).physicalObjects.containsKey(schedule_place_region)) {
                    if (!config.fixedBaseRegion
                            && logicalObjects.get(request.objKey).physicalObjects.size() == 1) {
                        for (Map.Entry<String, org.example.src.model.PhysicalObject> entry : logicalObjects
                                .get(request.objKey).physicalObjects.entrySet()) {
                            String region = entry.getKey();
                            org.example.src.model.PhysicalObject obj = entry.getValue();
                            // ttl is currently set to expire at the current timestamp.
                            org.example.src.model.PhysicalObject phys_obj = logicalObjects
                                    .get(request.objKey).physicalObjects.get(region);
                            if (phys_obj.expireImmediate) {
                                long seconds = Duration.between(obj.getStorageStartTime(), current_timestamp)
                                        .getSeconds();
                                phys_obj.setTtl((int) (seconds + transfer_time_millis / 1000));
                                phys_obj.expireImmediate = false;
                            }
                        }
                    }

                    int obj_ttl = calculateTtl(request, current_timestamp, srcNode, schedule_place_region);
                    BigDecimal objSize = (placementSizes != null && placementSizes.containsKey(schedule_place_region))
                            ? placementSizes.get(schedule_place_region) : request.size;
                    org.example.src.model.PhysicalObject phys_object = new org.example.src.model.PhysicalObject(
                            schedule_place_region,
                            request.objKey,
                            objSize,
                            obj_ttl,
                            logicalObjects.get(request.objKey),
                            org.example.src.model.Status.ready,
                            0);
                    phys_object.setStorageStartTime(current_timestamp.plusMillis(transfer_time_millis));
                    logicalObjects.get(request.objKey).physicalObjects.put(schedule_place_region, phys_object);
                    regionManager.addObjectToRegion(schedule_place_region, phys_object);
                }
            }

            transfer_times.add(transfer_time_millis);
        }

        return transfer_times;
    }

    private int calculateTtl(Request request,
            Instant currentTimestamp,
            String source,
            String dst) {
        String obj_key = request.objKey;
        int ttl = -1;
//读取当前的请求 ，当前的时间戳  srcreg,dstreg
if (!config.fixedBaseRegion || !dst.equals(logicalObjects.get(obj_key).baseRegion)) {//不是fB模式或是srcreg！=dstreg时
            if ("optimal".equals(String.valueOf(config.placementPolicy))) {
                ttl = Integer.MAX_VALUE; // float('inf')

            } else if ("teven".equals(String.valueOf(config.placementPolicy))) {
                Number costNum = (Number) totalGraph.getEdgeAttr(source, dst, "cost");
                double net_cost = costNum != null ? costNum.doubleValue() : 0;
                // for moving `base_region` case, make sure N is not zero because N/S=0 => ttl=0
                if (!config.fixedBaseRegion && net_cost == 0) {
                    net_cost = this.medianNetworkCost;
                }
                Number priceStorage = (Number) totalGraph.getNodeAttr(dst, "priceStorage");
                ttl = (int) Math.round((net_cost / priceStorage.doubleValue()) * 60 * 60 * 24 / 3);

                // self.tevens.append(ttl / 3600)

            } else if ("tevict".equals(String.valueOf(config.placementPolicy))
                    || "tevict_ranges".equals(String.valueOf(config.placementPolicy))
                    || "tevict_window".equals(String.valueOf(config.placementPolicy))
                    || "hystore_ec".equals(String.valueOf(config.placementPolicy))
                    || "hystore_lrc".equals(String.valueOf(config.placementPolicy))
                    || "milp_ec_new".equals(String.valueOf(config.placementPolicy))
                    || "milp_ec_new_v3".equals(String.valueOf(config.placementPolicy))) {//TTL注册新策略

                List<String> region_list = new ArrayList<>();
                for (PhysicalObject obj : logicalObjects.get(obj_key).physicalObjects.values()) {
                    if (!obj.getLocationTag().equals(request.issueRegion) && obj.ready(request.timestamp)) {
                        region_list.add(obj.getLocationTag());
                    }
                }//收集issueregion以外已有副本的region

                if (!region_list.isEmpty()) {
                }

                double net_cost;
                if (region_list.isEmpty()) {//region_list为空时，使用中位数网络成本
                    net_cost = this.medianNetworkCost;
                } else {
                    String src_2 = region_list.stream()//region_list不为空时，使用最小成本节点
                            .min((x, y) -> {
                                Number costX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion, "cost");
                                Number costY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion, "cost");
                                int cmp = Double.compare(costX.doubleValue(), costY.doubleValue());
                                if (cmp != 0)
                                    return cmp;
                                Number latX = (Number) totalGraph.getEdgeAttr(x, request.issueRegion, "latency");
                                Number latY = (Number) totalGraph.getEdgeAttr(y, request.issueRegion, "latency");
                                return Double.compare(latX.doubleValue(), latY.doubleValue());
                            })
                            .get();
                    net_cost = ((Number) totalGraph.getEdgeAttr(src_2, request.issueRegion, "cost")).doubleValue();
                }

                Number priceStorageNum = (Number) totalGraph.getNodeAttr(request.issueRegion, "priceStorage");
                double priceStorageVal = priceStorageNum.doubleValue();

                double ttlValue = (net_cost / priceStorageVal) * 60 * 60 * 24 / 3;

                // milp_ec_new_v3: on write path only — when 12h pair-TTL histogram advances, solve MILP so coeffs match TTL.
                if ("milp_ec_new_v3".equals(String.valueOf(config.placementPolicy))
                        && placementPolicy instanceof PolicyMilpECnewV3) {
                    PolicyMilpECnewV3 v3 =
                            (PolicyMilpECnewV3) placementPolicy;
                    if (region_list.isEmpty()) {
                        v3.syncMilpIfHistogramTtlAdvanced(request, dst);
                    } else {
                        v3.syncMilpIfHistogramTtlAdvanced(request, request.issueRegion);
                    }
                }

                // milp_ec_new / milp_ec_new_v3: region_list 为空时（r0 分片首次写），也通过 getTevict 获取 MILP 统一 TTL
                if (("milp_ec_new".equals(String.valueOf(config.placementPolicy))
                        || "milp_ec_new_v3".equals(String.valueOf(config.placementPolicy)))
                        && region_list.isEmpty()) {
                    double ecTtl = placementPolicy.getTevict(source, dst, request.timestamp);
                    if (ecTtl > 0) ttlValue = ecTtl;
                }

                if (!region_list.isEmpty()) {
                    String read_region = null;
                    Double set_ttl = null;
                    for (String read_r : region_list) {
                        double temp_ttl = placementPolicy.getTevict(read_r, request.issueRegion, request.timestamp);
                        org.example.src.model.PhysicalObject phys = logicalObjects.get(obj_key).physicalObjects
                                .get(read_r);
                        if (read_region == null
                                || phys.getTtl() == -1
                                || (temp_ttl < set_ttl
                                        && request.timestamp.plus(Duration.ofSeconds((long) temp_ttl))
                                                .isBefore(phys.getStorageStartTime()
                                                        .plus(Duration.ofSeconds(phys.getTtl()))))) {
                            read_region = read_r;
                            set_ttl = temp_ttl;
                        }
                    }
                    ttlValue = set_ttl;
                }

                // Extension hook right after TTL computed.
                onTtlComputed(request);

                ttl = (int) Math.round(ttlValue);
                // self.tevens.append(ttl / 3600)

            } else if ("fixedttl".equals(String.valueOf(config.placementPolicy))) {
                ttl = (int) Math.round(config.cacheTtl * 60 * 60); // translate cache_ttl from hours to seconds

            } else if ("dynamicttl".equals(String.valueOf(config.placementPolicy))) {
                Number costNum = (Number) totalGraph.getEdgeAttr(source, dst, "cost");
                double net_cost = costNum != null ? costNum.doubleValue() : 0;
                // for moving `base_region` case, make sure N is not zero because N/S=0 => ttl=0
                if (!config.fixedBaseRegion && net_cost == 0) {
                    net_cost = this.medianNetworkCost;
                }

                Number priceStorage = (Number) totalGraph.getNodeAttr(dst, "priceStorage");
                double teven = (net_cost / priceStorage.doubleValue()) * 60 * 60 * 24 / 3;
                double finalTtl = placementPolicy.getTtl(request.issueRegion, teven);
                ttl = (int) Math.round(finalTtl);

                if (org.example.src.utils.Logger.detailedStorage) {
                    org.example.src.utils.Logger.info(String.format(
                            "[TTL-CALC] Req=%s Src=%s Dst=%s NetCost=%.9f (Median=%.9f) Price=%.9f Teven=%.4f FinalTTL=%.4f",
                            obj_key, source, dst, net_cost, this.medianNetworkCost, priceStorage.doubleValue(), teven,
                            finalTtl));
                }

            } else if ("to_keep".equals(String.valueOf(config.placementPolicy))
                    || "ewma".equals(String.valueOf(config.placementPolicy))) {
                Number costNum = (Number) totalGraph.getEdgeAttr(source, dst, "cost");
                double net_cost = costNum != null ? costNum.doubleValue() : 0;
                if (!config.fixedBaseRegion && net_cost == 0) {
                    net_cost = this.medianNetworkCost;
                }
                double estimated_recency = placementPolicy.estimateArrivalRecency(dst, obj_key);
                double factor = placementPolicy.factor;
                Number priceStorage = (Number) totalGraph.getNodeAttr(dst, "priceStorage");
                double teven = (net_cost / priceStorage.doubleValue()) * 60 * 60 * 24 / 3;
                ttl = (int) Math.round(Math.min(estimated_recency * factor, teven));

                if (request.nextAccessSameRegTimestamp != null) {
                    long timeToNext = Duration.between(request.timestamp, request.nextAccessSameRegTimestamp)
                            .getSeconds();
                    if (ttl >= timeToNext) {
                        this.good_choices += 1;
                    }
                }
                this.total_choices += 1;

            } else if ("pull_on_read".equals(String.valueOf(config.placementPolicy))) {
                ttl = -1;

            } else if ("always_evict".equals(String.valueOf(config.placementPolicy))) {
                ttl = 0;

            } else if ("replicate_all".equals(String.valueOf(config.placementPolicy))) {
                ttl = -1;

            } else {
                throw new IllegalArgumentException("Invalid placement policy type");
            }
        }

        return ttl;
    }

    public List<String> get_placements(Request request) {
        String obj_key = request.objKey;
        List<String> place_regions = new ArrayList<>();

        // Simulate policy decision
        if (spanstorePolicies.contains(String.valueOf(config.placementPolicy))) {
            // SPANStore: update placement decisions
            place_regions = placementPolicy.place(obj_key);
        } else {
            // Call placement policy place method
            place_regions = placementPolicy.place(request, config);
        }

        logger.fine("Place regions: " + place_regions);
        return place_regions;
    }

    // Python 736-735：run() 的前半段准备与请求解析
    public void run() {
        logger.info("Running simulations...");

        List<org.example.graph.DiGraph> readGraphs = new ArrayList<>();
        List<org.example.graph.DiGraph> writeGraphs = new ArrayList<>();
        Instant startTimestamp = null, endTimestamp = null;
        int numLines = countLines(Helpers.getFullPath(this.tracePath));

        if (this.versionEnable) {
            versionateTrace(this.tracePath, this.tracePath + "-versioned");
            this.tracePath = this.tracePath + "-versioned";
        }

        prefillMilpDatasetEndEpochFromTraceIfApplicable();

        File in = new File(Helpers.getFullPath(this.tracePath));
        File out = new File(
                Helpers.getFullPath(this.tracePath) + "." + String.valueOf(config.placementPolicy) + ".prototype");

        try (BufferedReader br = new BufferedReader(new FileReader(in));
                BufferedWriter bw = new BufferedWriter(new FileWriter(out))) {
            String header = br.readLine();
            System.out.println("DEBUG: CSV Header: " + header);
            String[] headerCols = header.split(",");
            System.out.println("DEBUG: Header columns count: " + headerCols.length);
            for (int i = 0; i < headerCols.length; i++) {//打印表头调试用
                System.out.println("  Column " + i + ": " + headerCols[i]);
            }
            List<String> fieldnames = new ArrayList<>(Arrays.asList(headerCols));
            fieldnames.add("answer_region");
            List<String> reduced = new ArrayList<>();
            for (int i = 0; i < Math.min(5, fieldnames.size()); i++)//缩短字段名调试用
                reduced.add(fieldnames.get(i));
            if (fieldnames.size() > 7)
                reduced.add(fieldnames.get(7));
            fieldnames = reduced;

            String line;
            int processedLines = 0;
            int skippedLines = 0;
            while ((line = br.readLine()) != null) {
                processedLines++;
                boolean logAllRequests = Boolean.getBoolean("skystore.logAllRequests");
                boolean shouldLog = logAllRequests || processedLines <= 100 || processedLines > numLines - 100;
                org.example.src.utils.Logger.detailedStorage = shouldLog;

                if (processedLines <= 5) {
                    System.out.println("DEBUG: Processing line " + processedLines + ": "
                            + line.substring(0, Math.min(100, line.length())));
                }
                readGraphs = new ArrayList<>();
                writeGraphs = new ArrayList<>();
                org.example.graph.DiGraph writeTransferGraph = null;
                // double runtime = 0, latency = 0, throughput = 0, cost = 0; // Removed unused
                // variables
                String readRegion = "";

                Map<String, String> row = parseCsvRow(headerCols, line);
                if (processedLines <= 3) {
                    System.out.println("DEBUG: Parsed row keys: " + row.keySet());
                    System.out.println("DEBUG: op=" + row.get("op") + ", obj_key=" + row.get("obj_key"));
                }

                String timestampStr = row.get("timestamp");
                Instant requestTimestamp;
                String normalized = timestampStr == null ? ""
                        : timestampStr.replace("-", "").replace(":", "").replace(" ", "");
                if (!normalized.isEmpty() && normalized.chars().allMatch(Character::isDigit)) {//时间戳分析
                    requestTimestamp = Instant.ofEpochMilli(Long.parseLong(timestampStr));
                } else {
                    requestTimestamp = parseIsoMillis(timestampStr);
                }

                Map<String, Object> requestData = new HashMap<>();
                for (Map.Entry<String, String> e : row.entrySet())
                    requestData.put(e.getKey(), e.getValue());
                requestData.put("timestamp", requestTimestamp);//组装请求数据 时间戳字段，请求时间戳对象

                String op = (String) requestData.get("op");
                if ("GET".equals(op) || "REST.GET.OBJECT".equals(op)) {
                    requestData.put("op", "read");
                    if (processedLines <= 3)
                        System.out.println("DEBUG: Converted to READ operation");
                } else if ("PUT".equals(op) || "REST.PUT.OBJECT".equals(op)) {
                    requestData.put("op", "write");
                    if (processedLines <= 3)
                        System.out.println("DEBUG: Converted to WRITE operation");
                } else {
                    if (processedLines <= 10)
                        System.out.println("DEBUG: Skipping unknown op: " + op);
                    skippedLines++;
                    continue; // 其他操作跳过
                }

                String nextAccessStr = row.get("time_to_next_access");
                Instant nextAccessTimestamp;
                String norm2 = nextAccessStr == null ? ""
                        : nextAccessStr.replace("-", "").replace(":", "").replace(" ", "");
                if (!norm2.isEmpty() && norm2.chars().allMatch(Character::isDigit)) {
                    nextAccessTimestamp = Instant.ofEpochMilli(Long.parseLong(nextAccessStr));
                } else {
                    nextAccessTimestamp = parseIsoMillis(nextAccessStr);
                }
                requestData.put("next_access_timestamp", nextAccessTimestamp);

                String nextAccessSameRegStr = row.get("time_to_next_access_same_reg");
                if (nextAccessSameRegStr != null && !"-1".equals(nextAccessSameRegStr)) {//同区域下次访问时间字段分析
                    String norm3 = nextAccessSameRegStr.replace("-", "").replace(":", "").replace(" ", "");
                    Instant nextAccessSameRegTimestamp;
                    if (!norm3.isEmpty() && norm3.chars().allMatch(Character::isDigit)) {
                        nextAccessSameRegTimestamp = Instant.ofEpochMilli(Long.parseLong(nextAccessSameRegStr));
                    } else {
                        nextAccessSameRegTimestamp = parseIsoMillis(nextAccessSameRegStr);
                    }
                    requestData.put("time_to_next_access_same_reg", nextAccessSameRegTimestamp);
                }
                // 构造 Request 对象
                Request req = new org.example.src.model.Request();
                req.timestamp = requestTimestamp;
                req.op = (String) requestData.get("op");
                req.issueRegion = row.get("issue_region");
                req.objKey = row.get("obj_key");
                try {
                    req.size = new BigDecimal(row.getOrDefault("size", "0"));
                } catch (Exception e) {
                    req.size = BigDecimal.ZERO;
                }
                if (requestData.get("next_access_timestamp") instanceof Instant) {
                    req.nextAccessTimestamp = (Instant) requestData.get("next_access_timestamp");
                }
                Object nasr = requestData.get("time_to_next_access_same_reg");
                if (nasr instanceof Instant)
                    req.nextAccessSameRegTimestamp = (Instant) nasr;

                String obj_key = req.objKey;

                if (startTimestamp == null) {
                    startTimestamp = req.timestamp;
                    this.regionManager.setStartTimeAndIgnoredDays(startTimestamp, this.daysToIgnoreCost);
                }

                long current_day = Duration.between(startTimestamp, req.timestamp).toDays();
                if (current_day >= this.daysToIgnoreCost) {
                    this.ignoreCost = false;
                }

                // Create logical object if it doesn't exist
                if (!this.logicalObjects.containsKey(obj_key)) {
                    org.example.src.model.LogicalObject logical_obj;
                    if ("adaptive_tier".equals(String.valueOf(config.placementPolicy))) {
                        logical_obj = new org.example.src.model.LogicalObjectV2(
                                obj_key, req.size, req.timestamp, org.example.src.model.Status.ready);
                    } else {
                        logical_obj = new org.example.src.model.LogicalObject(
                                obj_key, req.size, req.timestamp, org.example.src.model.Status.ready);
                    }
                    this.logicalObjects.put(obj_key, logical_obj);
                } else {
                    // Update access time of the object if accessed again (e.g. LRU)
                    this.logicalObjects.get(obj_key).setLastModified(req.timestamp);
                }

                // Extension hook for policy-specific per-request timing.
                // Must run after logical object is guaranteed to exist.
                onRequestReady(req);

                // Track request size for metrics
                this.tracker.addRequestSize(req.size);

                // Detailed logging for first/last windows, or full stream when enabled.
                int currentReqIndex = this.tracker.numReq;
                if (shouldLog) {
                    org.example.src.utils.Logger.info(String.format(
                            "REQ #%d: %s %s size=%s region=%s",
                            currentReqIndex, req.op, req.objKey, req.size, req.issueRegion));
                }

                // Set base region: to local if write
                if ("write".equals(req.op)
                        && this.setBaseRegion
                        && (evictionBasedPolicies.contains(String.valueOf(config.placementPolicy))
                                || otherEvictionPolicies.contains(String.valueOf(config.placementPolicy)))) {
                    this.logicalObjects.get(obj_key).assignBaseRegion(req.issueRegion);
                }

                // 选择策略：SPANStore/Oracle 使用 placement_policy，否则使用 transfer_policy
                Object policy = (spanstorePolicies.contains(String.valueOf(config.placementPolicy))) ? placementPolicy
                        : transferPolicy;//选择策略：SPANStore/Oracle 使用 placement_policy，否则使用 transfer_policy

                if ("read".equals(req.op)) {
                    TransferPolicy.ReadPathResult rp = (transferPolicy != null)
                            ? transferPolicy.readTransferPath(req)
                            : null;
                    if (rp != null) {
                        readRegion = rp.src;
                        List<org.example.graph.DiGraph> readGraphList = rp.graphs;

                        if (!readRegion.equals(req.issueRegion)) {
                            this.misses += 1;
                            this.misses_size += req.size
                                    .divide(BigDecimal.valueOf(org.example.src.utils.Definitions.GB), 20,
                                            RoundingMode.HALF_UP)
                                    .doubleValue();
                        } else {
                            this.hits += 1;
                            this.hits_size += req.size.divide(BigDecimal.valueOf(org.example.src.utils.Definitions.GB),
                                    20, RoundingMode.HALF_UP).doubleValue();
                        }
                        for (org.example.graph.DiGraph g : readGraphList) {
                            readGraphs.add(g);//多读图？？？
                        }

                        List<String> place_regions = get_placements(req);

                        // Schedule placed region at the same time
                        List<String> cacheRegionsList = place_regions.contains(req.issueRegion)
                                ? java.util.Collections.singletonList(req.issueRegion)
                                : java.util.Collections.emptyList();
                        List<Long> transfer_time = initiate_data_transfer(
                                req.timestamp,
                                req,
                                readGraphList,
                                cacheRegionsList,
                                evictionBasedPolicies.contains(String.valueOf(config.placementPolicy)),
                                "read",
                                new HashSet<>(place_regions));

                        // Update read metrics to tracker (support multi-graph for EC 2-shard fetch)
                        double transferCost = 0.0;
                        double maxLatency = 0.0;
                        double minThroughput = Double.MAX_VALUE;
                        double maxTputRuntime = 0.0;
                        String debugSrc = "N/A";
                        for (int gi = 0; gi < readGraphList.size(); gi++) {//按分段算延迟/吞吐/费用
                            org.example.graph.DiGraph g = readGraphList.get(gi);
                            BigDecimal segSize = (gi < rp.segmentSizes.size()) ? rp.segmentSizes.get(gi)
                                    : getReadTransferSize(req);
                            TransferMetrics metrics = _updateTransferMetric(g, req, segSize);
                            maxLatency = Math.max(maxLatency, metrics.latency);
                            minThroughput = Math.min(minThroughput, metrics.throughput);
                            maxTputRuntime = Math.max(maxTputRuntime, metrics.throughputRuntime);
                            if (!this.ignoreCost) {
                                this.tracker.addTransferCost(metrics.cost);
                                transferCost += metrics.cost;
                            }
                            if (shouldLog && g != null) {
                                for (String node : g.getNodes()) {//找src节点打日志
                                    if (Boolean.TRUE.equals(g.getNodeAttrs(node).get("src"))) {
                                        debugSrc = node;
                                        break;
                                    }
                                }
                            }
                        }
                        this.tracker.addLatency("read", maxLatency);
                        this.tracker.addThroughput("read", (minThroughput < Double.MAX_VALUE ? minThroughput : 0) / this.numVms);
                        this.tracker.addTputRuntime("read", maxTputRuntime);
                        if (shouldLog && !this.ignoreCost) {
                            org.example.src.utils.Logger.info(String.format(
                                    "REQ #%d [READ-NET] src=%s cost=$%.9f",
                                    currentReqIndex, debugSrc, transferCost));
                        }
                    }
                } else if ("write".equals(req.op)) {
                    List<String> place_regions = get_placements(req);
                    Map<String, BigDecimal> placementSizes = getPlacementSizesForWrite(req);
                    for (String region : place_regions) {
                        writeTransferGraph = (transferPolicy != null)
                                ? transferPolicy
                                        .writeTransferPath(req, region)
                                : null;
                        if (writeTransferGraph != null) {
                            writeGraphs.add(writeTransferGraph);
                        }
                    }

                    if (!writeGraphs.isEmpty()) {
                        List<org.example.graph.DiGraph> relevantWriteGraphs = writeGraphs.subList(
                                Math.max(0, writeGraphs.size() - place_regions.size()), writeGraphs.size());
                        initiate_data_transfer(
                                req.timestamp,
                                req,
                                relevantWriteGraphs,
                                place_regions,
                                evictionBasedPolicies.contains(String.valueOf(config.placementPolicy)),
                                "write",
                                new HashSet<>(place_regions));

                        // Update write metrics to tracker
                        if (!writeGraphs.isEmpty()) {
                            // Calculate metrics for each write graph and take minimum
                            List<Double> latencies = new ArrayList<>();
                            List<Double> runtimes = new ArrayList<>();
                            List<Double> throughputs = new ArrayList<>();

                            for (int wi = 0; wi < relevantWriteGraphs.size(); wi++) {
                                org.example.graph.DiGraph writeGraph = relevantWriteGraphs.get(wi);
                                BigDecimal transferSize = (placementSizes != null && wi < place_regions.size()
                                        && placementSizes.containsKey(place_regions.get(wi)))
                                        ? placementSizes.get(place_regions.get(wi)) : null;
                                TransferMetrics metrics = _updateTransferMetric(writeGraph, req, transferSize);
                                latencies.add(metrics.latency);
                                runtimes.add(metrics.throughputRuntime);
                                throughputs.add(metrics.throughput);
                                if (!this.ignoreCost) {
                                    this.tracker.addTransferCost(metrics.cost);
                                    if (shouldLog) {
                                        String debugDst = "N/A";
                                        if (writeGraph != null) {
                                            for (String node : writeGraph.getNodes()) {
                                                if (Boolean.TRUE.equals(writeGraph.getNodeAttrs(node).get("dst"))) {
                                                    debugDst = node;
                                                    break;
                                                }
                                            }
                                        }
                                        org.example.src.utils.Logger.info(String.format(
                                                "REQ #%d [WRITE-NET] dst=%s cost=$%.9f",
                                                currentReqIndex, debugDst, metrics.cost));
                                    }
                                    // BigDecimal.valueOf(org.example.src.utils.Definitions.GB), 20,
                                    // RoundingMode.HALF_UP);

                                    // Extract src and dst from transfer graph
                                    // String src = "N/A";
                                    // String dst = "N/A";
                                    if (writeGraph != null) {
                                        for (String node : writeGraph.getNodes()) {
                                            Boolean isSrc = (Boolean) writeGraph.getNodeAttrs(node).get("src");
                                            Boolean isDst = (Boolean) writeGraph.getNodeAttrs(node).get("dst");
                                            if (Boolean.TRUE.equals(isSrc)) {
                                                // src = node;
                                            }
                                            if (Boolean.TRUE.equals(isDst)) {
                                                // dst = node;
                                                break; // Usually one dst
                                            }
                                        }
                                    }

                                    // Console output disabled for performance - check log file instead
                                    // System.out.println(String.format(
                                    // "[NET-COST-WRITE] Obj=%s, Src=%s, Dst=%s, Size=%.6f GB, TransferCost=$%.12f",
                                    // req.objKey, src, dst, sizeGB, metrics.cost));

                                    // Write to log file
                                    // org.example.src.utils.CostLogger.logNetworkCost(
                                    // "write", req.objKey, src, dst, sizeGB,
                                    // java.math.BigDecimal.valueOf(metrics.cost));
                                }
                            }

                            // Use minimum values for write metrics (Python behavior)
                            if (!latencies.isEmpty()) {
                                double minLatency = latencies.stream().min(Double::compare).orElse(0.0);
                                this.tracker.addLatency("write", minLatency);
                                this.tracker.addTputRuntime("write",
                                        runtimes.stream().min(Double::compare).orElse(0.0));
                                // Write throughput from self-loop is already correct (10 Gbps)
                                this.tracker.addThroughput("write",
                                        throughputs.stream().min(Double::compare).orElse(0.0));
                            }
                        }
                    }
                }

                // 更新时间范围
                endTimestamp = req.timestamp;

                // 写出占位的结果行（answer_region）
                String answerRegion = readRegion;
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < fieldnames.size(); i++) {
                    if (i > 0)
                        sb.append(',');
                    String col = fieldnames.get(i);
                    if ("answer_region".equals(col))
                        sb.append(answerRegion == null ? "" : answerRegion);
                    else
                        sb.append(row.getOrDefault(col, ""));
                }
                bw.write(sb.toString());
                bw.newLine();
                // 结束单条请求处理
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }

        // 处理尾部统计与成本计算（对应 1098 以及后续打印）
        if (startTimestamp != null && endTimestamp != null) {
            Duration remainedProcessTime = Duration.ofSeconds(50);
            Duration duration = remainedProcessTime.plus(Duration.between(startTimestamp, endTimestamp));
            if (this.tracker == null)
                this.tracker = new org.example.src.model.Tracker();
            // Format duration as "X days, HH:MM:SS.microseconds" to match Python
            long totalSeconds = duration.getSeconds();
            long days = totalSeconds / 86400;
            long hours = (totalSeconds % 86400) / 3600;
            long minutes = (totalSeconds % 3600) / 60;
            long seconds = totalSeconds % 60;
            long millis = duration.toMillis() % 1000;
            String durationStr = String.format("%d days, %d:%02d:%02d.%03d000",
                    days, hours, minutes, seconds, millis);
            this.tracker.setDuration(durationStr);
            if (this.regionManager != null) {
                if (this.enableJsonDetail) {
                    this.regionManager.calculateRemainingStorageCosts(endTimestamp.plus(remainedProcessTime),
                            this.logicalObjects, this.detailsLog);
                } else {
                    this.regionManager.calculateRemainingStorageCosts(endTimestamp.plus(remainedProcessTime),
                            this.logicalObjects);
                }
                this.regionManager.printStat();
            }
            dumpJson("logs/detailed_3reg/simulation_details.json");
        }

        _printRegionManager();
        // 命中/未命中统计与吞吐均值打印（占位）
        System.out.println(this.hits);
        System.out.println(this.misses);
        if ((this.hits + this.misses) > 0)
            System.out.println((this.hits * 100.0) / (this.hits + this.misses));
        System.out.println(this.hits_size);
        System.out.println(this.misses_size);
        if ((this.hits_size + this.misses_size) > 0)
            System.out.println((this.hits_size * 100.0) / (this.hits_size + this.misses_size));

        double avgRt = 0;
        if (!this.runtime_throughputs.isEmpty()) {
            double sum = 0;
            for (Double d : this.runtime_throughputs)
                sum += d;
            avgRt = sum / this.runtime_throughputs.size();
        }
        System.out.println(avgRt);
        System.out.println(this.good_choices + " " + this.total_choices);
        System.out.println(this.ttl_log);
        System.out.println(this.tracePath);
        File f = new File(this.tracePath);
        if (f.exists()) {
            f.delete();
        }
    }

    private int countLines(String path) {
        try (BufferedReader br = new BufferedReader(new FileReader(path))) {
            int c = 0;
            while (br.readLine() != null)
                c++;
            return c;
        } catch (IOException e) {
            return 0;
        }
    }

    /**
     * When {@link Config#fixedBaseRegion} is true and YAML did not set {@link Config#milpDatasetEndEpochSec}, set it from
     * the trace so FB MILP matches simulator accounting: last row's request time + {@link #TRACE_ACCOUNTING_TAIL_SECONDS}s
     * (same tail as {@link org.example.src.model.RegionManager#calculateRemainingStorageCosts} at end of {@link #run()}).
     */
    private void prefillMilpDatasetEndEpochFromTraceIfApplicable() {
        if (!Boolean.TRUE.equals(config.fixedBaseRegion) || config.milpDatasetEndEpochSec != null) {
            return;
        }
        Instant last = scanTraceLastRequestInstant(Helpers.getFullPath(this.tracePath));
        if (last != null) {
            config.milpDatasetEndEpochSec = last.plus(Duration.ofSeconds(TRACE_ACCOUNTING_TAIL_SECONDS)).getEpochSecond();
        }
    }

    /**
     * Sequential scan: timestamp of the last data row (matches {@code endTimestamp} updated each iteration in {@link #run()}).
     */
    private Instant scanTraceLastRequestInstant(String path) {
        try (BufferedReader br = new BufferedReader(new FileReader(path))) {
            String header = br.readLine();
            if (header == null) {
                return null;
            }
            String[] headerCols = header.split(",");
            String line;
            Instant last = null;
            while ((line = br.readLine()) != null) {
                Map<String, String> row = parseCsvRow(headerCols, line);
                last = parseTraceRowTimestamp(row.get("timestamp"));
            }
            return last;
        } catch (IOException e) {
            logger.warning("scanTraceLastRequestInstant failed for " + path + ": " + e.getMessage());
            return null;
        }
    }

    private Instant parseTraceRowTimestamp(String timestampStr) {
        String normalized = timestampStr == null ? ""
                : timestampStr.replace("-", "").replace(":", "").replace(" ", "");
        if (!normalized.isEmpty() && normalized.chars().allMatch(Character::isDigit)) {
            return Instant.ofEpochMilli(Long.parseLong(timestampStr.trim()));
        }
        return parseIsoMillis(timestampStr);
    }

    private Map<String, String> parseCsvRow(String[] header, String line) {
        // Optimized: Use StringTokenizer instead of String.split() for better
        // performance
        java.util.StringTokenizer tokenizer = new java.util.StringTokenizer(line, ",");
        Map<String, String> m = new HashMap<>();
        int i = 0;
        while (tokenizer.hasMoreTokens() && i < header.length) {
            m.put(header[i], tokenizer.nextToken());
            i++;
        }
        return m;
    }

    private Instant parseIsoMillis(String s) {
        try {
            // Python 使用 datetime.fromisoformat(timestamp_str / 1000)，这里简单按 ISO-8601 解析
            return Instant.parse(s);
        } catch (Exception e) {
            return Instant.EPOCH;
        }
    }

    // 简化版打印区域管理信息（对应 _print_region_manager）
    private void _printRegionManager() {
        if (this.regionManager != null)
            this.regionManager.printStat();
    }

    // 保持原始函数名（Python）：_print_config_details
    public void _print_config_details() {
        _printConfigDetails();
    }

    // Helper class to return multiple values from _updateTransferMetric
    private static class TransferMetrics {
        double throughput;
        double throughputRuntime;
        double latency;
        double cost;

        TransferMetrics(double throughput, double throughputRuntime, double latency, double cost) {
            this.throughput = throughput;
            this.throughputRuntime = throughputRuntime;
            this.latency = latency;
            this.cost = cost;
        }
    }

    // Calculate transfer metrics from transfer graph (equivalent to Python's
    // _update_transfer_metric)
    private TransferMetrics _updateTransferMetric(org.example.graph.DiGraph transferGraph,
            org.example.src.model.Request request) {
        return _updateTransferMetric(transferGraph, request, null);
    }

    private TransferMetrics _updateTransferMetric(org.example.graph.DiGraph transferGraph,
            org.example.src.model.Request request, BigDecimal sizeOverride) {
        List<org.example.graph.DiGraph.Edge> edges = transferGraph.edgesWithData();
        if (edges.isEmpty()) {
            return new TransferMetrics(0, 0, 0, 0);
        }
        BigDecimal effectiveSize = (sizeOverride != null) ? sizeOverride : request.size;
        int numPartitions = ((Number) edges.get(0).attrs.getOrDefault("num_partitions", 1)).intValue();

        // Calculate minimum throughput across all edges 2. 计算吞吐量
        double throughput = edges.stream()
                .mapToDouble(edge -> ((Number) edge.attrs.getOrDefault("throughput", 0.0)).doubleValue())
                .min()
                .orElse(0.0);

        // Calculate minimum throughput_runtime 3. 计算吞吐量运行时间
        double throughputRuntime = edges.stream()
                .mapToDouble(edge -> {
                    int[] partitions = (int[]) edge.attrs.getOrDefault("partitions", new int[0]);
                    double edgeThroughput = ((Number) edge.attrs.getOrDefault("throughput", 1.0)).doubleValue();
                    return effectiveSize.multiply(BigDecimal.valueOf(partitions.length))
                            .divide(BigDecimal.valueOf(org.example.src.utils.Definitions.GB * numPartitions), 20,
                                    RoundingMode.HALF_UP)
                            .multiply(BigDecimal.valueOf(1.0 / edgeThroughput)).doubleValue();
                })
                .min()
                .orElse(0.0);

        // Calculate latency (maximum from src to all dsts)
        List<String> srcNodes = new ArrayList<>();
        List<String> dstNodes = new ArrayList<>();
        for (String node : transferGraph.getNodes()) {
            Boolean isSrc = (Boolean) transferGraph.getNodeAttrs(node).get("src");
            Boolean isDst = (Boolean) transferGraph.getNodeAttrs(node).get("dst");
            if (Boolean.TRUE.equals(isSrc))
                srcNodes.add(node);
            if (Boolean.TRUE.equals(isDst))
                dstNodes.add(node);
        }

        double latency = 0;
        if (!srcNodes.isEmpty() && !dstNodes.isEmpty()) {
            String src = srcNodes.get(0);
            // 4. 计算延迟
            latency = dstNodes.stream()
                    .mapToDouble(dst -> {
                        Number lat = (Number) transferGraph.getEdgeAttr(src, dst, "latency");
                        return lat != null ? lat.doubleValue() : 0.0;
                    })
                    .max()
                    .orElse(0.0);
        }
        // 5. 检查入口/出口限制（
        // Check ingress/egress limits
        if ("read".equals(request.op)) {
            double ingressLimit;
            if (request.issueRegion.startsWith("aws")) {
                ingressLimit = org.example.src.utils.Definitions.AWS_INSTANCE_THROUGHPUT_LIMIT[1];
            } else if (request.issueRegion.startsWith("gcp")) {
                ingressLimit = org.example.src.utils.Definitions.GCP_INSTANCE_THROUGHPUT_LIMIT[1];
            } else {
                ingressLimit = org.example.src.utils.Definitions.AZURE_INSTANCE_THROUGHPUT_LIMIT[1];
            }
            ingressLimit = ingressLimit * this.numVms;
            throughputRuntime = Math.max(throughputRuntime,
                    effectiveSize.divide(BigDecimal.valueOf(org.example.src.utils.Definitions.GB * ingressLimit), 20,
                            RoundingMode.HALF_UP).doubleValue());
        } else if ("write".equals(request.op)) {
            double egressLimit;
            if (request.issueRegion.startsWith("aws")) {
                egressLimit = org.example.src.utils.Definitions.AWS_INSTANCE_THROUGHPUT_LIMIT[0];
            } else if (request.issueRegion.startsWith("gcp")) {
                egressLimit = org.example.src.utils.Definitions.GCP_INSTANCE_THROUGHPUT_LIMIT[0];
            } else {
                egressLimit = org.example.src.utils.Definitions.AZURE_INSTANCE_THROUGHPUT_LIMIT[0];
            }
            egressLimit = egressLimit * this.numVms;
            throughputRuntime = Math.max(throughputRuntime,
                    effectiveSize.divide(BigDecimal.valueOf(org.example.src.utils.Definitions.GB * egressLimit), 20,
                            RoundingMode.HALF_UP).doubleValue());
        }

        this.runtime_throughputs.add(throughputRuntime);
        // 6. 计算总成本
        double cost = edges.stream()
                .mapToDouble(edge -> {
                    Number edgeCost = (Number) edge.attrs.getOrDefault("cost", 0.0);
                    int[] partitions = (int[]) edge.attrs.getOrDefault("partitions", new int[0]);
                    return BigDecimal.valueOf(edgeCost.doubleValue()).multiply(effectiveSize)
                            .multiply(BigDecimal.valueOf(partitions.length))
                            .divide(BigDecimal.valueOf(org.example.src.utils.Definitions.GB * numPartitions), 20,
                                    RoundingMode.HALF_UP)
                            .doubleValue();
                })
                .sum();

        // 7. 返回指标
        return new TransferMetrics(throughput, throughputRuntime, latency, cost);
    }

    // 报告指标（对应 Python report_metrics）
    public void report_metrics() {
        if (this.tracker == null)
            this.tracker = new org.example.src.model.Tracker();
        if (this.regionManager != null) {
            this.tracker.addStorageCost(this.regionManager.aggregateStorageCost());
            this.tracker.addStorageCostWithoutBase(this.regionManager.aggregateStorageCostWithoutBase());
        }
        // java.util.Map<String, Object> metrics = this.tracker.getMetrics();
        // 打印 "Metric | Value" 风格的行（注释以避免刷屏）
        // for (java.util.Map.Entry<String, Object> e : metrics.entrySet()) {
        // System.out.println("| " + e.getKey() + " | " + String.valueOf(e.getValue()) +
        // " |");
        // }
    }

    // 保持原始函数名（Python）：_print_region_manager
    public void _print_region_manager() {
        _printRegionManager();
    }

    // 选择置放策略（对应 Python _select_placement_policy）
    private PlacementPolicy _select_placement_policy(String policy_type) {
        if ("local".equals(policy_type)) {
            return new PolicyLocal(this.config, this.totalGraph, this.logicalObjects,
                    this.regionManager);
        } else if ("single_region".equals(policy_type)) {
            return new PolicySingleRegion(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("replicate_all".equals(policy_type)) {
            return new PolicyReplicateAll(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("spanstore".equals(policy_type)) {
            // Python 传入 policy="spanstore" 与 trace_path；此处保持构造最简对等
            return new PolicySpanstore(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("lru".equals(policy_type)) {
            return new PolicyLru(this.config, this.totalGraph, this.logicalObjects,
                    this.regionManager);
        } else if ("teven".equals(policy_type)) {
            return new PolicyTeven(this.config, this.totalGraph, this.logicalObjects,
                    this.regionManager);
        } else if ("always_evict".equals(policy_type)) {
            return new PolicyAlwaysEvict(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("pull_on_read".equals(policy_type)) {
            return new PolicyAlwaysStore(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("tevict_window".equals(policy_type)) {
            return new PolicyTevictNew(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("tevict".equals(policy_type)) {
            return new PolicyTevictNew(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("tevict_ranges".equals(policy_type)) {
            return new PolicyTevictRangesNew(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("optimal".equals(policy_type)) {
            return new PolicyOptimalV2(this.config, this.totalGraph,
                    this.logicalObjects,
                    this.regionManager);
        } else if ("fixedttl".equals(policy_type)) {
            return new PolicyFixedTtl(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("dynamicttl".equals(policy_type)) {
            return new PolicyDynamicTtl(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("to_keep".equals(policy_type)) {
            // Python 为 IndividualTTL；此处映射到占位策略
            return new PolicySkypie(this.config, this.totalGraph, this.logicalObjects,
                    this.regionManager);
        } else if ("ewma".equals(policy_type)) {
            return new PolicyEwma(this.config, this.totalGraph, this.logicalObjects,
                    this.regionManager);
        } else if ("adaptive_tier".equals(policy_type)) {
            return new PolicyAdaptiveTier(this.config, this.totalGraph,
                    this.logicalObjects,
                    this.regionManager);
        } else if ("milp_ec_new".equals(policy_type)) {
            return new PolicyMilpECnew(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);
        } else if ("milp_ec_new_v3".equals(policy_type)) {
            return new PolicyMilpECnewV3(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager);//注册新策略
        } else {
            System.err.println("Error: Unknown placement policy type: [" + policy_type + "]");
            throw new IllegalArgumentException("Invalid placement policy type: " + policy_type);
        }
    }

    // 选择传输策略（对应 Python _select_transfer_policy）
    private TransferPolicy _select_transfer_policy(String policy_type,
            PlacementPolicy placement_policy) {
        if ("direct".equals(policy_type)) {
            return new DirectTransfer(this.config, this.totalGraph,
                    this.logicalObjects);
        } else if ("closest".equals(policy_type)) {
            return new ClosestTransfer(this.config, this.totalGraph,
                    this.logicalObjects);
        } else if ("cheapest".equals(policy_type)) {
            if ("milp_ec_new_v3".equals(String.valueOf(this.config.placementPolicy))) {
                return new CheapestTransferV3(this.config, this.totalGraph,
                        this.logicalObjects, this.regionManager, placement_policy);//注册最便宜的运输路径策略
            }
            return new CheapestTransferV2(this.config, this.totalGraph,
                    this.logicalObjects, this.regionManager, placement_policy);
        } else {
            throw new IllegalArgumentException("Invalid transfer policy type");
        }
    }

    private void dumpJson(String path) {
        if (!this.enableJsonDetail)
            return;
        if ((this.tracePath != null && this.tracePath.contains("T29")) || (this.config.placementPolicy != null
                && String.valueOf(this.config.placementPolicy).contains("T29"))) {
            System.out.println("JSON dump disabled for T29 dataset.");
            return;
        }
        try {
            File f = new File(path);
            if (f.getParentFile() != null)
                f.getParentFile().mkdirs();
            Gson gson = new GsonBuilder().setPrettyPrinting().create();
            try (FileWriter writer = new FileWriter(f)) {
                gson.toJson(this.detailsLog, writer);
                System.out.println("Detailed JSON log written to " + f.getAbsolutePath());
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    /**
     * 模拟结束后清理策略资源：
     * 对 MILP 策略调用 flushPendingEvents() 处理最后一个窗口并释放 Gurobi 环境。
     */
    public void cleanupPolicy() {
        if (placementPolicy instanceof PolicyMilpECnew) {
            ((PolicyMilpECnew) placementPolicy)
                    .flushPendingEvents(Instant.now());
        } else if (placementPolicy instanceof PolicyMilpECnewV3) {
            ((PolicyMilpECnewV3) placementPolicy)//将新策略注册到清洗策略
                    .flushPendingEvents(Instant.now());
        }
    }

    public void writeResultsToJson(String tag) {
        if (this.tracker == null) {
            System.err.println("Warning: Tracker is null, cannot write results to JSON");
            return;
        }

        try {
            // Generate filename with timestamp
            String timestamp = java.time.LocalDateTime.now()
                    .format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"));
            String filename = String.format("simulation_results_%s_%s.json", tag, timestamp);

            // Get metrics
            java.util.Map<String, Object> metrics = this.tracker.getMetrics();

            // Add additional metadata
            metrics.put("tag", tag);
            metrics.put("trace_path", this.tracePath);
            metrics.put("config_policy", this.config.placementPolicy);
            metrics.put("timestamp", timestamp);

            // Write to JSON file
            Gson gson = new GsonBuilder()
                    .setPrettyPrinting()
                    .create();

            try (FileWriter writer = new FileWriter(filename)) {
                gson.toJson(metrics, writer);
                System.out.println("Simulation results written to: " + new File(filename).getAbsolutePath());
            }
        } catch (Exception e) {
            System.err.println("Failed to write results to JSON: " + e.getMessage());
            e.printStackTrace();
        }
    }
}
