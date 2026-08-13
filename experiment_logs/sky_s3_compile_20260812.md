# sky-s3 编译错误与解决记录

- 日期：2026-08-12
- 环境：阿里云 ECS `118.31.175.106`，路径 `/data/ablation_exp/prototype/`
- 目标：产出 `s3-proxy/target/release/sky-s3`
- 约束：开闭原则 — 不改作者策略核心，仅处理 `generated/` 与构建脚本

---

## 时间线

| 时刻 (UTC+8) | 事件 |
|---|---|
| **06:42** | 用户确认开始编译 |
| **06:43** | 下载 openapi-generator **6.6.0 / 6.0.1 / 5.4.0**；首次批量试跑全部 `BUILT_FAIL`（约 18s 即失败，非真实编不过） |
| **06:43–06:44** | 排查：`skystore-rust-client` 目录不存在；`cp` 失败；根因是 **generate 未成功** |
| **06:44** | 手工跑 6.6.0 generate：OpenAPI **3.1.0** 校验失败 — `Cannot deserialize Boolean from Object`；提示可用 `--skip-validate-spec` |
| **06:45** | 将 spec 转为 OAS **3.0.3** 后，6.6.0 首次成功生成 client（含 `serde_with`、reqwest 0.11） |
| **06:45–06:48** | 发现 FastAPI `operationId` 形如 `start_upload_start_upload_post`，与 `skyproxy` 期望的 `start_upload` 等不匹配 |
| **~06:48–06:51** | 修正 operationId 后用 6.6.0 生成并 `cargo build`：**72** 个错误（大量 `Box<Etag>` / 空 struct / 缺字段） |
| **~06:51–06:53** | 对比 6.0.1 / 5.4.0：空 wrapper 问题同源 — OpenAPI **`anyOf[T, null]`** 被生成成空 struct / `AnyOfstringnull` |
| **~06:53–06:54** | 展平 nullable anyOf、去掉 `time_to_next_access`，用 **6.0.1** 重生：错误降至 **10**（均为 `size` 整型宽度） |
| **06:54** | 将生成模型中 `size` 改为 `u64` 后 `cargo build --release` **成功**；产物约 30MB |

产物：

`/data/ablation_exp/prototype/s3-proxy/target/release/sky-s3`

（服务器文件时间戳：**2026-08-12 06:54**）

相关本地脚本：`scripts/cloud/build_sky_s3_flatten.py`、`scripts/cloud/build_sky_s3_fix_sizes.py`

---

## 错误与解决

### 1. Generate 表面“编不过”，实为 client 未生成

**时刻：** 06:43

**现象：** `BUILD_EXIT=101`，`cp: .../skystore-rust-client: No such file or directory`，耗时异常短。

**原因：** `openapi-generator` 解析失败后脚本仍继续 `cargo build`。

**解决：** 检查 generate 退出码与日志（`/data/ablation_exp/logs/ogen_*.log`）；确认 `Cargo.toml` 存在后再编译。

---

### 2. OpenAPI 3.1 与旧版 generator 不兼容

**时刻：** 06:44

**现象：**

`SpecValidationException` / `Cannot deserialize value of type Boolean from Object`

**原因：** FastAPI 导出 `openapi: 3.1.0`；6.x 解析器按 3.0 语义处理部分字段失败。

**解决：**

1. 预处理将 `openapi` 标为 `3.0.3`
2. 生成时加 `--skip-validate-spec`
3. 展平 3.1 的 `type: [T, null]` / `anyOf` 等

---

### 3. operationId 与 skyproxy 不一致

**时刻：** 06:45–06:48

**现象：** 生成出 `start_upload_start_upload_post` 等，而 `skyproxy.rs` 调用 `apis::start_upload` 等。

**解决：** 生成前把 operationId 规范为短名（去掉 `_post/_patch`，折叠 `name_name` → `name`）。

---

### 4. `anyOf[string,null]` → 空 struct（主要编译错误源）

**时刻：** ~06:48–06:53（峰值 **72** errors）

**现象（示例）：**

- `Option<Box<Etag>>` / `VersionId` / `Op` / `MultipartUploadId`（空 struct）
- `missing field time_to_next_access`
- `Display` / `Iterator` 等 trait 不满足

**原因：** 可空字段被生成器建成独立空模型，而非 `Option<String>`。

**解决：**

1. 预处理：`{anyOf: [T, null]}` → `T` + `nullable: true`
2. 删除 skyproxy 结构体字面量未写的字段 `time_to_next_access`
3. 选用 **openapi-generator-cli 6.0.1**（Cargo.toml 更接近作者：reqwest 0.11，无 serde_with）

---

### 5. `size` 整型宽度（最后 10 个错误）

**时刻：** ~06:53–06:54

**现象：** `expected i64, found u64`；`Option<i32>` 与 `u64` 区间运算不兼容。

**原因：** 生成器把 `size` 做成 `i32`/`i64`，而作者 `skyproxy` 按 `u64` 使用（`as u64`）。

**解决：** 仅在 `generated/skystore-rust-client/src/models/*.rs` 将相关 `size` 改为 `u64` / `Option<u64>`（不改作者 `skyproxy`）。

---

## 最终可用配方（摘要）

1. `openapi.json` → 预处理 → `openapi_for_rust.json`（3.0.3 + 展平 nullable + 修 operationId + 丢 `time_to_next_access`）
2. `openapi-generator-cli-6.0.1.jar generate -g rust --skip-validate-spec`
3. 模型 `size` → `u64`
4. `cd s3-proxy && cargo build --release`

**未改：** 作者策略文件（`policy_skystore.py` 等）与 `skyproxy.rs` 源码。
