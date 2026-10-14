"""页面结构探测：B站改版导致选择器失效时，用它抓取当前页面元素再更新选择器。

用法：
    # 抓当前标签页（排查表单字段时用：此时页面应处于"视频已上传、表单已渲染"状态）
    python scripts/cli.py probe

    # 先导航再抓（排查上传区时用）
    python scripts/cli.py probe --url "https://member.bilibili.com/platform/upload/video/frame"

输出里除了各元素的属性清单，还有两样专为排查准备的东西：

- ``selector_report`` —— 逐个列出 selectors.py 里每条候选选择器当前是否命中，
  不用再一条条手工试。
- ``iframe_report`` —— 页面里的 iframe 清单。本技能的 DOM 查询与 CDP 操作都
  只作用于顶层文档，如果哪天上传区被搬进 iframe，元素会集体"消失"；有这个报告
  就能立刻区分"选择器坏了"和"结构变了"。
"""

from __future__ import annotations

import json

from .bridge import BridgePage
from .human import sleep_random

_INTERESTING = {
    "inputs": "input",
    "textareas": "textarea",
    "contenteditables": "div[contenteditable='true'], [contenteditable='true']",
    "file_inputs": "input[type='file']",
    "buttons": "button, .bcc-button, [role='button']",
    "tag_items": "div[class*='tag-item'], span[class*='tag-item']",
    "select_widgets": "div[class*='select-item-cont'], div[class*='select-controller']",
}

# 注意要把 data-placeholder 和 editor_id 都带上：B站的富文本编辑器用这两个属性
# 而不是 placeholder，漏掉会得出"简介编辑器没有 placeholder"的错误结论。
_ATTRS = [
    "placeholder",
    "data-placeholder",
    "type",
    "accept",
    "class",
    "aria-label",
    "editor_id",
]

# iframe 小到这种程度基本是埋点用的隐形框，报告出来只会干扰判断
_MIN_IFRAME_PX = 40


def probe_page(page: BridgePage, url: str | None = None) -> dict:
    """抓取当前（或指定）页面的关键元素清单。"""
    if url:
        page.navigate(url)
        page.wait_for_load(timeout=120)
        page.wait_for_dom_stable(timeout=8)
        sleep_random(500, 1000)

    result: dict = {
        "url": page.get_url(),
        "title": page.get_page_info().get("title", ""),
        "body_text": (
            page.evaluate("(document.body ? (document.body.innerText || '') : '').slice(0, 3000)")
            or ""
        ),
        "selector_report": selector_report(page),
        "iframe_report": iframe_report(page),
    }

    for name, selector in _INTERESTING.items():
        result[name] = page.get_elements_info(selector, _ATTRS)

    return result


def iframe_report(page: BridgePage) -> dict:
    """列出页面里尺寸有意义的 iframe，并在存在时给出提示。"""
    all_frames = page.get_iframes()
    frames = [f for f in all_frames if max(f.get("width", 0), f.get("height", 0)) >= _MIN_IFRAME_PX]
    if not frames:
        return {"count": 0, "frames": []}

    return {
        "count": len(frames),
        "count_total": len(all_frames),
        "frames": frames,
        "hint": (
            "页面存在 iframe。本技能的所有 DOM 查询与点击都只作用于顶层文档，"
            "如果找不到元素，请先确认它不在这些 iframe 里（见 CLAUDE.md 的 iframe 一节）。"
        ),
    }


def selector_report(page: BridgePage) -> dict[str, dict[str, object]]:
    """逐个检查 ``selectors.SELECTOR_CANDIDATES`` 里每条候选的命中情况。

    一次 JS 调用查完全部候选（而不是每条一次往返），并对非法选择器返回
    ``"INVALID_SELECTOR"`` 而不是让整次 probe 崩掉 —— 排查改版时最需要的是
    "哪条坏了"的信息，而不是又一个报错。
    """
    from . import selectors

    candidates = {name: list(getattr(selectors, name)) for name in selectors.SELECTOR_CANDIDATES}

    return (
        page.evaluate(
            f"""
        (() => {{
            const candidates = {json.dumps(candidates, ensure_ascii=False)};
            const out = {{}};
            for (const group of Object.keys(candidates)) {{
                out[group] = {{}};
                for (const sel of candidates[group]) {{
                    try {{
                        out[group][sel] = !!document.querySelector(sel);
                    }} catch (e) {{
                        out[group][sel] = "INVALID_SELECTOR";
                    }}
                }}
            }}
            return out;
        }})()
        """
        )
        or {}
    )
