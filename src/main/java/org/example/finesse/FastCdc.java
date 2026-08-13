package org.example.finesse;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;

/**
 * FastCDC 内容定义分块（Content-Defined Chunking, Gear rolling hash + normalized chunking）。
 *
 * <p>依据 FastCDC（Xia et al., USENIX ATC'16）：用 Gear 哈希做滚动指纹，在 [min, max] 之间
 * 用两段掩码（normalization level 2）寻找切点——小于平均块长用更严的 {@code maskS}（更难切，
 * 抑制小块），超过平均用更松的 {@code maskL}（更易切，抑制大块），使块长分布集中在平均值附近。
 *
 * <p>切点对内容敏感而对插入/删除导致的整体位移不敏感，这正是相似文件能大量复用相同块的前提。
 */
public final class FastCdc {

    /** 默认参数：平均 8KB（2^13），适合 KB~MB 级文本/二进制对象。 */
    public static final int DEFAULT_MIN = 2 * 1024;
    public static final int DEFAULT_AVG = 8 * 1024;
    public static final int DEFAULT_MAX = 64 * 1024;

    /** FastCDC 论文给出的 8KB 规范掩码（normalization level 2）。 */
    private static final long MASK_S = 0x0003590703530000L; // 较多 1 位 → 更难命中切点
    private static final long MASK_L = 0x0000d90003530000L; // 较少 1 位 → 更易命中切点

    private final int minSize;
    private final int avgSize;
    private final int maxSize;

    public FastCdc() {
        this(DEFAULT_MIN, DEFAULT_AVG, DEFAULT_MAX);
    }

    public FastCdc(int minSize, int avgSize, int maxSize) {
        if (minSize <= 0 || avgSize < minSize || maxSize < avgSize) {
            throw new IllegalArgumentException("require 0 < min <= avg <= max");
        }
        this.minSize = minSize;
        this.avgSize = avgSize;
        this.maxSize = maxSize;
    }

    /**
     * 对整段字节做 CDC，返回有序的块哈希与块长。
     */
    public Chunking chunk(byte[] data) {
        List<Long> hashes = new ArrayList<>();
        List<Integer> lens = new ArrayList<>();
        int pos = 0;
        int n = data.length;
        while (pos < n) {
            int len = nextCutLength(data, pos, n);
            hashes.add(hashChunk(data, pos, len));
            lens.add(len);
            pos += len;
        }
        long[] hArr = new long[hashes.size()];
        int[] lArr = new int[lens.size()];
        for (int i = 0; i < hArr.length; i++) {
            hArr[i] = hashes.get(i);
            lArr[i] = lens.get(i);
        }
        return new Chunking(hArr, lArr, n);
    }

    /**
     * 从 {@code start} 起寻找下一个切点，返回块长度（FastCDC 论文 Algorithm 2）。
     */
    private int nextCutLength(byte[] src, int start, int end) {
        int remaining = end - start;
        if (remaining <= minSize) {
            return remaining;
        }
        int normal = Math.min(avgSize, remaining);
        int limit = Math.min(maxSize, remaining);
        long fp = 0L;
        int i = minSize; // 跳过 min 之前的字节，不可能在此切
        // 段 1：min..normal 用更严掩码
        for (; i < normal; i++) {
            fp = (fp << 1) + GearHash.TABLE[src[start + i] & 0xFF];
            if ((fp & MASK_S) == 0) {
                return i;
            }
        }
        // 段 2：normal..max 用更松掩码
        for (; i < limit; i++) {
            fp = (fp << 1) + GearHash.TABLE[src[start + i] & 0xFF];
            if ((fp & MASK_L) == 0) {
                return i;
            }
        }
        return limit; // 强制切，避免超长块
    }

    /**
     * 块内容指纹：取 SHA-256 前 8 字节为 64-bit 哈希（碰撞概率对本场景可忽略）。
     */
    static long hashChunk(byte[] data, int off, int len) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            md.update(data, off, len);
            byte[] d = md.digest();
            long h = 0L;
            for (int i = 0; i < 8; i++) {
                h = (h << 8) | (d[i] & 0xFFL);
            }
            return h;
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
