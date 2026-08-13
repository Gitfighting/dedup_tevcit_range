# T065 全量在线实验 — 过程与结果分析（进行中）

- 更新：2026-08-12 10:55 CST
- 范围：**仅 T065 typeE 2reg**，五策略全量（283,086 ops）
- 形态：在线真实 FS（manifest 真实文件体 → sky-s3 → s3s-fs → store-server）
- 主机：`118.31.175.106`，`TAGS=["T065"]`，`flock` 单矩阵

---

## 1. 实验过程（流水线）

```
1. launch_local_stack(policy)
     s3s-fs :8014  → /data/ablation_exp/s3-local-cache
     store-server :3000（uvicorn ablation）
     sky-s3 :8002
2. CreateBucket(default-skybucket)   # 一次逻辑桶
3. replay .mc-aug（boto3）
     PUT: file_pool+manifest 流式真实内容
     GET: 经代理 locate / 可能跨区拉取
4. POST /clean_out_remaining → 费用 JSON
5. stop stack，清 cache，下一策略
```

策略顺序：`always_evict` → `always_store` → `t_even` → `skystore` → `dedup_aware_tevict`

### T065 workload 画像
| 项 | 值 |
|----|-----|
| ops | 283,086（PUT 3,065 + GET 280,021） |
| PUT 区 | 全部 `aws:us-east-1` |
| GET 区 | 全部 `aws:us-west-2`（跨区读） |
| 逻辑容量 | ~4.5 GB（主键副本，盘上可承受） |
| 对象数 | 3,065（manifest 全覆盖） |

---

## 2. 当前进度（always_evict）— 2026-08-12 10:55 CST

| 指标 | 快照 |
|------|------|
| 状态 | **running**（已跑 ~1h20m，自 09:35 CST） |
| 进度 | **34,000 / 283,086**（**12.0%**） |
| 速率 | **7.1 ops/s**（大对象 GET 主导） |
| put / get | 1800 / 32200 |
| skip / error / body_miss | **0 / 0 / 0** |
| bytes_put | 4.75 GB（PUT 已接近满逻辑容量） |
| bytes_get | **346 GB**（客户端累计读流量，非盘占） |
| s3-local-cache | **4.5G**（与逻辑容量一致） |
| `/data` 剩余 | ~536G free |
| 本策略 ETA | 约 **再 9.7h**（`(283k−34k)/7.1`） |
| 五策略粗算 | 约 **~55h / 2.3 天**墙钟（5×全量串行） |

栈健康：`:3000` uvicorn、`:8014` s3s-fs（~55% CPU）、`:8002` sky-s3（~18% CPU）、cell ~13% CPU。

> GET 修复后正常：此前 `always_evict` primary TTL=0 → locate 404；现 primary 可 locate，`get>0, skip=0`。

---

## 3. 过程解读

1. **占盘**：几乎只有 `s3-local-cache` ≈ 4.5GB；读流量 346GB≠盘占用。
2. **墙钟瓶颈**：28 万次 GET 经 sky-s3 拉体；PUT 仅 3k，很快结束。
3. **跨区语义**：代理 `CLIENT_FROM_REGION=us-east-1`，trace GET 在 west；费用由策略+locate 计，数据面仍从 east 副本服务。
4. **正确性**：`body_miss=0`、`error=0`、`skip=0` → 过程可信。
5. **范围控制**：矩阵 `TAGS=["T065"]`；看门狗 `watch_t065_only.py` 防串其它 trace。

---

## 4. 结果（待五策略 `clean_out_remaining`）

完整费用表齐套后填写：

```
/data/ablation_exp/results/T065_typeE_{always_evict,always_store,t_even,skystore,dedup_aware_tevict}.json
```

### 离线对照（同 trace verification，非本实验目标）
| 臂 | total $ | hit |
|----|--------:|----:|
| offline baseline | 5.252 | 0.877 |
| offline dedup | 2.508 | 0.877 |

在线将给出 `storage_cost` / `network_cost` / `stats`（G1–G4 vs G8）。

---

## 5. 监控

```bash
tail -f /data/ablation_exp/logs/cell_T065_always_evict_full.log
cat /data/ablation_exp/results/matrix_2reg_status.json
pgrep -af 'run_full_2reg_matrix|watch_t065'
```
