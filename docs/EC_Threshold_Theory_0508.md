# EC-Threshold：在 TevictRanges 之上用闭式规则引入 EC

> **本文目标。** 在已有最优基线 `TevictRanges` 之上，给出一条
> **可形式化证明的、毫秒级可计算的闭式判定规则**，决定一个对象类
> 是继续走「TTL 多副本」还是切换到「EC(K, R−K) 多区分片」。
> 凡满足规则的对象切换后，**单对象期望总成本严格不增**；
> 因总成本是逐对象之和，**仿真总账单严格不增**。
>
> 本文不依赖 MILP；MILP 仅作第 §6 节的对照。

---

## 1. 定义

**闭式规则（Closed-form rule）。** 不调用任何求解器，仅由
有限个标量输入与一条不等式即可给出决策的规则。
本工程中，输入为 `priceStorage`、`edgeCost`、TTL、对象大小与
访问预测，输出为 `{REPLICA, EC}`。

与 MILP 的关系：闭式规则是 MILP 在「**同一组保留区域、相同保留度
\(R\)、副本本地命中、EC 本地有 1 片**」假设下的解析最优解；MILP 是
本规则在「价格异构 + 区域可选 + 多发起区」场景下的离散搜索推广。

---

## 2. 模型设定（与 FB 模式 + TevictRanges 对齐）

| 符号 | 含义 | 来源 |
|------|------|------|
| \(S\) | 对象逻辑大小（GB） | trace |
| \(W\) | TevictRanges 给出的 TTL（秒） | `getTevict` |
| \(K\) | EC 数据片数（默认 2） | 配置 |
| \(R\) | 对象**预计活跃**的非 base 区个数 | 直方图区域分布 |
| \(\mathcal{A}\) | \(R\) 个非 base 区的集合 | 同上 |
| \(b\) | base 区（FB 模式下永远存整对象） | `LogicalObject.baseRegion` |
| \(p_r\) | 区 \(r\) 的存储单价（$/GB·sec） | `priceStorage` |
| \(c_{ij}\) | 区 \(i\to j\) 的跨区运费（$/GB） | 拓扑 |
| \(\bar{c}\) | 读侧平均跨区运费 | `avgNetworkCost` |
| \(\lambda\) | **窗 \(W\) 内**该对象类的预测读次数 | 直方图 + 计数 |

**两种候选方案**（同样在 \(\mathcal{A}\) 上保持耐久度 \(R\)）：

- **方案 A — Replica（TevictRanges 现状）**：每个 \(r\in\mathcal{A}\)
  保留一份整对象；TTL 各自驱逐。
- **方案 B — EC(K, R−K)**：每个 \(r\in\mathcal{A}\) 各放一片
  （片大小 \(S/K\)）；**整组同一 TTL，到期统一驱逐**（工程实现简单
  且与论文「一片到期则全部驱逐」假设一致）。

---

## 3. 总成本（逐项写出，便于消去）

读侧设：本次读发生在 \(\mathcal{A}\) 内某区 \(r^\star\)，
副本方案在 \(r^\star\) 本地命中（运费 0），
EC 方案在 \(r^\star\) 本地有 1 片，**还需再拉 \(K-1\) 片**
（运费按 \(\bar{c}\) 估计）。

**方案 A（Replica）：**
$$
C_{\mathrm{rep}} \;=\; \sum_{r\in\mathcal{A}} p_r S W
\;+\; \sum_{r\in\mathcal{A}\setminus\{b\}} c_{b,r}\, S
\;+\; \sum_{r\in\mathcal{A}} u_r
\;+\; \lambda\, g_{r^\star}.
$$

**方案 B（EC）：**
$$
C_{\mathrm{ec}} \;=\; \sum_{r\in\mathcal{A}} p_r \tfrac{S}{K} W
\;+\; \sum_{r\in\mathcal{A}\setminus\{b\}} c_{b,r}\,\tfrac{S}{K}
\;+\; \sum_{r\in\mathcal{A}} u_r
\;+\; \lambda\, g_{r^\star}
\;+\; \lambda\,(K-1)\,\bar{c}\,\tfrac{S}{K}.
$$

PUT 与 GET 单价项 \(\sum u_r,\; \lambda g_{r^\star}\) 在两式中相同，
**逐项抵消**。

---

## 4. 三类差额（节省 vs 增加）

**存储节省：**
$$
\Delta_{\mathrm{store}} \;=\; \Big(1-\tfrac{1}{K}\Big)\, S\, W\, \sum_{r\in\mathcal{A}} p_r .
$$

**写分发节省：**
$$
\Delta_{\mathrm{write}} \;=\; \Big(1-\tfrac{1}{K}\Big)\, S \sum_{r\in\mathcal{A}\setminus\{b\}} c_{b,r}.
$$

**读侧增加：**
$$
\Delta_{\mathrm{read}} \;=\; \lambda\,(K-1)\,\bar{c}\,\tfrac{S}{K}.
$$

---

## 5. 主定理与证明

> **定理 1（EC-Threshold）。** 在 §2 设定与 §3 的读侧假设下，
> $$
> C_{\mathrm{rep}} > C_{\mathrm{ec}} \;\Longleftrightarrow\;
> \lambda \;<\; \lambda^\star,
> $$
> 其中
> $$
> \boxed{\;
> \lambda^\star \;:=\; \frac{W\,\sum_{r\in\mathcal{A}} p_r \;+\;
>                              \sum_{r\in\mathcal{A}\setminus\{b\}} c_{b,r}}{\bar{c}}\;}
> $$

**证明。** 由 §3 直接相减：
$$
C_{\mathrm{rep}} - C_{\mathrm{ec}}
= \Delta_{\mathrm{store}} + \Delta_{\mathrm{write}} - \Delta_{\mathrm{read}}.
$$
代入 §4 三式：
$$
C_{\mathrm{rep}} - C_{\mathrm{ec}}
= \Big(1-\tfrac{1}{K}\Big)\Big(W\sum_r p_r + \sum_{r\ne b} c_{b,r}\Big)\,S
- \tfrac{K-1}{K}\,\bar{c}\,\lambda\,S.
$$
两端同除 \(\tfrac{K-1}{K}\,S>0\)（注意 \(K\ge 2\)）：
$$
\frac{C_{\mathrm{rep}} - C_{\mathrm{ec}}}{\tfrac{K-1}{K}\,S}
= \Big(W\sum_r p_r + \sum_{r\ne b} c_{b,r}\Big) - \bar{c}\,\lambda.
$$
故 \(C_{\mathrm{rep}} > C_{\mathrm{ec}} \iff
\lambda < \frac{W\sum_r p_r + \sum_{r\ne b} c_{b,r}}{\bar{c}} = \lambda^\star.\)
$\square$

**直观解读。** \(\lambda^\star\) 的分子等于「**窗 \(W\) 内 \(R\) 个区
副本要付的存储 + 写分发总钱**」；分母是「**单 GB 跨区运费**」。
**所以 \(\lambda^\star\) 就是「这笔钱够你在跨区上拉多少 GB 数据」**——
读得**比这少**就该 EC，**多于这数**就该 replica。

---

## 6. 全局结论与对照

> **推论 1（仿真总账严格不增）。** 仅当 \(\lambda<\lambda^\star\) 时
> 把对象类切换到 EC，则该对象类的期望成本严格下降；其余对象保持
> TevictRanges 不变。由总成本逐对象可加，仿真总账 **逐项不增**，
> 故 **不增**。$\square$

| 组别 | 策略 | 角色 |
|------|------|------|
| A | AlwaysStore | 上界（不驱逐） |
| B | AlwaysEvict | 下界（不留缓存） |
| C | **TevictRanges** | 当前最优基线 |
| **D** | **EC-Threshold（本文）** | **理论保证 ≤ C** |
| E | MILP+EC（V3） | 异构精细搜索（验证 D 是否近似 MILP） |

> **关于「严格 \(<\)」与「\(\le\)」。** 当 \(\lambda=\lambda^\star\) 时
> 两方案成本相等；规则中令此情形仍走 replica，避免决策抖动。
> 因此理论结论只能保证 **不增（\(\le\)）**，但只要存在
> 「冷于 \(\lambda^\star\)」的对象类，**严格下降即可达成**。

---

## 7. 算法（决策过程）

对每个 **对象类** \(o\)（按 (issueRegion, 相似访问模式) 聚类，
最简实现可按 issueRegion 聚类）：

```
Input: 当前 simulator 时刻 t, 对象 o
1. W   ← TevictRanges.getTevict(b, issueRegion, t)
2. A   ← 直方图给出的非 base 活跃区集合（去掉 b）
3. R   ← |A|；若 R < K，回退 replica，结束
4. λ   ← 窗 W 内对该对象类的读次数预测
       = round( read_count(issueRegion, last_W_seconds)
                / observed_seconds * W )
5. 计算 λ* = (W * Σ_{r∈A} p_r + Σ_{r∈A} c_{b,r}) / mean(c)
6. if λ < λ*  →  decision = EC（在 A 中各放一片）
   else       →  decision = REPLICA（保持 TevictRanges 行为）
7. 决策有效期 = max(W, MIN_REFRESH)；过期重新计算
```

**复杂度：** 单对象 \(O(R)\)，远低于 MILP。

---

## 8. 工程实现要点（与本仓库映射）

> 本节只列定位，不写代码；代码在 `EC_Threshold_Code.md` 落地。

| 步骤 | 现有工具 | 备注 |
|------|----------|------|
| TTL \(W\) | `PolicyTevictRangesNew.getTevict` | 已实现 |
| 直方图区分布 → \(\mathcal{A}\) | `nextPastRequests[region]` 键集合 | 已存在 |
| 窗内 \(\lambda\) | 读计数 + 观测时长（参考 V3 的 `predictTrafficForMilp`） | 已部分实现 |
| 价目 \(p_r, c_{ij}\) | `DiGraph` 节点/边属性 | 已存在 |
| 决策注入 | 新策略类 `PolicyEcThreshold` 重写 `place` | 待实现 |
| EC 落地大小 | `getPlacementWithSizes`（与 SimulatorV3 配合） | 待实现 |
| 整组 TTL 同步驱逐 | `RegionManager.removeObjectFromRegion` 或包装层 | 待实现 |

---

## 9. 实验设计（验证理论而不是仅看数）

**实验 9.1（合成数据，验证定理 1）：**
固定 \(R=3, K=2\)；扫描 \(\lambda\)，画 \(C_{\mathrm{rep}}(\lambda)\)
与 \(C_{\mathrm{ec}}(\lambda)\) 两条直线；交点必须等于 \(\lambda^\star\)。
**通过条件：** 数值差 \(<10^{-6}\)。

**实验 9.2（trace 上的对照实验）：**
在 9 region 配置下，跑 4 组 (A, B, C, D)，绘制
**总成本柱状图**与**单对象成本散点图**。
**通过条件：** D 总成本 \(\le\) C；散点图上仅 \(\lambda<\lambda^\star\)
的对象成本下降，其余不变。

**实验 9.3（对照 MILP）：**
同 trace 加跑 E（V3 MILP）。比较 D 与 E 的总成本差距：
若 \(|\,E - D\,|/C < 1\%\)，可在论文里声明「闭式规则
近似 MILP，且毫秒级可解」。

---

## 10. 与 SkyStore 论文的关系

- 本文 **不替代** TevictRanges，只在其结果之上加一层
  「冗余形态选择」。
- 论文 SkyStore 已证明 TevictRanges 在「时间维」近优；
  定理 1 给出 EC 在「形态维」可叠加节省的**充要条件**。
- 因此本文与 SkyStore 是**正交贡献**，可在论文中作为
  「形态维扩展」单独章节呈现。

---

## 11. 后续步骤

1. **实验 9.1（合成数据）**：一份 Python 脚本即可，先于代码落地，
   验证定理本身没有口算错误。
2. **代码骨架**：新增 `PolicyEcThreshold.java`，对照本文 §7 算法。
3. **对照实验 9.2**：与 C 在同 trace 下逐对象比对。
4. **MILP 对照 9.3**：仅作为附加验证。
