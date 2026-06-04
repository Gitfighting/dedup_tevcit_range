package org.example;

import org.example.src.utils.CostLogger;

public class Main {
    public static void main(String[] args) {
        // Parse command line arguments
        String config = null;//yaml文件
        String trace = null;//数据集
        int vm = 5;//虚拟机的数量
        boolean setbase = false;//是否设置基站
        int simversion = 1;//仿真版本
        String tag = "simulation";//tag标签

        for (int i = 0; i < args.length; i++) {//自动读取参数命令行参数
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
//java -jar Skystore1212.jar --config config\milp_ec_new_v3_9reg.yaml --trace Data\9reg\9reg\typeA\IBMObjectStoreTrace079Part0.typeA.mc.aug --vm 5 --setbase true --simversion 1 --tag milp_ec_new_v3_java_fb_9reg_T79
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
            System.exit(1);
        } finally {
            CostLogger.close();
        }
    }
}
