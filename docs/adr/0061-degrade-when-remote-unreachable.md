# 远端不可达时降级继续，而不是让一次代理抖动抹掉整天的产出

## Context

2026-10-25 下午，本机 Clash Verge 的代理通道坏了：`github.com` 直连超时、走代理 `LibreSSL SSL_ERROR_SYSCALL`，而国内站点（百度、头条）全部 200。也就是说**除了 GitHub，什么都通**。

当时 `run_all.sh` 的 `git_pull` 在重试耗尽后一律走 `fail_soft` → 写失败状态 → 出简报 → `exit 1`。这意味着那天 21:00 的主流程会死在第 ① 步，**当天零产出**。

这个耦合关系是 ADR-0026/0040 定下的：pull 必须在整轮开头、必须在写作之前，因为写作要用你最新的风格配置与教学副本。它的理由是「拿最新状态」，不是「GitHub 必须可达」。两者被实现成了同一件事。

## Decision

**把「远端不可达」与「本地有问题」分成两类，前者降级继续，后者保持熔断。**

### 一、判据

新增 `NET_ERR_RE`，比原来那条「要不要重试」的正则宽：

```
RPC 失败|HTTP2 framing|Could not resolve host|Connection reset|Connection timed out
|early EOF|The remote end hung up|SSL_ERROR_SYSCALL|LibreSSL|SSL connect error
|Failed to connect|unable to access|Operation timed out|Network is unreachable
|Connection refused
```

两条正则刻意不合并，因为它们回答的是不同问题：

| 正则 | 回答 | 合并的后果 |
|---|---|---|
| 重试白名单（原有的窄版） | 「这个错误重试有没有用」 | 混进 `SSL_ERROR_SYSCALL` 一类会白等三个 2 秒，收益为零 |
| `NET_ERR_RE`（本 ADR，宽版） | 「重试耗尽后，是等人动手还是等网络好」 | 混进「工作区脏」会把要人动手的事判成等一等，结果降级跑完再 push 冲突 |

### 二、降级时做什么

- `PULL_DEGRADED=1`、`DEGRADED_NOTE` 记一句人话，**继续跑**。
- 收尾 `sync_push`：**照常 `git add -A` + commit**（产出不能丢），但**跳过 push**。理由：没拉到远端就不知道远端有没有新提交，push 可能被拒，也可能把本地「没 rebase 过」的历史推上去。
- `write_status` 新增可选第 5 参数，写进 `run-status.json` 的 `degraded` 字段——与 `ok=false` 分开，因为处置完全不同：前者不需要「修复后重跑」，只需要人知道这一轮少了什么。
- 简报⑤段新增「**本轮降级完成**（不是失败，但有一处让步）」，排在运行失败那段之后。看守读 `ok`，所以降级不会触发告警——这是有意的：产出是完整的，只是没同步。

### 三、降级之前先试一次直连

2026-10-25 的实情比「断网」更细：同一个代理对 `https://www.baidu.com` 返回 200，对 `github.com` 返回 `LibreSSL SSL_ERROR_SYSCALL`，而**禁掉代理直连 github 也是 200**。也就是说代理没坏，是代理里指向 github 的那一跳坏了。

所以判定「网络不可达」之后、真正降级之前，补一次禁用代理的尝试：

```
git -c http.proxy= -c https.proxy= pull --rebase origin main
```

成功就照常完成同步、`PROXY_FALLBACK=1`，并在简报⑤段写一句「你的 git 代理对 github 不可用，本轮已改走直连」。`sync_push` 同理：push 撞网络类失败后也补一次直连。

两个坑（都实测过）：

- **必须同时清掉 `http.proxy` 和 `https.proxy`。** 只清 `https.proxy` 时 git 仍回退到 `http.proxy` 的值，照样失败——`git -c https.proxy= ls-remote origin` 报同样的 `SSL_ERROR_SYSCALL`，而两个都清就通。
- **代理是配在 `~/.gitconfig` 里的全局项**，不该由本系统去改它。所以用每次调用带 `-c` 的方式覆盖，不动用户的配置。ADR-0018：人的配置优先。

### 四、保持熔断的部分

工作区脏、合并冲突、认证失败、仓库不存在、无权限——**一律仍然 `fail_soft`**。这些要么需要人动手，要么继续跑没有意义。

顺带把「工作区脏」这条的 detail 补上脏文件清单（前 20 项）：git 只输出一行 `error: cannot pull with rebase: You have unstaged changes.`，不说是哪些文件。无人值守时没人能去翻工作区，不知道该 commit 还是该 stash。**2026-10-25 15:05 我自己就撞在这上面**——为了修每日 1 篇硬闸改了 `publish.py`/`brief.py` 没提交，整个 run 死在第 ① 步，而当时我看到的就是那一行英文。

## 实测

在 `/tmp` 的一次性 clone 里跑，三种条件各验一次：

| 条件 | 结果 |
|---|---|
| 干净工作树 + 不可达 remote（真实断网） | `DEGRADED=1`，打印「这不熔断：采集/分拣/写作/发布都不需要 github（ADR-0061）」，继续 |
| 干净 remote + 脏工作树 | 仍 `fail_soft`，简报里带 `     M DESIGN.md` 与处置说明 |
| 合成 `run-status.json`（`ok=true` + `degraded`） | 简报⑤段正确输出「本轮降级完成」 |
| 代理坏、直连好（真实场景） | 禁用代理的 `git ... ls-remote origin` 成功返回 HEAD；两个 `-c ...proxy=` 必须同时给 |

第一次测网络那条时失败过一次：我把测试脚本放在仓内，它自己让工作树变脏，于是走的是「脏工作树」分支（正确行为）。**要测「网络不可达」，先得让工作树干净**——这本身就是「两类失败不能混判」的一个侧面证据。

## Consequences

- **每天的主流程不再依赖 GitHub 可达。** 代理节点挂掉时仍会产出文章、仍会写回观点卡、仍会出简报，只是产出留在本地等人工 push。
- **降级轮可能用旧的风格配置写作。** 远端若有新提交，本轮看不到。这是刻意接受的代价：一篇文章用稍旧的风格，远好于零篇。简报⑤段会明写这件事。
- **降级轮不 push，所以看守看不到那天的新提交。** 但看守读的是 `reports/<日期>.md`（ADR-0038），而降级轮的简报照常入库并提交到本地——若那天 `git push` 一直失败，看守的告警是**正确**的（远端确实没有这天的记录）。
- **人工待办**：降级轮之后需要有人 `git push origin main` 一次。简报会写明。目前没有自动重试——因为「重试 push」在没解决网络之前只是把同一个错误多测几次。
- **这份改动本身没有被无人值守跑过。** 它是为了在今晚可能断网的情况下不丢产出而做的，所以今晚就是它的第一次真实运行。
- **直连兜底的「成功路径」没有端到端跑到过。** 机制验证了（代理坏的时候 `git -c http.proxy= -c https.proxy= ls-remote origin` 能拿到 HEAD），分支也验证了（真的走进了「改用直连重试一次」那一行），但「代理失败 → 直连 pull 成功 → 继续」这条完整链在真实断网窗口里没跑到——因为那次测试的 clone 处在冲突状态，直连 pull 也失败。等下一次真断网时它才算验过。在此之前，它的失败模式是「多试一次、然后照旧降级」，不会比不做更糟。
