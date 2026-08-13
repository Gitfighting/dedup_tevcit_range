# typeA–D 含义与实验计划 — 2026-08-11

## SkyStore 多云访问模式（来自 SNIA_traces yaml / multi_cloud_trace_new.py）

| Type | 定义 | 对跨区传输的影响 |
|------|------|------------------|
| **typeA** | PUT/GET 区域都随机 | 跨区 GET 多 → remote_fetch 高 → 传输费高 |
| **typeB** | 每个对象：PUT 固定一区，GET 固定另一（或同）区 | 访问局部性强 → 缓存命中高 → 传输低 |
| **typeC** | PUT 可多区随机，GET 收束到单一读区 | 读侧集中 → hit_cached 高 |
| **typeD** | PUT 固定一区，GET 分散到多区 | 写集中、读发散 → 类似 typeA 的高跨区拉取 |
| **typeE** | 2reg 基线布局（本实验用于 2reg） | 双区对照 |

T079 实测印证：typeA/D remote_fetch≈77k；typeB/C≈26–33k。

## 本次开跑范围
- **2reg (typeE)**：T015, T029, T065, T078, T079
- **9reg (typeA–D)**：T015, T065, T078, T079（不含 T029，按你的清单）
- T079 已完成，跳过重算；流水线顺序 T065→T078→T015→T029

## 高「压缩率」原因（内容去重，非 gzip）
对 T079：逻辑 623GB → 唯一块 85GB（86% 冗余），因 linux/ubuntu 同族 + CDC。
索引估算写入 `metrics.json`（Bloom+LMDB/区 + manifest）。
检索时延为设计值（Bloom≈0.1μs 否定，LMDB≈5–20μs）；回放用数组 O(1) 不计真实 LMDB。
