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
| `--no-merge` | 不做同日累积，直接覆盖写（逃生开关，默认关闭） |
| `--stale-minutes N` | 数据陈旧阈值的**下限**，默认 180 分钟。实际阈值按源自身刷新周期取 `max(该值, TTL × 1.5)`（ADR-0043）——传得比下限小**没有效果**，传得比下限大才生效（放宽） |

输出：`.newsagg/candidates/<日期>.json`，并在 stdout 打印简报友好摘要。

## 同日多次采集（ADR-0006 / ADR-0038）

白天按 `08:00 / 13:00 / 18:00` 采三次，**同日结果累积合并**，不是覆盖写。
合并规则（`merge_candidates`）：

- 条目按 `(source, id)` 归并；**保留 `first_seen`**（第一次被看到的时间）。
- 热度轨迹记进 `hot_history`——「发酵过程」正是这些中间值，而不是最后一个数字。
- 上一轮见过、这一轮没再出现的条目**保留并标 `seen_in_last_run: false`**，
  不静默丢弃：「掉出榜」本身就是信息。
- 每次采集追加一条 `collection_runs`，供简报 ② 段显示「今天采了几次、每次新进来多少条」。
  其中 `kept_new` 按 `first_seen == 本次采集时刻` 计数，**不等于本次的 `kept`**——
  第二次采集时预筛仍会"保留 100 条"，但可能全是旧的，取 `kept` 会把"没有新东西"
  显示成"采到 100 条新的"（ADR-0039）。

> ✅ `hot_history` 的消费者已接上（[ADR-0056](../../../docs/adr/0056-trend-signal-is-same-source-rank-percentile.md)）：
> 分拣按同源同快照内的**位次趋势**排序（`climbing` 优先于已很高但 `steady`/`cooling`），
> 不再只看"当前热度"。为此**观测现在会带 `rank` 与 `rank_total`**——上游位次和
> **榜单深度**（`payload.total`）一起原样写进每条观测。只记 `hot` 的话位次变化无从计算；
> 只记 `rank` 而不记深度，下游就只能拿"池内名次"归一，而池子只是赛道预筛后的子集、
> 条数会变（实测 toutiao 一个下午 8→7→5），池内名次会随池子缩水被拉伸，把**退步**读成
> **爬升**（`rank` 22→25 曾被判 `climbing`）。另外 `hot` 跨源不可比（同一天 toutiao 到
> 千万级、36kr 只有 2–44）。
>
> 存量观测（ADR-0056 之前落盘的）**没有 `rank` / `rank_total`**，下游的降级方式是：
> 同一快照内按 `hot` 降序重排得到序位，再按条目数归一（池内相对量，只是近似）。两个来源**在一条轨迹内不混用**（不是按快照——
> 换尺日会让前几次观测只有 hot、最后一次有 rank，按快照统一就会跨尺相减）。注意这条降级路径
> 不是临时补丁——`hot` 与 `rank` 在同源内只是粗略相关（10-23 36kr 前 12 名
> `(1,19) (2,40) (2,42) (3,44) (4,10) (5,14) (7,20) (7,22) (8,9) (8,22) (9,10) (11,3)`，
> 有并列、有乱序），所以新采集必须写 `rank`，重排只是**没有位次时的近似**。
>
> 另：改观测格式时**必须同时改 `normalize_hot_history`**——它重建字典、只保留
> `at`/`hot`/`hot_raw`，加字段而不同步它，下一次合并就会把新字段抹掉（残余风险 24 的
> 第三次同型，前两次是 ADR-0042 与 ADR-0043）。

## 输出形状

```json
{
  "collected_at": "...", "timezone": "Asia/Shanghai",
  "counts": {"fetched": 280, "kept": 107, "dropped": 173,
             "sources_ok": 7, "sources_failed": 0},
  "source_meta": [{"source":"toutiao","shape":"dailyhot","total":50,
                   "from_cache":true,"update_time":"...","age_minutes":11.4,
                   "stale_threshold_minutes":180.0,"ok":true}],
  "stale_sources": [{"source":"github","age_minutes":2300.0,"threshold_minutes":2160.0}],
  "failures": [],
  "items":    [{"source":"juejin","id":"...","title":"...","hot":51000,
                "url":"...","cover":"...","rank":3,"rank_total":50,"niche_hits":["..."],
                "first_seen":"...","seen_in_last_run":true,
                "hot_history":[{"at":"2026-10-23T08:02:11+08:00","hot":51000,
                                "rank":3,"rank_total":50}]}],
  "dropped":  [ ... 同上，供审计漏筛 ... ]
}
```

## 硬规则

- **`fromCache: true` 不是陈旧信号。** 它表示"由缓存提供"，是正常且对风控有利的行为。真正的陈旧判据是 `updateTime` 的**年龄**（`age_minutes`）。把 fromCache 当陈旧会产生每日误报。
- **陈旧阈值按源自身的刷新周期算，不是全局统一值。** `max(--stale-minutes, TTL × 1.5)`：github 的 TTL 是 24h（其余 1h），用全局 3 小时判它会让它每个自然日约 21 小时都"陈旧"——**每日必然触发的告警会被忽略**（ADR-0043）。`source_meta` 与 `stale_sources` 都带上实际判定阈值，便于解释"为什么它算陈旧"。
- **`hot` 一律是 `int` 或 `None`，不可是字符串。** github 上游给的是带千分位的 `'40,798'`，其余六源是 `int`；不归一化会让下游 `sorted(key=hot)` 抛 `TypeError`。归一化在入口做，存量轨迹由 `normalize_hot_history` 补齐（旧值留在 `hot_raw`）。
- **单源失败不中断整轮。** 失败记入 `failures`，并对头条尝试原始接口兜底（形状不同，已归一化）。只有**全部源都失败**才返回退出码 1。
- **两种返回形状必须走各自的归一化函数**（`normalize_dailyhot` / `normalize_raw_toutiao`），不可混用——`id/title` 与 `ClusterId/Title` 直接换用会静默失败。
- **保留 `dropped`。** 赛道预筛存在漏筛与误筛（ADR-0016），`dropped` 是审计窗口，不要丢弃。
- 赛道词在 `platforms/niche.txt`（缺失则用内置默认）。**该文件归本人所有**（ADR-0018），系统不直写。

## 与其他阶段的接口

- 下游是**分拣层**（`newsagg-triage`）：它读 `items`，做事件聚簇与观点卡匹配，产出三态决策。
- 采集层**不做**聚簇、不做判断、不认识观点卡。它只负责"把值得看的东西捞上来"。
- **编排入口是 `run_all.sh`，不是 `collect.py`。** 无人值守下白天三次只跑
  `bash .agents/skills/newsagg-collect/scripts/run_all.sh --collect-only`（纯脚本、不碰 git）；
  21:00 的主流程才跑 `--no-collect` / `--after-triage` / `--after-write` 三段（ADR-0038）。
  单独跑 `collect.py` 仅用于调试。

- **`run_all.sh` 自己钉住解释器，不要绕过它直接调脚本。** 开头有两处硬编码：
  `COVER_PY`（`.uv-tools/wewrite/bin/python`，Pillow 只装在那里，ADR-0039）与
  `node_dir()`（`~/.nvm/versions/node/v22.23.3/bin`，发布与表现数据都靠 node，
  ADR-0052）。**DSH 定时任务的 shell 不加载 `~/.zshrc` 里的 nvm init**，所以
  那里 `node` 不在 PATH——手动在交互式 shell 里跑通，不代表定时任务能跑通。
  2026-10-21 就是因为这个，发布直接崩、表现数据静默丢了两天（ADR-0052）。
  所以：**调试可以手跑单脚本，但验证必须走 `run_all.sh`**。
