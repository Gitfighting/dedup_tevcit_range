# `sqldump/` 与论文 SQL Dump 工作负载对照

## 论文里写的是什么

*Fast Variable-Grained Resemblance Data* §V.A 第三个数据集为 **SQL Dump**：**不同时期的备份**，引用 **[28] TravisTorrent (MSR 2017)**。  
TravisTorrent 提供的是 **CI/构建研究用 SQL dump 与 CSV**，并非论文作者单独发布的固定文件名。

## 你仓库里的 `sqldump/` 是什么

| 文件 | 大小（约） | 日期（文件名） |
|------|------------|----------------|
| `thinkmobile_20200505.sql` | 541 MB | 2020-05-05 |
| `thinkmobile_20200730.sql` | 543 MB | 2020-07-30 |
| `thinkmobile_dev_20200722.sql` | 542 MB | 2020-07-22 |
| `thinkmobile_dev_20200803.sql` | 543 MB | 2020-08-03 |

格式：**MySQL mysqldump**（`MySQL dump 10.13`，库 `thinkmobile` / `thinkmobile_dev`），含大量 `INSERT`，与论文描述的 **「dump 工作负载、多备份、高冗余」** **同类**，适合：

- Finesse / VCDiff 式 **相似 + delta** 实验  
- 与 `DICE-main` 中 `val_sql.tar` / `sql_dump.tar` 用途一致（见 `test_malloc_chunks.cpp`）

## 是不是「那一模一样的 TravisTorrent」？

**不完全是。** TravisTorrent 是 GitHub+Travis 的 **Java/Ruby 项目构建库**；你这里是 **ThinkMobile 业务库的 mysqldump**。  
对论文实验而言：**工作负载类型匹配**（多时点 SQL 备份、高相似度），**数据源不同**；写论文时应写「自建/第三方 SQL 备份序列」，引用 [28] 作 **同类工作负载**，勿称「我们使用了 MSR TravisTorrent 原始 dump 文件」除非你真的下载了该库。

## 建议用法

```powershell
# HTTP Range 管线（sqldump 走 file:，与本地等价；kernel/macos 在线扩量）
powershell -File scripts/run_finesse_http_batch.ps1 -Datasets sqldump,kernel
# 或单集: powershell -File scripts/run_finesse_http.ps1 sqldump

# 相邻备份差分（文件很大，耗时会较长）
python scripts/delta_encode_files.py --old sqldump/thinkmobile_20200505.sql --new sqldump/thinkmobile_20200730.sql
```

## 与 HTTP Range 扩量的关系

| 数据集 | 本地份数 | HTTP Range 可扩份数 | 有效 D_r |
|--------|----------|---------------------|----------|
| **sqldump** | 4（未压缩 .sql） | 同 4（`file:`）；加文件需放入 `sqldump/` 或改 `https://` | ✅ 主来源 |
| **kernel** | 0 | **16** 连续 `linux-6.1.90..105.tar.xz`（cdn.kernel.org） | ⚠️ 压缩包，r 仅参考 |
| **macos** | 0 | **16** 周快照（FSL 2012） | ⚠️ tar.bz2 归档 |

**论文说服力**：对象数用 kernel+macos HTTP 拉长链；**成本仿真用的真实 r 分布仍以 sqldump 未压缩快照为核心**，合并见 `results/finesse_merged_manifest.csv`。
