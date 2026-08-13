package org.example.finesse;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Finesse 模块单元测试：分块稳定性、相似检测、块级 delta 的 r、端到端 runner。
 * 全程纯内存、不依赖外部文件/二进制，可在任意机器复现。
 */
class FinesseModuleTest {

    /** CDC 对相同内容必须产出相同分块（确定性、可复现）。 */
    @Test
    void cdcIsDeterministic() {
        byte[] data = randomBytes(2_000_000, 1L);
        FastCdc cdc = new FastCdc();
        Chunking a = cdc.chunk(data);
        Chunking b = cdc.chunk(data);
        assertEquals(a.chunkCount(), b.chunkCount());
        for (int i = 0; i < a.chunkCount(); i++) {
            assertEquals(a.hashes[i], b.hashes[i]);
            assertEquals(a.lengths[i], b.lengths[i]);
        }
        long sum = 0;
        for (int l : a.lengths) {
            sum += l;
        }
        assertEquals(data.length, sum, "分块长度之和必须等于原始大小");
    }

    /** 在文件中段插入少量字节，绝大多数块应保持不变（CDC 抗位移）。 */
    @Test
    void cdcResilientToInsertion() {
        byte[] base = randomBytes(1_000_000, 7L);
        byte[] modified = insertBytes(base, 500_000, new byte[]{1, 2, 3, 4, 5, 6, 7, 8});
        FastCdc cdc = new FastCdc();
        Chunking cb = cdc.chunk(base);
        Chunking cm = cdc.chunk(modified);

        java.util.Set<Long> baseSet = new java.util.HashSet<>();
        for (long h : cb.hashes) {
            baseSet.add(h);
        }
        int shared = 0;
        for (long h : cm.hashes) {
            if (baseSet.contains(h)) {
                shared++;
            }
        }
        double ratio = (double) shared / cm.chunkCount();
        assertTrue(ratio > 0.8, "插入后应仍共享 >80% 的块，实际=" + ratio);
    }

    /** 几乎相同的两个对象：相似检测应找到 base，且 r 很小。 */
    @Test
    void deltaRatioSmallForSimilarObjects() {
        byte[] v1 = randomBytes(2_000_000, 42L);
        byte[] v2 = insertBytes(v1, 1_000_000, randomBytes(2000, 99L)); // 仅改 0.1%

        List<FinesseRunner.NamedBytes> objs = new ArrayList<>();
        objs.add(new FinesseRunner.NamedBytes("v1", v1));
        objs.add(new FinesseRunner.NamedBytes("v2", v2));

        FinesseRunner.RunSummary s = FinesseRunner.run(objs, new FastCdc(),
                FinesseRunner.DEFAULT_DELTA_ACCEPT_MAX);

        assertEquals(2, s.records.size());
        FinesseRunner.Record r1 = s.records.get(0);
        FinesseRunner.Record r2 = s.records.get(1);
        assertTrue(r1.isBase, "第一个对象应为 base");
        assertNull(r1.baseKey);
        assertTrue(!r2.isBase, "相似的第二个对象应判为 delta 成员");
        assertEquals("v1", r2.baseKey);
        assertTrue(r2.deltaRatio < 0.1, "相似对象 r 应远小于 1，实际=" + r2.deltaRatio);
        assertEquals(1, s.drStats.baseCount);
        assertEquals(1, s.drStats.deltaCount);
    }

    /** 完全不相关的对象：不应被强行配成 delta（各自为 base）。 */
    @Test
    void unrelatedObjectsStayBases() {
        List<FinesseRunner.NamedBytes> objs = new ArrayList<>();
        objs.add(new FinesseRunner.NamedBytes("a", randomBytes(800_000, 1L)));
        objs.add(new FinesseRunner.NamedBytes("b", randomBytes(800_000, 2L)));
        objs.add(new FinesseRunner.NamedBytes("c", randomBytes(800_000, 3L)));

        FinesseRunner.RunSummary s = FinesseRunner.run(objs, new FastCdc(),
                FinesseRunner.DEFAULT_DELTA_ACCEPT_MAX);

        for (FinesseRunner.Record r : s.records) {
            assertTrue(r.isBase, "无关对象应各自为 base: " + r.key + " r=" + r.deltaRatio);
        }
        assertEquals(0, s.drStats.deltaCount);
    }

    /** identical 内容：r 应接近 0（全部块命中，仅 COPY 指令开销）。 */
    @Test
    void identicalObjectsHaveNearZeroR() {
        byte[] data = randomBytes(1_500_000, 123L);
        List<FinesseRunner.NamedBytes> objs = new ArrayList<>();
        objs.add(new FinesseRunner.NamedBytes("orig", data));
        objs.add(new FinesseRunner.NamedBytes("copy", data.clone()));

        FinesseRunner.RunSummary s = FinesseRunner.run(objs, new FastCdc(),
                FinesseRunner.DEFAULT_DELTA_ACCEPT_MAX);
        FinesseRunner.Record copy = s.records.get(1);
        assertTrue(!copy.isBase);
        assertEquals(0, copy.addedChunks, "完全相同应无新增块");
        assertTrue(copy.deltaRatio < 0.001, "完全相同 r 应≈0，实际=" + copy.deltaRatio);
    }

    /** 相似检测能在多候选中挑出真正最相似的 base。 */
    @Test
    void resemblanceIndexPicksMostSimilar() {
        byte[] x = randomBytes(1_200_000, 5L);
        byte[] near = insertBytes(x, 600_000, randomBytes(1000, 8L)); // 与 x 极相似
        byte[] far = randomBytes(1_200_000, 6L); // 与 x 无关

        FastCdc cdc = new FastCdc();
        ResemblanceIndex idx = new ResemblanceIndex();
        idx.add(profile("x", x, cdc));
        idx.add(profile("far", far, cdc));

        ResemblanceIndex.Match m = idx.findBestBase(profile("near", near, cdc));
        assertNotNull(m);
        assertEquals("x", m.base.key, "应选最相似的 x 而非无关的 far");
    }

    private static ObjectProfile profile(String key, byte[] data, FastCdc cdc) {
        return new ObjectProfile(key, data.length, cdc.chunk(data), SuperFeature.compute(data));
    }

    private static byte[] randomBytes(int n, long seed) {
        byte[] b = new byte[n];
        new Random(seed).nextBytes(b);
        return b;
    }

    private static byte[] insertBytes(byte[] src, int at, byte[] ins) {
        byte[] out = new byte[src.length + ins.length];
        System.arraycopy(src, 0, out, 0, at);
        System.arraycopy(ins, 0, out, at, ins.length);
        System.arraycopy(src, at, out, at + ins.length, src.length - at);
        return out;
    }
}
