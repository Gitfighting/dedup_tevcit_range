# 原型文件系统问题清单与修复跟踪

- 登记时刻：**2026-08-12 07:24 (UTC+8)**
- 依据：代码实盘 + ECS `118.31.175.106` 现场核查（未依赖历史说明文档）
- 约束：开闭原则 — 作者策略核心（`policy_skystore` / `always_*` / `teven` / `helpers` / `skyproxy.rs`）尽量不改；以 `ablation/`、`dedup/`、启动脚本、依赖附加文件扩展

---

## 问题列表（07:24 登记）

| ID | 问题 | 严重度 | 计划手段 (OCP) | 状态 |
|----|------|--------|----------------|------|
| P1 | `/data/venv` 缺 `python-dotenv`，store-server `import app` 失败 | 阻断 | `requirements-ablation.txt` + 远程 pip | **已修** |
| P2 | `skystore_cli.py` 默认 sky-s3 路径错误 | 阻断 | **不改** CLI；`ablation/launch_local_stack.py` 传正确路径 | **已修** |
| P3 | `run_prototype_ablation.py` 仅为 stub | 阻断 | 改为可 sync + 启栈 | **已修** |
| P4 | sky-s3 不感知 manifest；`assemble` 未接线 | 功能缺口 | `dedup/cache_materialize.py` + GET 扩展物化到 cache | **已修（接线）** |
| P5 | `blake3` / `lmdb` 未列入依赖 | 高 | `requirements-ablation.txt` | **已修** |
| P6 | `L` 用全局 `duplicate_chunks` | 正确性 | `put.py`：`entry.region == region` | **已修** |
| P7 | DEDUP 状态仅内存 | 中 | `policy_dedup_aware_tevict` JSON sidecar | **已修** |
| P8 | PATH 无 `~/.cargo/bin` | 中 | 启动器 export PATH | **已修** |
| P9 | 全栈无进程 | 结果 | 启动器拉起三件套 | **已修** |

### 修复过程中新发现（07:24 后）

| ID | 问题 | 解决 | 时刻 |
|----|------|------|------|
| P10 | 作者 `requirements.txt` 的 `psycopg2` 源码编译失败拖垮 pip | 启动器跳过 psycopg2，改装 `psycopg2-binary` | ~07:28 |
| P11 | 缺 `skyplane`（`helper.py` 硬依赖） | OCP stub：`ablation/vendor_stubs/skyplane/`，不改 helper | ~07:35 |
| P12 | 当前 s3s-fs 参数为 `--domain` 而非作者 CLI 的 `--domain-name` | 仅改启动器 | ~07:37 |
| P13 | sky-s3 localhost SDK 需 region | 启动器设 `AWS_DEFAULT_REGION` / `AWS_REGION` | ~07:36 |
| P14 | 安装 UltraDict 触发作者 `db.py` `thread` 未定义 bug | **不装** UltraDict，走 SQLite 分支（与 Phase-A 一致）；不改 db.py | ~07:38 |
| P15 | sky-s3 在 store-server 就绪前启动会 panic | 启动器先等 `/docs` 再启 sky-s3 | ~07:38 |

---

## 修复时间线

| 时刻 (UTC+8) | 动作 |
|--------------|------|
| **07:24** | 本清单落盘 |
| **07:25–07:30** | 新增 `requirements-ablation.txt`、`launch_local_stack.py`、可执行 `run_prototype_ablation.py`；P4/P6/P7 代码落地 |
| **07:30–07:35** | 依赖安装踩坑（psycopg2 / dotenv / sqlalchemy_repr） |
| **07:35–07:37** | skyplane stub + 包装 uvicorn（`fix_price_storage`） |
| **07:37** | 修正 s3s-fs `--domain`；store-server `/docs`=200 |
| **07:38** | 卸 UltraDict；三件套齐（3000/8014/8002）；`skystore`：`register`+`start_upload` 200；timestamps=283086 |
| **07:39** | 切换 `dedup_aware_tevict` 全栈冒烟成功（`Policy update - put: dedup_aware_tevict`；`start_upload` 200；key mappings 283086） |

---

## 验证快照（07:39）

```
LISTEN 127.0.0.1:3000  store-server (dedup_aware_tevict)
LISTEN 127.0.0.1:8014  s3s-fs
LISTEN 127.0.0.1:8002  sky-s3
register 200
start_upload 200
```

启动命令（服务器）：

```bash
export ABLATION_PYTHON=/data/venv/bin/python
export PATH=$HOME/.cargo/bin:$PATH
export AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_DEFAULT_REGION=us-east-1
export TRACE_FILE_PATH=/data/skystore/traces/IBMObjectStoreTrace065Part0.typeE.mc-aug.csv
cd /data/ablation_exp/prototype
$ABLATION_PYTHON ablation/launch_local_stack.py --policy skystore
# 或 --policy dedup_aware_tevict
```

---

## 作者源码改动边界（OCP 审计）

| 文件 | 是否作者核心 | 本轮改动 |
|------|--------------|----------|
| `policy_skystore.py` / `always_*` / `teven` / `helpers.py` / `skyproxy.rs` / `skystore_cli.py` | 是 | **未改** |
| `policy_dedup_aware_tevict.py` | 我方策略 | 持久化 sidecar |
| `put.py` / `get.py`（已有 dedup 扩展块） | 作者文件中的扩展区 | 仅扩展块：L 统计 / assemble 物化 |
| `ablation/*`、`dedup/*`、`requirements-ablation.txt`、`scripts/cloud/run_prototype_ablation.py` | 扩展 | 新建/重写 |

---

## 仍开放（非阻断）

- 完整轨迹 `.mc-aug` 回放消融矩阵尚未批量跑（本轮仅全栈 + 策略冒烟）
- sky-s3 内嵌 chunk 组装仍未做（用 cache 物化规避）
- 作者 `db.py` UltraDict 分支 bug 仍在；本地消融靠“不装 UltraDict”绕过
