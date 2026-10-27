# 服务器退回非生产角色：本机成为唯一环境，deploy/ 只剩渠道策略

## Context

2026-10-27 一天之内，ADR-0077 把「服务器成为主环境」落了地：`deploy/` 下 4 个
systemd 单元（采集 08/13/18 + 主流程 21:00）由 `deploy/install-timers.sh` 装到
ECS，任务体用 `dsh headless -` 从 `deploy/prompt-*.md` 读。当晚 21:00 服务器
真实跑过一次并真发（提交 `53e990f`），当时调度链路是成立的。

2026-10-28 用户改变决定：**服务器不再承担 NewsAgg 的任何职责，相关内容直接移除。**
理由是这台 Windows 工作机已经能把整条链跑通，两台机器并存只会带来两种成本：
重复发文的风险（ADR-0038 已警告过）与「两边都会漂」的运维负担（ADR-0077 的
consequences 已经把 `deploy/` 列为新增运维责任）。

把链路搬回本机时，撞出三个**只在 Windows 上成立**的缺陷。它们与 ADR-0039/0052/0076
那一族同型——「交互式 shell 里能用，定时任务里不能用」——但成因是平台差异，不是 PATH：

| 现象 | 根因 | 处置 |
|---|---|---|
| 扫码登录成功，重开浏览器却「未登录」 | 本机 Playwright 持久化上下文**不落盘**：写入 `__persist_probe` 后重开即丢失，profile 的 cookie 库里只剩 7 条登录前的 cookie、没有 `sessionid` | 登录态改以文件为准：同一进程内扫码 → `ctx.cookies()` → `.newsagg/toutiao-cookies.json`；CLI 启动时按 `NEWSAGG_COOKIE_INJECT=1` 注入 |
| `publish.py` 指向的 profile 找不到 | `os.homedir()` 在 Windows 读 `USERPROFILE`、**不读 `HOME`**，ADR-0028 的 HOME 重定向在这台机器上整体失效（状态目录与 Playwright 缓存都还在用户目录） | 不纠正：`publish.py` 与 CLI 两侧都落到 `C:\Users\cyx\`，语义不同但结果一致；记在这里，以免下次有人按 ADR-0028 去找 |
| `export-pw-cookies.mjs` 报 `MODULE_NOT_FOUND`，路径是 `C:\C:\Users\...` | `resolve(new URL('..', import.meta.url).pathname)` 在 Windows 上给出 `/C:/Users/...`，再 `resolve` 一次叠成 `C:\C:\` | 改 `fileURLToPath`（POSIX 行为不变） |

## Decision

**一、唯一生产环境是本机 Windows 工作机。** 调度用 IDE 的定时任务，4 条：
`08:00`/`13:00`/`18:00` 纯脚本采集（`run_all.sh --collect-only`），`21:00` 主流程
（三段式，含真发）。这回到 ADR-0002 的原始形态（Skills + 宿主定时任务），不再有
第二套调度器。

**二、服务器侧清空，不留半套。** 已执行并在服务器上逐项验证：

```
systemctl disable --now newsagg-{collect-0800,collect-1300,collect-1800,main-pipeline}.timer
rm -f /etc/systemd/system/newsagg-*.{service,timer}; systemctl daemon-reload
pkill -f '/opt/newsagg'; pkill -f 'Xvfb :99'
rm -rf /opt/newsagg /etc/newsagg
```

验证结果：`0 unit files listed`、`0 timers listed`、`/opt/newsagg` 与 `/etc/newsagg`
均不存在、6688 无监听、无 `newsagg`/`Xvfb` 进程；`dsh-web`、`dsh-relay@b-linux`、
`dsh-relay@mac-cyx`、`dsh-loopback-forward` 全部仍 active（与 NewsAgg 无关，未动）。
删除前提是**服务器仓库无未推送提交、工作区干净**（已确认），因此 git 里没有丢东西。

`/etc/newsagg/dsh-credentials.env` 里是 DeepSeek key 明文，随目录一并删除。

**三、仓库侧同步，不留「照着做会装回服务器」的说明书。** ADR-0018 要求运维责任
跟着代码走，所以删的是文件，不是事实：

- 删 `deploy/` 下 4 个 `*.service`、4 个 `*.timer`、`gen-units.sh`、`install-timers.sh`、
  `run-prompt.sh`、4 份 `prompt-*.md`；
- 删 `tests/test_systemd_units.py`（它钉的是那 4 个单元的生成结果；对象没了，
  测试也就没有对象——ADR-0057 的同一条判据）；
- 删 `scripts/bootstrap-server.sh`，并从 `tests/test_shell_var_boundary.py` 的
  `SHELL_SCRIPTS` 里去掉它（否则测试会去扫一个不存在的文件）；
- `run_all.sh` 里「找不到 Python ≥ 3.9」的提示原本指向 `bootstrap-server.sh`，
  改为指向 `bootstrap-wewrite.sh`（它建的就是 `.uv-tools/wewrite` 那个 3.12 venv）。

**四、`deploy/` 保留，但只剩 `channel-policy.txt`。** 渠道策略是**运行时契约**
（`newsagg-brief/scripts/source_health.py` 会读它），不是服务器专属配置，删掉会让
「部署级渠道可用性」这个能力一起消失。但内容必须跟着部署事实改：

- 原内容有一行 `web | disabled | 服务器上实测 r.jina.ai 直连不通…`。本机有 Clash
  7897、Jina 实测可用，**留着这行等于每天在简报第⑤段报一个并不存在的降级**。
- 改为「无任何禁用渠道」，并把 `tests/test_channel_policy.py` 里那条「仓里的策略
  必须把 web 关掉」的断言**反向**为「当前部署没有禁用渠道」。这不是把测试改绿，
  而是测试要断言的事实变了；ADR-0078 的方法（写进部署级文件、运行时读、简报分开
  措辞）完好无损。

**五、ADR-0077 / ADR-0078 保留为历史。** ADR 记录的是决策当时的事实，不随现实改写；
本 ADR 只声明那两条里的**调度归属**与**服务器渠道降级**不再生效。

## Consequences

- **没有第二执行环境了。** ADR-0077 第五条说「没做互备」是「有意没做」；现在变成
  「没得做」——服务器上连代码都没有了。机器关机、IDE 未运行、会话不活，当天就不跑，
  而且没有别的东西会代跑。
- **调度绑定本机宿主**（ADR-0038 早已记过的形态）。定时任务到点投递为这个会话的
  后续消息，只在宿主运行时可触发；它不会像 systemd timer 那样在重启后
  `Persistent=true` 补跑。漏跑的当天不会有简报。
- **收尾 `commit + push` 仍会失败。** 本机 shell 在 Session 0，Git Credential Manager
  既写不进凭据库也没有 tty 可询问：
  `fatal: Unable to persist credentials with the 'wincredman' credential store` →
  `could not read Username for 'https://github.com'`。读路径不受影响（仓库可匿名读，
  `git ls-remote` 通）。后果：稿件照发，`reports/` 与 `outputs/` 留在本地，远端不更新。
  待办：deploy key（写权限）或 PAT——两者都需要人做一次。
- **GitHub 上还有一把有写权限的 deploy key**（`newsagg-ecs-deploy`，对应服务器
  `/root/.ssh/newsagg_deploy`）。服务器私钥按用户要求**暂留**，等 GitHub 侧撤销后再删；
  在那之前这把钥匙仍能写仓库。
- **Windows 三处平台差异要记住**（见上表）：持久化不落盘、`HOME` 重定向失效、
  `new URL().pathname` 叠成 `C:\C:\`。判据与 ADR-0076 一致：**在交互式 shell 里跑通
  不算验证**，只有「定时任务那套无桌面、无 tty 的环境里跑通」才算。
- **本机新增两个不在版本控制里的补丁**（`reference/` 与 `.uv-tools/` 都被 gitignore）：
  `reference/toutiao-ops/cli/src/browser.js` 的 cookie 注入（受 `NEWSAGG_COOKIE_INJECT`
  控制）与 `.uv-tools/wewrite/bin/python` 转发垫片（Windows venv 布局是 `Scripts/`、
  而 `run_all.sh` 找的是 `bin/`）。换机器重建环境时必须重做这两处。
