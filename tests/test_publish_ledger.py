"""发布账本的跨环境合并（ADR-0074）。

为什么值得一个测试：账本是**唯一**把平台作品关联回本地成稿的东西
（GLOSSARY「发布账本」条）。它此前不入库，服务器上就没有 → 实测 11 篇
只匹配 9 篇。这个测试钉住的是**合并规则**，因为合并规则一旦写错，
症状是「文章数据在、关联断了」——和没写一样，但更难查。

钉住的几条：
- 归档独有的记录必须保留（另一个环境发的文章不该被本地账本抹掉）。
- 同一 id 取更新的那份（本地标了 `deleted_at` 要能同步到归档）。
- 平手时运行时胜（当前环境刚写的版本是新鲜的）。
- 派生键 `at|slug|mode` 在两台机器上算出来一样。
- 归档写入前必须过凭证扫描，且**跳过**而不是报错（发布已经成功了）。
- `sync_archive` 幂等。

跑法：`python3 -m unittest discover -s tests -v`
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / ".agents" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import publish_ledger as pl  # noqa: E402


def _write(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class MergeRuleTest(unittest.TestCase):
    def test_archive_only_records_are_kept(self) -> None:
        """另一个环境发的文章，本地账本没有它 → 合并后必须还在。"""
        archive = [{"record_id": "a", "at": "2026-10-26T21:00:00+08:00", "gid": "1"}]
        merged = pl.merge([], archive)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["gid"], "1")

    def test_newer_runtime_wins(self) -> None:
        """同一 id 出现两份，取更新的那份。"""
        archive = [{"record_id": "a", "at": "2026-10-26T21:00:00+08:00",
                    "title": "旧的", "updated_at": "2026-10-26T21:00:00+08:00"}]
        runtime = [{"record_id": "a", "at": "2026-10-26T21:00:00+08:00",
                    "title": "新的", "updated_at": "2026-10-27T09:00:00+08:00"}]
        merged = pl.merge(runtime, archive)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "新的")

    def test_tie_goes_to_runtime(self) -> None:
        """平手时运行时胜——当前环境刚写的版本是新鲜的。"""
        archive = [{"record_id": "a", "at": "2026-10-26T21:00:00+08:00", "title": "归档版"}]
        runtime = [{"record_id": "a", "at": "2026-10-26T21:00:00+08:00", "title": "运行时版"}]
        merged = pl.merge(runtime, archive)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "运行时版")

    def test_derived_id_is_stable_across_environments(self) -> None:
        """旧记录没有 record_id，派生键必须在两台机器上一样。"""
        rec = {"at": "2026-10-26T21:00:00+08:00", "slug": "2026-10-26-x", "mode": "publish"}
        self.assertEqual(pl.record_id(rec), pl.record_id(dict(rec)))
        self.assertIn("2026-10-26-x", pl.record_id(rec))

    def test_make_record_id_is_idempotent(self) -> None:
        rec = {"at": "2026-10-26T21:00:00+08:00", "slug": "s", "mode": "publish"}
        first = pl.make_record_id(rec)
        second = pl.make_record_id(rec)
        self.assertEqual(first, second)
        self.assertIn("record_id", rec)

    def test_bad_line_does_not_break_reading(self) -> None:
        """账本是追加写的，进程被杀可能留半行——不该让读者崩。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l.jsonl"
            p.write_text('{"a": 1}\n半行\n{"b": 2}\n', encoding="utf-8")
            recs = pl.read_records(p)
            self.assertEqual(len(recs), 2)


class CredentialScanTest(unittest.TestCase):
    def test_credential_key_is_detected(self) -> None:
        self.assertTrue(pl.has_credentials({"sessionid": "abc"}))
        self.assertTrue(pl.has_credentials({"nested": {"ttwid": "x"}}))

    def test_credential_value_pattern_is_detected(self) -> None:
        self.assertTrue(pl.has_credentials({"note": "msToken=abc123"}))
        self.assertTrue(pl.has_credentials({"note": "a_bogus: xyz"}))

    def test_narrative_text_is_not_a_credential(self) -> None:
        """账本里出现过一次误报：叙述文字提到 `page.keyboard.type`。

        这类命中不该让整条记录被跳过——跳过了账本就会说谎，而账本是唯一
        可信记录。所以扫描只对键名与「参数名=值」形态生效。
        """
        rec = {"correction_note": "用 page.keyboard.type 逐字输入标题"}
        self.assertFalse(pl.has_credentials(rec))

    def test_ordinary_record_passes(self) -> None:
        rec = {"at": "2026-10-26T21:00:00+08:00", "slug": "s", "title": "标题",
               "gid": "7700", "verdict": "title_found"}
        self.assertFalse(pl.has_credentials(rec))


class SyncArchiveTest(unittest.TestCase):
    def _repo(self, td: str) -> Path:
        root = Path(td)
        return root

    def test_sync_writes_archive_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            runtime = pl.runtime_path(root)
            _write(runtime, [
                {"record_id": "a", "at": "2026-10-26T21:00:00+08:00", "gid": "1"},
                {"record_id": "b", "at": "2026-10-27T21:00:00+08:00", "gid": "2"},
            ])
            res1 = pl.sync_archive(root)
            self.assertTrue(res1["ok"])
            self.assertEqual(res1["written"], 2)
            first = pl.archive_path(root).read_text(encoding="utf-8")
            res2 = pl.sync_archive(root)
            self.assertEqual(res2["written"], 2)
            self.assertEqual(pl.archive_path(root).read_text(encoding="utf-8"), first)

    def test_archive_survives_runtime_loss(self) -> None:
        """运行时账本被清空（换机、清运行时）时，归档里的事实不该丢。"""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            _write(pl.runtime_path(root), [
                {"record_id": "a", "at": "2026-10-26T21:00:00+08:00", "gid": "1"},
            ])
            pl.sync_archive(root)
            pl.runtime_path(root).unlink()
            merged = pl.read_merged(root)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["gid"], "1")

    def test_archive_records_carry_explicit_record_id(self) -> None:
        """归档里的每条都写明 `record_id`，不靠派生键过日。

        派生键（`at|slug|mode`）够用，但显式写出来更耐改：将来补录一条
        记录时若 `at` 写错一次，派生键会分裂成两个 id，而显式 id 不会。
        """
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            _write(pl.runtime_path(root), [
                {"at": "2026-10-26T21:00:00+08:00", "slug": "s1", "mode": "publish"},
            ])
            pl.sync_archive(root)
            recs = pl.read_records(pl.archive_path(root))
            self.assertEqual(len(recs), 1)
            self.assertTrue(recs[0].get("record_id"))
            self.assertEqual(recs[0]["record_id"], pl.record_id(recs[0]))

    def test_credential_record_is_skipped_not_fatal(self) -> None:
        """含凭证的记录被跳过，但同步本身不失败。"""
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            _write(pl.runtime_path(root), [
                {"record_id": "a", "at": "2026-10-26T21:00:00+08:00", "gid": "1"},
                {"record_id": "b", "at": "2026-10-26T22:00:00+08:00",
                 "sessionid": "leaked"},
            ])
            res = pl.sync_archive(root)
            self.assertTrue(res["ok"])
            self.assertEqual(res["skipped_for_credentials"], 1)
            self.assertEqual(res["written"], 1)
            content = pl.archive_path(root).read_text(encoding="utf-8")
            self.assertNotIn("leaked", content)


class DailyLimitGateTest(unittest.TestCase):
    """「每日 1 篇」这道闸的门源（ADR-0014 / ADR-0059）。

    ## 为什么这个测试必须存在

    这道闸从 ADR-0074 落地起就**一直在空转**：`publish.py` 的
    `repo_root_of()` 上溯了三级而不是两级，于是它去找**仓库父目录**下的
    账本——那里什么都没有，`read_merged()` 恒返回空，`count_published_today()`
    恒返回 `[]`，闸门永远放行。

    它此前没有任何测试覆盖，而闸门「不报错、只是不拦」的失败形态意味着
    症状永远不会显现，直到某天真发了两篇。ADR-0057 的判据在这里正好适用：
    **测试只存在于有东西跑它的时候。**

    钉住两件事：
    1. `repo_root_of()` 必须真的指回仓库根（`<root>/.newsagg/publish-ledger.jsonl`
       的上两级）。
    2. 端到端：写一条今天真发的记录 → 闸门必须看见它。
    """

    def _load_publish(self):
        import importlib.util

        path = ROOT / ".agents" / "skills" / "newsagg-publish" / "scripts" / "publish.py"
        spec = importlib.util.spec_from_file_location("_pub_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod

    def test_repo_root_of_points_at_repo_root(self) -> None:
        """账本是 `<root>/.newsagg/publish-ledger.jsonl`，上两级就是根。"""
        pub = self._load_publish()
        fake_root = Path(tempfile.gettempdir()) / "newsagg-repo-x"
        ledger = fake_root / ".newsagg" / "publish-ledger.jsonl"
        self.assertEqual(pub.repo_root_of(ledger), fake_root.resolve())

    def test_gate_counts_todays_publish(self) -> None:
        """今天真发了一篇 → 闸门必须数到 1（这正是空转时做不到的）。"""
        pub = self._load_publish()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = root / ".newsagg" / "publish-ledger.jsonl"
            _write(ledger, [
                {"at": "2026-10-28T00:44:59+08:00", "slug": "a", "mode": "publish",
                 "verdict": "title_found"},
            ])
            done = pub.count_published_today(ledger, "2026-10-28")
            self.assertEqual(len(done), 1, "当天已发的必须被数到")
            self.assertEqual(done[0]["slug"], "a")

    def test_gate_ignores_preview_drafts_and_accidents(self) -> None:
        """dry-run 的草稿不消耗额度；误发也不占额度（ADR-0059 的两条例外）。"""
        pub = self._load_publish()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = root / ".newsagg" / "publish-ledger.jsonl"
            _write(ledger, [
                {"at": "2026-10-28T10:00:00+08:00", "slug": "draft", "mode": "dry-run",
                 "verdict": "draft_saved"},
                {"at": "2026-10-28T11:00:00+08:00", "slug": "acc", "mode": "publish",
                 "verdict": "title_found", "accidental": True},
                {"at": "2026-10-27T21:00:00+08:00", "slug": "yesterday", "mode": "publish",
                 "verdict": "title_found"},
            ])
            self.assertEqual(pub.count_published_today(ledger, "2026-10-28"), [])
            self.assertEqual(len(pub.count_published_today(ledger, "2026-10-27")), 1)


if __name__ == "__main__":
    unittest.main()
