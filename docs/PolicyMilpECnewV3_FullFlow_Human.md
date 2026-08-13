# PolicyMilpECnewV3 全流程人话版（含读 miss 比价、MILP 执行、FB/FP 与计费）

> 目的：把分散在 `PolicyTevictRangesNew`、`SimulatorV2`、`transfer_policy`、MILP 文档里的逻辑串成一条完整链路，避免“每段都懂，合起来不清楚”。

---

## 1) 先给结论：你最担心的点是对的

当前仿真阶段，必须同时满足这三件事，结论才可信：

1. **计费对齐真实云**：storage + transfer + API（`pricePut/priceGet`）都要算；
2. **读 miss 做候选路径比价**：不是写死“拉两片”，而是在可行候选里选最便宜；
3. **行为统计不被分片污染**：Histogram 统计逻辑对象访问行为，不把 shard 次数当“用户行为”重复记。

---

## 2) 你要的“原样回答”先放这里（核心版）

这套策略分两层：

- **行为预测层**：Histogram + TTL（看用户怎么访问）；
- **执行计费层**：Replicate/EC 放置与读取路径（看系统怎么存、怎么拉、怎么算钱）。

你之前想不通，主要是把两层混到一起了。

### 2.1 Histogram 到底怎么算

- 每条 READ 触发 `updatePastRequests(request, issueRegion)`（tevict 系策略）。
- 针对 `objKey` 在该 region 的相邻访问间隔 `tnext`，落入 `ranges` 桶，按 GB 累加。
- `window_size=-1` 时持续累计 `next_hist`；`window_size>0` 时按窗口交换到 `hist`。
- Histogram 语义固定为 **logical_access**：统计逻辑对象访问，不按 shard 展开行为事件。

### 2.2 TTL 怎么算

- `getTevict(src,dst,timestamp)` 先看 12h 缓存，命中则复用；
- 否则遍历 region 对并调用 `calcEvictCost + findMin`；
- 本质是在候选 TTL 网格里，比较“多存一会儿”vs“删掉后下次重拉”的综合成本，取最小值。

### 2.3 READ miss 的核心

不能写死“只拉两片”。必须做候选路径比价：

- 拉 full replica；
- 拉 shard 恢复（本地有一片时再拉一片；本地无片时拉 k 片）；
- 两者都可行时比较总成本后选最低。

统一口径：

\[
TotalCost = TransferCost + RequestApiCost
\]

其中 `priceGet` 采用**单点记账**：每次逻辑读只记一次，候选比价层与常数项层不得重复计。

---

## 3) 你指出的缺口补全：MILP 结果到底怎么执行

MILP 输出不是最终结果，必须经过“执行映射”：

1. **MILP 求解输出**：`z`, `x[]`, `y[][]`（模式 + 放置变量）；
2. **写入决策缓存**：`LayoutDecisionV3(issueRegion, milpTickSeq, objVal, ttlSecSnapshot, ...)`；
3. **请求落地时读取缓存**：
   - `z=0`：按 `x[]` 映射副本区；
   - `z=1`：按 `y[][]` 映射三片区；
4. **交给仿真主循环执行**：
   - `place()` 返回区域列表；
   - `getPlacementWithSizes()` 返回每个区域写入字节（EC 用分片大小）；
   - transfer policy 生成 read/write transfer graph；
   - `initiate_data_transfer(...)` 真正创建/更新 `PhysicalObject` 并计费。

> 重点：MILP 只是“选方案”，真正花钱和产生对象是在 `SimulatorV2` 的 transfer + object 管线里。

---

## 4) 计费怎么落账（按代码口径）

### 4.1 三类成本（必须分开看）

1. **Storage cost**：由 `RegionManager` 聚合剩余存储成本（与对象大小、TTL、区域存储价相关）；
2. **Transfer cost**：`_updateTransferMetric` 对 transfer graph 按边 `cost × bytes` 计算；
3. **Request API cost**：写时按放置区累计 `pricePut`，读时累计 `priceGet`。

### 4.2 关键口径

- API 费是“每次请求一笔”语义，不等同于按 GB 的 transfer 费；
- transfer 费与字节量绑定，EC 与 Replicate 会因 bytes/路径不同而显著不同；
- MILP 系数中的 REQ/T 必须与上述账本口径一致，否则优化目标与仿真账单不对齐。

---

## 5) FB/FP 两种模式（你点名要补）

这里按仿真器语义解释：

- **FB = Fixed-Base**（`config.fixedBaseRegion=true`）  
  base region 固定，不随请求漂移。TTL/驱逐里对 base 有特殊保护逻辑（避免把基础副本当普通副本处理）。

- **FP = Free-Placement**（`config.fixedBaseRegion=false`）  
  基础放置可随请求/策略变化，路径与 TTL 计算中会出现 `net_cost==0` 的修正分支（代码里有保护，避免异常 TTL）。

### 5.1 为什么 FB/FP 会影响结论

因为它直接改变：

1. 哪个副本算“基础副本”；
2. 哪些对象可被驱逐；
3. 读 miss 时可用候选集合（本地/远端可用副本与片集合不同）；
4. 最终 transfer 路径和成本。

> 所以实验报告必须注明 FB/FP 模式，否则同策略可得出不同成本结论。

---

## 6) 读 miss 比价决策表（执行时必须走这个）

| 场景 | 候选计划 | 可行条件 | 传输字节（示意） | 决策 |
|------|----------|----------|------------------|------|
| 本地有 replica | local-replica | 本地完整副本可读 | 0 | 直接选 |
| 本地有 1 片 + 远端有片 | fetch-1-shard | 可再拿到 `k-1` 片 | `size_logic/k` | 与 full-replica 比价 |
| 本地有 1 片 + 远端有 replica | fetch-full-replica | 任一远端完整副本可读 | `size_logic` | 与 fetch-1-shard 比价 |
| 本地无数据 + 远端有 replica | fetch-full-replica | 至少一份远端完整副本 | `size_logic` | 与 fetch-k-shards 比价 |
| 本地无数据 + 远端有片 | fetch-k-shards | 至少 `k` 片可拿到 | `k*(size_logic/k)` | 与 full-replica 比价 |
| 都不可恢复 | fail | 无可行候选 | - | strict_fail 终止 |

---

## 7) 一条 READ 的完整流程（从请求到落账）

1. 收到 READ，请求含 `objKey, issueRegion, timestamp, size`；
2. `updatePastRequests` 更新 Histogram；
3. 若命中 `milp_tick`：
   - 构建系数（含 TTL/STOR/REQ/T）；
   - Gurobi 解布局；
   - 写入布局缓存；
4. 调 `get_placements` 与 transfer policy 生成候选路径；
5. 若本地 miss，执行候选比价并选择最小成本路径；
6. `initiate_data_transfer` 执行传输、对象更新、TTL 刷新/赋值；
7. tracker 分别累加 transfer/request/storage 三类成本；
8. 输出 metrics 与日志（用于三账对齐）。

---

## 8) 执行版检查清单（含失败判据与责任模块）

下面 5 条是“放行云上实验”的硬门槛。每条都给出：责任模块、证据、失败判据。

| 检查项 | 责任模块 | 证据（必须有） | 失败判据 |
|------|---------|---------------|---------|
| REQ/T 纳入 `pricePut/priceGet` | `PolicyMilpECnewV3.buildCoefficients` | 每 tick 输出 API 分项（rep/ec）与单价来源 | API 分项长期为 0 或与 request cost 不可对账 |
| 读 miss 做 replica/shard 比价 | transfer policy + `chooseMinCostReadPlan` | 输出候选计划、分项成本、最终选择 | 出现硬编码优先级；候选集合不完整 |
| `T_ec` 与 runtime 口径一致 | MILP 系数构建 + transfer policy | 输出 `tEcModel` 与 runtime 对应 case | 同 case 下公式不一致且未版本化说明 |
| FB/FP 明确记录 | `SimulatorV2` 初始化 + 报告层 | 结果元数据含 `fixedBaseRegion` 与 base 规则 | 报告缺失 FB/FP 字段或解释 |
| 三账对齐（手算/仿真/MILP） | tracker + 报表汇总 | 分项误差报表（storage/transfer/API） | 任一分项误差超阈值（默认 1%） |

### 8.1 失败即停止（strict_fail）

任一失败判据命中时：

1. 当前实验批次标记为 failed；  
2. 返回非零退出；  
3. 禁止继续跑大规模云实验。

### 8.2 最低日志字段（供复盘）

- `milpTickSeq`, `issueRegion`, `templateObjKey`
- `sRep[]`, `sEc[][]`, `tRep`, `tEc`, solver status, objVal
- `selectedReadPlan`, `candidatePlans`, `candidateCostBreakdown`
- `fixedBaseRegion`, `baseRegionRule`

---

## 9) 与仓库文档的对应关系

- 代码结构与执行落点：`docs/PolicyMilpECnewV3_CodeDesign.md`
- 数学定义与变量：`docs/PolicyMilpECnewV3_Gurobi_Spec.md`
- 计费与语义拆分：`docs/Simulation_Cost_API_Histogram_EC.md`
- 单对象手算对账：`docs/HandCalc_Replicate_vs_EC_1GB.md`
- 总设计约束（FB/FP、读 miss、口径一致）：`docs/PolicyMilpECnewV3_Design.md`

