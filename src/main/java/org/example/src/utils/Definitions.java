package org.example.src.utils;

/**
 * 常量定义，等效于 Python src.utils.definitions
 */
public final class Definitions {
    public static final long KB = 1024L;
    public static final long MB = 1024L * 1024L;
    public static final long GB = 1024L * 1024L * 1024L;

    // provider bandwidth limits (egress, ingress)
    public static final double[] AWS_INSTANCE_THROUGHPUT_LIMIT = new double[]{5.0, 10.0};
    public static final double[] GCP_INSTANCE_THROUGHPUT_LIMIT = new double[]{7.0, 16.0};
    public static final double[] AZURE_INSTANCE_THROUGHPUT_LIMIT = new double[]{16.0, 16.0};

    private Definitions() {}
}

