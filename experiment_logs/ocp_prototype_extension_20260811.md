# Open-Closed Principle — Prototype Extension Policy (2026-08-11)

> 目标：在作者 SkyStore `prototype/` 文件系统上做消融，**保留原作者内容**，对新策略开放扩展。  
> 依据：`skystore.pdf`（VLDB 2025）artifact + 本地 `policy_dedup_aware_tevict.py`。

## 1. 关闭修改（作者核心 — 禁止改逻辑）

下列文件视为 **SkyStore 原作者策略/基座**，消融与 DedupAware 工作中 **不改算法、不改 TTL 公式**：

| 文件 | 角色（论文基线） |
|------|------------------|
| `policy_always_evict.py` | Always Evict |
| `policy_always_store.py` | Always Store / pull-on-read |
| `policy_teven.py` | Fixed break-even TTL |
| `policy_fixed_ttl.py` | Fixed TTL |
| `policy_skystore.py` | SkyStore / Tevict 自适应 TTL |
| `base.py` | PlacementPolicy 基类 |
| `policy_replicate_all.py` / `push` / `single_region` | 放置变体 |

作者硬编码的相对 trace 路径（如 `../experiment/trace/Tr065...`）**不修改源码**；用扩展包 `ablation/trace_bridge.py` 在运行前造 symlink/copy。

## 2. 开放扩展（我们的代码）

| 扩展 | 位置 | 说明 |
|------|------|------|
| **DedupAwareTevict** | `placement_policy/policy_dedup_aware_tevict.py` | 新策略文件；内部复用 SkyStore 自适应逻辑思想，另加 HOT/DEDUP |
| **注册** | `get_placement.py` 增加 `elif dedup_aware_tevict` | 经典 OCP 注册表；原有分支保持 |
| **Dedup 引擎** | `prototype/dedup/*` | 新模块，非论文原 artifact |
| **Import shim** | `operations/dedup/__init__.py` | **仅再导出**，不复制引擎，满足 `from operations.dedup import DedupEngine` |
| **消融驱动** | `prototype/ablation/*` | trace_bridge + replay；不进作者策略 |
| **已有 PUT/GET/clean 分支** | 仅当 `name()=="dedup_aware_tevict"` | 原策略路径保持；禁止把 dedup 逻辑写进 always_*/teven/skystore |

## 3. 与验证实验的关系

| | 验证 `/data/dedup_exp` | 消融 `/data/ablation_exp` |
|--|------------------------|---------------------------|
| 代码 | `scripts/cloud/replay_dedup.py` | **本 prototype 文件系统** |
| 策略 | teven/2 × {baseline,dedup} 计费 | 作者 4 基线 + DedupAwareTevict |
| OCP | 离线对照，不动 prototype | 扩展 prototype，不动作者策略体 |

## 4. 消融矩阵（仍按 G1–G8）

- **G1–G4**：只切 `PUT_POLICY` = always_evict / always_store / t_even / skystore（作者实现）
- **G8**：`dedup_aware_tevict`（扩展）
- **G5–G7**：需额外「基线 + DedupEngine」接线；**不得**改写作者策略类，应另加装饰/包装策略文件（待做）

## 5. 操作约定

1. 改策略行为 → 只加新 `policy_*.py` 或包装类，再在 `get_placement.py` 注册。  
2. 改 PUT/GET → 只在 `dedup_aware_tevict` 分支或抽到 `ablation/` / `dedup/`。  
3. 上传服务器：同步到 `/data/ablation_exp/prototype`，**禁止**覆盖或停止 `/data/dedup_exp` 验证流水线。  
4. 端口与目录与验证隔离（见 `ablation_vs_verification_20260811.md`）。
