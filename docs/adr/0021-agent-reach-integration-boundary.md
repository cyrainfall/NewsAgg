# 接入 Agent-Reach：阶段二只取正文，扩展检索放写作阶段

## Context

`Agent-Reach` 是本工作区 `reference/` 下的一个外部项目（[Panniantong/agent-reach](https://github.com/Panniantong/agent-reach)），定位是给 Agent 装互联网能力。它在设计上是一个**能力层，不是抓取器**：其 `agent_reach/channels/base.py` 的类注释明确写着"After installation, agents call upstream tools **directly**"，`check()` 的职责是判定"当前实际服务该渠道的后端是哪一个"。真正的抓取由 Agent 调用上游工具完成（`yt-dlp`、`gh`、`bili`、`curl https://r.jina.ai/URL`）。

它提供 15 个渠道，按配置成本分三层：

| tier | 渠道 | 含义 |
|---|---|---|
| 0 | `web`(Jina Reader)、`rss`、`exa_search`、`github`、`v2ex`、`youtube` | 装好即用，无需配置 |
| 1 | `bilibili`、`twitter`、`reddit`、`xiaohongshu`、`xueqiu`、`xiaoyuzhou`、`linkedin` | 需登录态或 key |
| 2 | `boss` 等 | 需专用浏览器 |

初始需求是"在阶段二抓正文时同时集成 Agent-Reach 获取更多相关内容"。判断后发现**这个需求把两件事合并了，而它们属于不同阶段**：

- "用 Agent-Reach 抓正文" —— 属于阶段二（判定该不该写）。
- "获取更多相关内容"（扩展检索）—— 属于写作阶段（怎么写好）。

理由有三。其一，ADR-0016 刻意收窄了阶段二（明确否决"全部抓取正文"，并把阶段二界定为"只对已匹配上观点卡的候选事件抓取正文"），而广泛扩展是放宽，方向相反。其二，每日最多发 1 篇，若在阶段二对每个候选都做扩展，绝大多数检索成本会花在最终被丢弃的事件上，与 $5/日 的预算冲突。其三，职责不同：阶段二问"该不该写"，扩展材料服务的是"怎么写好"；而判定"有无新进展"靠的是聚簇内那几条报道本身，外部材料再多也无法回答这个问题。

关键的是，写作阶段的扩展**已有位置**：`wewrite-write` 内建了素材采集（围绕 3–6 个主张搜索、每条经 `wewrite sources add --status verified` 记录、维护来源账本），且纪律更严（区分事实/推断/意见，禁止把模型记忆包装成已核实事实）。

## Decision

**一、阶段二只把 Agent-Reach 当作"取回正文"的执行手段**，即用 `web` 渠道（Jina Reader）读候选事件正文，不做扩展检索。这直接解决了 Agent-Reach 自己 README 点出的问题："抓回来一堆 HTML 标签，根本没法读"。

**二、扩展检索放到写作阶段的素材采集处**，按需调用其 tier-0 渠道，且与赛道高度吻合：`github`（dev tools 赛道的核心信源）、`exa_search`（语义检索）、`youtube`（技术讲座字幕）、`rss`、`v2ex`（中文技术社区）。这些正是 DailyHotApi 热榜覆盖不到的深度信源。

**三、只安装 CLI，调用上游工具。** 不注册它自带的 SKILL.md（那会往 agent skills 目录写文件，与本项目"自建 4 个技能入仓"的所有权划分混在一起，见 ADR-0012）。不 vendor 入仓——它明确宣称"持续换代"（"平台封了我们修"），与 ADR-0015 的钉版本意图直接冲突；因此它作为**活跃的外部依赖**存在，与 wewrite 的 vendor 策略有意不同。

**四、用 `agent-reach watch` 作为信源健康信号。** 该命令专为定时任务设计（"designed for scheduled tasks. Only outputs problems"）。其输出接入简报第 ⑤ 段（需你介入的事项）。

## Consequences

- **补上了当前设计的一个盲区。** 现有看守（ADR-0006）只能发现"系统完全没跑"，熔断只能发现"发布失败"，但**信源悄悄失效**会导致系统照常运行、简报照常发出、只是采集面变窄——这在之前完全不可见。`watch` 的输出使信源变窄成为一个显式信号。
- 信源变窄**不触发熔断**：采集面变窄不等于不能发文（可能仍有足够信号），停发代价过大。它只进入简报等人工判断。
- Agent-Reach 是活跃依赖，意味着**上游变更会影响本项目**，且它不钉版本，因此不存在"升级前重新校验契约"这道保护。它的多后端路由（平台封了自动切换）是韧性来源，但也使故障模式不易预测。
- **云部署约束**：tier-1/2 中的 `xiaohongshu`/`facebook`/`instagram`/`linkedin` 走 OpenCLI 复用 Chrome 登录态，其文档明说"服务器/无桌面环境不推荐支持"；`reddit` 从大陆需代理。第一阶段只发头条，这些渠道用不上，因此不构成阻塞，但也意味着**第一阶段的 Agent-Reach 实际只用 tier-0**。
- **Cookie 是凭证**：Agent-Reach 的 Cookie 按 ADR-0015/0018 的同一原则排除出库，不得进入 git。
- 阶段二的抓正文依赖 Jina Reader（外部服务），因此多了一个第三方可用性依赖；其渠道不可用时，退化为只用标题判定"有无新进展"，判定精度下降但不阻塞。
