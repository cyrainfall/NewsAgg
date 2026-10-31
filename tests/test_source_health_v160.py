"""信源健康：v1.6.0 的两条输出形状（ADR-0089）。

ADR-0021 的「活跃依赖、不钉版本」在 2026-10-31 兑现了：装上真身 v1.6.0 之后，
`doctor --json` 这个参数没了，`doctor` 也变成了纯本地状态（不走网络），真正实测
连通的是 `check`；渠道表里 `rss` / `exa_search` 两个名字也不存在了。

这里的夹具全部取自**实测输出**（2026-10-31 本机），不是照着文档编的：
两条形状的字段名（`results[].passed/backend/error`、`result.channels[].status/problems/hint`）
只要上游再改一次，这组测试就会先红——那正是我们要的报警时机。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "newsagg-brief" / "scripts" / "source_health.py"

spec = importlib.util.spec_from_file_location("newsagg_source_health_v160", SCRIPT)
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)


def _check_payload(rows: list[dict]) -> dict:
    return {"schema_version": 1, "ok": True, "scope": "all", "results": rows}


def _row(channel: str, action: str, backend: str, passed: bool, code: str = "") -> dict:
    return {
        "channel": channel, "action": action, "backend": backend,
        "passed": passed, "verified": passed, "elapsed_ms": 10,
        "error": None if passed else {"code": code or "TIMEOUT", "message": "upstream did not answer in time"},
        "item_count": 1 if passed else 0,
    }


class CheckPayloadTest(unittest.TestCase):
    """`check` 的一个渠道会有多个 action（`web` 的 search 走 exa、read 走 jina/exa）。"""

    def test_all_actions_pass_is_ok(self):
        ch = sh._channels_from_check(_check_payload([
            _row("github", "search", "github-api", True),
            _row("github", "read", "github-api", True),
        ]), {})
        self.assertEqual("ok", ch["github"]["status"])

    def test_partial_pass_is_warn_not_ok_and_not_off(self):
        """部分通过是真实存在的一档：还能用，但有后端在退。"""
        ch = sh._channels_from_check(_check_payload([
            _row("web", "search", "exa", True),
            _row("web", "read", "jina", False, "TIMEOUT"),
            _row("web", "read", "exa", True),
        ]), {})
        self.assertEqual("warn", ch["web"]["status"])
        self.assertIn("read/jina", ch["web"]["message"])
        self.assertIn("TIMEOUT", ch["web"]["message"])

    def test_all_fail_is_off(self):
        ch = sh._channels_from_check(_check_payload([
            _row("v2ex", "hot", "v2ex-api", False, "UPSTREAM_BROKEN"),
        ]), {})
        self.assertEqual("off", ch["v2ex"]["status"])

    def test_disabled_channels_are_skipped(self):
        ch = sh._channels_from_check(_check_payload([
            _row("web", "search", "exa", True),
        ]), {"web": "服务器上没有代理"})
        self.assertNotIn("web", ch)


class DoctorPayloadTest(unittest.TestCase):
    """`doctor` 是本地状态（no network），只做兜底，且**只取 tier-0**。"""

    def _payload(self) -> dict:
        return {
            "version": "1.6.0",
            "channels": [
                {"channel": "github", "level": "default", "status": "unverified",
                 "problems": [], "hint": "agent-reach check github", "regressed": False},
                {"channel": "youtube", "level": "default", "status": "needs_setup",
                 "problems": ["ffmpeg not installed: run `agent-reach add youtube`"], "regressed": False},
                {"channel": "web", "level": "default", "status": "ok",
                 "problems": [], "hint": "", "regressed": False},
                {"channel": "twitter", "level": "experimental", "status": "not_added",
                 "problems": [], "hint": "agent-reach add twitter", "regressed": False},
            ],
            "tools": {},
        }

    def test_only_tier0_is_kept(self):
        ch = sh._channels_from_doctor(self._payload(), {})
        self.assertEqual({"github", "youtube", "web"}, set(ch))
        self.assertNotIn("twitter", ch, "tier-1/2 的「未安装」不该进第 ⑤ 段")

    def test_status_mapping(self):
        ch = sh._channels_from_doctor(self._payload(), {})
        self.assertEqual("ok", ch["web"]["status"])
        self.assertEqual("warn", ch["youtube"]["status"])
        self.assertIn("ffmpeg", ch["youtube"]["message"])
        # 「从没实测过」不能算健康：它的字面意思是「我们不知道」（同 ADR-0084）
        self.assertEqual("warn", ch["github"]["status"])

    def test_empty_message_still_says_something(self):
        payload = {"channels": [{"channel": "web", "status": "unverified", "problems": [], "hint": ""}]}
        ch = sh._channels_from_doctor(payload, {})
        self.assertTrue(ch["web"]["message"], "无说明时也要写清「未实测」，不能留空")


class VerdictTest(unittest.TestCase):
    def test_ok_requires_every_tier0_channel_ok(self):
        channels = {k: {"status": "ok", "name": k} for k in sh.TIER0}
        v = sh._verdict(channels, {}, source="check")
        self.assertEqual("ok", v["status"])
        self.assertEqual(len(sh.TIER0), v["tier0_ok"])

    def test_missing_channel_is_not_reported_as_broken(self):
        """上游改名后，「查无此渠道」与「渠道坏了」是两件事（ADR-0089）。"""
        channels = {k: {"status": "ok", "name": k} for k in sh.TIER0 if k != "youtube"}
        v = sh._verdict(channels, {}, source="check")
        self.assertEqual(["youtube"], v["missing_channels"])
        self.assertEqual("degraded", v["status"])
        out = "\n".join(sh.render(v))
        self.assertIn("上游已不存在", out)
        self.assertIn("youtube", out)

    def test_disabled_leaves_the_denominator(self):
        channels = {k: {"status": "ok", "name": k} for k in sh.TIER0 if k != "web"}
        v = sh._verdict(channels, {"web": "服务器上没有代理"}, source="check")
        self.assertEqual(len(sh.TIER0) - 1, v["tier0_total"])
        self.assertEqual("ok", v["status"])

    def test_doctor_only_observation_is_labelled(self):
        channels = {"web": {"status": "warn", "name": "web", "message": "本地状态 unverified（未实测）"}}
        v = sh._verdict(channels, {}, source="doctor")
        out = "\n".join(sh.render(v))
        self.assertIn("本地状态", out)
        self.assertIn("不是「此刻连不连得上」", out)


class ExecutableLookupTest(unittest.TestCase):
    """Windows 上 uv tool 把 console script 装进 `Scripts/`，不是 POSIX 的 `bin/`。"""

    def test_finds_windows_scripts_layout(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / ".uv-tools" / "agent-reach" / "Scripts" / "agent-reach.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
            found = sh.agent_reach_cmd(root)
            self.assertIsNotNone(found)
            self.assertEqual(str(exe), found[0])

    def test_missing_everything_returns_none(self):
        """没有任何候选路径、且 PATH 里也没有时返回 None。

        这里必须把 `shutil.which` 打掉：本机装过之后 uv 会在 `~/.local/bin` 放一个
        shim（`agent-reach.EXE`），而那个目录在 PATH 上——不打掉的话，这条测试
        测的是「这台机器装没装」，不是「找不到时会不会返回 None」。
        """
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("shutil.which", return_value=None):
            self.assertIsNone(sh.agent_reach_cmd(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
