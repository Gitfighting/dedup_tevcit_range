package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.LogicalObject;
import org.example.src.model.Config;
import org.example.src.model.RegionManager;
import org.example.src.model.Request;
import org.example.src.utils.ConfigLoader;
import org.example.src.utils.Helpers;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIf;
import org.junit.jupiter.api.io.TempDir;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.fail;

/**
 * Replays T79 typeA read stream into V3 vs tevict histogram paths and asserts pair-TTL parity.
 */
class PolicyMilpECnewV3TevictTraceReplayTest {

    private static final String TRACE = "results/synth/3reg/typeA/"
            + "IBMObjectStoreTrace079Part0.typeA.p100.delta.mc.aug";
    private static final List<String> REGIONS = List.of(
            "aws:us-east-1", "azure:westeurope", "gcp:us-west1-a");

    private static DiGraph graph;
    private static Config config;

    @BeforeAll
    static void initGraph() throws Exception {
        graph = Helpers.makeNxGraph(null, null, null, null, 5);
        config = ConfigLoader.loadConfig("config/milp_ec_new_v4_3reg.yaml");
        config.milpStrictFail = true;
        config.milpMinRequests = Integer.MAX_VALUE;
    }

    static boolean traceAvailable() {
        return new File(TRACE).exists();
    }

    @Test
    @EnabledIf("traceAvailable")
    void readReplayGetTevictMatchesTevictRanges(@TempDir Path tempDir) throws Exception {
        File versioned = versionateTrace(new File(TRACE), tempDir.resolve("versioned.aug").toFile());

        Map<String, LogicalObject> objects = new HashMap<>();
        RegionManager rm1 = new RegionManager(graph, Logger.getLogger("replay-v3"));
        RegionManager rm2 = new RegionManager(graph, Logger.getLogger("replay-tev"));
        PolicyMilpECnewV3 v3 = new PolicyMilpECnewV3(config, graph, objects, rm1);
        PolicyTevictRangesNew tevict = new PolicyTevictRangesNew(config, graph, new HashMap<>(), rm2);

        int readCount = 0;
        int mismatchCount = 0;
        String firstMismatch = null;

        try (BufferedReader br = new BufferedReader(new FileReader(versioned))) {
            String header = br.readLine();
            String[] cols = header.split(",");
            String line;
            while ((line = br.readLine()) != null) {
                Map<String, String> row = parseRow(cols, line);
                String op = row.get("op");
                if (!"GET".equals(op) && !"REST.GET.OBJECT".equals(op)) {
                    continue;
                }
                Request req = toReadRequest(row);
                v3.updatePastRequests(req, req.issueRegion);
                tevict.updatePastRequests(req, req.issueRegion);
                readCount++;

                for (String src : REGIONS) {
                    for (String dst : REGIONS) {
                        if (src.equals(dst)) {
                            continue;
                        }
                        double v3Ttl = v3.getTevict(src, dst, req.timestamp);
                        double tevTtl = tevict.getTevict(src, dst, req.timestamp);
                        if (Math.abs(v3Ttl - tevTtl) > 1e-3) {
                            mismatchCount++;
                            if (firstMismatch == null) {
                                firstMismatch = String.format(
                                        "read#%d ts=%s pair %s->%s v3=%.6f tev=%.6f",
                                        readCount, req.timestamp, src, dst, v3Ttl, tevTtl);
                            }
                        }
                    }
                }
            }
        }

        if (firstMismatch != null) {
            fail("getTevict diverged " + mismatchCount + " times; first: " + firstMismatch);
        }
        assertEquals(0, mismatchCount);
    }

    private static Request toReadRequest(Map<String, String> row) {
        Request req = new Request();
        req.op = "read";
        req.issueRegion = row.get("issue_region");
        req.objKey = row.get("obj_key");
        req.size = new BigDecimal(row.get("size"));
        req.timestamp = parseEpochMillis(row.get("timestamp"));
        if (row.containsKey("is_base")) {
            String isBase = row.get("is_base");
            req.isBase = isBase == null || isBase.isEmpty() || "1".equals(isBase);
            String bk = row.get("base_key");
            req.baseKey = (bk == null || bk.isEmpty()) ? null : bk;
            req.deltaRatio = Double.parseDouble(row.getOrDefault("delta_ratio", "1.0"));
        }
        return req;
    }

    private static Instant parseEpochMillis(String s) {
        return Instant.ofEpochMilli(Long.parseLong(s.trim()));
    }

    private static Map<String, String> parseRow(String[] cols, String line) {
        java.util.StringTokenizer tok = new java.util.StringTokenizer(line, ",");
        Map<String, String> row = new HashMap<>();
        int i = 0;
        while (tok.hasMoreTokens() && i < cols.length) {
            row.put(cols[i++], tok.nextToken());
        }
        return row;
    }

    private static File versionateTrace(File in, File out) throws Exception {
        Map<String, Integer> versions = new HashMap<>();
        try (BufferedReader br = new BufferedReader(new FileReader(in));
                java.io.BufferedWriter bw = new java.io.BufferedWriter(new java.io.FileWriter(out))) {
            String header = br.readLine();
            bw.write(header);
            bw.newLine();
            String[] cols = header.split(",");
            String line;
            while ((line = br.readLine()) != null) {
                Map<String, String> row = parseRow(cols, line);
                String op = row.get("op");
                String objKey = row.get("obj_key");
                if ("PUT".equals(op) || "REST.PUT.OBJECT".equals(op)) {
                    versions.put(objKey, versions.getOrDefault(objKey, 0) + 1);
                }
                int v = versions.getOrDefault(objKey, 0);
                row.put("obj_key", objKey + "-v" + v);
                bw.write(joinRow(cols, row));
                bw.newLine();
            }
        }
        return out;
    }

    private static String joinRow(String[] cols, Map<String, String> row) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < cols.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(row.getOrDefault(cols[i], ""));
        }
        return sb.toString();
    }
}
