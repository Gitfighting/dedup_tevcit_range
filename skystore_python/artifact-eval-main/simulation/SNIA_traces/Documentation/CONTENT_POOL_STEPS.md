# 真实内容池：逐步可复现操作

本文只写 **内容层（Layer B）**：把公开真实文件下到本地、解压进 `file_pool`、按 IBM 对象 size 精确绑定、再做 FastCDC。

访问层（SNIA → `.mc.aug`）见 [DATASET_PREP.md](DATASET_PREP.md)。仿真读的是 **manifest + chunk store**，不是 IBM 原始字节。

运行环境：Linux（ECS 上是 `/data`，约需 **≥400 GB** 空闲；最小可复现约 **80 GB**）。  
代码根目录下文记为 `$REPO`（本仓库）。  
Python：`/data/venv/bin/python` 或已安装 `numpy`、`blake3`、`fastcdc` 的 python3。

不要改 `new_trace_gen.py` / `multi_cloud_trace_new.py` 里的 `random.seed(1)`（那是轨迹侧）。内容池本身无随机种子，确定性来自 **固定 URL 版本号 + 固定映射脚本**。

---

# 0. 目录约定（先建好）

```bash
sudo mkdir -p /data/{datasets/{linux,php,ubuntu,iso,sqldump,large},file_pool/mapped_tars,skystore/traces,manifests,dedup_exp,reports,pad}
# 若无 sudo、且你就是 root：
mkdir -p /data/datasets/linux /data/datasets/php /data/datasets/ubuntu \
         /data/datasets/iso /data/datasets/sqldump /data/datasets/large \
         /data/file_pool/mapped_tars /data/skystore/traces \
         /data/manifests /data/dedup_exp /data/reports
```

| 路径 | 放什么 |
|------|--------|
| `/data/datasets/` | **未解压** 的官方压缩包 / ISO（下载缓存） |
| `/data/file_pool/` | **解压后的真实字节**（映射只读这里） |
| `/data/file_pool/mapped_tars/` | 同族组合生成的 `fam_<size>.tar` / `combo_*.tar` |
| `/data/skystore/traces/` | 已生成的 `*.typeE.mc-aug.csv` |
| `/data/manifests/` | `obj_key → 文件配方` |
| `/data/dedup_exp/T0xx/` | FastCDC 块指纹 |

ECS 上实际池顶层约 459 项（169 棵 linux 树 + 216 个 php 树 + ubuntu ISO 树 + mapped_tars + 少量 bitnami）。**最小复现**用下面 §2 的 canonical 集合即可跑通映射逻辑；要贴近 ECS 去重率，再用 §2.5 的全量版本环。

---

# 1. 安装依赖

```bash
# Debian/Ubuntu
apt-get update
apt-get install -y wget curl tar xz-utils gzip coreutils python3 python3-pip

/data/venv/bin/pip install numpy blake3 fastcdc
# 若无 venv：
pip3 install numpy blake3 fastcdc
```

`remap_zero.py` 需要 `numpy`。`chunk_objects.py` 需要 `blake3` 与 `fastcdc`（`from fastcdc.fastcdc_cy import fastcdc_cy`）。

把脚本拷到机器上（若仓库已在 `/data/sim_exp` 或 `$REPO`）：

```bash
export REPO=/path/to/Skystore1212   # 改成你的仓库路径
export PY=/data/venv/bin/python     # 或 python3
```

---

# 2. 下载真实文件（官方 URL）

中国大陆可把 `cdn.kernel.org` 换成 `https://mirrors.aliyun.com/linux-kernel/`，把 `releases.ubuntu.com` 换成 `https://mirrors.aliyun.com/ubuntu-releases/`。

## 2.1 Linux 内核（主去重族）

**最小连续集**（解压后每棵约 1.3 GB，四棵约 6 GB）：

```bash
cd /data/datasets/linux
for v in 100 101 102 103; do
  wget -c "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.${v}.tar.xz"
done
# 校验
ls -lh linux-6.1.10*.tar.xz
# 每个应约 130–150 MB，过小则是 HTML 错误页，删掉重下
```

| 文件 | URL |
|------|-----|
| `linux-6.1.100.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.100.tar.xz |
| `linux-6.1.101.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.101.tar.xz |
| `linux-6.1.102.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.102.tar.xz |
| `linux-6.1.103.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.103.tar.xz |
| `linux-6.1.83.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.83.tar.xz |
| `linux-6.1.84.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.84.tar.xz |
| `linux-5.15.158.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-5.15.158.tar.xz |
| `linux-5.15.159.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-5.15.159.tar.xz |
| `linux-6.6.{145..149}.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.N.tar.xz |
| `linux-6.12.{98..102}.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.N.tar.xz |

```bash
for v in 145 146 147 148 149; do
  wget -c "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.${v}.tar.xz"
done
for v in 98 99 100 101 102; do
  wget -c "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.${v}.tar.xz"
done
wget -c https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.83.tar.xz
wget -c https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.84.tar.xz
wget -c https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-5.15.158.tar.xz
wget -c https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-5.15.159.tar.xz
```

## 2.2 PHP 源码（第二族）

```bash
cd /data/datasets/php
wget -c https://www.php.net/distributions/php-8.2.33.tar.xz
wget -c https://www.php.net/distributions/php-8.3.33.tar.xz
wget -c https://www.php.net/distributions/php-8.4.24.tar.xz
ls -lh php-8.*.tar.xz   # 每个约 11–13 MB
```

极老的 5.3.x 若 php.net 404：https://museum.php.net/php5/php-5.3.29.tar.gz

## 2.3 Ubuntu ISO（GB 级对象）

ECS 实际使用的 5 个 ISO：

```bash
cd /data/datasets/iso
wget -c https://releases.ubuntu.com/20.04.6/ubuntu-20.04.6-live-server-amd64.iso
wget -c https://releases.ubuntu.com/20.04.6/ubuntu-20.04.6-desktop-amd64.iso
wget -c https://releases.ubuntu.com/22.04.5/ubuntu-22.04.5-live-server-amd64.iso
wget -c https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-live-server-amd64.iso
wget -c https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-live-server-amd64.iso
ls -lh *.iso   # 每个约 1.5–5 GB
```

Aliyun 例：`https://mirrors.aliyun.com/ubuntu-releases/22.04.5/ubuntu-22.04.5-live-server-amd64.iso`

## 2.4 Ubuntu base 根文件系统

ECS 实际文件名是 `{codename}-base-amd64.tar.gz`：

```bash
cd /data/datasets/ubuntu
wget -c -O jammy-base-amd64.tar.gz \
  https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/ubuntu-base-22.04.5-base-amd64.tar.gz
wget -c -O focal-base-amd64.tar.gz \
  https://cdimage.ubuntu.com/ubuntu-base/releases/20.04/release/ubuntu-base-20.04.6-base-amd64.tar.gz
wget -c -O noble-base-amd64.tar.gz \
  https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.2-base-amd64.tar.gz
wget -c -O bionic-base-amd64.tar.gz \
  https://cdimage.ubuntu.com/ubuntu-base/releases/18.04/release/ubuntu-base-18.04.6-base-amd64.tar.gz
wget -c -O xenial-base-amd64.tar.gz \
  https://cdimage.ubuntu.com/ubuntu-base/releases/16.04/release/ubuntu-base-16.04.6-base-amd64.tar.gz
# 更老的 LTS 走 daily/old-releases
wget -c -O trusty-base-amd64.tar.gz \
  https://old-releases.ubuntu.com/ubuntu-base/trusty/daily/current/trusty-base-amd64.tar.gz
wget -c -O precise-base-amd64.tar.gz \
  https://old-releases.ubuntu.com/ubuntu-base/precise/daily/current/precise-base-amd64.tar.gz
```

## 2.5 （可选）贴近 ECS 全量内核 / PHP 版本环

ECS `file_pool` 有 **169** 个 `linux-*` 顶层目录、**216** 个 `php-*`。下载循环在 `$REPO/scripts/cloud/_max_dl.py`、`download_big.sh`。摘要：

```bash
# 3.0–3.19（多为 .tar.gz）
cd /data/datasets/linux
for v in $(seq 0 19); do
  wget -c "https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.${v}.tar.gz" || true
done
# 4.4.280–294、4.14.312–336、4.19.290–325 等（.tar.xz）
BASE4=https://cdn.kernel.org/pub/linux/kernel/v4.x
for v in $(seq 280 294); do wget -c "$BASE4/linux-4.4.${v}.tar.xz" || true; done
for v in $(seq 312 336); do wget -c "$BASE4/linux-4.14.${v}.tar.xz" || true; done
for v in $(seq 290 325); do wget -c "$BASE4/linux-4.19.${v}.tar.xz" || true; done
```

PHP 全量：`https://www.php.net/distributions/php-{X.Y.Z}.tar.xz`，版本列表见 `_max_dl.py` 中 5.3–8.4 循环。

Bitnami OVA **不是**论文去重族，复现时跳过。

## 2.6 SQL dump

ECS 用的是本地 `thinkmobile_*.sql`（无公网 URL）。公开替代：

```bash
cd /data/datasets/sqldump
wget -c https://dumps.wikimedia.org/enwiki/20260501/enwiki-20260501-categorylinks.sql.gz
wget -c https://dumps.wikimedia.org/enwiki/20260601/enwiki-20260601-categorylinks.sql.gz
wget -c https://dumps.wikimedia.org/enwiki/20260701/enwiki-20260701-categorylinks.sql.gz
```

索引：https://dumps.wikimedia.org/backup-index.html  
对去重率影响很小（T079 仅 1 个对象用到 SQL）。

## 2.7 （可选）中等体积源码包

```bash
cd /data/datasets/large
wget -c https://ftp.postgresql.org/pub/source/v16.2/postgresql-16.2.tar.bz2
wget -c https://ftp.postgresql.org/pub/source/v15.6/postgresql-15.6.tar.bz2
wget -c https://ftp.postgresql.org/pub/source/v14.11/postgresql-14.11.tar.bz2
wget -c https://ftp.postgresql.org/pub/source/v13.14/postgresql-13.14.tar.bz2
wget -c https://github.com/llvm/llvm-project/releases/download/llvmorg-17.0.6/llvm-17.0.6.src.tar.xz
wget -c https://github.com/llvm/llvm-project/releases/download/llvmorg-16.0.6/llvm-16.0.6.src.tar.xz
wget -c https://go.dev/dl/go1.21.5.src.tar.gz
wget -c https://go.dev/dl/go1.20.12.src.tar.gz
wget -c https://go.dev/dl/go1.19.13.src.tar.gz
wget -c https://archive.mariadb.org/mariadb-10.11.6/source/mariadb-10.11.6.tar.gz
wget -c https://archive.mariadb.org/mariadb-10.6.16/source/mariadb-10.6.16.tar.gz
```

下载脚本（仓库内）：`$REPO/scripts/cloud/final_download.py`、`download_big.sh`、`dl_large_files.py`。

---

# 3. 解压进 file_pool（两种，都要做）

## 3.1 整树解压（给同族组合 / GB 对象用）——必须

内核 tarball 解压后顶层目录名是 `linux-6.1.100/`。映射和 `fill_family_pad.py` 认的就是这些目录。

```bash
cd /data/datasets/linux
for f in linux-*.tar.xz linux-*.tar.gz; do
  [ -f "$f" ] || continue
  echo "extract $f"
  tar -xf "$f" -C /data/file_pool
done

cd /data/datasets/php
for f in php-*.tar.xz php-*.tar.gz; do
  [ -f "$f" ] || continue
  tar -xf "$f" -C /data/file_pool
done

# ubuntu-base：归档内部往往没有顶层目录，要自己建
cd /data/datasets/ubuntu
for f in *-base-amd64.tar.gz; do
  [ -f "$f" ] || continue
  name="${f%.tar.gz}"
  mkdir -p "/data/file_pool/${name}"
  tar -xf "$f" -C "/data/file_pool/${name}"
done

# large/ 源码包
cd /data/datasets/large
for f in *.tar.xz *.tar.gz *.tar.bz2; do
  [ -f "$f" ] || continue
  name=$(basename "$f" | sed 's/\.tar\..*$//')
  mkdir -p "/data/file_pool/${name}"
  tar -xf "$f" -C "/data/file_pool/${name}"
done
```

检查：

```bash
ls /data/file_pool | head
du -sh /data/file_pool/linux-6.1.100   # 应约 1.2–1.5G
du -sh /data/file_pool/php-8.2.33      # 应约 100MB+
```

`fill_family_pad.py` 只使用 **200 MB–1.5 GB** 的 `linux-*` 目录。少于约 20 棵树会直接报错退出。

## 3.2 扁平抽出小文件（给 KB–MB 对象配 size）——建议

### Script name: `extract_pool.py`

Code: `$REPO/scripts/cloud/extract_pool.py`  
Input: `/data/datasets/{linux,php,ubuntu}` 压缩包  
Output: `/data/file_pool/linux_kernel/<archive>/` 下最多 3 万个单文件（跳过 >50 MB 的成员）

```bash
$PY $REPO/scripts/cloud/extract_pool.py
```

这一步**不替代** 3.1。3.1 保留目录树给 CDC / 组合；3.2 增加大量离散 size 点，让 L1 单文件命中更容易。

## 3.3 ISO 挂载后整盘拷贝

需要 root / loop 设备：

```bash
for iso in /data/datasets/iso/*.iso; do
  name=$(basename "$iso" .iso)
  dest="/data/file_pool/${name}"
  if [ -d "$dest" ]; then
    echo "SKIP $name"
    continue
  fi
  mnt="/mnt/iso_${name}"
  mkdir -p "$mnt" "$dest"
  mount -o loop,ro "$iso" "$mnt"
  cp -a "$mnt"/. "$dest"/
  umount "$mnt"
  rmdir "$mnt"
  echo "DONE $name $(du -sh "$dest" | cut -f1)"
done
```

脚本版：`$REPO/scripts/cloud/dl_large_files.py` 的 STEP 1。

SQL dump 可直接放进池（不必解压 gzip，也可 gunzip 成 `.sql`）：

```bash
cp -a /data/datasets/sqldump /data/file_pool/sqldump
# 或
mkdir -p /data/file_pool/sqldump
gunzip -c /data/datasets/sqldump/enwiki-20260501-categorylinks.sql.gz \
  > /data/file_pool/sqldump/enwiki-20260501-categorylinks.sql
```

---

# 4. 放入 IBM 多云轨迹（映射的输入）

映射读 **typeE 2reg** 的 unique PUT size（同一 `obj_key` 在 typeA–D 上 size 相同，chunk 可共享）。

仓库已有生成好的文件：

```
$REPO/Data/2reg/2reg/IBMObjectStoreTrace0{15,29,65,78,79}Part0.typeE.mc-aug
```

拷到 ECS 并加上 `.csv` 后缀（`remap_zero.py` 写死了这个名字）：

```bash
cp $REPO/Data/2reg/2reg/IBMObjectStoreTrace*Part0.typeE.mc-aug /data/skystore/traces/
cd /data/skystore/traces
for f in *.typeE.mc-aug; do
  mv -n "$f" "${f}.csv"
done
ls -lh
```

确认表头：

```bash
head -1 /data/skystore/traces/IBMObjectStoreTrace065Part0.typeE.mc-aug.csv
# timestamp,op,issue_region,obj_key,size,time_to_next_access,time_to_next_access_same_reg
```

`op` 必须能被 `.upper() == "PUT"` 认出来（文件里是 `PUT`）。

若还没有 `.mc-aug`，先按 [DATASET_PREP.md](DATASET_PREP.md) §1–3 从 SNIA 生成。

---

# 5. 索引 file_pool + 逐对象绑定

## 5.1 映射规则（必须理解再跑）

每个 **第一次 PUT** 的 `(obj_key, S)` 只绑一份内容，后续 GET 复用。

禁止：跨 linux/php/ubuntu 混装；禁止全零填充。

对每个 `S`：

| action | 条件 | 读出的字节 |
|--------|------|------------|
| `exact` | 池中文件 size == S | 整个文件 |
| `trim` | 文件 size > S 且差值 ≤ 10 MB | 文件 **从头 S 字节** |
| `pad` | 文件略小于 S（≤64 KB **且** ≤ 5% S） | 整个文件 + **从文件头循环** 补到 S |
| `extent` | 上面配不上 | 从大文件上切一段 `[offset, offset+S)`，多对象可切同一文件的不相交区间 |
| `empty` | S == 0 | 无 payload |

代码：`$REPO/scripts/cloud/remap_zero.py`  
读出实现：`$REPO/skystore_python/artifact-eval-main/prototype/ablation/body_materialize.py` 的 `payload_buffers()`。

数值例子：对象要 `S = 1_000_000`。

- 池里有 `foo.c` 正好 1_000_000 → `action=exact`
- 池里最近的是 `bar.c` 1_004_000 → 差 4 KB ≤ 10 MB → `trim`，只读前 1_000_000
- 池里最近的是 `baz.c` 999_000 → 差 1 KB ≤ 64 KB → `pad`，读 999_000 再从头拷 1000 字节
- 都配不上 → 从某个 2 GB ISO 文件 `offset=500000000` 起读 1_000_000 字节 → `extent`

## 5.2 跑 remap

### Script name: `remap_zero.py`

Input: `/data/file_pool` + `/data/skystore/traces/*.csv`  
Output:

- `/data/pool2_sorted.tsv` — 全池 `size<TAB>相对路径`，按 size 排序  
- `/data/manifests/IBMObjectStoreTraceNNNPart0.typeE.mc-aug.manifest.csv.gz`  
- 若有缺口：同名 `.misses.csv`

```bash
cd $REPO/scripts/cloud
$PY remap_zero.py
# 改过 file_pool 后必须重建索引：
$PY remap_zero.py --reindex
```

日志应类似：

```
[T065] assigned=..., empty=..., miss=0 -> coverage 100.000%
[T079] ... miss=2  top misses: ['11.390G', '12.229G']
```

T079 两个 11–12 GB 缺口是 **预期的**，用下一节同族组合闭合。其它 trace 若 `miss>0` 且缺口是 GB 级，同样走 §6。

Manifest 列：

```
obj_key,size_trace,file_rel,file_size,offset,delta,action
```

`file_rel` 相对 `/data/file_pool/`，例如 `linux-6.1.100/Makefile` 或 `mapped_tars/fam_11390233215.tar`。

---

# 6. 同族组合：把几个真实树打成一个精确 size 的 tar

当单文件 / extent 仍配不上（典型是 T079 的 11.39 GB、12.23 GB）：

### Script name: `fill_family_pad.py`

Code: `$REPO/scripts/cloud/fill_family_pad.py`  
硬编码目标（T079 实测缺口）：

```python
TARGETS = [11390233215, 12228869939]   # 字节，必须与 miss 的 size 一致
```

若你的 `*.misses.csv` 里数字不同，先改 `TARGETS` 再跑。

**三阶段（只使用 linux 树，不混 php/ubuntu）：**

1. **Phase A** 按体积从大到小 `tar -rf` 追加整棵 `linux-*`。追加前估计 `du×1.15 + 8MB`，保证不超出目标。  
2. **Phase B** 余量 > 32 MB 时，从备用树追加一级/二级子目录（`arch/`、`fs/`、`Documentation/`…）。  
3. **Phase C** 从一棵未使用的 linux 树把源码文件拼成字节流 `/data/pad_linux_src.bin`，截取恰好 `target - 当前tar大小` 追加到 tar **尾部**。最终 `|size - target| == 0`。

```bash
# 确认有足够 linux 整树
ls -d /data/file_pool/linux-* | wc -l    # fill_family_pad 要求大约 ≥20 棵 200MB+ 的树

$PY $REPO/scripts/cloud/fill_family_pad.py
ls -l /data/file_pool/mapped_tars/fam_*.tar
# 大小必须正好等于 TARGETS
stat -c '%s %n' /data/file_pool/mapped_tars/fam_*.tar
```

ECS 记录（`experiment_logs/family_pad_fill_20260810.md`）：

| 目标字节 | 组成 | 补齐 | 输出 |
|----------|------|------|------|
| 11,390,233,215 | 12 整树 + 1×L1 + 2×L2 | 18.0 MB linux 源码 | `mapped_tars/fam_11390233215.tar` |
| 12,228,869,939 | 13 整树 + 1×L1 + 4×L2 | 15.4 MB | `mapped_tars/fam_12228869939.tar` |

中等缺口（几十 MB–数 GB）也可用 **同族** 脚本：

```bash
$PY $REPO/scripts/cloud/fill_same_type.py   # linux+linux / php+php / ubuntu+ubuntu
$PY $REPO/scripts/cloud/build_mapped_tars.py
```

组合完成后 **必须重新映射**，否则 manifest 看不到新 tar：

```bash
$PY $REPO/scripts/cloud/remap_zero.py --reindex
```

验收：五个 `.misses.csv` 不存在或为空；coverage 100%。

```bash
ls /data/manifests/*.misses.csv 2>/dev/null || echo "no misses files"
zcat /data/manifests/IBMObjectStoreTrace079Part0.typeE.mc-aug.manifest.csv.gz | wc -l
# T079 应为 12583 行（1 头 + 12582 对象）
```

---

# 7. FastCDC 块缓存（Dedup 仿真要用）

对 **每个** trace id 做一次。typeA–D / 2/3/6/9reg **共用** 同一套 `T0xx`（`obj_key` 与 size 相同）。

FastCDC 参数（写死在 `chunk_objects.py`）：min **2 KiB** / avg **8 KiB** / max **64 KiB**。指纹：BLAKE3，取前 8 字节。

### Script name: `chunk_objects.py`

```bash
cd $REPO/scripts/cloud
$PY chunk_objects.py 065
$PY chunk_objects.py 015
$PY chunk_objects.py 078
$PY chunk_objects.py 079
$PY chunk_objects.py 029    # 最大，耗时长、占盘
```

每个对象按 manifest 的 `action` **流式**拼出恰好 `size_trace` 字节，再切块。不把对象物化成独立文件。

输出 `/data/dedup_exp/T065/`：

| 文件 | 含义 |
|------|------|
| `fps.npy` | 每个块 BLAKE3 前 8 字节 |
| `lens.npy` | 每个块长度 |
| `index.csv.gz` | `obj_key,start,count,size` 指向上面两个数组 |

### Script name: `prep_chunkstore.py`

仿真 `ChunkStore.load()` 读的是转换后的 CSR 数组：

```bash
$PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T065
$PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T015
$PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T078
$PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T079
$PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T029
```

生成 `lens_u.npy`、`obj_uids_data.npy`、`obj_uids_indptr.npy`、`obj_meta.csv.gz`。

检查：

```bash
ls -lh /data/dedup_exp/T065/
# 应同时有 fps.npy lens.npy index.csv.gz lens_u.npy obj_uids_*.npy obj_meta.csv.gz
```

仿真器：`run_fig5_matrix_parallel.py` 对 Dedup 策略指向 `/data/dedup_exp/T0xx`。

---

# 8. 从一个对象看完整数据流

以 T065 某个 PUT 为例（数字示意）：

```
trace 行:  0,PUT,aws:us-east-1,0028dd9e06433b6e,1002631,...
           S = 1,002,631
```

1. `remap_zero` 在排序后的池里找最接近 1002631 的未占用文件，例如  
   `file_rel=linux-6.1.100/kernel/sched/core.c`，`file_size=1004100`  
   → `action=trim`，`offset=0`，`delta=1469`。  
2. `chunk_objects` 打开 `/data/file_pool/linux-6.1.100/kernel/sched/core.c`，只读前 1002631 字节，FastCDC 切块，BLAKE3。  
3. 仿真 GET 跨区时，按块指纹算 missing bytes，而不是整文件 1002631。

组合对象（T079 大对象）：

```
manifest: obj_key=..., size_trace=11390233215,
          file_rel=mapped_tars/fam_11390233215.tar, action=exact
```

该 tar 内部是 12 棵真实 linux 树 + 子目录 + 尾部真实源码字节，**没有 `\0` 填充**。

---

# 9. 推荐执行顺序（复制即用）

```bash
export REPO=/path/to/Skystore1212
export PY=/data/venv/bin/python

# A 下载（§2）
# B 整树解压 + ISO 拷贝 + 可选 extract_pool（§3）
# C 轨迹（§4）
cp $REPO/Data/2reg/2reg/*.typeE.mc-aug /data/skystore/traces/
cd /data/skystore/traces && for f in *.mc-aug; do mv -n "$f" "$f.csv"; done

# D 映射
cd $REPO/scripts/cloud
$PY remap_zero.py --reindex

# E 若 T079 miss 两个 11–12G 对象
$PY fill_family_pad.py
$PY remap_zero.py --reindex

# F CDC
for t in 015 065 078 079 029; do
  $PY chunk_objects.py $t
  $PY prep_chunkstore.py /data/dedup_exp/T$t
done
```

---

# 10. 代码索引

| 步骤 | 脚本 |
|------|------|
| 下载 php/ubuntu/wiki | `scripts/cloud/final_download.py` |
| 下载更多内核 + ISO | `scripts/cloud/download_big.sh` |
| 下载 postgres/llvm/go | `scripts/cloud/dl_large_files.py` |
| 扁平抽小文件 | `scripts/cloud/extract_pool.py` |
| size→文件配方 | `scripts/cloud/remap_zero.py` |
| 同族精确 tar | `scripts/cloud/fill_family_pad.py` |
| 同族填中等缺口 | `scripts/cloud/fill_same_type.py` |
| FastCDC | `scripts/cloud/chunk_objects.py` |
| 转仿真 CSR | `scripts/cloud/prep_chunkstore.py` |
| 在线物化（可选） | `skystore_python/.../prototype/ablation/body_materialize.py` |

相关记录：`experiment_logs/dataset_mapping_methodology_20260810.md`、`experiment_logs/family_pad_fill_20260810.md`。
