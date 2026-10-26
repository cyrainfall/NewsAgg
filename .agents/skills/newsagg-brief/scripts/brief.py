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


def read_ledger_all(path: Path) -> list[dict]:
    """读**全部**账本记录，不管日期。

    `read_ledger()` 按日期前缀过滤，那是给「今天发了什么」用的。但有些事实没有
    过期日期——比如一条已经发出去、删不掉的微头条（ADR-0047）。它发生在哪天不重要，
    重要的是它现在还在平台上。只在当天简报里提一次，等于让它静默消失。
    """
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ADR-0021 Decision 四：Agent-Reach 的渠道体检进第 ⑤ 段。
# 放在同目录、单独一个模块，是因为它有**自己的失败语义**——它是一个活跃的
# 外部依赖（ADR-0021 不钉版本），可能没装、可能超时、可能输出不可解析。
# 这些都必须降级成「读不到」而不是让简报失败：简报必须无条件每日存在
# （ADR-0006）。ADR-0021 承诺了这条通路五年没实现，这里是第一次接上。
import importlib.util as _ilu

def _load_source_health():
    """按路径加载同目录的 source_health 模块。

    不用 `import source_health`：简报是脚本，`python3 path/to/brief.py` 运行时
    同目录**不一定**在 sys.path 里（脚本所在目录会在，但 `python3 -m` 或
    被 import 时不一定）。按路径加载最稳，也和 tests/ 里的做法一致。
    """
    p = Path(__file__).resolve().parent / "source_health.py"
    spec = _ilu.spec_from_file_location("newsagg_source_health", p)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def unwrap(raw):
    """决策文件形状是 {date, decision:{...}, ...}；也接受扁平对象。

    这个解包必须统一，否则会出现"某一段读得到、另一段读不到"的静默空白
    （本文件早期版本就在 ① 段落上犯过这个错）。
    """
    if not raw:
        return {}
    return raw.get("decision", raw) if isinstance(raw, dict) else {}


def _is_weitoutiao(rec: dict) -> bool:
    """这条记录是不是微头条（ADR-0047）。

    `content list` 把文章与微头条混在同一个列表返回，两者都能匹配上本地标题。
    微头条是同一次发布动作的产物，不能算成「我们的文章」——否则简报会多出
    一行与文章同名、数字极小的「文章」，读起来像是发重了（10-18 就是如此）。
    老记录没有 `kind` 字段，按 gid 位数回退：文章 19 位、微头条 16 位。
    """
    kind = rec.get("kind")
    if kind in ("article", "weitoutiao"):
        return kind == "weitoutiao"
    gid = str(rec.get("gid") or "")
    return bool(gid) and len(gid) <= 17


def _photo_attribution(root: Path, photo_path: str) -> str:
    """从照片账本里查出这张图当时依据的许可证与出处（ADR-0048）。

    为什么要进简报：照片层只接受 CC0 / 公有领域，但「图文无关」没有自动检查
    （残余风险 37）。这条来源行是给人抽查用的唯一抓手——否则「今天用了哪张图、
    凭什么说它能用」在流水线里完全没有出口。

    查不到就返回空串而不是编一句。**账本读不到不等于这张图有问题，
    也不等于它没问题**——简报里少一行，比印一行假来源好。
    """
    if not photo_path:
        return ""
    ledger = root / ".newsagg" / "photos" / "ledger.jsonl"
    if not ledger.exists():
        return ""
    want = str(photo_path)
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("local") or "") != want:
            continue
        parts = []
        if rec.get("license"):
            parts.append(f"许可证 {rec['license']}")
        if rec.get("file_title"):
            parts.append(f"来源 {rec['file_title']}")
        if rec.get("artist"):
            parts.append(f"作者 {rec['artist']}")
        if rec.get("descriptionurl"):
            parts.append(str(rec["descriptionurl"]))
        return "；".join(parts)
    return ""


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
        if _is_weitoutiao(r):
            continue  # ADR-0047：微头条不算一篇文章
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


def _fmt_age(minutes) -> str:
    """把分钟数写成「分钟」或「小时」，避免出现「1230.9分」这种读不出量级的写法。

    陈旧阈值现在是**按源**算的（ADR-0043），github 的阈值是 2160 分钟。以分钟为单位
    呈现长周期源，会让「阈值 2160.0 分」看起来像一个荒谬的数字——读者得自己在脑子里
    除 60 才能判断这是 36 小时。简报是人机接口（ADR-0019），它不该要求人做换算。
    """
    if minutes is None:
        return "未知"
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return str(minutes)
    if m >= 120:
        return f"{m / 60:.1f}小时"
    return f"{m:.0f}分钟"


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
                # ADR-0051：平台实际收到的标题可能被输入框吞字，与成稿标题不同。
                # 不写出来，你会照着简报里的标题去后台搜，然后搜不到。
                shown = str(r.get("title_on_platform") or "").strip()
                if shown and shown != str(r.get("title") or "").strip():
                    out += [f"> ⚠ 平台实际标题是《{shown}》——成稿标题「{r.get('title','')}」"
                            f"在输入框里被吞了字（ADR-0051）。到后台按**实际标题**查。", ""]
        else:
            summary = clip((d.get("event") or {}).get("summary", ""), 120)
            out += [f"**已成稿，尚未发布**。选题：{summary}", ""]
    elif action == "skip":
        out += [f"**跳过**。卡 {d.get('card_file')} 已含该事件且无新进展。", ""]
    else:
        out += [f"**未识别的决策**：`{action}`", ""]
    return out


def section_overview(candidates, decision, trend_summary=None) -> list[str]:
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
            out += [f"- **陈旧源**（updateTime 超过该源自身的刷新周期）："
                    + "、".join(f"{s['source']}({_fmt_age(s.get('age_minutes'))}"
                               f" / 阈值 {_fmt_age(s.get('threshold_minutes'))})" for s in stale)]
        # 字段名是 failures（list[dict]），早期版本误读 failed_sources 导致这段永远空白
        failed = [f.get("source") for f in (candidates.get("failures") or [])]
        if failed:
            out += [f"- **失效源**：{', '.join(map(str, failed))}"]
        # 同一轮里已兜底成功的源不算失败，但值得一行——它说明聚合接口在抖
        # （ADR-0066）。不印出来，`recoveries` 就是个死字段（ADR-0062 的判据）。
        recovered = [f for f in (candidates.get("recoveries") or [])]
        if recovered:
            out += ["- **源兜底成功**（聚合接口出错、已改用原始接口，数据是齐的）："
                    + "、".join(
                        f"{f.get('source')}（{str(f.get('error',''))[:60]} → {f.get('recovered_by')}）"
                        for f in recovered)]
    else:
        out += ["- （无采集记录）"]

    # 位次趋势的**覆盖率**（ADR-0056）。这一行必须出现在简报里：trend 是分拣排序的
    # 依据之一，而它在「今天只采了一次」时会整池退化成 unknown。若简报只说
    # 「分拣已完成」，本人会以为排序看过爬升——实际那一轮排序退回了当前热度。
    # 不呈现信号质量，等于把「看不见」印成「没变化」。
    ts = trend_summary or {}
    by_state = ts.get("by_state") or {}
    if by_state:
        computable = sum(by_state.get(k, 0) for k in ("climbing", "steady", "cooling"))
        total = sum(by_state.values())
        line = (f"- 位次趋势：{computable}/{total} 条可算"
                f"（climbing {by_state.get('climbing',0)}"
                f" / steady {by_state.get('steady',0)}"
                f" / cooling {by_state.get('cooling',0)}"
                f"；unknown {by_state.get('unknown',0)}"
                f" / absent {by_state.get('absent',0)} 不参与排序）")
        srcs = ts.get("by_position_source") or {}
        if srcs:
            line += "｜位次来源 " + "、".join(f"{k} {v}" for k, v in sorted(srcs.items()))
        out += [line]
        if computable == 0:
            # 整池不可算是**信号失效**，不是「大家都很稳」。用加粗把它从
            # 「一行统计」升成「需要你知道的事」，否则它会被扫过去。
            out += ["- **本轮 trend 不可用**：没有任何条目能算出位次变化，"
                    "分拣已退回「当前热度 + 卡匹配度」排序（不是「所有条目都没动」）"]

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
    # 钱落点（ADR-0058）：分拣判定的角色落点。此前它只有生产者、没有消费者——
    # 写进决策 JSON 就再没人读过（写作阶段拿不到、简报也不印）。现在两头都接上：
    # 写作阶段作为硬约束（build_brief.py），简报作为人事后复核「这篇文章该对谁说话」
    # 的唯一窗口。整段印出来而不是截断：它是散文，截断会把「具体到角色」压成空话。
    money_point = (d.get("money_point") or "").strip()
    if money_point:
        out += ["", f"**钱落点（分拣判定）**：{money_point}"]

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


def section_assets(decision, cards_dir: Path, article_slug: str | None = None) -> list[str]:
    out = ["## ④ 资产变动", ""]
    before = len(list(cards_dir.glob("*.md"))) if cards_dir.exists() else 0
    out += [f"- 观点卡总数：{before}"]
    d = unwrap(decision)
    if d.get("action") == "new":
        cf = d.get("card_file")
        exists = bool(cf) and (cards_dir / str(cf)).exists()
        out += [f"- **新建卡**：{d.get('new_issue')}（可复用性：{clip(d.get('reusable_because',''), 300)}）"]
        out += [f"- 卡文件：{cf or '（决策未给出）'}{'' if exists else '——**文件不存在**'}"]
    elif d.get("action") == "continue":
        cf = str(d.get("card_file") or "")
        # 写回是否真的落地，只能看卡里有没有「来源 <slug>」这一行——
        # 它是 writeback.py 写入修订时的固定格式，也是它自己的幂等判据。
        # 此前这里印的是固定文案「待发布成功后写回」，在发布成功且写回已完成时
        # 会长期显示成"还没写"，与事实相反（2026-10-18 实测）。
        landed = False
        card_path = cards_dir / cf if cf else None
        if card_path and card_path.exists() and article_slug:
            landed = f"来源 {article_slug}" in card_path.read_text(encoding="utf-8")
        status = "已写回，带**未复核**标记（ADR-0026 循环 A）" if landed else "**未写回**（发布未成功，或写回失败）"
        out += [f"- **修订**：{cf} — {status}"]
    # 治理建议（退役/合并）由分拣层在需要时提供
    for key, label in (("retire_suggestions", "退役建议"), ("merge_suggestions", "合并建议")):
        items = (decision or {}).get(key) or []
        if items:
            out += ["", f"**{label}**（等你否决）："]
            for it in items:
                out += [f"- {it}"]
    out += [""]
    return out


def section_actions(publish_recs, candidates, decision, cover_path: Path | None, article_slug: str | None, perf: dict | None = None, run_status: dict | None = None, ledger_path: Path | None = None, cover_report: dict | None = None, photo_line: str = "", date: str = "", root: Path | None = None) -> list[str]:
    out = ["## ⑤ 需你介入的事项", ""]
    any_item = False
    root = root or repo_root()

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

    # 微头条泄漏（ADR-0047）：一次发布动作本该只产出一篇文章。若反查发现同一
    # 标题下还有微头条，说明 `--no-weitoutiao` 没生效——10-14 与 10-18 各发生过一次，
    # 两次都被反查判成 title_found、无人知晓。平台侧无法经本 CLI 删除，
    # 只能人工处置，所以必须在这里显式列出。
    #
    # ⚠️ 这里读的是**全部**账本，不是当天那几条：泄漏一旦发生就永久存在（删不掉），
    # 只在当天简报里提醒一次等于让它消失。提醒在两个终态下才停，而这两个终态
    # **不是同一件事**（ADR-0055）：
    #   `leak_resolved_at`  = 人删掉了那条微头条
    #   `leak_accepted_at`  = 人决定保留（2026-10-22 用户明确表示「保留即可」）
    # 早先只有前者，于是「决定保留」只能被记成「已删除」——账本会说谎，
    # 而账本是复盘时唯一的事实来源。所以宁可多一个字段。
    all_recs = read_ledger_all(ledger_path) if ledger_path else []
    leaks = [r for r in all_recs
             if r.get("weitoutiao_leak")
             and not r.get("leak_resolved_at")
             and not r.get("leak_accepted_at")]
    if leaks:
        any_item = True
        out += ["**同发了微头条，需要你手工删除**（ADR-0047）："]
        for r in leaks:
            out += [f"- {str(r.get('at',''))[:10]}《{r.get('title','')}》"
                    f" ← gid {r.get('weitoutiao_leak')}"]
        out += ["  发布页的「发布得更多收益」默认**未勾选**，旧代码却无条件点了一下、",
                "  反而把它勾上。发布链已改为「先读状态、只在该取消时点、点后读回验证」，",
                "  但已经发出去的删不掉（CLI 无删除能力）。",
                "  处理方式二选一，写进 `.newsagg/publish-ledger.jsonl` 对应记录：",
                "    `\"leak_resolved_at\": \"<日期>\"` —— 你删掉了那条微头条",
                "    `\"leak_accepted_at\": \"<日期>\"` —— 你决定保留它（ADR-0055）",
                "  写了任一个，这条提醒就会停。",
                ""]

    # 误发（ADR-0059）：账本里 `accidental: true` 是「不该发、但已经发出去了」的文章。
    # `count_published_today` 会跳过它（不占当日额度），但**跳过不等于解决**——平台侧
    # 还挂着那篇重复文章，只有人能在 Web 后台删掉。
    #
    # ADR-0059 当时只做到「留在账本里」，没做消费者：简报不读这两个字段，于是系统
    # 自己知道自己误发了，却从不告诉唯一能处理这件事的人。这是同一族缺陷（记录了但
    # 没有实现）的第七次，也是最短命的一次——ADR-0059 写完的第二天就复现了。
    # 与微头条泄漏同理：一旦发生就永久存在，所以每个终态都要显式写回账本才停。
    accidents = [r for r in all_recs if r.get("accidental") and not r.get("deleted_at")]
    if accidents:
        any_item = True
        out += ["**误发了一篇，需要你手工删除**（ADR-0059）："]
        for r in accidents:
            out += [f"- {str(r.get('at',''))[:10]}《{r.get('title','')}》"
                    f" ← gid {r.get('gid','')}"]
        out += ["  这些是绕过「每日 1 篇」上限测试时真的发出去的重复文章。",
                "  发布链本身没有重试，问题出在测试放行路径等于测试发布（ADR-0059）。",
                "  toutiao-ops CLI 没有 delete 命令，只能在头条 Web 后台删：",
                "  创作中心 → 内容管理 → 找到该篇 → 删除。",
                "  删完把 `\"deleted_at\": \"<日期>\"` 写进 `.newsagg/publish-ledger.jsonl` 对应记录，",
                "  这条提醒就会停。**不要**只把 `accidental` 改成 false——那会让账本说谎。",
                ""]

    # 决定保留的那些，只在**做决定那天**确认一次，之后不再出现。
    # 若每天都列一遍，「保留」就变成了另一种形式的提醒——用户要的是安静，
    # 不是换一种措辞的唠叨。事实本身留在账本里，简报不必每天复述。
    #
    # 这一段必须独立于上面的 `if leaks`：同一天完全可能既有「刚决定保留的」，
    # 又有「还没人处理的」。写进 else 分支会让前者在后者存在时静默消失——
    # 而「我昨天让你保留的那两条，今天没出现在简报里」正是最容易被误读成
    # 「指令没生效」的形态。
    accepted_today = [r for r in all_recs
                      if r.get("weitoutiao_leak")
                      and not r.get("leak_resolved_at")
                      and str(r.get("leak_accepted_at", ""))[:10] == date]
    if accepted_today:
        any_item = True
        out += ["**微头条按你的决定保留，不再提醒**（ADR-0055）："]
        for r in accepted_today:
            out += [f"- {str(r.get('at',''))[:10]}《{r.get('title','')}》"
                    f" ← gid {r.get('weitoutiao_leak')}"]
        out += [""]

    # 封面自检（ADR-0044 发现六）：封面质量问题的旧形态是**静默**——自检只回报
    # 一个常量、警告没人读。而「封面只是在复述标题」「缩略图下字太小」都不会让
    # 发布失败，只会让这篇白写。无人值守时没有第二双眼睛，所以必须在这里出现。
    # 数据来自 run_all.sh 存下的 `.newsagg/covers/<date>.report.json`。
    if cover_report:
        warns = cover_report.get("warnings") or []
        probs = cover_report.get("problems") or []
        # 照片来源（ADR-0048）：只要这篇用了 Commons 照片，就把许可证与出处列出来。
        # 「图文无关」没有自动检查（残余风险 37），这条来源行是给人抽查用的抓手；
        # 同时也是唯一能证明「这张图当时被判为 CC0」的位置。
        if cover_report.get("photo_enabled"):
            any_item = True
            out += [f"**封面照片**：{cover_report.get('photo') or ''}"]
            if photo_line:
                out += [f"  - {photo_line}"]
            else:
                out += ["  - （照片账本里查不到这张图的许可证记录——ADR-0048 要求可回查，"
                        "请确认 .newsagg/photos/ledger.jsonl 是否被清空过）"]
            out += [""]
        if warns or probs or cover_report.get("restates_title"):
            any_item = True
            out += ["**封面自检**（ADR-0044）："]
            for p in probs:
                out += [f"- ✗ {p}"]
            for w in warns:
                out += [f"- ! {w}"]
            if cover_report.get("restates_title"):
                out += ["  这张封面没有说出标题以外的任何东西。信息流里标题就印在封面",
                        "  旁边，复述等于浪费了唯一一块视觉位置。修法：写作阶段写",
                        "  `.newsagg/covers/<日期>.json` 给出视觉论据（`--spec`）。"]
            out += [""]

    # 运行失败（ADR-0038）：`.newsagg/run-status.json` 不入库、看守读不到，
    # 所以它必须在这里出现——否则"系统今天跑挂了"就没有任何出口。
    rs = run_status or {}
    if rs and not rs.get("ok"):
        any_item = True
        out += [f"**本轮运行未完成**：阶段 `{rs.get('stage')}` — {rs.get('reason')}",
                "  产出可能不完整。修复后重跑：`bash .agents/skills/newsagg-collect/scripts/run_all.sh`",
                ""]

    # 降级完成（ADR-0061）：跑完了，但有一处让步。与 ok=0 分开是因为它的处置
    # 完全不同——不需要「修复后重跑」，只需要人知道「这一轮少了什么」。
    # 当前唯一来源是 ① 远端不可达（代理/网络问题），让步是「收尾没 push」。
    degraded = (rs.get("degraded") or "").strip() if rs else ""
    if degraded:
        any_item = True
        out += ["**本轮降级完成**（不是失败，但有一处让步）：", f"- {degraded}", ""]

    # 信源健康（ADR-0021 Decision 四）：Agent-Reach 的 tier-0 渠道体检。
    # 这一段**独立取数**，不依赖采集层的 failed_sources——采集层只知道
    # DailyHotApi 那七个源，而 Agent-Reach 探测的是另外那批深度信源
    # （github / rss / exa / youtube / v2ex / web）。两者互补：前者说
    # 「热榜源挂了」，后者说「热榜源之外的那层也没了」。
    try:
        _sh = _load_source_health()
        _health = _sh.read_health(root)
        _items = _sh.render(_health)
    except Exception:  # noqa: BLE001 —— 体检跑不起来绝不能让简报出不来（ADR-0006）
        _items = []
    if _items:
        any_item = True
        out += _items

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
    # 分拣**输入包**（不只是决策）：位次趋势的覆盖率只存在于这里（ADR-0056）。
    # 决策文件里只有模型写下的 ranking_note，看不出「这一轮到底有没有 trend 可用」——
    # 而简报的职责正是让人一眼看出信号是否退化。
    triage_input = read_json(root / ".newsagg" / "triage" / f"{date}-input.json")
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
    lines += section_overview(cand, decision, (triage_input or {}).get("trend_summary"))
    lines += section_article(decision, publish_recs, root / ".newsagg" / "bodies", perf)
    lines += section_assets(decision, root / "knowledge" / "cards", slug)
    cover_report = read_json(root / ".newsagg" / "covers" / f"{date}.report.json")
    lines += section_actions(publish_recs, cand, decision, cover, slug, perf, run_status,
                             ledger_path=root / ".newsagg" / "publish-ledger.jsonl",
                             cover_report=cover_report,
                             photo_line=_photo_attribution(
                                 root, (cover_report or {}).get("photo_path") or ""),
                             date=date, root=root)

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
