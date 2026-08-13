package org.example.finesse;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 超特征倒排索引：超特征值 → 拥有它的对象列表。
 *
 * <p>给定一个新对象，收集所有「与它共享 ≥1 超特征」的候选 base，按共享超特征数排序，
 * 取共享最多者作为最相似 base（平票时取更大的对象，base 越大被复用价值越高）。
 *
 * <p>这正是 Finesse 选 base 的判据：相似度由共享超特征数近似，而非穷举两两比对。
 */
public final class ResemblanceIndex {

    private final Map<Long, List<ObjectProfile>> postings = new HashMap<>();

    /** 把对象的全部超特征登记进倒排表，使其可作为后续对象的候选 base。 */
    public void add(ObjectProfile obj) {
        for (long sf : obj.superFeatures) {
            postings.computeIfAbsent(sf, k -> new java.util.ArrayList<>()).add(obj);
        }
    }

    /**
     * 为 {@code target} 找最相似的已登记 base。
     *
     * @return 最佳 base 与共享超特征数；若无任何候选返回 null。
     */
    public Match findBestBase(ObjectProfile target) {
        Map<ObjectProfile, Integer> shared = new HashMap<>();
        for (long sf : target.superFeatures) {
            List<ObjectProfile> lst = postings.get(sf);
            if (lst == null) {
                continue;
            }
            for (ObjectProfile cand : lst) {
                if (cand == target) {
                    continue;
                }
                shared.merge(cand, 1, Integer::sum);
            }
        }
        ObjectProfile best = null;
        int bestShared = 0;
        for (Map.Entry<ObjectProfile, Integer> e : shared.entrySet()) {
            int c = e.getValue();
            if (c > bestShared || (c == bestShared && best != null && e.getKey().size > best.size)) {
                best = e.getKey();
                bestShared = c;
            }
        }
        return best == null ? null : new Match(best, bestShared);
    }

    /** 一次相似匹配的结果。 */
    public static final class Match {
        public final ObjectProfile base;
        public final int sharedSuperFeatures;

        Match(ObjectProfile base, int sharedSuperFeatures) {
            this.base = base;
            this.sharedSuperFeatures = sharedSuperFeatures;
        }
    }
}
