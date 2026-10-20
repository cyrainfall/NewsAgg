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
3. 封面图存在（头条必填；由 `make_cover.py` 模板拼图生成，ADR-0013）

## 封面：承载一个视觉论据，不复述标题（ADR-0044）

信息流里标题就印在封面旁边，**复述标题的封面不增加信息**，还会在 320px 缩略图下
把字号压到读不出来。旧版实测：1200x675 上亮像素仅 4.7%，缩到 320px 宽时正文约 8px 高。

封面只放**一个**元素，且必须能在 320px 宽下读出：

```bash
# 自动推导（从标题里找数字/对比/冒号切分）——能保证不出错，但选不出论据
.uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py --slug <slug>

# 显式指定视觉论据（推荐：由写作阶段判断"这篇文章的视觉论据是什么"）
.uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py \
  --slug <slug> --spec .newsagg/covers/<日期>.json
```

**没写 spec 就走自动推导，而自动推导只会切分标题、回答不了「这篇文章最值得被记住的
事实是什么」**。2026-10-20 那次漏写 spec，封面渲染成顶部 `Codex搬走Cursor配置`、
正文 `Codex搬走Cursor配置：/ 选AI编程工具的标准变了`——顶部小字与正文逐字重复，
且 `problems: []` 全绿，无人察觉（ADR-0044 发现五）。所以发布前要确认
`.newsagg/covers/<日期>.json` 存在；编排器在没有 spec 时会向 stderr 打一行提醒，
但那不是阻断。

三种布局：

| `layout` | 用途 | 字段 |
| --- | --- | --- |
| `number` | 有强数字（金额/比例/参数） | `kicker` `hero` `hero_unit` `payoff` |
| `contrast` | 两个短句相撞（A 成立 / B 不成立） | `kicker` `line1` `line2` |
| `statement` | 无数字、无论点对撞，只有一句判断 | `kicker` `statement` |

**数字要拆成 `hero` + `hero_unit`**：`hero` 放大到占满画面（`8160`），`hero_unit` 缩到
三分之一（`亿参数`）。把两者塞进同一个字符串会让主字号被压小。

## 封面自检：量的是画布上的像素，不是设计常量（ADR-0044 发现六、七）

`make_cover.py` 在每个**实际绘制点**记录元素字号（`drawn`），然后按
**320px 缩略图**（平台下发 640x360 是 2x 资源，保守按 1x 算）换算实测像素：

- `READABLE_PX = 11.0`——汉字在缩略图下小于这个高度就读不出来（正文最小可读约 11-12px）。
- `MIN_READABLE = 42` 画布像素（`math.ceil(11.0 / (320/1200))`）。**必须向上取整**：
  `int(11.0/0.2667)=41`，而 `41 × 0.2667 = 10.9px`——一个自己都通不过自己检查的下限。
- `MIN_MUTED = 42`（原先是 30，换算到缩略图只有 8px）。

任何元素实测低于 `READABLE_PX` 即进 `problems`，退出码 1，编排器不发布。
另有一条 `hero_ratio`（主元素占画布高度）低于 25% 的 `warnings`——画面太空。

**输出里要读三个字段**：`drawn`（每个元素在缩略图下的实际高度）、`restates_title`、
`warnings`。`restates_title` 为真表示「封面说的和标题是同一句话」——它是
**警告不是错误**（`derive_parts()` 原理上只能切分标题、造不出论据，判 fatal 会让
缺 spec 那天直接发不出去），但必须处理：去写 `.newsagg/covers/<日期>.json`。

`run_all.sh` 把整份自检结果落到 `.newsagg/covers/<日期>.report.json`，
`brief.py` ⑤ 段会渲染成「封面自检」条目（`- ✗` 问题 / `- !` 警告）。
**无人值守时没有第二双眼睛，所以警告必须走这条路进简报**——只打 stderr 等于没打。

**推导来的 kicker 过长（>16 字）会被整条省略，而不是截断。** 截断只会留下残句
（实测 `DeepSeek API 每 1000 次请求 5 元` → `DeepSeek API 每`），残句既不是标签、
又在复述标题。显式传进来的 kicker 才按分隔符截断。

**溢出会被挡住**：`make_cover.py` 用像素检查内容是否侵入左右边距带，溢出时
`problems` 非空且退出码 1，编排器据此不发布。一张溢出画布的封面比没有封面更糟——
它会被发出去，而没人看见。注意这只挡「放不下」，挡不住「放得下但读不出」——
后者由上面的缩略图可读性检查挡。

**封面上不印任何脚注。** 作品声明走平台的字段（`publish.py --declaration`），
不在图上的像素里重复。在图上再印一行脚注要占掉 7.5% 的画布高度——封面上每一寸
都该留给论据。`--note` 默认空，需要时才传。

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
| `--declaration` | 作品声明，默认 `个人观点`（ADR-0046；**含已知合规风险**，见该 ADR） |
| `--min-free-mb` | 发布前可用内存下限，默认 800（ADR-0023） |
| `--lock-timeout` | 等待浏览器锁的秒数，默认 300 |
| `--account` | 头条账号名，默认 `default` |
| `--card-file` | 本篇承接的观点卡文件名（ADR-0037）。由 `run_all.sh` 从决策文件传入 |

> **`--card-file` 为什么要记进账本**：循环 C 读回表现数据后要回答"**哪些卡**跑出了数据"，
> 而账本是唯一的关联点。文章标题会被本人改、决策文件属运行时状态（`.newsagg/` 不入库，
> ADR-0027 可能被清理或跨环境不同步），只有发布那一刻记下的 `card_file` 是可靠归因。
> 归因读取顺序是**账本优先、决策文件兜底**。

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

6. **反查的四种结果**（`verdict` 取值）：

| 反查结果 | `verdict` | 判定 |
| --- | --- | --- |
| 查到 `published` | `title_found:published` | 成功 |
| 查到 `reviewing` | `title_found:reviewing` | **视为已成功提交**（审核中，不是失败） |
| 查到 `rejected` | `rejected` | **熔断 + 告警**——内容违规信号，直接关系信用分（ADR-0013） |
| 查不到 | `not_published_needs_review` | 视为未发出，**不重发**，标为待处理 |
| 输出解析失败 | `unknown_needs_review` | **不可判为"未找到"**，交人工核实（静默误判会诱使重发） |

**`run_toutiao` 必须把 stdout 重定向到文件，不能用管道捕获**（ADR-0045）。toutiao-ops 的 runner 在 `console.log` 之后**立刻** `process.exit(0)`，Node 对管道的写是异步的，未 flush 的部分会被丢掉：`content list --limit 20` 的 103KB 输出走管道只回收到 64KB，且断在一个 JSON 字符串中间。写文件描述符是同步的，不受影响。2026-10-18 就因为这一点，把一次**成功发布**报成了 `title_not_found`。

## 绝不附带发微头条（ADR-0047）

`--no-weitoutiao` 每次都必须传。它看起来是个多余的防御，其实是必需的：
**头条发布页的「发布得更多收益」（= 同时发布微头条）默认是未勾选的**，
而 `toutiao-ops` 的 `uncheckWeitoutiao()` 曾经无条件点它一下 ——
那一次点击不是取消，而是**勾上**，于是文章和微头条各发一条。
10-14 与 10-18 各发生过一次，两次都被反查判成 `title_found`、无人知晓。

上游已修（先读 class 里的 `byte-checkbox-checked`，只在确实已勾选时才点，
点后再验一次）。**但这条规则仍然必须保留**：发布页是平台页面，
它的默认值随时会变，而我们唯一可靠的姿态是「每次都显式要求不要」。

反查会数同一标题下有几条文章、几条微头条，写进账本 `verify_kinds`；
出现微头条时置 `weitoutiao_leak`，简报 ⑤ 段会列出来等你手工删除
（本 CLI **没有删除能力**，发出去就删不掉）。

## 作品声明：当前是「个人观点」，这是有意的偏离（ADR-0046）

`--declaration` 默认 **`个人观点`**（平台显示为「个人观点，仅供参考」），
**不再标 `引用AI`**。

⚠️ **这是用户明确要求的偏离，并且带有已知合规风险**：《人工智能生成合成内容标识
办法》自 2025-09-01 施行，第十条禁止「恶意删除、篡改、伪造、隐匿」标识，用户侧
义务是「发布 AI 生成合成内容时应当主动声明」。本系统正文由 AI 生成。

该决定已记录在 ADR-0046，并登记为 DESIGN.md 的残余风险。**不要在无人知晓的情况下
把它改回 `引用AI`，也不要把它当成"已经合规"**——它是被知悉并接受的风险，不是被
解决的问题。

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
