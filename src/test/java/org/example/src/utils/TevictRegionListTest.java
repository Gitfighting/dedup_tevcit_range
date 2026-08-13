package org.example.src.utils;

import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;
import org.example.src.model.Status;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** t8c: MILP EC tevict region_list must exclude EC shards (full replicas only). */
class TevictRegionListTest {

    private static final String ISSUE = "aws:us-east-1";
    private static final String REMOTE = "gcp:us-west1-a";
    private static final String KEY = "obj-v1";
    private static final Instant T0 = Instant.parse("2020-01-01T00:00:00Z");
    private static final BigDecimal FULL = BigDecimal.valueOf(1_000_000);

    @Test
    void fullReplicaOnlyExcludesShard() {
        LogicalObject lo = logical(FULL);
        lo.physicalObjects.put(REMOTE, physical(REMOTE, FULL.divide(BigDecimal.valueOf(2))));

        List<String> regions = TevictRegionList.collectRemoteReadyRegions(lo, ISSUE, T0, true);
        assertTrue(regions.isEmpty());
    }

    @Test
    void fullReplicaOnlyIncludesFullCopy() {
        LogicalObject lo = logical(FULL);
        lo.physicalObjects.put(REMOTE, physical(REMOTE, FULL));

        List<String> regions = TevictRegionList.collectRemoteReadyRegions(lo, ISSUE, T0, true);
        assertEquals(List.of(REMOTE), regions);
    }

    @Test
    void tevictModeIncludesPartialCopy() {
        LogicalObject lo = logical(FULL);
        lo.physicalObjects.put(REMOTE, physical(REMOTE, FULL.divide(BigDecimal.valueOf(2))));

        List<String> regions = TevictRegionList.collectRemoteReadyRegions(lo, ISSUE, T0, false);
        assertEquals(List.of(REMOTE), regions);
    }

    private static LogicalObject logical(BigDecimal size) {
        LogicalObject lo = new LogicalObject(KEY, size, T0, Status.ready);
        lo.baseRegion = ISSUE;
        return lo;
    }

    private static PhysicalObject physical(String region, BigDecimal size) {
        PhysicalObject po = new PhysicalObject(region, KEY, size, 3600, null, Status.ready, 0);
        po.setStorageStartTime(T0);
        return po;
    }
}
