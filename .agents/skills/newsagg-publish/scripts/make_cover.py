#!/usr/bin/env python3
"""封面生成（模板拼图）：把标题排成 16:9 封面图。

设计依据：
- ADR-0013：头条封面必填；兜底链是 AI 生图 → 模板拼图，**不用新闻原图**（版权）
- Q43/B：当前选择"先只用模板拼图"（无图片 provider key）
- ADR-0023：纯 PIL 绘制，不启动浏览器、不联网，内存开销极小

本脚本只做确定性绘制：不判断标题好坏（那是分拣与写作的事）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # 提示用 wewrite 的 python 运行
    print(
        "错误：未安装 Pillow。本脚本需用 wewrite 的 Python 运行：\n"
        "  .uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py ...",
        file=sys.stderr,
    )
    sys.exit(2)

# 头条要求：建议 16:9，尺寸不小于 400x200（ADR-0024 记录的平台约束）
DEFAULT_SIZE = (1200, 675)

# 深色科技感底（与 style.yaml 的 cover_style 一致）
BG_TOP = (14, 18, 28)
BG_BOTTOM = (26, 36, 54)
ACCENT = (94, 168, 255)
TEXT = (240, 244, 250)
MUTED = (150, 162, 180)

CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_font(size: int):
    for p in CJK_FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap_cjk(text: str, font, max_width: int, draw) -> list[str]:
    """按像素宽度折行。中文逐字断，英文/数字按词断。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        w = draw.textlength(trial, font=font)
        if w <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def vertical_gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size, top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line(
            [(0, y), (w, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    return img


def make_cover(title: str, out_path: Path, size=DEFAULT_SIZE, footnote: str = "") -> dict:
    w, h = size
    img = vertical_gradient(size, BG_TOP, BG_BOTTOM)
    d = ImageDraw.Draw(img)

    # 左侧强调竖条
    d.rectangle([0, 0, 10, h], fill=ACCENT)

    margin = int(w * 0.08)
    max_text_w = w - margin * 2

    # 标题：字号随长度自适应（避免溢出）
    n = len(title)
    if n <= 12:
        fsize = int(h * 0.16)
    elif n <= 20:
        fsize = int(h * 0.125)
    elif n <= 30:
        fsize = int(h * 0.10)
    else:
        fsize = int(h * 0.08)
    font = find_font(fsize)

    lines = wrap_cjk(title, font, max_text_w, d)
    line_h = int(fsize * 1.32)
    block_h = line_h * len(lines)
    y = (h - block_h) // 2

    for line in lines:
        d.text((margin, y), line, font=font, fill=TEXT)
        y += line_h

    # 顶部标签
    label_font = find_font(int(h * 0.045))
    d.text((margin, int(h * 0.10)), "AI·开发者工具", font=label_font, fill=ACCENT)

    # 底部脚注
    if footnote:
        ff = find_font(int(h * 0.038))
        d.text((margin, h - int(h * 0.13)), footnote, font=ff, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return {
        "out": str(out_path),
        "size": list(size),
        "title_chars": n,
        "font_size": fsize,
        "lines": len(lines),
        "bytes": out_path.stat().st_size,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="模板拼图封面（ADR-0013 兜底链）")
    ap.add_argument("--slug", required=True, help="成稿 slug（取 outputs/<slug>.md 的标题）")
    ap.add_argument("--out", default=None, help="封面输出路径")
    ap.add_argument("--title", default=None, help="直接给标题（默认从成稿读一级标题）")
    ap.add_argument("--note", default="AI 辅助创作", help="封面脚注")
    args = ap.parse_args()

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
    info = make_cover(title, out, footnote=args.note)
    print(json.dumps({"ok": True, "title": title, **info}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
