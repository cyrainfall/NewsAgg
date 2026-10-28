"""台账必须真的从「跑一遍命令」这个层面接上（ADR-0083）。

前面两个测试文件测的是函数：`card_backlog.observe` 数得对、`validate_decision`
拦得住。但 2026-10-28 自测时发现的两个缺陷都在 `triage.py::main()` 里，
函数级测试**一个都抓不到**：

1. **顺序错了一轮**。原先的写法是「先校验、后记账」，于是「同一议题第 2 次出现」
   这一天，校验看到的次数仍是 1 → 闸门不响，真正的拦截要等到**第三次**。
   门槛整体晚了一轮，而且偏差方向是「更宽松」——不会有人抱怨，只会又跑一年 0 张卡。
2. **形状不同就静默失效**。`no_card_available` 写在 `decision` 里（而不是顶层）时，
   `observe` 读不到 → 不计数；闸门 `due` 恒为空 → 不拦；附加检查也只报「缺字段」
   而不报「位置不对」。退出码 0，一切看起来都正常。

这两个都属于本仓的重复失败形态（ADR-0042 三份拷贝漂移、ADR-0081 闸门空转、
ADR-0055 信号无消费者）：**错的地方不报错**。所以这里的测试必须经过
`main()` 走一遍真实的 `--validate` 调用，用退出码与 stdout 判断。

跑法：`python3 -m unittest discover -s tests`
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIAGE = ROOT / ".agents" / "skills" / "newsagg-triage" / "scripts" / "triage.py"
CARDS = ROOT / "knowledge" / "cards"

ISSUE = "AI 顶尖人才的定价"
EXISTING_CARD = "ai-tool-benefits-overestimated-by-demo-effect.md"
MONEY = "① 做 AI 岗的——薪酬带宽被重新定价；② 在招人的团队——同预算下的目标院校变了"


def _decision(day: str, event: str, *, nca_inside: bool = False,
              reason=None, where: str = "decision") -> dict:
    """造一个决策文件。

    `nca_inside` 复现那次的笔误：把 `no_card_available` 写进 `decision` 里。
    `where` 控制 `no_card_reason` 放哪一层（`decision` 或顶层）。
    """
    nca = [{"issue": ISSUE, "event": event, "why": "单一极端个案，等第二个数据点"}]
    dec = {
        "action": "continue",
        "card_file": EXISTING_CARD,
        "whats_new": "同议题再现",
        "confidence": 0.8,
        "money_point": MONEY,
        "event": {"summary": event},
    }
    if reason is not None:
        dec["no_card_reason"] = reason if where == "decision" else None
    raw = {"date": day, "decision": dec, "low_confidence_skipped": [], "abstain_reason": None}
    if nca_inside:
        dec["no_card_available"] = nca
    else:
        raw["no_card_available"] = nca
    if reason is not None and where == "top":
        raw["no_card_reason"] = reason
        dec.pop("no_card_reason", None)
    return raw


class TriageCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.backlog = self.dir / "card-backlog.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_validate(self, raw: dict) -> tuple[int, dict, str]:
        p = self.dir / "decision.json"
        p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(TRIAGE), "--validate", str(p),
             "--backlog", str(self.backlog)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            self.fail(f"stdout 不是 JSON：{r.stdout[:400]}\nstderr：{r.stderr[:400]}")
        return r.returncode, out, r.stderr

    def ledger(self) -> dict:
        if not self.backlog.exists():
            return {}
        return json.loads(self.backlog.read_text(encoding="utf-8"))

    def times_seen(self) -> int:
        for e in (self.ledger().get("issues") or {}).values():
            return e["times_seen"]
        return 0

    # ── 缺陷 1：顺序 ──────────────────────────────────────────────
    def test_gate_fires_on_the_second_day_not_the_third(self):
        """核心回归：第 1 天放行、第 2 天必须拦。

        此前「先校验后记账」让第 2 天放行、第 3 天才拦——门槛晚一轮。
        """
        code1, out1, _ = self.run_validate(_decision("2026-10-29", "17 岁 AI 研究员获 700 万年薪"))
        self.assertEqual(code1, 0, f"第 1 次出现不该拦：{out1.get('problems')}")

        code2, out2, _ = self.run_validate(_decision("2026-10-30", "应届 AI 岗起薪 60 万"))
        self.assertEqual(code2, 1, "第 2 次出现必须拦下——否则门槛又晚了一轮")
        self.assertTrue(any("已达新建门槛" in p for p in out2["problems"]), out2["problems"])

    def test_counting_happens_before_the_verdict(self):
        """被拦下的那一轮也必须计入。理由：这一天确实又出现了一次。"""
        self.run_validate(_decision("2026-10-29", "第一次"))
        self.run_validate(_decision("2026-10-30", "第二次（会被拦）"))
        self.assertEqual(self.times_seen(), 2,
                         "被拒的那一轮若不记账，「出现次数」就永远少一天")

    def test_same_day_reruns_do_not_inflate_the_count(self):
        """一天跑多轮是常态（主流程可能重跑）。按天去重，不是按轮。"""
        for _ in range(3):
            self.run_validate(_decision("2026-10-29", "同一天跑了三轮"))
        self.assertEqual(self.times_seen(), 1)
        code, out, _ = self.run_validate(_decision("2026-10-29", "同一天第四轮"))
        self.assertEqual(code, 0, "同一天不该被拦——它只是一次出现")

    # ── 缺陷 2：形状 ──────────────────────────────────────────────
    def test_nca_inside_decision_is_counted_not_silently_dropped(self):
        """`no_card_available` 写在 `decision` 里时，必须照样计数。

        这是自测时真实踩到的：不计数 → due 恒空 → 闸门永不响，退出码 0。
        """
        self.run_validate(_decision("2026-10-29", "第一次", nca_inside=True))
        self.assertEqual(self.times_seen(), 1,
                         "形状不同不能等于「没有这个事实」")

        code, out, _ = self.run_validate(_decision("2026-10-30", "第二次", nca_inside=True))
        self.assertEqual(code, 1, "写错层级不该让闸门顺带失效")

    def test_reason_inside_decision_passes(self):
        code, out, _ = self._two_days_ending_with(
            _decision("2026-10-30", "第二次", reason="只有一次独立报道", where="decision"))
        self.assertEqual(code, 0, f"{out.get('problems')}")
        self.assertEqual(out["backlog"]["reasons"], [ISSUE])

    def test_reason_at_top_level_also_passes(self):
        """SKILL.md 示例里理由与 `no_card_available` 相邻，极易被写到顶层。

        两处都接受，否则会出现「写了理由却被判缺理由」——那时人会去补一个
        已经写好的字段，越补越乱。
        """
        code, out, _ = self._two_days_ending_with(
            _decision("2026-10-30", "第二次", reason="只有一次独立报道", where="top"))
        self.assertEqual(code, 0, f"{out.get('problems')}")
        self.assertEqual(out["backlog"]["reasons"], [ISSUE])

    def _two_days_ending_with(self, second: dict):
        self.run_validate(_decision("2026-10-29", "第一次"))
        return self.run_validate(second)

    # ── 记账的其它可观察后果 ──────────────────────────────────────
    def test_blank_reason_is_rejected(self):
        """空白理由不算理由。否则「什么都不写」和「写了空格」等价。"""
        code, out, _ = self._two_days_ending_with(
            _decision("2026-10-30", "第二次", reason="   "))
        self.assertEqual(code, 1)

    def test_reason_does_not_clear_the_debt(self):
        """写了理由不等于销账：议题仍然欠着，下一轮还要面对。

        这是刻意的——理由只买一天。若写一次理由就永久销账，
        「不建」会比「建」更划算，整个门槛就退化成一个复选框。
        """
        self.run_validate(_decision("2026-10-29", "第一次"))
        code, out, _ = self._two_days_ending_with(
            _decision("2026-10-30", "第二次", reason="等第二个数据点"))
        self.assertEqual(code, 0, f"{out.get('problems')}")
        self.assertEqual(out["backlog"]["due_after"], [ISSUE],
                         "理由不是销账凭证；`due_after` 必须仍然列出这个议题")

    def test_ledger_written_even_when_validation_fails(self):
        """校验没过也落盘。`no_card_reason` 常常正是因为校验没过才被追问的。"""
        self.run_validate(_decision("2026-10-29", "第一次"))
        code, _, _ = self.run_validate(_decision("2026-10-30", "第二次（无理由被拒）"))
        self.assertEqual(code, 1)
        self.assertTrue(self.backlog.exists(), "被拒的那一轮也必须留下台账")
        self.assertEqual(self.times_seen(), 2)

    def test_no_issue_field_is_reported(self):
        """`issue` 是计数键。缺了它这条「无卡可接」进不了台账——必须报错。"""
        raw = _decision("2026-10-29", "第一次")
        raw["no_card_available"] = [{"event": "某事件", "why": "某理由"}]
        code, out, _ = self.run_validate(raw)
        self.assertEqual(code, 1)
        self.assertTrue(any("缺 issue" in p for p in out["problems"]), out["problems"])


class ThresholdIsOneNumberTest(unittest.TestCase):
    """判据用的阈值和报错文案里的阈值必须是同一个数字（ADR-0083 未做项一）。

    `TIMES_SEEN_THRESHOLD` 是 ADR-0083 明确要按实测分布调整的常量。若文案写死
    2，改阈值那天就会出现「实际按 3 判、提示还写 2」——判据变了而解释没变，
    比没有解释更坏，因为人会按错误的解释去补数据。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.backlog = self.dir / "card-backlog.json"

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self, day: str):
        p = self.dir / "decision.json"
        p.write_text(json.dumps(_decision(day, f"{day} 的事件"), ensure_ascii=False),
                     encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(TRIAGE), "--validate", str(p),
             "--backlog", str(self.backlog)],
            capture_output=True, text=True, cwd=str(ROOT))
        return r.returncode, json.loads(r.stdout)

    def test_ledger_stores_threshold(self):
        """台账必须自带阈值，否则读台账的人无从知道按几次判。"""
        self.validate("2026-10-29")
        data = json.loads(self.backlog.read_text(encoding="utf-8"))
        self.assertEqual(data["threshold"], 2)

    def test_raising_threshold_in_ledger_delays_the_gate(self):
        """把台账阈值改成 3 后，第 2 次出现不该再被拦——判据真的读台账。"""
        self.validate("2026-10-29")
        data = json.loads(self.backlog.read_text(encoding="utf-8"))
        data["threshold"] = 3
        self.backlog.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        code, out = self.validate("2026-10-30")
        self.assertEqual(code, 0, f"阈值 3 时第 2 次不该拦：{out.get('problems')}")

        code2, out2 = self.validate("2026-10-31")
        self.assertEqual(code2, 1, "阈值 3 时第 3 次必须拦")
        self.assertTrue(any("第 3 次出现" in p for p in out2["problems"]),
                        f"文案必须说 3 次，而不是写死的 2 次：{out2['problems']}")

    def test_old_ledger_without_threshold_still_works(self):
        """ADR-0083 之前手写的台账没有 `threshold` 字段，读入时要补默认值。"""
        self.backlog.write_text(json.dumps({"issues": {}}, ensure_ascii=False),
                                encoding="utf-8")
        code, _ = self.validate("2026-10-29")
        self.assertEqual(code, 0)
        data = json.loads(self.backlog.read_text(encoding="utf-8"))
        self.assertEqual(data["threshold"], 2, "缺字段时要补上默认值，而不是崩或算作 0")


class InputPackageTest(unittest.TestCase):
    """输入包里的 `backlog` 是分拣唯一能看见台账的入口。"""

    def test_package_carries_due_after_two_days(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            backlog, bb = d / "bl.json", d / "input.json"
            cand = d / "cand.json"
            cand.write_text(json.dumps({"date": "2026-10-29", "candidates": [
                {"title": "17 岁 AI 研究员获 700 万年薪", "source": "toutiao",
                 "hot": 111410879, "rank": 2, "rank_total": 50}]},
                ensure_ascii=False), encoding="utf-8")

            def run(day, event):
                p = d / "dec.json"
                p.write_text(json.dumps(_decision(day, event), ensure_ascii=False),
                             encoding="utf-8")
                subprocess.run([sys.executable, str(TRIAGE), "--validate", str(p),
                                "--backlog", str(backlog)],
                               capture_output=True, text=True, cwd=str(ROOT))

            run("2026-10-29", "第一次")
            run("2026-10-30", "第二次")
            subprocess.run([sys.executable, str(TRIAGE), "--candidates", str(cand),
                            "--out", str(bb), "--backlog", str(backlog)],
                           capture_output=True, text=True, cwd=str(ROOT))
            pkg = json.loads(bb.read_text(encoding="utf-8"))
        self.assertIn("backlog", pkg)
        self.assertEqual(pkg["backlog"]["threshold"], 2)
        self.assertEqual(pkg["backlog"]["due"], [ISSUE],
                         "到期议题必须出现在输入包里，否则分拣无从知道要建卡")
        self.assertTrue(pkg["backlog"]["open_issues"])


class ExistingCardsStillLoadTest(unittest.TestCase):
    """真实卡库必须仍能被读进来——闸门挂上去不能顺手弄坏既有 8 张卡。"""

    def test_all_cards_load_with_created(self):
        spec = importlib.util.spec_from_file_location("triage_real", TRIAGE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cards = mod.load_cards(CARDS)
        self.assertEqual(len(cards), 8, "卡库规模变了就要重看 ADR-0083 的假设")
        for c in cards:
            self.assertIn("created", c, "`created` 必须被读进来：退役回检靠它")
        self.assertEqual(sum(1 for c in cards if c["created"]), 8)


if __name__ == "__main__":
    unittest.main()
