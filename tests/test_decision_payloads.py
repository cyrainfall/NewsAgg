"""分拣决策里「被下游消费的字段」必须有消费者（ADR-0058）。

这个测试文件的由来：ADR-0054 只校验了 `low_confidence_skipped` 与
`no_card_available` 两个列表，并留下一条「`abstain_reason`、`money_point`、
`ranking_note` 同样被简报消费但本轮未校验」的边界。

落码时实测发现这条边界比原文更糟：`money_point` **根本没有任何消费者**。
分拣从 2026-10-21 起每天在决策里写 300+ 字（具体到四类角色的落点），
而全仓 `grep -rn money_point --include=*.py` 只命中一条 ADR 的遗留说明——
既不进写作 brief，也不进简报。模型每天认真做了一次「这事跟谁的什么处境有关」
的判断，然后扔掉了。

这是本仓库第四次「记录了但没有消费者」（前三次：ADR-0038 的 hot_history、
ADR-0042 的三份 frontmatter 解析器、ADR-0057 的 tests/）。所以这里测的不是
「函数返回什么」，而是**契约的两端都在**：生产者必须写、消费者必须读。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIAGE = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "triage.py"
BUILD_BRIEF = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "build_brief.py"
BRIEF = ROOT / ".agents" / "skills" / "newsagg-brief" / "scripts" / "brief.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


triage = _load("newsagg_triage_dp", TRIAGE)
build_brief = _load("newsagg_build_brief_dp", BUILD_BRIEF)
brief = _load("newsagg_brief_dp", BRIEF)


MONEY = "① 接外包的前端——单子报价这半年在被压；② 想靠副业对冲的——先算获客成本"

# 一份最小可用的观点卡：build_brief 会从正文抽「当前判断」「依据事实」「边界与反方」。
CARD = {
    "file": "ai-coding-workflow-over-model-capability.md",
    "issue": "AI 编码的竞争焦点在工作流集成",
    "covered_events": ["某模型发布"],
    "last_written": "2026-10-20",
    "body": "## 当前判断\n工作流集成比模型能力更决定留存。\n\n"
            "## 依据事实\n某模型强调 CI 集成。\n",
}

STYLE = {
    "name": "淆雨学学",
    "industry": "AI 变现与开发者职业",
    "target_audience": "技术从业者",
    "tone": "专业",
    "voice": "第一人称",
    "writing_persona": "industry-observer",
    "blacklist": {"words": ["震惊"], "topics": ["政治敏感"]},
}

LIMITS = {"max_articles_per_day": 1, "max_new_cards_per_day": 1, "cooldown_days": 3.0}


def decision(**over) -> dict:
    base = {
        "action": "continue",
        "card_file": "ai-coding-workflow-over-model-capability.md",
        "whats_new": "某模型首次把 CI 集成作为卖点",
        "confidence": 0.88,
        "money_point": MONEY,
        "event": {"summary": "某模型发布", "members": ["36kr"], "hot": 51000},
    }
    base.update(over)
    return base


def validate(d: dict) -> dict:
    return triage.validate_decision(
        d, cards=[CARD], cooldown={CARD["file"]: {"in_cooldown": False, "days_since": 9.0}},
        limits=LIMITS,
    )


class MoneyPointIsValidatedTest(unittest.TestCase):
    """生产者一侧：continue/new 必须给出 money_point。"""

    def test_continue_without_money_point_is_rejected(self):
        d = decision()
        d.pop("money_point")
        res = validate(d)
        self.assertFalse(res["ok"])
        self.assertTrue(any("money_point" in p for p in res["problems"]), res["problems"])

    def test_new_without_money_point_is_rejected(self):
        res = validate(decision(action="new", new_issue="新议题",
                                reusable_because="能接住未来同类新闻", card_file=CARD["file"],
                                money_point=""))
        self.assertFalse(res["ok"])
        self.assertTrue(any("money_point" in p for p in res["problems"]), res["problems"])

    def test_whitespace_only_money_point_is_rejected(self):
        # 「写了但等于没写」是最难发现的一种缺失：字段存在、内容为空。
        res = validate(decision(money_point="   \n\t "))
        self.assertFalse(res["ok"])
        self.assertTrue(any("money_point" in p for p in res["problems"]))

    def test_continue_with_money_point_passes(self):
        res = validate(decision())
        self.assertTrue(res["ok"], res["problems"])

    def test_skip_does_not_require_money_point(self):
        # skip 不进写作阶段，没有落点可写——不该逼模型为它编一段。
        res = validate(decision(action="skip", money_point=None))
        self.assertTrue(res["ok"], res["problems"])


class AbstainReasonIsValidatedTest(unittest.TestCase):
    """弃权是合法结果，但必须说明理由，否则看守分不清「无选题」与「没跑」。"""

    def test_abstain_without_reason_is_rejected(self):
        res = validate({"action": "abstain", "confidence": 0.4})
        self.assertFalse(res["ok"])
        self.assertTrue(any("abstain_reason" in p for p in res["problems"]), res["problems"])

    def test_abstain_with_empty_reason_is_rejected(self):
        res = validate({"action": "abstain", "confidence": 0.4, "abstain_reason": "  "})
        self.assertFalse(res["ok"])
        self.assertTrue(any("abstain_reason" in p for p in res["problems"]))

    def test_abstain_with_reason_passes(self):
        res = validate({"action": "abstain", "confidence": 0.4,
                        "abstain_reason": "所有候选卡都在冷却期"})
        self.assertTrue(res["ok"], res["problems"])

    def test_continue_does_not_require_abstain_reason(self):
        # 反向：不该把弃权字段变成所有决策的必填项。
        res = validate(decision(abstain_reason=None))
        self.assertTrue(res["ok"], res["problems"])


class MoneyPointReachesWriterBriefTest(unittest.TestCase):
    """消费者一侧之一：钱落点必须出现在 wewrite 的选题包里。"""

    def test_writer_brief_carries_money_point_verbatim(self):
        text = build_brief.build_brief(decision(), CARD, STYLE, "1200-1800")
        self.assertIn("## 钱落点（分拣已判定，写作必须落到这些角色上）", text)
        self.assertIn(MONEY, text)

    def test_writer_brief_says_it_is_a_judgment_not_a_suggestion(self):
        # 光传过去不够——写作模型得知道这是约束，不是可选素材。
        text = build_brief.build_brief(decision(), CARD, STYLE, "1200-1800")
        self.assertIn("不是建议", text)
        self.assertIn("必须与之一致", text)

    def test_writer_brief_omits_section_when_money_point_absent(self):
        # 老决策（2026-10-21 之前）没有这个字段，重放历史时不能崩，
        # 也不该印一个空标题。
        #
        # 注意断言写的是完整标题而不是 `"## 钱落点"`：写作任务书里本来就有一个
        # 静态的 `### 钱落点（2026-10-21 改赛道，ADR-0050）` 写作纪律段落，
        # 而 `### 钱落点` 含子串 `## 钱落点`——用短串断言会永远为真。
        # 这个坑正是当初审计「钱落点有没有进 brief」时踩过的：grep 到 2 次命中，
        # 全是静态纪律段落，看起来「已经接上了」，实际分拣的判断一个字都没进去。
        HEADING = "## 钱落点（分拣已判定，写作必须落到这些角色上）"
        d = decision()
        d.pop("money_point")
        text = build_brief.build_brief(d, CARD, STYLE, "1200-1800")
        self.assertNotIn(HEADING, text)
        self.assertIn("### 钱落点（2026-10-21 改赛道，ADR-0050）", text)  # 静态纪律仍在
        self.assertIn("## 真实素材锚点", text)


class MoneyPointReachesReportBriefTest(unittest.TestCase):
    """消费者一侧之二：简报③段必须印出钱落点，供人事后复核。"""

    def test_report_brief_renders_money_point(self):
        out = brief.section_article(decision(), [], Path("/nonexistent"))
        text = "\n".join(out)
        self.assertIn("钱落点（分拣判定）", text)
        self.assertIn(MONEY, text)

    def test_report_brief_omits_line_when_absent(self):
        d = decision()
        d.pop("money_point")
        text = "\n".join(brief.section_article(d, [], Path("/nonexistent")))
        self.assertNotIn("钱落点（分拣判定）", text)

    def test_report_brief_does_not_truncate_money_point(self):
        # 钱落点是散文，截断会把「具体到角色」压成空话——所以整段印出。
        long_money = MONEY + "；③ 团队里负责引入 AI 工具的——要盯的是产出而非改动量。" * 8
        text = "\n".join(brief.section_article(decision(money_point=long_money), [], Path("/nonexistent")))
        self.assertIn(long_money, text)


if __name__ == "__main__":
    unittest.main()
