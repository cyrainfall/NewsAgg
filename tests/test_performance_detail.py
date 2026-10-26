"""单篇详细指标的抽取与口径（ADR-0068）。

`content list` 只给展现与阅读。完读率、平均阅读时长、「超过 N% 同类作品」
这三个指标要逐篇问 `item/info`——而 `item/info` 之前被 ADR-0037 判为
「接口不可用」，实际只是 `--content-type` 默认值传错（文章是 1 不是 2）。

口径是这里最要紧的部分：
  - `-1` = 数据未就绪（作品发布**次日 14:00 后**才可查）
  - `0`  = 已就绪但为 0（0 阅读的文章完读率就是 0，不是缺失）
把这两者搞混，表现数据就会说假话。
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


perf = _load("performance", ".agents/skills/newsagg-performance/scripts/performance.py")


def _payload(consume_detail=None, ranking=None):
    stat = {}
    if consume_detail is not None:
        stat["consume_detail"] = consume_detail
    if ranking is not None:
        stat["ranking_data"] = ranking
    return {"item_data": {"item_stat": stat}}


class TestExtractDetailMetrics(unittest.TestCase):
    def test_reads_three_metrics(self):
        m = perf.extract_detail_metrics(_payload(
            consume_detail={"click_rate": 0.0024, "read_complete_rate": 84, "read_duration": 14},
            ranking={"click_rate_rank": 2, "interaction_rate_rank": -1},
        ))
        self.assertEqual(84, m["read_complete_rate"])
        self.assertEqual(14, m["read_duration"])
        self.assertEqual(2, m["click_rate_rank"])

    def test_keeps_minus_one_as_not_ready(self):
        """-1 是「未就绪」，不是 0。丢掉它就分不清「没数据」和「数据是零」。"""
        m = perf.extract_detail_metrics(_payload(
            consume_detail={"read_complete_rate": -1, "read_duration": -1}))
        self.assertEqual(-1, m["read_complete_rate"])
        self.assertEqual(-1, m["read_duration"])

    def test_zero_is_kept_as_zero(self):
        m = perf.extract_detail_metrics(_payload(
            consume_detail={"read_complete_rate": 0, "read_duration": 0}))
        self.assertEqual(0, m["read_complete_rate"])

    def test_empty_payload_yields_empty(self):
        self.assertEqual({}, perf.extract_detail_metrics({}))
        self.assertEqual({}, perf.extract_detail_metrics({"item_data": {}}))
        self.assertEqual({}, perf.extract_detail_metrics({"item_data": {"item_stat": {}}}))

    def test_ignores_non_numeric(self):
        m = perf.extract_detail_metrics(_payload(
            consume_detail={"read_complete_rate": "84", "read_duration": None}))
        self.assertEqual({}, m)

    def test_ignores_booleans(self):
        """bool 是 int 的子类，不排除就会把 True 记成 1。"""
        m = perf.extract_detail_metrics(_payload(
            consume_detail={"read_complete_rate": True}))
        self.assertEqual({}, m)

    def test_article_content_type_is_one(self):
        """文章是 1 不是 2。传 2 的话平台返回 1003，形状和「无数据」一模一样
        ——ADR-0037 正是把这个当成了「接口不可用」。"""
        self.assertEqual(1, perf.CONTENT_TYPE_ARTICLE)


if __name__ == "__main__":
    unittest.main()
