"""「今日额度已用」是闸门，不是故障——简报必须把它渲染成设计行为（ADR-0014/0059）。

为什么值得一个测试：2026-10-31 实测，`publish.py` 拒绝发布时打印
`{"ok": false, "stage": "daily_limit"}`，而编排器只看退出码、简报只看 `ok=false`，
于是这一轮被写成「**本轮运行未完成**……系统在运行途中失败了」，⑤ 段还附了
「修复后重跑：run_all.sh」。照那句话走只有两种结局——

1. 重跑：仍会被同一个闸拦下（当天额度已被用掉），白跑一轮；
2. 把闸门去掉（`--allow-daily-override`）：那就是**重复发文**，
   平台明令禁止，ADR-0059 已经为它付过一次代价。

这条链路上没有任何一处报错，错的只是「怎么称呼这次拒绝」。它属于本仓库
反复出现的那一类：**错的地方不报错，报错的地方说错原因**（ADR-0042/0047/0081）。
所以这里钉的是**渲染**：`daily_limit` 必须与真故障分开呈现，且不得出现
「修复后重跑」「运行未完成」这类会把人引向重发的措辞。

跑法：`python3 -m unittest discover`（仓库根）。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / ".agents" / "skills" / "newsagg-brief" / "scripts" / "brief.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief = _load("newsagg_brief_daily_limit", BRIEF)

DECISION = {
    "date": "2026-10-31",
    "decision": {
        "action": "continue",
        "card_file": "personal-ai-monetization-is-paid-demand-not-technology.md",
        "whats_new": "测试用",
        "confidence": 0.8,
        "money_point": "① 独立开发者——定价权在免费替代品手里",
        "event": {"summary": "测试事件", "members": [], "trend": "climbing"},
    },
}

DAILY_LIMIT_STATUS = {
    "ok": False,
    "date": "2026-10-31",
    "stage": "daily_limit",
    "reason": "今日额度已用（ADR-0014：每个日历日最多 1 篇），闸门按设计拒绝发布",
    "detail": "2026-10-31-ai-content-price-anchor-is-zero",
}

REAL_FAILURE_STATUS = {
    "ok": False,
    "date": "2026-10-31",
    "stage": "publish",
    "reason": "发布未成功（退出码 1）",
    "detail": "2026-10-31-ai-content-price-anchor-is-zero",
}


def conclusion(status) -> str:
    return "\n".join(brief.section_conclusion(DECISION, [], "2026-10-31",
                                              perf=None, run_status=status))


class DailyLimitIsAGateNotAFailureTest(unittest.TestCase):
    def test_conclusion_says_gate_not_failure(self):
        text = conclusion(DAILY_LIMIT_STATUS)
        self.assertIn("额度", text)
        self.assertIn("不是故障", text)
        # 关键否定：不能写成系统挂了
        self.assertNotIn("本轮运行未完成", text)
        self.assertNotIn("系统在运行途中失败了", text)

    def test_conclusion_warns_against_override(self):
        """必须明确禁止 override——那是唯一会造成重复发文的操作。"""
        text = conclusion(DAILY_LIMIT_STATUS)
        self.assertIn("--allow-daily-override", text)
        self.assertIn("重复发文", text)

    def test_real_failure_still_reported_as_failure(self):
        """反例：真故障不能被这条特判吞掉。"""
        text = conclusion(REAL_FAILURE_STATUS)
        self.assertIn("本轮运行未完成", text)
        self.assertNotIn("不是故障", text)


class DailyLimitInSectionFiveTest(unittest.TestCase):
    def _section_five(self, status) -> str:
        return "\n".join(brief.section_actions([], None, DECISION, None, None,
                                               None, run_status=status,
                                               date="2026-10-31", root=ROOT))

    def test_no_rerun_advice(self):
        text = self._section_five(DAILY_LIMIT_STATUS)
        self.assertIn("额度", text)
        self.assertNotIn("修复后重跑", text)
        self.assertIn("--allow-daily-override", text)

    def test_real_failure_keeps_rerun_advice(self):
        text = self._section_five(REAL_FAILURE_STATUS)
        self.assertIn("修复后重跑", text)


if __name__ == "__main__":
    unittest.main()
