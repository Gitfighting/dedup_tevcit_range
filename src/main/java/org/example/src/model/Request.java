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
    public Instant nextAccessTimestamp = Instant.ofEpochMilli(Long.MAX_VALUE);
    public Instant nextAccessSameRegTimestamp = Instant.ofEpochMilli(Long.MAX_VALUE);
    public String readFrom = null;
}
