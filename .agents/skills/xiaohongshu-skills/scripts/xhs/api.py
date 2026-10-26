"""小红书纯 HTTP 客户端：只读本机浏览器 Cookie，不启动浏览器。

为什么需要这一层
----------------
原来的 CLI 是「扩展 Bridge + 真实浏览器」模式：启动 Chrome、装扩展、由页面 JS 发起
请求。它能拿到 xs 签名，但代价是必须开着浏览器。本模块走另一条路：**只读本机浏览器
里已登录的 Cookie**，然后用 urllib 直接请求。不开浏览器就能跑；代价是拿不到 xs/xt
签名，而小红书在部分接口上会校验它。

实测结论（2026-10 维护本模块时逐条验证）
----------------------------------------
**不需要签名、纯 Cookie 即可读**：

* ``creator.xiaohongshu.com/api/galaxy/user/info``                 登录态与账号信息
* ``creator.xiaohongshu.com/api/galaxy/creator/home/personal_info`` 昵称/粉丝/关注/获赞
* ``creator.xiaohongshu.com/api/galaxy/creator/home/latest_note_data`` 最近一篇笔记
* ``creator.xiaohongshu.com/api/galaxy/creator/data/fans/overall_new`` 粉丝趋势
* ``www.xiaohongshu.com/explore``                                  首页流（页面内嵌
  ``__INITIAL_STATE__``，SSR 数据，不用调 feed 接口）
* ``www.xiaohongshu.com/user/profile/<uid>``                       用户主页基本信息 + 首屏笔记
* ``www.xiaohongshu.com/explore/<nid>?xsec_token=...``             笔记详情（含互动数，
  不含评论）

**必须 xs/xt 签名**（本模块明确抛 ``SignatureRequiredError``，绝不返回空列表假装成功）：

* ``/api/sns/web/v1/search/notes``、``/api/sns/web/v1/homefeed``（POST）
* ``/api/sns/web/v2/comment/page``
* ``creator.xiaohongshu.com/api/galaxy/v2/creator/note/user/posted``
* ``creator.xiaohongshu.com/api/galaxy/creator/datacenter/note/base``

为什么要诚实报错而不是返回空
----------------------------
平台接口在「签名不对」时的返回**不一定是 HTTP 4xx**。实测里 ``creator.../note/user/posted``
在 Cookie 有效的情况下返回 ``{"result":0,"success":true,"data":{"notes":[],"page":-1}}`` ——
HTTP 200、业务码 0、但数据是死的。如果把它当成「这个账号没有笔记」，上层就会拿到一个
假的空列表并且无从察觉。这类静默错误比直接报错危害大得多，所以本模块宁可抛异常。

为什么不做本地签名
------------------
xs 由页面里的 ``window._webmsxyw(...)`` 现算，输入是 (path, 请求体, a1 Cookie, 时间戳)
等，算法在每个前端 bundle 里被混淆，且随平台升级换代。把某一版算法抄进 Python 只能短期
可用：平台一升级就失效，而且失效时最可能的表现是**接口返回空数据或风控**。实测未签名
POST 搜索接口直接返回 ``code=300011「当前账号存在异常」``。所以这里选择明确报错 ——
需要签名的场景请加 ``--browser`` 走浏览器模式。

安全
----
本模块**不打印、不落盘任何 Cookie 值**。诊断信息只报 Cookie 数量与关键项是否存在。
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── 定位共享底座 .agents/lib/browserless ───────────────────────────────────


def _ensure_lib_path() -> None:
    """把仓库的 ``.agents/lib`` 加进 ``sys.path``，供 ``browserless`` 导入。

    不写死绝对路径：从本文件往上逐级找含 ``lib/browserless/http.py`` 的目录，
    并允许 ``BROWSERLESS_LIB`` 环境变量覆盖。
    """
    import os

    candidates: list[Path] = []
    override = os.environ.get("BROWSERLESS_LIB")
    if override:
        candidates.append(Path(override))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "lib")
    for candidate in candidates:
        if (candidate / "browserless" / "http.py").is_file():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    raise XhsApiError(
        "找不到 browserless 底座（期望 .agents/lib/browserless/http.py），"
        "请检查仓库结构或设置 BROWSERLESS_LIB 环境变量"
    )


_ensure_lib_path()

from browserless.chromium import CookieError
from browserless.http import ApiError, AuthRequired, Session

from .types import Feed, UserInteraction

# ─── 常量 ────────────────────────────────────────────────────────────────────

CREATOR_BASE = "https://creator.xiaohongshu.com"
WWW_BASE = "https://www.xiaohongshu.com"

# Cookie 归属域（含子域）
COOKIE_DOMAINS = ["xiaohongshu.com"]

# 首页流 SSR 里 noteDetailMap 需要 xsec_token，缺了拿不到详情
_NEED_TOKEN_MSG = (
    "该笔记缺少 xsec_token（页面授权令牌），无法读取详情，"
    "请从 list-feeds 输出里取 xsecToken"
)

_SIGN_MESSAGE = "该接口需要 xs 签名，暂不支持无浏览器调用，请加 --browser"


# ─── 异常 ────────────────────────────────────────────────────────────────────


class XhsApiError(ApiError):
    """本模块的通用错误（服务端明确拒绝或数据不可解析）。"""


class SignatureRequiredError(XhsApiError):
    """接口需要 xs/xt 签名，无浏览器模式下做不到。

    继承自底座 ``ApiError``：调用方只要 ``except ApiError`` 就能统一处理。
    """

    def __init__(self, what: str = "", message: str = _SIGN_MESSAGE) -> None:
        self.what = what
        full = f"{what}：{message}" if what else message
        super().__init__(full)


class EndpointRetiredError(XhsApiError):
    """接口还在（HTTP 200）但返回的是死数据，例如 ``page: -1`` + 空列表。"""


# ─── SSR 页面解析 ────────────────────────────────────────────────────────────


def _extract_js_object(text: str, marker: str) -> str:
    """从 HTML 里抠出 ``marker`` 之后那个完整的 JSON 对象字面量。

    不能只靠正则 ``\\{.*?\\}``：``__INITIAL_STATE__`` 里嵌套很深，非贪婪匹配会提前截断。
    这里做括号配对扫描，并跳过字符串内部的花括号。
    """
    index = text.find(marker)
    if index < 0:
        return ""
    start = text.find("{", index)
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    return ""


def _sanitize_js_literal(raw: str) -> str:
    """把 JS 字面量修成合法 JSON。

    页面里的 ``__INITIAL_STATE__`` 并不是严格 JSON，实测出现过三种情况：

    * ``undefined`` —— 出现频率最高，等价的 JSON 是 ``null``
    * ``NaN`` —— 同理
    * ``new Map([...])`` / ``new Set([...])`` —— 少数 store 用了原生容器

    其余部分都是标准 JSON 字面量，所以只处理这三种。
    """
    out: list[str] = []
    index = 0
    length = len(raw)
    in_string = False
    escaped = False
    while index < length:
        char = raw[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if raw.startswith("undefined", index):
            out.append("null")
            index += len("undefined")
            continue
        if raw.startswith("NaN", index):
            out.append("null")
            index += len("NaN")
            continue
        if raw.startswith("new Map(", index) or raw.startswith("new Set(", index):
            is_map = raw.startswith("new Map(")
            depth = 1
            scan = index + len("new Map(")
            while scan < length and depth > 0:
                if raw[scan] == "(":
                    depth += 1
                elif raw[scan] == ")":
                    depth -= 1
                scan += 1
            out.append("{}" if is_map else "[]")
            index = scan
            continue
        out.append(char)
        index += 1
    return "".join(out)


def parse_initial_state(html: str) -> dict:
    """解析页面里的 ``window.__INITIAL_STATE__``，失败返回空 dict。"""
    raw = _extract_js_object(html, "window.__INITIAL_STATE__")
    if not raw:
        return {}
    try:
        state = json.loads(_sanitize_js_literal(raw))
    except ValueError as exc:  # 页面结构变化时报错比静默返回空更好
        logger.debug("__INITIAL_STATE__ 解析失败: %s", exc)
        return {}
    return state if isinstance(state, dict) else {}


def _unwrap(value: Any) -> Any:
    """兼容 Vue ref 包装：``{value: ...}`` / ``{_value: ...}``。"""
    if isinstance(value, dict):
        if "value" in value:
            return value["value"]
        if "_value" in value:
            return value["_value"]
    return value


def _flatten_notes(notes: Any) -> list[dict]:
    """把主页 ``notes`` 摊平成 ``[{id, xsecToken, noteCard}, ...]``。

    这个字段的形状不稳定：实测同一份代码在同一台机器上，自己主页返回的是
    ``[[{...}, {...}], [{...}]]``（外层按行分组），别人主页返回的是
    ``[{...}, {...}]``。两种都要能吃下去。
    """
    items: list[Any] = list(notes) if isinstance(notes, list) else []
    flat: list[dict] = []
    while items:
        item = items.pop(0)
        if isinstance(item, list):
            items[0:0] = item
        elif isinstance(item, dict):
            flat.append(item)
    return flat


# ─── 客户端 ──────────────────────────────────────────────────────────────────


class XhsApi:
    """无浏览器的小红书只读客户端。

    典型用法::

        api = XhsApi()
        info = api.check_login()
        feeds = api.list_feeds()

    抛 ``browserless.chromium.CookieError`` 表示「技术上读不到浏览器 Cookie」，
    调用方应退回浏览器模式；抛 ``AuthRequired`` 表示「读到了但没登录」。
    """

    def __init__(self, browser: str = "chrome", *, min_interval: float = 0.35) -> None:
        self.browser = browser
        self.min_interval = min_interval
        self._jar: Any = None
        self._creator: Session | None = None
        self._www: Session | None = None

    # ── 会话 ────────────────────────────────────────────────────────

    def _cookie_jar(self) -> Any:
        """读一次浏览器 Cookie，两个会话共用，避免重复读盘。"""
        if self._jar is None:
            from browserless import chromium

            self._jar = chromium.load(self.browser, domains=COOKIE_DOMAINS)
        return self._jar

    @property
    def creator(self) -> Session:
        """创作者中心会话（creator.xiaohongshu.com）。"""
        if self._creator is None:
            self._creator = Session(
                self._cookie_jar(),
                referer=f"{CREATOR_BASE}/",
                min_interval=self.min_interval,
            )
        return self._creator

    @property
    def www(self) -> Session:
        """主站会话（www.xiaohongshu.com）。"""
        if self._www is None:
            self._www = Session(
                self._cookie_jar(),
                referer=f"{WWW_BASE}/",
                min_interval=self.min_interval,
            )
        return self._www

    # ── 底层请求 ────────────────────────────────────────────────────

    def _galaxy(self, path: str) -> dict:
        """请求 creator 的 ``/api/galaxy`` 接口并校验业务码。

        这些接口失败时**常常仍是 HTTP 200**，只有 ``success: false`` / ``result != 0``，
        所以必须自己检查，不能只看 HTTP 状态。
        """
        url = f"{CREATOR_BASE}{path}"
        try:
            payload = self.creator.get_json(url)
        except ApiError as exc:
            if exc.status == 401:
                raise AuthRequired("未登录或登录已过期，请在浏览器里重新登录小红书") from exc
            raise
        if not isinstance(payload, dict):
            raise XhsApiError(f"接口返回格式异常：{url}")
        success = payload.get("success")
        result = payload.get("result")
        code = payload.get("code")
        failed = success is False or (result not in (0, None)) or (code not in (0, None))
        if failed:
            message = payload.get("msg") or payload.get("message") or "接口返回失败"
            raise XhsApiError(f"{message}（{path}）", code=result if result is not None else code)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _page_state(self, path: str) -> dict:
        """请求 www 的一个页面并解析其 ``__INITIAL_STATE__``。"""
        url = path if path.startswith("http") else f"{WWW_BASE}{path}"
        try:
            response = self.www.get(url)
        except ApiError as exc:
            if exc.status in (401, 403):
                raise AuthRequired("未登录或登录已过期，请在浏览器里重新登录小红书") from exc
            raise
        html = response.text
        state = parse_initial_state(html)
        if not state:
            raise XhsApiError(f"页面里没有找到 __INITIAL_STATE__，小红书前端可能改版了：{url}")
        return state

    # ── 登录 / 账号 ─────────────────────────────────────────────────

    def check_login(self) -> dict:
        """检查登录状态并返回账号摘要。

        返回::

            {"logged_in": True, "userId": "...", "userName": "...", "redId": "..."}

        未登录时返回 ``{"logged_in": False, ...}`` 而不是抛异常 —— 便于 CLI 用
        exit code 1 表达「未登录」。Cookie 过期时 creator 接口回 401，按未登录处理。
        """
        try:
            data = self._galaxy("/api/galaxy/user/info")
        except AuthRequired:
            return {"logged_in": False, "message": "未登录或登录已过期，请在浏览器里重新登录小红书"}

        user_id = data.get("userId") or data.get("user_id") or ""
        if not user_id:
            return {"logged_in": False, "message": "接口未返回 userId，按未登录处理"}

        result: dict = {
            "logged_in": True,
            "userId": user_id,
            "userName": data.get("userName") or data.get("nickname") or "",
            "redId": data.get("redId") or "",
            "mode": "api",
        }
        if data.get("userDesc"):
            result["desc"] = data["userDesc"]
        return result

    def user_info(self) -> dict:
        """账号信息：昵称、小红书号、粉丝/关注/获赞数。

        数据来自 creator 的 ``user/info`` 与 ``home/personal_info`` 两个接口，
        后者才有粉丝数等统计。``phone`` 等敏感字段一律不返回。
        """
        info = self._galaxy("/api/galaxy/user/info")
        result: dict = {
            "userId": info.get("userId", ""),
            "userName": info.get("userName", ""),
            "redId": info.get("redId", ""),
            "desc": info.get("userDesc", ""),
            "avatar": info.get("userAvatar", ""),
        }
        try:
            personal = self._galaxy("/api/galaxy/creator/home/personal_info")
        except ApiError as exc:
            logger.debug("personal_info 不可用: %s", exc)
            return result
        result.update(
            {
                "nickname": personal.get("name", ""),
                "fansCount": personal.get("fans_count"),
                "followCount": personal.get("follow_count"),
                "likedAndCollectedCount": personal.get("faved_count"),
                "level": (personal.get("grow_info") or {}).get("level"),
            }
        )
        return result

    # ── 首页流 ──────────────────────────────────────────────────────

    def list_feeds(self, limit: int = 0) -> dict:
        """首页推荐流。

        走 ``/explore`` 页面内嵌的 SSR 数据，与浏览器模式读的是同一个
        ``__INITIAL_STATE__.feed.feeds``，因此字段完全一致。

        未登录时 SSR 不下发 feed（实测匿名访问返回 0 条），此时抛 ``AuthRequired``
        —— **不返回空列表**，否则上层无法区分「没数据」和「没登录」。
        """
        state = self._page_state("/explore")
        user = state.get("user") or {}
        feeds_raw = _unwrap((state.get("feed") or {}).get("feeds")) or []
        if not feeds_raw:
            if user.get("loggedIn") is False:
                raise AuthRequired("未登录，首页推荐流需要登录态；请在浏览器里登录小红书")
            raise XhsApiError(
                "首页推荐流没有返回数据（可能触发了风控或前端改版），"
                "请稍后重试或加 --browser 走浏览器模式"
            )
        items = [Feed.from_dict(item) for item in feeds_raw]
        if limit and limit > 0:
            items = items[:limit]
        return {
            "feeds": [item.to_dict() for item in items],
            "count": len(items),
            "source": "www-ssr",
            "note": "首屏 SSR 数据，数量受页面首屏限制；继续下滑的增量需要 xs 签名",
        }

    # ── 用户主页 ────────────────────────────────────────────────────

    def user_profile(self, user_id: str = "", xsec_token: str = "") -> dict:
        """用户主页：基本信息 + 互动数 + 第一页笔记。

        ``user_id`` 留空表示查自己（用 ``check_login`` 得到的 userId）。

        笔记数量说明：SSR 里 ``user.notes`` 的外层长度是 5，但元素本身是**列表**
        （按行分组），摊平后实测一个账号拿到 14/14 篇、另一个 32/116 篇、
        第三个 30/395 篇。所以这里是「第一页」而不是「前 5 条」，但仍不是全部 ——
        继续翻页的接口需要 xs 签名，``hasMore`` 会如实标出来。
        """
        if not user_id:
            user_id = self.check_login().get("userId", "")
            if not user_id:
                raise AuthRequired("未登录，无法确定要查询的用户；请传 --user-id 或先在浏览器登录")
        path = f"/user/profile/{user_id}"
        if xsec_token:
            path += f"?xsec_token={xsec_token}&xsec_source=pc_note"
        state = self._page_state(path)
        user = state.get("user") or {}
        page_data = _unwrap(user.get("userPageData")) or {}
        basic = page_data.get("basicInfo") or {}
        interactions = page_data.get("interactions") or []
        notes_raw = _unwrap(user.get("notes")) or []

        feeds: list[dict] = []
        for item in _flatten_notes(notes_raw):
            # 元素一般是 {"id","xsecToken","noteCard",...}；少数情况下直接是 noteCard
            if "noteCard" not in item:
                item = {"id": item.get("noteId", ""), "noteCard": item}
            feeds.append(Feed.from_dict(item).to_dict())

        result: dict = {
            "userId": user_id,
            "basicInfo": {
                "nickname": basic.get("nickname", ""),
                "redId": basic.get("redId", ""),
                "desc": basic.get("desc", ""),
                "gender": basic.get("gender", 0),
                "ipLocation": basic.get("ipLocation", ""),
            },
            "interactions": [
                UserInteraction.from_dict(item).__dict__
                for item in interactions
                if isinstance(item, dict)
            ],
            "feeds": feeds,
            "count": len(feeds),
            "totalPosted": page_data.get("posted"),
            "source": "www-ssr",
        }
        total_posted = page_data.get("posted")
        # 是否还有下一页：以「拿到的条数 < 主页显示的累计篇数」为准。SSR 里的
        # noteQueries 有多个分组（笔记/收藏/…），单看 queries[0].hasMore 会误判。
        if isinstance(total_posted, int) and total_posted > len(feeds):
            result["hasMore"] = True
            result["truncated"] = True
            result["warning"] = (
                f"只取到第一页 {len(feeds)} 条笔记（主页显示共 {total_posted} 篇）；"
                "继续翻页的接口需要 xs 签名，请加 --browser"
            )
        return result

    def list_posted_notes(self, user_id: str = "") -> dict:
        """已发布笔记列表（第一页）。

        creator 的 ``note/user/posted`` 接口已失效（HTTP 200 但只回空列表 +
        ``page: -1``），所以改走 www 主页的 SSR 数据。结果是**不完整的**：
        ``complete`` 恒为 ``False``，并带 ``warning`` 说明只取了第一页。
        需要完整列表请加 ``--browser``。
        """
        profile = self.user_profile(user_id=user_id)
        notes = [
            {
                "noteId": feed.get("id", ""),
                "xsecToken": feed.get("xsecToken", ""),
                "title": feed.get("displayTitle", ""),
                "type": feed.get("type", ""),
                "interactInfo": feed.get("interactInfo", {}),
                "user": feed.get("user", {}),
            }
            for feed in profile.get("feeds", [])
        ]
        warning = profile.get("warning") or (
            f"只返回第一页 {len(notes)} 条笔记；"
            "creator 的 note/user/posted 接口已失效（只回空列表），"
            "完整列表请加 --browser"
        )
        total_posted = profile.get("totalPosted")
        # 拿到的条数够不够，以主页显示的累计篇数为准；不够就如实说不够
        complete = isinstance(total_posted, int) and len(notes) >= total_posted
        result: dict = {
            "notes": notes,
            "count": len(notes),
            "totalPosted": total_posted,
            "complete": complete,
            "source": "www-ssr",
        }
        if not complete:
            result["warning"] = warning
        return result

    # ── 笔记详情 / 数据 ─────────────────────────────────────────────

    def note_stats(self, note_id: str, xsec_token: str = "") -> dict:
        """单篇笔记的正文与互动数据（阅读/点赞/收藏/评论计数）。

        走 ``/explore/<id>?xsec_token=...`` 的 SSR 数据。``xsec_token`` 是页面授权
        令牌（不是 xs 签名），从 ``list-feeds`` 输出的 ``xsecToken`` 字段取。

        评论区**取不到**：SSR 的 ``comments.list`` 恒为空且 ``hasMore: true``，
        真正的评论接口需要 xs 签名。这里用 ``commentsAvailable: false`` 明确标注，
        绝不返回一个看起来正常的空评论数组。
        """
        if not xsec_token:
            raise XhsApiError(_NEED_TOKEN_MSG)
        path = f"/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_feed"
        state = self._page_state(path)
        detail_map = _unwrap((state.get("note") or {}).get("noteDetailMap")) or {}
        if not isinstance(detail_map, dict) or not detail_map:
            raise XhsApiError(
                "笔记详情没有返回数据：xsec_token 可能已过期，或该笔记不可访问；"
                "请重新用 list-feeds 取一个 xsecToken"
            )
        entry = detail_map.get(note_id) or next(iter(detail_map.values()))
        if not isinstance(entry, dict):
            raise XhsApiError("笔记详情数据结构异常")
        note = entry.get("note") or {}
        interact = note.get("interactInfo") or {}
        stats = {
            "noteId": note.get("noteId") or note_id,
            "title": note.get("title", ""),
            "desc": note.get("desc", ""),
            "type": note.get("type", ""),
            "time": note.get("time", 0),
            "ipLocation": note.get("ipLocation", ""),
            "user": {
                "userId": (note.get("user") or {}).get("userId", ""),
                "nickname": (note.get("user") or {}).get("nickname", "")
                or (note.get("user") or {}).get("nickName", ""),
            },
            "interactInfo": {
                "likedCount": interact.get("likedCount", ""),
                "collectedCount": interact.get("collectedCount", ""),
                "commentCount": interact.get("commentCount", ""),
                "sharedCount": interact.get("shareCount", ""),
            },
            "imageCount": len(note.get("imageList") or []),
            "tags": [
                tag.get("name", "")
                for tag in (note.get("tagList") or [])
                if isinstance(tag, dict)
            ],
            "source": "www-ssr",
            "commentsAvailable": False,
            "commentsUnavailableReason": _SIGN_MESSAGE,
        }
        return stats

    # ── 需要签名的能力：明确失败 ────────────────────────────────────

    def sign_headers(self, uri: str, payload: Any = None) -> dict:
        """生成 xs/xt/x-s-common 签名头。

        当前**无法实现**，一律抛 ``SignatureRequiredError``。原因见模块 docstring：
        xs 由页面 ``window._webmsxyw`` 用混淆过的算法现算，随前端发版换代，
        本地复刻必然过期，而过期后的表现往往是「接口返回空数据」这种静默错误。
        """
        raise SignatureRequiredError("生成 xs 签名")

    def search_notes(self, keyword: str, **_: Any) -> dict:
        """搜索笔记。需要 xs 签名 → 明确失败。"""
        raise SignatureRequiredError(
            f"搜索「{keyword}」",
            "搜索接口（/api/sns/web/v1/search/notes）需要 xs 签名，"
            "暂不支持无浏览器调用，请加 --browser",
        )

    def get_comments(self, note_id: str, **_: Any) -> dict:
        """读取笔记评论。需要 xs 签名 → 明确失败。"""
        raise SignatureRequiredError(
            f"读取笔记 {note_id} 的评论",
            "评论接口（/api/sns/web/v2/comment/page）需要 xs 签名，"
            "暂不支持无浏览器调用，请加 --browser",
        )

    def publish_note(self, **_: Any) -> dict:
        """发布笔记。既需要签名又需要页面上传 → 明确失败。"""
        raise SignatureRequiredError(
            "发布笔记",
            "发布链路需要 xs 签名并依赖页面上传控件，无浏览器模式无法完成，请加 --browser",
        )


# ─── 便捷函数 ────────────────────────────────────────────────────────────────


def _call(action: Callable[[XhsApi], dict], browser: str = "chrome") -> dict:
    """建一个客户端执行一次调用（给不想管理对象的调用方用）。"""
    return action(XhsApi(browser=browser))


def check_login(browser: str = "chrome") -> dict:
    """检查登录状态。"""
    return _call(lambda api: api.check_login(), browser)


def user_info(browser: str = "chrome") -> dict:
    """账号信息。"""
    return _call(lambda api: api.user_info(), browser)


def list_feeds(browser: str = "chrome", limit: int = 0) -> dict:
    """首页推荐流。"""
    return _call(lambda api: api.list_feeds(limit=limit), browser)


def list_posted_notes(browser: str = "chrome", user_id: str = "") -> dict:
    """自己（或指定用户）已发布的笔记列表（第一页）。

    说明：creator 的 ``note/user/posted`` 接口已不再返回数据（HTTP 200 + 空列表 +
    ``page: -1``，见模块 docstring），所以这里改走 www 主页的 SSR 数据。
    结果**不保证是全部笔记**：实测 14/14、32/116、30/395 三种情况都有；
    ``complete`` 只在「拿到的条数 >= 主页显示的累计篇数」时为 True，
    否则附带 ``warning`` 说明还差多少。需要完整列表请加 ``--browser``。
    """
    return _call(lambda api: api.list_posted_notes(user_id=user_id), browser)


def note_stats(note_id: str, xsec_token: str = "", browser: str = "chrome") -> dict:
    """单篇笔记的正文与互动数据。"""
    return _call(lambda api: api.note_stats(note_id, xsec_token), browser)


# 供上层判断「这个异常要不要退回浏览器模式」
COOKIE_ERRORS = (CookieError,)
