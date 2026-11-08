"""Build advisor briefing PPT for TevictRangesDedup (local, no cloud)."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

NAVY = RGBColor(0x1B, 0x36, 0x5D)
TEAL = RGBColor(0x1F, 0x7A, 0x8C)
GOLD = RGBColor(0xC4, 0x8B, 0x2A)
RED = RGBColor(0xA3, 0x3B, 0x2B)
GREEN = RGBColor(0x2E, 0x6B, 0x4A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x1A, 0x1A, 0x1A)
GRAY = RGBColor(0x4A, 0x4A, 0x4A)
CODE_BG = RGBColor(0xF4, 0xF1, 0xEA)
LIGHT = RGBColor(0xE8, 0xEE, 0xF4)
MINT = RGBColor(0xE7, 0xF1, 0xEA)
PEACH = RGBColor(0xF8, 0xEC, 0xE6)

W = Inches(13.333)
H = Inches(7.5)
FONT = "Microsoft YaHei"
CODE = "Consolas"

OUT = Path(__file__).resolve().parent / "TevictRangesDedup_advisor_20260814.pptx"


def _set_run_font(run, name, size, bold=False, color=INK):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", name)


def add_bar(slide, color=NAVY, top=0, height=0.08):
    sh = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(top), W, Inches(height)
    )
    sh.fill.solid()
    sh.fill.fore_color.rgb = color
    sh.line.fill.background()


def add_rect(slide, l, t, w, h, fill):
    sh = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(l), Inches(t), Inches(w), Inches(h)
    )
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    return sh


def tb(slide, l, t, w, h, text, size=18, bold=False, color=INK, font=FONT, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    _set_run_font(run, font, size, bold, color)
    return box


def bullets(slide, l, t, w, h, items, size=16, color=INK):
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(7)
        run = p.add_run()
        run.text = "•  " + item
        _set_run_font(run, FONT, size, False, color)
    return box


def numbered(slide, l, t, w, h, items, size=15, color=INK):
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(8)
        run = p.add_run()
        run.text = f"{i + 1}.  {item}"
        _set_run_font(run, FONT, size, False, color)
    return box


def code_box(slide, l, t, w, h, text, size=12):
    add_rect(slide, l, t, w, h, CODE_BG)
    tb(slide, l + 0.12, t + 0.08, w - 0.2, h - 0.12, text, size=size, font=CODE, color=NAVY)


def footer(slide, n, total):
    add_bar(slide, NAVY, top=7.32, height=0.18)
    tb(slide, 0.4, 7.32, 9, 0.18, "chunk_tevict  组会汇报  2026-08-15", size=10, color=WHITE)
    tb(slide, 11.6, 7.32, 1.4, 0.18, f"{n} / {total}", size=10, color=WHITE, align=PP_ALIGN.RIGHT)


def title_slide(prs, title, subtitle=""):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_rect(slide, 0, 0, 13.333, 7.5, NAVY)
    add_rect(slide, 0, 0, 0.18, 7.5, GOLD)
    tb(slide, 0.7, 2.0, 12, 1.2, title, size=30, bold=True, color=WHITE)
    if subtitle:
        tb(slide, 0.7, 3.4, 12, 1.8, subtitle, size=16, color=RGBColor(0xD0, 0xDC, 0xE8))
    tb(slide, 0.7, 6.5, 12, 0.4, "本地仓库完成 · 不上云 · 2026-08-14", size=12, color=GOLD)
    return slide


def body_slide(prs, n, total, title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_rect(slide, 0, 0, 13.333, 0.85, NAVY)
    tb(slide, 0.4, 0.22, 12.5, 0.5, title, size=20, bold=True, color=WHITE)
    footer(slide, n, total)
    return slide


def section_slide(prs, n, total, kicker, title, blurb):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_rect(slide, 0, 0, 13.333, 7.5, NAVY)
    add_rect(slide, 0, 0, 0.18, 7.5, GOLD)
    tb(slide, 0.7, 2.0, 12, 0.4, kicker, size=14, color=GOLD)
    tb(slide, 0.7, 2.5, 12, 1.2, title, size=32, bold=True, color=WHITE)
    tb(slide, 0.7, 4.0, 12, 1.6, blurb, size=16, color=RGBColor(0xD0, 0xDC, 0xE8))
    tb(slide, 0.7, 6.9, 12, 0.3, f"{n} / {total}", size=12, color=GOLD)
    return slide


def flow_boxes(slide, y, boxes, box_h=2.55, start_x=0.35, gap=0.12):
    n = len(boxes)
    box_w = (12.6 - gap * (n - 1) - 0.28 * (n - 1)) / n
    palette = [NAVY, TEAL, GOLD, GREEN, RED]
    for i, (title, body) in enumerate(boxes):
        x = start_x + i * (box_w + gap + 0.28)
        add_rect(slide, x, y, box_w, box_h, LIGHT)
        add_rect(slide, x, y, box_w, 0.48, palette[i % len(palette)])
        tb(slide, x + 0.08, y + 0.08, box_w - 0.12, 0.36, title, size=13, bold=True, color=WHITE)
        tb(slide, x + 0.08, y + 0.58, box_w - 0.14, box_h - 0.7, body, size=12, color=INK)
        if i < n - 1:
            ax = x + box_w + 0.02
            tb(slide, ax, y + box_h / 2 - 0.18, 0.28, 0.36, "→", size=20, bold=True, color=GOLD, align=PP_ALIGN.CENTER)


def simple_table(slide, x0, y0, col_w, headers, rows, row_h=0.42, head_h=0.38, fsz=12):
    n_col = len(headers)
    total_w = sum(col_w)
    add_rect(slide, x0, y0, total_w, head_h, NAVY)
    acc = x0
    for w, htxt in zip(col_w, headers):
        tb(slide, acc + 0.06, y0 + 0.04, w - 0.08, head_h - 0.06, htxt, size=13, bold=True, color=WHITE)
        acc += w
    for k, row in enumerate(rows):
        yy = y0 + head_h + k * row_h
        add_rect(slide, x0, yy, total_w, row_h, LIGHT if k % 2 == 0 else WHITE)
        acc = x0
        for w, cell in zip(col_w, row):
            tb(slide, acc + 0.06, yy + 0.06, w - 0.08, row_h - 0.08, cell, size=fsz, color=INK)
            acc += w


def main():
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    total = 41
    n = 0

    def nxt():
        nonlocal n
        n += 1
        return n

    # 1
    nxt()
    title_slide(
        prs,
        "TevictRangesDedup：作者自适应 TTL + 区内 unique chunk 账本",
        "核心要讲清一件事：GET 发生在 B、从 A 拉取时，TTL 为什么是 A→B；\n"
        "单位从 object 换成 chunk 后，这条赋值规则为什么仍然成立。\n"
        "听众：指导老师 · 学弟学妹",
    )

    # 2
    i = nxt()
    s = body_slide(prs, i, total, "今天要看懂的整条过程（不是口号）")
    numbered(
        s,
        0.45,
        1.1,
        12.4,
        5.9,
        [
            "离线：IBM 访问轨迹绑上真实文件，切成 chunk，得到每对象的 unique 字节。",
            "运行：每个区一本账（本区磁盘上有哪些 chunk）。跨区没有指针，只有传输。",
            "学习：直方图记在 GET 发出区 B；TTL 表是有向边 src→dst。",
            "作者 object：B miss → 决定从 A 拉整对象 → 这份副本 TTL = get_tevict(A, B)。",
            "我们的 chunk：B 缺块 → 仍先选定完整副本源 A → TTL = get_tevict(A, B)；按块 gather 只改账单。",
            "到期：只减本区引用；本区 pin/ref 都为 0，这块字节才从 B 消失。",
        ],
        size=17,
    )

    # 3
    i = nxt()
    s = body_slide(prs, i, total, "一句话：算法不动，计量单位换成 chunk")
    add_rect(s, 0.4, 1.15, 6.1, 5.5, MINT)
    add_rect(s, 6.8, 1.15, 6.1, 5.5, PEACH)
    tb(s, 0.55, 1.3, 5.8, 0.4, "不改（作者 TevictRangesV2）", size=16, bold=True, color=GREEN)
    bullets(
        s,
        0.5,
        1.85,
        5.9,
        4.5,
        [
            "间隔怎么分桶、find_min、12 小时重算 pair TTL。",
            "三项期望成本公式。",
            "TTL 查表：get_tevict(src, dst)。",
            "不改 policy_tevict_ranges_new.py。",
        ],
        size=15,
    )
    tb(s, 6.95, 1.3, 5.8, 0.4, "只换（本策略）", size=16, bold=True, color=RED)
    bullets(
        s,
        6.9,
        1.85,
        5.9,
        4.5,
        [
            "副本计量：整对象 → 本区 unique chunk。",
            "直方图权重：request.size → unique_bytes。",
            "传输：整对象 egress → 本区还没有的块。",
            "TTL 的 src：仍是这次 miss 选定的完整副本源 A（选定即定边）。",
        ],
        size=15,
    )

    # 4 section data
    i = nxt()
    section_slide(
        prs,
        i,
        total,
        "第一部分",
        "数据从哪来",
        "论文可复现必须把「访问层」和「内容层」拆开。\n"
        "学弟学妹按文档做完这一段，仿真才有 ChunkStore 可读。",
    )

    # 5
    i = nxt()
    s = body_slide(prs, i, total, "两层不要混：IBM 给访问，真实文件给内容")
    add_rect(s, 0.4, 1.15, 6.1, 5.5, LIGHT)
    add_rect(s, 6.8, 1.15, 6.1, 5.5, CODE_BG)
    tb(s, 0.55, 1.3, 5.8, 0.4, "A. 访问层（IBM / SNIA）", size=17, bold=True, color=NAVY)
    bullets(
        s,
        0.5,
        1.85,
        5.9,
        4.5,
        [
            "只有 timestamp / PUT·GET / obj_key / size / issue_region。",
            "集合：T015 T029 T065 T078 T079（complete）。",
            "random.seed(1) 禁止改。",
            "论文写：IBM 访问模式 + 受控内容模型。",
            "不要写「IBM 去重率」。",
            "文档：DATASET_PREP.md、CHANGE_REGIONS.md",
        ],
        size=14,
    )
    tb(s, 6.95, 1.3, 5.8, 0.4, "B. 内容层（公开真实文件）", size=17, bold=True, color=NAVY)
    bullets(
        s,
        6.9,
        1.85,
        5.9,
        4.5,
        [
            "linux / php / ubuntu ISO，固定版本号。",
            "按 IBM 对象 size 精确绑定（manifest）。",
            "FastCDC 离线切块 → ChunkStore。",
            "仿真只读 manifest + chunk store。",
            "文档：CONTENT_POOL_STEPS.md",
        ],
        size=14,
    )

    # 6
    i = nxt()
    s = body_slide(prs, i, total, "访问层：SNIA 轨迹怎么变成多云请求")
    numbered(
        s,
        0.45,
        1.05,
        12.4,
        2.4,
        [
            "iotta.snia.org/traces/key-value/36305 登录下载（无匿名 wget）。",
            "parser_data.py → pickle；multi_cloud_trace_new.py → *.mc.aug。",
            "2/3/6/9 区 × typeA–E。区域怎么换见 CHANGE_REGIONS.md。",
        ],
        size=16,
    )
    code_box(
        s,
        0.45,
        3.6,
        12.4,
        3.2,
        "python3 parser_data.py ../raw/IBMObjectStoreTrace065Part0 \\\n"
        "    ../raw/IBMObjectStoreTrace065Part0.pickle\n\n"
        "# 输出示例：Data/2reg/2reg/IBMObjectStoreTrace065Part0.typeE.mc-aug\n"
        "# 一行 = 一次 PUT 或 GET：时间、发出区、obj_key、size",
        size=14,
    )

    # 7
    i = nxt()
    s = body_slide(prs, i, total, "内容层：IBM size 必须精确绑到真实字节")
    numbered(
        s,
        0.45,
        1.05,
        12.4,
        1.9,
        [
            "每个第一次 PUT 的 (obj_key, S) 只绑一份内容；后续 GET 复用。禁止跨 linux/php/ubuntu 混装。",
            "禁止全零填充。T079 的 11.39G / 12.23G 用同族 linux 树组合（fill_family_pad），不是 \\0。",
            "验收：五个 *.misses.csv 为空；coverage 100%。",
        ],
        size=15,
    )
    simple_table(
        s,
        0.45,
        3.2,
        [2.3, 5.4, 4.6],
        ["action", "条件", "读出的字节"],
        [
            ("exact", "池文件 size == S", "整文件"),
            ("trim", "文件更大且差 ≤ 10MB", "从头读 S 字节"),
            ("pad", "略小（≤64KB 且 ≤5% S）", "文件 + 从头循环补到 S"),
            ("extent", "上面都配不上", "大文件上切不相交区间"),
            ("empty", "S == 0", "无 payload"),
        ],
        row_h=0.4,
    )

    # 8
    i = nxt()
    s = body_slide(prs, i, total, "一个对象：从轨迹走到仿真可读的 chunk")
    numbered(
        s,
        0.45,
        1.05,
        12.4,
        5.9,
        [
            "轨迹：PUT aws:us-east-1  obj=0028dd9e…  S=1,002,631。",
            "remap_zero 找到 linux-6.1.100/kernel/sched/core.c（文件 1,004,100）→ action=trim，只读前 S 字节。",
            "chunk_objects.py：FastCDC min=2KiB avg=8KiB max=64KiB，BLAKE3[0:8]；流式读恰好 S 字节。",
            "prep_chunkstore.py 写出 lens_u.npy / obj_uids_* / obj_meta.csv.gz。",
            "之后仿真 GET 跨区：只给「本区还没有的 unique chunk」计运费，不再给整份 1,002,631。",
            "typeA–D、2/3/6/9reg 共用同一套 T0xx（obj_key 与 size 相同）。ChunkStore 不管 TTL、不管 FB/FP。",
        ],
        size=16,
    )

    # 9 section runtime
    i = nxt()
    section_slide(
        prs,
        i,
        total,
        "第二部分（本组会重点）",
        "运行时：GET 如何赋 TTL",
        "先看作者以 object 为最小单位怎么走；\n"
        "再看单位换成 chunk 时，哪一步变了、哪一步必须不变。",
    )

    # 10
    i = nxt()
    s = body_slide(prs, i, total, "先把三件事拆开，不要混在一个变量里")
    simple_table(
        s,
        0.4,
        1.15,
        [2.6, 3.4, 3.3, 3.0],
        ["", "记什么", "键是谁", "干什么"],
        [
            ("直方图 X / lastX", "访问间隔 + 权重", "GET 发出区 B（n 本）", "学「B 上这份该留多久」"),
            ("TTL 表", "get_tevict(src,dst)", "有向边 A→B（n(n−1) 条）", "赋给 B 上这份副本"),
            ("传输账单", "实际拉了多少字节", "每条物理边 / 每块", "算钱，不写回直方图"),
        ],
        row_h=0.72,
        head_h=0.4,
    )
    tb(
        s,
        0.45,
        4.0,
        12.4,
        2.8,
        "作者公式里：X 在 dst=B；网络费 N 和 T_even 用的是边 A→B。\n"
        "所以 TTL 不是「对象的寿命」，而是「若 B 不留，下一次 miss 要付的那条边」。\n"
        "运行时 missing_bytes 禁止写进直方图：它依赖当前占用，会和 TTL 循环；作者直方图只依赖轨迹。",
        size=16,
        color=NAVY,
        bold=True,
    )

    # 11
    i = nxt()
    s = body_slide(prs, i, total, "作者 object 单位：一次 GET miss 逐步发生什么")
    flow_boxes(
        s,
        1.1,
        [
            ("① GET 在 B", "对象 O 的请求发在 B。\nB 本地没有这份副本 → miss。"),
            ("② 决定从 A 拉", "在仍持有 O 完整副本的区里，选 egress（再比 latency）最便宜的 A。"),
            ("③ 付 A→B 运费", "整对象 size 按边 A→B 单价计费。数据落到 B。"),
            ("④ 赋 TTL", "TTL = get_tevict(A, B)。\n挂在 B 上这份 object 副本。"),
        ],
        box_h=2.7,
    )
    tb(
        s,
        0.45,
        4.05,
        12.4,
        2.8,
        "同时：用这次 GET 更新 B 的直方图（间隔按对象、权重作者用 request.size）。\n"
        "12 小时窗口到了，用 B 的 X 和边 A→B 的 N 重算 TTL 表。\n"
        "关键：源 A 一旦选定，TTL 查的就是这一对 (A, B)，不是「B 的某个默认 TTL」。",
        size=16,
    )

    # 12
    i = nxt()
    s = body_slide(prs, i, total, "原理：为什么 TTL 必须是 A→B，而不是「B 的全局 TTL」")
    add_rect(s, 0.4, 1.15, 12.5, 1.35, LIGHT)
    tb(
        s,
        0.55,
        1.28,
        12.2,
        1.1,
        "TevictRanges 比较的是两条路：B 留下（付存储） vs B 不留（下次 miss 再付一次网络 N）。\n"
        "N 是「从哪拉」决定的。决定从 A 拉，N 就是 A→B 的单价。所以最优持有时长 T* 挂在边 A→B 上。",
        size=16,
        color=NAVY,
        bold=True,
    )
    add_rect(s, 0.4, 2.7, 6.1, 4.0, MINT)
    add_rect(s, 6.8, 2.7, 6.1, 4.0, PEACH)
    tb(s, 0.55, 2.85, 5.8, 0.35, "留下", size=16, bold=True, color=GREEN)
    bullets(
        s,
        0.5,
        3.3,
        5.9,
        3.2,
        [
            "B 付存储，直到下一次访问。",
            "成本项：间隔 ≤ T 的命中存储；窗口切掉的 last_X 也按 T 存。",
            "直方图 X 记在 B：B 多久会再要这个对象。",
        ],
        size=14,
    )
    tb(s, 6.95, 2.85, 5.8, 0.35, "不留", size=16, bold=True, color=RED)
    bullets(
        s,
        6.9,
        3.3,
        5.9,
        3.2,
        [
            "间隔 > T：先存满 TTL，再付一次 N。",
            "N = 边 A→B 的网络费（作者用这条边的 cost）。",
            "T_even = N / 存储单价，也是这条边的。",
        ],
        size=14,
    )

    # 13
    i = nxt()
    s = body_slide(prs, i, total, "直方图有 n 本，TTL 表有 n(n−1) 条边")
    simple_table(
        s,
        0.4,
        1.15,
        [2.4, 2.4, 2.5, 2.5, 2.5],
        ["配置", "直方图本数", "TTL 有向边", "2reg typeE 直觉", "含义"],
        [
            ("2reg", "2（typeE 常 1）", "2", "只有一条「对面→本区」", "对面就是唯一 A"),
            ("3reg", "3", "6", "每个 dst 两条入边", "从哪拉，用哪条 TTL"),
            ("6reg", "6", "30", "—", "同上"),
            ("9reg", "9", "72", "—", "同上"),
        ],
        row_h=0.55,
        head_h=0.4,
    )
    tb(
        s,
        0.45,
        4.1,
        12.4,
        2.7,
        "同一本 B 的直方图，可以配不同入边的 N，得到不同的 get_tevict(A,B)、get_tevict(C,B)。\n"
        "作者 object 路径：这次实际拉取源是谁，就查谁到 B。\n"
        "2 区时只有一个对面，object 和后面的 chunk 完全重合，没有混源问题。",
        size=16,
    )

    # 14
    i = nxt()
    s = body_slide(prs, i, total, "换成 chunk 单位：同一问句，只把「整对象」换成「缺的块」")
    add_rect(s, 0.4, 1.15, 12.5, 1.5, LIGHT)
    tb(
        s,
        0.55,
        1.3,
        12.2,
        1.25,
        "作者：B 没有对象 O → 从 A 拉整份 → TTL = A→B。\n"
        "我们：B 缺 O 的若干 unique chunk → 仍先选定完整副本源 A → 边 A→B 已定 → TTL = get_tevict(A, B)。\n"
        "个别块可能从更便宜的 C 走，那只改运费账单，不再选一次 TTL。\n"
        "B 上仍然是一个对象的一份 chunk-ref、一个 TTL。不能给每个 chunk 单独一条 TTL。",
        size=15,
        color=NAVY,
        bold=True,
    )
    numbered(
        s,
        0.45,
        2.9,
        12.4,
        4.0,
        [
            "直方图仍只在 GET 时更新，键仍是发出区 B；权重改为该对象 unique_bytes（离线、与占用无关）。",
            "跨区没有指针：拉完后块在 B 落成 B 的 unique。引用只发生在本区内部（refcount）。",
            "计费可以按块拆边；TTL 在选定 A 时已经定了，不要按字节再投一次票。",
        ],
        size=16,
    )

    # 15
    i = nxt()
    s = body_slide(prs, i, total, "Chunk GET miss：先定源 A（TTL 已定），再按块付账")
    flow_boxes(
        s,
        1.1,
        [
            ("① 选定源 A", "与作者相同：持有 O 完整副本、egress 最低的区。\n边 A→B 此刻已定。"),
            ("② TTL 已定", "TTL = get_tevict(A, B)。\n不再根据块从哪来改 src。"),
            ("③ B 缺哪些块", "本区已有：只 ref++，不传。\n没有的：要拉字节。"),
            ("④ 运费可拆边", "每块可走更便宜的 holder。\n只影响账单，不改 TTL。"),
        ],
        box_h=2.75,
    )
    tb(
        s,
        0.45,
        4.1,
        12.4,
        2.7,
        "你的直觉是对的：src→dst 一旦选定，TTL 就已经确定。\n"
        "上一版用「缺失 unique 字节最多的区」再选一次 TTL src，是多余的第三步，已删。\n"
        "2 区只有一个对面，选源、拉块、TTL 三条边重合。",
        size=15,
    )

    # 16
    i = nxt()
    s = body_slide(prs, i, total, "对照：object 路径 vs chunk 路径（同一套 TTL 函数）")
    simple_table(
        s,
        0.35,
        1.15,
        [2.5, 4.9, 4.9],
        ["步骤", "作者 object", "本策略 chunk"],
        [
            ("谁 miss", "B 没有对象副本", "B 缺该对象的若干 unique chunk"),
            ("选源", "持有完整副本、egress 最低的一区 A", "同一规则：仍先选定这一区 A"),
            ("运费", "logical size × 边 A→B", "缺失字节可按块走更便宜的边"),
            ("TTL src", "就是这个 A", "就是这个 A（选定即定，不按字节投票）"),
            ("TTL 公式", "get_tevict(A, B)", "同一个 get_tevict(A, B)"),
            ("挂在哪", "B 上这份 object 副本", "B 上这份对象的 chunk-ref 集合"),
            ("直方图", "B；权重 request.size", "B；权重 unique_bytes"),
        ],
        row_h=0.58,
        head_h=0.38,
    )

    # 17
    i = nxt()
    s = body_slide(prs, i, total, "例子 1：2 区（object 和 chunk 走成同一条边）")
    add_rect(s, 0.4, 1.15, 12.5, 5.5, LIGHT)
    numbered(
        s,
        0.55,
        1.3,
        12.2,
        5.2,
        [
            "区：A = us-east-1，B = us-west-1。对象 O 第一次 PUT 在 A。A 写入 O 的全部 chunk（FB 则 pin、ttl=−1）。",
            "稍后 GET 发在 B。B 没有 O 的副本，也没有这些 chunk。",
            "对面只有 A。无论 object 还是 chunk，拉取源都是 A。运费全是 A→B。",
            "TTL = get_tevict(A, B)。直方图更新在 B。",
            "TTL 内 B 再 GET O：hit，不拉新块；刷新仍用 get_tevict(A, B)（A 仍是最便宜完整副本）。",
            "TTL 到期：B 上 O 的 ref−−；若这些 chunk 没有被本区其它对象引用且无 pin，字节从 B 消失。",
        ],
        size=16,
    )

    # 18
    i = nxt()
    s = body_slide(prs, i, total, "例子 2：3 区——选定 A 则 TTL 已定；从 C 拉块只改账单")
    numbered(
        s,
        0.45,
        1.05,
        12.4,
        2.35,
        [
            "A 持有 O 的完整副本。C 因别的对象碰巧有 O 的部分 chunk。GET 发在 B。",
            "先按作者规则选定源：完整副本里 egress 最低的是 A。边 A→B 已定 → TTL = get_tevict(A, B)。到此 TTL 结束。",
            "付钱时：c1 从 C 更便宜就走 C→B，c2 从 A 走。账单可以拆边。这不会把 TTL 改成 C→B，也不按谁字节多再投票。",
        ],
        size=14,
    )
    simple_table(
        s,
        0.4,
        3.55,
        [2.8, 4.75, 4.75],
        ["", "决定 TTL 的 src→dst", "个别 chunk 的物理路径"],
        [
            ("何时定", "选定完整副本源 A 的瞬间", "之后按块找更便宜 holder"),
            ("边", "只有 A→B", "可以是 A→B 和 C→B 同时存在"),
            ("查表", "get_tevict(A, B)", "不算 TTL，只乘单价进账单"),
            ("不要做", "不要按 unique 字节再选一次 src", "不要给每个 chunk 一条 TTL"),
        ],
        row_h=0.62,
        head_h=0.38,
    )

    # 19
    i = nxt()
    s = body_slide(prs, i, total, "GET hit：没有新的拉取，刷新哪条边")
    bullets(
        s,
        0.45,
        1.15,
        12.4,
        5.8,
        [
            "B 已有这份对象的 chunk-ref（还在 TTL 内）→ hit，不传新块，不加新 unique。",
            "作者 hit：在「当前仍持有完整副本的其它区」里选最便宜的，用 get_tevict(那一区, B) 刷新到期时间。",
            "我们对齐同一条 hit 路径：刷新用最便宜完整副本，而不是重新跑 per-chunk gather（没有新拉取）。",
            "FB：origin PUT 区 ttl=−1，作者 src==base 时 origin GET 不靠这次刷新续命。",
            "ttl=0 是直方图选中了 0 小时档，不是「默认不缓存」。我们仍挂上，立刻可到期。",
        ],
        size=17,
    )

    # 20
    i = nxt()
    s = body_slide(prs, i, total, "PUT：数据跟写入区进来，不从外区拉，也不赋 A→B")
    add_rect(s, 0.4, 1.15, 6.1, 5.5, LIGHT)
    add_rect(s, 6.8, 1.15, 6.1, 5.5, CODE_BG)
    tb(s, 0.55, 1.3, 5.8, 0.4, "PUT 在 A，对象 O", size=16, bold=True, color=NAVY)
    bullets(
        s,
        0.5,
        1.85,
        5.9,
        4.5,
        [
            "本区没有的 chunk：随 PUT 写入，A 增加 unique 字节。",
            "本区已有：只 ref++，不加存储。",
            "B 有没有同一块，与这次 PUT 无关。",
            "不会出现「A 上部分 chunk 是指向 B 的指针」。",
        ],
        size=14,
    )
    tb(s, 6.95, 1.3, 5.8, 0.4, "TTL 在 PUT 上", size=16, bold=True, color=NAVY)
    bullets(
        s,
        6.9,
        1.85,
        5.9,
        4.5,
        [
            "FB：PUT 区 pin，ttl=−1，永不驱逐。",
            "FP：PUT 区也走 TTL（尚无第二副本时用 median T_even）。",
            "直方图：本策略只在 GET 上更新（对齐作者 simulator 只在 read 学习）。",
            "PUT 不是「从某区拉到 A」，所以不走 get_tevict(src, A) 那条 miss 路径。",
        ],
        size=14,
    )

    # 21
    i = nxt()
    s = body_slide(prs, i, total, "到期：只减本区引用，不会扯断外区")
    numbered(
        s,
        0.45,
        1.1,
        12.4,
        5.9,
        [
            "TTL 挂在 (对象 O, 区 B)，不是挂在单个 chunk、也不是全局块时钟。",
            "到期：O 在 B 的 ref−−（或 drop_pin，若这是 FP 允许离开的 pin）。",
            "某 chunk 在 B 上 pin==0 且 ref==0 → 这块 unique 字节从 B 的 cur_bytes 减掉。",
            "A 上同一块不受影响。没有跨区指针，所以不存在 C→A→B 断链。",
            "区内另一对象仍引用这块 → 字节留在 B，只是 O 这份副本走了。",
        ],
        size=17,
    )

    # 22
    i = nxt()
    s = body_slide(prs, i, total, "FB / FP 只改写副本寿命，不改 TTL 公式、不改区内账本")
    simple_table(
        s,
        0.4,
        1.15,
        [2.6, 4.85, 4.85],
        ["", "FB（--setbase）", "FP"],
        [
            ("PUT 区", "pin，ttl=−1，永不驱逐", "也算 TTL；可到期"),
            ("last-copy", "origin 不删", "全图至少留一份对象副本；只剩一份时 freeze"),
            ("第二副本落地", "—", "pending 的旧份才允许离开"),
            ("unique / 引用", "仍是区内的", "仍是区内的"),
            ("跨区指针", "无", "无"),
            ("GET miss 的 TTL", "选定的完整副本源 → B", "同一条：选定的完整副本源 → B"),
        ],
        row_h=0.62,
        head_h=0.4,
    )
    tb(
        s,
        0.45,
        5.6,
        12.4,
        1.3,
        "矩阵默认 FB；--fp 结果文件名加 _fp，避免覆盖已有 FB。",
        size=16,
        color=NAVY,
        bold=True,
    )

    # 23
    i = nxt()
    s = body_slide(prs, i, total, "整条事件循环（一次请求从进到出）")
    numbered(
        s,
        0.45,
        1.05,
        12.4,
        5.9,
        [
            "推进时间：结算存储（本区 unique 占用 × Δt）；弹出到期 heap，做上面的 eviction。",
            "若是 GET：用 unique_bytes 作权重，更新发出区 B 的直方图。PUT 不更新。",
            "PUT：本区 add_chunks(pin=True)；只给 fresh unique 加 cur_bytes。按 FB/FP 设寿命。",
            "GET hit：刷新 (O,B) 到期时间（最便宜完整副本 → B）。",
            "GET miss：先选定完整副本源 A，TTL=get_tevict(A,B)；再按块 gather 只计运费；add_chunks；挂 cached。",
            "12h 到：用各 dst 直方图 + 各边 N 重算 region_pairs_ttl。下次 miss 用新表。",
        ],
        size=16,
    )

    # 24 section code
    i = nxt()
    section_slide(
        prs,
        i,
        total,
        "第三部分",
        "代码落在哪、改过什么",
        "作者文件只读。组合类把 unique_bytes 喂进同一套 get_tevict。",
    )

    # 25
    i = nxt()
    s = body_slide(prs, i, total, "代码结构（OCP：不改作者文件）")
    code_box(
        s,
        0.4,
        1.1,
        12.5,
        5.7,
        "policy_tevict_ranges_new.py     TevictRangesV2          ← 作者，只读\n"
        "policy_chunk_tevict.py          组合 get_tevict\n"
        "                                直方图权重 = unique_bytes\n"
        "                                resolve_cache(src,dst) = get_tevict(选定的 A, B)\n"
        "region_state.py                 每区 pin/ref；fresh unique 才加 cur_bytes\n"
        "                                cheapest_missing_transfer → (bytes, $) 只计费\n"
        "aware_replay.py                 PUT/GET/过期/按块传输/FB·FP last-copy\n"
        "chunk_store.py                  离线 CSR：uids / unique_bytes\n",
        size=14,
    )

    # 26
    i = nxt()
    s = body_slide(prs, i, total, "源码：直方图权重 + TTL 查边")
    code_box(
        s,
        0.4,
        1.1,
        12.5,
        5.7,
        "def _hist_request(self, request):\n"
        "    w = float(self.chunk_store.unique_bytes(request.obj_key))\n"
        "    return request.model_copy(update={'size': w})   # 只改权重，间隔仍按对象\n"
        "\n"
        "def update_past_requests(self, request, place_region):\n"
        "    # place_region = GET 发出区 = 更新哪一本直方图\n"
        "    return self._ttl.update_past_requests(self._hist_request(request), place_region)\n"
        "\n"
        "def resolve_cache(self, req, src, dst, L=None):\n"
        "    ttl = float(self._ttl.get_tevict(src, dst, req.timestamp))\n"
        "    return max(ttl, 0.0), ObjFormat.DEDUP   # 0 也挂上，再到期",
        size=14,
    )

    # 27
    i = nxt()
    s = body_slide(prs, i, total, "源码：先定 A 并查 TTL，gather 只加运费")
    code_box(
        s,
        0.35,
        1.1,
        12.6,
        5.7,
        "src = min(完整副本区, key=egress)          # 选定 A，边 A→B 已定\n"
        "ttl, fmt = policy.resolve_cache(req, src, B)  # get_tevict(A, B)\n"
        "xfer, tcost = cheapest_missing_transfer(...) # 只算钱，不改 src\n"
        "cost['transfer'] += tcost                    # 个别块可走更便宜的 C\n"
        "add_chunks(store, s, key, pin=False)\n"
        "s.cached[key] = (now + max(ttl, 0), ttl, DEDUP)\n"
        "\n"
        "# 不要：用「谁贡献缺失字节最多」覆盖 src\n"
        "# add_chunks：本区还没有的 uid 才加 cur_bytes；已有只 ref++",
        size=14,
    )

    # 28
    i = nxt()
    s = body_slide(prs, i, total, "代码审计：对照今晚口径")
    add_rect(s, 0.4, 1.15, 6.2, 5.5, MINT)
    add_rect(s, 6.8, 1.15, 6.1, 5.5, PEACH)
    tb(s, 0.55, 1.28, 5.9, 0.35, "已对齐", size=16, bold=True, color=GREEN)
    bullets(
        s,
        0.5,
        1.75,
        6.0,
        4.7,
        [
            "GET 运费：按块 cheapest holder",
            "TTL：选定的完整副本源 A → GET 区 B",
            "直方图：仅 GET；权重 unique_bytes",
            "pin/ref：共享块不会被一个对象删光",
            "TTL 挂 (对象, 区)，hit 刷新",
            "不改 policy_tevict_ranges_new.py",
            "矩阵默认策略含 chunk_tevict",
        ],
        size=14,
    )
    tb(s, 6.95, 1.28, 5.8, 0.35, "原先的偏差（已修）", size=16, bold=True, color=RED)
    bullets(
        s,
        6.9,
        1.75,
        5.9,
        4.7,
        [
            "PUT 曾按 logical_size 计费",
            "直方图曾用 request.size",
            "直方图曾在 PUT 也更新",
            "ttl==0 曾直接不缓存",
            "TTL src 曾按「缺失字节最多」再选一次（已改回：选定 A 即定 TTL）",
            "缓存曾另加 manifest 字节",
        ],
        size=14,
    )

    # 29
    i = nxt()
    s = body_slide(prs, i, total, "学弟学妹复现清单")
    numbered(
        s,
        0.45,
        1.1,
        12.4,
        5.9,
        [
            "读 DATASET_PREP.md、CONTENT_POOL_STEPS.md、CHANGE_REGIONS.md。",
            "轨迹：SNIA 五条 complete → pickle → *.mc.aug；不要改 random.seed(1)。",
            "内容：wget 固定版本 → tar 进 file_pool → remap_zero →（T079）fill_family_pad --reindex。",
            "切块：chunk_objects.py {065,015,078,079,029} → prep_chunkstore.py。",
            "策略：aware_replay --config config/chunk_tevict_2reg_fb.yaml",
            "矩阵：run_fig5_matrix_parallel.py（默认含 chunk_tevict）。",
        ],
        size=16,
    )

    # 30
    i = nxt()
    s = body_slide(prs, i, total, "本地 vs 云")
    bullets(
        s,
        0.45,
        1.2,
        12.4,
        5.6,
        [
            "代码与 PPT 在仓库本地完成。现在不开云，不产生费用。",
            "云上才有：/data/file_pool、/data/dedup_exp/T0xx、全量轨迹与 Fig.5。",
            "下次开机：同步今晚改动 → T065 2reg 先打 tevict_ranges vs chunk_tevict。",
            "设计备忘：experiment_logs/tevict_ranges_dedup_per_region_unique_20260814.md",
        ],
        size=17,
    )

    # 31
    i = nxt()
    s = body_slide(prs, i, total, "请老师指正")
    bullets(
        s,
        0.45,
        1.2,
        12.4,
        5.6,
        [
            "消融是否干净：同一 TevictRanges TTL，只换区内 unique 账本与直方图权重。",
            "个别块从更便宜区走只改账单；TTL 始终跟这次选定的完整副本源 A。",
            "直方图用 unique_bytes 后，TTL 数值会与纯 SkyStore 略有不同（去重率随对象变化）。",
            "数值等开机后再跑。",
        ],
        size=17,
    )

    # 32
    i = nxt()
    s = body_slide(prs, i, total, "附录：关键命令（开机后）")
    code_box(
        s,
        0.4,
        1.1,
        12.5,
        5.7,
        "export REPO=/path/to/Skystore1212 PY=/data/venv/bin/python\n"
        "$PY $REPO/scripts/cloud/remap_zero.py --reindex\n"
        "$PY $REPO/scripts/cloud/chunk_objects.py 065\n"
        "$PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T065\n"
        "cd $REPO/skystore_python/artifact-eval-main/simulation\n"
        "$PY run_fig5_matrix_parallel.py --setups 2reg --traces T65 \\\n"
        "    --policies tevict_ranges,chunk_tevict",
        size=15,
    )

    # 33
    i = nxt()
    s = body_slide(prs, i, total, "附录：三个函数各管一层")
    code_box(
        s,
        0.4,
        1.1,
        12.5,
        5.7,
        "ChunkStore.unique_bytes(key)     离线：该对象 unique chunk 字节（直方图权重）\n"
        "missing / cheapest_missing_...   运行时：本区缺哪些块、从哪拉、付多少钱（不选 TTL）\n"
        "TevictRangesV2.get_tevict(A, B)  作者：用 B 的直方图 + 边 A→B 的 N，给出 TTL\n"
        "\n"
        "禁止：把 missing_bytes 写进 X[i]\n"
        "禁止：跨区指针 / 引用链\n"
        "禁止：给每个 chunk 单独 TTL",
        size=15,
    )

    # 34
    i = nxt()
    s = body_slide(prs, i, total, "附录：一页把 GET miss 讲完")
    add_rect(s, 0.4, 1.15, 12.5, 5.5, LIGHT)
    tb(
        s,
        0.6,
        1.4,
        12.2,
        5.1,
        "作者（object）\n"
        "  GET 在 B 且 B 没有 O  →  选完整副本源 A  →  整对象沿 A→B 传输  →  TTL = get_tevict(A, B)\n"
        "\n"
        "我们（chunk）\n"
        "  GET 在 B 且 B 缺 O 的块  →  仍先选完整副本源 A  →  TTL = get_tevict(A, B) 已定\n"
        "  →  缺的块可从更便宜 holder 走（只改账单）  →  落成 B 的 unique；TTL 挂在 (O, B)\n"
        "\n"
        "不变式：直方图在 B；N 在边；区内账本；无跨区指针。",
        size=16,
        color=NAVY,
        bold=True,
    )

    # 35
    i = nxt()
    s = body_slide(prs, i, total, "审查记录 2026-08-15 06:53")
    bullets(
        s,
        0.45,
        1.15,
        12.4,
        5.8,
        [
            "对照 simulator_v2.calculate_ttl 与 GET hit 刷新循环。",
            "已修：FP PUT 本区已有 uid 不刷新；别处还有 → min live get_tevict。",
            "已修：get_tevict 已是秒，禁止再 ×3600。",
            "今日补：源租约必须盖住 now+T（uid 的 chunk_exp；pin/freeze = 永生）。",
            "GET miss：运费 cheapest；TTL 为活着的 holder 上 min get_tevict。",
        ],
        size=16,
    )

    # 36
    i = nxt()
    s = body_slide(prs, i, total, "源必须活过 TTL：object → uid")
    bullets(
        s,
        0.45,
        1.15,
        12.4,
        5.8,
        [
            "作者：now + get_tevict(R_i,B) ≤ R_i 对象副本到期，否则这条边的 N 是假的。",
            "我们：同一条件，到期换成该 uid 在 R_i 的租约。",
            "第一个 holder 先收下；更小的 T 仅当源还活着；永生源可替换。",
            "lease_covers + min_live_ttl；hit / miss / FP PUT 共用。",
        ],
        size=17,
    )

    # 37
    i = nxt()
    s = body_slide(prs, i, total, "刻意适配 vs 前文过时")
    bullets(
        s,
        0.45,
        1.15,
        12.4,
        5.8,
        [
            "单位是区内 unique chunk，不是整对象副本。一次 GET 可半命中。",
            "直方图权重 unique_bytes。无跨区指针。",
            "上一页「TTL 挂 (O,B) / 一份副本一个 T」是 8/14 口径，现已作废。",
            "旧名 tevict_ranges_dedup、DedupAware 已删除。",
        ],
        size=17,
    )

    # 38
    i = nxt()
    s = body_slide(prs, i, total, "尚未用数字验证")
    bullets(
        s,
        0.45,
        1.15,
        12.4,
        5.8,
        [
            "云未开机。本记录只对照源码，没有新的 T065 对比。",
            "开机后：2reg T065，tevict_ranges vs chunk_tevict，先 FB 再 FP。",
            "3/6/9 区 FP 才是「源先死」过滤的主战场。",
        ],
        size=17,
    )

    # 39
    i = nxt()
    s = body_slide(prs, i, total, "流程总述（2026-08-15）")
    tb(
        s,
        0.4,
        1.05,
        12.5,
        6.1,
        "IBM/SNIA 轨迹只提供访问模式；每个 obj_key 的体积 S 取首次 PUT/GET 的 size。"
        "真实字节来自公开内容池：ECS 上约 169 棵 Linux 内核树（解压后每棵约 1.2–1.5 GB，覆盖 3.x/4.x/5.15/6.1/6.6/6.12 等固定版本环）、"
        "约 216 棵 PHP 源码树（5.3–8.4 官方 tarball，压缩包约 11–13 MB，解压后约百 MB 量级），"
        "以及 5 个 Ubuntu ISO（20.04.6 server/desktop、22.04.5 server、24.04.2/24.04.4 server，各约 1.5–5 GB）；"
        "映射只读 /data/file_pool，不跨 linux/php/ubuntu 混装、不用全零填充。"
        "绑定不是按 size 升序逐个造不重复新文件，而是 remap_zero.py 按 S 做最大优先贪心："
        "exact 整文件、trim 取头 S 字节（多出不超过 10 MB）、小缺口 pad 循环补齐、再不行则 extent；"
        "T079 的 11.39 GB / 12.23 GB 再用同族 linux 树 tar 拼到恰好 S。"
        "离线去重按 obj_key 流式读出恰好 S 字节，FastCDC（2K/8K/64K）切块，BLAKE3 指纹前 8 字节查全局 unique 表："
        "hit 复用 uid，miss 插入新行仍记该 uid。账单 = 各区 unique chunk 存储时长 + 各次跨区拉取。"
        "\n\n"
        "PUT 本地写入、无跨区运费。FB：本区该对象每一块（hit 或 miss）都 pin、TTL 永生（作者 ttl=-1 的 origin），之后不因 PUT/GET 刷新或过期。"
        "FP 与作者 calculate_ttl 一致：本区已有 chunk 不因 PUT 刷新；本区没有且全局无其它 holder → 中位 T_even/3；"
        "本区没有但别处还拿着该 uid → min_i get_tevict（源 uid 租约须盖住 now+T，pin/freeze 视为永生），不是把 hit 块也刷成同一值。"
        "GET 先把这次读记入发出区直方图，权重 size(o)=该对象 unique chunk 长度之和（hit+miss 都计入）。一次 GET 可半命中。"
        "本区非 pin 块是缓存 hit，刷新与作者 GET hit 相同：对其它仍持有该 uid 且源能活过这段 T 的区取 min get_tevict，否则中位 T_even/3；FB pin 不刷新。"
        "本区缺块是 miss：从费用最低的 holder 拉并计运费；TTL 同样走 min live get_tevict，不必等于刚付钱的那条边。"
        "FP 下某 uid 的全局最后一份冻结，直到另一区再拿到它。",
        size=12,
        color=INK,
    )

    # 40
    i = nxt()
    s = body_slide(prs, i, total, "TTL 对照：你的总结 vs 作者源码（2026-08-15）")
    tb(
        s, 0.4, 1.00, 12.5, 0.52,
        "不完全对。GET miss 的钱走最低 egress，TTL 不是那条边的 get_tevict，而是对其它仍活着的副本算 min get_tevict；"
        "GET hit 也不是「剩余保质期的最小值」，而是 min_i get_tevict(R_i→B)（源必须活过这段 T）。"
        "FB：origin 不刷新，但 cache 仍刷新。FP PUT：已有不刷新。",
        size=12, color=RED, bold=True,
    )
    simple_table(
        s, 0.35, 1.52,
        [1.35, 3.7, 3.72, 3.72],
        ["", "PUT（本地写，无运费）", "GET hit（本区已有）", "GET miss（本区没有，要拉）"],
        [
            [
                "FB",
                "issue 区即 origin：ttl=-1 永生，pin。已有也不改。",
                "origin（ttl=-1）不刷新。cache 仍刷新：min live get_tevict；无其它源→中位 T_even/3。",
                "只发生在非 origin。运费 cheapest；TTL=min live get_tevict（无其它副本→中位 T_even/3）。",
            ],
            [
                "FP",
                "已有：不刷新。新装且全局无 holder：中位 T_even/3。别处还有：与 GET miss 同一套 min live get_tevict。",
                "非 pin：min live get_tevict；无活着的源→中位 T_even/3。最后一份可 freeze。",
                "运费 cheapest；TTL=min live get_tevict，不必等于付钱那条边。无 holder→中位 T_even/3。",
            ],
        ],
        row_h=1.62, head_h=0.36, fsz=11,
    )
    add_rect(s, 0.35, 5.18, 6.22, 1.95, LIGHT)
    add_rect(s, 0.35, 5.18, 0.10, 1.95, TEAL)
    tb(s, 0.58, 5.24, 5.85, 0.28, "min live get_tevict — 为何要「源活过 now+T」", size=13, bold=True, color=NAVY)
    tb(
        s, 0.58, 5.52, 5.85, 1.52,
        "T=get_tevict(R_i→本区) 的意思：在本区再存 T 秒，存储费刚好对冲「下次从 R_i 再拉一次」的运费。"
        "若 R_i 在 now+T 前就过期，这条 N 是假的——本区到期后拉不到 R_i，会过早驱逐。"
        "所以更短的 T 只有源能撑到 now+T 时才能赢；pin/freeze 永生始终可当候选。"
        "取的是这些合法 T 的最小，不是付钱边，也不是别人还剩多少秒。",
        size=11, color=INK,
    )
    add_rect(s, 6.75, 5.18, 6.22, 1.95, PEACH)
    add_rect(s, 6.75, 5.18, 0.10, 1.95, GOLD)
    tb(s, 6.98, 5.24, 5.85, 0.28, "中位 T_even — 为何没有 holder 时用它", size=13, bold=True, color=NAVY)
    tb(
        s, 6.98, 5.52, 5.85, 1.52,
        "没有其它 holder 时不存在真实的 src 边，get_tevict 无从谈起，但 FP 仍要一个有限寿命"
        "（不能把第一份写成永生，否则就变成 FB origin）。"
        "T_even=N/S 是「存满这段时间 = 付一次运费」的盈亏平衡；没有指定源时 N 取全图跨区运费中位数。"
        "÷3 是作者默认系数。有 holder 后改走左边的 min live。",
        size=11, color=INK,
    )

    # 41
    i = nxt()
    s = body_slide(prs, i, total, "作者源码：TTL 从哪来（最后一页）")
    code_box(
        s, 0.35, 1.05, 12.6, 5.95,
        "# simulator_v2.calculate_ttl  — 新装一份时（GET miss 或 FP PUT 新副本）\n"
        "ttl = -1   # 默认永生\n"
        "if (not FB) or (base_region != dst):          # FB origin 直接保持 -1\n"
        "    region_list = 其它仍 ready 的副本\n"
        "    if not region_list:\n"
        "        ttl = medianN / S * 86400 / 3         # 中位 T_even\n"
        "    else:\n"
        "        # 运费另走 cheapest；这里只定 TTL\n"
        "        for R_i in region_list:\n"
        "            T = get_tevict(R_i, B)            # 秒\n"
        "            if 第一个 or R_i.ttl==-1 or (T更小 且 R_i 能活过 now+T):\n"
        "                选用这个 T\n"
        "\n"
        "# GET hit 刷新：仅 op==read 且 src!=base_region（FB origin 不刷新，FB cache 仍走下面）\n"
        "同样循环其它 ready 副本，取 min live get_tevict，再 set_ttl(now+T)\n"
        "\n"
        "# chunk_tevict：同一套 min_live_ttl，单位换成 uid\n"
        "# 不是 min(别人还剩多少秒)，而是 min(按直方图新算出的 T_evict)",
        size=13,
    )

    if n != total:
        raise SystemExit(f"slide count {n} != total {total}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    print(f"wrote {OUT} slides={n}")


if __name__ == "__main__":
    main()
