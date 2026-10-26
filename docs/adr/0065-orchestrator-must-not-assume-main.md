# 编排器不能假定「当前分支就是 main」

## Context

从 2026-08 到 2026-10-25，`run_all.sh` 里有两处写死的远端引用：

- `git pull --rebase`（裸命令，依赖 upstream 跟踪）
- `git push origin main`

这两个写法的共同前提是「仓库当前在 `main` 上，且 `main` 有 upstream」。这个前提**在整整两个多月里每天成立**，所以没人发现它是前提——包括我自己，昨晚刚做完 ADR-0062 时也没注意到。

2026-10-26 08:00，仓库切到了 `feat/browserless-skills`（`main` + `23c5f19`，工作树干净），而这个分支：

- **没有 upstream**（`git rev-parse --abbrev-ref '@{upstream}'` → `fatal: no upstream configured`）
- **远端也不存在**（`git ls-remote --heads origin feat/browserless-skills` 返回空）

于是今晚 21:00 的主流程会以两种方式坏掉，而且**都不会有任何报错说明原因**：

1. **第①步直接死掉。** 裸 `git pull --rebase` 在无 upstream 时报 `fatal: no upstream configured for branch 'feat/browserless-skills'`。这句话既不在合并冲突正则里，也不在 `NET_ERR_RE` 里 → 落到 `fail_soft "git_pull"` → 写失败状态、出简报、`exit 1`。**当天零产出**，而简报会说是「拉取失败（已重试 0 次；非冲突）」，没有人能从这句话推出「因为你在特性分支上」。
2. **就算活下来，提交会留在本地。** 收尾 `git push origin main` 推的是**本地 `main`**，不是当前分支。今天的文章提交在 `feat/browserless-skills` 上，而 `main` 自 `374f482` 之后没有新提交 → push 成功、脚本打印「已推送 origin/main」、**远端其实什么都没更新**。

第二种比第一种更坏：第一种会中止，第二种会安静地假装成功。这正是本仓反复记录的那类缺陷（ADR-0057「测试只有跑起来才算存在」、ADR-0062「账本里的字段必须有人读」）的同族——**命令的退出码为 0，而它做的事不是你想要的那件事**。

## Decision

**起跑时解析当前分支，所有远端操作都显式点名它。**

- 新增 `current_branch()`：`git rev-parse --abbrev-ref HEAD`，取不到（分离头指针/非仓库）时回落 `main`。启动时算一次，存进 `BRANCH`。
- `git_pull`：改成 `git pull --rebase origin "$BRANCH"`，不再依赖 upstream 跟踪。
  - 先 `git ls-remote --exit-code --heads origin "$BRANCH"` 探一次：**远端没有这个分支是正常状态**（本地刚建、还没推过），不是失败——跳过同步、本轮照常跑，收尾 push 时用 `-u` 把分支建到远端，下一轮就正常了。
  - `ls-remote` 本身失败时要区分：命中 `NET_ERR_RE` 说明连不上远端，交给原有的重试/降级逻辑（ADR-0061）；否则才当作「远端没有这个分支」。
- `sync_push`：改成 `git push -u origin "$BRANCH"`（直连兜底那条同理）。`-u` 是为了让下次 `git status` 能直接告诉你领先/落后几个提交——不用每次都 `ls-remote`。
- 降级轮（`PULL_DEGRADED=1`）照旧不 push，只是提示语里的 `origin main` 换成实际分支。
- 启动时如果 `BRANCH != main`，打一行提示：**本轮所有提交都会落在这个分支上**。

**保持不变的**：工作区脏、合并冲突、认证失败、仓库不存在 —— 一律仍然 `fail_soft`。这次改的是「推到哪个 ref」，不是「失败要不要熔断」。

## 实测

在 `/tmp` 的一次性 bare remote + clone 里跑四种条件（把 `git_pull` / `sync_push` 两个函数原样摘出来跑，不是复述逻辑）：

| 条件 | 结果 |
|---|---|
| 分支在远端存在 | `git pull --rebase origin feat/x` 成功，`已同步到 3892e34` |
| 分支不在远端（新建），远端可达 | `远端还没有分支 feat/x（本地新建的分支）——本轮跳过同步，收尾 push 时建立`，`RC=0`，不降级 |
| 远端不可达 | `LS-remote` 报 `LibreSSL SSL_connect: SSL_ERROR_SYSCALL` → 命中 `NET_ERR_RE` → 降级继续，`PULL_DEGRADED=1`，不 push（ADR-0061 逻辑未受影响） |
| 工作树脏、远端可达 | 仍 `fail_soft "git_pull"`，没有被新分支判断放过 |
| `sync_push` 在未推送的新分支上 | `已推送 origin/feat/new2`，远端 `git branch` 里确实出现该分支 |
| 降级轮 `sync_push` | 打印「跳过 push：本轮未能从远端同步」，远端没有新提交 |

`bash -n` 通过。

## 一处当场复现的旧缺陷

我写第一版时用了：

```bash
echo "远端还没有分支 $BRANCH（本地新建的分支）……"
```

在 `LC_ALL=C` 下报 `BRANCH<字节>: unbound variable`——全角括号 `（` 的第一个字节被吃进了变量名。**这正是 DESIGN 残余风险 55 记录的问题**（ADR-0060 已记录、当时注明「`run_all.sh` 里同类写法还有存量，本次没有全面审计」），写进文档不到一天就由我自己在新增代码里复现了一次。

改成 `${BRANCH}` 后通过。教训不是「要小心」，而是：**这类缺陷不会被「记住」防止，只能被「不用 `$var` 紧跟非 ASCII」这条写法规则防止**。`run_all.sh` 里的存量仍未审计。

## Consequences

- **主流程可以在任何分支上跑。** 这是必要的：为了「必须在 main 上」而拒绝运行，等于特性分支开发的每一天都零产出（与 ADR-0061 拒绝「网络不可达就停工」是同一个理由）。
- **但产出会落在那个分支上，`main` 不会自动更新。** 这是一个新的、需要人处理的状态：如果连续几天在特性分支上跑，文章和观点卡的写回都在特性分支里，`main` 一直不动。降级提示只覆盖「没 push」，不覆盖「push 到了别的分支」。
- **所以启动时的分支提示是有意保留的噪声。** 只有在 `BRANCH != main` 时打印。它是这一轮唯一会主动告诉你「你不在 main 上」的地方。
- **未解决的**：仍然没有「今天跑在哪个分支」的结构化记录（`run-status.json` 里没有 `branch` 字段）。加字段容易，但按 ADR-0062 的判据，**得先有一个读它的地方**——目前除了启动那行 stderr，没有第二处。所以这次不加。
- **顺带暴露的**：`git_pull` 里对 `git pull` 的错误分类又多了一种输入（无 upstream）。现在这条路径被绕过了（改成显式 ref），但「git 的错误文本靠正则分类」这个脆弱点本身没变，见 DESIGN 残余风险 57。
