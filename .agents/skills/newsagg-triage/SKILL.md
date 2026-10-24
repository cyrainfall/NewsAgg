---
name: newsagg-triage
description: |
  NewsAgg 分拣层：把采集到的候选条目聚成事件，匹配观点卡，产出三态决策
  （跳过 / 续写 / 新写）或弃权。当需要判定"什么值得写"、跑分拣、或做取舍判定时使用。
  这是本项目相对 ybrain 的唯一新增能力（ybrain 刻意没有机器分拣层）。
---

# newsagg-triage — 分拣层

把候选条目收窄成一个可写作的选题，或明确弃权。设计依据 [ADR-0007](../../../docs/adr/0007-opinion-cards-as-cognitive-asset.md)、[ADR-0016](../../../docs/adr/0016-ingest-and-triage-pipeline-shape.md)、[ADR-0019](../../../docs/adr/0019-triage-edge-cases-and-brief-structure.md)。

## 分工

**脚本做确定性工作，你做判断。**

```bash
# 1. 组装输入包（读全部 active 卡 + 候选 + 冷却状态 + 限额）
python3 .agents/skills/newsagg-triage/scripts/triage.py
# → .newsagg/triage/<日期>-input.json

# 2. 你阅读输入包，产出决策（写在 triage/ 下）
# 3. 校验你的决策（结构 + 冷却期 + 限额）
python3 .agents/skills/newsagg-triage/scripts/triage.py --validate <决策文件>
```

## 你必须做的判断

读 `.newsagg/triage/<日期>-input.json`，它包含 `candidates`（条目）、`cards`（全部 active 卡，含正文）、`cooldown_state`、`limits`。

### 第一步：聚簇

把描述**同一现实事件**的条目合成一个事件聚簇。聚簇是判定"是否值得写"的最小单位——单条报道不足以支撑判断。

用标题判断即可，不要为聚簇去抓正文。输出每个聚簇的 `confidence` 与 `reason`（为何值得写）。

### 第二步：匹配观点卡

对每个聚簇，判断它接得住哪张卡。**读卡的正文**，不要只看标题——卡的精髓在判断正文里。

### 第三步：三态决策

| 决策 | 含义 | 硬性要求 |
| --- | --- | --- |
| `skip` | 卡已含此事件且**无新进展**；或纯重复报道（同一事实换家媒体） | 给出 `card_file` |
| `continue` | 卡已存在，且这是**新进展** | 给出 `card_file` + `whats_new` |
| `new` | 需要一张新卡 | 给出 `new_issue` + `reusable_because` + `card_file`（**你必须先写 `knowledge/cards/<file>.md`，再产出决策**） |
| `abstain` | 无任何聚簇接得住卡，也没达到新建门槛 | 说明原因 |

## 硬规则（不可违背）

1. **"新进展"的判据是引入新事实或改变判断。** 纯重复报道（同一事件换家媒体、同一事实换个说法）**不算**进展，必须判 `skip`。这是全系统防重复发文的**唯一**判定点，而重复发文是平台明令禁止的行为——这条判错代价最贵。

2. **冷却期优先于排序。** 卡在 `cooldown_state` 中 `in_cooldown: true` 时，该卡本轮**不参与竞争**，直接看下一名。若所有候选卡都在冷却，则 `abstain`。冷却期的目的是防重复，不是惩罚其他选题。

3. **新建门槛是可复用性。** 一条判断要成为新卡，必须能**接住未来的新闻**，而不只是支撑当前这一个事件。永远只有单一事件支撑的判断不是观点卡，只是一篇一次性文章。给出 `reusable_because` 说明理由。

   **建卡与决策是同一个动作。** `action: new` 时必须同时给出 `card_file`，且该文件必须已经存在——先写 `knowledge/cards/<file>.md`（五节结构），再产出决策。校验器会拒绝没有 `card_file` 或指向不存在文件的 `new`。理由：第 3 段的写回靠 `card_file` 取卡，缺失时写回被**静默跳过**，新卡永远拿不到 `covered_events` 与 `last_written`——认知资产停止生长且不报错（2026-10-17 实测发现）。

4. **限额。** 每日最多成文 1 篇（`max_articles_per_day`）、最多新建 1 张卡（`max_new_cards_per_day`）。多个事件通过分拣时，按「卡匹配度 × 事件热度」排序取第一名，其余记入简报备选。

   **「事件热度」里，位次趋势优先于当前热度。** 输入包的每条候选带 `trend` 对象（`state` 取 `climbing` / `steady` / `cooling` / `unknown` / `absent`，另有 `delta`、`observations`、`first_at`/`last_at`、`position_source`（这条 delta 是哪把尺子量的：`rank` 上游位次 / `hot` 无 rank 时的重排近似）、`reason`；算法见 [ADR-0056](../../../docs/adr/0056-trend-signal-is-same-source-rank-percentile.md)）。它是**位次变化**，刻度是上游榜单深度（`(rank-1)/(rank_total-1)`，所以跨快照可比；`position_source: hot` 的那部分改用同快照重排的池内近似，精度低一档）——`state: climbing` 表示名次在往上走（`delta` 为负），`cooling` 表示在退，`steady` 表示没动。两个聚簇同为高热度时，**正在爬升的优先于已经很高但 steady/cooling 的**：热度是存量，位次变化是正在发生的事。`absent` 表示该条目本轮已掉出榜单（本身是信息，通常意味着事件正在退潮）；`unknown` 表示可算的观测不足两条、或末观测不在最新快照（多为「今天只采了 1 次」），此时该条目的 trend 不可用，**按没有 trend 处理，不要当成 steady**。

   `unknown` / `absent` 的分布看输入包顶层的 `trend_summary`（`by_state` 各状态计数、`snapshots` 快照数、`thresholds` 判定阈值、`by_position_source` 位次来源计数、以及一段 `note`）。当它显示 `unknown + absent` 占了大半时，这一轮的 trend 整体没有信息量，排序退回「当前热度 + 卡匹配度」——不要从残差的几条 `climbing` 里硬读出偏好。

   **trend 参与排序，不参与打分，不设门槛。** 它不改变 `confidence`、不触发 `skip`、不单独决定弃权，与 `performance_reference`（ADR-0010/0037）同级。理由同下一条：每日一篇的样本量下把噪音升成硬规则会适得其反；而且**位次爬升 ≠ 值得写**——它只说「有人在看」，不说「这事跟读者的钱或职业有关」。

   **历史表现是排序参考，不是打分项。** 输入包的 `performance_reference`（循环 C，ADR-0010/0037）给出「每张卡接过的文章跑出了什么数据」。当两个候选的「匹配度 × 热度」**相当**时，可参考该卡的历史阅读率定先后；但它**不参与打分、不设阈值、不触发降权或退役**。理由：每日一篇的样本量下，把噪音升成硬规则会适得其反；一张卡历史表现差不等于它该退役——可能是那几篇的标题没写好。该字段为 `available: false` 时照常分拣。

5. **允许弃权。** 无选题是**合法结果**，不是故障。但必须在简报中显式声明「今日弃权」并说明原因（与"系统没跑"必须可区分）。

6. **置信度低于阈值不参与竞争**，但要列入简报的「低置信度（已跳过）」——把不确定显式表达出来，而不是伪装成判定。

7. **突发事件（重要但一次性）不为它开例外**，不建一次性卡；但列入简报的「**无卡可接**」列表供人工指定。理由：为一次性事件写出的文章本质是新闻综述，而本系统的价值在观点；但不该让你看不见系统的这个判断。

8. **`covered_events` 是防重复发文的唯一依据，读不到就等于没有。** 输入包里每张卡的 `covered_events` 来自 `cardfm.py`——观点卡 frontmatter 的**唯一**解析实现（ADR-0042）。此前三份拷贝漂移，`triage.py` 的那份把 `writeback.py` 写回的「空值 + 列表项」形态静默读成空，于是**分拣层看不到「这张卡写过什么」**，而写作阶段看得到（同一轮里分拣不知道、写作知道）。判 `skip` 还是 `continue` 全靠这个字段：若某张卡的 `covered_events` 与你的预期不符（例如 `last_written` 有日期却是空列表），先按 `cardfm.py` 重新解析核对，**不要**凭 `last_written` 推断「写过什么」。

9. **排序偏好：优先有「钱落点」或「职业处境落点」的那一个。** 赛道已于 2026-10-21 改为「AI 变现与开发者职业」（[ADR-0050](../../../docs/adr/0050-track-pivots-to-ai-monetization-keeping-technical-depth.md)）。当两个聚簇的「匹配度 × 热度」**相当**时，选那个能回答「这跟读者的钱或工作有什么关系」的。这与历史表现软信号同级：**参与排序，不参与打分，不设阈值**。

   **不要把它变成硬规则。** 「无钱落点即弃权」会让系统在只有纯技术新闻的日子系统性弃权，而弃权是合法结果但不是目标。纯技术议题仍可写，前提是能落到钱或职业上；落不上的，通常应判 `skip` 或列入简报备选，而不是硬写。

   判据的实测来源：头条站内搜 `Codex` / `MCP` 零相关结果，而 `AI 赚钱` / `程序员 失业` / `大模型 降价` 才有真实互动（ADR-0049 发现三）。改赛道改变的是「选题落在哪」，不改变「账号是第 7 天、0 粉丝」。

## 输出形状

```json
{
  "date": "2026-10-14",
  "decision": {
    "action": "continue",
    "card_file": "ai-coding-workflow-over-model-capability.md",
    "whats_new": "某模型发布并强调 CI 集成，首次把工作流作为卖点而非能力",
    "confidence": 0.88,
    "money_point": "① 接外包的前端——这类单子的报价这半年在被压，要盯的是甲方有没有把「用 AI」写进交付要求；② 团队里负责引入 AI 工具的——产出指标会从「写了多少行」换成「少了几个人」",
    "event": {"summary": "...", "members": ["..."], "hot": 51000, "trend": "climbing"},
    "ranking_note": "候选 1（匹配度 0.88，trend climbing，hot 51000）胜出；候选 2（0.71，trend steady，hot 89000）当前热度更高但位次未动，记入简报备选"
  },
  "low_confidence_skipped": [{"event": "...", "confidence": 0.41, "why": "..."}],
  "no_card_available":   [{"event": "...", "why": "一次性突发事件，不够格建卡"}],
  "abstain_reason": null
}
```

`ranking_note` 里写清**用了哪个 trend 状态**（以及它是被采纳还是被忽略）。这一栏是事后复核排序的唯一窗口：`trend_summary` 显示 `unknown` 占大半时，若 `ranking_note` 却写着「按 climbing 优先」，就能看出那一轮用了不可用的信号。

### `money_point` 与 `abstain_reason` 是必填的（ADR-0058）

`action` 为 `continue` / `new` 时，**必须**给出非空的 `money_point`：这件事跟读者的钱或工作处境有什么关系，**具体到角色**（不是「对行业有影响」）。`action` 为 `abstain` 时，**必须**给出非空的 `abstain_reason`。`skip` 不要求 `money_point`（它不进写作阶段，没有落点可写），非弃权日不要求 `abstain_reason`。

这两条由 `triage.py --validate` 硬校验，缺失会让编排器在第 2 阶段就停下。为什么值得卡住：

- **`money_point` 有真正的下游消费者。** 它被整段写进 `build_brief.py` 生成的写作任务书（`## 钱落点（分拣已判定，写作必须落到这些角色上）`），并在简报③段整段印出供人事后复核。所以它不是「写给人看的备注」，而是**写作阶段的硬约束**：写作模型拿不到它，就会自己另猜一套角色划分，与你在这里的判断不一致。
- **它此前一个消费者都没有。** 2026-10-21 起模型自发每天写 300+ 字，但既没进写作任务书也没进简报，全仓只有一个 ADR 提到过它（而且说错了）。这是本仓库第四次「记录了但没有消费者」。判据：**一个字段只有生产方、没有消费方，等于没写**。
- **弃权理由是对外可区分的唯一依据。** 简报①段要写出「今天为什么不写」。缺了它，看守（ADR-0038）与人都无法区分「今日无选题」和「系统没跑」。

**质量仍然验不了。** 校验只拦「空」不拦「差」——钱落点写得空泛（「对行业有影响」）、或与文章实际内容对不上，机器看不出来。那是残余风险 40 说的同一类东西，只能靠人读简报。所以**别把校验通过当成写对了**。

## 与相邻阶段的接口

- **上游**：`newsagg-collect` 的 `items`。
- **下游**：写作（`wewrite`）。若决策是 `continue`，把卡中判断 + 事件 + `whats_new` 拼成**选题包**交给 wewrite 的 topic；若 `new`，先建卡再走同一路径。
- **写回**：发布成功后向对应卡追加修订（`covered_events` += 本次事件、`last_written` = 今日），带**未复核**标记（ADR-0026）。
