# 在 SkyStore 成本放置之上引入相似数据差分编码与副本–纠删混合冗余：增量式仿真评估

**（论文完整稿 · 参照 Fast Variable-Grained Resemblance Data 结构与写作思路）**

---

## 摘要

云对象存储的多区域放置需要在**存储占用费**与**跨区域传输费**之间持续权衡。SkyStore 通过 write-local、replicate-on-read 与自适应 TTL 直方图，以货币成本为目标优化副本**保留时长**，但冗余形态始终为**整对象副本**，且未利用相似对象之间的 delta 冗余。另一方面，Finesse 等相似检测方案可在块级发现 resemblance 并配合 delta 压缩节省空间，却**未与多区域成本放置、TTL 驱逐语义联合建模**。

本文在 SkyStore 类仿真栈（TevictRanges 基线）之上，提出 **PolicyMilpECnewV4**：保留 TevictRanges 的 TTL/驱逐语义，在写路径引入（i）基于 manifest 的**差分编码（δ）**体积与传输计费；（ii）副本与 $(2{+}1)$ RS 纠删码（EC）的 **MILP 布局快照**比较。我们在与 SkyStore **相同请求行与价目表**的 IBM SNIA 轨迹上，完成 **δ×EC 2×2 因子分解**、**差分覆盖率 $p\in[0.05,0.5]$ 扫描**及 **T15/T65/T78/T79 跨 trace** 评估。

结果表明：（1）**δ 带来独立、可测的 incremental 收益**（T79 上 $p=0.1$ 约 **−1.1%**，$p=0.5$ 约 **−4.1%**），主要节省传输分项；（2）默认 MILP **从不选择 EC**（ec\_rate=0），强制 EC 略增成本；（3）δ 与 EC **无正向交互**。本文工作定位为 SkyStore 之上的**增量扩展**，而非革命性突破；实验设计与指标在沿用 SkyStore 三账的同时，补充成本归因与覆盖率敏感性分析。

**关键词**：云存储；SkyStore；相似数据；差分编码；纠删码；混合整数规划；多区域放置；增量评估

---

## I. 引言

### A. 背景与动机

随着数据外包至公有云，用户在多可用区、多地域部署对象存储时，面临**存储账单**与**跨区域 egress** 的长期权衡 [SkyStore]。SkyStore 将问题形式化为**成本管理**：写路径 local-write，读 miss 时 replicate-on-read，并通过访问间隔直方图估计 $\mathrm{ExpectedCost}(\mathrm{TTL})$，在``为副本付存储费''与``读 miss 再付传输费''之间自适应选择驱逐时长。仿真表明，相对固定 TTL 与工业基线，SkyStore 可在 IBM 类轨迹上取得显著节省。

然而，SkyStore 在**冗余形态**与**数据相似性**两个维度存在空白：

1. **体积维度**：远端缓存按**整对象体积 $V$** 计费；纠删码虽可将单片存储降为 $V/k$，SkyStore 未在 TTL 框架内比较 replica 与 EC 的价目表分支。
2. **相似性维度**：真实工作负载存在大量**非完全相同但高度相似**的对象 [Finesse]。对相似成员仅存储 delta，可在传输与存储上减少冗余；传统 SkyStore/tevict **不读取** similarity manifest，无法受益。

Fast Variable-Grained Resemblance Data Deduplication [Ye et al., 2021] 指出：相似去重若一律细粒度切分，会显著增加**元数据**并损害系统性能；应在**高冗余概率区域**细粒度、低概率区域粗粒度处理。受该思想启发，我们**不在本文重新设计 CDC/特征提取**，而是将 Finesse 离线 manifest 作为**可控旋钮**（覆盖率 $p$），在**已确定的多区域成本放置**之上评估：当相似成员比例从低到高变化时，delta 与 EC 各自贡献多少——这正是导师所强调的 **incremental、可分解** 实验路径。

### B. 问题陈述

给定：SkyStore 类价目表（$S_r$ 存储单价、$N_{ij}$ egress 单价）、IBM 访问轨迹、Finesse 合并 manifest（差分率 $r$、成员 key 集合）。

问：

1. 仅启用 δ、仅启用 EC、二者同时启用，相对 tevict **各节省多少**？
2. 当差分覆盖率 $p$ 从 0.05 增至 0.5，总成本如何变化？是否存在可报告的**单调趋势**？
3. 在不同 SNIA trace（T15–T79）与访问模式 slice（typeA–D）上，结论是否稳健？

### C. 主要贡献

1. **系统**：在 TevictRanges TTL 语义不变的前提下，实现 V4 策略——δ 体积/传输计费 + 写路径 MILP 比较 replica 与 EC 分支（`PolicyMilpECnewV4` / `GurobiEc21MilpBuilder`）。
2. **方法**：提出与 SkyStore 对齐的 **δ×EC 2×2  ablation** 及**因子归因公式**（内基线 nodelta\_rep），并补充 $/req、transfer/aggregate GB、ec\_rate 等 incremental 指标。
3. **实验**：在 T79 上完成 $p\in\{0.05,0.1,0.2,0.3,0.5\}$ 扫描；在 T15/T65/T78/T79 上跨 trace 对比；报告 typeA–D 访问模式与 2×2 分解结果。**诚实报告** EC 未选中、T15 略负等边界情形。

### D. 论文结构

第二节综述 SkyStore、相似去重与 EC 放置相关工作；第三节形式化系统模型与问题边界；第四节描述 V4 设计与 MILP 触发协议；第五节给出数据集、指标与实验结果；第六节总结。

---

## II. 相关工作

### A. 成本导向的多区域放置

**SkyStore** 以货币成本为目标，用 TTL 直方图学习访问间隔，在多区域有向图上决策副本保留时长。**SPANStore** 同样优化成本，但采用写时复制且不含 TTL 驱逐。**TevictRanges**（本仓库 `PolicyTevictRangesNew`）复现 SkyStore 配对边直方图与 FP/FB 语义，是本文**直接基线**。

本文**不**重新设计 TTL 学习，而是在其输出的 $T$ 快照上增加**体积形态**（$V$ vs $V/k$）与 **δ 缩减体积** $rV$ 的比较。

### B. 相似数据去重与 Finesse

传统去重仅识别完全相同的块；**Finesse** 通过分组秩特征加速 resemblance 检测，再对相似块做 delta 压缩。**Fast Variable-Grained** 工作进一步指出元数据与块粒度的权衡，采用可变粒度减少不必要的细粒度检测。

本文**离线**使用 Finesse manifest 标注 trace 中的 base/member 与差分率 $r$（均值约 0.007），在线仿真仅改变**计费体积与传输量**，不重跑特征提取——与 SkyStore ``同一 trace 行、同一定价'' 的公平对比原则一致。

### C. 纠删码与存储计价

$(k,m)$ EC 将持久占用从 $r$ 倍整对象降为约 $(k{+}m)/k$ 倍；Azure 等系统将 EC 作为默认冗余形态。Uluyol 等从延迟–成本前沿论证 EC 相对纯复制的潜力。

SkyStore 系策略优化**保留多久**；本文 MILP 在**保留窗口已定**时比较 replica 与 EC 的**存储+放置 egress**（不含编解码 CPU 费，与 SkyStore 忽略操作费一致）。

---

## III. 问题陈述

### A. 计价模型

沿用 SkyStore 仿真栈：每对象体积 $V$；区域 $r$ 存储费率 $S_r$；边 $(i,j)$ egress 费率 $N_{ij}$。7 天仿真窗口内，总成本：

\[
C = C_{\mathrm{storage}} + C_{\mathrm{transfer}}
\]

**整副本**：在区域 $r$ 驻留时长 $T$ 的存储费 $\approx V\cdot S_r\cdot T$。**EC 数据片**（$k{=}2$）：单片 $\approx (V/k)\cdot S_r\cdot T$。**δ member**：有效体积 $\approx r\cdot V$，base 仍按 $V$；读 member 可能拉取 base+delta。

### B. 决策边界

- **TevictRanges** 决定 TTL $T$ 与驱逐；MILP **不**将驱逐时刻作为变量。
- **MILP** 仅在写路径直方图 warm 且跨 12h 桶时触发，为每个 `issueRegion` 选择 replica 或 EC 落区。
- **δ** 由 trace 列与 manifest 决定；`allow_ec` / `force_ec_only` 为消融开关。

### C. 因子分解（2×2）

| 角 | δ | EC |
|----|---|---|
| nodelta\_rep | ✗ | 禁止 |
| nodelta\_ec | ✗ | 强制 |
| delta\_rep | ✓ | 禁止 |
| delta\_ec | ✓ | 允许 |

归因：$\Delta_\delta = C(\delta\_rep)-C(nodelta\_rep)$；$\Delta_{EC}=C(nodelta\_ec)-C(nodelta\_rep)$；交互 $= C(\delta\_ec)-C(nodelta\_rep)-\Delta_\delta-\Delta_{EC}$。

---

## IV. 系统设计

### A. 工作流程（参照 Fast 论文 Fig.1 叙事）

1. **离线**：IBM trace + Finesse manifest $\rightarrow$ 合成 trace（`.delta.mc.aug`），控制 $p$ = member key 占比。
2. **在线写**：write-local；直方图滚动 $\rightarrow$ 若触发 MILP，比较 replica/EC 分支成本 $\rightarrow$ 缓存布局决策。
3. **在线读**：miss 时按策略拉取（整副本 / base+delta / EC 恢复为整副本语义）。
4. **驱逐**：TTL 到期删除物理副本；与 tevict 语义对齐（含 t9fix：空 region\_list 不误调 getTevict）。

### B. MILP 分支（直觉）

- **Replica 分支**：基地外至多一整副本，存储 $\propto V$，放置 egress $\propto V$。
- **EC 分支**：两条外传 data shard，存储 $\propto 2V/k$，放置 egress $\propto 2V/k$；读 miss 升级整对象恢复。

当 $T$ 较长且 $V$ 较大时 EC 存储项理论上占优；实验显示在 IBM T79 + 3-reg FP 价目表下，**传输与读恢复**使 MILP 仍选 replica。

### C. 实现要点

- 策略：`PolicyMilpECnewV4`（SimulatorV3, FP）
- 基线：`PolicyTevictRangesNew`（SimulatorV1）
- 求解：`GurobiEc21MilpBuilder`；日志 `milp_solve_log_*.jsonl` 记录 ec\_rate

---

## V. 实验评估

### A. 数据集（参照 Fast §V-A 多工作负载叙事）

| 类别 | 说明 |
|------|------|
| **主轨迹** | IBM ObjectStoreTrace **T79** typeA，114,227 请求，3 区域 FP |
| **覆盖率扫描** | 同一 T79，$p\in\{0.05,0.1,0.2,0.3,0.5\}$ |
| **跨 trace** | T15, T65, T78, T79（$p{=}0.1$）；T29（13M 请求，仿真 OOM 跳过） |
| **访问模式** | typeA–D slice（同 member 集，地理访问不同） |
| **Manifest** | `finesse_merged_manifest.csv`；$r$ 中位数 $\approx 0.005$ |

与 SkyStore 一致：同一 trace 文件、同一 7 天窗口、同一价目表；tevict **不读** δ 列。

### B. 评价指标

**沿用 SkyStore**：total / storage / transfer cost (\$)，requests，aggregate GB。

**增量指标**（本文新增，便于 incremental 论文）：

| 指标 | 含义 |
|------|------|
| vs\_tevict\_% | 相对 SkyStore 基线 |
| vs\_nodelta\_rep\_% | 相对无 δ replica 内基线 |
| transfer/aggregate GB | 传输强度 |
| cost/request | 跨 trace 归一化 |
| ec\_rate | MILP 选 EC 比例 |
| $\Delta_\delta,\Delta_{EC}$ | 因子归因 (\$) |

（参照 Fast 论文引入 DCR 考虑元数据；本文用 **coverage $p$** 作为``旋钮''考察 resemblance 强度，而非另建 DCR 公式，以避免与云账单口径混用。）

### C. 差分覆盖率扫描（核心大实验）

**表 1** T79 typeA：V4（δ+EC 允许）vs tevict

| $p$ | members | total (V4) | total (tevict) | vs tevict | $\Delta$ transfer |
|--:|--------:|-----------:|---------------:|----------:|------------------:|
| 0.05 | 464 | 273.08 | 275.19 | **−0.77%** | −1.01 |
| 0.10 | 912 | 272.08 | 275.19 | **−1.13%** | −1.93 |
| 0.20 | 1808 | 270.17 | 275.19 | **−1.83%** | −3.63 |
| 0.30 | 2727 | 268.17 | 275.19 | **−2.55%** | −5.44 |
| 0.50 | 4548 | 263.88 | 275.19 | **−4.11%** | −9.27 |

**讨论**（参照 Fast 随块大小/粒度变化的讨论方式）：

- 随 $p$ 增加，δ 可作用的对象增多，节省**近似单调**上升，符合``相似数据越多、delta 越有用''的预期。
- 即使 $p{=}0.5$，相对 tevict 仍约 **4%**，属于**小幅 incremental** 改进，而非数量级突破。
- 全档 ec\_rate=0，说明在当前 manifest（$r{\approx}0.007$）与价目表下，**EC 不是有效杠杆**。

### D. δ×EC 2×2 分解（T79 typeA, $p{=}0.1$）

| 角 | total | vs tevict | ec\_rate |
|----|------:|----------:|---------:|
| tevict | 275.19 | — | — |
| delta\_ec / delta\_rep | **272.08** | **−1.13%** | 0% |
| nodelta\_rep | 274.22 | −0.35% | 0% |
| nodelta\_ec | 274.64 | −0.20% | 100% |

$\Delta_\delta{=}{-}\$2.14$（transfer −1.93）；$\Delta_{EC}{=}{+}\$0.43$；交互 $\approx 0$。

**结论**：当前收益 **≈ 纯 δ**；V4 相对 tevict 的额外 0.35% 来自 TTL/MILP 修复（nodelta\_rep），与 δ 无关。

### E. 跨 Trace（$p{=}0.1$）

| Trace | V4 | tevict | vs tevict |
|-------|---:|-------:|----------:|
| T65 | 6.56 | 6.59 | −0.42% |
| T78 | **24.18** | 26.41 | **−8.45%** |
| T79 | 272.08 | 275.19 | −1.13% |
| T15 | 77.06 | 76.56 | +0.65% |

T78 节省主要来自 storage（13.56 vs 15.68）；T15 略负说明 δ 收益**依赖 trace 结构**，不宜过度泛化。

### F. 访问模式 slice（typeA–D, $p{=}0.1$, t9fix）

| type | V4 | tevict | vs tevict | 特点 |
|------|---:|-------:|----------:|------|
| A | 272.08 | 275.19 | −1.13% | 写密集，transfer 占比高 |
| B | 208.21 | 209.74 | −0.73% | 区域感知 |
| C | 217.27 | 218.91 | −0.75% | 读集中，transfer 仅 ~19% |
| D | 270.88 | 273.94 | −1.12% | 多读 |

δ 在 transfer 占比高的 typeA/D 上略优；typeC 绝对节省更小，与**成本结构**一致，而非相似度不同（同 p 下 member 集相同）。

### G. 敏感性与其他实验（摘要）

- **9-reg / force EC**：δ 可叠加；force EC 仅 +$0.1 量级。
- **高 r manifest（×10）**：p100 覆盖下仅 +$0.16，说明**覆盖范围**比单点放大 r 更关键。
- **MILP 调参（gap/time/min\_requests）**：对 T79 p100 无影响，布局稳定。

---

## VI. 结论

本文在 SkyStore 类仿真栈上，对**相似数据差分编码**与**副本–EC MILP 布局**做增量式联合评估。核心发现：

1. **δ 有效但幅度有限**：T79 上随覆盖率 $p$ 从 0.05 到 0.5，相对 tevict 从约 **0.8% 增至 4.1%**，主要节省传输。
2. **EC 在当前配置下不被选用**；单独 force EC 略增成本；与 δ **无协同**。
3. 实验设计与 SkyStore **同一 trace、同一三账**；增量指标与 2×2 归因支持``小步可测''的论文叙事。

**未来工作**：（1）更大 $p$ 与更高 $r$ manifest 的联合扫描；（2）6/9 区域 EC 价目表下是否出现 ec\_rate>0；（3）T29 等超大 trace 的分段仿真；（4）读路径 EC 增量恢复以降低读放大。

---

## 参考文献（节选）

1. SkyStore (2025). Cost-optimal multi-region object storage.
2. Ye et al. (2021). Fast Variable-Grained Resemblance Data Deduplication For Cloud Storage. IEEE.
3. Finesse. Resemblance detection for delta compression.
4. SNIA IBM Object Store Trace (T15–T79).
5. Azure Erasure Coding; Plank EC survey; Uluyol latency–cost frontier.

---

## 附录 A：复现命令

```bash
# 2×2 ablation
python scripts/run_delta_ec_2x2.py
python scripts/run_delta_ec_2x2.py config/experiment_delta_ec_2x2_p200.yaml

# 覆盖率扫描 + 跨 trace
python scripts/run_delta_saturation_sweep.py
python scripts/compile_e13_results.py
```

## 附录 B：产物索引

| 文件 | 内容 |
|------|------|
| `results/E13_delta_saturation_summary.csv` | 覆盖率 + 跨 trace |
| `results/E12_delta_ec_2x2_*_summary.csv` | 2×2 分解 |
| `results/E12_incremental_paper_table.md` | 论文表 |
| `paper/zh_ec_milp_draft/main.tex` | LaTeX 模板（可拆节编译） |

---

*定位说明：本文为 incremental 工作——在 SkyStore 已验证的 TTL 放置之上，量化 δ 与 EC 的边际贡献；写作结构参照 Fast'21 的``动机–设计–多组实验–诚实讨论''，数值上不追求革命性突破。*
