"""正文规则的判定（`platforms/prose-rules.md`，与 ADR-0069 同一套分工）。

规则本体在 `platforms/prose-rules.md`（归本人），判定逻辑在发布层的
`prose_rules.py`。这里只测**判定**，不测那份文件的内容。

⚠️ 最重要的一条：13 篇**已经写完**的成稿必须全部能被打上标签、且不崩。
正文规则不拦发，所以这里的失败形态不是「误拒了一篇」，而是「标签打错了」——
打错的后果更隐蔽：它会让人以为「这篇没有边界收尾」，而实际上只是标记词漏了。
因此每条针对标记词的测试都是回归：**它们钉住的是曾经真的打错的那几次。**

与标题规则最大的不同：标题有硬下限（拒发），正文没有。所以这里一个
`check_body` 都不测——没有的东西不要测，测了会诱使后来的人把它实现出来。
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pr = _load("prose_rules", ".agents/skills/newsagg-publish/scripts/prose_rules.py")

# 真实成稿的判定口子：目录里有什么就测什么，不写死文件名——
# 写死会在每天新增一篇后失效，而这条测试的价值恰在于「所有成稿都能被判定」。
OUTPUTS = sorted((ROOT / "outputs").glob("*.md"))


class TestRulesLoad(unittest.TestCase):
    def test_rules_file_parses(self):
        r = pr.load_rules(ROOT)
        self.assertTrue(r.get("loaded"), msg=f"正文规则文件读不上：{r.get('error')}")
        self.assertIn("tags", r)

    def test_missing_file_is_not_fatal(self):
        """规则读不上时不能崩——发布层会跳过打标签、简报会说明（ADR-0062）。"""
        r = pr.load_rules(ROOT / "no-such-dir")
        self.assertFalse(r.get("loaded"))
        self.assertIsNotNone(r.get("error"))
        # 空规则下打标签也不能崩：发布路径上不该因为这个文件丢了而中断。
        self.assertIsInstance(pr.tag_body("一段正文。", r), dict)

    def test_every_tag_has_a_consumer_shape(self):
        """每个标签都要能被消费——没有消费者的观察项是死代码。

        简报 `fmt_body_tags` 读的就是这四个键；这里钉住定义与消费的形状一致。
        """
        rules = pr.load_rules(ROOT)
        tags = pr.tag_body("今天发了一篇。正文在这里，足够长的一段散文。", rules)
        for key in rules["tags"]:
            self.assertIn(key, tags, msg=f"标签 {key} 没有被打出来")


class TestRealOutputs(unittest.TestCase):
    """13 篇成稿是唯一的真实样本：它们必须都能被判定，且判定结果稳定。"""

    def setUp(self):
        self.rules = pr.load_rules(ROOT)

    def test_all_outputs_tag_without_crash(self):
        self.assertTrue(OUTPUTS, msg="outputs/ 下没有成稿，测试失去意义")
        for p in OUTPUTS:
            with self.subTest(slug=p.stem):
                body = p.read_text(encoding="utf-8")
                tags = pr.tag_body(body, self.rules)
                self.assertIn("ending_mode", tags)
                self.assertIn("contrast_has_source", tags)

    def test_known_endings(self):
        """三篇正例：10-24、10-25 收在行动上；10-27 收在边界上。

        这是复盘里最容易被搞错的一件事——12 篇里有 9 篇收在边界句，
        如果标签把行动句判成边界句，「收尾改造有没有生效」就永远看不出来。
        """
        want = {
            "2026-10-24-ai-efficiency-metrics-become-performance-theater": "action",
            "2026-10-25-ai-side-project-earning-odds": "action",
            "2026-10-27-gpu-rent-effective-card-hour": "boundary",
        }
        for slug, expect in want.items():
            p = ROOT / "outputs" / f"{slug}.md"
            if not p.exists():
                self.skipTest(f"成稿不在了：{slug}")
            with self.subTest(slug=slug):
                tags = pr.tag_body(p.read_text(encoding="utf-8"), self.rules)
                self.assertEqual(expect, tags["ending_mode"]["mode"])

    def test_only_two_documented_cases_hit(self):
        """先例段只有 10-19 命中；引自己上一篇是一张**显式白名单**。

        先例段（`has_precedent`）的标记词宁可漏检不可误标，全库仍只允许 10-19
        一个命中——这条断言不动。

        `quotes_own_prior` 不同：散文规则第 15 条**明确允许**一篇引一句自己
        上一篇的结论，所以命中集本来就会随成稿增长（2026-10-29 那篇就按第 15
        条引了 10-23 的结论）。原来断言「恰好等于 [10-22]」，形态是「守规矩的
        成稿每天都会把 CI 打红」——一条对正确行为报错的测试，只会训练人忽略它。
        改成白名单后它守的东西没变，而且更强了一点：**多出来的 slug 必须显式
        写进来**，写的时候人就得看一眼「这是真引了自己，还是标记词放宽造成的
        误命中」。
        """
        known_prior = [
            "2026-10-22-deepseek-per-card-economics-splits-ai-capital",
            # 引 2026-10-23《AI视频省下的成本，正被模型厂商收走》的结论：
            # 「省下的成本没留在创作者手里，被上游收走了」（规则第 15 条，一句）。
            "2026-10-29-token-unit-price-vs-task-cost",
            # 注意 2026-10-30 那篇**不在**这里，但它是真引了自己：00:39 重写后那句
            # 从「三天前我在这张卡里写过」改成了「三天前我写过」，而标记词只认
            # 「这张卡」，于是标签**漏检**了。这是刻意的：标记词宁可漏检不可误标
            # （ADR-0082），放宽到「我写过」会把大量普通叙述吃进来。所以这张名单
            # 与真实自引数**会差一篇**，差的就是这一篇——不要为了让名单对上而放宽标记词。
        ]
        prec, prior = [], []
        for p in OUTPUTS:
            tags = pr.tag_body(p.read_text(encoding="utf-8"), self.rules)
            if tags["has_precedent"]["hit"]:
                prec.append(p.stem)
            if tags["quotes_own_prior"]["hit"]:
                prior.append(p.stem)
        self.assertEqual(["2026-10-19-deepseek-search-per-call-pricing"], prec)
        self.assertEqual(sorted(known_prior), prior,
                         msg="quotes_own_prior 的命中集变了：新命中的要看一眼是"
                             "真引了自己上一篇，还是标记词被放宽导致的误命中")


class TestMarkerRegressions(unittest.TestCase):
    """标记词的误命中都是**真的发生过**的，逐条钉住。

    放宽标记词时最容易犯的错是「顺手把口语时间词也收进去」——
    「后来」「那之前」「上一次」在中文里多数时候只是叙述时间，不是先例。
    """

    def setUp(self):
        self.rules = pr.load_rules(ROOT)

    def _tags(self, text: str) -> dict:
        return pr.tag_body(text, self.rules)

    def test_casual_time_words_are_not_precedent(self):
        for text in (
            "他后来做了个写作平台，定价 30 元/月，最终会员累计 200 元。" * 2,
            "先别写代码。找一个你猜会付钱的人，问他上一次遇到这个问题是什么时候、当时花了多少钱。" * 2,
            "在那之前，这笔账仍然挂在人身上。它不响、不报错，只在某个普通的下午被一次性付掉。" * 2,
        ):
            with self.subTest(text=text[:16]):
                self.assertFalse(self._tags(text)["has_precedent"]["hit"])

    def test_real_precedent_is_caught(self):
        text = ("这件事以前发生过一次。检索增强刚流行的时候，很多团队自己搭向量库，"
                "那段工程从核心竞争力变成一行参数。今天是同一件事的第二遍。") * 2
        self.assertTrue(self._tags(text)["has_precedent"]["hit"])

    def test_boundary_wins_over_action_in_same_tail(self):
        """末段同时出现「先做一件事」和「我不确定」时判边界。

        理由在规则文件里：作者在收尾处仍在自我限定，正是要识别的那一类。
        这条如果反过来判，9/12 篇的边界收尾会被误报成行动收尾，
        于是「收尾改造有没有生效」又看不出来了。
        """
        tail = "先做一件事：把返工率记下来。但我不确定这个方法对你的团队成立，边界在这里。"
        body = "上一段正文，足够长，用来凑出一段以上的段落。\n\n" + tail
        self.assertEqual("boundary", self._tags(body)["ending_mode"]["mode"])

    def test_empty_body_does_not_crash(self):
        tags = self._tags("")
        self.assertEqual("none", tags["ending_mode"]["mode"])
        self.assertIsNone(tags["contrast_has_source"]["ratio"])

    def test_contrast_source_must_be_in_the_same_sentence(self):
        """对照只在**同一句**里算，不回看上一句。

        这是实测过的取舍，不是省事：13 篇里反转句所在的句子全部不含
        source_markers，回看上一句之后仍然是 0/51。既然两套口径给出同一个
        结论（全库真的 0 处），就没必要为了"更宽容"去引入跨句窗口——
        跨句匹配会把「这是降价」和下一句「不是 X，是 Y」错连成一对。
        """
        body = ("多数人以为这是降价。不是降价，是计价单位换了，成本结构也跟着换了。"
                "这句话没有反转，只是普通陈述，长度也够。") * 2
        cs = self._tags(body)["contrast_has_source"]
        self.assertEqual(2, cs["reversed_total"])
        self.assertEqual(0, cs["with_source"])
        self.assertEqual(0.0, cs["ratio"])

    def test_contrast_source_counts_when_present(self):
        """给了对照主体的反转句要被算进来——否则「补对照」这件事无处可验。"""
        body = ("多数人会把它读成降价，其实不是降价，是计价单位换了。"
                "这句话没有反转，只是普通陈述，长度也够。") * 2
        cs = self._tags(body)["contrast_has_source"]
        self.assertEqual(2, cs["with_source"])
        self.assertEqual(1.0, cs["ratio"])


if __name__ == "__main__":
    unittest.main()
