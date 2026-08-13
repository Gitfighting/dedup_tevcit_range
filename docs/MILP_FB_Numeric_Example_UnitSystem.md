---
title: "FB + MILP 数值例题"
subtitle: "一体积单位制（不做 KiB/GB、月/天换算）"
author: "与 `docs/MILP_FB_Base_EC_Cost_Walkthrough.md` 一致"
---

## 说明

- 体积：**\(S=9\)**（9 个单位，与仿真/MILP 模板计数一致即可）。
- **\(\rho\)**：存储单价，**USD/(单位·天)**。
- **\(\gamma\)**：跨区 placement 运费，**USD/单位**。
- 详细推导与实现对照见 **`docs/MILP_FB_Base_EC_Cost_Walkthrough.md`**。

## 设定

| 项 | 取值 |
|----|------|
| 对象 | `AA`，体积 **\(S=9\)** |
| Base | 区 **`a`**，持有 **30** 天 |
| 第二副本（分支 A） | 区 **`b`**，TTL **7** 天 |
| EC（分支 B） | **`c`/`d`**，TTL **5** / **6** 天；**\(K_{\mathrm{DATA}}=2\)** |

**单价（例题）**：**\(\rho_a=0.020\)**，**\(\rho_b=\rho_c=0.023\)**，**\(\rho_d=0.024\)**；**\(\gamma_{a\to b}=\gamma_{a\to c}=0.056\)**，**\(\gamma_{a\to d}=0.052\)**（设 **`issue_region=a`**）。

## Base 存储（整对象，30 天）

$$
\text{Cost}^{\mathrm{base}}_{\mathrm{stor}} = S\,\rho_a\,30 = 9\times 0.020\times 30 = \mathbf{5.40}\ \text{USD}.
$$

## 分支 A：第二整副本在 b

$$
\text{stor}_b = 9\times 0.023\times 7 = \mathbf{1.449},\qquad
\text{xfer}_{a\to b} = 0.056\times 9 = \mathbf{0.504}\ \text{USD}.
$$

$$
\text{Cost}^{\mathrm{rep}} = 5.40 + 1.449 + 0.504 = \mathbf{7.353}\ \text{USD}.
$$

## 分支 B：EC（两片 shard）

$$
S_{\mathrm{shard}} = S/2 = 4.5.
$$

$$
\text{stor}_{\mathrm{EC},1} = 4.5\times 0.023\times 5 = \mathbf{0.5175},\quad
\text{stor}_{\mathrm{EC},2} = 4.5\times 0.024\times 6 = \mathbf{0.648}\ \text{USD}.
$$

$$
\text{xfer}_{\mathrm{EC}} = 0.056\times 4.5 + 0.052\times 4.5 = \mathbf{0.486}\ \text{USD}.
$$

$$
\text{Cost}^{\mathrm{EC}} = 5.40 + 0.5175 + 0.648 + 0.486 = \mathbf{7.0515}\ \text{USD}\ (\approx \mathbf{7.05}).
$$

## 结论

$$
\text{Cost}^{\mathrm{EC}} < \text{Cost}^{\mathrm{rep}}\quad(\approx 7.05\ \text{vs}\ 7.35\ \text{USD}).
$$

换 TTL 或 **\(\gamma\)** 后优劣可能反转——由 MILP 在线择优。
