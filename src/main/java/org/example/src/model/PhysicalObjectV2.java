package org.example.src.model;

import java.math.BigDecimal;

public class PhysicalObjectV2 extends PhysicalObject {
    public enum ChunkType {
        REPLICA, // Full replica
        DATA_CHUNK, // Data shard
        LOCAL_PARITY, // Local repair parity
        GLOBAL_PARITY // Global repair parity
    }

    private final ChunkType chunkType;
    private final int chunkIndex; // Index within the coding group
    private String ecGroupId; // UUID linking all chunks of same EC object

    public PhysicalObjectV2(String locationTag, String key, BigDecimal size, int ttl, LogicalObject logicalObject,
            Status status, int versionId, ChunkType chunkType, int chunkIndex) {
        super(locationTag, key, size, ttl, logicalObject, status, versionId);
        this.chunkType = chunkType;
        this.chunkIndex = chunkIndex;
        this.ecGroupId = null; // Set later when EC group is formed
    }

    public ChunkType getChunkType() {
        return chunkType;
    }

    public int getChunkIndex() {
        return chunkIndex;
    }

    public String getECGroupId() {
        return ecGroupId;
    }

    public void setECGroupId(String groupId) {
        this.ecGroupId = groupId;
    }

    public boolean isPartOfECSet() {
        return ecGroupId != null && chunkType != ChunkType.REPLICA;
    }

    @Override
    public String toString() {
        return super.toString() + ", ChunkType: " + chunkType + ", Index: " + chunkIndex;
    }
}
