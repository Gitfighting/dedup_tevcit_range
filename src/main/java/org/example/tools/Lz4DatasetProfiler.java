package org.example.tools;

import net.jpountz.lz4.LZ4FrameOutputStream;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/** Measures actual LZ4 frame sizes without materializing compressed files. */
public final class Lz4DatasetProfiler {
    private Lz4DatasetProfiler() {
    }

    private static final class CountingOutputStream extends OutputStream {
        long count;

        @Override
        public void write(int b) {
            count++;
        }

        @Override
        public void write(byte[] b, int off, int len) {
            count += len;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("Usage: Lz4DatasetProfiler <dataset-directory> <output-csv>");
            System.exit(2);
        }
        Path dataset = Paths.get(args[0]);
        Path output = Paths.get(args[1]);
        if (!Files.isDirectory(dataset)) {
            throw new IllegalArgumentException("Dataset directory not found: " + dataset);
        }

        List<Path> files = new ArrayList<>();
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(dataset, "*.tar")) {
            for (Path file : stream) {
                files.add(file);
            }
        }
        Collections.sort(files, Comparator.comparing(p -> p.getFileName().toString()));
        if (files.isEmpty()) {
            throw new IllegalArgumentException("No *.tar files found in " + dataset);
        }

        if (output.getParent() != null) {
            Files.createDirectories(output.getParent());
        }
        try (java.io.BufferedWriter writer = Files.newBufferedWriter(output,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING)) {
            writer.write("obj_key,logical_size,compressed_size,compression_ratio");
            writer.newLine();
            for (Path file : files) {
                long logical = Files.size(file);
                long compressed = compressedSize(file);
                double ratio = logical == 0 ? 1.0 : (double) compressed / logical;
                writer.write(file.getFileName().toString());
                writer.write(",");
                writer.write(Long.toString(logical));
                writer.write(",");
                writer.write(Long.toString(compressed));
                writer.write(",");
                writer.write(String.format(java.util.Locale.ROOT, "%.9f", ratio));
                writer.newLine();
                writer.flush();
                System.out.printf(java.util.Locale.ROOT, "%s: %,d -> %,d bytes (%.4f)%n",
                        file.getFileName(), logical, compressed, ratio);
            }
        }
        System.out.println("Wrote profile: " + output.toAbsolutePath());
    }

    private static long compressedSize(Path file) throws IOException {
        CountingOutputStream counter = new CountingOutputStream();
        byte[] buffer = new byte[1024 * 1024];
        try (BufferedInputStream input = new BufferedInputStream(Files.newInputStream(file), buffer.length);
                LZ4FrameOutputStream lz4 = new LZ4FrameOutputStream(new BufferedOutputStream(counter, buffer.length))) {
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) {
                    lz4.write(buffer, 0, read);
                }
            }
        }
        return counter.count;
    }
}
