# Dataset preparation (reproducible)

This document is the dataset recipe for the Fig.5-style matrix (2/3/6/9 regions × typeA–E × T015/T029/T065/T078/T079).

Two layers are bound by a **manifest**. Do not treat IBM traces as if they contained file bytes.

| Layer | What it is | What it is not |
|-------|------------|----------------|
| **A. Access trace** | timestamp, op, `obj_key`, size, `issue_region` | object payload |
| **B. Content pool** | real files (linux / php / ubuntu / sql / iso) | IBM bucket contents |

Paper wording: *replayed on IBM access patterns under a controlled content model*. Do not write “IBM workload dedup ratio”.

`random.seed(1)` is hard-coded in `new_trace_gen.py` and `multi_cloud_trace_new.py`. Do not change it.

---

# 1. IBM Object Store traces (SNIA IOTTA)

Collection: [IBM Object Store](http://iotta.snia.org/traces/key-value/36305)<br>
Key-value index: [http://iotta.snia.org/traces/key-value](http://iotta.snia.org/traces/key-value)<br>
License / how to download: [FAQs](https://iotta.snia.org/faqs/download_traces)<br>
Paper: [It's time to revisit LRU vs. FIFO](https://research.ibm.com/publications/its-time-to-revisit-lru-vs-fifo)<br>
Blog: [Object Storage Traces](https://www.ibm.com/blog/object-storage-traces/)

SNIA does **not** give an anonymous wget URL. Cookies must be on; accept **SNIA Trace Data Files Download License**; then use **Download via HTTPS** (or the Unix shell script SNIA generates after accept).

We use **five complete traces** (SkyStore Fig.5). On SNIA they are named `IBM Object Store Trace Number NNN (complete)`. After download, SkyStore names them `IBMObjectStoreTraceNNNPart0` even when the listing says “complete”.

| Tag | SNIA listing name | Listing page (10/page) | Records | File size | Span |
|-----|-------------------|------------------------|---------|-----------|------|
| T015 | Trace Number **015** (complete) | [page 3](http://iotta.snia.org/traces/key-value/36305?page=3) | 1.99 M | 37.7 MB | 7 days |
| T029 | Trace Number **029** (complete) | [page 4](http://iotta.snia.org/traces/key-value/36305?page=4) | 18.1 M | 273 MB | 6 days |
| T065 | Trace Number **065** (complete) | [page 8](http://iotta.snia.org/traces/key-value/36305?page=8) | 693 K | 11.7 MB | 7 days |
| T078 | Trace Number **078** (complete) | [page 10](http://iotta.snia.org/traces/key-value/36305?page=10) | 4.61 M | 85.5 MB | 7 days |
| T079 | Trace Number **079** (complete) | [page 10](http://iotta.snia.org/traces/key-value/36305?page=10) | 204 K | 4.12 MB | 7 days |

Raw record format (space-separated, no header):

```
<timestamp_ms> <REST.OP.OBJECT> <object_id> <size> [<range_begin> <range_end>]
```

Sample in this repo: `SNIA_traces/IBMObjectStoreSample`

```
1219008 REST.PUT.OBJECT 8d4fcda3d675bac9 1056
1221974 REST.HEAD.OBJECT 39d177fb735ac5df 528
1232488 REST.GET.OBJECT 95d363d3fbdc0b03 1168 0 1167
```

Save as:

```
raw/IBMObjectStoreTrace015Part0
raw/IBMObjectStoreTrace029Part0
raw/IBMObjectStoreTrace065Part0
raw/IBMObjectStoreTrace078Part0
raw/IBMObjectStoreTrace079Part0
```

---

# 2. Transfer raw trace → per-object pickle

### Script name: `parser_data.py`

Code: `simulation/SNIA_traces/parser_data.py`<br>
Input: SNIA csv (space-separated)<br>
Output: pickle (`obj_key → [[timestamp, op, size, range_begin, range_end], ...]`)

```
python3 parser_data.py ../raw/IBMObjectStoreTrace065Part0 ../raw/IBMObjectStoreTrace065Part0.pickle
```

HEAD / DELETE / COPY stay in the pickle. The next step **keeps only PUT/GET**.

---

# 3. Generate multi-cloud `.mc.aug` traces

## 3.1 Workload types

Implemented in `new_trace_gen.py` (`TraceTypeA`–`D`) and `multi_cloud_trace_new.py` (legacy typeE):

| Type | Rule | Used for |
|------|------|----------|
| **typeA** | PUT and GET region both `random.choice(POSSIBLE_REGIONS)` | 3/6/9reg |
| **typeB** | Per object: one PUT region, one GET region (fixed) | 3/6/9reg |
| **typeC** | PUT any region; GET pinned to one region per group (`NUM_GROUPS: 1`) | 3/6/9reg |
| **typeD** | Per object: one PUT region; GET uniform over all regions | 3/6/9reg |
| **typeE** | All PUTs in one region, all GETs in the other | **2reg only** |

`new_trace_gen.py` typeE is **not** this: it is an A–D mix (`TYPE_DISTRIBUTION`). Do not use `yaml/VLDB/*/typeE.yaml` for Fig.5 2reg.

## 3.2 Region lists (must match VLDB yaml)

| Setup | Regions | Yaml dir |
|-------|---------|----------|
| **2reg typeE** | `aws:us-east-1` (PUT), `aws:us-west-2` (GET) | `yaml/VLDB/2_reg/typeE.yaml` |
| **3reg** | `aws:us-east-1`, `azure:westeurope`, `gcp:us-west1-a` | `yaml/VLDB/3_reg/type{A,B,C,D}.yaml` |
| **6reg** | + `aws:us-west-2`, `azure:eastus`, `gcp:us-east1-b` | `yaml/VLDB/6_reg/` |
| **9reg** | + `aws:us-west-1`, `aws:eu-west-1`, `azure:westus`, `gcp:europe-west1-b` | `yaml/VLDB/9_reg/` |

Verified on `Data/2reg/2reg/IBMObjectStoreTrace065Part0.typeE.mc-aug`: PUT count = 3065 all `aws:us-east-1`; GET count = 280021 all `aws:us-west-2`.

## 3.3 3 / 6 / 9 region (typeA–D)

### Script name: `new_trace_gen.py`

Code: `simulation/SNIA_traces/new_trace_gen.py`<br>
Input: pickle, yaml, output path, `--augment True`<br>
Output: sorted csv + `.aug` (adds `time_to_next_access`, `time_to_next_access_same_reg`)

If first op is not PUT, an init PUT at timestamp `0` is inserted (`Object.add_init_put`). Size for every op of an object is the **first PUT/GET size**.

```
cd simulation/SNIA_traces

python3 new_trace_gen.py \
  ../raw/IBMObjectStoreTrace065Part0.pickle \
  yaml/VLDB/3_reg/typeA.yaml \
  ../traces3/typeA/IBMObjectStoreTrace065Part0.typeA.mc \
  --augment True
```

The augment file is `...typeA.mc.aug`. Repeat for types B,C,D and setups 6reg / 9reg (`traces6/`, `traces9/`).

typeC yaml **must** include:

```
NUM_GROUPS: 1
GROUP_PROB: [1]
```

## 3.4 2 region (typeE)

### Script name: `multi_cloud_trace_new.py`

Code: `simulation/SNIA_traces/multi_cloud_trace_new.py`<br>
Config: `yaml/VLDB/2_reg/typeE.yaml` (write-only `us-east-1`, read-only `us-west-2`)

Then augment with `parser_aug_data.py` **or** prefer the author files already in this repo:

```
Data/2reg/2reg/IBMObjectStoreTrace0{15,29,65,78,79}Part0.typeE.mc-aug
```

Filename: 2reg uses **`mc-aug`** (hyphen). 3/6/9reg uses **`mc.aug`** (dot). The runner accepts both (`run_fig5_matrix_parallel.py`).

## 3.5 Pre-built traces in this repo (skip regen if you only need simulation)

```
Data/2reg/2reg/IBMObjectStoreTraceNNNPart0.typeE.mc-aug          # 5 files
Data/3reg/3reg/type{A,B,C,D}/IBMObjectStoreTraceNNNPart0.typeX.mc.aug
Data/6reg/6reg/type{A,B,C,D}/...
Data/9reg/9reg/type{A,B,C,D}/...
```

NNN ∈ {015, 029, 065, 078, 079}. Total **5 + 3×4×5 = 65** workload files.

Augmented csv header:

```
timestamp,op,issue_region,obj_key,size,time_to_next_access,time_to_next_access_same_reg
```

---

# 4. Real content files (download list)

逐步命令（建目录 → wget → 解压 → ISO 拷贝 → remap → 同族 tar → FastCDC）见 **[CONTENT_POOL_STEPS.md](CONTENT_POOL_STEPS.md)**。下面是文件与 URL 清单。

Archives go to `/data/datasets/<family>/`. Extracted trees go to `/data/file_pool/<name>/`. Mapping never reads SNIA bytes; it reads this pool.

China mirrors are optional (Aliyun). Canonical URLs below are the official ones.

## 4.1 Linux kernel (primary dedup family)

Pattern:

```
https://cdn.kernel.org/pub/linux/kernel/v{MAJOR}.x/linux-{VER}.tar.xz
https://mirrors.aliyun.com/linux-kernel/v{MAJOR}.x/linux-{VER}.tar.xz
```

Old 3.x tarballs are often `.tar.gz` under `v3.x/` or `v3.0/`.

**Minimal consecutive set** (enough for same-family CDC; also in `Data/kernel_consecutive/README.md`):

| File | URL |
|------|-----|
| `linux-6.1.100.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.100.tar.xz |
| `linux-6.1.101.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.101.tar.xz |
| `linux-6.1.102.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.102.tar.xz |
| `linux-6.1.103.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.103.tar.xz |
| `linux-6.1.83.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.83.tar.xz |
| `linux-6.1.84.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.84.tar.xz |
| `linux-5.15.158.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-5.15.158.tar.xz |
| `linux-5.15.159.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-5.15.159.tar.xz |
| `linux-6.6.145.tar.xz` … `linux-6.6.149.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.{145-149}.tar.xz |
| `linux-6.12.98.tar.xz` … `linux-6.12.102.tar.xz` | https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.{98-102}.tar.xz |

**Full ECS pool actually used** (169 extracted trees under `/data/file_pool/linux-*`): 3.0–3.19; 4.4.280–294; 4.14.312–336; 4.19.290–325; plus later 5.x/6.x from `_max_dl.py` / `download_big.sh`. Same URL pattern. Download script: `scripts/cloud/_max_dl.py`, `scripts/cloud/download_big.sh`.

Extract:

```
tar -xf linux-6.1.100.tar.xz -C /data/file_pool/
```

## 4.2 PHP source (second family)

Pattern:

```
https://www.php.net/distributions/php-{X.Y.Z}.tar.xz
```

Very old 5.3.x if php.net 404s: https://museum.php.net/php5/php-5.3.29.tar.gz

**Minimal set** (`scripts/cloud/final_download.py`):

| File | URL |
|------|-----|
| `php-8.2.33.tar.xz` | https://www.php.net/distributions/php-8.2.33.tar.xz |
| `php-8.3.33.tar.xz` | https://www.php.net/distributions/php-8.3.33.tar.xz |
| `php-8.4.24.tar.xz` | https://www.php.net/distributions/php-8.4.24.tar.xz |

**Full ECS pool**: 216 extracted trees `php-5.3.*` … `php-8.4.*` (`scripts/cloud/_max_dl.py`).

## 4.3 Ubuntu ISO + ubuntu-base

**ISOs actually on ECS** (`/data/datasets/iso/`, also copied into `file_pool/`):

| File | Official URL |
|------|----------------|
| `ubuntu-20.04.6-live-server-amd64.iso` | https://releases.ubuntu.com/20.04.6/ubuntu-20.04.6-live-server-amd64.iso |
| `ubuntu-20.04.6-desktop-amd64.iso` | https://releases.ubuntu.com/20.04.6/ubuntu-20.04.6-desktop-amd64.iso |
| `ubuntu-22.04.5-live-server-amd64.iso` | https://releases.ubuntu.com/22.04.5/ubuntu-22.04.5-live-server-amd64.iso |
| `ubuntu-24.04.2-live-server-amd64.iso` | https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-live-server-amd64.iso |
| `ubuntu-24.04.4-live-server-amd64.iso` | https://releases.ubuntu.com/24.04.4/ubuntu-24.04.4-live-server-amd64.iso |

Aliyun example: `https://mirrors.aliyun.com/ubuntu-releases/22.04.5/ubuntu-22.04.5-live-server-amd64.iso`

Mount-copy into the pool (`scripts/cloud/dl_large_files.py`):

```
mount -o loop ubuntu-22.04.5-live-server-amd64.iso /mnt/iso
cp -a /mnt/iso/. /data/file_pool/ubuntu-22.04.5-live-server-amd64/
umount /mnt/iso
```

**ubuntu-base tarballs** (ECS `/data/datasets/ubuntu/`):

| File | URL |
|------|-----|
| `noble-base-amd64.tar.gz` (24.04) | https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.2-base-amd64.tar.gz |
| `jammy-base-amd64.tar.gz` (22.04) | https://cdimage.ubuntu.com/ubuntu-base/releases/22.04/release/ubuntu-base-22.04.5-base-amd64.tar.gz |
| `focal-base-amd64.tar.gz` (20.04) | https://cdimage.ubuntu.com/ubuntu-base/releases/20.04/release/ubuntu-base-20.04.6-base-amd64.tar.gz |
| `bionic-base-amd64.tar.gz` (18.04) | https://cdimage.ubuntu.com/ubuntu-base/releases/18.04/release/ubuntu-base-18.04.6-base-amd64.tar.gz |
| `xenial-base-amd64.tar.gz` (16.04) | https://cdimage.ubuntu.com/ubuntu-base/releases/16.04/release/ubuntu-base-16.04.6-base-amd64.tar.gz |
| `trusty-base-amd64.tar.gz` (14.04) | https://old-releases.ubuntu.com/ubuntu-base/trusty/daily/current/trusty-base-amd64.tar.gz |
| `precise-base-amd64.tar.gz` (12.04) | https://old-releases.ubuntu.com/ubuntu-base/precise/daily/current/precise-base-amd64.tar.gz |

Daily aliases used by download scripts: `https://cdimage.ubuntu.com/ubuntu-base/{jammy,focal,bionic}/daily/current/{codename}-base-amd64.tar.gz`

## 4.4 SQL dumps

ECS files (not Wikipedia, despite some download scripts):

```
/data/datasets/sqldump/thinkmobile_20200505.sql
/data/datasets/sqldump/thinkmobile_20200730.sql
/data/datasets/sqldump/thinkmobile_dev_20200722.sql
/data/datasets/sqldump/thinkmobile_dev_20200803.sql
```

These are **local mysqldump snapshots** (see `docs/sqldump_dataset_note.md`). They are not on a public URL. For a public substitute of the same *class* (multi-date SQL dumps):

```
https://dumps.wikimedia.org/enwiki/20260501/enwiki-20260501-categorylinks.sql.gz
https://dumps.wikimedia.org/enwiki/20260601/enwiki-20260601-categorylinks.sql.gz
https://dumps.wikimedia.org/enwiki/20260701/enwiki-20260701-categorylinks.sql.gz
```

Index: https://dumps.wikimedia.org/backup-index.html

Manifest usage is tiny (T015: 3 objects, T079: 1). Dedup numbers are driven by linux/php/ubuntu, not SQL.

## 4.5 Optional size-coverage archives

From `scripts/cloud/dl_large_files.py` (ECS `/data/datasets/large/`):

| File | URL |
|------|-----|
| `postgresql-16.2.tar.bz2` | https://ftp.postgresql.org/pub/source/v16.2/postgresql-16.2.tar.bz2 |
| `postgresql-15.6.tar.bz2` | https://ftp.postgresql.org/pub/source/v15.6/postgresql-15.6.tar.bz2 |
| `postgresql-14.11.tar.bz2` | https://ftp.postgresql.org/pub/source/v14.11/postgresql-14.11.tar.bz2 |
| `postgresql-13.14.tar.bz2` | https://ftp.postgresql.org/pub/source/v13.14/postgresql-13.14.tar.bz2 |
| `llvm-17.0.6.src.tar.xz` | https://github.com/llvm/llvm-project/releases/download/llvmorg-17.0.6/llvm-17.0.6.src.tar.xz |
| `llvm-16.0.6.src.tar.xz` | https://github.com/llvm/llvm-project/releases/download/llvmorg-16.0.6/llvm-16.0.6.src.tar.xz |
| `go1.21.5.src.tar.gz` | https://go.dev/dl/go1.21.5.src.tar.gz |
| `go1.20.12.src.tar.gz` | https://go.dev/dl/go1.20.12.src.tar.gz |
| `go1.19.13.src.tar.gz` | https://go.dev/dl/go1.19.13.src.tar.gz |
| `mysql-boost-8.0.36.tar.gz` | https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-boost-8.0.36.tar.gz |
| `mysql-5.7.44.tar.gz` | https://downloads.mysql.com/archives/get/p/23/file/mysql-5.7.44.tar.gz |
| `mariadb-10.11.6.tar.gz` | https://archive.mariadb.org/mariadb-10.11.6/source/mariadb-10.11.6.tar.gz |
| `mariadb-10.6.16.tar.gz` | https://archive.mariadb.org/mariadb-10.6.16/source/mariadb-10.6.16.tar.gz |

Bitnami Debian OVAs (`file_pool/bitnami-*`) fill leftover sizes; they are **not** a paper family. Skip them for a clean linux/php/ubuntu reproduction.

---

# 5. How file combinations are constructed

Each unique `obj_key` has one size `S` (first PUT/GET). Payload is **exactly `S` bytes** of real content. Three levels, never cross-family mix, never zero-fill.

```
obj_key, S
    │
    ├─ L1  one pool file with |file_size − S| small
    │        exact : file_size == S
    │        trim  : take first S bytes (file_size > S)
    │        pad   : file bytes + cyclic same-file bytes until S (file_size < S)
    │
    ├─ L2  same-family combo tar  (linux+linux, php+php, ubuntu+ubuntu)
    │        tar -rf  whole trees, then L1/L2 subdirs, then same-family byte pad
    │
    └─ L3  extent : disjoint [offset, offset+S) carved from a large unused file
```

### Script name: `remap_zero.py` (L1 + L3, writes the manifest)

Code: `scripts/cloud/remap_zero.py`<br>
Input: `/data/file_pool`, 2reg `typeE.mc-aug.csv`<br>
Output: `/data/manifests/IBMObjectStoreTraceNNNPart0.typeE.mc-aug.manifest.csv.gz`

Manifest columns:

```
obj_key, size_trace, file_rel, file_size, offset, delta, action
```

`action` ∈ {`exact`, `trim`, `pad`, `extent`, `empty`}.

The same five typeE manifests are reused for typeA–D of the same trace id (same `obj_key` / size; only `issue_region` changes). Chunk cache: `/data/dedup_exp/T0{15,29,65,78,79}/`.

### Script name: `fill_family_pad.py` (L2, GB-scale leftovers)

Code: `scripts/cloud/fill_family_pad.py`<br>
Same-type only: `scripts/cloud/fill_same_type.py`<br>
Greedy multi-file tars: `scripts/cloud/build_mapped_tars.py`

L2 phases (T079 last two objects, error = 0 bytes):

1. Whole linux trees, largest first, `tar -rf`, guard `du×1.15+8MB` so we never overshoot.
2. If remainder > 32MB, append L1/L2 subdirs (`arch/`, `fs/`, …) from a reserve tree.
3. Append bytes from an unused linux tree (`linux-6.0.12` pad stream) to hit **exact** `S`.

Example (logged in `experiment_logs/family_pad_fill_20260810.md`):

| Target (bytes) | Combo | Pad | Output |
|----------------|-------|-----|--------|
| 11,390,233,215 | 12 trees + 1×L1 + 2×L2 | 18.0 MB linux source | `file_pool/mapped_tars/fam_11390233215.tar` |
| 12,228,869,939 | 13 trees + 1×L1 + 4×L2 | 15.4 MB | `fam_12228869939.tar` |

T079 mapping: **12582 / 12582 = 100%**.

Extracted ECS manifest mix (typeE, Aug 10 2026):

| Trace | objects | linux | php | ubuntu | mapped_tars | other |
|-------|---------|-------|-----|--------|-------------|-------|
| T015 | 671582 | 473762 | 24304 | 4733 | 168722 | bitnami/sql |
| T029 | 3966994 | 2897384 | 1066777 | 2829 | 0 | 4 empty |
| T065 | 3065 | 2222 | 378 | 211 | 4 | 247 empty |
| T078 | 91176 | 70212 | 10793 | 826 | 9345 | — |
| T079 | 12582 | 5616 | 1647 | 403 | 4905 | bitnami/sql |

---

# 6. Chunk cache (needed by DedupAware / TevictRangesDedup)

### Script name: `chunk_objects.py`

Code: `scripts/cloud/chunk_objects.py`<br>
Replay of the recipe: `prototype/ablation/body_materialize.py` (`payload_buffers`)

FastCDC: min 2 KiB / avg 8 KiB / max 64 KiB, fingerprint BLAKE3 (first 8 bytes).

```
python3 scripts/cloud/chunk_objects.py 065
```

Writes `/data/dedup_exp/T065/{fps.npy,lens.npy,index.csv.gz}`. Shared across 2/3/6/9reg and typeA–E of that trace id.

---

# 7. End-to-end command order

```
# A. IBM traces
#    browser: http://iotta.snia.org/traces/key-value/36305
#    download 015, 029, 065, 078, 079 (complete)

cd simulation/SNIA_traces
python3 parser_data.py ../raw/IBMObjectStoreTrace065Part0 ../raw/IBMObjectStoreTrace065Part0.pickle

# 3reg typeA (repeat B/C/D and 6/9reg)
python3 new_trace_gen.py ../raw/IBMObjectStoreTrace065Part0.pickle \
    yaml/VLDB/3_reg/typeA.yaml \
    ../traces3/typeA/IBMObjectStoreTrace065Part0.typeA.mc --augment True

# 2reg typeE (or copy Data/2reg)
python3 multi_cloud_trace_new.py ../raw/IBMObjectStoreTrace065Part0.pickle \
    yaml/VLDB/2_reg/typeE.yaml ../traces/IBMObjectStoreTrace065Part0.typeE.mc

# B. Content
wget -P /data/datasets/linux https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.100.tar.xz
# ... remaining URLs in §4
tar -xf /data/datasets/linux/*.tar.xz -C /data/file_pool/

# C. Bind size → bytes, then CDC
python3 scripts/cloud/remap_zero.py
python3 scripts/cloud/fill_family_pad.py    # only if remap reports GB-scale misses
python3 scripts/cloud/chunk_objects.py 065
```

Simulation then uses `.mc.aug` + `/data/dedup_exp/T0xx` (`run_fig5_matrix_parallel.py`).

---

# 8. Code index

| Path | Role |
|------|------|
| `SNIA_traces/parser_data.py` | SNIA → pickle |
| `SNIA_traces/new_trace_gen.py` | 3/6/9reg typeA–D + augment (`random.seed(1)`) |
| `SNIA_traces/multi_cloud_trace_new.py` | 2reg typeE (`random.seed(1)`) |
| `SNIA_traces/yaml/VLDB/{2,3,6,9}_reg/` | region lists + type |
| `scripts/cloud/final_download.py` | php + ubuntu-base + wiki sql (public) |
| `scripts/cloud/download_big.sh` | extra kernels + ubuntu/debian ISO |
| `scripts/cloud/dl_large_files.py` | ISO extract + postgres/llvm/go/mysql |
| `scripts/cloud/extract_pool.py` | archives → `file_pool` files |
| `scripts/cloud/remap_zero.py` | obj_key → file recipe (manifest) |
| `scripts/cloud/fill_family_pad.py` | same-family exact-size combo tars |
| `scripts/cloud/chunk_objects.py` | FastCDC chunk cache |
| `simulation/src/dedup/chunk_store.py` | simulator reads the cache |
| `Data/{2,3,6,9}reg/` | already generated `.mc.aug` |

Related notes: `experiment_logs/dataset_mapping_methodology_20260810.md`, `experiment_logs/family_pad_fill_20260810.md`.
