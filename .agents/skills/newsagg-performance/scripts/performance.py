#!/usr/bin/env python3
"""表现数据回路（循环 C）——读回每篇的展现/阅读，算自身基准，驱动降频。

设计依据：
- ADR-0010 表现数据只作**软信号**：分拣的排序参考 + 简报内容 + 自动降频。
  恢复需要人工确认；「自身基准」需要冷启动豁免期；回路刻意保持"软"。
- ADR-0030 浏览器命令必须串行（本脚本与发布层**共用同一把锁文件**）；
  `analytics works` 的 DOM 噪声不可直接消费；`content list` 需归一化。
- ADR-0023 2GB 约束：Chrome 按需启动、用完即杀（toutiao-ops 每条命令自开自关）。
- ADR-0011 两类重试：只读探测可重试 3 次；写操作（发布）绝不。
- ADR-0025 / ADR-0018：本脚本是运行时产物与入库产物的分界。

**本脚本必须永不致命**：它在 `run_all.sh` 里以 `|| true` 调用。理由是简报必须
无条件每日存在（ADR-0006）——若表现数据读不回来就让整轮运行死掉，看守将无法
区分"系统没跑"与"表现数据读不到"，那是比丢失数据更坏的故障。
失败一律记录到 `.newsagg/performance-status.json`，由简报呈现。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

# ADR-0030：只按**已知指标名**白名单提取，不消费 DOM 噪声
# （`analytics works` 的 metrics 里混着省份名与频道来源百分比）。
METRIC_KEYS = (
    "showCount",      # 展现
    "readCount",      # 阅读
    "diggCount",      # 点赞
    "commentCount",   # 评论
    "repinCount",     # 转发
    "videoWatchCount",
)

# 经验值而非推导值（ADR-0014 的做法：记录在此以便复核，而不是因为经过了论证）。
# 冷启动豁免 5 篇由本人选定，与 Q88「首周只看链路与是否愿署名」大致对齐。
DEFAULTS = {
    "window": 5,        # 基准取最近 N 篇的中位数
    "coldstart": 5,     # 评估满 N 篇之前不建立基准、不判降频（ADR-0010）
    "low_ratio": 0.5,   # 低于基准的这个比例算「明显低于」
    "low_streak": 3,    # 连续 N 篇才算「持续低迷」
}

HOW_TO_CLEAR = (
    "人工确认表现已恢复后，删除 .newsagg/frequency.json 即可解除"
    "（ADR-0010：恢复同样需要人工确认，系统不自动恢复）"
)

# 读回后端。**默认 api**：纯 HTTP 路径不需要 Chrome，而 Chrome 恰恰是 2GB
# 内存机器上最可能崩的东西（ADR-0023 记录过它把表现数据读回来搞挂的次数）。
# browser 后端保留，是因为**只有它能看见微头条**（ADR-0047 的泄漏判据要数
# 同标题出现几次）——所以发布反查仍走浏览器（publish.py 的 --verify-backend
# 默认 auto，跟随写入后端）。
READ_BACKENDS = ("api", "browser")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_sibling_script(rel: str):
    """按路径加载兄弟技能里的脚本。

    ⚠️ 为什么是导入而不是复制：ADR-0030 要求**整个账号的浏览器命令共用串行化**。
    `content list` 是发布反查与表现数据读取共用的同一条命令，如果这里复制一份锁
    实现，就会产生第二把锁文件——两把锁互不感知，等于没有串行。
    因此串行锁、CLI 调用、失败分类一律复用发布层的唯一实现。
    """
    path = repo_root() / rel
    spec = importlib.util.spec_from_file_location(path.stem + "_shared", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_api_module():
    """懒加载 toutiao_api（纯 HTTP 客户端）。失败不致命——回落浏览器后端。

    为什么是懒加载而不是顶层 import：浏览器后端跑得好好的时候，
    不该因为纯 HTTP 客户端的 import 失败而崩（与 publish.py 的 `_load_api_module`
    同一个理由）。
    """
    path = repo_root() / ".agents/skills/newsagg-publish/scripts/toutiao_api.py"
    spec = importlib.util.spec_from_file_location("toutiao_api_shared", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _api_client(account: str):
    """建一个纯 HTTP 客户端。返回 (module, client) 或抛异常。

    `--account` 在 api 后端没有意义（它用的是**当前登录的那个号**，不是某个
    profile）。与 publish.py 一致：不是 default 就打一条警告，不假装支持。

    ## 服务器上为什么能用注入的 cookie

    `from_browser()` 依赖本机 Chrome 的 cookie 库，而服务器上没装 Chrome
    （ADR-0071 实测：`CookieError: 找不到浏览器 chrome 的 profile`）。
    所以 `NEWSAGG_COOKIE_FILE` 指向一个注入的 cookie 文件时走
    `from_cookie_file()`——那条路在本机已实测走通（`login_status` 正常、
    作品列表 16 条）。

    优先级：环境变量 `NEWSAGG_COOKIE_FILE` > 本机 Chrome。
    """
    mod = _load_api_module()
    cookie_file = os.environ.get("NEWSAGG_COOKIE_FILE", "").strip()
    if cookie_file:
        return mod, mod.Client.from_cookie_file(cookie_file)
    if account and account != "default":
        print(f"⚠️ api 后端不支持 --account={account}：它用的是浏览器里当前登录的账号。",
              file=sys.stderr)
    return mod, mod.Client.from_browser()


def cookie_expiry_warning() -> dict:
    """注入的 cookie 快过期了吗（ADR-0071 残余风险 69）。

    ## 为什么要有这个函数

    服务器上的纯 HTTP 链路依赖一份注入的 cookie 文件。那份文件的有效期
    在**导出时**就被丢掉了——`Cookie.to_dict()` 原本不导出过期时间，Chrome
    存的是 WebKit 纪元（1601 起）的微秒数，混用单位会得到「年份 425885148」
    这种荒谬值。

    后果是：cookie 过期这件事，**只有症状没有预告**。症状是「某天起纯 HTTP
    全部 401，而浏览器后端还好」——那个现象看起来像代码坏了，不像凭证过期，
    于是会去查代码，查半天。

    所以这里做两件事：
    1. 导出时把过期时间**写进文件**（`expires_at`，Unix 秒）——修的是源头。
    2. 每次读表现数据时检查一次，**快过期就报出来**——修的是「没人提醒」。

    ## 为什么用「快过期」而不是「已过期」

    已过期时请求会直接失败，那种情况走 api 失败 → 回落浏览器后端就够了。
    真正需要提前说的是「还有 3 天就过期了」——那是唯一还能从容处理的时候。

    没有 cookie 文件时返回空 dict：那是本机直读 Chrome 的场景，cookie 由
    浏览器自己管，不该在这里提醒。
    """
    path = os.environ.get("NEWSAGG_COOKIE_FILE", "").strip()
    if not path:
        return {}
    p = Path(path)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 读不出来不由这里判：加载时已经报过更具体的错。
        return {}
    earliest = int(payload.get("earliest_expires_at") or 0)
    if not earliest:
        return {}
    now_ts = datetime.now(CST).timestamp()
    days = (earliest - now_ts) / 86400.0
    info = {
        "cookie_file": p.name,
        "earliest_expires_at": earliest,
        "earliest_expires_date": datetime.fromtimestamp(earliest, CST).strftime("%Y-%m-%d"),
        "days_left": round(days, 1),
    }
    if days < 0:
        info["level"] = "expired"
        info["message"] = (f"注入的 cookie 已于 {info['earliest_expires_date']} 过期"
                           f"（{abs(days):.0f} 天前）。纯 HTTP 链路会全部失败，"
                           f"请在本机重新登录并重新导出。")
    elif days < 7:
        info["level"] = "expiring"
        info["message"] = (f"注入的 cookie 还有 {days:.0f} 天过期（{info['earliest_expires_date']}）。"
                           f"请在本机重新导出并替换服务器上的文件。")
    else:
        info["level"] = "ok"
    return info


def read_jsonl(path: Path) -> list[dict]:
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
            continue  # 容忍坏行，不让一行脏数据毁掉整段历史
    return out


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def epoch_to_iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), CST).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# ────────────────────────── 归一化 ──────────────────────────

def classify_kind(base: dict) -> str:
    """判定一条作品是 `article`（图文文章）还是 `weitoutiao`（微头条）。

    ADR-0047：`content list` 把文章和微头条混在同一个列表里返回，两者都能
    匹配上本地标题，于是「我们发了几篇」会被算多。2026-10-18 实测：一次发布
    动作产出了一篇文章 + 一条微头条，标题相同、各自独立计数，导致
    `reports/performance.jsonl` 里同一篇出现两个 gid。

    三条互相独立的判据（实测样本 5 条，全部一致）：
    - gid 位数：文章 19 位、微头条 16 位；
    - URL 形态：文章 `/item/<gid>/`、微头条 `/a<gid>`；
    - `content` 字段：文章为空，微头条**非空**（内容就是正文/标题本身）。

    用多数票而不是单条判据：任何一条判据都可能因平台改版失效，
    三条同时看能在改版时退化成「仍大致正确」而不是全错。
    """
    gid = str(base.get("gidStr") or "")
    url = str(base.get("articleURL") or base.get("displayURL") or "")
    content = str(base.get("content") or "").strip()

    votes = {"article": 0, "weitoutiao": 0}
    if gid:
        if len(gid) >= 18:
            votes["article"] += 1
        elif len(gid) <= 17:
            votes["weitoutiao"] += 1
    if "/a" in url and "/item/" not in url:
        votes["weitoutiao"] += 1
    elif "/item/" in url:
        votes["article"] += 1
    if content:
        votes["weitoutiao"] += 1
    else:
        votes["article"] += 1

    return "weitoutiao" if votes["weitoutiao"] > votes["article"] else "article"


def extract_articles(raw: dict) -> tuple[list[dict], dict]:
    """从 `content list` 的原始信封里抽出每篇文章（ADR-0030 决策四）。

    实测形状（ADR-0037 记录）::

        {success, source, count, items: [
            {data: [{assembleCell: {itemCell: {articleBase, itemCounter, ...}}}, ...]},
            {error_tips, error_code, error_msg, data: {...}}     # 游标/错误包装
        ]}

    两个坑：
    - 顶层 `count` 是**包装元素个数**，不是作品数（当前实测 count=2 而只有 1 篇作品）；
    - `items[1]` 是游标包装，没有 `assembleCell`，遍历时必须跳过。
    """
    meta = {"source": raw.get("source") if isinstance(raw, dict) else None, "parse": "ok"}
    articles: list[dict] = []

    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        meta["parse"] = "failed"
        meta["why"] = "顶层没有 items 数组"
        return articles, meta

    meta["envelope_items"] = len(items)

    for wrapper in items:
        if not isinstance(wrapper, dict):
            continue
        data = wrapper.get("data")
        if not isinstance(data, list):
            continue
        for cell in data:
            if not isinstance(cell, dict):
                continue
            item_cell = (cell.get("assembleCell") or {}).get("itemCell")
            if not isinstance(item_cell, dict):
                continue  # 游标/错误包装

            base = item_cell.get("articleBase") or {}
            counter = item_cell.get("itemCounter") or {}

            metrics = {}
            for k in METRIC_KEYS:
                v = counter.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    metrics[k] = int(v)

            show = metrics.get("showCount")
            read = metrics.get("readCount")
            # 展现为 0 时阅读率无定义——记 None 而不是 0，避免把"没被展现"
            # 混进"被展现了但没人点"的统计里。
            rate = (read / show) if (show and read is not None) else None

            articles.append({
                "gid": str(base.get("gidStr") or "") or None,
                "title": (base.get("title") or "").strip() or None,
                "published_at": epoch_to_iso(base.get("publishTime")),
                "item_status": base.get("itemStatus"),
                "url": base.get("articleURL") or base.get("displayURL"),
                # ADR-0047：区分文章与微头条。两者出现在同一个作品列表里，
                # 若不区分，「我们的文章」会把一条微头条也算成一篇——
                # 冷启动豁免（5 篇）提前耗尽、基准被稀释。
                "kind": classify_kind(base),
                "metrics": metrics,
                "read_rate": round(rate, 6) if rate is not None else None,
            })

    if not articles:
        meta["parse"] = "empty"
    return articles, meta


def local_title_index(root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """平台作品 → slug 的索引。**gid 优先，其次标题；账本优先于成稿。**

    ⚠️ 为什么账本优先（实测踩到）：本人在平台上改过标题之后，本地成稿的一级标题
    已经是**新**标题，而平台上那篇作品的标题仍是**发布当时**的旧标题。只按
    `outputs/*.md` 匹配会把自己唯一发出去的文章匹配不上——数据在，关联断了。
    账本里记的正是发布当时的标题，这是唯一可靠的关联。

    ⚠️ 为什么还要按 gid 匹配（ADR-0051 的后遗症）：平台标题可能被输入框吞字
    （2026-10-21「中间层岗位」→「中间层位」），此时**任何**基于标题的索引都
    匹配不上——账本里的 `title` 是成稿标题、`title_on_platform` 是平台实际值，
    两个都收才够；而 gid 是平台分配的、不受输入路径影响，是更强的关联。
    2026-10-21 这天就是靠这里补上的：那篇被吞字的文章本来判成 `matched: false`，
    既不进基准序列、也不消耗冷启动豁免的 5 篇额度——数据在，关联又断了一次。
    """
    from_ledger: dict[str, str] = {}
    from_outputs: dict[str, str] = {}
    by_gid: dict[str, str] = {}

    for rec in read_jsonl(root / ".newsagg" / "publish-ledger.jsonl"):
        slug = rec.get("slug")
        if not slug:
            continue
        gid = str(rec.get("gid") or "").strip()
        if gid:
            by_gid.setdefault(gid, str(slug))
        # 成稿标题与平台实际标题都收：吞字时只有后者能匹配上（ADR-0051）
        for key in ("title", "title_on_platform"):
            t = rec.get(key)
            if t:
                from_ledger.setdefault(str(t).strip(), str(slug))

    for md in sorted((root / "outputs").glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.match(r"^#\s+(.+)$", text, re.M)
        if m:
            from_outputs.setdefault(m.group(1).strip(), md.stem)

    index = dict(from_outputs)
    index.update(from_ledger)  # 账本覆盖成稿
    return index, by_gid, {"from_ledger": len(from_ledger),
                           "from_outputs": len(from_outputs),
                           "by_gid": len(by_gid)}


# ────────────────────────── 单篇详细指标（ADR-0068）──────────────────────────

# 平台枚举：`{All:0,PgcArticle:1,Video:2,Weitoutiao:3,Wenda:4,ShortVideo:5,Audio:6,PgcGallery:7}`
# 文章是 1。传错编号不会报参数错，只返回 1003「数据开小差啦」——与「无数据」一模一样。
CONTENT_TYPE_ARTICLE = 1


def extract_detail_metrics(payload: dict) -> dict:
    """从 `content-detail` 的单篇信封里抽出三个 `content list` 没有的指标。

    这三个指标是后台「查看数据」弹层里显示、但 `content list` 不给的：
      - `read_complete_rate`  平均阅读完成率（百分数，如 84.0）
      - `read_duration`       平均阅读时长（秒）
      - `click_rate_rank`     「超过 N% 同类作品」的 N（后台文案里的那个 N）

    口径（ADR-0068 核实）：
      - `-1` = 数据未就绪（作品发布**次日 14:00 后**才可查）→ 保留 -1，不当 0
      - `0`  = 已就绪但为 0（0 阅读的文章完读率就是 0，不是缺失）

    ⚠️ 为什么不直接用 `consume_data.impression_count` 覆盖 `showCount`：
    实测两者口径一致（`content list` 的 showCount 1290 == item/info 的 impression_count 1290），
    但 `content list` 是列表级读数、`item/info` 是单篇读数，覆盖会让同一天两轮运行
    之间出现无谓的差异。就地更新时只更新这三项，展示量仍以 `content list` 为准。
    """
    out: dict = {}
    if not isinstance(payload, dict):
        return out
    item = payload.get("item_data")
    if not isinstance(item, dict):
        return out
    stat = item.get("item_stat")
    if not isinstance(stat, dict):
        return out

    detail = stat.get("consume_detail")
    if isinstance(detail, dict):
        for src, dst in (("read_complete_rate", "read_complete_rate"),
                         ("read_duration", "read_duration")):
            v = detail.get(src)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[dst] = v

    ranking = stat.get("ranking_data")
    if isinstance(ranking, dict):
        for k in ("click_rate_rank", "interaction_rate_rank", "read_complete_rate_rank"):
            v = ranking.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = v
    return out


def fetch_content_details(pub, root: Path, account: str, gids: list[str]) -> tuple[dict, dict]:
    """批量读单篇详细指标。返回 (gid → 指标字典, 观测元数据)。

    为什么要批量：`getContentDetail` 每篇都要冷启一次浏览器 + 登录 + 导航，
    10 篇就是 10 次。`content-detail-batch` 只启一次浏览器（ADR-0068）。

    失败不致命：表现数据读不回来不阻断本轮（ADR-0006）。
    """
    meta: dict = {"requested": len(gids), "source": "content-detail-batch"}
    if not gids:
        meta["skipped"] = "没有 gid"
        return {}, meta

    env = {
        "TT_CLI_DIR": root / "reference" / "toutiao-ops" / "cli",
        "ENVIRON": {
            **os.environ,
            "HOME": str(root / ".home"),  # ADR-0028
            "PATH": f"{root / '.uv-bin'}:{os.environ.get('PATH', '')}",
        },
    }

    lock = pub.FileLock(root / ".newsagg" / "browser.lock", timeout=300.0)
    try:
        with lock:
            code, out, err = pub.run_toutiao(
                ["analytics", "content-detail-batch",
                 "--content-id", *gids,
                 "--content-type", str(CONTENT_TYPE_ARTICLE),
                 "--account", account],
                env, timeout=300,
            )
    except TimeoutError as exc:
        meta["error"] = f"锁等待超时：{exc}"
        return {}, meta

    if code != 0:
        meta["error"] = f"content-detail-batch 失败（code={code}）"
        meta["stderr_tail"] = (err or out)[-400:]
        return {}, meta

    try:
        raw = json.loads(out)
    except json.JSONDecodeError as exc:
        meta["error"] = f"JSON 解析失败：{exc}"
        return {}, meta

    if not raw.get("success"):
        meta["error"] = raw.get("error") or "接口返回 success=false"
        return {}, meta

    out_map: dict[str, dict] = {}
    no_data: list[str] = []
    failed: list[dict] = []
    for item in raw.get("items") or []:
        gid = str(item.get("contentId") or "")
        if not gid:
            continue
        if not item.get("success"):
            failed.append({"gid": gid, "error": item.get("error")})
            continue
        if item.get("noData"):
            no_data.append(gid)
            continue
        m = extract_detail_metrics(item.get("data") or {})
        if m:
            out_map[gid] = m

    meta["got"] = len(out_map)
    meta["no_data"] = no_data
    meta["failed"] = failed
    if no_data:
        # 1003 合法但无数据 = 作品发布次日 14:00 前。这是**正常状态**，不是故障。
        meta["note"] = (f"{len(no_data)} 篇暂无数据（作品发布次日 14:00 前才可查，"
                        f"ADR-0068 口径）")
    return out_map, meta


# ────────────────────────── 基准与降频 ──────────────────────────

def record_kind(rec: dict) -> str:
    """历史记录里的 `kind`；老记录没有这个字段，按 gid 位数回退（ADR-0047）。

    `reports/performance.jsonl` 是**跨环境保留的**入库文件，里面已经有修复前
    写下的记录（kind 缺失），而其中一条正是微头条。若只认新字段，那条
    微头条会继续留在基准序列里，等于没修。所以老记录必须能就地判定。
    """
    kind = rec.get("kind")
    if kind in ("article", "weitoutiao"):
        return kind
    gid = str(rec.get("gid") or "")
    if gid and len(gid) <= 17:
        return "weitoutiao"
    return "article"


def latest_per_article(records: list[dict], ours_only: bool = True) -> list[dict]:
    """每篇文章取最新一次观测，再按发布时间排序。"""
    latest: dict[str, dict] = {}
    for r in records:
        if ours_only and not r.get("ours"):
            continue
        # ADR-0047：微头条不算一篇（同一次发布动作的产物，标题与文章相同）
        if record_kind(r) != "article":
            continue
        key = r.get("gid") or r.get("slug")
        if not key:
            continue
        prev = latest.get(key)
        cur_key = (str(r.get("date") or ""), str(r.get("collected_at") or ""))
        if prev is None:
            latest[key] = r
        else:
            prev_key = (str(prev.get("date") or ""), str(prev.get("collected_at") or ""))
            if cur_key > prev_key:
                latest[key] = r
    return sorted(latest.values(), key=lambda r: (str(r.get("published_at") or ""),
                                                 str(r.get("date") or "")))


def _is_low(rate: float, threshold: float) -> bool:
    """基准为 0 时，`rate < threshold` 恒为假——那会让一串零阅读永远不触发降频。"""
    if threshold <= 0:
        return rate <= 0.0
    return rate < threshold


def evaluate(records: list[dict], *, window: int, coldstart: int,
             low_ratio: float, low_streak: int) -> dict:
    """对比自身基准，判断是否应降频（ADR-0010）。"""
    series = latest_per_article(records)
    evaluated = [r for r in series if r.get("read_rate") is not None]

    result: dict = {
        "articles_seen": len(series),
        "articles_evaluated": len(evaluated),
        "effective": {"window": window, "coldstart": coldstart,
                      "low_ratio": low_ratio, "low_streak": low_streak},
        "status": "ok",
        "baseline_read_rate": None,
        "threshold": None,
        "low_streak": 0,
    }

    if len(evaluated) < coldstart:
        result["status"] = "cold_start"
        result["note"] = (
            f"已评估 {len(evaluated)} 篇 < 冷启动豁免 {coldstart} 篇："
            "不建立基准、不判降频（ADR-0010）"
        )
        return result

    history = evaluated[:-1][-window:]
    if len(history) < 2:
        result["status"] = "cold_start"
        result["note"] = f"可用历史只有 {len(history)} 篇，不足以形成基准"
        return result

    baseline = statistics.median([r["read_rate"] for r in history])
    threshold = baseline * low_ratio

    streak = 0
    for r in reversed(evaluated):
        if _is_low(r["read_rate"], threshold):
            streak += 1
        else:
            break

    result.update({
        "baseline_read_rate": round(baseline, 6),
        "threshold": round(threshold, 6),
        "low_streak": streak,
        "history": [
            {"slug": r.get("slug"), "date": r.get("date"), "read_rate": r.get("read_rate"),
             "metrics": r.get("metrics")}
            for r in history
        ],
        "latest": {
            "slug": evaluated[-1].get("slug"),
            "title": evaluated[-1].get("title"),
            "date": evaluated[-1].get("date"),
            "read_rate": evaluated[-1].get("read_rate"),
            "metrics": evaluated[-1].get("metrics"),
        },
    })

    if streak >= low_streak:
        result["status"] = "downshift"
        result["reason"] = (
            f"连续 {streak} 篇阅读率低于自身基准的 {low_ratio:.0%}"
            f"（基准 {baseline:.4%}，阈值 {threshold:.4%}）"
        )
    return result


def is_skip_day(freq: dict | None, date: str) -> tuple[bool, str]:
    """降频生效后按隔日跳过。降频当日不跳，其后每个奇数日跳过。"""
    if not freq or not freq.get("downshifted"):
        return False, ""
    since = str(freq.get("since") or "")
    try:
        d0 = datetime.strptime(since, "%Y-%m-%d").date()
        d1 = datetime.strptime(date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, ""
    delta = (d1 - d0).days
    if delta <= 0:
        return False, f"降频自 {since} 生效（当日仍发文）"
    if delta % 2 == 1:
        return True, f"降频中（ADR-0010）：自 {since} 起隔日发文，今日为跳过日"
    return False, f"降频中（自 {since} 起隔日发文），今日为发文日"


# ────────────────────────── 读取 ──────────────────────────

# 平台作品列表接口（纯 HTTP 可取，见 ADR-0071）。
# `impression_count` / `go_detail_count_v2` 与浏览器侧 `content list` 的
# `showCount` / `readCount` **逐篇一致**（实测 10/10 篇全等），所以 api 后端
# 不是「降级的近似」，而是同一组数字的另一种取法。
API_ITEM_INFO_URL = (
    "https://mp.toutiao.com/mp/agw/statistic/v2/item/info?item_id={gid}&type={type}&app_id=1231"
)

# 平台的内容类型枚举。**图文文章是 1，不是 2**（ADR-0068 从发布页 bundle 里
# 抄下来的原文）。传错编号不会报参数错误，只会返回 1003「数据开小差啦」，
# 与「这篇没有数据」长得一模一样——这正是 ADR-0037 误判接口不可用的原因。
CONTENT_TYPE_PGC_ARTICLE = 1
CONTENT_TYPE_KNOWN = (0, 1, 2, 3, 4, 5, 6, 7)


def classify_kind_api(item: dict, gid: str, url: str, is_draft: bool) -> str:
    """纯 HTTP 路径的作品类型判定。

    与浏览器路径的 `classify_kind`（三条判据多数票）不同，这里只有两条可用的：
    - 草稿：浏览器路径的 `content` 字段在纯 HTTP 里没有，但 `is_draft` 更直接；
    - gid 位数：文章 19 位、微头条 ≤17 位（ADR-0047 实测）。

    ⚠️ 边界：草稿**一律**算 `article`（草稿没有微头条这种形态），
    且草稿不参与表现评估。
    """
    if is_draft:
        return "article"
    if gid and len(gid) <= 17:
        return "weitoutiao"
    if "/a" in url and "/item/" not in url:
        return "weitoutiao"
    return "article"


def fetch_content_list_api(account: str, limit: int) -> tuple[list[dict], dict]:
    """纯 HTTP 取作品列表。返回 (归一化后的作品, 观测元数据)。

    ## 为什么不复用 `content list` 的解析器

    两条路返回的是**不同的结构**：浏览器后端是 `items[].data[].assembleCell
    .itemCell.{articleBase,itemCounter}` 的深层嵌套，纯 HTTP 是 `data.articles[]`
    的扁平对象。强行统一成一个形状，等于在解析层塞一个「兼容两种源」的分支——
    而分支的判据只能靠「某个键在不在」来猜。这里选择**各自解析、同一出口**：
    两条路都产出同一组 `{"gid","title","metrics","kind",...}` 字段，
    后续的匹配、落库、评估完全共用。

    ## 口径核实（2026-10-27 实测 10/10 篇）

    | 字段 | 纯 HTTP | 浏览器 `content list` |
    |---|---|---|
    | 展现 | `impression_count` | `itemCounter.showCount` |
    | 阅读 | `go_detail_count_v2` | `itemCounter.readCount` |

    10 篇逐篇比对**全部相等**（98/1、170/0、421/1、597/4、668/0、18/2、
    1084/10、1214/2、1201/0、1303/0）。所以这不是近似值，换后端不改变读数。

    ## 微头条为什么取不到

    纯 HTTP 的作品列表只返回**图文文章**（实测 16 条里 `article_type` 全是 0，
    微头条根本不在这个列表里）。浏览器后端能取到微头条是因为它拦截的是
    另一个接口。

    → **api 后端在微头条上瞎**。这意味着 ADR-0047 的泄漏判据（数同标题出现
    几次）在 api 后端下会漏掉微头条。所以：**api 后端只用于表现数据读取
    （我们的文章都是文章），发布反查仍然走浏览器后端**。
    """
    meta: dict = {"backend": "api"}
    try:
        mod, client = _api_client(account)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"api 客户端不可用：{exc}"
        return [], meta

    try:
        raws = client.session.get_json(
            f"{mod.ARTICLE_LIST_URL}?status=all&limit={int(limit)}&offset=0"
        )
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"作品列表请求失败：{exc}"
        return [], meta

    data = (raws or {}).get("data") or {}
    articles_raw = data.get("articles")
    if not isinstance(articles_raw, list):
        meta["error"] = ("作品列表结构变了：data.articles 期望 list，"
                         f"实得 {type(articles_raw).__name__}")
        return [], meta

    out: list[dict] = []
    drafts: list[str] = []
    for item in articles_raw:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id") or item.get("pgc_id") or item.get("id") or "")
        if not gid:
            continue
        is_draft = bool(item.get("is_draft"))
        if is_draft:
            # 草稿不进表现序列。理由：草稿的展现恒为 0（实测 5 条草稿全是 0），
            # 混进去会把「没被展现」当成「被展现了但没人点」。
            # 浏览器后端压根不返回草稿（它走的是已发布列表），所以这里
            # **必须**过滤，否则两条路的样本集不一样，基准会不可比。
            drafts.append(gid)
            continue
        metrics: dict[str, int] = {}
        for src, dst in (("impression_count", "showCount"),
                         ("go_detail_count_v2", "readCount"),
                         ("digg_count", "diggCount"),
                         ("comment_count", "commentCount")):
            v = item.get(src)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                metrics[dst] = int(v)

        show = metrics.get("showCount")
        read = metrics.get("readCount")
        # 展现为 0 时阅读率无定义——记 None 而不是 0（与浏览器路径同口径）。
        rate = (read / show) if (show and read is not None) else None

        url = str(item.get("article_url") or "")
        out.append({
            "gid": gid,
            "title": (str(item.get("title") or "")).strip() or None,
            "published_at": epoch_to_iso(item.get("create_time")),
            "item_status": item.get("status"),
            "url": url,
            # 纯 HTTP 列表里只有图文文章。微头条不在这个接口里（见 docstring），
            # 所以这里恒为 "article"；但仍然走同一个判定函数而不是写死——
            # 万一平台把微头条也塞进这个接口，判定会自己跟上。
            "kind": classify_kind_api(item, gid, url, is_draft),
            "metrics": metrics,
            "read_rate": round(rate, 6) if rate is not None else None,
            "is_draft": is_draft,
            "status_desc": str(item.get("status_desc") or ""),
        })

    meta["count"] = len(out)
    meta["source"] = "api_article_list"
    if drafts:
        meta["drafts_skipped"] = len(drafts)
    return out, meta


def fetch_content_details_api(account: str, gids: list[str]) -> tuple[dict, dict]:
    """纯 HTTP 逐篇取详细指标。返回 (gid → 指标字典, 观测元数据)。

    ## 为什么逐篇串行而不是并发

    浏览器版本在页面内也是**逐个 await**（`reference/toutiao-ops/cli/src/
    analytics.js:112`），注释写明「并发打太多容易被平台的接口频控挡掉，
    而错判成「无数据」比慢更糟」。纯 HTTP 版的频控阈值未知，所以沿用同样的
    保守做法：16 篇约 9.3 秒，慢但不会把频控误判成「这篇没数据」。

    ## 口径（ADR-0068）
    - `code=1003` + type 合法 → 这篇暂无数据（发布次日 14:00 前），**不是故障**；
    - `code=1003` + type 非法 → 参数错，要报错；
    - `code=0` → 有数据。
    """
    meta: dict = {"requested": len(gids), "backend": "api",
                  "source": "api_item_info"}
    if not gids:
        meta["skipped"] = "没有 gid"
        return {}, meta

    try:
        mod, client = _api_client(account)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"api 客户端不可用：{exc}"
        return {}, meta

    out_map: dict[str, dict] = {}
    no_data: list[str] = []
    failed: list[dict] = []
    for gid in gids:
        url = API_ITEM_INFO_URL.format(gid=gid, type=CONTENT_TYPE_PGC_ARTICLE)
        try:
            payload = client.session.get_json(url)
        except Exception as exc:  # noqa: BLE001
            failed.append({"gid": gid, "error": str(exc)})
            continue
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == 1003:
            if CONTENT_TYPE_PGC_ARTICLE not in CONTENT_TYPE_KNOWN:
                failed.append({"gid": gid, "error": "content-type 编号不在平台枚举内"})
            else:
                no_data.append(gid)
            continue
        if code != 0:
            failed.append({"gid": gid,
                           "error": f"接口返回 code={code}：{payload.get('message') or ''}"})
            continue
        m = extract_detail_metrics(payload)
        if m:
            out_map[gid] = m

    meta["got"] = len(out_map)
    meta["no_data"] = no_data
    meta["failed"] = failed
    if no_data:
        meta["note"] = (f"{len(no_data)} 篇暂无数据（作品发布次日 14:00 前才可查，"
                        f"ADR-0068 口径）")
    return out_map, meta


def _fetch_via_browser(pub, root: Path, account: str, limit: int) -> tuple[list[dict], dict]:
    """浏览器后端取作品列表。返回 (归一化后的作品, 观测元数据)。

    这是 `fetch_content_list` 的薄包装——浏览器路径要解析 `content list` 的
    深层嵌套信封（`items[].data[].assembleCell.itemCell`），把它收敛成与
    纯 HTTP 路径**同一组字段**，两条路才能共用后面的匹配、落库、评估。
    """
    raw, meta = fetch_content_list(pub, root, account, limit)
    if not raw:
        return [], meta
    articles, parse_meta = extract_articles(raw)
    meta = {**meta, **parse_meta}
    return articles, meta


def fetch_content_list(pub, root: Path, account: str, limit: int) -> tuple[dict, dict]:
    """在串行锁内跑 `content list`。返回 (原始信封, 观测元数据)。

    只读命令，按 ADR-0011 可重试 3 次；并发冲突（ADR-0030）不算失败。
    """
    env = {
        "TT_CLI_DIR": root / "reference" / "toutiao-ops" / "cli",
        "ENVIRON": {
            **os.environ,
            "HOME": str(root / ".home"),  # ADR-0028：$HOME 重定向是所有命令的前置条件
            "PATH": f"{root / '.uv-bin'}:{os.environ.get('PATH', '')}",
        },
    }

    meta = {"attempts": [], "lock_timeout": False}
    lock = pub.FileLock(root / ".newsagg" / "browser.lock", timeout=300.0)

    try:
        with lock:
            for attempt in range(1, 4):
                code, out, err = pub.run_toutiao(
                    ["content", "list", "--type", "article", "--status", "published",
                     "--limit", str(limit), "--account", account],
                    env, timeout=240,
                )
                kind = pub.classify_failure(out + err)
                meta["attempts"].append({"attempt": attempt, "code": code, "kind": kind})
                if code == 0:
                    try:
                        return json.loads(out), meta
                    except json.JSONDecodeError as exc:
                        meta["error"] = f"JSON 解析失败：{exc}"
                        return {}, meta
                if kind == "concurrency":
                    continue  # 瞬时竞态 → 重试（ADR-0030）
                meta["error"] = f"content list 失败（kind={kind}, code={code}）"
                meta["stderr_tail"] = (err or out)[-400:]
                return {}, meta
    except TimeoutError as exc:
        meta["lock_timeout"] = True
        meta["error"] = str(exc)

    meta.setdefault("error", "重试 3 次仍失败")
    return {}, meta


# ────────────────────────── 主流程 ──────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="NewsAgg 表现数据回路（循环 C）")
    ap.add_argument("--from-file", default=None,
                    help="解析已保存的 content list JSON，不启动浏览器（测试/复算用）")
    ap.add_argument("--account", default="default")
    ap.add_argument("--backend", choices=list(READ_BACKENDS), default="api",
                    help="读回后端：api=纯 HTTP（不需要 Chrome），browser=Playwright")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--window", type=int, default=DEFAULTS["window"])
    ap.add_argument("--coldstart", type=int, default=DEFAULTS["coldstart"])
    ap.add_argument("--low-ratio", type=float, default=DEFAULTS["low_ratio"])
    ap.add_argument("--low-streak", type=int, default=DEFAULTS["low_streak"])
    ap.add_argument("--min-free-mb", type=int, default=800, help="启动 Chrome 前的可用内存下限（ADR-0023）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写（不落库、不改降频状态）")
    ap.add_argument("--check-skip", action="store_true",
                    help="只回答「今天是不是降频跳过日」并打印 JSON，不启动浏览器")
    ap.add_argument("--date", default=None, help="用于 --check-skip 的日期，默认今天")
    args = ap.parse_args()

    root = repo_root()
    now = datetime.now(CST)
    today = args.date or f"{now:%Y-%m-%d}"

    freq_path = root / ".newsagg" / "frequency.json"
    status_path = root / ".newsagg" / "performance-status.json"
    store_path = root / "reports" / "performance.jsonl"

    freq = {}
    if freq_path.exists():
        try:
            freq = json.loads(freq_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            freq = {}

    # ── 只回答跳过日（run_all.sh 在写作之前问这一句）──
    if args.check_skip:
        skip, why = is_skip_day(freq, today)
        print(json.dumps({"ok": True, "date": today, "skip": skip, "why": why,
                          "downshifted": bool(freq.get("downshifted")),
                          "how_to_clear": HOW_TO_CLEAR if freq.get("downshifted") else None},
                         ensure_ascii=False, indent=2))
        return 0

    pub = _load_sibling_script(".agents/skills/newsagg-publish/scripts/publish.py")

    # ── 读数据 ──
    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        fetch_meta = {"source_file": args.from_file, "attempts": [], "offline": True}
        articles, parse_meta = extract_articles(raw)
    elif args.backend == "api":
        # 纯 HTTP 路径：**不启 Chrome，也不需要串行锁**。
        # 锁是给浏览器命令用的（ADR-0030：整个账号的浏览器命令共用一把），
        # 纯 HTTP 请求不经过浏览器，不该去抢那把锁。
        articles, fetch_meta = fetch_content_list_api(args.account, args.limit)
        parse_meta = fetch_meta
        if not articles:
            # api 失败**不直接判死**——回落浏览器后端再试一次。
            # 理由：api 依赖本机浏览器 cookie（服务器上要注入），注入坏了
            # 是运维问题，不是数据问题；而浏览器后端是更老、更可靠的那条路。
            fb_articles, fb_meta = _fetch_via_browser(pub, root, args.account, args.limit)
            if fb_articles:
                fetch_meta = {"backend": "api", "api_error": fetch_meta.get("error"),
                              "fallback": "browser", "fallback_detail": fb_meta}
                articles, parse_meta = fb_articles, fb_meta
            else:
                fetch_meta = {"backend": "api", "error": fetch_meta.get("error"),
                              "fallback": "browser", "fallback_detail": fb_meta}
    else:
        ok, why = pub.memory_check(args.min_free_mb)
        if not ok:
            result = {"ok": False, "stage": "memory", "reason": "memory_insufficient",
                      "message": why,
                      "note": "内存不足，跳过表现数据读取；不影响发布门的独立预检（ADR-0023）"}
            if not args.dry_run:
                write_json(status_path, {**result, "at": now.isoformat()})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0  # 本脚本永不致命
        articles, fetch_meta = _fetch_via_browser(pub, root, args.account, args.limit)
        parse_meta = fetch_meta

    if not articles:
        result = {"ok": False, "stage": "fetch",
                  "reason": fetch_meta.get("error", "未取到数据"),
                  "detail": fetch_meta,
                  "note": "表现数据读不回来不阻断本轮；简报会呈现本项失败（ADR-0006）"}
        if not args.dry_run:
            write_json(status_path, {**result, "at": now.isoformat()})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    title_index, gid_index, index_meta = local_title_index(root)

    for a in articles:
        # gid 优先（不受输入路径影响），标题兜底（老账本没有 gid）
        slug = gid_index.get(str(a.get("gid") or "").strip()) \
            or title_index.get(a.get("title") or "")
        a["slug"] = slug
        a["matched"] = bool(slug)
        # ADR-0047：只有**文章**算「我们的文章」。微头条即便标题匹配也不算——
        # 它与文章是同一次发布动作的产物，算两篇会让冷启动豁免（5 篇）
        # 与基准都被提前消耗。微头条仍然记录（kind 字段留着），只是不进序列。
        a["ours"] = bool(slug) and a.get("kind") == "article"
        a["date"] = today
        a["collected_at"] = now.isoformat()
        a["source"] = parse_meta.get("source")

    ours = [a for a in articles if a["ours"]]
    weitoutiao = [a for a in articles if a.get("matched") and a.get("kind") != "article"]
    unmatched = [a for a in articles if not a.get("matched")]

    # ── 身份核对：api 与浏览器读的是**两个不同的 Chrome profile** ──
    # api 读**本机真实 Chrome** 的 cookie 库；浏览器后端读 `.home/.toutiao-ops/
    # accounts/default/browser-data`（Playwright 自己的 profile）。实测这两个
    # profile 的 cookie **互相解不开**（Playwright profile 的 Local State 里
    # `os_crypt` 是空 dict），所以它们是两个独立的登录态，可能是两个账号。
    #
    # 判据：如果 api 读回来一堆文章，却**一篇都不是我们的**，那多半不是
    # 「我们没发文章」，而是「登录的是另一个号」。这时用浏览器后端再取一次
    # ——浏览器那条路的账号是确定的（它就是发布时用的那个）。
    if args.backend == "api" and not args.from_file and articles and not ours:
        fb_articles, fb_meta = _fetch_via_browser(pub, root, args.account, args.limit)
        fb_ours = [a for a in fb_articles
                   if (gid_index.get(str(a.get("gid") or "").strip())
                       or title_index.get(a.get("title") or ""))
                   and a.get("kind") == "article"]
        if fb_ours:
            # 浏览器那边认得出我们的文章 → 确认是账号不对，不是我们没发。
            fetch_meta = {**fetch_meta,
                          "api_identity_mismatch": True,
                          "fallback": "browser", "fallback_detail": fb_meta}
            articles = fb_articles
            parse_meta = fb_meta
            for a in articles:
                slug = gid_index.get(str(a.get("gid") or "").strip()) \
                    or title_index.get(a.get("title") or "")
                a["slug"] = slug
                a["matched"] = bool(slug)
                a["ours"] = bool(slug) and a.get("kind") == "article"
                a["date"] = today
                a["collected_at"] = now.isoformat()
                a["source"] = parse_meta.get("source")
            ours = [a for a in articles if a["ours"]]
            weitoutiao = [a for a in articles
                          if a.get("matched") and a.get("kind") != "article"]
            unmatched = [a for a in articles if not a.get("matched")]

    # ── 单篇详细指标（ADR-0068）──
    # `content list` 只给展现与阅读；完读率、平均时长、「超过 N% 同类作品」要逐篇问
    # `item/info`。这四个指标是唯一能把「标题结构」变成可测量对象的东西
    # （ADR-0069 的观察项要靠它们才可能升为硬规则）。
    # 只取**我们的**文章：别人的文章跟标题无关，取了只是浪费接口配额。
    detail_metrics: dict[str, dict] = {}
    detail_meta: dict = {"skipped": "离线模式（--from-file）"}
    if not args.from_file:
        our_gids = [str(a.get("gid")) for a in ours if a.get("gid")]
        if args.backend == "api":
            detail_metrics, detail_meta = fetch_content_details_api(args.account, our_gids)
            if detail_meta.get("error") and not detail_metrics:
                # api 失败 → 回落浏览器后端（与列表同一条降级路径）
                dm, dmeta = fetch_content_details(pub, root, args.account, our_gids)
                if dm:
                    detail_meta = {"backend": "api", "api_error": detail_meta.get("error"),
                                   "fallback": "browser", "fallback_detail": dmeta}
                    detail_metrics = dm
                else:
                    detail_meta = {"backend": "api", "error": detail_meta.get("error"),
                                   "fallback": "browser", "fallback_detail": dmeta}
        else:
            detail_metrics, detail_meta = fetch_content_details(
                pub, root, args.account, our_gids)
        for a in ours:
            m = detail_metrics.get(str(a.get("gid")))
            if m:
                a["detail"] = m

    # ── 落库（每篇每天只留一行；同一天重跑则**就地更新**）──
    # 为什么不追加：一天跑两次不该让同一篇文章在基准里算两遍。
    # 为什么不冻结首行：若冻结，当天第二次运行会拿到凌晨的旧数字，
    # 而"读回最新表现"正是本脚本存在的理由——更新的必须是数字，不是行数。
    existing = read_jsonl(store_path)
    by_key = {(str(r.get("date")), str(r.get("gid"))): i
              for i, r in enumerate(existing)}
    records = list(existing)
    new_records: list[dict] = []
    updated_records: list[dict] = []
    for a in articles:
        k = (str(a.get("date")), str(a.get("gid")))
        i = by_key.get(k)
        if i is None:
            by_key[k] = len(records)
            records.append(a)
            new_records.append(a)
        else:
            # 保留上一轮的 `detail`：离线复算（--from-file）或平台暂时 1003 时
            # 本轮取不到这三个指标，直接整条覆盖会把已经拿到的完读率/时长擦掉。
            # 「就地更新」更新的是**数字**，不是「把没读到的当 0」（ADR-0043 的教训同型）。
            old = records[i]
            if not a.get("detail") and old.get("detail"):
                a["detail"] = old["detail"]
            if old != a:
                records[i] = a
                updated_records.append(a)

    verdict = evaluate(records, window=args.window, coldstart=args.coldstart,
                       low_ratio=args.low_ratio, low_streak=args.low_streak)

    # ── 降频状态：只置位，不自动恢复（ADR-0010）──
    if verdict["status"] == "downshift" and not freq.get("downshifted"):
        freq = {
            "downshifted": True,
            "since": today,
            "reason": verdict.get("reason"),
            "baseline_read_rate": verdict.get("baseline_read_rate"),
            "threshold": verdict.get("threshold"),
            "low_streak": verdict.get("low_streak"),
            "set_at": now.isoformat(),
            "how_to_clear": HOW_TO_CLEAR,
        }
        if not args.dry_run:
            write_json(freq_path, freq)

    if not args.dry_run:
        if new_records or updated_records:
            store_path.parent.mkdir(parents=True, exist_ok=True)
            with store_path.open("w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        write_json(status_path, {
            "ok": True, "at": now.isoformat(), "parse": parse_meta,
            "articles_on_platform": len(articles),
            "articles_matched": len(ours),
            "weitoutiao_matched": [{"title": a.get("title"), "gid": a.get("gid")}
                                   for a in weitoutiao],
            "unmatched": [{"title": a.get("title"), "gid": a.get("gid")} for a in unmatched],
            "new_records": len(new_records),
            "updated_records": len(updated_records),
            "store": str(store_path.relative_to(root)),
            "fetch": fetch_meta,
            "detail": detail_meta,
            # ADR-0071 残余风险 69：注入的 cookie 快过期时，简报要提醒。
            # 判据是「还有几天」，不是「今天有没有失败」——过期前是唯一
            # 能从容处理的时候。
            "cookie_expiry": cookie_expiry_warning(),
        })

    skip, skip_why = is_skip_day(freq, today)
    print(json.dumps({
        "ok": True,
        "date": today,
        "parse": parse_meta,
        "index": index_meta,
        "articles_on_platform": len(articles),
        "articles_matched": len(ours),
        "weitoutiao_matched": [{"title": a.get("title"), "gid": a.get("gid")}
                               for a in weitoutiao],
        "unmatched": [{"title": a.get("title"), "gid": a.get("gid")} for a in unmatched],
        "new_records": len(new_records),
        "updated_records": len(updated_records),
        "dry_run": args.dry_run,
        "verdict": verdict,
        "cookie_expiry": cookie_expiry_warning(),
        "frequency": {"downshifted": bool(freq.get("downshifted")), "since": freq.get("since"),
                      "today_is_skip_day": skip, "why": skip_why},
        "articles": [{"slug": a.get("slug"), "title": a.get("title"), "ours": a["ours"],
                      "kind": a.get("kind"),
                      "published_at": a.get("published_at"), "read_rate": a.get("read_rate"),
                      "metrics": a.get("metrics"), "detail": a.get("detail")} for a in articles],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
