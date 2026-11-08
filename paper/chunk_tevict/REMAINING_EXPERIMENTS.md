# ChunkTevict：开机备忘

**完成/待跑以 [`EXPERIMENT_STATUS.md`](EXPERIMENT_STATUS.md) 为准（2026-10-21）。**  
论文缺口以 [`PAPER_TODO.md`](PAPER_TODO.md) 为准。

TTL **不是**新规则：作者 `get_tevict` / `calculate_ttl` 原样，只把单位从 object 换成 unique chunk。不要把 min-live 当卖点。真三云延迟和 vs AWS API 默认不做。

---

## 现在还要不要开机

费用矩阵 **811/820**。缺的 9 格全是 **JuiceFS T29 6/9 区**（OOM）。ChunkTevict / SkyStore 的 Fig.5、Table 4、Fig.6（含 T29）、3 区 FP T29 **已经齐**。

优先顺序：

1. **写论文 + 用现有 JSON 重算 Table 5**（不必开机）。
2. **M3 消融**（2 区 T65+T78）：要先加实验入口，再开机；这是正文机制证明。
3. 若要 Table 5 JuiceFS 9 区 T29：独占整机重跑 J1–J3，失败就在正文声明缺失。

对照三份材料：

- FAST 短文 [Ye et al., ICC 2021]：§V-A Datasets → §V-B Metrics → §V-C 按数据集串讲。
- SkyStore [Liu et al., VLDB 2025] §6。
- 初稿 [`paper/chunk_tevict/`](.)。

---

## 明确不必做

- 重跑已完成的 2/3 区 FB、6 区 FB、CT/Sky 的 6/9 T29。
- 旧 DedupAware / `tevict_ranges_dedup`。
- SPANStore、真 AWS API、2 区 FP。
- 全量重切 `file_pool`。
- 改 `policy_tevict_ranges_new.py` 或 `simulator_v2.py` 核心。
