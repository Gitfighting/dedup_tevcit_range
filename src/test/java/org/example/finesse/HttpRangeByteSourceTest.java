package org.example.finesse;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.nio.file.Path;
import java.nio.file.Paths;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HttpRangeByteSourceTest {

    @Test
    void loaderReadsSqldumpFromConfig() throws Exception {
        Path root = Paths.get("").toAbsolutePath().normalize();
        Path cfg = root.resolve("config/finesse_http_datasets.json");
        FinesseDatasetLoader.DatasetSpec spec = FinesseDatasetLoader.load(cfg, "sqldump", root);
        assertEquals(4, spec.sources.size());
        assertFalseCompressed(spec);
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "FINESSE_HTTP_INTEGRATION", matches = "1")
    void kernelCdnSupportsRange() throws Exception {
        HttpRangeByteSource.RangeProbeResult info = HttpRangeByteSource.probe(
                "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.100.tar.xz");
        assertTrue(info.acceptRanges);
        assertTrue(info.contentLength > 100_000_000L);
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "FINESSE_HTTP_INTEGRATION", matches = "1")
    void macosFslSupportsRange() throws Exception {
        HttpRangeByteSource.RangeProbeResult info = HttpRangeByteSource.probe(
                "https://tracer.filesystems.org/traces/macos/2012/macos-2012-01-05-005505.tar.bz2");
        assertTrue(info.acceptRanges);
        assertTrue(info.contentLength > 100_000_000L);
    }

    private static void assertFalseCompressed(FinesseDatasetLoader.DatasetSpec spec) {
        assertNotNull(spec.description);
        org.junit.jupiter.api.Assertions.assertFalse(spec.compressed);
    }
}
