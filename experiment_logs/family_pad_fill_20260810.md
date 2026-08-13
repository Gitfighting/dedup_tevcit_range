# 同族组合 + 同族真实字节补齐——T079 最后两个缺口闭合

时间：2026-08-10 11:55 (UTC+8)

## 方法（最终采用）

对每个缺口对象（linux 同族，不跨类型混合）：

1. **Phase A 整树组合**：按体积从大到小，用 `tar -rf` 逐棵追加完整 linux 版本树，
   追加前用保守估计（du×1.15 + 8MB）保证不会超出目标。
2. **Phase B 子目录下钻**：余量 > 32MB 时，取备用树的 L1/L2 子目录
   （如 `arch/`、`fs/`、`sound/`、`Documentation/`）继续缩小余量。
3. **Phase C 真实字节补齐**：从一棵未使用的 linux 树（linux-6.0.12）拼接的
   0.95G 真实源码字节流中截取恰好所需字节数追加到文件尾，**精确到 1 字节**。

不用零填充；补齐字节是同族真实内容，对 CDC 去重实验无污染。

## 结果

| 目标 | 组成 | 补齐量 | 最终大小 | 耗时 |
|---|---|---|---|---|
| 11,390,233,215 B | 12 棵整树 + 1 个 L1 + 2 个 L2 | 18.0 MB | 精确相等 | 11.1 min |
| 12,228,869,939 B | 13 棵整树 + 1 个 L1 + 4 个 L2 | 15.4 MB | 精确相等 | 14.1 min |

输出：`/data/file_pool/mapped_tars/fam_11390233215.tar`、`fam_12228869939.tar`

## 校验

- **T079 映射 12582/12582 = 100.000%**，无剩余缺口。
- 旧填充进程（fill_linux_pad_parallel / fill_linux_hierarchical / fill_linux_hier_12）
  及 11 个并发 tar 已全部终止，12 个 `.partial` 残留（70.3G）已清理。
- 磁盘：/data 1.6T 已用 1.4T，可用 163G（90%）。

## 依据

SkyStore 作者原型实验中 payload 即按规则生成
（`dd if=/dev/urandom bs={size} count=1`，见 `prototype/run_client.py`），
trace size 与文件 size 天然相等。本方法继承"精确 size 规则生成"思想，
仅将随机内容替换为同族真实内容以保留去重潜力。

脚本：`scripts/cloud/fill_family_pad.py`（构建）、`_stop_clean_fill.py`（清理）。
