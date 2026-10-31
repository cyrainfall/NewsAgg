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

## 封面样式：每日随机一种（有限目录）

样式（style）是**材质**——底色、墨色、强调色、显示字体与装饰线的组合——不是
排版计划（同 reference/hallmark 的主题观）。三种布局、测量自适应、3:2 安全区、
可读性下限与样式**正交**：任何论据在任何样式下都必须通过同一组自检，
样式不允许引入一条新的豁免。

每天的样式由日期哈希从有限目录（`STYLE_ORDER`，8 种）里**确定性地**选出：
同一天重跑结果一致、不需要状态文件（状态文件会坏，哈希不会）、跨天自然轮换
（相邻两天撞色时 +1 错开）。spec 的 `"style"` 字段 / `--style` 可以从目录里
**显式钉死**一种；未知名字显式报错退出 1，绝不静默回退——静默回退会让
「我明明指定了样式」变成一句无人验证的假设（同 ADR-0044 对 --spec 的纪律）。

## 封面不印任何脚注

作品声明走平台的字段（`publish.py --declaration`），不在图上的像素里重复。
在图上再印一行脚注要占掉 7.5% 的画布高度——而封面上每一寸都该留给论据。
故 `--note` 默认为空。

（作品声明当前默认为「个人观点」而非「引用AI」，那是一个带已知合规风险的
有意偏离，见 ADR-0046。封面不涉及该决定：图上一个字都不印。）
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import re
import sys
import zlib
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

# ── 信息流卡片是 3:2，不是 16:9 ────────────────────────────────────
#
# 2026-10-21 实测（Playwright 遍历首页信息流 img 的 getBoundingClientRect）：
# 桌面网页的图片槽是 **156x104**，`object-fit: cover`，自然尺寸 1035x582 /
# 1280x720 等。也就是说我们 1200x675（16:9）的封面会被**居中裁切**成 3:2。
#
# 后果：左右各丢 (1200 - 675*1.5)/2 = 93.75px → int() 取整为 93px，约 7.8%。5 张历史封面逐像素统计
# 全部有内容落在安全区外，最严重那张把「做应用的人省钱」的「钱」字切掉一半。
#
# 所以排版必须**先保证内容在 3:2 安全区内**，再谈字号。
FEED_CARD_ASPECT = 1.5
# 3:2 安全区左右各被切掉多少（1200x675 时 = 93px；理论值 93.75，向下取整）
SAFE_INSET = max(0, int((DEFAULT_SIZE[0] - DEFAULT_SIZE[1] * FEED_CARD_ASPECT) / 2))

INK = (8, 10, 16)
INK_SOFT = (22, 34, 54)
WHITE = (247, 249, 253)
MUTED = (126, 138, 162)
BLUE = (64, 140, 255)
ORANGE = (255, 106, 61)
MINT = (52, 211, 153)

# ── 封面样式目录：样式是材质，不是排版计划 ─────────────────────────
#
# 取材 reference/hallmark（Together AI 的 48 主题设计 skill）的核心观念：
# 主题 = 纸、墨、强调色、显示字体的组合，**不是布局**。布局保持正交，
# 于是同一论据在任何样式下都走同一套测量、同一组自检——样式只换材质，
# 不引入新的豁免，也不带来新的失败模式。
#
# 8 种样式的字体全部实测过有真实 CJK 字形（textbbox 逐字验证过宽度，
# 区别于零宽的豆腐块）。宋体系（Songti Black）给衬线报刊感，黑体系给
# 现代感，Menlo 给等宽印刷感。每个样式必须带 bg，自检用它判断
# 「什么是墨」——这是纯函数式的材质声明，没有隐式全局。
STYLES: dict[str, dict] = {
    "ink": {           # 原版：深夜蓝黑 + 蓝（hallmark: editorial-ink）
        "bg": INK, "ink": WHITE, "muted": MUTED, "accent": BLUE, "alt": ORANGE,
        "font": None, "font_index": None, "dark": True,
        "deco": "bottom_bar",
    },
    "paper": {         # 报纸白纸黑字 + 红强调（hallmark: newsprint）
        "bg": (244, 242, 237), "ink": (24, 24, 28), "muted": (110, 108, 104),
        "accent": (196, 30, 30), "alt": (24, 24, 28),
        "font": "/System/Library/Fonts/Supplemental/Songti.ttc", "font_index": 0,
        "dark": False,
        "deco": "top_bar",
    },
    "slate": {         # 石板灰 + 薄荷（hallmark: graphite）
        "bg": (30, 34, 40), "ink": (238, 240, 244), "muted": (148, 156, 168),
        "accent": MINT, "alt": (255, 196, 92),
        "font": "/System/Library/Fonts/Hiragino Sans GB.ttc", "font_index": 2,
        "dark": True,
        "deco": "bottom_bar",
    },
    "cream": {         # 米纸 + 深棕墨 + 朱砂（hallmark: aged-paper）
        "bg": (247, 240, 226), "ink": (46, 36, 28), "muted": (119, 107, 93),
        "accent": (178, 60, 34), "alt": (46, 36, 28),
        "font": "/System/Library/Fonts/Supplemental/Songti.ttc", "font_index": 1,
        "dark": False,
        "deco": "top_bar",
    },
    "cobalt": {        # 钴蓝底 + 白字 + 金强调（hallmark: klein-blue）
        "bg": (16, 44, 96), "ink": WHITE, "muted": (150, 172, 210),
        "accent": (255, 206, 96), "alt": WHITE,
        "font": "/System/Library/Fonts/STHeiti Medium.ttc", "font_index": 1,
        "dark": True,
        "deco": "bottom_bar",
    },
    "forest": {        # 墨绿底 + 米白 + 暖橙（hallmark: botanical-ink）
        "bg": (16, 44, 38), "ink": (240, 246, 240), "muted": (140, 168, 152),
        "accent": (255, 150, 80), "alt": (240, 246, 240),
        "font": "/System/Library/Fonts/Hiragino Sans GB.ttc", "font_index": 2,
        "dark": True,
        "deco": "top_bar",
    },
    "mono": {          # 高对比黑白 + 等宽显示（hallmark: swiss-mono）
        "bg": (12, 12, 14), "ink": WHITE, "muted": (144, 146, 152),
        "accent": WHITE, "alt": (232, 96, 64),
        "font": "/System/Library/Fonts/Menlo.ttc", "font_index": 1,
        "dark": True,
        "deco": "top_bar",
    },
    "blush": {         # 暖灰纸 + 黑墨 + 珊瑚（hallmark: warm-paper）
        "bg": (240, 232, 228), "ink": (32, 28, 30), "muted": (114, 103, 101),
        "accent": (179, 65, 80), "alt": (32, 28, 30),
        "font": "/System/Library/Fonts/STHeiti Medium.ttc", "font_index": 1,
        "dark": False,
        "deco": "top_bar",
    },
}
# 目录顺序固定：日期哈希按这个顺序取模。用 dict 而不是 list[名字] + 二次查表，
# 是让「有哪些样式」只有一处声明——两处列表迟早不同步。
STYLE_ORDER = list(STYLES.keys())
LEGACY_STYLE = "ink"  # 旧常量（INK/BLUE…）继续生效时的名字


def style_for_date(day: str) -> str:
    """按日期确定性地选一种样式。无状态：同一天重跑必一致。

    为什么不用 random：无人值守的定时任务可能一天跑多次，random 会让
    「今天用哪张样式」变成不可复现的——重跑一次封面就换了长相。日期哈希
    不需要状态文件（状态文件会坏、会丢、会被 git 搞脏），天然幂等。
    相邻两天撞上同一种时 +1 错开，保证「每天换一种」的直觉成立。
    """
    idx = zlib.crc32(day.encode("utf-8")) % len(STYLE_ORDER)
    if STYLE_ORDER[idx] == style_for_date_prev(day):
        idx = (idx + 1) % len(STYLE_ORDER)
    return STYLE_ORDER[idx]


def style_for_date_prev(day: str) -> str:
    """昨天的样式名——用于相邻日错开。"""
    d = datetime.date.fromisoformat(day)
    prev = (d - datetime.timedelta(days=1)).isoformat()
    return STYLE_ORDER[zlib.crc32(prev.encode("utf-8")) % len(STYLE_ORDER)]

# ── 缩略图可读性：从「声明」变成「测量」──────────────────────────────
#
# 平台下发到信息流的封面资源实测是 640x360（`tplv-tt-shrink:640:0`）。
# 640 是给视网膜屏的 2x 资源，对应 CSS 展示宽约 320px。
# 平台自己选 640 而不是 320 下发，说明它预期的最大展示面就是 320 CSS px，
# 所以 320 是「正常展示」尺寸，也是可读性阈值该用的基准。
FEED_THUMB_W = 320
# 但桌面网页的信息流图片槽实测只有 156x104 —— 那是最差展示尺寸。
# 主元素在这个尺寸下也必须认得出，否则「缩略图可读」只是个平均值幻觉。
FEED_CARD_W = 156
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
    # Windows：少了这几条，本机一个候选都命不中 → load_default()（无中文字形）
    # → 中文全渲染成方块，而自检用同一支坏字体测量，于是 problems 为 []。
    # 2026-10-28 本机实测：封面只有 39 个小方块却能「通过」。
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑 Regular
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "C:/Windows/Fonts/simsun.ttc",   # 宋体
]

_FONT_PATH: str | None = None
_FONT_CACHE: dict[int, object] = {}
_STYLE_FONT_CACHE: dict[tuple[str, int | None, int], object] = {}


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


def find_style_font(style: dict, size: int):
    """样式指定的显示字体；样式没带字体（或打不开）就回落全局默认。

    为什么回落而不是报错：样式字体是**观感选择**，不是正确性前提。
    换一台没有 Songti.ttc 的机器，封面仍要能生成——自检会照样把关可读性。
    ttc 的 index 也随样式走：Songti.ttc 的 0 号是 Black、3 号是 Light，
    不传 index 会静默拿到 Regular，样式就失去了它要的笔重。
    """
    key = (str(style.get("font")), style.get("font_index"), size)
    if key in _STYLE_FONT_CACHE:
        return _STYLE_FONT_CACHE[key]
    font = None
    p = style.get("font")
    if p and Path(p).exists():
        try:
            font = ImageFont.truetype(p, size, index=style.get("font_index") or 0)
        except OSError:
            font = None
    if font is None:
        font = find_font(size)
    _STYLE_FONT_CACHE[key] = font
    return font


# ── 测量驱动的排版工具 ────────────────────────────────────────────


def text_w(d, text: str, font) -> float:
    return d.textlength(text, font=font)


def fit_font(
    d, text: str, max_w: float, start: int, floor: int = MIN_HERO,
    font_fn=None,
):
    """从 start 起逐级缩小，返回第一个能让 text 单行放进 max_w 的字号。

    ⚠️ 必须先 `max(start, floor)`：调用方传的 start 是按画布比例算的（如
    `int(H * 0.062)` = 41），可能**本身就低于 floor**。此时 while 条件
    `size > floor` 一开始就为假，函数会把低于下限的字号原样返回——
    于是「缩到下限为止」的约束从第一步就没生效。
    实测：kicker 用 41px 画出去，缩略图下 10.9px，而它自己的下限是 11px。

    `font_fn`：样式化的取字体函数（默认全局 CJK 字体）。测量必须用
    **将要画出去的那支字体**——不同字体的同字号宽度不同，拿 A 字体测量、
    用 B 字体绘制，自适应就是拿错觉当依据。
    """
    ff = font_fn or find_font
    size = max(start, floor)
    while size > floor and text_w(d, text, ff(size)) > max_w:
        size -= 4
    return ff(size), size


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


def fit_block(
    d, text: str, max_w: float, max_lines: int, start: int,
    floor: int = MIN_HERO, font_fn=None,
):
    """在 max_lines 行内放下的最大字号。放不下就缩，缩到 floor 仍放不下则截断。

    同 `fit_font`：start 可能低于 floor，必须先夹到 floor 之上。
    `font_fn` 同 `fit_font`：测量与绘制必须同字体。
    """
    ff = font_fn or find_font
    size = max(start, floor)
    while size > floor:
        font = ff(size)
        lines = wrap_cjk(d, text, font, max_w)
        if len(lines) <= max_lines:
            return font, size, lines
        size -= 4
    font = ff(floor)
    lines = wrap_cjk(d, text, font, max_w)[:max_lines]
    # 末行加省略号，明确表示"这里被截断了"而不是悄悄少一句话
    if lines and len(wrap_cjk(d, text, font, max_w)) > max_lines:
        last = lines[-1]
        while last and text_w(d, last + "…", font) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return font, floor, lines


def clip_to_width(d, text: str, font, max_w: float, font_fn=None) -> str:
    """把 text 截到 max_w 内，末位加省略号。

    为什么必须有这一步：`fit_font` 缩到 floor 就停手，若文字在 floor 字号下仍
    超宽，它会**原样返回超宽文本**——画出去就是溢出画布。溢出比难看更糟：
    无人值守时没人看得见，而它每天都发生。
    `font_fn` 仅供内部回退路径（fit 返回的 font 已是样式字体，正常不需用）。
    """
    if text_w(d, text, font) <= max_w:
        return text
    cur = text
    while cur and text_w(d, cur + "…", font) > max_w:
        cur = cur[:-1]
    return (cur + "…") if cur else ""


# ── 对比度：文字与底色之间必须读得出来 ────────────────────────────
#
# ⚠️ 这条检查是 ADR-0067 引入样式目录后**必须补上**的：换材质换掉了参照系，
# 而自检的判据没跟着换。
#
# 2026-10-26 实测（8 种样式逐个生成、逐个统计）：
#   blush  accent (214,78,96) on (240,232,228) = 3.39   ← 不达标
#   cream  muted  (128,116,100) on (247,240,226) = 4.03 ← 不达标
#   blush  muted  (124,112,110) on (240,232,228) = 3.95 ← 不达标
#   paper  accent (196,30,30) on (244,242,237) = 5.29  ← 达标
# 全部 `problems: []` `warnings: []`——自检全过，因为 `edge_check` 与
# `safe_zone_check` 只判「有没有侵入边距/裁切区」，**不判对比度**；
# `tests/test_make_cover_style.py` 只钉「墨底 sum-abs > 60」这个
# 「是墨不是底」的判据，也不判墨与底的亮度差。
#
# ⚠️ 这里的数字是**用 WCAG 的 sRGB 线性化公式**算的。第一版诊断我漏了
# 线性化，直接用 0.2126R+0.7152G+0.0722B，算出 accent 2.16 / 2.89 这种值，
# 差点把 8 种样式的材质全部破坏性地改暗。教训：**报出来的数字必须用与
# 标准一致的定义算一遍**——错的判据比没有判据更贵。
#
# 根因是**一个颜色承担了两种角色**：`accent` 既是装饰条的颜色（只需 9~11px
# 高的色带，与底色几乎贴在一起也无所谓），又是 kicker 与 hero 单位的小字
# 颜色（缩略图下必须读得出来）。这两种角色的对比度要求差一个数量级，
# 而目录里只声明了一个 `accent`。
#
# 处置：**按更严的那个角色取值**。判定阈值用 WCAG：正文 4.5:1、
# 大字（≥24px）3:1，且**字号按缩到 156px 信息流卡片后的实际尺寸算**
# （画布上 42px 的 kicker 到卡片上只有 5.5px，属正文级）。
# 实测后只改了三个不达标的色值：blush accent、cream muted、blush muted；
# paper / slate / cobalt / forest / mono / ink 全部保持原值。
CONTRAST_MIN_TEXT = 4.5
CONTRAST_MIN_LARGE = 3.0
# 大字阈值（WCAG）：≥24px，或 ≥18.66px 且为粗体。封面上的字没有粗体
# 概念（字体族固定），所以只按像素高度判。
LARGE_TEXT_PX = 24.0


def _rel_lum(rgb) -> float:
    """WCAG 相对亮度。rgb 是 0-255 的三元组。"""
    def ch(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(float(v)) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg) -> float:
    """WCAG 对比度，1.0 ~ 21.0。"""
    l1, l2 = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def contrast_check(drawn: list, bg: tuple, scale: float = 1.0) -> list[str]:
    """检查每个**文字**元素与底色的对比度。

    判据是 WCAG：≥24px 的算大字（3:1），更小的算正文（4.5:1）。

    ⚠️ **字号按缩放后的尺寸算，不按画布尺寸算。**
    封面在桌面信息流里只有 156px 宽（`FEED_CARD_W`，ADR-0049 实测），
    画布上 42px 的 kicker 到那里只剩 5.5px——按画布尺寸判它是「大字」
    （3:1 就放行），但读者看到的是 5.5px 的正文级小字。判据跟着
    「读者看到的尺寸」走，才和 ADR-0049 的整节推理一致。
    画布 307px 的 hero 到卡片上是 40px，仍是大字。

    ⚠️ 脚注（note）用 `muted` 画，且它按设计就是最弱的一层信息。
    它在 1200px 画布上只有 28px，缩到 156px 卡片后约 3.6px——那时候
    它本来就读不出来（自检二之二已经会警告主元素偏小）。对脚注按
    4.5:1 硬拦会把「浅色纸样式」整个判死，而脚注在信息流里不可见
    是**已知且可接受**的取舍（ADR-0049 的 156px 讨论已经确立：
    想更大只能把句子改短，那是写作阶段的选择）。所以脚注**不查**，
    只在样式目录层面保证 muted 至少不与底色同色（测试已钉）。

    查的是真正画上去的颜色——`drawn` 里每项都记了画时用的 `color`，
    而不是「这个元素按惯例应该用哪个色」。这是 ADR-0044 发现六的教训：
    自检必须读产出物本身。
    """
    problems: list[str] = []
    for el in drawn:
        color = el.get("color")
        if not color:
            continue
        size = float(el.get("size", 0)) * scale
        need = CONTRAST_MIN_LARGE if size >= LARGE_TEXT_PX else CONTRAST_MIN_TEXT
        ratio = contrast_ratio(color, bg)
        if ratio < need:
            problems.append(
                f"{el['element']} 的文字颜色 {tuple(color[:3])} 与底色 {tuple(bg[:3])} "
                f"对比度仅 {ratio:.2f}（需 ≥{need:.1f}，WCAG "
                f"{'大字' if need == CONTRAST_MIN_LARGE else '正文'}级，"
                f"按 {size:.1f}px 实际显示尺寸判）：{el['text'][:20]!r}"
            )
    return problems


def edge_check(img, margin: int, y_from: int = 0, bg: tuple = INK) -> list[str]:
    """检查内容是否侵入左右边距带（= 溢出的可靠代理）。

    为什么用像素而不是逐个测量：布局函数靠"记得测量"才正确，那是每个
    绘制点都要维持的不变量，漏一处就静默溢出。而**内容侵入边距带**与
    溢出的实现方式无关——文字画到 margin 以内（更别说画到画布外）就会看见。
    无人值守时没有第二双眼睛，所以把不变量变成可验证的输出。

    横跨全宽的装饰条（顶部/底部强调线）会天然穿过边距带，必须排除。判据是
    「该行几乎只有一个颜色」——不靠调用方传参记得排除，否则漏传就误报，
    而误报的自检等同于没有自检。

    ⚠️ `bg`：样式目录引入了浅纸底色，「是不是墨」不能再拿写死的 INK
    判断——白纸底上的白字会整幅被算成墨，检查就从把关变成全体通过或
    全体误报。底色由样式声明，检查按与底色的距离识别墨。

    ⚠️ `y_from`：照片层是**满幅**的（它本来就该铺到画布边缘），从 y_from
    以上检查它必然把每一行都判成侵入。所以照片带必须排除在检查之外——
    但排除的是「照片那一段」，不是「照片存在时整张图」，文字区仍要查。
    """
    W, H = img.size
    px = img.load()
    band = max(2, margin - 2)
    intruders = 0
    br, bgc, bb = bg[:3]
    for y in range(max(0, y_from), H):
        row = [px[x, y][:3] for x in range(W)]
        uniq = len(set(row))
        # 单一颜色的整行 = 装饰带（顶线/底线/纯色底），跳过
        if uniq <= 2:
            continue
        for x in list(range(0, band)) + list(range(W - band, W)):
            r, g, b = row[x]
            if abs(r - br) + abs(g - bgc) + abs(b - bb) > 60:
                intruders += 1
    if intruders > 8:  # 容忍抗锯齿边缘的少量像素
        return [f"内容侵入左右边距带：{intruders} 像素（可能存在溢出）"]
    return []


def draw_kicker(
    d, text: str, x: int, y: int, max_w: float, start: int, H: int,
    drawn=None, style: dict | None = None,
) -> None:
    """顶部小字：先缩字号，缩到下限还放不下就截断。"""
    if not text:
        return
    st = style or STYLES[LEGACY_STYLE]
    font, size = fit_font(d, text, max_w, start, MIN_MUTED, font_fn=lambda s: find_style_font(st, s))
    shown = clip_to_width(d, text, font, max_w, font_fn=lambda s: find_style_font(st, s))
    d.text((x, y), shown, font=font, fill=st["accent"])
    if drawn is not None:
        drawn.append({"element": "kicker", "text": shown, "size": size,
                      "color": st["accent"]})


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


def draw_lines_center(d, lines: list[str], font, y: int, fills, cx: float, line_h: int) -> int:
    """逐行以 cx 为中线居中绘制；fills 可以是单色或逐行颜色列表。返回结束 y。

    ⚠️ 中线是**安全区中线**而不是画布中线（W/2）：1200x675 进信息流会被居中
    裁成 3:2，左右各丢 93px（理论 93.75，向下取整）。按画布中线居中会让长句的两端落进被裁掉的部分。
    """
    per_line = not _is_color(fills)
    for i, line in enumerate(lines):
        fill = fills[i] if per_line else fills
        d.text((cx - text_w(d, line, font) / 2, y), line, font=font, fill=fill)
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


def safe_zone_check(
    img, inset: int, y_from: int = 0, tolerance: float = 0.001, bg: tuple = INK,
) -> list[str]:
    """检查内容是否落到 3:2 裁切区（左右各 inset 像素）里。

    为什么需要它，而 `edge_check` 不够：`edge_check` 查的是「有没有画到画布
    边缘之外」，而真实问题是「画在画布内、但会被平台裁掉」。这是两个不同的
    失败，前者是我们画错了，后者是我们画对了地方、平台不给你看。

    判据同样是像素而不是逐个测量：布局函数靠「记得用安全区宽度」才正确，
    那是每个绘制点都要维持的不变量。像素统计与实现方式无关——内容落进
    裁切区就会看见。

    ⚠️ 分母必须是**亮像素**（我们画上去的墨），不是「总像素」。
    总像素作分母时，1200x675 的 0.2% 阈值等于 1620 个像素——实测 5 张历史
    封面「越界亮像素/全部亮像素」是 0.18% / 0.57% / 0.50% / 0.41% / 0.27%，
    除以总像素后全部降到 0.1% 以下，于是检查一声不响。这就是 ADR-0044 发现六
    的同一个错误：**分母选错，检查就会变成装饰**。改用亮像素作分母后，
    新排版是 0.00%，旧产物 0.18% 起，阈值 0.1% 能把两者干净分开。

    与 `edge_check` 一样，横跨全宽的装饰带（顶部/底部强调线）会天然穿过裁切区，
    必须按「该行几乎只有一个颜色」排除，而不是靠调用方记得传参。
    `y_from` 排除照片带：照片是**故意**满幅铺的，它被裁是设计的一部分。
    `bg` 同 `edge_check`：墨的判定以样式底色为参照。
    """
    W, H = img.size
    if inset <= 0:
        return []
    px = img.load()
    outside = 0
    ink = 0
    br, bgc, bb = bg[:3]
    for y in range(max(0, y_from), H):
        row = [px[x, y][:3] for x in range(W)]
        if len(set(row)) <= 2:  # 装饰带（顶线/底线/纯色底）
            continue
        for x in range(W):
            r, g, b = row[x]
            if abs(r - br) + abs(g - bgc) + abs(b - bb) > 60:  # 是墨不是底
                ink += 1
                if x < inset or x >= W - inset:
                    outside += 1
    ratio = (outside / ink) if ink else 0.0
    if ratio > tolerance:
        return [
            f"内容落入 3:2 裁切区：{outside}/{ink} 个墨点（{ratio:.2%} > {tolerance:.2%}）"
            f"——信息流会把左右各 {inset}px 切掉，这些字看不到（ADR-0049）"
        ]
    return []


def text_bounds(W: int) -> tuple[int, float]:
    """返回可排版的左边界与最大宽度（右边界 = left + max_w）。

    为什么不是「画布宽 - 两侧边距」：1200x675 的封面进信息流会被**居中裁成
    3:2**（`object-fit: cover`），左右各丢 93px（理论 93.75）。按整幅画布排版，等于把内容
    排进了会被切掉的那块区域。5 张历史封面逐像素统计**全部**有内容越界，
    最严重那张把「做应用的人省钱」的「钱」字切掉一半。

    所以排版区间 = 3:2 安全区再内缩一个边距。代价是可用宽度从 1044 降到 856
    （-18%），字号相应变小；换来的是「信息流里看到的字，一个都不缺」。
    """
    margin = int(W * 0.065)
    left = SAFE_INSET + margin
    right = W - SAFE_INSET - margin
    return left, float(right - left)


def layout_number(
    size, kicker: str, hero: str, unit: str, payoff: str, note: str,
    drawn: list | None = None, top_pad: int = 0, style: dict | None = None,
) -> Image.Image:
    """数字英雄：一个数字占满画面。缩略图下最抗缩放。

    分四个**垂直带**，先算下面的带再给上面剩下的空间：
    kicker / hero / payoff / note。所有字号都由测量得出，且 hero 同时受
    宽度和可用高度约束——只按宽度放大会让数字撞进论点区。

    样式（材质）经 `style` 注入：底色、墨色、强调色、显示字体。本函数对
    「今天用哪种材质」一无所知，也不该知道——布局与样式正交。
    """
    st = style or STYLES[LEGACY_STYLE]
    ffn = lambda s: find_style_font(st, s)  # noqa: E731
    W, H = size
    img = Image.new("RGB", size, st["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 11, W, H], fill=st["accent"])

    margin, max_w = text_bounds(W)

    note_h = int(H * 0.075) if note else 0
    kicker_y = int(H * 0.075)
    kicker_h = int(H * 0.068) if kicker else 0

    # ① 先定 payoff 带（贴底，向上排）
    payoff_top = H - note_h - int(H * 0.02)
    pf = psize = None
    plines: list[str] = []
    if payoff:
        pf, psize, plines = fit_block(d, payoff, max_w, 2, int(H * 0.115), MIN_HERO, font_fn=ffn)
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
        hf, uf = ffn(b), ffn(unit_size(b))
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

    x = margin + (max_w - total) / 2
    # hero 在其带内垂直居中
    hero_px_h = int(big_start * 0.86)  # 视觉上数字的实际高度
    y_hero = band_top + max(0, (band_h - hero_px_h) // 2)
    d.text((x, y_hero), hero, font=hero_font, fill=st["ink"])
    if drawn is not None:
        drawn.append({"element": "hero", "text": hero, "size": big_start,
                      "color": st["ink"]})
    if unit:
        d.text(
            (x + text_w(d, hero, hero_font) + 18, y_hero + int(big_start * 0.58)),
            unit,
            font=unit_font,
            fill=st["accent"],
        )
        if drawn is not None:
            drawn.append({"element": "unit", "text": unit, "size": unit_size(big_start),
                          "color": st["accent"]})

    # ③ payoff 贴底
    if payoff and plines:
        line_h = int(psize * 1.22)
        block_h = line_h * len(plines)
        y = H - note_h - block_h - int(H * 0.04)
        draw_lines_center(d, plines, pf, y, st["alt"], margin + max_w / 2, line_h)
        if drawn is not None:
            drawn.append({"element": "payoff", "text": " / ".join(plines), "size": psize,
                          "color": st["alt"]})

    draw_kicker(d, kicker, margin, kicker_y, max_w, int(H * 0.068), H, drawn, style=st)

    if note:
        nf = ffn(max(MIN_MUTED, int(H * 0.042)))
        d.text((margin, H - int(H * 0.075)), note, font=nf, fill=st["muted"])
    return img


def layout_contrast(
    size, kicker: str, line1: str, line2: str, note: str,
    drawn: list | None = None, top_pad: int = 0, style: dict | None = None,
) -> Image.Image:
    """对比双行：两个短句撞在一起，第二句用暖色（样式 alt）。"""
    st = style or STYLES[LEGACY_STYLE]
    ffn = lambda s: find_style_font(st, s)  # noqa: E731
    W, H = size
    img = Image.new("RGB", size, st["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 9], fill=st["accent"])

    margin, max_w = text_bounds(W)

    draw_kicker(d, kicker, margin, int(H * 0.068), max_w, int(H * 0.062), H, drawn, style=st)

    # 两句各自测量，取能放下的较大字号，且两句同号（视觉上成对）
    start = int(H * 0.30)
    f1, s1 = fit_font(d, line1, max_w, start, MIN_HERO, font_fn=ffn)
    f2, s2 = fit_font(d, line2, max_w, start, MIN_HERO, font_fn=ffn)
    fs = min(s1, s2)
    font = ffn(fs)

    # 缩到 MIN_HERO 仍超宽时必须截断，否则文字会溢出画布两侧
    line1 = clip_to_width(d, line1, font, max_w)
    line2 = clip_to_width(d, line2, font, max_w)
    if drawn is not None:
        drawn.append({"element": "line1", "text": line1, "size": fs, "color": st["ink"]})
        drawn.append({"element": "line2", "text": line2, "size": fs, "color": st["alt"]})

    line_h = int(fs * 1.16)
    gap = int(H * 0.055)
    block = line_h * 2 + gap
    # top_pad 之上留给 kicker。`(H + top_pad - block) // 2` 是「在
    # [top_pad, H] 这个区间里居中」的等价写法；top_pad=0 时与原来逐字相同。
    y = max(top_pad, (H + top_pad - block) // 2 + int(H * 0.015))

    # 居中必须在**安全区**内居中，不是画布居中：画布居中会把长句的两端推进
    # 3:2 裁切区（左右各 93px），信息流里看到的就是被切掉半截的字。
    cx = margin + max_w / 2
    d.text((cx - text_w(d, line1, font) / 2, y), line1, font=font, fill=st["ink"])
    # 中间分隔短线
    bar_y = y + line_h + gap // 2 - 4
    d.rectangle([cx - int(W * 0.055), bar_y, cx + int(W * 0.055), bar_y + 8], fill=st["accent"])
    d.text((cx - text_w(d, line2, font) / 2, y + line_h + gap), line2, font=font, fill=st["alt"])

    if note:
        nf = ffn(max(MIN_MUTED, int(H * 0.042)))
        d.text((margin, H - int(H * 0.075)), note, font=nf, fill=st["muted"])
    return img


def layout_statement(
    size, kicker: str, text: str, note: str,
    drawn: list | None = None, top_pad: int = 0, style: dict | None = None,
) -> Image.Image:
    """一句断言：单行（最多两行）占满画面。"""
    st = style or STYLES[LEGACY_STYLE]
    ffn = lambda s: find_style_font(st, s)  # noqa: E731
    W, H = size
    img = Image.new("RGB", size, st["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 11, W, H], fill=st["accent"])

    margin, max_w = text_bounds(W)

    draw_kicker(d, kicker, margin, int(H * 0.072), max_w, int(H * 0.064), H, drawn, style=st)

    font, fsize, lines = fit_block(d, text, max_w, 2, int(H * 0.30), MIN_HERO, font_fn=ffn)
    line_h = int(fsize * 1.18)
    block_h = line_h * len(lines)
    y = max(top_pad, (H + top_pad - block_h) // 2 + int(H * 0.02))
    draw_lines_center(d, lines, font, y, st["ink"], margin + max_w / 2, line_h)
    if drawn is not None:
        drawn.append({"element": "statement", "text": " / ".join(lines), "size": fsize,
                      "color": st["ink"]})

    if note:
        nf = ffn(max(MIN_MUTED, int(H * 0.042)))
        d.text((margin, H - int(H * 0.075)), note, font=nf, fill=st["muted"])
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
    style: str = "",
    day: str = "",
) -> dict:
    # ── 样式解析：显式指定 > 日期哈希 ────────────────────────────────
    # 未知名字必须硬错：静默回退会让「我明明指定了样式」变成一句
    # 无人验证的假设（同 ADR-0044 对 --spec 的纪律）。
    if style:
        if style not in STYLES:
            raise ValueError(
                f"未知样式：{style!r}。可选：{'/'.join(STYLE_ORDER)}"
            )
        style_name = style
    else:
        style_name = style_for_date(day or datetime.date.today().isoformat())
    st = STYLES[style_name]
    ff = lambda s: find_style_font(st, s)  # noqa: E731

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
                panel_size, kicker, hero, hero_unit, payoff, footnote, drawn, top_pad, st
            )
    if lay == "contrast":
        line1 = line1 or derived.get("line1", "")
        line2 = line2 or derived.get("line2", "")
        if not line1 or not line2:
            lay = "statement"
            statement = statement or derived.get("text", "") or title
        else:
            panel = layout_contrast(
                panel_size, kicker, line1, line2, footnote, drawn, top_pad, st
            )
    if lay == "statement":
        # 2026-10-20 实测缺陷：这里原先只写 `statement or title`，于是当 derive_parts()
        # 推出 statement 布局时（无强数字、冒号后只有一个短句），推导出的 text 被丢弃，
        # 封面正文退化成**整条标题原样**，而 kicker 又取了冒号前半句 → 顶部小字与正文
        # 前半句逐字重复，正是 ADR-0044 判定「不增加信息」的那种封面。
        panel = layout_statement(
            panel_size, kicker, statement or derived.get("text", "") or title,
            footnote, drawn, top_pad, st,
        )

    if photo_band is not None:
        img = Image.new("RGB", size, st["bg"])
        img.paste(photo_band, (0, 0))
        d = ImageDraw.Draw(img)
        y_div = photo_band.height
        d.rectangle([0, y_div, W, y_div + PHOTO_DIVIDER - 1], fill=st["accent"])
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
    problems = edge_check(img, margin, y_from=check_from, bg=st["bg"])

    # ── 自检一之二：3:2 裁切区（ADR-0049）────────────────────────────
    #
    # `edge_check` 只保证「没画出画布」，但 1200x675 进信息流会被居中裁成 3:2，
    # 左右各丢 93px（理论 93.75，向下取整）。5 张历史封面逐像素统计**全部**有内容落在这块区域里，
    # 最严重那张把「做钱」的「钱」字切掉一半——而它在画布上完全合法。
    problems += safe_zone_check(img, SAFE_INSET, y_from=check_from, bg=st["bg"])

    # ── 自检一之三：文字与底色的对比度（ADR-0067 补）────────────────
    #
    # 这条检查是在换材质之后才发现缺的：`edge_check` 与 `safe_zone_check`
    # 判的是「内容画在哪」，不判「内容看不看得见」。而 8 种浅色样式的
    # accent/muted 在白纸上只有 3.4~4.0 的对比度——自检全过，字读不出来。
    #
    # 查的是 `drawn` 里记录的**实际画上去的颜色**，不是「按惯例该用哪个色」；
    # 字号按 `card_scale` 缩到 156px 信息流卡片后再判（理由见 contrast_check）。
    problems += contrast_check(drawn, st["bg"], scale=FEED_CARD_W / size[0])

    # ── 自检二：缩略图可读性（测量实际画上去的字号）──────────────────
    #
    # ⚠️ 这里必须用 `drawn` 里记录的**实际字号**，不能用 MIN_HERO 之类的常量。
    # 旧版自检返回 `hero_px_at_thumb = MIN_HERO * scale`——那是把设计意图乘以
    # 一个比例再报出来，与画布上真正画了多大毫无关系。于是 2026-10-20 的封面
    # kicker 只有 38px（缩略图下 10px），自检却一声不响。ADR-0044 发现六。
    scale = FEED_THUMB_W / size[0]
    card_scale = FEED_CARD_W / size[0]
    _, text_max_w = text_bounds(size[0])
    for d_el in drawn:
        px_at_thumb = d_el["size"] * scale
        if px_at_thumb < READABLE_PX:
            problems.append(
                f"{d_el['element']} 在 {FEED_THUMB_W}px 缩略图下仅 {px_at_thumb:.1f}px 高"
                f"（需 ≥{READABLE_PX:.0f}px）：{d_el['text'][:24]!r}"
            )

    # ── 自检二之二：最差展示尺寸（桌面信息流 156px 卡片）──────────────
    #
    # 320px 是「平台下发的资源宽」，但桌面网页信息流实测图片槽只有 156x104。
    # 在 156px 下，320px 时刚好 11px 的字只剩 5.4px——技术上过了上面的检查，
    # 实际上没人读得出。
    #
    # ⚠️ 这条只对**主元素**做警告而不是错误，因为能不能达标主要由内容决定：
    # 856px 宽的安全区里，一行 10 个汉字最多排到 ~85px，想更大就必须把句子
    # 改短——那是写作阶段的选择，不是排版能修的。判成 fatal 会让「句子稍长」
    # 直接发不出去，代价大于收益。
    main_el = next((e for e in drawn if e["element"] in ("hero", "statement", "line1")), None)
    if main_el is not None:
        px_at_card = main_el["size"] * card_scale
        if px_at_card < READABLE_PX:
            warnings.append(
                f"主元素在 {FEED_CARD_W}px 信息流卡片下仅 {px_at_card:.1f}px 高"
                f"（建议 ≥{READABLE_PX:.0f}px）：{main_el['text'][:20]!r} ——"
                f"想让它更大只能把这句话改短（ADR-0049）"
            )

    # ── 自检：换行有没有折出「孤字行」──────────────────────────────
    #
    # 2026-10-31 实测：payoff「免费的那批，先替你定了价」被 `fit_block` 排成
    # 两行——第一行「免费的那批，先替你定了」、第二行只剩一个「价」。`max_lines=2`
    # 之内，所以既没截断也没告警，report 里 `problems: [] warnings: []` 全绿，
    # 图却是坏的。这与本节其他几条同型：**无人值守时没人看图，排版坏了必须
    # 自己说出来**。不改渲染（改了会影响所有存量封面），只把它标出来。
    for e in drawn:
        if e["element"] != "payoff":
            continue
        lines = [seg for seg in str(e.get("text") or "").split(" / ") if seg != ""]
        if len(lines) >= 2 and len(lines[-1]) <= 1:
            warnings.append(
                f"payoff 末行只有 {len(lines[-1])!r} 一个字（换行不均衡）："
                f"{e['text']!r} —— 在 320px 缩略图上会显得像排版事故，"
                f"建议把 payoff 改短 2~3 个字（写作阶段的选择，不是排版能修的）"
            )

    # ── 自检：有没有真的拿到一支带中文字形的字体 ────────────────────
    #
    # 这条必须存在，否则「字体全没命中」这件事对调用方完全不可见：
    # 退回 PIL 默认字体后一切测量照常进行（默认字体没有中文字形，宽度按
    # 缺字框算），`problems` 依然是 []，图里却只有一串方块。
    # 判据同上一条：无人值守时没人看得见，所以失败必须自己说出来。
    if _font_path() is None:
        warnings.append(
            "未找到任何中文字体（CJK_FONT_CANDIDATES 全部不存在），已退回 PIL "
            "默认字体——中文会渲染成方块，请在本机安装中文字体或补一条候选路径"
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
        "style": style_name,
        "style_pinned": bool(style),
        "kicker": kicker,
        "title_chars": len(title),
        "bytes": out_path.stat().st_size,
        "thumb_scale": round(scale, 3),
        "drawn": [{"element": e["element"], "size": e["size"],
                   "px_at_thumb": round(e["size"] * scale, 1),
                   "px_at_card": round(e["size"] * card_scale, 1)} for e in drawn],
        "hero_ratio": round(hero_ratio, 3),
        "restates_title": restates,
        "safe_inset": SAFE_INSET,
        "text_width": round(text_max_w, 1),
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
        "--style",
        default="",
        help=f"钉死封面样式（{'/'.join(STYLE_ORDER)}）。留空则按日期从目录里确定性选一种",
    )
    ap.add_argument(
        "--spec",
        default="",
        help="JSON 文件：显式指定封面论据（layout/kicker/hero/hero_unit/payoff/line1/line2/statement/photo/style）",
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
    style = args.style or str(spec.get("style", "") or "")
    # 未知样式在进绘制前就拦下：显式指定错的样式必须硬错，绝不静默回退。
    if style and style not in STYLES:
        print(json.dumps(
            {"ok": False, "message": f"未知样式：{style!r}（可选：{'/'.join(STYLE_ORDER)}）"},
            ensure_ascii=False,
        ))
        return 1
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
        style=style,
    )
    # ok 由自检决定（是否溢出）。溢出时退出码非 0，让编排器挡住发布——
    # 一张溢出画布的封面比没有封面更糟：它会被发出去，而没人看见。
    ok = info.pop("ok")
    print(json.dumps({"ok": ok, "title": title, **info}, ensure_ascii=False, indent=2))
    return 0 if not info.get("problems") else 1


if __name__ == "__main__":
    sys.exit(main())
