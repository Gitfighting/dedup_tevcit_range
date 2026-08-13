package org.example.finesse;

import java.io.IOException;

/**
 * 对象字节的来源：本地文件或 HTTP Range 顺序拉取（设计 §8.2.1）。
 *
 * <p>读完即处理、不持久化到磁盘；调用方负责在算完 r 后丢弃引用。
 */
public interface ByteSource {

    /** 逻辑对象名（写入 manifest 的 logical_key）。 */
    String name();

    /** 声明的来源 URI（http(s):// 或 file:）。 */
    String uri();

    /** 顺序读取全部字节（Range 分块拉取，仅在内存中组装）。 */
    byte[] readAll() throws IOException;

    /** 是否通过 HTTP Range 获取（用于日志/报告）。 */
    boolean usedHttpRange();
}
