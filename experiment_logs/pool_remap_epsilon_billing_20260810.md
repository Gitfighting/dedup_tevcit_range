# 文件池重建、映射误差与实验计费口径（方法论）

> **记录时刻**: 2026-08-10 08:36:30 CST（UTC+8）  
> **关联**: T079 真实文件映射；DedupAwareTevict / 传统 CDC 去重实验  
> **当时状态**: T079 ≈ 12580/12582；剩 11.390G、12.229G；层级拼包仍在 trim；盘约 93% / 剩 ~123G

---

## 1. 核心问题（用户关切）

1. 多轮下载+打包后文件池在膨胀，如何**重新建立文件池索引**（可并行），又能**提速且不粗制滥造**？  
2. 映射误差能否从 **10MB** 收到 **1KB** 甚至字节级？  
3. 映射做不到与 trace `size` 分毫不差时，实验里计费/统计用 **trace 大小** 还是 **真实文件大小**？

---

## 2. 实验时应采用哪个“大小”？——结论先说

### 2.1 分工：两个量不要混用

| 量 | 来源 | 用途 |
|----|------|------|
| \(S_{\mathrm{trace}}\) | IBM/MC-aug trace 的 `size` 列 | 工作负载定义、对象身份、与论文基线**对齐对比** |
| \(S_{\mathrm{file}}\) | 映射后真实文件字节数 | 真实 PUT/GET 读写、CDC 分块、**物理存储费/运费** |

映射关系是：

\[
\mathrm{obj\_key} \mapsto \mathrm{path},\quad |S_{\mathrm{file}}-S_{\mathrm{trace}}| \le \varepsilon
\]

### 2.2 推荐口径（可写进论文）

**混合口径（推荐）：**

1. **回放逻辑仍按 trace**：何时 PUT/GET、哪个 `obj_key`、哪个 region，完全跟 trace。  
2. **字节与费用按真实文件**：上传/下载/落盘/CDC 用 \(S_{\mathrm{file}}\)；存储费、跨区运费用实际传输字节。  
3. **报告时同时披露**：
   - 映射覆盖率（unique PUT keys 命中率）  
   - \(\varepsilon\)（如 10MB / 1KB / 0）  
   - \(\sum |S_{\mathrm{file}}-S_{\mathrm{trace}}|\) 与相对误差分布  
   - 灵敏度：若把计费强行改成 \(S_{\mathrm{trace}}\)，总成本相对变化多少（附录一张表即可）

**不要**只采用“全程假装文件就是 \(S_{\mathrm{trace}}\)”却读真实文件——那会让去重/CDC 与费用脱节。  
**也不要**改写 trace 的 size 去迁就文件——会破坏与基线 SkyStore（无真实文件）的可比性。

### 2.3 为何 10MB 仍可接受（相对意义）

对 12GB 级对象，10MB 相对误差：

\[
\frac{10\times 10^6}{12\times 10^9} \approx 0.083\%
\]

对费用主项（GB 级运费）通常是噪声量级；但对**极小对象**（KB～MB）10MB 窗口过大，应分层设 \(\varepsilon\)：

| 对象量级 | 建议 \(\varepsilon\) |
|----------|----------------------|
| &lt; 1MB | 0（精确）或 ≤ 1KB |
| 1MB–100MB | ≤ 64KB～1MB |
| 100MB–1GB | ≤ 1MB～4MB |
| &gt; 1GB | ≤ 10MB（当前）；目标收敛到 ≤ 1MB 或精确 pad |

### 2.4 1KB / 精确匹配的定位

- **科学上最干净**：\(S_{\mathrm{file}} = S_{\mathrm{trace}}\)（\(\varepsilon=0\)）。  
- **工程上**：对大对象用「同类型层级组合 + **同类型内容 pad 到精确字节**」可达 \(\varepsilon=0\)，无需穷举所有子集。  
- **1KB**：比 10MB 严两个数量级，对 GB 级对象几乎等价于“费用上无差别”，又比字节精确略好实现（pad 对齐到 1KB 边界即可）。  
- 当前 10MB 尚未 100% 覆盖，主因是**大缺口材料粒度粗**（整棵 linux ~1GB）+ tar 头开销，不是“10MB 太松”；收紧到 1KB **必须**上 pad/层级，而不是只收紧验收阈值。

---

## 3. “重建文件池”应重建什么？

不是把数据再下一遍，而是重建**可增量、可并行的索引与映射流水线**：

```
/data/file_pool/          # 内容本体（已有）
/data/pool_index/         # 新建：持久化索引（建议）
  ├── sizes.lmdb          # path → (size, mtime, ino, type)
  ├── sizes_sorted.npy    # 仅 size 多重集，供 greedy 映射
  ├── inventory.jsonl     # 人类可读清单
  └── hierarchy/          # linux 版本 → L1/L2 子目录 size 缓存
```

原则：

- **内容只增不瞎删**（成功 mapped_tars / 源树保留策略另议）。  
- **索引可随时从盘重建**，且支持增量（mtime/ino 未变则跳过 `du`）。  
- **映射是计算问题**，池扩建后应 *reindex → remap*，而不是每次全盘 `du`。

---

## 4. 如何提速且不粗制滥造

### 4.1 并行重建索引（推荐）

1. `os.scandir` 列出 `file_pool` 顶层 + `linux_kernel` + 需要的层级。  
2. **进程池**（8～16 worker）对目录做 `du -sb` 或等价遍历；结果写入 LMDB。  
3. 二次任务：对 linux 树缓存 L1/L2 子目录 size（专供层级拼包）。  
4. 导出 `pool_sizes.npy`（多重集，保留相同 size 多文件）。

粗制滥造的反例（避免）：

- 随机截断文件冒充 size  
- 全零 pad 充当“内容”（CDC 实验信号被污染）  
- 删掉成功 `fill*.tar` 只为腾盘却不重建映射  

可接受的精确手段：

- **同类型内容 pad**（从同棵 linux 树读字节循环写入 pad 文件）到 \(S_{\mathrm{trace}}\) 或 \(S_{\mathrm{trace}}\bmod 1024\) 对齐  

### 4.2 映射算法提速

当前慢点：每次填洞都重新 `du` + 反复 `tar` 整包试错。

改进：

| 步骤 | 做法 |
|------|------|
| 选料 | 在**已缓存 size** 上做层级背包（整树→L1→L2），O(可并行 per-gap) |
| 打包 | 每个缺口 **一次** `tar`（目标略小） |
| 收口 | 同类型 pad 补到 \(\varepsilon\)（0 或 1KB），**禁止**几十轮整包 trim |
| 并行 | 缺口级并行（每进程一个 target）；索引只读共享 |
| 清理 | 失败只留一个 `.partial`；成功 `os.replace`；禁僵尸 `linuxpad` 多路狂写 |

这样提速来自**少做无效 tar**，不是降低匹配质量。

### 4.3 精确 / 1KB 的标准收口公式

设层级选出的路径集合内容合计（或一次 tar 后）为 \(S_0\)，且 \(S_0 \le S_{\mathrm{trace}}\)：

\[
S_{\mathrm{pad}} = S_{\mathrm{trace}} - S_{\mathrm{tar}} - H
\]

其中 \(H\) 为再打入 pad 成员引入的 tar 头（可小范围搜索 512…64KB）。  
pad 字节取自同类型文件 → **类型纯度保留**，CDC 仍有意义。

对 \(\varepsilon=1\mathrm{KB}\)：允许 \(|S_{\mathrm{file}}-S_{\mathrm{trace}}|\le 1024\)，pad 搜索更容易。

---

## 5. 与对比实验的关系

- **无真实文件的基线**（原 SkyStore）：全程 \(S_{\mathrm{trace}}\)。  
- **有真实文件 + 去重**：物理层必须用 \(S_{\mathrm{file}}\)；策略层（TTL 学习等）可继续用 trace 时间与访问模式。  
- 公平对比写法示例：

> Workload timing and object keys follow IBM traces. Object payloads are real files mapped under tolerance \(\varepsilon\). Storage and egress costs are charged on actual bytes. We report cost sensitivity when substituting \(S_{\mathrm{trace}}\) for \(S_{\mathrm{file}}\).

---

## 6. 当前映射现实与下一步

- 10MB 窗口下仍非 100%，卡在少数 **&gt;11GB** 缺口；根因是整树粒度与反复 trim，不是窗口太松。  
- 下一步应用：**索引重建（并行）+ 层级选料 + 单次 tar + 同类型精确/1KB pad**，而不是继续整包 trim 烧盘。  
- 计费口径按 §2.2 固定，避免后期改口径导致实验不可比。

---

## 7. 待执行清单（需用户确认后再动盘）

1. 清理僵尸 `.partial` / 无主 `linuxpad` tar 进程，回收空间。  
2. 并行重建 `/data/pool_index`（sizes + linux L1/L2）。  
3. 对剩余大缺口改用「一层级组合 + 同类型 pad→0 或 1KB」。  
4. 五迹统一 remap，导出覆盖率与误差分布表。  
5. 实验代码：费用路径读 `S_file`；trace 表保留 `S_trace`。

---

*End of entry — 2026-08-10 08:36:30 CST*
