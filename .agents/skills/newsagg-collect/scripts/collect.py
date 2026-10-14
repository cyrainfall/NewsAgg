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
                "hot": raw.get("hot") or raw.get("hotValue"),
                "url": raw.get("url") or raw.get("mobileUrl") or "",
                "cover": raw.get("cover") or "",
                "rank": rank,
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
                "hot": raw.get("HotValue"),
                "url": raw.get("Url") or "",
                "cover": (raw.get("Image") or {}).get("url") if isinstance(raw.get("Image"), dict) else "",
                "rank": rank,
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
    # 内置默认：AI 与开发者工具 / 技术行业
    return [
        "AI", "人工智能", "大模型", "LLM", "GPT", "Claude", "Gemini", "DeepSeek", "Qwen",
        "智能体", "Agent", "机器学习", "深度学习", "神经网络", "训练", "推理", "算力",
        "芯片", "GPU", "英伟达", "NVIDIA", "CUDA", "编程", "代码", "开发", "开发者",
        "开源", "GitHub", "前端", "后端", "全栈", "架构", "数据库", "API", "SDK",
        "Python", "JavaScript", "TypeScript", "Rust", "Go", "Java", "框架", "编译器",
        "云", "DevOps", "容器", "Kubernetes", "Docker", "服务器", "运维", "部署",
        "模型", "算法", "数据", "自动化", "工具", "IDE", "编辑器", "Copilot", "Cursor",
        "Copilot", "插件", "版本", "发布", "开源项目", "技术", "程序员", "工程师",
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


def main() -> int:
    ap = argparse.ArgumentParser(description="NewsAgg 采集层：热榜 → 归一化 → 赛道预筛")
    ap.add_argument("--base", default=os.environ.get("NEWSAGG_HOT_BASE", DEFAULT_BASE))
    ap.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--out", default=None, help="输出路径；默认 .newsagg/candidates/<日期>.json")
    ap.add_argument("--no-prescreen", action="store_true", help="跳过赛道预筛（保留全部条目）")
    ap.add_argument("--stale-minutes", type=float, default=180.0,
                    help="数据超过该分钟数未更新则计为陈旧（默认 180）")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    now = datetime.now(CST)
    all_items, meta, failures = collect(sources, args.base, args.timeout)

    # 为每个源计算新鲜度（ADR-0029 第四条：updateTime 是真正的陈旧信号）
    stale_sources = []
    for m in meta:
        age = source_age_minutes(m, now)
        m["age_minutes"] = round(age, 1) if age is not None else None
        if age is not None and age > args.stale_minutes:
            stale_sources.append({"source": m["source"], "age_minutes": round(age, 1)})

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
        },
        "source_meta": meta,
        "stale_sources": stale_sources,
        "failures": failures,
        "niche_keyword_count": len(keywords),
        "items": kept,
        "dropped": dropped,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 简报友好的摘要
    print(json.dumps({
        "ok": True,
        "out": str(out_path),
        "collected_at": result["collected_at"],
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
