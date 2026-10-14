# toutiao-ops 集成实测：状态重定向、浏览器构建与错误语义

## Context

按 ADR-0027 的阶段 0，本地实际安装并运行 `toutiao-ops` 以"先撞墙"。实测推翻了 ADR-0024 中的两处判断，并暴露了三项接口事实。

## 实测发现

### 一、状态目录由 `homedir()` 硬编码，需以 `HOME` 重定向

`cli/src/browser.js:9` 写死 `const BASE_DIR = join(homedir(), '.toutiao-ops')`，账号会话、浏览器数据、截图全在其下。在 workspace-write 沙箱下写入 `~/.toutiao-ops` 被拒（`EPERM`）。

**解法：以 `HOME` 环境变量重定向**，而非修改源码。副作用：Playwright 的浏览器缓存路径同样随 `HOME` 走（`$HOME/Library/Caches/ms-playwright`），因此**状态与浏览器构建被绑定在同一 HOME 下**——两者必须同时重定向，不能只改其一。

### 二、错误语义：退出码是正确的

**此处更正 ADR-0024。** 该 ADR 曾据"`auth check` 报了 error 但看起来退出了 0"推断其错误处理有问题。实测表明**该观察是错的**：`index.js` 的 `run()` 包装器在 catch 分支中执行 `process.exit(1)`，真实退出码为 1。

先前的误判源于**管道掩码**：`node index.js auth check | tail -20` 的退出码是 `tail` 的退出码，恒为 0。这是一个观测方法错误，不是工具缺陷。

因此 ADR-0024 中"乐观返回"的批评需要收窄：**问题不在退出码，而只在发布路径的返回值**——`publish-article.js` 在点完「确认发布」后仅 `sleep` 便 `return { success: true, action: 'published' }`，且 `confirmPublish.click({ timeout: 10000 }).catch(() => {})` 吞掉点击失败。即：**CLI 层的失败会正确报错，但"发布是否真的成功"没有被验证**。ADR-0024 的"发布后反查验证"因此依然必要，且其必要性完全集中在这一个点上。

### 三、Playwright 版本与浏览器构建不匹配

`toutiao-ops` 依赖 `playwright ^1.50.0`，实装 **1.64.1**，其 `browsers.json` 要求 **chromium revision 1248**。本机缓存中仅有 1208/1223（其他项目的），因此 `launchPersistentContext` 直接抛"Executable doesn't exist"。

**这不是 toutiao-ops 的缺陷**，而是"跳过 `postinstall`（`npx playwright install chromium`）"的必然后果——ADR-0024 要求阻止其另装浏览器以复用系统 Chromium，但**系统 Chrome 与 Playwright 期望的构建不是一回事**：Playwright 需要它自己版本对应的特定构建，无法直接指向 `/Applications/Google Chrome.app`。

**结论：ADR-0024"复用系统 Chromium"的设想不可行**，至少不能通过"阻止安装"实现。修正方案：**让 Playwright 安装其匹配的构建**（`playwright install chromium`，约 150MB），并将缓存位置随 `HOME` 落在 workspace 内。40GB 服务器盘容得下，而"装两份浏览器"的担忧应让位于"能跑起来"。

## Decision

**一、以 `HOME` 重定向 toutiao-ops 的全部状态**（会话、浏览器数据、截图）与 **Playwright 浏览器缓存**，两者绑定同一 HOME，落在仓内被 gitignore 的目录（如 `.home/`）。不修改源码中的 `homedir()` 调用。

**二、接受 Playwright 安装其匹配的浏览器构建**，放弃"复用系统 Chromium"。安装时显式指定 workspace 内的 HOME，使构建落入仓内缓存目录。

**三、错误处理以退出码为准，但发布结果不以返回值为准。** 所有调用 `toutiao-ops` 的地方必须检查退出码（`set -o pipefail` 或不经管道取码）；而发布是否成功，一律以 ADR-0024 的反查验证（`content list` 比对标题）为准，不看 `success` 字段。

**四、内存参数仍按 ADR-0024 注入**，但改为通过 fork 的最小补丁或启动包装实现——此项与上面的两者独立，未因本次实测而改变。

## Consequences

- **`HOME` 重定向成为所有调用的前置条件**。任何忘记设置的调用都会静默尝试写 `~/.toutiao-ops`，在沙箱下得到 `EPERM`（退出码 1，可被发现），但在无沙箱的环境（如服务器）会**真的写到用户 HOME**，造成会话分散在两处。因此重定向必须封装到一个调用包装里，而不是靠每次记得加。
- **浏览器构建约 150MB 且绑定 Playwright 版本**。升级 `playwright` 会要求新的构建，因此版本钉定（ADR-0015）同样适用于它，否则`playwright` 浮动升级会让浏览器缓存失配。
- **观测方法错误是一个需要记录的教训**：管道会掩盖退出码。任何"命令是否成功"的判断都必须不经管道取码，或用 `set -o pipefail`。这一条同时适用于本项目自己的脚本。
- ADR-0024 的"发布后反查验证"被本次实测**强化**了必要性，因为它是防住"乐观返回"的唯一手段；但它的理由已从"工具普遍不可信"收窄为"发布路径的返回值不可信"。
