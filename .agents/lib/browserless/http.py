"""平台无关的 HTTP 客户端：Cookie 注入、JSON 往返、重试、错误归一。

用 `urllib` 而不是 `requests`：四个技能里只有两个声明了 requests 依赖，而底座要
被所有技能共用，不能反过来给没装 requests 的技能加依赖。标准库够用 —— 这些接口
都是普通 JSON over HTTPS，没有流式、没有连接池压力。

## 这里集中处理的四件事

1. **Cookie 从哪来**：`Session.from_browser()` 一次读盘，之后整个进程复用。
   不打开浏览器、不连 CDP。
2. **浏览器指纹**：平台会看 UA / Referer / `Origin`。默认伪造一个与自己 Chrome
   同版本的 UA —— 注意这是**一致性**问题不是伪装问题：UA 说 Chrome 131 而 TLS
   指纹是 urllib，本来就不完全一致；但至少不要出现「UA 是 2019 年的旧版本还带
   现代 Cookie」这种明显矛盾。
3. **失败分类**：网络错误、HTTP 错误、业务 `code != 0` 三种要分开。混在一起会让
   上层无法判断该重试、该退浏览器、还是该告诉用户「没登录」。
4. **限速**：平台对短时间批量请求敏感。默认每次请求之间留间隔。
"""

from __future__ import annotations

import json
import os
import random
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .chromium import CookieJar

DEFAULT_TIMEOUT = 30.0

# 与真实 Chrome 一致的默认 UA。版本号写死不用随机 —— 随机反而更容易被判定异常，
# 因为同一个会话里 UA 变了比 UA 旧更可疑。
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class HttpError(RuntimeError):
    """网络层失败（连不上、超时、TLS）。可以重试。"""

    def __init__(self, message: str, *, url: str = "", status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


class ApiError(RuntimeError):
    """服务端明确返回了错误（HTTP 4xx/5xx 或业务 code 非 0）。

    不需要重试 —— 重试同一个被拒绝的请求只会浪费配额。
    """

    def __init__(self, message: str, *, code: Any = None, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.body = body


class AuthRequired(ApiError):
    """服务端说「你没登录」。

    单独一个类型，因为它的处置方式与别的错误不同：应当提示用户去浏览器登录，
    而不是重试、也不是换参数。
    """


@dataclass
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            snippet = self.body[:200].decode("utf-8", errors="replace")
            raise ApiError(f"响应不是合法 JSON（{exc}）：{snippet}", status=self.status) from exc

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


_WINDOWS_EXPOSURE_SIDS = (
    ("S-1-1-0", "Everyone"),
    ("S-1-5-32-545", "BUILTIN\\Users"),
    ("S-1-5-11", "Authenticated Users"),
)


def _check_cookie_file_exposure(p: Path, st: os.stat_result) -> None:
    """cookie 文件的「别的用户能不能读」闸。按平台分叉，判据都是真实系统状态。

    ## POSIX

    `mode & 0o077` 必须为 0。防的是一次 `chmod 644` 手滑。

    ## Windows

    NTFS 没有 POSIX 位：`os.stat()` 对任何普通文件都返回 `0o666`，拿 mode 判
    等于「永远拒绝」。真实的判据是 DACL，而 `icacls` 的中文名会随系统语言变
    （Everyone / 所有人 / Jeder），所以按 **SID** 查、不按名字查：

        icacls <file> /findsid S-1-1-0

    退出码 0 = 找到该条目。找到任意一个「非本机管理员组」的宽口径主体就拒绝。

    ## 判不出来就拒绝（fail closed）

    `icacls` 不存在、超时、返回码既不是 0 也不是 1332 —— 一律拒绝并说清原因。
    这里宁可挡住一次正常使用，也不能在「不知道谁能读」的情况下把会话凭证
    加载进来；那是把「我不知道」写成「应该没事」。
    """
    if os.name != "nt":
        if st.st_mode & 0o077:
            raise CookieError(
                f"cookie 文件 {p} 权限太宽（mode={oct(st.st_mode & 0o777)}）："
                f"组/其他用户可读就等于把账号交出去。"
                f"改成 `chmod 600 {p}` 再试。"
            )
        return

    for sid, label in _WINDOWS_EXPOSURE_SIDS:
        try:
            proc = subprocess.run(
                ["icacls", str(p), "/findsid", sid],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CookieError(
                f"cookie 文件 {p} 的 ACL 查不出来（icacls 调用失败：{exc}）。"
                f"Windows 上没有 mode 位可判，查不出来就按「可能泄露」处理。"
            ) from exc
        if proc.returncode == 0:
            raise CookieError(
                f"cookie 文件 {p} 的 ACL 太宽：`{label}`（{sid}）能读。"
                f"收干净再试：\n"
                f'    icacls "{p}" /inheritance:r /grant:r "%USERNAME%":R'
            )
        if proc.returncode != 1332:  # 1332 = 没找到该 SID，是正常结果
            raise CookieError(
                f"cookie 文件 {p} 的 ACL 查不出来：icacls 返回 {proc.returncode}，"
                f"既不是「找到」也不是「没找到」。按「可能泄露」处理。"
            )


class Session:
    """带 Cookie 的 HTTP 会话。

    典型用法：

        jar = chromium.load("chrome", domains=["bilibili.com"])
        s = Session(jar, referer="https://member.bilibili.com/")
        data = s.get_json("https://api.bilibili.com/x/web-interface/nav")
    """

    def __init__(
        self,
        jar: CookieJar,
        *,
        user_agent: str = DEFAULT_UA,
        referer: str | None = None,
        origin: str | None = None,
        min_interval: float = 0.35,
        retries: int = 2,
        verify_tls: bool = True,
    ):
        self.jar = jar
        self.user_agent = user_agent
        self.referer = referer
        self.origin = origin
        self.min_interval = min_interval
        self.retries = retries
        self._last_call = 0.0
        self._ctx = None if verify_tls else ssl._create_unverified_context()

    # ── 构造 ────────────────────────────────────────────────────────

    @classmethod
    def from_browser(
        cls, browser: str, domains: list[str], **kwargs: Any
    ) -> "Session":
        """从本机浏览器读 Cookie 建会话。这是推荐入口 —— 不需要浏览器在运行。"""
        from . import chromium

        jar = chromium.load(browser, domains=domains)
        return cls(jar, **kwargs)

    @classmethod
    def from_cookie_file(cls, path: str | Path, **kwargs: Any) -> "Session":
        """从**注入的 cookie 文件**建会话。没有浏览器的环境（服务器）用它。

        文件格式：JSON `{"cookies": [{"domain","name","value","path","secure",
        "http_only"}]}`（`browserless.chromium.Cookie.to_dict()` 的形状）。

        ## 权限检查为什么在这一层

        这个文件是**账号凭证**。它的产生方式注定它会被人 `scp` 上服务器、
        或者塞进 gitignored 的配置目录。组或其他用户可读 = 账号被交出去。

        所以这里硬性要求：文件模式不能有组可读/组可写/其他任何权限
        （即 `mode & 0o077` 必须为 0），否则拒绝加载并说清怎么改。
        这不是「安全最佳实践」的泛泛建议——它防的是一次 `chmod 644` 手滑。

        Windows 上这条判据**恒假也恒真**：NTFS 没有 POSIX 位，`os.stat()` 对
        任何普通文件都返回 `0o666`，于是 `0o666 & 0o077 == 0o066` 恒为真——
        所有 cookie 文件都会被拒，包括 ACL 已经收干净的那些。所以按平台分叉：
        POSIX 看 mode 位，Windows 看 DACL（见 `_check_cookie_file_exposure`）。

        ## 为什么不提供「从环境变量读」

        环境变量会进 `ps e`、进 shell history、进 systemd unit 文件。
        文件 + 0600 权限 + gitignore 是更可控的一条路。
        """
        from .chromium import Cookie, CookieJar, CookieError

        p = Path(path)
        try:
            st = p.stat()
        except OSError as exc:
            raise CookieError(f"读不到 cookie 文件 {p}：{exc}") from exc

        _check_cookie_file_exposure(p, st)
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CookieError(f"cookie 文件 {p} 读不出来：{exc}") from exc

        raw = payload.get("cookies") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise CookieError(
                f"cookie 文件 {p} 格式不对：期望 "
                f'{{"cookies": [{{"domain","name","value",...}}]}}，实得 {type(raw).__name__}'
            )
        if not raw:
            raise CookieError(f"cookie 文件 {p} 里没有一条可用的 cookie")

        cookies: list[Cookie] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            if not name or not value:
                continue
            cookies.append(Cookie(
                domain=str(item.get("domain") or ""),
                name=name,
                value=value,
                path=str(item.get("path") or "/"),
                secure=bool(item.get("secure")),
                http_only=bool(item.get("http_only")),
                # 导出文件里的 `expires_at` 是 Unix 秒（ADR-0071 之后才有的
                # 字段），这里换回 Cookie 内部用的 WebKit 微秒，保持同一套
                # 表示。旧文件没有这个字段 → 0 = 会话 cookie。
                expires_utc=(int(item.get("expires_at") or 0) + 11644473600) * 1_000_000
                if item.get("expires_at") else 0,
            ))
        if not cookies:
            raise CookieError(f"cookie 文件 {p} 里没有一条可用的 cookie")
        return cls(CookieJar(cookies=cookies), **kwargs)

    # ── 请求 ────────────────────────────────────────────────────────

    def _headers(self, url: str, extra: Mapping[str, str] | None) -> dict[str, str]:
        host = urllib.parse.urlsplit(url).hostname or ""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
        }
        cookie = self.jar.header(host)
        if cookie:
            headers["Cookie"] = cookie
        if self.referer:
            headers["Referer"] = self.referer
        if self.origin:
            headers["Origin"] = self.origin
        if extra:
            headers.update(extra)
        return headers

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        delta = time.monotonic() - self._last_call
        if delta < self.min_interval:
            # 加一点抖动，避免固定节奏被识别成脚本
            time.sleep(self.min_interval - delta + random.uniform(0, 0.08))
        self._last_call = time.monotonic()

    def request(
        self,
        method: str,
        url: str,
        *,
        data: Any = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        accept_status: tuple[int, ...] = (200,),
    ) -> Response:
        """发一个请求，返回 `Response`。

        `accept_status` 之外的 HTTP 状态一律抛 `ApiError`；网络层失败抛 `HttpError`
        并按 `retries` 重试（指数退避）。
        """
        extra = dict(headers or {})
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            extra.setdefault("Content-Type", "application/json; charset=UTF-8")
        elif data is not None:
            if isinstance(data, (bytes, bytearray)):
                body = bytes(data)
            elif isinstance(data, str):
                body = data.encode("utf-8")
            else:
                body = urllib.parse.urlencode(data).encode("utf-8")
                extra.setdefault("Content-Type", "application/x-www-form-urlencoded")

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            req = urllib.request.Request(
                url, data=body, headers=self._headers(url, extra), method=method.upper()
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as resp:
                    payload = resp.read()
                    status = resp.status
                    resp_headers = dict(resp.headers.items())
            except urllib.error.HTTPError as exc:
                payload = exc.read()
                if exc.code in accept_status:
                    return Response(exc.code, payload, dict(exc.headers.items()), url)
                # 5xx 值得重试；4xx 是客户端问题，重试无意义
                if exc.code >= 500 and attempt < self.retries:
                    last_error = HttpError(f"HTTP {exc.code}", url=url, status=exc.code)
                    time.sleep(0.6 * (2**attempt))
                    continue
                raise ApiError(
                    f"HTTP {exc.code}：{payload[:300].decode('utf-8', errors='replace')}",
                    code=exc.code,
                    status=exc.code,
                    body=payload,
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = HttpError(f"网络错误：{exc}", url=url)
                if attempt < self.retries:
                    time.sleep(0.6 * (2**attempt))
                    continue
                raise last_error from exc
            return Response(status, payload, resp_headers, url)

        raise last_error or HttpError("请求失败且无具体错误", url=url)

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def post_json(self, url: str, **kwargs: Any) -> Any:
        return self.post(url, **kwargs).json()


def quote(value: str) -> str:
    """URL 查询参数编码（空格编码成 %20 而不是 +，平台接口对此敏感）。"""
    return urllib.parse.quote(value, safe="")


def build_query(params: Mapping[str, Any] | None) -> str:
    """把 dict 拼成 query string，跳过 None 值，bool 转成 1/0（平台都这么要）。"""
    if not params:
        return ""
    parts = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = 1 if value else 0
        parts.append(f"{quote(str(key))}={quote(str(value))}")
    return "&".join(parts)
