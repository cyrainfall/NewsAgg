# -*- coding: utf-8 -*-
"""部署级渠道策略（ADR-0078）。

## 这个问题是什么

渠道可用性不是代码属性，是**部署属性**。同一份代码：
- 在本机（Clash Verge 代理在跑）→ Jina Reader 能用；
- 在服务器（不装代理，用户 2026-10-27 定案）→ `r.jina.ai` 直连不通。

把这件事写进代码，等于把「本机的网络」硬编码进整个系统；写进部署策略
文件，就是「部署时声明一次，运行时读」。

## 为什么要单独报，而不是让 doctor 自己说

ADR-0059 残余风险 53 记的就是这件事：Jina Reader 在服务器上**带着绿色状态
失效**——体检说 ok，实际上取不到正文。

`doctor` 只能回答「这个渠道此刻能不能连上」，回答不了「我们这次部署**决定**
不用它」。这两件事必须分开说：
- 主动关掉的 → 读者需要知道今天的采集面少了一块、少的那块怎么补。
- 它自己坏了 → 需要人介入。

混在一起会犯两个错：把「我们关的」说成「它坏了」，人去查一个根本没坏的
东西；把「它坏了」混进「我们关的」，人以为已经处理过了。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".agents" / "skills" / "newsagg-brief" / "scripts" / "source_health.py"


def _load():
    spec = importlib.util.spec_from_file_location("source_health_mod", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["source_health_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


sh = _load()


class PolicyFileTest(unittest.TestCase):
    def test_shipped_policy_declares_no_disabled_channel(self):
        """仓里那份策略：当前部署（本机）没有关掉任何渠道（ADR-0079）。

        这条断言的方向是被有意反转的。ADR-0078 时它断言「web 必须被关掉」——
        因为当时唯一的生产环境是没装代理的服务器。服务器退出生产角色后
        （2026-10-28），本机有代理、Jina 可用，若还留着那条 disabled，
        简报会天天报一个并不存在的降级。
        """
        policy = sh.read_policy(REPO)
        disabled = {name: entry for name, entry in policy.items()
                    if entry.get("status") == "disabled"}
        self.assertEqual({}, disabled,
                         "当前部署没有禁用渠道；若真要禁用，必须写实测理由（ADR-0078）")

    def test_missing_policy_file_is_not_an_error(self):
        """本机没有 deploy/channel-policy.txt 是正常的（文件在仓库里，
        但换一台机器部署时可能没有）→ 空策略，不报错。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual({}, sh.read_policy(Path(tmp)))

    def test_comments_and_blank_lines_ignored(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy").mkdir()
            (root / "deploy" / "channel-policy.txt").write_text(
                "# 注释\n\n  \nfoo | disabled | 因为实测不通\nbar | enabled | 正常\n",
                encoding="utf-8",
            )
            policy = sh.read_policy(root)
            self.assertEqual({"foo": {"status": "disabled", "reason": "因为实测不通"},
                              "bar": {"status": "enabled", "reason": "正常"}}, policy)

    def test_malformed_line_is_skipped_not_fatal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy").mkdir()
            (root / "deploy" / "channel-policy.txt").write_text(
                "只有一列\nx | disabled | 理由\n", encoding="utf-8")
            policy = sh.read_policy(root)
            self.assertEqual(["x"], list(policy.keys()))


class RenderDistinguishesDisabledFromBrokenTest(unittest.TestCase):
    """两种「不可用」必须分开说。"""

    def test_disabled_is_reported_even_when_everything_else_is_ok(self):
        health = {
            "status": "ok",
            "channels": {"web": {"status": "disabled", "tier": 0, "name": "web",
                                 "message": "服务器上没有代理"}},
            "tier0_ok": 5, "tier0_total": 5,
            "policy_disabled": {"web": "服务器上没有代理"},
            "reason": "",
        }
        out = "\n".join(sh.render(health))
        self.assertIn("主动关掉", out)
        self.assertIn("web", out)
        self.assertIn("不是故障", out)
        self.assertNotIn("信源渠道失效", out, "主动关掉的渠道不该被说成「失效」")

    def test_broken_channel_is_reported_as_failure(self):
        health = {
            "status": "degraded",
            "channels": {"github": {"status": "off", "tier": 0, "name": "github",
                                    "message": "gh 未授权"}},
            "tier0_ok": 5, "tier0_total": 6,
            "policy_disabled": {},
            "reason": "",
        }
        out = "\n".join(sh.render(health))
        self.assertIn("信源渠道失效", out)
        self.assertNotIn("主动关掉", out)

    def test_both_can_appear_together(self):
        health = {
            "status": "degraded",
            "channels": {
                "web": {"status": "disabled", "tier": 0, "name": "web", "message": "没代理"},
                "github": {"status": "off", "tier": 0, "name": "github", "message": "gh 未授权"},
            },
            "tier0_ok": 4, "tier0_total": 5,
            "policy_disabled": {"web": "没代理"},
            "reason": "",
        }
        out = "\n".join(sh.render(health))
        self.assertIn("主动关掉", out)
        self.assertIn("信源渠道失效", out)


class DisabledChannelLeavesTheDenominatorTest(unittest.TestCase):
    """被声明关掉的渠道不计入可用率的分母。

    否则每天简报都挂着一个已知项，久而久之整段被无视
    （与 ADR-0021 边界二同一个理由：第 ⑤ 段是「需你介入的事项」）。
    """

    def test_denominator_excludes_disabled(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy").mkdir()
            (root / "deploy" / "channel-policy.txt").write_text(
                "web | disabled | 没代理\n", encoding="utf-8")
            policy = sh.read_policy(root)
            disabled = {n for n, e in policy.items() if e["status"] == "disabled"}
            effective = [k for k in sh.TIER0 if k not in disabled]
            self.assertNotIn("web", effective)
            self.assertEqual(len(sh.TIER0) - 1, len(effective))


if __name__ == "__main__":
    unittest.main()
