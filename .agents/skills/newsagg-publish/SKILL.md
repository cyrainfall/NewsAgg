---
name: newsagg-publish
description: |
  NewsAgg 发布层：把成稿发到今日头条，含熔断、串行锁、反查验证与 dry-run。
  当需要发布文章到头条、验证发布链路、或处理发布失败时使用。
  复用 vendored 的 toutiao-ops；本技能只加安全门与验证，不重写发布逻辑。
---

# newsagg-publish — 发布层

把 `outputs/` 里的成稿发到今日头条。设计依据 [ADR-0024](../../../docs/adr/0024-reuse-toutiao-ops-for-publishing.md)、[ADR-0011](../../../docs/adr/0011-bounded-review-loop-and-layered-recovery.md)、[ADR-0032](../../../docs/adr/0032-dry-run-creates-real-drafts-and-verification-limits.md)。

## 前置

成稿需满足（包装层会校验，不满足直接拒绝）：

1. 位于 `outputs/<slug>.md`，且**第一行是一级标题**（`# 标题`）
2. 标题长度 **2~30 字**（头条硬限制）
3. 封面图存在（头条必填；由 `wewrite-visual` 生成或模板拼图，ADR-0013）

## 执行

```bash
# dry-run（默认）：填表 → 存草稿，不公开
python3 .agents/skills/newsagg-publish/scripts/publish.py \
  --slug <成稿slug> --cover <封面路径>

# 真发
python3 .agents/skills/newsagg-publish/scripts/publish.py \
  --slug <成稿slug> --cover <封面路径> --mode publish
```

| 参数 | 说明 |
| --- | --- |
| `--mode` | `dry-run`（默认，存草稿）/ `publish`（真发） |
| `--declaration` | 作品声明，默认 `引用AI`（ADR-0013 的 AI 标识） |
| `--min-free-mb` | 发布前可用内存下限，默认 800（ADR-0023） |
| `--lock-timeout` | 等待浏览器锁的秒数，默认 300 |
| `--account` | 头条账号名，默认 `default` |

## 安全门（按执行顺序）

| 门 | 内容 | 不过时 |
| --- | --- | --- |
| 0 | **熔断状态**（`.newsagg/breaker.json`） | 拒绝，提示如何解除 |
| 1 | 成稿存在 + 标题 2~30 字 + 封面存在 | 拒绝 |
| 2 | **内存预检**（ADR-0023） | 熔断 + 告警 |
| 3 | **登录预检**（只读，可重试 3 次） | 登录失效 → 熔断 |
| 4 | 发布 / 存草稿 | — |
| 5 | **反查验证**（仅真发，ADR-0024） | 见下 |

## 硬规则

1. **绝不信任发布返回值。** `toutiao-ops` 的发布是乐观返回（点完按钮即 `success: true`）。判定"发出去了没有"只认 `content list` 的反查结果。

2. **两类重试严格区分**（ADR-0011）：
   - **只读探测可重试**（登录检查、信用分、表现数据）——重试无副作用。
   - **发布绝不自动重试**——重发会造成重复发文，而平台明令禁止"批量发布重复内容"。

3. **发布结果未知时不猜。** 若点击发布后失败或超时，先回后台列表核实该标题是否已存在；**核实不清则熔断等人**，不重发。

4. **并发冲突不算失败。** 同一账号的浏览器命令不能并发（ADR-0030）。若失败信息含"Target page, context or browser has been closed"或"正在现有的浏览器会话中打开"，那是**瞬时竞态**，不是平台拒绝——但本包装层已用串行锁避免，故出现即说明锁失效，应报告而非静默重试。

5. **dry-run 有副作用**（ADR-0032）：它走 `--draft`，会在真实账号上**创建真实草稿**。它不是无痕验证，每次都会留痕，需人工到 `mp.toutiao.com` 草稿箱清理。**草稿无法被 `content list` 反查**（`--status` 只有 published/reviewing/rejected），因此 dry-run 只靠退出码判定。

6. **反查的三种结果**：

| 反查结果 | 判定 |
| --- | --- |
| 查到 `published` | 成功 |
| 查到 `reviewing` | **视为已成功提交**（审核中，不是失败） |
| 查到 `rejected` | **熔断 + 告警**——内容违规信号，直接关系信用分（ADR-0013） |
| 查不到 | 视为未发出，**不重发**，标为待处理 |
| 解析失败 | **不可判为"未找到"**，必须报告（静默误判会诱使重发） |

## 熔断

状态存 `.newsagg/breaker.json`（仓内，便于人工解除；guard 可同步读，ADR-0025）。

**解除方式**：人工修复后删除该文件即可（ADR-0011 的"人工介入"）。

熔断触发条件：登录失效、内存不足、内容被拒（`rejected`）。

## 输出

- `.newsagg/publish-ledger.jsonl`：每次发布的台账（追加）
- stdout：结构化结果（`verdict` 字段是判定结论）

## 与其他阶段的接口

- **上游**：写作阶段产出的 `outputs/<slug>.md` + 封面图。
- **下游**：成功发布后触发**认知写回**（向对应观点卡追加修订，带未复核标记，ADR-0026）与**教学副本**交接。
- 反查所需的 `content list` 输出形状见 ADR-0032：作品在 `items[*].data.msg_list[*]`。
