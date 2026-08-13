# TevictRangesNew：直方图怎么建、TTL 怎么算、和 EC 分片啥关系（人话 + 对照代码）

> **代码依据**：  
> - Java：`src/main/java/org/example/src/placement_policy/PolicyTevictRangesNew.java`  
> - Python：`skystore_python/artifact-eval-main/simulation/src/placement_policy/policy_tevict_ranges_new.py`  
> 两边逻辑**同一套思想**（细节如 `ranges` 生成略有写法差异，不影响你理解流程）。

---

## 一句话先说清：这套策略里**没有**「对每个 EC shard 单独跑一遍直方图 / TTL」

- **直方图**：按 **「哪个 region 上的这次读」** + **「哪个逻辑对象 `obj_key`」** 更新；用的是 **整条 `Request`（逻辑体量）**，**不是**「shard1 一条、shard2 一条」在策略里循环。  
- **TTL（getTevict）**：按 **图上的 (src→dst) 边** 和 **dst 区的直方图** 算，**不是**按「每个 shard 在各区」各算一个 TTL。  
- **EC 的真实计价**（分片多大、`pricePut` 乘几次、传多少字节、拉几片恢复）在 **仿真器 + 多个 `PhysicalObject`** 里发生；**本策略类**只负责「用户访问模式 → 预测驱逐成本 → 给一个 TTL 秒数」这条线。

下面分块讲「人话」。

---

## 1. 直方图（Histogram）到底在记什么？

### 人话

想象你在 **每个 region** 上各放一本账：

- **按「逻辑对象 ID」**：这个对象**上一次**在这个 region 被统计到是什么时候。  
- **按「两次访问间隔」分桶**：间隔越短，落在越小的「时间桶」里；间隔越长，落在越大的桶里（桶的边界是 **指数增长的「小时」刻度**，见代码里 `ranges`，约 2% 步长）。  
- **每个桶里累加的是 GB**：表示「有多少 **GB·次** 的访问量，其**相邻两次访问间隔**落在这个范围里」。

另外有一本 **`last_hist`（Java: `lastHist` / `nextLastHist`）**：和「当前整点到下一次整点之间还剩多少时间窗」有关，用来把「这一轮小时里还没结束的那点量」记进去；和 `hist` 一起在 **`calcEvictCost`** 里算钱（见下节）。

### 代码里怎么更新？（`updatePastRequests` / `update_past_requests`）

仿真器在 **读路径**上（对 tevict_ranges 等）**每处理一条 READ**，调用一次：

`updatePastRequests(request, placeRegion)`  

其中 **`placeRegion` 在仿真里通常等于 `request.issueRegion`**（发起读的那一侧）。

1. **时间窗**（`window_size`）：  
   - `window_size == -1`：**不**按小时「提交」草稿，一直往 **`next_hist` / `next_last_hist`** 上累（Java 里 `calcEvictCost` 直接读 `next_*`）。  
   - `window_size > 0`：隔满 `window_size` 小时，把 **`next_*` 拷到正式的 `hist` / `last_hist`**，再清空 `next_*`；时间跨太大还会整表清空（大间隔重置）。

2. **该 region 的请求计数**：`nextNumRequests[placeRegion] += 1`。

3. **体量 GB**：`sizeGB = getHistogramSizeGB(request, placeRegion)`。  
   - **基类默认** = **整条请求的逻辑大小**（`request.size` → GB）。  
   - **注意**：这里**不是**在策略里按「EC 第 k 片」循环；若 EC 子类**不重写**，直方图用的就是 **逻辑对象体量**（见 `Simulation_Cost_API_Histogram_EC.md`：避免用分片语义污染「用户访问模式」统计）。

4. **同一 `obj_key` 在**同一 `placeRegion`** 第二次及以后访问**：  
   - 算 **`tnext`** = 本次时间 − 上次时间（秒）。  
   - 用 `findNextIndexInRange(tnext)` 得到桶号 `i`，执行：  
     `next_hist[placeRegion][i] += sizeGB`。  
   - 同时维护 `next_last_hist`（当前小时窗内的加减，和「到整点还剩几小时」那类桶有关）——和 Python 里对 `last_hist` 的更新一致。

5. **第一次**在该 region 见到这个 `obj_key`：  
   - 只记时间戳列表、给 `next_last_hist` 的对应窗加 `sizeGB`，**不**给 `next_hist` 的间隔桶加「间隔量」（因为没有「上一次」可减）。

**结论**：直方图是 **「用户在各个 region 上、对各个逻辑对象的重复访问间隔」** 的统计；**一次 READ 调用 = 最多更新一条这样的链**，**不会**在 `PolicyTevictRangesNew` 里对「三个 shard」for 循环各更新三遍。

---

## 2. TTL 怎么算？（hit / miss、遍历、最低价时间）

你记得的 **「遍历 + 找最低成本对应的时间」** 主要发生在 **`calcEvictCost`**，不是简单「hit/miss」两个字能概括；**hit/miss** 是 **仿真器**里缓存命中（有没有本地副本），和这里 **两条短路** 容易混：

### 2.1 第一层：`getTevict(src, dst, timestamp)` 里的 **12 小时缓存**

- 若 **dst** 在 **过去 12 小时内**已经算过一轮，**直接返回**上次给 `(src, dst)` 存下的 TTL（`regionPairsTtl` 里取最后一个）。  
- **这不是**缓存命中意义上的 hit，而是 **「TTL 别每条请求全图重算」** 的性能/稳定性捷径。

### 2.2 第二层：真要算时，**双层遍历「所有 region 对」**

若没走 12h 短路，代码会对 **每一对** `(region → region2)`，`region != region2`：

1. **`net_cost`** = 边上 `region → region2` 的传输单价（`cost`）。  
2. **`storage`** = **目的区 `region2`** 的 `priceStorage`。  
3. **`teven`** = `net_cost / storage * 一天`（秒）——特征时间尺度，表示「存多久存储费和传一次差不多」那种量级。  
4. 调用 **`calcEvictCost(region2, teven/3600, net_cost)`**：  
   - 用 **`region2`** 上的直方图 `X` / `lastX`；  
   - 对 **每一个候选驱逐时间** `tevict = ranges[c]`（小时），算一种「总成本」：  
     - 直方图里每个间隔桶 `i`：若 **典型间隔 ≤ tevict**，多付 **存一段时间** 的钱；若 **> tevict**，相当于 **会先被驱逐再重拉**，多付 **再传一次 `net_cost`**；  
     - 再加上 `last_hist` 里「当前小时窗残留」那一块 **纯存储** 成本。  
   - **在所有 `c` 里选让总成本最小的那个**，返回 **`ranges[ret]`（小时）**——这就是 **「最优桶对应的驱逐时间尺度」**。

5. 再把这个小时数丢进 **`findMin(...)`**，和 **`teven`、样本数、是否冷启动** 等规则合成 **最终 TTL（秒）**。

### 2.3 第三层：`findMin`（不是再算一遍直方图）

- 若 **该区还没有足够请求**（`window_size==-1` 时看 `nextNumRequests`）或 **`calcEvictCost` 返回 -1`** 或 **请求数 < 1000**：  
  - 用 **默认短 TTL**：`teven/2`（换算成小时再乘 3600 变秒）。  
- 否则：用 **`calcEvictCost` 给出的最优小时数 `y`** 作为 TTL。  
- 再按 **dst region** 记一条 **12h 滚动**用的 `seenDays` / `ttls`（和 Python 一致）。

**人话总结**：  
- **「遍历」**：遍历的是 **所有 (src区, dst区) 边** 和 **所有候选 TTL 桶 `ranges[c]`**，**不是**遍历每个 shard。  
- **「最低价」**：在 **`calcEvictCost`** 里对每个候选 `tevict` 算 **存储 vs 再传输** 的期望成本，**取成本最小的那个 `tevict`**（小时），再经 **`findMin`** 变成秒。

---

## 3. 那你问的 EC：**每个 shard 怎么遍历？**

### 实话：**在 `PolicyTevictRangesNew` 里根本不遍历 shard**

- 策略只看见 **`Request`（一个逻辑对象、一个 `obj_key`、一个 `issueRegion`）**。  
- **shard** 是仿真里 **`LogicalObject` 下面挂了多个 `PhysicalObject`**（每个 region 一块），**计费**时：  
  - **每个 region 一次 `pricePut`**、  
  - **传输**按 **每段图上的字节数**（分片大小）、  
  - **读**可能 **多段 graph / 多倍传输量**（拉两片解码等）。  

这些都在 **`SimulatorV2` / `SimulatorV3` + `TransferPolicy`**，**不在** `updatePastRequests` / `getTevict` 里。

### 「shard 继承 object 就和 replicate 一样」——对一半

- **对的一半**：若每个 shard 在代码里就是 **`PhysicalObject`**（有自己的 `size`），那么 **存储费、按边的 transfer、按次的 pricePut** 都是 **同一套记账函数**，只是 **字节数变小了、次数变多了**（例如三 region 写 = 三次 put 费）。  
- **要注意的一半**：  
  - **直方图**：基类仍按 **逻辑请求** 更新（默认 **整对象 GB**），**不会**自动「每个 shard 各加一条间隔统计」——否则同一逻辑读会在统计里被乘上片数，**污染用户访问模式**（详见 `Simulation_Cost_API_Histogram_EC.md`）。  
  - **TTL**：仍然是 **「这条边、这个 dst region」** 的一个数，用来给 **新拉的副本 / 新写的物理块** 贴过期时间；**不是**「每个 shard 各一个 getTevict」在策略里算三遍。

### 「多出来的逻辑」：主动分发、主动拉取恢复

- **分发**：写路径上 **`place` 返回多 region**，仿真器 **多次 `initiate_data_transfer` / 多个 PhysicalObject**——策略的 `place` 在基类里很简单，**EC/MILP 子类**才返回多 region + 分片大小。  
- **恢复**：读路径上 **transfer 策略** 建 **多张 transfer graph**（或分段 size），**拉齐数据 + 解码**是 **仿真语义**，**不是** `PolicyTevictRangesNew` 里再写一套。

**若论文/实现要强调「EC 真实计价」**：应在文档里写清三层分工：

| 层次 | 管什么 |
|------|--------|
| **TevictRangesNew** | 用户级访问间隔直方图 + 边上网费/存储比 → **一个 TTL（秒）** |
| **放置/传输策略 + 仿真器** | 几片、落哪、每段传多少字节、几次 API 费 |
| **MILP（若用）** | 布局 + 系数里把 **API、分片存储、分片传输** 和仿真 **对账**（见 `PolicyMilpECnewV3_Gurobi_Spec.md`） |

---

## 4. 和「hit / miss」别混了

| 概念 | 在哪 | 含义 |
|------|------|------|
| **读命中 / 未命中** | 仿真器主循环 | 本地是否已有副本；决定要不要 **从远端拉**、要不要 **新建 PhysicalObject** |
| **12h TTL 缓存** | `getTevict` | **同 (src,dst) 12 小时内不重算** 全图 MILP 式子 |
| **直方图冷启动** | `findMin` | 请求数 < 1000 等 → 用 **`teven/2`** |

---

## 5. 相关文档

- `docs/Simulation_Cost_API_Histogram_EC.md` — API 费、直方图与 EC 分工  
- `docs/PolicyMilpECnewV3_Gurobi_Spec.md` — MILP 里如何把 **API + 分片** 和仿真对齐  

---

*若实现 EC 子类：直方图是否重写 `getHistogramSizeGB`、TTL 是否按片区分，应单独写清假设；**默认基类**保持「逻辑对象一条链」与源码一致。*
