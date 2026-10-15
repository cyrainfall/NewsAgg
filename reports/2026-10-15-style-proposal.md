# 风格提议 2026-10-15：从首篇改稿中提取

> **本文件是提议，不是已生效的改动。** `style.yaml` 归本人所有（ADR-0018），系统不直写。
> 请你判断后手工应用。来源：`5139bc8` 对 `outputs/2026-10-14-gemini4-price-war.md` 的改稿。

## 一、真实缺口：`style.yaml` 没有任何标题要求

`wewrite-write/SKILL.md:87` 写着「标题准确、有明确对象和利益点」，但这条约束**不在** `.wewrite/style.yaml` 里。
`style.yaml` 现有字段只有 name / industry / target_audience / topics / tone / voice / word_count / content_style / writing_persona / blacklist / reference_accounts / theme / cover_style / cover_size / author。

后果：标题要求只存在于 wewrite 技能（通用写作规范），**没有归属到本账号**，也不区分分发形态。

**建议**：在 `style.yaml` 增加一节。若你认可，请手工加入：

```yaml
# 标题要求（来源：首篇改稿 5139bc8；分发形态为推荐流，见 DESIGN.md）
title_rules:
  - 必须含明确主体，不得泛指（反例「模型打五折」→ 正例「Gemini 4 打五折」）
  - 必须含读者利益点，读者 1 秒内能判断「这与我何干」
  - 直白优于悬念（推荐流读者无前置上下文，悬念式会被跳过）
  - 2~30 字（头条硬约束）

# 结构要求
structure_rules:
  - 开头第一段给读者利益或核心判断，不用元叙事铺垫
  - H2 是读者利益句，不是作者思路节点
  - 核心判断只出现在前 1/3，且只下一遍结论；谨慎只保留文末一条边界声明
  - 抽象数字必须给可执行换算，并紧跟一行边界说明
  - 至少一个具体场景
```

注意：`build_brief.py` 现已把上述要求写进**每日选题包**（作为写作时的硬约束），所以即使你不改 `style.yaml`，写作阶段也会看到它们。改 `style.yaml` 的意义是让这些要求成为**账号级持久身份**，而不是每次由选题包临时携带。

## 二、已自动生效的：6 条 pattern 进入软参考

`.wewrite/lessons/2026-10-15-diff.yaml` 已由 agent 从本次 diff 提取 6 条 pattern（`wewrite learn-edits --summarize` 确认 `total_patterns 6`）：

| key | 类型 | 内容 |
|---|---|---|
| `title_actor_and_benefit` | title | 标题须含具体主体与读者利益点 |
| `opening_benefit_first` | structure | 开篇第一段直接给读者利益 |
| `h2_reader_benefit` | structure | H2 用读者利益句，非作者思路节点 |
| `no_self_referential_trail` | expression | 删除「我原来怎么想」的自我指涉 |
| `thesis_early_not_diluted` | expression | 核心判断前置且只下一次 |
| `abstract_number_to_bill` | expression | 抽象数字换算成读者能对号入座的口径 |

每条 `occurrences: 1 / confidence: 3.0 / hard: false` —— 按 wewrite 契约，**单次修改只作软参考**，重复出现或你明确确认才会成为硬约束。
本文件第①节的两组 rules 与它们是同一批洞察，区别在于前者是"我明确提议写进你的身份配置"，后者是"系统按飞轮规则自动学的软参考"。

## 三、需要你知道的一处污染（已修）

`.wewrite/exemplars/` 此前有 2 篇范文，其中 `story-emotional-001.md` 是**伪造的**：它是阶段 0 做端到端验证时用模拟修改造的临时终稿，被 `learn-edits` 的自动增长机制（`learn_edits.py:468-481`，凡质量分 ≥50 即入库）收进范文库，并标注为 `ownership: user` / `authenticity: user_edited`，正文**不是任何真实版本**（`git log --all -S` 只命中它自己）。

危害：`wewrite-write/SKILL.md:79-83` 会让 agent 读 `ownership=user` 的范文来"校准声音和结构"，因此一篇 AI 生成稿被当作**你的声音样本**注入未来写作。

已处理：删除 001；真实的那篇重新归档为 `tech-opinion-001.md`（原先被误标为 `story-emotional`——分类器的 `STORY_MARKERS` 含「我」，中文技术观点文必然命中误判）；`style_learn.py` 现于调用前后还原范文库，自动增长一律丢弃并记入输出的 `exemplars_purged`。详见 ADR-0036。

**范文库现在只有 1 篇（你的真实改稿）。** 若你希望它更厚，建议你手工选稿并用 `wewrite exemplar <file> --category tech-opinion --user-authored` 显式添加——不要依赖自动增长。

## 四、请你看一件事

`wewrite-write/SKILL.md:79-83` 的范文使用纪律规定「第三方或缺少元数据的旧范文一律按第三方处理，只能参考节奏与结构」「任何范文都不能提供可复用的个人经历、人物、对话、具体细节、观点或句子」。

但这套纪律**依赖元数据诚实**。本次污染说明：元数据是自动生成时，`ownership: user` 不构成它真是你写的证据。当前靠"不自动入库"规避。若将来要重新启用自动入库，必须先修 wewrite 包内的 `detect_category`，并提高 `quality_score >= 50` 这个过低门槛（满分 100）。这属于 vendor 代码改动，需按 ADR-0024 的最小改动原则处理。
