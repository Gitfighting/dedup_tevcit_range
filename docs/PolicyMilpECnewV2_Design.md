# PolicyMilpECnewV2 设计文档

## 关键词

- **TTL**：生存时间，决定对象何时驱逐
- **直方图**：按访问间隔累积，用于计算副本 TTL（与 PolicyTevictRangesNew 一致）
- **副本**：完整对象单份存储
- **EC(2+0)**：2 个数据片，0 校验片，需 2 片才能重构
- **MILP**：混合整数规划，决策采用副本存储还是 EC 存储

---

## 1. 核心设计

### 1.1 副本 vs EC 决策

- **MILP** 为每个 region 决策：`d=0` 副本，`d=1` EC(2+0)
- 副本：1 个 region 存完整对象
- EC(2+0)：2 个 region 各存 1 片（size/2）

### 1.2 TTL 计算

| 存储类型 | TTL 计算方式 |
|----------|--------------|
| 副本 | 与 PolicyTevictRangesNew 一致（直方图 + calcEvictCost） |
| EC 片 | **各片 TTL 与其所在 region 一致**：shard1 在 A reg → TTL_shard1 = TTL_regA，shard2 在 B reg → TTL_shard2 = TTL_regB |

### 1.3 EC 驱逐规则（关键）

- **联动驱逐**：任一 EC 片到期时，**所有 EC 片一起驱逐**（结算费用）
- **删除前检查**：**仅当不是最后一对时才允许驱逐**，否则数据完全丢失
  - 有副本 + EC：可驱逐到期的 EC 对或到期的副本（有冗余）
  - 仅有 EC 对：**不能驱逐**（EC 是唯一存储，驱逐即数据丢失）
  - 仅有副本：**不能驱逐**（副本是唯一存储，驱逐即数据丢失）

---

## 2. 伪代码

### 2.1 MILP 决策（简化）

```
function solveRegionMilp(regionKey, stats):
    d = BINARY  // 0=副本, 1=EC(2+0)
    sigma[1..n] = BINARY  // EC 时选 2 个 shard region
    
    // EC(2+0): 2 片
    C1: Σ sigma[i] = 2 * d
    C2: sigma[rIdx] >= d  // 本地必须有片
    
    // 目标：最小化 存储 + 传输
    minimize: 副本成本*(1-d) + EC成本*d
    
    if d==1:
        decisions[regionKey] = EC, shardRegions=[...]
    else:
        decisions[regionKey] = REPLICA
```

### 2.2 副本 TTL（与 PolicyTevictRangesNew 一致）

```
function getTevict_Replica(src, dst):
    // 直方图累积、calcEvictCost、findMin
    return parent.getTevict(src, dst)  // 复用父类
```

### 2.3 EC TTL（各片与其所在 region 一致）

```
function getTevict_EC(src, dst):
    // EC 片 TTL = 该片所在 region 的 TTL
    // shard1 在 A reg → TTL_shard1 = TTL_regA
    // shard2 在 B reg → TTL_shard2 = TTL_regB
    return tauH[dst] * 3600  // dst = 片所在 region，秒
```

### 2.4 驱逐前检查

```
function canEvict(obj, logicalObject, allPhysObjs, timestamp):
    hasReplica = 存在副本
    hasEC = 存在完整 EC 对（2 片）
    
    // 仅当有冗余时才允许驱逐，否则数据完全丢失
    if hasReplica and hasEC:
        return true   // 可驱逐到期的那一类
    else:
        return false  // 仅有一种存储，不能驱逐
```

### 2.5 EC 联动驱逐

```
function getLinkedEvictionObjects(obj, logicalObject, allPhysObjs, timestamp):
    if obj 是副本:
        if hasEC: return [obj]   // 有 EC 冗余，可驱逐副本
        else: return []          // 最后一份，不能驱逐
    if obj 是 EC 片:
        if 任一 EC 片到期 and hasReplica:
            return 所有 EC 片   // 联动删除，副本保留
        else:
            return []           // 最后一对，不能驱逐
```

---

## 3. 类图

见 [PolicyMilpECnewV2_Class.puml](plantuml/PolicyMilpECnewV2_Class.puml)

---

## 4. 流程图

### 4.1 主流程

见 [PolicyMilpECnewV2_Flow.puml](plantuml/PolicyMilpECnewV2_Flow.puml)

### 4.2 EC 联动驱逐流程

见 [PolicyMilpECnewV2_Eviction.puml](plantuml/PolicyMilpECnewV2_Eviction.puml)

---

## 5. 计费模型

### 5.1 最终价格计算是否使用 EC 真实逻辑？

| 计费项 | 扩展点 | 是否使用 EC 逻辑 | 说明 |
|--------|--------|------------------|------|
| **写存储** | `getPlacementWithSizes` → PhysicalObject.size | ✓ 是 | EC 时每 region 存 size/2，存储成本按 shard 大小 |
| **写传输** | `getPlacementWithSizes` → `_updateTransferMetric(transferSize)` | ✓ 是 | 写传输成本按 size/2 计算 |
| **读传输** | `getReadTransferSize` → `_updateTransferMetric(readSize)` | ✓ 是 | EC 布局时读传输按 size/2 |
| **直方图** | `getHistogramSizeGB` | ✓ 是 | EC 时累积 size/2，τ* 与 EC 存储一致 |
| **驱逐/联动** | `canEvict` / `getLinkedEvictionObjects` | ✗ 否 | 当前 Simulator/TransferPolicy 未调用，需集成 |

**结论**：写、读、直方图相关计费已使用 EC 真实逻辑（需配合 SimulatorV3 + `milp_ec_new_v2` 配置）。驱逐与联动逻辑尚未接入，需在 `CheapestTransferV2.readTransferPath()` 中调用上述扩展方法。

计费模型图见 [PolicyMilpECnewV2_Cost.puml](plantuml/PolicyMilpECnewV2_Cost.puml)

---

## 6. MILP 伪代码与流程图

### 6.1 MILP 伪代码（solveRegionMilp）

```
输入: regionKey, RegionStats(sWrites, sReads), n, sp[], tc[][], tauH[], nowSec

变量:
  d ∈ {0,1}           // 0=副本, 1=EC(2+0)
  sigma[i] ∈ {0,1}   // region i 是否存 shard
  hatTau ∈ [0, maxTauH]
  z[i] = sigma[i] × hatTau  // McCormick 线性化
  vread[i] ∈ [0,1]   // 读路由 (i ≠ rIdx)

约束:
  C1: Σ_i sigma[i] = 2 × d
  C2: sigma[rIdx] ≥ d
  C_PROVIDER: EC 时 sigma[i]=0 对跨厂商 region i
  C3: hatTau ≥ tauH[i] × sigma[i]  (∀i)
  C4: z[i] 的 McCormick 线性化
  C5: Σ_{i≠rIdx} vread[i] = (K-1)×d,  vread[i] ≤ sigma[i]

目标 (最小化):
  副本存储: sWrites × sp[r] × tauH[r]/720 × (1-d)
  EC 存储:  Σ_i (sWrites/K) × sp[i]/720 × z[i]
  EC 分发:  Σ_{i≠r} (sWrites/K) × tc[r→i] × sigma[i]
  副本读:   sReads × avgNetworkCost × (1-d)
  EC 读:    Σ_{i≠r} (sReads/K) × tc[i→r] × vread[i]

输出: decisions[regionKey] = RegionDecision(dec, sigV, ht, nowSec)
```

### 6.2 MILP 流程图

见 [PolicyMilpECnewV2_MILP.puml](plantuml/PolicyMilpECnewV2_MILP.puml)

### 6.3 运行时序图

见 [PolicyMilpECnewV2_Sequence.puml](plantuml/PolicyMilpECnewV2_Sequence.puml)

---

## 7. 与 PolicyMilpECnew 的差异

| 项目 | PolicyMilpECnew | PolicyMilpECnewV2 |
|------|-----------------|-------------------|
| EC 编码 | RS(2,1) 3 片 | EC(2+0) 2 片 |
| 副本 TTL | 同父类 | 同父类 |
| EC 片 TTL | MILP hatTau 统一 | **各片与其所在 region 一致**：TTL_shard1=TTL_regA，TTL_shard2=TTL_regB |
| 驱逐规则 | 各片独立 | **一片到期则整对驱逐**，且仅当有冗余（rep+EC）时才允许 |
| 删除前检查 | 无 | 有，**最后一对/最后一份不能删**（否则数据丢失） |

---

## 8. Simulator/TransferPolicy 集成说明

PolicyMilpECnewV2 提供以下扩展方法，需 Simulator/TransferPolicy 调用：

1. **canEvict(obj, logicalObject, allPhysObjs, timestamp)**  
   驱逐前调用，返回是否允许驱逐（避免数据丢失）。

2. **getLinkedEvictionObjects(obj, logicalObject, allPhysObjs, timestamp)**  
   返回需联动驱逐的对象列表（EC 时返回所有 EC 片）。

集成位置：`CheapestTransferV2.readTransferPath()` 中，在 `regionManager.removeObjectFromRegion` 之前调用上述方法。
