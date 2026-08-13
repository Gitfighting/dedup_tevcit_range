# 2reg 全量在线 Prototype 深度分析 — 2026-08-12

- 实验形态：**在线真实文件系统**（store-server + sky-s3 + s3s-fs + manifest 真实文件体）
- 矩阵：T065 → T029 → T078 → T015 → T079 × {always_evict, always_store, t_even, skystore, dedup_aware_tevict}
- cache：`/data/ablation_exp/s3-local-cache`（禁止再用 `/tmp`）

## 1. 工作负载与磁盘

| Trace | ops | 逻辑GB | 可否全量 |
|---|---:|---:|---|
| T065 | 0.28M | 4.5 | ✅ 首选（PUT全在 east，GET全在 west） |
| T029 | ~13M | 9.7 | ✅ 容量OK，墙钟很长 |
| T078 | 2.41M | 37 | ✅ |
| T015 | 1.58M | 402 | ⚠️ 紧 |
| T079 | 0.11M | **581** | ❌ > 空闲~540G → skipped_disk |

## 2. 离线 verification 对照（同 typeE 2reg，非本实验目标）

| Trace | baseline$ | dedup$ | 节省% | hit | content_dedup |
|---|---:|---:|---:|---:|---:|
| T065 | 5.252 | 2.508 | 52.2 | 0.877 | 0.541 |
| T029 | 31.857 | 27.220 | 14.6 | 0.018 | 0.964 |
| T078 | 18.648 | 4.200 | 77.5 | 0.927 | 0.858 |
| T015 | 97.748 | 29.532 | 69.8 | 0.409 | 0.814 |
| T079 | 355.135 | 51.401 | 85.5 | — | 很高 |

离线只说明「同 TTL 下块级计费能省多少」；**作者策略差必须在线矩阵**。

## 3. 已修复的阻塞（否则全量结果无效）

1. **cache 盘**：`/tmp` 仅~43G → 迁 `/data`
2. **zombie 多矩阵抢端口**：flock + PID 强杀
3. **always_evict GET 全 404**：作者 `get_ttl()` 恒返回 0，primary 被 TTL 过滤；`get.py` OCP 保留 `is_primary` 可 locate（Head 有、Locate 无 → 已对齐）
4. CreateBucket 只建逻辑桶；boto3 流式真实 body

## 4. 当前执行

- 进程：单矩阵 + `T065 × always_evict` 全量回放中
- 监控：`/data/ablation_exp/logs/matrix_2reg.log`、`cell_T065_*_full.log`、`matrix_2reg_status.json`
- 刷新：`python scripts/cloud/analyze_online_2reg.py`

## 5. 深度结论框架（T065×5 出数后填表）

| 策略 | 预期 storage | 预期 network | 角色 |
|---|---|---|---|
| always_evict | 低 | 高 | 下界 |
| always_store | 高 | 低 | 上界 |
| t_even | 中 | 中 | 固定 TTL |
| skystore | 自适应 | 自适应 | 作者基线 |
| dedup_aware_tevict | 更低字节 | 组装/传输混合 | 本文扩展 |

真实内容 → CDC 可命中同族共享；随机填充会严重低估去重。
