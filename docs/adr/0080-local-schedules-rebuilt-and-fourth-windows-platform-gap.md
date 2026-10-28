# 本机 4 条定时任务重建，并撞出第四处 Windows 平台差异（仓库路径下的解释器起不了浏览器）

## Context

ADR-0079 定「唯一生产环境是本机 Windows 工作机，调度用宿主定时任务 4 条」，
但那 4 条任务**只存在于决策文字里**：`schedule_list` 返回空数组，`deploy/` 下的
4 份 prompt 已随服务器一起删掉，`scripts/bootstrap-server.sh` 也没了。也就是说
ADR-0079 落地了"服务器退出"，却把"本机接管"留在了待办状态——**当天没人会跑**。

2026-10-28 20:22 按要求重建这 4 条任务。过程中逐条验证前置条件，撞出两个问题：
一个缺件（热榜服务需要常驻，但没有任何东西保证它活着），一个**新的平台级缺陷**
（从仓库路径下的 python 启动 node，浏览器起不来）。

## 发现

### 一、「热榜服务怎么起」此前只写在 SKILL.md 的一行手工命令里

`newsagg-collect/SKILL.md` 写的是 `cd reference/DailyHotApi && PORT=6688 npx tsx src/index.ts`。
这条命令是**给人看的**：它不脱离调用者生命周期（任务跑完服务就死）、不判断是否已在跑
（重复起会端口冲突）、也不报告失败。四条定时任务的第一句都需要它——若照旧把命令
抄进四份 prompt，就是四份会漂的副本（ADR-0042 / ADR-0050 / ADR-0077 各吃过一次）。

处置：新增 `scripts/ensure-hot.sh`，把「起服务」变成一句可判定的调用：
已在跑 → 立刻 0；没在跑 → `nohup ... &` detached 启动并轮询根路径；
缺件（无 `reference/DailyHotApi`、无 `node_modules/tsx`、PATH 里无 node）→ 显式 1
并说清缺哪个。四条 prompt 都只调它。

顺带把它加进 `tests/test_shell_var_boundary.py` 的 `SHELL_SCRIPTS`——ADR-0076
残余风险 81 说的正是「新增脚本不在检查范围内」。加进去之后**立刻抓到本脚本
4 处 `$var` 紧跟全角标点**，已改成 `${var}`。这条不是巧合：那个坑的复发方式
就是"写新脚本时没人检查"。

### 二、从仓库路径下的 python 启动 node，浏览器起不来（第四处 Windows 平台差异）

第 1 段跑到 ②b 表现数据时，api 后端因 cookie 权限被拒（`mode=0o666`），
回落到 browser 后端，三次全失败，`kind=concurrency`。把失败原文挖出来后是：

```
[pid=xxxx][err] CreateFile ...\browser-data\Crashpad\settings.dat: 拒绝访问。 (0x5)
[pid=xxxx][err] Failed to create a message-only window: 拒绝访问。 (0x5)
[pid=xxxx][err] FATAL:base\message_loop\message_pump_win.cc:98] Check failed: .
```

`concurrency` 这个分类来自 `CONCURRENCY_MARKERS` 里的
`"Target page, context or browser has been closed"`——而那句话是 Playwright 的
**外层报错**，真实原因在里面。于是它被当成"瞬时竞态 → 重试"，重试三次自然无效。
**分类器把一次稳定的环境failure读成了抖动**（与 ADR-0045「解析失败 ≠ 查不到」同型）。

归因过程（每步都可重现）：

| 变量 | 结果 |
|---|---|
| `bash -c 'node ...'`（不经 python） | ✅ 正常 |
| python 在 `C:\ProgramData\anaconda3\python.exe` | ✅ 正常 |
| python exe 复制到 `C:\wwt1` / `C:\Users\cyx` / `%TEMP%` | ✅ 正常 |
| **python exe 位于 `<repo>\.uv-tools\wewrite\Scripts\`** | ❌ 6/6 次失败 |
| **同一个 exe 复制到 `<repo>\` 根下** | ❌ 同样失败 |
| 同上，但 cwd 换成 `C:\wwt1` | ❌ 仍失败（不是 cwd） |

也就是说：**判据是"解释器镜像是否位于这个仓库路径下"，不是 cwd、不是 venv、不是环境变量**
（env dump 逐键比对无差异；`-S`、`-I`、`CREATE_NO_WINDOW`、`CREATE_NEW_CONSOLE`、
`close_fds`、新建标准 venv 都试过，只有"换位置"有效）。成因未查清，
从现象看更像安全策略按镜像路径生效（`0x5` 拒绝访问），而不是 Python 或 Playwright 的问题。

**这与 ADR-0079 记的三处平台差异（持久化不落盘、HOME 重定向失效、`new URL().pathname`
叠成 `C:\C:\`）严格同型**：都是在别的机器/别的 shell 里成立不了的假设，且都只在
「定时任务那套无桌面环境」里暴露。

处置：改 `.uv-tools/wewrite/bin/python` 这个垫片。它原先 `exec Scripts/python.exe`，
现在改成 `PYTHONPATH=<venv>/Lib/site-packages exec C:/ProgramData/anaconda3/python.exe`：

- `wewrite` 与 `Pillow` 都能 import（实测 `PIL 12.3.0`）；
- 解释器镜像在仓库外，浏览器能正常起（`auth check` → `logged_in: true / 淆雨学学`）；
- base 不存在时退回原来的 `Scripts/python.exe`，行为不倒退。

代价：**这个垫片绑定了一台机器的 anaconda 路径**（`C:/ProgramData/anaconda3/python.exe`）。
换机器要重做——与 `reference/` 的 cookie 注入补丁、`.uv-tools` 的 `bin/` 布局补丁
同性质，都是 ADR-0079 已声明"不在版本控制里、换机器必须重做"的那一类。

## Decision

1. **重建 4 条宿主定时任务**（`Asia/Shanghai`，`daily`）：
   `08:00` / `13:00` / `18:00` 跑 `run_all.sh --collect-only`；
   `21:00` 跑主流程三段（`--no-collect` / 分拣判断 / `--after-triage` → `--after-write`，**含真发**）。
   四条都以 `bash scripts/ensure-hot.sh` 开头。
2. **prompt 里写死判断纪律，不写死实现细节**：分拣七条硬规则的要点、写作的
   「钱落点具体到角色」、封面承载论据、**发布绝不自动重试**、**不加
   `--allow-daily-override`**（ADR-0059 那次真发了一篇重复文章）、简报必须无条件存在。
   实现细节（解释器、PATH、cookie 文件）交给 `run_all.sh` 自己钉住——
   prompt 只描述"要发生什么"，不描述"怎么做到"，否则它就成了第二份实现。
3. **热榜服务的前置抽成 `scripts/ensure-hot.sh` 一份**，不在四份 prompt 里各写一份。
4. **`.uv-tools/wewrite/bin/python` 垫片改用 base 解释器 + venv site-packages**，
   并把这个新发现的平台差异记在文件头注释里（下一个读这份垫片的人得知道为什么
   它不是一行 `exec`）。

## Consequences

- **四条任务只在这个 DSH 宿主运行时才真的触发**（ADR-0038 早已记过的形态）；
  漏跑的当天不会有简报，看守会在 GitHub 上告警。
- **`scripts/ensure-hot.sh` 是新增的运行时依赖**：热榜服务挂了会让采集层
  静默失去全部输入（ADR-0029 残余风险）。它现在至少会以非零退出码把
  「服务起不来」说出来，而不是让 `collect.py` 报"全部源不可达"。
- **`.uv-tools` 垫片的可移植性又降一档**：现在除了"Windows venv 是 `Scripts/`
  不是 `bin/`"，还多了"解释器不能放在仓库路径下"。两条都只在换机器时暴露，
  且都不会报出"是这台机器的路径问题"——它们表现为浏览器崩溃。
- **`classify_failure` 把稳定的环境失败读成 `concurrency` 这件事没有修**。
  真实原因是 Playwright 外层消息里含 `has been closed`，而分类器只看这一句。
  能修的方向是"先匹配更具体的失败特征（`message-only window`、`Crashpad`、
  `拒绝访问`），再匹配泛化的 `has been closed`"，但那需要一份**失败特征表**，
  而今天只观测到一个样本。如实记下：**当前它会让环境问题消耗 3 次重试并报
  "重试 3 次仍失败"，掩盖真实原因。**
- **cookie 权限那道闸在 Windows 上是空转的**：`os.chmod(p, 0o600)` 在 Windows
  上返回后 `st_mode` 仍是 `0o666`，于是 `from_cookie_file()` 的
  `st_mode & 0o077` 检查**永远不通过**。实测 `mode before: 0o666 → after: 0o666`。
  这意味着 **api 后端在本机根本走不通**（表现读回只能回落到 browser），
  而错误信息把原因指向"你用 chmod 修一下"，那条路在 Windows 上不存在。
  这是 ADR-0071/0072 那条安全要求在 Windows 上的**静默失效**——不是不安全，
  是功能缺失且提示指错方向。未修，记在这里。
