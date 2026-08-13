package org.example.src.model;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;

public class LogicalObject {
    private final String key;
    /** First-write (PUT) instant for this logical key; not updated by {@link #setLastModified}. */
    private final Instant putInstant;
    private BigDecimal size;
    private Instant lastModified;
    private Status status;
    public Map<String, PhysicalObject> physicalObjects = new HashMap<>();
    public String baseRegion = null;
    /** Delta cluster root key; empty when this object is a base. */
    public String baseKey = null;
    public boolean isBase = true;
    /** Delta compression ratio r = |patch|/|full|; 1.0 for bases. */
    public double deltaRatio = 1.0;
    public Map<String, PhysicalObject> latestPhysicalObjects = new HashMap<>();
    public Integer latestVersionId = null;

    public LogicalObject(String key, BigDecimal size, Instant lastModified, Status status) {
        this.key = key;
        this.putInstant = lastModified;
        this.size = size;
        this.lastModified = lastModified;
        this.status = status;
    }

    public Instant getPutInstant() {
        return putInstant;
    }

    public Integer getLatestVersionId() {
        return latestVersionId;
    }

    public void incrementVersionId() {
        if (this.latestVersionId == null) {
            this.latestVersionId = 0;
        }
        this.latestVersionId += 1;
    }

    public void addPhysicalObject(String region, PhysicalObject physicalObject) {
        physicalObjects.put(region, physicalObject);
    }

    public void setLastModified(Instant lastModified) {
        this.lastModified = lastModified;
    }

    public void assignBaseRegion(String region) {
        if (this.baseRegion == null)
            this.baseRegion = region;
    }

    public boolean isReadyInRegion(String region) {
        PhysicalObject po = physicalObjects.get(region);
        return po != null && po.getStatus() == Status.ready;
    }

    public PhysicalObject getLastElement() {
        return physicalObjects.values().stream()
                .max(Comparator.comparing(po -> {
                    Instant start = po.getStorageStartTime();
                    int ttl = po.getTtl();
                    return start == null ? Instant.MIN : start.plusSeconds(ttl);
                }))
                .orElse(null);
    }

    public String getKey() {
        return key;
    }

    public BigDecimal getSize() {
        return size;
    }

    public Instant getLastModified() {
        return lastModified;
    }

    public Status getStatus() {
        return status;
    }
}
