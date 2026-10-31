# Agent-Reach 装上了：PyPI 同名陷阱，与 v1.6.0 的三处接口漂移

## Context

2026-10-30 夜里用户问「agent-reach 是否可以安装」。当时查到的结论是：能装，但装不了
——真身只在 GitHub，而本机到 GitHub 的唯一通道（Clash `ProxyAgent`）连 baidu 都拒。
2026-10-31 上午网络恢复，接着把这件事做完。

这条线的来龙去脉值得先写一句：ADR-0021 决定「只装 CLI、不注册 SKILL、不 vendor 入仓」，
ADR-0059 发现这条承诺当时**没有任何代码消费**，于是补了 `source_health.py`。但那个文件
写的命令是 `agent-reach doctor --json`，而**这台机器上从来没有装过 agent-reach**——
所以它每天都只打印一句「找不到 agent-reach 可执行文件」，从来没被真正跑到过。

## 发现（一）：PyPI 上有一个同名但无关的包

`pip install agent-reach` 装出来的**不是这个项目**：PyPI 上那个 `agent-reach 0.1.0`
作者是 Jean Galea（`github.com/jgalea/agent-reach`），只有 `rss` / `youtube` 两个渠道；
而 ADR-0021 描述的项目是 `Panniantong/Agent-Reach`，15 个渠道、有
`agent_reach/channels/base.py`。

我先按最省事的路装了 PyPI 那个（腾讯云镜像，`agent-reach==0.1.0`），`list --all` 只
出来两条渠道，与 ADR 记录对不上，查 PyPI 元数据才发现作者与仓库都不是同一个，随后
`uv tool uninstall` 并删掉目录。上游自己的 `llms.txt` 第 8 行写得很直白：

> Install from GitHub only… **The PyPI package named `agent-reach` is NOT this project.**

**这是「名称即契约」的一次破产**：一个听起来完全同名的包，装上之后能跑、能列出渠道、
退出码 0，只是它回答的是另一个问题。如果没去核对作者，这个错误会以「装好了」的形态
留在系统里，而 ⑤ 段会从「找不到可执行文件」变成「渠道全不可用」——两者都是假的。

## 发现（二）：v1.6.0 把三条接口全换了

装上真身（`agent-reach==1.6.0`，16 个包，含 `yt-dlp`）之后：

1. **`doctor --json` 没了**（`unrecognized arguments: --json`）。本文件原来调的正是它，
   所以装上之后**仍然**报「输出无法解析」——装上 ≠ 接上，这是第二个例子
   （第一个是 ADR-0059 的「记录了但没有消费者」）。
2. **`doctor` 变成纯本地状态**（`no network`），实测连通的是 `check`。两者语义不同：
   doctor 回答「装没装、缺什么工具」，check 回答「此刻连不连得上」。ADR-0021 要的是
   后者（信源变窄），而 ADR-0059 当年以为 `doctor --json` 与 `watch` 探测的是同一批
   东西——在 v0 时代也许成立，v1.6.0 不成立。
3. **渠道名变了**：`rss` / `exa_search` 在 v1.6.0 的渠道表里不存在（Exa 只是
   `web search` 的后端）。旧 `TIER0` 里的这两个名字永远查无此渠道，分母永远算不满。

## 发现（三）：Windows 上的两处静默失败

1. **`Scripts/` 而不是 `bin/`**：`uv tool install` 在 Windows 把 console script 放进
   `Scripts/`，而 `agent_reach_cmd()` 只找 `bin/agent-reach`——**装好了也永远报「找不到
   可执行文件」**。与 ADR-0079/0080 记的那几处同型：同一份代码在 Windows 上少一条路径。
2. **`subprocess(text=True)` 用 GBK 解码**：agent-reach 输出的是 UTF-8 JSON（带中文
   描述），`text=True` 会按系统 locale（本机 GBK）解码，抛 `UnicodeDecodeError`。
   更麻烦的是它在 **reader 线程**里抛，主线程只看到空 stdout——于是「装上、能跑、接口
   也对」依然被判成不可用。必须显式 `encoding="utf-8"`。

第二条是这次唯一一个**自己写出来又自己踩到**的坑，靠「跑一次真的」发现，而不是读代码。

## 发现（四）：一条命令跑四个渠道会被自己的超时拖死

`check` 每个 action 都是一次外部请求。实测一条 `check web github v2ex --timeout 8`
要 **47 秒**——jina 那条读请求会吃满 20 秒硬超时（上游不听 `--timeout`）。四个渠道串成
一条命令就会顶到外层 110 秒超时，**一个慢渠道把整轮观测拖成「读不到」**。

改成**逐渠道跑、每渠道 30 秒上限**之后：慢的只影响自己，四个渠道的四种原因各自报出来
（web 的 jina 读超时、github 被 rate limit、youtube 缺 ffmpeg、v2ex 实测失败），整轮
65 秒。部分信息胜过一次全无。

## 决定

1. **主路径 `check`（逐渠道），兜底 `doctor`（只补没实测到的 tier-0）**。兜底不许覆盖
   实测结果，也不许把 tier-1/2 拉进来——第一版兜底把 14 个渠道全报上来了（9 个是
   「未安装」），正是本文件头「边界二」警告过的噪声。
2. **`TIER0 = ("web", "github", "v2ex", "youtube")`**，并新增 `missing_channels`：
   上游改名后「查无此渠道」与「渠道坏了」必须分开报，否则这件事会静默消失。
3. **`tier0_ok` 之外加 `tier0_partial` / `tier0_off`**：`web` 的 exa 通、jina 超时是
   「部分可用」，说成「0/4 可用」会把方向指错。
4. **不跑 `agent-reach setup`**：它会把 skill 写到 `~/.agents/skills/` 并链接进本机其他
   AI 的技能目录，与 ADR-0021 第三条（不注册它的 SKILL）直接冲突。
5. **编码固定 `utf-8`**（见发现三之二）。

## 追加（同日上午）：两个渠道都 `add` 了，一个卡在网络，一个卡在子进程环境

用户要求把 `youtube` / `bilibili` 也装上，于是跑了 `agent-reach add`。两个都成功
（`ffmpeg 0.7.0`、`bili-cli 0.6.2`，都装在 `~/.agent-reach/tools/` 的私有 venv 里，
**不进仓库**）。但实测连通时，两个都失败，**原因完全不同，而且都不是「没装好」**：

**`youtube`：装好了，是 YouTube 不可达。** `yt-dlp` 报 `[SSL: UNEXPECTED_EOF_WHILE_READING]`
与 `ConnectionAbortedError(10053)`。同期 curl 实测：直连 `https://www.youtube.com` 挂满 15 秒，
经代理 0.003 秒即拒。这与 github.com 直连不通是同一类环境问题——**代码这边没有可修的东西**，
代理节点能到 YouTube 时它自然就好了。

**`bilibili`：站是通的（`api.bilibili.com` 200 / 0.26 秒），坏在 agent-reach 起子进程的方式。**
`bili-cli` 单独跑（哪怕 HOME 重定向到 session 目录）完全正常；一旦经
`agent_reach.runtime.run.run_cmd` 启动，子进程就报
`FileNotFoundError: No usable temporary directory found in [...]`——列出 8 个候选目录，
其中 `C:\Users\cyx\AppData\Local\Temp` 与 `C:\Windows\Temp` 都确实存在且可写（用 anaconda
的 3.12 当场 `mkstemp` 成功）。定位过程收敛到「**子进程的 cwd/env 组合**」：
`run_cmd(..., cwd=<repo>)` 能过，默认的 `cwd=<用户主目录>` 必挂；把 HOME 改成 Windows 路径、
把 TEMP 换成真实目录都不解决。

**没有继续往下修**，理由是：`run_cmd` 属于第三方包（ADR-0021 明确不 vendor 它），改它等于
在我们的仓库外打补丁，下次 `uv tool install` 升级就没了；而**同一个 bili-cli 直接调用是好的**，
所以这不是「渠道不能用」，是「经这层封装不能用」。要真用，得由人决定接受一个直接调用的旁路
（那就绕过了 agent-reach 的统一错误码与缓存），或者等上游改默认 cwd。两条都不该我顺手定。
- **没有把 `check` 结果落盘成历史**。`doctor` 有 `regressed` 字段（上次检查能通、这次
  不能），但需要先有 `check` 的历史；现在每天只算当天。
- **github 的 rate limit 是今天装包时反复打 API 撞出来的**，不是我改的东西坏了。明天
  再看它是否恢复——如果 ⑤ 段继续报 rate limited，那才是真问题。

## 边界（会静默失效的假设）

**「⑤ 段每天都 degraded」是可接受的，但不该无限期如此。** 现在 tier-0 全通要求四个渠道
每个 action 都过，而 `web` 的 jina 读请求在本机长期 20 秒超时——于是判定几乎必然停在
degraded。这是**如实的**（免费端点确实慢），但三个月后如果还是每天一条「信源渠道失效」，
说明该重新定阈值（例如：一个渠道有一个 action 通就算可用），而不是继续报警。告警一旦
变成背景噪声，就等于没有（ADR-0006/0021/0031 同一条）。
