# 补上四道「记录了但没有实现」的缺口，以及一次误发教会我的事

## Context

ADR-0058 之后做了一次阶段 0 的逐条取证：把 DESIGN.md 的落地顺序与 ADR 清单对照，看「写下来的决定」与「仓库里的实现」差在哪。结果发现四处缺口，性质完全相同——**决定是对的，实现没有跟上**：

| 缺口 | ADR 原文 | 实测 |
|---|---|---|
| 信源体检进简报第⑤段 | ADR-0021 Decision 四：「用 `agent-reach watch` 作信源健康信号接入简报第⑤段」 | `.agents/**/*.py\|*.sh` 全仓 `grep -rn "agent-reach\|agent_reach"` **零命中**。只有 `DESIGN.md:60` 与 `WALKTHROUGH.md:391` 的文字描述。 |
| 每日发布上限 | ADR-0014：「每日 1 篇」锁定参数之一 | `grep "max_articles_per_day" run_all.sh` **零命中**；`publish.py` 只写账本，从不读账本。 |
| wewrite 源码 vendor 入仓 | ADR-0015 Decision 一 | `.gitignore:3` 排除 `.uv-tools/`，`git ls-files \| grep -c '^\.uv-tools\|^vendor'` = 0。 |
| Jina Reader 可用性 | ADR-0021 把它列为 tier-0 | 直连 20s 超时，必须经 Clash mixed-port 7980 代理；这个事实此前只存在于某次对话里，没有落盘 |

本 ADR 记录前三项的处理，以及处理过程中发生的一次真实误发。

---

## 发现一：这是「记录了但没有消费者」的第五次，而且是最隐蔽的一次

ADR-0058 的残余风险 49 数过四次：`hot_history`（ADR-0038/0056）、三份 `parse_frontmatter`（ADR-0042）、`tests/`（ADR-0057）、`money_point`（ADR-0058）。本次是第五次，且比前四次更隐蔽——前四次至少都有一个**落地文件**（数据文件、解析器、测试目录、决策字段），只是没人读；而 ADR-0021 Decision 四承诺的是一条命令的输出**直接**进简报，中间没有落地文件，**它从来没被调用过**。审计方式是「grep 仓库存不存在相关代码」，而不是「grep 字段有没有消费者」，所以它躲过了前四次的教训。

判据因此要再往前推一步：

> 决定里出现「X 进 Y」时，必须能在 Y 的代码里找到读 X 的那一行。找不到就是没做，不是「以后会做」。

---

## Decision 一：信源体检进简报第⑤段，只报 tier-0，且不熔断

新建 `.agents/skills/newsagg-brief/scripts/source_health.py`，被 `brief.py` 的 `section_actions()` 调用，输出进简报⑤段（需你介入的事项）。

### 为什么用 `agent-reach doctor --json` 而不是 ADR 原文写的 `watch`

ADR-0021 写的是 `agent-reach watch`，而 `watch` 是**给人看的文本**：每条问题带多行安装指引。简报需要的是结构化判 tier，`doctor --json` 才是。两者探测同一批渠道（都跑 `channels/` 下的探测），换命令不换语义。ADR 原文在这一点上写得太细，越细越容易过期，所以此处记为对 ADR-0021 的实现层修正。

### 两条边界

**① 只报 tier-0。** `TIER0 = ("web", "rss", "exa_search", "github", "v2ex", "youtube")`。tier-1/2 需要登录态或专用浏览器，ADR-0021 的 Consequences 已写明第一阶段用不上。若把它们一起报上去，第⑤段会堆满 11 条「未安装」——而第⑤段是**需你介入的事项**，堆噪声等于让这一页被无视。只报 6 条 tier-0，实测现状 `tier0_ok 2/6`（rss / web 可用，github 与 v2ex 告警，youtube 与 exa_search 未装）。

**② 信源变窄不熔断。** 采集面变窄不等于不能发文。停发代价过大（ADR-0022 第五条）。它只报告，由人判断今天要不要换个选题。

### 为什么放进第⑤段，而不是另起一段

第⑤段就是「需你介入的事项」，而信源失效是唯一**人工能处理**的系统级信号（装 yt-dlp、配 mcporter、查代理）。它与 `failed_sources` 互补：采集层只知道 DailyHotApi 那七个源，Agent-Reach 探测的是热榜之外的深度信源层。

### 失败路径必须返回 `unavailable` 而不是抛异常

简报必须无条件每日存在（ADR-0006）——它是心跳。体检跑不起来（命令缺失、超时、OSError、非零退出、无 JSON、解析失败、非对象）时一律返回 `{"status": "unavailable"}`，`brief.py` 侧再包一层 `try/except`。`unavailable` 的文案明写：

> 这不等于「信源都健康」——它等于我们**失去了观察能力**。

---

## Decision 二：「每日 1 篇」必须在脚本层有闸，而不只在纪律里

### 缺口为什么严重

ADR-0014 把「每日 1 篇」列为锁定参数，但它此前只存在于两处：分拣输入包的 `limits` 与 `newsagg-triage/SKILL.md` 的纪律条文。**两者都是约定，不是闸门。** 约定只在有人（或模型）遵守时有效，而发布是这条流水线上唯一不可撤销的写操作。无人值守时唯一执行者是 agent，它可能因为续跑、补写、或流程被中断后重跑而调用发布两次。

### 闸的判据：账本里今天的真发布数

用**账本**而不是「今天有没有成稿」——成稿可以写好几篇，发布才消耗额度。
用 **`verdict`** 而不是「命令有没有返回 0」——dry-run 存草稿也算返回 0，但它没发，所以 `draft_saved` 故意不计入；`title_not_found` 与 `unknown_needs_review` 同样不计入。

### 闸的位置与形态

`main()` 在门 0（熔断）之后插「门 0.5」。**只在 `args.mode == "publish"` 时查**——dry-run 不消耗额度，否则一次例行验证就会把当天堵死。达到上限打印：

```json
{"ok": false, "stage": "daily_limit",
 "message": "今天（2026-10-25）已发布 1 篇，达到 ADR-0014 的每日上限 1 篇。若确需补发/重发，加 --allow-daily-override 并写明理由。",
 "published_today": [{"slug": "...", "at": "...", "gid": "...", "verdict": "title_found"}]}
```

返回码 1。`published_today` 带上 slug/gid/时间，是为了让看到这条错误的人立刻知道「今天发的是哪一篇」，而不是只有一句「已满」。

### 为什么保留覆盖通道

「今天已经发过一篇」不等于「这一篇一定不该发」。上一篇可能是误发（ADR-0047 的微头条泄漏、ADR-0045 的截断误判），需要人工补发或重发。这时**应当由人显式说一句**，而不是让系统替人决定。

参数 `--allow-daily-override` + `--daily-override-reason`，理由写进账本 `record["daily_override"]`。事后回看「今天为什么有两篇」，不该靠回忆。

---

## Decision 三：误发不占额度，但必须留在账本里

见下一节「一次真实误发」。补录的误发篇标 `accidental: true`，`count_published_today` 跳过它。

理由是：误发是操作失误或测试跑漏，不是当天的正常产出。若计入额度，一次误发就会把当天剩下的正常发文也堵死——**用一个已经发生的错误去惩罚正确的那一次**，没有道理。

但它仍然**必须**留在账本里。删掉它会让「今天到底发了几篇」与平台实际状态不符，而账本的意义正是成为唯一可信的本地记录（ADR-0045/0053 同源）。

---

## 一次真实误发：我把「测试闸的放行路径」当成了「停在闸口」

### 发生了什么

为验证覆盖通道能绕过门 0.5，我用 `--allow-daily-override --daily-override-reason "测试覆盖通道"` 跑了真发。

**我以为测试只会走到闸口。但 override 是「绕过闸」而不是「停在闸后」——脚本绕过闸之后，继续执行了完整的发布流程，真的发出了一篇。**

平台侧确认：gid `7700497729948041762`「AI 提效写进绩效，先涨的是你的返工率」，与 10-24 已发布的 `7700222179094430248` 内容重复。平台允许删除（`MenuList.ShowDelete=true`、`ForbidDeleteReason` 为空），但 `toutiao-ops` **没有 delete 命令**，只能人工在头条 Web 后台删。

### 教训

**测「闸」的放行路径，等于测「发布」。** 这是把「验证一个拦截器」误当成「验证一个开关」：拦截器的正确形态是「条件不满足时它拦住」，而覆盖参数测试的是「条件满足时它放行」——放行之后就是真实副作用。

从此，override 类参数必须有**不触达真实发布的验证方式**：注入一个假 CLI，或在 override 分支里先 dry-run 一次再退出。不能靠改账本 + 真发。

### 连带修出的账本缺口

误发当时账本已还原，那篇发布**没有任何本地记录**——账本与平台实际状态不一致。已补录第 10 条，带 `accidental: true`、`needs_human_delete: true`、`note`。这与 ADR-0045 的教训同型：**账本记的是事实，不是我们希望的样子。**

---

## 未处理项

### ADR-0015 的 vendor 决定仍未落实，且比原先记录的更难落实

ADR-0015 决定「wewrite 源码 vendor 入仓、密钥排除、成稿存 markdown」。实测 `.gitignore:3` 排除 `.uv-tools/`，`git ls-files | grep -c '^\.uv-tools\|^vendor'` = 0。

但**关键信息此前记错了**：wewrite 不是本仓的 vendored 依赖之一。`git ls-files | grep -ci wewrite` = 41，全是 `.agents/skills/wewrite-*/` 下的 SKILL.md 与 references——**那是技能文档，不是包源码**。wewrite 实际装在 `.uv-tools/wewrite/`（被 gitignore），wheel 源在 `.uv-cache/archive-v0/WITHaAg6PavimxbP/wewrite`（432K，MIT，v4.2.1，依赖 markdown/beautifulsoup4/cssutils）。

所以 ADR-0015 的「vendor 入仓」从未执行过，而 DESIGN.md 一句话形状里仍写着「wewrite 是写作引擎（vendor 入仓、钉版本）」。这是**第六次「记录了但没有实现」**，本次只记录，未修——理由见下。

### Jina Reader 必须走代理

ADR-0021 把 `web`（Jina Reader）列为 tier-0，但从未记录「它直连不通」。实测直连 20s 超时，必须经本机 Clash mixed-port 7980 代理。这与 ADR-0022（头条与国内源直连）不冲突——Jina 是境外服务。**但它在无人值守时若无代理可用就会静默失效**，而 `source_health.py` 现在报 `web: ok` 正是因为它在本机有代理。这条要写进部署清单，否则阶段 1 上线即失效。

---

## Consequences

- **「记录了但没有实现」现在是六次，且每次的发现方式都不同。** 前五次靠 grep 代码/字段，本次的第一次（agent-reach）靠的是「grep 不到」，但它连字段都没有，所以只能靠人工逐条比对 ADR 与 DESIGN。**没有任何机制能自动发现它**——这是这个系统最结构性的缺陷，比任何单点缺陷都重要。缓解只能是阶段切换时做一次逐条取证（本次就是这样发现的）。
- **加了一道不可撤销操作的闸，但它只挡「次数」，不挡「内容」。** 每日 1 篇的闸能挡住重跑导致的重复发布，挡不住一篇不该发的文章发出去。后者是残余风险 40 的范畴。
- **误发的成本被完整记下来了，但成本本身没有消失。** 平台上那篇重复文章要靠人删；本地靠 `accidental` 标记与 `needs_human_delete` 保持诚实。
