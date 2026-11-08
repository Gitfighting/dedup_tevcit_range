# PolicyTevictRangesDedup：区内 unique、禁止跨区引用链

- **时刻**：2026-08-14 20:09（UTC+8）
- **范围**：`PolicyTevictRangesDedup`（作者 TevictRanges 自适应 TTL + chunk 账本）
- **不跑**：`PolicyDedupAwareTevictRanges`（暂缓）

---

## 1. 一句话（当日结论）

**unique 以本区为准；引用只发生在本区内部。跨区只有拉块，没有指针。GET 只找一个物理上持有完整对象副本的源，一跳拉到本地。不会出现 A→B 的引用连锁，FP 也不会造出这种链。**

---

## 2. 区内账本（PUT / GET / cache 同一套）

每个 region 自己一本账：

- **unique**：该区磁盘上真正占字节的那份 chunk
- **引用**：本区另一个对象也要用这块 → `refcount++`，不再占一份字节，也 **不指向外区**

### PUT 在 A 区（对象 O）

对 O 的每个 chunk：

| 本区 A 有没有这块 | 做什么 | 计费 |
|---|---|---|
| 没有 | **在 A 物理写入**（数据跟这次 PUT 进来，不从 B 拉） | A 增加 unique 字节 |
| 已有（别的对象或旧缓存留下的） | 本区 `ref++` | A 不加存储 |

B 区有没有同一块，与这次 PUT 无关。  
不会出现「A 上对象 O 的一部分是指针，指向 B 的 unique」。

FP 只决定 **A 这份对象副本能不能到期离开**，不改变「副本在哪区，chunk 就物理在哪区」。

### GET 在 C 区

只做 **一跳**，与作者 SkyStore 相同：选一个 **当前还持有对象 O 完整副本** 的区（该区物理上有 O 的全部 chunk），按 egress 最便宜。

然后对 C：

| C 有没有这块 | 做什么 |
|---|---|
| 有 | 本区引用，不传 |
| 没有 | 从 **那一个源区** 把块拉到 C，在 C 落成 C 的 unique |

**不会** 先查 A、发现 A 再指向 B、再去 B。没有这条链。  
源区必须是「有 O 的副本」= 本区 ref/pin 覆盖 O 的全部 chunk，而不是「碰巧有几块」。

### cache

cache 不是另一种跨区引用，只是 **带 TTL 的本区副本**：

- GET miss 且 TTL>0：缺的块拉进 C，在 C unique（或本区已有则 ref），TTL 挂在 `(O, C)` 这份引用上
- 命中刷新：只刷新 `(O, C)` 的到期时间（块在 TTL 内被同一对象再次 GET → 再刷）
- 到期：`ref--`；本区 ref 到 0 且没有 pin，这块的 unique 字节才从 C 消失
- PUT 的 FB pin 也是 **本区 pin**，不是指向外区

---

## 3. 为什么不会连锁

只有允许「A 的副本里部分 chunk 是指向 B 的指针」时才会连锁：C→A→B，B 再被驱逐就断链，FP last-copy 会变成图。

本设计不允许这种指针：

- 引用 = 本区 refcount
- unique = 本区物理块
- 跨区只有 **传输**，没有引用

---

## 4. 与直方图 / 三项成本的关系（同日讨论）

作者用 **Tnext 图 \(X\)** + **Last 图 \(last_X\)** 算期望成本，三项：

1. **命中存储**（间隔 \(\le T\)）：\(X[i]\times \bar t(i)\times S\)
2. **未命中：存满 TTL + 再传**（间隔 \(> T\)）：\(X[i]\times (T\cdot S + N)\)
3. **长尾存储**（窗口内未见下次访问的残余）：\(last_X[i]\times T\times S\)

选出最小 \(T\)，作为该 `(src, dst)` 的 TTL，赋给 dst 上这次留下的副本。

作者累加的是 `request.size`（逻辑对象 GB），因为留下/拉回都是整对象。  
现在 TTL 打在 **该区的 chunk** 上，进桶权重应与账单同量纲：

| | 作者 | 本策略 |
|---|---|---|
| 间隔分桶 | (对象, 区) 的访问间隔 | **不变** |
| Tnext / Last 累积量 | `request.size` | 该对象的 **unique chunk 字节**（离线、与状态无关） |
| 三项公式 / `find_min` / 12h pair TTL | 不动 | 不动 |
| 输出 | 赋给 object 副本 | 赋给该区这份对象对应的 **chunk 引用** |

**不要** 把运行时 `missing_bytes` 写进直方图（依赖当前占用 → 与 TTL 循环；作者直方图只依赖 trace）。

运行时账单仍按：

- 存储：本区 unique 占用 × 时间
- 传输：miss 时本区还没有的 chunk（`missing_bytes`）

预测会略高估占用（直方图未计入跨对象共享），与作者用整对象高估是同一类近似，只是从整对象收到 unique。

---

## 5. FB / FP（不改变区内账本）

| | FB | FP |
|---|---|---|
| 不变式 | PUT 区这份对象副本永驻（ttl=-1，本区 pin） | 全图至少一份 **对象副本**；PUT 区也可到期 |
| unique/引用 | 仍是区内的 | 仍是区内的 |
| last-copy | origin 不删 | 只剩一份对象副本时 freeze；第二份落地后旧份才可走 |
| 跨区指针 | 无 | 无 |

---

## 6. 代码对照（2026-08-14 20:34 已按本口径改本地）

- PUT：本区 `add_chunks(..., bill=True)`，只加 fresh unique，不再加 `logical_size`
- 直方图：`update_past_requests` 权重改为该对象 `unique_bytes`；仅在 GET 上更新（与作者 TevictRanges 一致）
- GET miss：`missing_bytes` 传输；留下时赋 `get_tevict` 的 TTL（0 也挂上）
- TTL 挂在 `(对象, 区)` 引用上刷新；无跨区指针
- 矩阵默认策略已去掉 `dedup_aware_tevict_ranges`

---

## 8. 跨区拉取按 chunk 选源（2026-08-14 20:44 UTC+8）

GET miss **不再**整对象只选一个源区、所有缺块都按该区单价计费。

对每个本区还没有的 unique chunk：在所有 **物理持有该块** 的区（本区 pin/ref>0，含其它对象带进来的共享块）里，选 **egress 单价最低** 的源（单价相同则 latency 更小）。字节相同，单价最低即费用最低。

这不是跨区指针：拉完后块在本区落成自己的 unique。TTL 的 `get_tevict(src,dst)` 仍用「持有完整对象副本、单价最低」的那一区，与作者 pair TTL 一致。

## 7. 讨论脉络（同日）

- 下午：确认 GET 是给副本赋/刷新 TTL，不是默认 ttl=0；作者 ttl=0 只是直方图选中 0 小时档。
- 傍晚：直方图累积量须与「TTL 打在该区 chunk」对齐 → unique chunk GB，而非逻辑对象 size。
- **20:09**：确认 unique **按区**、引用 **只在区内**、GET **一跳**、无跨区引用链。
- **20:44**：跨区拉取按 **chunk** 选 egress 单价最低的持有区（9reg 多缓存）。
