#!/usr/bin/env python3
"""封面生成（模板拼图）：把一篇文章的**视觉论据**排成 16:9 封面图。

设计依据：
- ADR-0013：头条封面必填；兜底链是 AI 生图 → 模板拼图，**不用新闻原图**（版权）
- Q43/B：当前选择"先只用模板拼图"（无图片 provider key）
- ADR-0023：纯 PIL 绘制，不启动浏览器、不联网，内存开销极小
- ADR-0044：封面不复述标题，改为承载一个视觉论据（数字 / 对比 / 断言）

本脚本只做确定性绘制：不判断标题好坏（那是分拣与写作的事）。

## 为什么封面不能"把标题排一遍"

信息流里标题就印在封面旁边。一个只复述标题的封面**不增加任何信息**，
却在缩略图尺寸下把字号压到读不出来——两头的坏处都占了。实测旧版：
1200x675 画布上亮像素仅占 4.7%，缩到信息流 320px 宽时正文只有约 8px 高。

所以封面只承载**一个**元素，且必须能在 320px 宽下读出来：
- `number`   —— 一个数字占满画面（"8160 亿"），配一句论点
- `contrast` —— 两个短句撞在一起（"模型免费" / "运维不免费"）
- `statement`—— 一句断言

## 字号一律测量后自适应

所有文字都经 `fit_font()` / `fit_block()` 由**测量**决定字号，绝不写死尺寸再赌
它放得下。写死尺寸的后果是溢出画布——那比难看更糟，因为无人值守时没人看得见。

## 封面不印任何脚注

作品声明走平台的字段（`publish.py --declaration`），不在图上的像素里重复。
在图上再印一行脚注要占掉 7.5% 的画布高度——而封面上每一寸都该留给论据。
故 `--note` 默认为空。

（作品声明当前默认为「个人观点」而非「引用AI」，那是一个带已知合规风险的
有意偏离，见 ADR-0046。封面不涉及该决定：图上一个字都不印。）
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont
except ImportError:  # 提示用 wewrite 的 python 运行
    print(
        "错误：未安装 Pillow。本脚本需用 wewrite 的 Python 运行：\n"
        "  .uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py ...",
        file=sys.stderr,
    )
    sys.exit(2)

# 头条要求：建议 16:9，尺寸不小于 400x200（ADR-0024 记录的平台约束）
DEFAULT_SIZE = (1200, 675)

INK = (8, 10, 16)
INK_SOFT = (22, 34, 54)
WHITE = (247, 249, 253)
MUTED = (126, 138, 162)
BLUE = (64, 140, 255)
ORANGE = (255, 106, 61)
MINT = (52, 211, 153)

# ── 缩略图可读性：从「声明」变成「测量」──────────────────────────────
#
# 平台下发到信息流的封面资源实测是 640x360（`tplv-tt-shrink:640:0`）。
# 640 是给视网膜屏的 2x 资源，对应 CSS 展示宽约 320px；最保守按 320 算。
FEED_THUMB_W = 320
# 缩略图宽度下，一段文字要被认出来至少要这么高（px）。
# iOS/Android 正文最小可读字号约 11-12px；汉字在 8px 以下基本糊成一团。
READABLE_PX = 11.0
# 由 FEED_THUMB_W / 画布宽 换算出的**画布内**最小字号（1200 宽、11px 时约 42px）。
# 注意它只是「下限」，具体每个元素仍由测量决定。
# ⚠️ 必须向上取整：`int(11.0 / 0.2667) = 41`，而 41 * 0.2667 = 10.9px ——
# 一个自己都通不过自己检查的下限。这种自相矛盾会让「缩到下限」变成静默失败。
MIN_READABLE = math.ceil(READABLE_PX / (FEED_THUMB_W / DEFAULT_SIZE[0]))

# 主元素在缩略图下至少 14px 高 → 1200 宽下至少 52px
MIN_HERO = 52
# 次要文字（kicker / unit）的下限。它必须 >= MIN_READABLE，否则「缩到下限」
# 与「缩略图可读」两条约束互相打架。
# ⚠️ 2026-10-20 实测缺陷：这里原先是 30，即缩略图下 8px——技术上"放得下"，
# 实际上读不出。而自检只回报 `hero_px_at_thumb = MIN_HERO * scale`（一个常量），
# 于是它从不为 8px 的 kicker 报警。**自检必须测量实际画上去的字号，而不是
# 复述设计意图的常量。** 这就是 ADR-0044 发现六。
MIN_MUTED = max(MIN_READABLE, 42)
# kicker 是**标签**不是句子。超过这个长度它就只能被缩到下限再截断，
# 而在缩略图下那是一串读不出的字。与其让它缩到不可辨，不如先截短。
KICKER_MAX = 16

# ── 照片层（ADR-0048）────────────────────────────────────────────
#
# 照片占上方，文字占下方**实心黑块**，中间一条蓝线分隔。
#
# 为什么不是「照片铺满 + 半透明黑遮罩」（更常见的做法）：
# 实测那张 CC0 机房照片，左渐变遮罩方案下橙字压在格子衬衫上，局部对比度塌陷
# （line1 区亮度均值 119.5 / 标准差 90.9，而纯模板封面是 80.7 / 107.6）；
# 全幅压暗到 86% 文字才够读，但那时照片本身只剩模糊剪影，等于花复杂度换一张
# 几乎无效的底图。上下分区是唯一同时满足「照片完整可见」和「文字对比度最好」
# 的方案：文字区是纯 INK 底色，与无照片封面完全一致。
PHOTO_BAND = 0.50        # 照片占画布高度比例
PHOTO_DIVIDER = 6        # 照片与文字区之间的蓝线高度
MIN_PANEL_H = 240        # 文字区最小高度：再小就会把字压到缩略图读不出
# 文字区内，kicker 带占的高度比例。布局函数靠它把「正文居中区」压到 kicker 之下。
#
# ⚠️ 为什么需要这个：布局函数原本是在**整幅 675 高**的画布上居中的，kicker 在
# 6.8% 处、正文在 35% 处，天然不撞。一旦画布变成 300 多高的文字区，两个比例
# 同时缩下来就叠在一起了——实测第一版照片封面上「AI 编程工具」与正文首行
# 基线只差十几像素，看着像漏对齐。比例缩放不会保持间距，只会保持比例。
KICKER_BAND = 0.24

CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

_FONT_PATH: str | None = None
_FONT_CACHE: dict[int, object] = {}


def _font_path() -> str | None:
    global _FONT_PATH
    if _FONT_PATH is None:
        for p in CJK_FONT_CANDIDATES:
            if Path(p).exists():
                _FONT_PATH = p
                break
        else:
            _FONT_PATH = ""
    return _FONT_PATH or None


def find_font(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    p = _font_path()
    if p:
        try:
            f = ImageFont.truetype(p, size)
            _FONT_CACHE[size] = f
            return f
        except OSError:
            pass
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


# ── 测量驱动的排版工具 ────────────────────────────────────────────


def text_w(d, text: str, font) -> float:
    return d.textlength(text, font=font)


def fit_font(d, text: str, max_w: float, start: int, floor: int = MIN_HERO):
    """从 start 起逐级缩小，返回第一个能让 text 单行放进 max_w 的字号。

    ⚠️ 必须先 `max(start, floor)`：调用方传的 start 是按画布比例算的（如
    `int(H * 0.062)` = 41），可能**本身就低于 floor**。此时 while 条件
    `size > floor` 一开始就为假，函数会把低于下限的字号原样返回——
    于是「缩到下限为止」的约束从第一步就没生效。
    实测：kicker 用 41px 画出去，缩略图下 10.9px，而它自己的下限是 11px。
    """
    size = max(start, floor)
    while size > floor and text_w(d, text, find_font(size)) > max_w:
        size -= 4
    return find_font(size), size


def wrap_cjk(d, text: str, font, max_w: float) -> list[str]:
    """按像素宽度折行。中文逐字断，英文/数字整体保留。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        if text_w(d, trial, font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def fit_block(d, text: str, max_w: float, max_lines: int, start: int, floor: int = MIN_HERO):
    """在 max_lines 行内放下的最大字号。放不下就缩，缩到 floor 仍放不下则截断。

    同 `fit_font`：start 可能低于 floor，必须先夹到 floor 之上。
    """
    size = max(start, floor)
    while size > floor:
        font = find_font(size)
        lines = wrap_cjk(d, text, font, max_w)
        if len(lines) <= max_lines:
            return font, size, lines
        size -= 4
    font = find_font(floor)
    lines = wrap_cjk(d, text, font, max_w)[:max_lines]
    # 末行加省略号，明确表示"这里被截断了"而不是悄悄少一句话
    if lines and len(wrap_cjk(d, text, font, max_w)) > max_lines:
        last = lines[-1]
        while last and text_w(d, last + "…", font) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return font, floor, lines


def draw_center(d, text: str, font, y: int, fill, W: int) -> None:
    d.text(((W - text_w(d, text, font)) / 2, y), text, font=font, fill=fill)


def clip_to_width(d, text: str, font, max_w: float) -> str:
    """把 text 截到 max_w 内，末位加省略号。

    为什么必须有这一步：`fit_font` 缩到 floor 就停手，若文字在 floor 字号下仍
    超宽，它会**原样返回超宽文本**——画出去就是溢出画布。溢出比难看更糟：
    无人值守时没人看得见，而它每天都发生。
    """
    if text_w(d, text, font) <= max_w:
        return text
    cur = text
    while cur and text_w(d, cur + "…", font) > max_w:
        cur = cur[:-1]
    return (cur + "…") if cur else ""


def edge_check(img, margin: int, y_from: int = 0) -> list[str]:
    """检查内容是否侵入左右边距带（= 溢出的可靠代理）。

    为什么用像素而不是逐个测量：布局函数靠"记得测量"才正确，那是每个
    绘制点都要维持的不变量，漏一处就静默溢出。而**内容侵入边距带**与
    溢出的实现方式无关——文字画到 margin 以内（更别说画到画布外）就会看见。
    无人值守时没有第二双眼睛，所以把不变量变成可验证的输出。

    横跨全宽的装饰条（顶部/底部蓝线）会天然穿过边距带，必须排除。判据是
    「该行几乎只有一个颜色」——不靠调用方传参记得排除，否则漏传就误报，
    而误报的自检等同于没有自检。

    ⚠️ `y_from`：照片层是**满幅**的（它本来就该铺到画布边缘），从 y_from
    以上检查它必然把每一行都判成侵入。所以照片带必须排除在检查之外——
    但排除的是「照片那一段」，不是「照片存在时整张图」，文字区仍要查。
    """
    W, H = img.size
    px = img.load()
    band = max(2, margin - 2)
    intruders = 0
    for y in range(max(0, y_from), H):
        row = [px[x, y][:3] for x in range(W)]
        uniq = len(set(row))
        # 单一颜色的整行 = 装饰带（顶线/底线/纯色底），跳过
        if uniq <= 2:
            continue
        for x in list(range(0, band)) + list(range(W - band, W)):
            r, g, b = row[x]
            if abs(r - 8) + abs(g - 10) + abs(b - 16) > 60:
                intruders += 1
    if intruders > 8:  # 容忍抗锯齿边缘的少量像素
        return [f"内容侵入左右边距带：{intruders} 像素（可能存在溢出）"]
    return []


def draw_kicker(d, text: str, x: int, y: int, max_w: float, start: int, H: int, drawn=None) -> None:
    """顶部小字：先缩字号，缩到下限还放不下就截断。"""
    if not text:
        return
    font, size = fit_font(d, text, max_w, start, MIN_MUTED)
    shown = clip_to_width(d, text, font, max_w)
    d.text((x, y), shown, font=font, fill=BLUE)
    if drawn is not None:
        drawn.append({"element": "kicker", "text": shown, "size": size})


def _is_color(v) -> bool:
    """判断 v 是否是一个 RGB 颜色，而不是一组颜色。

    ⚠️ 不能用 `isinstance(v, (list, tuple))` 来区分「单色」和「逐行颜色」——
    RGB 颜色本身就是 tuple，`(247, 249, 253)` 会被当成三个颜色，
    于是 `fills[0]` 取到标量 247，PIL 把它读成 `(247, 0, 0)` 深红。
    实测这条错误让 statement 布局的正文整片变成红色，而截图上看着"只是有点怪"，
    直到统计像素颜色才暴露。
    """
    return (
        isinstance(v, (tuple, list))
        and len(v) == 3
        and all(isinstance(c, int) for c in v)
    )


def draw_lines_center(d, lines: list[str], font, y: int, fills, W: int, line_h: int) -> int:
    """逐行居中绘制；fills 可以是单色或逐行颜色列表。返回结束 y。"""
    per_line = not _is_color(fills)
    for i, line in enumerate(lines):
        fill = fills[i] if per_line else fills
        d.text(((W - text_w(d, line, font)) / 2, y), line, font=font, fill=fill)
        y += line_h
    return y


# ── 照片层（ADR-0048）────────────────────────────────────────────

def _load_photo_band(path: str, size) -> tuple[Image.Image | None, str]:
    """把照片裁成画布宽度 × PHOTO_BAND 高度的横条。返回 (图, 说明)。

    ⚠️ 必须居中裁切而不是拉伸：拉伸会让机房照片里的人变形，而变形是那种
    「说不上哪里不对但就是廉价」的观感来源——与本项目封面一直追求的
    「像一张设计过的图」正好相反。

    只做轻微压暗（0.94），不做大幅压暗：文字区已经是纯黑，照片带只需要
    不刺眼，不需要为文字让路。这与「照片铺满 + 重遮罩」方案的区别就在这里。
    """
    p = Path(path)
    if not p.exists():
        return None, f"照片不存在：{p}"
    try:
        src = Image.open(p).convert("RGB")
    except Exception as e:
        return None, f"照片打不开（{e}）"

    W, H = size
    band_h = int(H * PHOTO_BAND)
    # 保证文字区不至于被压到读不出
    if H - band_h - PHOTO_DIVIDER < MIN_PANEL_H:
        band_h = max(1, H - PHOTO_DIVIDER - MIN_PANEL_H)

    scale = max(W / src.width, band_h / src.height)
    resized = src.resize(
        (max(1, int(src.width * scale + 0.5)), max(1, int(src.height * scale + 0.5))),
        Image.LANCZOS,
    )
    left = (resized.width - W) // 2
    top = (resized.height - band_h) // 2
    band = resized.crop((left, top, left + W, top + band_h))
    band = ImageEnhance.Brightness(band).enhance(0.94)
    return band, f"{src.width}x{src.height} → {W}x{band_h}"


def layout_number(
    size, kicker: str, hero: str, unit: str, payoff: str, note: str,
    drawn: list | None = None, top_pad: int = 0,
) -> Image.Image:
    """数字英雄：一个数字占满画面。缩略图下最抗缩放。

    分四个**垂直带**，先算下面的带再给上面剩下的空间：
    kicker / hero / payoff / note。所有字号都由测量得出，且 hero 同时受
    宽度和可用高度约束——只按宽度放大会让数字撞进论点区。
    """
    W, H = size
    img = Image.new("RGB", size, INK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 11, W, H], fill=BLUE)

    margin = int(W * 0.065)
    max_w = W - margin * 2

    note_h = int(H * 0.075) if note else 0
    kicker_y = int(H * 0.075)
    kicker_h = int(H * 0.068) if kicker else 0

    # ① 先定 payoff 带（贴底，向上排）
    payoff_top = H - note_h - int(H * 0.02)
    pf = psize = None
    plines: list[str] = []
    if payoff:
        pf, psize, plines = fit_block(d, payoff, max_w, 2, int(H * 0.115), MIN_HERO)
        line_h = int(psize * 1.22)
        block_h = line_h * len(plines)
        payoff_top = H - note_h - block_h - int(H * 0.04)

    # ② hero 带：kicker 之下、payoff 之上
    band_top = kicker_y + kicker_h + int(H * 0.03)
    band_h = max(int(H * 0.30), payoff_top - band_top - int(H * 0.02))

    # 字号同时受高度与宽度约束：以字高定上限，再按总宽收缩
    big_max_by_h = int(band_h * 0.92)
    big_start = min(int(H * 0.46), big_max_by_h)
    big_min = max(MIN_HERO, min(int(H * 0.26), big_max_by_h))

    def unit_size(b: int) -> int:
        return max(MIN_MUTED, int(b * 0.36))

    def measure(b: int) -> tuple[float, object, object]:
        hf, uf = find_font(b), find_font(unit_size(b))
        w = text_w(d, hero, hf) + (text_w(d, unit, uf) + 18 if unit else 0)
        return w, hf, uf

    total, hero_font, unit_font = measure(big_start)
    if total > max_w:  # 太宽 → 缩
        big_start = max(big_min, int(big_start * (max_w / total)))
        total, hero_font, unit_font = measure(big_start)
    elif total < max_w * 0.55:  # 太窄（单字符数字）→ 放大，但不超过高度上限
        grow = min(1.9, (max_w * 0.82) / total, big_max_by_h / max(1, big_start))
        big_start = int(big_start * grow)
        total, hero_font, unit_font = measure(big_start)

    x = (W - total) / 2
    # hero 在其带内垂直居中
    hero_px_h = int(big_start * 0.86)  # 视觉上数字的实际高度
    y_hero = band_top + max(0, (band_h - hero_px_h) // 2)
    d.text((x, y_hero), hero, font=hero_font, fill=WHITE)
    if drawn is not None:
        drawn.append({"element": "hero", "text": hero, "size": big_start})
    if unit:
        d.text(
            (x + text_w(d, hero, hero_font) + 18, y_hero + int(big_start * 0.58)),
            unit,
            font=unit_font,
            fill=BLUE,
        )
        if drawn is not None:
            drawn.append({"element": "unit", "text": unit, "size": unit_size(big_start)})

    # ③ payoff 贴底
    if payoff and plines:
        line_h = int(psize * 1.22)
        block_h = line_h * len(plines)
        y = H - note_h - block_h - int(H * 0.04)
        draw_lines_center(d, plines, pf, y, ORANGE, W, line_h)
        if drawn is not None:
            drawn.append({"element": "payoff", "text": " / ".join(plines), "size": psize})

    draw_kicker(d, kicker, margin, kicker_y, max_w, int(H * 0.068), H, drawn)

    if note:
        nf = find_font(max(MIN_MUTED, int(H * 0.042)))
        d.text((margin, H - int(H * 0.075)), note, font=nf, fill=MUTED)
    return img


def layout_contrast(
    size, kicker: str, line1: str, line2: str, note: str,
    drawn: list | None = None, top_pad: int = 0,
) -> Image.Image:
    """对比双行：两个短句撞在一起，第二句用暖色。"""
    W, H = size
    img = Image.new("RGB", size, INK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 9], fill=BLUE)

    margin = int(W * 0.065)
    max_w = W - margin * 2

    draw_kicker(d, kicker, margin, int(H * 0.068), max_w, int(H * 0.062), H, drawn)

    # 两句各自测量，取能放下的较大字号，且两句同号（视觉上成对）
    start = int(H * 0.30)
    f1, s1 = fit_font(d, line1, max_w, start, MIN_HERO)
    f2, s2 = fit_font(d, line2, max_w, start, MIN_HERO)
    fs = min(s1, s2)
    font = find_font(fs)

    # 缩到 MIN_HERO 仍超宽时必须截断，否则文字会溢出画布两侧
    line1 = clip_to_width(d, line1, font, max_w)
    line2 = clip_to_width(d, line2, font, max_w)
    if drawn is not None:
        drawn.append({"element": "line1", "text": line1, "size": fs})
        drawn.append({"element": "line2", "text": line2, "size": fs})

    line_h = int(fs * 1.16)
    gap = int(H * 0.055)
    block = line_h * 2 + gap
    # top_pad 之上留给 kicker。`(H + top_pad - block) // 2` 是「在
    # [top_pad, H] 这个区间里居中」的等价写法；top_pad=0 时与原来逐字相同。
    y = max(top_pad, (H + top_pad - block) // 2 + int(H * 0.015))

    d.text(((W - text_w(d, line1, font)) / 2, y), line1, font=font, fill=WHITE)
    # 中间分隔短线
    bar_y = y + line_h + gap // 2 - 4
    d.rectangle([W // 2 - int(W * 0.055), bar_y, W // 2 + int(W * 0.055), bar_y + 8], fill=ORANGE)
    d.text(((W - text_w(d, line2, font)) / 2, y + line_h + gap), line2, font=font, fill=ORANGE)

    if note:
        nf = find_font(max(MIN_MUTED, int(H * 0.042)))
        d.text((margin, H - int(H * 0.075)), note, font=nf, fill=MUTED)
    return img


def layout_statement(
    size, kicker: str, text: str, note: str,
    drawn: list | None = None, top_pad: int = 0,
) -> Image.Image:
    """一句断言：单行（最多两行）占满画面。"""
    W, H = size
    img = Image.new("RGB", size, INK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 11, W, H], fill=BLUE)

    margin = int(W * 0.065)
    max_w = W - margin * 2

    draw_kicker(d, kicker, margin, int(H * 0.072), max_w, int(H * 0.064), H, drawn)

    font, fsize, lines = fit_block(d, text, max_w, 2, int(H * 0.30), MIN_HERO)
    line_h = int(fsize * 1.18)
    block_h = line_h * len(lines)
    y = max(top_pad, (H + top_pad - block_h) // 2 + int(H * 0.02))
    draw_lines_center(d, lines, font, y, WHITE, W, line_h)
    if drawn is not None:
        drawn.append({"element": "statement", "text": " / ".join(lines), "size": fsize})

    if note:
        nf = find_font(max(MIN_MUTED, int(H * 0.042)))
        d.text((margin, H - int(H * 0.075)), note, font=nf, fill=MUTED)
    return img


# ── 从标题推导视觉论据 ────────────────────────────────────────────

# 带单位的数字才够格当主元素："8160 亿" 是论据，"4" 不是。
# ⚠️ 两点必须注意：
# 1) 单位长的排在前——正则交替取首个能匹配的，写 `亿|万` 会把「4 万亿元」
#    切成 `4`/`万`，丢掉万亿的量级。
# 2) 允许「量级 + 货币」组合——`120 亿美元` 只取到「亿」会漏掉「美元」，
#    金额就变成了一个没有单位的数字。
NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万亿|亿|万|千|倍|%|美元|元)(美元|元)?")
SPLIT_RE = re.compile(r"[：:，,]")


def derive_parts(title: str) -> dict:
    """把标题拆成 (kicker, hero, unit, payoff) 或 (kicker, line1, line2)。

    目标不是复述标题，而是**挑出其中最有画面感的那一处**：
    有带单位的大数字就用数字布局，否则用对比布局。
    """
    raw = title.strip()
    # 冒号后半句通常是论点
    head, _, tail = raw.partition("：")
    if not tail:
        head, _, tail = raw.partition(":")

    m = NUM_RE.search(raw)
    if m:
        num = m.group(1)
        unit = m.group(2) + (m.group(3) or "")  # 「亿」+「美元」→「亿美元」
        kicker = raw[: m.start()].strip(" ：:，,")
        payoff = tail.strip() if tail else raw[m.end():].strip(" ：:，,")
        if not payoff:
            payoff = head.strip()
        return {"layout": "number", "kicker": kicker, "hero": num, "unit": unit, "payoff": payoff}

    # 无强数字 → 对比布局：取冒号后的两个短句
    body = tail.strip() if tail else raw
    # kicker 只在**真有冒号前半句**时才取。没有冒号时 head 就是整条标题，
    # 拿它当顶部小字等于把标题重复一遍，还会因为太长而只能截断成一串省略号。
    head_kicker = head.strip() if tail else ""
    parts = [p.strip() for p in SPLIT_RE.split(body) if p.strip()]
    if len(parts) >= 2:
        return {"layout": "contrast", "kicker": head_kicker, "line1": parts[0], "line2": parts[1]}
    if len(parts) == 1 and len(parts[0]) <= 14:
        return {"layout": "statement", "kicker": head_kicker, "text": parts[0]}
    return {"layout": "statement", "kicker": "", "text": raw}


def make_cover(
    title: str,
    out_path: Path,
    size=DEFAULT_SIZE,
    footnote: str = "",
    *,
    layout: str = "auto",
    kicker: str = "",
    hero: str = "",
    hero_unit: str = "",
    payoff: str = "",
    line1: str = "",
    line2: str = "",
    statement: str = "",
    photo: str = "",
) -> dict:
    # 显式参数优先；缺失的从标题推导
    derived = derive_parts(title) if layout == "auto" else {"layout": layout}
    lay = derived["layout"] if layout == "auto" else layout
    # 记录「实际画上去的每个元素及其字号」，供自检测量。空列表由布局函数填充。
    drawn: list[dict] = []

    kicker_given = bool(kicker)
    kicker = kicker or derived.get("kicker", "")
    warnings: list[str] = []
    if len(kicker) > KICKER_MAX:
        if not kicker_given:
            # 推导出来的 kicker 过长时**整条丢掉**，不截断。
            # 理由：它本来就是标题的一个前缀片段，截到 16 字只会剩一个残句
            # （实测「DeepSeek API 每 1000 次请求 5 元」→「DeepSeek API 每」），
            # 而残句既不是标签、又在复述标题——正是 ADR-0044 要消灭的那种封面。
            warnings.append(
                f"推导出的 kicker 过长（{len(kicker)} 字 > {KICKER_MAX}），已整条省略："
                f"截断只会留下残句，而残句仍在复述标题"
            )
            kicker = ""
        else:
            # 显式传入的 kicker 是作者意图，截到最后一个分隔符，避免把词切一半
            cut = kicker[:KICKER_MAX]
            for sep in ("，", ",", "、", " ", "：", ":"):
                i = cut.rfind(sep)
                if i >= KICKER_MAX // 2:
                    cut = cut[:i]
                    break
            warnings.append(
                f"kicker 过长（{len(kicker)} 字）已截为 {cut!r}："
                f"kicker 是标签不是句子，过长只能靠缩小字号容纳，缩到缩略图下就读不出了"
            )
            kicker = cut

    # ── 照片层：先把「文字区」的尺寸定下来 ──────────────────────────
    #
    # 关键决定：布局函数仍然在**一整块画布**上画，只是这块画布是「文字区」，
    # 尺寸为 (W, panel_h)，最后整体贴到照片下方。这样布局函数里的所有
    # 相对坐标（kicker_y = H*0.075 之类）自动按文字区缩放，一行都不用改，
    # 也不会出现「按整图算位置、但只画在下半部分」这种错位。
    photo_band = None
    photo_note = ""
    W, H = size
    if photo:
        photo_band, photo_note = _load_photo_band(photo, size)
        if photo_band is None:
            warnings.append(f"照片层未启用：{photo_note}")
    if photo_band is not None:
        band_h = photo_band.height
        panel_h = H - band_h - PHOTO_DIVIDER
        panel_size = (W, panel_h)
    else:
        panel_h = H
        panel_size = size

    # 有照片时，文字区变矮，正文的「居中」必须排除 kicker 占的那一带，
    # 否则两者会撞在一起（见 KICKER_BAND 的注释）。
    top_pad = int(panel_h * KICKER_BAND) if (photo_band is not None and kicker) else 0

    if lay == "number":
        hero = hero or derived.get("hero", "")
        hero_unit = hero_unit or derived.get("unit", "")
        payoff = payoff or derived.get("payoff", "")
        if not hero:
            lay = "contrast"  # 没数字就退回对比，不画空画布
            line1 = line1 or derived.get("line1", "")
            line2 = line2 or derived.get("line2", "")
        else:
            panel = layout_number(
                panel_size, kicker, hero, hero_unit, payoff, footnote, drawn, top_pad
            )
    if lay == "contrast":
        line1 = line1 or derived.get("line1", "")
        line2 = line2 or derived.get("line2", "")
        if not line1 or not line2:
            lay = "statement"
            statement = statement or derived.get("text", "") or title
        else:
            panel = layout_contrast(
                panel_size, kicker, line1, line2, footnote, drawn, top_pad
            )
    if lay == "statement":
        # 2026-10-20 实测缺陷：这里原先只写 `statement or title`，于是当 derive_parts()
        # 推出 statement 布局时（无强数字、冒号后只有一个短句），推导出的 text 被丢弃，
        # 封面正文退化成**整条标题原样**，而 kicker 又取了冒号前半句 → 顶部小字与正文
        # 前半句逐字重复，正是 ADR-0044 判定「不增加信息」的那种封面。
        panel = layout_statement(
            panel_size, kicker, statement or derived.get("text", "") or title,
            footnote, drawn, top_pad,
        )

    if photo_band is not None:
        img = Image.new("RGB", size, INK)
        img.paste(photo_band, (0, 0))
        d = ImageDraw.Draw(img)
        y_div = photo_band.height
        d.rectangle([0, y_div, W, y_div + PHOTO_DIVIDER - 1], fill=BLUE)
        img.paste(panel, (0, y_div + PHOTO_DIVIDER))
    else:
        img = panel

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)

    # 自检：内容是否溢出到边距带（装饰条所在行排除）
    margin = int(size[0] * 0.065)
    # ⚠️ 照片带满幅铺到画布边缘，从第 0 行开始查它必然每行都算侵入。
    # 所以只查文字区；照片带本身不承载文字，不需要这项检查。
    check_from = (photo_band.height + PHOTO_DIVIDER) if photo_band is not None else 0
    problems = edge_check(img, margin, y_from=check_from)

    # ── 自检二：缩略图可读性（测量实际画上去的字号）──────────────────
    #
    # ⚠️ 这里必须用 `drawn` 里记录的**实际字号**，不能用 MIN_HERO 之类的常量。
    # 旧版自检返回 `hero_px_at_thumb = MIN_HERO * scale`——那是把设计意图乘以
    # 一个比例再报出来，与画布上真正画了多大毫无关系。于是 2026-10-20 的封面
    # kicker 只有 38px（缩略图下 10px），自检却一声不响。ADR-0044 发现六。
    scale = FEED_THUMB_W / size[0]
    for d_el in drawn:
        px_at_thumb = d_el["size"] * scale
        if px_at_thumb < READABLE_PX:
            problems.append(
                f"{d_el['element']} 在 {FEED_THUMB_W}px 缩略图下仅 {px_at_thumb:.1f}px 高"
                f"（需 ≥{READABLE_PX:.0f}px）：{d_el['text'][:24]!r}"
            )

    # ── 自检三：封面是否只是在复述标题 ──────────────────────────────
    #
    # ADR-0044 的核心决定是「封面承载论据，不复述标题」，但它一直只是文字约定，
    # 没有可执行检查。2026-10-20 就产出了一张 kicker 与正文首行逐字重复的封面。
    #
    # 判据：把封面上所有文字按顺序拼起来，与标题（都去掉标点空白后）比较。
    # 相等 / 互为子串 → 这张封面没有说出标题以外的任何东西。
    #
    # ⚠️ 注意这条**只是警告不是错误**。原因：`derive_parts()` 只能切分标题，
    # 它在原理上就造不出论据——论据必须由写作阶段写进 `--spec`。所以自动推导
    # 几乎必然复述标题，这是「没人写 spec」的症状，不是这张图本身的缺陷。
    # 把它判成 fatal 会让缺 spec 的那天直接发不出去，代价比一张平庸封面更大。
    body_els = [e for e in drawn if e["element"] != "kicker"]
    kick = next((e["text"] for e in drawn if e["element"] == "kicker"), "")

    def _norm(s: str) -> str:
        # ⚠️ 必须把 `/` 也去掉：`layout_statement` 记录折行结果是
        # `" / ".join(lines)`，折行点会留下一个斜杠。第一版没有剥离它，
        # 于是「推导出的封面」永远判不出复述——combined 变成
        # `'…选AI编程工具/的标准变了'`，与标题差一个字符，比较全部为假。
        # 这是「自检只在手工构造的样例上验过、没在真实推导路径上验」的典型后果。
        return re.sub(r"[\s：:，,、。！？!?·—\-/／|]", "", s or "")

    combined = _norm(kick) + "".join(_norm(e["text"]) for e in body_els)
    title_n = _norm(title)
    restates = bool(title_n) and bool(combined) and (
        combined == title_n or title_n in combined or combined in title_n
    )
    if restates:
        warnings.append(
            f"封面复述标题（{combined[:20]!r} ≈ 标题）：自动推导只能切分标题、"
            f"造不出论据。要在写作阶段写 .newsagg/covers/<日期>.json 给出 --spec（ADR-0044）"
        )

    # 主元素占画布高度比：低于 1/4 说明视觉重心不够，缩略图里认不出来。
    # ⚠️ 分母是**文字区**高度而不是整图高度：有照片时文字区更矮，
    # 若按整图算，同一个字号会被判成「更小」，判断基准就随照片有无而漂移。
    hero_el = next((e for e in drawn if e["element"] in ("hero", "statement", "line1")), None)
    hero_ratio = (hero_el["size"] / panel_size[1]) if hero_el else 0.0
    if hero_ratio < 0.25:
        warnings.append(
            f"主元素只占文字区高度 {hero_ratio:.0%}（建议 ≥25%）：缩略图下视觉重心不足"
        )

    return {
        "out": str(out_path),
        "size": list(size),
        "layout": lay,
        "kicker": kicker,
        "title_chars": len(title),
        "bytes": out_path.stat().st_size,
        "thumb_scale": round(scale, 3),
        "drawn": [{"element": e["element"], "size": e["size"],
                   "px_at_thumb": round(e["size"] * scale, 1)} for e in drawn],
        "hero_ratio": round(hero_ratio, 3),
        "restates_title": restates,
        "photo": photo_note if photo_band is not None else "",
        "photo_enabled": photo_band is not None,
        # 路径要带出去：简报靠它去 `.newsagg/photos/ledger.jsonl` 里查许可证与出处
        # （ADR-0048）。只给「有没有照片」不够——「这张图当时依据什么被判为 CC0」
        # 才是需要留痕的那部分。
        "photo_path": photo if photo_band is not None else "",
        "panel_height": panel_size[1],
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="模板拼图封面（ADR-0013 兜底链 / ADR-0044 视觉论据）")
    ap.add_argument("--slug", required=True, help="成稿 slug（取 outputs/<slug>.md 的标题）")
    ap.add_argument("--out", default=None, help="封面输出路径")
    ap.add_argument("--title", default=None, help="直接给标题（默认从成稿读一级标题）")
    ap.add_argument(
        "--note",
        default="",
        help="可选脚注（默认**不画**）。AI 标识走平台的作品声明，不印在图上",
    )
    ap.add_argument("--layout", default="auto", choices=["auto", "number", "contrast", "statement"])
    ap.add_argument("--kicker", default="", help="顶部小字（默认从标题推导）")
    ap.add_argument("--hero", default="", help="主数字，如 8160（number 布局）")
    ap.add_argument("--hero-unit", default="", help="数字单位，如 亿")
    ap.add_argument("--payoff", default="", help="论点短句（number 布局）")
    ap.add_argument("--line1", default="", help="对比第一句（contrast 布局）")
    ap.add_argument("--line2", default="", help="对比第二句（contrast 布局）")
    ap.add_argument("--statement", default="", help="断言（statement 布局）")
    ap.add_argument(
        "--photo",
        default="",
        help="背景照片路径（CC0/公有领域，来自 fetch_photo.py）。留空则纯模板封面（ADR-0048）",
    )
    ap.add_argument(
        "--spec",
        default="",
        help="JSON 文件：显式指定封面论据（layout/kicker/hero/hero_unit/payoff/line1/line2/statement/photo）",
    )
    args = ap.parse_args()

    # --spec 让 agent 把「这篇文章的视觉论据是什么」写进文件，而不是靠标题推导。
    # 命令行参数优先级更高（便于临时覆盖），spec 次之，标题推导兜底。
    spec: dict = {}
    if args.spec:
        sp = Path(args.spec)
        if not sp.exists():
            print(json.dumps({"ok": False, "message": f"找不到 spec：{sp}"}, ensure_ascii=False))
            return 1
        try:
            spec = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "message": f"spec 不是合法 JSON：{e}"}, ensure_ascii=False))
            return 1
        if not isinstance(spec, dict):
            print(json.dumps({"ok": False, "message": "spec 必须是 JSON 对象"}, ensure_ascii=False))
            return 1

    root = Path(__file__).resolve().parents[4]
    title = args.title
    if title is None:
        md = root / "outputs" / f"{args.slug}.md"
        if not md.exists():
            print(json.dumps({"ok": False, "message": f"找不到成稿：{md}"}, ensure_ascii=False))
            return 1
        m = re.match(r"^#\s+(.+)$", md.read_text(encoding="utf-8"), re.M)
        if not m:
            print(json.dumps({"ok": False, "message": "成稿缺少一级标题"}, ensure_ascii=False))
            return 1
        title = m.group(1).strip()

    out = Path(args.out) if args.out else root / "outputs" / "covers" / f"{args.slug}.png"
    def pick(cli_val: str, key: str) -> str:
        """命令行 > spec > 空（空则由标题推导）。"""
        return cli_val or str(spec.get(key, "") or "")

    layout = args.layout if args.layout != "auto" else str(spec.get("layout", "auto") or "auto")
    info = make_cover(
        title,
        out,
        footnote=args.note,
        layout=layout,
        kicker=pick(args.kicker, "kicker"),
        hero=pick(args.hero, "hero"),
        hero_unit=pick(args.hero_unit, "hero_unit"),
        payoff=pick(args.payoff, "payoff"),
        line1=pick(args.line1, "line1"),
        line2=pick(args.line2, "line2"),
        statement=pick(args.statement, "statement"),
        photo=pick(args.photo, "photo"),
    )
    # ok 由自检决定（是否溢出）。溢出时退出码非 0，让编排器挡住发布——
    # 一张溢出画布的封面比没有封面更糟：它会被发出去，而没人看见。
    ok = info.pop("ok")
    print(json.dumps({"ok": ok, "title": title, **info}, ensure_ascii=False, indent=2))
    return 0 if not info.get("problems") else 1


if __name__ == "__main__":
    sys.exit(main())
