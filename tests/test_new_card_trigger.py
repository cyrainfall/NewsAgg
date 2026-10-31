"""新建卡的触发条件必须有消费者（ADR-0083）。

这个文件回答一个具体的账单问题：**2026-10-14 到 10-28 的 13 轮分拣里，
`action: new` 出现了 0 次，产出 0 张新卡**，而同期有 5 个「无卡可接」的事件簇。

原因不是门槛太高，是三道闸全都没接上：

1. `no_card_available`（唯一能指向「这议题该建卡」的事实）只被 `brief.py`
   打印给人看，**从没有任何代码把它读回来**。上一轮的建议到下一轮就消失了。
2. `max_new_cards_per_day` 只写在输入包的 `limits` 里给人看，**无任何代码数它**。
3. `reusable_because` 要求模型预测「能不能接住未来的新闻」——那是预测不是判定，
   实测 `reusable_because: "不知道"` 也一路通过。

所以这里测的不是「函数返回什么」，而是**闸真的会拦**：
到期议题既不建卡也不写理由时，`validate_decision` 必须拒绝。

判据的分界线（ADR-0083 的核心取舍）：**同一议题第二次出现**才算到期。
第一次出现不算——孤例可能就是一次性事件；第二次出现才证明它不是孤例。
计数由脚本按天去重做（`card_backlog.observe`），不是模型自报。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIAGE = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "triage.py"
TRIAGE_INPUT = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "triage.py"
BACKLOG = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "card_backlog.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


triage = _load("newsagg_triage_nc", TRIAGE)
backlog_mod = _load("newsagg_card_backlog_nc", BACKLOG)

MONEY = "① 做 AI 岗的——薪酬带宽被重新定价；② 在招人的团队——同预算下的目标院校变了"

# 一张最小可用卡。注意**没有 `created`**：历史卡有的缺这个字段，
# 新逻辑不能因为缺字段就崩掉（真实卡库 8 张里 8 张都有，但兼容性要留）。
CARD = {
    "file": "ai-coding-workflow-over-model-capability.md",
    "issue": "AI 编码的竞争焦点在工作流集成",
    "covered_events": ["某模型发布"],
    "last_written": "2026-10-20",
    "body": "## 当前判断\n工作流集成比模型能力更决定留存。\n",
}

LIMITS = {"max_articles_per_day": 1, "max_new_cards_per_day": 1, "cooldown_days": 3.0}
DAY = "2026-10-29"


def decision(**over) -> dict:
    base = {
        "action": "continue",
        "card_file": CARD["file"],
        "whats_new": "某模型首次把 CI 集成作为卖点",
        "confidence": 0.88,
        "money_point": MONEY,
        "event": {"summary": "某模型发布", "members": ["36kr"], "hot": 51000},
    }
    base.update(over)
    return base


def validate(d: dict, backlog: dict | None = None, cards: list[dict] | None = None,
             day: str = DAY) -> dict:
    return triage.validate_decision(
        d, cards=cards if cards is not None else [CARD],
        cooldown={CARD["file"]: {"in_cooldown": False, "days_since": 9.0}},
        limits=LIMITS,
        backlog=backlog if backlog is not None else backlog_mod.empty_backlog(),
        day=day,
    )


def backlog_with(issue: str, times: int, days: list[str] | None = None) -> dict:
    """造一份台账：某议题已经出现过 `times` 次、且没有卡。"""
    b = backlog_mod.empty_backlog()
    use_days = days or [f"2026-10-{20 + i:02d}" for i in range(times)]
    e = backlog_mod._entry(issue, use_days[0])
    for d in use_days:
        backlog_mod._touch(e, d, f"{issue} 的一个事件")
    b["issues"][backlog_mod.norm_issue(issue)] = e
    return b


class BacklogCountingTest(unittest.TestCase):
    """计数由脚本做，按天去重——这是整套判据的地基。"""

    def test_same_day_twice_counts_once(self):
        # 一天里主流程可能跑多轮（白天 3 次采集 + 21:00 主流程），
        # 同一条新闻每轮都在候选池里。若按轮计数，门槛半天就被击穿。
        b = backlog_mod.empty_backlog()
        d = {"decision": {"action": "skip"},
             "no_card_available": [{"issue": "AI 顶尖人才的定价", "event": "700 万年薪", "why": "x"}]}
        first = backlog_mod.observe(b, d, [CARD], DAY)
        second = backlog_mod.observe(b, d, [CARD], DAY)
        self.assertEqual(len(first["bumped"]), 1)
        self.assertEqual(len(second["bumped"]), 0, "同一天第二次不该再加一")
        self.assertEqual(b["issues"][backlog_mod.norm_issue("AI 顶尖人才的定价")]["times_seen"], 1)

    def test_different_days_count_separately(self):
        b = backlog_mod.empty_backlog()
        d = {"decision": {"action": "skip"},
             "no_card_available": [{"issue": "AI 顶尖人才的定价", "event": "e", "why": "x"}]}
        backlog_mod.observe(b, d, [CARD], "2026-10-28")
        backlog_mod.observe(b, d, [CARD], "2026-10-29")
        e = b["issues"][backlog_mod.norm_issue("AI 顶尖人才的定价")]
        self.assertEqual(e["times_seen"], 2)
        self.assertEqual(e["first_seen"], "2026-10-28")
        self.assertEqual(e["last_seen"], "2026-10-29")

    def test_punctuation_difference_is_same_issue(self):
        # 归一化只去标点与空白，不做语义合并——宁可少算（推迟建卡），
        # 不要多算（错误合并会逼出一张错卡，而错卡会被后续所有分拣反复引用）。
        b = backlog_mod.empty_backlog()
        for i, issue in enumerate(["AI 顶尖人才的定价", "AI顶尖人才的定价。", "「AI 顶尖人才的定价」"]):
            backlog_mod.observe(
                b, {"decision": {"action": "skip"},
                    "no_card_available": [{"issue": issue, "event": "e", "why": "x"}]},
                [CARD], f"2026-10-{20 + i:02d}")
        self.assertEqual(len(b["issues"]), 1, f"应归一成一条：{sorted(b['issues'])}")

    def test_new_card_attaches_and_clears_due(self):
        # 建卡即销账：这个议题此后不再到期，否则每天都会被要求再建一次。
        b = backlog_with("AI 顶尖人才的定价", 2)
        self.assertEqual(len(backlog_mod.due_issues(b, [CARD])), 1, "建卡前应到期")
        newcard = {"file": "ai-talent-pricing-is-tiering.md",
                   "issue": "AI 顶尖人才的定价", "covered_events": [], "last_written": None,
                   "body": "## 当前判断\n"}
        backlog_mod.observe(
            b, {"decision": {"action": "new", "new_issue": "AI 顶尖人才的定价",
                             "card_file": newcard["file"],
                             "event": {"summary": "700 万年薪"}}},
            [CARD, newcard], DAY)
        self.assertEqual(backlog_mod.due_issues(b, [CARD, newcard]), [], "建卡后不该再到期")

    def test_card_deleted_makes_issue_due_again(self):
        # 台账记了卡名但文件没了（被人删掉/改名）→ 议题又裸了，必须重新到期。
        # 判据只看**文件是否真实存在**，不信台账里的自述。
        b = backlog_with("AI 顶尖人才的定价", 2)
        b["issues"][backlog_mod.norm_issue("AI 顶尖人才的定价")]["card_file"] = "gone.md"
        due = backlog_mod.due_issues(b, [CARD])
        self.assertEqual(len(due), 1, "卡文件不存在时应重新到期")

    def test_issue_matching_existing_card_is_self_healed(self):
        # 走了很久的老议题其实早就有卡（措辞不同但归一化后相同）→ 自动挂上，
        # 不累计计数。否则会把「已经有卡」的议题当成新议题反复催建。
        b = backlog_mod.empty_backlog()
        d = {"decision": {"action": "skip"},
             "no_card_available": [{"issue": "AI 编码的竞争焦点在工作流集成",
                                    "event": "e", "why": "x"}]}
        touched = backlog_mod.observe(b, d, [CARD], DAY)
        self.assertEqual(touched["bumped"], [], "有卡可接的议题不该被计数")
        e = b["issues"][backlog_mod.norm_issue("AI 编码的竞争焦点在工作流集成")]
        self.assertEqual(e["card_file"], CARD["file"])


class UrlSetCountingTest(unittest.TestCase):
    """计数键从「议题 × 天」扩成「议题 × 报道」（2026-10-31）。

    起因是一个真实的形状：10-29 记进台账的两条议题，第二天候选池里出现的
    **仍是同一 URL 的原报道**（热榜挂着没掉）。按天计数，它们第二天就够 2 次、
    被逼着建卡——而「第二次出现」的全部意义是「不是孤例」，同一篇文章挂两天
    证明的是热榜没换，不是议题复发。
    """

    def _d(self, urls):
        item = {"issue": "AI 输出致害的责任归属", "event": "e", "why": "x"}
        if urls is not None:
            item["urls"] = urls
        return {"decision": {"action": "skip"}, "no_card_available": [item]}

    def _entry(self, b):
        return b["issues"][backlog_mod.norm_issue("AI 输出致害的责任归属")]

    def test_same_article_two_days_counts_once(self):
        b = backlog_mod.empty_backlog()
        u = ["https://www.36kr.com/p/4045728212341632"]
        backlog_mod.observe(b, self._d(u), [CARD], "2026-10-30")
        touched = backlog_mod.observe(b, self._d(u), [CARD], "2026-10-31")
        e = self._entry(b)
        self.assertEqual(1, e["times_seen"], "同一篇报道挂两天不该被数成两次")
        self.assertEqual([], touched["bumped"])
        self.assertEqual("2026-10-31", e["last_seen"], "还是要更新最近出现时间")

    def test_new_article_counts(self):
        b = backlog_mod.empty_backlog()
        backlog_mod.observe(b, self._d(["https://a.example/1"]), [CARD], "2026-10-30")
        touched = backlog_mod.observe(b, self._d(["https://a.example/2"]), [CARD], "2026-10-31")
        self.assertEqual(2, self._entry(b)["times_seen"], "换了一篇报道才算新的一次")
        self.assertEqual(1, len(touched["bumped"]))

    def test_new_article_alongside_old_one_counts(self):
        """第二天两篇报道里有一篇是新的 → 算新的一次（哪怕另一篇还是旧的）。"""
        b = backlog_mod.empty_backlog()
        backlog_mod.observe(b, self._d(["https://a.example/1"]), [CARD], "2026-10-30")
        backlog_mod.observe(
            b, self._d(["https://a.example/1", "https://a.example/2"]), [CARD], "2026-10-31")
        self.assertEqual(2, self._entry(b)["times_seen"])

    def test_same_day_new_url_still_one(self):
        """同一天出现两篇不同报道，仍算一次——按天去重的语义不变。"""
        b = backlog_mod.empty_backlog()
        backlog_mod.observe(b, self._d(["https://a.example/1"]), [CARD], "2026-10-31")
        backlog_mod.observe(b, self._d(["https://a.example/2"]), [CARD], "2026-10-31")
        self.assertEqual(1, self._entry(b)["times_seen"])

    def test_without_urls_falls_back_to_day_counting(self):
        """老决策没有 urls：保持历史语义（按天），不然会静默减少所有历史计数。"""
        b = backlog_mod.empty_backlog()
        backlog_mod.observe(b, self._d(None), [CARD], "2026-10-30")
        backlog_mod.observe(b, self._d(None), [CARD], "2026-10-31")
        self.assertEqual(2, self._entry(b)["times_seen"])

    def test_non_http_entries_are_ignored(self):
        """urls 里混进空值/相对路径时不能让计数变成「永远是新报道」。"""
        b = backlog_mod.empty_backlog()
        backlog_mod.observe(b, self._d(["", "not-a-url"]), [CARD], "2026-10-30")
        e = self._entry(b)
        self.assertEqual([], e["urls_seen"])
        self.assertEqual(1, e["times_seen"])

    def test_url_set_is_recorded_as_evidence(self):
        b = backlog_mod.empty_backlog()
        backlog_mod.observe(b, self._d(["https://a.example/1", "https://a.example/1"]),
                            [CARD], "2026-10-30")
        self.assertEqual(["https://a.example/1"], self._entry(b)["urls_seen"],
                         "证据集要去重")


class NewCardGateTest(unittest.TestCase):
    """闸：到期议题必须二选一——建卡，或写下不建的理由。"""

    def test_first_appearance_is_not_due(self):
        # 第一次出现不算到期。孤例可能就是一次性事件（2026-10-28 的 700 万年薪
        # 当时正是这个判断），硬逼建卡会逼出一堆靠单个案例支撑的卡。
        b = backlog_with("AI 顶尖人才的定价", 1)
        res = validate(decision(), backlog=b)
        self.assertTrue(res["ok"], res["problems"])

    def test_second_appearance_without_card_or_reason_is_rejected(self):
        # 这是本轮改动的核心断言：13 轮里没有任何一条会拒绝**该建卡却没建**。
        b = backlog_with("AI 顶尖人才的定价", 2)
        res = validate(decision(), backlog=b)
        self.assertFalse(res["ok"], "到期议题既不建卡也不写理由，必须拒绝")
        joined = "\n".join(res["problems"])
        self.assertIn("AI 顶尖人才的定价", joined)
        self.assertIn("no_card_reason", joined)

    def test_second_appearance_with_reason_passes(self):
        b = backlog_with("AI 顶尖人才的定价", 2)
        res = validate(decision(no_card_reason="两个数据点都是同一家媒体的转载，不算独立信源"),
                       backlog=b)
        self.assertTrue(res["ok"], res["problems"])

    def test_second_appearance_with_per_issue_reason_passes(self):
        # 多议题同时到期时，理由要能按议题分开写（写成对象）。
        b = backlog_with("议题甲", 2, ["2026-10-26", "2026-10-27"])
        backlog_mod.observe(
            b, {"decision": {"action": "skip"},
                "no_card_available": [{"issue": "议题乙", "event": "e", "why": "x"}]},
            [CARD], "2026-10-26")
        backlog_mod.observe(
            b, {"decision": {"action": "skip"},
                "no_card_available": [{"issue": "议题乙", "event": "e", "why": "x"}]},
            [CARD], "2026-10-27")
        self.assertEqual(len(backlog_mod.due_issues(b, [CARD])), 2)

        res = validate(decision(no_card_reason={"议题甲": "证据只有一条", "议题乙": "样本太小"}),
                       backlog=b)
        self.assertTrue(res["ok"], res["problems"])

        # 只给一个议题的理由 → 另一个仍然缺
        res2 = validate(decision(no_card_reason={"议题甲": "证据只有一条"}), backlog=b)
        self.assertFalse(res2["ok"])
        self.assertIn("议题乙", "\n".join(res2["problems"]))

    def test_blank_reason_does_not_count(self):
        # 「写了但等于没写」是最难发现的缺失：字段存在、内容为空白。
        b = backlog_with("AI 顶尖人才的定价", 2)
        res = validate(decision(no_card_reason="   \n\t "), backlog=b)
        self.assertFalse(res["ok"], "空白理由不该算数")

    def test_building_a_different_card_does_not_clear_the_debt(self):
        # 到期的是 A，你建的是 B 卡 → A 仍然是零处理，必须拒绝。
        b = backlog_with("AI 顶尖人才的定价", 2)
        other = {"file": "some-other-card.md", "issue": "另一个议题",
                 "covered_events": [], "last_written": None, "body": "## 当前判断\n"}
        res = validate(
            decision(action="new", new_issue="另一个议题", card_file=other["file"]),
            backlog=b, cards=[CARD, other])
        self.assertFalse(res["ok"], "建别的卡不能抵掉这个议题的账")
        self.assertIn("AI 顶尖人才的定价", "\n".join(res["problems"]))

    def test_building_the_due_card_passes(self):
        b = backlog_with("AI 顶尖人才的定价", 2)
        newcard = {"file": "ai-talent-pricing-is-tiering.md", "issue": "AI 顶尖人才的定价",
                   "covered_events": [], "last_written": None, "body": "## 当前判断\n"}
        res = validate(
            decision(action="new", new_issue="AI 顶尖人才的定价",
                     card_file=newcard["file"], reusable_because="顶端溢价与中位贬值会反复出现"),
            backlog=b, cards=[CARD, newcard])
        self.assertTrue(res["ok"], res["problems"])

    def test_no_backlog_means_gate_is_inert(self):
        # 不传台账 = 退回 2026-10-28 之前的行为。这不是「通过」，是「闸没装」。
        # 生产路径（triage.py main）永远传；测试里也传。
        res = triage.validate_decision(
            decision(), cards=[CARD],
            cooldown={CARD["file"]: {"in_cooldown": False, "days_since": 9.0}},
            limits=LIMITS)
        self.assertTrue(res["ok"], res["problems"])

    def test_build_one_and_explain_the_other_passes(self):
        # 两个议题同时到期时，混着用是合法的：建一个、对另一个写明理由。
        # 闸的判据是「每个到期议题都被处理」，不是「必须全建」也不是「必须全写理由」。
        # 早先按 action 分支写的版本会在这里误拒——到期 A、建 A 的卡、同时对 B
        # 写了理由，却被判成「你在建别的卡」。
        b = backlog_with("议题甲", 2, ["2026-10-26", "2026-10-27"])
        backlog_mod.observe(
            b, {"decision": {"action": "skip"},
                "no_card_available": [{"issue": "议题乙", "event": "e", "why": "x"}]},
            [CARD], "2026-10-26")
        backlog_mod.observe(
            b, {"decision": {"action": "skip"},
                "no_card_available": [{"issue": "议题乙", "event": "e", "why": "x"}]},
            [CARD], "2026-10-27")
        self.assertEqual(len(backlog_mod.due_issues(b, [CARD])), 2)

        card_a = {"file": "issue-a.md", "issue": "议题甲", "covered_events": [],
                  "last_written": None, "body": "## 当前判断\n"}
        res = validate(
            decision(action="new", new_issue="议题甲", card_file="issue-a.md",
                     no_card_reason={"议题乙": "只有一家口径，等第二家"}),
            backlog=b, cards=[CARD, card_a])
        self.assertTrue(res["ok"], res["problems"])


class ReusableBecauseIsNoLongerPredictionTest(unittest.TestCase):
    """`reusable_because` 从「必填的预测」降为「可选的自述」。"""

    def test_new_without_reusable_because_passes(self):
        # 原规则要求模型预测「能否接住未来的新闻」。13 轮实测：这条从未拦住
        # 任何东西（`reusable_because: "不知道"` 也通过），只是增加了一次表态。
        # 真正的可复用性判据改成事后回检（retire_candidates）。
        newcard = {"file": "x.md", "issue": "新议题", "covered_events": [],
                   "last_written": None, "body": "## 当前判断\n"}
        res = validate(decision(action="new", new_issue="新议题", card_file="x.md"),
                       cards=[CARD, newcard])
        self.assertTrue(res["ok"], res["problems"])

    def test_new_still_requires_card_file_to_exist(self):
        # 结构缺失仍然要拦：card_file 指向的文件必须真实存在，
        # 否则第 3 段写回会被静默跳过（ADR-0041 的教训）。
        res = validate(decision(action="new", new_issue="新议题", card_file="nope.md"))
        self.assertFalse(res["ok"])
        self.assertIn("不存在", "\n".join(res["problems"]))


class RetireCandidatesTest(unittest.TestCase):
    """可复用性的**唯一**可验证形式：建成之后到底接住没有。"""

    def test_card_never_written_after_review_window_is_candidate(self):
        # `ai-tool-benefits-overestimated-by-demo-effect.md` 是真实例子：
        # 2026-10-14 建，到 10-28 仍 `last_written: null`。
        cards = [{"file": "a.md", "issue": "甲", "created": "2026-09-20", "last_written": None}]
        out = backlog_mod.retire_candidates(cards, "2026-10-28", review_days=30)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["file"], "a.md")

    def test_card_written_recently_is_not_candidate(self):
        cards = [{"file": "b.md", "issue": "乙", "created": "2026-09-20",
                  "last_written": "2026-10-27"}]
        self.assertEqual(backlog_mod.retire_candidates(cards, "2026-10-28", review_days=30), [])

    def test_young_card_is_not_candidate(self):
        # 门槛是「建满 30 天」。刚建的卡一次没写是正常的，不该被判退。
        cards = [{"file": "c.md", "issue": "丙", "created": "2026-10-20", "last_written": None}]
        self.assertEqual(backlog_mod.retire_candidates(cards, "2026-10-28", review_days=30), [])

    def test_missing_created_is_skipped_not_crashed(self):
        cards = [{"file": "d.md", "issue": "丁", "last_written": None}]
        self.assertEqual(backlog_mod.retire_candidates(cards, "2026-10-28", review_days=30), [])


class TriageInputCarriesBacklogTest(unittest.TestCase):
    """输入包必须带上台账——闸门只是兜底，模型看不见时它只能从零重推。"""

    def test_input_package_has_backlog_block(self):
        b = backlog_with("AI 顶尖人才的定价", 2)
        pkg = triage.build_triage_input(
            {"items": [], "counts": {}}, [CARD],
            {CARD["file"]: {"in_cooldown": False, "days_since": 9.0}},
            backlog=b)
        self.assertIn("backlog", pkg)
        self.assertEqual(pkg["backlog"]["due"], ["AI 顶尖人才的定价"])
        self.assertEqual(pkg["backlog"]["threshold"], 2)
        open_items = pkg["backlog"]["open_issues"]
        self.assertEqual(len(open_items), 1)
        self.assertEqual(open_items[0]["times_seen"], 2)

    def test_attached_issues_are_not_listed_as_open(self):
        # 已建卡的议题不进 open_issues：它对分拣没有信息量，只会稀释注意力。
        b = backlog_with("已建好卡的老议题", 5)
        b["issues"][backlog_mod.norm_issue("已建好卡的老议题")]["card_file"] = CARD["file"]
        pkg = triage.build_triage_input({"items": [], "counts": {}}, [CARD], {}, backlog=b)
        self.assertEqual(pkg["backlog"]["open_issues"], [])


if __name__ == "__main__":
    unittest.main()
