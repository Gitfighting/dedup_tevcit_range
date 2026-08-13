package org.example.finesse;

/**
 * 超特征（super-feature）相似检测，Finesse / Odess 方法的对象级实现。
 *
 * <p>思路（Broder 的 min/max-hash resemblance + Odess 内容定义采样）：
 * <ol>
 *   <li>对整段字节跑 Gear 滚动指纹；只在「内容定义的采样点」(指纹低位命中 {@link #SAMPLE_MASK})
 *       才参与特征计算——这是 Odess 的关键加速，避免逐字节做 N 次乘法，且采样点对内容敏感、
 *       对位移稳健。</li>
 *   <li>用 {@link #N_FEATURES} 组线性变换 {@code (A_i*fp + B_i)}，各自取流上的最大值 → N 个特征。</li>
 *   <li>把 N 个特征按 {@link #GROUP} 个一组分成 {@link #K_SUPER} 个超特征，组内特征混合成 1 个 64-bit 值。</li>
 * </ol>
 *
 * <p>两个对象只要共享 ≥1 个超特征即判为「相似候选」（共享越多越相似），用于挑选 delta 的 base。
 */
public final class SuperFeature {

    /** 特征个数与分组：12 个特征 → 每 4 个一组 → 3 个超特征（Finesse 常用规模）。 */
    public static final int N_FEATURES = 12;
    public static final int GROUP = 4;
    public static final int K_SUPER = N_FEATURES / GROUP; // 3

    /** 内容定义采样率 ~1/64：指纹低 6 位为 0 才取样。 */
    private static final long SAMPLE_MASK = 0x3FL;

    /** 线性变换系数（确定性派生，保证可复现）。 */
    private static final long[] A = new long[N_FEATURES];
    private static final long[] B = new long[N_FEATURES];

    static {
        java.util.Random r = new java.util.Random(0x9E3779B97F4A7C15L ^ GearHash.SEED);
        for (int i = 0; i < N_FEATURES; i++) {
            A[i] = r.nextLong() | 1L; // 奇数乘子，保证可逆混合
            B[i] = r.nextLong();
        }
    }

    private SuperFeature() {
    }

    /**
     * 计算一个对象的 K 个超特征。空/极短输入返回全 0（仍可比较，但通常不与他人共享）。
     */
    public static long[] compute(byte[] data) {
        long[] feat = new long[N_FEATURES];
        for (int i = 0; i < N_FEATURES; i++) {
            feat[i] = Long.MIN_VALUE;
        }
        long fp = 0L;
        for (int p = 0; p < data.length; p++) {
            fp = (fp << 1) + GearHash.TABLE[data[p] & 0xFF];
            if ((fp & SAMPLE_MASK) == 0) {
                for (int i = 0; i < N_FEATURES; i++) {
                    long t = A[i] * fp + B[i];
                    if (t > feat[i]) {
                        feat[i] = t;
                    }
                }
            }
        }
        long[] sf = new long[K_SUPER];
        for (int g = 0; g < K_SUPER; g++) {
            long acc = 0x100000001b3L; // FNV offset-like 起点
            for (int j = 0; j < GROUP; j++) {
                acc = mix(acc ^ feat[g * GROUP + j]);
            }
            sf[g] = acc;
        }
        return sf;
    }

    /** 64-bit 雪崩混合（SplitMix64 finalizer）。 */
    static long mix(long z) {
        z = (z ^ (z >>> 30)) * 0xbf58476d1ce4e5b9L;
        z = (z ^ (z >>> 27)) * 0x94d049bb133111ebL;
        return z ^ (z >>> 31);
    }
}
