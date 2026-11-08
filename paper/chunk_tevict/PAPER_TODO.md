# 论文待完善（ChunkTevict）

对照：SkyStore §6 提问方式；FAST/Ye Datasets → Metrics → 按图串讲。  
实验盘点：[`EXPERIMENT_STATUS.md`](EXPERIMENT_STATUS.md)（2026-10-21）。

稿件已经有完整骨架（Intro / Related / Motivation / Design / Eval / Conclusion），**不是空白**，但还不能当终稿。

---

## A. 数字与表格（先做，和云上 JSON 对齐）

- [x] Fig.6 type A 补上 T29（3/6/9 CT $0.357 / $0.615 / $0.853）；五条均值 6/3=$1.53\times$、9/3=$2.06\times$（`eval.tex` 2026-10-21）。
- [ ] **Table 5 / 三区 FP 均值用五条迹重算**（T29 type C Sky/CT ≈ $14.3\times$，会拉高 type C 列；abstract 里 $4.25$–$4.61\times$ 仍是「四条轻迹」）。
- [ ] 6/9 区 FP：CT/Sky/Evict/Store 已有 T29，补进正文或附录；JuiceFS 9 区 T29 标明缺失。
- [ ] C1–C4 的绝对美元与云上 JSON 再对一遍（2/3 区 FB 不应改；只防誊写误差）。
- [ ] Abstract / Intro / Conclusion 的「四条迹 / T29 尚未」句全部删掉或改成现数。

## B. 必须补的实验叙事（否则审稿易打回）

- [ ] **M3 消融表**：直方图 `unique_bytes` vs `|o|`；per-uid TTL vs 整对象一条。没有这张表，贡献会被说成「只换了计费单位」。
- [ ] 写清 **TTL 适配**（见下节口径）：TevictRanges 的 `get_tevict` **公式不改**；适配发生在「对谁调用、租约挂在谁身上」。
- [ ] 仿真 RTT ≠ SkyStore Table 6：已有一句，相关工作/评测开头再钉死一次。
- [ ] JuiceFS 9 区 T29：要么独占重跑，要么正文一句「OOM，未报」。

## C. 结构与文风（对标 FAST / SkyStore，未完成）

- [ ] 主图改成 **分组柱**（trace 或区数为横轴，系列 ≤ Sky+CT+少数基线）。现在 `fig5_3reg_table4.png` 仍偏热力/拥挤。
- [ ] 每个数据集一段（已有雏形）：T29 6/9 增长、FP 比 FB 贵，要写进 C5，不要只留在备忘。
- [ ] Related：CDC / FastCDC / 对象存储 TTL 各收紧到与贡献直接相关的几段。
- [ ] Design：Algorithm 1 与 `aware_replay.py` 的 FB pin / FP freeze 对齐；不要把 `min_live` 当新 TTL 算法卖。
- [ ] 页数：IEEE 会议双栏，砍掉实验室口吻（JSON 路径、日期、`/data`）。
- [ ] Prototype（`prototype/dedup/`）**不要**写成评测过的在线系统，除非补了本地延迟实验。

## D. 明确不写进主张

- 真三云客户端延迟、vs AWS API。
- 抽样 CDC 的 $0.186$ MB/s 当峰值吞吐。
- 「我们发明了新的 $T_{\mathrm{evict}}$ 公式」。

---

## TTL 口径（写进 Design 时用这句话）

SkyStore `SimulatorV2.calculate_ttl`：在 **对象** 的其它副本上取 `get_tevict(src, dst)`，取仍能覆盖 `now+T` 的最小 $T$；没有其它副本则用 median $T_{\mathrm{even}}/3$。

ChunkTevict：**同一函数、同一直方图**（`TevictRangesV2.get_tevict`），但

1. 直方图更新的 size 换成该对象的 unique-chunk 字节；
2. 每个 **uid、每个区域** 各有一条租约（`RegionChunkState.chunk_exp`）；
3. `min_live_ttl` 在 **持有该 uid 的区域** 上做作者那次 min，而不是在「持有整个对象」的区域上。

不修改 `policy_tevict_ranges_new.py`。
