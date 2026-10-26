"""同轮兜底成功的源不算失败（ADR-0066）。

2026-10-26 18:00 采集实测：头条的日榜聚合接口返回 HTTP 500，原始接口兜底成功
（`recovered_by: raw_endpoint`），条目确实进池了 10 条。但候选池里同时出现：

    counts.sources_ok     = 7
    counts.sources_failed = 1
    failed_sources        = ["toutiao"]

7 个源算出 8 个结果，而那个「失效」的源其实供了 10 条。简报会照抄这组数字，
印出「源：7 成功 / 1 失败」+「失效源：toutiao」——一次假的失效告警。

ADR-0047 修的是**跨轮**恢复（上一轮失败、本轮成功要从 failures 里消失）；
这里漏掉的是**同轮**恢复：兜底成功就地发生，跨轮那条排除逻辑管不到。
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECT = ROOT / ".agents" / "skills" / "newsagg-collect" / "scripts" / "collect.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_pool():
    return {
        "collected_at": "2026-10-26T18:00:00+08:00",
        "source_meta": [{"source": "toutiao", "ok": True, "fallback": True}],
        "failures": [],
        "recoveries": [{"source": "toutiao", "error": "500",
                        "recovered_by": "raw_endpoint"}],
        "counts": {"fetched": 1, "kept": 1, "dropped": 0, "sources_ok": 1,
                   "sources_failed": 0, "kept_new": 1},
        "items": [{"source": "toutiao", "id": "1",
                   "first_seen": "2026-10-26T18:00:00+08:00"}],
        "dropped": [],
    }


def _existing_pool():
    return {
        "collected_at": "2026-10-26T13:00:00+08:00",
        "collection_runs": [],
        "source_meta": [{"source": "toutiao", "ok": True}],
        "failures": [],
        "recoveries": [{"source": "toutiao", "error": "旧的一次 500",
                        "recovered_by": "raw_endpoint"}],
        "counts": {"fetched": 1, "kept": 1, "dropped": 0, "sources_ok": 1,
                   "sources_failed": 0, "kept_new": 1},
        "items": [],
        "dropped": [],
    }


class RecoveredFailureIsNotAFailure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collect = _load("collect_mod", COLLECT)

    def test_recovered_failure_is_not_a_failure(self):
        raw = [
            {"source": "toutiao", "backend": "dailyhot",
             "error": "HTTP Error 500: Internal Server Error",
             "recovered_by": "raw_endpoint"},
            {"source": "csdn", "backend": "dailyhot", "error": "HTTP Error 500"},
        ]
        failures, recoveries = self.collect.split_failures(raw)

        self.assertEqual([f["source"] for f in failures], ["csdn"],
                         "兜底成功的源不该留在 failures 里")
        self.assertEqual([f["source"] for f in recoveries], ["toutiao"],
                         "兜底本身有信息量，不能丢弃")

    def test_all_recovered_means_zero_failed(self):
        raw = [{"source": "toutiao", "error": "500", "recovered_by": "raw_endpoint"}]
        failures, recoveries = self.collect.split_failures(raw)

        self.assertEqual(failures, [])
        self.assertEqual(len(recoveries), 1)
        # 关键：7 个源不能算出 8 个结果
        self.assertEqual(len(failures) + len(recoveries), 1)

    def test_fallback_that_also_failed_stays_a_failure(self):
        # 兜底也失败时没有 recovered_by，必须仍算失败——否则真正的失效会被藏起来
        raw = [{"source": "toutiao", "error": "500",
                "fallback_error": "HTTP Error 502"}]
        failures, recoveries = self.collect.split_failures(raw)

        self.assertEqual([f["source"] for f in failures], ["toutiao"])
        self.assertEqual(recoveries, [])

    def test_merge_keeps_recoveries_and_dedups_by_source(self):
        merged = self.collect.merge_candidates(_existing_pool(), _fresh_pool())

        self.assertEqual(len(merged["recoveries"]), 1, "同一个源只留最新一条")
        self.assertEqual(merged["recoveries"][0]["error"], "500")
        self.assertEqual(merged["counts"]["sources_failed"], 0)

    def test_merge_reports_real_failure_alongside_recovery(self):
        fresh = _fresh_pool()
        fresh["failures"] = [{"source": "github", "error": "HTTP Error 500"}]
        merged = self.collect.merge_candidates(_existing_pool(), fresh)

        self.assertEqual(merged["counts"]["sources_failed"], 1)
        self.assertEqual([f["source"] for f in merged["failures"]], ["github"])
        self.assertEqual([r["source"] for r in merged["recoveries"]], ["toutiao"])


if __name__ == "__main__":
    unittest.main()
