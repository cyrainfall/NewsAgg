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
| `new` | 需要一张新卡 | 给出 `new_issue` + `reusable_because` |
| `abstain` | 无任何聚簇接得住卡，也没达到新建门槛 | 说明原因 |

## 硬规则（不可违背）

1. **"新进展"的判据是引入新事实或改变判断。** 纯重复报道（同一事件换家媒体、同一事实换个说法）**不算**进展，必须判 `skip`。这是全系统防重复发文的**唯一**判定点，而重复发文是平台明令禁止的行为——这条判错代价最贵。

2. **冷却期优先于排序。** 卡在 `cooldown_state` 中 `in_cooldown: true` 时，该卡本轮**不参与竞争**，直接看下一名。若所有候选卡都在冷却，则 `abstain`。冷却期的目的是防重复，不是惩罚其他选题。

3. **新建门槛是可复用性。** 一条判断要成为新卡，必须能**接住未来的新闻**，而不只是支撑当前这一个事件。永远只有单一事件支撑的判断不是观点卡，只是一篇一次性文章。给出 `reusable_because` 说明理由。

4. **限额。** 每日最多成文 1 篇（`max_articles_per_day`）、最多新建 1 张卡（`max_new_cards_per_day`）。多个事件通过分拣时，按「卡匹配度 × 事件热度」排序取第一名，其余记入简报备选。

5. **允许弃权。** 无选题是**合法结果**，不是故障。但必须在简报中显式声明「今日弃权」并说明原因（与"系统没跑"必须可区分）。

6. **置信度低于阈值不参与竞争**，但要列入简报的「低置信度（已跳过）」——把不确定显式表达出来，而不是伪装成判定。

7. **突发事件（重要但一次性）不为它开例外**，不建一次性卡；但列入简报的「**无卡可接**」列表供人工指定。理由：为一次性事件写出的文章本质是新闻综述，而本系统的价值在观点；但不该让你看不见系统的这个判断。

## 输出形状

```json
{
  "date": "2026-10-14",
  "decision": {
    "action": "continue",
    "card_file": "ai-coding-workflow-over-model-capability.md",
    "whats_new": "某模型发布并强调 CI 集成，首次把工作流作为卖点而非能力",
    "confidence": 0.88,
    "event": {"summary": "...", "members": ["..."], "hot": 51000},
    "ranking_note": "候选 1（匹配度 0.88）胜出；候选 2（0.71）记入简报备选"
  },
  "low_confidence_skipped": [{"event": "...", "confidence": 0.41, "why": "..."}],
  "no_card_available":   [{"event": "...", "why": "一次性突发事件，不够格建卡"}],
  "abstain_reason": null
}
```

## 与相邻阶段的接口

- **上游**：`newsagg-collect` 的 `items`。
- **下游**：写作（`wewrite`）。若决策是 `continue`，把卡中判断 + 事件 + `whats_new` 拼成**选题包**交给 wewrite 的 topic；若 `new`，先建卡再走同一路径。
- **写回**：发布成功后向对应卡追加修订（`covered_events` += 本次事件、`last_written` = 今日），带**未复核**标记（ADR-0026）。
