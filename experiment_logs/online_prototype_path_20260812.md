# 在线整栈 Prototype 实验（真实文件系统）— 2026-08-12

## 目标口径（与用户对齐）
- **不上 simulation**；云上跑 `prototype/` 真实 FS：store-server + s3s-fs + sky-s3
- 读 `.mc-aug` → 真实文件体（manifest + `/data/file_pool`）→ PUT/GET → 策略 → `clean_out_remaining`
- 作者策略 + `dedup_aware_tevict`；离线 `/data/dedup_exp` 仅作单组件观察

## 本轮新增（OCP）
| 文件 | 作用 |
|------|------|
| `ablation/body_materialize.py` | manifest 物化真实 payload（禁零填充） |
| `ablation/replay_mc_aug.py` | 在线回放，endpoint 默认 `:8002` |
| `ablation/run_online_cell.py` | CreateBucket → replay → clean_out → results JSON |
| `scripts/cloud/run_prototype_ablation.py` | `--run-cell` 编排 |
| `launch_local_stack.py` | `start_new_session` 防 SSH 杀进程 |
| `clean.py` | `network_cost` 缺失策略兜底 |

## 冒烟结果（T065 typeE，max_ops=20）

### always_evict（作者）
- CreateBucket `default-skybucket` OK；三端口全开
- `put=20 error=0 body_miss=0 bytes_put≈31.2MB`
- 结果：`/data/ablation_exp/results/T065_typeE_always_evict_max20.json`

### dedup_aware_tevict（扩展）
- `put=20 error=0 body_miss=0 bytes_put≈31.2MB wall=65.7s`（CDC 后台更慢属预期）
- `clean_out` stats：`avg_manifest_bytes≈5767.5`，`chunk_locality_L[us-east-1]=1.0`
- `/tmp/dedup_chunks` + `/tmp/dedup_policy_state.json` 已落盘（同区 PUT 未触发 HOT/DEDUP，`obj_state` 空为预期）
- 结果：`/data/ablation_exp/results/T065_typeE_dedup_aware_tevict_max20.json`

## 关键修复
- **禁止** `register_buckets` 与物理桶同名后再 `mb`（UNIQUE → sky-s3 unwrap panic）
- 正确：只对逻辑桶 `aws s3 mb s3://default-skybucket` 一次

## 下一步（完整矩阵）
1. 提高 `--max-ops`（含 GET）再跑 full T065（`--max-ops 0`）
2. 作者四策略 + `dedup_aware_tevict` 各一格
3. 换 boto3 加速 aws-cli 逐 op 瓶颈（全量 28 万 ops）
