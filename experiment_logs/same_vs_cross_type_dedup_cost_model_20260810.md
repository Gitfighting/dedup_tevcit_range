# 同类型 vs 异类型组合对传统去重与 DedupAwareTevict 的影响（含建模）

> **记录时刻**: 2026-08-10 07:08:39 CST（UTC+8）  
> **作者**: CloudSun / Cursor Agent  
> **关联**: `experiment_logs/dedup_aware_tevict_strategy_20260809.md`  
> **上下文**: T079 真实文件映射接近收尾；填充进程并行尝试关闭最后大缺口；讨论映射阶段「同类型拼包 vs 异类型拼包」对 CDC 去重与费用的影响。

---

## 0. 当时填充进度快照（2026-08-10 07:08 CST）

| 项 | 状态 |
|----|------|
| T079 覆盖 | **12580 / 12582（约 99.98%）**，缺口 **2** 个：11.390G、12.229G |
| 磁盘 `/data` | 1.6T 总量，约 **171G** 空闲（89%） |
| `fill_same_type` | **仍在运行**：11.390G 已 FAIL（linux 偏大 / ubuntu·php 偏小）；正在试 12.229G 的 linux 组合 |
| `fill_linux_pad_parallel` | **已崩溃**（`Can't pickle local object scan_linux.<locals>.sz`）；需修复后重启 |
| 并行意图 | 同机 8 vCPU / 32GB，两套路并行以缩短占机时间 |

---

## 1. 设定与符号

| 符号 | 含义 |
|------|------|
| \(S\) | 对象逻辑大小（字节） |
| \(c\) | 平均 chunk 大小（FastCDC，约 8KB） |
| \(n = S/c\) | 对象 chunk 数 |
| \(\rho\) | **跨对象去重率**：新对象中与索引已有指纹重合的比例 |
| \(R_w, R_g\) | 写入区 / 读区 |
| \(P_s\) | 存储单价 |
| \(P_n\) | 跨区 egress 单价 |
| \(T_{\mathrm{even}}\) | 热副本缓存 TTL |
| \(\Delta t\) | 预估下次访问间隔 |
| \(\alpha\) | 组装时需跨区拉取的块比例 |
| \(\beta\) | 读区已缓存块比例（使 \(\alpha_2 = 1-\beta\)） |

**同类型 vs 异类型（映射阶段 tar 怎么拼）：**

- **同类型**（linux+linux / php+php / ubuntu+ubuntu）：同源或相邻版本，CDC 切点稳定 → \(\rho_{\mathrm{same}}\) 高（内核相邻版常 \(\rho \gtrsim 0.9\)）。
- **异类型**（linux+ubuntu+ova 混打）：内容几乎无关 → \(\rho_{\mathrm{cross}} \approx 0\)。

映射只保证 size ±10MB，**不改变 IBM 访问时间线**；去重只看字节内容。

---

## 2. 索引表在哪里

与 `DedupAwareTevict` 设计一致：

```
每 region 本地（推荐不做全局跨 region 共享索引）:
  Bloom (内存)     → 快速否定
  LMDB             → fp → (region, offset, size)   # 精确块索引

控制面 / 对象元数据:
  obj_key → chunk_manifest = [fp₁, fp₂, …]
  state ∈ {DEDUP_STATE, HOT_STATE}
  replica_region, TTL
```

- **块指纹索引**：存储侧各 region 的 Bloom + LMDB。  
- **对象→块列表 + Tevict 状态**：控制面。  
- GET/PUT 决策读 manifest + TTL，不必每次全盘扫描。

---

## 3. 首次 PUT（写入区 \(R_w\)）

流程：FastCDC → 查本地索引 → 新块落盘，旧块只加引用。

\[
W_{\mathrm{put}} = (1-\rho)\, S
\]

\[
C^{\mathrm{stor}}_{\mathrm{put}} = P_s \cdot (1-\rho)\, S \cdot \tau_{\mathrm{hold}}
\]

| 组合方式 | 典型 \(\rho\) | 首次 PUT 物理写入 |
|----------|---------------|-------------------|
| 同类型 | \(\rho_{\mathrm{same}}\) 大 | \((1-\rho_{\mathrm{same}})S\) |
| 异类型 | \(\rho_{\mathrm{cross}}\approx 0\) | ≈ \(S\) |

对象进入 **DEDUP_STATE**：有新块 + manifest，**没有**整文件第二份明文副本。

---

## 4. 首次 GET（读区 \(R_g \neq R_w\)）

按 manifest 取块组装；跨区块产生运费：

\[
C^{\mathrm{net}}_{\mathrm{get1}} = P_n \cdot \alpha \cdot S
\]

首次在 \(R_g\) 通常 \(\alpha \approx 1\)（本地尚无这些块）。同/异类型对「这一次拉取体积」往往都接近 \(S\)；差别在**库级总块数**与后续跨对象命中。

### 4.1 热路径：\(\Delta t < T_{\mathrm{even}}\)

1. 付组装运费 \(C^{\mathrm{net}}_{\mathrm{get1}}\)  
2. 在 \(R_g\) **物化完整副本** → **HOT_STATE**  
3. 副本保留至 TTL：

\[
C^{\mathrm{stor}}_{\mathrm{hot}} = P_s \cdot S \cdot T_{\mathrm{even}}
\]

### 4.2 冷路径：\(\Delta t \ge T_{\mathrm{even}}\)

只组装返回，**不**留完整副本，保持 DEDUP_STATE；运费仍为 \(C^{\mathrm{net}}_{\mathrm{get1}}\)。

---

## 5. 第二次 GET（同一 \(R_g\)）

### 5.1 HOT 未过期

\[
C^{\mathrm{net}}_{\mathrm{get2}} = 0
\]

读**本地整文件物理副本**，不是「再记一套引用」。

### 5.2 已回 DEDUP（或首次即冷）

再次按块组装：

\[
C^{\mathrm{net}}_{\mathrm{get2}} = P_n \cdot \alpha_2 \cdot S,\quad \alpha_2 = 1-\beta
\]

| 实现 | 二次 GET |
|------|----------|
| 仅组装、块不落 \(R_g\) | 再付 ≈ \(P_n S\) |
| 组装后块写入 \(R_g\) chunk store | 运费 \(P_n(1-\beta)S\)；存储 \(+P_s\beta S\) |
| HOT 未过期 | 运费 0；已付 \(P_s S T_{\mathrm{even}}\) |

**同类型红利**：若 \(R_g\) 已有同系列其它对象的块，则 \(\beta\)（乃至首次 \(\alpha\)）更易下降。异类型几乎无此红利。

---

## 6. 费用模型对比

对「1×PUT @ \(R_w\) + \(k\)×GET @ \(R_g\)」：

**AlwaysEvict + Dedup（不留 HOT）：**

\[
C_{\mathrm{AE}} = P_s(1-\rho)S\,\tau + k\, P_n S
\]

**AlwaysStore + Dedup：**

\[
C_{\mathrm{AS}} = P_s(1-\rho)S\,\tau + P_n S + P_s S\, T_{\mathrm{life}}
\]

**DedupAwareTevict（一次 HOT，窗内命中 \(m\) 次）：**

\[
\begin{aligned}
C_{\mathrm{DAT}} &= P_s(1-\rho)S\,\tau + P_n S + P_s S\, T_{\mathrm{even}} \\
&\quad + m\cdot 0 + (k-1-m)\, P_n S
\end{aligned}
\]

（末项假设过期后块未本地化。）

同类型 \(\rho\uparrow\) 主要压缩 PUT 存储项；运费由 Tevict/HOT 与 \(\beta\) 决定。异类型 \(\rho\approx 0\) 时去重几乎不省存储，还增加 CDC/索引开销。

**HOT 划算条件（相对每次全量跨区）：**

\[
T_{\mathrm{even}} < \frac{P_n}{P_s}(k-1)
\]

---

## 7. 实验含义（消融假设）

| 维度 | 同类型组合 | 异类型组合 |
|------|------------|------------|
| 跨对象 \(\rho\) | 高 | ≈0 |
| 首次 PUT 物理写 | \((1-\rho)S\) | ≈\(S\) |
| 索引条目 | 少（指纹复用） | 多 |
| 首次跨区 GET | ≈\(P_n S\)（块未在本地） | 同左 |
| 二次 GET（HOT 内） | 0 运费，读物理副本 | 同左 |
| 二次 GET（DEDUP+块本地化） | \(\beta\) 易升 | \(\beta\) 难升 |
| 存储费 | 明显下降 | 几乎不降 |
| 运费 | 靠 Tevict；同类型有块命中红利 | 几乎只靠 Tevict |

**映射建议：** 为体现传统去重收益，大对象应优先 **同类型拼包**。异类型仅适合填 ±10MB size，但会稀释去重实验信号。

**状态机对齐：**

1. 首次 PUT：按块存于 \(R_w\)；对象 = manifest 引用。  
2. 首次 GET：远端拉块组装；热 → 再存整文件物理副本并设 TTL；冷 → 只返回、保持引用。  
3. 二次 GET：HOT 内读物理副本；DEDUP 则按引用再组装（本区有块则少拉）。  
4. 索引：Bloom+LMDB 在 region；manifest/TTL 在控制面。

---

## 8. 后续动作（本时刻记录）

1. 将本分析固化于本日志文件。  
2. 修复并重启 `fill_linux_pad_parallel`（pickle 问题）。  
3. 保留 `fill_same_type` 继续试 12.229G。  
4. 缺口关闭后重建/校验 `pool_sizes.npy`，再跑五迹映射确认。

---

*End of entry — 2026-08-10 07:08:39 CST*
