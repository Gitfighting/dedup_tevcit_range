package org.example.finesse;

import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

/**
 * 阶段 1 驱动：在真实数据集上用 Finesse 方法（超特征相似检测 + 块级差分）算出
 * <b>每个对象的真实差分率 r</b>，并汇成 <b>分布 D_r</b>，写出 manifest.csv 供方式 B 合成 trace。
 *
 * <p>流程（设计 §8.1.1 / §8.2）：按文件名顺序逐个对象处理——
 * <ol>
 *   <li>读字节 → FastCDC 分块 + 计算超特征；</li>
 *   <li>在已见对象里找最相似 base（共享超特征最多者）；</li>
 *   <li>找到则做块级 delta 得 r；r 过高（不够相似）则该对象自立为 base；</li>
 *   <li>把该对象登记进倒排索引，供后续对象复用。</li>
 * </ol>
 *
 * <p><b>重要</b>：必须用<em>未压缩</em>内容（如 .sql、解包后的 .tar），压缩文件会破坏相似性。
 *
 * <p>用法：
 * <pre>
 *   # 本地目录
 *   java -Xmx4g org.example.finesse.FinesseRunner &lt;inputDir&gt; [outManifest.csv] [datasetLabel]
 *
 *   # HTTP Range 在线读（kernel / sqldump / macos，见 config/finesse_http_datasets.json）
 *   java -Xmx4g org.example.finesse.FinesseRunner --http config/finesse_http_datasets.json kernel [out.csv]
 * </pre>
 */
public final class FinesseRunner {

    /** r 高于此阈值视为「不够相似」，目标自立为 base（避免把无关文件硬塞成 delta）。 */
    public static final double DEFAULT_DELTA_ACCEPT_MAX = 0.9;

    private FinesseRunner() {
    }

    /** 单个对象的产出记录。 */
    public static final class Record {
        public final String key;
        public final boolean isBase;
        public final String baseKey;
        public final long size;
        public final double deltaRatio;
        public final int sharedSf;
        public final int copiedChunks;
        public final int addedChunks;

        Record(String key, boolean isBase, String baseKey, long size, double deltaRatio,
               int sharedSf, int copiedChunks, int addedChunks) {
            this.key = key;
            this.isBase = isBase;
            this.baseKey = baseKey;
            this.size = size;
            this.deltaRatio = deltaRatio;
            this.sharedSf = sharedSf;
            this.copiedChunks = copiedChunks;
            this.addedChunks = addedChunks;
        }
    }

    /** 一次运行的汇总：逐对象记录 + D_r 统计。 */
    public static final class RunSummary {
        public final List<Record> records;
        public final DrStats drStats;

        RunSummary(List<Record> records, DrStats drStats) {
            this.records = records;
            this.drStats = drStats;
        }
    }

    /** delta 成员 r 的分布统计（论文要报告的 D_r）。 */
    public static final class DrStats {
        public final int deltaCount;
        public final int baseCount;
        public final double min, p10, p50, p90, max, mean;

        DrStats(int deltaCount, int baseCount, double min, double p10, double p50,
                double p90, double max, double mean) {
            this.deltaCount = deltaCount;
            this.baseCount = baseCount;
            this.min = min;
            this.p10 = p10;
            this.p50 = p50;
            this.p90 = p90;
            this.max = max;
            this.mean = mean;
        }
    }

    /**
     * 核心：对给定字节内容的对象序列建簇并算 r。便于单测直接喂内存数据。
     *
     * @param objects 顺序对象列表（key + 原始字节）
     */
    public static RunSummary run(List<NamedBytes> objects, FastCdc cdc, double deltaAcceptMax) {
        ResemblanceIndex index = new ResemblanceIndex();
        List<Record> records = new ArrayList<>();
        List<Double> rs = new ArrayList<>();
        for (NamedBytes nb : objects) {
            byte[] data = nb.bytes;
            Chunking ck = cdc.chunk(data);
            long[] sf = SuperFeature.compute(data);
            ObjectProfile prof = new ObjectProfile(nb.name, data.length, ck, sf);

            ResemblanceIndex.Match m = index.findBestBase(prof);
            Record rec;
            if (m != null) {
                DeltaCodec.Result d = DeltaCodec.delta(m.base, prof);
                if (d.deltaRatio < deltaAcceptMax) {
                    rec = new Record(prof.key, false, m.base.key, prof.size, d.deltaRatio,
                            m.sharedSuperFeatures, d.copiedChunks, d.addedChunks);
                    rs.add(d.deltaRatio);
                } else {
                    rec = new Record(prof.key, true, null, prof.size, 1.0, 0, 0, 0);
                }
            } else {
                rec = new Record(prof.key, true, null, prof.size, 1.0, 0, 0, 0);
            }
            records.add(rec);
            index.add(prof);
        }
        return new RunSummary(records, computeStats(rs, countBases(records)));
    }

    private static int countBases(List<Record> records) {
        int c = 0;
        for (Record r : records) {
            if (r.isBase) {
                c++;
            }
        }
        return c;
    }

    private static DrStats computeStats(List<Double> rs, int baseCount) {
        if (rs.isEmpty()) {
            return new DrStats(0, baseCount, 0, 0, 0, 0, 0, 0);
        }
        double[] a = new double[rs.size()];
        double sum = 0;
        for (int i = 0; i < a.length; i++) {
            a[i] = rs.get(i);
            sum += a[i];
        }
        Arrays.sort(a);
        return new DrStats(a.length, baseCount, a[0], percentile(a, 10), percentile(a, 50),
                percentile(a, 90), a[a.length - 1], sum / a.length);
    }

    private static double percentile(double[] sorted, double p) {
        if (sorted.length == 1) {
            return sorted[0];
        }
        double idx = (p / 100.0) * (sorted.length - 1);
        int lo = (int) Math.floor(idx);
        int hi = (int) Math.ceil(idx);
        double frac = idx - lo;
        return sorted[lo] + (sorted[hi] - sorted[lo]) * frac;
    }

    /** key + 原始字节。 */
    public static final class NamedBytes {
        public final String name;
        public final byte[] bytes;

        public NamedBytes(String name, byte[] bytes) {
            this.name = name;
            this.bytes = bytes;
        }
    }

    /**
     * 从 {@link ByteSource} 列表顺序加载并跑 Finesse（HTTP Range 或 file:）。
     */
    public static RunSummary runFromSources(List<ByteSource> sources, FastCdc cdc, double deltaAcceptMax)
            throws IOException {
        List<NamedBytes> objects = new ArrayList<>();
        for (ByteSource src : sources) {
            System.out.printf("  loading %-40s (%s)%n", src.name(), src.uri());
            long t0 = System.currentTimeMillis();
            byte[] bytes = src.readAll();
            long ms = System.currentTimeMillis() - t0;
            String mode = src.usedHttpRange() ? "HTTP-Range" : "local";
            System.out.printf("    -> %,d bytes in %.1fs [%s]%n", bytes.length, ms / 1000.0, mode);
            objects.add(new NamedBytes(src.name(), bytes));
        }
        return run(objects, cdc, deltaAcceptMax);
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            printUsage();
            System.exit(2);
        }
        if ("--http".equals(args[0])) {
            runHttpMode(args);
            return;
        }
        runLocalDirMode(args);
    }

    private static void runHttpMode(String[] args) throws IOException {
        if (args.length < 3) {
            printUsage();
            System.exit(2);
        }
        Path projectRoot = Paths.get("").toAbsolutePath().normalize();
        Path configPath = projectRoot.resolve(args[1]).normalize();
        String datasetId = args[2];
        String outPath = args.length >= 4 ? args[3] : "results/finesse_" + datasetId + "_http_manifest.csv";

        FinesseDatasetLoader.DatasetSpec spec = FinesseDatasetLoader.load(configPath, datasetId, projectRoot);
        System.out.printf("[Finesse] HTTP/local dataset=%s%n", spec.id);
        if (!spec.description.isEmpty()) {
            System.out.println("  note: " + spec.description);
        }
        if (spec.compressed) {
            System.out.println("  warning: 对象为压缩格式，r 仅作参考；论文级请在未压缩内容上重测。");
        }
        System.out.printf("  objects=%d, config=%s%n", spec.sources.size(), configPath);

        long t0 = System.currentTimeMillis();
        RunSummary s = runFromSources(spec.sources, new FastCdc(), DEFAULT_DELTA_ACCEPT_MAX);
        long ms = System.currentTimeMillis() - t0;

        writeManifest(outPath, spec.id, s.records);
        printStats(spec.id, s.drStats, ms);
    }

    private static void runLocalDirMode(String[] args) throws IOException {
        File dir = new File(args[0]);
        String outPath = args.length >= 2 ? args[1] : "results/finesse_manifest.csv";
        String label = args.length >= 3 ? args[2] : dir.getName();

        File[] listed = dir.listFiles(File::isFile);
        if (listed == null || listed.length == 0) {
            System.err.println("No files in " + dir.getAbsolutePath());
            System.exit(1);
            return;
        }
        Arrays.sort(listed, Comparator.comparing(File::getName));

        System.out.printf("[Finesse] dataset=%s, files=%d, dir=%s%n", label, listed.length, dir.getAbsolutePath());
        List<NamedBytes> objects = new ArrayList<>();
        for (File f : listed) {
            byte[] bytes = Files.readAllBytes(f.toPath());
            objects.add(new NamedBytes(f.getName(), bytes));
            System.out.printf("  loaded %-40s %,d bytes%n", f.getName(), bytes.length);
        }

        long t0 = System.currentTimeMillis();
        RunSummary s = run(objects, new FastCdc(), DEFAULT_DELTA_ACCEPT_MAX);
        long ms = System.currentTimeMillis() - t0;

        writeManifest(outPath, label, s.records);
        printStats(label, s.drStats, ms);
    }

    private static void printUsage() {
        System.err.println("Usage:");
        System.err.println("  FinesseRunner <inputDir> [outManifest.csv] [datasetLabel]");
        System.err.println("  FinesseRunner --http <config.json> <datasetId> [outManifest.csv]");
        System.err.println("  datasetId: kernel | sqldump | macos (see config/finesse_http_datasets.json)");
    }

    static void writeManifest(String outPath, String label, List<Record> records) throws IOException {
        File out = new File(outPath);
        if (out.getParentFile() != null) {
            out.getParentFile().mkdirs();
        }
        try (PrintWriter w = new PrintWriter(out, "UTF-8")) {
            w.println("dataset,logical_key,is_base,base_key,size,delta_ratio,shared_sf,copied_chunks,added_chunks");
            for (Record r : records) {
                w.printf("%s,%s,%d,%s,%d,%.8f,%d,%d,%d%n",
                        label, r.key, r.isBase ? 1 : 0,
                        r.baseKey == null ? "" : r.baseKey,
                        r.size, r.deltaRatio, r.sharedSf, r.copiedChunks, r.addedChunks);
            }
        }
        System.out.println("[Finesse] manifest -> " + out.getAbsolutePath());
    }

    static void printStats(String label, DrStats d, long ms) {
        System.out.println("----- D_r (delta members) -----");
        System.out.printf("dataset=%s  bases=%d  delta_members=%d  (%.1fs)%n",
                label, d.baseCount, d.deltaCount, ms / 1000.0);
        if (d.deltaCount > 0) {
            System.out.printf("r: min=%.6f  p10=%.6f  p50=%.6f  p90=%.6f  max=%.6f  mean=%.6f%n",
                    d.min, d.p10, d.p50, d.p90, d.max, d.mean);
        } else {
            System.out.println("r: (no delta members found — all objects are bases)");
        }
    }
}
