# 数据集构造方法论：trace 记录 → 真实内容的三级瀑布映射

记录时间：2026-08-10 12:51 (UTC+8)

## 1. 背景：矛盾的来源与消解

- IBM trace（SNIA）每条访问记录带 size，但 trace 是 content-free 的——没有任何内容字节。
- 下载的真实文件（linux/ubuntu/php/sqldump/iso 等）size 离散，与 trace 记录的
  任意字节数难以精确相符，曾长期卡在 ±10MB 误差收不到 1KB。
- 查证作者原型代码（`prototype/run_client.py`）后矛盾消解：作者根本不做匹配，
  而是按 trace size 用 `dd if=/dev/urandom bs={size} count=1` **规则化现场生成**
  随机字节文件。"精确 size 回放"的思想可直接继承，只需把随机内容换成
  有去重潜力的真实内容。

## 2. trace 构造流水线的字段真实性（typeE.mc-aug）

流水线：SNIA 原始 trace → `parser_data.py`（格式转换）→
`new_trace_gen.py --augment`（typeE，`random.seed(1)` 固定种子，可复现）。

| 字段 | 性质 |
|---|---|
| timestamp / obj_key / 访问序列结构 | 真实测量（2019 IBM Cloud 单 bucket 一周） |
| size 数值本身 | 真实测量 |
| size 每对象恒定 | 作者简化：取首条 PUT/GET 记录值，覆盖该对象全部记录 |
| op 流 | 真实但被过滤：只留 PUT/GET，丢弃 HEAD/DELETE/COPY |
| t=0 初始 PUT | 作者编造（首现为 GET 的对象补插） |
| issue_region 整列 | 作者编造（A/B/C/D 访问模式 + random.choices） |
| aug 两列 | 确定性派生 |

要点：作者在核心实验变量（多云 region 维度）上做了"公开规则的编造"并被接受；
我们在 size→内容绑定上做同性质、程度更轻的规则化构造，方法论有先例可依。
"对象 = 恒定 size 的不可变 blob"（作者的对象模型）与"每个 obj_key 绑定一个
固定真实文件"一一对应，不引入额外假设。

## 3. 三级瀑布映射方法（最终采用）

逐对象绑定（同一 obj_key 的所有记录共用一个 size / 一份内容）：

1. **单文件直配**：池中有 size 相符的完整真实文件（ISO / 发行版 tar / SQL dump），
   直接绑定。覆盖绝大多数对象。
2. **同族组合打包**：单文件配不上时，把同族多个文件/目录树组合成**一个归档对象**
   （如 12 棵相邻版本 linux 内核树 → 一个 tar）。云用户上传备份/镜像/快照本就是
   归档形态，12GB 级对象在现实中必然是归档；组合使可达 size 从离散点变为近似连续区间。
   只在同族内组合，不跨类型混合，保持内容纯度、去重率不被稀释。
3. **同族真实字节修边**：组合逼近后的零头（实测十几 MB，占比 ~0.15%）用同族真实
   内容字节截断补齐到精确字节数。非零填充，CDC 分块统计不受污染。

实现：`scripts/cloud/fill_family_pad.py`
（整树 `tar -rf` 逐棵追加，du×1.15+8MB 余量保证只欠不超 → L1/L2 子目录下钻收窄到
32MB 内 → 真实字节流补齐到 0 误差）。

## 4. 结果与论文表述要点

- T079：12582/12582 = 100% 映射，最后两个大对象（11.390G / 12.229G）误差为 0 字节。
- 评审防御三件套：
  1. 声明 trace content-free、内容为受控合成，去重率是**数据集属性**而非 IBM 负载测量值；
  2. 冗余度作为显式实验变量做敏感性分析（低/中/高冗余池）；
  3. 映射规则确定性、配方（obj_key → 树列表/截断偏移）随 artifact 公开，可复现。
- 措辞禁区：不得写"IBM 负载去重率为 X"；正确写法为
  "replayed on IBM access patterns under a controlled content model"。
- 注意保持 size 与访问模式的**联合分布**：逐对象绑定 + 有界扰动可以，
  只匹配 size 边际分布再随意指派不可以。

## 5. 后续

剩余四条 trace 复用同一流水线：先缺口扫描，单文件配不上的走第 2/3 级
（`fill_family_pad.py` 改 TARGETS 或泛化为读缺口列表）。
