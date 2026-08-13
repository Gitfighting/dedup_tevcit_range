package org.example.finesse;

import java.util.Set;

/**
 * 块级差分编码（去重式 delta），产出 patch 大小与差分率 r = |patch| / size(target)。
 *
 * <p>语义：把目标按 CDC 切块，逐块判断是否已存在于 base 的块集合：
 * <ul>
 *   <li>命中 → COPY 指令（只付极小指令开销 {@link #COPY_INSTR_BYTES}，不付数据字节）；</li>
 *   <li>未命中 → ADD 指令（付该块全部数据字节 + 指令头）。</li>
 * </ul>
 * patch = Σ(未命中块字节) + 指令开销。这是一种忠实且偏保守的 delta 估计（VCDiff 还能做
 * 子块级 COPY，故真实 r 通常 ≤ 本估计），自洽、可在纯 JVM 单测里复现，不依赖外部二进制。
 *
 * <p>设计 §8.1.2：相似检测（超特征）自实现，差分本身首版用块级 dedup；论文版可换标准 VCDiff
 * 库或 {@code zstd --patch-from} 做交叉校验。
 */
public final class DeltaCodec {

    /** COPY 指令头估计字节（地址+长度编码）。 */
    static final int COPY_INSTR_BYTES = 5;
    /** ADD 指令头估计字节。 */
    static final int ADD_INSTR_BYTES = 3;

    private DeltaCodec() {
    }

    /**
     * 计算 target 相对 base 的块级 delta。
     *
     * @return 差分结果（patch 字节数、命中/未命中块数、r）。
     */
    public static Result delta(ObjectProfile base, ObjectProfile target) {
        Set<Long> baseChunks = base.chunkSet();
        long patchBytes = 0L;
        int copied = 0;
        int added = 0;
        long copiedBytes = 0L;
        long[] th = target.chunking.hashes;
        int[] tl = target.chunking.lengths;
        for (int i = 0; i < th.length; i++) {
            if (baseChunks.contains(th[i])) {
                patchBytes += COPY_INSTR_BYTES;
                copiedBytes += tl[i];
                copied++;
            } else {
                patchBytes += (long) tl[i] + ADD_INSTR_BYTES;
                added++;
            }
        }
        long size = target.size;
        double r = size == 0 ? 0.0 : Math.min(1.0, (double) patchBytes / size);
        return new Result(patchBytes, size, copied, added, copiedBytes, r);
    }

    /** 块级差分结果。 */
    public static final class Result {
        public final long patchBytes;
        public final long targetBytes;
        public final int copiedChunks;
        public final int addedChunks;
        public final long copiedBytes;
        /** r = patch / target，限幅到 [0,1]。 */
        public final double deltaRatio;

        Result(long patchBytes, long targetBytes, int copiedChunks, int addedChunks,
               long copiedBytes, double deltaRatio) {
            this.patchBytes = patchBytes;
            this.targetBytes = targetBytes;
            this.copiedChunks = copiedChunks;
            this.addedChunks = addedChunks;
            this.copiedBytes = copiedBytes;
            this.deltaRatio = deltaRatio;
        }
    }
}
