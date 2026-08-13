package org.example.src.placement_policy;

import org.example.graph.DiGraph;
import org.example.src.model.Config;
import org.example.src.model.LogicalObject;
import org.example.src.model.RegionManager;

import java.util.Map;

/**
 * SkyStore TevictRanges + end-to-end LZ4 compression.
 * <p>
 * Placement / TTL / histogram logic is identical to {@link PolicyTevictRangesNew}
 * (single-variable ablation). The only difference is that this policy forces
 * transfer-byte and stored-physical-size compression via Config flags that
 * {@link org.example.SimulatorV2} applies when creating PhysicalObjects and
 * billing network transfers.
 * <p>
 * Logical Request.size and histogram inputs remain uncompressed so TTL learning
 * stays comparable to the uncompressed SkyStore baseline.
 */
public class PolicyTevictRangesLz4 extends PolicyTevictRangesNew {

    /** Mean wire/logical ratio measured on {@code docker_image/*.tar} with LZ4 frame. */
    public static final double DEFAULT_LZ4_RATIO = 0.3443;

    public PolicyTevictRangesLz4(Config c, DiGraph g, Map<String, LogicalObject> lo, RegionManager rm) {
        super(enableLz4(c), g, lo, rm);
        System.out.println("[DEBUG] PolicyTevictRangesLz4: transfer+storage LZ4 enabled"
                + " ratioDefault=" + c.lz4RatioDefault);
    }

    private static Config enableLz4(Config c) {
        c.transferCompressionEnabled = true;
        c.storageCompressionEnabled = true;
        c.transferCompressionAlgorithm = "lz4";
        if (c.lz4RatioDefault == null || c.lz4RatioDefault <= 0.0 || c.lz4RatioDefault > 1.0) {
            c.lz4RatioDefault = DEFAULT_LZ4_RATIO;
        }
        return c;
    }
}
