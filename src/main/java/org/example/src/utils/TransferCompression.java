package org.example.src.utils;

import org.example.src.model.Request;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * Scales physical / on-wire bytes for LZ4 while keeping logical Request.size unchanged
 * for histograms and request-size metrics.
 */
public final class TransferCompression {
    private TransferCompression() {
    }

    public static BigDecimal effectiveSize(Request request, BigDecimal logicalSizeOverride, boolean enabled) {
        return effectiveSize(request, logicalSizeOverride, enabled, null);
    }

    public static BigDecimal effectiveSize(Request request, BigDecimal logicalSizeOverride, boolean enabled,
            Double defaultRatio) {
        BigDecimal logicalSize = logicalSizeOverride != null ? logicalSizeOverride : request.size;
        if (!enabled || logicalSize == null || request.size == null || request.size.signum() <= 0) {
            return logicalSize;
        }

        BigDecimal ratio;
        if (request.compressedSize != null && request.compressedSize.signum() >= 0) {
            ratio = request.compressedSize.divide(request.size, 20, RoundingMode.HALF_UP);
        } else if (defaultRatio != null && defaultRatio > 0.0 && defaultRatio <= 1.0) {
            ratio = BigDecimal.valueOf(defaultRatio);
        } else {
            return logicalSize;
        }

        if (ratio.compareTo(BigDecimal.ONE) > 0) {
            ratio = BigDecimal.ONE;
        }
        return logicalSize.multiply(ratio).setScale(0, RoundingMode.CEILING);
    }
}
