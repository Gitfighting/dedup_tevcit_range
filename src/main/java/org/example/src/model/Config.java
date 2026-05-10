package org.example.src.model;

import java.util.List;

/**
 * 延迟SLO配置
 */
class LatencySLO {
    public int read; // ms
    public int write; // ms

    public LatencySLO(int read, int write) {
        this.read = read;
        this.write = write;
    }
}

/**
 * 配置模型，等效于 Python 的 pydantic Config。
 */
public class Config {
    public String storageRegion = "";
    public String placementPolicy;
    public String transferPolicy;
    public String policy; // For SPANStore policy type

    public Boolean fixedBaseRegion = false;
    public List<String> regions;

    public Integer windowSize; // T_evict only
    public Long cacheSize; // bytes
    public Integer cacheTtl; // hours

    public String oracleDirectory; // SkyPIE/SpanStore
    public String tracePath; // For SkyPIE/SPANStore trace file
    public Integer replicationFactorMin = 1;
    public Integer replicationFactorMax = 1;
    public Boolean noStrictReplication = true;
    public String regionSelector = "";
    public String objectStoreSelector = "";

    // EC (Erasure Coding) configuration
    public Boolean ecEnabled = false; // Enable EC optimization
    public Integer ecK = 6; // Number of data chunks
    public Integer ecM = 3; // Number of parity chunks

    /** When true, Simulator/read path follows Python artifact semantics more closely. */
    public Boolean pythonCompat = false;

    // MILP V3 (PolicyMilpECnewV3 + Gurobi)
    public Double milpTimeLimitSec = 2.0;
    public Double milpMipGap = 0.05;
    public Boolean milpStrictFail = true;
    public String milpTriggerMode = "HISTOGRAM_TICK";
    /**
     * When true, MILP fixes binary {@code z=1}: only EC(2+1) stripe layouts are optimized (replica branch disabled).
     * For experiments; default false keeps EC vs replica comparison inside the solver.
     */
    public Boolean milpEcV3ForceEcOnly = false;

    /**
     * When false, MILP constrains {@code z=0} (replica layouts only). Default true allows EC vs replica in the solver.
     */
    public Boolean milpEcV3AllowEc = true;
}
