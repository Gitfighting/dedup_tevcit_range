package org.example.finesse;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/** 本地 file: 来源（sqldump 等已在磁盘上的未压缩明文）。 */
public final class LocalByteSource implements ByteSource {

    private final String name;
    private final String uri;
    private final Path path;

    public LocalByteSource(String name, String uri, Path path) {
        this.name = name;
        this.uri = uri;
        this.path = path;
    }

    public static LocalByteSource fromUri(String name, String uri, Path projectRoot) throws IOException {
        String u = uri.trim();
        Path p;
        if (u.startsWith("file:")) {
            String rest = u.substring(5);
            while (rest.startsWith("/") && rest.length() > 2 && rest.charAt(2) == ':') {
                rest = rest.substring(1); // file:/D:/... -> D:/...
            }
            p = Paths.get(rest);
        } else {
            p = Paths.get(u);
        }
        if (!p.isAbsolute()) {
            p = projectRoot.resolve(p).normalize();
        }
        if (!Files.isRegularFile(p)) {
            throw new IOException("Local file not found: " + p);
        }
        return new LocalByteSource(name, uri, p);
    }

    @Override
    public String name() {
        return name;
    }

    @Override
    public String uri() {
        return uri;
    }

    @Override
    public byte[] readAll() throws IOException {
        return Files.readAllBytes(path);
    }

    @Override
    public boolean usedHttpRange() {
        return false;
    }

    public Path path() {
        return path;
    }
}
