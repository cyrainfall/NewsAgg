"""B站创作中心协议层客户端：纯 HTTP，不打开浏览器。

## 为什么有这一层

原本技能里所有操作都要经过「CLI → bridge server → 浏览器扩展 → 页面 DOM」。
只读操作（登录态、稿件列表）和投稿上传其实都有稳定的 Web 接口，走 HTTP 快几百倍，
也不需要用户先把 Chrome 打开。所以这里把协议部分抽出来，浏览器路径降级为
``cli.py --browser`` 的显式兜底。

## 认证态

从本机浏览器**只读**读取 B站 Cookie（``.agents/lib/browserless/chromium.py``），
不写回、不落盘、不打印任何值。``CookieError`` 表示技术上读不到（该退浏览器），
``AuthRequired`` 表示读到了但服务端说未登录（该让用户去登录）—— 两者的处置相反，
所以不能混成一个异常。

## 上传协议（UPOS）

B站 现在用 UPOS 分块上传，三步（字段名与顺序都来自
``s1.hdslb.com/bfs/static/creator-monorepo/videoup/static/js/607.*.js`` 的 UPOS hook，
不是猜的）：

1. ``GET /preupload`` 拿到 ``auth`` / ``biz_id`` / ``chunk_size`` / ``upos_uri`` / ``endpoint``；
2. ``POST <endpoint>/<upos_path>?uploads&output=json``（header ``X-Upos-Auth``）拿 ``upload_id``；
3. 每个分块 ``PUT <endpoint>/<upos_path>?partNumber=<i+1>&uploadId=..&chunk=<i>&chunks=<n>
   &size=..&start=..&end=..&total=..&output=json``（header ``X-Upos-Auth`` +
   ``Content-Type: application/octet-stream``）；
4. 合片 ``POST <endpoint>/<upos_path>?output=json&name=..&profile=..&uploadId=..&biz_id=..``，
   body ``{"parts": [{"partNumber": 1, "eTag": "etag"}]}`` —— 前端就是**字面量** ``"etag"``；
5. 投稿 ``POST /x/vu/web/add/v3``，``csrf`` = cookie ``bili_jct``。

## 诚实边界

- 分块上传顺序执行（前端默认 ``threads: 3``）。慢一点，但失败点少、日志可读。
- 封面上传只做「原图直传」，不做前端那套 16:9 / 4:3 裁剪。``--cover`` 请直接给
  裁好的图。
- 投稿接口没有 dry-run，所以本模块不提供「假投稿」语义：任何一步失败都抛异常。
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

# ─── 共享底座（.agents/lib/browserless）───────────────────────────────────────
#
# 技能可能被整目录拷到别的仓库里，所以不写死仓库绝对路径：从本文件往上找
# `.agents/lib/browserless`。找不到就给出中文报错，而不是 ImportError 栈。


def _find_lib() -> Path | None:
    env = os.environ.get("NEWSAGG_BROWSERLESS_LIB")
    if env:
        return Path(env).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".agents" / "lib"
        if (candidate / "browserless" / "__init__.py").is_file():
            return candidate
    return None


_LIB_DIR = _find_lib()
_IMPORT_ERROR: str | None = None

if _LIB_DIR is not None and str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

try:  # 需要区分「缺底座」与「其它 import 错误」
    from browserless.chromium import CookieError
    from browserless.http import ApiError, AuthRequired, HttpError, Session
except ImportError as exc:  # pragma: no cover - 只在技能被孤立拷贝时触发
    _IMPORT_ERROR = (
        f"缺少共享底座 .agents/lib/browserless（{exc}）。"
        "bilibili-skills 的协议层需要它；请确认技能目录位于仓库的 .agents/skills 下，"
        "或用 NEWSAGG_BROWSERLESS_LIB 指向 browserless 包所在目录。"
    )

    class CookieError(Exception):  # type: ignore[no-redef]
        """读不到浏览器 Cookie（占位定义，保证模块可导入）。"""

    class HttpError(Exception):  # type: ignore[no-redef]
        """网络层失败（占位定义）。"""

    class ApiError(Exception):  # type: ignore[no-redef]
        """服务端拒绝（占位定义）。"""

    class AuthRequired(ApiError):  # type: ignore[no-redef]  # noqa: N818
        """未登录（占位定义）。"""

    Session = None  # type: ignore[assignment,misc]


# ─── 接口常量（均已实测）─────────────────────────────────────────────────────

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
ARCHIVES_URL = "https://member.bilibili.com/x/web/archives"
ARCHIVE_PRE_URL = "https://member.bilibili.com/x/vupre/web/archive/pre"
TYPE2_LIST_URL = "https://member.bilibili.com/x/vupre/web/archive/human/type2/list"
PREUPLOAD_URL = "https://member.bilibili.com/preupload"
SUBMIT_URL = "https://member.bilibili.com/x/vu/web/add/v3"
COVER_UP_URL = "https://member.bilibili.com/x/vu/web/cover/up"

CREATOR_REFERER = "https://member.bilibili.com/platform/upload/video/frame"
WWW_REFERER = "https://www.bilibili.com/"

# preupload 的固定 query（来自前端 preuploadQuery）
UPLOAD_PROFILE = "ugcfx/bup"
UPLOAD_VERSION = "2.14.0"
UPLOAD_BUILD = 2140000

# 稿件状态
ARCHIVE_STATUSES = "is_pubing,pubed,not_pubed"

# 未登录 / 登录态失效 的业务码
_LOGIN_CODES = frozenset({-101})
# csrf 校验失败（cookie 过期或被换掉）
_CSRF_CODES = frozenset({-111})

# 分区兜底 tid（前端在没选分区时用 21，这里只用于错误提示，不自动兜底）
_FALLBACK_TID = 21

# 投稿字段上限（B站 前端的硬限制）
TITLE_MAX = 80
TAGS_MAX = 10
TAG_MAX = 20
DESC_MAX = 2000

# 定时发布窗口：前端提示「发布时间离当前时间必须 ≥5 分钟且 ≤15 天」
SCHEDULE_MIN_SECONDS = 5 * 60
SCHEDULE_MAX_SECONDS = 15 * 24 * 3600

# 封面：``/x/vu/web/cover/up`` 只吃 base64 data URL，体积会膨胀 4/3，给个上限兜底
_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}
COVER_DATA_URL_MAX = 8 * 1024 * 1024


# ─── 小工具 ──────────────────────────────────────────────────────────────────


def _platform_code() -> int:
    """``web_os`` 字段：Windows=1，macOS=2，其它=3（与前端 ``tp()`` 一致）。"""
    if sys.platform.startswith("win"):
        return 1
    if sys.platform == "darwin":
        return 2
    return 3


def _abs_path(path: str | Path, label: str) -> Path:
    """校验并返回绝对路径。文件相关操作一律用绝对路径（技能硬约束）。"""
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ApiError(f"{label}必须是绝对路径：{path}")
    if not raw.is_file():
        raise ApiError(f"{label}不存在或不是文件：{raw}")
    return raw


def _json_payload(payload: Any, *, url: str) -> Any:
    """解开 B站 的 ``{code, message, data}`` 信封，按错误类型分流。

    ``AuthRequired`` 与普通 ``ApiError`` 必须分开：前者要提示用户去登录，后者是参数
    或服务端策略问题，换条路也一样错。所以这里不做「统一成 RuntimeError」这种省事
    的处理。
    """
    if not isinstance(payload, dict):
        raise ApiError(f"响应不是 JSON 对象：{url}", body=payload)
    if "code" not in payload:
        # preupload 用 `OK` 而不是 `code`，由调用方自己判
        return payload
    code = payload.get("code")
    message = str(payload.get("message") or payload.get("msg") or "").strip()
    if code == 0:
        return payload.get("data")
    if code in _LOGIN_CODES:
        raise AuthRequired(
            "B站 登录态已失效，请在浏览器里重新登录（或运行 "
            "`python scripts/cli.py --browser login`）",
            code=code,
            body=payload,
        )
    if code in _CSRF_CODES:
        raise ApiError(
            "csrf 校验失败（cookie bili_jct 已失效），请在浏览器里刷新一次 B站 页面后重试",
            code=code,
            body=payload,
        )
    detail = f"：{message}" if message else ""
    raise ApiError(f"B站 接口返回错误（code={code}{detail}）：{url}", code=code, body=payload)


def _query(params: Mapping[str, Any]) -> str:
    """拼 query string（复用底座实现，保证空格编码等细节一致）。"""
    from browserless.http import build_query

    return build_query(params)


# preupload 的限频/风控码 → 中文处置建议。
# 这几个码是上传链路最常撞的：406 是 HTTP 层，601/602 是 body 里的 code。
_PREUPLOAD_HINTS: dict[int, str] = {
    406: "上传被限频：B站 认为你在短时间内申请了太多次上传。请等 10 分钟以上再试，"
    "并确认没有别的脚本/页面在同时上传。",
    601: "上传被限频：B站 返回「您上传视频过快，请您稍作休息后再继续」。"
    "请等一段时间再试（这是服务端按账号计的频率，不是本地 bug）。",
    602: "上传验证失败：B站 要求做一次人机验证。请打开浏览器访问 "
    "https://member.bilibili.com/platform/upload/video/frame 完成验证后再试，"
    "或改用 --browser 走页面提交。",
}


def _preupload_error(exc: ApiError) -> ApiError:
    """把 preupload 的限频/风控响应翻译成能照着做的中文提示。

    底座的 ``ApiError.body`` 是**原始字节**（不是解好的 dict），所以这里自己解一次：
    HTTP 406 的 body 里还有个业务 ``code``（601 限频 / 602 验证失败），它比 406
    本身更能说明问题。
    """
    code: int | None = None
    raw = exc.body
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8", errors="replace").strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                try:
                    code = int(parsed.get("code"))
                except (TypeError, ValueError):
                    code = None
    elif isinstance(raw, dict):
        try:
            code = int(raw.get("code"))
        except (TypeError, ValueError):
            code = None

    hint = _PREUPLOAD_HINTS.get(code) if code is not None else None
    if hint is None and exc.status is not None:
        hint = _PREUPLOAD_HINTS.get(exc.status)
    if hint is None:
        return exc
    return ApiError(hint, code=code or exc.code, status=exc.status, body=raw)


def _form_field(boundary: str, name: str, value: str) -> bytes:
    """拼一个纯文本 multipart 表单字段（不带 filename，走普通字段语义）。"""
    return (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
    ).encode()


def _suggest(wanted: str, options: Iterable[str]) -> list[str]:
    """给用户一次改错的机会：找名字有交集的选项（B站 分区名用 `·` 分隔，很容易打错）。"""
    if not wanted:
        return []
    chars = set(wanted)
    scored: list[tuple[int, str]] = []
    for option in dict.fromkeys(options):
        overlap = len(chars & set(option))
        if overlap >= 2:
            scored.append((overlap, option))
    scored.sort(key=lambda pair: (-pair[0], len(pair[1])))
    return [name for _, name in scored[:3]]


@dataclass(frozen=True)
class PreuploadInfo:
    """``/preupload`` 的响应。字段名与前端 ``preuploadInfo`` 一致。"""

    endpoint: str
    upos_uri: str
    # auth 是 preupload 签发的一次性上传令牌 —— 与 cookie 同级敏感，
    # 绝不能跟着 repr 进日志或异常信息里，所以整个字段 repr=False。
    auth: str = field(repr=False)
    biz_id: int
    chunk_size: int
    threads: int
    timeout: float
    put_query: str
    endpoints: tuple[str, ...]
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def filename(self) -> str:
        """投稿 ``videos[].filename``：upos_uri 的文件名主干（不含扩展名）。

        前端：``upos_uri.split(/(\\|\\/)/g).pop().split(".")[0]``。
        """
        path = self.upos_uri.split("://", 1)[-1]
        return PurePosixPath(path).name.split(".")[0]

    @property
    def cid(self) -> int:
        """投稿 ``videos[].cid``：前端直接用 preupload 的 ``biz_id``。"""
        return int(self.biz_id)


# ─── 客户端 ──────────────────────────────────────────────────────────────────


class BilibiliAPI:
    """B站创作中心协议层客户端。

    典型用法::

        api = BilibiliAPI()
        api.check_login()
        api.list_archives(ps=10)
    """

    def __init__(
        self,
        browser: str = "chrome",
        *,
        domains: Sequence[str] = ("bilibili.com",),
        session: Any = None,
        min_interval: float = 0.35,
    ) -> None:
        if Session is None:
            raise ApiError(_IMPORT_ERROR or "共享底座不可用")
        self.browser = browser
        self._session = session or Session.from_browser(
            browser,
            list(domains),
            referer=CREATOR_REFERER,
            min_interval=min_interval,
        )

    # ── 基础 ────────────────────────────────────────────────────────────

    @property
    def session(self) -> Any:
        return self._session

    @property
    def csrf(self) -> str:
        """``bili_jct`` 就是 csrf token（所有投稿类 POST 必须带）。"""
        token = self._session.jar.get("bili_jct")
        if not token:
            raise AuthRequired("Cookie 里没有 bili_jct，无法提交投稿请求，请在浏览器里重新登录 B站")
        return token

    def cookie_summary(self) -> dict:
        """安全的 Cookie 摘要（只有域名与键名，永不含值）。"""
        return self._session.jar.describe()

    def _get(self, url: str, *, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        if params:
            url = f"{url}?{_query(params)}"
        resp = self._session.get(url, **kwargs)
        return _json_payload(resp.json(), url=url)

    def _post_json(
        self,
        url: str,
        data: Any,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        accept_status: tuple[int, ...] = (200,),
        timeout: float = 30.0,
    ) -> Any:
        if params:
            url = f"{url}?{_query(params)}"
        resp = self._session.post(
            url,
            json_body=data,
            headers=headers,
            accept_status=accept_status,
            timeout=timeout,
        )
        return _json_payload(resp.json(), url=url)

    # ── 只读 ────────────────────────────────────────────────────────────

    def check_login(self) -> dict:
        """返回登录信息：``{"logged_in": True, "mid": .., "nickname": .., ...}``。"""
        data = self._get(NAV_URL, headers={"Referer": WWW_REFERER})
        if not isinstance(data, dict):
            raise ApiError("nav 接口返回了意外的结构", body=data)
        if not data.get("isLogin"):
            raise AuthRequired("未登录 B站：请先在浏览器里登录，或运行 get-qrcode 扫码登录")
        level = data.get("level_info") or {}
        vip = data.get("vip") or {}
        return {
            "logged_in": True,
            "mid": data.get("mid"),
            "nickname": data.get("uname") or "",
            "level": level.get("current_level"),
            "vip_status": data.get("vipStatus", 0),
            "vip_label": (vip.get("label") or {}).get("text", ""),
            "money": data.get("money"),
        }

    def list_archives(
        self,
        *,
        status: str = ARCHIVE_STATUSES,
        pn: int = 1,
        ps: int = 10,
    ) -> dict:
        """稿件列表。返回 ``{counts, page, archives}``，archives 里只留展示需要的字段。"""
        data = self._get(ARCHIVES_URL, params={"status": status, "pn": pn, "ps": ps})
        data = data if isinstance(data, dict) else {}
        archives = []
        for item in data.get("arc_audits") or []:
            archive = (item or {}).get("Archive") or {}
            if not archive:
                continue
            archives.append(
                {
                    "aid": archive.get("aid"),
                    "bvid": archive.get("bvid"),
                    "title": archive.get("title"),
                    "tid": archive.get("tid"),
                    "state": archive.get("state"),
                    "state_desc": archive.get("state_desc"),
                    "ptime": archive.get("ptime"),
                    "duration": archive.get("duration"),
                    "reject_reason": archive.get("reject_reason") or "",
                }
            )
        return {
            "counts": data.get("class") or {},
            "page": data.get("page") or {},
            "archives": archives,
        }

    def archive_pre(self) -> dict:
        """投稿前置信息：分区树、类型列表、创作声明选项等。"""
        data = self._get(ARCHIVE_PRE_URL, params={"lang": "cn"})
        if not isinstance(data, dict):
            raise ApiError("投稿前置接口返回了意外的结构", body=data)
        return data

    def type2_list(self) -> list[dict]:
        """「类型」（``human_type2``）列表：30 项 ``{id, name}``。"""
        data = self._get(TYPE2_LIST_URL)
        items = (data or {}).get("type_list") if isinstance(data, dict) else None
        return [{"id": int(i.get("id")), "name": str(i.get("name") or "")} for i in items or []]

    # ── 分区 / 声明解析 ─────────────────────────────────────────────────

    def resolve_category(
        self,
        pre: Mapping[str, Any],
        primary: str,
        secondary: str = "",
        *,
        human_type: str = "",
        type_list: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[int, int]:
        """把「生活,日常」解析成 ``(tid, human_type2)``，失败时列出可选值。

        ``type_mode == 2`` 时投稿表单同时要求「分区」（``tid``，来自 ``typelist``）
        与「类型」（``human_type2``，来自 ``/x/vupre/web/archive/human/type2/list``）。
        B站 的 ``checkArchive`` 对两者分别拦 ``categoryRequired``「至少选择一个分区吧」，
        所以缺一不可 —— 这也是本方法返回二元组而不是单个 tid 的原因。

        ``human_type2`` 的语义是「父类型」（前端的 ``type_pid`` 就是它），跟
        ``typelist`` 的一级分区**不是同一套 id**，名字也不完全重合（例如一级分区
        「生活」在类型列表里对应「生活兴趣/生活经验/vlog」）。所以这里按
        `--human-type` → 二级分区名 → 一级分区名 的顺序匹配，都不中就报错并列出
        全部可选类型，绝不猜一个 id 蒙过去。
        """
        typelist = list(pre.get("typelist") or [])
        type_mode = pre.get("type_mode")
        wants_human_type = type_mode == 2 or type_mode is True
        # 「类型」列表要单独请求（pre 里没有），提前拿到：下面判断第二个位置是否是
        # 合法的「类型」名时要用，后面定位 human_type2 时也复用，不重复请求。
        if wants_human_type and type_list is None:
            type_list = self.type2_list()

        top: dict[str, int] = {}
        children: dict[str, tuple[int, int]] = {}
        for item in typelist:
            name = str(item.get("name") or "").strip()
            item_tid = int(item.get("id") or 0)
            if name and item_tid:
                top.setdefault(name, item_tid)
            for child in item.get("children") or []:
                cname = str((child or {}).get("name") or "").strip()
                cid = int((child or {}).get("id") or 0)
                if cname and cid:
                    children.setdefault(cname, (cid, item_tid))

        # ① 先定位 tid（具体分区）
        tid = 0
        matched_parent = ""
        if secondary and secondary in children:
            tid = children[secondary][0]
            matched_parent = next(
                (name for name, pid in top.items() if pid == children[secondary][1]), ""
            )
        elif primary and primary in children:
            tid = children[primary][0]
            matched_parent = next(
                (name for name, pid in top.items() if pid == children[primary][1]), ""
            )
        elif primary and primary in top:
            tid = top[primary]
            matched_parent = primary
        elif secondary and secondary in top:
            tid = top[secondary]
            matched_parent = secondary

        # 用户在 `--category` 里写了二级分区，就必须真的匹配上某个分区或某个
        # 「类型」名：一级分区名命中不代表第二个位置可以随便写 —— 静默退到一级会把
        # 稿件投进错误的目录。允许「类型」名落在第二个位置，是因为
        # `--category 生活/生活兴趣` 是很自然的写法（生活兴趣正是 type2 的名字）。
        type_names = {str(i.get("name") or "").strip() for i in (type_list or [])}
        bogus_secondary = (
            bool(secondary)
            and secondary not in children
            and secondary not in top
            and secondary not in type_names
        )

        if not tid or bogus_secondary:
            wanted = f"{primary}{'/' + secondary if secondary else ''}"
            suggestions = _suggest(wanted.replace("/", ""), list(children) + list(top))
            hint = f"你是不是想写「{'」或「'.join(suggestions)}」？" if suggestions else ""
            raise ApiError(
                f"找不到分区「{wanted}」。{hint}"
                f"可选一级分区：{'、'.join(sorted(top)) or '（接口未返回分区）'}。"
                "用法：--category 一级/二级，例如 --category 知识/社科·法律·心理"
            )

        if not wants_human_type:
            return tid, 0

        # ② 再定位 human_type2（父类型）
        by_name = {str(i.get("name") or "").strip(): int(i.get("id") or 0) for i in type_list}
        by_id = {int(i.get("id") or 0): str(i.get("name") or "") for i in type_list}
        if not by_name:
            raise ApiError(
                "B站 要求选「类型」（human_type2），但类型列表接口返回为空，无法投稿。"
                "可用 `--browser` 走页面提交。"
            )

        human_type2 = 0
        candidates = [
            human_type.strip(),
            secondary,
            _HUMAN_TYPE_BY_PARENT.get(matched_parent, ""),
            matched_parent,
            primary,
        ]
        for candidate in candidates:
            if candidate and candidate in by_name and by_name[candidate]:
                human_type2 = by_name[candidate]
                break
        if not human_type2 and human_type.strip().isdigit() and int(human_type) in by_id:
            human_type2 = int(human_type)
        if not human_type2:
            wanted = human_type.strip() or f"{primary}/{secondary}".strip("/")
            suggestions = _suggest(wanted, list(by_name))
            hint = f"你是不是想写「{'」或「'.join(suggestions)}」？" if suggestions else ""
            raise ApiError(
                f"找不到「类型」（human_type2）「{wanted}」。{hint}"
                f"用 --human-type 指定，可选：{'、'.join(by_name)}"
            )
        return tid, human_type2

    @staticmethod
    def resolve_declaration(pre: Mapping[str, Any], declaration: str) -> int:
        """创作声明：把文案或简写映射成 ``neutral_mark.mark_list`` 里的 id。"""
        mark = pre.get("neutral_mark") or {}
        options: list[tuple[int, str]] = [
            (int(item.get("id")), str(item.get("content") or ""))
            for item in (mark.get("mark_list") or [])
            if item
        ]
        if not options:
            raise ApiError("投稿前置接口没返回创作声明选项（neutral_mark），无法投稿")
        wanted = (declaration or "").strip()
        if not wanted:
            listed = "、".join(text for _, text in options)
            raise ApiError(
                f"投稿必须选一个创作声明（B站 提交前会拦下）。可选：{listed}。"
                ' 用 --declaration 指定，例如 --declaration "含AI生成内容"'
            )
        for mark_id, text in options:
            if wanted == text or wanted == str(mark_id):
                return mark_id
        aliases = {
            "AI": "含AI生成内容",
            "含AI": "含AI生成内容",
            "虚构": "含虚构演绎内容",
            "营销": "内容含营销信息",
            "观点": "个人观点，仅供参考",
            "转载": "内容为转载",
            "自制": "内容为自制",
            "无": "内容无需标注",
            "无需标注": "内容无需标注",
        }
        target = aliases.get(wanted, wanted)
        for mark_id, text in options:
            if text == target or target in text or text in target:
                return mark_id
        listed = "、".join(text for _, text in options)
        raise ApiError(f"创作声明「{declaration}」不在可选列表里。可选：{listed}")

    # ── 上传 ────────────────────────────────────────────────────────────

    def preupload(self, video_path: str | Path, *, profile: str = UPLOAD_PROFILE) -> PreuploadInfo:
        """申请上传：拿到 auth / biz_id / chunk_size / upos_uri / endpoint。

        这个接口是 B站 上传限频的**第一道闸**：短时间内反复申请会直接 406，body 里
        ``code=601``「您上传视频过快，请您稍作休息后再继续」，``code=602`` 是验证失败。
        所以这里把它翻译成明确的中文提示，而不是让用户对着一句 HTTP 406 猜。
        """
        path = _abs_path(video_path, "视频文件")
        try:
            data = self._get(
                PREUPLOAD_URL,
                params={
                    "name": path.name,
                    "size": path.stat().st_size,
                    "r": "upos",
                    "profile": profile,
                    "ssl": 0,
                    "version": UPLOAD_VERSION,
                    "build": UPLOAD_BUILD,
                },
            )
        except ApiError as exc:
            raise _preupload_error(exc) from exc
        if not isinstance(data, dict):
            raise ApiError("preupload 返回了意外的结构", body=data)
        if data.get("OK") not in (1, "1", True):
            raise ApiError(
                f"preupload 被拒绝（OK={data.get('OK')!r}）：{data.get('message') or data}",
                body=data,
            )
        auth = str(data.get("auth") or "")
        upos_uri = str(data.get("upos_uri") or "")
        endpoint = str(data.get("endpoint") or "")
        if not auth or not upos_uri or not endpoint:
            raise ApiError(
                "preupload 响应缺少 auth / upos_uri / endpoint，无法继续上传",
                body=data,
            )
        endpoints = tuple(str(e) for e in (data.get("endpoints") or []) if e)
        return PreuploadInfo(
            endpoint=endpoint,
            upos_uri=upos_uri,
            auth=auth,
            biz_id=int(data.get("biz_id") or 0),
            chunk_size=int(data.get("chunk_size") or 0),
            threads=int(data.get("threads") or 1),
            timeout=float(data.get("timeout") or 1200),
            put_query=str(data.get("put_query") or ""),
            endpoints=endpoints or (endpoint,),
            raw=data,
        )

    @staticmethod
    def _upos_url(info: PreuploadInfo, endpoint: str | None = None) -> str:
        """把 ``upos://bucket/key`` 换成 ``https://<endpoint>/bucket/key``。

        前端是把 ``upos://`` 直接替换成 ``endpoint + "/"``；endpoint 常以 ``//``
        开头（协议相对），这里补上 https。
        """
        host = endpoint or info.endpoint
        if host.startswith("//"):
            host = "https:" + host
        path = info.upos_uri.split("://", 1)[-1]
        return f"{host.rstrip('/')}/{path}"

    def _read_upload_id(self, body: bytes) -> str:
        text = body.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            upload_id = data.get("upload_id") or data.get("uploadId") or data.get("UploadId")
            if upload_id:
                return str(upload_id)
        match = re.search(r"<UploadId>([^<]+)</UploadId>", text)
        if match:
            return match.group(1).strip()
        raise ApiError(f"初始化上传没有返回 upload_id：{text[:300]}")

    def upload_video(
        self,
        video_path: str | Path,
        *,
        info: PreuploadInfo | None = None,
        profile: str = UPLOAD_PROFILE,
        progress: Any = None,
    ) -> dict:
        """完整分块上传，返回 ``{"filename", "cid", "biz_id", "upload_id", "chunks"}``。

        ``progress`` 是可选的 ``callable(chunk_index, chunks, uploaded_bytes, total)``，
        用来给 CLI 打进度 —— 大文件要传十几分钟，静默会让人以为卡死。
        """
        path = _abs_path(video_path, "视频文件")
        info = info or self.preupload(path, profile=profile)
        base = self._upos_url(info)
        auth_headers = {"X-Upos-Auth": info.auth}
        size = path.stat().st_size
        chunk_size = info.chunk_size or size or 1

        # ① 初始化分片上传。
        # 这 4 个参数缺一不可（实测：少 filesize -> 400/400001001，
        # 少 profile 或 partsize -> 400/400001004）：它们是前端的 uploadsQuery，
        # 由 updateUploadsQuery 在 preupload 之后填充。profile 里的斜杠**不能**转义。
        init_query = "uploads&output=json"
        init_query += "&profile=" + profile
        init_query += f"&partsize={chunk_size}"
        init_query += f"&biz_id={info.biz_id}"
        init_query += f"&filesize={size}"
        resp = self._session.post(
            f"{base}?{init_query}",
            headers=auth_headers,
            timeout=float(info.timeout),
        )
        upload_id = self._read_upload_id(resp.body)

        # ② 逐块 PUT（前端默认 3 线程；顺序传更稳，失败点唯一）
        chunks = max(1, (size + chunk_size - 1) // chunk_size)
        uploaded = 0
        with path.open("rb") as handle:
            for index in range(chunks):
                blob = handle.read(chunk_size)
                if not blob:
                    break
                start = index * chunk_size
                end = start + len(blob)
                query = _query(
                    {
                        "partNumber": index + 1,
                        "uploadId": upload_id,
                        "chunk": index,
                        "chunks": chunks,
                        "size": len(blob),
                        "start": start,
                        "end": end,
                        "total": size,
                        "output": "json",
                    }
                )
                headers = dict(auth_headers)
                headers["Content-Type"] = "application/octet-stream"
                self._session.request(
                    "PUT",
                    f"{base}?{query}",
                    data=blob,
                    headers=headers,
                    timeout=float(info.timeout),
                )
                uploaded += len(blob)
                if progress is not None:
                    progress(index, chunks, uploaded, size)

        # ③ 合片。前端 body 里的 eTag 是字面量 "etag"，不是真实 ETag —— 照实复现。
        parts = [{"partNumber": i + 1, "eTag": "etag"} for i in range(chunks)]
        complete_query = _query(
            {
                "output": "json",
                "name": path.name,
                "profile": profile,
                "uploadId": upload_id,
                "biz_id": info.biz_id,
            }
        )
        resp = self._session.post(
            f"{base}?{complete_query}",
            json_body={"parts": parts},
            headers=auth_headers,
            timeout=float(info.timeout),
        )
        self._check_complete(resp)
        return {
            "filename": info.filename,
            "cid": info.cid,
            "biz_id": info.biz_id,
            "upload_id": upload_id,
            "chunks": chunks,
            "size": size,
        }

    @staticmethod
    def _check_complete(resp: Any) -> None:
        text = resp.body.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            data = json.loads(text)
        except ValueError:
            if "<Error>" in text or "error" in text.lower():
                raise ApiError(f"合片失败：{text[:300]}") from None
            return
        if isinstance(data, dict):
            if data.get("OK") in (1, "1", True) or data.get("code") == 0:
                return
            message = data.get("message") or data.get("error") or text[:300]
            raise ApiError(f"合片失败（{message}）", body=data)

    def upload_cover(self, cover_path: str | Path) -> str:
        """上传封面原图，返回可直接填进 ``cover`` 字段的 url。

        实现要点（实测出来的，别改成「普通文件上传」）：``/x/vu/web/cover/up``
        只接受 **base64 data URL 字符串** 放在 ``cover`` 表单字段里。用
        ``multipart`` 传二进制文件字节会被拒（``code=-400 请求错误``），
        用 ``files=`` 甚至会被 WAF 拦成 412。前端 ``uploadImage`` 收的就是
        canvas 导出的 data URL，所以这里照它的真实形态复现。

        只做原图直传：前端那套 16:9 / 4:3 双裁（``cutImage``）没有复现，
        需要精确构图的封面请自己裁好再传；不传 ``cover43`` 字段时 B站 用同一张。
        """
        path = _abs_path(cover_path, "封面文件")
        payload = path.read_bytes()
        # data URL 的 mime 从扩展名推，未知一律按 jpeg —— B站 只按 data URL 头解析
        suffix = path.suffix.lower()
        mime = _IMAGE_MIME.get(suffix, "image/jpeg")
        data_url = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
        if len(data_url) > COVER_DATA_URL_MAX:
            raise ApiError(
                f"封面转成 base64 后 {len(data_url)} 字节，超过上限 {COVER_DATA_URL_MAX}，"
                "请先压缩（建议 ≤2MB 的 jpg/png）"
            )
        boundary = "----bilibili-skills-" + uuid.uuid4().hex
        body = b"".join(
            [
                _form_field(boundary, "cover", data_url),
                _form_field(boundary, "csrf", self.csrf),
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        resp = self._session.post(
            COVER_UP_URL,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Referer": CREATOR_REFERER,
            },
            timeout=180.0,
        )
        data = _json_payload(resp.json(), url=COVER_UP_URL)
        url = ""
        if isinstance(data, dict):
            url = str(data.get("url") or data.get("cover") or "")
        if not url:
            raise ApiError("封面上传成功但没有返回 url", body=data)
        return url

    # ── 投稿 ────────────────────────────────────────────────────────────

    def submit_archive(self, payload: Mapping[str, Any]) -> dict:
        """提交稿件（``/x/vu/web/add/v3``）。返回 ``{aid, bvid}``。"""
        body = dict(payload)
        body["csrf"] = self.csrf
        params = {"web_location": "333.1024", "t": int(time.time() * 1000), "csrf": self.csrf}
        try:
            data = self._post_json(
                SUBMIT_URL,
                body,
                params=params,
                accept_status=(200, 412),
                timeout=60.0,
            )
        except ApiError as exc:
            if exc.status == 412:
                raise ApiError(
                    "投稿被风控拦下（HTTP 412）：B站 认为提交内容存在安全风险。"
                    "常见原因：标题/简介/标签触发了审核词、短时间投稿过频、账号新设备登录。"
                    "请改文案或稍后重试，必要时改用 `--browser` 走页面提交。",
                    status=412,
                    code=412,
                ) from exc
            raise
        if not isinstance(data, dict):
            raise ApiError("投稿接口返回了意外的结构", body=data)
        return {"aid": data.get("aid"), "bvid": data.get("bvid")}

    @staticmethod
    def build_submit_payload(
        *,
        title: str,
        tid: int,
        human_type2: int,
        tags: Sequence[str],
        description: str = "",
        filename: str,
        cid: int,
        declaration_id: int,
        copyright: int = 1,
        source: str = "",
        cover_url: str = "",
        cover43_url: str = "",
        dtime: int = 0,
    ) -> dict:
        """组装 ``add/v3`` 的 data。

        字段全部来自投稿页前端 bundle 的 ``getBasicParams``（不是猜的）：
        ``tag`` 是**逗号拼接的字符串**、``videos`` 是单元素数组且 ``cid`` 用
        preupload 的 ``biz_id``、``interactive``/``recreate`` 硬编码 0。
        """
        payload: dict[str, Any] = {
            "title": title,
            "copyright": copyright,
            "tid": tid,
            "human_type2": human_type2,
            "tag": ",".join(tags),
            "desc": description,
            "desc_format_id": 0,
            "videos": [{"filename": filename, "title": title, "desc": "", "cid": cid}],
            "interactive": 0,
            "recreate": 0,
            "web_os": _platform_code(),
            "creation_statement": {"id": declaration_id},
            "no_disturbance": 0,
        }
        if copyright == 2:
            payload["source"] = source
        if cover_url:
            payload["cover"] = cover_url
        if cover43_url:
            payload["cover43"] = cover43_url
        if dtime:
            payload["dtime"] = dtime
        return payload

    # ── 一步投稿 ────────────────────────────────────────────────────────

    def publish_video(
        self,
        *,
        video_path: str | Path,
        title: str,
        category: str,
        human_type: str = "",
        tags: Sequence[str] = (),
        description: str = "",
        cover_path: str | Path | None = None,
        declaration: str = "",
        schedule_at: str | None = None,
        source: str = "",
        dry_run: bool = False,
        progress: Any = None,
    ) -> dict:
        """完整链路：校验 → preupload → 分块上传 → 封面 → 提交。

        ``dry_run=True`` 时做完除「提交」以外的所有步骤，把将要提交的 payload
        结构（不含 csrf）打印出来。B站 的投稿接口没有 dry-run，所以这是唯一
        能安全验证链路的办法。
        """
        path = _abs_path(video_path, "视频文件")
        title = (title or "").strip()
        if not title:
            raise ApiError("稿件标题不能为空（--title-file 指向的文件是空的？）")
        if len(title) > TITLE_MAX:
            raise ApiError(f"标题超过 {TITLE_MAX} 字（当前 {len(title)} 字），B站 会拒收")
        if len(description) > DESC_MAX:
            raise ApiError(f"简介超过 {DESC_MAX} 字（当前 {len(description)} 字）")
        cleaned_tags = [t.strip().lstrip("#") for t in tags if t and t.strip()]
        if not cleaned_tags:
            raise ApiError("至少填一个标签（--tags），B站 提交前会拦下")
        if len(cleaned_tags) > TAGS_MAX:
            raise ApiError(f"标签最多 {TAGS_MAX} 个（当前 {len(cleaned_tags)} 个）")
        too_long = [t for t in cleaned_tags if len(t) > TAG_MAX]
        if too_long:
            raise ApiError(f"标签最长 {TAG_MAX} 字，超长：{'、'.join(too_long)}")
        if not cover_path:
            raise ApiError(
                "B站 投稿必须有封面（不传封面会在提交时被「请先上传封面」拦下）。"
                "请用 --cover 传一张裁好的图片（建议 16:9，≥1146x717）。"
            )

        pre = self.archive_pre()
        primary, secondary = _split_category(category)
        type_list = self.type2_list() if pre.get("type_mode") == 2 else []
        tid, human_type2 = self.resolve_category(
            pre,
            primary,
            secondary,
            human_type=human_type,
            type_list=type_list,
        )
        declaration_id = self.resolve_declaration(pre, declaration)
        if declaration_id == -2 and not source.strip():
            raise ApiError("创作声明选了「内容为转载」，必须用 --source 填转载来源")
        copyright_value = 2 if declaration_id == -2 else 1
        dtime = _parse_schedule(schedule_at) if schedule_at else 0

        info = self.preupload(path)
        uploaded = self.upload_video(path, info=info, progress=progress)
        cover_url = self.upload_cover(cover_path)

        payload = self.build_submit_payload(
            title=title,
            tid=tid,
            human_type2=human_type2,
            tags=cleaned_tags,
            description=description,
            filename=uploaded["filename"],
            cid=int(uploaded["cid"]),
            declaration_id=declaration_id,
            copyright=copyright_value,
            source=source.strip(),
            cover_url=cover_url,
            dtime=dtime,
        )
        result: dict[str, Any] = {
            "success": True,
            "title": title,
            "tid": tid,
            "human_type2": human_type2,
            "tags": cleaned_tags,
            "upload": {
                "filename": uploaded["filename"],
                "cid": uploaded["cid"],
                "chunks": uploaded["chunks"],
                "size": uploaded["size"],
            },
            "cover_url": cover_url,
            "declaration_id": declaration_id,
            "dtime": dtime,
        }
        if dry_run:
            result["dry_run"] = True
            result["payload"] = payload
            result["message"] = "已完成上传与封面，未提交稿件（--dry-run）"
            return result

        submitted = self.submit_archive(payload)
        result.update(submitted)
        result["message"] = "投稿已提交，B站 需要几分钟处理，可用 list-archives 确认是否落地"
        return result


# 一级分区 → 默认「类型」（human_type2）的**名字**提示表。
#
# 两套分类体系并不同构：`typelist`（分区，20 个一级）与 `human/type2/list`
# （类型，30 项）名字与粒度都不一样，接口也不返回两者的对应关系。所以只能给
# 一张名字级的提示表，让 `--category 知识/社科·法律·心理` 这种常见写法开箱可用；
# id 仍然一律从接口取，表里的名字在接口列表里不存在就报错并列出全部可选值 ——
# 不猜 id，也不静默兜底。
_HUMAN_TYPE_BY_PARENT: dict[str, str] = {
    "动画": "动画",
    "番剧": "动画",
    "国创": "动画",
    "电影": "影视",
    "电视剧": "影视",
    "纪录片": "影视",
    "影视": "影视",
    "鬼畜": "鬼畜",
    "音乐": "音乐",
    "舞蹈": "舞蹈",
    "游戏": "游戏",
    "知识": "知识",
    "科技": "科技数码",
    "运动": "体育运动",
    "汽车": "汽车",
    "生活": "生活兴趣",
    "美食": "美食",
    "动物圈": "动物",
    "时尚": "时尚美妆",
    "娱乐": "娱乐",
}


def _split_category(value: str) -> tuple[str, str]:
    """``"知识/社科·法律·心理"`` → ``("知识", "社科·法律·心理")``。

    与 cli.py 里的同名逻辑保持一致（分隔符允许中英文逗号、顿号、斜杠、大于号），
    但这里不 import cli，避免 api.py 反向依赖命令行入口。
    """
    parts = [p.strip() for p in re.split(r"[,，、>/]+", value or "") if p.strip()]
    primary = parts[0] if parts else ""
    secondary = parts[1] if len(parts) > 1 else ""
    return primary, secondary


def _parse_schedule(value: str) -> int:
    """把 ISO8601 本地时间转成秒级时间戳，并校验 B站 的 5min~15days 窗口。"""
    text = (value or "").strip().replace("/", "-").replace(" ", "T")
    parsed = None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        try:
            parsed = time.mktime(time.strptime(text, fmt)) if "%z" not in fmt else None
            if parsed is not None:
                break
        except ValueError:
            continue
    if parsed is None:
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(text).timestamp()
        except ValueError as exc:
            raise ApiError(
                f"定时发布时间「{value}」无法解析，请用 ISO8601 格式，如 2026-03-10T12:00"
            ) from exc
    now = time.time()
    if parsed - now < SCHEDULE_MIN_SECONDS:
        raise ApiError("定时发布时间必须距现在至少 5 分钟（B站 的限制）")
    if parsed - now > SCHEDULE_MAX_SECONDS:
        raise ApiError("定时发布时间不能超过 15 天（B站 的限制）")
    return int(parsed)


def check_login() -> dict:
    """便捷函数：读一次浏览器 Cookie 并查登录态。"""
    return BilibiliAPI().check_login()


def list_archives(*, ps: int = 10, pn: int = 1, status: str = ARCHIVE_STATUSES) -> dict:
    """便捷函数：稿件列表。"""
    return BilibiliAPI().list_archives(ps=ps, pn=pn, status=status)


__all__ = [
    "ARCHIVES_URL",
    "ARCHIVE_PRE_URL",
    "ARCHIVE_STATUSES",
    "COVER_UP_URL",
    "NAV_URL",
    "PREUPLOAD_URL",
    "SUBMIT_URL",
    "TYPE2_LIST_URL",
    "UPLOAD_PROFILE",
    "AuthRequired",
    "BilibiliAPI",
    "CookieError",
    "HttpError",
    "PreuploadInfo",
    "check_login",
    "list_archives",
]
