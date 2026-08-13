package org.example.src.utils;

import org.example.src.model.LogicalObject;
import org.example.src.model.PhysicalObject;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/** Shared tevict TTL region_list construction (SimulatorV2 read refresh + calculateTtl). */
public final class TevictRegionList {

    private TevictRegionList() {
    }

    public static List<String> collectRemoteReadyRegions(LogicalObject lo, String issueRegion, Instant at,
            boolean fullReplicaOnly) {
        BigDecimal logicalSize = lo.getSize();
        List<String> regionList = new ArrayList<>();
        for (PhysicalObject obj : lo.physicalObjects.values()) {
            if (obj.getLocationTag().equals(issueRegion) || !obj.ready(at)) {
                continue;
            }
            if (fullReplicaOnly && !isFullReplicaSize(obj.getSize(), logicalSize)) {
                continue;
            }
            regionList.add(obj.getLocationTag());
        }
        return regionList;
    }

    static boolean isFullReplicaSize(BigDecimal physicalSize, BigDecimal logicalSize) {
        return physicalSize != null && logicalSize != null && physicalSize.compareTo(logicalSize) >= 0;
    }
}
