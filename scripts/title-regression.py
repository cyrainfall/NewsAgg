"""标题回归：用已发文章的真实表现检验 `platforms/title-rules.md` 的观察项。

用法（仓库根目录）：
    python3 scripts/title-regression.py

纪律：
1. 特征**先按规则文件的定义**（生产标签器 `title_rules.tag_title`）标注，再看表现。
2. 只统计 showCount ≥ 100 的文章——低于它的比率没有统计意义（MIN_SHOW_FOR_RATE）。
3. 显著性用**条件二项检验**：给定总点击数，H0 是按展现量等比例分配。n 小，不下强结论。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "newsagg-publish" / "scripts"))
import title_rules  # noqa: E402

MIN_SHOW = 100


def load_articles() -> list[dict]:
    """每篇取最后一次观测；丢掉小样本。"""
    path = ROOT / "reports" / "performance.jsonl"
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    latest: dict[str, dict] = {}
    for r in recs:
        key = str(r.get("gid") or r.get("slug") or "")
        if not key:
            continue
        cur = (str(r.get("date") or ""), str(r.get("collected_at") or ""))
        old = latest.get(key)
        if old is None or cur > (str(old.get("date") or ""), str(old.get("collected_at") or "")):
            latest[key] = r

    out = []
    for r in latest.values():
        m = r.get("metrics") or {}
        show, read = m.get("showCount") or 0, m.get("readCount") or 0
        if show < MIN_SHOW:
            continue
        out.append({"title": str(r.get("title") or ""), "show": show, "read": read,
                    "slug": str(r.get("slug") or "?")})
    return out


def binom_two_sided(k: int, n: int, p: float) -> float:
    """精确二项双侧 p（n 很小，直接求和，不引第三方库）。"""
    if n == 0:
        return 1.0

    def pmf(i: int) -> float:
        return math.comb(n, i) * p ** i * (1 - p) ** (n - i)

    obs = pmf(k)
    return sum(pmf(i) for i in range(n + 1) if pmf(i) <= obs + 1e-15)


def main() -> int:
    rules = title_rules.load_rules(ROOT)
    rows = load_articles()
    if not rows:
        print("没有可用样本（showCount >= %d）" % MIN_SHOW)
        return 1

    for r in rows:
        t = title_rules.tag_title(r["title"], rules)
        r["flags"] = {
            "角色词（reader_side_landing.hit）": t["reader_side_landing"]["hit"],
            "抽象量（ends_on_abstract_metric）": t["ends_on_abstract_metric"]["hit"],
            "变化结构（contrast_shape）": t["contrast_shape"]["hit"],
            "有数字（concrete_number）": t["concrete_number"]["hit"],
        }

    total_show = sum(r["show"] for r in rows)
    total_read = sum(r["read"] for r in rows)
    print(f"样本 {len(rows)} 篇 | 展现 {total_show} | 阅读 {total_read} "
          f"| 整体 CTR {total_read / total_show:.4f}\n")

    print(f"{'CTR':>7} {'show':>5} {'read':>4}  标题")
    for r in sorted(rows, key=lambda x: -(x["read"] / x["show"])):
        print(f"{r['read'] / r['show']:7.4f} {r['show']:5d} {r['read']:4d}  {r['title'][:40]}")

    print("\n分组（条件二项检验，H0 = 按展现量等比例分配点击）：")
    for key in rows[0]["flags"]:
        a = [r for r in rows if r["flags"][key]]
        b = [r for r in rows if not r["flags"][key]]
        if not a or not b:
            print(f"  {key}：分组为空，跳过")
            continue
        ra, sa = sum(x["read"] for x in a), sum(x["show"] for x in a)
        rb, sb = sum(x["read"] for x in b), sum(x["show"] for x in b)
        ratio = (ra / sa) / (rb / sb) if rb and sb and ra else float("inf") if ra else 0.0
        p = binom_two_sided(ra, ra + rb, sa / (sa + sb)) if (ra + rb) else 1.0
        print(f"  {key}：命中 {len(a):2d} 篇 {ra:3d}/{sa:5d}={ra / sa:.4f}"
              f" | 未命中 {len(b):2d} 篇 {rb:3d}/{sb:5d}={rb / sb:.4f}"
              f" | {ratio:.1f}x | p={p:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
