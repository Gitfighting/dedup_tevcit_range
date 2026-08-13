# MILP-EC 三版本（V1 / V2 / V3）说明（先结论、后细节）

---

## 1. 人话结论（先看这段）

下面这些是你做科研时**最需要先搞清楚的**，不用背术语。

1. **三个版本都是在用 MILP 帮每个区域决定：多用副本还是多用 EC 分片、分片放哪，但「EC 长什么样」不一样。**  
   - V1：**3 片**（2 数据 + 1 校验，和常见 RS(2,1) 一致）。  
   - V2：**只有 2 片**（没有单独校验片），而且第二片位置有一部分是**按最便宜网线贪心**定的，不全是求解器输出。  
   - V3：**3 片**，外加你设计的 **FB（固定基底整副本）** 和 **读缺失之后还要把分片「分发」到别的区**——所以**跨区传输往往比 V1/V2 多**，总账更容易上去，**不是因为仿真少算钱**。

2. **仿真器最后算多少钱，是看「实际发生了多少次存、传、请求」；MILP 只是帮策略做决策。**  
   V1/V2 的 MILP 里**没有**把云厂商的 **PUT/GET 按次费**写进目标函数；V3 的 MILP 里**有**。  
   所以：**V2 比 V3 便宜，主要常常是「干的活少」（尤其少读路径分发），不是 V2「漏算价格」。**

3. **直方图 + TTL：**  
   - V1：直方图和 TevictRanges **共用父类**那一套；算出来的 TTL 用来喂给 MILP 里的「时间尺度」。  
   - V2：自己抄了一套**和 TevictRanges 同算法的**直方图/TTL，**不继承**父类。  
   - V3：也是**独立一份**直方图/TTL，但和 MILP 系数绑得更紧（你还做了 TTL 快照、和触发时机对齐）。

4. **MILP 什么时候跑：**  
   - V1/V2：**按时间窗口 + 事件数**（例如攒够一定量写/读、或窗口满）**批量**给多个 region 各解一次。  
   - V3：**按 region 节流**（例如一小时一桶），读处理完后触发；读缺失时还可能**当场补一次**，保证有决策可用。

5. **读缺失之后会不会「再传一波分片」：**  
   - V1、V2：**不会**在 CheapestTransfer 里专门加「分发分片」的额外路径。  
   - V3：**会**（`CheapestTransferV3` + `SimulatorV3`），所以**传输费**这一项经常明显变大。

6. **实验上别只盯总美元：**  
   有的 trace（例如 T29）**请求次数计费**占比很大，总成本看起来差不多；有的 trace（例如 T65 小区域）**传输**会爆。  
   对比策略请拆开看 **`total storage / transfer / request`**，见仓库里的 `analysis_simulation_results.md` 和 `PolicyMilpEC_V1_V2_V3_Comparison.md`。

---

## 2. 一张表：三版本差在哪

| 你关心的点 | V1 `milp_ec_new` | V2 `milp_ec_new_v2` | V3 `milp_ec_new_v3` |
|------------|------------------|---------------------|---------------------|
| 代码和 TevictRanges | **继承**父类，直方图走 `super` | **不继承**，自己实现同款直方图/TTL | **不继承**，独立拷贝 |
| 几片 EC | **3** | **2** | **3** |
| 分片放哪 | 基本由 MILP 选 | 一片本地 + **第二片贪心最便宜边** | MILP + **FB 取消基底上的分片迁移** 等执行规则 |
| 读 miss 后是否再分发分片 | **否** | **否** | **是**（主要差异） |
| MILP 里有没有 PUT/GET 按次费 | **无** | **无** | **有**（缺图属性时有 fallback） |
| MILP 大概啥时候跑 | 窗口满 / 事件够 / 区域太多 | 同左 | 按 region 时间桶 + 读应用后；miss 可现解 |

---

## 3. 各版本在代码里「干的一件事」（各三行）

**V1**  
把每个区域的写流量、以及「按当前决策算出来需要远程拉」的读流量，攒成一个窗口；窗口到了就给**活跃区域**各解一次 MILP；写的时候若决定 EC，就**一次写满 3 个分片区**。直方图 TTL 和 TevictRanges **同一条链路**。仿真计费仍是 Simulator 按真实 IO 算。

**V2**  
思路和 V1 很像（也是窗口 + 聚合 + 每 region 一次 MILP），但 EC 只有 **2 片**，且第二片**贪心**。TTL/直方图写在 V2 自己类里。同样**没有**读路径上的「分片再分发」那一段，所以**跨区传输通常比 V3 少**。MILP 目标里**没有** PUT/GET API 项。

**V3**  
你研究的重点：直方图/TTL 与 MILP 系数（含存储、边费、**PUT/GET**）对齐；MILP 结果进 `decisionCache`；读完成后节流触发；读缺失时 `place` 给出 issue + 分片目标区，**再追加传输图做分片落地**。所以**物理行为和账单**和你设计的 FB+GET 语义一致，但**总成本**要和 V1/V2 比时，必须说明**做的事不一样**。

---

## 4. 实验和日志：去哪看、看什么

**汇总数字（表格）**  

- PF 下 V1 vs TevictRanges：`analysis_simulation_results.md`  
- 带 `policy`、`timestamp` 的 CSV：`java_result/All_Results.csv`；整理好的对比：`docs/PolicyMilpEC_V1_V2_V3_Comparison.md`  

**单次跑出来的 JSON**（分项最清楚）  

- 看 `total storage cost`、`total transfer cost`、`total request cost`，不要只看一行 `total cost`。  
- 例：V2 T29 `simulation_results_milp_ec_new_v2_fb_9reg_T29_pycompat_*.json`（请求费占比可以很大）。  
- 例：V3 T79 `simulation_results_milp_ec_new_v3_java_fb_9reg_T79_slotaware2_*.json`（transfer、storage 都高时要对照基线同配置）。

**日志关键字（确认「求解了没有、EC 还是副本」）**  

- V1：`[MILP-ECnew]`，`EC  ttl=` / `REP ttl=`，`FINAL SUMMARY`  
- V2：`[MILP-ECnewV2]`，`EC(2+0)` / `REP`  
- V3：`[READ-DIST-V3]`、`[READ-PLACE-V3]`、strict-fail；大日志可能是 UTF-16，用编辑器或脚本时注意编码  

---

## 5. 需要写进论文的「一句严谨话」

若论文里写「MILP 优化总成本」，建议改成：**「MILP 优化的是代码中定义的代理目标；仿真总成本由 Simulator 按存储、传输与请求分项统计。」**  
V1/V2 与 V3 的**代理目标不完全同构**（尤其 API 费与读路径分发），**横向比数字时要说明可比范围**。

---

## 6. 细节补充（可选读）

以下内容给**要对照源码**时用；若上面几节已够用，可跳过。

### 6.1 V1 补充

- `updatePastRequests`：**先** `super.updatePastRequests`（与 TevictRanges 共享直方图）。  
- `solveWindowBatch` 里 `tauH[i] = calcEvictCost(...)` → 进 MILP 约束 `hatTau >= tauH[i]*sigma[i]`。  
- 读流量进窗口条件：`RegionDecision` 认为当前 issue 区在 EC 下**本地没有分片**时才累计 `readSizeGB`（不是全量 read）。  
- 触发：`windowSizeHours`、或 `windowStats` 区域数 > `2*n`、或 `windowEventCount >= 500`。  
- `getTevict`：EC 时用 `milpEcTtlByRegion` 里 **hatTau（键为 src）**；`src==dst` 有 teven/2 短路；否则父类 region 对 TTL。

### 6.2 V2 补充

- 直方图/TTL：类内 `calcEvictCost` / `findMin` / `getReplicaTevict`，与 TevictRanges **算法同构**、**不继承**。  
- EC(2,0)：`d=1` 时 **强设**本区一片 + `pickMinCostRegionForEcShard` 选另一片。  
- `getTevict`：优先 `milpEcTtlByRegion.get(**dst**)`；EC 片 TTL 注释为与 **local tauH** 一致（与 V1 用 hatTau 写入的方式不同）。  
- `place(read)`：只返回 `issueRegion`，**无** V3 式分片分发图。

### 6.3 V3 补充

- 详见 `docs/PolicyMilpECnewV3_CodeDesign.md`、`docs/PolicyMilpECnewV3_Design.md`。  
- 执行链：读完成 → `onReadApplied` → `runMilpForIssueRegionIfDue`；读 miss → `place` → `CheapestTransferV3.maybeAppendShardDistributions` → `SimulatorV3` 定每段对象大小。

---

## 7. 源码文件索引

| 版本 | 主类 |
|------|------|
| V1 | `src/main/java/org/example/src/placement_policy/PolicyMilpECnew.java` |
| V2 | `src/main/java/org/example/src/placement_policy/PolicyMilpECnewV2.java` |
| V3 | `src/main/java/org/example/src/placement_policy/PolicyMilpECnewV3.java` |
| 仿真注册 / TTL 调用 | `src/main/java/org/example/SimulatorV2.java` |
| V3 读路径分发 | `src/main/java/org/example/src/transfer_policy/CheapestTransferV3.java`、`SimulatorV3.java` |

---

## 8. Simulator 里 EC 怎么计费？（放置、传输、存储时长——V1 你最关心的部分）

下面说明：MILP 选了 EC 且给出了多个放置区域之后，**账单在代码里是按什么量算的**。主干在 `SimulatorV2`（`initiate_data_transfer`、读写循环、`_updateTransferMetric`）。

**重要前提**：`Main.java` 里 **`milp_ec_new`、`milp_ec_new_v2`、`milp_ec_new_v3` 都会构造 `SimulatorV3`**，因此写时各区的对象大小会走 **`SimulatorV3.getPlacementSizesForWrite` → `placementPolicy.getPlacementWithSizes`**（V1/V2/V3 各自返回；EC 时一般为每区 **`size/2` 一片**）。

### 8.0 结论先行（你问的那两句话，到底什么意思？）

下面三句是**最终判断**，读完再往下看过程即可。

1. **「写的时候要拉 shard」在当前 Java 里 = 整笔写都不计费？**  
   **不是。**  
   - **有计费**：模型里对 **每个目标区** 画了一条 **`issueRegion → dst`**，传输费按 **`placementSizes` 里的分片大小**（`sizeOverride`）算，**写 EC 的「往外发分片」是计费的**。  
   - **没计费的是另一种故事**：若真实流程是 **「先从第三方区 A 把数据拉到 issue，再从 issue 发到 dst」**，代码里**没有**单独再画 **A → issue** 这张图，那 **「先拉 A」那一段传输就不会出现在 `transfer cost` 里**。  
   → 结论：**不是「写侧 EC 没计费」**，而是 **「多跳里没建图的那一跳不计费」**。

2. **「读图用 `req.size`、`getReadTransferSize` 用不上 = EC 读也没按 EC 算、等于没算 EC？」**  
   **也不等于「EC 完全没算」。**  
   - **存储**：多个区、多个 **`PhysicalObject`，每个是分片大小**——**EC 存储是按分片算的**。  
   - **读传输**：默认 **`CheapestTransferV2` 单张图**上 **`segmentSizes` 往往等于整对象**，于是 **`_updateTransferMetric` 按整对象字节计费**；**`getReadTransferSize`（`size/2`）在这种单图情况下经常轮不到**。  
   → 结论：**EC「落地存盘」是按分片的；读「这一笔跨区传输」在 V1/V2 默认路径上常常按「整对象流量」计费**，和「理论上只拉 1 片」**可能不一致**。这不是「全没算」，是 **读传输字节数口径与纯 EC 拉片语义可能对不齐**。

3. **和 V3 对比**  
   V3 用 **`CheapestTransferV3`**，可以 **多段图 + 分片 `segmentSizes` + 分发段**，**更容易**在账单上体现「分片级」传输；所以你会觉得 V3「写清楚了」，同时 **transfer/storage 变高**——因为 **多做了段、多存了片**。

### 8.1 三条总规则（人话）

1. **存储费**：每个 **`PhysicalObject`** 在 **`RegionManager`** 里按 **`objSize` × 从 `storageStartTime` 到删除/仿真结束的时间 × 该区存储单价** 累计。对象是在 **`initiate_data_transfer`** 落地时创建的（`new PhysicalObject(..., objSize, ttl, ...)`）。  
2. **传输费**：每条传输图走 **`_updateTransferMetric(transferGraph, request, sizeOverride)`**。计费用的字节数：**若传入 `sizeOverride` 则用 override，否则用整笔请求的 `request.size`**。费用与边上 **`cost`**、以及「多少 GB」的换算挂钩（与 Python artifact 对齐的实现）。  
3. **请求费（按次 PUT 等）**：写路径结束会 **`applyPythonArtifactRequestCosts(place_regions, ...)`**，大致是 **每个放置区一次 put 类计费**（与 EC/副本模式无关，但 **区越多越容易累加**）。

### 8.2 写（PUT）：已经知道三个（或 V2 两个）放置区时，Simulator 怎么做？

1. **`place` + `getPlacementWithSizes`**：得到 `place_regions` 和 **`Map<region, 该片大小>`**（EC 时多为 **`req.size/2`** 每区一份）。  
2. 对 **每个目标区** 调 **`writeTransferPath(req, region)`**（`CheapestTransferV2`）：图上是 **issueRegion → dst**，边上带的 `size` 字段仍是 **`req.size`（整对象）**。  
3. **真正算传输钱**时用的是 **`_updateTransferMetric(writeGraph, req, transferSize)`**，其中 **`transferSize` 来自 `placementSizes.get(place_regions.get(wi))`**——也就是 **分片大小**。所以：**三条写边 = 三次传输计费，每次按「一片」的字节算**，和「往三个区各放一片」一致。  
4. **落地存盘**：同一函数里 **`PhysicalObject` 的 `objSize`** 用 **`placementSizes`（经 `chooseObjectSizeForPlacement`，V2 默认即 fallback）**，所以 **存储账也是三（二）个小对象**，不是三个整副本。

**一句话**：**写的传输费 = 按分片大小 × 边费，重复次数 = 放置区个数；存储费 = 各区分片对象 × 各自存活时间。**

### 8.3 「写的时候还要从别处拉 shard」在当前代码里怎么体现？

（**结论已写在 §8.0 第 1 条**：**issue→dst 有计费**；**未建图的「先 A→issue」不计费**。）

**`CheapestTransferV2.writeTransferPath` 只有一跳：`issueRegion → dst`。** 没有「先从第三方 region 拉到 issue，再转发到 dst」的第二张图。  
若业务语义是「数据先在 A，写到 B 时要先拉再存」，而 trace 仍标成在 issue 发起写，**仿真里默认只计这一跳 issue→dst**；多跳若未建图则 **不会单独多记一段传输**。写论文时要说明 **模型是单跳写分发**。

### 8.4 读（GET）：为什么你觉得 V1「不清楚」，而 V3 写清楚之后费用反而高？

**V1 / V2**（配置 `transfer_policy: cheapest` 且 **不是** V3）用的是 **`CheapestTransferV2`**：

- 一般 **只有一张读图**：`src → issueRegion`，边上 **`size` = `req.size`（整对象）**。  
- `ReadPathResult` 的 **`segmentSizes`** 从 **图边 `size`** 抄出来；读循环里 **`_updateTransferMetric` 优先用这个 segmentSize**。  
- **`SimulatorV3.getReadTransferSize`** 虽可在 EC 布局下返回 **`size/2`**，但 **单图时往往用不上**（因为 segment 列表里已经有来自边的整对象大小）。  
- **因此**（**结论见 §8.0 第 2 条**）：磁盘上虽是 **多区分片**，**读传输计费在默认 V2 路径上常常是「整对象拉一次」的量级**——**存储按 EC 分片，读传输字节常按整对象**；和「EC 只拉两片数据」的直觉可能不一致。**写论文或和 Python 对账时务必以本仓库代码为准**。

**V3** 用 **`CheapestTransferV3`**：

- 读 miss 可有多段 **重建拉取**（`segmentSizes` 与多图对齐）；  
- 再 **`maybeAppendShardDistributions`**：追加 **issue → 各 MILP 目标区** 的 **`ec_distribution`**，**每段边上分片大小**，并在 **`SimulatorV3.chooseObjectSizeForPlacement`** 里 **issue 落整副本、目标区落分片**。  

所以：**V3 = 显式「多读几段网 + 多几个区存分片」**；**传输和存储分项上去，是行为变完整、变频繁的结果**，不是「V1 少算、V3 多算」这种简单关系。你要降本，需要在 **策略层少分发、加冷却或收益门槛**（你已在日志里看到大量 `[READ-DIST-V3]` 即属于这类成本来源）。

### 8.5 存储「时长」从哪来？

新对象的 **TTL** 在 **`calculateTtl` → `placementPolicy.getTevict(...)`** 等链路里定（含 TevictRanges / MILP 相关策略）。**`RegionManager`** 在对象生命周期结束时把 **占用时间 × 单价** 计入 storage。**EC 多片** = **多个 `PhysicalObject`**，**各自 TTL、各自计费**；驱逐、联动删片见各策略的 **`canEvict` / `getLinkedEvictionObjects`**（V2/V3 有定制）。

### 8.6 和日志、JSON 怎么对？

- 写：`[WRITE-NET]`（若开详细日志）对 `_updateTransferMetric` 的 cost；对照 **`placementSizes` 是否非空**、各 `wi` 的 `transferSize`。  
- 读：`[READ-NET]`；V3 另搜 **`[READ-DIST-V3]` / `[READ-PLACE-V3]`**。  
- 结果文件：看 **`total transfer cost`、`total storage cost`、`total request cost` 分项**，不要只盯 `total cost`。

### 8.7 关键代码锚点（便于跳转）

写路径用 `placementSizes` 作为传输计费字节、并创建 `PhysicalObject`：

```1161:1166:src/main/java/org/example/SimulatorV2.java
                            for (int wi = 0; wi < relevantWriteGraphs.size(); wi++) {
                                org.example.graph.DiGraph writeGraph = relevantWriteGraphs.get(wi);
                                BigDecimal transferSize = (placementSizes != null && wi < place_regions.size()
                                        && placementSizes.containsKey(place_regions.get(wi)))
                                        ? placementSizes.get(place_regions.get(wi)) : null;
                                TransferMetrics metrics = _updateTransferMetric(writeGraph, req, transferSize);
```

`sizeOverride` 如何进入 `effectiveSize`：

```1381:1388:src/main/java/org/example/SimulatorV2.java
    private TransferMetrics _updateTransferMetric(org.example.graph.DiGraph transferGraph,
            org.example.src.model.Request request, BigDecimal sizeOverride) {
        List<org.example.graph.DiGraph.Edge> edges = transferGraph.edgesWithData();
        if (edges.isEmpty()) {
            return new TransferMetrics(0, 0, 0, 0);
        }
        BigDecimal effectiveSize = (sizeOverride != null) ? sizeOverride : request.size;
```

`milp_ec_new*` 使用 `SimulatorV3`（从而启用 `getPlacementWithSizes` 写路径）：

```73:76:src/main/java/org/example/Main.java
            SimulatorV2 simulator = ("milp_ec_new".equals(String.valueOf(loadedConfig.placementPolicy))
                    || "milp_ec_new_v2".equals(String.valueOf(loadedConfig.placementPolicy))
                    || "milp_ec_new_v3".equals(String.valueOf(loadedConfig.placementPolicy)))//注册milpecneweV3策略，返回SimulatorV3对象
                    ? new SimulatorV3(
```

---

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-03-28 | 初版 |
| 2026-03-28 | **重写**：开篇「人话结论」；压缩表格与分节；细节移至第 6 节可选读；实验/日志单独一节 |
| 2026-03-28 | **新增 §8–§8.7**：Simulator 中 EC 写/读计费链；V1/V2 读侧默认按边 `size` 与 `getReadTransferSize` 关系；V3 多段导致费用升高原因；关键代码锚点 |
| 2026-03-28 | **新增 §8.0**：结论先行——澄清「写拉 shard 是否不计费」「读是否没算 EC」两种误解 |
