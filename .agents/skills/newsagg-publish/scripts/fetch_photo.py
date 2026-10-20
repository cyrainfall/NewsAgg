#!/usr/bin/env python3
"""从 Wikimedia Commons 取一张**许可证明确、可无人值守使用**的照片，作封面背景。

设计依据 ADR-0048（封面照片的来源与许可证边界）、ADR-0013（不使用新闻原图）、
ADR-0044（封面承载一个视觉论据）。

三条硬约束，缺一条就不取图：

1. **只取不需要署名的许可证**（CC0 / Public domain）。CC-BY 系列要求署名，
   而无人值守的发布链没有「记得署名」这个环节——忘一次就是违约一次。
   把「必须署名」的图排除在候选之外，比事后提醒可靠。
2. **相关性可判定**。Commons 的搜索结果里混着地图、旗帜、logo、人物肖像、
   新闻截图。实测搜 `DeepSeek` 返回的第一页里有《DeepSeek – VR China und Taiwan》
   （一张中台地图）——这类图发到头条是政治风险，不是配图。所以要求查询词
   至少命中一个，且标题/描述/分类不含风险词。
3. **失败绝不阻断发布**。取不到合格照片就返回 ok=false，编排器回退到纯模板封面。

许可证与来源**全部记账**（`.newsagg/photos/ledger.jsonl`），因为「我们当时以为
它是 CC0」不是抗辩理由，账本才是。

⚠️ User-Agent 必须带联系方式。实测用 `NewsAgg/1.0 (personal news digest)` 连续
请求会收到 **HTTP 429 Too Many Requests**，换成带 URL 的 UA 后恢复 200。
Commons 对匿名批量的限流很敏感，而限流失败在这里会退化成「没有照片」——不致命，
但会让这个功能时灵时不灵。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
API = "https://commons.wikimedia.org/w/api.php"
UA = "NewsAggBot/1.0 (https://github.com/cyrainfall/NewsAgg) python-urllib"

# 不需要署名的许可证。用 LicenseShortName 的小写前缀判定，同时用机器可读的
# License（如 cc0、pd）兜底——两个字段来自同一份 extmetadata，互为校验。
FREE_PREFIXES = ("cc0", "public domain", "pd-", "pd ")
FREE_IDS = {"cc0", "cc-zero", "pd", "publicdomain"}

# 风险词：命中即排除。这不是审美偏好，是发布风险——地图/旗帜涉及政治，
# 商标与人物肖像涉及另一套权利（CC0 只放弃版权，不放弃人格权与商标权）。
# ⚠️ 「china」**不在**这张表里：我们的赛道就是中国 AI 公司，把它列进去会把
# 绝大多数合法候选筛掉。真正的政治风险是**领土/政治人物**类素材，由 map /
# taiwan / tibet / xinjiang / hong kong / political 这些更窄的词承担——
# 实测那条《DeepSeek – VR China und Taiwan, heikel》（中台地图）被 taiwan 命中。
RISK_WORDS = (
    "map", "maps", "flag", "flags", "logo", "logos", "coat of arms",
    "portrait", "face", "faces", "nude", "nudity", "protest", "riot",
    "war", "military", "weapon", "soldier", "election", "political",
    "president", "minister", "parliament", "taiwan", "tibet",
    "xinjiang", "hong kong", "screenshot", "screengrab", "meme",
    "trademark", "poster", "cover art", "album",
)

# 只接受位图；PDF/SVG 要么是文档要么是矢量，都不适合当照片底。
OK_MIME = ("image/jpeg", "image/png", "image/webp")

MIN_W = 1200
MIN_H = 560
MIN_ASPECT = 1.15  # 太窄的竖图裁成 16:9 会只剩一条


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def api_get(params: dict, timeout: int = 25, tries: int = 3) -> dict:
    """带退避重试的 GET。429/5xx 是限流与瞬时故障，重试有意义。"""
    url = f"{API}?{urllib.parse.urlencode(params)}"
    delay = 2.0
    last: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < tries:
                print(f"Commons 返回 {e.code}，{delay:.0f}s 后重试（第 {attempt}/{tries} 次）",
                      file=sys.stderr)
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except Exception as e:  # 网络抖动
            last = e
            if attempt < tries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Commons 请求失败：{last}")


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def license_is_free(em: dict) -> tuple[bool, str]:
    short = ((em.get("LicenseShortName") or {}).get("value") or "").strip()
    ident = ((em.get("License") or {}).get("value") or "").strip().lower()
    low = short.lower()
    if low.startswith(FREE_PREFIXES) or ident in FREE_IDS:
        return True, short or ident
    return False, short or ident or "（未标注）"


def risk_hit(text: str) -> str | None:
    low = text.lower()
    for w in RISK_WORDS:
        if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
            return w
    return None


def query_tokens(query: str) -> list[str]:
    """查询词切成相关性判定用的 token。中文按原样整词，英文按空白切。"""
    raw = [t for t in re.split(r"[\s,，、]+", query.strip()) if t]
    out = []
    for t in raw:
        # 单个字符的词（如「的」）不参与判定，会命中一切
        if len(t) >= 2:
            out.append(t.lower())
    return out


def score_candidate(pg: dict, ii: dict, em: dict, tokens: list[str]) -> tuple[int, dict]:
    """打分。返回 (分数, 说明)。分数 <= 0 即不合格。"""
    title = pg.get("title") or ""
    desc = strip_html((em.get("ImageDescription") or {}).get("value") or "")
    cats = strip_html((em.get("Categories") or {}).get("value") or "")
    hay = f"{title} {desc} {cats}".lower()

    hits = [t for t in tokens if t in hay]
    if not hits:
        return 0, {"reason": "与查询词无任何交集"}

    risk = risk_hit(hay)
    if risk:
        return 0, {"reason": f"命中风险词 {risk!r}"}

    w = ii.get("width") or 0
    h = ii.get("height") or 0
    if (ii.get("mime") or "") not in OK_MIME:
        return 0, {"reason": f"格式不适合（{ii.get('mime')}）"}
    if w < MIN_W or h < MIN_H:
        return 0, {"reason": f"尺寸不足（{w}x{h}）"}
    if h and (w / h) < MIN_ASPECT:
        return 0, {"reason": f"太窄（{w}x{h}）"}

    # 相关性权重最高：命中 token 越多越好；其次偏好横向大图
    score = 10 * len(hits)
    score += min(6, int((w / max(h, 1)) * 2))       # 横向加分
    score += min(4, int(w / 1000))                   # 分辨率加分
    return score, {"reason": "ok", "hits": hits, "w": w, "h": h}


def fetch_photo(query: str, out_dir: Path, *, limit: int = 30, min_score: int = 10) -> dict:
    """按 query 取一张合格照片。返回 dict，ok=False 时附带原因（调用方应回退）。"""
    tokens = query_tokens(query)
    if not tokens:
        return {"ok": False, "reason": "查询词太短（切不出判定用 token）", "query": query}

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1600",
    }
    try:
        data = api_get(params)
    except Exception as e:
        return {"ok": False, "reason": f"Commons 请求失败：{e}", "query": query}

    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return {"ok": False, "reason": "Commons 无搜索结果", "query": query}

    ranked: list[tuple[int, dict, dict, dict, str]] = []
    rejected: list[dict] = []
    for _pid, pg in pages.items():
        ii = (pg.get("imageinfo") or [{}])[0]
        em = ii.get("extmetadata") or {}
        free, lic = license_is_free(em)
        if not free:
            rejected.append({"title": pg.get("title"), "reason": f"许可证需要署名或不明：{lic}"})
            continue
        sc, why = score_candidate(pg, ii, em, tokens)
        if sc < min_score:
            rejected.append({"title": pg.get("title"), "reason": why.get("reason")})
            continue
        ranked.append((sc, pg, ii, em, lic))

    if not ranked:
        return {
            "ok": False,
            "reason": f"{len(pages)} 个候选全部不合格",
            "query": query,
            "rejected_sample": rejected[:6],
        }

    ranked.sort(key=lambda x: -x[0])
    sc, pg, ii, em, lic = ranked[0]
    url = ii.get("thumburl") or ii.get("url")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Referer": "https://commons.wikimedia.org/"}
        )
        with urllib.request.urlopen(req, timeout=40) as r:
            blob = r.read()
    except Exception as e:
        return {"ok": False, "reason": f"下载失败：{e}", "query": query}

    sha = hashlib.sha256(blob).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{sha[:12]}.img"
    dest.write_bytes(blob)

    record = {
        "at": datetime.now(CST).isoformat(timespec="seconds"),
        "query": query,
        "score": sc,
        "hits": [t for t in tokens if t in (
            f"{pg.get('title','')} "
            f"{strip_html((em.get('ImageDescription') or {}).get('value') or '')} "
            f"{strip_html((em.get('Categories') or {}).get('value') or '')}"
        ).lower()],
        "file_title": pg.get("title"),
        "license": lic,
        "license_url": (em.get("LicenseUrl") or {}).get("value"),
        "artist": strip_html((em.get("Artist") or {}).get("value") or ""),
        "credit": strip_html((em.get("Credit") or {}).get("value") or ""),
        "descriptionurl": ii.get("descriptionurl"),
        "width": ii.get("width"),
        "height": ii.get("height"),
        "sha256": sha,
        "bytes": len(blob),
        "local": str(dest),
        "candidates_total": len(pages),
        "candidates_qualified": len(ranked),
    }
    ledger = out_dir / "ledger.jsonl"
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"ok": True, **record}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 Wikimedia Commons 取一张 CC0/公有领域照片作封面背景（ADR-0048）"
    )
    ap.add_argument("--query", required=True, help="搜索词（英文效果更好，如 'data center servers'）")
    ap.add_argument("--out-dir", default=None, help="下载目录（默认 .newsagg/photos）")
    ap.add_argument("--limit", type=int, default=30, help="候选条数")
    ap.add_argument("--min-score", type=int, default=10, help="合格分数线")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else repo_root() / ".newsagg" / "photos"
    info = fetch_photo(args.query, out_dir, limit=args.limit, min_score=args.min_score)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    # 取不到照片**不是错误**：调用方回退到纯模板封面。退出码 0 让编排器继续。
    return 0


if __name__ == "__main__":
    sys.exit(main())
