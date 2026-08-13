# 第一遍代码走查与 Debug 记录

- 开始时刻：**2026-08-12 07:40 (UTC+8)**
- 结束时刻：**2026-08-12 ~07:55 (UTC+8)**
- 范围：live 原型栈（store-server + s3s-fs + sky-s3）+ DedupAware 扩展路径
- 约束：OCP，尽量不改作者策略核心 / `skyproxy.rs`

---

## 走查结论（摘要）

| 项 | 结果 |
|----|------|
| 三件套启停 | 可用（启动器） |
| S3 PUT/GET 往返 | **通过**（`match True`, 32768B） |
| Dedup CDC 后台 | **通过**（obj2 命中 index-dup，chunk 目录仅 1 个物理块） |
| 策略 sidecar 持久化 | **通过**（`/tmp/dedup_policy_state.json`） |

最终复现命令要点：`aws s3 mb` 注册元数据 → `aws s3 cp` PUT/GET；**不要**再对同一物理桶名 `register_buckets`。

---

## 发现并已修复的 Bug

| ID | 时刻 | 问题 | 修复（OCP） |
|----|------|------|-------------|
| W1 | ~07:42 | `set_obj_chunk_refs` 用物理 key，GET 用逻辑 key → 查不到 | `put.py` 改为 `logical_object.key` |
| W2 | ~07:43 | `complete_upload`/`start_upload` 未把 `dedup_aware` 当 skystore 处理时间戳 | 扩展分支对齐 |
| W3 | ~07:43 | 启动器仍可能装上 UltraDict → 触发 `db.py` thread bug | deps 跳过 UltraDict |
| W4 | ~07:43 | stop 杀不掉 `_run_uvicorn_ablation` | 按 wrapper cmdline 杀进程 |
| W5 | ~07:46 | 缺 `manual.csv` + 默认 `closest` → locate 500 | 默认 `cheapest` + `ensure_profiles` 拷贝 |
| W6 | ~07:47 | `get.py` 桶不存在时 `version_enabled[0]` IndexError | 返回 404 |
| W7 | ~07:48 | 作者 TTL SQL 用 Postgres `::interval`，SQLite 报 `unrecognized token :` | `LOCAL=true` 时改 strftime 方言 |
| W8 | ~07:52 | `LOCK TABLE` 在 SQLite 非法 | `LOCAL=true` 跳过 |
| W9 | ~07:52 | `VERSION_ENABLE=Enabled` 时 s3s-fs 无 PutBucketVersioning → sky-s3 panic | 保持 `NULL` |
| W10 | ~07:53 | `cdc_chunker.py`：`0xDEADBEEF_FASTCDC` 非法十六进制字面量 | 改为 `0xDEADBEEF` |

---

## 操作层注意（非代码缺陷，但会踩坑）

1. **sky-s3 启动只在 s3s-fs 建物理桶，不写 store-server 元数据** → 必须再 `aws s3 mb`（或等价 CreateBucket）才能 PUT。
2. **不要**对已由 sky-s3 CreateBucket 注册的同一 `physical bucket` 再 `register_buckets`（UNIQUE 冲突 → 500 → sky-s3 unwrap panic）。
3. sky-s3 多处 `.unwrap()`：元数据 500 会直接把代理进程打崩。

---

## 现场验证快照（~07:55）

```
PUT rc=0 / GET rc=0 / match True 32768
[dedup] walkthrough/obj1.bin: 1 chunks, 1 new, dedup_ratio=1.000
[dedup] walkthrough/obj2.bin: 1 chunks, 1 index-dup, 0 new, dedup_ratio=0.000
CHUNKS=1
STATE 含 obj_chunk_refs 逻辑 key + chunk_local_hits
LISTEN :3000 :8014 :8002
```

---

## 仍开放（本遍未闭环）

- HOT/DEDUP 决策在同区 PUT 冒烟中 `_obj_state` 仍为空（跨区/ttn 路径未在本遍压测）
- 作者 `db.py` UltraDict 分支 bug、`skyproxy` unwrap 仍在（靠环境绕过）
- 完整 `.mc-aug` 消融回放未跑
