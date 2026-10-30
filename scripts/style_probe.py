#!/usr/bin/env python3
"""平台样本 vs 我们成稿：用同一把尺子量。

为什么需要它
------------
`platforms/title-rules.md` 与 `prose-rules.md` 里的每一条观察项都写着「待检验的假设」。
检验需要两样东西：**平台的样本**，和**我们自己的同口径数字**。此前两者都没有落成文件——
2026-10-28 那次复盘是人工读 13 篇、人工数出来的，下一次复盘又得从头来一遍。

这个脚本只做确定性的事：抓样本、算同一组指标、并排打印。判断（要不要改规则）不在这里。

用法
----
    # 量我们自己的成稿
    python3 scripts/style_probe.py --ours

    # 抓平台样本（头条文章 id，逗号分隔）并存档
    python3 scripts/style_probe.py --ids 7702342639261205021,7697408937204744758

    # 两边一起，出对照表
    python3 scripts/style_probe.py --ids <ids> --ours

样本怎么来
----------
头条文章正文只在移动页 `m.toutiao.com/i<id>/` 里是服务端渲染的（PC 页与 web_fetch 都拿不到，
返回的是 JS 壳）。**文章 id 需要另外找**（站内搜索或第三方转载页），这一步没有做成自动的：
头条的搜索/feed 接口要 `_signature`，伪造它属于绕过平台风控，本项目不做。所以 id 由人/agent
提供，脚本负责其余全部工作，并把「这次用了哪几个 id」写进样本文件，保证可复查。

输出
----
`.newsagg/platform-samples/<日期>.json`：样本原文指标 + 我们的指标 + 生成时间。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import statistics
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = ROOT / ".newsagg" / "platform-samples"
OUTPUTS = ROOT / "outputs"

HDR = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                   "Mobile/15E148 Safari/604.1"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://m.toutiao.com/",
}


def _clean(s: str) -> str:
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


CAPTION = re.compile(r"^(图片来源|图源|资料图|摄影|现场图|发布会现场|海报新闻|记者)\b|"
                     r"(图片来源|图源)[:：]")


def _strip_captions(paras: list[str]) -> list[str]:
    """去掉图片说明段。

    头条的正文里图片说明也是一个 `<p>`（「图片来源：unsplash」「发布会现场」），
    直接拿 paras[0] 当「首段」会量到说明文字——第一版就是这么错的：平台首段
    中位数算出 13 字，看着像「平台开头极短」，实际量的是图注。
    """
    return [p for p in paras if not (len(p) <= 16 and CAPTION.search(p))]


def _stats(title: str, paras: list[str], headings: list[str]) -> dict:
    """一篇稿子的可比较指标。标题单独量，因为它有自己的硬约束。"""
    body = "\n".join(paras)
    n = len(body)
    real = _strip_captions(paras)
    # 中文引号有两种：直角 「」（我们全篇用这个）与弯引号 “”。只数弯引号会把
    # 我们的稿子量成「从不引用原话」，那是尺子的错，不是稿子的错。
    quote_marks = body.count("“") + body.count("「")
    return {
        "title": title,
        "title_len": len(title),
        "chars": n,
        "paras": len(paras),
        "para_avg": round(statistics.mean([len(p) for p in paras]), 1) if paras else 0,
        "para_max": max((len(p) for p in paras), default=0),
        "lead_len": len(real[0]) if real else 0,
        "headings": len(headings),
        # 归一化到「每 100 字」/「每千字」，否则长短稿不可比
        "digits_per100": round(len(re.findall(r"[0-9]", body)) / n * 100, 1) if n else 0,
        "quotes_per1000": round(quote_marks / n * 1000, 1) if n else 0,
        "i_per1000": round(body.count("我") / n * 1000, 1) if n else 0,
    }


def measure_markdown(p: pathlib.Path) -> dict:
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = ""
    for ln in lines:
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    paras = [ln.strip() for ln in lines
             if ln.strip() and not ln.startswith("#") and not ln.startswith("!")]
    headings = [ln for ln in lines if ln.startswith("## ")]
    st = _stats(title, paras, headings)
    st["slug"] = p.stem
    st["images"] = len(re.findall(r"!\[", text))
    return st


def fetch_article(item_id: str) -> dict:
    url = f"https://m.toutiao.com/i{item_id}/"
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    m = re.search(r"(?s)<title>(.*?)</title>", raw)
    title = _clean(m.group(1)) if m else ""
    title = re.sub(r"\s*-\s*今日头条$", "", title)
    block_m = re.search(r"(?s)<article.*?</article>", raw)
    block = block_m.group(0) if block_m else ""
    paras = [x for x in (_clean(p) for p in re.findall(r"(?s)<p[^>]*>(.*?)</p>", block)) if x]
    headings = [_clean(h[1]) for h in
                re.findall(r"(?s)<h([123])[^>]*>(.*?)</h\1>", block)]
    imgs = len(re.findall(r"data-src=", block)) or len(re.findall(r"<img", block))
    st = _stats(title, paras, headings)
    st.update({"id": item_id, "url": url, "images": imgs})
    return st


def table(rows: list[dict], label: str) -> None:
    print(f"\n=== {label}（n={len(rows)}）===")
    if not rows:
        return
    keys = [("title_len", "标题字"), ("chars", "正文字"), ("paras", "段数"),
            ("para_avg", "段均"), ("para_max", "最长段"), ("lead_len", "首段"),
            ("headings", "小标题"), ("digits_per100", "数字/百字"),
            ("quotes_per1000", "引号/千字"), ("i_per1000", "我/千字"),
            ("images", "图")]
    head = "  ".join(f"{k:>9}" for _, k in keys)
    print(head)
    for r in rows:
        print("  ".join(f"{r.get(f, 0)!s:>9}" for f, _ in keys))
    print("-" * len(head))
    med = {f: round(statistics.median([r.get(f, 0) for r in rows]), 1) for f, _ in keys}
    print("  ".join(f"{med[f]!s:>9}" for f, _ in keys) + "   <- 中位数")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="平台样本 vs 成稿，同口径测量")
    ap.add_argument("--ids", default="", help="头条文章 id，逗号分隔")
    ap.add_argument("--ours", action="store_true", help="同时量 outputs/*.md")
    ap.add_argument("--limit", type=int, default=0, help="只量最近 N 篇成稿（0=全部）")
    args = ap.parse_args(argv)

    today = dt.date.today().isoformat()
    record: dict = {"date": today, "at": dt.datetime.now().astimezone().isoformat(),
                    "platform": [], "ours": [], "ids_used": []}

    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        record["ids_used"] = ids
        for i in ids:
            try:
                record["platform"].append(fetch_article(i))
            except Exception as e:  # 网络/结构变化都不该让整轮失败
                record["platform"].append({"id": i, "error": str(e)})
        record["platform"] = [r for r in record["platform"] if "error" not in r]
        table(record["platform"], "平台样本")

    if args.ours or not args.ids:
        files = sorted(OUTPUTS.glob("*.md"), key=lambda p: p.stat().st_mtime)
        if args.limit:
            files = files[-args.limit:]
        record["ours"] = [measure_markdown(p) for p in files]
        table(record["ours"], "我们的成稿")

    if record["platform"] and record["ours"]:
        p_med = {k: statistics.median([r[k] for r in record["platform"]])
                 for k in ("title_len", "chars", "para_avg", "para_max",
                           "lead_len", "headings", "digits_per100")}
        o_med = {k: statistics.median([r[k] for r in record["ours"]])
                 for k in p_med}
        print("\n=== 差距（我们 - 平台）===")
        for k, v in p_med.items():
            print(f"  {k:<14} 平台 {v:>8.1f}   我们 {o_med[k]:>8.1f}   "
                  f"差 {o_med[k] - v:+.1f}")
        record["gap"] = {k: round(o_med[k] - p_med[k], 1) for k in p_med}

    SAMPLES.mkdir(parents=True, exist_ok=True)
    out = SAMPLES / f"{today}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n存档：{out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
