package org.example.src.utils;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

/**
 * 解析指标文件的辅助类，等效于 Python 的 plot_graph.py 中的解析逻辑。
 * 注意：JDK 1.8 标准库不包含绘图能力，图表生成功能不实现。
 */
public final class PlotGraph {

    public static Map<String, Object> parseMetrics(String filePath) throws IOException {
        Map<String, Object> metrics = new HashMap<>();
        File f = new File(filePath);
        if (!f.exists()) return metrics;
        try (BufferedReader br = new BufferedReader(new FileReader(f))) {
            String[] lines = br.lines().toArray(String[]::new);
            int start = Math.max(0, lines.length - 13);
            for (int i = start; i < lines.length; i++) {
                String line = lines[i];
                // 简化解析：匹配 | key | value |
                int p1 = line.indexOf('|');
                if (p1 >= 0) {
                    int p2 = line.indexOf('|', p1 + 1);
                    int p3 = line.indexOf('|', p2 + 1);
                    if (p1 >= 0 && p2 >= 0 && p3 >= 0) {
                        String key = line.substring(p1 + 1, p2).trim();
                        String valueStr = line.substring(p2 + 1, p3).trim();
                        try {
                            metrics.put(key, Double.parseDouble(valueStr));
                        } catch (NumberFormatException e) {
                            metrics.put(key, valueStr);
                        }
                    }
                }
            }
        }
        return metrics;
    }

    private PlotGraph() {}
}

