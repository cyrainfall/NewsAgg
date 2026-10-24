#!/usr/bin/env python3
"""采集层：拉取热榜 → 归一化 → 赛道预筛。

设计依据：
- ADR-0016 采集与分拣管道形状（赛道预筛在聚簇之前）
- ADR-0029 源清单与归一化（两种返回形状必须归一）
- ADR-0023 内存约束（本脚本不启动浏览器，纯 HTTP + 文本处理）

只做确定性工作：拉取、归一化、关键词预筛、落盘。不做任何判断（那是分拣层的事）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

DEFAULT_BASE = "http://127.0.0.1:6688"

# ADR-0029：实测通过的六个赛道相关源 + 头条主榜
DEFAULT_SOURCES = ["toutiao", "36kr", "juejin", "csdn", "ithome", "sspai", "github"]

# 原始热榜接口（ADR-0029 的兜底路径）。形状与 DailyHotApi 不同，需归一化。
RAW_ENDPOINTS = {
    "toutiao": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
}

# 各源在 DailyHotApi 里的缓存 TTL（分钟）。默认 60 分钟来自 `src/config.ts:48`
# 的 `CACHE_TTL: getNumericEnvVariable("CACHE_TTL", 3600)`（秒）；github 是唯一
# 显式覆盖 TTL 的源，见 `src/routes/github.ts:93` 的 `ttl: 60 * 60 * 24`。
#
# 为什么需要这张表：陈旧阈值若对全部源用同一个数字，就等于拿一个**统一的时长**
# 去量**刷新周期本来就不同的源**。github 每 24 小时才刷新一次，用 3 小时判它陈旧
# 会让它每个自然日有约 21 小时处于"被判陈旧"状态——每日必然误报，而每日必然
# 触发的告警会被忽略，这正是 ADR-0006/0021/0031 反复要避免的"狼来了"。
# 实测：2026-10-17 与 2026-10-18 两次采集都把 github 列为陈旧源，而它的
# update_time 两次都是 2026-10-17T05:21:31Z（同一次刷新，只是年龄更大了）。
SOURCE_TTL_MINUTES = {
    "github": 60.0 * 24,  # 1440 分钟
}
DEFAULT_SOURCE_TTL_MINUTES = 60.0  # DailyHotApi 默认 CACHE_TTL=3600s

# 陈旧阈值 = max(配置阈值, 源自身 TTL × STALE_TTL_FACTOR)。
#
# 为什么是乘一个系数而不是加「最大采集间隔」：在一次**采集时刻**能观察到的
# 合法最大年龄就是 TTL 本身，不需要加采集间隔——
#   - 请求落在缓存有效期内 → 返回缓存，age ≤ TTL；
#   - 请求落在缓存过期之后 → 触发重新填充 → age ≈ 0。
# 也就是说**过期后的那次请求会把数据刷新掉，年龄不会累积到 TTL 之上**。
# （曾经想过 `TTL + 最大采集间隔`，那是把"缓存过期"与"没被重取"混为一谈，
#  会把短周期源一起放宽：toutiao 会从 3h 变 15h，那是回归。）
#
# 实测印证（github，TTL 24h）：2026-10-17 13:21 CST 填充 → 18:00 时 age=4.7h
# → 2026-10-18 08:11 时 age=18.8h（仍在有效期内）→ 次日 13:00 过期并刷新 → age≈0。
# 观测最大值 18.8h < TTL 24h ✓。
#
# 系数 > 1 只留边界余量：年龄正好等于 TTL 时是**边界**，定时抖动会让它反复在
# 陈旧/不陈旧之间翻转，于是告警变成噪音。1.5 意味着"超出刷新周期一半才算陈旧"。
# 这是经验值，非推导值（与 ADR-0016/0031/0037 的阈值同策略：先记下、后实测）。
STALE_TTL_FACTOR = 1.5

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)


def repo_root() -> Path:
    """从脚本位置回溯到仓库根（.agents/skills/newsagg-collect/scripts/ → 根）。"""
    return Path(__file__).resolve().parents[4]


def http_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def normalize_hot(value) -> int | None:
    """把上游各源形态不一的 `hot` 统一成 int 或 None。

    实测各源的原始形态（2026-10-18，逐一打 DailyHotApi 得到）：

    | 源 | 形态 |
    | --- | --- |
    | toutiao / 36kr / juejin / csdn / ithome / sspai | `int` |
    | github | `str`，且带千分位：`'40,798'` |
    | 36kr（个别条目） | `None`（字段缺失） |

    统一成数字的理由不是"整洁"，而是**类型不一致会让排序静默出错**：
    `sorted(items, key=lambda x: x["hot"])` 在 str 与 int 混排时抛
    `TypeError: '<' not supported between instances of 'str' and 'int'`；
    更隐蔽的是，若全部当字符串比较，字典序会把 `'9,000' > '51,667'` 判反
    （'9' > '5'）。

    当前没有任何脚本按 `hot` 排序（「卡匹配度 × 事件热度」由模型判断），
    所以这个坑还没绊到人——但残余风险 16 记的「热度轨迹无消费者」一旦接上，
    它就是第一个会踩的雷。在**入口处**归一，比在每个消费者里各写一遍容错便宜。

    返回 None 而不是 0 的理由：`None` 表示"这个源不提供热度"，`0` 会被读成
    "热度为零"。两者在下游的排序语义完全不同，不能混为一谈。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def normalize_rank_total(value) -> int | None:
    """把上游榜单深度（payload 的 `total`）读成 int；读不出就 None。

    为什么深度必须跟着 rank 一起存下来：分拣判「正在爬升」用的是位次变化
    （ADR-0056），而位次必须归一成一个**稳定刻度**才能跨快照相减。第一版用
    「池内名次」（`(pos-1)/(n-1)`），撞上了赛道预筛这个坑——池子只是榜单的
    子集，每个快照的池内条数会变（实测 toutiao 7→5），池子一缩水同一名次就被
    拉伸成更高的位次，凭空造出爬升：toutiao「辽宁舰编队」实测 rank 22→25
    （**退步**）却被判 climbing，只因池子从 7 条缩到 5 条。

    榜单深度是上游给的、不随我们的预筛变化，`(rank-1)/(total-1)` 才是稳定刻度。
    读不出 total 时宁可退回 `hot` 近似（消费者按条目降级），也不拿池内名次冒充
    位次——那是**看起来更准、实际更错**的读数。
    """
    return normalize_hot(value)


def normalize_dailyhot(source: str, payload: dict) -> tuple[list[dict], dict]:
    """归一化 DailyHotApi 形状：{id,title,cover,hot,url}。"""
    items = []
    for rank, raw in enumerate(payload.get("data") or [], start=1):
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "source": source,
                "id": str(raw.get("id") or ""),
                "title": title,
                "hot": normalize_hot(raw.get("hot") if raw.get("hot") is not None else raw.get("hotValue")),
                "url": raw.get("url") or raw.get("mobileUrl") or "",
                "cover": raw.get("cover") or "",
                "rank": rank,
                "rank_total": normalize_rank_total(payload.get("total")),
                "shape": "dailyhot",
            }
        )
    meta = {
        "source": source,
        "shape": "dailyhot",
        "total": payload.get("total"),
        "from_cache": payload.get("fromCache"),
        "update_time": payload.get("updateTime"),
    }
    return items, meta


def normalize_raw_toutiao(source: str, payload: dict) -> tuple[list[dict], dict]:
    """归一化头条原始接口形状：{ClusterId,Title,Url}（ADR-0029 兜底路径）。"""
    items = []
    for rank, raw in enumerate(payload.get("data") or [], start=1):
        title = (raw.get("Title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "source": source,
                "id": str(raw.get("ClusterIdStr") or raw.get("ClusterId") or ""),
                "title": title,
                "hot": normalize_hot(raw.get("HotValue")),
                "url": raw.get("Url") or "",
                "cover": (raw.get("Image") or {}).get("url") if isinstance(raw.get("Image"), dict) else "",
                "rank": rank,
                "rank_total": len(payload.get("data") or []),
                "shape": "raw_toutiao",
            }
        )
    return items, {"source": source, "shape": "raw_toutiao", "total": len(items)}


def load_niche_keywords() -> list[str]:
    """从 platforms/niche.txt 读取赛道词；缺失则用内置默认。"""
    path = repo_root() / "platforms" / "niche.txt"
    if path.exists():
        words = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
        if words:
            return words
    # 内置默认：AI 变现与开发者职业（2026-10-21 改赛道，ADR-0050）
    # 这是 platforms/niche.txt 缺失时的兜底，不是主路径——主路径永远读该文件。
    # 因此这里只需保证「兜底时不会把赛道筛没」，不必与 niche.txt 逐词同步。
    return [
        "AI", "人工智能", "大模型", "LLM", "GPT", "Claude", "Gemini", "DeepSeek", "Qwen",
        "智能体", "Agent", "机器学习", "深度学习", "神经网络", "训练", "推理", "算力",
        "芯片", "GPU", "英伟达", "NVIDIA", "CUDA", "编程", "代码", "开发", "开发者",
        "开源", "GitHub", "前端", "后端", "全栈", "架构", "数据库", "API", "SDK",
        "Python", "JavaScript", "TypeScript", "Rust", "Go", "Java", "框架", "编译器",
        "云", "DevOps", "容器", "Kubernetes", "Docker", "服务器", "运维", "部署",
        "模型", "算法", "数据", "自动化", "工具", "IDE", "编辑器", "Copilot", "Cursor",
        "插件", "版本", "发布", "开源项目", "技术", "程序员", "工程师",
        # 个人变现与职业处境（ADR-0050）。注意不收「收入/涨价/降价/免费/成本/
        # 盈利/薪资/IP/钱」——实测这些在热榜里几乎全是消费电子折扣与财报。
        "副业", "赚钱", "变现", "知识付费", "独立开发", "自由职业", "接单", "月入",
        "客单价", "付费", "私域", "涨粉", "流量主", "广告分成", "带货", "商单",
        "SaaS", "小程序", "出海", "失业", "工资", "外包", "远程办公", "跳槽", "降薪",
    ]


def niche_prescreen(items: list[dict], keywords: list[str]) -> tuple[list[dict], list[dict]]:
    """赛道预筛：命中关键词的进入 kept，其余进入 dropped（保留供简报观察漏筛）。"""
    # 大小写不敏感匹配；英文词做词边界处理以免 'go' 命中 'google' 之类
    patterns = []
    for kw in keywords:
        if re.fullmatch(r"[A-Za-z0-9+#.]+", kw):
            patterns.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z0-9])", re.I))
        else:
            patterns.append(re.compile(re.escape(kw), re.I))

    kept, dropped = [], []
    for it in items:
        title = it["title"]
        hits = [kw for kw, pat in zip(keywords, patterns) if pat.search(title)]
        if hits:
            it = {**it, "niche_hits": hits[:5]}
            kept.append(it)
        else:
            dropped.append(it)
    return kept, dropped


def collect(sources: list[str], base: str, timeout: int) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (all_items, source_meta, failures)。单源失败不影响其余源。"""
    all_items, meta, failures = [], [], []
    for source in sources:
        try:
            payload = http_json(f"{base}/{source}", timeout=timeout)
            if payload.get("code") not in (200, None):
                raise ValueError(f"code={payload.get('code')}")
            items, m = normalize_dailyhot(source, payload)
            all_items.extend(items)
            meta.append({**m, "ok": True})
        except Exception as exc:  # noqa: BLE001 — 单源失败需隔离
            failures.append({"source": source, "backend": "dailyhot", "error": str(exc)[:200]})
            # 兜底：尝试原始接口（仅头条有）
            raw_url = RAW_ENDPOINTS.get(source)
            if raw_url:
                try:
                    payload = http_json(raw_url, timeout=timeout)
                    items, m = normalize_raw_toutiao(source, payload)
                    all_items.extend(items)
                    meta.append({**m, "ok": True, "fallback": True})
                    failures[-1]["recovered_by"] = "raw_endpoint"
                except Exception as exc2:  # noqa: BLE001
                    failures[-1]["fallback_error"] = str(exc2)[:200]
    return all_items, meta, failures


def source_age_minutes(meta: dict, now: datetime) -> float | None:
    """由 updateTime 计算数据新鲜度（分钟）。

    注意：fromCache 表示「由缓存提供」，是正常且对风控有利的行为，
    不能当作陈旧信号——两者的区别见 ADR-0031。
    """
    ts = meta.get("update_time")
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - parsed).total_seconds() / 60.0


def source_stale_threshold(source: str, configured: float) -> float:
    """该源的陈旧阈值 = max(配置阈值, 源自身 TTL × STALE_TTL_FACTOR)。

    ADR-0031 定下「宁可漏报陈旧，也不误报」，但当时的阈值是**全局**的：
    所有源共用 `--stale-minutes`。这对刷新周期短于该阈值的源是对的，对
    github 这种 TTL 为 24 小时的源则**每天必然误报**（见 SOURCE_TTL_MINUTES
    与 STALE_TTL_FACTOR 的注释）。

    用 `max` 而非直接取 TTL 的倍数，是为了让 `--stale-minutes` 仍能**放宽**判定
    （例如某源已知在维护，想让告警晚点响）；但它**不能收紧到低于该源的刷新周期**
    ——那正是本 ADR 要消除的误报。实测语义：github（下限 2160 分钟）传 60/180/600/
    1440 阈值一律 2160，传 3000 才变 3000；toutiao（下限 90）传 60 被托到 90，
    传 180/600 则生效。也就是说**传得比下限小没有效果，传得比下限大才生效**。

    调用方传 `configured` 而非在函数内读全局，是为了让单元测试能直接构造
    场景，不必改环境或参数解析。
    """
    ttl = SOURCE_TTL_MINUTES.get(source, DEFAULT_SOURCE_TTL_MINUTES)
    return max(configured, ttl * STALE_TTL_FACTOR)


def normalize_hot_history(history) -> list[dict]:
    """把历史观测里的 `hot` 也归一化。

    为什么需要这一遍：`normalize_hot` 只在**写入新观测**时生效，而
    `hot_history` 里可能存着归一化之前落盘的旧值——实测 2026-10-18 的池子里
    github 条目带着 `['40,798', 40798]` 这种"新旧混杂"的轨迹。只修入口不修
    存量，等于让同一个字段在一个文件里同时有两种类型。

    这是 ADR-0042 的同一形状：**修复只做在生产者一侧时，消费者仍会读到旧
    格式的数据**。那次是 `kept_new` 的 `None` 在简报里印成 `?`；这次是旧
    `hot` 字符串留在轨迹里。两者都指向同一条纪律——修数据格式时，必须同时
    处理"已经在盘上的数据"，否则缺陷以"历史条目"的形态存活下来。

    保留原始值到 `hot_raw` 的理由：归一化可能**改变**数值（`'40,798'` 去掉
    逗号是同一个数，但若上游哪天给出 `'1.2万'` 这类形式，归一化就是一次有
    损转换）。留下原文，将来出现归一化争议时能回溯。

    ⚠️ 只保留 `at`/`hot`/`hot_raw` 是错的：本函数是**重建**字典，凡是它不认识
    的键都会被静默抹掉。2026-10-24 给观测加 `rank`（ADR-0056：分拣要用同源
    同快照内的**位次变化**判「正在爬升」）时，这里就是第一个会吞掉新字段的
    地方——写进去的 rank 会在下一次合并时消失，而且不会有任何报错。所以改成
    保留所有未知键，只覆盖本函数负责归一化的 `hot`/`hot_raw`。
    """
    out = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        raw = h.get("hot")
        norm = normalize_hot(raw)
        entry = {**h, "at": h.get("at"), "hot": norm}
        if norm != raw and raw is not None:
            entry["hot_raw"] = raw
        out.append(entry)
    return out


def hot_observation(at: str, item: dict) -> dict:
    """造一条热度轨迹观测。

    `rank` 必须进观测：分拣判「正在爬升」用的是**位次变化**（ADR-0056），而位次
    只能来自这里。条目的当前 `rank` 会被下一次采集覆盖，只有观测里的那一份留得住。
    存量观测没有 rank，消费者按「可算观测数」降级——那属于历史，新观测从一开始就带。

    `rank_total`（上游榜单深度）必须跟着 `rank` 一起写：位次要归一成稳定刻度才能
    跨快照相减，而分母若取「池内条数」就会随赛道预筛的池子大小漂移（见
    `normalize_rank_total` 的说明）。两个字段要么都有、要么都没有——只有 rank 的
    观测会被消费者整条退回 hot 近似，不会拿池内名次凑数。
    """
    obs = {"at": at, "hot": item.get("hot")}
    rank = item.get("rank")
    if rank is not None:
        obs["rank"] = rank
        # 深度必须和 rank 一起进观测：位次要归一成稳定刻度才能跨快照相减，
        # 而「池内名次」会随赛道预筛的池子大小漂移（见 normalize_rank_total）。
        total = item.get("rank_total")
        if total is not None:
            obs["rank_total"] = total
    return obs


def item_key(it: dict) -> str:
    return f"{it.get('source')}:{it.get('id')}"


def merge_candidates(existing: dict, fresh: dict) -> dict:
    """把本次采集并入同日已有候选池（ADR-0006：采集 2–3 次以捕捉事件发酵过程）。

    合并规则：
    - 条目按 (source, id) 归并；**保留 first_seen**（第一次被看到的时间），
      热度轨迹记进 hot_history —— 「发酵过程」正是这些中间值，而不是最后一个数字。
    - 已有条目只更新热度/排名/封面等易变字段，不动 first_seen。
    - 已恢复的源从 failures 中移除；sources 取并集。
    - 每次采集追加一条 collection_runs，供简报呈现「今天采了几次、每次多少条」。
    """
    merged = dict(fresh)
    prev_items = {item_key(i): i for i in existing.get("items", []) if item_key(i) != "None:None"}
    prev_dropped = {item_key(i): i for i in existing.get("dropped", []) if item_key(i) != "None:None"}

    items: list[dict] = []
    for it in fresh.get("items", []):
        old = prev_items.pop(item_key(it), None)
        if old is None:
            it = {**it, "first_seen": fresh["collected_at"],
                  "hot_history": [hot_observation(fresh["collected_at"], it)]}
        else:
            it = {**it,
                  "first_seen": old.get("first_seen", existing.get("collected_at")),
                  "hot_history": normalize_hot_history(old.get("hot_history")) + [
                      hot_observation(fresh["collected_at"], it)]}
        items.append({**it, "seen_in_last_run": True})
    # 上一轮见过、这一轮没再出现的条目：保留并标记，不静默丢弃。
    # hot 与其轨迹同样过一遍归一化——这些条目本轮没有新观测，但它们的**存量**
    # 旧格式仍会被下游读到（见 normalize_hot_history 的说明）。
    for old in prev_items.values():
        items.append({**old,
                      "hot": normalize_hot(old.get("hot")),
                      "hot_history": normalize_hot_history(old.get("hot_history")),
                      "seen_in_last_run": False})

    dropped: list[dict] = []
    for it in fresh.get("dropped", []):
        old = prev_dropped.pop(item_key(it), None)
        dropped.append(it if old is None else {**it, "first_seen": old.get("first_seen")})
    dropped.extend(prev_dropped.values())

    meta_by_source: dict[str, dict] = {}
    for m in existing.get("source_meta", []):
        meta_by_source[m["source"]] = {**m, "observations": m.get("observations", 1)}
    for m in fresh.get("source_meta", []):
        old = meta_by_source.get(m["source"])
        meta_by_source[m["source"]] = {**m, "observations": (old or {}).get("observations", 0) + 1}

    # 失败的源：本次失败的直接用；旧的失败只在「本次也没成功」时保留。
    # ⚠️ 原实现只排除了「本次也失败」的源，于是**已恢复的源永远不会从 failures 里消失**
    # ——实测 10-19 第 1、2 次采集 csdn/sspai 报 500、第 3 次成功，简报却同时印出
    # 「源：7 成功 / 0 失败」与「失效源：csdn, sspai」（ADR-0047）。
    fresh_failed = {f["source"] for f in fresh.get("failures", [])}
    recovered = {m["source"] for m in fresh.get("source_meta", []) if m.get("ok")}
    failures = [f for f in fresh.get("failures", [])]
    failures += [f for f in existing.get("failures", [])
                 if f["source"] not in fresh_failed and f["source"] not in recovered]

    runs = list(existing.get("collection_runs", []))
    runs.append({"at": fresh["collected_at"], **fresh.get("counts", {})})

    # 本轮新进来的条目：以 first_seen 是否等于本次采集时刻判定。用 first_seen
    # 而不是 fresh.counts.kept_new，是因为后者在"本次采集的条目全部是旧的"时
    # 仍会报出本次的 kept 数——那会把"没新东西"显示成"采到 100 条新的"。
    new_count = sum(1 for i in items if i.get("first_seen") == fresh["collected_at"])
    runs[-1]["kept_new"] = new_count

    merged.update({
        "collected_at": fresh["collected_at"],
        "first_collected_at": existing.get("first_collected_at")
                              or existing.get("collected_at")
                              or fresh["collected_at"],
        "collection_runs": runs,
        "sources": sorted({*existing.get("sources", []), *fresh.get("sources", [])}),
        "source_meta": list(meta_by_source.values()),
        "stale_sources": fresh.get("stale_sources", []),
        "failures": failures,
        "items": items,
        "dropped": dropped,
        "counts": {
            "fetched": fresh.get("counts", {}).get("fetched", 0),
            "kept": len(items),
            "dropped": len(dropped),
            "sources_ok": fresh.get("counts", {}).get("sources_ok", 0),
            # 用合并后的 failures 长度，而不是本次的 sources_failed：否则会出现
            # 「0 失败」与「失效源：csdn」同时出现在简报里的自相矛盾（ADR-0047）
            "sources_failed": len(failures),
            "kept_new": len([i for i in items if i.get("first_seen") == fresh["collected_at"]]),
            "runs_today": len(runs),
        },
    })
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description="NewsAgg 采集层：热榜 → 归一化 → 赛道预筛")
    ap.add_argument("--base", default=os.environ.get("NEWSAGG_HOT_BASE", DEFAULT_BASE))
    ap.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", default=None, help="输出路径；默认 .newsagg/candidates/<日期>.json")
    ap.add_argument("--no-prescreen", action="store_true", help="跳过赛道预筛（保留全部条目）")
    ap.add_argument("--no-merge", action="store_true",
                    help="不并入同日已有候选池，直接覆盖（默认是累积合并，见 ADR-0006）")
    ap.add_argument("--stale-minutes", type=float, default=180.0,
                    help="数据超过该分钟数未更新则计为陈旧（默认 180）")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    now = datetime.now(CST)
    all_items, meta, failures = collect(sources, args.base, args.timeout)

    # 为每个源计算新鲜度（ADR-0029 第四条：updateTime 是真正的陈旧信号）
    #
    # 阈值按源计算而不是全局统一（ADR-0043）：各源在 DailyHotApi 里的缓存 TTL
    # 不同（github 24h，其余 1h），统一阈值等于拿一个时长量不同周期的源。
    # `stale_sources` 的每条同时带上判定所用的阈值，便于简报解释"为什么它算陈旧"。
    stale_sources = []
    for m in meta:
        age = source_age_minutes(m, now)
        m["age_minutes"] = round(age, 1) if age is not None else None
        threshold = source_stale_threshold(m["source"], args.stale_minutes)
        m["stale_threshold_minutes"] = round(threshold, 1)
        if age is not None and age > threshold:
            stale_sources.append({
                "source": m["source"],
                "age_minutes": round(age, 1),
                "threshold_minutes": round(threshold, 1),
            })

    keywords = load_niche_keywords()
    if args.no_prescreen:
        kept, dropped = all_items, []
    else:
        kept, dropped = niche_prescreen(all_items, keywords)

    out_path = Path(args.out) if args.out else repo_root() / ".newsagg" / "candidates" / f"{now:%Y-%m-%d}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "collected_at": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "base": args.base,
        "sources": sources,
        "counts": {
            "fetched": len(all_items),
            "kept": len(kept),
            "dropped": len(dropped),
            "sources_ok": sum(1 for m in meta if m.get("ok")),
            "sources_failed": len(failures),
            # 本次采集新进来的条目数。merge_candidates 会把它写进 collection_runs，
            # 简报 ② 段据此显示"每次采到多少新东西"——否则那一行只有时间没有内容。
            "kept_new": len(kept),
        },
        "source_meta": meta,
        "stale_sources": stale_sources,
        "failures": failures,
        "niche_keyword_count": len(keywords),
        "items": kept,
        "dropped": dropped,
    }

    # ADR-0006：采集每日 2–3 次以捕捉事件发酵过程。默认累积合并而非覆盖——
    # 覆盖会丢掉「同一事件热度如何爬升」这一正是多次采集想要的信息。
    #
    # 注意：即使没有旧文件也走 merge_candidates（以空池为底）。否则**当日第一次
    # 采集**产出的条目不会有 first_seen / hot_history，第二次合并时无轨迹可续，
    # runs_today 也永远停在 1。
    existing: dict = {}
    if not args.no_merge and out_path.exists():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("items"):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            pass  # 旧文件坏了就当没有：本次结果本身是完整的

    merged = bool(existing)
    result = merge_candidates(existing, result)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 简报友好的摘要
    print(json.dumps({
        "ok": True,
        "out": str(out_path),
        "collected_at": result["collected_at"],
        "merged_with_existing": merged,
        "runs_today": result.get("counts", {}).get("runs_today", 1),
        "counts": result["counts"],
        "failed_sources": [f["source"] for f in failures],
        "stale_sources": stale_sources,
    }, ensure_ascii=False, indent=2))

    # 全部源失败视为采集层失效（退出码 1，供看守/熔断识别）
    if failures and len(failures) == len(sources):
        print("错误：全部源采集失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
