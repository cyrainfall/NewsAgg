---
name: newsagg-performance
description: |
  NewsAgg 表现数据回路（循环 C）：经浏览器会话读回每篇文章的展现/阅读，
  与**自身基准**对比，驱动自动降频，并把结果交给分拣做排序参考、交给简报呈现。
  当需要看文章数据、回填表现历史、判断是否该降频、或查看系统表现时使用。
  这是软信号——**不据此自动降权或退役观点卡**（ADR-0010）。
---

# newsagg-performance — 表现数据回路（循环 C）

把「获得关注」这个原始目标接上执行器。设计依据 [ADR-0010](../../../docs/adr/0010-performance-data-feeds-back-as-soft-signal.md)、[ADR-0030](../../../docs/adr/0030-toutiao-ops-login-and-readback-findings.md)、[ADR-0037](../../../docs/adr/0037-loop-c-readback-shape-and-downshift.md)。

## 执行

```bash
# 读回 + 落库 + 算基准 + 判降频（默认 api 后端：纯 HTTP，不需要 Chrome）
python3 .agents/skills/newsagg-performance/scripts/performance.py

# 只算不写（复算、调试）
python3 .agents/skills/newsagg-performance/scripts/performance.py --dry-run

# 强制走浏览器后端（能看见微头条，但单次读回吃 1GB+ 内存）
python3 .agents/skills/newsagg-performance/scripts/performance.py --backend browser

# 解析已保存的 content list JSON，完全不联网
python3 .agents/skills/newsagg-performance/scripts/performance.py --from-file /tmp/list.json

# 只回答「今天是不是降频跳过日」（run_all.sh 用）
python3 .agents/skills/newsagg-performance/scripts/performance.py --check-skip
```

## 后端：默认 api（纯 HTTP）

`--backend api`（默认）| `browser`。ADR-0071 决定默认 api，因为**服务器只有 2GB 内存**，
而浏览器后端单次读回就吃 1GB+（Playwright 三进程 403+231+207MB + Chrome 宿主 215MB）。

两条路的读数**逐篇相等**（10/10 篇展现与阅读都相等），性能 2.0s vs 20.14s。

**browser 后端保留的理由**：只有它能看见微头条——纯 HTTP 的
`/mp/agw/article/list` 里 `article_type` 全是 0，微头条根本不在这个数据源里，
ADR-0047 的泄漏判据在 api 路径下会失效。

**服务器上用注入的 cookie**：`NEWSAGG_COOKIE_FILE=/path/to/cookies.json`
（优先级：环境变量 > 本机 Chrome）。文件权限必须 0600，否则拒绝加载。
导出：`python3 scripts/export-cookies.py --browser chrome --out <path>`。
cookie 主体约 23 天有效，**每月要重新导出一次**。

**快过期会报出来（ADR-0072）**：每次读表现数据都检查注入文件的
`earliest_expires_at`，不足 7 天记 `cookie_expiry.level = "expiring"`、
已过期记 `"expired"`，简报第⑤段会打印重新导出的命令。
没有注入文件（本机直读 Chrome）时不报——那里 cookie 由浏览器管。

api 后端失败会**自动回落浏览器后端**并记 `fallback: "browser"`。


## 数据从哪来

**默认用 api 后端**（纯 HTTP 作品列表 + 逐篇 `item/info`），`--backend browser` 时用
`content list`，不用 `analytics works`。这是实测结论：

| 命令 | 结论 |
| --- | --- |
| 纯 HTTP `/mp/agw/article/list` | ✅ 逐篇有展现/阅读/点赞/评论，**不需要浏览器**（ADR-0071） |
| 纯 HTTP `/mp/agw/statistic/v2/item/info?type=1` | ✅ 额外给出完读率、平均时长、「超过 N% 同类作品」 |
| `content list`（浏览器） | ✅ 逐篇 `itemCounter`，但**只有它能看见微头条** |
| `analytics works` | ❌ 只有**账号级聚合**，且 `source: dom_scrape`，`metrics` 里混着省份名与频道来源百分比 |
| `analytics content-detail` | ✅ **可用**，但**默认 `--content-type` 曾传错**（2=视频，文章是 1）→ 传错时接口返回 1003，与「没有数据」长得一模一样，ADR-0037 因此误判它不可用（ADR-0068 勘误） |

原始信封形状见 [ADR-0030](../../../docs/adr/0030-toutiao-ops-login-and-readback-findings.md)（三个形状）与 [ADR-0037](../../../docs/adr/0037-loop-c-readback-shape-and-downshift.md)：作品在 `items[0].data[j].assembleCell.itemCell`，顶层 `count` 是包装元素数而**不是作品数**。

⚠️ 纯 HTTP 列表**含草稿**（实测 16 条里 5 条），而浏览器那条不含——api 后端显式过滤草稿并在 `meta.drafts_skipped` 里记数，否则两条路的样本集不同、**基准不可比**。


## 指标与判据

- **判据用阅读率**（`readCount / showCount`），不用阅读量绝对值。理由：阅读量受平台推荐权重支配，新号无权重时必然低，会把「平台没推」误判成「内容差」；阅读率直接度量「被展现后是否被点开」，正是标题/封面质量的病灶（见 m01997 的归因）。
- **展现为 0 时阅读率记 `null`**，不记 0——否则「没被展现」会混进「被展现了但没人点」的统计。
- **基准取最近 N 篇（默认 5）的中位数**，阈值 = 基准 × `low_ratio`（默认 0.5），连续 `low_streak`（默认 3）篇低于阈值即降频。
- 基准为 0 时（一串零阅读）仍必须能触发——判据是 `rate <= 0`，而不是 `rate < threshold`（后者恒为假，等于永不降频）。
- 第三方作品（非本账号）**不参与**基准计算。

## 冷启动豁免

已评估篇数 < `coldstart`（默认 5）时**不建立基准、不判降频**。这是 ADR-0010 的明确要求：新号首周数据几乎必然很低，首周的成败判据是**链路是否跑通与本人是否愿意署名**，不是阅读数据。

## 降频与恢复

- 触发时写 `.newsagg/frequency.json`，`run_all.sh` 据此**隔日跳过**（降频当日仍发文，其后奇数日跳过）。
- **只置位，不自动恢复**（ADR-0010：「恢复同样需要人工确认」）。解除 = 人工确认表现已恢复后删除该文件。
- 跳过日**只出简报**，不采集、不写作、不发布；简报 ① 段必须显式写「降频跳过日」——否则会与「系统没跑」无法区分。

## 硬规则

1. **本脚本永不致命。** `run_all.sh` 以 `|| true` 调用它。表现数据读不回来绝不能拖垮整轮运行——简报必须无条件每日存在（ADR-0006），否则看守无法区分「系统没跑」与「数据读不到」。失败写 `.newsagg/performance-status.json`，由简报 ⑤ 段呈现。

2. **必须复用发布层的串行锁。** 本脚本导入 `newsagg-publish/scripts/publish.py` 用它的 `FileLock`、`run_toutiao`、`classify_failure`，**不复制**这些实现。理由：ADR-0030 要求整个账号的浏览器命令共用串行化；复制一份就会产生第二把锁文件，两把锁互不感知 = 没有串行。

3. **只读可重试 3 次，并发冲突不算失败。** 只读命令属 ADR-0011 的「可自动重试」一类；并发冲突（`Target page, context or browser has been closed` 等）是瞬时竞态，重试即可，不是失败。

4. **匹配靠发布账本，不靠本地标题。** 本人在平台改过标题之后，本地成稿的一级标题已是新标题，而平台上那篇仍是发布当时的旧标题。只用 `outputs/*.md` 匹配会把自己唯一发出去的文章匹配不上。账本里记的正是发布当时的标题（`publish.py` 写入），这是唯一可靠的关联。

5. **软信号，不设硬规则。** 分拣拿到的 `performance_reference` 只作「卡匹配度 × 事件热度」相当时的排序参考，**不参与打分、不触发降权或退役**。卡片历史表现差不等于该卡该退役——每日一篇的样本量下，把噪音升成硬规则会适得其反。

6. **降频必须告警。** 自动降频使系统在变差时变得更不活跃，产出下降本身会掩盖问题的严重性。若不在简报 ⑤ 段告警，系统会静默滑向长期停摆。

## 产物

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `reports/performance.jsonl` | 系统（**入库**） | 历史观测，**每篇每天一行**；同一天重跑就地更新该行（不追加，避免基准里重复计一篇；也不冻结首行，否则第二次运行会拿到旧数字）。入库是为了跨环境不丢基准（ADR-0018） |
| `.newsagg/performance-status.json` | 运行时（不入库） | 本次读回的结果/失败，供简报读取 |
| `.newsagg/frequency.json` | 运行时（不入库） | 降频状态；人工删除即解除 |

## 与其他阶段的接口

- **上游**：发布（`newsagg-publish`）——共用浏览器会话与锁；账本提供标题关联。
- **下游**：分拣（`newsagg-triage` 的 `performance_reference` 排序参考）、简报（`newsagg-brief` 的 ③/⑤ 段）、编排（`run_all.sh` 的跳过日门）。
- **缺口**：ADR-0013 要求「每次发布后读回信用分」，但 vendored 的 toutiao-ops **没有任何信用分命令**（已核实 `src/` 与 `index.js` 里 `credit|信用|score` 为空）。该项未实现，记在残余风险中。
