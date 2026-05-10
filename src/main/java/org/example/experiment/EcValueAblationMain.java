package org.example.experiment;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import org.example.SimulatorV3;
import org.example.SimulatorV2;
import org.example.src.model.Config;
import org.example.src.utils.ConfigLoader;
import org.example.src.utils.CostLogger;

import java.io.FileWriter;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Runs two simulations that differ only by {@link Config#milpEcV3AllowEc}
 * (full MILP EC-or-replica vs. same MILP with {@code z} fixed to replica-only),
 * then writes a small comparison JSON next to the per-arm {@code simulation_results_*.json} files.
 *
 * <p>Usage:
 * <pre>
 *   java -cp Skystore1212-1.0-SNAPSHOT-shaded.jar org.example.experiment.EcValueAblationMain \
 *       --config-ec-on &lt;yaml&gt; --config-ec-off &lt;yaml&gt; --trace &lt;trace&gt; [--vm N] [--setbase true|false] [--tag prefix]
 * </pre>
 * Typical YAML pair: identical files except {@code milpEcV3AllowEc: true} vs {@code false}.
 */
public final class EcValueAblationMain {

    private EcValueAblationMain() {
    }

    public static void main(String[] args) {
        String configEcOn = null;
        String configEcOff = null;
        String trace = null;
        int vm = 5;
        boolean setbase = false;
        String tag = "ec_ablation";

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--config-ec-on":
                    if (i + 1 < args.length) {
                        configEcOn = args[++i];
                    }
                    break;
                case "--config-ec-off":
                    if (i + 1 < args.length) {
                        configEcOff = args[++i];
                    }
                    break;
                case "--trace":
                    if (i + 1 < args.length) {
                        trace = args[++i];
                    }
                    break;
                case "--vm":
                    if (i + 1 < args.length) {
                        vm = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--setbase":
                    if (i + 1 < args.length) {
                        setbase = Boolean.parseBoolean(args[++i]);
                    }
                    break;
                case "--tag":
                    if (i + 1 < args.length) {
                        tag = args[++i];
                    }
                    break;
                default:
                    break;
            }
        }

        if (configEcOn == null || configEcOff == null || trace == null) {
            System.err.println(
                    "Usage: java ... EcValueAblationMain --config-ec-on <yaml> --config-ec-off <yaml> "
                            + "--trace <trace_file> [--vm N] [--setbase true|false] [--tag prefix]");
            System.exit(1);
        }

        validatePolicy(configEcOn, configEcOff);

        try {
            Map<String, Object> on = runArm(tag + "_ec_on", configEcOn, trace, vm, setbase);
            Map<String, Object> off = runArm(tag + "_ec_off", configEcOff, trace, vm, setbase);

            Map<String, Object> summary = new LinkedHashMap<>();
            summary.put("tag", tag);
            summary.put("trace_path", trace);
            summary.put("config_ec_on", configEcOn);
            summary.put("config_ec_off", configEcOff);
            summary.put("metrics_ec_on", on);
            summary.put("metrics_ec_off", off);
            summary.put("delta_total_cost",
                    num(off, "total cost ($)") - num(on, "total cost ($)"));
            summary.put("delta_storage_cost",
                    num(off, "total storage cost ($)") - num(on, "total storage cost ($)"));
            summary.put("delta_transfer_cost",
                    num(off, "total transfer cost ($)") - num(on, "total transfer cost ($)"));
            summary.put("delta_request_cost",
                    num(off, "total request cost ($)") - num(on, "total request cost ($)"));
            summary.put("interpretation",
                    "positive delta_* means the EC-off (replica-only MILP) arm cost more than EC-on; "
                            + "negative means EC-off was cheaper.");

            String ts = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"));
            String out = String.format("ec_value_ablation_summary_%s_%s.json", tag, ts);
            Gson gson = new GsonBuilder().setPrettyPrinting().create();
            try (FileWriter w = new FileWriter(out)) {
                gson.toJson(summary, w);
            }
            System.out.println("EC ablation summary written to: " + out);
            printHumanSummary(summary);
        } catch (Exception e) {
            System.err.println("EcValueAblationMain failed: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }

    private static void validatePolicy(String pathOn, String pathOff) {
        Config a = ConfigLoader.loadConfig(pathOn);
        Config b = ConfigLoader.loadConfig(pathOff);
        if (!"milp_ec_new_v3".equals(String.valueOf(a.placementPolicy))
                || !"milp_ec_new_v3".equals(String.valueOf(b.placementPolicy))) {
            throw new IllegalStateException("Both configs must use placement_policy: milp_ec_new_v3");
        }
        Boolean allowOn = a.milpEcV3AllowEc == null || a.milpEcV3AllowEc;
        Boolean allowOff = b.milpEcV3AllowEc == null || b.milpEcV3AllowEc;
        if (!allowOn || allowOff) {
            throw new IllegalStateException(
                    "Expected config-ec-on with milpEcV3AllowEc true (or omitted) "
                            + "and config-ec-off with milpEcV3AllowEc false.");
        }
    }

    private static Map<String, Object> runArm(String armTag, String configPath, String tracePath, int vm,
            boolean setbase) throws Exception {
        CostLogger.initialize(armTag, tracePath, configPath, setbase, vm);
        try {
            Config cfg = ConfigLoader.loadConfig(configPath);
            SimulatorV2 simulator = pickSimulator(cfg, configPath, tracePath, vm, setbase);
            simulator.run();
            simulator.cleanupPolicy();
            simulator.report_metrics();
            simulator.writeResultsToJson(armTag);
            return new HashMap<>(simulator.getTracker().getMetrics());
        } finally {
            CostLogger.close();
        }
    }

    private static SimulatorV2 pickSimulator(Config loadedConfig, String configPath, String tracePath, int vm,
            boolean setbase) {
        String pp = String.valueOf(loadedConfig.placementPolicy);
        if ("milp_ec_new".equals(pp) || "milp_ec_new_v2".equals(pp) || "milp_ec_new_v3".equals(pp)) {
            return new SimulatorV3(configPath, tracePath, vm, setbase, 0, true, false);
        }
        return new SimulatorV2(configPath, tracePath, vm, setbase, 0, true, false);
    }

    private static double num(Map<String, Object> m, String key) {
        Object v = m.get(key);
        if (v instanceof Number) {
            return ((Number) v).doubleValue();
        }
        return Double.NaN;
    }

    private static void printHumanSummary(Map<String, Object> summary) {
        double dTotal = (Double) summary.get("delta_total_cost");
        double dStor = (Double) summary.get("delta_storage_cost");
        double dXfer = (Double) summary.get("delta_transfer_cost");
        double dReq = (Double) summary.get("delta_request_cost");
        System.out.println("=== EC value ablation (EC_off minus EC_on) ===");
        System.out.printf("  delta total cost:    %+$.9f%n", dTotal);
        System.out.printf("  delta storage:       %+$.9f%n", dStor);
        System.out.printf("  delta transfer:      %+$.9f%n", dXfer);
        System.out.printf("  delta request (API): %+$.9f%n", dReq);
    }
}
