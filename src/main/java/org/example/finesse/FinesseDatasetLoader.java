package org.example.finesse;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 从 {@code config/finesse_http_datasets.json} 加载数据集对象列表。
 *
 * <p>每个 object：{@code name} + {@code url}（https:// 走 Range；{@code file:} 走本地）。
 */
public final class FinesseDatasetLoader {

    private FinesseDatasetLoader() {
    }

    public static final class DatasetSpec {
        public final String id;
        public final String description;
        public final boolean compressed;
        public final List<ByteSource> sources;

        DatasetSpec(String id, String description, boolean compressed, List<ByteSource> sources) {
            this.id = id;
            this.description = description;
            this.compressed = compressed;
            this.sources = sources;
        }
    }

    public static DatasetSpec load(Path configPath, String datasetId, Path projectRoot) throws IOException {
        JsonObject root;
        try (Reader r = Files.newBufferedReader(configPath, StandardCharsets.UTF_8)) {
            root = JsonParser.parseReader(r).getAsJsonObject();
        }
        JsonObject datasets = root.getAsJsonObject("datasets");
        if (datasets == null || !datasets.has(datasetId)) {
            Set<String> keys = datasets == null ? java.util.Collections.emptySet() : datasets.keySet();
            throw new IOException("Unknown dataset '" + datasetId + "'. Available: " + keys);
        }
        JsonObject ds = datasets.getAsJsonObject(datasetId);
        String desc = ds.has("description") ? ds.get("description").getAsString() : "";
        boolean compressed = ds.has("compressed") && ds.get("compressed").getAsBoolean();
        JsonArray objects = ds.getAsJsonArray("objects");
        if (objects == null || objects.size() == 0) {
            throw new IOException("Dataset '" + datasetId + "' has no objects");
        }
        List<ByteSource> sources = new ArrayList<>();
        for (JsonElement el : objects) {
            JsonObject o = el.getAsJsonObject();
            String name = o.get("name").getAsString();
            String url = o.get("url").getAsString();
            sources.add(resolveSource(name, url, projectRoot));
        }
        return new DatasetSpec(datasetId, desc, compressed, sources);
    }

    private static ByteSource resolveSource(String name, String url, Path projectRoot) throws IOException {
        String u = url.trim();
        if (u.startsWith("http://") || u.startsWith("https://")) {
            return new HttpRangeByteSource(name, u);
        }
        return LocalByteSource.fromUri(name, u.startsWith("file:") ? u : "file:" + u, projectRoot);
    }

    public static List<String> listDatasetIds(Path configPath) throws IOException {
        JsonObject root;
        try (Reader r = Files.newBufferedReader(configPath, StandardCharsets.UTF_8)) {
            root = JsonParser.parseReader(r).getAsJsonObject();
        }
        JsonObject datasets = root.getAsJsonObject("datasets");
        List<String> ids = new ArrayList<>();
        if (datasets != null) {
            for (Map.Entry<String, JsonElement> e : datasets.entrySet()) {
                ids.add(e.getKey());
            }
        }
        return ids;
    }
}
