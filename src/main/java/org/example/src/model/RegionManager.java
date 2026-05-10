package org.example.src.model;

import org.example.graph.DiGraph;
import org.example.src.utils.Definitions;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.logging.Logger;

public class RegionManager {
    private final DiGraph totalGraph;
    private final Logger logger;

    private final Map<String, Set<String>> regions = new HashMap<>();
    private final Map<String, Set<PhysicalObject>> regionObjects = new HashMap<>();
    private final Map<String, BigDecimal> regionTotSize = new HashMap<>();

    private final Map<String, BigDecimal> storageCosts = new HashMap<>();
    private final Map<String, BigDecimal> storageCostsWithoutBase = new HashMap<>();

    private Instant traceStartTime = null;
    private int daysToIgnore = 0;

    public Map<String, BigDecimal> getStorageCosts() {
        return storageCosts;
    }

    public Map<String, BigDecimal> getStorageCostsWithoutBase() {
        return storageCostsWithoutBase;
    }

    public RegionManager(DiGraph totalGraph, Logger logger) {
        this.totalGraph = totalGraph;
        this.logger = logger;
    }

    public void setStartTimeAndIgnoredDays(Instant startTime, int daysToIgnore) {
        this.traceStartTime = startTime;
        this.daysToIgnore = daysToIgnore;
    }

    private BigDecimal pricePerGB(String region, Duration duration) {
        Object price = totalGraph.getNodeAttrs(region).get("priceStorage");
        BigDecimal pricePerGbPerMonth;
        if (price instanceof BigDecimal) {
            pricePerGbPerMonth = ((BigDecimal) price).multiply(BigDecimal.valueOf(3));
        } else if (price instanceof Number) {
            pricePerGbPerMonth = BigDecimal.valueOf(((Number) price).doubleValue()).multiply(BigDecimal.valueOf(3));
        } else {
            pricePerGbPerMonth = BigDecimal.ZERO;
        }

        // Duration in days with high precision
        BigDecimal durationNs = BigDecimal.valueOf(duration.toNanos());
        BigDecimal nsPerDay = BigDecimal.valueOf(1_000_000_000L).multiply(BigDecimal.valueOf(86400L));
        BigDecimal daysBD = durationNs.divide(nsPerDay, 30, RoundingMode.HALF_UP);

        return pricePerGbPerMonth.multiply(daysBD).setScale(12, RoundingMode.HALF_UP);
    }

    public void addObjectToRegion(String region, PhysicalObject physicalObject) {
        regions.computeIfAbsent(region, k -> new HashSet<>());
        regionObjects.computeIfAbsent(region, k -> new HashSet<>());
        regionTotSize.putIfAbsent(region, BigDecimal.ZERO);

        regions.get(region).add(physicalObject.getKey());
        regionObjects.get(region).add(physicalObject);
        regionTotSize.put(region, regionTotSize.get(region).add(physicalObject.getSize()));
        // Log storage size update
        if (org.example.src.utils.Logger.detailedStorage) {
            org.example.src.utils.Logger.info(String.format(
                    "[STORAGE-ADD] Region=%s Obj=%s Size=%s Bytes (Total Region Size=%s)",
                    region, physicalObject.getKey(), physicalObject.getSize(), regionTotSize.get(region)));
        }
        logger.info("Adding object " + physicalObject.getKey() + " to region " + region + ".");
    }

    public Set<String> getObjectInRegion(String region) {
        Set<String> objects = regions.getOrDefault(region, Collections.emptySet());
        logger.info("Getting object in region " + region + ": " + objects + ".");
        return objects;
    }

    public boolean hasObjectInRegion(String region, String physicalObjectKey) {
        Set<PhysicalObject> objects = regionObjects.getOrDefault(region, Collections.emptySet());
        PhysicalObject physicalObject = null;
        for (PhysicalObject obj : objects) {
            if (obj.getKey().equals(physicalObjectKey)) {
                physicalObject = obj;
                break;
            }
        }

        if (physicalObject != null && physicalObject.getStatus() == Status.ready) {
            // Object is ready
        } else {
            physicalObject = null;
        }
        boolean exist = physicalObject != null;
        logger.info("Object " + physicalObjectKey + " in region " + region + ": " + exist + ".");
        return exist;
    }

    public void clearAllObjects() {
        regionObjects.clear();
        regionTotSize.clear();
        logger.info("Clearing all objects in all regions.");
    }

    public void removeObjectFromRegion(String region, PhysicalObject physicalObject, Instant endTime) {
        if (regions.containsKey(region) && regions.get(region).contains(physicalObject.getKey())) {
            regions.get(region).remove(physicalObject.getKey());
            regionObjects.get(region).remove(physicalObject);
            regionTotSize.put(region, regionTotSize.get(region).subtract(physicalObject.getSize()));

            Instant ignoreTimestamp = traceStartTime.plus(Duration.ofDays(daysToIgnore));
            Duration duration;
            if (physicalObject.getStorageStartTime().isAfter(ignoreTimestamp)) {
                duration = physicalObject.storageDuration(endTime);
            } else {
                if (endTime.isBefore(ignoreTimestamp)) {
                    duration = Duration.ZERO;
                } else {
                    Instant startTime = traceStartTime.plus(Duration.ofDays(daysToIgnore));
                    duration = Duration.between(startTime, endTime);
                }
            }
            if (duration.isNegative())
                duration = Duration.ZERO;

            BigDecimal pricePerGbForDuration = pricePerGB(region, duration);
            BigDecimal sizeInGb = physicalObject.getSize()
                    .divide(BigDecimal.valueOf(Definitions.GB), 20, RoundingMode.HALF_UP);
            BigDecimal cost = pricePerGbForDuration.multiply(sizeInGb).setScale(12, RoundingMode.HALF_UP);

            // Log ALL removals for debugging
            // BigDecimal durationHours = BigDecimal.valueOf(duration.toNanos())
            // .divide(BigDecimal.valueOf(1_000_000_000L * 3600L), 20,
            // RoundingMode.HALF_UP);

            // Write to log file
            // org.example.src.utils.CostLogger.logStorageRemoval(
            // region, physicalObject.getKey(), sizeInGb, durationHours, cost);

            if (org.example.src.utils.Logger.detailedStorage) {
                org.example.src.utils.Logger.info(String.format(
                        "[STORAGE-REM] Region=%s Obj=%s Size=%.6f GB Duration=%.2f Hrs Cost=$%.9f (Price*Size*Dur)",
                        region, physicalObject.getKey(), sizeInGb, duration.getSeconds() / 3600.0, cost));
            }

            // Accumulate cost
            storageCosts.put(region, storageCosts.getOrDefault(region, BigDecimal.ZERO).add(cost));

            String base = physicalObject.getLogicalObject().baseRegion;
            if (base == null || !region.equals(base)) {
                storageCostsWithoutBase.put(region,
                        storageCostsWithoutBase.getOrDefault(region, BigDecimal.ZERO).add(cost));
            }

            logger.info("Removing object " + physicalObject.getKey() + " from region " + region + ".");
        }
    }

    public BigDecimal getRegionObjectSize(String region) {
        return regionTotSize.getOrDefault(region, BigDecimal.ZERO);
    }

    public void evictLru(String region, BigDecimal objSize, long cacheSize, Instant endTime) {
        BigDecimal cacheSizeBD = BigDecimal.valueOf(cacheSize);
        while (regionTotSize.getOrDefault(region, BigDecimal.ZERO).add(objSize).compareTo(cacheSizeBD) > 0) {
            PhysicalObject lruPhysical = regionObjects.get(region).stream()
                    .min(Comparator.comparing(po -> po.getLogicalObject().getLastModified()))
                    .orElse(null);
            if (lruPhysical == null)
                break;
            if (lruPhysical.getStorageStartTime() == null)
                throw new IllegalStateException("Storage start time is None.");
            if (lruPhysical.getStorageStartTime().isAfter(endTime))
                throw new IllegalStateException("Storage start time " + lruPhysical.getStorageStartTime()
                        + " is greater than end time " + endTime);
            removeObjectFromRegion(region, lruPhysical, endTime);
        }
    }

    public void calculateRemainingStorageCosts(Instant endTime, Map<String, LogicalObject> logicalObjects) {
        calculateRemainingStorageCosts(endTime, logicalObjects, null);
    }

    public void calculateRemainingStorageCosts(Instant endTime, Map<String, LogicalObject> logicalObjects,
            List<Map<String, Object>> detailsCollector) {
        Instant ignoreTimestamp = traceStartTime.plus(Duration.ofDays(daysToIgnore));
        int c = 0;
        int remainingLogCount = 0; // Counter to limit console logs
        final int MAX_REMAINING_LOGS = 200; // Limit to prevent console flooding

        // Add region summary logging
        System.out.println("=== Region Objects Summary ===");
        for (Map.Entry<String, Set<PhysicalObject>> entry : regionObjects.entrySet()) {
            System.out.println(String.format("Region %s has %d remaining objects",
                    entry.getKey(), entry.getValue().size()));
        }
        System.out.println("==============================");

        for (Map.Entry<String, Set<PhysicalObject>> entry : regionObjects.entrySet()) {
            String region = entry.getKey();
            for (PhysicalObject physicalObject : entry.getValue()) {
                Duration duration;
                if (physicalObject.getStorageStartTime().isBefore(ignoreTimestamp)) {
                    LogicalObject logicalObject = logicalObjects.get(physicalObject.getKey());
                    boolean single = logicalObject.physicalObjects.size() == 1;
                    if (physicalObject.getTtl() == -1 || single) {
                        Instant maxStart = physicalObject.getStorageStartTime().isAfter(ignoreTimestamp)
                                ? physicalObject.getStorageStartTime()
                                : ignoreTimestamp;
                        duration = Duration.between(maxStart, endTime);
                    } else {
                        Duration ttlDur = Duration.ofSeconds(physicalObject.getTtl());
                        Duration d1 = Duration.between(ignoreTimestamp, endTime);
                        Duration d2 = Duration.between(physicalObject.getStorageStartTime(), endTime);
                        duration = min(ttlDur, min(d1, d2));
                    }
                } else {
                    LogicalObject logicalObject = logicalObjects.get(physicalObject.getKey());

                    if (physicalObject.getTtl() == -1 ||
                            logicalObject.physicalObjects.size() == 1) {
                        duration = physicalObject.storageDuration(endTime);
                        if (physicalObject.getTtl() == -1) {
                            c++;
                        }
                    } else {
                        double storageDurationSeconds = physicalObject.storageDuration(endTime).toNanos()
                                / 1_000_000_000.0;
                        if (storageDurationSeconds > physicalObject.getTtl()) {
                            duration = Duration.ofNanos((long) (physicalObject.getTtl() * 1_000_000_000.0));
                        } else {
                            duration = physicalObject.storageDuration(endTime);
                        }
                    }
                }
                if (duration.isNegative())
                    duration = Duration.ZERO;

                BigDecimal pricePerGbForDuration = pricePerGB(region, duration);
                BigDecimal sizeInGb = physicalObject.getSize()
                        .divide(BigDecimal.valueOf(Definitions.GB), 20, RoundingMode.HALF_UP);
                BigDecimal cost = pricePerGbForDuration.multiply(sizeInGb).setScale(12, RoundingMode.HALF_UP);

                if (org.example.src.utils.Logger.detailedStorage && remainingLogCount < MAX_REMAINING_LOGS) {
                    org.example.src.utils.Logger.info(String.format(
                            "[STORAGE-REM] [REMAINING] Region=%s Obj=%s Size=%.6f GB Duration=%.2f Hrs Cost=$%.9f",
                            region, physicalObject.getKey(), sizeInGb, duration.getSeconds() / 3600.0, cost));
                    remainingLogCount++;
                }

                // Log ALL remaining objects for complete debugging visibility
                // LogicalObject logObj = logicalObjects.get(physicalObject.getKey());
                // int physCount = logObj.physicalObjects.size();
                // DEBUG: Use actual Key to allow matching with Python logs
                // String branch = physicalObject.getKey(); // (physicalObject.getTtl() == -1 ||
                // physCount == 1) ?
                // "FULL_DUR" : "TTL_CAP";

                // BigDecimal durationHours = BigDecimal.valueOf(duration.toNanos())
                // .divide(BigDecimal.valueOf(1_000_000_000L * 3600L), 20,
                // RoundingMode.HALF_UP);

                // Console output disabled for performance - check log file
                // System.out.println(String.format(
                // "[REMAIN-COST] Region=%s, PhysCount=%d, Branch=%s, TTL=%d, Size=%.6f GB,
                // Duration=%.6f hrs, Cost=$%.12f",
                // region, physCount, branch, physicalObject.getTtl(),
                // sizeGB, durationHours, cost));

                // Write to log file
                // org.example.src.utils.CostLogger.logStorageRemaining(
                // region, physCount, branch, physicalObject.getTtl(),
                // sizeInGb, durationHours, cost);

                storageCosts.put(region, storageCosts.getOrDefault(region, BigDecimal.ZERO).add(cost));

                String base = physicalObject.getLogicalObject().baseRegion;
                if (base == null || !region.equals(base)) {
                    storageCostsWithoutBase.put(region,
                            storageCostsWithoutBase.getOrDefault(region, BigDecimal.ZERO).add(cost));
                }
            }
        }
        System.out.println("c: " + c);
    }

    private static Duration min(Duration a, Duration b) {
        return a.compareTo(b) <= 0 ? a : b;
    }

    public BigDecimal aggregateStorageCost() {
        for (Map.Entry<String, BigDecimal> e : storageCosts.entrySet()) {
            logger.info("Storage cost for region " + e.getKey() + ": " + e.getValue() + ".");
            // System.out.println("Storage cost for region " + e.getKey() + ": " +
            // e.getValue() + ".");
        }
        return storageCosts.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    public BigDecimal aggregateStorageCostWithoutBase() {
        for (Map.Entry<String, BigDecimal> e : storageCostsWithoutBase.entrySet()) {
            logger.info("Storage cost without base for region " + e.getKey() + ": " + e.getValue() + ".");
            // System.out.println("Storage cost without base for region " + e.getKey() + ":
            // " + e.getValue() + ".");
        }
        return storageCostsWithoutBase.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    public void printStat() {
        System.out.println("Total number of regions storing objects: " + regions.size());
        List<String> printRst = new ArrayList<>();
        for (Map.Entry<String, Set<String>> e : regions.entrySet()) {
            printRst.add("(" + e.getKey() + "," + e.getValue().size() + ")");
        }
        System.out.println("Number of objects stored in each region: " + printRst);
        BigDecimal size = BigDecimal.ZERO;
        for (Set<PhysicalObject> set : regionObjects.values()) {
            for (PhysicalObject po : set)
                size = size.add(po.getSize().divide(BigDecimal.valueOf(Definitions.GB), 20, RoundingMode.HALF_UP));
        }
        BigDecimal sum = regionTotSize.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add)
                .divide(BigDecimal.valueOf(Definitions.GB), 20, RoundingMode.HALF_UP);
        if (size.subtract(sum).abs().compareTo(BigDecimal.valueOf(1e-9)) > 0) {
            System.out.println("Size mismatch detected: computed=" + size + " GB, tracked=" + sum + " GB, diff="
                    + size.subtract(sum).abs());
        }
        System.out.println("Sum of all object sizes stored in all regions: " + size + " GB.");
    }
}
