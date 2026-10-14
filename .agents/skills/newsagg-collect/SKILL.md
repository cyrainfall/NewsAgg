---
name: newsagg-collect
description: |
  NewsAgg 采集层：从本地 DailyHotApi 拉取热榜、归一化、按赛道预筛，输出候选条目。
  当需要采集资讯、拉热榜、跑采集层、或为分拣准备候选时使用。
  只做确定性工作（拉取/归一化/关键词预筛/落盘），不做任何判断——判断属于分拣层。
---

# newsagg-collect — 采集层

把资讯流收窄成候选条目。设计依据 [ADR-0016](../../../docs/adr/0016-ingest-and-triage-pipeline-shape.md)、[ADR-0029](../../../docs/adr/0029-collection-layer-findings-and-source-list.md)。

## 前置：热榜服务必须运行

公开实例 `api-hot.imsyy.top` **已不可用**（ADR-0029 实测 HTTP 000），因此依赖本地部署：

```bash
cd reference/DailyHotApi && PORT=6688 npx tsx src/index.ts
```

## 执行

```bash
python3 .agents/skills/newsagg-collect/scripts/collect.py
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--base <url>` | 热榜服务地址，默认 `http://127.0.0.1:6688`（或 `NEWSAGG_HOT_BASE`） |
| `--sources a,b,c` | 指定源，默认 `toutiao,36kr,juejin,csdn,ithome,sspai,github` |
| `--no-prescreen` | 跳过赛道预筛，保留全部条目（用于审计预筛质量） |
| `--stale-minutes N` | 数据陈旧阈值，默认 180 分钟 |

输出：`.newsagg/candidates/<日期>.json`，并在 stdout 打印简报友好摘要。

## 输出形状

```json
{
  "collected_at": "...", "timezone": "Asia/Shanghai",
  "counts": {"fetched": 280, "kept": 107, "dropped": 173,
             "sources_ok": 7, "sources_failed": 0},
  "source_meta": [{"source":"toutiao","shape":"dailyhot","total":50,
                   "from_cache":true,"update_time":"...","age_minutes":11.4,"ok":true}],
  "stale_sources": [],
  "failures": [],
  "items":    [{"source","id","title","hot","url","cover","rank","niche_hits"}],
  "dropped":  [ ... 同上，供审计漏筛 ... ]
}
```

## 硬规则

- **`fromCache: true` 不是陈旧信号。** 它表示"由缓存提供"，是正常且对风控有利的行为。真正的陈旧判据是 `updateTime` 的**年龄**（`age_minutes`）。把 fromCache 当陈旧会产生每日误报。
- **单源失败不中断整轮。** 失败记入 `failures`，并对头条尝试原始接口兜底（形状不同，已归一化）。只有**全部源都失败**才返回退出码 1。
- **两种返回形状必须走各自的归一化函数**（`normalize_dailyhot` / `normalize_raw_toutiao`），不可混用——`id/title` 与 `ClusterId/Title` 直接换用会静默失败。
- **保留 `dropped`。** 赛道预筛存在漏筛与误筛（ADR-0016），`dropped` 是审计窗口，不要丢弃。
- 赛道词在 `platforms/niche.txt`（缺失则用内置默认）。**该文件归本人所有**（ADR-0018），系统不直写。

## 与其他阶段的接口

- 下游是**分拣层**（`newsagg-triage`）：它读 `items`，做事件聚簇与观点卡匹配，产出三态决策。
- 采集层**不做**聚簇、不做判断、不认识观点卡。它只负责"把值得看的东西捞上来"。
