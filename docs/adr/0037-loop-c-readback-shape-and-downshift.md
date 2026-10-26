# 表现数据回路的读回形状与降频判据

## Context

ADR-0010 决定了「表现数据只作软信号」——排序参考 + 简报内容 + 自动降频，并明确**不据此自动降权或退役观点卡**。但那个决定留下的是**契约**，不是实现：它说清了「不做硬规则」，没说清数据从哪条命令取、按什么指标判、阈值怎么定。

本轮接线（循环 C，三条回路里最后一条未接的）在实机探测中发现：**ADR-0010 假设的数据源与平台实际提供的数据对不上**，而 ADR-0030 早已预警过这一点（`analytics works` 的 DOM 噪声「在源头就不可靠」）。若不先钉死形状，实现会照着 ADR-0024 的能力表去用 `content-detail`，然后拿到一个永远为空的字段却毫无察觉。

## 发现

### 一、`content list` 本身就有每篇的计数器——循环 C 不需要 `content-detail`

实机 `node index.js content list --limit 20 --account default`（exit 0）的顶层形状：

```json
{"success": true, "source": "api_intercept", "count": 2, "items": [ ... ]}
```

两个坑：

- **顶层 `count` 是包装元素个数，不是作品数**。实测 `count: 2` 而平台上只有 1 篇作品。
- **`items[1]` 是游标/错误包装**（键 `error_tips`/`error_code`/`error_msg`/`data:{has_more, read_cursor, ...}`），没有 `assembleCell`，遍历时必须跳过。

每篇作品的真实路径：

```
items[0].data[j].assembleCell.itemCell.{articleBase, itemCounter, richContentInfo, ...}
```

其中 `itemCounter` 正是循环 C 需要的**每篇**数据：

```json
{"commentCount": 0, "diggCount": 0, "readCount": 1, "repinCount": 0, "showCount": 234, "videoWatchCount": 0}
```

`articleBase` 提供 `title`、`gidStr`、`publishTime`（epoch，`1791988725` → `2026-10-14T22:38:45+08:00`）、`itemStatus`（20）、`articleURL`。

**即发布反查（门 5）与表现读取是同一条命令**，不需要额外能力。

### 二、`analytics` 两条路都不能用作每篇数据源

| 命令 | 实测结果 | 结论 |
| --- | --- | --- |
| `analytics works --type article --period 7d` | `source: "dom_scrape"`，`metrics` 同时混入白名单指标（`昨日展现量 13`）、**频道来源百分比**（`推荐频道 76.92%`）、**全部省份名**（30+ 个键）与扁平别名 | **账号级聚合**，且噪声比 ADR-0030 记录的更多；无法归因到篇 |
| `analytics content-detail --content-id <gid> --content-type 2` | `{code: 1003, item_data: {}, message: "数据开小差啦~请稍后再试"}` | 上游 API `/mp/agw/statistic/v2/item/info` **返回空数据，不可用** |

第二条**推翻了 ADR-0024 能力表里 `analytics works` / `content-detail` 标 ✅ 已实现的状态**——至少 `content-detail` 名不副实。ADR-0024 该表据此修正。

> **⚠️ 勘误（2026-10-26，ADR-0068）**：上表第二行的**结论是错的**——错在参数，不在接口。
> `--content-type 2` 是 `Video`，而**图文文章是 1**（平台自己的枚举：`{All:0,PgcArticle:1,Video:2,Weitoutiao:3,Wenda:4,ShortVideo:5,Audio:6,PgcGallery:7,Invalid:8}`）。
> 用 `type=1` 调同一个接口返回 `code:0` 与完整数据。CLI 的默认值当时是 2，于是每一篇文章都拿到 1003——
> **而 1003 与「这个作品没有数据」的响应完全相同**，因此被读成了「接口坏了」。
> 本 ADR 的**决策一**（循环 C 的唯一数据源是 `content list`）**仍然成立**、未被推翻：
> `content list` 一次拿到全部篇的展现与阅读，确实比逐篇调 `content-detail` 便宜。
> 被推翻的是「`content-detail` 不可用」这个事实判断，以及据此关掉的那条能力路
> （「读不到完读率 / 停留时长 / 同类百分位」这三个指标其实读得到）。
> 详见 `docs/adr/0068-content-detail-type-param-was-wrong.md`。

`analytics works` 返回的聚合展现量（13）与 `content list` 的每篇展现量（234）口径不同，不能互相印证，也不可混用。

### 三、ADR-0013 的信用分门没有任何实现路径

ADR-0013 明写「信用分监控：每次发布后读回信用分；低于阈值即提前熔断。**这是唯一能在封号前踩刹车的机制**」。

实测：

```bash
grep -rln '信用分\|creditScore\|credit_score' reference/toutiao-ops/   # 排除 node_modules → 空
grep -rn 'credit\|信用\|score' reference/toutiao-ops/cli/src/*.js      # → 空
```

`analytics` 下也没有任何 credit 类命令。**该安全门目前不存在**，不是「未接线」而是「无数据源」。这与循环 C 同属浏览器读回，故记录在此。

## Decision

**一、循环 C 的唯一数据源是 `content list`。** 不使用 `analytics works`（聚合 + DOM 噪声），不使用 `content-detail`（上游空数据）。归一化函数按上述路径取 `itemCounter` 与 `articleBase`，并显式跳过游标包装元素。

**二、判据用阅读率（`readCount / showCount`），不用阅读量绝对值。** 理由：阅读量受平台推荐权重支配，新号无权重时必然低，会把「平台没推」误判成「内容差」；阅读率直接度量「被展现后是否被点开」，正是标题与封面质量的病灶（与 m01997 对首篇的归因一致：展现 234 / 阅读 1 = 0.43%）。

**三、展现为 0 时阅读率记 `null`，不记 0。** 否则「没被展现」会混进「被展现了但没人点」的统计，把两种不同的失败合并。

**四、基准取最近 N 篇（默认 5）的中位数，阈值 = 基准 × 0.5，连续 3 篇低于阈值即降频。** 这些数值是**经验值而非推导值**（沿用 ADR-0014 的做法：记录在此以便复核，而不是因为经过了论证）。

边界情形必须显式处理：**基准为 0 时判据是 `rate <= 0` 而非 `rate < threshold`**——后者在 `threshold == 0` 时恒为假，会让一串零阅读的文章**永远不触发降频**。实测该情形现正确报 `downshift`。

**五、冷启动豁免 5 篇。** 评估篇数 < 5 时不建立基准、不判降频。这是 ADR-0010 的明确要求（新号首周数据几乎必然很低，首周判据是链路是否跑通与本人是否愿意署名）。

**六、降频只置位，不自动恢复。** 触发时写 `.newsagg/frequency.json`；`run_all.sh` 据此隔日跳过。**解除 = 人工删除该文件**（ADR-0010：「恢复同样需要人工确认」）。跳过日只出简报，且简报 ① 段必须显式写「降频跳过日」——否则会与 ADR-0019 要求区分开的「系统没跑」混淆。

**七、复用发布层的串行锁，不复制。** `performance.py` 通过 `importlib` 加载 `publish.py`，直接用它的 `FileLock`、`run_toutiao`、`classify_failure`。理由：ADR-0030 要求**整个账号的浏览器命令共用串行化**；复制一份实现就会产生第二把锁文件，两把锁互不感知 = 没有串行。只读命令按 ADR-0011 可重试 3 次，并发冲突不算失败。

**八、本脚本永不致命。** `run_all.sh` 以 `|| true` 调用它。表现数据读不回来不能拖垮整轮运行——简报必须无条件每日存在（ADR-0006），否则看守无法区分「系统没跑」与「数据读不到」，那是比丢失数据更坏的故障。失败写 `.newsagg/performance-status.json`，由简报 ⑤ 段呈现。

**九、匹配靠发布账本，不靠本地标题。** 本人若在平台改过标题，本地成稿的一级标题已是新标题，而平台上那篇仍是发布当时的旧标题；只用 `outputs/*.md` 匹配会把自己唯一发出去的文章匹配不上。账本记的正是发布当时的标题（`publish.py` 写入），这是唯一可靠的关联。

**十、历史观测入 `reports/performance.jsonl`（入库）。** 与 `.newsagg/` 下运行时状态不同，历史表现是**基准的来源**，跨环境丢失会让基准从头积累；按 ADR-0018 的归属原则，冻结成结论的产物入 `reports/`。

**十一、每篇每天只留一行，同一天重跑则就地更新该行。** 不追加（否则一天跑两次会让同一篇文章在基准里算两遍），也不冻结首行（否则当天第二次运行会拿到凌晨的旧数字，而"读回最新表现"正是本脚本存在的理由——**更新的必须是数字，不是行数**）。实测重跑输出 `updated_records: 1`、文件行数不变而 `showCount` 由 234 刷新为 239。

**十二、信用分门本轮不实现，记入残余风险。** 循环 C 不因此阻塞——信用分是独立的安全门，与表现数据无数据依赖，另开一轮处理。ADR-0013 的该条**当前无实现路径**，必须显式记录而非留作默认成立。

**十三、发布账本必须记下 `card_file`。** 接无人值守编排时实测发现：账本记录只有 `at/slug/title/mode/steps/stdout_tail/snapshot/verdict`，**没有"这篇属于哪张卡"**。而 ADR-0010 要的排序参考是"**哪些卡**跑出了数据"——缺这一字段，读回来的数据只能停在文章粒度，归因无从落地。故 `publish.py` 新增 `--card-file`，由 `run_all.sh` 从决策文件取 `card_file` 一并写入账本（dry-run 也记，便于验证）。

归因读取顺序**账本优先、决策文件兜底**：账本记的是发布那一刻的事实、不随日期漂移；决策文件属运行时状态（`.newsagg/`，ADR-0027 不入库），可能被清理或跨环境不同步，只用它会静默丢归因。实测账本写入后 `performance_reference` 正确报出 `by_card: {llm-price-war-signals-commoditization.md: [...]}`、`unlinked_articles: 0`。

## Consequences

**降频的告警责任被加重。** ADR-0010 已指出自动降频使系统在变差时**变得更不活跃**，产出下降本身会掩盖问题的严重性。现在这条路径真实存在了，因此简报 ⑤ 段必须无条件呈现降频状态与解除方法——否则系统会静默滑向长期停摆，而这恰恰是 ADR-0010 警告的失效模式。

**`analytics content-detail` 的不可用不会阻塞任何东西**，因为循环 C 已经改走 `content list`。但它意味着 ADR-0024 能力表需要按本条修正：标注 ✅ 的命令不等于可用，**能力表是"接口存在"的证据，不是"数据可用"的证据**。

**信用分门缺失改变了风险画像。** ADR-0001 接受账号风险的前提之一是「有信用分监控可以在封号前踩刹车」（ADR-0013）。该机制现在**不存在**，因此「接受封号风险」这一决定的支撑比原先记录的更弱。这不是要推翻 ADR-0001（用户已知悉并坚持），而是必须承认：当前系统对账号健康**没有主动感知能力**，只能靠发布反查的 `rejected` 结果被动发现。

**两个阈值仍是空配置项。** ADR-0016 决定聚簇置信度与卡匹配度阈值「上线后用真实数据实测选定」，本 ADR 的降频参数按同一策略处理：当前值是经验值，冷启动期结束后应据实际阅读率分布复核。**尤其 `low_ratio 0.5` 在样本量小时极易被单篇波动触发**——若首批文章阅读率方差很大，应先放宽该比例再谈降频。

**`.newsagg/` 的入库矛盾需要单独处理。** 实测 `git ls-files .newsagg/` 有 5 个文件已被追踪（`bodies/` 2 个、`candidates/` 1 个、`triage/` 2 个），它们在 `.gitignore` 生效前加入，而 gitignore 对已追踪文件无效。这与 ADR-0027「双环境各自持有运行时状态，不入库以免冲突」直接冲突——两个环境都会写这些路径。本条 ADR 的新增状态文件（`performance-status.json`、`frequency.json`）**未入库**，符合 ADR-0027；那 5 个既有文件需另行处置。
