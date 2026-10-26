"""跨平台读取本机 Chromium 系浏览器的 Cookie。

这是「不打开浏览器」的入口：业务脚本不需要驱动浏览器，只需要拿到用户**已经登录**
产生的那几个 Cookie，然后在协议层直接发 HTTP 请求。

## 为什么读磁盘而不是连 CDP

kd-devops 的做法给出了答案：认证态从本地缓存复用，浏览器只在需要取证时才出场。
连 CDP 要求浏览器正在运行且开了 `--remote-debugging-port`；读磁盘则不需要浏览器
在运行，也不需要它配合。代价是要自己处理三套加密：macOS Keychain、Linux
libsecret、Windows DPAPI —— 就是本模块。

## 安全边界（强制）

- 本模块**只读**浏览器数据，绝不写入、删除、修改。
- 取到的 Cookie 只存在于进程内存，不落盘、不打印。`describe()` 只输出
  「哪个域有几个 Cookie、关键 Cookie 在不在」，不输出值。
- 复制出的临时 SQLite 文件在 `close()` 时删除，用完即焚。

## 跨平台支持矩阵

| 平台 | 密钥来源 | 加密 | 状态 |
|---|---|---|---|
| macOS | `security` 命令取 Keychain 的 `<Browser> Safe Storage` | AES-128-CBC | 实测可用 |
| Linux | `secret-tool`（gnome-keyring）/ 回落固定口令 | AES-128-CBC | 依赖桌面环境 |
| Windows | DPAPI 解 `Local State` 的 `os_crypt.encrypted_key` | AES-256-GCM | 未在真机验证 |

> Windows 分支按 Chromium 源码与公开文档实现，但本仓库的开发与运行环境是
> macOS/Linux，所以它被标注为「未在真机验证」。调用方应把解密失败当成
> 「退回浏览器模式」的信号，而不是崩溃。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import crypto

# Chromium 的 PBKDF2 参数。macOS 上是 1003 轮，Linux 是 1 轮 —— 这不是笔误，
# 是两边 Chromium 的历史实现不同，写错会得到「能解密但全是乱码」。
_MAC_ITERATIONS = 1003
_LINUX_ITERATIONS = 1
_SALT = b"saltysalt"

# 各家浏览器的 User Data 目录名（跨平台通用的那一层）
_BROWSER_DIRS = {
    "chrome": {
        "darwin": "Google/Chrome",
        "linux": "google-chrome",
        "win32": "Google/Chrome/User Data",
    },
    "chromium": {"darwin": "Chromium", "linux": "chromium", "win32": "Chromium/User Data"},
    "edge": {
        "darwin": "Microsoft Edge",
        "linux": "microsoft-edge",
        "win32": "Microsoft/Edge/User Data",
    },
    "brave": {
        "darwin": "BraveSoftware/Brave-Browser",
        "linux": "BraveSoftware/Brave-Browser",
        "win32": "BraveSoftware/Brave-Browser/User Data",
    },
}

# Keychain / libsecret 里的服务名
_KEYRING_SERVICE = {
    "chrome": "Chrome Safe Storage",
    "chromium": "Chromium Safe Storage",
    "edge": "Microsoft Edge Safe Storage",
    "brave": "Brave Safe Storage",
}
_SECRET_TOOL_APP = {
    "chrome": "chrome",
    "chromium": "chromium",
    "edge": "microsoft-edge",
    "brave": "brave",
}


class CookieError(RuntimeError):
    """读不到浏览器 Cookie 时的统一异常。

    调用方（各技能的 API 客户端）应当捕获它并退回浏览器模式，
    而不是把它当成业务失败 —— 「读不到本地 Cookie」和「用户没登录」
    是两件事，混在一起会让用户看到错误的提示。
    """


@dataclass
class Cookie:
    domain: str
    name: str
    value: str
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    expires_utc: int = 0
    same_site: int = 0

    @property
    def domain_key(self) -> str:
        """去掉前导点，便于做后缀匹配。"""
        return self.domain.lstrip(".")

    @property
    def expires_at(self) -> float:
        """过期时刻（Unix 秒）。0 = 会话 cookie（浏览器退出即失效）。

        ## 为什么要做这个换算

        Chrome 的 cookie 库把过期时间存成 **WebKit 纪元（1601-01-01）
        起的微秒数**（`expires_utc` 列）。直接 `datetime.fromtimestamp(那个数)`
        会得到「年份 425885148」这种荒谬结果——第一次尝试时就是这个报错
        才让我发现单位不对。

        换算：Unix 纪元与 WebKit 纪元相差 11644473600 秒。

        之前 `to_dict()` **完全没有导出这个字段**，于是导出的 cookie 文件
        不带任何有效期信息——「这份 cookie 什么时候过期」在服务器上无从
        得知，ADR-0071 残余风险 69 说的「每月提醒续期」也就无从实现。
        """
        if not self.expires_utc:
            return 0.0
        return self.expires_utc / 1_000_000.0 - 11644473600.0

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "name": self.name,
            "value": self.value,
            "path": self.path,
            "secure": self.secure,
            "http_only": self.http_only,
            # 0 = 会话 cookie（没有明确过期时间）
            "expires_at": int(self.expires_at),
        }


@dataclass
class CookieJar:
    """一批 Cookie 的内存视图。

    `__repr__` 刻意不打印值 —— 这些是账号凭证，任何日志、异常回溯、
    agent 的调试输出都可能被记录或回显。
    """

    cookies: list[Cookie] = field(default_factory=list)
    browser: str = ""
    profile: str = ""

    def __repr__(self) -> str:
        domains = sorted({c.domain_key for c in self.cookies})
        return f"CookieJar(browser={self.browser!r}, profile={self.profile!r}, " f"domains={domains}, n={len(self.cookies)})"

    def get(self, name: str, domain: str | None = None) -> str | None:
        """按名字取 Cookie 值；给了 domain 就只在该域及其子域里找。"""
        for c in self.cookies:
            if c.name != name:
                continue
            if domain is None or c.domain_key == domain or c.domain_key.endswith("." + domain):
                return c.value
        return None

    def for_domain(self, domain: str) -> "CookieJar":
        """筛出适用于某个域的 Cookie（含父域）。"""
        keep = [c for c in self.cookies if _domain_matches(c, domain)]
        return CookieJar(cookies=keep, browser=self.browser, profile=self.profile)

    def header(self, domain: str | None = None) -> str:
        """拼成 HTTP `Cookie:` 头的值。"""
        jar = self.for_domain(domain) if domain else self
        return "; ".join(f"{c.name}={c.value}" for c in jar.cookies)

    def describe(self, keys: list[str] | None = None) -> dict:
        """不含敏感值的摘要，可以安全打印 / 进 JSON 输出。"""
        domains = sorted({c.domain_key for c in self.cookies})
        out = {
            "browser": self.browser,
            "profile": self.profile,
            "domains": domains,
            "count": len(self.cookies),
        }
        if keys:
            present = {k: any(c.name == k for c in self.cookies) for k in keys}
            out["keys_present"] = present
        return out


def _domain_matches(cookie: Cookie, domain: str) -> bool:
    """RFC 6265 的域匹配：精确相等，或 cookie 域是请求域的后缀。"""
    cd = cookie.domain_key.lower()
    d = domain.lower()
    if cd == d:
        return True
    return cookie.domain.startswith(".") and d.endswith("." + cd)


# ── 路径探测 ────────────────────────────────────────────────────────────


def _user_data_roots(browser: str) -> list[Path]:
    """列出某浏览器可能的 User Data 根目录（按平台）。"""
    sysname = "win32" if sys.platform.startswith("win") else sys.platform
    name = _BROWSER_DIRS.get(browser, {}).get(sysname)
    if not name:
        return []

    home = Path.home()
    if sysname == "darwin":
        return [home / "Library/Application Support" / name]
    if sysname == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData/Local"))
        return [local / name]
    # linux
    roots = [home / ".config" / name]
    if os.environ.get("SNAP"):
        pass
    return roots


def _cookie_dbs(profile_dir: Path) -> list[Path]:
    """Cookie 数据库在 profile 目录下的候选位置。

    新版 Chromium 挪进了 `Network/`，旧版在 profile 根。两个都看，
    因为用户机器上的版本不可控。
    """
    return [p for p in (profile_dir / "Network/Cookies", profile_dir / "Cookies") if p.is_file()]


def list_profiles(browser: str = "chrome") -> list[tuple[str, Path]]:
    """返回 [(profile 名, profile 目录)]，只列出确实有 Cookie 库的。"""
    found: list[tuple[str, Path]] = []
    for root in _user_data_roots(browser):
        if not root.is_dir():
            continue
        candidates = [root / "Default"] + sorted(
            p for p in root.glob("Profile *") if p.is_dir()
        )
        for prof in candidates:
            if prof.is_dir() and _cookie_dbs(prof):
                found.append((prof.name, prof))
    return found


def _default_profile(browser: str) -> tuple[str, Path] | None:
    """按 `Local State` 里记录的「最后使用」profile 挑一个，找不到就 Default。"""
    for root in _user_data_roots(browser):
        state = root / "Local State"
        if state.is_file():
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
                info = data.get("profile", {}).get("info_cache", {})
                if info:
                    # 优先 last_used，其次字母序第一个
                    key = data.get("profile", {}).get("last_used") or sorted(info)[0]
                    prof = root / key
                    if prof.is_dir() and _cookie_dbs(prof):
                        return (key, prof)
            except (OSError, ValueError):
                pass
    profiles = list_profiles(browser)
    if not profiles:
        return None
    for name, prof in profiles:
        if name == "Default":
            return (name, prof)
    return profiles[0]


# ── 密钥派生 ────────────────────────────────────────────────────────────


def _macos_key(browser: str) -> bytes:
    """从 Keychain 取口令并派生 AES-128 密钥。

    不走 `security find-generic-password -g`（它把口令写到 stderr，格式难解），
    用 `-w` 直接拿明文。
    """
    service = _KEYRING_SERVICE.get(browser, "Chrome Safe Storage")
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CookieError(f"调用 security 失败：{exc}") from exc
    if proc.returncode != 0:
        raise CookieError(
            f"取不到 Keychain 条目「{service}」"
            f"（{proc.stderr.strip() or '可能需要授予钥匙串访问权限'}）"
        )
    password = proc.stdout.strip()
    if not password:
        raise CookieError(f"Keychain 条目「{service}」为空")
    return hashlib.pbkdf2_hmac("sha1", password.encode(), _SALT, _MAC_ITERATIONS, 16)


def _linux_key(browser: str) -> bytes:
    """Linux：先问 secret-tool，再回落固定口令。

    Chromium 在 Linux 上有三种密码存储后端：
      - gnome-libsecret / kwallet：真实口令，PBKDF2 **1 轮**
      - basic：口令写死为 "peanuts"，也是 1 轮
    所以两种后端派生参数一样，只是口令不同。
    """
    app = _SECRET_TOOL_APP.get(browser, "chrome")
    password = ""
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "application", app],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            password = proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        password = ""
    if not password:
        # basic 后端。这条路必须能用，否则服务器/容器里必然失败。
        password = "peanuts"
    return hashlib.pbkdf2_hmac("sha1", password.encode(), _SALT, _LINUX_ITERATIONS, 16)


def _windows_key(browser: str) -> bytes:
    """Windows：DPAPI 解 Local State 里的 encrypted_key，得到 32 字节 AES-256 密钥。

    用 ctypes 调 CryptUnprotectData，不依赖 pywin32。
    解出来是「DPAPI」5 字节魔数 + 实际密钥。
    """
    if not sys.platform.startswith("win"):
        raise CookieError("DPAPI 只在 Windows 上可用")
    root = None
    for candidate in _user_data_roots(browser):
        if (candidate / "Local State").is_file():
            root = candidate
            break
    if root is None:
        raise CookieError("找不到浏览器 Local State 文件")
    state = json.loads((root / "Local State").read_text(encoding="utf-8"))
    enc_b64 = state.get("os_crypt", {}).get("encrypted_key")
    if not enc_b64:
        raise CookieError("Local State 里没有 os_crypt.encrypted_key")

    blob = base64.b64decode(enc_b64)
    if blob.startswith(b"DPAPI"):
        blob = blob[5:]

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(_blob(blob)), None, None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise CookieError("DPAPI 解密失败（可能不是同一用户会话）")
    raw = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)  # type: ignore[attr-defined]
    if len(raw) != 32:
        raise CookieError(f"DPAPI 解出的密钥长度异常：{len(raw)}")
    return raw


def _derive_key(browser: str) -> tuple[bytes, str]:
    """返回 (密钥, 加密模式标记)。模式决定后面怎么解 Cookie。"""
    if sys.platform == "darwin":
        return _macos_key(browser), "cbc"
    if sys.platform.startswith("win"):
        return _windows_key(browser), "gcm"
    return _linux_key(browser), "cbc"


# ── Cookie 解密 ─────────────────────────────────────────────────────────


def _unpad(raw: bytes) -> bytes:
    """去掉 PKCS#7 填充。

    Chromium 的 v10 Cookie 用标准 PKCS#7（末字节 N 表示末尾有 N 个 N）。
    这里容忍「末字节不是合法填充长度」的情况并原样返回：宁可让上层的
    UTF-8 解码去暴露问题，也不要在解密层猜。

    历史坑：早期实现按「空格填充」rstrip(b" ")，在 openssl 子进程里因为
    openssl 自己会去填充而看不出问题，换成纯 Python 解密后 Cookie 值尾部
    全是 \\x10 之类的控制字符 —— 直接导致 HTTP 头非法。所以填充必须显式处理。
    """
    if not raw:
        return raw
    pad = raw[-1]
    if 1 <= pad <= 16 and raw.endswith(bytes([pad]) * pad):
        return raw[:-pad]
    return raw


def _decrypt_value(blob: bytes, key: bytes, mode: str, host: str) -> str:
    """把 `encrypted_value` 解成明文。

    Chromium 的几个坑，逐条处理：
      1. 空值直接返回空串。
      2. 前缀 `v10`/`v11` 表示「已加密」；`v20` 是 Chrome 127+ 在部分平台启用的
         App-Bound 加密，需要额外绑定浏览器进程，本模块解不了 —— 明确报错而不是
         返回乱码，让调用方退回浏览器模式。
      3. Chrome 130+ 会在明文前再插 32 字节 `sha256(host_key)` 做域绑定校验，
         必须按长度前缀识别并剥掉，否则 Cookie 值会带一串二进制垃圾。
      4. Linux 的 basic 后端有时直接存明文（没有 v10 前缀）。
    """
    if not blob:
        return ""

    if blob[:3] in (b"v10", b"v11"):
        body = blob[3:]
        if mode == "gcm":
            if len(body) < 12 + 16:
                return ""
            plain = crypto.aes_gcm_decrypt(key, body[:12], body[12:-16], body[-16:])
        else:
            if len(body) % 16:
                # 长度不对说明这不是我们能解的东西
                return ""
            raw = crypto.aes_cbc_decrypt(key, b" " * 16, body)
            plain = _unpad(raw)
    elif blob[:3] == b"v20":
        raise CookieError(
            "Cookie 使用了 v20（App-Bound Encryption）格式，本模块无法解密。"
            "这是 Chrome 127+ 在部分平台的默认行为，请改用浏览器模式，"
            "或在浏览器里关闭 App-Bound Encryption 后重试。"
        )
    else:
        # 未加密（Linux basic 后端的旧数据），原样返回
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            return ""

    # 域绑定前缀：32 字节 sha256(host_key)
    if len(plain) > 32:
        expect = hashlib.sha256(host.encode()).digest()
        if plain[:32] == expect:
            plain = plain[32:]

    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain.decode("utf-8", errors="replace")


def load(
    browser: str = "chrome",
    profile: str | None = None,
    *,
    domains: list[str] | None = None,
) -> CookieJar:
    """读取 Cookie。

    参数：
      browser: chrome / chromium / edge / brave
      profile: profile 目录名（如 "Default"、"Profile 1"）；None 则自动选最后使用的
      domains: 只要这些域（含子域）的 Cookie；None 表示全要

    抛 `CookieError` 表示「技术上读不到」，不含「用户没登录」的语义。
    """
    if profile:
        found = dict(list_profiles(browser))
        prof_dir = found.get(profile)
        if prof_dir is None:
            raise CookieError(f"浏览器 {browser} 下找不到 profile「{profile}」")
        prof_name = profile
    else:
        picked = _default_profile(browser)
        if picked is None:
            raise CookieError(f"找不到浏览器 {browser} 的 profile（未安装或未使用过）")
        prof_name, prof_dir = picked

    db = _cookie_dbs(prof_dir)[0]

    # 复制到临时文件再读：浏览器运行时 SQLite 会被锁，直接连可能 database is locked。
    # 用 copy 而不是 `immutable=1`，因为 immutable 会忽略 WAL 里的最新写入。
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="browserless-cookies-", suffix=".db")
    os.close(tmp_fd)
    try:
        shutil.copyfile(db, tmp_path)
        # WAL 存在时一并复制，否则可能丢最近的登录态
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.is_file():
                shutil.copyfile(side, tmp_path + suffix)

        key, mode = _derive_key(browser)
        con = sqlite3.connect(tmp_path)
        try:
            rows = con.execute(
                "SELECT host_key, name, encrypted_value, value, path, "
                "is_secure, is_httponly, expires_utc, samesite FROM cookies"
            ).fetchall()
        finally:
            con.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp_path + suffix)
            except OSError:
                pass

    cookies: list[Cookie] = []
    for host, name, enc, plain, path, secure, http_only, expires, samesite in rows:
        if domains and not any(_domain_matches(Cookie(host, name, ""), d) for d in domains):
            continue
        if plain:
            value = plain
        else:
            try:
                value = _decrypt_value(enc or b"", key, mode, host)
            except CookieError:
                raise
            except Exception:  # 单条解不开不该毁掉整批
                value = ""
        if not value:
            continue
        cookies.append(
            Cookie(
                domain=host,
                name=name,
                value=value,
                path=path or "/",
                secure=bool(secure),
                http_only=bool(http_only),
                expires_utc=expires or 0,
                same_site=samesite or 0,
            )
        )

    return CookieJar(cookies=cookies, browser=browser, profile=prof_name)


def probe() -> dict:
    """体检：这台机器上哪个浏览器、哪个 profile 能读到 Cookie。

    给 `--doctor` 用。**不返回任何 Cookie 值** —— 只报数量与关键项是否在。
    """
    report: dict = {"platform": sys.platform, "python": platform.python_version(), "browsers": {}}
    for browser in _BROWSER_DIRS:
        entry: dict = {"installed": False, "profiles": [], "error": None}
        try:
            profiles = list_profiles(browser)
            entry["installed"] = bool(profiles)
            entry["profiles"] = [name for name, _ in profiles]
            if profiles:
                jar = load(browser)
                entry["readable"] = True
                entry["cookie_count"] = len(jar.cookies)
                entry["domains"] = sorted({c.domain_key for c in jar.cookies})
        except CookieError as exc:
            entry["readable"] = False
            entry["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - 体检要吞掉一切异常
            entry["readable"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        report["browsers"][browser] = entry
    return report


def main(argv: list[str] | None = None) -> int:
    """`python -m browserless.chromium` 的体检入口。"""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in ("probe", "--doctor", "-h", "--help"):
        print(f"未知子命令：{argv[0]}", file=sys.stderr)
        return 2
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
