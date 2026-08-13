# 传统去重 + SkyStore 组合方案

> 写于 2026‑08‑08 03:41

## 目标

在真实云服务器上，先对数据集做**传统去重**（CDC 分块 + hash 索引），再将去重后的数据接入 **SkyStore 原型**（Rust S3 代理 + Python 控制面），最终降低 total cost。

核心变量：去重组件单独打开/关闭，不改 SkyStore 的 TTL / 放置 / 传输策略（类比 `tevict_ranges_lz4` 的单一变量消融）。

---

## 1. 现有原型架构（Rust s3‑proxy）

```
客户端 S3 SDK
    │
    ▼
hyper::Server (127.0.0.1:8002)  ← s3s S3Service
    │
    ▼
SkyProxy
    ├── store_clients: HashMap<region_tag, ObjectStoreClient>
    │       ├── aws:us‑east‑1  → S3ObjectStoreClient  (aws‑sdk‑s3)
    │       ├── aws:us‑west‑2  → S3ObjectStoreClient
    │       └── …
    ├── dir_conf:  Python store‑server (控制面，3000端口)
    │       └──  start_upload / complete_upload / locate_object …
    └── 策略:  get_policy=cheapest, put_policy=tevict_ranges|always_store|…
```

**路径示例**（PUT）：

```
客户端 PUT /bucket/key
  → SkyProxy::put_object
    → split_streaming_blob(bytes, N)   // 流扇出 N 份
    → start_upload(CP)                 // CP 返还 locator 列表
    → 并行: upload → 云存储
    → complete_upload(CP)              // CP 记录 etag/size/ttl
```

**关键点**：`split_streaming_blob` 把同一份字节扇出 N 份写到不同 region。去重后需要改为「按 chunk 写、按 chunk 引用」，这一层在扇出前介入。

---

## 2. 去重组件——在 SkyProxy 前面插一层 `DedupStore`

```
客户端 PUT → SkyProxy
              │
              ▼
         [DedupStore]     ← 新组件
              │
    ┌─────────┼─────────┐
    │  CDC 分块          │  fastCDC / Rabin
    │  指纹 = SHA‑256   │
    │  索引: HashMap<指纹, chunk_ref>   │
    │         chunk_ref = (region, bucket, key) │
    └─────────┼─────────┘
              │
    chunk 已在本地 region？ → 引用 (不传)
    chunk 不在？          → 上传 → 索引入库
              │
              ▼
    元数据写回: 对象 key → chunk 指纹列表
              │
              ▼
    SkyProxy 原路径 (或直接 return)
```

### 2.1 CDC 分块

```rust
// 伪代码
const AVG_CHUNK_SIZE: usize = 8192;      // 8KB 平均
const MIN_SIZE: usize = 2048;
const MAX_SIZE: usize = 65536;           // 64KB 上限

fn chunk_stream(mut stream: impl BufRead) -> Vec<Vec<u8>> {
    let mut chunks = Vec::new();
    let mut buf = Vec::new();
    let mut window = RollingHash::new(48);  // Rabin 滑动窗口
    loop {
        let b = stream.read_u8();
        buf.push(b);
        window.push(b);
        if window.mask_low14() == 0 && buf.len() >= MIN_SIZE || buf.len() >= MAX_SIZE {
            chunks.push(buf.split_off(0));
        }
    }
    // 剩余尾块
    if !buf.is_empty() { chunks.push(buf); }
    chunks
}
```

### 2.2 索引结构

```rust
struct ChunkIndex {
    /// hash → 该 chunk 已被写到的所有 (region, bucket) 列表
    locations: HashMap<Sha256Digest, Vec<ChunkLocation>>,
    /// 每个 region 的引用计数: chunk_hash → count
    ref_counts: HashMap<String, HashMap<Sha256Digest, u64>>,
}

struct ChunkLocation {
    region_tag: String,        // "aws:us‑east‑1"
    bucket: String,            // "skystore‑dedup"
    chunk_key: String,         // hex(hash)[..2]/hex(hash)
}
```

### 2.3 写入路径

```
PUT /mybucket/ubuntu_00.tar  (1.3GB)

DedupStore:
  1. CDC → 162 个 chunk (各 ~8KB)
  2. 对每个 chunk:
      a. hash = SHA‑256(chunk_bytes)
      b. 查本地索引: chunk 是否已存在于当前 region？
         已有 → 引用计数 +1, 跳过上传
         没有 → 调用 ObjectStoreClient.put_object(chunk_key, chunk_bytes)
               → 索引录入
  3. 写对象元数据:
      {
        "obj_key": "ubuntu_00.tar",
        "logical_size": 138479826,
        "chunks": ["a1b2c3...", "d4e5f6...", ...],  // 162 entries
        "version": 1
      }
      → 写入控制面 (store‑server 的 start_upload/complete_upload)
  4. 控制面记录: 此对象在哪些 region 有完整 chunk 集合

返回值: e_tag, 逻辑 size (不变，TTL 学习用)
```

### 2.4 读取路径

```
GET /mybucket/ubuntu_00.tar

DedupStore:
  1. 从控制面取对象元数据 → chunk 列表
  2. 对每个 chunk:
      本地 region 有？ → 从本地 ObjectStoreClient 读
      本地没有？      → 从 CheapestTransfer 最近 region 拉
  3. 按 chunk 顺序拼回字节流 → 返回客户端
```

**成本节约点**：
- **存储**：跨对象的公共 chunk 只存一份（同一 region 内）。docker 镜像层、sqldump 公共前缀、kernel 相同源文件等。
- **传输**：region A 的 GET 只拉「该 region 缺失的 chunk」，不是全对象。首次跨区访问后，冷 region 逐步暖热 chunk 集合。

---

## 3. 如何模拟 IBM 操作记录

IBM trace 的每一行是 `(timestamp, op, issue_region, obj_key, size)`，没有文件内容。需要**从真实文件集生成 obj_key→文件映射**，并在 trace 的 `PUT` 事件中把对应文件的字节流传入 CDC。

### 3.1 可用数据集

| 数据集 | 文件 | 总大小 |
|---|---|---|
| **kernel 连续版本** | `linux‑6.1.{100,101,102,103}.tar.xz` | ~515MB (压缩), ~5.2GB (解压) |
| docker 镜像 (无源文件，只有 trace) | 20 个 `ubuntu_*.tar` 的 LZ4 尺寸 | 源文件缺失 |
| sqldump | `thinkmobile_*.sql` (4 个季度快照) | 源文件缺失，只有 delta 比例 |
| **合成/公开数据集** | 可自行下载: Ubuntu 镜像、维基百科 dumps、GitHub release 等 | 自选 |

**最小可跑方案**：**kernel 4 个连续版本**

- 每个版本 ~1.3GB 解压
- 版本间内容高度重叠（相邻内核 patch 级变更）
- 解压后做 CDC → 公共块去重率预计 >80%（和 sqldump 类似）
- 4 个对象，每个 ~1.3GB → 总逻辑 ~5.2GB，去重后物理存储可能 <1GB

### 3.2 生成 trace + 文件映射

Python 脚本（新增 `scripts/generate_trace_with_files.py`）：

```python
FILES = {
    "kernel_v100": "Data/kernel_consecutive/extracted/linux-6.1.100/",
    "kernel_v101": "Data/kernel_consecutive/extracted/linux-6.1.101/",
    "kernel_v102": "Data/kernel_consecutive/extracted/linux-6.1.102/",
    "kernel_v103": "Data/kernel_consecutive/extracted/linux-6.1.103/",
}

# 对每个 kernel 版本，tar 打包成一个 tar 流 → 算 total size
# 生成微量请求: 每个对象先 PUT(写), 然后按大论文的 Type E 模式加少量 GET
# trace 新增列 file_path:

# 输出 CSV:
# timestamp,op,issue_region,obj_key,size,file_path,time_to_next_access
# 0,PUT,aws:us-west-2,kernel_v100,138479826,Data/kernel_consecutive/extracted/linux-6.1.100.tar,
# 3600,GET,aws:us-east-1,kernel_v100,138479826,,
# ...
```

### 3.3 映射到 IBM 风格的 trace 列

在 `SimulatorV2` 已有 `compressed_size` 扩展的先例下，可类似增加 `file_path` 列：

```java
// Request.java 新增字段
public String filePath;          // 非空 → 从磁盘读字节；空 → 纯尺寸仿真
public byte[] backingContent;    // 懒加载：第一次访问时读文件

// LogicalObject.java 新增字段
public transient ByteSource byteSource;  // 后续 EC/去重 需要真实字节时用
```

已有 `LocalByteSource` (`finesse/LocalByteSource.java`) 可直接复用，它就是做 `Files.readAllBytes(path)`。

---

## 4. 完整架构（去重 + SkyStore）

```
                客户端 S3 SDK
                     │
                     ▼
   ┌─────────────────────────────┐
   │  DedupStore (Rust/Java)     │  ← 在 SkyProxy 前面
   │    • CDC 分块               │
   │    • SHA‑256 指纹           │
   │    • 全局 chunk 索引        │
   │    • 对象→chunk 列表 元数据 │
   └──────────┬──────────────────┘
              │ 经去重后的 chunk 流 / 引用
              ▼
   ┌─────────────────────────────┐
   │  SkyProxy (Rust s3‑proxy)   │  ← 不改
   │    • 多 region 扇出         │
   │    • copy‑on‑read           │
   │    • 策略委托到 CP           │
   └──────────┬──────────────────┘
              │
              ▼
   ┌─────────────────────────────┐
   │  Python store‑server (CP)   │  ← 扩展元数据类型
   │    • start/complete_upload   │
   │    • 记录 chunk 列表         │
   │    • TTL / placement         │
   └──────────┬──────────────────┘
              │
              ▼
   ┌─────────────────────────────┐
   │  云存储 (AWS S3 / GCP GCS)  │
   │    • chunk‑dedup‑{region}   │  ← 每 region 一个 chunk bucket
   │    • skystore‑{region}       │  ← 元数据 bucket
   └─────────────────────────────┘
```

---

## 5. 成本降低路径（为什么这个组合能降 cost）

### 5.1 去重降存储

```
去重前: 4 个 kernel 版本，每个 1 份完整副本 → reg A & B 各存 4×1.3GB = 5.2GB/region
去重后: 4 个版本共享 ~80% 公共 chunk，物理: ~1GB/region
storage cost ↓ ~80%
```

### 5.2 去重降运输

```
场景: kernel_v103 在 reg B 被 GET，但 reg B 只有 v100/v101 的 chunk 集合

去重前: 拉全对象 → 1.3GB × egress 单价
去重后: 只拉 v103 新增/变更的 ~20% chunk → 0.26GB × egress 单价
transfer cost ↓ ~80%
```

### 5.3 SkyStore 的 TTL 不再浪费去重收益

去重后冷对象的「驱逐」更便宜：删一个冷 kernel 版本不会删掉它和其他版本共享的公共 chunk（引用计数 >1），只减引用并删独有 chunk。SkyStore 可以更大胆地给冷数据较长 TTL，因为共享块不会被重复计多次存储费。

### 5.4 LZ4 叠加

在 chunk 写入前对每个 chunk 做 LZ4 压缩（`chunk_bytes → LZ4(chunk_bytes) → 写入`），进一步缩减物理尺寸。注意：LZ4 的压缩边界是 chunk 级（一个 8KB chunk 独立压），不影响跨文件去重。

---

## 6. 实现路线图（分阶段）

### 阶段 1A — 离线 CDC Profiler（1-2天）

```bash
# 在真实数据集上跑一次 CDC，产出:
# - 每个对象的 chunk 列表
# - 全局 unique_chunks / total_chunks
# - 对象间共享矩阵
java -cp target/... org.example.tools.DedupProfiler \
  --dataset Data/kernel_consecutive/ \
  --output results/kernel_dedup_profile.csv
```

产出文件：
```
obj_key,logical_size,file_path,num_chunks,unique_chunks,shared_chunks,dedup_ratio
kernel_v100,1460772864,Data/kernel_consecutive/linux-6.1.100.tar,358215,41281,316934,0.884
...
```

全局去重率 ≈ 1 - unique_hash / total_raw_chunks

### 阶段 1B — 仿真验证（无需云服务器）

类比 `tevict_ranges_lz4`，写 `tevict_ranges_dedup`：
- 用 profiler 产出的 `dedup_ratio` 作为存储/运输的 `TransferCompression` 缩放因子
- 跑 2-reg / 3-reg 矩阵，对比 `tevict_ranges` 基准
- 不改 TTL / 直方图

若仿真结果显示 ~60-80% 总成本下降，进入阶段 2。

### 阶段 2 — Rust DedupStore 原型（在 s3‑proxy 内嵌）

在 `s3‑proxy/src/` 里新增 `dedup/` 模块：

```
s3-proxy/src/
  dedup/
    mod.rs               ← 模块入口
    chunker.rs           ← CDC 分块 (fastCDC)
    fingerprint.rs       ← SHA-256
    index.rs             ← HashMap<Hash, Vec<ChunkLocation>>
    metadata.rs          ← 对象 → chunk 列表序列化
    dedup_store.rs       ← DedupStore 主结构
```

DedupStore 作为 `ObjectStoreClient` trait 的**另一个实现**（Decorator 模式）：

```rust
pub struct DedupStore {
    inner: Arc<Box<dyn ObjectStoreClient>>,  // 底层 S3/GCP/Azure
    index: ChunkIndex,
    bucket: String,                           // chunk 专用 bucket
    avg_chunk_size: usize,
}
```

`SkyProxy::new()` 里加可选开关：

```rust
if config.enable_dedup {
    // 对每个 region 的 store_client 包一层 DedupStore
    for (tag, client) in &store_clients {
        store_clients[tag] = Arc::new(DedupStore::new(client.clone(), ...));
    }
}
```

控制面的 `start_upload` 接收新增字段 `chunk_list: Vec<String>`（十六进制 hex SHA-256），存储在逻辑对象表。

### 阶段 3 — 2-reg 真实云跑

1. 在 AWS 起 3 台 EC2（us-east-1 ×1, us-west-2 ×1, 控制面 ×1）
2. 部署 Rust s3-proxy + DedupStore + Python CP
3. 把 kernel 4 个版本上传到 us-west-2
4. 生成 trace：PUT 4 个对象 → 间隔 GET 模拟访问
5. 对比：`enable_dedup=true` vs `enable_dedup=false`，记录 S3 账单（storage + egress）

### 阶段 4 — 扩展到 IBM trace

当阶段 3 验证通过后，用 `synth_multicloud_delta_trace.py` 的方法：
- 从 IBM trace 里采样 obj_key 子集
- 对每个采样 obj_key 关联一个真实或合成文件内容
- 生成 `file_path` 列的 augmented trace
- 在 Rust 原型上按此 trace 回放

---

## 7. 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 分块算法 | fastCDC (Rabin 滑动窗口) | 比定长 CDC 去重率高 20-40%，生产验证 |
| 平均块大小 | 8KB | 去重研究和工业中常用 (Data Domain 等) |
| 哈希 | SHA-256 | 碰撞概率可忽略，标准 |
| 索引存储 | 内存 HashMap + 定期快照到磁盘 | 实验阶段简单，生产再加 RocksDB |
| 元数据格式 | JSON（每对象一个文件到控制面） | 与现有 CP 兼容 |
| chunk 压缩 | LZ4（每 chunk 独立） | 去重后叠压缩，已在 `TransferCompression` 验证过 |
| TTL / 放置 | 不变，继续由 CP 的 SkyStore 策略决定 | 保持单一变量 |

---

## 8. 关键风险与缓解

| 风险 | 缓解 |
|---|---|
| CDC 分块 CPU 开销过高 | 块大小选 8-16KB；批量 hash 用 SHA-256 SIMD 加速 |
| 索引膨胀（大量小 chunk 的 hash 表） | 仅维护当前 region 的索引，每 region 独立；定期 prune 引用计数 0 的条目 |
| 真实云拉取 chunk 增加 GET 次数（不是一次 GET 全对象） | 并行 chunk GET (20-50 并发)；对小文件可以跳过 CDC（< min_size 直接整对象） |
| kernel 数据集太小，去重率不具一般性 | 阶段 1 先跑 profiler 确认；不够则加入更多公开数据集（Ubuntu 仓库、Wikipedia dumps） |

---

## 9. 与 LZ4 / EC / delta 的关系

这是**正交分层**，不是替代关系：

```
数据 → CDC 去重 (去除跨对象公共 chunk)
     → LZ4 压缩 (每 chunk 体积缩减)
     → SkyStore (TTL / 驱逐 / 副本 placement)
     → EC     (如果有容灾需求)
```

各层各降一种冗余：
- 去重：跨对象内容冗余
- LZ4：单 chunk 内字节冗余
- SkyStore：跨 region 访问模式冗余
- Delta (Finesse)：相似但不完全相同块之间的冗余（未放在此第一阶段）
- EC：容灾冗余（以存储换可用性）

