# ChunkTevict 实验对照清单（相对 SkyStore VLDB 2025）

记录日期：**2026-10-21**（SSH 盘点云主机 `118.31.166.174`）。  
对照 SkyStore §6（Fig.5 / Table 3–5 / Fig.6 / Table 6–7）。

机器：8 vCPU、29 GiB，当时 **空闲**（load≈0，无 `main.py` / `run_remaining`）。  
T29 补跑于 **2026-09-19 09:39** 结束：`ok=115/124`。之后未再开新 job。

结果目录（云）：`/data/sim_exp/python_simulation/results_fig5_matrix/`  
快照：同目录下的 `paper/chunk_tevict/matrix_status_oct21.json`（本机从云拉取）。

---

## 矩阵完成度（论文要对的费用格）

不含 2 区 FP（SkyStore Fig.5 没有）。含 Optimal 的 2 区 Table 3。

| 集合 | 完成 | 缺 |
|---|---|---|
| FB 2reg Fig.5（8 策略 × 5 迹） | **40/40** | 0 |
| FB 3reg Table 4（A–D × 8 × 5） | **160/160** | 0 |
| FB 6reg A–D | **160/160** | 0 |
| FB 9reg A–D | **156/160** | **4**：JuiceFS (`replicate_all`) T29 types A–D |
| FP 3reg Table 5 集合 | **100/100** | 0（含 T29 CT A–D） |
| FP 6reg | **99/100** | **1**：JuiceFS T29 type A |
| FP 9reg | **96/100** | **4**：JuiceFS T29 A–D |
| 2reg Optimal | **5/5** | 0 |
| **合计（上表）** | **811/820** | **9** |

缺的 9 格 **全是 JuiceFS T29、6/9 区**。9 月 10–14 日多次 OOM（单进程 RSS 曾到 ~21 GiB）。ChunkTevict / SkyStore / Evict / Store 的 T29 **包括 6/9 区 FB 与 FP 均已齐**。

不要跑 SPANStore、真三云 Table 6、vs AWS API、2 区 FP。

---

## 与 SkyStore §6 的对应

| SkyStore | 我们要回答的问题 | 状态（2026-10-21） |
|---|---|---|
| §6.1 / Table 2 | 五条 IBM 轨迹特征 | **不必跑**，引用作者表 |
| §6.3 **Fig.5** | 两区 FB | **已齐**（费用 + 仿真 RTT） |
| §6.3 **Table 3** | 两区 vs 对象级 Optimal | **已齐** |
| §6.4 **Table 4** | 三区 FB types A–D | **已齐**（160/160） |
| §6.5 **Fig.6** | 3/6/9 区 type A，费用 / 自己的三区费用 | **五条迹 CT 与 Sky 已齐**（含 T29：3/6/9 = $0.357 / $0.615 / $0.853）。正文 Table scale **已补 T29**。9 区 JuiceFS T29 type A 仍缺，不影响 CT/Sky 的 Fig.6 |
| §6.5 **Table 5** | 3/6/9 区 FP：Evict / Store / JuiceFS + Sky / CT | **3 区五条已齐**；6 区缺 JuiceFS T29-A；9 区缺 JuiceFS T29 全部。**eval 里 Table 5 均值仍按「四条迹」写，尚未用 T29 重算** |
| §6.6 Table 6 | 真三云客户端延迟 | **不做**（仿真 RTT 已在同一趟回放） |
| §6.7 Fig.7 | vs AWS API | **不做** |

---

## 关键费用（云上 JSON，USD without request）

### Fig.5 两区 FB：Sky / CT

| Trace | SkyStore | ChunkTevict | Sky/CT | 存储占 gap |
|---|---:|---:|---:|---:|
| T15 | 92.02 | 25.22 | 3.65× | 96.7% |
| T29 | 4.62 | 0.39 | 11.80× | 95.1% |
| T65 | 4.39 | 2.01 | 2.18× | 96.5% |
| T78 | 16.79 | 2.50 | 6.71× | 87.8% |
| T79 | 337.02 | 51.92 | 6.49× | 92.6% |

### Fig.6 type A FB：ChunkTevict 3/6/9

| Trace | 3-reg $ | 6-reg $ | 9-reg $ | 6/3 | 9/3 |
|---|---:|---:|---:|---:|---:|
| T15 | 36.69 | 46.40 | 53.24 | 1.26× | 1.45× |
| T29 | 0.357 | 0.615 | 0.853 | 1.72× | 2.39× |
| T65 | 3.06 | 5.79 | 8.68 | 1.89× | 2.84× |
| T78 | 4.22 | 5.83 | 7.46 | 1.38× | 1.77× |
| T79 | 71.18 | 99.21 | 130.65 | 1.39× | 1.84× |
| **五条均值** | — | — | — | **1.53×** | **2.06×** |

SkyStore 同表 T29 几乎不随区数涨（$5.35 → $5.50 → $5.67）；五条均值 6/3 ≈ **1.25×**、9/3 ≈ **1.52×**（原先四条均值 1.30 / 1.63）。

### 3 区 FP T29（Table 5 集合，现已齐）

| type | Evict | Store | JuiceFS | SkyStore | ChunkTevict | Sky/CT |
|---|---:|---:|---:|---:|---:|---:|
| A | 5.27 | 7.38 | 13.21 | 5.30 | **1.24** | 4.28× |
| B | 5.27 | 6.10 | 13.21 | 4.58 | **0.91** | 5.05× |
| C | 5.34 | 6.31 | 13.21 | 4.87 | **0.34** | **14.3×** |
| D | 5.27 | 7.38 | 13.21 | 5.30 | **1.24** | 4.28× |

科学现象（不是算错）：T29 上 CT 的 **FP 往往贵于 FB**（type A：$1.24 vs FB $0.36），对象级 Sky 则略便宜。Origin pin 对 T29 块复用很值钱。Type C 的 Sky/CT 被 CT=$0.34 拉高，**重算五条平均前不要直接改 abstract 的 4.25–4.61×**。

---

## 还要跑的实验

### 费用矩阵（可选，9 格）

全是 **JuiceFS T29、6/9 区**，历史 OOM。只为和 SkyStore Table 5 的 JuiceFS 行完全对齐。

| ID | 格子 | 建议 |
|---|---|---|
| J1 | 6 区 FP type A JuiceFS T29 | 独占整机，不要并行 |
| J2 | 9 区 FB JuiceFS T29 A–D | 同上；FB 9 区 type A 已有其它策略 |
| J3 | 9 区 FP JuiceFS T29 A–D | 同上 |

**ChunkTevict 自身数字不依赖这 9 格。** 正文可以写 JuiceFS 9 区 T29 因内存未报。

### 机制消融（论文缺口，P0）

| ID | 内容 | 用途 |
|---|---|---|
| **M3** | 2 区 T65+T78：直方图权重 `unique_bytes` vs `|o|`；TTL 单位 per-uid vs 整对象一条 | 证明省钱来自粒度，不是换了 TevictRanges 公式。**尚未实现入口、尚未跑** |

### 已做 / 明确不做

| ID | 内容 | 状态 |
|---|---|---|
| M4 CDC | 抽样 + 索引字节 | 已有 `cdc_sample.json`。禁止把抽样 **0.186 MB/s** 当峰值写进论文 |
| Table 6 / Fig.7 | 真三云、AWS API | **不做** |
| 2 区 FP | Fig.5 没有 | **不跑** |

---

## 论文待完善（比缺实验更急）

详见 [`PAPER_TODO.md`](PAPER_TODO.md)。摘要：eval 已补 Fig.6 的 T29；Table 5 五条平均、M3 小表、分组柱图、TTL 表述、结论数字同步仍待做。

## 调度备忘（若重跑 JuiceFS）

- 对象级 T29 两路曾把 29 GiB 吃满；9 区 JuiceFS T29 RSS ~21 GiB。
- **JuiceFS 6/9 T29 必须独占**；不要和 CT 或第二路对象级并行。
- 不要改 `policy_tevict_ranges_new.py` / `simulator_v2.py` 核心循环。
