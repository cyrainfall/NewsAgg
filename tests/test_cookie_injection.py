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


if __name__ == "__main__":
    unittest.main()
