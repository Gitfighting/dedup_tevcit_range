# ChunkTevict：待做实验 + 出图方案 + 成稿后怎么改论文

对照：FAST 短文（Ye et al., ICC 2021）§V；SkyStore §6；初稿 [`paper/chunk_tevict/`](.)；代码 [`simulation/src`](../../skystore_python/artifact-eval-main/simulation/src)。

TTL **不是**新规则：作者 `calculate_ttl` 原样，只把单位从 object 换成 unique chunk。图和消融都不要再出现 “min-live TTL” 当卖点。

---

## 0. FAST 评测写法（成稿必须跟）

FAST 固定顺序，不把所有图堆在一节开头：

1. **A. Datasets**（三个 workload 各一段特征）
2. **B. Metrics**（主指标 DCR + 辅指标 overall time；写清机器）
3. **C. Numerical Results** **按图串讲**：先 Fig.2 说数字 → 解释 → 再 Fig.3 → …

FAST 图的视觉约定（我们出图时照做）：

- 一张图 **一个主指标**，横轴是扫描参数（他们是平均 chunk size；我们是 **trace** 或 **区数**）。
- SkyStore **Fig.5** 横轴就是 Always Evict / Always Store / Teven / TTL-CC / EWMA / AWS Multi-Region（分母是 SkyStore）。
- SkyStore **Table 4** 行是 Evict / Store / Teven / TTL-CC / EWMA / **JuiceFS**（3 区不用 AWS Multi-Region）。
- 我们对应这两处写：同样的基线，分母改成 ChunkTevict；SkyStore（TevictRanges）多画一条以便对比作者系统。
- Optimal 是 SkyStore **Table 3**（vs CGP），不要塞进 Fig.5。
- 分组柱状图，网格浅，图例在图内或上方；IEEE 双栏宽度 `\columnwidth`。
- **每个数据集单独成图或单独一段讨论**（MacOS / Kernel / SQL Dump → 我们对应 T15 / T65 / T78 / T79 / T29）。
- 不使用拥挤热力图当主图（现有 `fig5_3reg_table4.png` 成稿时改成分组柱）。

主指标映射：FAST 的 DCR ↔ **费用比 `cost(policy)/cost(ChunkTevict)`**。  
辅指标映射：FAST 的 overall time ↔ **回放墙钟 + CDC MB/s**；SkyStore Table 6 ↔ **仿真 RTT 延迟（avg/p90/p99）**，不上真三云。

---

## 1. 一趟回放同时出三项（对照 SkyStore 代码，不是对照 Table 6 真云）

SkyStore 论文把 **费用**（§6.3–6.5 仿真）和 **延迟**（§6.6 真三云原型）拆开。我们的 simulation **本来就能在同一次 `SimulatorV2.run()` 里记下三项**：

| 指标 | 作者对象级 | ChunkTevict（现已接上） |
|---|---|---|
| 存储 $ | `total storage cost ($)` = GB·h × 区单价 | 同上，单位是 unique chunk |
| 传输 $ | `total transfer cost ($)` = 跨区字节 × 边 `cost` | 缺失 uid 的字节 × 边 `cost` |
| 延迟 ms | 边 `avg_rtt`；同区 1 ms；`tracker` 现输出 avg/p50/p90/p99 | 同模型；GET miss 多源取 **max RTT** |

**能一起测：** 矩阵每个 job 一份 JSON，同时有 storage、transfer、read/write latency。对比实验 **不要** 再拆成「先跑费用、再跑延迟」。

**现在还测不出来的：** 2026-08-15 的 ChunkTevict JSON **只有费用**，没有 latency。对象级旧 JSON 一般有 **avg** 延迟，没有 p90（`tracker` 刚补百分位）。因此论文对比矩阵需要 **用当前代码重跑**（费用数字应与旧跑接近，延迟是新字段）。

**论文必须写清：** 这是查表 RTT，不是 SkyStore Table 6 的客户端墙钟；PUT 各策略都≈1 ms（本地写），对比看 **GET**。不上真三云，不做 vs AWS API。

---

## 2. 论文对比实验（按这个跑）

策略（严格对应 SkyStore Fig.5 / Table 4 / Table 3）：

- **Fig.5 两区：** Always Evict / Always Store / Teven / TTL-CC (`dynamicttl`) / EWMA (`ewma`, α=0.5) / AWS Multi-Region (`replicate_all`) + ChunkTevict；SkyStore 作额外一条。
- **Table 4 三区：** 同上，但工业基线换成 **JuiceFS**（`replicate_all` 跨云）。
- **Table 3：** object-level Optimal（CGP），单独小表，不进 Fig.5。

TTL 仍是作者 `calculate_ttl`，只改 object→chunk。不改 `policy_tevict_ranges_new.py`、不改 `simulator_v2` 主循环。

入口：`run_fig5_matrix_parallel.py`（对象级走 `main.py`，ChunkTevict 走 `aware_replay`）。每个 job 读同一条 `.mc-aug`。

### 核心矩阵 M1（必须，费用+延迟同一趟）

| 格子 | 模式 | traces | 工作负载 | 用途 |
|---|---|---|---|---|
| 2 区 | FB | T15, **T29**, T65, T78, T79 | type E | Fig.2 总费用比、Fig.3 存储/传输、**Fig.6 GET 延迟**、Table 绝对 $ + latency |
| 3 区 | FB | 同上五条 | types A–D | Fig.4 费用比；延迟表可只报 type A 或 T65/T78 以免页数爆炸 |

T29 先 **单 worker** 探内存。2 区 T15/T65/T78/T79 旧费用可作核对，**成稿用重跑（带延迟）**。

### 扩展矩阵 M2（SkyStore §6.5，仍同一趟记三项）

| 格子 | 说明 |
|---|---|
| 6 区 / 9 区 FB | 至少 T15/T65/T78/T79；有内存再 T29。Fig.5 费用随区数；延迟正文点一句即可 |
| FP `--fp` | 至少 2 区 + 3 区 A–D。证明不是 FB pin 把存储优势做大。延迟同样记，图以费用为主 |

### 消融 M3（2 区 T65+T78）

直方图 `unique_bytes` vs 对象 size；per-uid 时钟 vs 整对象一条 TTL。TTL 绑付钱边 **不做**。

### 离线切块 M4（不进矩阵）

现成索引统计 + `file_pool` **抽样** FastCDC MB/s → Fig.7。与回放墙钟 `wall_s` 一起当 FAST overall time。

### 开机顺序

1. M4 抽样 CDC（空闲）  
2. M1：T29 2reg FB 单 job → 通过后 2reg 全策略五条 trace → 3reg type A → B/C/D  
3. M2：6reg → 9reg → `--fp`  
4. M3 消融  

Worker ≤7；T29 `--max-heavy 1` 起。云路径 `/data/sim_exp/python_simulation`，chunk `/data/dedup_exp/T0xx`。

---

## 3. 论文里放哪些图（对照 FAST / SkyStore，按页数锁死）

IEEE 双栏约 7–8 页（FAST 是 8 页、评测约 6 张图）。我们 **正文锁定 7 张图 + 4 张表**。能画但不进正文的放附录或只写一句。

视觉跟 FAST：一张图一个主指标、分组柱、双栏 `\columnwidth`（Fig.5 四联可用 `figure*` 通栏）、矢量 PDF、分母一律 ChunkTevict、不要热力图当主图。
Fig.2 横轴必须是 SkyStore Fig.5 那一组（含 TTL-CC / EWMA / AWS-MR）；Fig.4 必须含 Table 4 的 JuiceFS。

### 3.1 和两篇原文的对应

| FAST (Ye ICC) | SkyStore (VLDB) | 我们正文 | 对比什么 |
|---|---|---|---|
| Fig.1 设计/算法 | Fig.1–3 TTL/架构 | **Fig.1** 对象时钟 vs chunk 时钟 | 机制，不是实验 |
| Fig.2–4 DCR×三数据集 | **Fig.5** 2 区费用比 | **Fig.2** 2 区总费用比 | 六策略 × 五条 trace |
| （DCR 公式里谈 metadata） | （费用拆存储/网络，文内） | **Fig.3** 存储 vs 传输堆叠 | SkyStore vs ChunkTevict，解释 *为何* 省钱 |
| — | **Table 4** 3 区 A–D | **Fig.4** 3 区类型柱（均值） | 策略 × Type A/B/C/D |
| 「参数变大 gap 变」 | **Fig.6** 3/6/9 区 | **Fig.5** 费用随区数 | ChunkTevict vs SkyStore，四 type 小图 |
| Fig.5–7 overall time | **Table 6** 真云延迟 | **Fig.6** 仿真 GET RTT（同 M1） | 六策略 × T65/T78；**不是**真云 |
| Fig.5–7 overall time | — | **Fig.7** CDC + 回放时间 | 离线切块 / 仿真墙钟 |
| — | Fig.7 vs AWS API | **不画** | 我们不是 overlay 系统 |
| — | Fig.4 轨迹特征 | **不重画**，引用作者 Table 2 | — |

### 3.2 正文 7 图（必须进论文）

**Fig.1 问题（§III，已有 tikz）**  
对象 A/B 共享 uid₂：对象级双 lease vs 一区一 uid。不必等实验。

**Fig.2 主对比：2 区总费用比（§V-C，对标 SkyStore Fig.5）**  
- 数据：M1 2reg FB type E，T15/T29/T65/T78/T79  
- 横轴：Always Evict / Always Store / Teven / TTL-CC / EWMA / AWS Multi-Region（与 SkyStore Fig.5 相同）  
- 分组柱：五条 trace 五色（沿用 `TRACE_COLORS`）  
- 纵轴：`(storage+transfer) / ChunkTevict`，y 从 0；ChunkTevict=1 用虚线或省略柱  
- 对比点：对象级策略相对 chunk 时钟贵几倍；Optimal 仍贵 → 粒度不是 TTL 估计器  
- 正文按 trace 串讲：T15 一击→Evict 还好；T65 热→Evict 传输爆炸；T79 大对象长尾→Store 存储爆炸  

**Fig.3 归因对比：存储 vs 传输（解释 Fig.2）**  
- 数据：与 Fig.2 同一 JSON 的 `storage` / `transfer`  
- 横轴：五条 trace  
- 每 trace **两根堆叠柱**：SkyStore | ChunkTevict（下存储、上传输），USD  
- 对比点：gap 主要是存储（旧 2 区约 88–97%）还是传输；热 trace 上 Evict 可在文内点一句，主图只留 SkyStore vs 我们，避免 6 策略×堆叠太挤  
- 可选右轴或旁注：GET chunk hit %（T65/T78 近 99%）  

**Fig.4 工作负载对比：3 区 Type A–D（对标 SkyStore Table 4）**  
- 数据：M1 3reg FB，五条 trace 对每个 type **先求 cost/ChunkTevict 再对 trace 平均**  
- 横轴：Uniform / Region / Aggregation / Replication  
- 分组柱：Evict / Store / Teven / SkyStore / Optimal  
- 纵轴：均值比值  
- **不要**现有 heatmap  
- 对比点：类型变了排名稳不稳；Type B Evict 最差（贵边集中 miss）  

**Fig.5 可扩展对比：3→6→9 区（对标 SkyStore Fig.6）**  
- 数据：M2；**归一锁死**：纵轴 = 该策略在该 type 下的费用 / **该策略自己的 3 区费用**（与作者 Fig.6 一致）  
- 四小图 A/B/C/D，通栏 `figure*`  
- 横轴：3 / 6 / 9  
- 系列：ChunkTevict 实线圆点；SkyStore 虚线方点（可加 Always Store 点线）  
- 对比点：区变多后 SkyStore 存储是否抬升、ChunkTevict 是否更稳  
- 缺 T29 就五条里能跑的都画，caption 写清  

**Fig.6 延迟对比：仿真 GET RTT（与 Fig.2 同一次 M1，对标 Table 6 的 *列* 不是 *测法*）**  
- 数据：`avg read latency (ms)` + `p90 read latency (ms)`  
- 两子图：T65 | T78（2 区 FB type E）  
- 横轴：六策略（含 ChunkTevict）  
- 每策略两根柱：avg 与 p90  
- 纵轴：ms，查表 RTT；命中=1；miss=边 `avg_rtt`；ChunkTevict 多源 **max**  
- 对比点：Evict miss→高 RTT；Store/SkyStore/ChunkTevict 命中多→接近 1 ms；chunk 命中更高时 GET 是否仍低于对象级 SkyStore  
- Caption 必须写 *simulated RTT, not end-to-end cloud latency*  
- PUT≈1 ms 各策略相同 → **不占图**，Table 4 一行  

**Fig.7 时间开销（对标 FAST overall time，不是延迟）**  
- (a) 抽样 FastCDC MB/s  
- (b) 回放 Kops/s 或 wall_s，横轴五条 trace（2 区 ChunkTevict）  
- 对比点：离线切块一次、回放可完成；**不是** GET 延迟  

### 3.3 正文 4 表（配合图，避免图里塞数字）

| 表 | 内容 | 数据 | 位置 |
|---|---|---|---|
| **Table 1** | Trace 特征 | 引用 SkyStore Table 2，不重测 | §V-A |
| **Table 2** | 2 区绝对存储+传输 $ | M1 2reg，与 Fig.2 同 run | Fig.2 旁 |
| **Table 3** | 3 区均值比值（Fig.4 的数字版） | M1 3reg 五条平均 | Fig.4 旁 |
| **Table 4** | GET/PUT avg·p90·p99 | M1 2reg 全策略；PUT 一行说清楚几乎相同 | Fig.6 旁 |

消融（M3）：2 区 T65/T78，**Table 5** 即可，不另占正文图（页数不够再加 Fig.8 两柱：`unique_bytes` vs `|o|`，per-uid vs 对象 TTL）。  
FP / 6–9 区细节：Fig.5 放不下就 **Table 6** 均值±std。

### 3.4 能画但不要进正文

| 图 | 原因 |
|---|---|
| 3/6/9 区每条 trace 的费用热力图 | 挤，FAST 不用；数字进 Table 3/6 |
| 3 区四种 type × 五条 trace 的延迟大图 | 页数；Fig.6 用 2 区 T65/T78 代表热/大流量，3 区延迟一句或附录 |
| 对象 hit vs chunk hit 单独大图 | 并进 Fig.3 旁注或 Table 2 两列 |
| Always Evict 六策略全堆叠存储/传输 | Fig.3 只留 SkyStore vs ChunkTevict |
| vs AWS API（SkyStore Fig.7） | 不做 |
| 平均 chunk size 扫描（FAST Fig.2–7 的横轴） | 不是我们的 knob |
| 真云 GET 墙钟 | 不做 |

### 3.5 评测节讲图顺序（FAST：先数字后机制）

§V-C 不要把图堆在开头，按段：

1. **C1 2 区费用** — Fig.2 + Table 2（总账）  
2. **C2 为何便宜** — Fig.3（存储/传输 + hit）  
3. **C3 延迟** — Fig.6 + Table 4（与 C1 **同一趟**，先费用后延迟）  
4. **C4 3 区类型** — Fig.4 + Table 3  
5. **C5 3/6/9** — Fig.5  
6. **C6 时间** — Fig.7  
7. **C7 消融** — Table 5（若有）  

### 3.6 出图文件名（实验齐了再画）

`paper/chunk_tevict/figures/`：

- `fig1_mismatch.pdf`（tikz 已在 tex 里可不再导出）  
- `fig2_2reg_cost_ratio.pdf`  
- `fig3_2reg_storage_transfer.pdf`  
- `fig4_3reg_types.pdf`  
- `fig5_scale_369.pdf`  
- `fig6_2reg_get_rtt.pdf`  
- `fig7_cdc_replay_time.pdf`  

脚本：`simulation/plot_chunk_tevict_paper.py`，读 M1/M2 的 `summary_all.json`。旧 `fig5_*heatmap*.png` 成稿删掉。


---

## 4. 出图脚本约定

在 `simulation/analyze_fig5_matrix.py` 旁加 `plot_chunk_tevict_paper.py`（实验齐了再写也行），输入 `summary_all.json` + 延迟 JSON，输出 `paper/chunk_tevict/figures/fig2_2reg_ratio.pdf` 等 **矢量 PDF**（不要只 png）。

分母一律 **ChunkTevict**（初稿已如此）。旧 DedupAware 不得进图例。

---

## 5. 实验齐了之后怎么改论文（FAST 节结构）

保持：I Intro → II Related → III Problem → IV Design（Algorithm 1）→ **V Eval** → VI Conclusion。

§V 改成：

- **A. Datasets：** 五条 IBM trace + types A–E；补上 T29 一句（写重大、对象小）。
- **B. Metrics：** (i) 存储+传输，无 request；(ii) 同一趟仿真 GET/PUT RTT avg/p90/p99；(iii) CDC MB/s + replay wall。写 8 vCPU。删 “Latency TBD”。
- **C. Results：** C1 Fig.2 总费用 → C2 Fig.3 存储/传输 → C3 Fig.6 延迟（同 M1）→ C4 Fig.4 三区类型 → C5 Fig.5 3/6/9 → C6 Fig.7 时间 → C7 消融表。
- 删现在的 “C5–C7 Not in this draft” 列表。
- Abstract / Intro 第三条贡献补 T29、6/9、延迟数字（有多少写多少）。
- Conclusion 不再写 “matrix omits T29…”。

页数目标：IEEE 双栏 **7–8 页**（FAST 是 8 页）。现有 6 页 + Fig.5–7 刚好。
