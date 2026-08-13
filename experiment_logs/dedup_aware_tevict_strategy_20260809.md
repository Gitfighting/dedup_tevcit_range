# DedupAwareTevict 策略设计文档

> **创建时间**: 2026-08-09 07:37 UTC+8 (北京时间 15:37)
> **作者**: CloudSun
> **关联论文基线**: SkyStore (VLDB 2025) — `skystore.pdf`
> **基线代码**: `skystore_python/artifact-eval-main/prototype/`

---

## 1. 问题背景

### 1.1 传统去重的核心矛盾

在跨 region / 跨云对象存储中引入传统去重（CDC分块 + SHA-256/BLAKE3指纹去重）可以大幅降低**存储成本**，但会引发**传输成本爆炸**：

```
PUT:  文件 → CDC分块 → 指纹查重 → 重复块不存 → 存储成本 ↓↓
GET:  按块清单从多region fetch → 组装 → 传输成本 ↑↑ (跨region egress费用昂贵)

净效果:
  存储节省 << 传输增加 → 总成本反而更高 → 策略无效
```

**示例**: 一个 1GB 对象分为 256 个 4MB 块，去重后 200 个块已存在于 3 个不同 region。GET 时这 200 个块需要 3 次跨 region 传输，每次产生 egress 费用。而如果该对象被反复访问（如 T65 中 hot 对象有 100+ 次 GET），传输费用将远超去重节省的存储费用。

### 1.2 SkyStore 已有策略的局限性

| 策略 | 机制 | 弱点 |
|---|---|---|
| `skystore` (自适应 TTL) | histogram 学习 Tnext 分布 → 选最优 TTL | 仅管理完整副本，未利用去重消除冗余 |
| `always_store` | 每次 GET 后存完整副本 | 存储随 region 数量线性增长 |
| `always_evict` | 每次 GET 后立即驱逐 | 反复 GET 产生巨额传输费 |
| `t_even` | 固定 break-even TTL | 不感知访问模式变化 |

**SkyStore 论文公式** (Section 3.2.2):

```
ExpectedCost(TTL) = Σ I[fetched remotely] · N                           (初始读)
                   + Σ_{t(j)≤TTL} hist(j) · ̄t(j) · S                    (命中)
                   + Σ_{t(j)>TTL} hist(j) · (N + TTL · S)               (未命中)
                   + Σ_{j∈last} last(j) · TTL · S                       (最后访问)
```

**问题**: 该公式假设每个对象是**完整物理副本**。去重后，对象以 chunk manifest 形式存在，每个 chunk 可能分布在不同的 region。GET 时需要重新考虑 chunk 级别的传输成本。

---

## 2. 核心设计思想

### 2.1 协同策略: 去重 + 自适应 TTL

**核心原则**: **冷数据去重存引用，热数据组装成物理副本缓存本地**

```
对象状态机:

                    ┌──────────┐
           PUT      │  去重态   │  PUT: CDC分块 → 重复块不存
         ─────────▶│ DEDUP_STATE│       仅保留 chunk_manifest
                    │ (chunk引用)│
                    └─────┬────┘
                          │
                          │ GET(obj)
                          │
                    ┌─────▼────┐
              Yes    │  Tnext   │  No
          ┌──────────│  < TTL?  │──────────┐
          │          └──────────┘          │
          ▼                                ▼
   ┌─────────────┐                  ┌──────────────┐
   │   热态       │                  │  保持去重态    │
   │ HOT_STATE   │                  │ (按需fetch)   │
   │ (完整副本)   │                  │ 仅传输费      │
   │ 本地0传输费  │                  └──────────────┘
   └──────┬──────┘
          │ TTL过期
          ▼
   ┌─────────────┐
   │  降级回      │
   │  DEDUP_STATE │
   │  (保留引用)  │
   └─────────────┘
```

### 2.2 形式化定义

```
策略: DedupAwareTevict

PUT(obj, content):
    chunks = FastCDC.chunk(content)              # 内容定义分块
    fps = [BLAKE3(c) for c in chunks]            # 加密哈希指纹
    locations = DedupIndex.lookup(fps)           # 全局去重索引

    new_chunks = [(c, fp) for (c,fp) in zip(chunks,fps)
                  if fp not in DedupIndex]

    for c, fp in new_chunks:
        DedupStore.put(obj.issue_region, fp, c)  # 唯一块写入本地region
        DedupIndex.insert(fp, obj.issue_region)

    obj.state = DEDUP_STATE
    obj.chunk_manifest = locations               # [fp → region] 清单

GET(obj, current_region):
    if obj.state == HOT_STATE and obj.replica_region == current_region:
        return local_read(obj)                   # 本地完整副本命中

    time_to_next = get_time_to_next_access(obj)

    if time_to_next < T_even:
        # 热数据: 第1次GET → 组装 → 存完整副本 → 后续GET 0传输费
        full_obj = DedupEngine.assemble(obj.chunk_manifest)
        store_full_replica(current_region, full_obj)
        obj.state = HOT_STATE
        obj.replica_region = current_region
        obj.ttl = T_even                         # T_even 后降级回 DEDUP_STATE
        cost = transfer_cost(assemble) + storage_cost(full_copy, T_even)
    else:
        # 冷数据: 仅按需fetch → 组装 → 返回 → 不留完整副本
        full_obj = DedupEngine.assemble(obj.chunk_manifest)
        cost = transfer_cost(assemble)
        # 对象保持 DEDUP_STATE

    return cost

EVICT(background, timestamp):
    for obj in objects:
        if obj.state == HOT_STATE and ttl_expired(obj, timestamp):
            delete_full_replica(obj)
            obj.state = DEDUP_STATE              # 降级: 保留chunk引用
            obj.replica_region = None
```

### 2.3 成本分析（与纯策略对比）

```
场景1: 频繁 GET 的热对象 (T65: 67% hot, avg 93 GETs)
  +------------------+---------------------+----------------------+
  | 策略             | 存储成本            | 传输成本             |
  +------------------+---------------------+----------------------+
  | AlwaysEvict+Dedup| ≈0 (仅chunk引用)    | 93 × assemble_cost  |
  | AlwaysStore+Dedup| 1个完整副本 × N天   | 1 × assemble_cost   |
  | DedupAwareTevict | 1个完整副本 × T_even| 1 × assemble_cost   |
  +------------------+---------------------+----------------------+
  → DedupAwareTevict 在传输上与 AlwaysStore 持平，存储优于 AlwaysStore

场景2: 一次性的冷对象 (T15: 48% one-hit wonders)
  +------------------+---------------------+----------------------+
  | 策略             | 存储成本            | 传输成本             |
  +------------------+---------------------+----------------------+
  | AlwaysEvict+Dedup| ≈0 (仅chunk引用)    | 1 × assemble_cost   |
  | AlwaysStore+Dedup| 完整副本 × N天      | 1 × assemble_cost   |
  | DedupAwareTevict | 保持去重态, ≈0      | 1 × assemble_cost   |
  +------------------+---------------------+----------------------+
  → DedupAwareTevict 跟踪 AlwaysEvict，优于 AlwaysStore

场景3: 稀疏访问的温数据 (T29: 98% cold)
  +------------------+---------------------+----------------------+
  | 策略             | 存储成本            | 传输成本             |
  +------------------+---------------------+----------------------+
  | AlwaysEvict+Dedup| ≈0                  | k × assemble_cost   |
  | AlwaysStore+Dedup| 完整副本 × 全程     | k × assemble_cost   |
  | DedupAwareTevict | Tnext<T_even才存    | 自适应最小化         |
  +------------------+---------------------+----------------------+
  → DedupAwareTevict 动态选择: 间隔短→缓存, 间隔长→保持去重态
```

---

## 3. 去重组件技术选型

### 3.1 分块算法: FastCDC

- **来源**: Wen Xia et al., "FastCDC: a Fast and Efficient Content-Defined Chunking Approach for Data Deduplication", USENIX ATC 2016
- **原理**: 基于滚动hash的CDC，通过 Gear hash + 归一化 chunk size 分布优化速度
- **相比 Rabin CDC**: ~10x 更快，chunk 大小分布更均匀
- **参数**: min_chunk=2KB, avg_chunk=8KB, max_chunk=64KB

### 3.2 指纹算法: BLAKE3

- **替代原因**: SHA-256 速度慢 (~500 MB/s)，BLAKE3 可达 ~5 GB/s（单核）
- **安全级别**: 256-bit 输出，碰撞概率 ≈ 2^-256，满足去重需求
- **Python 实现**: `pip install blake3`

### 3.3 去重索引: 两阶段索引

```
阶段1: 内存布隆过滤器 (Bloom Filter)
  - 快速否定: 判断 chunk 是否"可能"已存在
  - 内存占用: ~100MB for 10M chunks @ 1% false positive

阶段2: 磁盘哈希表 (LMDB / SQLite)
  - 精确查询: fingerprint → [region, offset]
  - 持久化, crash-safe
```

### 3.4 重组优化: 最大化本地命中率

```
GET 时重组策略:
  1. 遍历 chunk_manifest
  2. 统计每个 region 拥有的 chunk 数量
  3. 优先从拥有最多 chunk 的 region 拉取批次 (减少跨region请求数)
  4. 剩余零散 chunk 从最近/最便宜的 region 拉取
```

---

## 4. 系统架构

### 4.1 组件关系图

```
┌─────────────────────────────────────────────────────────┐
│                   ECS 云服务器                             │
│                                                           │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐ │
│  │  run_client │───▶│  s3-proxy    │───▶│  s3s-fs      │ │
│  │  (实验编排)  │    │  (Rust,8002) │    │  (本地S3,8014)│ │
│  └─────────────┘    └──────┬───────┘    └──────────────┘ │
│                            │                              │
│                     ┌──────▼───────┐                      │
│                     │ store-server  │                     │
│                     │ (Python,8000) │                     │
│                     └──────┬───────┘                      │
│                            │                              │
│         ┌──────────────────┼──────────────────┐          │
│         │                  │                  │          │
│  ┌──────▼──────┐  ┌───────▼───────┐  ┌───────▼───────┐  │
│  │ PostgreSQL  │  │ DedupEngine  │  │  策略引擎      │  │
│  │ (元数据)    │  │ (去重引擎)   │  │  Placement    │  │
│  └─────────────┘  └───────┬───────┘  │  Policy       │  │
│                           │          └───────────────┘  │
│                  ┌────────▼────────┐                    │
│                  │ DedupIndex      │                    │
│                  │ (LMDB+布隆)     │                    │
│                  └────────┬────────┘                    │
│                           │                              │
│                  ┌────────▼────────┐                    │
│                  │ /data/dedup/    │                    │
│                  │ (块存储目录)    │                    │
│                  └─────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

### 4.2 新增模块

```
prototype/
├── dedup/                              ← [新增] 去重引擎模块
│   ├── __init__.py
│   ├── cdc_chunker.py                  ← FastCDC 分块
│   ├── dedup_index.py                  ← 两阶段索引 (Bloom + LMDB)
│   ├── dedup_store.py                  ← 块存储
│   └── dedup_engine.py                 ← 统一接口 (chunk/assemble)
│
├── store-server/
│   └── operations/
│       ├── object_operations/
│       │   ├── put.py                  ← [修改] PUT 时调用 DedupEngine
│       │   └── get.py                  ← [修改] GET 时调用 DedupEngine.assemble
│       │
│       └── policy/
│           └── placement_policy/
│               ├── get_placement.py    ← [修改] 注册新策略
│               └── policy_dedup_aware_tevict.py  ← [新增] DedupAwareTevict
```

---

## 5. 实验设计

### 5.1 消融实验矩阵

| 实验组 ID | 去重 | 缓存策略 | 描述 |
|---|---|---|---|
| **G1** | ❌ 无 | `always_evict` | 纯驱逐基线 |
| **G2** | ❌ 无 | `always_store` | 纯存储基线 |
| **G3** | ❌ 无 | `t_even` | 盈亏平衡基线 |
| **G4** | ❌ 无 | `skystore` (自适应TTL) | SkyStore 原始策略 |
| **G5** | ✅ FastCDC+BLAKE3 | `always_evict` (TTL=0) | 纯去重 (无缓存) |
| **G6** | ✅ FastCDC+BLAKE3 | `always_store` (TTL=∞) | 去重+全缓存 |
| **G7** | ✅ FastCDC+BLAKE3 | `t_even` (固定TTL) | 去重+固定TTL |
| **G8** | ✅ FastCDC+BLAKE3 | `DedupAwareTevict` (自适应) | **本论文策略** |

### 5.2 消融维度

| 维度 | 对比组 | 解决的问题 |
|---|---|---|
| **去重效果** | G1→G5, G2→G6, G2→G8 | 去重能省多少存储？ |
| **自适应TTL** | G7→G8 | 自适应 vs 固定 break-even TTL |
| **协同 vs 分离** | G6→G8 | 去重+TTL 协同决策 vs 独立决策 |
| **vs 原始SkyStore** | G4→G8 | 引入去重后的净收益 |

### 5.3 轨迹选择

| Trace | 特征 | 关键检验点 |
|---|---|---|
| T15 | 48% one-hit, 80% small objects | 冷数据场景：去重后应保持与AlwaysEvict相同的传输成本 |
| T29 | 98% cold, 12M requests | 稀疏访问：自适应TTL不应过度缓存 |
| T65 | 67% hot, 99% GETs | 热数据：DedupAwareTevict应接近AlwaysStore的传输成本 |
| T78 | 51% warm, 22% GETs在最后2月 | 访问模式变化：自适应TTL应随模式调整 |
| T79 | 89% one-hit/cold, avg 48MB objects | 大对象：去重节省的存储 vs 传输费用的权衡 |

### 5.4 指标

- **总成本** ($): 存储成本 + 传输成本 + 操作成本
- **存储节省率**: 1 - (去重后存储 / 原始存储)
- **去重率**: 1 - (唯一chunk数 / 总chunk数)
- **本地命中率**: 从本地region提供的 GET 比例
- **平均组装延迟**: GET 时 assemble 的耗时 (ms)

---

## 6. 预期结果与假设

### 6.1 假设

1. **H1**: DedupAwareTevict 在所有 5 个 IBM trace 上的总成本低于不含去重的 SkyStore 策略
2. **H2**: DedupAwareTevict 在热数据 trace (T65, T78) 上总成本与 AlwaysStore+Dedup 持平，但存储成本显著更低
3. **H3**: DedupAwareTevict 在冷数据 trace (T15, T29) 上的传输成本控制与 AlwaysEvict 持平
4. **H4**: 自适应 TTL (G8) 在成本上优于固定 break-even TTL (G7)

### 6.2 预期风险

| 风险 | 缓解措施 |
|---|---|
| 去重率低于预期 | 使用 DEB VM 镜像数据集（论文验证过的高去重数据集） |
| FastCDC 分块 overhead | 异步分块，预计算常用文件指纹 |
| 索引内存溢出 | 两级索引，热指纹在内存，冷指纹在 LMDB |
| GET 组装延迟过高 | chunk 批量预取，并行从多个 region 拉取 |

---

## 7. 实现计划

| 阶段 | 任务 | 预计时间 |
|---|---|---|
| Phase 1 | `dedup/` 模块实现 (FastCDC + BLAKE3 + 索引) | ~2h |
| Phase 2 | 修改 PUT/GET 流程接入去重引擎 | ~1h |
| Phase 3 | 实现 `policy_dedup_aware_tevict.py` | ~1h |
| Phase 4 | 在云服务器上部署 prototype + 去重层 | ~30min |
| Phase 5 | 运行全部 8 组消融实验 | ~2-4h |
| Phase 6 | 结果分析 + 绘制 Figure | ~1h |

---

## 8. 文件清单

| 文件 | 状态 | 说明 |
|---|---|---|
| `prototype/dedup/cdc_chunker.py` | ✅ 已完成 | FastCDC 分块器 |
| `prototype/dedup/dedup_index.py` | ✅ 已完成 | 两阶段去重索引 (Bloom + LMDB) |
| `prototype/dedup/dedup_store.py` | ✅ 已完成 | chunk 物理存储 |
| `prototype/dedup/dedup_engine.py` | ✅ 已完成 | 统一去重接口 (BLAKE3 + FastCDC) |
| `prototype/store-server/operations/object_operations/put.py` | 待修改 | 接入去重层 |
| `prototype/store-server/operations/object_operations/get.py` | 待修改 | 接入去重组装 + set_key_context |
| `prototype/store-server/operations/policy/placement_policy/policy_dedup_aware_tevict.py` | ✅ 已完成 | **独立策略**（抄写 SkyStore 全部逻辑 + 去重状态机, 阈值 = adaptive_ttl） |
| `prototype/store-server/operations/policy/placement_policy/get_placement.py` | ✅ 已完成 | 注册 `dedup_aware_tevict` |
| `prototype/store-server/operations/object_operations/clean.py` | 待修改 | HOT_STATE→DEDUP_STATE 降级 |
| `experiment_logs/dedup_aware_tevict_strategy_20260809.md` | ✅ 已创建 | 本文档 |

### `policy_dedup_aware_tevict.py` 实现方式

采用 **"抄写 + 叠加"** 方式（非继承）:

```
SkyStore 全部代码 (387 行)     →  复制到 DedupAwareTevict
  ├── __init__                + 新增去重专用字段 (_obj_state, _obj_chunk_refs)
  ├── update_past_requests    保留不动
  ├── calc_evict_cost         保留不动
  ├── find_min                保留不动
  ├── get_ttl                 ★ 重写:
  │       Phase 1: _compute_skystore_ttl()  — 原 SkyStore TTL 计算
  │       Phase 2: 去重存储形式决策
  │           if time_to_next ≤ T_even → HOT → 返回 adaptive TTL
  │           else                      → DEDUP → 返回 0
  ├── set_key_context         新增（GET handler 桥接）
  ├── stats                   新增（去重统计）
  └── name                    改为 "dedup_aware_tevict"
```

---

## 附录A: 关键论文引用

1. **SkyStore**: S. Liu et al., "SkyStore: Cost-Optimized Object Storage Across Regions and Clouds", VLDB 2025
2. **FastCDC**: W. Xia et al., "FastCDC: a Fast and Efficient Content-Defined Chunking Approach for Data Deduplication", USENIX ATC 2016
3. **BLAKE3**: J. O'Connor et al., "BLAKE3: One Function, Fast Everywhere", 2020
4. **TTL-CC**: D. Carra et al., "TTL-based Cloud Caches", IEEE INFOCOM 2019
5. **SPANStore**: Z. Wu et al., "SPANStore: Cost-Effective Geo-Replicated Storage Spanning Multiple Cloud Services", SOSP 2013
6. **DEB Dataset**: VectorCDC (FAST 2025) / SeqCDC (Middleware 2024) — `kaggle.com/datasets/sreeharshau/vm-deb-fast25`

---

## 附录B: 与论文图 5 的对照

论文 Fig-5 (2-Region FB) 展示的基线:
- AlwaysEvict, AlwaysStore, Teven, TTL-CC, EWMA, AWS Multi-Region Bucket

本策略需要插入到这个图中:
```
新增对比线: DedupAwareTevict (vs SkyStore, AlwaysEvict, AlwaysStore, Teven)
展示: 5 traces × 5 baselines + 1 our policy = 6 条线
```

---

> **最后更新**: 2026-08-09 16:50 CST
> **云端 DEB 数据集状态**: 提取完成，stage2 扫描中（~1200万文件）
> **阈值决策**: Phase 2 已改为 **Dedup-Aware 公式** `Δt < (1−L) × N/S`
> **代码实现状态**:
>   - ✅ `dedup/` 去重组件 (4 modules)
>   - ✅ `policy_dedup_aware_tevict.py` (SkyStore 全量抄写 + Dedup-Aware 公式, ~685 行)
>   - ✅ `record_chunk_locality()` / `get_chunk_L()` chunk 局域性追踪
>   - ✅ `_compute_teven()` T_even = N/S
>   - ✅ Phase 2: `time_to_next ≤ (1−L) × T_even → HOT`
>   - ✅ `stats()` 包含 `chunk_locality_L`
>   - ✅ `get_placement.py` 注册完成
>   - ⬜ PUT/GET handler 集成（需调用 `record_chunk_locality`）
>   - ⬜ 消融实验

---

## Break-Even 公式推导: HOT vs DEDUP

> **记录时间**: 2026-08-09 16:32 CST

### 1. 基础推导 (不含 Chunk 局域性)

**场景**: 对象大小 `size`, 下一次访问距现在 `Δt` 秒.

| 决策 | 存储成本 | 下次 GET 传输费 | 总成本 (仅本次间隔内) |
|---|---|---|---|
| **HOT** (完整物理副本) | `size × S × Δt` | `0` (本地命中) | `size × S × Δt` |
| **DEDUP** (仅 chunk 引用) | `≈ 0` (chunk 已在去重存储) | `size × N` (远端组装) | `size × N` |

其中:
- `S` = 存储单价 ($/GB/hour)
- `N` = 传输单价 ($/GB)

等开销条件:

\[
size \times S \times Δt = size \times N
\]

\[
\boxed{Δt = \frac{N}{S} \equiv T_{even}}
\]

**= T_even**: 这是经典结论——存储多久，存储费和重传费持平。

**问题**: 假设 DEDUP 需要**全部** chunk 从远端拉取，忽略了 chunk 局域性。

---

### 2. 引入 Chunk 局域性 L

定义 **chunk 局域性** `L` = GET 发起时，在**目标区域已存在的 chunk 比例**。

- `L = 0.0`: 全部 chunk 在远端（最差情况）
- `L = 0.5`: 一半 chunk 本地已存在
- `L = 0.9`: 绝大部分 chunk 本地已有

| 决策 | 存储成本 | 下次 GET 传输费 |
|---|---|---|
| **HOT** | `size × S × Δt` | `0` |
| **DEDUP** | `≈ 0` | `size × (1 − L) × N` |

等开销条件:

\[
size \times S \times Δt = size \times (1 - L) \times N
\]

\[
\boxed{Δt_{effective} = \frac{(1 - L) \times N}{S} = (1 - L) \times T_{even}}
\]

**核心公式**:

> **Effective Break-Even Time = (1 − L) × T_even**

---

### 3. 公式解读

| L | T_even (2-reg≈7h) | Effective τ | 含义 |
|---|---|---|---|
| 0.0 | 7h | **7.0h** | 全远端: 和 T_even 一致 |
| 0.3 | 7h | **4.9h** | 30% 局域性: DEDUP 更划算 |
| 0.5 | 7h | **3.5h** | 一半本地: Hot threshold 降半 |
| 0.7 | 7h | **2.1h** | 70% 局域性: 极少需 HOT |
| 0.9 | 7h | **0.7h** | 高局域性: DEDUP 压倒性优势 |

**直觉**: 
```
  HOT 更优 ←————————————————————→ DEDUP 更优

  L=0   ████████████████████████████████  T_even (7h)
  L=0.3 █████████████████████████        4.9h
  L=0.5 ████████████████████            3.5h
  L=0.7 ██████████████                  2.1h
  L=0.9 ████████                        0.7h
```

L 越大（本地 chunk 越多），DEDUP 优势越大，应更倾向 DEDUP。

---

### 4. 三种阈值方案对比

| 方案 | 公式 | 优点 | 缺点 |
|---|---|---|---|
| **T_even 固定** | `Δt < N/S` | 最简单 | 不考虑 chunk 局域性; 不随 trace 变化 |
| **adaptive_ttl** (当前) | `Δt < TTL_adaptive` | 随 trace 自适应; SkyStore 已算出最优驱逐点 | 不考虑对象级的 chunk 分布 |
| **Dedup-Aware 公式** | `Δt < (1−L) × N/S` | 最精确; 对象级粒度; 纳入局域性 | 需实时跟踪 per-region chunk L |

---

### 5. 当前实现: adaptive_ttl 阈值

当前 `DedupAwareTevict` 采用 **adaptive_ttl** 作为阈值（论文中作为单变量消融的基准版本）:

```
Phase 1:  SkyStore 自适应 TTL 计算 (histogram → calc_evict_cost → find_min)
Phase 2:  if time_to_next ≤ adaptive_ttl → HOT  (完整物理副本)
          if time_to_next >  adaptive_ttl → DEDUP (chunk 引用)
```

**理由**:
1. SkyStore 已通过 800-cell histogram 和 calc_evict_cost 算出该 workload 的 cost-optimal 驱逐点
2. 不同 trace 的 adaptive_ttl 差异巨大:
   - T15 (one-hit 48%): adaptive_ttl ≈ 30min → 仅极短间隔变 HOT
   - T65 (hot 67%): adaptive_ttl ≈ 3h → 更多对象变 HOT，降低传输
   - T29 (cold 98%): adaptive_ttl ≈ 1h → 适应稀疏访问
3. 若用 T_even（固定 7h），对不同 trace 一刀切，丧失自适应能力

**未来方向**: 可将公式升级为 `(1−L) × T_even` 或 `(1−L) × adaptive_ttl`，通过实验验证 L 的贡献。

---

### 6. 关键变量定义汇总

| 符号 | 含义 | 单位 |
|---|---|---|
| `S` | 存储单价 | $/GB/hour |
| `N` | 网络传输单价 | $/GB |
| `T_even` | N/S: 盈亏平衡时间 | hour |
| `Δt` | 距下次访问时间 | hour |
| `TTL_adaptive` | SkyStore 学习的自适应 TTL | hour |
| `L` | Chunk 本地命中率 for GET region | [0, 1] |
| `τ_effective` | `(1−L) × T_even`: 有效盈亏平衡时间 | hour |


---

## 7. 真实文件池与Trace映射关系分析

> **记录时间**: 2026-08-09 12:35 UTC+8
> **服务器**: 121.40.28.243 (Alibaba Cloud ECS, 8核 32GB, 数据盘 590GB)

### 7.1 文件池统计总览

| 类别 | 版本数 | 文件数 | 总大小 | 大小范围 |
|------|--------|--------|--------|----------|
| **Linux Kernel** | 169 | 14,015,137 | 178.17 GB | 0B ~ 72.71 GB |
| **PHP** | 216 | 4,112,747 | 23.19 GB | 52 MB ~ 141 MB |
| **Mapped Tars** | 7 | 7 | 73.49 GB | 180 MB ~ 16.1 GB |
| **DEB OVA (Bitnami)** | 47 | 141 | 29.14 GB | 277 MB ~ 1.67 GB |
| **DEB OVA (Debian)** | 16 | 107 | 49.46 GB | 281 MB ~ 39.30 GB |
| **Ubuntu** | 5 | 4,062 | 13.56 GB | 1.38 GB ~ 4.05 GB |
| **SQL Dumps** | 2 | ~4 | 2.12 GB | — |
| **ISO** | 5 | 5 | 13.59 GB | — |
| **合计** | — | **18,143,065** | **~406 GB** | 100B ~ 16.1 GB |

### 7.2 大小分桶分布

| 桶 | 文件数 | 总大小 | 典型内容 |
|----|--------|--------|----------|
| <1KB | 4,753,984 | 2.24 GB | 源码碎片、符号链接、空文件 |
| 1KB–1MB | 13,380,826 | 170.16 GB | .c/.h/.php源文件、配置文件 |
| 1MB–10MB | 7,169 | 17.39 GB | 固件blob、压缩包、库文件 |
| 10MB–100MB | 855 | 15.14 GB | 大压缩包、VMDK磁盘片段 |
| 100MB–1GB | 202 | 100.41 GB | Bitnami OVA文件、大tar包 |
| >1GB | 29 | 101.26 GB | mapped tar (5-16GB)、Ubuntu squashfs |

### 7.3 磁盘空间分析与可删除清单

| 分区 | 总大小 | 已用 | 可用 | 使用率 |
|------|--------|------|------|--------|
| `/` (系统盘) | 99 GB | 52 GB | 43 GB | 56% |
| `/data` (数据盘) | 590 GB | 526 GB | 37 GB | 94% |

**⚠️ 可删除项（请审查后再决定，未删除）：**

| 路径 | 大小 | 原因 |
|------|------|------|
| `/data/file_pool/mapped_tars/_bins.npy` | 136 MB | 临时二进制索引（已无用） |
| `/data/file_pool/mapped_tars/_paths.npy` | 1.29 GB | 临时路径数组（已无用） |
| `/data/file_pool/mapped_tars/_assignments.pkl` | 4.6 KB | 临时分配文件 |
| `/data/skystore/prototype.tar.gz` | 834 KB | 已有解压目录 |
| `/data/datasets/` 原始压缩包 | ~58 GB | 已提取到file_pool后可考虑删除 |
| **合计可回收** | **~59 GB** | |

### 7.4 Trace映射关系核心讨论

#### 7.4.1 IBM Trace记录格式

| 字段 | 示例 | 含义 |
|------|------|------|
| `timestamp` | `0` | 毫秒时间戳 |
| `op` | `PUT` / `GET` | 操作类型 |
| `issue_region` | `aws:us-east-1` | 请求发起region |
| `obj_key` | `00001224c76754a7` | 对象唯一标识（hash） |
| `size` | `473231` | 对象字节数 |

#### 7.4.2 PUT和GET的对应关系

```
PUT obj_key="abc123", size=473231, region=us-east-1
   ↓ （同一个 obj_key）
GET obj_key="abc123", region=us-west-2
```

**关键结论**:
- IBM Trace中，PUT的size和同一obj_key的GET是一致的——上传486KB的文件，后续GET的就是486KB
- 不存在"PUT 1GB 但 GET 500MB"的情况
- 用户提到的场景（上传10GB压缩包→下载10GB压缩包）正是IBM trace记录的语义

#### 7.4.3 但在去重场景下的实际处理

- **PUT时**: CDC分块 → BLAKE3指纹 → 查重 → 只存唯一chunk → 去重后存储量远小于原始size
- **GET时**: 按chunk清单从各region拉取 → 组装 → 实际传输量 = `去重后size × (1-L)`
- **Trace记录的size是原始object size**，但成本计算时用实际去重后的存储/传输量

#### 7.4.4 映射到真实文件的方案

1. trace中每个PUT指定了`obj_key`和`size`
2. 从18.1M文件池中按`size`找最接近的真实文件 → 作为PUT的实际内容
3. 后续GET同一个`obj_key`时返回chunk引用（去重后）或完整文件
4. **映射准确性**: 5条trace的 >100MB 对象覆盖率 >99%（10MB误差内）。小文件直接匹配。

#### 7.4.5 time_to_next_access的来源

- augmented trace的额外字段，由`new_trace_gen.py`生成
- `DedupAwareTevict.__init__`也自己从timestamp列计算`_key_time_to_next`
- 不需要外部传入——策略内部自给自足

### 7.5 去重索引表设计方案

#### 7.5.1 索引核心问题

跨region去重需回答："chunk指纹`X`在哪些region有副本？"

#### 7.5.2 两阶段索引架构（当前实现）

```
PUT 到达 region R
    │
    ├─ 第1阶段: Bloom Filter（内存级, ~10MB/region）
    │     O(1)判断"一定不存在"
    │     误报率0.1%，过滤99%查询
    │
    ├─ 第2阶段: LMDB持久化索引
    │     fingerprint → ChunkLocation(region, size, path)
    │     仅Bloom返回"可能存在"时才查
    │     mmap映射，~0.1ms/查询
    │
    └─ 物理存储: /tmp/dedup_chunks/{region}/
        按fingerprint前2字符分片，防单目录过大
```

#### 7.5.3 跨Region索引放置

```
              Global Index (生产环境用Redis/etcd)
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
    us-west-1     us-east-1    eu-central-1
    (本地Bloom)   (本地Bloom)   (本地Bloom)
    + LMDB        + LMDB        + LMDB
```

**当前单机模式下**：每个region的Bloom+LMDB独立维护，共享物理磁盘但按region隔离。

**生产环境改进方案**：
1. **集中式**（Redis）：所有查找打中心节点，适合中小规模
2. **DHT**：fingerprint前N bits确定负责region，适合大规模WAN
3. **本地Bloom + 远端确认**：当前方案最快，适合实验

#### 7.5.4 索引存储开销

| 组件 | 规模 | 备注 |
|------|------|------|
| Bloom Filter | ~10 MB/region | 10M unique chunks, 0.1%误报率 |
| LMDB | ~100 MB/region | fp(32B)+location(~64B)×10M |
| **4 regions总计** | **~440 MB** | 完全适配32GB内存 |

---

## 8. 当前原型状态

> **更新时间**: 2026-08-09 13:20 UTC+8
> **子代理**: `2ca982d1`

### 8.1 已修复的4个问题

| # | 问题 | 位置 | 修复方式 |
|---|------|------|----------|
| 1 | GCP节点无priceStorage导致KeyError | helpers.py:90 | 添加`"priceStorage" in G.nodes[node]`守卫 |
| 2 | CheapestTransfer需要manual.csv | profiles/ | 创建最小CSV（预期列） |
| 3 | put.py导入DedupEngine失败 | dedup/ | 创建`__init__.py` stub类 |
| 4 | SQLite不兼容`::interval`语法 | get.py:103 | 改为纯时间戳比较 |

### 8.2 Smoke Test通过

```
✅ POST /register_buckets     → 200 OK
✅ POST /start_upload         → locator ID=1, ttl=-1
✅ PATCH /complete_upload     → success
✅ POST /locate_object        → size=1MB, storage_format=hot
✅ POST /clean_out_remaining  → stats={hits:1, hit_rate:1.0}
```

**uvicorn运行中**: `121.40.28.243:3000`，DedupAwareTevict策略加载成功，准备全量实验。
