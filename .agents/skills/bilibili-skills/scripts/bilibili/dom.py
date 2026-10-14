"""DOM 定位辅助：优先用「可见文本 + 语义属性」定位，而不是脆弱的类名。

B站创作中心用 BCC 组件库，类名比抖音稳定（无 hash 后缀），但页面结构仍然会随版本
变化。这里统一用 JS 在页面里按文本筛选元素，选中后给元素打一个临时标记属性，
再用该属性作为选择器交给扩展执行真实鼠标点击（isTrusted=true），兼顾"定位稳"
和"点击真"。

为什么不让扩展直接按文本点击（``click_element_by_text``）：那个方法的匹配规则是
"文本包含"，遇到「下一步」「确定」这类到处都有的短文案很容易点错元素。先在这里
按"最内层元素 + 可见性 + 元素类型打分"选准了再点，错误会暴露在 Python 侧而不是
变成一个莫名其妙的状态。
"""

from __future__ import annotations

import json
import logging

from .bridge import BridgePage
from .errors import BridgeError

logger = logging.getLogger(__name__)

# 打标记用的属性名，每次定位前会清空旧的标记
MARK_ATTR = "data-bl-target"

# 可点击元素的默认候选池。
# `span[class*='submit']` 是实测补上的：B站的「立即投稿」「存草稿」是
# `span.submit-add` / `span.submit-draft`，不是 button 也不是 .bcc-button。
CLICKABLE_POOL = (
    "button, .bcc-button, [role='button'], a, "
    "div[class*='tab'], span, label, li, div[class*='option'], span[class*='submit']"
)

_MARK_BY_JS = """
(() => {{
  document.querySelectorAll('[{mark}]').forEach((el) => el.removeAttribute('{mark}'));
  const el = (() => {{ {finder} }})();
  if (!el) return null;
  el.setAttribute('{mark}', '1');
  return '[{mark}="1"]';
}})()
"""

_FIND_BY_TEXT = """
const TEXTS = {texts};
const EXACT = {exact};
const POOL = {pool};
const isVisible = (el) => {{
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const st = window.getComputedStyle(el);
  return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
}};
const score = (el) => {{
  let s = 0;
  if (el.closest('button, .bcc-button, [role="button"], a')) s += 100;
  if (['BUTTON', 'A', 'LABEL'].includes(el.tagName)) s += 50;
  const r = el.getBoundingClientRect();
  // 面积越小越可能是"这个文本本身"，而不是包住它的大容器
  s -= Math.sqrt(Math.max(1, r.width * r.height)) / 10;
  return s;
}};
const candidates = Array.from(document.querySelectorAll(POOL));
for (const text of TEXTS) {{
  const matches = candidates.filter((el) => {{
    const t = (el.textContent || '').trim();
    if (!t) return false;
    if (EXACT ? t !== text : !t.includes(text)) return false;
    return isVisible(el);
  }});
  if (matches.length) {{
    // "最内层优先"是**偏好**，不是过滤条件。
    //
    // 曾经把它写成硬性过滤（"有子元素也含该文本就直接排除"），结果只要池子只列了
    // 外层容器、没列真正的叶子元素，就永远找不到 —— 真实踩到：分区条目给的是
    // .drop-list-v2-item（外层），而真正的叶子是 <p class="item-cont-main">，
    // 两个候选都被排除，报出"未在面板中找到一级分区"这种把人引到错误方向的错误。
    //
    // 所以：优先选没有"也含该文本的子元素"的那个（避免命中整块大容器），
    // 若都没有这样的候选，就退回按面积打分挑最小的。
    const innermost = (el) =>
      Array.from(el.children).some((c) => (c.textContent || '').includes(text)) ? 0 : 1;
    matches.sort((a, b) => {{
      const d = innermost(b) - innermost(a);
      if (d !== 0) return d;
      return score(b) - score(a);
    }});
    return matches[0];
  }}
}}
return null;
"""


def mark_by_js(page: BridgePage, finder: str) -> str | None:
    """执行一段返回元素的 JS（``finder`` 需以 ``return ...`` 结尾），返回标记选择器。"""
    expression = _MARK_BY_JS.format(mark=MARK_ATTR, finder=finder)
    return page.evaluate(expression)


def mark_by_text(
    page: BridgePage,
    texts: tuple[str, ...],
    *,
    exact: bool = False,
    pool: str = CLICKABLE_POOL,
) -> str | None:
    """按文本定位元素，返回标记后的选择器。"""
    finder = _FIND_BY_TEXT.format(
        texts=json.dumps(list(texts), ensure_ascii=False),
        exact="true" if exact else "false",
        pool=json.dumps(pool),
    )
    return mark_by_js(page, finder)


def click_text(
    page: BridgePage,
    texts: tuple[str, ...],
    *,
    exact: bool = False,
    pool: str = CLICKABLE_POOL,
) -> bool:
    """按文本定位并真实点击，返回是否点击成功。"""
    selector = mark_by_text(page, texts, exact=exact, pool=pool)
    if not selector:
        return False
    try:
        page.click_element(selector)
    except BridgeError as e:
        logger.warning("点击 %s 失败: %s", texts, e)
        return False
    logger.info("已点击文本: %s", texts)
    return True


def text_exists(page: BridgePage, texts: tuple[str, ...], *, exact: bool = False) -> bool:
    """页面是否存在匹配文本的可见元素。"""
    return mark_by_text(page, texts, exact=exact) is not None


def find_by_js(page: BridgePage, finder: str) -> str | None:
    """用自定义 JS 定位元素（finder 需以 return 结尾），返回标记选择器。"""
    return mark_by_js(page, finder)
