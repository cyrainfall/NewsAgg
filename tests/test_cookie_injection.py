"""cookie 注入路径的测试——服务器上唯一可用的入口。

## 为什么要有这个文件

`browserless` 与 `toutiao-ops` 是**两套独立的登录态**：

- `from_browser()` 读本机 Chrome 的 cookie 库（macOS 走 Keychain 取口令）。
- toutiao-ops 的浏览器后端读 Playwright 自己的 profile
  （`.home/.toutiao-ops/accounts/default/browser-data`），而那个 profile 的
  `Local State` 里 `os_crypt` 是**空 dict**，48 条 toutiao cookie 用任何
  口令都解出非 ASCII 垃圾。

服务器上没装 Chrome，`from_browser()` 直接抛
`CookieError: 找不到浏览器 chrome 的 profile`。→ 必须注入。

## 这个文件不联网

真实验证是本机跑（2026-10-27）：
`python3 scripts/export-cookies.py --browser chrome --out /tmp/c.json` →
`python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py
--cookie-file /tmp/c.json login` → `logged_in: true`，`list` 拿到 16 条。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / ".agents" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# browserless 内部用相对导入（`from .chromium import ...`），必须当包导入，
# 不能用 spec_from_file_location 单独加载一个子模块。
from browserless.http import Session  # noqa: E402


class TestCookieFileLoading(unittest.TestCase):
    """`Session.from_cookie_file` 的行为。"""

    def _write(self, payload, mode=0o600) -> Path:
        fd, name = tempfile.mkstemp(prefix="cookie-", suffix=".json")
        os.close(fd)
        p = Path(name)
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(p, mode)
        return p

    def test_loads_cookies_from_expected_shape(self):
        p = self._write({"cookies": [
            {"domain": ".toutiao.com", "name": "sessionid", "value": "abc",
             "path": "/", "secure": True, "http_only": True},
        ]})
        try:
            sess = Session.from_cookie_file(p)
            self.assertEqual(sess.jar.header("www.toutiao.com"), "sessionid=abc")
        finally:
            p.unlink()

    def test_rejects_group_or_other_readable_file(self):
        """权限太宽 = 把账号交出去。必须拒绝，且说清怎么改。"""
        p = self._write({"cookies": [
            {"domain": ".toutiao.com", "name": "sessionid", "value": "abc"},
        ]}, mode=0o644)
        try:
            with self.assertRaises(Exception) as ctx:
                Session.from_cookie_file(p)
            msg = str(ctx.exception)
            self.assertIn("chmod 600", msg)
            self.assertIn("权限太宽", msg)
        finally:
            p.unlink()

    def test_rejects_file_readable_by_group_only(self):
        """0o640 也是「别人能读」。只测 0o644 会漏掉这一类。"""
        p = self._write({"cookies": [
            {"domain": ".toutiao.com", "name": "sessionid", "value": "abc"},
        ]}, mode=0o640)
        try:
            with self.assertRaises(Exception):
                Session.from_cookie_file(p)
        finally:
            p.unlink()

    def test_rejects_missing_file(self):
        with self.assertRaises(Exception) as ctx:
            Session.from_cookie_file(Path("/nonexistent/cookie.json"))
        self.assertIn("读不到", str(ctx.exception))

    def test_rejects_malformed_json(self):
        fd, name = tempfile.mkstemp(prefix="cookie-", suffix=".json")
        os.close(fd)
        p = Path(name)
        p.write_text("{not json", encoding="utf-8")
        os.chmod(p, 0o600)
        try:
            with self.assertRaises(Exception) as ctx:
                Session.from_cookie_file(p)
            self.assertIn("读不出来", str(ctx.exception))
        finally:
            p.unlink()

    def test_rejects_wrong_shape(self):
        """形状不对要说清楚要什么，而不是静默加载 0 条。"""
        p = self._write({"cookies": "not-a-list"})
        try:
            with self.assertRaises(Exception) as ctx:
                Session.from_cookie_file(p)
            self.assertIn("格式不对", str(ctx.exception))
        finally:
            p.unlink()

    def test_rejects_empty_cookie_list(self):
        p = self._write({"cookies": []})
        try:
            with self.assertRaises(Exception) as ctx:
                Session.from_cookie_file(p)
            self.assertIn("没有一条", str(ctx.exception))
        finally:
            p.unlink()

    def test_skips_entries_without_name_or_value(self):
        p = self._write({"cookies": [
            {"domain": ".toutiao.com", "name": "", "value": "x"},
            {"domain": ".toutiao.com", "name": "y", "value": ""},
            {"domain": ".toutiao.com", "name": "sessionid", "value": "abc"},
        ]})
        try:
            sess = Session.from_cookie_file(p)
            self.assertEqual(sess.jar.header("www.toutiao.com"), "sessionid=abc")
        finally:
            p.unlink()


class TestExportScript(unittest.TestCase):
    """导出脚本不联网，但它的安全行为可以离线验。"""

    def test_script_exists_and_is_executable(self):
        p = REPO / "scripts" / "export-cookies.py"
        self.assertTrue(p.exists(), "缺少 scripts/export-cookies.py")
        self.assertTrue(os.access(p, os.X_OK), "导出脚本应可执行")

    def test_written_file_is_0600(self):
        """脚本写出的文件必须是 0600——这是它自己承诺的。"""
        p = REPO / "scripts" / "export-cookies.py"
        src = p.read_text(encoding="utf-8")
        self.assertIn("os.chmod(out, 0o600)", src)

    def test_never_prints_cookie_values(self):
        """导出脚本只报条数与域，绝不打印值。"""
        p = REPO / "scripts" / "export-cookies.py"
        src = p.read_text(encoding="utf-8")
        for bad in ("print(c[\"value\"])", "print(cookie[\"value\"])"):
            self.assertNotIn(bad, src)


class TestPerformanceCookieFileEnv(unittest.TestCase):
    """`performance.py` 必须能通过环境变量用注入的 cookie。"""

    def test_reads_newsagg_cookie_file_env(self):
        src = (REPO / ".agents/skills/newsagg-performance/scripts/performance.py").read_text(
            encoding="utf-8")
        self.assertIn("NEWSAGG_COOKIE_FILE", src)
        # 优先级：环境变量 > 本机 Chrome
        self.assertLess(src.index("NEWSAGG_COOKIE_FILE"),
                        src.index("Client.from_browser()"))


class TestCookieExpiry(unittest.TestCase):
    """cookie 的过期时间必须能一路带到服务器上（ADR-0071 残余风险 69）。

    ## 背景

    Chrome 把 cookie 的过期时间存成 **WebKit 纪元（1601-01-01）起的微秒数**。
    直接 `datetime.fromtimestamp(那个数)` 会得到「年份 425885148」——
    这个荒谬的报错正是发现单位不对的契机。

    更严重的是：`Cookie.to_dict()` **原本完全不导出过期时间**，于是导出的
    cookie 文件不带任何有效期信息，服务器上无从知道它什么时候过期。
    后果是 cookie 过期只有症状没有预告：症状是「纯 HTTP 全部 401、浏览器
    后端还好」，那个现象看起来像代码坏了，不像凭证过期。

    所以这一组测试钉住三件事：单位换算对、导出带得上、注入读得回。
    """

    def test_webkit_epoch_converts_to_unix_seconds(self):
        from browserless.chromium import Cookie
        # 2026-11-19 00:00:00 UTC 的 WebKit 微秒数
        target_unix = 1794432000
        webkit = (target_unix + 11644473600) * 1_000_000
        c = Cookie(domain=".toutiao.com", name="sessionid", value="x",
                   expires_utc=webkit)
        self.assertAlmostEqual(c.expires_at, target_unix, delta=1)

    def test_session_cookie_has_no_expiry(self):
        from browserless.chromium import Cookie
        c = Cookie(domain=".toutiao.com", name="x", value="y", expires_utc=0)
        self.assertEqual(c.expires_at, 0.0)

    def test_to_dict_carries_expiry(self):
        from browserless.chromium import Cookie
        target_unix = 1794432000
        webkit = (target_unix + 11644473600) * 1_000_000
        d = Cookie(domain=".toutiao.com", name="sessionid", value="x",
                   expires_utc=webkit).to_dict()
        self.assertIn("expires_at", d)
        self.assertEqual(d["expires_at"], target_unix)

    def test_expiry_survives_export_import_roundtrip(self):
        """导出 → 注入回来，过期时间必须一样。"""
        from browserless.chromium import Cookie
        target_unix = 1794432000
        webkit = (target_unix + 11644473600) * 1_000_000
        original = Cookie(domain=".toutiao.com", name="sessionid", value="abc",
                           path="/", secure=True, http_only=True,
                           expires_utc=webkit)
        fd, name = tempfile.mkstemp(prefix="cookie-", suffix=".json")
        os.close(fd)
        p = Path(name)
        p.write_text(json.dumps({"cookies": [original.to_dict()]}), encoding="utf-8")
        os.chmod(p, 0o600)
        try:
            sess = Session.from_cookie_file(p)
            got = sess.jar.get("sessionid", domain="toutiao.com")
            self.assertEqual(got, "abc")
            restored = sess.jar.cookies[0]
            self.assertAlmostEqual(restored.expires_at, target_unix, delta=1)
        finally:
            p.unlink()

    def test_old_file_without_expiry_still_loads(self):
        """旧格式（没有 expires_at）不能因此加载失败——向后兼容。"""
        fd, name = tempfile.mkstemp(prefix="cookie-", suffix=".json")
        os.close(fd)
        p = Path(name)
        p.write_text(json.dumps({"cookies": [
            {"domain": ".toutiao.com", "name": "sessionid", "value": "abc"},
        ]}), encoding="utf-8")
        os.chmod(p, 0o600)
        try:
            sess = Session.from_cookie_file(p)
            self.assertEqual(sess.jar.header("www.toutiao.com"), "sessionid=abc")
            self.assertEqual(sess.jar.cookies[0].expires_utc, 0)
        finally:
            p.unlink()


class TestCookieExpiryWarning(unittest.TestCase):
    """表现层要能提前说出「cookie 快过期了」（ADR-0071 残余风险 69）。"""

    def _perf(self):
        import importlib.util
        p = REPO / ".agents/skills/newsagg-performance/scripts/performance.py"
        spec = importlib.util.spec_from_file_location("perf_mod_expiry", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _write(self, payload) -> Path:
        fd, name = tempfile.mkstemp(prefix="cookie-", suffix=".json")
        os.close(fd)
        p = Path(name)
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(p, 0o600)
        return p

    def test_no_cookie_file_means_no_warning(self):
        """本机直读 Chrome 的场景不该被提醒——那里没有「注入文件」可过期。"""
        mod = self._perf()
        old = os.environ.pop("NEWSAGG_COOKIE_FILE", None)
        try:
            self.assertEqual(mod.cookie_expiry_warning(), {})
        finally:
            if old is not None:
                os.environ["NEWSAGG_COOKIE_FILE"] = old

    def test_expiring_cookie_is_flagged(self):
        import time
        mod = self._perf()
        p = self._write({"earliest_expires_at": int(time.time()) + 3 * 86400,
                         "cookies": []})
        old = os.environ.get("NEWSAGG_COOKIE_FILE")
        os.environ["NEWSAGG_COOKIE_FILE"] = str(p)
        try:
            info = mod.cookie_expiry_warning()
            self.assertEqual(info["level"], "expiring")
            self.assertLess(info["days_left"], 7)
            self.assertIn("重新导出", info["message"])
        finally:
            if old is None:
                os.environ.pop("NEWSAGG_COOKIE_FILE", None)
            else:
                os.environ["NEWSAGG_COOKIE_FILE"] = old
            p.unlink()

    def test_expired_cookie_is_flagged(self):
        import time
        mod = self._perf()
        p = self._write({"earliest_expires_at": int(time.time()) - 86400,
                         "cookies": []})
        old = os.environ.get("NEWSAGG_COOKIE_FILE")
        os.environ["NEWSAGG_COOKIE_FILE"] = str(p)
        try:
            info = mod.cookie_expiry_warning()
            self.assertEqual(info["level"], "expired")
            self.assertIn("过期", info["message"])
        finally:
            if old is None:
                os.environ.pop("NEWSAGG_COOKIE_FILE", None)
            else:
                os.environ["NEWSAGG_COOKIE_FILE"] = old
            p.unlink()

    def test_fresh_cookie_is_not_flagged(self):
        import time
        mod = self._perf()
        p = self._write({"earliest_expires_at": int(time.time()) + 30 * 86400,
                         "cookies": []})
        old = os.environ.get("NEWSAGG_COOKIE_FILE")
        os.environ["NEWSAGG_COOKIE_FILE"] = str(p)
        try:
            self.assertEqual(mod.cookie_expiry_warning()["level"], "ok")
        finally:
            if old is None:
                os.environ.pop("NEWSAGG_COOKIE_FILE", None)
            else:
                os.environ["NEWSAGG_COOKIE_FILE"] = old
            p.unlink()

    def test_brief_renders_the_warning(self):
        """简报第⑤段必须把这件事说出来——不然等于没提醒。"""
        import importlib.util
        p = REPO / ".agents/skills/newsagg-brief/scripts/brief.py"
        spec = importlib.util.spec_from_file_location("brief_mod_expiry", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lines = mod.section_actions(
            [], {}, {}, None, None,
            perf={"status": {"ok": True, "cookie_expiry": {
                "level": "expiring", "days_left": 3.0,
                "message": "注入的 cookie 还有 3 天过期（2026-10-30）。"}}},
        )
        text = "\n".join(lines)
        self.assertIn("cookie", text)
        self.assertIn("快过期", text)
        self.assertIn("export-cookies.py", text)


if __name__ == "__main__":
    unittest.main()
