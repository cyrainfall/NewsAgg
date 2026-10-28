"""正文标签必须有消费者：简报要把它印出来（ADR-0082）。

为什么值得一个测试：`title_tags` 从 2026-10-27 起就写进账本，但全仓**没有任何代码读它**
——「记录下来」被当成了「已经做完」。正文四个标签如果也只在账本里躺着，
几个月后会和 `title_tags` 一样没人看。所以这里钉的是**渲染**：

- 有标签时，四个字段都要变成人能扫的一行；
- 老记录（本次改造之前发的那批）没有 `body_tags` 时**什么都不印**，
  不能印「无标签」——「没打标签」与「打了标签说没问题」必须可区分；
- 收尾形态必须翻译成中文（`action` / `boundary` / `none` 直接印出来没人看得懂）。

不测判定本身（那是 `test_prose_rules.py` 的事）。这里只回答一个问题：
**判定出来的东西，人能不能在每天的简报上看见。**

跑法：`python3 -m unittest discover -s tests`（仓库根）或 `-s tests -v`
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


brief = _load("newsagg_brief_bodytags", BRIEF)


def _rec(mode="action", reversed_total=5, with_source=0,
         precedent=False, prior=False):
    return {"body_tags": {
        "ending_mode": {"mode": mode},
        "contrast_has_source": {"reversed_total": reversed_total,
                                "with_source": with_source},
        "has_precedent": {"hit": precedent},
        "quotes_own_prior": {"hit": prior},
    }}


class BodyTagsInBriefTest(unittest.TestCase):
    def test_action_ending_is_translated_to_chinese(self):
        lines = brief.fmt_body_tags(_rec(mode="action"))
        self.assertIn("收尾：行动句收尾", lines)

    def test_boundary_ending_is_translated(self):
        lines = brief.fmt_body_tags(_rec(mode="boundary"))
        self.assertIn("收尾：边界句收尾", lines)

    def test_none_ending_is_not_folded_into_either_side(self):
        """三态的意义就在这一条：既非行动也非边界是真实存在的一类（10-23）。"""
        lines = brief.fmt_body_tags(_rec(mode="none"))
        self.assertIn("收尾：既非行动也非边界", lines)

    def test_zero_source_contrast_carries_a_note(self):
        """0 处来源是这轮复盘的主要发现（38 处反转句、0 处给对照），
        所以它不该只是「0/5」——得带一句这意味着什么。"""
        lines = brief.fmt_body_tags(_rec(reversed_total=5, with_source=0))
        joined = "\n".join(lines)
        self.assertIn("反转句：0/5 句有对照来源", joined)
        self.assertIn("多数人会怎么读", joined)

    def test_contrast_with_source_has_no_warning_note(self):
        joined = "\n".join(brief.fmt_body_tags(_rec(reversed_total=5, with_source=3)))
        self.assertIn("反转句：3/5 句有对照来源", joined)
        self.assertNotIn("多数人会怎么读", joined)

    def test_precedent_and_prior_render_only_when_hit(self):
        no = "\n".join(brief.fmt_body_tags(_rec(precedent=False, prior=False)))
        self.assertIn("先例段：无", no)
        self.assertNotIn("引了自己上一篇", no)
        yes = "\n".join(brief.fmt_body_tags(_rec(precedent=True, prior=True)))
        self.assertIn("先例段：有", yes)
        self.assertIn("引了自己上一篇的结论", yes)

    def test_old_record_without_tags_prints_nothing(self):
        """老记录不印「无标签」：那会把「没测」渲染成「测了没问题」。"""
        self.assertEqual([], brief.fmt_body_tags({}))
        self.assertEqual([], brief.fmt_body_tags({"body_tags": None}))
        self.assertEqual([], brief.fmt_body_tags({"body_tags": {}}))

    def test_malformed_tags_do_not_crash(self):
        """账本是外部文件、会被人手工改；渲染不能因为一个键写错就整段炸掉。"""
        self.assertEqual([], brief.fmt_body_tags({"body_tags": "oops"}))
        lines = brief.fmt_body_tags({"body_tags": {"ending_mode": {"mode": "action"}}})
        self.assertIn("收尾：行动句收尾", lines)


class TestWiring(unittest.TestCase):
    """线接上了没有——这一层测的是**调用存在**，不是行为。

    为什么要测源码而不是测行为：`title_tags` 的教训是「写了没人读」，
    而那种失败**任何行为测试都抓不到**（账本里多一个字段，所有断言照样通过）。
    唯一能挡住它的判据是「消费点是否真的调用了它」。这与
    `test_cookie_injection.py::test_reads_newsagg_cookie_file_env` 同一手法。
    """

    def test_publish_writes_body_tags(self):
        src = (ROOT / ".agents/skills/newsagg-publish/scripts/publish.py").read_text(
            encoding="utf-8")
        self.assertIn("body_tags", src)
        self.assertIn("tag_body(", src)
        # 打了标签就必须进账本：只有计算没有落盘等于没做。
        self.assertLess(src.index("tag_body("), src.index('"body_tags"'))

    def test_brief_prints_body_tags(self):
        """③ 段必须真的调 `fmt_body_tags`——函数存在但没人调，就是下一个 title_tags。"""
        src = (ROOT / ".agents/skills/newsagg-brief/scripts/brief.py").read_text(
            encoding="utf-8")
        self.assertIn("fmt_body_tags(tagged[-1])", src)

    def test_build_brief_reads_the_same_rule_file(self):
        """写作侧与发布侧必须读**同一份**规则，不各抄一份（ADR-0042）。"""
        src = (ROOT / ".agents/skills/newsagg-triage/scripts/build_brief.py").read_text(
            encoding="utf-8")
        # 走同一份规则文件，而不是把要求抄成第二份散文。
        self.assertIn("prose-rules.md", src)
        self.assertIn("_load_prose_rules", src)
        self.assertIn("### 发布后会记下的正文标签", src)
        # 标签是**从规则文件渲染**的（`for key, spec in …tags…`），
        # 所以源文件里不该出现任何标签名——出现就说明抄了一份。
        for key in ("ending_mode", "contrast_has_source",
                    "has_precedent", "quotes_own_prior"):
            self.assertNotIn(key, src, msg=f"标签名 {key} 被抄进了选题包，应改为从规则文件读")

    def test_build_brief_does_not_leak_marker_words(self):
        """选题包只说明「会被记什么」，**不印标记词表**（ADR-0082 Decision 五）。

        印了就等于教人作弊：写作方只要在末段塞一个「先别」就满足了标签，
        而标签本该检测结构而不是口令。判据是「渲染时只取 `desc`」——
        规则文件里的 `action_markers` / `boundary_markers` / `precedent_markers`
        一个都不许出现在选题包的源文件里（出现即意味着有人把它抄了一份）。
        """
        src = (ROOT / ".agents/skills/newsagg-triage/scripts/build_brief.py").read_text(
            encoding="utf-8")
        for field in ("action_markers", "boundary_markers",
                      "precedent_markers", "source_markers"):
            self.assertNotIn(field, src, msg=f"{field} 被带进选题包了，应只渲染 desc")
        # 渲染那一段必须只取 desc——这是上面那条反断言的正面表述。
        self.assertIn('spec.get(\'desc\'', src)


if __name__ == "__main__":
    unittest.main()
