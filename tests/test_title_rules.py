"""标题规则的判定（ADR-0069）。

规则本体在 `platforms/title-rules.md`（归本人），判定逻辑在发布层的
`title_rules.py`。这里只测**判定**，不测那份文件的内容——内容由本人改，
这里跟着改就没意义了。

⚠️ 最重要的一条：10 篇**真实发过**的标题必须全部通过。
一条规则如果会拦掉我们自己已经发出去的文章，那它要么是错的，
要么是平台规则变了。两种情况都必须在测试里立刻暴露，而不是等到下一次发布。
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


tr = _load("title_rules", ".agents/skills/newsagg-publish/scripts/title_rules.py")

# 我们真实发过的 10 个标题（2026-10-17 ~ 2026-10-26）。
PUBLISHED_TITLES = [
    "模型打五折，未必是好事",
    "Meta 开源 8160 亿参数模型：模型免费，运维不免费",
    "DeepSeek搜索5元一千次，做Agent的成本要重算",
    "拼豆App三个月43美元：AI小工具死在留存",
    "Codex搬走Cursor配置：选AI编程工具的标准变了",
    "DeepSeek公开单卡账本：便宜的API还能便宜多久",
    "AI视频省下的成本，正被模型厂商收走",
    "AI 提效写进绩效，先涨的是你的返工率",
    "想靠 AI 做副业的人：年入 10 万美元的只有 2.4%",
    "老板叫停AI裁员，中间层位已经换成外包",
]


class TestRulesLoad(unittest.TestCase):
    def test_rules_file_parses(self):
        r = tr.load_rules(ROOT)
        self.assertTrue(r.get("loaded"), msg=f"规则文件读不上：{r.get('error')}")
        self.assertIn("hard", r)
        self.assertIn("observe", r)

    def test_missing_file_is_not_fatal(self):
        """规则读不上时不能崩——发布门会退回到平台硬长度限制（ADR-0069）。"""
        r = tr.load_rules(ROOT / "no-such-dir")
        self.assertFalse(r.get("loaded"))
        self.assertIsNotNone(r.get("error"))


class TestHardLimits(unittest.TestCase):
    def setUp(self):
        self.rules = tr.load_rules(ROOT)

    def test_all_published_titles_pass(self):
        """回归：真实发过的标题必须全部通过。"""
        for t in PUBLISHED_TITLES:
            with self.subTest(title=t):
                self.assertEqual([], tr.check_title(t, self.rules))

    def test_too_short_rejected(self):
        self.assertTrue(tr.check_title("短", self.rules))

    def test_too_long_rejected(self):
        self.assertTrue(tr.check_title("字" * 31, self.rules))

    def test_forbidden_word_rejected(self):
        probs = tr.check_title("震惊！AI 已经取代程序员了", self.rules)
        self.assertTrue(probs)
        self.assertEqual("H3", probs[0]["code"])

    def test_empty_title_rejected(self):
        self.assertTrue(tr.check_title("", self.rules))

    def test_percentage_ending_is_not_rejected(self):
        """曾经有一条硬规则「不得以百分号结尾」，它会拦掉我们真实发过的那篇。

        它是本仓库 ADR-0069 记下的真实错误：证据只是直觉。
        这条测试钉住「它必须保持是观察项」——
        哪天真要升为硬下限，得同时改掉这条测试并说明理由。
        """
        t = "想靠 AI 做副业的人：年入 10 万美元的只有 2.4%"
        self.assertEqual([], tr.check_title(t, self.rules))
        tags = tr.tag_title(t, self.rules)
        self.assertIn("no_bare_percentage_ending", tags)
        self.assertTrue(tags["no_bare_percentage_ending"]["hit"])


class TestObservationTags(unittest.TestCase):
    def setUp(self):
        self.rules = tr.load_rules(ROOT)

    def test_every_observation_has_a_consumer_shape(self):
        """每个观察项都要能被打成标签——没有消费者的观察项是死代码。"""
        for key in self.rules["observe"]:
            tags = tr.tag_title("老板叫停AI裁员，中间层位已经换成外包", self.rules)
            self.assertIn(key, tags, msg=f"观察项 {key} 没有被打标签")

    def test_tags_are_always_present(self):
        """标签写 null 而不是省略（ADR-0069）：省略与「没打标签」在消费端不可区分。"""
        tags = tr.tag_title("任意标题", self.rules)
        self.assertIsInstance(tags, dict)
        for key in self.rules["observe"]:
            self.assertIn(key, tags)


if __name__ == "__main__":
    unittest.main()
