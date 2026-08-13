# PolicyMilpECnewV3 详细代码设计（实现指导）

> **依据**：`docs/PolicyMilpECnewV3_Gurobi_Spec.md`（公式、变量、聚合单元 = `issueRegion`、`TTL`→存储、**§2.5.2 API 边际**）、`PolicyMilpECnewV3_Design.md`（业务语义）、**`docs/Simulation_Cost_API_Histogram_EC.md`**（仿真计费人话、直方图与 EC 分工）。  
> **目标**：给出 **包/类/方法/配置/与仿真器对接** 的可落地设计，便于直接开写 Java。  
> **对账**：系数构建 **必须** 含 **`pricePut`/`priceGet`**，否则 MILP 与 trace 仿真 **request cost** 维度不对齐（见 Gurobi 规范文首硬性要求）。
> **部署模式（本稿固定）**：**`strict_fail`**。Gurobi 不可用、求解失败/超时、关键系数异常时 **立即失败退出**，不做 Tevict 降级。

---

## 0. 实现现状与「EC 分片放置」是否已落地（重要）

| 项 | 状态 |
|----|------|
| **Gurobi 规范**（`z,x,y`、TTL→存储、`issueRegion` 聚合） | 文档已写 |
| **`PolicyMilpECnewV3.java` / `milpv3/*`** | **仓库中尚未存在**；设计仅为实现指导 |
| **仿真器内「真的」把 EC 三片放到三个 region** | **依赖** `place` / `getPlacementWithSizes` 返回 **三处 region + 分片大小**，且 **`SimulatorV2`（或等价路径）据此创建/更新多个 `PhysicalObject`**；**未写完策略类前，端到端 EC 分片放置未成立** |

**设计上的诚实说明**：MILP 里的 **`y[r,k]` 只是「决策变量」**；**只有** 在 Java 里 **(1)** 求解后写入 `LayoutDecisionV3`，**(2)** `place` 按 `y` 选出 **三个不同 region**，**(3)** `getPlacementWithSizes` 给出 **与 EC(2+1) 一致的分片字节**（`kData=2` 时每片 `size/2`），**(4)** 仿真器 **为同一 logical 对象写入三条物理记录并计入传输/存储**，才算 **「实现了 EC shard 的放置」**。当前文档描述的是 **应如何实现**；**不是**「已在主分支完成并可跑 trace 出数」。

**建议验证顺序**（与「先仿真证明再上云」一致）：

1. **本地/CI**：小 trace 或单对象场景，断言 EC 模式下 **3 个 region、3 个 physical、大小之和正确、读路径可重构**。
2. **再**：长 trace + Gurobi 环境，导出指标（成本、命中率、违例）。
3. **最后**：云服务器（许可证、数据路径、配置）跑 **可复现实验**，保存日志与汇总表。

---

## 1. 范围与非目标

| 包含 | 不包含 |
|------|--------|
| 新策略类 **`PolicyMilpECnewV3`**、Gurobi 建模样板、系数管线 | 不改 **`PolicyMilpECnew` / `PolicyMilpECnewV2`** 旧逻辑（仅可参考其 Gurobi 依赖写法） |
| **`SimulatorV2`** 中 **placement 名**、**`updatePastRequests`**、**`calculateTtl`** 挂钩点 | Python `simulator_v2` 全量移植（可后续对齐） |
| **`Config`** 新字段建议 | 完整单元测试用例代码 |

---

## 1.1 语义冻结（云实验前不再摇摆）

1. **Histogram 语义固定为 `logical_access`**：统计用户对 `LogicalObject` 的访问行为，不按 shard 数量扩写行为统计。
2. **计费分层固定**：TTL/Histogram 用逻辑对象语义；存储/传输/API 用物理分片语义。
3. **求解策略固定为 `strict_fail`**：不允许静默降级到纯 Tevict。
4. **REQ/T 首版必须含 API**：`pricePut`/`priceGet` 不可占位为 0。
5. **计费执行口径固定为 `python_compat=true`**：V3 对账默认走 `SimulatorV2.applyPythonArtifactRequestCosts(...)` 分支；旧 Java 记账路径保留但不作为 V3 实验口径。

---

## 1.2 改动边界（继承优先，最小侵入）

目标：尽量通过继承扩展实现 V3，减少对现有主流程和旧策略的改动，降低回归风险。

### A. 优先级（必须遵守）

1. **优先继承**：`PolicyMilpECnewV3 extends PolicyTevictRangesNew`，先复用再扩展。  
2. **其次新增类**：把新增逻辑放在 `placement_policy/milpv3/*`（builder/bundle/helper/cache model）。  
3. **最后改主流程**：`SimulatorV2` 仅做策略注册与最小分支接入，不重写主循环。

### B. 允许改动（白名单）

- 新增 `PolicyMilpECnewV3.java` 与 `milpv3/*`。  
- `SimulatorV2` 中与 `placementPolicy` 注册、策略名单、`pythonCompat` 启动断言相关的小改。  
- `Config` 增加 V3 所需字段（不改变旧字段语义）。

### C. 禁止改动（黑名单）

- 不改 `PolicyTevictRangesNew` 的核心统计/TTL算法语义。  
- 不重写 `SimulatorV2` 的主请求循环与 tracker 主计费路径。  
- 不为 V3 破坏旧策略（`milp_ec_new` / `milp_ec_new_v2` / `tevict_ranges`）既有行为。  

### D. 变更审查规则

- 任何触及黑名单的改动都必须在设计文档先补“原因 + 影响面 + 回归清单”，并单独评审。  
- 默认不接受“为了方便实现”而扩大改动范围。

---

## 2. 包与类一览

建议路径（与现有 `placement_policy` 一致）：

```
src/main/java/org/example/src/placement_policy/
  PolicyMilpECnewV3.java          // 主策略：继承 PolicyTevictRangesNew，实现 MILP + 模板缓存
  milpv3/
    AggregateUnitKey.java         // 可选：record，默认 issueRegion
    LayoutDecisionV3.java         // record：z, x[], y[][], decidedAt, issueRegion, objVal
    MilpCoefficientBundle.java    // 本拍 S_rep[], S_ec[][], T_rep, T_ec, ttl_sec[]
    GurobiEc21MilpBuilder.java    // 纯建模型 + optimize + 读解（无业务）
    StorageCostHelper.java        // 封装 TTL→pricePerGB×size，与 RegionManager 语义对齐
```

**继承关系**：`PolicyMilpECnewV3 extends PolicyTevictRangesNew`  
- **复用**：`updatePastRequests`、`calcEvictCost`、`findMin`、`getTevict`、直方图状态。  
- **新增**：`milp_tick` 判定、**`buildCoefficients`**、**`solveAndCacheLayout`**、`place`/`getPlacementWithSizes` 读缓存决策。

---

## 3. 核心数据结构与职责

### 3.1 `AggregateUnitKey`（默认）

```java
public record AggregateUnitKey(String issueRegion) {}
```

- **与 §0.1 Gurobi 规范一致**：**一次 MILP 实例** 由 **`(milpTickSeq, issueRegion)`** 唯一确定（`milpTickSeq` 可用 **时间窗序号** 或 **单调递增 long**）。

### 3.2 `LayoutDecisionV3`

| 字段 | 类型 | 含义 |
|------|------|------|
| `mode` | `int` | `0`=副本，`1`=EC（与 `z` 一致）；布局为 **base + 2 shard**（见 `GurobiEc21MilpBuilder.NUM_EC_SHARD_SLOTS`） |
| `z` | `boolean` | 是否 EC |
| `x` | `boolean[n]` | 与 `regions` 顺序一致 |
| `y` | `boolean[n][2]` | 两片 shard（与 `GurobiEc21MilpBuilder.NUM_EC_SHARD_SLOTS` 一致） |
| `baseRegionIndex` | `int` | 基底 \(b\) |
| `issueRegion` | `String` | 聚合单元键 |
| `decidedAt` | `Instant` | 求解时刻 |
| `objVal` | `double` | Gurobi 目标值 |
| `ttlSecSnapshot` | `double[]` | 本拍用于 STOR 的 `ttl_sec[r]`（对账） |

**可选**：`expiresAt` = `decidedAt + policyTtl` 若需要 **决策过期** 再重解。

### 3.3 `MilpCoefficientBundle`

封装 **`buildCoefficients`** 输出，供 `GurobiEc21MilpBuilder` 与日志 **只读** 传入：

- `double[] sRep`（长度 \(n\)）
- `double[][] sEc`（`n×2`，与 **`NUM_EC_SHARD_SLOTS`** 一致）
- `double tRep`, `double tEc`
- `double[] ttlSec`（长度 \(n\)）

---

## 4. `PolicyMilpECnewV3` 方法清单（建议）

| 方法 | 可见性 | 职责 |
|------|--------|------|
| 构造函数 | `public` | `super(config, graph, logicalObjects, regionManager)`；初始化 `GRBEnv`（懒加载）、`Map<AggregateUnitKey, LayoutDecisionV3> decisionCache` |
| `updatePastRequests` | `@Override` | `super.updatePastRequests(...)` 后 **若** `shouldRunMilpTick(request)` **则** `runMilpForIssueRegion(request.issueRegion, request.timestamp)` |
| `place` | `@Override` | 若 `decisionCache.get(key)` 有效且未过期 → 按 `mode` 返回 **副本区列表** 或 **EC 三片区**；仅在“策略未接管/初始化”场景允许默认路径，**求解失败场景不得 fallback**（strict_fail） |
| `getPlacementWithSizes` 等 | `@Override` | EC 时返回 **分片大小** `size/2`（EC(2+1), `kData=2`） |
| `getTevict` | `@Override` | **优先**复用父类（与现有仿真一致）；若需 **EC 片 TTL** 与 **父类一致** 即可 |
| `shouldRunMilpTick(request)` | `private` | 实现 **§10.3.2** 触发（如直方图 `hist` 交换、`window_size`、或独立计数器）；**且** `hasRedundancy`（准入） |
| `runMilpForIssueRegion(issueRegion, now)` | `private` | ① **模板 `LogicalObject`**（见 §2.2）② **`buildCoefficients`** ③ **`GurobiEc21MilpBuilder.solve`** ④ **写 `decisionCache`** |
| `buildCoefficients(issueRegion, templateLo, now)` | `private` | ① **父类 `getTevict(src,dst,now)`** 填 `ttl_sec[r]`（规则写死）② **`StorageCostHelper`** 算 STOR ③ **REQ/T（首版即含 API）** ④ 返回 `MilpCoefficientBundle` |
| `resolveTemplateKey(Request)` | `private` | 返回本拍 **模板** `objKey` / `LogicalObject`（见 Gurobi Spec §2.2） |

**注意**：`runMilpForIssueRegion` **必须**在 **同一线程** 与仿真主循环一致（当前 `SimulatorV2` **单线程**），避免 Gurobi 与状态并发问题。  
**严格模式**：求解状态非 `OPTIMAL`/`SUBOPTIMAL`（按你最终允许列表）即抛出异常，终止实验。

---

## 5. `GurobiEc21MilpBuilder`（纯 MILP）

**输入**：`MilpCoefficientBundle coeff`、`int baseIndex`、`List<String> regions`（有序）  
**输出**：`LayoutDecisionV3`（严格模式下不返回空；失败直接抛错）

**步骤**：

1. `GRBModel model = new GRBModel(env)`  
2. `addVar`：`z`, `x[r]`, `y[r][k]`（`k=1..3` 映射到数组 0..2）  
3. `addConstr`：§4 Gurobi 规范  
4. **目标**：`A = sum(sRep[r]*x[r]) + tRep`，`B = sum(sEc[r][k]*y[r][k]) + tEc`，**线性展开** `A + z*(B-A)`（`LinExpr`）  
5. `model.set(GRB.DoubleParam.TimeLimit, ...)` 等  
6. `optimize()` → 校验 `Status`：若 `INFEASIBLE` / `TIME_LIMIT` / `NUMERIC` 等异常状态，直接抛错  
7. 状态合法才读 `X`  
8. `model.dispose()`（**每解一次** 或 **池化**；若 `persistentEnv` 则 **勿** dispose env）

**常量**：`GRB.BINARY`，`GRB.MINIMIZE`。

---

## 6. `StorageCostHelper`

**职责**：把 **Gurobi Spec §2.4** 固定成 **可单测** 的 API：

```java
double storRepGb(double sizeLogicGb, String region, double ttlSec, RegionManager rm, DiGraph g);
double storEcShardGb(double sizeShardGb, String region, double ttlSec, RegionManager rm, DiGraph g);
```

**实现**：内部 `Duration.ofSeconds((long) ttlSec)` + 调用 **`RegionManager` 的 `pricePerGB` 逻辑**——  
- **推荐**：**抽取** `RegionManager` 中 `private BigDecimal pricePerGB(String region, Duration duration)` 为 **包可见** 或 **复制** 到 `StorageCostHelper`（**与 `RegionManager` 数值一致** 为验收标准）。  
- **禁止**：与 `RegionManager` 两套 **不一致** 的月/天换算。

---

## 7. REQ 与 T（首版可上云口径）

| 分量 | 首版必须 | 备注 |
|------|----------|------|
| `REQ_rep[r]` | **必须包含 API 边际**：至少 `Nw_pred * pricePut[r]`，并与 `priceGet` 口径一致对账 | 禁止先置 0 再补 |
| `REQ_ec[r][k]` | **必须包含每片 API 边际**：每个 `(r,k)` 对应写 API 成本项 | 与 `y[r][k]` 同现 |
| `T_rep`, `T_ec` | 必须包含传输成本；`T_ec` 必须按固定恢复口径计算 | 见 §7.2 |

### 7.1 Histogram 与 TTL 的语义（固定）

- Histogram 只表示 **用户访问行为**（`LogicalObject` / `Request.size` 语义），不按 shard 重复累积行为事件。  
- TTL 仍由 `PolicyTevictRangesNew` 路径给出（`calcEvictCost` + `findMin` / `getTevict` 缓存机制）。

### 7.2 `T_ec` 恢复口径（必须写死一种）

为了云端实验可复现，本稿固定如下：

1. **本地有 1 片**：按“再拉 1 片可恢复”计跨区传输。  
2. **本地无片**：按“拉 2 片可恢复”计跨区传输。  
3. 每片字节按 `size_logic / kData`（EC(2+1) 固定 `kData=2`，即每片 `size_logic/2`）计算。  
4. 若后续改为别的恢复策略，必须同步改：`T_ec` 公式、transfer policy、实验说明。

**日志**：`Logger.info` 至少打印 **本拍** `ttl_sec[]`、`sRep/sEc` 摘要、`tRep/tEc`、`objVal` 便于对账。

### 7.3 读 miss 比价决策表（必须实现，禁止写死“只拉两片”）

目标：当 `issueRegion` 本地无法直接满足读取时，在**可恢复候选**里比较总成本，选择最便宜路径。  
总成本统一口径：

\[
\text{TotalCost(plan)} = \text{TransferCost(plan)} + \text{RequestApiCost(plan)}
\]

其中：

- `TransferCost`：边 `cost × bytes`（由 transfer graph 求和）
- `RequestApiCost`：与仿真 request 计费口径一致；每次逻辑读的 `priceGet` **只计一次**（固定单点记账，禁止重复）

| 场景 | 候选计划 | 可行条件 | 传输字节（示意） | 决策 |
|------|----------|----------|------------------|------|
| 本地有 replica | `PlanA: local-replica` | 本地存在未过期完整副本 | `0` | 直接选 `PlanA` |
| 本地有 1 片 + 远端有足够片 | `PlanB: fetch-1-shard` | 可再拿到 `kData-1` 片（EC(2+1) 固定 `kData=2`，即 1 片） | `size_logic / kData` | 与 `PlanC` 比价后选低 |
| 本地有 1 片 + 远端有 replica | `PlanC: fetch-full-replica` | 任一远端有未过期完整副本 | `size_logic` | 与 `PlanB` 比价后选低 |
| 本地无数据 + 远端有 replica | `PlanD: fetch-full-replica` | 任一远端有未过期完整副本 | `size_logic` | 与 `PlanE` 比价后选低 |
| 本地无数据 + 远端有足够片 | `PlanE: fetch-k-shards` | 可拿到 `kData` 片（EC(2+1) 固定 `kData=2`） | `kData * (size_logic / kData)` | 与 `PlanD` 比价后选低 |
| replica 与 shard 都不可用 | `PlanF: fail` | 无可恢复候选 | - | 按 strict_fail 终止 |

**实现要求（强制）**：

1. 候选集合必须同时包含 **replica 路径** 和 **EC 恢复路径**（只要它们可行）。  
2. 统一调用一个 `chooseMinCostReadPlan(...)`，返回最小 `TotalCost` 的计划；禁止硬编码优先级。  
3. 日志必须输出：候选计划、每项分成本、最终选择、`readFrom`。  
4. `T_ec` 估计与运行时 transfer policy 必须使用同一口径（否则三账无法对齐）。
5. `RequestApiCost` 与 `T_*` 常数项必须遵守“GET 单点记账”互斥规则，禁止重复计。

---

## 8. `Config` 与 YAML

| 字段 | 类型 | 含义 |
|------|------|------|
| `placementPolicy` | `String` | 新增 **`milp_ec_new_v3`**（与 `SimulatorV2` 分支一致） |
| `milpTimeLimitSec` | `double` | Gurobi `TimeLimit`，默认 `2.0` |
| `milpMipGap` | `double` | 默认 `0.05` |
| `milpTriggerMode` | `enum` | `HISTOGRAM_TICK` / `INDEPENDENT_WINDOW`（可选，与 §10.3.2 对齐） |
| `milpStrictFail` | `boolean` | 固定为 `true`（云实验） |
| `pythonCompat` | `boolean` | **V3 必须为 `true`**（计费与 Python artifact 对齐） |

**`SimulatorV2`** 需修改：

1. **`_select_placement_policy`**：`case "milp_ec_new_v3": return new PolicyMilpECnewV3(...)`  
2. **`initiate_data_transfer`** 中 **`updatePastRequests`** 的 **策略名列表** 增加 **`milp_ec_new_v3`**  
3. **`calculateTtl`** 中 **`tevict_ranges` 分支** 的 **策略名列表** 增加 **`milp_ec_new_v3`**（与 `milp_ec_new` 并列）
4. **V3 启动时强制 `pythonCompat=true`**：可通过 YAML 或 JVM `-Dskystore.pythonCompat=true`；若为 `false` 则按 strict_fail 直接报错退出（防止误走旧 Java 记账分支）

---

## 9. 调用时序（单次 read + MILP）

```
SimulatorV2.initiate_data_transfer(..., read)
  → placementPolicy.updatePastRequests(request, issueRegion)
       → PolicyMilpECnewV3: super.updatePastRequests
       → shouldRunMilpTick? → runMilpForIssueRegion(issueRegion, ts)
            → buildCoefficients → GurobiEc21MilpBuilder.solve → decisionCache.put
  → ... 传输/计费 ...
  → calculateTtl → getTevict (父类)
```

**MILP 与 TTL**：**同拍** 在 `runMilpForIssueRegion` **内**先算 **`ttl_sec`**（通过 **`getTevict`** 或 **内部** 调 `calcEvictCost`+`findMin` 路径），再用于 **`StorageCostHelper`** 与 **系数**。

---

## 10. 错误处理（strict_fail）

| 情况 | 行为 |
|------|------|
| `GRB.INFEASIBLE` / `TIME_LIMIT` / 其它异常状态 | **立即抛错并终止实验**（非零退出码） |
| 系数 **NaN** / `Infinity` / 维度不匹配 | **立即抛错并终止实验** |
| Gurobi 未安装 / 许可证不可用 | **启动即失败退出**，不进入仿真主循环 |
| 运行时 env/model 异常 | 释放资源后失败退出；日志写明 `issueRegion` 与 `milpTickSeq` |

**禁止行为**：保留上一拍结果继续跑、回退纯 Tevict、静默改成 `z=0`。

---

## 11. 实现顺序（建议）

1. **先锁定运行口径**：V3 运行前断言 `pythonCompat=true`，并打印当前计费分支（artifact / legacy）。  
2. **`StorageCostHelper` + 单测**（与 `RegionManager` 对同一 `ttl`/`size` 比对）  
3. **`GurobiEc21MilpBuilder` + 固定小系数** 跑通 **可行解**  
4. **`PolicyMilpECnewV3`** 只接 **`buildCoefficients`** 假数据 + **cache**  
5. **接入** `getTevict` 真 **`ttl_sec`**  
6. **REQ/T** 首版直接按 API+恢复口径实现，不走“先 0 后补”  
7. **`SimulatorV2`** 注册与 **端到端 trace** 回归（强制 python_compat）  

---

## 12. 上云前验收门槛（必须全部通过）

1. **三账对齐**：同一批请求下  
   - 手算基准（`docs/HandCalc_Replicate_vs_EC_1GB.md`）  
   - 仿真 tracker 分项（storage/transfer/request API）  
   - MILP 系数分项（`S_rep/S_ec/T_rep/T_ec`）  
   三者误差在预设阈值内（建议 <=1%）。
2. **恢复口径一致**：`T_ec` 的拉片规则与 transfer policy 实际行为一致。  
3. **严格模式验证**：人为制造 `TIME_LIMIT` / 许可证缺失时，进程必须失败退出。  
4. **可复现日志完整**：每个 MILP tick 有下列字段：
   - `milpTickSeq`, `issueRegion`, `templateObjKey`
   - `ttlSec[]`, `sRep[]`, `sEc[][]`, `tRep`, `tEc`
   - solver status, runtime, mipGap, objVal
5. **接口冻结**：`place`、`getPlacementWithSizes`、`getReadTransferSize` 与 `buildCoefficients` 的输入输出在编码前锁定。

---

## 12.1 “8）检查清单”落实映射表（规则 -> 证据 -> 失败条件）

| 检查项 | 可执行规则（实现约束） | 证据/日志（必须可导出） | 失败条件（任一命中即判失败） |
|------|--------------------|----------------------|------------------------------|
| REQ/T 已纳入 `pricePut`/`priceGet` | `buildCoefficients` 首版即产出非空 API 分项；禁止占位 0 | 每个 tick 输出 `reqApiRep[]/reqApiEc[][]` 摘要及 `pricePut/priceGet` 来源 | 任一 tick API 分项全为 0，或与 request cost 长期不可对账 |
| V3 走 python_compat 分支 | `config.pythonCompat` 必须为 `true`，计费调用 artifact 分支 | 启动日志输出 `python_compat=true` + `getApiChargePoint=python_artifact` | 任一 V3 实验出现 `pythonCompat=false` 或走 legacy Java 记账 |
| 读 miss 比较 replica 与 shard | read path 必须走 `chooseMinCostReadPlan(...)`；候选含 replica+EC（只要可行） | 输出候选集合、分项成本、最终 `selectedPlan`、`readFrom` | 出现硬编码优先级（如固定先 EC）或候选缺失 |
| `T_ec` 与 transfer policy 口径一致 | `T_ec` 使用与 runtime transfer policy 同一恢复规则与 bytes 公式 | 输出 `tEcModel`, `tEcRuntimeEstimate`, `recoveryCase`（local1/noLocal） | 同一 case 下 model/runtime 口径不一致且无版本说明 |
| GET 记账不重复 | `RequestApiCost` 与 `T_*` 对 GET 二选一记账 | 输出 `getApiChargePoint`（runtime 或 T_const）与 `getApiCount` | 同一逻辑读出现重复 GET 计费 |
| FB/FP 显式记录 | 每次实验必须记录 `fixedBaseRegion` 与 base 行为规则 | 结果元数据含 `fixedBaseRegion`, `baseRegionRule`, `placementPolicy` | 报告或结果文件缺失 FB/FP 字段 |
| 三账对齐日志完整 | 每批次输出手算基准、tracker 分项、MILP 分项并计算误差 | 统一报表含 `storage/transfer/requestAPI` 与 `S/T` 对账字段 | 任一分项误差 > 阈值（默认 1%）且未给出解释 |

**备注**：

1. 该表是“是否允许进入云上大规模实验”的闸门；不是建议项。  
2. 与 `strict_fail` 一致：对账失败应中止实验，不可静默继续。

---

## 13. 文档索引

| 文档 | 用途 |
|------|------|
| `PolicyMilpECnewV3_Gurobi_Spec.md` | 数学与聚合单元 **操作定义** |
| `PolicyMilpECnewV3_Design.md` | EC 联动删、业务场景 |
| `PolicyMilpECnewV3_FullFlow_Human.md` | 全流程人话说明 + 执行版检查清单 |
| **本文** | **代码结构与对接点 + 验收闸门** |
