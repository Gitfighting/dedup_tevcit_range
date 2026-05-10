package org.example.src.placement_policy.milpv3;

import org.example.graph.DiGraph;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;

/**
 * Storage cost helper aligned with RegionManager.pricePerGB semantics.
 */
public final class StorageCostHelper {//存储成本辅助类，用于计算存储成本
    private StorageCostHelper() {
    }

    private static BigDecimal pricePerGbForDuration(String region, double ttlSec, DiGraph g) {
        Object price = g.getNodeAttrs(region).get("priceStorage");
        BigDecimal pricePerGbPerMonth;
        if (price instanceof Number) {
            pricePerGbPerMonth = BigDecimal.valueOf(((Number) price).doubleValue()).multiply(BigDecimal.valueOf(3));
        } else {
            pricePerGbPerMonth = BigDecimal.ZERO;
        }

        long sec = (long) Math.max(0.0, ttlSec);
        Duration duration = Duration.ofSeconds(sec);
        BigDecimal durationNs = BigDecimal.valueOf(duration.toNanos());
        BigDecimal nsPerDay = BigDecimal.valueOf(1_000_000_000L).multiply(BigDecimal.valueOf(86400L));
        BigDecimal daysBD = durationNs.divide(nsPerDay, 30, RoundingMode.HALF_UP);
        return pricePerGbPerMonth.multiply(daysBD).setScale(12, RoundingMode.HALF_UP);
    }

    public static double storageCostGb(double sizeGb, String region, double ttlSec, DiGraph g) {
        BigDecimal p = pricePerGbForDuration(region, ttlSec, g);
        return p.multiply(BigDecimal.valueOf(sizeGb)).doubleValue();
    }
}

