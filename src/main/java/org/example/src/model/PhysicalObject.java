package org.example.src.model;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.Objects;

public class PhysicalObject {
    private final String locationTag;
    public final String cloud;
    public final String region;
    private final String key;
    private Status status;
    private BigDecimal size;
    private int ttl; // seconds
    private final LogicalObject logicalObject;
    private Instant storageStartTime;
    public boolean expireImmediate = false;
    private int versionId;
    /**
     * EC 片位身份（slot identity）：{@code SHARD_1}/{@code SHARD_2}/{@code PARITY}/{@code REPLICA}。
     *
     * <p>用于读恢复时强制「拿到互补片」而非两片同类片（EC(K+M) 重建需 K 个<em>不同</em>片位）。
     * 见 {@code ec_shard_logic_review_20260328.md} §36-39。{@code null} 表示身份未知，
     * 读路径据此退回到旧的「按成本取两片」近似（向后兼容）。
     */
    private String shardSlot;

    public PhysicalObject(String locationTag,
            String key,
            BigDecimal size,
            int ttl,
            LogicalObject logicalObject,
            Status status,
            int versionId) {
        this.locationTag = locationTag;
        String[] parts = locationTag.split(":");
        this.cloud = parts[0];
        this.region = parts.length > 1 ? parts[1] : "";
        this.key = key;
        this.status = status;
        this.size = size;
        this.ttl = ttl;
        this.logicalObject = logicalObject;
        this.versionId = versionId;
    }

    public String getLocationTag() {
        return locationTag;
    }

    public String getKey() {
        return key;
    }

    public Status getStatus() {
        return status;
    }

    public void setStatus(Status status) {
        this.status = status;
    }

    public BigDecimal getSize() {
        return size;
    }

    public void setSize(BigDecimal size) {
        this.size = size;
    }

    public int getTtl() {
        return ttl;
    }

    public void setTtl(int ttl) {
        this.ttl = ttl;
    }

    public LogicalObject getLogicalObject() {
        return logicalObject;
    }

    public Instant getStorageStartTime() {
        return storageStartTime;
    }

    public void setStorageStartTime(Instant t) {
        this.storageStartTime = t;
    }

    public boolean ready(Instant timestamp) {
        return status == Status.ready && storageStartTime != null && !timestamp.isBefore(storageStartTime);
    }

    public boolean isExpired(Instant timestamp) {
        if (ttl == -1)
            return false;
        if (storageStartTime == null)
            return false;
        Instant end = storageStartTime.plusSeconds(ttl);
        return !timestamp.isBefore(end);
    }

    public Duration storageDuration(Instant endTime) {
        if (storageStartTime == null)
            return Duration.ZERO;
        return Duration.between(storageStartTime, endTime);
    }

    @Override
    public String toString() {
        return "Object Location: " + locationTag + ", Object Key: " + key +
                ", Object Size: " + size + ", Object storage start time: " + storageStartTime +
                ", Object TTL: " + ttl;
    }

    @Override
    public int hashCode() {
        return Objects.hash(key, locationTag);
    }

    @Override
    public boolean equals(Object other) {
        if (!(other instanceof PhysicalObject))
            return false;
        PhysicalObject po = (PhysicalObject) other;
        return key.equals(po.key) && locationTag.equals(po.locationTag);
    }

    public int getVersionId() {
        return versionId;
    }

    public void setVersionId(int versionId) {
        this.versionId = versionId;
    }

    /** EC 片位身份；{@code null} 表示未知。见字段 {@link #shardSlot} 注释。 */
    public String getShardSlot() {
        return shardSlot;
    }

    public void setShardSlot(String shardSlot) {
        this.shardSlot = shardSlot;
    }
}
