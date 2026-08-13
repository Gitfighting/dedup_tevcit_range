package org.example.src.utils;

import org.example.src.model.Config;
import org.yaml.snakeyaml.Yaml;

import java.io.FileInputStream;
import java.io.InputStream;
import java.util.List;
import java.util.Map;

/**
 * 配置文件加载工具类
 */
public class ConfigLoader {

    public static Config loadConfig(String configPath) {

        Config config = new Config();

        try (InputStream input = new FileInputStream(configPath)) {
            Yaml yaml = new Yaml();
            Map<String, Object> data = yaml.load(input);

            // 加载placement_policy
            if (data.containsKey("placement_policy")) {
                config.placementPolicy = (String) data.get("placement_policy");
            }

            // 加载transfer_policy
            if (data.containsKey("transfer_policy")) {
                config.transferPolicy = (String) data.get("transfer_policy");
            }

            // 加载policy (for SPANStore)
            if (data.containsKey("policy")) {
                config.policy = (String) data.get("policy");
            }

            // 加载storage_region
            if (data.containsKey("storage_region")) {
                config.storageRegion = (String) data.get("storage_region");
            }

            // 加载regions
            if (data.containsKey("regions")) {
                @SuppressWarnings("unchecked")
                List<String> regions = (List<String>) data.get("regions");
                config.regions = regions;
            }

            // 加载window_size
            if (data.containsKey("window_size")) {
                config.windowSize = ((Number) data.get("window_size")).intValue();
            }

            // 加载cache_size
            if (data.containsKey("cache_size")) {
                config.cacheSize = ((Number) data.get("cache_size")).longValue();
            }

            // 加载cache_ttl
            if (data.containsKey("cache_ttl")) {
                config.cacheTtl = ((Number) data.get("cache_ttl")).intValue();
            }

            // 加载oracle_directory
            if (data.containsKey("oracle_directory")) {
                config.oracleDirectory = (String) data.get("oracle_directory");
            }

            // 加载trace_path
            if (data.containsKey("trace_path")) {
                config.tracePath = (String) data.get("trace_path");
            }

            // 加载replication_factor_min
            if (data.containsKey("replication_factor_min")) {
                config.replicationFactorMin = ((Number) data.get("replication_factor_min")).intValue();
            }

            // 加载replication_factor_max
            if (data.containsKey("replication_factor_max")) {
                config.replicationFactorMax = ((Number) data.get("replication_factor_max")).intValue();
            }

            // 加载no_strict_replication
            if (data.containsKey("no_strict_replication")) {
                config.noStrictReplication = (Boolean) data.get("no_strict_replication");
            }

            // 加载region_selector
            if (data.containsKey("region_selector")) {
                config.regionSelector = (String) data.get("region_selector");
            }

            // 加载object_store_selector
            if (data.containsKey("object_store_selector")) {
                config.objectStoreSelector = (String) data.get("object_store_selector");
            }

            // 加载EC配置
            if (data.containsKey("ec_enabled")) {
                config.ecEnabled = (Boolean) data.get("ec_enabled");
            }

            if (data.containsKey("ec_k")) {
                config.ecK = ((Number) data.get("ec_k")).intValue();
            }

            if (data.containsKey("ec_m")) {
                config.ecM = ((Number) data.get("ec_m")).intValue();
            }

            if (data.containsKey("python_compat")) {
                config.pythonCompat = (Boolean) data.get("python_compat");
            }

            if (data.containsKey("milp_time_limit_sec")) {
                config.milpTimeLimitSec = ((Number) data.get("milp_time_limit_sec")).doubleValue();
            }
            if (data.containsKey("milp_mip_gap")) {
                config.milpMipGap = ((Number) data.get("milp_mip_gap")).doubleValue();
            }
            if (data.containsKey("milp_strict_fail")) {
                config.milpStrictFail = (Boolean) data.get("milp_strict_fail");
            }
            if (data.containsKey("milpEcV3ForceEcOnly")) {
                config.milpEcV3ForceEcOnly = (Boolean) data.get("milpEcV3ForceEcOnly");
            }
            if (data.containsKey("milp_ec_v3_force_ec_only")) {
                config.milpEcV3ForceEcOnly = (Boolean) data.get("milp_ec_v3_force_ec_only");
            }
            if (data.containsKey("milpEcV3AllowEc")) {
                config.milpEcV3AllowEc = (Boolean) data.get("milpEcV3AllowEc");
            }
            if (data.containsKey("milp_ec_v3_allow_ec")) {
                config.milpEcV3AllowEc = (Boolean) data.get("milp_ec_v3_allow_ec");
            }
            if (data.containsKey("milp_dataset_end_epoch_sec")) {
                config.milpDatasetEndEpochSec = ((Number) data.get("milp_dataset_end_epoch_sec")).longValue();
            }
            if (data.containsKey("milp_fb_replica_horizon_sec")) {
                config.milpFbReplicaHorizonSec = ((Number) data.get("milp_fb_replica_horizon_sec")).doubleValue();
            }
            if (data.containsKey("milp_min_requests")) {
                config.milpMinRequests = ((Number) data.get("milp_min_requests")).intValue();
            }
            if (data.containsKey("delta_enabled")) {
                config.deltaEnabled = (Boolean) data.get("delta_enabled");
            }
            if (data.containsKey("ec_on_patch")) {
                config.ecOnPatch = (Boolean) data.get("ec_on_patch");
            }
            if (data.containsKey("transfer_compression_enabled")) {
                config.transferCompressionEnabled = (Boolean) data.get("transfer_compression_enabled");
            }
            if (data.containsKey("transfer_compression_algorithm")) {
                config.transferCompressionAlgorithm = String.valueOf(data.get("transfer_compression_algorithm"));
            }
            if (data.containsKey("storage_compression_enabled")) {
                config.storageCompressionEnabled = (Boolean) data.get("storage_compression_enabled");
            }
            if (data.containsKey("lz4_ratio_default")) {
                config.lz4RatioDefault = ((Number) data.get("lz4_ratio_default")).doubleValue();
            }

        } catch (Exception e) {
            throw new RuntimeException("Failed to load config file: " + configPath, e);
        }

        return config;
    }
}
