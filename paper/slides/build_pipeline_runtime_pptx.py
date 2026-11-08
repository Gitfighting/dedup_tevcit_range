"""Pipeline + runtime PPT with geometric background (图片1.png)."""
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
GRAY = RGBColor(0x3A, 0x3A, 0x3A)
CODE_BG = RGBColor(0xF7, 0xF4, 0xEE)
LIGHT = RGBColor(0xF4, 0xF7, 0xFB)
MINT = RGBColor(0xE4, 0xF0, 0xE8)
PEACH = RGBColor(0xF8, 0xEC, 0xE6)
CARD = RGBColor(0xFF, 0xFF, 0xFF)

W = Inches(13.333)
H = Inches(7.5)
FONT = "Microsoft YaHei"
CODE = "Consolas"

HERE = Path(__file__).resolve().parent
BG = HERE / "bg_lowpoly.png"
OUT = HERE / "TevictRangesDedup_pipeline_runtime_20260814.pptx"


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


def add_bg(slide):
    slide.shapes.add_picture(str(BG), Inches(0), Inches(0), width=W, height=H)


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


def bullets(slide, l, t, w, h, items, size=15, color=INK):
    box = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(6)
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
        p.space_after = Pt(7)
        run = p.add_run()
        run.text = f"{i + 1}.  {item}"
        _set_run_font(run, FONT, size, False, color)
    return box


def code_box(slide, l, t, w, h, text, size=12):
    add_rect(slide, l, t, w, h, CODE_BG)
    tb(slide, l + 0.12, t + 0.08, w - 0.2, h - 0.12, text, size=size, font=CODE, color=NAVY)


def footer(slide, n, total):
    add_rect(slide, 0, 7.32, 13.333, 0.18, NAVY)
    tb(slide, 0.4, 7.32, 9.2, 0.18, "真实文件映射 · FastCDC · PUT/GET 账本  2026-08-15", size=10, color=WHITE)
    tb(slide, 11.5, 7.32, 1.5, 0.18, f"{n} / {total}", size=10, color=WHITE, align=PP_ALIGN.RIGHT)


def body_slide(prs, n, total, title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide)
    add_rect(slide, 0.25, 0.95, 12.85, 6.22, CARD)
    add_rect(slide, 0, 0, 13.333, 0.82, NAVY)
    tb(slide, 0.4, 0.18, 12.5, 0.5, title, size=20, bold=True, color=WHITE)
    footer(slide, n, total)
    return slide


def section_slide(prs, n, total, kicker, title, blurb):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide)
    add_rect(slide, 0.7, 1.7, 11.9, 4.1, NAVY)
    add_rect(slide, 0.7, 1.7, 0.16, 4.1, GOLD)
    tb(slide, 1.1, 1.95, 11, 0.4, kicker, size=14, color=GOLD)
    tb(slide, 1.1, 2.45, 11, 1.2, title, size=30, bold=True, color=WHITE)
    tb(slide, 1.1, 3.85, 11, 1.5, blurb, size=16, color=RGBColor(0xD0, 0xDC, 0xE8))
    tb(slide, 1.1, 5.45, 11, 0.25, f"{n} / {total}", size=12, color=GOLD)
    return slide


def simple_table(slide, x0, y0, col_w, headers, rows, row_h=0.40, head_h=0.36, fsz=12):
    total_w = sum(col_w)
    add_rect(slide, x0, y0, total_w, head_h, NAVY)
    acc = x0
    for w, htxt in zip(col_w, headers):
        tb(slide, acc + 0.05, y0 + 0.04, w - 0.08, head_h - 0.06, htxt, size=12, bold=True, color=WHITE)
        acc += w
    for k, row in enumerate(rows):
        yy = y0 + head_h + k * row_h
        add_rect(slide, x0, yy, total_w, row_h, LIGHT if k % 2 == 0 else WHITE)
        acc = x0
        for w, cell in zip(col_w, row):
            tb(slide, acc + 0.05, yy + 0.05, w - 0.08, row_h - 0.08, cell, size=fsz, color=INK)
            acc += w


def flow_boxes(slide, y, boxes, box_h=2.4, start_x=0.45, gap=0.10):
    n = len(boxes)
    box_w = (12.4 - gap * (n - 1) - 0.26 * (n - 1)) / n
    palette = [NAVY, TEAL, GOLD, GREEN, RED]
    for i, (title, body) in enumerate(boxes):
        x = start_x + i * (box_w + gap + 0.26)
        add_rect(slide, x, y, box_w, box_h, LIGHT)
        add_rect(slide, x, y, box_w, 0.44, palette[i % len(palette)])
        tb(slide, x + 0.08, y + 0.06, box_w - 0.12, 0.34, title, size=12, bold=True, color=WHITE)
        tb(slide, x + 0.08, y + 0.52, box_w - 0.14, box_h - 0.62, body, size=11, color=INK)
        if i < n - 1:
            tb(slide, x + box_w, y + box_h / 2 - 0.16, 0.26, 0.32, "→", size=18, bold=True, color=GOLD, align=PP_ALIGN.CENTER)


def main():
    if not BG.exists():
        raise SystemExit(f"missing background: {BG}")
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    total = 38
    n = 0

    def nxt():
        nonlocal n
        n += 1
        return n

    # 1
    nxt()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(s)
    add_rect(s, 0.7, 1.55, 11.9, 4.4, NAVY)
    add_rect(s, 0.7, 1.55, 0.16, 4.4, GOLD)
    tb(s, 1.1, 1.85, 11, 1.3, "从真实文件到区内 chunk 账本", size=30, bold=True, color=WHITE)
    tb(
        s,
        1.1,
        3.25,
        11,
        2.0,
        "Linux / Ubuntu / PHP 选了哪些、多大；如何按 object_key 的 size 映射；\n"
        "FastCDC + BLAKE3 如何得到全局 uid；FB/FP 下 PUT 与 GET 如何赋 TTL；\n"
        "直方图用什么当 size(o)；账单如何记。并对照当前代码哪里一致、哪里矛盾。",
        size=16,
        color=RGBColor(0xD0, 0xDC, 0xE8),
    )
    tb(s, 1.1, 5.45, 11, 0.3, "另做一版 · 背景为仓库 图片1.png · 2026-08-14", size=12, color=GOLD)

    # 2
    i = nxt()
    s = body_slide(prs, i, total, "今天要走完的整条链（先记这 8 步）")
    numbered(
        s,
        0.5,
        1.15,
        12.3,
        5.9,
        [
            "下载固定版本的 linux / php / ubuntu，解压进 /data/file_pool（真实字节，禁止全零）。",
            "读轨迹第一次 PUT：得到 (object_key, S)，按 S 从小到大排序后逐一绑文件，同一文件字节不重复占用。",
            "离线 FastCDC（2K/8K/64K）切块，BLAKE3 前 8 字节当指纹，建成全局 unique 表 → uid。",
            "仿真只读 ChunkStore（uids + lens_u），不再跑 CDC。",
            "PUT：按本区账本对每个 chunk 做 hit/miss。FB miss 永驻 ttl=−1；FP miss 用中位 T_even（已含 /3）。",
            "GET hit：按作者方法刷新 TTL = min_i get_tevict(R_i→本区)；没有其它副本则用中位 T_even。",
            "GET miss：从拉取费用最低的区拉缺块，TTL 用这条 src→dst。",
            "直方图 size(o) = 该对象 chunk 长度之和（hit+miss 都算进这一次访问）。账单 = 本区 unique 存储 + 各区拉取费。",
        ],
        size=15,
    )

    # 3
    i = nxt()
    section_slide(
        prs, i, total, "第一部分", "真实文件选了哪些",
        "ECS 全量池约 406 GB、1800 万文件。论文最小复现用连续内核 + 三份 PHP + 五个 Ubuntu ISO。",
    )

    # 4
    i = nxt()
    s = body_slide(prs, i, total, "ECS 全量文件池（experiment_logs 2026-08-09 实测）")
    simple_table(
        s, 0.45, 1.15,
        [2.6, 1.6, 2.3, 2.4, 3.4],
        ["类别", "版本/项", "文件数", "总大小", "大小范围"],
        [
            ("Linux Kernel", "169 棵树", "14,015,137", "178.17 GB", "单文件 0B～72.71 GB"),
            ("PHP", "216 棵树", "4,112,747", "23.19 GB", "每棵约 52～141 MB"),
            ("Ubuntu（解压/根fs）", "5", "4,062", "13.56 GB", "1.38～4.05 GB"),
            ("Ubuntu ISO", "5 个 ISO", "5", "13.59 GB", "约 1.5～5 GB / 个"),
            ("SQL dump", "2", "~4", "2.12 GB", "T079 仅 1 个对象用到"),
            ("Mapped tars", "7", "7", "73.49 GB", "含 T079 的 11.39G / 12.23G"),
        ],
        row_h=0.48, fsz=13,
    )
    tb(s, 0.5, 5.15, 12.3, 1.7,
       "Bitnami/Debian OVA 在池里，但不是论文去重族，复现时跳过。\n"
       "合计约 18,143,065 个文件、~406 GB。最小复现不必下全 169+216，用下一页 canonical 集合即可跑通映射。",
       size=15, color=NAVY, bold=True)

    # 5
    i = nxt()
    s = body_slide(prs, i, total, "Linux：canonical 连续集 + 全量版本环")
    add_rect(s, 0.45, 1.1, 6.15, 5.7, LIGHT)
    tb(s, 0.6, 1.22, 5.9, 0.35, "最小连续集（必须）", size=16, bold=True, color=NAVY)
    bullets(s, 0.55, 1.65, 5.9, 4.9, [
        "linux-6.1.{100,101,102,103}.tar.xz",
        "每个压缩包约 130–150 MB；过小则是 HTML 错误页。",
        "解压后每棵约 1.2–1.5 GB，四棵约 6 GB。",
        "另建议：6.1.83/84，5.15.158/159，6.6.{145–149}，6.12.{98–102}。",
        "fill_family_pad 要求大约 ≥20 棵 200MB+ 的 linux 树。",
        "目录：/data/file_pool/linux-6.1.100/ …",
    ], size=13)
    add_rect(s, 6.8, 1.1, 6.05, 5.7, CODE_BG)
    tb(s, 6.95, 1.22, 5.8, 0.35, "ECS 全量（贴近去重率）", size=16, bold=True, color=NAVY)
    bullets(s, 6.9, 1.65, 5.8, 4.9, [
        "169 个 linux-* 顶层目录。",
        "含 3.0–3.19（.tar.gz），4.4.280–294，4.14.312–336，4.19.290–325，以及 5.x/6.x 补丁环。",
        "循环在 scripts/cloud/_max_dl.py、download_big.sh。",
        "大陆镜像：mirrors.aliyun.com/linux-kernel/。",
        "同族组合只使用 linux 树，不混 php/ubuntu。",
    ], size=13)

    # 6
    i = nxt()
    s = body_slide(prs, i, total, "PHP 与 Ubuntu：版本、个数、体积")
    tb(s, 0.5, 1.05, 12.3, 0.35, "PHP（第二族）", size=16, bold=True, color=NAVY)
    simple_table(
        s, 0.45, 1.42,
        [4.2, 2.6, 2.6, 2.9],
        ["版本", "压缩包", "解压后（约）", "角色"],
        [
            ("php-8.2.33 / 8.3.33 / 8.4.24", "各 11–13 MB xz", "各 100MB+", "最小复现三份"),
            ("php-5.3.29（museum.php.net）", "老包 .tar.gz", "较小", "极老补点"),
            ("ECS 全量 5.3–8.4 循环", "216 棵树", "合计 23.19 GB", "贴近 ECS 去重率"),
        ],
        row_h=0.42, fsz=12,
    )
    tb(s, 0.5, 3.35, 12.3, 0.32, "Ubuntu ISO（GB 级对象）+ base 根文件系统", size=16, bold=True, color=NAVY)
    simple_table(
        s, 0.45, 3.72,
        [5.8, 3.2, 3.3],
        ["文件", "约体积", "用途"],
        [
            ("ubuntu-20.04.6-live-server-amd64.iso", "约 1.2–1.5 GB", "GB 对象 / extent 母本"),
            ("ubuntu-20.04.6-desktop-amd64.iso", "约 3.8–4.1 GB", "同上"),
            ("ubuntu-22.04.5-live-server-amd64.iso", "约 2.0 GB", "同上"),
            ("ubuntu-24.04.2 / 24.04.4 live-server ISO", "约 2.5–3.1 GB", "同上"),
            ("focal/jammy/noble/bionic/xenial-base-amd64.tar.gz", "各数百 MB", "根文件系统树"),
        ],
        row_h=0.38, fsz=12,
    )

    # 7
    i = nxt()
    section_slide(
        prs, i, total, "第二部分", "真实文件如何映射到 object_key",
        "每个第一次 PUT 的 (object_key, S) 只绑一份内容。后续 GET 复用。禁止跨族混装，禁止 \\0 填充。",
    )

    # 8
    i = nxt()
    s = body_slide(prs, i, total, "映射输入：轨迹里只取第一次 PUT 的 size")
    numbered(s, 0.5, 1.1, 12.3, 2.5, [
        "读 typeE 2reg 的 *.mc-aug（obj_key 与 size 在 typeA–D、2/3/6/9reg 上相同，chunk 可共享）。",
        "只收 op=PUT。同一 object_key 只记第一次出现的 size → 字典 puts[key]=S。",
        "然后 objs = sorted(puts.items(), key=(S, object_key))：先按字节数升序，同 size 再按 key。",
        "代码：scripts/cloud/remap_zero.py → read_trace_objs()。",
    ], size=15)
    code_box(
        s, 0.45, 3.85, 12.4, 2.9,
        "def read_trace_objs(trace):\n"
        "    puts = {}\n"
        "    for row in csv.DictReader(...):\n"
        "        if row['op'].upper() == 'PUT' and key not in puts:\n"
        "            puts[key] = int(float(row['size']))\n"
        "    return sorted(puts.items(), key=lambda kv: (kv[1], kv[0]))  # size 升序",
        size=13,
    )

    # 9
    i = nxt()
    s = body_slide(prs, i, total, "池文件也按 size 排好，再逐对象构造字节（不重复）")
    bullets(s, 0.5, 1.1, 12.3, 2.35, [
        "全池扫描写出 /data/pool2_sorted.tsv：size<TAB>相对路径，按 size 数值排序。",
        "每个对象从这份有序表里找最近、尚未被整文件占用的真实文件，写出一份配方（manifest），磁盘文件本身不改。",
        "整文件一旦分给某个 object_key，本条轨迹里不再给别人当 exact/trim/pad。",
        "配不上的大对象：从更大文件上切不相交区间 [offset, offset+S)，这就是 extent，字节仍然不重叠。",
    ], size=15)
    tb(s, 0.5, 3.55, 12.3, 3.2,
       "实现细节（审查时必须说清）：数组按 size 升序排好，但 Pass 1 是从大对象往小走（descending），\n"
       "先让大对象抢走最接近的整文件，小对象再吃剩下的。这与「升序逐一」在顺序上不完全相同，\n"
       "但「一份字节不重复占用」仍然成立。跨五条轨迹可以共用同一池文件——每条轨迹有自己的 T0xx ChunkStore。",
       size=15, color=NAVY, bold=True)

    # 10
    i = nxt()
    s = body_slide(prs, i, total, "五种配方：如何从真实文件读出恰好 S 字节")
    simple_table(
        s, 0.4, 1.15,
        [1.8, 4.6, 5.9],
        ["action", "条件", "读出的字节"],
        [
            ("exact", "池文件 size == S", "整文件"),
            ("trim", "文件更大且差 ≤ 10 MB", "从头读 S 字节（例：core.c 1,004,100 → 读 1,002,631）"),
            ("pad", "略小：差 ≤ 64 KB 且 ≤ 5% S", "整文件 + 从头循环补到 S（不是 \\0）"),
            ("extent", "上面都配不上", "大文件上切 [offset, offset+S)，多对象不相交"),
            ("empty", "S == 0", "无 payload"),
        ],
        row_h=0.52, fsz=13,
    )
    tb(s, 0.5, 4.4, 12.3, 2.4,
       "禁止：跨 linux/php/ubuntu 混装进同一个对象；禁止全零填充。\n"
       "读出实现：chunk_objects.py 的 payload_buffers() 按 manifest 流式拼出恰好 size_trace 字节。",
       size=15, color=NAVY, bold=True)

    # 11
    i = nxt()
    s = body_slide(prs, i, total, "T079 两个超大对象：同族 linux 树组合成精确 size")
    numbered(s, 0.5, 1.1, 12.3, 2.3, [
        "单文件/extent 仍配不上：11,390,233,215 B 与 12,228,869,939 B。",
        "fill_family_pad.py 只使用 linux 树：整树 tar 追加 → 子目录 → 真实源码字节截取，|size−target|=0。",
        "输出 mapped_tars/fam_<size>.tar 后必须 remap_zero.py --reindex。T079 = 12582/12582 = 100%。",
    ], size=15)
    simple_table(
        s, 0.4, 3.7,
        [3.5, 5.5, 3.3],
        ["目标字节", "组成", "补齐"],
        [
            ("11,390,233,215", "12 整树 + 1×L1 + 2×L2", "18.0 MB linux 源码"),
            ("12,228,869,939", "13 整树 + 1×L1 + 4×L2", "15.4 MB 同源码流"),
        ],
        row_h=0.55, fsz=14,
    )

    # 12
    i = nxt()
    section_slide(
        prs, i, total, "第三部分", "离线 FastCDC：全局 unique 表",
        "仿真运行时不再切块。每个 object_key 变成一份 uid 列表。uid 是全局 unique chunk 的下标。",
    )

    # 13
    i = nxt()
    s = body_slide(prs, i, total, "按 object_key 流式切块：FastCDC + BLAKE3")
    flow_boxes(s, 1.15, [
        ("读配方", "打开 file_pool 里的真实文件，按 exact/trim/pad/extent 流式读恰好 S 字节。不物化成独立对象文件。"),
        ("FastCDC", "min=2 KiB，avg=8 KiB，max=64 KiB。跨 buffer 有 carry，避免切块被 32MB 缓冲切断。"),
        ("BLAKE3", "对每个 chunk 字节做 blake3().digest()[:8]，小端解释成 uint64 指纹。"),
        ("写出", "fps.npy / lens.npy 按对象顺序铺开；index.csv.gz 记 obj_key,start,count,size。"),
    ], box_h=2.55)
    tb(s, 0.5, 4.0, 12.3, 2.8,
       "脚本：scripts/cloud/chunk_objects.py {015,029,065,078,079}。\n"
       "typeA–D 与 2/3/6/9reg 共用同一套 T0xx（obj_key 与 size 相同）。\n"
       "这一步还没有去重：同一指纹可能在数组里出现多次。去重在下一页的全局表。",
       size=15)

    # 14
    i = nxt()
    s = body_slide(prs, i, total, "全局 unique 表：指纹 → uid（实现是 np.unique，不是 Bloom）")
    numbered(s, 0.5, 1.1, 12.3, 2.55, [
        "思想：全局表记录 unique chunk 的 fingerprint 与 index。对象切出一块就去表里搜：hit 记已有 uid，miss 则插入再记新 uid。对象始终存 uid 列表。",
        "当前代码：chunk_objects 先把所有块的指纹铺平；prep_chunkstore.py 用 numpy.unique(fps, return_inverse=True) 一次建成全局表。",
        "没有 Bloom 过滤器。等价结果：每个 object_key 得到一份不重复 uid 列表；lens_u[uid] 是这块的字节数。",
    ], size=15)
    code_box(
        s, 0.45, 3.9, 12.4, 2.85,
        "uniq, inverse = np.unique(fps, return_inverse=True)   # 全局指纹 → uid\n"
        "lens_u[inverse] = lens                                 # uid → 长度\n"
        "对每个 object:  u = unique(inverse[start:start+count])  # 该对象的 uid 列表\n"
        "写出 lens_u.npy, obj_uids_data.npy, obj_uids_indptr.npy, obj_meta.csv.gz",
        size=13,
    )

    # 15
    i = nxt()
    s = body_slide(prs, i, total, "仿真读 ChunkStore：运行时不再碰真实文件")
    code_box(
        s, 0.45, 1.15, 12.4, 5.6,
        "store.uids(key)          → 该 object_key 的 uid 数组\n"
        "store.lens_u[uid]        → 这块 unique 字节数\n"
        "store.unique_bytes(key)  → sum(lens_u[uids])   ← 直方图 size(o)\n"
        "store.logical_size(key)  → 轨迹 size，仅对照\n"
        "\n"
        "每个 region 另有一本运行时账本 RegionChunkState：\n"
        "  pinned[uid] / ref[uid]  > 0  ⇒ 本区磁盘上有这块（unique 占字节）\n"
        "  跨区没有指针。拉过来之后，块落成该区自己的 unique。",
        size=15,
    )

    # 16
    i = nxt()
    section_slide(
        prs, i, total, "第四部分", "运行时：PUT / GET / 直方图 / 账单",
        "TTL 在作者模型里挂在「本区这一份对象副本」上。账本按 chunk 计费。下面按 FB 与 FP 分开写。",
    )

    # 17
    i = nxt()
    s = body_slide(prs, i, total, "PUT（FB）：本区按 chunk 检索，miss 永驻")
    numbered(s, 0.5, 1.1, 12.3, 5.9, [
        "请求发在写入区 R。只付存储，不付跨区运费（数据跟 PUT 进来）。",
        "取出该 object_key 的 chunk 列表。对每一块查本区账本 pin/ref。",
        "Hit：本区已有（别的对象或旧缓存留下的）→ 只记索引，ref++ 或 pin++，不加存储字节。",
        "Miss：在本区账本记下这块，cur_bytes += lens_u[uid]，并标记这份 PUT 副本 ttl=−1，永不过期（pinned_objs）。",
        "代码：fixed_base_region=True 时 resolve_put 返回 −1；install_put → add_chunks(pin=True)。",
        "同一 object_key 若本区已有对象副本（has_copy），第二次 PUT 直接跳过。",
    ], size=16)

    # 18
    i = nxt()
    s = body_slide(prs, i, total, "PUT（FP）：miss 用中位 T_even；hit 也应刷新 TTL")
    numbered(s, 0.5, 1.1, 12.3, 3.3, [
        "同样按 chunk 做本区 hit/miss。Miss 的块写入本区 unique。",
        "这份副本的 TTL = 写入区上、用中位跨区运费算出的 T_even（秒，公式已含 /3）：",
    ], size=16)
    code_box(
        s, 0.45, 3.45, 12.4, 1.35,
        "T_even(秒) = median(N_src→dst) / (priceStorage(写入区) × 3) × 86400",
        size=15,
    )
    tb(s, 0.5, 5.0, 12.3, 1.8,
       "你的设计：已经 hit 的 chunk 也要把 TTL 更新成这次算出的值。\n"
       "当前代码：FP 的 resolve_put 走的是 ttl_for_region = get_tevict(入边伙伴, 写入区)，\n"
       "不是中位 T_even；且 has_copy 时整对象跳过，不会刷新已有 chunk 的 TTL。见审查页。",
       size=14, color=RED, bold=True)

    # 19
    i = nxt()
    s = body_slide(prs, i, total, "GET：先看本区 chunk 是 hit 还是 miss")
    add_rect(s, 0.4, 1.15, 6.15, 5.55, MINT)
    add_rect(s, 6.75, 1.15, 6.15, 5.55, PEACH)
    tb(s, 0.55, 1.3, 5.9, 0.4, "本区 hit（对象副本还在）", size=16, bold=True, color=GREEN)
    bullets(s, 0.5, 1.85, 5.9, 4.5, [
        "不拉新块，不加 unique。",
        "FB 或 FP 缓存命中：刷新 TTL。",
        "作者方法：TTL = min_i get_tevict(R_i → 本区)。",
        "若没有其它 R_i：用中位 T_even（已含 /3）。",
        "FP 的 PUT 区副本：同样按作者规则刷新，而不是一直 −1。",
    ], size=14)
    tb(s, 6.9, 1.3, 5.9, 0.4, "本区 miss（缺块）", size=16, bold=True, color=RED)
    bullets(s, 6.85, 1.85, 5.9, 4.5, [
        "在仍持有这些字节的区里，选拉取费用最低的源。",
        "缺的块拉到本区，落成本区 unique。",
        "TTL = get_tevict(src, dst)，挂在本区这一份副本。",
        "运费按实际拉了多少 unique 字节 × 边单价。",
        "直方图：这次 GET 仍按整个对象的 chunk 长度和计入，不只记 miss 的块。",
    ], size=14)

    # 20
    i = nxt()
    s = body_slide(prs, i, total, "GET hit 刷新：与原论文一致的 min_i TTL")
    bullets(s, 0.5, 1.1, 12.3, 2.5, [
        "作者 simulator_v2：在其它仍持有该对象的区上循环，取 get_tevict(R_i, 本区) 最小的那个（并检查源副本尚未到期）。",
        "没有其它副本时，用中位跨区运费算 T_even（公式里已经 /3）。",
        "这不是「按 egress 最低的区再查一次表」——先比的是 TTL 数值，不是单价。",
    ], size=15)
    tb(s, 0.5, 3.7, 12.3, 3.0,
       "2026-08-15 已补：min_i 只接受「该 uid 在源区的租约盖住 now+T」的 R_i（pin / freeze 视为永生）。\n"
       "与 simulator_v2 的 phys.storage_start+ttl 过滤同一句话，单位从对象副本换成 uid。",
       size=16, color=NAVY, bold=True)

    # 21
    i = nxt()
    s = body_slide(prs, i, total, "GET miss：费用最低的区拉取，TTL 用这条 src→dst")
    flow_boxes(s, 1.15, [
        ("选定源", "仍持有该对象内容的区里，egress（再比 latency）最低的 A。边 A→B 已定。"),
        ("赋 TTL", "TTL = get_tevict(A, B)。选定源的瞬间 TTL 已定，不再按字节投票。"),
        ("拉缺块", "本区已有的 chunk：只 ref++。没有的：从费用最低的 holder 拉（账单可拆边）。"),
        ("挂上", "缺块落成 B 的 unique。TTL 挂在 (object_key, B) 这一份引用上。"),
    ], box_h=2.55)
    tb(s, 0.5, 4.05, 12.3, 2.7,
       "你写的「赋值给当前 chunk」：作者模型里一份对象副本只有一个 T，不能每块一条 TTL。\n"
       "实现上是：每个 chunk 有本区 pin/ref 账本；TTL 时钟挂在对象副本上，到期时整份引用一起 ref−−。\n"
       "这是审查里的核心粒度问题，见后面对照页。",
       size=15)

    # 22
    i = nxt()
    s = body_slide(prs, i, total, "直方图：Tnext / Last 的 size(o) = 该对象 chunk 长度和")
    bullets(s, 0.5, 1.1, 12.3, 5.9, [
        "只在 GET 上更新（与作者 simulator 只在 read 学习一致）。PUT 不进直方图。",
        "键是 GET 发出区。2reg typeE 常常只有 1 本有效直方图；3/6/9reg 各区一本。",
        "间隔分桶不变：该区、该对象两次访问的间隔。Last 是窗口内还没见到下次访问的残余。",
        "你的规定：这一次访问把该 object_key 所列 chunk 的 hit 与 miss 都算进去，size(o)=这些 chunk 长度之和。",
        "代码：unique_bytes(obj) = sum(lens_u[uids])。对象内部 uid 已 unique，故等于该对象 unique chunk 长度和。",
        "不要把运行时 missing_bytes 写进 X[i]（依赖占用，会和 TTL 循环）。",
        "三项公式、find_min、12 小时重算 pair TTL：不改 policy_tevict_ranges_new.py。",
    ], size=15)

    # 23
    i = nxt()
    s = body_slide(prs, i, total, "真实记账：本区 unique 存储费 + 各区拉取费")
    add_rect(s, 0.4, 1.15, 6.15, 5.55, LIGHT)
    tb(s, 0.55, 1.3, 5.9, 0.4, "存储", size=16, bold=True, color=NAVY)
    bullets(s, 0.5, 1.85, 5.9, 4.5, [
        "每个区 cur_bytes = 本区 pin 或 ref > 0 的 unique chunk 字节和。",
        "时间推进：gbh += (cur_bytes/GB) × Δt/3600。",
        "美元：gbh × priceStorage × 3/24（与作者 GB·hour 口径一致）。",
        "共享：本区另一对象引用同一 uid，不加倍。",
    ], size=14)
    add_rect(s, 6.75, 1.15, 6.15, 5.55, CODE_BG)
    tb(s, 6.9, 1.3, 5.9, 0.4, "传输", size=16, bold=True, color=NAVY)
    bullets(s, 6.85, 1.85, 5.9, 4.5, [
        "只在 GET miss 发生。PUT 无跨区运费。",
        "对每个本区还没有的 uid，选 egress 最低的持有区。",
        "费用 = Σ 字节 × 该边单价 / GB。",
        "总账单 = 各区存储之和 + 各次拉取之和。",
    ], size=14)

    # 24
    i = nxt()
    section_slide(
        prs, i, total, "第五部分", "对照当前代码：一致与矛盾",
        "思想可以写进 PPT；落地必须诚实。下面把你今晚的流程逐条对过 aware_replay / region_state / remap / CDC。",
    )

    # 25
    i = nxt()
    s = body_slide(prs, i, total, "已对齐（可以按这一页讲实现）")
    simple_table(
        s, 0.35, 1.1,
        [3.3, 9.0],
        ["条目", "代码实际做什么"],
        [
            ("真实文件族", "linux / php / ubuntu ISO+base；ECS 169+216+5 ISO"),
            ("映射排序", "第一次 PUT 的 (key,S)；sorted by (S, key)"),
            ("不重复占用", "整文件不复用；extent 区间不相交"),
            ("FastCDC+BLAKE3", "2K/8K/64K；digest[:8] → uint64"),
            ("全局 uid", "prep_chunkstore np.unique；对象存 uid 列表"),
            ("PUT 本区 hit/miss", "add_chunks：fresh unique 才加 cur_bytes"),
            ("FB PUT", "resolve_put 返回 −1；pin 永驻"),
            ("直方图 size(o)", "GET 上 unique_bytes = chunk 长度和"),
            ("账单", "本区 unique×时间 + miss 拉取费"),
            ("GET miss 选源", "完整副本里 egress 最低；TTL=该边 get_tevict"),
        ],
        row_h=0.42, fsz=12,
    )

    # 26
    i = nxt()
    s = body_slide(prs, i, total, "矛盾与部分对齐（审查结论）")
    simple_table(
        s, 0.35, 1.1,
        [3.4, 8.9],
        ["你今晚的表述", "当前代码"],
        [
            ("切块时 Bloom 查全局表", "无 Bloom；切完后 np.unique 一次性建表"),
            ("映射「从小到大逐一」", "数组升序，但 Pass1 从大对象往小分配"),
            ("TTL 赋给当前 chunk", "TTL 挂在 (对象, 区)，账本才是 per-chunk"),
            ("FP PUT = 中位 T_even", "走 get_tevict(入边伙伴, 写入区)，不是中位"),
            ("FP PUT 刷新已 hit 的 chunk TTL", "has_copy 则整对象 skip，不刷新"),
            ("GET hit = min_i TTL(R_i→R)", "先 min egress，再 get_tevict(那一区)"),
            ("GET miss 每块一条 TTL", "一跳选定 A 后整份副本一个 T；拉取可按块计费"),
        ],
        row_h=0.55, fsz=13,
    )

    # 27
    i = nxt()
    s = body_slide(prs, i, total, "核心粒度：为什么代码没有「每 chunk 一个 TTL」")
    bullets(s, 0.5, 1.1, 12.3, 5.9, [
        "作者 TevictRanges 的直方图按对象访问间隔累积，输出 get_tevict(src,dst) 赋给 dst 上这一份副本。",
        "若每个 chunk 自己一条 TTL，Tnext 的「下一次访问」不再有定义（同一 GET 会同时命中/未命中不同块）。",
        "因此实现拆成两层：时钟在 (object_key, region)；字节账本在 uid 的 pin/ref。",
        "GET miss 时「给当前 chunk 赋 src→dst TTL」在作者语义下等于：这份对象副本的 TTL = get_tevict(src,dst)；各 chunk 只决定拉不拉、占不占字节。",
        "若坚持物理上每块不同到期时间，就不再是 TevictRanges，需要另写模型。本组会默认不改作者公式文件。",
    ], size=16)

    # 28
    i = nxt()
    s = body_slide(prs, i, total, "建议：哪些该改代码，哪些保持作者语义")
    add_rect(s, 0.4, 1.15, 6.15, 5.55, MINT)
    tb(s, 0.55, 1.3, 5.9, 0.4, "建议改（更贴你今晚的话）", size=16, bold=True, color=GREEN)
    bullets(s, 0.5, 1.85, 5.9, 4.5, [
        "GET hit 刷新改为 min_i get_tevict(R_i→R)，没有 R_i 则中位 T_even。",
        "FP PUT 的缺省 TTL 改为中位 T_even（已含 /3），与作者 PUT 口径对齐。",
        "PPT/文档写明：无 Bloom，等价于事后 unique。",
        "映射页写明：大对象优先抢整文件，不是严格升序贪心。",
    ], size=14)
    add_rect(s, 6.75, 1.15, 6.15, 5.55, PEACH)
    tb(s, 6.9, 1.3, 5.9, 0.4, "建议保持", size=16, bold=True, color=RED)
    bullets(s, 6.85, 1.85, 5.9, 4.5, [
        "TTL 仍挂 (对象, 区)，不要每块一个时钟。",
        "直方图 size(o)=unique chunk 长度和，含 hit+miss。",
        "GET miss：选定完整副本源 A 即定 TTL；按块 gather 只改账单。",
        "不改 policy_tevict_ranges_new.py。",
    ], size=14)

    # 29
    i = nxt()
    s = body_slide(prs, i, total, "附录：复现命令（开机后）")
    code_box(
        s, 0.4, 1.1, 12.5, 5.7,
        "export REPO=/path/to/Skystore1212 PY=/data/venv/bin/python\n"
        "# 下载 + 解压见 CONTENT_POOL_STEPS.md §2–3\n"
        "$PY $REPO/scripts/cloud/remap_zero.py --reindex\n"
        "$PY $REPO/scripts/cloud/fill_family_pad.py          # 仅 T079 两大对象\n"
        "$PY $REPO/scripts/cloud/remap_zero.py --reindex\n"
        "for t in 015 065 078 079 029; do\n"
        "  $PY $REPO/scripts/cloud/chunk_objects.py $t\n"
        "  $PY $REPO/scripts/cloud/prep_chunkstore.py /data/dedup_exp/T$t\n"
        "done",
        size=14,
    )

    # 30
    i = nxt()
    s = body_slide(prs, i, total, "附录：关键路径")
    code_box(
        s, 0.4, 1.1, 12.5, 5.7,
        "/data/datasets/{linux,php,ubuntu,iso}     未解压官方包\n"
        "/data/file_pool/                          真实字节（映射只读这里）\n"
        "/data/manifests/*.manifest.csv.gz         obj_key → 配方\n"
        "/data/dedup_exp/T0xx/                     fps/lens + ChunkStore CSR\n"
        "scripts/cloud/remap_zero.py               映射\n"
        "scripts/cloud/chunk_objects.py            FastCDC + BLAKE3\n"
        "scripts/cloud/prep_chunkstore.py          全局 uid 表\n"
        "src/dedup/aware_replay.py                 PUT/GET 回放\n"
        "src/placement_policy/policy_chunk_tevict.py\n"
        "文档：CONTENT_POOL_STEPS.md  DATASET_PREP.md",
        size=14,
    )

    # 31
    i = nxt()
    s = section_slide(
        prs, i, total,
        "审查记录",
        "2026-08-15 06:53  对照作者后再改",
        "chunk 级补上「源租约必须盖住这段 TTL」。下面记下已对齐、刻意适配、以及本 PPT 前文过时的句子。",
    )

    # 32
    i = nxt()
    s = body_slide(prs, i, total, "2026-08-15：已与作者对齐")
    bullets(s, 0.45, 1.15, 12.4, 5.8, [
        "直方图只在 GET 更新；权重 = unique_bytes（对象级间隔，chunk 体积）。",
        "FB PUT：pin 永生，无跨区传输。",
        "FP PUT：本区已有 uid 不刷新；本区没有、别处也没有 → 中位 T_even/3；别处还有 → min live get_tevict。",
        "GET hit：非 pin 的 uid 刷新；min_i get_tevict；无其它 holder → 中位 T_even/3。",
        "GET miss：运费按最便宜 holder；TTL 按仍活着的 holder 取 min get_tevict（不必等于付钱那条边）。",
        "源活着：now + T_evict(R_i→B) ≤ 该 uid 在 R_i 的到期；pin / freeze=inf 等同作者 ttl=-1。",
        "get_tevict 已是秒（find_min 内 ×3600），不再二次乘 3600。",
        "FP last-copy：按 uid 冻结，第二份出现后释放。",
    ], size=15)

    # 33
    i = nxt()
    s = body_slide(prs, i, total, "源副本必须活过 TTL（object → uid）")
    bullets(s, 0.45, 1.15, 12.4, 5.8, [
        "作者：now + get_tevict(R_i,B) ≤ R_i 上那份对象副本的到期，否则这条边的 N 是假的。",
        "chunk：同一句话，到期时刻换成该 uid 在 R_i 的 chunk_exp。",
        "规则与 simulator_v2 循环一致：第一个 holder 先收下；永生源可替换；更小的 T 仅当源租约盖住 now+T。",
        "2 区 FB 几乎总是对面 pin，过滤空转。3/6/9 区 FP 才容易出现「公式更短但源先死」。",
        "代码：region_state.lease_covers + aware_replay.min_live_ttl，hit / miss / FP PUT 共用。",
    ], size=16)

    # 34
    i = nxt()
    s = body_slide(prs, i, total, "刻意适配（不是没对上）")
    bullets(s, 0.45, 1.15, 12.4, 5.8, [
        "时钟挂在 (region, uid)，不是 (region, object)。一次 GET 可半命中。",
        "直方图 size(o) 用 unique_bytes，不用逻辑对象大小。",
        "账单按块 cheapest；TTL 按活着的 holder min（作者对象级也是钱和 TTL 可以不是同一条边）。",
        "作者 dict 遍历顺序下「永生源无条件覆盖当前选择」是实现细节；我们永生源参与同一套比较。",
        "前文第 21 页仍写「TTL 挂在对象副本、不能每块一条 TTL」——那是 8/14 的旧口径，以本页为准。",
    ], size=16)

    # 35
    i = nxt()
    s = body_slide(prs, i, total, "审查时仍须记住")
    bullets(s, 0.45, 1.15, 12.4, 5.8, [
        "不改 policy_tevict_ranges_new.py；get_tevict 只读复用。",
        "无跨区指针：缺块拉到本区自己存。",
        "旧名 tevict_ranges_dedup / DedupAware 已删，配置只留 chunk_tevict。",
        "云未开机，本记录只对照源码，没有新的 T065 数字。",
        "矩阵：tevict_ranges vs chunk_tevict；分母仍是 SkyStore。",
    ], size=16)

    # 36
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

    # 37
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

    # 38
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
    print(f"wrote {OUT} slides={n} bg={BG.exists()}")


if __name__ == "__main__":
    main()
