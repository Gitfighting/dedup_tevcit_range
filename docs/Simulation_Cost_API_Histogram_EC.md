# 仿真计费、API 费、直方图与 EC：说明（人话版）

> **目的**：把「云上 REST 计费」「MILP 里该不该算 API」「直方图到底在统计什么」一次说清楚，避免和代码/论文假设打架。  
> **配套**：`PolicyMilpECnewV3_Gurobi_Spec.md`（公式）、`PolicyMilpECnewV3_Design.md`（总设计）。  
> **直方图 / TTL 流程（对照源码）**：`TevictRanges_Histogram_TTL_and_EC.md`。

---

## 1. 仿真里 `pricePut` / `priceGet` 和「按流量计费」是两回事

### 人话

- **`pricePut` / `priceGet`**（记在图每个 region 节点上）：  
  **更像「每发起一次 PUT/GET 请求，云厂商收一笔固定费」**——数值在配置里是多少就是多少，**不是「对象大小 × 某单价」**。  
  写路径上：**每往一个 region 成功算一次放置，就加一笔该区的 `pricePut`**（多副本 = 多个区就多次）。  
  读路径上：**一次 GET 通常加一笔读相关区的 `priceGet`**（具体以 `SimulatorV2` / `simulator_v2.py` 实现为准）。

- **跨域传数据的钱**（边上的 `cost`）：  
  **才是「传了多少字节 × 每 GB 多少钱」这类**，和对象/分片大小挂钩。Java 里由 `_updateTransferMetric`、Python 里由 `_update_transfer_metric` 用 **`effectiveSize`（可为 EC 分片）** 计算。

### 小结（对照表）

| 费用类型 | 大概怎么算 | 和对象大小的关系 |
|----------|------------|-------------------|
| **API 类** `pricePut` / `priceGet` | 按**请求次数/区域**各加一笔常数 | **默认不乘 size** |
| **传输类** 边 `cost` | **字节（或分片字节）× 单价 / GB** 等 | **乘 size** |

---

## 2. 现行口径（已冻结）：成本 = 传输 + 存储，**不含 API**

> **决策（与代码一致）**：仿真总成本与 MILP 目标都**只算运费（传输）+ 存储费**，**不计 PUT/GET API 请求费**。两边用同一把尺子，依然对齐——只是这把尺子里没有 API 这一项。
>
> **代码依据**：`Tracker.addRequestCost` 为空实现（no-op）、`totalCost = totTransfer + totStorage`（`Tracker.java:114`）；`PolicyMilpECnewV3` 系数中无 `pricePut/priceGet`。`getMetrics()` 仍输出 `total request cost ($)`（恒为 0）与 `total cost without request ($)` 仅作展示，不参与决策。

### 历史说明（已废弃）

早期版本曾要求"MILP 必须计入 API"以与仿真对齐。**现已统一为两边都排除 API**，对齐性同样成立，且模型更简单。下文凡提到 `pricePut/priceGet` 边际项之处均已失效，仅留作历史记录。

---

## 3. 直方图：记的是**用户访问行为**，EC **不该**去「改」这套统计

### 人话（纠正一种常见误会）

- **不是**「为了省事故意用逻辑对象大小做粗粒度近似」。  
- **直方图的本质**：**历史访问记录**叠出来的**统计模型**，用来**猜「以后用户还会怎么访问」**（间隔、频次、和**逻辑对象体量**相关的量等）。  
- **EC 分片**（每片多大、放几个区）是 **系统内部怎么存** 的细节。  
- 若把 **分片字节** 当成直方图里的「访问量」，等于用 **存储实现** 去扭曲 **用户侧访问特征**——**会污染**「对未来行为」的预测，和直方图的设计目的相反。

### 结论（与实现建议）

- **直方图侧**：继续用 **用户对逻辑对象的访问语义**（与 trace 里 **`Request.size` / 逻辑对象体量** 一致），**不要**因为 EC 就把 histogram 改成「每片累加」来代表用户行为。  
- **计费 / MILP**：在 **存储、传输、API** 里单独用 **分片大小、多 region PUT 次数** 去算钱；和 **直方图预测用户行为** 分开。  
- 代码里 `PolicyTevictRangesNew.getHistogramSizeGB` 的**默认**（整对象 GB）对 **「统计用户访问模式」** 是合理的；**除非**有明确论文假设要统计别的量，否则**不应**为 EC 把直方图改成「shard GB 累积」。

---

## 4. 与仿真主循环的对应关系（便于自查）

| 环节 | API | 按流量计费 |
|------|-----|------------|
| 写：多 region 放置 | 每 region 一次 `pricePut`（见仿真循环） | 每段 transfer graph 按字节 × 边价 |
| 读 | 通常一次 `priceGet`（主路径） | 多图/分片时用 `segmentSizes` / `getReadTransferSize` |

---

## 5. 文档修订记录（仓库内）

- 本文：**新建**，汇总 FAQ + **MILP 必含 API** + **直方图不掺杂 EC 分片语义**。  
- `PolicyMilpECnewV3_Gurobi_Spec.md`：**已增加** API 边际与仿真对齐说明。  
- `PolicyMilpECnewV3_Design.md`：**已增加**直方图人话段与交叉引用。

---

## 6. 统一口径（实现前冻结）

1. **EC(2+1) 固定 `kData=2`**：分片大小按 `size_logic/2` 计算。  
2. **GET 单点记账**：每次逻辑读只计一次 `priceGet`，禁止在“候选比价层”和“常数项层”重复计。  
3. **不启用 DecodePenalty**：当前阶段只对齐云账单成本（storage/transfer/API），不计内部编解码性能成本。
