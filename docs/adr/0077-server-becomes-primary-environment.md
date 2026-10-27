# 服务器成为主环境：调度用 systemd，模型用 dsh headless

## Context

阶段 1 的目标是把发布从本机搬到服务器（`i-wz93frlza7g5nz9ft5dc`，2GB 内存，Alibaba Cloud Linux 3）。到 2026-10-27 中午，服务器侧的准备已全部完成：

| 能力 | 状态 | 出处 |
|---|---|---|
| 采集 | ✅ 七源全通，DailyHotApi 在 6688 | ADR-0076 |
| 表现读回 | ✅ 纯 HTTP，不起浏览器 | ADR-0071 |
| 发布写路径 | ✅ 浏览器 + Xvfb，登录态已拷过去 | ADR-0073 / ADR-0075 |
| git 同步 | ✅ deploy key + 仓库级身份 | ADR-0076 |
| 账本跨环境 | ✅ 运行时 + `reports/` 归档 | ADR-0074 |
| 定时调度 | ❌ **没着落** | 本 ADR |

前五项都有对应的 ADR 与实测记录。第六项——**定时调度**——是最后一个缺口，也是唯一一个「不是靠装软件能解决」的缺口。

## 缺口的具体形状

本机跑这件事用的是 DSH 的定时任务（`schedule_list` 里那 4 条）。但那套机制**搬不过去**：

- DSH 的 schedule 域**只存在于 web-app bundle 里**。服务器的 DSH 是 0.2.1-rc.2，有 `web` 与 `headless` 两个 profile，`/root/.dsh/storages/` 下有 `workspace.json` 但**没有 `schedule.json`**。
- headless/base bundle 不含 `dsh-schedule` → `dsh headless` **建不了定时任务**。
- 换句话说：DSH 的定时任务是这个 GUI 产品的功能，而我们要的是一个**不依赖 GUI 的服务器**。

同时本机的 4 条任务**必须停掉**——21:00 那条会真发，服务器接管后两边都发就是重复发文（平台明令禁止）。

## Decision

**服务器上的定时用 systemd timer，任务体用 `dsh headless`。**

### 一、为什么是 systemd 而不是 cron

- systemd timer 是服务器自己的调度器：不需要 GUI 进程常驻，服务器重启后 `Persistent=true` 会补跑错过的任务。
- 我们要跑的东西**本来就要一次跑很久**（主流程含两次模型调用），systemd 的 `TimeoutStartSec=7200` 与 `Type=oneshot` 正好合适；cron 的任务重叠问题要自己处理。
- 现有服务器已经在用 systemd（`dsh-web.service`、`dsh-relay@*.service`），不引入第二套调度概念。

### 二、为什么任务体还是 dsh headless

主流程里有两处**必须由模型做**的判断（ADR-0025 的分工：技能装判断，脚本装确定性）：

1. 分拣：读候选包 → 判断写什么/弃权 → 产决策文件。
2. 写作：按选题包自写成稿（服务器的 wewrite writer key 没配，`llm-write` 退出码 3 → agent 自写是设计内路径）。

这两处不是脚本能替代的。所以定时器不能只跑 `run_all.sh`——它必须能唤起一个 agent。

`dsh headless` 正是干这个的，实测可用：

```
Usage: dsh --profile headless [options] [task...]
  --json             写 NDJSON 事件
  --session-id <id>  续用既有 Session
  -                  从 stdin 读任务文本
```

服务器上用它跑 `PONG` 返回正常（用的是服务器自己 `.credentials.yaml` 里那把 `DEEPSEEK_API_KEY`；直接跑会报 `MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"`，导出那个 key 后正常）。

### 三、prompt 从文件读，不写在 unit 里

`deploy/prompt-*.md` 是四条 prompt 的**唯一副本**，unit 只负责调用 `deploy/run-prompt.sh` 读文件。

理由：prompt 里有大量要跟着代码改的内容（赛道定义、钱落点要求、服务器与本机的差异说明）。写在 systemd unit 里会造成第二份副本，然后它们会漂——本仓库已经因为「三份解析器漂移」（ADR-0042）和「四处赛道定义副本」（ADR-0050 残余风险 41）吃过两次亏。

`run-prompt.sh` 还负责：`PATH` 补上 `/usr/local/node/bin`（ADR-0052 教训）、`cd` 到仓库（DSH 技能目录约定：`<projectRoot>/.agents/skills`）、日志落盘、时间戳命名、退出码透传。

### 四、本机的 21:00 任务改成只读看护

本机保留 08/13/18 三条采集（它们是纯脚本、无副作用、不发布），**21:00 那条改成不再发布**。原因很直接：服务器接管后，两边都发就是重复发文。

采集留在本机是因为它们**不发布、不耗模型**，纯脚本跑在哪都行；留着还能在服务器出问题时有个观察窗口。

### 五、这次没做的

- **没有做「本机与服务器互为备份」**。两台机器都跑主流程是明确的坏主意（重复发文）；真要互备，只能做「服务器挂了 → 本机接手」，而这需要人判断，不该自动。
- **没有改 DSH 本身**。DSH 的 schedule 域在 web bundle 里，这是它的产品形态，不是缺陷。

## Consequences

- 服务器成为主环境后，**产出、账本、简报都落在服务器**，git 是唯一的同步机制。
- 服务器的定时任务不依赖 DSH GUI，所以「服务器上的 DSH web 界面」只用于人工查看与调试。
- `deploy/` 目录成为新的运行时配置载体：prompt、systemd 单元、渠道策略。它必须在仓里（否则本机与服务器会漂）。
- **新增一条运维责任**：systemd 单元与 `deploy/` 里的文件要跟着代码更新。`bootstrap-server.sh` 只装环境，不装定时器——定时器要 `git pull` 之后单独跑 `deploy/install-timers.sh`，这样「代码更新」和「定时器更新」是同一步，不会脱节。
- 本机的 21:00 任务如果忘了改，两边都会发。**这个步骤不可自动化**（删掉自己所在环境里的定时任务，且另一侧是否已接管只有用户知道），所以它留在用户的人工清单上。
