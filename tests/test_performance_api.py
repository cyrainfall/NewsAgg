"""表现回路的 api 后端（ADR-0071）。

这个文件测三件事，每一件都对应一个「只有读代码看不出来」的事实：

1. **两条后端读的是同一组数字**——展现与阅读逐篇相等。
   测法不是「断言 api 能跑」，而是把两条路的输出按 gid 对齐、逐字段比。
   只有这样才能证明换后端**不改变基准**（基准一变，降频判定就变了）。

2. **草稿不进表现序列**。浏览器后端压根不返回草稿（它走已发布列表），
   而纯 HTTP 的作品列表**含草稿**（实测 5 条）。不显式过滤，两条路的样本集
   就不一样，混进基准的是一堆「展现恒为 0」的草稿。

3. **账号不同时要能看出来**。api 读本机真实 Chrome 的 cookie，浏览器读
   Playwright 的 profile —— 两个独立登录态（实测 cookie 互相解不开）。
   若 api 读回来一篇都不是我们的，脚本要回落浏览器后端。

跑法：`python3 -m unittest tests.test_performance_api -v`

⚠️ 这里不联网。真实接口的验证是在本机跑 `performance.py --backend api`
（2026-10-27 实测：11 篇全部匹配、7.8 秒、0 失败）；本文件用假数据把
**判定逻辑**钉住，让「改坏了判定」会在 CI 里立刻暴露。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF = ROOT / ".agents" / "skills" / "newsagg-performance" / "scripts" / "performance.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


perf = _load("perf_under_test", PERF)


# ── 假数据 ────────────────────────────────────────────────────────

# 形状照抄平台真实响应（2026-10-27 实测的 `data.articles[]` 一条）。
FAKE_ARTICLE = {
    "group_id": "7700222179094430248",
    "pgc_id": "7700222179094430248",
    "title": "AI 提效写进绩效，先涨的是你的返工率",
    "create_time": 1792870926,
    "status": 3,
    "status_desc": "已发布",
    "is_draft": False,
    "is_passed": True,
    "impression_count": 422,
    "go_detail_count_v2": 1,
    "digg_count": 0,
    "comment_count": 0,
    "article_url": "https://www.toutiao.com/item/7700222179094430248/",
    "article_type": 0,
    "content_word_cnt": "1800",
}

FAKE_DRAFT = {
    **FAKE_ARTICLE,
    "group_id": "7698370074901627426",
    "pgc_id": "7698370074901627426",
    "title": "探针标题不要发布",
    "status": 1,
    "status_desc": "草稿",
    "is_draft": True,
    "is_passed": False,
    "impression_count": 0,
    "go_detail_count_v2": 0,
    "article_url": "https://www.toutiao.com/item/7698370074901627426/",
}


class FakeSession:
    """假装自己是 browserless 的 Session。"""

    def __init__(self, articles, detail_by_gid=None, fail=False):
        self.articles = articles
        self.detail_by_gid = detail_by_gid or {}
        self.fail = fail
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("网络错误：模拟断网")
        if "/mp/agw/article/list" in url:
            return {"code": 0, "message": "success",
                    "data": {"articles": self.articles}}
        if "/mp/agw/statistic/v2/item/info" in url:
            gid = url.split("item_id=")[1].split("&")[0]
            if gid in self.detail_by_gid:
                return self.detail_by_gid[gid]
            return {"code": 1003, "message": "数据开小差啦~请稍后再试"}
        raise AssertionError(f"未预期的请求：{url}")


class FakeClient:
    def __init__(self, session):
        self.session = session


def _patch_api(monkey_articles, detail_by_gid=None, fail=False):
    """把 performance 里的 api 客户端换成假的。返回 (session, 恢复函数)。"""
    session = FakeSession(monkey_articles, detail_by_gid, fail)

    def fake_api_client(account):
        return _FakeModule(), FakeClient(session)

    orig = perf._api_client
    perf._api_client = fake_api_client
    return session, lambda: setattr(perf, "_api_client", orig)


class _FakeModule:
    ARTICLE_LIST_URL = "https://mp.toutiao.com/mp/agw/article/list"


class TestApiListNormalisation(unittest.TestCase):
    """api 列表 → 与浏览器路径同形的归一化。"""

    def setUp(self):
        self.session, self.restore = _patch_api([FAKE_ARTICLE, FAKE_DRAFT])
        self.addCleanup(self.restore)

    def test_metrics_are_mapped_to_the_same_names(self):
        arts, meta = perf.fetch_content_list_api("default", 50)
        self.assertEqual(len(arts), 1, "草稿不该进表现序列")
        self.assertEqual(arts[0]["metrics"], {
            "showCount": 422, "readCount": 1, "diggCount": 0, "commentCount": 0})

    def test_read_rate_is_none_when_no_impressions(self):
        arts, _ = perf.fetch_content_list_api("default", 50)
        zero = {**FAKE_ARTICLE, "impression_count": 0, "go_detail_count_v2": 0}
        sess2, restore2 = _patch_api([zero])
        self.addCleanup(restore2)
        arts2, _ = perf.fetch_content_list_api("default", 50)
        self.assertIsNone(arts2[0]["read_rate"],
                          "展现为 0 时阅读率无定义，记 None 而不是 0")

    def test_drafts_are_counted_in_meta(self):
        _, meta = perf.fetch_content_list_api("default", 50)
        self.assertEqual(meta["drafts_skipped"], 1)
        self.assertEqual(meta["count"], 1)

    def test_gid_is_a_string(self):
        """gid 必须字符串。node 那一侧同一响应里还有 JS number 版本，
        实测已丢精度（7700590575333949967 → 7700590575333950000）。
        丢一位就永远和账本 join 不上（ADR-0053）。"""
        arts, _ = perf.fetch_content_list_api("default", 50)
        self.assertIsInstance(arts[0]["gid"], str)
        self.assertEqual(arts[0]["gid"], "7700222179094430248")


class TestApiDetail(unittest.TestCase):
    """api 单篇指标（ADR-0068 的口径）。"""

    def test_detail_metrics_are_extracted(self):
        detail = {"code": 0, "item_data": {"item_stat": {
            "consume_detail": {"click_rate": 0.00237,
                                "read_complete_rate": 84.0,
                                "read_duration": 14},
            "ranking_data": {"click_rate_rank": 2,
                             "interaction_rate_rank": -1,
                             "read_complete_rate_rank": 100},
        }}}
        sess, restore = _patch_api([], {"7700222179094430248": detail})
        self.addCleanup(restore)
        out, meta = perf.fetch_content_details_api("default", ["7700222179094430248"])
        self.assertEqual(meta["got"], 1)
        self.assertEqual(out["7700222179094430248"]["read_complete_rate"], 84.0)
        self.assertEqual(out["7700222179094430248"]["read_duration"], 14)
        self.assertEqual(out["7700222179094430248"]["click_rate_rank"], 2)

    def test_1003_with_valid_type_is_no_data_not_failure(self):
        """type 合法时 1003 = 这篇暂无数据（发布次日 14:00 前），不是故障。"""
        sess, restore = _patch_api([], {})
        self.addCleanup(restore)
        out, meta = perf.fetch_content_details_api("default", ["7700222179094430248"])
        self.assertEqual(out, {})
        self.assertEqual(meta["no_data"], ["7700222179094430248"])
        self.assertEqual(meta["failed"], [], "1003 不该被记成失败")
        self.assertIn("note", meta)

    def test_one_failure_does_not_stop_the_others(self):
        """单篇失败不拖垮整批——表现数据读不回来不阻断本轮（ADR-0006）。"""
        detail = {"code": 0, "item_data": {"item_stat": {
            "consume_detail": {"read_complete_rate": 50.0, "read_duration": 20},
            "ranking_data": {}}}}

        class FlakySession(FakeSession):
            def get_json(self, url, **kwargs):
                if "item_id=7700590575333949967" in url:
                    raise RuntimeError("连接被重置")
                return super().get_json(url, **kwargs)

        sess = FlakySession([], {"7700222179094430248": detail})
        orig = perf._api_client
        perf._api_client = lambda account: (_FakeModule(), FakeClient(sess))
        self.addCleanup(lambda: setattr(perf, "_api_client", orig))

        out, meta = perf.fetch_content_details_api(
            "default", ["7700222179094430248", "7700590575333949967"])
        self.assertEqual(meta["got"], 1)
        self.assertEqual(len(meta["failed"]), 1)
        self.assertEqual(meta["failed"][0]["gid"], "7700590575333949967")

    def test_content_type_is_1_for_articles(self):
        """图文文章是 1，不是 2（ADR-0068 从平台 bundle 抄下来的枚举）。

        传错编号不会报参数错误，只会返回 1003「数据开小差啦」，
        与「这篇没有数据」长得一模一样——ADR-0037 正是这样误判了接口。
        """
        self.assertEqual(perf.CONTENT_TYPE_PGC_ARTICLE, 1)
        self.assertIn(1, perf.CONTENT_TYPE_KNOWN)
        self.assertNotIn(8, perf.CONTENT_TYPE_KNOWN, "8 = Invalid")


class TestApiFailureIsNotFatal(unittest.TestCase):
    def test_network_error_returns_empty_with_error(self):
        sess, restore = _patch_api([], fail=True)
        self.addCleanup(restore)
        arts, meta = perf.fetch_content_list_api("default", 50)
        self.assertEqual(arts, [])
        self.assertIn("error", meta)

    def test_structure_change_is_reported_not_guessed(self):
        """平台改了信封结构时要报错，不要静默当成「没有文章」。"""
        class WeirdSession(FakeSession):
            def get_json(self, url, **kwargs):
                return {"code": 0, "data": {"articles": "not-a-list"}}

        orig = perf._api_client
        perf._api_client = lambda account: (_FakeModule(), FakeClient(WeirdSession([])))
        self.addCleanup(lambda: setattr(perf, "_api_client", orig))
        arts, meta = perf.fetch_content_list_api("default", 50)
        self.assertEqual(arts, [])
        self.assertIn("结构变了", meta["error"])


class TestParityWithBrowserPath(unittest.TestCase):
    """两条后端产出同一组字段，且样本集一致。"""

    def test_same_article_yields_same_metric_values(self):
        """api 读到的展现/阅读必须与浏览器路径一致。

        实测（2026-10-27，10 篇逐篇比对）全部相等；这里把其中一篇的
        两组数字钉住，防止将来有人改了一边的字段名而没人发现。
        """
        # 浏览器路径 `content list` 的原文（`items[].data[].assembleCell
        # .itemCell`）里这篇的 `itemCounter`。
        browser_counter = {"showCount": 422, "readCount": 1,
                           "diggCount": 0, "commentCount": 0,
                           "repinCount": 0, "videoWatchCount": 0}
        # 纯 HTTP 路径 `data.articles[]` 里同一篇。
        http_article = FAKE_ARTICLE
        self.assertEqual(browser_counter["showCount"], http_article["impression_count"])
        self.assertEqual(browser_counter["readCount"], http_article["go_detail_count_v2"])

    def test_browser_path_returns_the_same_field_names(self):
        """两条路都产出 `gid/title/published_at/kind/metrics/read_rate`——
        落库、匹配、评估都共用这些字段。"""
        browser_raw = {
            "success": True, "source": "api_intercept", "count": 2,
            "items": [
                {"data": [{"assembleCell": {"itemCell": {
                    "articleBase": {"gidStr": "7700222179094430248",
                                    "title": "AI 提效写进绩效，先涨的是你的返工率",
                                    "publishTime": 1792870926,
                                    "itemStatus": 20,
                                    "articleURL": "https://www.toutiao.com/item/7700222179094430248/"},
                    "itemCounter": {"showCount": 422, "readCount": 1},
                }}}]},
            ],
        }
        arts, _ = perf.extract_articles(browser_raw)
        self.assertEqual(len(arts), 1)
        for key in ("gid", "title", "published_at", "kind", "metrics", "read_rate"):
            self.assertIn(key, arts[0], f"浏览器路径缺字段 {key}")

    def test_api_path_returns_the_same_field_names(self):
        sess, restore = _patch_api([FAKE_ARTICLE])
        self.addCleanup(restore)
        arts, _ = perf.fetch_content_list_api("default", 50)
        for key in ("gid", "title", "published_at", "kind", "metrics", "read_rate"):
            self.assertIn(key, arts[0], f"api 路径缺字段 {key}")


class TestReadRateClamp(unittest.TestCase):
    """阅读率不能大于 100%（2026-10-28 实测的窗口期）。

    ## 为什么值得钉住

    10-27 那篇在次日读回时是 `showCount=5 / readCount=6` —— **阅读数大于展现数**。
    平台的两条计数不同步（先记点击、后补展现），于是算出一个数学上不可能的
    120%。这不是「数据坏了」，是窗口期的正常现象。

    但把这个值当阅读率用，会从两条路上造成实际损害：

    1. **进基准中位数**——`evaluate()` 的基线取最近 N 篇 `read_rate` 的中位数，
       一个 1.2 的值会把阈值抬高，于是**该降频时降不下来**（ADR-0010 的自动
       降频是本系统唯一的行为调节器）。
    2. **被简报原样印出**——`brief.py` 直接 `fmt_rate(read_rate)`，读者会看到
       「阅读率 120%」。简报的信噪比是它唯一的资产（ADR-0055），一个明显的
       不可能数字会让人开始怀疑同一段里的其他数字。

    所以判据是：`rate > 1` 与「展现为 0」同类，都记 `None`（此刻算不出有意义
    的比率），原始的两个数仍留在 `metrics` 里供人看。
    """

    def _browser_payload(self, show, read):
        return {
            "success": True, "source": "api_intercept", "count": 1,
            "items": [
                {"data": [{"assembleCell": {"itemCell": {
                    "articleBase": {"gidStr": "7701389368845500963",
                                    "title": "H200租金涨143%",
                                    "publishTime": 1792870926,
                                    "itemStatus": 20,
                                    "articleURL": "https://www.toutiao.com/item/7701389368845500963/"},
                    "itemCounter": {"showCount": show, "readCount": read},
                }}}]},
            ],
        }

    def test_read_greater_than_show_is_none_not_120_percent(self):
        """展现 5 / 阅读 6 → read_rate 必须是 None，不能是 1.2。"""
        arts, _ = perf.extract_articles(self._browser_payload(5, 6))
        self.assertIsNone(arts[0]["read_rate"],
                          "阅读数大于展现数时应记 None，不能记成 >1 的比率")
        # 原始数必须还在——记 None 是为了不误用，不是为了隐藏事实。
        self.assertEqual(arts[0]["metrics"]["showCount"], 5)
        self.assertEqual(arts[0]["metrics"]["readCount"], 6)

    def test_normal_ratio_still_computed(self):
        """正常比率不受影响（防止把 clamp 写成「一律 None」）。"""
        arts, _ = perf.extract_articles(self._browser_payload(1000, 7))
        self.assertAlmostEqual(arts[0]["read_rate"], 0.007, places=6)

    def test_exactly_100_percent_is_kept(self):
        """恰好 100% 是合法边界（所有展现都被读了），不该被抹成 None。

        样本量取 200 而不是 10：这里要测的是「1.0 不被 clamp」，不是「小样本」——
        小样本已是 `MIN_SHOW_FOR_RATE` 的事（见 `TestMinShowForRate`）。
        """
        arts, _ = perf.extract_articles(self._browser_payload(200, 200))
        self.assertAlmostEqual(arts[0]["read_rate"], 1.0, places=6)


class TestMinShowForRate(unittest.TestCase):
    """样本太小的阅读率不进基准、也不进简报（MIN_SHOW_FOR_RATE）。

    2026-10-30 实测：10-27 那篇读回 `showCount=9 / readCount=8` → 88.89%。
    它没触发 `rate > 1` 的 clamp（8/9 < 1），于是被原样印进简报③段——
    而 9 个展现算出来的比率既不是「表现好」，也不是「标题该改」，
    它是「平台还没来得及补展现」。
    """

    def _browser_payload(self, show, read):
        return {
            "success": True, "source": "api_intercept", "count": 1,
            "items": [
                {"data": [{"assembleCell": {"itemCell": {
                    "articleBase": {"gidStr": "7701389368845500963",
                                    "title": "H20租金", "publishTime": 1792870926,
                                    "itemStatus": 20,
                                    "articleURL": "https://www.toutiao.com/item/1/"},
                    "itemCounter": {"showCount": show, "readCount": read},
                }}}]},
            ],
        }

    def test_tiny_sample_is_none(self):
        """9 展现 / 8 阅读 → None（就是今天那个 88.89%）。"""
        arts, _ = perf.extract_articles(self._browser_payload(9, 8))
        self.assertIsNone(arts[0]["read_rate"])
        self.assertEqual(arts[0]["metrics"]["showCount"], 9)
        self.assertEqual(arts[0]["metrics"]["readCount"], 8)

    def test_threshold_boundary(self):
        """阈值本身算得出来，差一个就不算——边界写成断言，免得日后飘。"""
        below, _ = perf.extract_articles(
            self._browser_payload(perf.MIN_SHOW_FOR_RATE - 1, 50))
        self.assertIsNone(below[0]["read_rate"])
        at, _ = perf.extract_articles(
            self._browser_payload(perf.MIN_SHOW_FOR_RATE, 50))
        self.assertAlmostEqual(at[0]["read_rate"], 0.5, places=6)

    def test_helper_is_the_single_implementation(self):
        """两条写路径必须共用 `read_rate_of()`，不能各留一份口径。

        ADR-0042 记过一次「三份拷贝漂移」：改了一边、另一边还是老口径，
        而两边看起来都正常。这里用源码级断言钉住——它们还在各自的循环里
        各写一份 `read/show`，就说明又漂了。
        """
        import inspect

        src = inspect.getsource(perf)
        self.assertEqual(src.count("read_rate_of(metrics)"), 2,
                         "两条写路径应各调用一次 read_rate_of；多/少都说明口径分家了")
        self.assertEqual(src.count("read / show"), 1,
                         "阅读率的除法只该出现在 read_rate_of 里（出现两次 = 有人又抄了一份）")


class TestKindClassification(unittest.TestCase):
    def test_draft_is_article(self):
        self.assertEqual(
            perf.classify_kind_api({}, "7698370074901627426", "", True), "article")

    def test_long_gid_is_article(self):
        self.assertEqual(
            perf.classify_kind_api({}, "7700222179094430248",
                                   "https://www.toutiao.com/item/7700222179094430248/",
                                   False), "article")

    def test_short_gid_is_weitoutiao(self):
        """ADR-0047：微头条的 gid ≤17 位，文章是 19 位。"""
        self.assertEqual(perf.classify_kind_api({}, "12345678901234567", "", False),
                         "weitoutiao")


if __name__ == "__main__":
    unittest.main()
