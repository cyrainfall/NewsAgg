#!/usr/bin/env python3
"""选题包生成：把分拣决策 + 观点卡 拼成 wewrite 可用的 brief（NewsAgg → wewrite 的接口）。

设计依据：
- Q46 / ADR-0004：边界取"只要成稿"，接口是「一个字符串 + 一篇卡」
- ADR-0026 循环 A：文章必须**引用或推进**卡中判断，不得凭空另起
- llm-write 的 brief 契约：选题 / 框架 / 真实素材锚点 / 人格 / 目标字数

本脚本只做拼接（确定性工作），写作与判断由 wewrite / 编排 agent 完成。
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


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm, key = {}, None
    for line in m.group(1).splitlines():
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
    return fm, m.group(2)


def load_card(path: Path) -> dict:
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "file": path.name,
        "issue": fm.get("issue") or path.stem,
        "covered_events": fm.get("covered_events") or [],
        "last_written": fm.get("last_written"),
        "body": body.strip(),
    }


def extract_section(body: str, name: str) -> str:
    """从卡片正文中抽取一节（## 名称 ... 到下一个 ## 或文末）。"""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def build_brief(decision: dict, card: dict | None, style: dict, target_words: str) -> str:
    """拼装 brief。素材锚点只放**输入里真实存在**的内容，不编造。"""
    ev = decision.get("event") or {}
    members = ev.get("members") or []
    evidence = ev.get("key_evidence") or []

    lines: list[str] = []
    lines.append("# 写作任务书（由 NewsAgg 自动生成）")
    lines.append("")
    lines.append(f"生成时间：{datetime.now(CST).isoformat()}")
    lines.append("")

    # ── 选题 ──
    lines.append("## 选题")
    if card:
        lines.append(f"**议题**：{card['issue']}")
        lines.append("")
        lines.append(f"**本次事件**：{ev.get('summary', '（未提供）')}")
        lines.append("")
        if decision.get("whats_new"):
            lines.append(f"**推进方向**：{decision['whats_new']}")
            lines.append("")
    else:
        lines.append(f"**议题**：{decision.get('new_issue', '（未提供）')}")
        lines.append("")
        lines.append(f"**本次事件**：{ev.get('summary', '（未提供）')}")
        lines.append("")

    # ── 已有认知（ADR-0026：必须引用或推进，不得另起）──
    if card:
        lines.append("## 已有认知（必须引用或推进，不得另起）")
        cur = extract_section(card["body"], "当前判断")
        if cur:
            lines.append("**当前判断**：")
            lines.append(cur)
            lines.append("")
        facts = extract_section(card["body"], "依据事实")
        if facts:
            lines.append("**已有依据**：")
            lines.append(facts)
            lines.append("")
        if card["covered_events"]:
            lines.append("**已覆盖事件**（不要重复叙述）：")
            for e in card["covered_events"]:
                lines.append(f"- {e}")
            lines.append("")
        boundary = extract_section(card["body"], "边界与反方")
        if boundary:
            lines.append("**边界与反方**（写作时须避免过度断言）：")
            lines.append(boundary)
            lines.append("")

    # ── 真实素材锚点（只放输入里真实存在的）──
    lines.append("## 真实素材锚点")
    lines.append("**只使用以下素材。不得补写未提供的数字、引述、案例或个人经历。**")
    lines.append("")
    if evidence:
        for e in evidence:
            lines.append(f"- {e}")
        lines.append("")
    if members:
        lines.append("**报道来源**：")
        for mm in members:
            lines.append(f"- {mm}")
        lines.append("")

    # ── 人格与字数 ──
    lines.append("## 写作要求")
    lines.append(f"- **账号**：{style.get('name', '')}（{style.get('industry', '')}）")
    lines.append(f"- **目标读者**：{style.get('target_audience', '')}")
    lines.append(f"- **语气**：{style.get('tone', '')}")
    lines.append(f"- **人称**：{style.get('voice', '')}")
    lines.append(f"- **人格**：{style.get('writing_persona', '')}")
    lines.append(f"- **目标字数**：{target_words}")
    lines.append("")
    black = style.get("blacklist") or {}
    if black.get("words"):
        lines.append(f"- **禁用词**：{'、'.join(black['words'])}")
    if black.get("topics"):
        lines.append(f"- **禁写话题**：{'、'.join(black['topics'])}")
    lines.append("")
    lines.append("## 内容要求")
    lines.append("1. 必须有一个明确的核心判断，并在结尾给出锐利但不极端的收束。")
    lines.append("2. 区分事实、推断与个人意见；无法证实的数字不得写入。")
    lines.append("3. 不得编造个人经历（未提供亲历材料时，'我'只能用于表达判断）。")
    lines.append("4. 标题需在 2~30 字内（头条硬限制），口语化、不含禁用词。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 wewrite brief（选题包）")
    ap.add_argument("--decision", required=True, help="分拣决策 JSON")
    ap.add_argument("--out", default=None, help="brief 输出路径")
    ap.add_argument("--words", default="1200-1800", help="目标字数")
    args = ap.parse_args()

    root = repo_root()
    raw = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    decision = raw.get("decision", raw)

    # 载入对应卡（continue/skip 时）
    card = None
    cf = decision.get("card_file")
    if cf:
        cpath = root / "knowledge" / "cards" / cf
        if cpath.exists():
            card = load_card(cpath)
        else:
            print(f"警告：决策引用的卡不存在：{cf}", file=sys.stderr)

    # 载入风格（yaml 极简解析，只取需要的标量）
    style: dict = {}
    spath = root / ".wewrite" / "style.yaml"
    if spath.exists():
        txt = spath.read_text(encoding="utf-8")
        for k in ("name", "industry", "target_audience", "tone", "voice", "writing_persona"):
            m = re.search(rf'^{k}:\s*"?([^"\n]+)"?', txt, re.M)
            if m:
                style[k] = m.group(1).strip()

        # 黑名单必须**限定在 blacklist 块内**解析。
        # 注意：顶层还有 content 方向的 `topics:`，若不加限定会误取成"禁写话题"
        # ——那会让写作模型避开我们最想写的方向。
        bl = re.search(r"^blacklist:\s*$((?:\n[ \t]+.*|\n)*)", txt, re.M)
        blk = bl.group(1) if bl else ""
        wm = re.search(r"^[ \t]+words:\s*\n((?:[ \t]+-[ \t]*.+\n)+)", blk, re.M)
        tm = re.search(r"^[ \t]+topics:\s*\n((?:[ \t]+-[ \t]*.+\n)+)", blk, re.M)
        style["blacklist"] = {
            "words": [l.strip()[2:].strip().strip('"') for l in wm.group(1).splitlines()] if wm else [],
            "topics": [l.strip()[2:].strip().strip('"') for l in tm.group(1).splitlines()] if tm else [],
        }

    brief = build_brief(decision, card, style, args.words)

    now = datetime.now(CST)
    out = Path(args.out) if args.out else root / ".newsagg" / "briefs" / f"{now:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(brief, encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out),
        "card": card["file"] if card else None,
        "issue": (card or {}).get("issue") or decision.get("new_issue"),
        "chars": len(brief),
        "evidence_count": len((decision.get("event") or {}).get("key_evidence") or []),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
