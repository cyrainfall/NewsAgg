#!/usr/bin/env python3
"""议题台账：把「同一议题第几次出现」变成一条可读的事实（ADR-0083）。

设计依据：
- ADR-0007：观点卡是一条**可复用**的判断，能接住未来的新闻
- ADR-0025：技能装判断、插件装强制。本模块只做计数与查表，不做判断
- ADR-0042：同一件事只留一份实现。议题归一化与台账读写只在这里
- ADR-0083：新建门槛从「预测可复用性」改成「事后计数 + 事后回检」

## 为什么需要这个文件

2026-10-14 到 10-28（13 轮分拣）产出 0 张新卡，而同期有 5 个「无卡可接」的
事件簇。原因不是门槛太高，是**「第二次出现」这个事实不存在于任何地方**：
每一轮分拣都重新全量读卡，模型看不到「上一轮有人说过某议题该建卡」，
只能每轮凭记忆重新推出同一个结论。事实被丢掉了，于是它无法累积。

于是这里把那个事实落成文件：每个「无卡可接」的议题记一行，`times_seen` 由
**脚本**数（不是模型报），`days` 去重（同一天跑两轮仍算出现一次）。

## 计数为什么是 2 而不是 1

1 等于每轮都可能建卡，等于没有门槛。2 有物理意义：**第二次出现证明它不是孤例**。
这个数字是按结构推出来的，不是实测的——所以 ADR-0083 要求先记账 30 天，
用攒下来的真实分布再定；改动阈值只需改本文件的 `TIMES_SEEN_THRESHOLD`。

## 与校验器的分工

本模块只回答「哪些议题已经到期」。`triage.py::validate_decision` 拿这份名单
做硬校验：到期议题**要么**这一轮建成卡，**要么**写下 `no_card_reason`。
两个动作都留痕，偏置才会消失——在此之前只有「建」有成本，「不建」零成本，
所以系统必然不建。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date as _date, datetime
from pathlib import Path

# 同一议题第几次出现才够格要求建卡（ADR-0083）。改这个数字要先看台账攒出来的分布。
TIMES_SEEN_THRESHOLD = 2

# 建成后多少天内一次都没被 continue，就进退役候选（ADR-0083 的回检段）。
# 这是「可复用性」这个词唯一能被验证的地方：不看建成前能否接住未来，
# 看建成后到底接住没有。
REVIEW_DAYS = 30

BACKLOG_VERSION = 1

# 归一化时剥掉的字符：中英文标点、空白、引号括号。只影响**键**，不影响展示。
# 「」『』〈〉 这几个必须在内：# 议题名带书名号/引号是常见写法（「AI 顶尖人才的定价」），
# 漏掉它们会让同一个议题被记成两条台账——而计数正是整套判据的唯一输入。
_PUNCT = re.compile(
    r"[\s,，。、;；:：!！?？\"'“”‘’()（）\[\]【】<>《》〈〉「」『』/\\|·—\-_…~`@#$%^&*+=]+")


def norm_issue(text: str) -> str:
    """议题名的归一化键。

    同一议题在两轮的措辞不可能逐字相同（「AI 顶尖人才的定价」/
    「AI 人才的定价正在分层」），所以这里只做去除标点与空白、
    NFKC 折半角、英文小写——**不做语义合并**。语义合并需要判断，
    而判断属于模型（ADR-0025）；模型改措辞时应当保持议题名稳定，
    SKILL.md 里对此有要求。

    这也意味着：措辞变了就是另一条台账。这是有意的取舍——宁可少算一次
    （漏计数只会推迟建卡），不要多算一次（错合并会让不相干的议题共享计数，
    进而逼出一张错误的新卡，而错误的新卡会被后续所有分拣反复引用）。
    """
    s = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    return _PUNCT.sub("", s)


def empty_backlog() -> dict:
    # `threshold` 进台账而不是只留在模块常量里：判据（`due_issues`）与所有
    # 面向人的文案（校验器报错、简报回声）都从**同一个数字**读。否则改常量
    # 那天会出现「判据已经变 3、提示还写 2」——判据与解释分家比没有解释更坏。
    return {"version": BACKLOG_VERSION, "issues": {}, "updated_at": None,
            "threshold": TIMES_SEEN_THRESHOLD}


def load(path: Path) -> dict:
    """读台账。文件不存在或损坏时返回空台账——台账不该阻塞分拣。"""
    p = Path(path)
    if not p.exists():
        return empty_backlog()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_backlog()
    if not isinstance(data, dict) or not isinstance(data.get("issues"), dict):
        return empty_backlog()
    # 老台账（ADR-0083 之前建的，或手写的）没有 threshold，补上当前默认值。
    if not isinstance(data.get("threshold"), int):
        data["threshold"] = TIMES_SEEN_THRESHOLD
    return data


def save(path: Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = BACKLOG_VERSION
    data["updated_at"] = datetime.now().astimezone().isoformat()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entry(issue: str, day: str) -> dict:
    return {
        "issue": issue,
        "times_seen": 0,
        "first_seen": day,
        "last_seen": day,
        "days": [],
        # 这个议题**已经见过的报道**（URL 集合）。计数键从「议题 × 天」扩成
        # 「议题 × 报道」：同一篇文章在热榜上挂两天，不该被数成「第二次出现」。
        # 2026-10-31 实测的正是这个形状——10-29 那两条议题，10-30 候选池里
        # 出现的仍是**同一 URL 的原报道**，按天计数它们就该被逼着建卡了。
        "urls_seen": [],
        "card_file": None,
        "reasons": {},
        "events": [],
    }


def _norm_urls(urls) -> list[str]:
    """把决策里的 urls 收成去重、有序、只留 http(s) 的列表。"""
    out: list[str] = []
    for u in urls or []:
        s = str(u or "").strip()
        if not s.lower().startswith(("http://", "https://")):
            continue
        if s not in out:
            out.append(s)
    return out


def _touch(entry: dict, day: str, event: str = "", urls=None) -> bool:
    """记一次出现。返回 True 表示**这一次计入 `times_seen`**。

    两重去重，缺一不可：

    1. **按天**（原有）：一天里主流程可能跑多次（白天 3 次采集 + 21:00 主流程），
       同一条新闻每轮都会重新出现在候选池里；按轮计数会让「出现 2 次」只需半天。
    2. **按报道**（2026-10-31 加）：同一篇文章在热榜上挂两三天，**每天都会再被
       分拣看见一次**。按天计数会把「一篇挂了三天」读成「三次出现」，逼着建一张
       没有新证据支撑的卡——而阈值 2 的全部意义是「不是孤例」。

    所以判据是：**只要这一次带来了没见过的 URL，才算新的一次出现**；URL 都见过，
    就只更新 `last_seen` 与事件列表，不计数。没有给 `urls` 的（老决策、老条目）
    退回按天计数，保持历史语义不变。
    """
    days = entry.setdefault("days", [])
    seen = entry.setdefault("urls_seen", [])
    norm = _norm_urls(urls)
    fresh = [u for u in norm if u not in set(seen)]
    if fresh:
        seen.extend(fresh)  # 证据集无论计不计数都要更新

    if norm:
        counted = bool(fresh) and day not in days
    else:
        # 没有可用 URL（老决策，或 urls 里全是空值/非 http）：退回按天计数。
        # 注意判据用的是**归一化之后**的列表——不能因为调用方填了个空数组就
        # 静默把这一次丢掉，那会让计数悄悄变少，而台账的全部价值在于可信。
        counted = day not in days

    if not counted:
        entry["last_seen"] = day
        if event:
            _append_event(entry, day, event, counted=False)
        return False

    days.append(day)
    entry["times_seen"] = int(entry.get("times_seen") or 0) + 1
    entry["first_seen"] = entry.get("first_seen") or day
    entry["last_seen"] = day
    if event:
        _append_event(entry, day, event, counted=True)
    return True


def _append_event(entry: dict, day: str, event: str, *, counted: bool) -> None:
    evs = entry.setdefault("events", [])
    evs.append({"date": day, "event": str(event)[:200], "counted": counted})
    del evs[:-10]  # 只留最近 10 条：台账是计数器，不是档案


def card_issue_index(cards: list[dict]) -> dict:
    """归一化议题名 → 卡文件名。用于「议题其实已经有卡」的自愈。"""
    out = {}
    for c in cards or []:
        cf = c.get("file")
        if not cf:
            continue
        for key_src in (c.get("issue"), Path(str(cf)).stem):
            k = norm_issue(key_src or "")
            if k:
                out.setdefault(k, cf)
    return out


def observe(data: dict, decision: dict, cards: list[dict], day: str) -> dict:
    """把一份决策并进台账。返回做了什么（供编排器打印）。

    三件事：
    1. `action: new` → 把新卡挂到对应议题上，此后不再到期
    2. `no_card_available[]` → 每个条目计数一次（按天去重）
    3. `no_card_reason` → 记下「这一轮为什么没建」，随台账一起复盘

    只有决策里的**显式字段**会被读。不从 `ranking_note` 这类散文里猜议题——
    猜错一次就会让一个不相干议题的计数多 1，而台账的全部价值在于可信。
    """
    import sys as _sys

    issues = data.setdefault("issues", {})
    idx = card_issue_index(cards)
    raw = decision.get("decision", decision) or {}
    touched = {"attached": [], "bumped": [], "reasons": []}

    def _get(issue_text: str) -> dict | None:
        key = norm_issue(issue_text)
        if not key:
            return None
        if key not in issues:
            issues[key] = _entry(str(issue_text).strip(), day)
        return issues[key]

    # ── 1) 本轮建了卡 ──
    if raw.get("action") == "new":
        e = _get(raw.get("new_issue") or "")
        if e is not None:
            e["card_file"] = raw.get("card_file")
            _touch(e, day, (raw.get("event") or {}).get("summary", ""))
            touched["attached"].append(e["issue"])
        else:
            print("警告：action=new 但没有 new_issue，台账无法挂卡（校验器应已拦下）",
                  file=_sys.stderr)

    # ── 2) 无卡可接：这就是「出现一次」的正式计数点 ──
    for it in decision.get("no_card_available") or []:
        if not isinstance(it, dict):
            continue
        e = _get(it.get("issue") or "")
        if e is None:
            # 没有 issue 的条目无法计数。校验器要求每个条目都带 issue，
            # 这里再兜一次底，免得历史决策让编排器崩掉。
            print(f"警告：no_card_available 条目缺 issue，跳过计数："
                  f"{str(it.get('event', ''))[:60]}", file=_sys.stderr)
            continue
        if e.get("card_file") is None:
            # 议题其实已经有卡（措辞不同但归一化后相同）→ 自愈，不累计
            hit = idx.get(norm_issue(e["issue"]))
            if hit:
                e["card_file"] = hit
                continue
        if _touch(e, day, it.get("event", ""), it.get("urls")):
            touched["bumped"].append(e["issue"])

    # ── 3) 到期了但这一轮决定不建：理由必须留下 ──
    reason = raw.get("no_card_reason")
    if reason:
        pairs = []
        if isinstance(reason, dict):
            pairs = [(k, v) for k, v in reason.items()]
        elif isinstance(reason, str):
            # 一个字符串统管所有到期议题（最常见的情形）
            pairs = [(e["issue"], reason) for e in due_issues(data, cards)]
        for issue_text, why in pairs:
            e = _get(issue_text)
            if e is None:
                continue
            e.setdefault("reasons", {})[day] = str(why)
            touched["reasons"].append(e["issue"])

    return touched


def due_issues(data: dict, cards: list[dict],
               threshold: int | None = None) -> list[dict]:
    """到期议题：出现次数已达阈值、且**至今没有卡**。

    返回按出现次数降序（同次数按首次出现升序）。每项含
    `issue` / `times_seen` / `first_seen` / `last_seen` / `last_reason`。

    三种不算到期：
    - 已有卡文件存在 → 议题已被接住，台账的使命结束（之后归退役回检管）
    - 台账记了卡名但文件**不存在** → 算到期。卡被人删掉了，等于议题又裸了
    - 归一化后撞上现有卡的议题 → 算已被接住（`observe` 会顺手自愈挂上卡名）
    """
    if threshold is None:
        threshold = int((data or {}).get("threshold") or TIMES_SEEN_THRESHOLD)
    have = {c.get("file"): c for c in (cards or [])}
    idx = card_issue_index(cards)
    out = []
    for key, e in (data.get("issues") or {}).items():
        if not isinstance(e, dict):
            continue
        cf = e.get("card_file")
        if cf and cf in have:
            continue
        if not cf and key in idx:
            continue
        seen = int(e.get("times_seen") or 0)
        if seen < threshold:
            continue
        reasons = e.get("reasons") or {}
        out.append({
            "key": key,
            "issue": e.get("issue") or key,
            "times_seen": seen,
            "first_seen": e.get("first_seen"),
            "last_seen": e.get("last_seen"),
            "last_reason": reasons.get(e.get("last_seen")) if isinstance(reasons, dict) else None,
        })
    out.sort(key=lambda x: (-x["times_seen"], str(x["first_seen"] or "")))
    return out


def fmt_due(due: list[dict]) -> str:
    """一行式摘要，供日志与简报使用。"""
    if not due:
        return "无到期议题"
    return "、".join(f"{d['issue']}（第 {d['times_seen']} 次，首见 {d['first_seen']}）"
                    for d in due)


def _days_between(a: str, b: str) -> int | None:
    try:
        return (_date.fromisoformat(str(b)[:10]) - _date.fromisoformat(str(a)[:10])).days
    except (TypeError, ValueError):
        return None


def retire_candidates(cards: list[dict], today: str,
                      review_days: int = REVIEW_DAYS) -> list[dict]:
    """退役候选：建成满 `review_days` 天，却一次都没被 continue 过（ADR-0083）。

    判据只用 `created` 与 `last_written` 两个字段，都是卡自己写的、可核对：
    `last_written` 为空的卡从未成文——它连一次「接住新闻」都没兑现。

    注意这是**候选**，不是自动退役。ADR-0007 要求退役写入简报供人否决，
    ADR-0083 保留那一条：机器只能提出，不能执行。
    """
    out = []
    for c in cards or []:
        created = str(c.get("created") or "")[:10]
        if not created:
            continue
        age = _days_between(created, today)
        if age is None or age < review_days:
            continue
        if c.get("last_written"):
            continue
        out.append({"file": c.get("file"), "issue": c.get("issue"),
                    "created": created, "age_days": age})
    out.sort(key=lambda x: -x["age_days"])
    return out
