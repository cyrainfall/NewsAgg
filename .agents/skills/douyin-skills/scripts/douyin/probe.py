"""页面结构探测：抖音改版导致选择器失效时，用它抓取当前页面元素再更新选择器。

用法：
    # 抓当前标签页（排查表单字段时用：此时页面应处于"视频已上传、表单已渲染"状态）
    python scripts/cli.py probe

    # 先导航再抓（排查上传区时用）
    python scripts/cli.py probe --url "https://creator.douyin.com/creator-micro/content/upload"

输出里除了各元素的属性清单，还有 ``selector_report`` —— 逐个列出 selectors.py 里
每条候选选择器当前是否命中，排查时不用再一条条手工试。
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
    "buttons": "button, .semi-button, [role='button']",
    "images": "img[src^='data:image']",
}

# 注意要包含 data-placeholder：抖音的富文本编辑器用 data-placeholder 而不是
# placeholder，漏掉它会得出"简介编辑器没有 placeholder"的错误结论。
_ATTRS = ["placeholder", "data-placeholder", "type", "accept", "class", "aria-label"]


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
    }

    for name, selector in _INTERESTING.items():
        result[name] = page.get_elements_info(selector, _ATTRS)

    return result


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
