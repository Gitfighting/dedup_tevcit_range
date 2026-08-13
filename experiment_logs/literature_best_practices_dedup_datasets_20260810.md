# 文献最佳实践：去重评测数据集、映射与 pad（联网检索纪要）

> **记录时刻**: 2026-08-10 08:52 CST（UTC+8）  
> **检索主题**: CDC/去重实验的数据集构建、真实内容 vs trace、是否允许 pad、顶尖论文做法  
> **关联**: `pool_remap_epsilon_billing_20260810.md`（计费口径）；同类型字节 pad 讨论

---

## 1. 顶尖论文 / 社区“金标准”做法（共识）

### 1.1 内容侧：用「连续版本、同族真实数据」

顶会评测几乎从不靠“按 size 随便凑文件”，而是用**高版本间冗余**的真实语料：

| 工作 | 出处 | 数据集做法 |
|------|------|------------|
| **FastCDC** | Xia et al., **USENIX ATC’16** | GCC 多版本 tar、网站多日快照、VM 镜像序列、Redis 备份、合成备份操作 [1] |
| FastCDC 扩展 | Xia et al., **IEEE TPDS’20** | 同上类：源码/VM/DB 等，总量 TB 级 [2] |
| JC chunking | **IEEE TPDS’23** | DOC / 连续日 NEWS / **108 版 GCC tar** / Linux 发行版 qcow2 / VM 备份 [3] |
| CDC 全面复现研究 | arXiv:2409.06066（2024） | 刻意选“文本或**连续版本增量相关**”的数据；另设 RAND 作对照；同规模约 10 GiB [4] |
| FSL / Destor 生态 | Tarasov et al. **ATC’12**；FSL Homes/MacOS；Destor | 真实文件系统多日快照；连续 snapshot 评备份去重 [5][6] |
| DedupBench / VectorCDC | Middleware’24 / **FAST’25** 相关工具链 | 标准公开集（如 DEB VM images）+ 可复现参数 [7] |

**共识一句话**：去重率实验的“优秀做法”= **同类型连续版本真实内容**（GCC/linux/VM/website），让 CDC 看到真实冗余；不是跨类型乱拼，更不是全零填充。

这与我们的「linux+linux / php+php」方向一致，且被 ATC/FAST/TPDS 反复采用。

### 1.2 Trace 侧：IBM Object Store 的本意

IBM Object Store traces（Eytan et al., **HotStorage’20**，SNIA IOTTA）提供的是 **PUT/GET 时间与对象 size 元数据**，原论文用于缓存策略（LRU vs FIFO 等），**并不附带对象真实字节内容** [8]。

因此社区常见拆法是：

- **Access/size workload** ← IBM（或其它）trace  
- **Content/dedup ratio** ← FSL / GCC / VM 等真实语料  

我们做的「用真实文件近似填满 trace size」是**混合实验设计**，必须在论文中显式声明，不能暗示“IBM 桶里原来就是这些 linux 源码”。

### 1.3 关于 “pad”

文献里 **FastCDC 的 zero-padding** 指的是 **hash mask 里补零比特**（算法技巧），**不是**把文件末尾填 `\\0` [1][2]。

对评测内容：

- **全零 pad**：会产生大量相同空块，严重抬高/扭曲去重率 → **应视为差实践**（评测污染）。  
- **同语料真实字节 pad**（从同版本树拷贝字节补长度）：文献中少有专门命名，但是工程上合理，且仍属“同族真实内容”；**必须在论文 Methods 披露**，并报告 pad 占比。  
- **简单拼接同族文件成流**：CDC 复现研究常用 concatenation，并警告跨文件切块边界与真实按文件去重的差异 [4] —— 我们的 tar/组合本质类似，需同样披露。

### 1.4 参数与可复现（反“粗制滥造”）

**BigData’24 *Common Pitfalls with Data Deduplication Parameters and Metrics*** [9]：

- 不可只报 “expected chunk size”，必须报齐 min/avg/max、mask 等低层参数；  
- 同 expected size、不同参数 → 去重率可不同；  
- 参数应对**每个数据集**调，不能一套打天下；  
- 文件边界会导致理论 chunk 分布与观测分布偏离。

优秀做法：固定 FastCDC 参数表，对 linux/php/ubuntu **分别报告**去重率与 chunk 分布，而不是只给一个总数。

---

## 2. 对我们问题的直接启示

### 2.1 “同类型字节 pad 补到目标”算不算优秀？

相对排序（从优到劣）：

1. **对象本身已是真实文件且 size 自然等于需求**（理想，常不可得）  
2. **同族多版本组合 + 少量同族真实字节补齐到 \(S_{trace}\)**（工程最优可复现；对齐 ATC 语料哲学）  
3. **同族组合 + 10MB/1KB 容差、不 pad**（可接受，需报误差分布）  
4. **异类型拼包**（削弱跨对象去重信号）  
5. **全零 / 随机无结构填充**（评测污染，差）

结论：同类型字节 pad **符合“真实同族内容”精神**，优于 trim 穷举；但要写清 pad 比例，避免审稿人质疑造假 size。

### 2.2 计费用 trace size 还是 file size？

文献无统一“映射后口径”，但物理系统论文（备份/云存储）费用与吞吐一律按 **实际字节**。  
结合基线对比需求，维持此前结论：

- 时间线 / obj_key / 访问模式 ← trace  
- 存储费 / 运费 / CDC ← \(S_{file}\)  
- 附录：\(S_{trace}\) 计费灵敏度  

### 2.3 1KB vs 10MB vs 精确

顶会不去卷 “映射误差 1KB”，因为他们通常**不做 IBM size→文件映射**。  
对我们的混合设计：

- **\(\varepsilon=0\)（精确）**：用同族 pad 一次收口，比反复整包 trim 更“优秀、可复现”。  
- **\(\varepsilon=1\\mathrm{KB}\)**：对 GB 级对象与精确几乎等价，实现略松。  
- **\(\varepsilon=10\\mathrm{MB}\)**：相对误差对 12GB 约 0.08%，费用上常可忽略；当前未 100% 是**选料粒度**问题，不是窗口太严。

---

## 3. 推荐采用的“最佳实践组合”（可执行）

1. **内容池**：坚持同类型连续版本（linux / php / ubuntu），对齐 FastCDC/FSL 传统。  
2. **索引**：并行重建 path→size + L1/L2 缓存（提速，不降质量）。  
3. **大缺口收口**：层级选料使 \(S_0 \\le S_{trace}\) → **一次 tar** → **同族字节 pad 至 \(\\varepsilon\\in\\{0,1\\mathrm{KB}\\}\)**；禁止多路僵尸 partial。  
4. **披露**：覆盖率、\(\\varepsilon\)、pad 字节占比、FastCDC 全参数、分数据集去重率。  
5. **对照**：可选 RAND/异类型消融，证明同类型假设的必要性（学习 [4] 的 RAND 对照）。

---

## 4. 主要参考文献（链接）

1. Xia et al., FastCDC, USENIX ATC’16 — https://www.usenix.org/system/files/conference/atc16/atc16-paper-xia.pdf  
2. Xia et al., FastCDC design, IEEE TPDS’20  
3. JumpCondition CDC, IEEE TPDS’23  
4. Thorough Investigation of CDC, arXiv:2409.06066 — https://arxiv.org/abs/2409.06066  
5. Tarasov et al., Generating Realistic Datasets for Deduplication Analysis, ATC’12；FSL — https://tracer.filesystems.org/  
6. Destor — https://github.com/fomy/destor  
7. DedupBench / VectorCDC 工具链 — https://github.com/UWASL/dedup-bench  
8. IBM Object Store traces, HotStorage’20；SNIA IOTTA  
9. Randall & Lu, Common Pitfalls with Data Deduplication Parameters and Metrics, IEEE BigData’24  

---

*End of search memo — 2026-08-10 08:52 CST*
