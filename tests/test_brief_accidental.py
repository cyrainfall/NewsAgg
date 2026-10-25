"""误发的文章必须出现在简报第⑤段（ADR-0059 + ADR-0062）。

为什么值得一个测试：ADR-0059 决定「误发不占额度，但必须留在账本里」。账本留了
字段，简报却没读——系统自己知道自己误发了，却从不告诉唯一能处理这件事的人。
这是同一族缺陷（「记录了但没有实现」）的第七次，而且是最短命的一次：ADR-0059
写完的第二天就复现了。所以这里钉住的是**渲染**，不是账本写入。

另外钉住两条容易写错的边界：
- 终态是 `deleted_at`，不是「把 accidental 改成 false」——改前者是记录事实，
  改后者是让账本说谎（ADR-0055 在微头条那条上犯过同一个错）。
- 提醒只在未删除时出现，删完就停——否则「保留」会变成换一种措辞的唠叨。

跑法：`python3 -m unittest discover`（仓库根）或 `python3 -m unittest discover -s tests -v`
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / ".agents" / "skills" / "newsagg-brief" / "scripts" / "brief.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief = _load("newsagg_brief_accidental", BRIEF)

CANDIDATES = {"counts": {"fetched": 241, "kept": 117, "dropped": 192,
                         "sources_ok": 7, "sources_failed": 0},
              "collection_runs": [{"at": "2026-10-25T18:00:00+08:00", "kept_new": 10}],
              "failures": []}

ACCIDENT = {
    "at": "2026-10-25T15:07:54+08:00",
    "slug": "2026-10-24-ai-efficiency-metrics-become-performance-theater",
    "title": "AI 提效写进绩效，先涨的是你的返工率",
    "mode": "publish",
    "verdict": "title_found",
    "gid": "7700497729948041762",
    "accidental": True,
    "needs_human_delete": True,
}


def render(records) -> str:
    """把账本写成临时文件后渲染第⑤段。"""
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "publish-ledger.jsonl"
        ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                          encoding="utf-8")
        return "\n".join(brief.section_actions(
            publish_recs=[],
            candidates=CANDIDATES,
            decision=None,
            cover_path=None,
            article_slug=None,
            ledger_path=ledger,
            cover_report=None,
            run_status=None,
            date="2026-10-25",
            root=ROOT,
        ))


class AccidentalPublishInBriefTest(unittest.TestCase):
    def test_unresolved_accident_is_listed(self) -> None:
        text = render([ACCIDENT])
        self.assertIn("误发了一篇", text)
        self.assertIn("7700497729948041762", text)
        self.assertIn("AI 提效写进绩效", text)

    def test_resolution_hint_names_the_right_field(self) -> None:
        """终态字段必须是 deleted_at，否则提醒永远停不下来。"""
        text = render([ACCIDENT])
        self.assertIn("deleted_at", text)
        # 明确禁止用「改 accidental」冒充已解决
        self.assertIn("不要", text)

    def test_deleted_accident_stops_nagging(self) -> None:
        resolved = dict(ACCIDENT, deleted_at="2026-10-26")
        self.assertNotIn("误发了一篇", render([resolved]))

    def test_ordinary_record_is_not_reported(self) -> None:
        """正常发布不能被误报成误发——否则这一整段会被无视。"""
        normal = {"at": "2026-10-25T21:03:59+08:00", "slug": "2026-10-25-x",
                  "title": "正常的一篇", "mode": "publish", "verdict": "title_found",
                  "gid": "7700590575333949967"}
        self.assertNotIn("误发了一篇", render([normal]))


if __name__ == "__main__":
    unittest.main()
