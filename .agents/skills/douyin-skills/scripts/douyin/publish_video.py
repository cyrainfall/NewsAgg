"""视频发布：上传视频 → 填写作品信息 → 点击发布。

流程刻意拆成"填表"和"点发布"两步（``fill_publish_video_form`` /
``click_publish_video_button``），这样 Agent 可以先填好表单让用户在浏览器里
肉眼确认，再决定是否真的发布 —— 发布是不可逆的，这一步的确认不能省。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

from . import dom
from .bridge import BridgePage
from .errors import (
    AccountRiskControlError,
    DescriptionTooLongError,
    NotLoggedInError,
    PublishError,
    TitleTooLongError,
    UploadTimeoutError,
)
from .human import sleep_random
from .login import page_text
from .selectors import (
    DESCRIPTION_EDITORS,
    DRAFT_BUTTON_TEXTS,
    LOGGED_OUT_TEXTS,
    PUBLISH_BUTTON_TEXTS,
    SCHEDULE_DATETIME_INPUTS,
    SCHEDULE_RADIO_TEXTS,
    TITLE_INPUTS,
    TOPIC_ITEM_SELECTORS,
    TOPIC_POPUP_SELECTORS,
    UPLOAD_INPUTS,
    VISIBILITY_TEXTS,
)
from .types import PublishVideoContent
from .urls import MANAGE_URL, UPLOAD_URL

logger = logging.getLogger(__name__)

# 抖音作品标题上限 30 字、作品简介上限 1000 字（页面自身也会限制）
TITLE_MAX_LEN = 30
DESCRIPTION_MAX_LEN = 1000

# 上传/转码 30 分钟仍未就绪就放弃
UPLOAD_MAX_WAIT = 1800.0
# 「表单已渲染 + 发布按钮可点击」连续保持这么久，才认为视频真的就绪
FORM_STABLE_SECONDS = 5.0

UPLOAD_FAILURE_KEYWORDS = ("上传失败", "解析失败", "格式不支持", "视频损坏", "重新上传失败")
# 风控文案必须足够具体：页面上「发文助手」面板常年写着"…降低违规风险"，
# 用"违规"这种短词会让每次发布都被误判成风控失败（真实踩过）。
RISK_KEYWORDS = (
    "账号异常",
    "无法发布",
    "禁止发布",
    "发布失败",
    "审核未通过",
    "违反社区规范",
    "因违规无法",
)
PUBLISH_SUCCESS_KEYWORDS = ("发布成功", "已发布", "作品发布成功", "发布完成")

# 可见范围选项的候选池：只在这些"选择类"元素里按文本找，避免误点正文
VISIBILITY_POOL = (
    "label, .semi-radio, .semi-select-option, [class*='option'], "
    "[class*='radio'], [class*='select'] span"
)


# ─── 对外入口 ────────────────────────────────────────────────────────────────


def publish_video_content(page: BridgePage, content: PublishVideoContent) -> dict:
    """一步完成：填写表单 + 点击发布，返回发布结果。"""
    fill_publish_video_form(page, content)
    return click_publish_video_button(page)


def fill_publish_video_form(
    page: BridgePage,
    content: PublishVideoContent,
    *,
    skip_upload: bool = False,
) -> None:
    """上传视频并填写表单，但**不**点击发布。

    Args:
        skip_upload: True 时假定当前标签页已经停在上传完成的发布页，只填表单 ——
            既不重新导航也不重新上传。用于反复调试表单字段选择器：抖音的发布页
            可以一直复用，这样调 N 轮也只会往账号里传一次视频。
    """
    if skip_upload:
        if not _is_form_ready(page):
            raise PublishError(
                "当前标签页不是「视频已上传、表单已渲染」的发布页，无法跳过上传。"
                "请先正常跑一次 fill-publish-video。"
            )
    else:
        if not content.video_path:
            raise PublishError("视频不能为空")
        if not os.path.exists(content.video_path):
            raise PublishError(f"视频文件不存在: {content.video_path}")
        _navigate_to_upload_page(page)
        _upload_video(page, content.video_path)

    _fill_form(
        page,
        title=content.title,
        description=content.description,
        tags=content.tags,
        schedule_time=content.schedule_time,
        visibility=content.visibility,
    )
    logger.info("表单已填写完成，等待用户确认后发布")


def click_publish_video_button(page: BridgePage, timeout: float = 120.0) -> dict:
    """点击「发布」按钮并等待结果。"""
    selector = _find_button(page, PUBLISH_BUTTON_TEXTS, enabled_only=True)
    if not selector:
        raise PublishError("未找到可点击的「发布」按钮（视频可能仍在处理中）")

    page.click_element(selector)
    logger.info("已点击发布按钮，等待平台反馈...")
    time.sleep(2)
    return _wait_for_publish_result(page, timeout)


def save_as_draft(page: BridgePage) -> None:
    """点击「存草稿」，把当前内容保存到草稿箱。"""
    selector = _find_button(page, DRAFT_BUTTON_TEXTS, enabled_only=False)
    if not selector:
        raise PublishError("未找到「存草稿」按钮")
    page.click_element(selector)
    time.sleep(2)
    logger.info("已点击「存草稿」")


# ─── 导航与上传 ──────────────────────────────────────────────────────────────


def _navigate_to_upload_page(page: BridgePage) -> None:
    """打开发布页并确认登录态。

    这里只检查"登录页文案"这一个强信号：出现即确定未登录；没出现就交给后续
    ``_upload_video`` 去找上传输入框 —— 找不到时给出的报错里自然会带上登录提示。
    这样既不会把已登录误判成未登录，也不会在未登录时把错误拖到点击发布那一步。
    """
    page.navigate(UPLOAD_URL)
    page.wait_for_load(timeout=180)
    page.wait_for_dom_stable(timeout=10)
    time.sleep(2)

    if dom.text_exists(page, LOGGED_OUT_TEXTS):
        raise NotLoggedInError()


def _upload_video(page: BridgePage, video_path: str) -> None:
    """把本地视频文件塞进上传输入框，并等待平台处理完成。"""
    selector = page.first_present_selector(UPLOAD_INPUTS)
    if not selector:
        raise PublishError(
            "未找到视频上传输入框。若尚未登录请先执行 `python scripts/cli.py check-login`；"
            "若已登录，请运行 `python scripts/cli.py probe` 抓取当前发布页元素，"
            "再把选择器补充到 scripts/douyin/selectors.py 的 UPLOAD_INPUTS。"
        )

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    logger.info("上传视频: %s (%.1f MB)", os.path.basename(video_path), size_mb)

    page.set_file_input(selector, [video_path])
    _wait_for_video_processed(page)


def _is_form_ready(page: BridgePage) -> bool:
    """判断发布表单是否已经渲染出来。

    上传前页面只有一个 ``input[type=file]``；标题框和简介编辑器都是视频上传完成后
    才渲染的，所以它们是"已经进入发布页"的可靠信号（两次真机 probe 都验证过）。
    """
    return (
        page.first_present_selector(TITLE_INPUTS) is not None
        or page.first_present_selector(DESCRIPTION_EDITORS) is not None
    )


def _wait_for_video_processed(page: BridgePage) -> None:
    """等待视频上传 + 转码完成。

    只用两个真机验证过的判据，不用 class 名去猜"是否还在上传"：

    1. **表单已渲染** —— 见 ``_is_form_ready``。
    2. **「发布」按钮可点击** —— 视频没就绪前抖音不会放开这个按钮。

    为什么不用"上传进度条"之类的 class 匹配：抖音给预览播放器的进度条滑块也挂了含
    ``progress`` 的类名，宽泛匹配会永久命中，让这个循环永远不会结束。

    即使这里判断偏早（按钮提前可用），后续 ``click_publish_video_button`` 还会再查一次
    按钮是否可点击，最坏结果是报错而不会误发布。
    """
    start = time.monotonic()
    stable_since: float | None = None

    while time.monotonic() - start < UPLOAD_MAX_WAIT:
        failure = next(
            (kw for kw in UPLOAD_FAILURE_KEYWORDS if kw in page_text(page, limit=4000)), None
        )
        if failure:
            raise PublishError(f"视频上传失败：{failure}")

        ready = _is_form_ready(page) and _find_button(page, PUBLISH_BUTTON_TEXTS, enabled_only=True)
        if ready:
            stable_since = stable_since if stable_since else time.monotonic()
            if time.monotonic() - stable_since >= FORM_STABLE_SECONDS:
                logger.info("视频上传/处理完成（耗时 %.0fs）", time.monotonic() - start)
                return
        else:
            stable_since = None

        time.sleep(1.5)

    raise UploadTimeoutError(f"视频上传/处理超时（{UPLOAD_MAX_WAIT / 60:.0f} 分钟）")


# ─── 表单填写 ────────────────────────────────────────────────────────────────


def _fill_form(
    page: BridgePage,
    *,
    title: str,
    description: str,
    tags: list[str],
    schedule_time: str | None,
    visibility: str,
) -> None:
    if title:
        _fill_title(page, title)

    if description or tags:
        editor = page.first_present_selector(DESCRIPTION_EDITORS)
        if not editor:
            raise PublishError(
                "未找到作品简介编辑器。请运行 `python scripts/cli.py probe` 后更新 "
                "scripts/douyin/selectors.py 的 DESCRIPTION_EDITORS。"
            )
        if description:
            _fill_description(page, editor, description)
        if tags:
            _input_topic_tags(page, editor, tags)

    if schedule_time:
        _set_schedule(page, schedule_time)

    if visibility:
        _set_visibility(page, visibility)


def _fill_title(page: BridgePage, title: str) -> None:
    if len(title) > TITLE_MAX_LEN:
        raise TitleTooLongError(str(len(title)), str(TITLE_MAX_LEN))

    selector = page.first_present_selector(TITLE_INPUTS)
    if not selector:
        raise PublishError(
            "未找到作品标题输入框。请运行 `python scripts/cli.py probe` 后更新 "
            "scripts/douyin/selectors.py 的 TITLE_INPUTS。"
        )

    # 同理，input_text 内部会 el.focus()，不需要先用 CDP 点击聚焦
    page.input_text(selector, title)
    sleep_random(300, 600)


def _editor_is_empty(page: BridgePage, editor_selector: str) -> bool:
    """编辑器是否为空（只剩零宽字符 / 空白 / 占位符）。"""
    return bool(
        page.evaluate(
            f"""
            (() => {{
              const el = document.querySelector({json.dumps(editor_selector)});
              if (!el) return false;
              const text = (el.innerText || "").replace(/[\\u200b\\u00a0\\s]/g, "");
              return text.length === 0;
            }})()
            """
        )
    )


def _fill_description(page: BridgePage, editor_selector: str, description: str) -> None:
    """写入作品简介 —— 一次写入，且只在编辑器为空时进行。

    抖音这个编辑器有三个实测特性，共同决定了这里必须"一次写完、且不可覆盖"：

    1. ``selectAll`` + ``delete`` **清不掉**已有内容 —— 它的行模型不跟随 DOM 选区，
       实测删除后旧内容会被 React 重新渲染回来，而新写入的内容只是叠加在前面；
    2. 换行完全无法保留：``execCommand("insertParagraph")`` 无效，真实 Enter 也
       不增加行（``.ace-line`` 数量不变），``insertHTML`` 的块级标签会被压平；
    3. 因此多次写入（比如逐行输入）会让文字顺序错乱。

    所以策略是：先确认编辑器为空，否则直接报错而不是把内容叠上去；把多行简介压成
    一整段写入（写简介时请让每一行都能独立成句、以标点结尾，压平后仍然通顺）。
    """
    if len(description) > DESCRIPTION_MAX_LEN:
        raise DescriptionTooLongError(str(len(description)), str(DESCRIPTION_MAX_LEN))

    if not _editor_is_empty(page, editor_selector):
        raise PublishError(
            "作品简介编辑器里已有内容，无法安全覆盖：抖音的编辑器不接受程序化清空，"
            "继续写入会变成新旧内容叠加。请刷新发布页后重新上传（不要用 --skip-upload）再试。"
        )

    # 这里刻意不先 click_element 去聚焦：input_content_editable 内部已经 el.focus()，
    # 而每次 CDP 点击都要 chrome.debugger.attach —— 实测那一下要 6~7 秒，纯浪费。
    page.input_content_editable(editor_selector, description.replace("\n", ""))
    sleep_random(300, 500)


def _focus_editor_end(page: BridgePage, editor_selector: str) -> None:
    """把光标放到编辑器末尾（``el.focus()`` 对富文本编辑器不够，必须设置选区）。"""
    page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector({json.dumps(editor_selector)});
            if (!el) return;
            el.focus();
            const range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        }})()
        """
    )


def _input_topic_tags(page: BridgePage, editor_selector: str, tags: list[str]) -> list[str]:
    """逐个输入 #话题并点选联想条目，返回真正创建成话题节点的标签。

    为什么要点选而不是直接留纯文本：点选后抖音会把它变成正式的话题节点（带播放量
    的联想条目），曝光更明确。但点选**必须校验结果** —— 曾经出现过点中了外层容器，
    抖音把正文里的片段当成了话题名（"#交给人——阈"），而当时的代码只看"有没有点到
    某个可见元素"，所以错误被完全掩盖。

    点不到时保留纯文本 `#话题`：纯文本不会破坏正文，而错误的话题节点会。
    """
    created: list[str] = []
    for raw in tags:
        tag = raw.lstrip("#").strip()
        if not tag:
            continue

        page.type_text("#", delay_ms=0)
        sleep_random(200, 400)
        page.type_text(tag, delay_ms=60)

        clicked = _click_topic_item(page, tag)

        # 无论是否点中条目，都按一次空格：
        #  - 点选失败时，空格就是**提交键** —— 抖音会把当前高亮的联想项直接变成
        #    话题节点（这是实际生效的那条路径，实测两次都在这里成功）；
        #  - 点选成功时，空格只是把话题与后续内容分开。
        page.press_key("Space")

        if _has_topic_mention(page, tag):
            created.append(tag)
            logger.info("已创建话题节点: #%s（点选条目=%s）", tag, clicked)
        else:
            logger.warning("未创建话题节点，保留为纯文本: #%s", tag)

        sleep_random(400, 700)

    return created


def _click_topic_item(page: BridgePage, tag: str, timeout: float = 2.5) -> bool:
    """等 # 联想下拉出现后，精确点选「#tag」条目。

    两点实测经验：

    - **必须轮询等下**：输入完关键字立刻查，下拉框通常还没渲染出来，会误判成
      "没有联想"（这正是之前每次都报"未匹配到话题联想"的原因）。
    - 用页面内派发鼠标事件，而不是扩展的坐标真实点击：浮层是 React 渲染的，
      坐标点击要「标记元素 → 取坐标 → 派发」三次跨进程往返，元素容易在中间被
      重新渲染而点不中。

    条目文本把话题名和播放量拼在一起（"#人工智能654.9亿"），所以只能按
    "以 #话题名 开头"匹配，并且必须精确到条目而不是外层容器。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _dispatch_topic_item_click(page, tag):
            return True
        time.sleep(0.25)
    return False


def _dispatch_topic_item_click(page: BridgePage, tag: str) -> bool:
    """在下拉框里找到「#tag」条目并派发一次完整鼠标事件序列，返回是否找到。"""
    return bool(
        page.evaluate(
            f"""
            (() => {{
              const rootSelectors = {json.dumps(list(TOPIC_POPUP_SELECTORS))};
              const itemSelectors = {json.dumps(list(TOPIC_ITEM_SELECTORS))};
              const want = {json.dumps("#" + tag, ensure_ascii=False)};

              const root = rootSelectors.map((s) => document.querySelector(s)).find(Boolean);
              if (!root) return false;

              for (const itemSel of itemSelectors) {{
                const item = Array.from(root.querySelectorAll(itemSel)).find((el) => {{
                  const t = (el.textContent || "").trim();
                  const r = el.getBoundingClientRect();
                  return t.startsWith(want) && r.width > 0 && r.height > 0;
                }});
                if (!item) continue;

                const r = item.getBoundingClientRect();
                const opts = {{
                  bubbles: true, cancelable: true, view: window,
                  clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
                }};
                item.dispatchEvent(new PointerEvent("pointerdown", opts));
                item.dispatchEvent(new MouseEvent("mousedown", opts));
                item.dispatchEvent(new PointerEvent("pointerup", opts));
                item.dispatchEvent(new MouseEvent("mouseup", opts));
                item.dispatchEvent(new MouseEvent("click", opts));
                return true;
              }}
              return false;
            }})()
            """
        )
    )


def _has_topic_mention(page: BridgePage, tag: str, timeout: float = 3.0) -> bool:
    """检查编辑器里是否真的产生了对应的话题节点。

    抖音创建话题节点是异步的（React 重渲染），所以点选后要轮询几次再判定 ——
    实测过"点完立刻查不到、稍后确实存在"的情况，直接查一次会误报失败。
    """
    deadline = time.monotonic() + timeout
    while True:
        found = bool(
            page.evaluate(
                f"""
                (() => {{
                  const want = {json.dumps("#" + tag, ensure_ascii=False)};
                  return Array.from(document.querySelectorAll("[data-mention]")).some(
                    (n) => (n.textContent || "").replace(/\\u00a0/g, "").trim().startsWith(want)
                  );
                }})()
                """
            )
        )
        if found:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def _set_visibility(page: BridgePage, visibility: str) -> None:
    """设置「谁可以看」。设置失败时**必须**报错，避免把私密内容公开出去。"""
    texts = VISIBILITY_TEXTS.get(visibility)
    if not texts:
        supported = "、".join(VISIBILITY_TEXTS)
        raise PublishError(f"不支持的可见范围: {visibility}（支持: {supported}）")

    if dom.click_text(page, texts, pool=VISIBILITY_POOL):
        logger.info("已设置可见范围: %s", visibility)
        sleep_random(300, 600)
        return

    raise PublishError(
        f"未能设置可见范围为「{visibility}」，已中止发布。"
        "请在浏览器中手动确认可见范围，或运行 probe 后更新 VISIBILITY_TEXTS。"
    )


def _set_schedule(page: BridgePage, schedule_time: str) -> None:
    """设置定时发布。设置失败时**必须**报错，避免误按立即发布。"""
    try:
        dt = datetime.fromisoformat(schedule_time)
    except ValueError as e:
        raise PublishError(f"定时发布时间格式错误（需 ISO8601，如 2026-03-10T12:00）: {e}") from e

    if not dom.click_text(page, SCHEDULE_RADIO_TEXTS):
        raise PublishError("未找到「定时发布」选项，已中止发布。请手动确认发布时间设置。")

    sleep_random(500, 900)

    selector = page.first_present_selector(SCHEDULE_DATETIME_INPUTS)
    if not selector:
        raise PublishError("未找到定时发布的时间输入框，已中止发布。")

    value = dt.strftime("%Y-%m-%d %H:%M")
    page.click_element(selector)
    sleep_random(150, 300)
    page.select_all_text(selector)
    page.input_text(selector, value)
    page.press_key("Enter")
    sleep_random(400, 800)

    actual = page.get_element_attribute(selector, "value") or ""
    if value.split(":")[0] not in actual:
        raise PublishError(
            f"定时发布时间未生效（期望 {value}，控件当前值 {actual!r}），已中止发布。"
        )
    logger.info("已设置定时发布: %s", value)


# ─── 按钮定位与发布结果 ──────────────────────────────────────────────────────


def _find_button(
    page: BridgePage,
    texts: tuple[str, ...],
    *,
    enabled_only: bool,
) -> str | None:
    """在按钮类元素里按精确文本找按钮，返回标记后的选择器。"""
    enabled_clause = "if (isDisabled(el)) return false;" if enabled_only else ""
    finder = (
        "const texts = " + json.dumps(list(texts), ensure_ascii=False) + ";"
        "const pool = Array.from(document.querySelectorAll("
        "\"button, .semi-button, [role='button']\"));"
        "const isDisabled = (el) => {"
        " const b = el.closest('button, .semi-button, [role=\"button\"]') || el;"
        " return !!b.disabled || String(b.className || '').includes('disabled')"
        " || b.getAttribute('aria-disabled') === 'true'; };"
        "const isVisible = (el) => { const r = el.getBoundingClientRect();"
        " return r.width > 0 && r.height > 0; };"
        "return pool.find((el) => {"
        " const t = (el.textContent || '').trim();"
        " if (!texts.includes(t)) return false;"
        " if (!isVisible(el)) return false;"
        " " + enabled_clause + " return true; }) || null;"
    )
    return dom.find_by_js(page, finder)


def _wait_for_publish_result(page: BridgePage, timeout: float) -> dict:
    """轮询发布结果：成功 / 风控 / 失败 / 未知。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = page.get_url()
        text = page_text(page, limit=6000)

        if "/creator-micro/content/manage" in url or MANAGE_URL in url:
            return {"success": True, "status": "发布成功，已跳转到作品管理页", "url": url}

        success = next((kw for kw in PUBLISH_SUCCESS_KEYWORDS if kw in text), None)
        if success:
            return {"success": True, "status": success, "url": url}

        risk = next((kw for kw in RISK_KEYWORDS if kw in text), None)
        if risk:
            raise AccountRiskControlError("risk_control", risk)

        failure = next((kw for kw in UPLOAD_FAILURE_KEYWORDS if kw in text), None)
        if failure:
            raise PublishError(f"发布失败：{failure}")

        time.sleep(1.5)

    raise PublishError(
        f"已点击发布，但 {timeout:.0f}s 内未捕获到明确结果。"
        f"请到 {MANAGE_URL} 确认是否发布成功，避免重复发布。"
    )
