# SkyStore 扩展：副本—纠删混合冗余与 MILP 最小成本放置 — 中英文摘要 & 例题（组会汇报稿）

> **用途**：论文摘要以 **SkyStore**（`skystore.pdf`）为源工作，交代 **引入 EC** 与 **MILP 最小成本目标** 的改进动机与方法；§7 英文摘要句式对齐 *Fast Variable-Grained Resemblance Data*（参见仓库根目录抽印文本 <code>_pdf_extract.txt</code>）。数值推导见 `docs/MILP_FB_Base_EC_Cost_Walkthrough.md`；**一体积单位制例题（幻灯片源稿）**见 `docs/MILP_FB_Numeric_Example_UnitSystem.md`，可由 pandoc 导出 `docs/MILP_FB_Numeric_Example_UnitSystem.pptx`。

---

## 1. Abstract（English）

With the growing adoption of geo-distributed object caches, **SkyStore** offers a trace-driven framework to evaluate cost-aware replication and TTL policies across regions under realistic cloud pricing. Nevertheless, the baseline design centers on **full-object replicas** and rule-based or heuristic placement, while **erasure coding (EC)**—which can lower stored bytes at the expense of coded shards and cross-region transfers—is not integrated as a joint redundancy choice driven by an explicit **global minimum-cost** objective. Moreover, as the number of regions grows, picking between an optional second replica versus coded stripes becomes combinatorial, so hand-tuned policies may drift away from cost-optimal layouts under heterogeneous storage and egress rates. **Therefore, we propose an extension of SkyStore that introduces hybrid replica–EC redundancy and couples it to a compact mixed-integer linear program (MILP)** whose objective minimizes a linear surrogate of storage plus placement egress: a **fixed-base** mandatory replica at the pinned region is priced over **PUT-to-horizon** duration as a constant term; optional replicas use **TTL snapshots**, while EC places **two outbound data stripes** (`size/K_DATA`) without a separate parity decision variable in the MILP (parity overhead folded into the base replica narrative). Coefficients read regional prices and edge **cost** attributes from the simulator topology; **Gurobi** solves for binary layout decisions on the **write path**. **Finally, we implement the policy in our SkyStore-derived simulator and analyze trace-driven runs together with a worked numeric illustration**; the analysis shows how MILP-guided hybrid redundancy reconciles replica and EC under the minimum-cost objective when TTL and prices shift the optimum between **base + second replica** and **base + two shards**.

**Keywords:** SkyStore; geo-distributed caching; erasure coding; mixed-integer linear programming; minimum-cost placement; fixed-base replication; TTL approximation.

---

## 2. 摘要（中文）

随着跨区域对象缓存与成本感知仿真的普及，**SkyStore**（见 `skystore.pdf`）为在多区域云计价模型下评估副本与 TTL 策略提供了可追溯驱动的框架。**然而**，既有主线仍以 **整对象副本**及启发式或规则化放置为主，**纠删码（EC）**尚未作为可与副本并列、并由 **显式全局最小成本目标**统一驱动的冗余形态引入。**此外**，区域数目增加后，「是否启用 EC、条纹落于何地」与「是否增设第二整副本」的组合爆炸使单纯手工调参难以逼近成本最优布局。**因此，本文在 SkyStore 框架之上提出扩展：引入副本—纠删混合冗余，并将其决策嵌入以小规模混合整数线性规划（MILP）表述的最小成本目标中**。在 **固定基地（FB）** 语义下，base 区强制整副本按 **首次 PUT 至记账终点** 的全长 horizon 计入 **base 区从 put 到 end 的存储费用**（与 **issue→base 边费** 一并写入 **`fbBaseReplicaObjectiveConstant`**）；非 base 副本系数结合 **TTL 快照近似** 与 **边费 × 整对象体积**；EC 分支仅对 **两条外传数据条纹**（`size/K_DATA`）建变量，**不在 MILP 中单列第三条 parity 条纹**（parity 成本叙事上并入 base）。系数由拓扑节点存储价与边 **`cost`** 导出，模型交由 **Gurobi** 求解并接入 **写路径** 刷新。**最后**，我们在 SkyStore 派生的离散事件仿真器中实现该策略，并结合 trace 实验与 **一体积单位制**手算例题（不做 KiB/GB、月/天换算）展示：在 TTL 与单价变化时，最优安排可在「base+第二副本」与「base+两片 shard」之间翻转，体现 MILP 最小成本判别机制。

**关键词：** SkyStore；跨地域缓存；纠删码；混合整数线性规划；最小成本放置；固定基地副本；TTL 近似。

---

## 3. 汇报时可强调的三句话（对应代码）

> **预览说明**：本节公式只用 `$...$`（行内）与 `$$...$$`（独行块）。请勿使用 `\(...\)`：Cursor / VS Code 内置 Markdown 数学预览通常不认该定界符。

### 句 1：Base（FB）

整对象时长 = `putInstant → milpDatasetEndEpochSec`（或配置 horizon），系数进 **`fbBaseReplicaObjectiveConstant`**，**`x[base]` 固定为 0**。

### 句 2：副本 vs EC（MILP 目标）

目标为 Gurobi `MINIMIZE`；记号与论文一致（例如 $\mathrm{SC}$、$\sum_r$ 等）。

**符号**：$t_{\mathrm{put}}$、$t_{\mathrm{end}}$ 对应 `putInstant` 与记账终点 epoch（或等价 horizon）；$S_o$ 为对象体积（GB，`StorageCostHelper` 口径）；$r_{\mathrm{base}}$ 为 base；$\mathrm{iss}$ 为本次快照的 **`issueRegion`**；$c(\mathrm{iss}, r_{\mathrm{base}})$ 来自图上边 **`cost`**（GB 计价下的 placement egress 单价）。

**base 区「put→end」存储费**记为 $\mathrm{SC}(r_{\mathrm{base}}, S_o, \tau)$（对应 `StorageCostHelper.storageCostGb(S_o, baseRg, horizonSec, ...)`）。先定义 horizon（单位：秒）：

$$
\tau := t_{\mathrm{end}} - t_{\mathrm{put}}
$$

与 **`issueRegion→base`** 边费合并后、**不进入** $x_r$ 的加性部分：

$$
f_b = \mathrm{SC}(r_{\mathrm{base}}, S_o, \tau) + c(\mathrm{iss}, r_{\mathrm{base}})\, S_o .
$$

若未启用 FB，则 $f_b = 0$。全体区域上求和（下文 $\sum_r$ 表示对所有区域 $r$）：

$$
\min \quad f_b + \sum_{r} s^{\mathrm{rep}}_{r}\, x_{r} + \sum_{r} \sum_{k=1}^{2} s^{\mathrm{ec}}_{r,k}\, y_{r,k}.
$$

**口头归纳**：在 $f_b$（$\mathrm{SC}$ + iss→base 边费）之上，再加上每个区域 $r$ 的整副本线性项 $s^{\mathrm{rep}}_{r} x_{r}$，以及两条 EC 条纹槽 $k \in \{1,2\}$ 的线性项 $s^{\mathrm{ec}}_{r,k} y_{r,k}$。

**代码**：$f_b$ 即 **`fbBaseReplicaObjectiveConstant`**（`buildCoefficients`）；$x_r$、$y_{r,k}$、$z$ 与 $s^{\mathrm{rep}}$、$s^{\mathrm{ec}}$ 见 **`GurobiEc21MilpBuilder`**。

**约束（摘录）**：$\sum_{r \neq \mathrm{base}} x_{r} \leq 1$；对每个 stripe 槽 $k$，$\sum_{r} y_{r,k} = z$；对每个区域 $r$，$\sum_{k} y_{r,k} \leq 1$（两片 stripe 不同区）。

### 句 3：实现链路

`PolicyMilpECnewV3.buildCoefficients` 构造 `sRep` / `sEc` / `fbConst`；`GurobiEc21MilpBuilder` 建模求解；边费来自拓扑 **`cost`**（**自环 cost = 0** 时同区无跨区边费）。

---

## 4. 数值例题（一体积单位制）

正文已拆至独立文稿：**`docs/MILP_FB_Numeric_Example_UnitSystem.md`**（可用 pandoc 生成 **`docs/MILP_FB_Numeric_Example_UnitSystem.pptx`**）。完整推导与术语见 **`docs/MILP_FB_Base_EC_Cost_Walkthrough.md`**。

---

## 5. 论文写法提示（可选）

- **Baseline**：明确全文建立在 **SkyStore**（`skystore.pdf`）仿真框架之上；Related work 中交代其与后续 geo-cost / TTL 类工作的关系。  
- **Contribution**：**引入 EC 冗余分支** + **MILP 最小成本目标**做放置决策（与源框架启发式的差异要写清）。  
- **Method**：FB 语义、系数构造（horizon vs TTL）、**2 stripe** MILP、写路径触发、与 `RegionManager` 记账衔接。  
- **Evaluation**：真实 trace、图上 `priceStorage` / `cost`；对比 SkyStore 既有策略或 replica-only 基线 + MILP-EC 的建模成本与求解开销。  

---

## 6. 参考文献占位

（至少收录：**SkyStore** 源论文 PDF：`skystore.pdf`；云区域计费与拓扑数据；**Gurobi** / MILP 相关文献；若对比 EC 放置启发式可补充相应引用。）

---

## 7. 论文标题与 IEEE 体例摘要（仿 *Fast Variable-Grained Resemblance Data* 行文结构）

句式模板对齐参考文首页（仓库内抽印文本 <code>_pdf_extract.txt</code>）：**行业/先行工作背景 → Nevertheless / However 指出现状不足 → Moreover 递进 → Therefore, we propose … → Finally, we implement … / The results show that …**；技术面落脚为你的工作：**在 SkyStore（`skystore.pdf`）上扩展 EC + MILP 最小成本决策**。

### 中文标题

**面向云存储的副本--纠删混合冗余：基于混合整数线性规划的多区域成本最优放置与迁出决策**

### Abstract—

Abstract—With the prevalence of geo-distributed object caching research, **SkyStore** has been proposed as a trace-driven framework to evaluate cost-aware replication and TTL management across cloud regions. Nevertheless, its baseline redundancy model emphasizes **full-object replicas** driven by heuristic or policy rules, while **erasure coding (EC)**—a classical knob for trading stored bytes against coded shards and cross-region transfers—is **not** wired into placement as a first-class alternative optimized under a **single minimum-cost** surrogate. Currently, operators still struggle to decide, region by region, whether to add another replica or to stripe coded data when egress and storage prices differ. Moreover, enumerating all feasible replica–EC layouts scales poorly as the region count grows, which deteriorates the practicality of purely manual tuning. **Therefore, we propose an extension built on SkyStore that introduces hybrid replica–EC redundancy and encodes placement as a compact mixed-integer linear program (MILP)** minimizing linearized storage plus placement egress: under **fixed-base (FB)** semantics, the mandatory base replica is priced over **PUT-to-horizon** duration via an objective constant; non-base replicas absorb **TTL snapshots**, while EC activates **two outbound data stripes** (`size/K_DATA`) without introducing a third parity stripe variable in the MILP (parity overhead is folded into the base replica narrative). Edge coefficients come from the simulator’s regional **cost** graph; **Gurobi** solves binary decisions integrated into the **write path**. **Finally, we implement the MILP-guided policy atop our SkyStore-derived simulator and conduct trace-driven experiments together with a worked numeric example. The results show that** the minimum-cost objective systematically selects between **base + optional second replica** and **base + two shards** as TTL and pricing regimes shift, demonstrating when hybrid EC lowers modeled total cost versus replica-centric baselines.

**Index Terms** — SkyStore, geo-distributed caching, erasure coding, hybrid redundancy, mixed-integer linear programming, minimum-cost placement, cloud egress, fixed-base policy

### 摘要—

摘要—随着跨地域对象缓存与成本评估研究的兴起，**SkyStore**（`skystore.pdf`）已被用于在真实 trace 与多云计价下评估副本及 TTL 策略。**然而**，其既有冗余主线仍以 **整副本**为主，**纠删码（EC）**尚未作为可与副本并列、并由 **统一最小成本目标**最优选择的扩展纳入框架。**目前**，各区域存储与出口单价不一，运维侧仍缺乏系统化手段在「增设第二副本」与「启用 EC、条纹跨区放置」之间做出全局折中。**此外**，可行放置组合随区域数增长而膨胀，纯启发式调参会削弱成本最优性。**因此，本文在 SkyStore 之上提出改进：引入副本—纠删混合冗余，并以混合整数线性规划（MILP）的最小成本目标联合刻画放置决策及与之对应的迁出侧计费近似**。在 **固定基地（FB）** 约束下，base 强制整副本按 **PUT 至记账终点** horizon 计入常数项；可选副本使用 **TTL 快照** 近似保留时长并结合 **边费 × 对象体积**；EC 分支仅建模 **两条外传数据条纹**（`size/K_DATA`），**不在 MILP 中单列第三条 parity 变量**。系数源于拓扑存储价与边 **`cost`**，由 **Gurobi** 求解并嵌入仿真 **写路径**。**最后，我们在 SkyStore 派生的离散事件平台上实现原型并开展 trace 实验，辅以一体积单位制手算比较例题（不做 KiB/GB、月/天换算）；结果表明**，当 TTL 与单价改变时，MILP 能在「base+第二副本」与「base+两片 shard」之间自动切换最优布局，从而在最小成本意义下体现 EC 相对纯副本基线的收益区间。

**关键词** — SkyStore；跨地域缓存；纠删码；混合冗余；混合整数线性规划；最小成本放置；云出口；固定基地策略
