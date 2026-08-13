package org.example.finesse;

/**
 * 一个对象经 FastCDC 切分后的结果：按出现顺序排列的块哈希与块长度。
 *
 * <p>{@code hashes[i]} 是第 i 个块内容的 64-bit 指纹，{@code lengths[i]} 是其字节数。
 * 顺序保留是为了块级 delta（按目标块序判断是否能从 base 复制）。
 */
public final class Chunking {

    public final long[] hashes;
    public final int[] lengths;
    public final long totalBytes;

    Chunking(long[] hashes, int[] lengths, long totalBytes) {
        this.hashes = hashes;
        this.lengths = lengths;
        this.totalBytes = totalBytes;
    }

    public int chunkCount() {
        return hashes.length;
    }
}
