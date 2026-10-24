"""热度轨迹的消费者：位次趋势（ADR-0056）。

这个仓库此前没有任何测试文件（ADR-0043 的「验证方式」声称跑过单元测试，但测试
本身从未入库）。本文件是第一个，用标准库 `unittest`——两个解释器
（`.uv-tools/wewrite/bin/python` 3.12、系统 `python3` 3.9）都没装 pytest，而
`run_all.sh` 用裸 `python3` 调这些脚本，所以测试不能引入依赖。

跑法：`python3 -m unittest discover`（仓库根）或 `python3 -m unittest discover -s tests -v`
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECT = ROOT / ".agents" / "skills" / "newsagg-collect" / "scripts" / "collect.py"
TRIAGE = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "triage.py"


def _load(name: str, path: Path):
    """按路径加载脚本模块。

    不能用 `import collect`：两个脚本同名不同目录，且分拣脚本会自己 sys.path 注入
    兄弟模块 `cardfm`。按路径加载能保证测的就是仓库里这份。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load("newsagg_collect", COLLECT)
triage = _load("newsagg_triage", TRIAGE)


# 测试里的「榜单深度」默认取 10：位次是 `(rank-1)/(rank_total-1)`，深度越小
# 名次变化越明显。需要验证「深度会不会漂移」的用例自己显式传 total。
DEFAULT_BOARD_DEPTH = 10


def obs(at: str, hot, rank=None, total=DEFAULT_BOARD_DEPTH) -> dict:
    """造一条观测。`rank` 与 `rank_total` 必须成对出现——位次的刻度是上游榜单深度。

    只给 rank 不给深度时，消费者会整条轨迹退回 hot 近似（那是设计上的降级，
    有专门的用例覆盖），所以默认把深度一起写上，让其余用例专注射在判定逻辑上。
    """
    entry = {"at": at, "hot": hot}
    if rank is not None:
        entry["rank"] = rank
        if total is not None:
            entry["rank_total"] = total
    return entry


def item(source: str, title: str, history: list, seen: bool = True, hot=None) -> dict:
    return {
        "source": source,
        "id": title,
        "title": title,
        "hot": hot if hot is not None else history[-1].get("hot"),
        "rank": history[-1].get("rank"),
        "hot_history": history,
        "seen_in_last_run": seen,
        "first_seen": history[0]["at"],
    }


class NormalizeHotHistoryTest(unittest.TestCase):
    """`normalize_hot_history` 是**重建**字典，任何它不认识的键都会被静默抹掉。"""

    def test_keeps_unknown_keys(self):
        # 这是 ADR-0056 落码时踩到的坑：原实现只留 at/hot/hot_raw，
        # 于是刚写进观测的 rank 会在下一次合并时消失，且不报错。
        out = collect.normalize_hot_history([{"at": "t1", "hot": 10, "rank": 3}])
        self.assertEqual(out, [{"at": "t1", "hot": 10, "rank": 3}])

    def test_normalizes_string_hot_and_records_raw(self):
        out = collect.normalize_hot_history([{"at": "t1", "hot": "40,798", "rank": 2}])
        self.assertEqual(out[0]["hot"], 40798)
        self.assertEqual(out[0]["hot_raw"], "40,798")
        self.assertEqual(out[0]["rank"], 2)

    def test_tolerates_none_and_junk(self):
        out = collect.normalize_hot_history([{"at": "t1", "hot": None}, {"at": "t2", "hot": "1.2万"}, "junk", 7])
        self.assertEqual(len(out), 2)
        self.assertIsNone(out[0]["hot"])
        self.assertIsNone(out[1]["hot"])

    def test_empty(self):
        self.assertEqual(collect.normalize_hot_history(None), [])
        self.assertEqual(collect.normalize_hot_history([]), [])


class HotObservationTest(unittest.TestCase):
    def test_writes_rank_when_present(self):
        self.assertEqual(
            collect.hot_observation("t1", {"hot": 44, "rank": 3}),
            {"at": "t1", "hot": 44, "rank": 3},
        )

    def test_writes_rank_total_with_rank(self):
        # 深度不跟着 rank 进观测，分拣就只能退回池内名次——那会造假爬升
        self.assertEqual(
            collect.hot_observation("t1", {"hot": 44, "rank": 3, "rank_total": 50}),
            {"at": "t1", "hot": 44, "rank": 3, "rank_total": 50},
        )

    def test_omits_rank_when_absent(self):
        self.assertEqual(collect.hot_observation("t1", {"hot": 44}), {"at": "t1", "hot": 44})


class NormalizeRankTotalTest(unittest.TestCase):
    """榜单深度决定位次的刻度，读不出就必须承认读不出。"""

    def test_reads_int_and_numeric_string(self):
        self.assertEqual(collect.normalize_rank_total(50), 50)
        self.assertEqual(collect.normalize_rank_total("50"), 50)

    def test_none_when_missing_or_junk(self):
        for junk in (None, "", "abc", True, []):
            self.assertIsNone(collect.normalize_rank_total(junk), junk)


class MergeCandidatesRankTest(unittest.TestCase):
    def test_rank_survives_a_merge_round(self):
        existing = {
            "collected_at": "2026-10-24T08:00:00+08:00",
            "items": [item("36kr", "A", [obs("2026-10-24T08:00:00+08:00", 10, rank=1)])],
        }
        fresh = {
            "collected_at": "2026-10-24T13:00:00+08:00",
            "items": [item("36kr", "A", [obs("2026-10-24T13:00:00+08:00", 22, rank=2)])],
        }
        merged = collect.merge_candidates(existing, fresh)
        hist = merged["items"][0]["hot_history"]
        self.assertEqual([h.get("rank") for h in hist], [1, 2])
        self.assertEqual([h["hot"] for h in hist], [10, 22])

    def test_first_seen_preserved_and_absent_items_kept(self):
        existing = {
            "collected_at": "2026-10-24T08:00:00+08:00",
            "items": [item("36kr", "B", [obs("2026-10-24T08:00:00+08:00", 10, rank=1)])],
        }
        fresh = {"collected_at": "2026-10-24T13:00:00+08:00", "items": []}
        merged = collect.merge_candidates(existing, fresh)
        kept = merged["items"][0]
        self.assertFalse(kept["seen_in_last_run"])
        self.assertEqual(kept["first_seen"], "2026-10-24T08:00:00+08:00")


class TrendTest(unittest.TestCase):
    """位次趋势的判定。所有断言都写成「同源同快照内的相对位置」。"""

    def test_climbing_when_position_improves(self):
        # A 从榜尾爬到榜首，B 从榜首掉到榜尾：同快照内互为依据。
        items = [
            item("36kr", "A", [obs("08:00", 2, rank=3), obs("13:00", 44, rank=1)]),
            item("36kr", "B", [obs("08:00", 44, rank=1), obs("13:00", 2, rank=3)]),
        ]
        trends, summary = triage.compute_trends(items)
        by_title = {t["title"]: t["trend"] for t in trends}
        self.assertEqual(by_title["A"]["state"], "climbing")
        self.assertEqual(by_title["B"]["state"], "cooling")
        self.assertLess(by_title["A"]["delta"], 0)
        self.assertEqual(summary["by_state"]["climbing"], 1)
        self.assertEqual(summary["by_state"]["cooling"], 1)

    def test_steady_inside_threshold(self):
        items = [
            item("36kr", "A", [obs("08:00", 44, rank=1), obs("13:00", 40, rank=1)]),
            item("36kr", "B", [obs("08:00", 2, rank=2), obs("13:00", 2, rank=2)]),
        ]
        trends, _ = triage.compute_trends(items)
        self.assertEqual({t["trend"]["state"] for t in trends}, {"steady"})

    def test_unknown_when_only_one_observation(self):
        items = [item("36kr", "A", [obs("13:00", 44, rank=1)])]
        trends, summary = triage.compute_trends(items)
        self.assertEqual(trends[0]["trend"]["state"], "unknown")
        self.assertIn("不足", trends[0]["trend"]["reason"])
        # 全池不可算时必须显式说「还看不出」，而不是让模型读成「都很稳」
        self.assertIn("看不出", summary["note"])

    def test_absent_when_not_seen_in_last_run(self):
        items = [item("36kr", "A", [obs("08:00", 44, rank=1), obs("13:00", 40, rank=2)], seen=False)]
        trends, _ = triage.compute_trends(items)
        self.assertEqual(trends[0]["trend"]["state"], "absent")

    def test_unknown_when_last_observation_is_stale(self):
        # 末观测不在最新快照上 → 轨迹可能已中断，不能拿旧位置和最新快照比。
        # C 是陪衬：位次是**快照内相对量**，单条目快照算不出百分位（见下一个用例）。
        items = [
            item("36kr", "A", [obs("08:00", 44, rank=1), obs("13:00", 40, rank=2)]),
            item("36kr", "B", [obs("18:00", 10, rank=1)]),
            item("36kr", "C", [obs("08:00", 2, rank=2), obs("13:00", 2, rank=1),
                               obs("18:00", 2, rank=2)]),
        ]
        trends, _ = triage.compute_trends(items)
        by_title = {t["title"]: t["trend"] for t in trends}
        self.assertEqual(by_title["A"]["state"], "unknown")
        self.assertIn("最新快照", by_title["A"]["reason"])

    def test_single_item_snapshot_is_not_usable_in_hot_basis(self):
        # hot 口径是**同快照内降序重排**的相对刻度：快照里只有一条条目时百分位恒为 0。
        # 若把它当 0 用，这类源会永远 steady——那是把「量不出」说成「没动」。
        items = [item("github", "A", [obs("08:00", 10), obs("13:00", 20)])]
        trends, summary = triage.compute_trends(items)
        self.assertEqual(trends[0]["trend"]["state"], "unknown")
        self.assertIn("不足", trends[0]["trend"]["reason"])
        self.assertEqual(summary["by_state"]["unknown"], 1)

    def test_single_item_snapshot_is_usable_in_rank_basis(self):
        # rank 口径是**绝对刻度**（按上游榜单深度归一），不依赖同快照的其它条目，
        # 所以「一个源每次只有一条进池」也能算出趋势——这是 rank 相对 hot 的
        # 实质优势，不是实现细节。
        items = [item("github", "A", [obs("08:00", 10, rank=8), obs("13:00", 20, rank=2)])]
        trends, _ = triage.compute_trends(items)
        self.assertEqual(trends[0]["trend"]["position_source"], "rank")
        self.assertEqual(trends[0]["trend"]["state"], "climbing")

    def test_basis_is_chosen_per_item_not_per_snapshot(self):
        # 换尺日（2026-10-24 实测）：观测从这天起才带 rank，于是同一条候选的前几次
        # 观测只能按 hot 重排、最后一次却有 rank。跨口径相减等于拿两把尺子的读数
        # 相减，所以口径要**整条轨迹统一**，而不是按快照统一。
        #
        # A 只有 1 条带 rank 的观测（算不出变化）→ 整条退回 hot，用 08:00 与 18:00
        #   两次 hot 位次（都是 hot 尺子，可比）。
        # B 有 2 条带 rank 的观测 → 用 rank（上游真实位次，比 hot 重排准）。
        items = [
            item("36kr", "A", [obs("08:00", 100), obs("18:00", 950, rank=1)]),
            item("36kr", "B", [obs("13:00", 10, rank=2), obs("18:00", 20, rank=1)]),
            # 陪衬：位次是快照内的相对量，每个快照至少要两条才量得出。
            item("36kr", "D", [obs("08:00", 50)]),
            item("36kr", "C", [obs("13:00", 60, rank=1), obs("18:00", 70, rank=2)]),
        ]
        trends, summary = triage.compute_trends(items)
        by_title = {t["title"]: t["trend"] for t in trends}
        self.assertEqual(by_title["A"]["position_source"], "hot")
        self.assertEqual(by_title["A"]["observations"], 2)
        self.assertEqual(by_title["B"]["position_source"], "rank")
        # 关键：不再出现「两种尺子混用」这个状态
        self.assertNotIn("mixed", summary["by_position_source"])

    def test_degrades_to_hot_order_without_rank(self):
        # 存量观测没有 rank（实测 2026-10-21/22/23 三天 n_rank_pairs = 0）：
        # 同快照内按 hot 降序重排，位次仍然可比。
        items = [
            item("juejin", "A", [obs("08:00", 100), obs("13:00", 900)]),
            item("juejin", "B", [obs("08:00", 900), obs("13:00", 100)]),
            item("juejin", "C", [obs("08:00", 500), obs("13:00", 500)]),
        ]
        trends, summary = triage.compute_trends(items)
        by_title = {t["title"]: t["trend"] for t in trends}
        self.assertEqual(by_title["A"]["state"], "climbing")
        self.assertEqual(by_title["B"]["state"], "cooling")
        self.assertEqual(by_title["A"]["position_source"], "hot")
        self.assertEqual(summary["by_position_source"], {"hot": 3})

    def test_partial_rank_does_not_poison_the_whole_snapshot(self):
        # rank 是逐条的绝对刻度，所以「同一快照里只有部分观测带 rank」不再需要整片
        # 退回 hot：带 rank 的条目自己算得出来。这与第一版（按快照统一口径）不同，
        # 那版在换尺日会把 94/115 条打成 mixed。
        items = [
            item("36kr", "A", [obs("08:00", 100, rank=1), obs("13:00", 900, rank=1)]),
            item("36kr", "B", [obs("08:00", 900), obs("13:00", 100)]),
        ]
        trends, _ = triage.compute_trends(items)
        by_title = {t["title"]: t["trend"] for t in trends}
        self.assertEqual(by_title["A"]["position_source"], "rank")
        self.assertEqual(by_title["B"]["position_source"], "hot")
        self.assertNotIn("mixed", _["by_position_source"])

    def test_rank_position_uses_board_depth_not_pool_size(self):
        """回归用例：池内名次会在池子缩水时造假爬升（实测踩过一次）。

        实测原始数据（toutiao 2026-10-24 16:35→16:46）：「辽宁舰编队有了"豪华版本"」
        上游 rank **22 → 25**（退步 3 位），但赛道预筛后的池子从 7 条缩到 5 条，
        用池内名次算位次，它从 0.5 变成 0.25 → delta -0.25 → 被判 climbing。
        榜单深度不变（total=50），位次 0.447 → 0.533 → delta +0.086，方向正确。

        这里用深度 50 与实测同一组数字，断言「退步就是退步」。
        """
        a = item("toutiao", "辽宁舰", [obs("16:35", 1, rank=22, total=50),
                                     obs("16:46", 1, rank=25, total=50)])
        # 陪衬：池子缩水意味着有的条目这一轮没再出现
        b = item("toutiao", "父亲脑梗", [obs("16:35", 1, rank=1, total=50),
                                        obs("16:46", 1, rank=1, total=50)])
        trends, _ = triage.compute_trends([a, b])
        by_title = {t["title"]: t["trend"] for t in trends}
        self.assertEqual(by_title["辽宁舰"]["position_source"], "rank")
        self.assertGreater(by_title["辽宁舰"]["delta"], 0)
        self.assertEqual(by_title["辽宁舰"]["state"], "steady")
        # 榜首没动
        self.assertEqual(by_title["父亲脑梗"]["delta"], 0)

    def test_rank_without_board_depth_degrades_to_hot(self):
        # 存量观测只有 rank、没有 rank_total（换尺当天的尾巴）→ 不拿池内名次冒充位次，
        # 整条退回 hot 近似。宁可承认「近似」，也不要一个看起来更准的错读数。
        items = [
            item("36kr", "A", [obs("08:00", 100, rank=1, total=None),
                               obs("13:00", 900, rank=1, total=None)]),
            item("36kr", "B", [obs("08:00", 900, rank=2, total=None),
                               obs("13:00", 100, rank=2, total=None)]),
        ]
        trends, _ = triage.compute_trends(items)
        self.assertEqual({t["trend"]["position_source"] for t in trends}, {"hot"})

    def test_tolerates_dirty_hot_values(self):
        # 存量轨迹里实测有 15 条 str、76 条 None：不能因为一条脏数据整轮崩掉
        items = [
            item("36kr", "A", [obs("08:00", "40,798"), obs("13:00", None)]),
            item("36kr", "B", [obs("08:00", 1), obs("13:00", 2)]),
        ]
        trends, _ = triage.compute_trends(items)
        self.assertEqual(len(trends), 2)
        self.assertIn(trends[0]["trend"]["state"], ("unknown", "climbing", "steady", "cooling"))

    def test_summary_exposes_thresholds_and_coverage(self):
        items = [item("36kr", "A", [obs("08:00", 44, rank=1), obs("13:00", 2, rank=3)])]
        _, summary = triage.compute_trends(items)
        self.assertEqual(summary["thresholds"]["climb_at_or_below"], triage.TREND_CLIMB_DELTA)
        self.assertEqual(summary["thresholds"]["min_observations"], triage.TREND_MIN_OBSERVATIONS)
        self.assertEqual(summary["snapshots"], 2)
        self.assertIn("参与排序", summary["usage"])

    def test_summary_admits_hot_based_deltas_are_approximate(self):
        # 用 hot 重排得到的位次是**近似**（hot 与真实 rank 在同源内只是粗略相关）。
        # 不写清这一点，模型会把这些 delta 当成和上游位次一样准。
        items = [
            item("juejin", "A", [obs("08:00", 100), obs("13:00", 900)]),
            item("juejin", "B", [obs("08:00", 900), obs("13:00", 100)]),
        ]
        _, summary = triage.compute_trends(items)
        self.assertIn("近似", summary["note"])
        # 全用 rank 时不该有这句（不制造无关噪音）
        ranked = [
            item("36kr", "A", [obs("08:00", 2, rank=3), obs("13:00", 44, rank=1)]),
            item("36kr", "B", [obs("08:00", 44, rank=1), obs("13:00", 2, rank=3)]),
        ]
        _, summary2 = triage.compute_trends(ranked)
        self.assertNotIn("近似", summary2["note"])

    def test_input_package_carries_trend_fields(self):
        # 位次是**相对**的：一个快照里只有一条时没有「位置」可言（pct 恒 0），
        # 所以这里必须给两条，才谈得上谁爬升。
        candidates = {
            "counts": {"kept": 2},
            "items": [
                item("36kr", "A", [obs("08:00", 2, rank=3), obs("13:00", 44, rank=1)]),
                item("36kr", "B", [obs("08:00", 44, rank=1), obs("13:00", 2, rank=3)]),
            ],
        }
        pkg = triage.build_triage_input(candidates, [], {})
        by_title = {c["title"]: c["trend"] for c in pkg["candidates"]}
        self.assertEqual(by_title["A"]["state"], "climbing")
        self.assertIn("trend_summary", pkg)
        self.assertEqual(pkg["trend_summary"]["by_state"]["climbing"], 1)

    def test_does_not_mutate_input_items(self):
        original = item("36kr", "A", [obs("08:00", 44, rank=1), obs("13:00", 2, rank=2)])
        triage.compute_trends([original])
        self.assertNotIn("trend", original)


if __name__ == "__main__":
    unittest.main()
