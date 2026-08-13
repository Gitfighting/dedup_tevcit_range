# 第二次代码走查 / Debug（剩余问题清单）

- 时刻：**2026-08-12 08:18 (UTC+8)**
- 范围：当前代码 + 服务器实盘探针（栈当时已 DOWN，模块 import 正常）
- 相对第一遍：S3 同区 PUT/GET + CDC 去重后台已验证通过；本遍聚焦**仍未解决**项

---

## 当前状态（探针）

| 项 | 状态 |
|----|------|
| 进程 3000/8002/8014 | **DOWN**（未常驻；需手动 `launch_local_stack`） |
| `dedup` / CDC import | OK（`0xDEADBEEF` 已修） |
| 第一遍 S3+CDC 冒烟 | 已通过（见 walkthrough1 日志） |

---

## 仍存在的问题（按优先级）

### P1 — 功能闭环未完成

1. **HOT/DEDUP 决策路径几乎未被同区冒烟触发**  
   `get_ttl` 里写 `_obj_state` 只发生在：跨区拉取且 `adaptive_ttl>0` 且能算到 `ttn`。  
   同区 PUT 走 `fixed_base_region=True` → 直接 TTL=-1，**不决策**。  
   故第一遍 STATE 里 `obj_state={}` 是预期现象，不是“去重引擎坏了”，而是**消融尚未压到决策分支**。

2. **完整 `.mc-aug` 消融回放未落地**  
   `run_prototype_ablation.py` 仍主要是 sync+启栈；无自动 replay → `clean_out_remaining` → 结果 JSON 矩阵。

3. **sky-s3 数据面仍不读 `storage_format`/`chunk_manifest`**  
   靠 cache 物化兜底；作者代理内 **~117 处 `unwrap()`**，元数据 500 会直接打崩代理。

### P2 — 正确性 / 计账

4. **`TraceIdx` 与 PUT 时间戳错位**  
   `start_upload` 用 `get()-1`；计数器只在 GET 路径 `increment`。首批 PUT 时 idx=-1 → 取 `timestamps[-1]`（最后一条），与真实事件序可能严重偏离，影响 SkyStore/DedupAware 学习与 TTL。

5. **`L` 仍偏乐观**  
   虽已改为 `entry.region == PUT region`，但对“新写入本区”的 chunk 也算 local hit，首写 L 易冲到 1.0；跨区“目标区是否已有副本”的语义仍弱。

6. **DEDUP TTL=0 的物理语义**  
   决策为 DEDUP 时返回 TTL 0（不缓存整副本），但 PUT 侧仍把整文件写进 s3s-fs；存储节省主要在**计账/跨区副本**，不是本区磁盘立刻变“仅 manifest”。

### P3 — 工程 / 环境

7. **`delete.py` 仍有 `LOCK TABLE`**（SQLite 下删除路径会炸）；`put.py` 已对 LOCAL 跳过，delete 未对齐。

8. **作者 `db.py` UltraDict 分支 bug 仍在** — 靠“不装 UltraDict + SQLite”绕过。

9. **启动/运维脆弱**  
   - 必须 `aws s3 mb` 才能写元数据（启动只建 s3s-fs 物理桶）  
   - 禁止对同名物理桶重复 `register_buckets`  
   - 栈不常驻，重启后状态依赖 `/tmp/dedup_*` 与 sqlite

10. **`get.py` 仍保留 Postgres `::interval` 分支**（非 LOCAL 时）；LOCAL 已切 SQLite 方言 — 若误关 `LOCAL` 会再炸。

---

## 已关闭（相对第一遍，不再算“当前问题”）

- CDC 非法字面量、逻辑/物理 key、SQLite `::interval`（LOCAL）、缺 `manual.csv` / closest、S3 同区往返、CDC 二次上传去重命中、sidecar 持久化 refs

---

## 建议的下一刀（若继续）

1. 构造**跨区 GET** 用例，强制走进 `get_ttl` HOT/DEDUP 分支并断言 `obj_state` / `storage_format`  
2. 给 `delete.py` 做与 put 相同的 LOCAL LOCK 跳过  
3. 把 `run_prototype_ablation` 接到 `replay_mc_aug` + `clean_out_remaining`  
4. （可选）纠正 TraceIdx 与 PUT 时间戳对齐
