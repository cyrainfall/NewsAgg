"""简报要呈现位次趋势的**覆盖率**（ADR-0056）。

为什么值得一个测试：trend 是分拣排序的依据之一，但它在「今天只采了一次」时
整池退化成 `unknown`。若简报只写「分拣已完成」，读的人会以为排序看过爬升——
实际那一轮退回了当前热度。把「看不见」印成「没变化」是这一整类缺陷的共同形状
（ADR-0042 的 `kept_new`、ADR-0047 的失效源都同型），所以这里钉住的是**渲染**，
不是算法（算法在 test_hot_trend.py）。

跑法：`python3 -m unittest discover`（仓库根）或 `python3 -m unittest discover -s tests -v`
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


brief = _load("newsagg_brief", BRIEF)

CANDIDATES = {"counts": {"fetched": 276, "kept": 108, "dropped": 168,
                         "sources_ok": 7, "sources_failed": 0},
              "collection_runs": [{"at": "2026-10-24T14:31:00+08:00", "kept_new": 108}],
              "failures": []}


def render(trend_summary) -> str:
    return "\n".join(brief.section_overview(CANDIDATES, None, trend_summary))


class TrendCoverageInBriefTest(unittest.TestCase):
    def test_reports_computable_fraction_and_states(self):
        ts = {"by_state": {"climbing": 22, "steady": 33, "cooling": 5, "unknown": 28, "absent": 38},
              "by_position_source": {"hot": 115, "none": 11},
              "snapshots": 19,
              "thresholds": {"climb_at_or_below": -0.1, "cool_at_or_above": 0.1,
                             "min_observations": 2}}
        text = render(ts)
        self.assertIn("60/126 条可算", text)
        self.assertIn("climbing 22", text)
        self.assertIn("unknown 28", text)
        self.assertIn("不参与排序", text)
        self.assertIn("hot 115", text)
        # 可算时不该冒出「不可用」告警
        self.assertNotIn("本轮 trend 不可用", text)

    def test_flags_total_degradation_explicitly(self):
        # 2026-10-24 的真实形态：只有 1 次采集 → 108 条全 unknown。
        ts = {"by_state": {"climbing": 0, "steady": 0, "cooling": 0, "unknown": 108, "absent": 0},
              "by_position_source": {"hot": 104, "none": 4},
              "snapshots": 7}
        text = render(ts)
        self.assertIn("0/108 条可算", text)
        # 关键：必须说「不可用 / 退回当前热度」，并且否认「都没动」这个误读
        self.assertIn("本轮 trend 不可用", text)
        self.assertIn("退回", text)
        self.assertIn("不是「所有条目都没动」", text)

    def test_absent_only_pool_is_also_degraded(self):
        # 整池掉出榜单同样算「算不出趋势」，不能因为 absent 有数字就当有信号
        ts = {"by_state": {"climbing": 0, "steady": 0, "cooling": 0, "unknown": 0, "absent": 9}}
        text = render(ts)
        self.assertIn("本轮 trend 不可用", text)

    def test_absent_omitted_when_input_package_predates_the_feature(self):
        # 2026-10-23 及更早的输入包里没有 trend_summary：不能印出半行空白，
        # 也不能因此崩掉（简报是心跳，缺字段不能让它失败）。
        self.assertNotIn("位次趋势", render(None))
        self.assertNotIn("位次趋势", render({}))
        self.assertNotIn("位次趋势", render({"by_state": {}}))
        # 其余段落照常
        self.assertIn("采集 276 条", render(None))


if __name__ == "__main__":
    unittest.main()
