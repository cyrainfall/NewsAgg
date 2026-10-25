"""抖音创作服务平台的纯 HTTP 客户端（不打开浏览器）。

为什么需要这一层
----------------
原来的 CLI 是「扩展 Bridge + 真实浏览器」模式：先启动 Chrome、装扩展、由页面 JS 去
发起请求。它可靠，但代价是每次都要先把浏览器拉起来。本模块走另一条路：**只读本机
浏览器里已登录的 Cookie**，然后用 urllib 直接请求，不启动浏览器。

分层纪律（与 ``.agents/lib/browserless`` 的约定一致）：本模块只依赖 ``browserless``
的 ``http`` / ``chromium``，**不 import 本技能自己的浏览器模块**（``bridge`` 等）；
浏览器路径只作为 ``--browser`` 显式开关存在，由 CLI 决定用哪条路。

实测结论（2026-10 在本仓库机器上逐条验证，Chrome 里有 38 条 douyin.com Cookie）
------------------------------------------------------------------------
**纯 Cookie 即可读，不需要浏览器**（``referer: https://creator.douyin.com/``）：

* ``GET /web/api/media/user/info/``         账号信息（昵称 / uid / 粉丝数 …）
* ``GET /aweme/v1/creator/user/info/``      创作者信息（开通日期 / 实名 / 权限位）
* ``GET /web/api/creator/item/list``        已发布作品列表（**必须带 count 与 order_by**，
  缺任一个都返回 ``status_code: 5「参数不合法」``）

**必须浏览器**（签名缺口，见下）：

* ``POST /web/api/media/aweme/create_v2/``  发布作品
* ``POST /web/api/media/aweme/draft``       存草稿
* ``POST /web/api/media/aweme/update_v2/``、``/web/api/media/aweme/delete`` 同理

未登录的判定：上述接口在 Cookie 失效时返回 **HTTP 200 + ``{"status_code": 8,
"status_msg": "用户未登录"}``**，不是 401。所以只看 HTTP 状态会漏判，必须看业务码。

为什么发布类接口不做本地签名（诚实缺口）
----------------------------------------
creator.douyin.com 的前端在启动时执行::

    Jl.setConfig({aid: 2906, scene: "web_protect", certType: "cookie",
                  consumerPathList: [...create, create_v2, draft, update_v2, delete...],
                  signVersion: 2})

其 ``consumerPathList`` 覆盖了发布 / 草稿 / 删除等接口；对应请求在业务代码里显式传
``needSign: true, withSecondVerify: true``。真正加签的是 secure-consumer-sdk：它劫持
``XMLHttpRequest.prototype.setRequestHeader``（见 ``runtime_bundler_*`` 里的
``XHR_REQUEST_SETQEQUESTHEADER`` 钩子），在浏览器内按**时间 + 请求体 + 设备/证书上下文**
现算并注入 ``bd-ticket-guard-*`` 系列请求头。这段逻辑与浏览器环境绑定、且随平台发版迭代，
用 Python 复刻只能短期可用，失效时最可能的表现是「返回 status_code 非 0」或静默风控。
因此这里**宁可抛 ``SignatureRequiredError``，也不返回空成功、更不伪造发布成功**。

视频上传的另一半（VOD）用的是标准 AWS4-HMAC-SHA256 签名（STS 临时凭证来自
``GET /web/api/media/upload/auth/v5/``），理论上纯标准库可实现；但 ``create_v2`` 这一
步拿不到签名，上传上去只会留下孤儿对象。所以 ``publish_video()`` 在**发出任何网络请求
之前**就抛错，避免产生无意义的副作用。

安全
----
本模块**不打印、不落盘任何 Cookie 值**；也不返回 ``upload/auth`` 里的 STS 凭证
（``AccessKeyID`` / ``SecretAccessKey`` / ``SessionToken``）—— ``upload_preflight()``
只回报「拿不拿得到凭证」这种布尔信息。诊断信息最多包含 Cookie 数量。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .errors import DouyinError, NotLoggedInError, SignatureRequiredError

logger = logging.getLogger(__name__)

# ─── 定位共享底座 .agents/lib/browserless ───────────────────────────────────


def _ensure_lib_path() -> None:
    """把仓库的 ``.agents/lib`` 加进 ``sys.path``，供 ``browserless`` 导入。

    不写死绝对路径：从本文件往上逐级找含 ``browserless/http.py`` 的目录，
    并允许 ``BROWSERLESS_LIB`` 环境变量覆盖（换仓库布局时能用上）。
    """
    candidates: list[Path] = []
    override = os.environ.get("BROWSERLESS_LIB")
    if override:
        candidates.append(Path(override))
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "lib")
        candidates.append(parent / ".agents" / "lib")
    for candidate in candidates:
        if (candidate / "browserless" / "http.py").is_file():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return
    raise DouyinError(
        "找不到 browserless 底座（期望 .agents/lib/browserless/http.py）。"
        "请检查仓库结构，或设置 BROWSERLESS_LIB 环境变量指向包含 browserless/ 的目录"
    )


_ensure_lib_path()

from browserless.chromium import CookieError
from browserless.http import (
    ApiError,
    AuthRequired,
    HttpError,
    Session,
    build_query,
)

# ─── 常量 ────────────────────────────────────────────────────────────────────

CREATOR_ORIGIN = "https://creator.douyin.com"

# Cookie 归属域（含子域）
COOKIE_DOMAINS = ["douyin.com"]

# 接口路径（全部从 creator.douyin.com 的前端 bundle 核实过，不是猜的）
USER_INFO_PATH = "/web/api/media/user/info/"
CREATOR_INFO_PATH = "/aweme/v1/creator/user/info/"
ITEM_LIST_PATH = "/web/api/creator/item/list"
UPLOAD_CONFIG_PATH = "/web/api/media/upload/config"
UPLOAD_AUTH_PATH = "/web/api/media/upload/auth/v5/"
AWEME_CREATE_V2_PATH = "/web/api/media/aweme/create_v2/"
AWEME_DRAFT_PATH = "/web/api/media/aweme/draft"

# 未登录时平台返回的业务码（HTTP 仍是 200）
STATUS_NOT_LOGGED_IN = 8

_SIGN_DETAIL = (
    "该请求在前端 bundle 里标记为 needSign=true，由 secure-consumer-sdk 在浏览器内"
    "现算 bd-ticket-guard-* 签名头（绑时间/请求体/证书上下文，随平台发版迭代）"
)


# ─── 异常 ────────────────────────────────────────────────────────────────────


class DouyinApiError(DouyinError):
    """服务端明确拒绝或返回不可解析的数据。"""


# ─── 客户端 ──────────────────────────────────────────────────────────────────


class DouyinApi:
    """无浏览器的抖音创作者客户端（只读能力 + 明确的签名缺口）。

    典型用法::

        api = DouyinApi()
        api.check_login()        # 未登录抛 NotLoggedInError
        api.list_works(count=10)

    ``browserless.chromium.CookieError`` 表示「技术上读不到浏览器 Cookie」，
    调用方应退回浏览器模式；``NotLoggedInError`` 表示「读到了但没登录」。
    """

    def __init__(self, browser: str = "chrome", *, min_interval: float = 0.35) -> None:
        self.browser = browser
        self.min_interval = min_interval
        self._session: Session | None = None
        self._jar: Any = None

    # ── 会话 ────────────────────────────────────────────────────────

    def _cookie_jar(self) -> Any:
        """读一次浏览器 Cookie 并缓存，避免每个请求都去解 SQLite。"""
        if self._jar is None:
            from browserless import chromium

            self._jar = chromium.load(self.browser, domains=COOKIE_DOMAINS)
            # 只报数量，绝不报值
            logger.debug("读到底座 Cookie：%s", self._jar.describe())
        return self._jar

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = Session(
                self._cookie_jar(),
                referer=f"{CREATOR_ORIGIN}/",
                min_interval=self.min_interval,
            )
        return self._session

    # ── 底层请求 ────────────────────────────────────────────────────

    def _check(self, payload: Any, path: str) -> dict:
        """校验业务码。

        平台把「未登录」放在业务码里（HTTP 仍是 200），所以必须自己检查：
        ``status_code == 8`` 一律转成 ``NotLoggedInError``，其余非 0 转成
        ``DouyinApiError`` 并把 ``status_msg`` 带出去。
        """
        if not isinstance(payload, dict):
            raise DouyinApiError(f"接口返回格式异常（不是 JSON 对象）：{path}")
        code = payload.get("status_code")
        if code == 0:
            return payload
        message = payload.get("status_msg") or payload.get("message") or "接口返回失败"
        if code == STATUS_NOT_LOGGED_IN:
            raise NotLoggedInError()
        raise DouyinApiError(
            f"{message}（{path}，status_code={code}）",
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """GET 一个 creator 接口并校验业务码。"""
        url = f"{CREATOR_ORIGIN}{path}"
        query = build_query(params)
        if query:
            url = f"{url}?{query}"
        try:
            payload = self.session.get_json(url)
        except ApiError as exc:
            if exc.status in (401, 403):
                raise NotLoggedInError() from exc
            raise DouyinApiError(
                f"请求失败：{exc}（{path}）",
            ) from exc
        return self._check(payload, path)

    # ── 登录 / 账号 ─────────────────────────────────────────────────

    def check_login(self) -> dict:
        """检查登录状态，返回昵称等账号摘要。

        未登录时抛 ``NotLoggedInError``（CLI 转成 exit 1）—— 这里不返回
        ``{"logged_in": False}``，是为了让「没登录」和「读到了但字段缺失」区分开。
        """
        user = self._user_info_raw()
        nickname = user.get("nickname") or ""
        if not nickname and not user.get("uid"):
            raise NotLoggedInError()
        result: dict = {
            "logged_in": True,
            "nickname": nickname,
            "uid": user.get("uid") or "",
            "sec_uid": user.get("sec_uid") or "",
            "short_id": user.get("short_id") or "",
            "follower_count": user.get("follower_count", 0),
            "aweme_count": user.get("aweme_count", 0),
            "mode": "api",
        }
        return result

    def _user_info_raw(self) -> dict:
        payload = self._get(USER_INFO_PATH)
        user = payload.get("user")
        if not isinstance(user, dict):
            raise DouyinApiError(f"接口没有返回 user 字段（{USER_INFO_PATH}）")
        return user

    def user_info(self) -> dict:
        """账号信息：昵称、抖音号、粉丝/关注/作品数、头像。

        刻意**不返回** ``bind_phone``（虽然有打码）等账号私密字段。
        """
        user = self._user_info_raw()
        avatars = user.get("avatar_larger") or {}
        url_list = avatars.get("url_list") if isinstance(avatars, dict) else None
        return {
            "nickname": user.get("nickname") or "",
            "uid": user.get("uid") or "",
            "sec_uid": user.get("sec_uid") or "",
            "short_id": user.get("short_id") or "",
            "unique_id": user.get("unique_id") or "",
            "signature": user.get("signature") or "",
            "follower_count": user.get("follower_count", 0),
            "following_count": user.get("following_count", 0),
            "aweme_count": user.get("aweme_count", 0),
            "total_favorited": user.get("total_favorited", "0"),
            "region": user.get("region") or "",
            "avatar": (url_list or [""])[0] if isinstance(url_list, list) and url_list else "",
            "mode": "api",
        }

    def creator_info(self) -> dict:
        """创作者信息：开通日期、实名状态、权限位。

        注意 ``user_profile.mobile`` 是打码手机号，这里**不返回**。
        """
        payload = self._get(CREATOR_INFO_PATH)
        profile = payload.get("user_profile") or {}
        verify = payload.get("douyin_user_verify_info") or {}
        permissions = payload.get("permissions") or []

        granted: list[str] = []
        if isinstance(permissions, list):
            for item in permissions:
                if isinstance(item, dict) and item.get("key") and item.get("status") == 1:
                    granted.append(str(item["key"]))

        return {
            "nickname": (profile.get("nick_name") or verify.get("nick_name") or ""),
            "unique_id": profile.get("unique_id") or "",
            "follower_count": profile.get("follower_count", 0),
            "following_count": profile.get("following_count", 0),
            "total_favorited": profile.get("total_favorited", 0),
            "region": profile.get("region") or "",
            "dashboard_opend_date": payload.get("dashboard_opend_date") or "",
            "is_registered": payload.get("is_registered"),
            "verify_status": verify.get("verify_status"),
            "permission_count": len(granted),
            "permissions": granted,
            "mode": "api",
        }

    # ── 作品列表 ────────────────────────────────────────────────────

    def list_works(
        self,
        count: int = 10,
        *,
        order_by: int = 0,
        max_cursor: int = 0,
    ) -> dict:
        """已发布作品列表（第一页 / 按游标翻页）。

        ``count`` 与 ``order_by`` 是**必传**的：实测只传其中一个（或都不传）都会得到
        ``status_code: 5「参数不合法」``。``order_by=0`` 为按发布时间倒序。
        """
        params: dict[str, Any] = {"count": count, "order_by": order_by}
        if max_cursor:
            params["max_cursor"] = max_cursor
        payload = self._get(ITEM_LIST_PATH, params)

        items = payload.get("items") or []
        works = [self._normalize_work(item) for item in items if isinstance(item, dict)]
        return {
            "total": payload.get("total", 0),
            "count": len(works),
            "has_more": bool(payload.get("has_more")),
            "max_cursor": payload.get("max_cursor", 0),
            "min_cursor": payload.get("min_cursor", 0),
            "items": works,
            "mode": "api",
        }

    @staticmethod
    def _normalize_work(item: dict) -> dict:
        """把平台返回的作品字段收敛成稳定的小集合。"""
        cover = item.get("cover") or {}
        urls = cover.get("url_list") if isinstance(cover, dict) else None
        video = item.get("video_info") or {}
        review = item.get("review") or {}
        return {
            "id": item.get("id") or "",
            "description": item.get("description") or "",
            "create_time": item.get("create_time", 0),
            "type": item.get("type"),
            "review_status": (review.get("status") if isinstance(review, dict) else None),
            "duration_ms": (video.get("duration") if isinstance(video, dict) else None),
            "cover": (urls or [""])[0] if isinstance(urls, list) and urls else "",
        }

    # ── 上传前置检查（只读，不产生任何副作用） ──────────────────────

    def upload_preflight(self) -> dict:
        """检查「上传所需的 STS 凭证能不能拿到」，只回报非敏感摘要。

        只是把上传链路的前半段验证到「凭证可用」这一步；**不上传任何文件**。
        之所以不继续往下做真正的 VOD 上传：``create_v2`` 那一步的签名拿不到，
        传上去只会留下孤儿对象（见模块 docstring）。
        """
        config = self._get(UPLOAD_CONFIG_PATH)
        video_config = config.get("video_config") or {}
        result: dict = {
            "host": video_config.get("host") or "",
            "region": video_config.get("region") or "",
            "has_fallback_host": bool(video_config.get("fallback_host")),
            "upload_auth_available": False,
            "mode": "api",
        }
        try:
            auth = self._get(UPLOAD_AUTH_PATH)
        except (DouyinApiError, NotLoggedInError) as exc:
            logger.debug("upload auth 不可用：%s", exc)
            return result

        # 只判断键在不在，绝不把凭证值带出这个函数
        result["upload_auth_available"] = bool(auth.get("auth")) and bool(auth.get("ak"))
        raw_auth = auth.get("auth")
        if isinstance(raw_auth, str):
            try:
                parsed = json.loads(raw_auth)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                result["upload_auth_expired_time"] = parsed.get("ExpiredTime") or 0
        return result

    # ── 发布：明确的签名缺口 ────────────────────────────────────────

    def publish_video(self, *_: Any, **__: Any) -> dict:
        """发布作品。需要浏览器内签名 → 明确失败。

        在**发出任何网络请求之前**抛错：既不尝试 create_v2（无法签名），
        也不先把视频传到 VOD（会留下孤儿对象）。绝不返回假的发布成功。
        """
        raise SignatureRequiredError(AWEME_CREATE_V2_PATH, _SIGN_DETAIL)

    def save_draft(self, *_: Any, **__: Any) -> dict:
        """保存草稿。同样标记 needSign → 明确失败。"""
        raise SignatureRequiredError(AWEME_DRAFT_PATH, _SIGN_DETAIL)

    def upload_video(self, *_: Any, **__: Any) -> dict:
        """上传视频到 VOD。属于发布链路的前半段 → 明确失败。

        单独暴露这个方法是刻意的：它能明确告诉调用方「缺的不是上传能力，而是
        create_v2 的签名」，而不是让人误以为「上传还没实现」。
        """
        raise SignatureRequiredError(
            "VOD ApplyUploadInner/CommitUploadInner",
            "上传本身可用 STS + AWS4 签名实现，但后续 create_v2 需要 bd-ticket-guard 签名，"
            "整条发布链路无法在无浏览器下完成，因此不发起上传以免留下孤儿对象",
        )


# ─── 便捷函数（给不想管理对象的调用方） ─────────────────────────────────────


def _call(action: str, browser: str = "chrome", **kwargs: Any) -> dict:
    api = DouyinApi(browser=browser)
    return getattr(api, action)(**kwargs)


def check_login(browser: str = "chrome") -> dict:
    """检查登录状态。"""
    return _call("check_login", browser)


def user_info(browser: str = "chrome") -> dict:
    """账号信息。"""
    return _call("user_info", browser)


def creator_info(browser: str = "chrome") -> dict:
    """创作者信息。"""
    return _call("creator_info", browser)


def list_works(count: int = 10, *, order_by: int = 0, browser: str = "chrome") -> dict:
    """已发布作品列表。"""
    return _call("list_works", browser, count=count, order_by=order_by)


def upload_preflight(browser: str = "chrome") -> dict:
    """上传前置检查（只读）。"""
    return _call("upload_preflight", browser)


# 供上层判断「这个异常要不要退回浏览器模式」
COOKIE_ERRORS = (CookieError,)

# 供上层把底座异常统一成业务异常
API_ERRORS: tuple[type[BaseException], ...] = (
    DouyinApiError,
    NotLoggedInError,
    HttpError,
    ApiError,
    AuthRequired,
)


def iter_paths() -> Iterable[str]:
    """本模块用到/记录的所有接口路径（测试用，防止有人随手改成猜的端点）。"""
    return (
        USER_INFO_PATH,
        CREATOR_INFO_PATH,
        ITEM_LIST_PATH,
        UPLOAD_CONFIG_PATH,
        UPLOAD_AUTH_PATH,
        AWEME_CREATE_V2_PATH,
        AWEME_DRAFT_PATH,
    )
