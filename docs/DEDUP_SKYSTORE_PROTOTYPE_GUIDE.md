# 传统去重 + SkyStore 原型 完整操作指南

> 写于 2026-08-08 04:00 · 秒级精度

## 0. 架构总览

```
客户端 → S3 API → [Rust s3-proxy :8002] → [Python CP :3000] → PostgreSQL
                       │                       │
                       │  PUT bytes            │  start_upload (选址)
                       │  GET bytes            │  locate_object (选源)
                       │  fan-out              │  TTL / eviction
                       └──→ AWS S3 / GCP       └──→ 定价 CSV (cost/throughput/latency)
```

**去重组件插入位置**：Python CP 的 `/start_upload` 和 `/locate_object`，在数据库读写之间。

---

## 1. 云服务器租赁与基础环境

### 1.1 租 AWS EC2（最简方案：先一台测试）

```
AWS 控制台 → EC2 → Launch Instance

1. 名称: skystore-dedup
2. AMI: Ubuntu 22.04 LTS (ami-0c7217cddeea385ec 等)
3. 实例类型: t3.large (2 vCPU / 8 GB，实验够用)
4. 密钥对: 创建或选一个 .pem，下载到本地
5. 安全组: 开放端口
   - 22  (SSH, 0.0.0.0/0)
   - 3000 (Python CP, 0.0.0.0/0)
   - 8002 (s3-proxy, 127.0.0.1 即可)
   - 5432 (PostgreSQL, 127.0.0.1 only)
6. 存储: 30 GB gp3

7. 启动后:
   ssh -i your-key.pem ubuntu@<public-ip>
```

### 1.2 安装基础软件

```bash
# 更新
sudo apt update && sudo apt upgrade -y

# PostgreSQL
sudo apt install -y postgresql postgresql-client
sudo -u postgres psql -c "CREATE USER ubuntu WITH PASSWORD 'skystore';"
sudo -u postgres psql -c "CREATE DATABASE skystore OWNER ubuntu;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE skystore TO ubuntu;"
sudo -u postgres psql -c "ALTER USER ubuntu CREATEDB;"

# Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Rust (for s3-proxy, if using)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env

# Git + 基础工具
sudo apt install -y git build-essential pkg-config libssl-dev
```

### 1.3 上传仓库到云服务器

```bash
# 在本地 (Windows):
cd D:\JavaProjects\Skystore1212
# 打包 prototype 目录
tar -czf prototype.tar.gz skystore_python/artifact-eval-main/prototype/
# 上传
scp -i your-key.pem prototype.tar.gz ubuntu@<public-ip>:~/

# 在云服务器:
mkdir -p ~/skystore && cd ~/skystore
tar -xzf ~/prototype.tar.gz
```

---

## 2. 环境初始化

### 2.1 Python 控制面

```bash
cd ~/skystore/skystore_python/artifact-eval-main/prototype/store-server

# 虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 注册初始 bucket（修改 conf.py 的 TEST_CONFIGURATION 为你的区域）
python register.py

# 启动服务器
uvicorn app:app --host 0.0.0.0 --port 3000 --workers 1
```

### 2.2 Rust s3-proxy（可选，若需完整 S3 兼容层）

```bash
cd ~/skystore/skystore_python/artifact-eval-main/prototype/s3-proxy

# 本地模式（不连真实云，用本地文件系统 + s3s-fs）
export INIT_REGIONS="aws:us-west-1,aws:us-east-2,gcp:us-west1-a"
export CLIENT_FROM_REGION="aws:us-west-1"
export LOCAL="true"
export SKYSTORE_BUCKET_PREFIX="skystore"
export GET_POLICY="cheapest"
export PUT_POLICY="skystore"
export VERSION_ENABLE="NULL"

cargo run
```

**对于纯实验目的，可以直接用 Python CP 的 REST API 而不跑 Rust 代理**——CP 自身就是完整的控制面。

### 2.3 设置策略

```bash
# 设置 put 策略
curl -X POST http://localhost:3000/update_policy \
  -H "Content-Type: application/json" \
  -d '{"put_policy": "skystore", "get_policy": "cheapest"}'

# 验证
curl http://localhost:3000/healthz  # (需确认是否有此端点)
```

---

## 3. 数据集准备

### 3.1 已有数据（在仓库里）

```bash
# kernel 连续版本（已存在）
ls Data/kernel_consecutive/
# linux-6.1.100.tar.xz, 101, 102, 103 (各 ~129MB)

# 解压
cd Data/kernel_consecutive
for f in linux-6.1.*.tar.xz; do
    xz -d -k "$f"  # 得到 .tar，各 ~1.3GB
done
```

### 3.2 生成 S3 PUT 事件（模拟 IBM trace）

创建一个简单的 Python 脚本 `scripts/gen_trace_for_dedup.py`：

```python
"""生成 kernel 数据集的 trace，用于去重实验。"""

import time
import csv
import os

TRACE_PATH = "experiment/trace/kernel_dedup.prototype"
# 假设 2 个 region: aws:us-west-1 (local), aws:us-east-2 (remote)
KERNEL_DIR = "../Data/kernel_consecutive/"

files = [
    ("kernel_v100", f"{KERNEL_DIR}linux-6.1.100.tar"),
    ("kernel_v101", f"{KERNEL_DIR}linux-6.1.101.tar"),
    ("kernel_v102", f"{KERNEL_DIR}linux-6.1.102.tar"),
    ("kernel_v103", f"{KERNEL_DIR}linux-6.1.103.tar"),
]

with open(TRACE_PATH, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["timestamp", "op", "issue_region", "obj_key", "size", "file_path"])
    t = 0
    for key, filepath in files:
        size = os.path.getsize(filepath)
        # PUT 到 us-west-1
        writer.writerow([t * 1000, "PUT", "aws:us-west-1", key, size, filepath])
        t += 2
        # GET 从 us-east-2 (模拟跨区读)
        writer.writerow([t * 1000, "GET", "aws:us-east-2", key, size, filepath])
        t += 2
```

---

## 4. 去重组件设计 —— 具体插入位置

### 4.1 数据库 schema 扩展

在 `object_schemas.py` 中给 `DBPhysicalObjectLocator` 加一列：

```python
# 在 DBPhysicalObjectLocator 类中添加 (约第110行之后):
content_hash = Column(String, nullable=True, default=None)
# SHA-256 of the object bytes — used for dedup lookup
```

**迁移命令**：

```bash
psql -U ubuntu -d skystore -c "
  ALTER TABLE physical_object_locators
  ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);
  CREATE INDEX IF NOT EXISTS ix_physical_locators_hash_bucket
    ON physical_object_locators(bucket, content_hash);
"
```

### 4.2 插入位置 1：PUT — `/start_upload` 去重

在 `put.py` 第 33-366 行，在创建 `DBPhysicalObjectLocator` 之前（第 184 行之后），插入去重逻辑：

```python
# ============ 去重插件：在 put.py start_upload() 中的第 184 行之后插入 ============

# 1. 计算 content_hash (SHA-256)
import hashlib

content_hash = None
if request.file_path:  # 新字段：文件路径
    with open(request.file_path, "rb") as f:
        content_hash = hashlib.sha256(f.read()).hexdigest()
    logger.info(f"[DEDUP] PUT {request.key} hash={content_hash[:16]}...")

# 2. 检查是否有相同 content 的本地副本（即使 TTL 已过期）
if content_hash:
    existing_dedup_stmt = select(DBPhysicalObjectLocator).where(
        and_(
            DBPhysicalObjectLocator.location_tag == request.client_from_region,
            DBPhysicalObjectLocator.content_hash == content_hash,
        )
    )
    existing_dedup = (await db.execute(existing_dedup_stmt)).scalars().first()

    if existing_dedup:
        logger.info(
            f"[DEDUP] HIT! {request.key} already exists in "
            f"{request.client_from_region} (ttl={existing_dedup.ttl})"
        )
        # 去重命中：不需要再上传，刷新 TTL 即可
        existing_dedup.ttl = set_ttl  # 用当前策略的 TTL 刷新
        existing_dedup.storage_start_time = timestamp
        await db.commit()
        # 返回空 locator → 客户端跳过上传
        return StartUploadResponse(
            locators=[],  # 空 = 不需要传输
            copy_src_buckets=[],
            copy_src_keys=[],
        )

# 3. 没有命中：正常走原有流程，但在 complete_upload 时记录 hash
# （将 content_hash 存到 DBPhysicalObjectLocator 上）
```

**修改 `StartUploadRequest`** 添加 `file_path` 字段：

```python
class StartUploadRequest(BaseModel):  # 在 object_schemas.py 中
    # ... 原有字段
    file_path: Optional[str] = None  # 新增
```

### 4.3 插入位置 2：PUT — `/complete_upload` 记录 hash

在 `put.py` 第 426 行（`await db.commit()` 之前）：

```python
# 记录 content_hash 到物理 locator
if content_hash and physical_locator:
    physical_locator.content_hash = content_hash
```

### 4.4 插入位置 3：GET — `/locate_object` 本地命中检查

在 `get.py` 第 91 行（查询 ready locators）之前，加一个**扩展查找**：

```python
# ============ 去重插件：在 get.py locate_object() 的第 91 行之前插入 ============

# 检查本地是否有相同 content 的过期副本
local_dedup_stmt = (
    select(DBPhysicalObjectLocator)
    .join(DBLogicalObject)
    .where(
        and_(
            DBPhysicalObjectLocator.location_tag == request.client_from_region,
            DBPhysicalObjectLocator.content_hash.isnot(None),
            DBLogicalObject.bucket == request.bucket,
            DBLogicalObject.key == request.key,
        )
    )
    .order_by(DBPhysicalObjectLocator.storage_start_time.desc())
)
local_dedup = (await db.execute(local_dedup_stmt)).scalars().first()

if local_dedup:
    # 本地有同内容对象（即使 TTL 已过期！）
    # 刷新 TTL，避免跨区传输
    local_dedup.ttl = -1  # 或让策略重新计算
    local_dedup.storage_start_time = timestamp
    logger.info(
        f"[DEDUP] Local dedup HIT for GET {request.key} — "
        f"skipping cross-region transfer"
    )
    # 重新走 ready_locators 逻辑，现在它会被 include
```

**注意**：在第 91 行的 SQL 查询中，条件 `DBPhysicalObjectLocator.status == Status.ready` 且 `ttl > now` 会排除过期副本。上面的去重插件单独查一次，若命中则刷新 TTL 后再走原逻辑。

---

## 5. 执行完整实验矩阵

### 5.1 启动脚本

```bash
#!/bin/bash
# run_dedup_experiment.sh

source .venv/bin/activate
export PYTHONPATH=.

# ---------- 步骤 1: 启动服务器 ----------
echo "[$(date +%H:%M:%S)] Starting server..."
uvicorn app:app --host 0.0.0.0 --port 3000 &
SERVER_PID=$!
sleep 3

# ---------- 步骤 2: 对每个策略跑一遍 ----------
for POLICY in "always_evict" "always_store" "t_even" "skystore"; do
  echo ""
  echo "=============================================="
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running: $POLICY"
  echo "=============================================="

  # 2a. 设置策略
  curl -s -X POST http://localhost:3000/update_policy \
    -H "Content-Type: application/json" \
    -d "{\"put_policy\": \"$POLICY\", \"get_policy\": \"cheapest\"}"

  # 2b. 重置 trace 索引和数据库
  curl -s -X POST http://localhost:3000/update_policy \
    -H "Content-Type: application/json" \
    -d '{"put_policy": "'$POLICY'", "get_policy": "cheapest"}'

  sleep 1

  # 2c. 回放 trace（PUT → GET）
  python -c "
import csv, requests, os, time

TRACE = 'experiment/trace/kernel_dedup.prototype'
with open(TRACE) as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        ts = int(row['timestamp'])
        op = row['op']
        key = row['obj_key']
        size = int(row['size'])
        file_path = row.get('file_path', '')
        region = row['issue_region']

        if op == 'PUT':
            # start_upload
            resp = requests.post(
                'http://localhost:3000/start_upload',
                json={
                    'bucket': 'default-skybucket',
                    'key': key,
                    'client_from_region': region,
                    'file_path': file_path,
                    'size': size,
                }
            )
            if resp.status_code == 200:
                locators = resp.json().get('locators', [])
                if locators:  # 需要上传
                    for loc in locators:
                        # 模拟上传（实际上传文件字节）
                        with open(file_path, 'rb') as f:
                            _ = f.read()  # 实际环境会通过网络传
                        # complete_upload
                        time.sleep(0.01)
                        requests.patch(
                            'http://localhost:3000/complete_upload',
                            json={
                                'id': loc['id'],
                                'size': size,
                                'etag': 'dummy',
                                'last_modified': '2026-01-01T00:00:00'
                            }
                        )

        elif op == 'GET':
            resp = requests.post(
                'http://localhost:3000/locate_object',
                json={
                    'bucket': 'default-skybucket',
                    'key': key,
                    'client_from_region': region,
                }
            )
            # 读响应

        # 进度
        if i % 100 == 0:
            print(f\"  [{i}] {op} {key} ok\")
  "
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done: $POLICY"
done

# ---------- 步骤 3: 清理 ----------
curl -s -X POST http://localhost:3000/clean_out_remaining \
  -H "Content-Type: application/json" \
  -d '{"timestamp": "2026-12-31T23:59:59"}'

kill $SERVER_PID
echo "[$(date +%H:%M:%S)] All done."
```

### 5.2 策略名与 TTL 特性

| 策略名 | `/update_policy` 的值 | TTL | 运费策略 |
|---|---|---|---|
| Always Evict | `always_evict` | 0（即驱逐） | pull-on-read，每读一次付一次运 |
| Always Store | `always_store` | -1（永久） | pull-on-read，第一次运后本地永久留 |
| Teven | `t_even` | 运/存 break-even | 基于交叉点 |
| SkyStore | `skystore` | 动态（直方图学习） | 自适应 TTL |

---

## 6. 成本对比（预期）

| 指标 | Always Evict | Always Store | Teven | SkyStore | SkyStore+Dedup |
|---|---|---|---|---|---|
| 去重前 object storage | 0 (随时删) | 4×1.3GB=5.2GB | variable | variable | 5.2GB 逻辑 |
| 去重后 physical storage | 0 | ~5.2GB | variable | variable | **~1GB** (80% dedup) |
| 跨区 GET 首次 | 全价运 1.3GB | 全价运 1.3GB | 第一次全价 | 第一次全价 | 只运独有 chunk (~0.26GB) |
| 跨区 GET 后续 | 每次全价 | 0 (本地 hit) | TTL 内本地 hit | TTL 内本地 hit | TTL 内本地 hit |

**关键降费点**：
- PUT 时检测本地已有相同 content → 不传，省 80%+ GB
- GET 时本地过期 TTL 的副本若 content 相同 → 刷新 TTL，不跨区传

---

## 7. 快速指令速查

```bash
# 设置策略为 always_store
curl -X POST localhost:3000/update_policy \
  -H "Content-Type:application/json" \
  -d '{"put_policy":"always_store","get_policy":"cheapest"}'

# 设置策略为 skystore
curl -X POST localhost:3000/update_policy \
  -H "Content-Type:application/json" \
  -d '{"put_policy":"skystore","get_policy":"cheapest"}'

# 设置策略为 always_evict
curl -X POST localhost:3000/update_policy \
  -H "Content-Type:application/json" \
  -d '{"put_policy":"always_evict","get_policy":"cheapest"}'

# 设置策略为 teven
curl -X POST localhost:3000/update_policy \
  -H "Content-Type:application/json" \
  -d '{"put_policy":"t_even","get_policy":"cheapest"}'

# 查看最终统计（需要加代码记录；或查 PostgreSQL）
PGPASSWORD=skystore psql -U ubuntu -d skystore -c "
  SELECT
    location_tag,
    COUNT(*) AS objects,
    SUM(ttl IS NULL OR ttl = -1) AS forever,
    SUM(ttl = 0) AS immediate_evict
  FROM physical_object_locators
  WHERE status = 'ready'
  GROUP BY location_tag
  ORDER BY location_tag;
"
```

---

## 8. 关键文件修改清单

| 文件 | 修改 |
|---|---|
| `store-server/operations/schemas/object_schemas.py` | `DBPhysicalObjectLocator` 加 `content_hash` 列；`StartUploadRequest` 加 `file_path` |
| `store-server/operations/object_operations/put.py` | `/start_upload` 第 184 行后加去重查询；`/complete_upload` 最后加 content_hash 写入 |
| `store-server/operations/object_operations/get.py` | `/locate_object` 第 91 行前加本地过期副本查重 |
| PostgreSQL | `ALTER TABLE physical_object_locators ADD COLUMN content_hash VARCHAR` |
| 新文件: `experiment/trace/kernel_dedup.prototype` | trace CSV |

---

## 9. 下一步：扩展到真实云存储

当前方案在**单台 EC2 上跑所有 region 的逻辑**（用 PostgreSQL 和 local files 模拟多个 region）。真实多云的下一步：

1. 每个 region 起一台 EC2（不同 AZ/cloud）
2. 每台跑自己的 s3-proxy + local S3（或用真实 AWS S3 bucket）
3. CP 部署在其中一台，通过内网 IP 互联
4. 用真实 S3 SDK 客户端调用 s3-proxy 的 8002 端口
5. 成本直接从 AWS/GCP billing API 或 CSV pricing 计算

