# 风格回路的两个实现发现：基线来源与 pattern 提取缺口

> **部分作废**：下方「发现一」被 [ADR-0036](0036-publish-snapshot-must-be-recorded-and-exemplars-not-auto-grown.md) 推翻。
> 发布快照**不能**由"最后一次提交"推断（本人改稿 push 后基线即错位），必须由发布时记录。
> 「发现二」（pattern 提取缺口）继续有效。

## Context

按 ADR-0026 实现风格回路（`learn-edits` 的 diff 学习）时，实测暴露两个此前设计未覆盖的事实。两者都会导致**回路静默失效**——看起来在跑，实际不产生任何学习效果。

## 发现

### 一、发布快照由 git 天然提供，无需自建机制

ADR-0026 定"基线取发布快照，不取 `draft.md`"，但未说明快照从何而来。实测确认：**系统发布时会 commit 成稿**（ADR-0018 的写入者划分已如此规定），因此该文件的**最后一次提交内容就是发布内容**：

```bash
git log --format=%H -1 -- outputs/<slug>.md    # 取发布那次提交
git show <commit>:outputs/<slug>.md            # 得到发布快照内容
```

无需另存副本或维护哈希基线。git blob 天然内容寻址，因此**内容哈希去重也是免费的**。

**推论**：`outputs/` 的历史必须视为**不可重写**——若该提交被 rebase 或 force push 改写，快照基线即错位。这与 ADR-0018 的既有约束一致，但其重要性在此变得具体：它不只是"历史可追溯"，而是**风格回路的输入**。

### 二、`learn-edits` 只记录 diff，不提取 pattern（必须补一环）

`wewrite` 的 `learn-edits.py` 在写出 lesson 时**硬编码空 patterns**：

```python
# Agent fills these after analyzing the draft and final:
"patterns": [],
```

即它故意只做确定性部分（算 diff、写摘要），把"这次修改反映了什么偏好"这一判断留给 agent。这与 wewrite 自身的架构原则一致（"prompt 负责判断，Python 负责确定性"），也符合本项目 ADR-0025 的分工。

**但这意味着回路不会自动闭环**：不加 agent 步骤，lesson 永远是 `total_patterns: 0`，pattern 永不可能累积到 `hard=true`。

因此补了 `style_patterns.py`：
- `--prepare`：读出 lesson 引用的 draft/final 两份内容，输出 unified diff 供 agent 判断
- `--merge`：把 agent 写好的 patterns 合并回 lesson 的 YAML 结构
- 只做字段校验（`type`/`key`/`rule`/`scope`），**不猜 pattern**

### 三、端到端验证（用模拟修改）

| 阶段 | 结果 |
|---|---|
| 检测改动 | `would_learn`，正确识别 baseline 与 current 哈希不同 |
| 学习 | lesson 记录 diff：`char_diff: -50`、`+5/-3 行` |
| 合并 patterns | 2 条，读回校验通过 |
| `--summarize`（1 次） | `occurrences: 1`、`confidence: 3.0`、**`hard: false`** |
| `--summarize`（模拟 2 次） | `occurrences: 2`、`confidence: 5.5`、**`hard: true`** |
| 哈希去重 | 重复调用返回 `already_learned`，不产生新 lesson |

**阈值行为与 wewrite 契约完全一致**：单次修改保持软参考，重复出现才升为硬规则。这正是 ADR-0026 依赖的保护机制，而它的前提是"同一次修改不被重复计数"——由本脚本的哈希去重保证。

## Decision

**一、发布快照取自 git 的该文件最后一次提交**，不另建快照机制。同时确认 `outputs/` 历史不可重写。

**二、风格学习是两步流程**，不可省略第二步：
```
style_learn.py    → 调 learn-edits，产出 lesson（patterns 为空）
style_patterns.py --prepare → 输出 diff 供 agent 判断
（agent 写出 patterns）
style_patterns.py --merge   → 合并回 lesson
```
该两步流程已接入 `run_all.sh` 的第②步，并在有改动时提示下一步命令。

**三、哈希去重是必需的，不是优化。** 它防的是 ADR-0026 已记录的缺陷：同一次修改被多日观察到，从而被计为多次出现。缺少它，`hard` 判定会被污染。

## Consequences

- **风格回路的闭环依赖 agent 参与判断**，因此它在无人值守下**不是全自动的**。这与 ADR-0026 的时序（挂在每日运行开头）有张力：每日运行若没有 agent 介入第二步，lesson 会持续累积但 patterns 始终为空。
  - 这是本 ADR 最重要的后果。可能的解法（未采纳，待决）：在每日运行的 agent 上下文里显式安排"若有新 lesson 则提取 patterns"这一步；或在无 agent 时接受"只记录不提取"，等下次有人在场时批量处理。
- 哈希去重使"重复出现"的语义变为**不同的编辑事件**，而非同一次编辑被反复观察——这正是 wewrite 阈值设计所预期的。
- 本次验证用**模拟修改**完成，因此实测数据已全部清理（删除 2 个 lesson、diff 临时文件、学习状态，并还原成稿）。风格资产保持洁净，等本人真实修改喂养。**因此风格回路的真实闭环仍未验证**——机械部分已验证，判断部分待真实输入。
