#!/usr/bin/env python3
"""认知写回：发布成功后向观点卡追加一条修订（ADR-0026 循环 A）。

设计依据：
- ADR-0007 卡片结构：当前判断 / 依据事实 / 修订史 / covered_events
- ADR-0026 循环 A：写回**不阻塞**，但带**未复核**标记与来源；本人可推翻
- ADR-0026：`covered_events` 与 `last_written` 是防重复发文的关键字段
  （前者让下次能判「跳过 vs 续写」，后者决定冷却期）
- ADR-0018：knowledge/cards/ 由系统写；但仅追加，不改写旧判断

本脚本只做追加（确定性工作）。不判断该不该写回——那是分拣决策的事。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """返回 (fm, body, 原始 frontmatter 文本)。"""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text, ""
    raw = m.group(1)
    fm: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*-\s+", line) and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(line.strip()[2:].strip().strip('"'))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key, v = k.strip(), v.strip()
            fm[key] = [] if v in ("", "[]") else (None if v.lower() in ("null", "~") else v.strip('"'))
    return fm, m.group(2), raw


def set_frontmatter_scalar(raw_fm: str, key: str, value: str) -> str:
    """替换或追加 frontmatter 中的标量字段。"""
    pat = re.compile(rf"^{re.escape(key)}:.*$", re.M)
    line = f"{key}: {value}"
    if pat.search(raw_fm):
        return pat.sub(line, raw_fm, count=1)
    return raw_fm.rstrip() + "\n" + line


def append_covered_event(raw_fm: str, event_desc: str, date: str) -> str:
    """向 covered_events 追加一条。空列表写成 [] 时需展开。"""
    entry = f"  - {date} {event_desc}"
    # 情形一：covered_events: []  → 展开成多行
    if re.search(r"^covered_events:\s*\[\]\s*$", raw_fm, re.M):
        return re.sub(r"^covered_events:\s*\[\]\s*$",
                      "covered_events:\n" + entry, raw_fm, count=1, flags=re.M)
    # 情形二：已有列表 → 在其末尾追加
    m = re.search(r"^(covered_events:\s*)$((?:\n\s+-\s+.*)*)", raw_fm, re.M)
    if m:
        insert_at = m.end(2)
        return raw_fm[:insert_at] + "\n" + entry + raw_fm[insert_at:]
    # 情形三：字段不存在 → 追加字段
    return raw_fm.rstrip() + "\ncovered_events:\n" + entry


def append_revision(body: str, revision: str) -> str:
    """把一条修订追加到「## 修订史」段末尾。段落缺失则创建。"""
    m = re.search(r"^##\s+修订史\s*$", body, re.M)
    if not m:
        return body.rstrip() + "\n\n## 修订史\n\n" + revision + "\n"
    rest = body[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    tail_start = m.end() + (nxt.start() if nxt else len(rest))
    head = body[:tail_start].rstrip()
    tail = body[tail_start:]
    return head + "\n" + revision + "\n" + tail


def main() -> int:
    ap = argparse.ArgumentParser(description="发布后写回观点卡（ADR-0026 循环 A）")
    ap.add_argument("--card", required=True, help="卡文件名，如 llm-price-war-...md")
    ap.add_argument("--event", required=True, help="本次事件的简短描述（写入 covered_events）")
    ap.add_argument("--judgment", default=None, help="本次新增/推进的判断（写入修订史）")
    ap.add_argument("--evidence", default=None, help="本次新增依据（逗号分隔）")
    ap.add_argument("--article", default=None, help="来源文章 slug（记录来源）")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = ap.parse_args()

    root = repo_root()
    path = root / "knowledge" / "cards" / args.card
    if not path.exists():
        print(json.dumps({"ok": False, "message": f"卡不存在：{path}"}, ensure_ascii=False))
        return 1

    now = datetime.now(CST)
    date = args.date or f"{now:%Y-%m-%d}"
    text = path.read_text(encoding="utf-8")
    fm, body, raw_fm = parse_frontmatter(text)

    # ── 构造修订记录 ──
    lines = [f"- {date} 修订（**未复核**，来源 "
             f"{args.article or '（未注明）'}）：{args.judgment or args.event}"]
    if args.evidence:
        for e in [x.strip() for x in args.evidence.split(",") if x.strip()]:
            lines.append(f"  - 新增依据：{e}")
    revision = "\n".join(lines)

    new_fm = set_frontmatter_scalar(raw_fm, "last_written", date)
    new_fm = append_covered_event(new_fm, args.event, date)
    new_body = append_revision(body, revision)
    new_text = f"---\n{new_fm}\n---\n{new_body.lstrip()}"

    result = {
        "ok": True,
        "card": args.card,
        "date": date,
        "covered_events_before": len(fm.get("covered_events") or []),
        "revision": revision,
        "unreviewed": True,
    }

    if args.dry_run:
        result["dry_run"] = True
        result["preview_head"] = new_text[:400]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    path.write_text(new_text, encoding="utf-8")

    # 读回校验（ybrain 的"写后读回"纪律）
    back = path.read_text(encoding="utf-8")
    result["verified"] = (date in back) and (args.event[:20] in back)
    result["bytes"] = len(back)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
