#!/usr/bin/env python3
"""简报层：把当日运行结果汇成五段式简报（ADR-0019）。

设计依据：
- ADR-0019 固定五段式：①今日结论 ②采集与分拣概览 ③本篇与发布结果
  ④资产变动 ⑤需你介入的事项
- ADR-0032 dry-run 有副作用，需在简报中提示清理草稿
- ADR-0006 简报必须**无条件每日存在**（它是心跳）：弃权日也要发

本脚本只做汇整（确定性工作）。它读各阶段的产物，不重新判断。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_ledger(path: Path, date_prefix: str) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("at", "")).startswith(date_prefix):
            out.append(rec)
    return out


def unwrap(raw):
    """决策文件形状是 {date, decision:{...}, ...}；也接受扁平对象。

    这个解包必须统一，否则会出现"某一段读得到、另一段读不到"的静默空白
    （本文件早期版本就在 ① 段落上犯过这个错）。
    """
    if not raw:
        return {}
    return raw.get("decision", raw) if isinstance(raw, dict) else {}


def section_conclusion(decision, publish_recs, date: str) -> list[str]:
    out = ["## ① 今日结论", ""]
    d = unwrap(decision)
    if not d:
        out += ["**无决策记录**（分拣层今日未运行或未产出）。", ""]
        return out

    # 熔断优先呈现
    tripped = [r for r in publish_recs if r.get("verdict") in
               ("login_lost", "rejected", "not_published_needs_review", "publish_failed")]
    if tripped:
        r = tripped[-1]
        out += [f"**熔断 / 需处理**。发布环节未完成：`{r.get('verdict')}`", ""]
        return out

    action = d.get("action")
    if action == "abstain":
        reason = d.get("abstain_reason") or "（未说明）"
        out += [f"**弃权**。原因：{reason}", ""]
        out += ["> 弃权是合法结果，不是故障。本行存在的意义是让看守能区分"
                "「今日无选题」与「系统没跑」。", ""]
    elif action in ("continue", "new"):
        published = [r for r in publish_recs if str(r.get("verdict", "")).startswith("title_found")
                     or r.get("verdict") == "draft_saved"]
        if published:
            r = published[-1]
            v = r.get("verdict")
            if v == "draft_saved":
                out += [f"**已存草稿（dry-run）**：《{r.get('title','')}》", ""]
                out += ["> dry-run 会在账号上留真实草稿，需人工到 "
                        "mp.toutiao.com 草稿箱清理（ADR-0032）。", ""]
            else:
                state = v.split(":")[-1] if ":" in v else v
                out += [f"**已发布**：《{r.get('title','')}》（状态：{state}）", ""]
        else:
            summary = (d.get("event") or {}).get("summary", "")[:60]
            out += [f"**已成稿，尚未发布**。选题：{summary}", ""]
    elif action == "skip":
        out += [f"**跳过**。卡 {d.get('card_file')} 已含该事件且无新进展。", ""]
    else:
        out += [f"**未识别的决策**：`{action}`", ""]
    return out


def section_overview(candidates, decision) -> list[str]:
    out = ["## ② 采集与分拣概览", ""]
    if candidates:
        c = candidates.get("counts", {})
        out += [f"- 采集 {c.get('fetched',0)} 条 → 赛道预筛后 {c.get('kept',0)} 条"
                f"（丢弃 {c.get('dropped',0)}）"]
        out += [f"- 源：{c.get('sources_ok',0)} 成功 / {c.get('sources_failed',0)} 失败"]
        stale = candidates.get("stale_sources") or []
        if stale:
            out += [f"- **陈旧源**（updateTime 超过阈值）："
                    + "、".join(f"{s['source']}({s['age_minutes']}分)" for s in stale)]
        failed = candidates.get("failed_sources") or []
        if failed:
            out += [f"- **失效源**：{', '.join(map(str, failed))}"]
    else:
        out += ["- （无采集记录）"]

    if decision:
        low = decision.get("low_confidence_skipped") or []
        if low:
            out += ["", "**低置信度（已跳过）**："]
            for it in low:
                out += [f"- {it.get('event','')[:60]}（{it.get('confidence')}）"]
                if it.get("why"):
                    out += [f"  - {it['why'][:160]}"]
        nca = decision.get("no_card_available") or []
        if nca:
            out += ["", "**值得关注但无卡可接**（供你人工指定）："]
            for it in nca:
                out += [f"- {it.get('event','')[:60]}"]
                if it.get("why"):
                    out += [f"  - {it['why'][:160]}"]
        note = (decision.get("decision") or decision).get("ranking_note")
        if note:
            out += ["", f"**排序说明**：{note[:300]}"]
    out += [""]
    return out


def section_article(decision, publish_recs, body_dir: Path) -> list[str]:
    out = ["## ③ 本篇文章与发布结果", ""]
    d = unwrap(decision)
    if not d or d.get("action") == "abstain":
        out += ["本日无文章。", ""]
        return out

    ev = d.get("event") or {}
    if ev:
        out += [f"- 事件：{ev.get('summary','')[:120]}"]
    if d.get("card_file"):
        out += [f"- 接卡：{d['card_file']}（匹配度 {d.get('confidence')}）"]
    if d.get("whats_new"):
        out += [f"- 新在哪：{d['whats_new']}"]

    pending = [r for r in publish_recs if r.get("verdict") in
               ("not_published_needs_review", "publish_failed", "login_lost")]
    if pending:
        out += ["", "**待处理成稿**（不重发，等人核实，ADR-0011）："]
        for r in pending:
            out += [f"- {r.get('title','')}  ← `{r.get('verdict')}`"]

    drafts = [r for r in publish_recs if r.get("verdict") == "draft_saved"]
    if drafts:
        out += ["", "**本次为 dry-run**：已创建草稿，需人工清理。"]
    out += [""]
    return out


def section_assets(decision, cards_dir: Path) -> list[str]:
    out = ["## ④ 资产变动", ""]
    before = len(list(cards_dir.glob("*.md"))) if cards_dir.exists() else 0
    out += [f"- 观点卡总数：{before}"]
    d = unwrap(decision)
    if d.get("action") == "new":
        out += [f"- **新建卡**：{d.get('new_issue')}（可复用性：{d.get('reusable_because','')[:80]}）"]
    elif d.get("action") == "continue":
        out += [f"- **修订**：{d.get('card_file')}（待发布成功后写回，带未复核标记）"]
    # 治理建议（退役/合并）由分拣层在需要时提供
    for key, label in (("retire_suggestions", "退役建议"), ("merge_suggestions", "合并建议")):
        items = (decision or {}).get(key) or []
        if items:
            out += ["", f"**{label}**（等你否决）："]
            for it in items:
                out += [f"- {it}"]
    out += [""]
    return out


def section_actions(publish_recs, candidates, decision, cover_path: Path | None, article_slug: str | None) -> list[str]:
    out = ["## ⑤ 需你介入的事项", ""]
    any_item = False

    tripped = [r for r in publish_recs if r.get("verdict") in
               ("login_lost", "rejected", "publish_failed", "not_published_needs_review")]
    if tripped:
        any_item = True
        r = tripped[-1]
        out += [f"**熔断待处理**：`{r.get('verdict')}`"]
        out += [""]
        if r.get("verdict") == "login_lost":
            out += ["重新扫码：`node index.js auth login`（HOME=<repo>/.home 指向仓内）"]
        if r.get("verdict") == "rejected":
            out += ["内容被平台拒审——检查是否触发合规红线（直接关系信用分，ADR-0013）"]
        if r.get("verdict") == "not_published_needs_review":
            out += ["发布结果未知，请到 mp.toutiao.com 作品列表核实是否已发出（**不要重发**）"]
        out += ["", "解除熔断：修复后删除 `.newsagg/breaker.json`（ADR-0011）", ""]

    drafts = [r for r in publish_recs if r.get("verdict") == "draft_saved"]
    if drafts:
        any_item = True
        out += ["**清理 dry-run 草稿**：mp.toutiao.com → 草稿箱（ADR-0032）", ""]

    if article_slug and cover_path and cover_path.exists():
        any_item = True
        out += [f"**教学副本已就绪**：`outputs/{article_slug}.md`",
                "  你若修改并 push，系统下次运行会 diff 学习（ADR-0026 循环 B）。", ""]

    failed = (candidates or {}).get("failed_sources") or []
    if failed:
        any_item = True
        out += [f"**信源失效**：{', '.join(map(str, failed))}", ""]

    if not any_item:
        out += ["无。", ""]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="生成五段式简报（ADR-0019）")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None)
    ap.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    root = repo_root()
    now = datetime.now(CST)
    date = args.date or f"{now:%Y-%m-%d}"

    cand = read_json(root / ".newsagg" / "candidates" / f"{date}.json")
    dec_path = root / ".newsagg" / "triage" / f"{date}-decision.json"
    decision = read_json(dec_path)
    publish_recs = read_ledger(root / ".newsagg" / "publish-ledger.jsonl", date)

    # 找当日成稿与封面
    slug = None
    cover = None
    if decision:
        d = decision.get("decision", decision)
        ev = (d.get("event") or {}).get("summary", "")
        # 成稿 slug 约定：<date>-<theme>；从 outputs 里按日期前缀找
        outs = sorted((root / "outputs").glob(f"{date}-*.md"))
        if outs:
            slug = outs[-1].stem
            c = root / "outputs" / "covers" / f"{slug}.png"
            cover = c if c.exists() else None

    lines = [f"# NewsAgg 简报 {date}", ""]
    lines += section_conclusion(decision, publish_recs, date)
    lines += section_overview(cand, decision)
    lines += section_article(decision, publish_recs, root / ".newsagg" / "bodies")
    lines += section_assets(decision, root / "knowledge" / "cards")
    lines += section_actions(publish_recs, cand, decision, cover, slug)

    lines += ["---", f"*生成于 {now.isoformat()} ｜ 时区 Asia/Shanghai*"]
    md = "\n".join(lines)

    if args.stdout:
        print(md)
        return 0

    out = Path(args.out) if args.out else root / "reports" / f"{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out), "date": date,
        "has_decision": decision is not None,
        "has_candidates": cand is not None,
        "publish_records": len(publish_recs),
        "sections": 5,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
