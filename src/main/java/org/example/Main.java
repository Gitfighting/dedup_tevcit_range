package org.example;

import org.example.src.utils.CostLogger;

public class Main {
    public static void main(String[] args) {
        // Parse command line arguments
        String config = null;
        String trace = null;
        int vm = 5;
        boolean setbase = false;
        int simversion = 1;
        String tag = "simulation";

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--config":
                    if (i + 1 < args.length) {
                        config = args[++i];
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
                case "--simversion":
                    if (i + 1 < args.length) {
                        simversion = Integer.parseInt(args[++i]);
                    }
                    break;
                case "--tag":
                    if (i + 1 < args.length) {
                        tag = args[++i];
                    }
                    break;
            }
        }

        // Validate required arguments
        if (config == null || trace == null) {
            System.err.println(
                    "Usage: java -jar Skystore1212.jar --config <config_file> --trace <trace_file> [--vm <num>] [--setbase <true|false>] [--simversion <version>] [--tag <tag>]");
            System.exit(1);
        }

        // Initialize cost logger
        CostLogger.initialize(tag, trace, config, setbase, vm);

        try {
            System.out.println("Starting simulation with:");
            System.out.println("  Config: " + config);
            System.out.println("  Trace: " + trace);
            System.out.println("  VMs: " + vm);
            System.out.println("  SetBase: " + setbase);
            System.out.println("  SimVersion: " + simversion);
            System.out.println("  Tag: " + tag);

            // Create and run simulator
            SimulatorV2 simulator = new SimulatorV2(
                    config,
                    trace,
                    vm,
                    setbase,
                    0, // daysToIgnoreCost
                    true, // versionEnable
                    false // storeDecision
            );

            simulator.run();

            simulator.cleanupPolicy();

            simulator.report_metrics();

            // Generate results JSON file
            simulator.writeResultsToJson(tag);

            System.out.println("Simulation completed successfully.");
        } catch (Exception e) {
            System.err.println("Simulation failed: " + e.getMessage());
            e.printStackTrace();
            CostLogger.logError("Simulation failed for trace: " + trace, e);
            System.exit(1);
        } finally {
            CostLogger.close();
        }
    }
}
