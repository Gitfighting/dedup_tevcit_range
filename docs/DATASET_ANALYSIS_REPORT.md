# SkyStore 仿真数据集分析报告

本文档将 **论文原文**（`skystore.pdf`）、**数据集构造代码**（`skystore_python/artifact-eval-main/simulation/SNIA_traces`）与本仓库中的 **`Data/`** 实际文件对应起来，便于后续做策略对比与实验设计。

---

## 1. 论文与数据的关系（`skystore.pdf`）

- **位置**：仓库根目录 `skystore.pdf`（SkyStore 相关论文全文）。
- **与数据的关系（概念层）**：
  - 论文中的多区域对象存储成本/放置仿真，需要 **带区域信息的请求轨迹** 与 **云厂商价目/VM 拓扑**；本仓库的 Java/Python 仿真器消费的是 **多区域 trace CSV**，而非 SNIA 原始单行文本。
  - **IBM / SNIA 对象存储轨迹**在论文与 artifact 中通常作为 **真实世界访问模式** 的来源：一周窗口内的 REST 操作序列，经脚本映射到多云区域后用于评估放置、迁移与纠删等策略。
- **说明**：PDF 内具体图表与 trace 编号请以原文为准；本报告以下各节以 **artifact 代码与 `Data/` 实际文件** 为准描述可复现特征。
- **论文对 T15/T29/T65/T78/T79 的专门归纳**（对象大小、冷热、突发、GET/PUT、时间拉伸与 Type A–E）：见 **`docs/SKYSTORE_PAPER_DATASETS.md`**。

---

## 2. 原始轨迹来源（SNIA / IBM）

依据 `skystore_python/artifact-eval-main/simulation/SNIA_traces/README.md`：

| 特征 | 说明 |
|------|------|
| **来源** | SNIA IOTTA 上的 IBM 对象存储轨迹（见 README 中的 SNIA 链接）。 |
| **粒度** | 共约 **98 条** trace，每条对应 **单个 bucket** 上 **2019 年同一自然周** 内的请求。 |
| **原始记录格式** | `时间戳(ms)`, `请求类型`, `对象 ID`, 可选 `大小`, 可选 `范围读起止` 等。 |
| **示例** | `1219008 REST.PUT.OBJECT 8d4fcda3d675bac9 1056` |

本仓库实验常用的命名形如 **`IBMObjectStoreTrace{NN}Part0`**（如 `029`、`015`、`065`），表示 IBM 公开轨迹中的某一 **Part0** 切片；不同编号代表 **不同 bucket / 不同访问形态**（读多写少、大对象为主等），需在对比实验时 **固定 trace 与区域数** 再比较策略。

---

## 3. 构造流水线（`SNIA_traces` 目录）

### 3.1 整体阶段

1. **原始 CSV（无区域）** → 按对象聚合（`parser.py` / `parser_data.py`）→ **pickle**（`obj_dict`：对象为 key，值为该对象上的操作序列）。
2. **多区域映射**（新版主路径：`new_trace_gen.py` + YAML；旧版/文档亦提及 `multi_cloud_trace.py`、`multi_cloud_trace_new.py`）→ 生成 **`*.mc`**：每行带 **`issue_region`** 的 PUT/GET 等。
3. **按时间排序**：`new_trace_gen.py` 内部对生成结果按 `timestamp` 排序后写出；`MULTI_TRACE_new.md` 中亦给出 `sort -t, -k1,1n` 的等价步骤。
4. **增强列（augment）**：`new_trace_gen.py --augment True` 在 `*.mc` 基础上生成 **`*.aug`**，供 **Simulator v2** 等快速利用“下一次访问”信息。

### 3.2 多区域配置（YAML）

- 配置文件位于 `yaml/`，VLDB 实验常用子目录：
  - `yaml/VLDB/3_reg/typeA.yaml`
  - `yaml/VLDB/6_reg/typeA.yaml`
  - `yaml/VLDB/9_reg/typeA.yaml`
- **`typeA`**：注释标明为 **Random Puts and Gets**（随机读写发起区域）；`POSSIBLE_REGIONS` 列出 **AWS / Azure / GCP** 的若干区域标识（如 `aws:us-east-1`）。
- **`START_TIME` / `END_TIME`**：如 `0 days` ~ `7 days`，与 **7 天窗口** 的合成/截取一致。

### 3.3 `*.aug` 文件列语义（核心）

由 `new_trace_gen.py` 中 augment 逻辑（第二遍扫描）写入：

| 列名 | 含义 |
|------|------|
| `timestamp` | 请求时间（毫秒，与原始 IBM 轨迹一致为相对起点偏移） |
| `op` | 操作类型（如 `PUT`, `GET`） |
| `issue_region` | 客户端发起请求时所在的云区域 |
| `obj_key` | 对象键 |
| `size` | 对象大小（字节） |
| `time_to_next_access` | **全局**该对象下一次访问的时间戳；若无则 **`-1`** |
| `time_to_next_access_same_reg` | **同一 `issue_region` 内**该对象下一次访问的时间戳；若无则 **`-1`** |

`Data/` 中示例行（`IBMObjectStoreTrace029Part0.typeA.mc.aug-versioned`）：

```text
timestamp,op,issue_region,obj_key,size,time_to_next_access,time_to_next_access_same_reg
0,PUT,aws:us-west-2,9a13c61328309aae-v1,536,14303742,-1
```

这些列与 Java 侧 `SimulatorV2` 读取 `time_to_next_access*` 的逻辑一致，用于 **Tevict / TevictRanges** 等基于下一访问时间的策略。

### 3.4 合成轨迹（可选）

- 脚本：`synthetic_trace.py`，文档：`Documentation/SYNTHETIC_TRACE.md`。
- 用途：在 **无区域** 的受控条件下调节 **对象数、GET 次数、GET/PUT 比例、对象大小分布** 等，再接入多区域生成流程。
- 默认合成 YAML 约 **10000** 对象；大小分桶：约 50% 100KB–1MB、47% 1MB–10MB、3% 1GB–1TB（见 `SYNTHETIC_TRACE.md`）。

### 3.5 其他 trace 类型（B/C/D/E…）

`yaml/` 下还有 `typeB`、`typeC`、`typeE` 等，用于 **分组访问、不同类型发起模式** 等场景；本仓库 `Data/` 当前以 **`typeA`** 为主，部分 bat 使用 **`typeE`**（例如 `2reg/FB` 下的 `*.typeE.mc-aug`）。

---

## 4. 本仓库 `Data/` 目录结构与文件特征

### 4.1 目录布局（当前快照）

| 路径 | 内容概要 |
|------|-----------|
| `Data/2reg/2reg/` | 小规模调试 CSV：`debug_10_requests.csv`、`test_50_requests.csv` |
| `Data/3reg/3reg/` | 三元区域相关数据（目录内可能含 macOS 辅助文件 `__MACOSX`） |
| `Data/6reg/6reg/typeA/` | 6 区域 `typeA` 轨迹，如 `IBMObjectStoreTrace029Part0.typeA.mc.aug-versioned`、T065 等 |
| `Data/9reg/9reg/typeA/` | 9 区域 `typeA` 轨迹；含 **完整 trace**、**sample500** 子集、以及带策略后缀的 **prototype** 文件 |

### 4.2 命名约定

典型模式：

```text
IBMObjectStoreTrace{NN}Part0.typeA.mc.aug[-versioned][.{placementPolicy}.prototype]
```

- **`typeA`**：YAML 中的 typeA 多区域随机读写映射。
- **`mc`**：multi-cloud 轨迹（中间产物概念上对应 `*.mc`；你处可能直接与 `aug` 合并命名）。
- **`aug` / `aug-versioned`**：带 augment 列；`-versioned` 表示经仿真器 **对象版本化**（见下节）后的路径。
- **`.tevict_ranges.prototype` / `.milp_ec_new_v2.prototype`**：Java `SimulatorV2` 在运行时根据 **`placementPolicy`** 再写入 **`answer_region`** 等列时生成的 **原型/中间输出**（见 `SimulatorV2.run()` 中对输出文件名的拼接）。

### 4.3 Java 仿真器对输入 trace 的再处理

- **`versionEnable`** 为真时：会将 trace **版本化**（对象键增加 `-v1`、`-v2` …），并切换到 `*-versioned` 路径（与 `Data/` 中文件名一致）。
- 读取 CSV 时解析 **`time_to_next_access`** 与 **`time_to_next_access_same_reg`**（字符串转时间戳或哨兵值），供放置策略使用。

---

## 5. 常用 IBM 轨迹编号与实验量级（9 区域 typeA）

以下数据来自本仓库 `java_result/All_Results.csv` 中 **9reg + typeA 系列** 的统计字段（**总请求数、总数据量、仿真时长**），便于把握 **数据集规模与“冷热”差异**：

| Trace 标签 | 总请求数 `total_requests` | 聚合业务量 `aggregate_size_GB` | 仿真时长 `trace_duration`（约） |
|------------|---------------------------|--------------------------------|----------------------------------|
| **T15** | 1,575,627 | ~760 | 7 天 |
| **T29** | 13,019,220 | ~32.6 | ~6 天 4 小时 |
| **T65** | 283,086 | ~2,649 | 7 天 |
| **T78** | 2,409,805 | ~2,497 | 7 天 |
| **T79** | 114,227 | ~4,422 | 7 天 |

**解读要点**：

- **T29**：请求数 **千万级**，但聚合对象体积相对 **较小** → 典型 **小对象、高 QPS** 型 workload，**请求费用**在总成本中占比往往很高。
- **T65 / T79**：在相近请求数或更短时间跨度下 **聚合 GB 数很大** → **大对象** 主导，**存储与传输** 对策略更敏感。
- **T15 / T78**：介于两者之间，适合作为 **中等规模** 回归用例。

（具体成本数字随 **VM 价目、是否 fixed base、placement 策略** 变化；上表仅刻画 **trace 本身规模**。）

---

## 6. 与实验脚本的对应关系

- `java_bat/` 下各区域子目录中的 `DATA_DIR` 通常指向：
  - `Data/9reg/9reg/typeA/`、`Data/6reg/6reg/typeA/`、`Data/3reg/...` 等。
- 常见参数：`--trace` 指向 `IBMObjectStoreTrace029Part0.typeA.mc.aug` 或带 `-versioned` / `.prototype` 的派生文件（取决于是否预先跑过一遍写回）。
- **对比 Java 与 Python artifact** 时，应保证 **同一 `Part0` 编号、同一区域 YAML（2/3/6/9 reg）、同一 augment 版本**，否则 `time_to_next_access*` 与区域分布不一致会导致结论不可比。

---

## 7. 小结与后续实验建议

1. **数据血缘**：SNIA IBM 单桶一周轨迹 → pickle 按对象聚合 → `new_trace_gen.py` + VLDB YAML → 排序后的 **`*.mc`** → **`--augment`** 得到 **`*.aug`** →（可选）Java 侧 **versioned** / **prototype** 派生文件。
2. **策略相关特征**：除 `op`/`size`/`issue_region` 外，**`time_to_next_access*`** 是 **Tevict 系列** 的关键输入；MILP 类策略还依赖 **配置中的价目、窗口、EC 参数** 等，与 trace 列正交。
3. **选 trace 建议**：先根据论文/artifact 固定 **区域数（如 9reg）** 与 **typeA**；再在 T15/T29/T65/T78/T79 中按 **请求量、对象大小、时长** 选 **覆盖不同形态** 的子集，避免只测单一 workload。

---

## 8. 参考路径索引

| 类型 | 路径 |
|------|------|
| 论文 PDF | `skystore.pdf` |
| SNIA 说明 | `skystore_python/artifact-eval-main/simulation/SNIA_traces/README.md` |
| 多区域生成（旧文档） | `Documentation/MULTI_TRACE.md`, `Documentation/MULTI_TRACE_new.md` |
| 合成轨迹 | `Documentation/SYNTHETIC_TRACE.md` |
| 生成与 augment 实现 | `skystore_python/artifact-eval-main/simulation/SNIA_traces/new_trace_gen.py` |
| VLDB YAML 示例 | `skystore_python/artifact-eval-main/simulation/SNIA_traces/yaml/VLDB/{3,6,9}_reg/typeA.yaml` |
| 本仓库数据 | `Data/` |
| Java 读取 augment | `src/main/java/org/example/SimulatorV2.java`（`time_to_next_access` 相关逻辑） |
| Trace 轻量统计 + 可选绘图 | `tools/analyze_trace_access.py`、`tools/sample_trace_head.ps1` |

---

## 9. 访问特征、访问模式与访问频率（用于策略设计）

本节在 **IBM 原始语义 + typeA 多区域映射 + 仿真统计量** 三个层次上归纳 **数据访问特征**，并映射到 **放置 / 迁移 / 纠删 / 计费敏感点**，便于设计或调参新策略（如 MILP 窗口、TTL、EC 与副本权衡等）。

### 9.1 原始 IBM 轨迹层面的访问特征

| 维度 | 特征 | 对策略的含义 |
|------|------|----------------|
| **时间范围** | 单桶 **约一周** 的真实 REST 序列 | 适合评估 **中短期** 放置与驱逐；长周期季节性不在 trace 内，需外推或换 trace。 |
| **时间结构** | 毫秒时间戳；负载在周内 **非均匀**（昼夜/业务峰谷可能体现为 RPS 变化） | **周期性 MILP / 固定窗口** 可能与真实峰谷错位；可考虑 **事件驱动** 或 **自适应窗口**（与 `window=-1` 类设计衔接）。 |
| **对象粒度** | 按 **对象 ID** 串起 PUT/GET/HEAD… | 天然支持 **每对象** 的再访问间隔、生命周期分析；与 `*.aug` 中 **下一次访问时间** 一致。 |
| **操作类型** | 以 **GET/PUT** 为主（及 HEAD 等），经解析归一为短操作名 | 成本模型上 **请求费** 与 **字节数×单价** 拆开；小对象多时 **API 次数** 敏感。 |
| **对象大小** | 跨 trace **差异极大**（见 9.3） | 大对象主导 trace → **存储与跨区带宽** 权重大；小对象高 QPS trace → **请求费与元数据/控制面** 权重大。 |

### 9.2 typeA 多区域映射引入的“访问模式”变化（实验假设）

VLDB **`typeA`（Random Puts and Gets）** 在 **保留 IBM 时间顺序与每对象操作序列** 的前提下，为每条请求 **随机分配 `issue_region`**（服从 YAML 中区域集合与概率）。

| 项目 | 说明 | 设计上的注意 |
|------|------|----------------|
| **空间分布** | 发起区域在 **给定多集合内被“打散”** | 相对原始单区域 IBM 轨迹，**跨区读/写被放大**，更考验 **跨区域传输策略** 与 **多副本/EC 放置**。 |
| **与真实地理负载的偏差** | 真实 workload 常有 **地域亲和**；typeA **弱化**了这种亲和 | 结论宜表述为：在 **“区域发起较均匀”** 的压力下策略是否鲁棒；若研究 **强局部性**，需 **typeB/C/E** 或改 YAML/加权区域概率。 |
| **可重复性** | 同一 pickle + 同一 YAML + 固定随机种子（若实现中有）→ 可复现 | 对比实验必须 **固定 trace 文件与区域数**，否则 **issue_region 分布** 变化会导致结果不可比。 |

### 9.3 访问频率与“对象大小—请求密度”形态（基于 9reg 仿真统计）

利用第 5 节表中 **`total_requests`、`aggregate_size_GB`、`trace_duration`、`avg_request_size_GB`**，可把各 **Txx** 理解为不同 **访问频率 × 对象体积** 的典型形态（以下为 **9 区域 typeA + 当前价目与统计口径** 下的相对结论）。

**（1）平均请求速率（粗算 RPS，仅用于量级）**

将 `trace_duration` 换算为秒 \(T\)，则 \(\text{RPS} \approx \text{total\_requests}/T\)。按 `All_Results.csv` 中典型记录：

| Trace | 总请求数（约） | 时长（约） | 量级 RPS（约） |
|-------|----------------|------------|----------------|
| T29 | 1.30×10⁷ | ~6 天 4.5 小时 | **~20+**（显著高于其他） |
| T78 | 2.41×10⁶ | ~7 天 | **~4** |
| T15 | 1.58×10⁶ | ~7 天 | **~2–3** |
| T65 | 2.83×10⁵ | ~7 天 | **~0.5** |
| T79 | 1.14×10⁵ | ~7 天 | **~0.2** |

**（2）“平均请求大小”与 workload 类型**

`avg_request_size_GB` 在结果表中可理解为 **聚合口径下的平均请求相关体量**（与仿真器统计方式一致；用于 **相对比较** 各 trace 即可）：

| Trace | 相对特征 | 成本与策略敏感点 |
|-------|------------|------------------|
| **T29** | 请求数极高，**平均请求体量极小**（表中约 **10⁻⁶ GB 量级**） | **请求费用占比高**；策略应优先关注 **减少无效 PUT/重复元数据操作**、**轻量决策路径**；重优化（大规模 MILP 每请求）易 **得不偿失**，适合 **粗粒度聚合、窗口化或事件触发**。 |
| **T65 / T79** | 请求数较少但 **aggregate_size_GB 很大**，单请求平均体量 **明显更大** | **存储与跨区传输** 对总成本更敏感；**EC vs 多副本、条带大小、是否固定 base、迁移批量** 差异会被放大。 |
| **T15 / T78** | 介于中间 | 适合作为 **回归与消融**：既能暴露请求费，又有一定 **存储/传输** 压力。 |

### 9.4 由 `time_to_next_access*` 刻画的“可预测性与局部性”

`*.aug` 中：

- **`time_to_next_access`**：对象 **下一次被访问** 的绝对时间；末次为 **`-1`**。
- **`time_to_next_access_same_reg`**： **同一 `issue_region`** 内下一次访问；若无则 **`-1`**。

| 现象 | 含义 | 策略启发 |
|------|------|----------|
| 大量 **`-1`（全局）** | 许多对象在观测窗内 **只有一次访问** 或末次之后无后续 | **复制/缓存收益有限**；**冷数据** 适合 **少副本、EC 或尽快降冗余**。 |
| **全局有下一跳，但同区 `-1`** | 下一次访问往往 **换区发起** | 在 typeA 随机发起下 **更常见**；**跟随客户端区域的副本** 不一定命中，**固定 base + 远程读** 或 **按需拉取** 的权衡更突出。 |
| **同区下一跳密集** | **同一区域内重复触摸** | 适合 **Tevict / TevictRanges** 等用 **同区再访问间隔** 做驻留；也支持 **区域亲和的缓存**。 |

设计 **TTL、驱逐阈值、MILP 触发条件** 时，建议显式区分：**“全局再访问间隔”**（是否还值得保留副本）与 **“同区再访问间隔”**（是否值得在该区域保留块）。

### 9.5 访问模式小结（面向策略设计）

1. **频率维度**：从 **~0.2 RPS（T79）到 ~20+ RPS（T29）**，跨度大；同一套策略应在 **高 QPS 小对象** 与 **低 QPS 大对象** 两类 trace 上各测一次，避免 **过拟合单一 workload**。  
2. **空间维度**：typeA **随机 issue_region** 偏 ** stress test** 式跨区压力；若策略目标是 **真实地域局部性**，需补充 **非 typeA** 或 **区域加权** 的 trace。  
3. **可预测性维度**：**`*.aug` 提供oracle式下一访问信息** —— 强上界；实际在线策略只能用 **估计**（EWMA、历史分位数等）。离线评估时可分层：**oracle 辅助上界 vs 估计器下的性能**。  
4. **成本维度**：**T29 → 请求费主导**；**T65/T79 → 存储与流量主导** —— 新策略（如 **MilpEC V3**）宜分别声明 **优化主目标**（min 总费 vs min 传输 vs SLA）。  

### 9.6 可复现的后续量化（可选）

若需 **更细分布**（GET/PUT 比例、对象流行度 Zipf 拟合、分时段 RPS、`time_to_next_access` 的 CDF），建议在 `*.aug`（或 `sample500` 子集）上跑 **一次性离线脚本**：按 `op` 聚合计数、按 `obj_key` 统计访问次数分布、按小时桶统计 RPS。本报告不绑定具体数值，避免与 **版本化 trace** 或 **不同截取窗口** 不一致。

---

## 10. 轻量分析方式（避免大文件「卡住」）

千万行级 CSV **全表扫描**在机械硬盘或单线程 Python 下可能 **数十分钟级**；建议按下面顺序选用。

### 10.1 推荐：默认只出 JSON、限制行数（不写图）

仓库脚本：`tools/analyze_trace_access.py`。

- **默认**：最多分析 **20 万行**数据（可改），**不加载 matplotlib**，只输出 `docs/figures/trace_analysis/<tag>_summary.json` + 控制台摘要，通常 **数秒～一两分钟** 内结束。
- **全量**：显式传 `--max-rows 0`（可能很慢、占内存）。
- **需要 PNG**：再加 `--plot`（需安装 `matplotlib`、`numpy`，见 `skystore_python/artifact-eval-main/simulation/requirements.txt`）。

```text
python tools/analyze_trace_access.py --trace Data/6reg/6reg/typeA/IBMObjectStoreTrace065Part0.typeA.mc.aug-versioned
python tools/analyze_trace_access.py --trace path/to/huge.csv --max-rows 500000
python tools/analyze_trace_access.py --trace path/to/huge.csv --max-rows 0 --plot
```

每处理约 **10 万行**会向 **stderr** 打一行进度（可用 `--no-progress` 关闭）。

### 10.2 Windows：先用 PowerShell 截断再分析（不经过 Python 读全文件）

对 **GB 级** 单行超长 CSV，`Get-Content -TotalCount` 只读前若干行，再对 **小文件** 跑全量统计：

```text
powershell -ExecutionPolicy Bypass -File tools/sample_trace_head.ps1 -In "path\to\trace.csv" -Out "path\to\trace_head.csv" -Lines 200001
python tools/analyze_trace_access.py --trace path/to/trace_head.csv --max-rows 0 --plot
```

### 10.3 与论文 / 数据目录的对应

- **`skystore.pdf`**：问题定义与成本模型仍以论文为准；本节工具只刻画 **给定 CSV 的统计特征**。
- **`skystore_python/.../SNIA_traces`**：trace **生成逻辑**仍以 `new_trace_gen.py`、YAML 为准。
- **`Data/`**：若本地只保留部分 `*.aug-versioned`，分析脚本对 **任意同表头 CSV** 均可运行；**不同机器路径** 请自行替换 `--trace`。

---

*文档生成说明：基于仓库内脚本与 `Data/` 抽样内容整理；第 9 节中的 RPS 与 workload 解读部分依赖 `java_result/All_Results.csv` 中统计字段与 trace 时长，若 trace 或仿真器统计口径变更请同步重算。若你更新 YAML 或重新生成 trace，请以 `new_trace_gen.py` 与本地 CSV 表头为准修订前文各节。*
