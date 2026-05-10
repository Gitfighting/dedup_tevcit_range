package org.example.src.model;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.util.HashSet;
import java.util.Set;

/**
 * LogicalObjectV2 extends LogicalObject to support Locally Repairable Codes
 * (LRC) and adaptive temperature-based storage strategies.
 * It maintains the state of whether the object is currently replicated or
 * erasure-coded, the specific LRC parameters, and access behavior tracking
 * for temperature classification.
 */
public class LogicalObjectV2 extends LogicalObject {

    // LRC Parameters
    private int k; // Number of data chunks
    private int l; // Number of local parity groups (or local parities)
    private int r; // Number of global parities
    private boolean isLRC; // Flag to indicate if currently using LRC

    // EC State Tracking
    private int replicaCount; // Number of full replicas
    private String ecGroupId; // UUID for EC chunk group (null if not EC)
    private Instant ecTransitionTimestamp; // When EC conversion happened

    // Access Behavior Tracking (for temperature classification)
    private int readCount; // Number of read accesses
    private int writeCount; // Number of write accesses
    private Instant creationTime; // When object was created
    private Instant lastAccessTime; // Last access timestamp
    private Set<String> accessedRegions; // Regions that accessed this object

    // Dual-Timescale EWMA Hotness Tracking (for PolicyAdaptiveTier)
    // Fast EWMA: captures burst behavior (short-term)
    private double fastEwmaHotness = 0.0;
    private static final double FAST_ALPHA = 0.3; // Burst-sensitive
    private static final double FAST_DECAY = 0.0001; // Half-life ~2 hours

    // Slow EWMA: captures long-term popularity
    private double slowEwmaHotness = 0.0;
    private static final double SLOW_ALPHA = 0.05; // Long-term trend
    private static final double SLOW_DECAY = 0.00001; // Half-life ~19 hours

    public LogicalObjectV2(String key, BigDecimal size, Instant lastModified, Status status) {
        super(key, size, lastModified, status);
        this.isLRC = false; // Default to full replication initially
        this.k = 1;
        this.l = 0;
        this.r = 0;
        this.replicaCount = 0;
        this.ecGroupId = null;
        this.ecTransitionTimestamp = null;

        // Initialize access tracking
        this.readCount = 0;
        this.writeCount = 0;
        this.creationTime = lastModified; // Use object's lastModified as creation time
        this.lastAccessTime = this.creationTime;
        this.accessedRegions = new HashSet<>();

        // Initialize dual-timescale hotness
        this.fastEwmaHotness = 0.0;
        this.slowEwmaHotness = 0.0;
    }

    /**
     * Converts the object state to LRC with specified parameters.
     * Note: This strictly updates metadata. Actual PhysicalObject updates must be
     * handled by the RegionManager/Policy.
     */
    public void convertToLRC(int k, int l, int r) {
        this.isLRC = true;
        this.k = k;
        this.l = l;
        this.r = r;
    }

    /**
     * Reverts the object state to full replication.
     */
    public void convertToReplica() {
        this.isLRC = false;
        // k, l, r can be reset or ignored
        this.k = 1;
        this.l = 0;
        this.r = 0;
    }

    public boolean isLRC() {
        return isLRC;
    }

    public int getK() {
        return k;
    }

    public int getL() {
        return l;
    }

    public int getR() {
        return r;
    }

    /**
     * helper to calculate shard size based on current K
     */
    public BigDecimal getShardSize() {
        if (!isLRC || k <= 0)
            return getSize();
        return getSize().divide(BigDecimal.valueOf(k), 0, RoundingMode.CEILING);
    }

    /**
     * Overridden to handle specific logic if needed,
     * but usually LogicalObject's map is sufficient as long as we put
     * PhysicalObjectV2 instances in it.
     */
    @Override
    public void addPhysicalObject(String region, PhysicalObject physicalObject) {
        super.addPhysicalObject(region, physicalObject);
        updateReplicaCount();
    }

    /**
     * Updates replica count based on current physical objects.
     * Only counts REPLICA type chunks, not EC chunks.
     */
    private void updateReplicaCount() {
        int count = 0;
        for (PhysicalObject po : physicalObjects.values()) {
            if (po instanceof PhysicalObjectV2) {
                PhysicalObjectV2 pov2 = (PhysicalObjectV2) po;
                if (pov2.getChunkType() == PhysicalObjectV2.ChunkType.REPLICA) {
                    count++;
                }
            } else {
                // Original PhysicalObject is always full replica
                count++;
            }
        }
        this.replicaCount = count;
    }

    public int getReplicaCount() {
        return replicaCount;
    }

    public void setReplicaCount(int count) {
        this.replicaCount = count;
    }

    public String getECGroupId() {
        return ecGroupId;
    }

    public void setECGroupId(String groupId) {
        this.ecGroupId = groupId;
        this.ecTransitionTimestamp = Instant.now();
    }

    public Instant getECTransitionTimestamp() {
        return ecTransitionTimestamp;
    }

    /**
     * Returns the effective storage overhead.
     * For replicas: replicaCount
     * For EC: (k+l+r)/k
     */
    public double getStorageOverhead() {
        if (isLRC) {
            return (double) (k + l + r) / k;
        } else {
            return replicaCount;
        }
    }

    /**
     * Checks if this object is in EC state.
     */
    public boolean isInECState() {
        return isLRC && ecGroupId != null;
    }

    public void setInECState(boolean inEC) {
        if (inEC) {
            this.isLRC = true;
        } else {
            this.isLRC = false;
            this.ecGroupId = null;
        }
    }

    // ========== Access Behavior Tracking Methods ==========

    /**
     * Record an access to this object
     */
    public void recordAccess(Request req) {
        this.lastAccessTime = req.timestamp;

        // Update dual-timescale EWMA hotness
        this.fastEwmaHotness = FAST_ALPHA * 1.0 + (1 - FAST_ALPHA) * this.fastEwmaHotness;
        this.slowEwmaHotness = SLOW_ALPHA * 1.0 + (1 - SLOW_ALPHA) * this.slowEwmaHotness;

        // Handle both read/write and PUT/GET/REST.GET.OBJECT formats
        String op = req.op;
        if ("read".equals(op)) {
            this.readCount++;
        } else if ("write".equals(op)) {
            this.writeCount++;
        }

        // Track accessed regions
        if (req.issueRegion != null) {
            this.accessedRegions.add(req.issueRegion);
        }
    }

    // Getters for access behavior
    public int getReadCount() {
        return readCount;
    }

    public int getWriteCount() {
        return writeCount;
    }

    public long getCreationTime() {
        return creationTime.toEpochMilli();
    }

    public long getLastAccessTime() {
        return lastAccessTime.toEpochMilli();
    }

    public Instant getCreationTimeInstant() {
        return creationTime;
    }

    public Instant getLastAccessTimeInstant() {
        return lastAccessTime;
    }

    public Set<String> getAccessedRegions() {
        return new HashSet<>(accessedRegions);
    }

    // Helper methods for temperature classification
    /**
     * Calculate object age in seconds from creation time to current time.
     * 
     * @param currentTime The current timestamp to calculate age against
     * @return Age in seconds
     */
    public long getAgeSeconds(Instant currentTime) {
        return (currentTime.toEpochMilli() - creationTime.toEpochMilli()) / 1000;
    }

    /**
     * Calculate object age in hours from creation time to current time.
     * 
     * @param currentTime The current timestamp to calculate age against
     * @return Age in hours
     */
    public long getAgeHours(Instant currentTime) {
        return getAgeSeconds(currentTime) / 3600;
    }

    public long getHoursSinceLastAccess() {
        // This needs current time context - should be passed from caller
        return 0; // Placeholder - use getHoursSinceLastAccess(Instant currentTime) instead
    }

    public long getHoursSinceLastAccess(Instant currentTime) {
        return (currentTime.toEpochMilli() - lastAccessTime.toEpochMilli()) / 3600000;
    }

    // ========== Dual-Timescale EWMA Hotness Methods ==========

    /**
     * Get fast hotness (captures burst behavior)
     */
    public double getFastHotness(Instant currentTime) {
        long elapsedSeconds = Duration.between(this.lastAccessTime, currentTime).getSeconds();
        return this.fastEwmaHotness * Math.exp(-FAST_DECAY * elapsedSeconds);
    }

    /**
     * Get slow hotness (captures long-term popularity)
     */
    public double getSlowHotness(Instant currentTime) {
        long elapsedSeconds = Duration.between(this.lastAccessTime, currentTime).getSeconds();
        return this.slowEwmaHotness * Math.exp(-SLOW_DECAY * elapsedSeconds);
    }

    /**
     * Get current combined hotness (max of fast and slow)
     */
    public double getCurrentHotness(Instant currentTime) {
        double fastHotness = getFastHotness(currentTime);
        double slowHotness = getSlowHotness(currentTime);
        return Math.max(fastHotness, slowHotness);
    }

    /**
     * Get raw fast EWMA hotness without decay
     */
    public double getFastEwmaHotness() {
        return this.fastEwmaHotness;
    }

    /**
     * Get raw slow EWMA hotness without decay
     */
    public double getSlowEwmaHotness() {
        return this.slowEwmaHotness;
    }
}
