package org.example.src.utils;

import org.example.src.model.Request;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;

class TransferCompressionTest {
    @Test
    void scalesTransferOverrideButKeepsLogicalRequestSize() {
        Request request = new Request();
        request.size = BigDecimal.valueOf(1000);
        request.compressedSize = BigDecimal.valueOf(400);

        assertEquals(new BigDecimal("200"),
                TransferCompression.effectiveSize(request, BigDecimal.valueOf(500), true));
        assertEquals(BigDecimal.valueOf(1000), request.size);
    }

    @Test
    void fallsBackToLogicalSizeWhenDisabledOrProfileMissing() {
        Request request = new Request();
        request.size = BigDecimal.valueOf(1000);
        request.compressedSize = BigDecimal.valueOf(400);

        assertEquals(BigDecimal.valueOf(1000), TransferCompression.effectiveSize(request, null, false));
        request.compressedSize = null;
        assertEquals(BigDecimal.valueOf(1000), TransferCompression.effectiveSize(request, null, true));
    }

    @Test
    void usesDefaultRatioWhenCompressedSizeMissing() {
        Request request = new Request();
        request.size = BigDecimal.valueOf(1000);
        request.compressedSize = null;

        assertEquals(new BigDecimal("345"),
                TransferCompression.effectiveSize(request, null, true, 0.3443));
    }

    @Test
    void neverExpandsOnWireData() {
        Request request = new Request();
        request.size = BigDecimal.valueOf(1000);
        request.compressedSize = BigDecimal.valueOf(1200);

        assertEquals(new BigDecimal("1000"), TransferCompression.effectiveSize(request, null, true));
    }
}
