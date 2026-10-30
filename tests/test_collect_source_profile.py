"""源级例外：github 的请求超时与预筛口径（ADR-0085）。

为什么单独一个文件：这两处都不是算法，是**从上游借来的常量**——
超时必须大于服务端的重试预算、预筛必须匹配对字段。它们的失效形状是**静默**的：
failures 里照样写着 `timed out`、池子里照样少一个源，两个都不报错，也没人会发现
「记下来的错误文本本身是错的」。所以两条断言都用上游代码里的真实数字当依据
（`reference/DailyHotApi/src/routes/github.ts` 的 `maxRetries = 3` 与 20 秒 abort），
而不是写死一个魔法数——上游改了重试次数，这里会先红。

跑法：`python3 -m unittest discover -s tests -t .`（仓库根，标准库 unittest，无第三方依赖）
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECT = ROOT / ".agents" / "skills" / "newsagg-collect" / "scripts" / "collect.py"
GITHUB_ROUTE = ROOT / "reference" / "DailyHotApi" / "src" / "routes" / "github.ts"


def _load(name: str, path: Path):
    """按路径加载脚本模块（不能 `import collect`：两个脚本同名不同目录）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load("newsagg_collect_source_profile", COLLECT)


class TestGithubTimeoutCoversServerRetryBudget(unittest.TestCase):
    """客户端超时必须**大于**服务端的重试预算，否则真错误永远被自己的弃权盖住。"""

    def test_timeout_is_at_least_the_retry_budget(self):
        # 上游常量：`src/routes/github.ts:112` maxRetries = 3，`:119` 每次 abort 在 20 秒
        retries, per_attempt_seconds = 3, 20
        self.assertIn("github", collect.SOURCE_TIMEOUTS, msg="github 的超时需要显式覆盖")
        self.assertGreaterEqual(
            collect.SOURCE_TIMEOUTS["github"],
            retries * per_attempt_seconds,
            msg=(
                "github 的客户端超时小于服务端重试预算：服务端要 3 × 20 秒才回话，"
                "客户端会先弃权并记下 `timed out`，把真正的原因（fetch failed / "
                "TLS 被中断）挡在账本之外（2026-10-30 实测）"
            ),
        )

    def test_route_constants_are_what_the_guard_assumes(self):
        """守住上面那个断言的前提：上游的重试次数/单次超时变了，这条先红。"""
        src = GITHUB_ROUTE.read_text(encoding="utf-8", errors="replace")
        self.assertIn("maxRetries = 3", src, msg="github 路由的重试次数变了，重算超时预算")
        self.assertIn("controller.abort(), 20000", src, msg="github 路由的单次 abort 变了，重算超时预算")

    def test_override_is_scoped_to_github(self):
        self.assertEqual(75, collect.source_timeout("github", 20))
        # 其余源照旧用 --timeout，不被这张表顺手改掉
        self.assertEqual(20, collect.source_timeout("36kr", 20))
        self.assertEqual(30, collect.source_timeout("juejin", 30))

    def test_collect_uses_per_source_timeout(self):
        seen = {}

        def fake_http_json(url: str, timeout: int = 20):
            seen[url.rsplit("/", 1)[-1]] = timeout
            return {"code": 200, "total": 1, "data": [{"title": "某条目"}]}

        original = collect.http_json
        collect.http_json = fake_http_json
        try:
            collect.collect(["toutiao", "github"], "http://127.0.0.1:6688", 20)
        finally:
            collect.http_json = original
        self.assertEqual(20, seen["toutiao"])
        self.assertEqual(75, seen["github"])


class TestPrescreenFieldScope(unittest.TestCase):
    """预筛默认只看 title；github 额外看 desc（title 是英文仓库名，命中不了中文词表）。"""

    def _item(self, source: str, title: str, desc: str = "") -> dict:
        return {"source": source, "id": "1", "title": title, "desc": desc,
                "hot": 100, "url": "", "cover": "", "rank": 1, "rank_total": 15}

    def test_github_matches_desc(self):
        item = self._item("github", "OpenMausBot", "An open multi-Agent framework")
        kept, dropped = collect.niche_prescreen([item], ["Agent"])
        self.assertEqual([], dropped)
        self.assertEqual(1, len(kept))
        self.assertIn("Agent", kept[0]["niche_hits"])

    def test_other_sources_still_title_only(self):
        """同一个条目换个源就不该被 desc 救回来——否则等于给全库放宽口径。"""
        item = self._item("juejin", "OpenMausBot", "An open multi-Agent framework")
        kept, dropped = collect.niche_prescreen([item], ["Agent"])
        self.assertEqual([], kept)
        self.assertEqual(1, len(dropped))

    def test_title_hit_still_works_for_github(self):
        item = self._item("github", "claude-mem", "Unrelated english blurb")
        kept, _ = collect.niche_prescreen([item], ["Claude"])
        self.assertEqual(1, len(kept))

    def test_empty_desc_does_not_crash(self):
        item = {k: v for k, v in self._item("github", "OpenBot").items() if k != "desc"}
        kept, dropped = collect.niche_prescreen([item], ["Agent"])
        self.assertEqual([], kept)
        self.assertEqual(1, len(dropped))


class TestNormalizeCarriesDesc(unittest.TestCase):
    def test_desc_is_kept(self):
        payload = {"code": 200, "total": 1,
                   "data": [{"title": "claude-mem", "desc": "Persistent context for agents"}]}
        items, _ = collect.normalize_dailyhot("github", payload)
        self.assertEqual("Persistent context for agents", items[0]["desc"])

    def test_missing_desc_becomes_empty_string(self):
        payload = {"code": 200, "total": 1, "data": [{"title": "某条目"}]}
        items, _ = collect.normalize_dailyhot("36kr", payload)
        self.assertEqual("", items[0]["desc"])

    def test_description_alias_is_accepted(self):
        payload = {"code": 200, "total": 1,
                   "data": [{"title": "OpenBot", "description": "desc from alias field"}]}
        items, _ = collect.normalize_dailyhot("github", payload)
        self.assertEqual("desc from alias field", items[0]["desc"])


if __name__ == "__main__":
    unittest.main()
