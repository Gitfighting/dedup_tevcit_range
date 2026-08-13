# Toolchain + priceStorage 全覆盖 — 2026-08-11

## priceStorage（对照 simulation）
- simulation 与 prototype 的 `helpers.make_nx_graph` **同样**只读 `storage.csv` 的
  `Group==storage` + `Tier∈{General Purpose,Hot}`，**未接线** `gcp_storage_pricing.csv`
- 因此原先 **71 节点中 24 个 GCP 无 priceStorage**（simulation 同样如此）

### OCP 数据修复（不改 helpers.py）
脚本：`scripts/cloud/merge_gcp_into_storage_csv.py`
- 把 GCP Standard Storage 写成 `Group=storage, Tier=Hot` 追加进 `storage.csv`
- 并把 `StartingRange==0` 的 storage 行放到后面（作者循环后写覆盖 → 取基础档价）

已应用到：
- `prototype/.../profiles/storage.csv`
- `simulation/.../profiles/storage.csv`
- 服务器 `/data/ablation_exp/.../profiles/` 与 `/data/skystore/prototype/.../profiles/`

**验证**：`nodes 71 missing_priceStorage 0` → `COVERAGE_OK`  
`aws:us-east-1=0.023`, `gcp:us-west1-a=0.02`

## 工具链
| 组件 | 状态 |
|------|------|
| rustc/cargo | **1.97.1**（rustup） |
| s3s-fs | **/root/.cargo/bin/s3s-fs** v0.14.1 |
| awscli | 1.46.0 |
| openapi → rust client | 已生成 `/data/ablation_exp/prototype/generated/skystore-rust-client` |
| sky-s3 | **编译失败 EXIT=101**（约 85 个错误）：新版 openapi-generator rust 客户端 API 与作者 `s3-proxy` 源码不匹配。需旧版 generator 或作者预生成 client；**s3s-fs + store-server 已可用** |

## 原则
- 不改 `policy_skystore.py` / 作者 `helpers.py`
- 缺 csv 时从 skystore 树 / simulation profiles 复制；GCP 空洞用 merge 脚本补全
- `sky-s3` 需与作者时代匹配的 openapi rust client（当前 7.6 generator 导出函数名/类型与 `s3-proxy` 源码不一致）
