package org.example.finesse;

import java.util.HashSet;
import java.util.Set;

/**
 * 一个对象的相似/差分画像：键、大小、分块结果与超特征。
 *
 * <p>分块结果同时用于：① 作为 base 时提供「块哈希集合」供目标做去重 delta；
 * ② 作为 target 时按块序判断哪些块可从 base 复制。
 */
public final class ObjectProfile {

    public final String key;
    public final long size;
    public final Chunking chunking;
    public final long[] superFeatures;

    private Set<Long> chunkSet; // 惰性构建，base 去重用

    public ObjectProfile(String key, long size, Chunking chunking, long[] superFeatures) {
        this.key = key;
        this.size = size;
        this.chunking = chunking;
        this.superFeatures = superFeatures;
    }

    /** base 端的块哈希集合（去重后），用于判断目标块能否复制。 */
    public Set<Long> chunkSet() {
        if (chunkSet == null) {
            Set<Long> s = new HashSet<>(chunking.chunkCount() * 2);
            for (long h : chunking.hashes) {
                s.add(h);
            }
            chunkSet = s;
        }
        return chunkSet;
    }
}
