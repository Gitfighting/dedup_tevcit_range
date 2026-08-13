# FB 模式下「base」含义与副本 / EC 计价演算（示例）

本文用**同一颗对象**走一遍「基地（base） + 可选第二副本」与「**base + 两片等大 shard**」的成本叙事：逻辑上可在 base 对整副本做 RS **2+1** 编码，但 **MILP 与仿真放置只对「两条外传 shard」列存储与边费**（**不建第三条 parity 条纹变量**；单片仍用同一 **`size_shard = size_full / K_DATA`**）。数值单价量级可与 **`target/profiles/`**（`gcp_storage_pricing.csv`、`storage.csv`、`tevencost.csv`、`cost.csv`）对照；**手算例题不引入 KiB↔GB、月↔天等换算**，一律 **「一体积单位」对应 trace/MILP 模板里的一个计数单位**，按下面约定逐项相乘即可。

### 术语：`issue_region`（本文写法）

- **在本教程的正文与公式里**：**`issue_region` 指「发起 PUT 的区域」**，也就是对象首次写入 trace 时、写入发起侧所在的区域（上文记为 **`a`**）。这与 FB 叙事一致：**仿真里首次写满足条件时，`assignBaseRegion(req.issueRegion)` 把「这一条 PUT 行的 `issue_region`」记成该对象的 `baseRegion`**。
- **与 CSV 每一行的关系**：仿真对**每一条**请求（含读）都会读入列 **`issue_region`** 到 `Request.issueRegion`。因此 **读请求行上的列值**，在数据集中通常是 **该读请求的 issue / 发起区域**，**不一定**等于「当初 PUT 的区域 **`a`**」。本文后面的数值例若要用 **`a → b`** 的边费，相当于假设 **触发 MILP 的那一行**上 **`issue_region` 恰好等于 `a`**（例如 MILP 恰在 PUT 所在区发起的请求上触发，或 trace 约定读写共用同一 issue 语义）。
- **实现细节与 Python 溯源**：边费系数里用的「源区」取自 **当前这条 trace 行的 `issue_region`**，见 §6；**读路径上是否会刷新 TTL / 触发 MILP**，见文末附录（对照 **`simulator_v2.py`**）。

---

## 1. Base（基底区）在 FB 模式里指什么？

**设定（Fixed-base / FB）**

- 对象 **`AA`** 在某次 **PUT** 写入 trace，写入发起区为 **`a`**。
- 启用 FB 后，**`a` 被固定为该逻辑对象的 `baseRegion`**（仿真里在首次写且 `setBase` 等条件满足时赋值）。
- **语义**：在计价故事里，`AA` 在 **`a` 区始终保持一份「整对象」副本**，生命周期从 **首次 PUT 时刻 `t_put`** 一直计到 **仿真记账终点 `t_end`**（与 `SimulatorV2` 一致：通常为 **trace 最后一条请求时间 + 50s**，用于 `RegionManager` 尾部结算）。

这与「第二副本在哪、是否 EC」**无关**：base 这份整对象是 **单独一块、按全长存贮时长** 计费（除非你在线上真实删掉 base——本仿真假设 FB 下 base 常驻到 `t_end`）。

---

## 2. 共用时间与体量（例子）

| 量 | 取值 | 说明 |
|----|------|------|
| 对象 | `AA` | |
| 体积（计数） | **\(S=9\)** | **9 个体积单位**，与仿真/MILP 模板一致即可；**本节不做 KiB、GB 换算** |
| PUT 区 | **`a`** | base |
| base 持有天数 | **30** | 从 `t_put` 到 `t_end` |
| （示例）`b` 区 TTL | **7** 天 | 非 base 副本保留近似 |
| （示例）`c`/`d` 区 TTL | **5** / **6** 天 | 两片 shard |

**计价约定（同一套「单位」从头到尾）**

- **存储**：**\(\rho_r\)** = **USD/(单位·天)**，持有 **\(T\)** 天 → **\(S_{\mathrm{vol}}\,\rho_r\,T\)**（整对象用 **\(S\)**，shard 用 **\(S/2\)**）。  
- **运费（placement egress）**：**\(\gamma\)** = **USD/单位**（一次传出多少单位就乘多少）→ **\(\gamma\,S_{\mathrm{vol}}\)**。

下文数值取 **\(\rho_a=0.020\)**，**\(\rho_b=\rho_c=0.023\)**，**\(\rho_d=0.024\)**；**\(\gamma_{a\to b}=0.056\)**，**\(\gamma_{a\to c}=0.056\)**，**\(\gamma_{a\to d}=0.052\)**（与画像里「存储价小数点后两位、跨区费更高一档」的相对关系一致即可）。

---

## 3. Base 区存贮费（整对象、全长）

```text
Cost_base_stor_FB = S × ρ_a × D_base_days
```

**数值例**：**\(S=9\)**，**\(\rho_a=0.020\)** USD/(单位·天)，**\(D_{\mathrm{base}}=30\)** 天：

```text
Cost_base_stor = 9 × 0.020 × 30 = 5.40 USD
```

**要点**：这一项 **不使用** 配对 TTL 直方图给出的 `dst_TTL`；TTL 主要用于 **非 base 的缓存副本或分片** 在模型里「预期保留多久」的近似。

---

## 4. 分支 A：再决定「第二个整副本」落在哪个区？

FB 下叙事常为：**base(`a`) 已定价** → MILP 再选 **至多一个「额外整副本」区** `r`（`r ≠ a`）（与当前 `GurobiEc21MilpBuilder` 里 `at_most_one_extra_full_replica` 一致）。

对候选区 **`b`**：

**存贮（按该区 TTL 窗口近似）**

在你写法里：

```text
stor_b = S × ρ_b × T_TTL_b
xfer_issue_to_b = γ_issue_to_b × S
```

- **`T_TTL_b`**：本例 **7** 天；**`S`**：整副本 **9** 单位。

**实现对照**：边费起点 **`src`** 在 Java 里取自 **当前 trace 行的 `issue_region`** → `edgeCostRequired(issueRegion, dst)`，不一定是叙事里的 **`a`**，除非触发行满足 **`issue_region == a`**。

若你的业务叙事是「数据一定从 **base `a`** 拷到第二副本 **`b`**」，应在模型里显式用 **`a` 作为 `src`**（或保证 **触发 MILP 的那一行**上 **`issue_region == a`**）。本文已将 **`a` 定义为 PUT 发起区**；读行上的 CSV 列未必仍是 **`a`**，见文首术语说明与附录。

**数值例**（设 **`issue_region = a`**）

- **\(\rho_b = 0.023\)** USD/(单位·天)，**\(T_{\mathrm{TTL},b}=7\)** 天  
- **\(\gamma_{a\to b} = 0.056\)** USD/单位  

```text
stor_b = 9 × 0.023 × 7 = 1.449 USD
xfer_a_to_b = 0.056 × 9 = 0.504 USD
候选 b 总分摊（副本方案）≈ 5.40 + 1.449 + 0.504 = 7.353 USD
```

再对 **`c`、`d`、…** 各算一遍，取 **总成本最小** 的区作为第二副本。

**释义：「MILP 用线性系数代替这套离散枚举」**

手算做法是 **离散枚举**：对每个候选区 **`b`、`c`、`d`…** 分别代入公式算出一条 **标量成本**，再在有限个候选里 **穷举比较谁最小**；若再加入 EC、多块条纹与布尔开关（副本 vs EC），组合数会继续变大。

**MILP 的做法**是：先把「若在 `r` 放一整副本 / 若在 `(r,k)` 放一条纹」带来的 **存储 + 边费** 近似成 **与决策变量相乘的一个已知权重**（代码里的 **`sRep[r]`、`sEc[r][k]`**），目标函数形如：

```text
cost ≈ sum_r sRep[r] * x[r] + sum_{r,k} sEc[r][k] * y[r][k]
```

其中 **`x[r]`、`y[r][k]`** 取 **0 或 1**。求解器在 **线性目标 + 线性约束** 下一次性给出 **最优 0/1 赋值**，等价于在「所有可行放置图案」上做最优挑选，但 **不必手写两重、三重 for 循环去枚举每一种组合**。代价是：必须把真实计费近似成这种 **可加、对每个 placement 选项线性** 的形式（TTL、时长等先固定在系数里）。

---

## 5. 分支 B：EC — **base + 2 shard**（与 MILP 一致）

先在 **base** 上对 **\(S=9\)** 单位整副本做 RS **2+1**（叙事上三块等大）；MILP 仅 **两条外传 shard**，**\(S_{\mathrm{shard}} = S/2\)**（**\(K_{\mathrm{DATA}}=2\)**）。**parity** 不进 MILP 条纹变量。

```text
S_shard = S / 2 = 4.5
```

**Base（与 §3 相同）**

```text
Cost_base_stor = 5.40 USD
```

**分片存贮（修正笔误）**

你原文若写成「`shard_b` 也用 `dst_a_TTL`」应为 **`dst_b_TTL`**（两片在不同区，TTL 一般也不同）：

```text
stor_EC_1 = S_shard × ρ_c × T_c
stor_EC_2 = S_shard × ρ_d × T_d
```

约束：**`c ≠ d`**（且都与「是否允许 base 上再放 shard」由模型决定；概念上允许 shard 与 base 同区时，`c` 或 `d` 可以等于 `a`，单价取该区存储价即可）。

**数值例**

- **\(\rho_c = 0.023\)**，**\(T_c = 5\)**；**\(\rho_d = 0.024\)**，**\(T_d = 6\)**  

```text
stor_EC_1 = 4.5 × 0.023 × 5 = 0.5175 USD
stor_EC_2 = 4.5 × 0.024 × 6 = 0.648 USD
```

**分发（跨区边费 × 体积；FB 下 PUT 就在 base 时通常没有「进 base」这段运费）**

- **你的设定**：PUT **发起区**就是 **base（`a`，basement）**——对象就地写入 base，**不经过跨区域链路**。这时 **不该再单独列一项「PUT 源 → base」的网侧运费**：这块成本叙事里只有 **base 存贮费**（上文 **`Cost_base_stor`**），**没有额外的跨 reg 分发费进 base**。
- **一般写法**：若 PUT 源与 base 不同，可再加 **`γ_src_to_base × S`**；FB 下 PUT 在 **`a`** 时该项为 **0**。

```text
xfer_EC = γ_a_to_c × S_shard + γ_a_to_d × S_shard
```

**数值例**：**\(\gamma_{a\to c}=0.056\)**，**\(\gamma_{a\to d}=0.052\)** USD/单位  

```text
xfer_EC = 0.056 × 4.5 + 0.052 × 4.5 = 0.252 + 0.234 = 0.486 USD
```

**EC 方案本例总**

```text
5.40 + 0.5175 + 0.648 + 0.486 = 7.0515 ≈ 7.05 USD
```

与 §4「base + 第二副本」**≈7.35 USD** 对比：**EC 略低**；换 TTL 或 \(\gamma\) 后会变——这正是 MILP 要比的内容。

---

## 6. 与当前 Java 实现的简要对照

| 概念 | 本说明 | 仓库现状（便于核对） |
|------|--------|----------------------|
| Base 整对象时长 | `t_end - t_put` | `fbBaseReplicaObjectiveConstant`：`StorageCostHelper` + **一条** `issueRegion→base` 边项（`PolicyMilpECnewV3`）；当 **`issue_region` 与 base 同区且图上无自环费时，该项应为 **0**，叙事上与「PUT 在 base、无进 base 运费」一致 |
| Base 是否进 x | 不进，单独常数项 | `fixedBaseFb` 时 `x[base]=0` 且 `obj.addConstant(...)` |
| 第二整副本 | 枚举思想 | MILP 变量 `x[r]`，`r ≠ base`，且 **`extraRep ≤ 1`** |
| EC **base + 2 shard** | base **RS 叙事**可含 parity；**成本式只对 2 条外传 shard** | **`NUM_EC_SHARD_SLOTS = 2`**：`y[r][k]` 仅 **`k ∈ {0,1}`**，**`sEc[r][k]`** 同维；与 §5 手算一致 |
| 模板大小 | **\(S=9\)** 单位手算例 | **`LogicalObject.getSize()`**，由 trace 填入 |
| 边费「src」 | 叙事里 **PUT 发起区 `a`→`b`** | **代码**：`edgeCostRequired(Request.issueRegion, dst)`，即 **当前 trace 行的 `issue_region`→候选区**（见文首术语） |

**代码里 MILP 用到的 `issueRegion`**：`PolicyMilpECnewV3.syncMilpIfHistogramTtlAdvanced` 里 **`profileRegion = request.issueRegion`**，也就是 **当前这条请求** CSV 列 **`issue_region`**，不会被后台改成 `baseRegion`。

若你希望 **边费 src 固定为 PUT 发起区 `a`**，需要在策略里显式传入 **`template.baseRegion`**（或单独字段），再用 `edgeCostRequired(a, dst)`（当前仓库默认仍是「当前行的 `issue_region`」）。

---

## 7. 附录：TTL 刷新真的会在「读命中」时发生吗？（Python `simulator_v2.py` 根源）

结论：**会，但只是在满足一组条件时**；**不是**「任意读命中都会刷新」。Python 与 Java 此处逻辑对齐。

**Python**（`skystore_python/artifact-eval-main/simulation/src/simulator_v2.py`，`initiate_data_transfer`）里先计算 `refresh_ttl`：

```python
refresh_ttl = (
    ttl
    and src in physical_objects
    and src != logical_object.base_region
    and src == request.issue_region
    and op == "read"
)  # Only refresh on reads form the same region
```

含义逐项拆开：

| 条件 | 含义 |
|------|------|
| `ttl` | 传入 `initiate_data_transfer` 的开关；对读路径，由「是否为基于驱逐的策略」（如 `tevict_ranges` 等）决定是否为真 |
| `src in physical_objects` | 本次传输图里标为 **源** 的区 **`src`**，本地已有该对象的物理副本 |
| **`src != logical_object.base_region`** | **数据源不是 base**：在 **base 区上的读不满足此条，不会走这段 TTL 刷新** |
| **`src == request.issue_region`** | **服务本次读的源区**与 **本请求 CSV 的 `issue_region`** 相同（本地 issue 区命中缓存副本的那类路径） |
| `op == "read"` | 仅读操作 |

只有 **`refresh_ttl` 为真**时，才会进入后面一大段「Simulate a cache hit: Handle refresh TTL…」（含 `tevict_ranges` 等调 `get_tevict` / 更新本地副本 TTL）。因此：**TTL 刷新发生在「读、且本地用于服务的副本区等于 `issue_region`、且该区不是 base」这一类命中路径上**。

**Java**：`SimulatorV2.initiate_data_transfer` 中 **`refresh_ttl`** 判断与上式等价。**过去**曾在该读路径分支内对 **`milp_ec_new_v3`** 调用 `syncMilpIfHistogramTtlAdvanced`，现已 **移除**：**MILP 仅在写路径 `calculateTtl` 中**在直方图滚桶时触发。**`milp_ec_new_v3`** 不在 Python `simulator_v2.py` 策略列表中出现；Python 仅有相同的 **`refresh_ttl`** 与 TTL 刷新逻辑。

**写入路径**：`calculate_ttl` / Java `calculateTtl` 在 **write** 时也会推进 TTL、并在滚桶时触发 MILP（与读路径独立）。

---

## 8. 小结

- **Base**：**\(S\,\rho_a\,D_{\mathrm{base}}\)**（配对 TTL **不**用于 base 全长）。  
- **第二副本**：**\(S\,\rho_r\,T_{\mathrm{TTL}}\)** + **\(\gamma\,S\)**。  
- **EC**：base 同上 + 两片 shard：**\( (S/2)\,\rho\,\cdot\)天数** + **\(\gamma\,(S/2)\)** 每条边。
