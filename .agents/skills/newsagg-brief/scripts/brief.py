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


def read_performance(root: Path) -> dict:
    """读表现数据回路的状态（ADR-0010 循环 C）。

    它有两半：入库的历史（`reports/performance.jsonl`）与运行时的本次结果
    （`.newsagg/performance-status.json`）。后者决定"读到了没有"——表现数据
    是经浏览器会话读的（Q41），浏览器失效时它和发布链路一起失明，
    这正是简报必须显式呈现、而不是静默显示"无数据"的原因（ADR-0010）。
    """
    out: dict = {"available": False, "status": None, "records": [],
                 "frequency": {}, "articles": {}}

    store = root / "reports" / "performance.jsonl"
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out["records"].append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 容忍坏行

    st = read_json(root / ".newsagg" / "performance-status.json")
    if st:
        out["status"] = st
        out["available"] = bool(st.get("ok"))

    freq = read_json(root / ".newsagg" / "frequency.json")
    if freq:
        out["frequency"] = freq

    # 每篇取最新观测（用于 ③ 段呈现本篇表现）
    for r in out["records"]:
        if not r.get("ours"):
            continue
        key = r.get("gid") or r.get("slug")
        if key:
            out["articles"][key] = r
    return out


def fmt_rate(rate) -> str:
    if rate is None:
        return "无"
    try:
        return f"{float(rate):.2%}"
    except (TypeError, ValueError):
        return "无"


def fmt_metrics(metrics: dict | None) -> str:
    m = metrics or {}
    label = {"showCount": "展现", "readCount": "阅读", "diggCount": "点赞",
             "commentCount": "评论", "repinCount": "转发"}
    parts = [f"{label[k]} {m[k]}" for k in label if k in m]
    return " / ".join(parts) if parts else "无"


def clip(text: str, limit: int) -> str:
    """按句截断，而不是按字符硬切。

    理由（实测 2026-10-17）：硬切会把理由砍在半句上，读者拿到的是
    「…现有 6 张卡无一接得住「AI 资本定价」这一议题。它够格建新卡——可复用性明确：
    能接住融资、估值、资本开支、算力投资回报等未来新闻，且与现有卡形成互补（现有卡
    都在谈「技术」，没有一张谈「钱」）。但本轮每日成文上限为 1，已在与 Meta 开源簇的排序中让」
    ——最后半句没了，而 ADR-0019 说简报是**人机接口**、⑤ 段承载所有需要人否决的事项。
    砍在句末至少让每一段可读；若单句本身就超长，才退回硬切。
    """
    s = str(text or "")
    if len(s) <= limit:
        return s
    head = s[:limit]
    # 优先在句末断开（中文句号/问号/叹号/分号，或英文句点后跟空格）
    for mark in ("。", "？", "！", "；", ". "):
        cut = head.rfind(mark)
        if cut > limit * 0.5:
            return head[:cut + len(mark)].rstrip()
    return head.rstrip() + "…"


def read_run_status(root: Path, date: str) -> dict:
    """读本轮运行的成败（ADR-0038）。

    `.newsagg/` 是运行时状态、不入库，看守读不到；但简报可以。因此编排器每轮
    无论成败都写一份 run-status，由简报把它呈现给人——这是把"主系统自己失败了"
    这件事从 `.newsagg/` 带到 `reports/` 的唯一通路。

    只有当这份状态属于本简报的日期时才采用，避免把昨天失败写在今天简报里。
    """
    st = read_json(root / ".newsagg" / "run-status.json")
    if not st or str(st.get("date")) != date:
        return {}
    return st


def section_conclusion(decision, publish_recs, date: str, perf: dict | None = None,
                       run_status: dict | None = None) -> list[str]:
    out = ["## ① 今日结论", ""]
    d = unwrap(decision)

    # 运行失败优先于一切：此时"没发布"不是判断，是故障。
    rs = run_status or {}
    if rs and not rs.get("ok"):
        out += [f"**本轮运行未完成**（阶段 `{rs.get('stage')}`）：{rs.get('reason')}",
                "",
                "> 这不是「今日无选题」，也不是弃权——系统在运行途中失败了。",
                "> 它已经把自己的成败写进本简报，因此看守不会把这天当成正常日。",
                ""]
        detail = (rs.get("detail") or "").strip()
        if detail:
            out += ["```", detail[:1200], "```", ""]
        return out

    if not d:
        # 降频跳过日会刻意不产出决策文件。若不特判，① 段会写成"无决策记录"，
        # 那正是 ADR-0019 要求区分开的两种情形之一（"系统没跑"）——会误报。
        freq = (perf or {}).get("frequency") or {}
        if freq.get("downshifted"):
            out += [f"**降频跳过日**（系统处于自动降频状态，自 {freq.get('since')} 起隔日发文）。",
                    "今日只出简报，不采集、不写作、不发布。",
                    "",
                    "> 这是 ADR-0010 的设计行为，不是故障；也不是「今日无选题」。",
                    f"> 恢复需人工确认：{freq.get('how_to_clear') or '删除 .newsagg/frequency.json'}",
                    ""]
            return out
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
            summary = clip((d.get("event") or {}).get("summary", ""), 120)
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
        runs = candidates.get("collection_runs") or []
        out += [f"- 采集 {c.get('fetched',0)} 条 → 赛道预筛后 {c.get('kept',0)} 条"
                f"（丢弃 {c.get('dropped',0)}）"]
        # ADR-0006：白天采 2–3 次以捕捉事件发酵过程。采了几次、每次进来多少条
        # 新条目，正是"发酵"的可观测形式——不呈现就等于白采。
        if runs:
            # kept_new 在早期版本里是 None（字段尚未写入），`.get(k, '?')` 的默认值
            # 不会生效（键存在、值是 None），会印出「新 ?」这种读不懂的东西。
            def _kn(r):
                v = r.get("kept_new")
                return "未记录" if v is None else v
            out += [f"- 今日采集 {len(runs)} 次：" + "、".join(
                f"{str(r.get('at',''))[11:16]}（新 {_kn(r)}）" for r in runs)]
        out += [f"- 源：{c.get('sources_ok',0)} 成功 / {c.get('sources_failed',0)} 失败"]
        stale = candidates.get("stale_sources") or []
        if stale:
            out += [f"- **陈旧源**（updateTime 超过阈值）："
                    + "、".join(f"{s['source']}({s['age_minutes']}分)" for s in stale)]
        # 字段名是 failures（list[dict]），早期版本误读 failed_sources 导致这段永远空白
        failed = [f.get("source") for f in (candidates.get("failures") or [])]
        if failed:
            out += [f"- **失效源**：{', '.join(map(str, failed))}"]
    else:
        out += ["- （无采集记录）"]

    if decision:
        low = decision.get("low_confidence_skipped") or []
        if low:
            out += ["", "**低置信度（已跳过）**："]
            for it in low:
                out += [f"- {clip(it.get('event',''), 120)}（{it.get('confidence')}）"]
                if it.get("why"):
                    out += [f"  - {clip(it['why'], 400)}"]
        nca = decision.get("no_card_available") or []
        if nca:
            out += ["", "**值得关注但无卡可接**（供你人工指定）："]
            for it in nca:
                out += [f"- {clip(it.get('event',''), 120)}"]
                if it.get("why"):
                    out += [f"  - {clip(it['why'], 400)}"]
        note = (decision.get("decision") or decision).get("ranking_note")
        if note:
            out += ["", f"**排序说明**：{clip(note, 800)}"]
    out += [""]
    return out


def section_article(decision, publish_recs, body_dir: Path, perf: dict | None = None) -> list[str]:
    out = ["## ③ 本篇文章与发布结果", ""]
    d = unwrap(decision)

    # 表现数据（ADR-0010 循环 C）：呈现的是**已发表文章**的最新一次读回，
    # 不是今天这篇——今天这篇当天还没有数据（这句必须说清，否则会误导）。
    # 放在弃权早返回之前：弃权日恰恰是本人最想看已发文章表现的时候。
    perf_block: list[str] = []
    if perf and perf.get("articles"):
        perf_block = ["", "**已发表文章的表现**（截至本次读回，累计值）："]
        rows = sorted(perf["articles"].values(),
                      key=lambda r: str(r.get("published_at") or ""))
        for r in rows:
            perf_block += [f"- {r.get('title','')}：阅读率 {fmt_rate(r.get('read_rate'))}"
                           f"（{fmt_metrics(r.get('metrics'))}）"]

    if not d or d.get("action") == "abstain":
        out += ["本日无文章。"]
        out += perf_block
        out += [""]
        return out

    ev = d.get("event") or {}
    if ev:
        out += [f"- 事件：{clip(ev.get('summary',''), 200)}"]
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

    out += perf_block
    out += [""]
    return out


def section_assets(decision, cards_dir: Path) -> list[str]:
    out = ["## ④ 资产变动", ""]
    before = len(list(cards_dir.glob("*.md"))) if cards_dir.exists() else 0
    out += [f"- 观点卡总数：{before}"]
    d = unwrap(decision)
    if d.get("action") == "new":
        out += [f"- **新建卡**：{d.get('new_issue')}（可复用性：{clip(d.get('reusable_because',''), 300)}）"]
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


def section_actions(publish_recs, candidates, decision, cover_path: Path | None, article_slug: str | None, perf: dict | None = None, run_status: dict | None = None) -> list[str]:
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

    failed = [f.get("source") for f in ((candidates or {}).get("failures") or [])]
    if failed:
        any_item = True
        out += [f"**信源失效**：{', '.join(map(str, failed))}", ""]

    # 运行失败（ADR-0038）：`.newsagg/run-status.json` 不入库、看守读不到，
    # 所以它必须在这里出现——否则"系统今天跑挂了"就没有任何出口。
    rs = run_status or {}
    if rs and not rs.get("ok"):
        any_item = True
        out += [f"**本轮运行未完成**：阶段 `{rs.get('stage')}` — {rs.get('reason')}",
                "  产出可能不完整。修复后重跑：`bash .agents/skills/newsagg-collect/scripts/run_all.sh`",
                ""]

    if perf:
        freq = perf.get("frequency") or {}
        if freq.get("downshifted"):
            any_item = True
            out += [f"**系统已自动降频（隔日发文）**，自 {freq.get('since')} 起。",
                    f"  原因：{freq.get('reason') or '（未记录）'}",
                    "  这是 ADR-0010 的设计行为，不是故障——但它会让系统**变得更不活跃**，",
                    "  产出下降本身会掩盖问题的严重性，所以必须在这里告警。",
                    f"  恢复需人工确认：{freq.get('how_to_clear') or '删除 .newsagg/frequency.json'}",
                    ""]

        status = perf.get("status")
        if status and not status.get("ok"):
            any_item = True
            out += [f"**表现数据读回失败**：{status.get('reason')}（阶段 {status.get('stage')}）",
                    "  它和发布链路共用浏览器会话（ADR-0010），因此失败往往与登录态相关；",
                    "  本次运行不受影响，但排序参考与降频判据暂时是旧的。",
                    ""]
            st = status.get("detail") or {}
            if st.get("lock_timeout"):
                out += ["  等待浏览器锁超时——检查是否有残留的锁文件或并发进程（ADR-0030）。", ""]

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
    perf = read_performance(root)
    run_status = read_run_status(root, date)
    lines += section_conclusion(decision, publish_recs, date, perf, run_status)
    lines += section_overview(cand, decision)
    lines += section_article(decision, publish_recs, root / ".newsagg" / "bodies", perf)
    lines += section_assets(decision, root / "knowledge" / "cards")
    lines += section_actions(publish_recs, cand, decision, cover, slug, perf, run_status)

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
        "performance_available": bool(perf.get("available")),
        "run_ok": run_status.get("ok") if run_status else None,
        "sections": 5,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
