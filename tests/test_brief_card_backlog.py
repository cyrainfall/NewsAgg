"""建卡台账必须有消费者：闸门、输入包、简报三处都要读它（ADR-0083）。

为什么值得一个测试：13 轮 0 张卡的根因是「事实被丢掉了」——`no_card_available`
每轮打印完就消失，没有任何地方记着「这个议题我见过」。这次改动把那个事实落成
`.newsagg/triage/card-backlog.json`，并且要求它在三个地方被读：

- 闸门（`triage.validate_decision`）：到期议题必须建卡或写理由 → `test_new_card_trigger.py`
- 输入包（`triage.build_triage_input`）：分拣要知道哪些议题欠着 → `test_new_card_trigger.py`
- 简报（`brief.py`）：**人**要知道哪些议题欠着 → 本文件

第三处最容易退化。`title_tags` 写进账本三天没任何代码读它（ADR-0082 六节），
`merge_suggestions` / `retire_suggestions` 两个键挂在简报渲染里但从来没有生产方
（ADR-0083 一并接上了退役那一半）。「记录下来」被当成「已经做完」是本仓的
重复失败形态，所以回声也要钉住。

另有一条纪律必须测：**没有台账时什么都不印**。编一个「第 1 次」会让
「没见过」和「第一次见到」看起来一样，而这两件事在决策上是相反的。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / ".agents" / "skills" / "newsagg-brief" / "scripts" / "brief.py"
TRIAGE_SCRIPTS = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts"
CARDS = ROOT / "knowledge" / "cards"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief = _load("newsagg_brief_backlog", BRIEF)
if str(TRIAGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(TRIAGE_SCRIPTS))
import card_backlog  # noqa: E402


def backlog(issue="AI 顶尖人才的定价", times=2, days=None,
            card_file=None, reason=None) -> dict:
    """造一份台账视图（输入包里的那段形状，不是文件本身）。"""
    use_days = days or [f"2026-10-{27 + i:02d}" for i in range(times)]
    e = card_backlog._entry(issue, use_days[0])
    for d in use_days:
        card_backlog._touch(e, d, f"{issue} 的一个事件")
    e["card_file"] = card_file
    if reason:
        e["reasons"][use_days[-1]] = reason
    return {
        "threshold": card_backlog.TIMES_SEEN_THRESHOLD,
        "review_days": card_backlog.REVIEW_DAYS,
        "due": [issue] if times >= 2 and not card_file else [],
        "open_issues": [{"issue": issue, "times_seen": times}],
        "issues": {card_backlog.norm_issue(issue): e},
    }


class BacklogEchoTest(unittest.TestCase):
    """② 段：每个「无卡可接」条目下面要接一行台账回声。"""

    def test_first_time_says_so_without_the_threshold_line(self):
        line = brief._backlog_echo({"issue": "AI 顶尖人才的定价"},
                                   backlog(times=1))
        self.assertIn("第 1 次出现", line)
        self.assertNotIn("已达建卡门槛", line,
                         "第一次出现不该威胁「不建就要给理由」")

    def test_second_time_says_so_and_flags_the_threshold(self):
        line = brief._backlog_echo({"issue": "AI 顶尖人才的定价"}, backlog(times=2))
        self.assertIn("第 2 次出现", line)
        self.assertIn("已达建卡门槛", line)
        self.assertIn("no_card_reason", line)

    def test_last_reason_is_surfaced(self):
        """「上一轮为什么不建」是这一轮最该读的一句话。不印出来就会被无理由重复。"""
        line = brief._backlog_echo(
            {"issue": "AI 顶尖人才的定价"},
            backlog(times=2, reason="两个数据点都来自同一家媒体的转载"))
        self.assertIn("上次", line)
        self.assertIn("同一家媒体的转载", line)

    def test_punctuation_variant_matches_same_issue(self):
        """议题名带不带引号，必须指向同一条台账。"""
        line = brief._backlog_echo({"issue": "「AI 顶尖人才的定价」"}, backlog(times=2))
        self.assertIn("第 2 次出现", line)

    def test_attached_card_says_attached_not_threshold(self):
        line = brief._backlog_echo(
            {"issue": "AI 顶尖人才的定价"},
            backlog(times=3, card_file="ai-talent-pricing.md"))
        self.assertIn("已挂卡", line)
        self.assertNotIn("已达建卡门槛", line)

    def test_no_backlog_prints_nothing(self):
        """没有台账 → 空串。**不编「第 1 次」**：「没见过」和「第一次」不是一回事。"""
        self.assertEqual(brief._backlog_echo({"issue": "AI 顶尖人才的定价"}, None), "")

    def test_unknown_issue_prints_nothing(self):
        self.assertEqual(
            brief._backlog_echo({"issue": "从没见过的议题"}, backlog(times=2)), "")

    def test_entry_without_issue_prints_nothing(self):
        """老决策（`issue` 字段出现之前）里的条目不该凭空回声。"""
        self.assertEqual(brief._backlog_echo({"event": "某事件"}, backlog(times=2)), "")

    def test_echo_reaches_the_overview_section(self):
        """接线：② 段渲染里真的带上了回声（不是只有函数能跑）。"""
        decision = {
            "action": "continue", "card_file": "x.md", "confidence": 0.85,
            "money_point": "① 接外包的前端",
            "event": {"summary": "某模型发布"},
            "no_card_available": [{"issue": "AI 顶尖人才的定价",
                                   "event": "17 岁 AI 研究员获 700 万年薪",
                                   "why": "单一极端个案"}],
        }
        out = "\n".join(brief.section_overview(
            {"clusters": [], "candidates": []}, decision,
            trend_summary=None, backlog=backlog(times=2)))
        self.assertIn("AI 顶尖人才的定价", out)
        self.assertIn("第 2 次出现", out)


    def test_threshold_comes_from_data_not_hardcoded(self):
        """门槛数字必须读台账里的 `threshold`，不能写死 2。

        `TIMES_SEEN_THRESHOLD` 是 ADR-0083 明确要按实测分布调整的一个常量
        （未做项第一条）。若回声里写死「第 2 次起算」，改常量那天回声会继续
        说 2——判据变了而提示没变，比没有提示更坏。
        """
        b = backlog(times=3)
        b["threshold"] = 3
        self.assertIn("第 3 次起算", brief._backlog_echo({"issue": "AI 顶尖人才的定价"}, b))

        b2 = backlog(times=2)          # times=2 < threshold=3
        b2["threshold"] = 3
        b2["due"] = []
        line = brief._backlog_echo({"issue": "AI 顶尖人才的定价"}, b2)
        self.assertNotIn("已达建卡门槛", line, "阈值 3 时第 2 次不该报门槛")


class CardStockLinesTest(unittest.TestCase):
    """③ 资产段：到期议题与退役候选。"""

    def test_retire_line_reads_review_days_from_module(self):
        """退役天数同理：写死 30 会让措辞与判据分家。"""
        line = "\n".join(brief._card_stock_lines(CARDS, None, "2026-11-20"))
        self.assertIn(f"建卡满 {card_backlog.REVIEW_DAYS} 天", line)

    def test_due_issue_is_listed(self):
        lines = brief._card_stock_lines(CARDS, backlog(times=2), "2026-10-28")
        joined = "\n".join(lines)
        self.assertIn("到期未建卡", joined)
        self.assertIn("AI 顶尖人才的定价", joined)
        self.assertIn("第 2 次", joined)

    def test_no_backlog_prints_no_due_section(self):
        """老日期重放简报时没有台账，不该凭空印一个「无到期」——那是编的。"""
        lines = "\n".join(brief._card_stock_lines(CARDS, None, "2026-10-28"))
        self.assertNotIn("到期未建卡", lines)

    def test_backlog_with_nothing_due_says_checked(self):
        """一行「无到期」比整段消失有用：它证明这一轮真的查过。"""
        lines = "\n".join(brief._card_stock_lines(
            CARDS, {"threshold": 2, "issues": {"某议题": {
                "issue": "某议题", "times_seen": 1, "card_file": None}}}, "2026-10-28"))
        self.assertIn("无到期未建卡", lines)

    def test_retire_candidate_needs_review_window(self):
        """建满 30 天且从未成文 → 候选。当天不算（刚建就判死是荒谬的）。"""
        # `ai-tool-benefits-overestimated-by-demo-effect.md` 的 created 是 2026-10-14
        # 且 last_written 为空——它是真实样本，不是造出来的。
        early = "\n".join(brief._card_stock_lines(CARDS, None, "2026-10-28"))
        self.assertNotIn("退役候选", early, "才 14 天，不该出现")

        late = "\n".join(brief._card_stock_lines(CARDS, None, "2026-11-20"))
        self.assertIn("退役候选", late)
        self.assertIn("ai-tool-benefits-overestimated-by-demo-effect.md", late)
        self.assertIn("机器不自动退役", late, "候选不是执行——ADR-0007 要求人否决")

    def test_written_card_is_not_a_retire_candidate(self):
        """已经成过文的卡不进候选：`last_written` 非空就是证据。"""
        late = "\n".join(brief._card_stock_lines(CARDS, None, "2026-11-20"))
        self.assertNotIn("ai-coding-workflow-over-model-capability.md", late)


class NormIssueIsSingleImplementationTest(unittest.TestCase):
    """归一化只能有一份实现（ADR-0042）。

    简报回声与 triage 计数必须用同一把尺子。两份漂移的后果不是崩溃，
    是回声指向另一条台账——显示「第 3 次」而实际记在别的键下，比不显示更坏。
    """

    def test_brief_and_backlog_agree_on_keys(self):
        samples = ["AI 顶尖人才的定价", "「AI 顶尖人才的定价」", "AI 顶尖人才的定价。",
                   "  ai 顶尖人才的定价  ", "AI-顶尖人才的定价"]
        for s in samples:
            self.assertEqual(brief._norm_issue(s), card_backlog.norm_issue(s), s)

    def test_different_issues_stay_different(self):
        self.assertNotEqual(brief._norm_issue("AI 人才的定价"),
                            brief._norm_issue("AI 编码的工作流集成"))


class SectionAssetsWiringTest(unittest.TestCase):
    def test_card_stock_reaches_section_assets(self):
        """接线：③ 段真的调用了体检线，而不是只定义了一个函数。"""
        decision = {
            "action": "continue", "card_file": "ai-coding-workflow-over-model-capability.md",
            "confidence": 0.85, "money_point": "① 接外包的前端",
            "event": {"summary": "某模型发布"},
        }
        out = "\n".join(brief.section_assets(
            decision, CARDS, article_slug="some-slug",
            backlog=backlog(times=2), date="2026-10-28"))
        self.assertIn("观点卡总数", out)
        self.assertIn("到期未建卡", out)


class SummaryBacklogTest(unittest.TestCase):
    """输入包给的是**摘要**，简报要自己读回台账（2026-10-31）。

    这是「测试全绿、生产空转」的又一例：输入包的 `backlog` 字段只有
    `threshold / review_days / due / open_issues / path`，**没有 `issues`**；
    而 ④ 段按 `issues` 逐条判「到期未建卡」。用例手造了带 `issues` 的 dict，
    所以一直绿；生产里那两行（到期未建卡 / 台账在跟）从来没印过。
    """

    def _write_ledger(self, tmp: Path) -> Path:
        p = tmp / "card-backlog.json"
        data = card_backlog.empty_backlog()
        card_backlog.observe(
            data,
            {"decision": {"action": "skip"},
             "no_card_available": [{"issue": "到期议题", "event": "e", "why": "x"}]},
            [], "2026-10-20")
        card_backlog.observe(
            data,
            {"decision": {"action": "skip"},
             "no_card_available": [{"issue": "到期议题", "event": "e2", "why": "x"}]},
            [], "2026-10-21")
        card_backlog.save(p, data)
        return p

    def test_summary_is_resolved_to_full_ledger(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._write_ledger(Path(tmp))
            summary = {"threshold": 2, "due": [{"issue": "到期议题"}],
                       "open_issues": [], "path": str(p), "note": "有到期"}
            full = brief._load_backlog_full(summary)
            self.assertIn("issues", full, "摘要应被换成完整台账")
            self.assertIn(card_backlog.norm_issue("到期议题"), full["issues"])

    def test_due_line_now_fires_in_production_shape(self):
        """生产形状（摘要无 issues）下，「到期未建卡」必须真的印出来。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = self._write_ledger(Path(tmp))
            summary = {"threshold": 2, "due": [], "open_issues": [],
                       "path": str(p), "note": ""}
            lines = "\\n".join(brief._card_stock_lines(CARDS, summary, "2026-10-31"))
            self.assertIn("到期未建卡", lines)
            self.assertIn("到期议题", lines)

    def test_none_backlog_never_reads_disk(self):
        """`backlog=None` 是「没有台账」（老日期重放），不许去读当天的文件。"""
        self.assertIsNone(brief._load_backlog_full(None))

    def test_empty_state_says_checked(self):
        """两条线都空时留一行「查过了」——承诺在这儿，别再静默。"""
        lines = "\\n".join(brief._card_stock_lines(
            CARDS, backlog(times=1), "2026-10-31"))
        self.assertIn("无到期未建卡", lines)
        self.assertIn("无退役候选", lines)


if __name__ == "__main__":
    unittest.main()
