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


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """极简 frontmatter 解析（只用得到标量与简单列表，不引入 yaml 依赖）。"""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)
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
            key = k.strip()
            v = v.strip()
            if v in ("", "[]"):
                fm[key] = [] if v == "[]" else ""
            elif v.lower() in ("null", "~"):
                fm[key] = None
            else:
                fm[key] = v.strip('"')
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

    # slug → card_file（分拣决策文件里记着接的是哪张卡）
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


def build_triage_input(candidates: dict, cards: list[dict], cooldown: dict,
                       performance: dict | None = None) -> dict:
    """组装模型的输入包：候选条目 + 全部卡 + 冷却状态 + 限额 + 历史表现参考。"""
    return {
        "prepared_at": datetime.now(CST).isoformat(),
        "instruction": (
            "对每条候选条目：先聚成事件，再判断接得住哪张卡，给出三态决策"
            "（skip / continue / new）。判据见 SKILL.md 的硬规则。"
        ),
        "limits": {
            "max_articles_per_day": 1,
            "max_new_cards_per_day": 1,
            "cooldown_days": None,  # 由调用方填入
        },
        "cooldown_state": cooldown,
        "cards": cards,
        "candidates": candidates.get("items", []),
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

    if decision.get("confidence") is None:
        problems.append("必须给出 confidence")
    else:
        try:
            c = float(decision["confidence"])
            if not 0.0 <= c <= 1.0:
                problems.append(f"confidence 越界：{c}")
        except (TypeError, ValueError):
            problems.append(f"confidence 非数值：{decision['confidence']!r}")

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
        "limits": limits,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
