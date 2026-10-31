#!/usr/bin/env python3
"""把 viz/facts/repo-facts.json 渲染成单文件 index.html。

渲染层**不在**这个文件里：它在 `.agents/skills/repo-flow-viz/scripts/render_html.py`，
那一层对仓库完全无知——所有内容、分组、统计都来自 facts JSON。

这份文件只是一条仍然可用的命令（`python3 viz/facts/build_html.py`）。旧版本在这里
存了一份渲染器拷贝，后来与技能里的版本漂移了 667 行：同一份 facts 会渲染出两个
不同的页面，而「哪个才是页面」没人说得清。分叉已删除，改为直接调用技能里的实现。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / ".agents" / "skills" / "repo-flow-viz" / "scripts" / "render_html.py"
HERE = Path(__file__).resolve().parent

if not RENDERER.exists():
    sys.exit(f"[FATAL] 找不到渲染层：{RENDERER}（repo-flow-viz 技能未安装？）")

sys.argv = [
    str(RENDERER),
    str(HERE / "repo-facts.json"),
    "-o",
    str(HERE / "index.html"),
]
runpy.run_path(str(RENDERER), run_name="__main__")
