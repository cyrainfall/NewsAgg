"""封面样式目录与每日轮换（ADR-0067）。

为什么值得一个测试：样式选择是无人值守路径上每天都要走一次的决定。
它的失败模式全是静默的——random 不可复现、未知名字静默回退、浅色纸底
让像素自检把整幅图误判成墨。这里钉住的是**决定本身**，不是绘制
（绘制由自检在生成时把关，跑一遍冒烟即可）。

钉住的边界：
- 同一天重跑必同一种样式（幂等，无状态文件）；
- 相邻两天必不同种（「每天换一种」的直觉）；
- 未知样式名必须抛错（显式指定错了还静默回退 = 假设无人验证）；
- 样式必须声明 bg，且墨色与底色可分（像素自检的「是墨不是底」判据
  依赖这一点，浅色纸样式不声明就全体误报）。

跑法：`python3 -m unittest discover`（仓库根）
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MC = ROOT / ".agents" / "skills" / "newsagg-publish" / "scripts" / "make_cover.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# make_cover 只在 wewrite 的 python（带 Pillow）下能 import——它对
# ImportError 的处理是打印提示后 sys.exit(2)。系统 python3 跑全量测试时
# 没有 Pillow，这里必须跳过而不是让整个测试进程崩掉：样式选择逻辑本身
# 与 Pillow 无关，但被测模块的 import 是硬依赖。
try:
    mc = _load("newsagg_make_cover_style", MC)
    _PIL_OK = True
except (ImportError, SystemExit):
    mc = None
    _PIL_OK = False


class TestStyleCatalog(unittest.TestCase):
    def setUp(self):
        if not _PIL_OK:
            self.skipTest("无 Pillow（系统 python3）；用 wewrite 的 python 跑")

    def test_catalog_is_finite_and_nonempty(self):
        self.assertGreaterEqual(len(mc.STYLES), 4)
        self.assertEqual(mc.STYLE_ORDER, list(mc.STYLES.keys()))

    def test_every_style_declares_material(self):
        """样式是材质：bg/ink/accent 必须声明，否则自检没有「底色」参照。"""
        for name, st in mc.STYLES.items():
            for key in ("bg", "ink", "muted", "accent", "alt"):
                self.assertIn(key, st, f"样式 {name} 缺 {key}")
                self.assertEqual(len(st[key]), 3, f"样式 {name} 的 {key} 不是 RGB")
                self.assertTrue(all(isinstance(c, int) for c in st[key]))

    def test_ink_distinguishable_from_bg(self):
        """像素自检判「是墨」用的阈值是 sum-abs > 60。每种样式的墨与底
        都必须真的分得开——否则检查从把关变成全体误报或全体通过。"""
        for name, st in mc.STYLES.items():
            dist = sum(abs(a - b) for a, b in zip(st["ink"], st["bg"]))
            self.assertGreater(dist, 60, f"样式 {name} 的墨色与底色 sum-abs={dist} ≤ 60")
            dist_a = sum(abs(a - b) for a, b in zip(st["accent"], st["bg"]))
            self.assertGreater(dist_a, 60, f"样式 {name} 的强调色与底色分不开")


class TestDailySelection(unittest.TestCase):
    def setUp(self):
        if not _PIL_OK:
            self.skipTest("无 Pillow（系统 python3）；用 wewrite 的 python 跑")

    def test_same_day_is_deterministic(self):
        a = mc.style_for_date("2026-10-26")
        b = mc.style_for_date("2026-10-26")
        self.assertEqual(a, b)

    def test_adjacent_days_differ(self):
        """相邻两天撞上同一种时 +1 错开——「每天换一种」必须成立。

        ⚠️ 日期必须用 timedelta 连续生成：手拼 `{m}-{d}` 再只到 28 号，
        会让 01-28 和 02-01 在列表里「相邻」——但它们中间隔着三天，
        那不是要钉住的约束。
        """
        import datetime
        d0 = datetime.date(2027, 1, 1)
        days = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(365)]
        for prev, cur in zip(days, days[1:]):
            self.assertNotEqual(
                mc.style_for_date(prev), mc.style_for_date(cur),
                f"{prev} 与 {cur} 撞了同一种样式",
            )

    def test_distribution_covers_catalog(self):
        seen = {mc.style_for_date(f"2027-{m:02d}-{d:02d}")
                for m in range(1, 13) for d in range(1, 29)}
        self.assertEqual(seen, set(mc.STYLE_ORDER), "一年内每种样式都该轮到")


class TestStylePinning(unittest.TestCase):
    TITLE = "AI 裁员两年：4 条原因，一条都不是 AI"

    def setUp(self):
        if not _PIL_OK:
            self.skipTest("无 Pillow（系统 python3）；用 wewrite 的 python 跑")

    def test_unknown_style_raises(self):
        with self.assertRaises(ValueError):
            mc.make_cover(self.TITLE, Path("/tmp/_never.png"), style="nope")

    def test_pinned_style_used(self):
        info = mc.make_cover(
            self.TITLE, Path("/tmp/_never1.png"),
            layout="statement", statement="标准变了", style="paper",
        )
        self.assertEqual(info["style"], "paper")
        self.assertTrue(info["style_pinned"])

    def test_day_based_style_used(self):
        info = mc.make_cover(
            self.TITLE, Path("/tmp/_never2.png"),
            layout="statement", statement="标准变了", day="2026-10-26",
        )
        self.assertEqual(info["style"], mc.style_for_date("2026-10-26"))
        self.assertFalse(info["style_pinned"])


class TestContrast(unittest.TestCase):
    """文字与底色的对比度必须达标（ADR-0067 补）。

    为什么值得钉：这套自检是在换材质**之后**才发现缺的——`edge_check`
    判「内容画在哪」，`safe_zone_check` 判「有没有落进裁切区」，都不判
    「看不看得见」。而 accent 既是装饰条的颜色、又是 kicker 小字的颜色，
    一个颜色承担两种角色，对比度要求差一个数量级。
    """

    def setUp(self):
        if not _PIL_OK:
            self.skipTest("无 Pillow（系统 python3）；用 wewrite 的 python 跑")

    def test_accent_and_muted_reach_450_against_bg(self):
        """accent 与 muted 都要能当小字读（kicker、hero 单位、脚注都靠它们）。

        ⚠️ 阈值取 4.5:1（WCAG 正文级）而不是 3:1（大字级）：封面在
        信息流里只有 156px 宽（ADR-0049），画布上 42px 的 kicker 到那里
        只有 5.5px——按读者看到的尺寸，它是正文不是大字。
        """
        for name, st in mc.STYLES.items():
            for key in ("accent", "muted"):
                ratio = mc.contrast_ratio(st[key], st["bg"])
                self.assertGreaterEqual(
                    ratio, mc.CONTRAST_MIN_TEXT,
                    f"样式 {name} 的 {key} {tuple(st[key])} 与底色 "
                    f"{tuple(st['bg'])} 对比度仅 {ratio:.2f} < {mc.CONTRAST_MIN_TEXT}",
                )

    def test_ink_and_alt_reach_450_against_bg(self):
        """正文主色（ink/alt）承载的是主元素，阈值同样按正文级。"""
        for name, st in mc.STYLES.items():
            for key in ("ink", "alt"):
                ratio = mc.contrast_ratio(st[key], st["bg"])
                self.assertGreaterEqual(
                    ratio, mc.CONTRAST_MIN_TEXT,
                    f"样式 {name} 的 {key} 对比度仅 {ratio:.2f}",
                )

    def test_check_catches_low_contrast_color(self):
        """把 blush 的 accent 换回修改前的珊瑚色，这条检查必须报错。

        回归值来自 2026-10-26 的实测：(214,78,96) on (240,232,228)
        = 3.39，不达标。若这条检查哪天变哑了，测试会先于线上发现。
        """
        st = dict(mc.STYLES["blush"])
        st["accent"] = (214, 78, 96)
        drawn = [{"element": "kicker", "text": "10150 个真实收款产品",
                  "size": 42, "color": st["accent"]}]
        problems = mc.contrast_check(drawn, st["bg"], scale=156 / 1200)
        self.assertTrue(problems, "低对比度的 accent 没被抓住")
        self.assertIn("3.39", problems[0])

    def test_check_is_scale_aware(self):
        """按画布尺寸判会放过小字，按 156px 卡片的实际尺寸判才会拦。

        同一个 42px 的 kicker：画布上算「大字」（3:1 放行），缩到
        156px 卡片上是 5.5px 属正文级（4.5:1）。这条测试钉住「按
        读者看到的尺寸判」这个决定。
        """
        st = dict(mc.STYLES["blush"])
        st["accent"] = (214, 78, 96)
        drawn = [{"element": "kicker", "text": "x", "size": 42,
                  "color": st["accent"]}]
        self.assertEqual(mc.contrast_check(drawn, st["bg"], scale=1.0), [])
        self.assertTrue(mc.contrast_check(drawn, st["bg"], scale=156 / 1200))

    def test_every_style_generates_without_contrast_problems(self):
        """8 种样式逐个生成，都不许有对比度问题。"""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        for name in mc.STYLE_ORDER:
            info = mc.make_cover(
                "想靠 AI 做副业的人：年入 10 万美元的只有 2.4%",
                tmp / f"{name}.png",
                layout="number", kicker="10150 个真实收款产品",
                hero="2.4", hero_unit="% 年入 10 万美元",
                payoff="AI 与非 AI，几乎没区别",
                style=name,
            )
            self.assertEqual(info["problems"], [], f"样式 {name} 有对比度问题")




class PayoffOrphanLineTest(unittest.TestCase):
    """payoff 折出孤字行时必须告警（2026-10-31 实测）。

    那次 payoff 是「免费的那批，先替你定了价」，`fit_block` 允许两行，于是排成
    「免费的那批，先替你定了」+「价」——`max_lines=2` 之内，既没截断也没告警，
    report 里 `problems: [] warnings: []` 全绿，图却明显是坏的。
    无人值守时没人看图，排版坏了只能自己说出来（同节其他自检的判据）。
    """

    def _render(self, payoff: str) -> dict:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            return mc.make_cover(
                "AI电影票房150万：你卖的AI内容，价已经被定死了",
                Path(td) / "c.png",
                layout="number",
                kicker="首部AI院线电影",
                hero="1",
                hero_unit="% 上座率",
                payoff=payoff,
                style="blush",
            )

    @unittest.skipIf(mc is None, "需要 Pillow（wewrite 的 python）")
    def test_orphan_last_line_warns(self):
        rep = self._render("免费的那批，先替你定了价")
        self.assertTrue(any("payoff" in w and "一个字" in w for w in rep["warnings"]),
                        msg=f"孤字行没有告警：{rep['warnings']}")

    @unittest.skipIf(mc is None, "需要 Pillow（wewrite 的 python）")
    def test_short_payoff_does_not_warn(self):
        rep = self._render("免费的，先替你定了价")
        self.assertFalse(any("一个字" in w for w in rep["warnings"]),
                         msg=f"短 payoff 不应告警：{rep['warnings']}")


if __name__ == "__main__":
    unittest.main()
