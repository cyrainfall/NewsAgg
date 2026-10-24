#!/usr/bin/env python3
"""分拣层的确定性部分：读卡、组包、落盘三态决策结果。

设计依据：
- ADR-0007 观点卡结构（frontmatter 的 covered_events 支撑跳过/续写判定）
- ADR-0016 管道形状（全量读卡 → 三态决策 → 排序取一 → 弃权）
- ADR-0019 冷却期优先于排序、突发事件走「无卡可接」
- ADR-0025 技能装判断、插件装强制：本脚本只做确定性工作

本脚本**不做判断**。判断（这条事件接得住哪张卡、有无新进展）属于模型，
由 SKILL.md 引导。脚本负责：
  1. 读全部观点卡（frontmatter + 正文）供模型阅读
  2. 把候选条目按事件线索粗分组（供模型聚簇时参考，非替代）
  3. 校验模型给出的三态决策（结构校验 + 冷却期检查 + 每日限额）
  4. 落盘选题信号
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


def _load_cardfm():
    """加载同目录的 `cardfm`（观点卡 frontmatter 的唯一解析实现，ADR-0042）。

    用 sys.path 注入而不是直接 `import cardfm`：本脚本既可能被 `python3 <路径>`
    直接执行（脚本目录自动在 sys.path 上），也可能被 importlib 按路径加载
    （那时脚本目录不在 sys.path 上）。两种方式都要能工作。
    """
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import cardfm
    return cardfm


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """委托给同目录 `cardfm`（ADR-0042：三份拷贝漂移过一次，只留一份实现）。

    本函数曾把「空值」记成 `""`，而 `writeback.py` 写回的 `covered_events`
    是「空值 + 列表项」形态——列表项因此被静默丢弃，**分拣层永远看不到
    「已覆盖事件」**，而 `build_brief.py` 看得到。详见 `cardfm.py` 的模块说明。
    """
    fm, body, _ = _load_cardfm().parse_frontmatter(text)
    return fm, body


def load_cards(cards_dir: Path) -> list[dict]:
    """读入全部 active 卡（ADR-0007：分拣全量读卡）。退役卡不参与。"""
    cards = []
    for path in sorted(cards_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        if (fm.get("status") or "active") != "active":
            continue
        cards.append(
            {
                "file": path.name,
                "issue": fm.get("issue") or path.stem,
                "status": fm.get("status") or "active",
                "covered_events": fm.get("covered_events") or [],
                "last_written": fm.get("last_written"),
                "body": body.strip(),
            }
        )
    return cards


def cooldown_state(cards: list[dict], cooldown_days: int, now: datetime) -> dict:
    """计算每张卡的冷却状态（ADR-0019：冷却中的卡不参与竞争）。"""
    state = {}
    for c in cards:
        lw = c.get("last_written")
        if not lw or lw in ("null", "None"):
            state[c["file"]] = {"in_cooldown": False, "days_since": None}
            continue
        try:
            dt = datetime.fromisoformat(str(lw)).replace(tzinfo=CST) if "+" not in str(lw) else datetime.fromisoformat(str(lw))
        except ValueError:
            state[c["file"]] = {"in_cooldown": False, "days_since": None, "parse_error": str(lw)}
            continue
        days = (now - dt).total_seconds() / 86400
        state[c["file"]] = {
            "in_cooldown": days < cooldown_days,
            "days_since": round(days, 2),
        }
    return state


def load_candidates(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def performance_reference(perf_path: Path, runs_path: Path) -> dict:
    """汇总历史表现，供分拣做**排序参考**（ADR-0010）。

    ⚠️ 这是软信号，不是硬规则：ADR-0010 明确不据此自动降权或退役卡片。
    因此这里只把「每张卡接过的文章跑出了什么数据」摆出来，让模型在
    「卡匹配度 × 事件热度」相当时多一个参考维度——**不参与打分，也不设阈值**。
    读不到就返回 available: false，分拣照常进行。
    """
    ref: dict = {"available": False, "note": "无表现数据历史"}
    if not perf_path.exists():
        return ref

    try:
        records = []
        for line in perf_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except (json.JSONDecodeError, OSError) as exc:
        return {"available": False, "note": f"表现数据不可解析：{exc}"}

    # 每篇取最新一次观测
    latest: dict[str, dict] = {}
    for r in records:
        if not r.get("ours"):
            continue
        key = r.get("gid") or r.get("slug")
        if key:
            latest[key] = r
    if not latest:
        return {"available": False, "note": "表现数据里没有本账号的文章"}

    # slug → card_file。两个来源，**账本优先**（ADR-0037）：
    #   ① 发布账本：发布那一刻记下的 card_file，是权威关联，不随日期漂移；
    #   ② 决策文件兜底：账本修复前的旧记录没有该字段，且决策文件按日期散落。
    # 只用 ② 会静默丢归因——决策文件属运行时状态，可能被清理或跨环境不同步。
    slug_to_card: dict[str, str] = {}
    if runs_path.exists():
        for d in sorted(runs_path.glob("*-decision.json")):
            try:
                raw = json.loads(d.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            dec = raw.get("decision", raw)
            card = dec.get("card_file")
            if not card:
                continue
            date = raw.get("date") or d.name[:10]
            for md in sorted((runs_path.parent.parent / "outputs").glob(f"{date}-*.md")):
                slug_to_card[md.stem] = card

    ledger = runs_path.parent / "publish-ledger.jsonl"
    if ledger.exists():
        try:
            for line in ledger.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                slug, card = rec.get("slug"), rec.get("card_file")
                if slug and card:
                    slug_to_card[str(slug)] = str(card)
        except (json.JSONDecodeError, OSError):
            pass

    by_card: dict[str, list[dict]] = {}
    unlinked = 0
    for r in latest.values():
        card = slug_to_card.get(str(r.get("slug") or ""))
        item = {
            "slug": r.get("slug"), "date": r.get("date"),
            "read_rate": r.get("read_rate"), "metrics": r.get("metrics"),
        }
        if card:
            by_card.setdefault(card, []).append(item)
        else:
            unlinked += 1

    return {
        "available": True,
        "note": ("软信号（ADR-0010）：仅作「卡匹配度 × 事件热度」相当时的排序参考，"
                 "不参与打分、不触发降权或退役。卡片历史表现差不等于该卡该退役。"),
        "articles_observed": len(latest),
        "by_card": by_card,
        "unlinked_articles": unlinked,
    }


# ── 热度轨迹 → 位次趋势（ADR-0056）───────────────────────────────────────────
# 采集层把每条候选的 hot_history 累积下来（ADR-0038），但在此之前它没有消费者。
# 判「正在爬升」不能用 hot 的百分比：hot 跨源差几个数量级（2026-10-23 池里 toutiao
# 是 36 万–1128 万、36kr 是 2–44），而且一半的源给的是**静态快照**（sspai 6 条全
# 不动、ithome 10 条里 7 条不动）——对静态源算百分比是纯噪音。实测「首→末」百分比
# 在 2026-10-23 池里 rising 54 / cooling **0**，向上偏置到几乎测不出降温。
#
# 所以主信号取**位次变化**：位次天然按源可比，且直接对应「正在爬升」。
# 位次的刻度必须是**上游榜单深度**，不能是池内名次——池子只是赛道预筛后的子集，
# 每个快照的池内条数会变（实测 toutiao 7→5），池子一缩水同一名次就被拉伸成更高
# 的位次，凭空造出爬升（实测 rank 22→25 退步却被判 climbing，只因池子从 7 缩到 5）。
# 所以 rank 口径用 `(rank-1)/(rank_total-1)`，深度由采集层随 rank 一起写进观测。
# 位次来源两种，**一条轨迹全程只用一种口径**（混用等于拿两把尺子的读数相减）：
#   1. 观测自带 `rank` + `rank_total`（2026-10-24 起写进观测）；
#   2. 都没有的存量观测 → 同快照内按 hot 降序重排（池内相对量，只是近似）。
# delta = 末位次 - 首位次，**为负 = 爬升**。
TREND_CLIMB_DELTA = -0.10
TREND_COOL_DELTA = 0.10
TREND_MIN_OBSERVATIONS = 2


def _hot_int(value):
    """把轨迹里的 hot 读成 int；读不出就 None。

    容忍 str/None 是历史包袱：`hot_history` 里可能存着归一化之前的旧值
    （`'40,798'`，实测 2399 条观测里 15 条 str、76 条 None）。归一化本身属于采集层
    （collect.py 的 `normalize_hot`），这里只做「读得动就读」的最小兜底，免得分拣
    因为一条脏数据整轮崩掉。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def _hot_spread(values: list[int]) -> list[float]:
    """把 hot 序列映射成 0..1 的位次（0 = 最热），同值同位置。

    **只用于 hot。** 上游 `rank` 不走这里——它有自己的绝对刻度 `_rank_position`。
    第一版这个函数同时服务两种口径，靠一个 `descending` 开关区分方向；而 rank 1
    才是榜首，方向写反不会报错，只会让所有爬升被读成降温（实测踩过一次）。现在
    两种口径各有一个函数，那个开关连同它代表的陷阱一起没了。
    """
    uniq = sorted(set(values), reverse=True)
    n = len(uniq)
    if n <= 1:
        return [0.0 for _ in values]
    rank_of = {v: i for i, v in enumerate(uniq)}
    return [rank_of[v] / (n - 1) for v in values]


def _rank_position(obs: dict):
    """把一条观测的上游位次换算成 0..1 的稳定刻度（0 = 榜首）。

    `(rank-1)/(rank_total-1)`：分母是**上游榜单深度**，不是池内条数。这是本函数
    存在的全部理由——池内名次会随赛道预筛的池子大小漂移（见模块头注释里
    toutiao 7→5 的实测），而上游深度不随我们的预筛变化，所以跨快照相减有意义。

    缺 `rank` 或缺 `rank_total` 都返回 None（调用方整条轨迹退回 hot 近似）。
    宁可退回近似也不拿池内名次冒充：那是**看起来更准、实际更错**的读数。
    """
    rank = obs.get("rank")
    total = obs.get("rank_total")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        return None
    if not isinstance(total, int) or isinstance(total, bool) or total <= 1:
        return None
    return min(max((rank - 1) / (total - 1), 0.0), 1.0)


def _snapshot_positions(observations: list[dict]) -> list[dict]:
    """算一个快照（同源同一时刻）内每条观测的位次，两种口径各算一遍。

    返回与 `observations` 等长的列表，每项是 `{"rank": 位次或 None, "hot": 位次或 None}`，
    由 `_item_trend` **按条目**挑一个贯穿始终（口径必须整条轨迹统一）。

    两种口径的性质完全不同，这也是为什么它们能各自独立算：
    - `rank` 是**绝对刻度**（上游深度归一），每条观测自己就能算，不需要同快照的
      其它条目陪衬，也不怕快照里只有一条；
    - `hot` 是**相对刻度**（同快照内降序重排），必须有至少两条同快照条目才量得出来
      ——单条目快照的百分位恒为 0，把它当 0 用会让这类源永远 steady。
    """
    n = len(observations)
    rank_pcts: list = [None] * n
    hot_pcts: list = [None] * n

    for i, obs in enumerate(observations):
        rank_pcts[i] = _rank_position(obs)

    hots = [_hot_int(o.get("hot")) for o in observations]
    usable = [i for i, h in enumerate(hots) if h is not None]
    if len(usable) >= TREND_MIN_OBSERVATIONS:
        spread = _hot_spread([hots[i] for i in usable])
        for slot, i in enumerate(usable):
            hot_pcts[i] = spread[slot]

    return [{"rank": r, "hot": h} for r, h in zip(rank_pcts, hot_pcts)]


def _item_trend(item: dict, positions: dict, latest_at: dict) -> dict:
    """单条候选的 trend。`absent` / `unknown` 是**独立状态**，不能折叠成 steady。

    口径（rank / hot）在这里**按条目定一次，整条轨迹用同一个**。这是必须的：
    位次只有在同一把尺子下相减才有意义，换尺子的那一端会让 delta 变成「两个刻度
    的差值」而不是「名次的移动」。2026-10-24 实测就撞上了这个换尺日——观测从这天
    起才带 rank，于是同一条候选的前几次观测只能按 hot 重排、最后一次却按 rank，
    若跨口径相减，94/115 条会得到无意义的 delta（当时被标成 `mixed`）。

    规则：**rank 覆盖到两条及以上观测才用它，否则整条轨迹退回 hot**。理由：rank
    是上游真实位次，比「hot 重排的近似」准；但只有一条 rank 观测时算不出变化，
    而 hot 在**每一条**观测里都有（新观测同时写 hot 与 rank），所以退回 hot 不会
    丢观测，只是精度降到近似。`hot` 与 `rank` 在同源内只是粗略相关，这层近似
    记在 `position_source` 里，让人看得出这一条的 delta 是哪把尺子量的。
    """
    source = item.get("source")
    per_basis: dict = {"rank": [], "hot": []}
    for obs in item.get("hot_history") or []:
        if not isinstance(obs, dict):
            continue
        pos = positions.get(id(obs)) or {}
        at = obs.get("at") or ""
        for basis in ("rank", "hot"):
            if pos.get(basis) is not None:
                per_basis[basis].append((at, pos[basis]))

    basis = "rank" if len(per_basis["rank"]) >= TREND_MIN_OBSERVATIONS else "hot"
    usable = sorted(per_basis[basis], key=lambda pair: pair[0])
    # 同一时刻可能因合并出现重复观测，只留最后一条
    dedup: list[tuple] = []
    for at, pct in usable:
        if dedup and dedup[-1][0] == at:
            dedup[-1] = (at, pct)
        else:
            dedup.append((at, pct))

    base = {
        "observations": len(dedup),
        "first_at": dedup[0][0] if dedup else None,
        "last_at": dedup[-1][0] if dedup else None,
        "position_source": basis if dedup else None,
    }

    if item.get("seen_in_last_run") is False:
        return {"state": "absent", **base,
                "reason": "本轮采集里没有再次出现；掉出榜单本身是信息，不当作降温"}
    if len(dedup) < TREND_MIN_OBSERVATIONS:
        return {"state": "unknown", **base,
                "reason": f"可算观测不足（{len(dedup)} < {TREND_MIN_OBSERVATIONS}）"}
    if latest_at.get(source) and dedup[-1][0] != latest_at[source]:
        return {"state": "unknown", **base,
                "reason": "末观测不在最新快照上，轨迹可能已中断"}

    delta = round(dedup[-1][1] - dedup[0][1], 3)
    if delta <= TREND_CLIMB_DELTA:
        state = "climbing"
    elif delta >= TREND_COOL_DELTA:
        state = "cooling"
    else:
        state = "steady"
    return {"state": state, "delta": delta, **base}


def compute_trends(items: list[dict]) -> tuple[list[dict], dict]:
    """给每条候选算 `trend`，并给出整包的 `trend_summary`（ADR-0056）。

    返回 (带 trend 的候选列表, 汇总)。原条目不被就地修改。

    汇总里必须暴露**覆盖率与阈值**，而不是只给结论：阈值是经验值、会静默过期
    （与 ADR-0043 的 `SOURCE_TTL_MINUTES` 同型）；「今天只采了一次」会让整池都是
    unknown，如果只给结论，模型会把它读成「今天大家都很稳」——那是把**看不见**
    说成了**没变化**。
    """
    groups: dict = {}
    for it in items:
        source = it.get("source")
        for obs in it.get("hot_history") or []:
            if not isinstance(obs, dict):
                continue
            at = obs.get("at")
            if not at:
                continue
            groups.setdefault((source, at), []).append(obs)

    # 位次按 (source, at) 快照整片算，所以先扫全池、再回填到每条观测上。
    # 用 id(obs) 作键：观测字典在本轮内存里唯一，且都是上面这批对象本身。
    positions: dict = {}
    latest_at: dict = {}
    for (source, at), obs_list in groups.items():
        for obs, pos in zip(obs_list, _snapshot_positions(obs_list)):
            positions[id(obs)] = pos
        if source not in latest_at or at > latest_at[source]:
            latest_at[source] = at

    with_trend = []
    by_state = {"climbing": 0, "steady": 0, "cooling": 0, "unknown": 0, "absent": 0}
    by_basis: dict = {}
    for it in items:
        trend = _item_trend(it, positions, latest_at)
        with_trend.append({**it, "trend": trend})
        by_state[trend["state"]] = by_state.get(trend["state"], 0) + 1
        basis = trend.get("position_source") or "none"
        by_basis[basis] = by_basis.get(basis, 0) + 1

    computable = by_state["climbing"] + by_state["steady"] + by_state["cooling"]
    if computable == 0:
        note = (
            f"本池没有任何条目能算出趋势（快照 {len(groups)} 个、可算条目 0 条）——"
            "这**不是**「大家都很稳」，是「还看不出」。排序请退回当前热度。"
        )
    else:
        note = (
            f"{computable}/{len(items)} 条可算趋势：climbing {by_state['climbing']}、"
            f"steady {by_state['steady']}、cooling {by_state['cooling']}；"
            f"absent {by_state['absent']}、unknown {by_state['unknown']} 不参与排序。"
        )
        # 位次口径也要说出来：靠 `hot` 重排得到的是**近似**位次，而 hot 与真实
        # rank 在同源内只是粗略相关。不写清这一点，模型会把这些 delta 当成和
        # 上游位次一样准。换尺日（观测刚开始带 rank）时整池都是 hot 口径。
        # 注意这里数的是**整池**的条目（含 unknown/absent），所以措辞不能接在
        # 「可算」后面当子集——否则读起来像「94 条里 110 条」。
        if by_basis.get("hot", 0):
            basis_parts = "、".join(f"{k} {v}" for k, v in sorted(by_basis.items()))
            note += (
                f" 位次口径（整池 {len(items)} 条）：{basis_parts}；"
                f"`hot` 口径是 rank 覆盖不足两条时的**近似**位次，其 delta 精度低于上游 rank。"
            )
    summary = {
        "computed_at": datetime.now(CST).isoformat(),
        "thresholds": {
            "climb_at_or_below": TREND_CLIMB_DELTA,
            "cool_at_or_above": TREND_COOL_DELTA,
            "min_observations": TREND_MIN_OBSERVATIONS,
        },
        "snapshots": len(groups),
        "latest_snapshot_by_source": latest_at,
        "by_state": by_state,
        "by_position_source": by_basis,
        "note": note,
        "usage": (
            "trend 参与排序、不参与打分（ADR-0056）：卡匹配度相当时，climbing 的聚簇"
            "优先于已经很高但 steady/cooling 的。它不设硬门槛，也不单独构成"
            "「值得写」的理由——没有新事实仍然跳过。"
        ),
    }
    return with_trend, summary


def build_triage_input(candidates: dict, cards: list[dict], cooldown: dict,
                       performance: dict | None = None) -> dict:
    """组装模型的输入包：候选条目 + 全部卡 + 冷却状态 + 限额 + 历史表现参考。"""
    trends, trend_summary = compute_trends(candidates.get("items", []))
    return {
        "prepared_at": datetime.now(CST).isoformat(),
        "instruction": (
            "对每条候选条目：先聚成事件，再判断接得住哪张卡，给出三态决策"
            "（skip / continue / new）。判据见 SKILL.md 的硬规则。"
            "候选条目上的 trend 是**同源位次变化**（ADR-0056），"
            "trend_summary 说明该信号的覆盖率——它退化时按当前热度排序。"
        ),
        "limits": {
            "max_articles_per_day": 1,
            "max_new_cards_per_day": 1,
            "cooldown_days": None,  # 由调用方填入
        },
        "cooldown_state": cooldown,
        "cards": cards,
        "candidates": trends,
        "trend_summary": trend_summary,
        "collection_counts": candidates.get("counts", {}),
        "stale_sources": candidates.get("stale_sources", []),
        "failed_sources": [f.get("source") for f in candidates.get("failures", [])],
        "performance_reference": performance or {"available": False, "note": "未读取"},
    }


def validate_decision(decision: dict, cards: list[dict], cooldown: dict, limits: dict) -> dict:
    """校验模型的三态决策。返回 (ok, problems)。

    这是确定性校验，不是判断替代：它只检查结构性合法性与已知的硬约束
    （冷却期、每日限额、卡是否存在）。语义正确性仍需人工在简报中复核。
    """
    problems = []
    action = decision.get("action")
    if action not in ("skip", "continue", "new", "abstain"):
        problems.append(f"action 非法：{action!r}")

    card_files = {c["file"] for c in cards}

    if action in ("skip", "continue"):
        cf = decision.get("card_file")
        if not cf:
            problems.append(f"{action} 必须给出 card_file")
        elif cf not in card_files:
            problems.append(f"card_file 不存在：{cf}")
        elif action == "continue" and cooldown.get(cf, {}).get("in_cooldown"):
            # ADR-0019：冷却中的卡不参与竞争 → 应由调用方顺位，而不是这里放行
            days = cooldown[cf].get("days_since")
            problems.append(f"卡在冷却期（{days} 天 < {limits.get('cooldown_days')} 天）：{cf}")

    if action == "continue":
        # ADR-0016：必须引入新事实或改变判断才算进展
        if not decision.get("whats_new"):
            problems.append("continue 必须说明 whats_new（新事实或判断变化）")

    if action == "new":
        if not decision.get("new_issue"):
            problems.append("new 必须给出 new_issue（新卡的议题名）")
        # ADR-0007：可复用性判据
        if not decision.get("reusable_because"):
            problems.append("new 必须说明 reusable_because（为何能接住未来的新闻）")
        # 2026-10-17 实测发现：new 原先不校验 card_file，而第 3 段的写回靠它取卡
        # （run_all.sh 的 `[[ -n "$CARD" ]]`），缺失时写回被**静默跳过**——
        # 新卡永远不会拿到 covered_events 与 last_written，认知资产停止生长且不报错。
        # 建卡是分拣层的动作，card_file 必须在决策里点名。
        cf = decision.get("card_file")
        if not cf:
            problems.append(
                "new 必须给出 card_file（你新建的卡的文件名）；"
                "缺失会让第 3 段的写回被静默跳过，新卡永远拿不到 covered_events"
            )
        elif cf not in card_files:
            problems.append(
                f"new 的 card_file 不存在：{cf}——建卡与决策是同一个动作，"
                f"必须先写 knowledge/cards/{cf} 再产出决策"
            )

    if decision.get("confidence") is None:
        problems.append("必须给出 confidence")
    else:
        try:
            c = float(decision["confidence"])
            if not 0.0 <= c <= 1.0:
                problems.append(f"confidence 越界：{c}")
        except (TypeError, ValueError):
            problems.append(f"confidence 非数值：{decision['confidence']!r}")

    # ── 被下游消费的展示字段（ADR-0058）──
    # ADR-0054 只校验了 `low_confidence_skipped` / `no_card_available` 两个列表，
    # 并留下一条「`abstain_reason`、`money_point`、`ranking_note` 同样被简报消费
    # 但本轮未校验」的边界。ADR-0058 落码时实测发现这条边界比原文更糟：
    # `money_point` **根本没有任何消费者**——分拣从 2026-10-21 起每天写 300+ 字，
    # 却既不进写作 brief 也不进简报（已补上消费方）。校验必须与消费方同时建立，
    # 否则「消费者读到空值」与「生产者没写」外部不可区分。
    #
    # 这里只校验「该有内容时不能为空」，不校验内容质量——钱落点写得好不好，
    # 是残余风险 40 里那类机器验不了的东西，只能靠人读。
    action = decision.get("action")
    if action in ("continue", "new"):
        money_point = decision.get("money_point")
        if not isinstance(money_point, str) or not money_point.strip():
            problems.append(
                "continue/new 必须给出 money_point（这件事跟读者的钱或工作处境有什么关系，"
                "具体到角色）。它会被写进写作 brief 作为硬约束，并在简报③段呈现；"
                "缺失时写作阶段会自己重新猜一套角色划分，与分拣判断不一致"
            )

    if action == "abstain":
        reason = decision.get("abstain_reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(
                "abstain 必须给出 abstain_reason（为什么今天不写）。"
                "弃权是合法结果，但简报①段要把它写出来，否则看守无法区分"
                "「今日无选题」与「系统没跑」"
            )

    return {"ok": not problems, "problems": problems}


def main() -> int:
    ap = argparse.ArgumentParser(description="NewsAgg 分拣层：读卡 → 组包 → 校验决策")
    ap.add_argument("--candidates", default=None, help="候选文件；默认取 .newsagg/candidates/ 最新")
    ap.add_argument("--out", default=None, help="triage 输入包输出路径")
    ap.add_argument("--cooldown-days", type=float, default=3.0, help="冷却期天数（ADR-0014）")
    ap.add_argument("--validate", default=None, help="校验一个决策 JSON 文件")
    args = ap.parse_args()

    root = repo_root()
    cand_path = Path(args.candidates) if args.candidates else None
    if cand_path is None:
        cands = sorted((root / ".newsagg" / "candidates").glob("*.json"))
        if not cands:
            print("错误：找不到候选文件，请先跑采集层", file=sys.stderr)
            return 1
        cand_path = cands[-1]

    candidates = load_candidates(cand_path)
    cards = load_cards(root / "knowledge" / "cards")
    if not cards:
        print("警告：没有 active 观点卡；分拣只能产出弃权或新建", file=sys.stderr)

    now = datetime.now(CST)
    cooldown = cooldown_state(cards, args.cooldown_days, now)

    limits = {
        "max_articles_per_day": 1,
        "max_new_cards_per_day": 1,
        "cooldown_days": args.cooldown_days,
    }

    # 校验模式：只校验，不重写输入包
    if args.validate:
        raw = json.loads(Path(args.validate).read_text(encoding="utf-8"))
        # SKILL.md 记录的输出形状是 {date, decision:{...}, low_confidence_skipped,
        # no_card_available, abstain_reason}；也接受扁平的 decision 对象，
        # 以免调用方必须包一层。
        decision = raw.get("decision", raw)
        result = validate_decision(decision, cards, cooldown, limits)
        # 附带简报相关的结构检查（不计入 ok，但会在简报中用到）
        extras = []
        for key in ("low_confidence_skipped", "no_card_available"):
            if key in raw and not isinstance(raw[key], list):
                extras.append(f"{key} 应为数组")
            elif isinstance(raw.get(key), list):
                # 2026-10-22 实测发现：原先只校验「是数组」，不校验**数组项的字段名**。
                # 那天我写成 {"cluster": ..., "reason": ...}，校验器一路放行，
                # 而 brief.py 读的是 it["event"] / it["why"] → 简报里渲染成
                # 「- （0.78）」五个空条目，原因全部丢失，且退出码仍是 0。
                # 与 ADR-0042 的三份 parse_frontmatter 漂移同型：字段名是隐式契约，
                # 消费方读不到时不会报错，只会安静地少显示一行。
                for i, it in enumerate(raw[key]):
                    if not isinstance(it, dict):
                        extras.append(f"{key}[{i}] 应为对象")
                        continue
                    for field in ("event", "why"):
                        if not str(it.get(field) or "").strip():
                            extras.append(
                                f"{key}[{i}] 缺 {field} 字段"
                                f"（简报靠它渲染，缺了会变成空条目；现有键：{sorted(it)}）"
                            )
        if extras:
            result["problems"].extend(extras)
            result["ok"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    pkg = build_triage_input(
        candidates, cards, cooldown,
        performance=performance_reference(
            root / "reports" / "performance.jsonl",
            root / ".newsagg" / "triage",
        ),
    )
    pkg["limits"] = limits

    out_path = Path(args.out) if args.out else root / ".newsagg" / "triage" / f"{now:%Y-%m-%d}-input.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "out": str(out_path),
        "candidates_file": str(cand_path),
        "cards_loaded": len(cards),
        "cards_in_cooldown": [f for f, s in cooldown.items() if s.get("in_cooldown")],
        "candidate_count": len(pkg["candidates"]),
        # 趋势信号是否可用要出现在**人看得见的地方**：编排器把这段打印进日志，
        # 若整池都是 unknown（今天只采了一次），看日志的人立刻知道这一轮排序
        # 退回当前热度了，而不是以为「分拣考虑了爬升」（ADR-0056）。
        "trend": {
            "by_state": pkg["trend_summary"]["by_state"],
            "by_position_source": pkg["trend_summary"]["by_position_source"],
            "snapshots": pkg["trend_summary"]["snapshots"],
            "note": pkg["trend_summary"]["note"],
        },
        "limits": limits,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
