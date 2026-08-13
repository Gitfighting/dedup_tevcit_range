package org.example.src.model;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * 请求模型，Request。
 */
public class Request {
    public Instant timestamp;
    public String op; // "read", "write", "evict"
    public String issueRegion;
    public String objKey;
    public BigDecimal size;
    /** Optional on-wire size after compression; logical/storage size remains {@link #size}. */
    public BigDecimal compressedSize;
    public Instant nextAccessTimestamp = Instant.ofEpochMilli(Long.MAX_VALUE);
    public Instant nextAccessSameRegTimestamp = Instant.ofEpochMilli(Long.MAX_VALUE);
    public String readFrom = null;

    /** Parsed from augmented trace; bases default true / ratio 1.0. */
    public boolean isBase = true;
    public String baseKey = null;
    public double deltaRatio = 1.0;
}
