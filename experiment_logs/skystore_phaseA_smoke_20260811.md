# OCP：作者 skystore 策略 Phase A 冒烟成功 — 2026-08-11

## 原则
- **未修改** `policy_skystore.py` / `helpers.py` / `put.py`
- 仅用扩展：`ablation/trace_bridge.py`、`ablation/fix_price_storage.py`、冒烟脚本与配置

## 已跑通（store-server + 真实 T065）
| 步骤 | 结果 |
|------|------|
| 真实 trace | `/data/skystore/traces/IBMObjectStoreTrace065Part0.typeE.mc-aug.csv` |
| 作者相对路径 | symlink → `../experiment/trace/Tr065.typeE.3reg.tevict.prototype` |
| `PUT_POLICY=skystore` | uvicorn 单 worker |
| register_buckets | 200 |
| SkyStore 加载时间戳 | **Total length of timestamps: 283086** |
| start_upload | **200**，locator=`aws:us-east-1`，`ttl=-1`（base region） |

结果：`/data/ablation_exp/results/skystore_smoke_phaseA.json`

## 环境注意（未改作者代码的绕法）
1. 缺失 `storage.csv` → 从 `/data/skystore/prototype/.../profiles/` 复制  
2. 全局图部分节点无 `priceStorage` → `fix_price_storage` 运行时补全  
3. SQLite 不支持 `LOCK TABLE` → register 时 `versioning=True` 避开该分支  
4. UltraDict 装不上 → 作者 `helper.py` 已有单进程 dict 回退  

## 尚未完成（完整「文件系统」S3 路径）
服务器无 `rustc`/`s3s-fs`/`sky-s3` 二进制 → 真实对象字节的 PUT/GET 经 s3-proxy 尚未接通。  
下一步：安装 Rust 或拷贝预编译 `sky-s3`+`s3s-fs`，再跑 Phase B（仍 OCP，不改 `policy_skystore.py`）。
