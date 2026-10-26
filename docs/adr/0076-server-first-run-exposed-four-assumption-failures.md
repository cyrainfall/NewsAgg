# 阶段 2 第一次在服务器上跑：四个失败都来自同一件事——脚本假定环境是本机的

## Context

阶段 1 把服务器环境装齐了（ADR-0060 vendor 的 wewrite、ADR-0071 纯 HTTP 读数 + cookie 注入、ADR-0075 浏览器登录态拷贝、ADR-0060 的 `bootstrap-server.sh`），然后阶段 2 第 10 步要在服务器上先跑 dry-run。

第一次跑，脚本在**四个不同的地方**摔倒了，而每一个都曾经在本机发生过或看起来像「不可能发生」：

| # | 症状 | 真相 |
|---|---|---|
| 1 | `SyntaxError: future feature annotations is not defined` | 服务器系统 `python3` 是 **3.6.8**（Alibaba Cloud Linux 3 自带），全部脚本用了 `from __future__ import annotations`（3.7+ 特性） |
| 2 | `Author identity unknown` → `fatal: unable to auto-detect email address` | 服务器上 clone 出来的仓库没有 `user.name`/`user.email`，收尾的 `git commit` 失败 |
| 3 | `error: cannot pull with rebase: You have unstaged changes.`（**并且每轮都这样**） | ② 失败时 `git add -A` 已执行、commit 失败 → 索引停在半暂存状态 → 下一轮 ① 撞上它 |
| 4 | `读回失败（阶段 fetch）：找不到浏览器 chrome 的 profile` | cookie 文件在服务器上，但没告诉 `performance.py` 去哪找 → 回落 `from_browser()` → 服务器没 Chrome |

## Decision

**把「脚本假定环境」这件事当成一类缺陷来修，而不是逐个打补丁。**

### 一、解释器：显式找，并验证版本（ADR-0076）

编排器新增 `find_python()`，按候选顺序找**版本 ≥ 3.9** 的解释器：

```
.uv-tools/wewrite/bin/python → .uv-bin/python
→ /usr/bin/python3.11 / 3.12 / 3.10 / 3.9
→ uv 装的 cpython-3.12 / 3.11
→ 系统 PATH 的 python3（版本够新才用）
```

每个候选都用 `sys.version_info >= (3,9)` **实测**，不靠文件名猜（`python3.11` 这个名字本身不保证它真是 3.11）。找不到就在整轮开始前硬失败并说清修法。

`run_all.sh` 里 24 处 `python3` 全部换成 `"$PY"`——包括 9 处 heredoc 和 8 个技能脚本调用。

### 二、git 身份：写进 bootstrap

`scripts/bootstrap-server.sh` 新增一段：仓库级设置 `user.name`/`user.email`（不改全局，ADR-0018），并把「git 身份」加进 `--check` 的缺项清单。

### 三、索引脏：只处理系统自己每天写的路径

① `git pull` 之前，若 `reports/` 有未提交内容，先**单独提交**再同步。两条理由：

1. 判据是「这个路径是本系统每天写的」，不是「这个文件我看着像垃圾」——前者可判定，后者是猜测（ADR-0018：不替人决定什么该丢）。
2. 按 ADR-0012，`reports/` 本来就该入库（简报是历史档案）。它不是垃圾，是当天该留下的东西，提交它是对的而不是权宜之计。

不碰 `outputs/`、`knowledge/`、`platforms/`、`style/`——那些可能是你改的。

### 四、cookie：编排器自己指路

`<repo>/.newsagg/toutiao-cookies.json` 存在时自动设 `NEWSAGG_COOKIE_FILE`（不覆盖已设的值）。显式设的环境变量优先；文件不存在时不设，留给 `from_browser()` 兜底。

「本机直读 Chrome / 服务器注入」这条分工本来就写在 ADR-0071 里，但只在 SKILL.md 的人读文档里，没进编排器——**「人记得做」与「系统自己做」之间差着一段**。

## 为什么这四个失败值得一起记

它们都不是「代码写错了」，而是**代码在本机是对的、在服务器上是错的**。这类缺陷有三个共同特征：

1. **本机永远测不出来。** 本机 `python3` 是 3.9、仓库有 git 身份、cookie 是自动找到的。
2. **在无人值守时的表现是「跑完了、退出码 0、但什么都没发生」。** ①②③ 都被 `|| true` 或错误兜住过：②b 读回失败后「排序参考与降频判据沿用旧值」，一句提示都没有。
3. **第一个失败是偶发的，之后每一轮都必然失败。** ③ 尤其典型：真正的原因是两轮之前的 commit 失败，而每天的错误信息都指向「工作区脏」。

第 3 条是这类缺陷最难查的地方：**它看起来像持续故障，实际是一次故障的后果**。修法不能只针对症状（`git stash`），得针对那个「失败会留下状态、状态会阻塞下一轮」的结构。

## 一条更普遍的判据

**凡是在编排器里以「命令名」调用外部东西的地方（`python3`、`node`、`git`、`uv`），都要问一句：这个命令在这台机器上还指着我以为的那个东西吗？**

仓库里已经有三个同型案例，各自留下一个 ADR：

- ADR-0039：封面用系统 python 跑，缺 Pillow → 每天白跑一次
- ADR-0052：`node` 由 nvm 装在用户目录、不在定时任务的 PATH 里 → `FileNotFoundError: 'node'`，静默丢了两天表现数据
- ADR-0060：`uv` 在 `~/.local/bin` 不在 PATH → bootstrap 脚本找不到它
- ADR-0076（本 ADR）：系统 `python3` 版本太旧 → 分拣、简报、表现读回**全部**在第一步就崩

四个案例的共同点：**交互式 shell 里都能用，定时任务里不能用。** 而定时任务是无人值守的唯一形态。

## Consequences

- 服务器上 `run_all.sh --no-collect` 现在能跑通第① ② ②b ③ ③b 五步：pull ✓、风格学习 ✓（11 篇成稿、0 篇需学习）、表现读回 ✓（**平台作品 11 篇、本账号 11 篇、新增记录 1 条**，基准阅读率 0.002217、阈值 0.001108、连续低迷 0 篇 → ok）、分拣输入包 ✓（102 条候选、8 张卡、2 张在冷却）。
- 内存：`available 940MB`（总 1870MB）。**整个第一段没有起过浏览器**——这正是 ADR-0071 想要的：Chrome 只在发布那一刻才起来。
- 服务器上**还没有 DSH 定时任务**（schedule 域只在 web-app bundle 里，headless profile 不含）。要么在服务器上用 web profile 建，要么用 systemd/cron 跑 `dsh headless`（已实测 `dsh headless` 可用，见本轮勘察）。
- 服务器上**还缺代理**：`r.jina.ai` 直连不通 → ADR-0021 tier-0 的 `web`（Jina Reader）会带着绿色状态失效（ADR-0059 残余风险 53）。装 mihomo 照搬订阅分流（ADR-0022）还是把 Jina 降级并在简报说明，**尚未决定**。
- 阶段 2 第 10 步的模型部分（分拣判断 → 写作 → 封面 → dry-run 发布）还没在服务器上跑过——那部分需要 DSH 在服务器上能定时触发。
