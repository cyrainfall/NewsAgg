"""视频投稿：上传视频 → 填写稿件信息 → 立即投稿。

流程刻意拆成"填表"（``fill_publish_video_form``）和"提交"
（``click_publish_video_button``）两步：投稿不可逆，Agent 应当先填好表单让用户在
浏览器里肉眼确认，再决定是否真的提交。

关于投稿页的两种形态（这点对理解下面的等待逻辑很关键）：
B站的视频投稿页在不同版本 / 灰度下有两种形态，代码必须同时兜住 ——

    A. 单页表单：选完文件就地展开「基本设置」（标题 / 分区 / 标签 / 简介）+ 立即投稿
    B. 两步向导：选完文件先进入"分P 列表"页（每个分P 可改标题），点「下一步」
       才进入基本设置

所以等待"视频就绪"时，**两种就绪信号都认**（见 ``_wait_for_video_ready``），
再按信号决定要不要点「下一步」。只在探测到分P 列表形态时才走第二步。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime

from . import dom
from .bridge import BridgePage
from .errors import (
    AccountRiskControlError,
    DescriptionTooLongError,
    NotLoggedInError,
    PublishError,
    TagTooLongError,
    TitleTooLongError,
    TooManyTagsError,
    UploadTimeoutError,
)
from .human import sleep_random
from .login import page_text
from .selectors import (
    BUTTON_POOL,
    CATEGORY_ITEM_POOL,
    CATEGORY_PANEL_SELECTORS,
    CATEGORY_PLACEHOLDER_TEXTS,
    CATEGORY_TRIGGER_SELECTORS,
    COVER_CONFIRM_TEXTS,
    COVER_DIALOG_POOL,
    COVER_ENTRY_TEXTS,
    COVER_INPUTS,
    COVER_MISSING_TEXTS,
    DECLARATION_OPTION_POOL,
    DECLARATION_PANEL_SELECTOR,
    DECLARATION_TEXTS,
    DECLARATION_TRIGGER_SELECTORS,
    DESCRIPTION_EDITORS,
    DESCRIPTION_TEXTAREAS,
    DRAFT_BUTTON_TEXTS,
    LOGGED_OUT_TEXTS,
    NEXT_STEP_BUTTON_TEXTS,
    PUBLISH_BUTTON_TEXTS,
    PUBLISH_CONFIRM_TEXTS,
    PUBLISH_FAILURE_KEYWORDS,
    PUBLISH_SUCCESS_KEYWORDS,
    RISK_KEYWORDS,
    SCHEDULE_DATETIME_INPUTS,
    SCHEDULE_RADIO_TEXTS,
    SUBMITTING_TEXTS,
    TAG_DELETE_SELECTORS,
    TAG_INPUTS,
    TAG_ITEM_SELECTORS,
    TAG_SELECTED_REGION_SELECTORS,
    TITLE_INPUTS,
    UPLOAD_FAILURE_KEYWORDS,
    UPLOADING_TEXTS,
)
from .types import PublishVideoContent
from .urls import MANAGE_URL, PASSPORT_HOST, UPLOAD_URL

logger = logging.getLogger(__name__)

# B站作品标题上限 80 字、简介上限 2000 字、标签最多 10 个且每个最长 20 字
TITLE_MAX_LEN = 80
DESCRIPTION_MAX_LEN = 2000
TAG_MAX_COUNT = 10
TAG_MAX_LEN = 20

# 上传 / 转码 30 分钟仍未就绪就放弃
UPLOAD_MAX_WAIT = 1800.0
# 同一状态连续保持这么久，才认为视频真的就绪（挡住"表单先渲染、随后被重置"的抖动）
FORM_STABLE_SECONDS = 5.0
# 点「下一步」之后等基本设置渲染出来
NEXT_STEP_TIMEOUT = 30.0
# 等待就绪时每隔多久打一次进度日志（大文件要跑几分钟，不能全程静默）
PROGRESS_LOG_SECONDS = 10.0
# 轮询稿件列表的间隔（判定投稿是否真的落地的权威手段）
ARCHIVE_POLL_SECONDS = 15.0
# 稿件列表接口 + 时间容差。
#
# 为什么必须查接口、而不是只看页面文案/URL：实测 B站 的投稿请求要**好几分钟**才落地
# （这条视频 23:24:36 点击、23:33:12 才出现在列表里，约 8.5 分钟），全程页面一直停在
# 「提交中...」，既不跳转也不弹任何提示。只看页面的话只能得出"没结果"的错误结论。
#
# ctime 是服务端时间，与本地时钟可能有偏差，所以给足容差再判断"这是本次投稿产生的"。
ARCHIVE_LIST_PATH = "/x/web/archives"
ARCHIVE_TIME_TOLERANCE_SECONDS = 180.0

# 简介回读校验时比对的最小长度：太短的文本容易和页面其它文本撞上
_VERIFY_MIN_CHARS = 8
# 回读时忽略的字符：空白 + 零宽字符（富文本编辑器会塞这些）
_IGNORED_CHARS = "\u200b\u200c\u200d\u2060\ufeff\u00a0"


# ─── 对外入口 ────────────────────────────────────────────────────────────────


def publish_video_content(page: BridgePage, content: PublishVideoContent) -> dict:
    """一步完成：填写表单 + 立即投稿，返回投稿结果。"""
    detail = fill_publish_video_form(page, content)
    result = click_publish_video_button(page)
    return {**result, **detail}


def fill_publish_video_form(
    page: BridgePage,
    content: PublishVideoContent,
    *,
    skip_upload: bool = False,
) -> dict:
    """上传视频并填写表单，但**不**点「立即投稿」。

    Args:
        skip_upload: True 时假定当前标签页已经停在「视频已上传、表单已渲染」的
            投稿页，只填表单 —— 既不重新导航也不重新上传。用于反复调试表单字段的
            选择器：B站的投稿页可以一直复用，这样调 N 轮也只会往账号里传一次视频。

    Returns:
        描述这次填写做了什么的 dict（供 CLI 原样透给用户，便于确认）。
    """
    # 每一步都自报家门。这个流程里有多次几十秒级的等待（加载页面、上传转码），
    # 全程静默会让用户以为脚本卡死了 —— 这个误会真实发生过好几次。
    if skip_upload:
        logger.info("跳过上传，直接在当前投稿页填表")
        if not _is_form_ready(page):
            raise PublishError(
                "当前标签页不是「视频已上传、表单已渲染」的投稿页，无法跳过上传。"
                "请先正常跑一次 fill-publish-video。"
            )
        detail: dict = {"upload": {"skipped": True}}
    else:
        if not content.video_path:
            raise PublishError("视频不能为空")
        if not os.path.exists(content.video_path):
            raise PublishError(f"视频文件不存在: {content.video_path}")
        _navigate_to_upload_page(page)
        detail = {"upload": _upload_and_wait(page, content.video_path)}

    detail.update(
        _fill_form(
            page,
            title=content.title,
            description=content.description,
            tags=content.tags,
            category_primary=content.category_primary,
            category_secondary=content.category_secondary,
            cover_path=content.cover_path,
            schedule_time=content.schedule_time,
            replace_tags=content.replace_tags,
            declaration=content.declaration,
        )
    )
    # 如实报出最终的标签列表。B站 会按视频内容预选一批标签，脚本请求的和稿件上真正
    # 带的往往不是一回事 —— 不报出来，用户只能自己在浏览器里发现"标签不对"。
    detail["tags_final"] = _selected_tags(page)
    logger.info("表单已填写完成，当前标签: %s", "、".join(detail["tags_final"]) or "（无）")
    return detail


def click_publish_video_button(
    page: BridgePage,
    timeout: float = 600.0,
    *,
    force: bool = False,
) -> dict:
    """点击「立即投稿」并等待平台反馈。"""
    assert_publish_ready(page, force=force)

    selector = _find_button(page, PUBLISH_BUTTON_TEXTS, enabled_only=True)
    if not selector:
        # 区分"按钮不存在"和"按钮在但点不了"。前者是选择器/候选池的问题（要去 probe），
        # 后者是必填项没填完（要去补表单）。混成一条报错会把人引到完全错误的方向。
        if _find_button(page, PUBLISH_BUTTON_TEXTS, enabled_only=False):
            raise PublishError(
                "找到了「立即投稿」按钮，但它当前不可点击 —— 通常是必填项还没填完"
                "（标题 / 分区 / 创作声明等）。请在浏览器里看一下还缺哪一项，补上后重试；"
                "内容不想丢的话可以先执行 `python scripts/cli.py save-draft`。"
            )
        raise PublishError(
            "未找到「立即投稿」按钮。请运行 `python scripts/cli.py probe` 确认按钮的元素"
            "结构，并把它的类名补进 scripts/bilibili/selectors.py 的 BUTTON_POOL。"
        )

    clicked_at = time.time()
    landed = page.click_element(selector)
    logger.info(
        "已点击「立即投稿」（落点 %s, %s，命中 %s），等待平台反馈..."
        "（B站 的投稿请求实测可能要好几分钟，期间按钮会一直显示「提交中...」，属正常）",
        landed.get("x"),
        landed.get("y"),
        landed.get("tag"),
    )
    time.sleep(2)
    _confirm_publish_dialog_if_any(page)
    return _wait_for_publish_result(page, timeout, clicked_at=clicked_at)


def save_as_draft(page: BridgePage) -> None:
    """点击「存草稿」，把当前内容保存到草稿箱。"""
    selector = _find_button(page, DRAFT_BUTTON_TEXTS, enabled_only=False)
    if not selector:
        raise PublishError(
            "未找到「存草稿」按钮。请运行 `python scripts/cli.py probe` 确认按钮的元素"
            "结构，并把它的类名补进 scripts/bilibili/selectors.py 的 BUTTON_POOL。"
        )
    page.click_element(selector)
    time.sleep(2)
    logger.info("已点击「存草稿」")


def assert_publish_ready(page: BridgePage, *, force: bool = False) -> None:
    """提交前的最后一道闸。

    投稿不可逆，而"页面看起来填好了"和"B站真的收到了"是两件事。这里只检查
    三个能立刻验出问题的点：标题非空、分区已选、没有挡住按钮的弹窗。
    任何一项不满足就中止，并把"怎么补"写进错误里。
    """
    # 先区分"找不到标题框"和"标题框是空的"：前者是选择器坏了，让人去补 --title-file
    # 只会白折腾一轮，还会因为"补了文案还是不行"而开始怀疑文案。
    if not page.first_present_selector(TITLE_INPUTS):
        raise PublishError(
            "未找到稿件标题输入框，无法确认标题已填写，已中止投稿。"
            "请运行 `python scripts/cli.py probe` 后更新 scripts/bilibili/selectors.py "
            "的 TITLE_INPUTS。"
        )

    if not _read_title(page):
        raise PublishError(
            "稿件标题为空，B站要求必须填写标题。请补 --title-file 后重新执行 "
            "fill-publish-video（不要直接再点一次投稿）。"
        )

    if not _category_chosen(page):
        raise PublishError(
            '尚未选择分区，B站要求每个稿件都必须有分区。请用 --category "一级,二级"'
            '（例如 --category "生活,日常"）重新执行 fill-publish-video，'
            "或直接在浏览器里手动选择分区。"
        )

    # 封面是**必填**的 —— 实测不设封面时点投稿会被 B站 拦下并提示「请先上传封面」。
    # 这条检查很值：不拦的话，"点了没反应"会表现成一个 120 秒的静默超时。
    if dom.text_exists(page, COVER_MISSING_TEXTS):
        raise PublishError(
            "还没设置封面，B站 会拒绝投稿（实测提示「请先上传封面」）。"
            "请二选一：在浏览器里点一张系统推荐封面，或用 --cover /abs/path/cover.jpg "
            "配合 publish-video 指定封面图。"
        )

    # 创作声明同样是必填。不拦的话，用户会遇到"投稿按钮点不动但不知道为什么"。
    if not _read_declaration(page):
        known = sorted({text for group in DECLARATION_TEXTS.values() for text in group})
        raise PublishError(
            "「创作声明」还没设置，B站 要求必填。请在浏览器里选一下，"
            '或用 --declaration 指定，例如 --declaration "含AI生成内容"'
            "（AI 生成画面/合成语音的视频选这个）。可选值：" + "、".join(known)
        )

    if force:
        return

    dialog = _visible_dialog_text(page)
    if dialog:
        raise PublishError(
            f"页面上有未关闭的弹窗，很可能挡住「立即投稿」按钮：{dialog[:120]}。"
            "请先在浏览器里处理它（或按 Esc 关闭）；确认没有遮挡时可在命令后加 --force 跳过本检查。"
        )


# ─── 导航与上传 ──────────────────────────────────────────────────────────────


def _navigate_to_upload_page(page: BridgePage) -> None:
    """打开发布页并确认登录态。

    未登录时 member.bilibili.com 会 302 到 passport，所以先看 URL 域（最快的强信号），
    再看登录页文案。两者都没有就交给后面的上传步骤 —— 找不到视频输入框时给出的
    报错里自然会带上登录提示，不会卡在一个无关的地方。
    """
    logger.info("正在打开发布页: %s", UPLOAD_URL)
    page.navigate(UPLOAD_URL)
    page.wait_for_load(timeout=180)
    page.wait_for_dom_stable(timeout=10)
    time.sleep(2)

    if PASSPORT_HOST in page.get_url():
        raise NotLoggedInError()
    if dom.text_exists(page, LOGGED_OUT_TEXTS):
        raise NotLoggedInError()


def _find_video_input(page: BridgePage) -> str | None:
    """找出视频文件输入框。

    **不能**简单地取第一个 ``input[type=file]``：封面上传也有一个，而且它在 DOM 里
    的位置不固定。抖音那边可以靠"页面只有一个 file input"蒙对，B站这边蒙错就是
    把 mp4 塞进封面框，报错还看不出原因。

    判据按可靠性递减：accept 里出现视频标识 → accept 不是"只收图片" → 放弃。
    宁可返回 None 让调用方报出可排查的错误，也不要赌一把选错输入框。
    """
    finder = """
    const pool = Array.from(document.querySelectorAll("input[type='file']"));
    const acc = (el) => (el.getAttribute('accept') || '').toLowerCase();
    const KEYS = ['video', 'mp4', 'mov', 'mkv', 'flv', 'avi', 'wmv', 'webm', 'm4v', 'mpeg'];
    const strong = pool.find((el) => KEYS.some((k) => acc(el).includes(k)));
    if (strong) return strong;
    return pool.find((el) => {
      const a = acc(el);
      if (!a) return false;
      return !a.includes('image') && !a.includes('png') && !a.includes('jpg');
    }) || null;
    """
    return dom.find_by_js(page, finder)


def _upload_and_wait(page: BridgePage, video_path: str) -> dict:
    """把本地视频塞进上传框，等平台处理完，必要时跨过「下一步」。"""
    selector = _find_video_input(page)
    if not selector:
        raise PublishError(
            "未找到视频上传输入框。若尚未登录请先执行 `python scripts/cli.py check-login`；"
            "若已登录，请运行 `python scripts/cli.py probe` 抓取当前投稿页元素，"
            "再把选择器补充到 scripts/bilibili/selectors.py 的 UPLOAD_INPUTS，"
            "并确认上传区没有被放进 iframe（probe 输出里的 iframe_report）。"
        )

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    logger.info("上传视频: %s (%.1f MB)", os.path.basename(video_path), size_mb)

    page.set_file_input(selector, [video_path])
    state = _wait_for_video_ready(page)

    if state["layout"] == "wizard":
        _click_next_step(page)
        state = _wait_for_video_ready(page)

    state.update({"input_selector": selector, "size_mb": round(size_mb, 1)})
    return state


def _is_form_ready(page: BridgePage) -> bool:
    """投稿表单（基本设置）是否已经渲染出来。

    选文件之前页面上只有上传区；标题框 / 简介编辑器都是**上传流程推进之后**
    才渲染的，所以它们是"已经进入填表阶段"的可靠信号 —— 比猜 class 稳得多。
    """
    return (
        page.first_present_selector(TITLE_INPUTS) is not None
        or page.first_present_selector(DESCRIPTION_EDITORS) is not None
        or page.first_present_selector(DESCRIPTION_TEXTAREAS) is not None
    )


def _wait_for_video_ready(page: BridgePage, timeout: float = UPLOAD_MAX_WAIT) -> dict:
    """等待投稿页进入"可以填表"的状态，返回这一页是哪种形态。

    完成信号有两种，都认（原因见模块 docstring）：

    - ``single_page``：基本设置（标题 / 简介 / 分区…）已渲染出来。
    - ``wizard``：出现可点的「下一步」按钮，且基本设置还没渲染。

    ⚠️ **不要拿「立即投稿」按钮是否可点击当作就绪判据**（这是踩过的真坑）。
    B站的投稿页在选完文件后就把基本设置渲染出来，而「立即投稿」按钮在**表单填完
    之前一直都是可点的** —— 页面自己写着"信息填完后，就可投稿！不需等待上传完成哦~"。
    所以那样判据在填表前永远不成立，脚本会一直空转到 1800 秒超时。

    也不按"上传进度条 / 转码中的 class"判断：B站的播放器也带 progress 类名，
    宽泛匹配会永久命中（抖音那边真实踩过这个坑，见 CLAUDE.md）。

    另外这个循环**必须**定期打日志。上传大文件时它能跑几分钟，全程静默会让用户
    以为脚本卡死了（真实发生过）。
    """
    start = time.monotonic()
    stable_since: float | None = None
    next_progress = start + PROGRESS_LOG_SECONDS

    while time.monotonic() - start < timeout:
        text = page_text(page, limit=6000)

        failure = next((kw for kw in UPLOAD_FAILURE_KEYWORDS if kw in text), None)
        if failure:
            raise PublishError(f"视频上传失败：{failure}")

        layout = _detect_ready_layout(page, text)
        if layout:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= FORM_STABLE_SECONDS:
                logger.info("投稿页已就绪（形态=%s，耗时 %.0fs）", layout, time.monotonic() - start)
                return {"layout": layout, "elapsed_seconds": round(time.monotonic() - start, 1)}
        else:
            stable_since = None

        if time.monotonic() >= next_progress:
            uploading = next((kw for kw in UPLOADING_TEXTS if kw in text), None)
            logger.info(
                "仍在等待投稿页就绪（已 %.0fs，%s）...",
                time.monotonic() - start,
                f"页面显示「{uploading}」" if uploading else "未见「上传中」提示",
            )
            next_progress = time.monotonic() + PROGRESS_LOG_SECONDS

        time.sleep(1.5)

    raise UploadTimeoutError(f"视频上传/转码超时（{timeout / 60:.0f} 分钟）")


def _detect_ready_layout(page: BridgePage, text: str) -> str | None:
    """返回 "single_page" / "wizard" / None。

    先否掉"还在上传中"：页面自己说"正在上传"时就不算就绪。
    注意这只影响"什么时候开始填表"，不影响能不能投稿 —— B站允许上传未完成就投稿，
    但等它明确说完会更稳妥。
    """
    if any(kw in text for kw in UPLOADING_TEXTS):
        return None

    if _is_form_ready(page):
        return "single_page"

    if _find_button(page, NEXT_STEP_BUTTON_TEXTS, enabled_only=True):
        return "wizard"

    return None


def _click_next_step(page: BridgePage) -> None:
    """两步向导形态：点「下一步」进入基本设置。"""
    selector = _find_button(
        page, NEXT_STEP_BUTTON_TEXTS, enabled_only=True, timeout=NEXT_STEP_TIMEOUT
    )
    if not selector:
        raise PublishError("视频已就绪，但未找到可点击的「下一步」按钮，无法进入投稿表单。")
    page.click_element(selector)
    logger.info("已点击「下一步」进入投稿表单")
    page.wait_for_dom_stable(timeout=10)
    time.sleep(1)


# ─── 表单填写 ────────────────────────────────────────────────────────────────


def _fill_form(
    page: BridgePage,
    *,
    title: str,
    description: str,
    tags: list[str],
    category_primary: str,
    category_secondary: str,
    cover_path: str,
    schedule_time: str | None,
    replace_tags: bool,
    declaration: str,
) -> dict:
    """填表主流程。

    顺序是有讲究的：标题 → 分区 → 创作声明 → 标签 → 简介 → 封面 → 定时发布。
    简介放在标签后面，是因为 B站简介编辑器是富文本，填写时会抢焦点；如果先写简介
    再输标签，标签的按键事件可能落到编辑器里变成正文（这是抖音那边真实踩过的
    顺序坑）。
    """
    detail: dict = {}

    if title:
        logger.info("① 填写标题（%d 字）...", len(title))
        _fill_title(page, title)
        detail["title_length"] = len(title)

    if category_primary:
        logger.info("② 选择分区: %s / %s ...", category_primary, category_secondary)
        _select_category(page, category_primary, category_secondary)
        detail["category"] = f"{category_primary} / {category_secondary}".strip(" /")

    if declaration:
        logger.info("③ 设置创作声明: %s ...", declaration)
        detail["declaration"] = _set_declaration(page, declaration)

    if replace_tags:
        logger.info("④ 先清空 B站 已有标签...")
        detail["tags_removed"] = _remove_all_tags(page)

    if tags:
        logger.info("④ 创建标签（请求 %d 个）...", len(tags))
        detail["tags_created"] = _input_tags(page, tags)

    if description:
        logger.info("⑤ 写入简介（%d 字）...", len(description))
        detail["description_editor"] = _fill_description(page, description)

    if cover_path:
        logger.info("⑥ 上传封面...")
        detail["cover_uploaded"] = _upload_cover(page, cover_path)

    if schedule_time:
        logger.info("⑦ 设置定时发布: %s ...", schedule_time)
        _set_schedule(page, schedule_time)
        detail["schedule_time"] = schedule_time

    return detail


def _fill_title(page: BridgePage, title: str) -> None:
    if len(title) > TITLE_MAX_LEN:
        raise TitleTooLongError(str(len(title)), str(TITLE_MAX_LEN))

    selector = page.first_present_selector(TITLE_INPUTS)
    if not selector:
        raise PublishError(
            "未找到稿件标题输入框。请运行 `python scripts/cli.py probe` 后更新 "
            "scripts/bilibili/selectors.py 的 TITLE_INPUTS。"
        )

    page.input_text(selector, title)
    sleep_random(300, 600)

    actual = page.get_input_value(selector).strip()
    if actual != title.strip():
        raise PublishError(
            f"标题写入后回读不一致（期望 {title!r}，实际 {actual!r}）。"
            "B站可能限制了标题字符数或改用了其它控件，请运行 probe 确认后更新选择器。"
        )


def _read_title(page: BridgePage) -> str:
    selector = page.first_present_selector(TITLE_INPUTS)
    if not selector:
        return ""
    return page.get_input_value(selector).strip()


# ─── 创作声明 ────────────────────────────────────────────────────────────────


def _read_declaration(page: BridgePage) -> str:
    selector = page.first_present_selector(DECLARATION_TRIGGER_SELECTORS)
    if not selector:
        return ""
    return page.get_input_value(selector).strip()


def _declaration_panel_open(page: BridgePage) -> bool:
    """下拉面板是否**真的展开了**。

    不能用 has_element 判断：`ul.bcc-select-option-list` 在**收起时也留在 DOM 里**
    （只是 height 为 0），用存在性判断等于永远为真，这个校验就形同虚设 ——
    接着去点选项会落在一个 0 高度的元素上，报出误导性的"未找到选项"。
    """
    return bool(
        page.evaluate(
            f"""
            (() => {{
              const el = document.querySelector({json.dumps(DECLARATION_PANEL_SELECTOR)});
              if (!el) return false;
              const r = el.getBoundingClientRect();
              if (r.height <= 0 || r.width <= 0) return false;
              const st = window.getComputedStyle(el);
              if (st.display === 'none' || st.visibility === 'hidden') return false;
              return Number(st.opacity) > 0.1;
            }})()
            """
        )
    )


def _declaration_options(page: BridgePage) -> list[str]:
    """列出下拉里实际可选的文案，用于报错时给出可选项。"""
    return (
        page.evaluate(
            f"""
            (() => {{
              const lis = document.querySelectorAll({json.dumps(DECLARATION_OPTION_POOL)});
              return Array.from(lis).map((el) => (el.innerText || '').trim())
                .filter((t) => t && t.length <= 40);
            }})()
            """
        )
        or []
    )


def _set_declaration(page: BridgePage, declaration: str) -> str:
    """设置「创作声明」。**必填项**，不设置 B站 会拒绝投稿。

    Returns:
        实际写入的文案（回读校验通过后的值）。
    """
    wanted = DECLARATION_TEXTS.get(declaration, (declaration,))
    selector = page.first_present_selector(DECLARATION_TRIGGER_SELECTORS)
    if not selector:
        raise PublishError(
            "未找到「创作声明」控件。请运行 `python scripts/cli.py probe` 确认结构，"
            "并更新 selectors.py 的 DECLARATION_TRIGGER_SELECTORS。"
        )

    # 触发器是开关语义：面板已经开着时再点会把它关掉（分区那边踩过同一个坑）。
    if not _declaration_panel_open(page):
        page.click_element(selector)
        if not _poll_until(lambda: _declaration_panel_open(page), timeout=3.0):
            raise PublishError(
                "「创作声明」下拉打不开，已中止投稿。这个控件在一个**被程序化反复操作过的**"
                "页面上会失去响应（实测：可信点击、pointerdown、focus 都送达了，"
                "Vue 状态就是不展开）——**刷新投稿页后立刻就能打开**。请让用户刷新页面后重试。"
            )

    if not dom.click_text(page, tuple(wanted), pool=DECLARATION_OPTION_POOL):
        options = _declaration_options(page)
        available = "、".join(options) or "（没抓到选项）"
        raise PublishError(f"未找到创作声明选项「{declaration}」。当前可选：{available}")

    if not _poll_until(lambda: bool(_read_declaration(page)), timeout=3.0):
        raise PublishError(
            f"点了创作声明选项「{declaration}」，但控件值仍为空，已中止投稿（回读校验失败）。"
        )

    actual = _read_declaration(page)
    logger.info("已设置创作声明: %s", actual)

    # 选完不一定自动收起。留着它可能盖住底部的「存草稿 / 立即投稿」，
    # 那时点击会被浮层守卫拦下（错误信息会指向"被遮挡"，而不是真正的原因）。
    if _declaration_panel_open(page):
        page.press_key("Escape")
        sleep_random(200, 400)
        if _declaration_panel_open(page):
            logger.warning("创作声明面板没有收起，可能挡住底部按钮；必要时手动按 Esc 关闭")

    return actual


# ─── 分区 ────────────────────────────────────────────────────────────────────


def _read_category_text(page: BridgePage) -> str:
    selector = page.first_present_selector(CATEGORY_TRIGGER_SELECTORS)
    if not selector:
        return ""
    return (page.get_element_text(selector) or "").strip()


def _category_chosen(page: BridgePage) -> bool:
    """分区控件上还挂着占位文案，就说明没选过。"""
    text = _read_category_text(page)
    if not text:
        return False
    return not any(marker in text for marker in CATEGORY_PLACEHOLDER_TEXTS)


def _open_category_panel(page: BridgePage) -> bool:
    """展开分区选择面板，返回面板是否**真的**开了。

    为什么不靠"请选择分区"这类占位文案定位：B站会按视频内容**预选**一个分区
    （实测这条视频被自动选中了「人工智能」），此时触发器上根本没有占位文案，
    按文本找必然落空。直接点控件本身更可靠。

    为什么必须校验面板真的开了：触发器点不中时页面毫无反应，接着去找一级分区就会
    报"找不到一级分区"—— 把"触发器没点到"误报成"分区名不对"，排查方向直接跑偏。
    """
    # 已经开着就直接复用。触发器是**开关**语义：再点一次会把面板关掉，于是后面的
    # 条目查找发生在一个正在消失的面板上，`isVisible` 因为 opacity 掉到 0 而失败，
    # 最后报出一个"找不到一级分区"的假错误（实测踩到）。
    if _category_panel_open(page):
        return True

    selector = page.first_present_selector(CATEGORY_TRIGGER_SELECTORS)
    if not selector:
        return False

    page.click_element(selector)
    sleep_random(400, 700)
    return _poll_until(lambda: _category_panel_open(page), timeout=5.0)


def _category_panel_open(page: BridgePage) -> bool:
    return page.first_present_selector(CATEGORY_PANEL_SELECTORS) is not None


def _select_category(page: BridgePage, primary: str, secondary: str) -> None:
    """选择一级 / 二级分区。

    任一步失败都**中止投稿**，绝不带着"分区没选上"的状态去点投稿 ——
    B站会在提交时用一句笼统的错误拒绝，用户根本不知道是哪一步没生效。
    """
    if not _open_category_panel(page):
        raise PublishError(
            "未能展开分区选择面板，已中止投稿。请运行 `python scripts/cli.py probe` "
            "确认分区控件与面板条目的结构，并更新 selectors.py 的 "
            "CATEGORY_TRIGGER_SELECTORS / CATEGORY_PANEL_SELECTORS。"
        )

    if not dom.click_text(page, (primary,), pool=CATEGORY_ITEM_POOL):
        raise PublishError(
            f"未在分区面板中找到一级分区「{primary}」，已中止投稿。"
            "请确认分区名与 B站当前分区树一致（用 probe 抓取面板条目核对；"
            "B站现在的一级分区是 影视 / 娱乐 / 音乐 / … / 知识 / 人工智能 / 科技数码 / …，"
            "注意已经没有叫「科技」的一级分区了）。"
        )
    sleep_random(500, 900)

    # 二级面板不是独立弹层，而是同一位置换成该一级分区下的二级列表，所以
    # 复用同一个候选池再点一次即可。
    if secondary and not dom.click_text(page, (secondary,), pool=CATEGORY_ITEM_POOL):
        raise PublishError(
            f"未在二级分区列表中找到「{secondary}」，已中止投稿。"
            "请确认分区名与 B站当前分区树一致（用 probe 抓取面板条目核对）。"
        )
    sleep_random(500, 900)

    if not _poll_until(lambda: _category_chosen(page), timeout=5.0):
        raise PublishError(
            f"分区「{primary} / {secondary}」点选后回读校验失败，已中止投稿。"
            "这说明点到的可能不是条目本身（例如点中了外层容器）。"
        )


# ─── 标签 ────────────────────────────────────────────────────────────────────


def _input_tags(page: BridgePage, tags: list[str]) -> list[str]:
    """逐个输入标签并按回车创建，返回真正创建成功的标签。

    为什么要回读校验：B站的标签靠 keydown 里的 Enter 创建，按键没送到就只是把
    文字留在输入框里。曾经（抖音那边）出现过"看起来点到了、实际上是空操作"，
    所以这里逐个确认，并把结果如实报告 —— 少了哪几个标签，用户一眼能看到。
    """
    if len(tags) > TAG_MAX_COUNT:
        raise TooManyTagsError(len(tags), TAG_MAX_COUNT)
    for tag in tags:
        if len(tag) > TAG_MAX_LEN:
            raise TagTooLongError(tag, TAG_MAX_LEN)

    selector = page.first_present_selector(TAG_INPUTS)
    if not selector:
        raise PublishError(
            "未找到标签输入框。请运行 `python scripts/cli.py probe` 后更新 "
            "scripts/bilibili/selectors.py 的 TAG_INPUTS。"
        )

    created: list[str] = []
    for raw in tags:
        tag = raw.lstrip("#").strip()
        if not tag:
            continue

        if _create_one_tag(page, selector, tag):
            created.append(tag)
        else:
            logger.warning("标签创建失败（已重试）: %s", tag)

        sleep_random(300, 600)

    return created


def _create_one_tag(page: BridgePage, selector: str, tag: str, *, attempts: int = 2) -> bool:
    """输入一个标签并按回车创建，失败时清空输入框重试一次。

    为什么要重试：B站的标签输入框在每次创建后会重渲染，紧接着的输入有几率丢字 ——
    实测 5 个标签里有 1 个首次失败、重试立刻成功。

    为什么重试不会造成重复标签：每次尝试后都会**回读校验**（``_tag_exists`` 只查
    已选标签区）。第一次要是其实成功了，校验会直接返回 True，不会再输第二遍。
    """
    for attempt in range(1, attempts + 1):
        page.click_element(selector)
        sleep_random(150, 300)
        page.type_text(tag, delay_ms=60)
        sleep_random(250, 450)
        page.press_key("Enter")

        if _poll_until(lambda t=tag: _tag_exists(page, t), timeout=3.0):
            logger.info("已创建标签: %s%s", tag, "" if attempt == 1 else "（第 2 次尝试）")
            return True

        # 失败时输入框里往往还留着半截文字，不清掉会污染下一次尝试
        logger.warning("标签第 %d 次尝试未成功: %s", attempt, tag)
        _clear_tag_input(page, selector)
        sleep_random(400, 700)

    return False


def _tag_exists(page: BridgePage, tag: str) -> bool:
    """「已选标签」区域里是否存在这个标签。

    两个限定缺一不可，都是踩出来的：

    1. **限定区域**（``TAG_SELECTED_REGION_SELECTORS``）—— 只在已选标签区里找。
       B站的标签区旁边就挂着「推荐标签」列表，全页搜文本会把推荐里的同名条目
       当成"已经加上了"。
    2. **等值比较**（去掉删除按钮的 × 之后）—— 标签文字也常出现在标题/简介里，
       包含匹配同样会误判。

    找不到已选区域时退回 body：真要改版到那种程度，宁可多报几次失败让人去 probe，
    也不要静默地"什么都算成功"。
    """
    region = page.first_present_selector(TAG_SELECTED_REGION_SELECTORS) or "body"
    return bool(
        page.evaluate(
            f"""
            (() => {{
              const want = {json.dumps(tag, ensure_ascii=False)};
              const root = document.querySelector({json.dumps(region)});
              if (!root) return false;
              const sels = {json.dumps(list(TAG_ITEM_SELECTORS))};
              // 标签项里带着删除按钮的 ×，比较前先去掉
              const strip = (s) => s.replace(/[\\u00d7\\u2715\\u2716\\u2573]/g, '');
              for (const sel of sels) {{
                for (const el of root.querySelectorAll(sel)) {{
                  const t = strip(el.textContent || '').trim();
                  if (t === want) return true;
                }}
              }}
              return false;
            }})()
            """
        )
    )


def _selected_tags(page: BridgePage) -> list[str]:
    """读出当前**已选**标签的名字列表。

    要把它如实报给用户：B站 会按视频内容预选一批标签（实测一条 AI 讲解视频被预选了
    "吉他指弹 / 吉他 / 独奏"），脚本只往上面叠加的话，稿件最终带着什么标签和脚本
    请求的标签并不是一回事。不报出来，用户就只能自己在浏览器里发现。
    """
    region = page.first_present_selector(TAG_SELECTED_REGION_SELECTORS) or "body"
    return (
        page.evaluate(
            f"""
            (() => {{
              const root = document.querySelector({json.dumps(region)});
              if (!root) return [];
              const sels = {json.dumps(list(TAG_ITEM_SELECTORS))};
              const strip = (s) => s.replace(/[\\u00d7\\u2715\\u2716\\u2573]/g, '').trim();
              const seen = [];
              for (const sel of sels) {{
                for (const el of root.querySelectorAll(sel)) {{
                  const t = strip(el.textContent || '');
                  if (t && !seen.includes(t)) seen.push(t);
                }}
                if (seen.length) break;   // 第一组选择器命中就够了，避免不同组重复计数
              }}
              return seen;
            }})()
            """
        )
        or []
    )


def _first_tag_chip(page: BridgePage) -> tuple[str, str] | None:
    """返回第一个已选标签的 ``(文本, 删除按钮的标记选择器)``，没有则 None。"""
    region = page.first_present_selector(TAG_SELECTED_REGION_SELECTORS) or "body"
    result = page.evaluate(
        f"""
        (() => {{
          const root = document.querySelector({json.dumps(region)});
          if (!root) return null;
          // 清掉上一次定位留下的标记，避免点到旧元素
          const mark = '{dom.MARK_ATTR}';
          document.querySelectorAll('[' + mark + ']').forEach((el) => el.removeAttribute(mark));
          const chip = root.querySelector("[class*='label-item-v2-container']");
          if (!chip) return null;
          const nameEl = chip.querySelector("[class*='label-item-v2-content']") ||
                         chip.querySelector("[class*='label-item']");
          const text = nameEl ? (nameEl.textContent || '').trim() : '';
          const delSels = {json.dumps(list(TAG_DELETE_SELECTORS))};
          let del = null;
          for (const sel of delSels) {{
            del = chip.querySelector(sel);
            if (del) break;
          }}
          if (!del) del = chip.lastElementChild;
          if (!text || !del) return null;
          del.setAttribute(mark, '1');
          return {{ text: text, selector: '[' + mark + '="1"]' }};
        }})()
        """
    )
    if not result:
        return None
    return result["text"], result["selector"]


def _remove_all_tags(page: BridgePage) -> list[str]:
    """逐个点掉已选标签，返回被移除的标签名。

    为什么需要"删"这个能力：B站 会按视频内容**预选**标签，脚本只做叠加的话，
    稿件就会带着一堆与内容无关的标签，用户还得手动一个个删（真实反馈：
    "标签不对"）。所以提供 ``--replace-tags`` 让脚本先清空再写自己的。

    每一步都校验"标签真的少了一个"再继续，删不掉就中止 —— 否则会变成"以为清空了、
    实际还留着"，最后还是用户去发现。
    """
    removed: list[str] = []
    for _ in range(TAG_MAX_COUNT + 5):
        chip = _first_tag_chip(page)
        if not chip:
            break

        text, delete_selector = chip
        page.click_element(delete_selector)
        if not _poll_until(lambda t=text: not _tag_exists(page, t), timeout=2.5):
            raise PublishError(
                f"删除标签「{text}」没有生效，已中止投稿（避免带着不相关的标签继续）。"
                "请运行 `python scripts/cli.py probe` 确认标签条目的删除按钮结构，"
                "并更新 selectors.py 的 TAG_DELETE_SELECTORS。"
            )

        removed.append(text)
        logger.info("已移除标签: %s（剩余 %d 个）", text, len(_selected_tags(page)))
        sleep_random(200, 400)

    return removed


def _clear_tag_input(page: BridgePage, selector: str) -> None:
    """清掉标签输入框里的残留文字。

    ⚠️ 必须先确认输入框里**真的有字**才动手。标签输入框在空的时候按 Backspace，
    B站 会把它当成"删除上一个标签" —— 实测因此把一个已经建好的标签静默删掉了，
    而报告里还写着"已创建"。这种错误比报错难查得多，所以宁可什么都不做。
    """
    if not page.get_input_value(selector).strip():
        return

    page.select_all_text(selector)
    sleep_random(80, 160)
    page.press_key("Backspace")
    sleep_random(120, 240)


# ─── 简介 ────────────────────────────────────────────────────────────────────


def _fill_description(page: BridgePage, description: str) -> str:
    """写入稿件简介，返回实际使用的编辑器类型。

    和抖音不同，B站的简介编辑器是**标准**富文本（新版 Quill，旧版自研），
    程序化写入 + 换行都是它本来就支持的路径。但"支持"不等于"一定成功"，
    所以写完后一律**回读校验**：内容被吞、被重复追加、被压平都能立刻发现。
    """
    if len(description) > DESCRIPTION_MAX_LEN:
        raise DescriptionTooLongError(str(len(description)), str(DESCRIPTION_MAX_LEN))

    textarea = page.first_present_selector(DESCRIPTION_TEXTAREAS)
    if textarea:
        page.input_text(textarea, description)
        sleep_random(200, 400)
        _verify_description(page.get_input_value(textarea), description)
        return "textarea"

    editor = page.first_present_selector(DESCRIPTION_EDITORS)
    if not editor:
        raise PublishError(
            "未找到稿件简介编辑器。请运行 `python scripts/cli.py probe` 后更新 "
            "scripts/bilibili/selectors.py 的 DESCRIPTION_EDITORS / DESCRIPTION_TEXTAREAS。"
        )

    page.input_content_editable(editor, description)
    sleep_random(400, 700)
    _verify_description(_read_editor_text(page, editor), description)
    return "rich"


def _read_editor_text(page: BridgePage, editor_selector: str) -> str:
    return (
        page.evaluate(
            f"""
            (() => {{
              const el = document.querySelector({json.dumps(editor_selector)});
              return el ? (el.innerText || el.textContent || '') : '';
            }})()
            """
        )
        or ""
    )


def _normalize(text: str) -> str:
    """去掉空白与零宽字符后再比较 —— 富文本编辑器会自由增删这些。"""
    return "".join(ch for ch in text if not ch.isspace() and ch not in _IGNORED_CHARS)


def _verify_description(actual: str, expected: str) -> None:
    """回读校验：写进去的简介必须与期望完全一致（忽略空白差异）。

    这里刻意**不**退化成"前缀相同就算过"。历史上真正的故障是"新旧内容叠加"——
    前缀一定相同，但后面多出一截旧内容。前缀校验会把它放过去，等值校验不会。
    短文本（少于 _VERIFY_MIN_CHARS）跳过校验：太短的字符串容易和编辑器自带的
    占位内容撞上，误报比漏报更烦人。
    """
    want = _normalize(expected)
    got = _normalize(actual)
    if want == got:
        return

    if len(want) < _VERIFY_MIN_CHARS:
        logger.warning("简介较短（%d 字），跳过回读校验（实际写入 %r）", len(want), got[:40])
        return

    raise PublishError(
        f"简介写入后回读不一致：期望 {len(want)} 字，实际 {len(got)} 字"
        f"（实际内容开头：{got[:40]!r}）。"
        "常见原因是编辑器不支持程序化清空/换行，导致内容被叠加或压平。"
        "请刷新投稿页重新上传（不要用 --skip-upload）后再试；"
        "若仍然如此，用 probe 确认编辑器类型并在 CLAUDE.md 记录该编辑器的行为。"
    )


# ─── 封面（可选） ────────────────────────────────────────────────────────────


def _upload_cover(page: BridgePage, cover_path: str) -> bool:
    """上传自定义封面。**失败只告警不中断**。

    封面是必填的（见 selectors.COVER_MISSING_TEXTS 的说明），但这里失败仍然只告警：
    失败之后要么用户自己点一张系统推荐封面，要么 B站 自动补一张，两种都能继续。
    真正的"没有封面"状态由 ``assert_publish_ready`` 在提交前统一拦下 —— 那是唯一
    能确定"到底有没有封面"的地方。
    但失败时**必须**尝试关掉弹窗：封面选择是模态框，留着它会让后面的「立即投稿」
    点不动，而报错会指向"按钮找不到"，排查方向完全错。
    """
    if not os.path.exists(cover_path):
        raise PublishError(f"封面文件不存在: {cover_path}")

    if not dom.click_text(page, COVER_ENTRY_TEXTS):
        logger.warning("未找到封面入口，改用 B站自动抽帧封面")
        return False
    sleep_random(600, 1000)

    selector = page.first_present_selector(COVER_INPUTS)
    if not selector:
        logger.warning("未找到封面上传输入框，改用自动封面")
        _close_cover_dialog(page)
        return False

    page.set_file_input(selector, [cover_path])

    if not _poll_until(lambda: dom.text_exists(page, COVER_CONFIRM_TEXTS), timeout=30.0):
        logger.warning("封面上传后未出现确认按钮，改用自动封面")
        _close_cover_dialog(page)
        return False

    if not dom.click_text(page, COVER_CONFIRM_TEXTS, pool=COVER_DIALOG_POOL):
        logger.warning("封面对话框确认失败，改用自动封面")
        _close_cover_dialog(page)
        return False

    sleep_random(400, 800)
    logger.info("已上传自定义封面: %s", os.path.basename(cover_path))
    return True


def _close_cover_dialog(page: BridgePage) -> None:
    """按 Esc 关闭封面弹窗，避免它挡住投稿按钮。"""
    page.press_key("Escape")
    sleep_random(300, 600)


# ─── 定时发布 ────────────────────────────────────────────────────────────────


def _set_schedule(page: BridgePage, schedule_time: str) -> None:
    """设置定时发布。设置失败时**必须**报错，避免误按立即投稿。"""
    try:
        dt = datetime.fromisoformat(schedule_time)
    except ValueError as e:
        raise PublishError(f"定时发布时间格式错误（需 ISO8601，如 2026-03-10T12:00）: {e}") from e

    if not dom.click_text(page, SCHEDULE_RADIO_TEXTS):
        raise PublishError("未找到「定时发布」选项，已中止投稿。请手动确认发布时间设置。")

    sleep_random(500, 900)

    selector = page.first_present_selector(SCHEDULE_DATETIME_INPUTS)
    if not selector:
        raise PublishError("未找到定时发布的时间输入框，已中止投稿。")

    value = dt.strftime("%Y-%m-%d %H:%M")
    page.click_element(selector)
    sleep_random(150, 300)
    page.select_all_text(selector)
    page.input_text(selector, value)
    page.press_key("Enter")
    sleep_random(400, 800)

    actual = page.get_input_value(selector)
    if value.split(":")[0] not in actual:
        raise PublishError(
            f"定时发布时间未生效（期望 {value}，控件当前值 {actual!r}），已中止投稿。"
        )
    logger.info("已设置定时发布: %s", value)


# ─── 按钮定位与投稿结果 ──────────────────────────────────────────────────────


def _find_button(
    page: BridgePage,
    texts: tuple[str, ...],
    *,
    enabled_only: bool,
    timeout: float = 0.0,
) -> str | None:
    """在按钮类元素里按**精确**文本找可点击按钮，返回标记后的选择器。

    只认精确文本：B站的投稿页上有「立即投稿」也有「存草稿」，还有若干说明文案。
    用"包含"匹配会让「立即投稿」命中包住它的整块容器，点下去什么也不会发生。
    """
    enabled_clause = "if (isDisabled(el)) return false;" if enabled_only else ""
    finder = (
        "const texts = " + json.dumps(list(texts), ensure_ascii=False) + ";"
        "const pool = Array.from(document.querySelectorAll(" + json.dumps(BUTTON_POOL) + "));"
        # 禁用状态要看自己和外层容器：B站的「立即投稿」是 span.submit-add，
        # disabled 类可能挂在这个 span 上，也可能挂在 div.submit-container 上。
        # pointer-events:none 是另一种常见写法，一并算进去。
        "const isDisabled = (el) => {"
        " const cands = [el, el.parentElement,"
        " el.closest(\"[class*='submit-container']\")].filter(Boolean);"
        " for (const b of cands) {"
        "  if (b.disabled) return true;"
        "  if (String(b.className || '').includes('disabled')) return true;"
        "  if (b.getAttribute('aria-disabled') === 'true') return true;"
        "  if (window.getComputedStyle(b).pointerEvents === 'none') return true;"
        " }"
        " return false; };"
        "const isVisible = (el) => { const r = el.getBoundingClientRect();"
        " return r.width > 0 && r.height > 0; };"
        "return pool.find((el) => {"
        " const t = (el.textContent || '').trim();"
        " if (!texts.includes(t)) return false;"
        " if (!isVisible(el)) return false;"
        " " + enabled_clause + " return true; }) || null;"
    )

    if timeout <= 0:
        return dom.find_by_js(page, finder)

    # 带超时的版本：按钮出现得比较慢（等视频就绪）时用，避免调用方自己写循环
    deadline = time.monotonic() + timeout
    while True:
        selector = dom.find_by_js(page, finder)
        if selector:
            return selector
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _visible_dialog_text(page: BridgePage) -> str:
    """返回页面上可见弹窗的文本（没有则空串）。

    尺寸阈值是为了排除 DOM 里常驻的隐藏弹窗骨架 —— BCC 的弹窗组件通常一直挂在
    DOM 上，只是 display:none，不加过滤会永远报"有弹窗"。
    """
    return (
        page.evaluate(
            """
            (() => {
              const sels = ["[class*='bcc-dialog']", "[role='dialog']", "[class*='modal']"];
              for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                  const r = el.getBoundingClientRect();
                  if (r.width < 260 || r.height < 160) continue;
                  const st = window.getComputedStyle(el);
                  if (st.display === 'none' || st.visibility === 'hidden') continue;
                  if (Number(st.opacity) < 0.1) continue;
                  const t = (el.innerText || '').trim();
                  if (t) return t.slice(0, 200);
                }
              }
              return '';
            })()
            """
        )
        or ""
    )


def _confirm_publish_dialog_if_any(page: BridgePage) -> bool:
    """投稿前若弹出二次确认框，点掉它。

    只用**专属**文案匹配（「确认投稿」「继续投稿」），绝不用通用的「确定」——
    页面上任何弹窗都有「确定」，靠它自动点有可能把还没填完的内容提交出去。
    没有匹配到就什么都不做（绝大多数版本没有这个确认框）。
    """
    selector = _find_button(page, PUBLISH_CONFIRM_TEXTS, enabled_only=True)
    if not selector:
        return False
    page.click_element(selector)
    logger.info("已点击投稿二次确认")
    time.sleep(1.5)
    return True


def _wait_for_publish_result(page: BridgePage, timeout: float, *, clicked_at: float) -> dict:
    """轮询投稿结果：成功 / 风控 / 失败 / 未知。

    这里**必须**周期性打日志，而且要带上"页面现在在说什么"。原因有两个，都踩过：

    1. 等结果最长 120 秒，全程静默会让用户以为卡死（真实反馈："卡住了"）。
    2. 更关键：B站 校验不通过时只弹一条 toast（比如"请选择创作声明"），几秒后自己
       消失。如果只认风控/失败关键词，这条提示就完全看不见 —— 于是既没成功、也没
       报错，脚本干等到超时，用户完全不知道发生了什么。
    """
    start = time.monotonic()
    deadline = start + timeout
    next_progress = start + PROGRESS_LOG_SECONDS
    next_archive_check = start
    submitting = False

    while time.monotonic() < deadline:
        url = page.get_url()
        text = page_text(page, limit=8000)

        # 「提交中...」是正在提交的正常中间态。认出来有两个好处：进度日志能说清
        # 现在的状态；超时时能给出一句有用的结论（而不是"未捕获到明确结果"）。
        if any(kw in text for kw in SUBMITTING_TEXTS):
            submitting = True

        # 查稿件列表 —— 这是唯一权威的判据。页面既不跳转也不弹提示时，只有它能
        # 告诉我们"其实已经投出去了"（实测就是这样确认成功的）。
        if time.monotonic() >= next_archive_check:
            archive = _find_new_archive(page, clicked_at)
            if archive:
                return {
                    "success": True,
                    "status": f"投稿成功（{archive['state']}），稿件已出现在稿件列表里",
                    "bvid": archive["bvid"],
                    "archive_title": archive["title"],
                    "url": MANAGE_URL,
                }
            next_archive_check = time.monotonic() + ARCHIVE_POLL_SECONDS

        if "upload-manager" in url or MANAGE_URL in url:
            return {"success": True, "status": "投稿成功，已跳转到稿件管理页", "url": url}

        success = next((kw for kw in PUBLISH_SUCCESS_KEYWORDS if kw in text), None)
        if success:
            return {"success": True, "status": success, "url": url}

        risk = next((kw for kw in RISK_KEYWORDS if kw in text), None)
        if risk:
            raise AccountRiskControlError("risk_control", risk)

        failure = next((kw for kw in PUBLISH_FAILURE_KEYWORDS if kw in text), None)
        if failure:
            raise PublishError(f"投稿失败：{failure}")

        # 平台校验提示一律当成失败处理，并且**把原文带出去**。
        #
        # 为什么不能只认关键词：点完立刻出现的校验提示（实测「请先上传封面」）几秒后
        # 就自己消失，而它是"点了没反应"的唯一线索。过去只匹配风控/失败关键词，这条
        # 提示被完全忽略，脚本一路静默等到超时，用户只看到"卡住了"。
        #
        # 点完投稿后 B站 会弹提示只有两种可能：出问题，或者成功。成功文案上面已经
        # 匹配过了，所以走到这里都当失败。
        toast = _visible_toast_text(page)
        if toast:
            raise PublishError(f"B站 提示：{toast}（投稿未提交成功，已中止等待）")

        if time.monotonic() >= next_progress:
            toast = _visible_toast_text(page)
            dialog = _visible_dialog_text(page)
            logger.info(
                "等待投稿结果（已 %.0fs / 上限 %.0fs）：%s%s%s",
                time.monotonic() - start,
                timeout,
                "按钮显示「提交中...」（B站 提交很慢，可能要好几分钟，切勿重复点击）"
                if submitting
                else "页面上没有提交中的迹象",
                f"，页面提示「{toast}」" if toast else "，也没有提示条",
                f"，弹窗「{dialog[:60]}」" if dialog else "",
            )
            next_progress = time.monotonic() + PROGRESS_LOG_SECONDS

        time.sleep(1.5)

    if submitting:
        raise PublishError(
            f"已点击「立即投稿」，按钮一直停在「提交中...」，{timeout:.0f}s 内稿件列表里"
            "还没出现新稿件。**不要重复点击**。B站 的投稿可能要几分钟到十几分钟才落地"
            f"（实测有过 8.5 分钟才出现的），请过几分钟再打开 {MANAGE_URL} 看一眼；"
            "确认确实没有稿件后，再刷新投稿页重试。"
        )

    raise PublishError(
        f"已点击「立即投稿」，但 {timeout:.0f}s 内未捕获到明确结果，而且按钮文案没有变成"
        "「提交中...」—— 说明这次点击可能没被 B站 受理。"
        f"请先到 {MANAGE_URL} 确认是否已有稿件（避免重复投稿），"
        "再考虑刷新投稿页重试；必要时运行 `python scripts/cli.py page-info` 复查当前页面。"
    )


def _recent_archives(page: BridgePage, limit: int = 8) -> list[dict]:
    """读取稿件列表（最新在前）。在页面里同源 fetch，不需要额外登录态。"""
    return (
        page.evaluate(
            f"""
            (async () => {{
              const r = await fetch(
                "{ARCHIVE_LIST_PATH}?pn=1&ps={limit}&status=is_pubing,pubed,not_pubed",
                {{ credentials: "include" }}
              );
              const j = await r.json();
              const list = (j.data && (j.data.arc_audits || j.data.archives)) || [];
              return list.map((x) => {{
                const a = x.Archive || x;
                return {{ title: a.title || "", bvid: a.bvid || "",
                          state: a.state_desc || a.state || "", ctime: a.ctime || 0 }};
              }});
            }})()
            """
        )
        or []
    )


def _find_new_archive(page: BridgePage, since_epoch: float) -> dict | None:
    """找出本次投稿产生的稿件，没有则 None。

    按**创建时间**判断，不按标题 —— 用户完全可能在页面上改过标题（实测就改过，
    加了 "Jev"），拿请求时的标题去比会白白漏判。
    """
    for item in _recent_archives(page):
        if float(item.get("ctime") or 0) >= since_epoch - ARCHIVE_TIME_TOLERANCE_SECONDS:
            return item
    return None


def _visible_toast_text(page: BridgePage) -> str:
    """页面上当前可见的 toast / message 提示条文本（没有则空串）。

    专门用来抓 B站 的校验提示：它出现几秒就自己消失，轮询页面全文很容易错过，
    而这些提示恰恰是"点了没反应"的唯一线索。
    """
    return (
        page.evaluate(
            """
            (() => {
              const sels = ["[class*='bcc-message']", "[class*='toast']",
                            "[class*='message']", "[class*='notice']"];
              for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                  const r = el.getBoundingClientRect();
                  if (r.width <= 0 || r.height <= 0) continue;
                  const st = window.getComputedStyle(el);
                  if (st.display === 'none' || st.visibility === 'hidden') continue;
                  if (Number(st.opacity) < 0.1) continue;
                  const t = (el.innerText || '').trim();
                  // 侧边导航里的未读消息条也带 message 类，长度过滤不掉但有 99+ 这种文本
                  if (t && t.length <= 120 && !/^\\d+\\+?$/.test(t)) return t;
                }
              }
              return '';
            })()
            """
        )
        or ""
    )


# ─── 小工具 ──────────────────────────────────────────────────────────────────


def _poll_until(check: Callable[[], bool], timeout: float, interval: float = 0.3) -> bool:
    """轮询直到条件成立或超时。

    页面上的状态变更（标签项渲染、分区回填、弹窗出现）都是异步的，写完立刻查
    会读到旧状态。这类"查一次就下结论"是自动化脚本最常见的误报来源。
    """
    deadline = time.monotonic() + timeout
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
