package org.example.finesse;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * HTTP Range 顺序流式读取（RFC 7233），不落盘。
 *
 * <p>实现要点（设计 §8.2.1）：
 * <ul>
 *   <li>先 HEAD 探测 {@code Content-Length} 与 {@code Accept-Ranges: bytes}；</li>
 *   <li>支持 Range 时按固定块（默认 4MB）顺序请求 {@code Range: bytes=start-end}；</li>
 *   <li>不支持 Range 时退回单次 GET 流式读入内存；</li>
 *   <li>省磁盘、不省带宽——全文仍须过一遍网络。</li>
 * </ul>
 */
public final class HttpRangeByteSource implements ByteSource {

    /** 默认 Range 块大小 4MB。 */
    public static final int DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024;

    private static final int CONNECT_MS = 30_000;
    private static final int READ_MS = 300_000;
    private static final int MAX_RETRIES = 5;

    private final String name;
    private final String url;
    private final int chunkBytes;
    private boolean usedRange;

    public HttpRangeByteSource(String name, String url) {
        this(name, url, DEFAULT_CHUNK_BYTES);
    }

    public HttpRangeByteSource(String name, String url, int chunkBytes) {
        this.name = name;
        this.url = url;
        this.chunkBytes = Math.max(64 * 1024, chunkBytes);
    }

    @Override
    public String name() {
        return name;
    }

    @Override
    public String uri() {
        return url;
    }

    @Override
    public boolean usedHttpRange() {
        return usedRange;
    }

    @Override
    public byte[] readAll() throws IOException {
        RangeProbeResult info = probe(url);
        if (info.contentLength <= 0) {
            return readGetStream(url);
        }
        if (!info.acceptRanges) {
            System.out.printf("  [http] %s: server无 Range，退回 GET 流式 (%d bytes)%n",
                    name, info.contentLength);
            return readGetStream(url);
        }
        usedRange = true;
        ByteArrayOutputStream out = new ByteArrayOutputStream((int) Math.min(info.contentLength, Integer.MAX_VALUE));
        long pos = 0;
        int chunk = 0;
        while (pos < info.contentLength) {
            long end = Math.min(pos + chunkBytes - 1L, info.contentLength - 1);
            byte[] part = readRange(url, pos, end);
            out.write(part, 0, part.length);
            pos = end + 1;
            chunk++;
            if (chunk % 8 == 0 || pos >= info.contentLength) {
                System.out.printf("  [http] %s: Range %d/%d chunks, %,d / %,d bytes%n",
                        name, chunk, (info.contentLength + chunkBytes - 1) / chunkBytes,
                        pos, info.contentLength);
            }
        }
        return out.toByteArray();
    }

    /** HEAD 探测结果（供测试与诊断）。 */
    public static final class RangeProbeResult {
        public final long contentLength;
        public final boolean acceptRanges;

        RangeProbeResult(long contentLength, boolean acceptRanges) {
            this.contentLength = contentLength;
            this.acceptRanges = acceptRanges;
        }
    }

    public static RangeProbeResult probe(String urlStr) throws IOException {
        HttpURLConnection conn = open(urlStr, "HEAD");
        try {
            conn.connect();
            int code = conn.getResponseCode();
            if (code >= 400) {
                throw new IOException("HEAD " + urlStr + " -> HTTP " + code);
            }
            long len = conn.getContentLengthLong();
            if (len < 0) {
                String cl = conn.getHeaderField("Content-Length");
                if (cl != null) {
                    len = Long.parseLong(cl.trim());
                }
            }
            String ar = conn.getHeaderField("Accept-Ranges");
            boolean ranges = ar != null && ar.toLowerCase().contains("bytes");
            return new RangeProbeResult(len, ranges);
        } finally {
            conn.disconnect();
        }
    }

    private static byte[] readRange(String urlStr, long start, long end) throws IOException {
        return withRetry(() -> {
            HttpURLConnection conn = open(urlStr, "GET");
            conn.setRequestProperty("Range", "bytes=" + start + "-" + end);
            try {
                int code = conn.getResponseCode();
                if (code != HttpURLConnection.HTTP_PARTIAL && code != HttpURLConnection.HTTP_OK) {
                    throw new IOException("Range GET " + urlStr + " [" + start + "-" + end + "] -> HTTP " + code);
                }
                return readStream(conn.getInputStream());
            } finally {
                conn.disconnect();
            }
        }, "Range " + start + "-" + end + " " + urlStr);
    }

    private static byte[] readGetStream(String urlStr) throws IOException {
        return withRetry(() -> {
            HttpURLConnection conn = open(urlStr, "GET");
            try {
                int code = conn.getResponseCode();
                if (code >= 400) {
                    throw new IOException("GET " + urlStr + " -> HTTP " + code);
                }
                return readStream(conn.getInputStream());
            } finally {
                conn.disconnect();
            }
        }, "GET " + urlStr);
    }

    private interface IoSupplier<T> {
        T get() throws IOException;
    }

    private static byte[] withRetry(IoSupplier<byte[]> action, String label) throws IOException {
        IOException last = null;
        for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            try {
                return action.get();
            } catch (IOException e) {
                last = e;
                if (attempt < MAX_RETRIES) {
                    long sleep = 2000L * attempt;
                    System.out.printf("  [http] retry %d/%d %s: %s (sleep %dms)%n",
                            attempt, MAX_RETRIES, label, e.getMessage(), sleep);
                    try {
                        Thread.sleep(sleep);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        throw new IOException("Interrupted during retry", ie);
                    }
                }
            }
        }
        throw last;
    }

    private static byte[] readStream(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] tmp = new byte[8192];
        int n;
        while ((n = in.read(tmp)) >= 0) {
            if (n > 0) {
                buf.write(tmp, 0, n);
            }
        }
        return buf.toByteArray();
    }

    private static HttpURLConnection open(String urlStr, String method) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
        conn.setRequestMethod(method);
        conn.setConnectTimeout(CONNECT_MS);
        conn.setReadTimeout(READ_MS);
        conn.setInstanceFollowRedirects(true);
        conn.setRequestProperty("User-Agent", "Skystore1212-Finesse/1.0");
        return conn;
    }
}
