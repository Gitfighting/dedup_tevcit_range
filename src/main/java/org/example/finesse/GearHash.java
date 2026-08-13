package org.example.finesse;

import java.util.Random;

/**
 * 确定性 Gear 哈希表（256 个 64-bit 随机值），供 FastCDC 与超特征滚动指纹共用。
 *
 * <p>固定随机种子 → 同一份字节流在任何机器上得到完全一致的分块/特征，保证实验可复现。
 */
final class GearHash {

    /** 固定种子，保证跨机器可复现。 */
    static final long SEED = 0x5179A3C2F1E0B7D9L;

    static final long[] TABLE = build(SEED);

    private GearHash() {
    }

    static long[] build(long seed) {
        long[] t = new long[256];
        Random r = new Random(seed);
        for (int i = 0; i < 256; i++) {
            t[i] = r.nextLong();
        }
        return t;
    }
}
