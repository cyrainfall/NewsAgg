# 默认走协议层，浏览器降级为显式兜底：一次「打开浏览器」到底贵在哪

## Context

本仓的发布类技能一直是**浏览器驱动**的：`bilibili-skills` / `xiaohongshu-skills` / `douyin-skills` 走 bridge server（ws://localhost:9333/9334/9335）连浏览器扩展，在真实页面里执行点击、输入、上传；`newsagg-publish` 走 vendored 的 `toutiao-ops`，用 Playwright 启动持久化 context 操作头条发布页。

这条路当初是对的：它绕开了三个平台的反爬签名（B站的 `wbi`、小红书的 `xs`/`xt`、抖音的 `a_bogus`/`X-Bogus`），把「协议难度」换成了「UI 稳定性」。但它同时把**一切业务都押在浏览器可用上**：

- 每次调用都要保证 Chrome 在跑、扩展已连、页面能加载。其中任何一环坏了，业务就整段失败，而失败信息通常是「超时」这种指向不明的东西。
- 浏览器窗口会抢焦点、吃内存（ADR-0023 就是为 2GB 机器加的 `--disable-gpu --renderer-process-limit=1`）。
- 无头/远程/CI 环境里根本没有「用户的那个已登录 Chrome」。
- 一次发布要 8.5 分钟（B站投稿实测），期间全程占着一个浏览器和一个可见窗口。

用户在 2026-10-25 提出：参考 `kd-devops` 的做法，把这些技能改成「不用打开浏览器也能处理业务」，脚本用 Python，保证跨平台。

## 调查：kd-devops 到底怎么做的

这是本次决策的事实基础，值得写清楚，因为它是**反面教材与正面教材并存**的案例。

`kd-devops`（`/Users/cyx/repo/kd-devops`）的业务命令形态是 `dist/devops_cli.exe <模块> <操作>`，直连 `https://devops.kingdee.com:8000` 的 `getConfig.do` / `invokeAction.do` / `batchInvokeAction.do`，认证态存在 `dist/.cache.dat`（pickle，含 `session.cookies`），失效时 `raw -login` 自动刷新。也就是说：

**它的主路径一行浏览器代码都没有。** 认证态来自本地缓存，业务走 HTTP。

它确实带了一个浏览器组件 `ext/simple_capture.pyz`，但那是**二开的抓包录制器**，用途是「人操作、脚本取证」：解包后只有一个 1224 行的 `__main__.py`，零依赖单文件，纯 CDP 不用 Playwright，对浏览器只做 4 个动作 —— `Network.enable`、`Network.setCookie`、`Page.navigate`、`Network.getResponseBody`。**没有任何点击、输入、元素定位。** UI 操作永远由人完成，脚本只旁听协议。

这个范式可以拆成两句可迁移的话：

1. **认证态从本地复用，不是每次重新登录。**
2. **业务走协议层（HTTP），浏览器只在需要取证或人机协同时出现。**

第一句是本仓可以直接照搬的。第二句里「浏览器只做旁听」这一点本仓用不上（我们不是抓包），但它反过来说明：**浏览器不是业务的必要条件，只是获取认证态的一种方式** —— 而认证态还有别的获取方式。

`kd-devops` 也提供了一个反面判据：它的 `devops_cli.exe` 是 PE32+ Windows 可执行（x86-64），在 macOS 上根本跑不起来；`find_chrome` 用 `where` + `Program Files`、user_data 取 `%TEMP%`，全是 Windows 硬编码。**所以「不用浏览器」不等于「跨平台」，用 Python 重写才顺带解决跨平台。** 用户要求「脚本使用 python，保证跨平台兼容」正是针对这一点。

## Decision

### 一、抽一层 `browserless` 底座，四个技能共用

`/Users/cyx/repo/NewsAgg/.agents/lib/browserless/`，三个模块，**全部只用标准库**：

| 模块 | 职责 |
|---|---|
| `crypto.py` | 纯 Python AES-CBC / AES-GCM（含 GHASH）。无 `cryptography` 依赖 |
| `chromium.py` | 跨平台读本机 Chromium 系浏览器 Cookie（只读） |
| `http.py` | Cookie 注入的 HTTP 会话、限速、错误分层（`HttpError` / `ApiError` / `AuthRequired`） |

**为什么不用 `cryptography`**：它在本机系统 python3 和所有已有 venv 里都没装。底座要被四个技能共用，反向给它们加二进制依赖，等于让「clone 下来就能跑」变成「先装 C 扩展」。纯 Python AES 的代价是慢，但 Cookie 解密是几十条记录的一次性操作，几毫秒的差别不值得换一个编译期依赖。

**为什么不用 `requests`**：四个技能里只有两个声明了 requests。底座用 `urllib`，谁都能用。

**为什么读 Cookie 是对的而不是「偷凭证」**：技能原本就在用用户的已登录浏览器，只是通过扩展在页面里点击。读 Cookie 不扩大权限范围 —— 它拿到的是同一份认证态，只是换了搬运方式。约束是**只读、不写、不落盘、不打印**：`CookieJar.__repr__` 刻意只输出浏览器名与域名列表，`describe()` 是唯一可进 JSON 的形式，且不含值。`v20`（App-Bound Encryption）明确抛 `CookieError` 而不是返回乱码。

### 二、每个技能内部：`api.py` 是默认路径，`--browser` 是显式兜底

```
SKILL.md  →  cli.py  →  api.py（协议层，默认）
                    ↘  bridge/playwright（--browser，兜底）
```

判据（决定某个子命令默认走哪条）：

| 情况 | 默认路径 |
|---|---|
| 只读（登录态、列表、详情、账号信息） | `api.py`，无浏览器 |
| 写操作且签名可复现 | `api.py`，无浏览器 |
| 写操作且签名不可复现 | **报错并提示 `--browser`**，不静默降级 |
| 读不到 Cookie（`CookieError`） | 打印中文提示，**自动**降级 `--browser` 一次，仍失败 exit 2 |

### 三、「诚实降级」是硬约束，不是风格偏好

三个平台的签名难度差别巨大，这是本次改造最需要如实交代的部分：

- **头条**：无签名门槛。`/mp/agw/*` 系列带 Cookie 即可，已实测读通了登录态、文章列表、草稿列表。
- **B站**：读接口无签名（`nav`、`x/web/archives`），上传通道 `preupload` 直接返回 `auth` 串，也走 HTTP。
- **小红书**：`creator.xiaohongshu.com/api/galaxy/*` 部分接口无签名（`user/info` 实测可用），但 `www`/`edith` 侧需要 `xs`/`xt` HMAC 签名，算法有版本迭代。
- **抖音**：发布侧需要 `a_bogus`/`X-Bogus`，同样是活体算法。

**遇到签不了的接口，必须抛明确的中文错误并 exit 2，绝不允许返回「空列表」或「假装成功」。** 理由：静默返回空成功比直接失败危害大得多 —— 上层会以为「今天没有新内容」「发布成功了」，而事实是请求根本没被接受。本仓已经吃过这个亏（ADR-0059/0062 的「记录了但没有实现」一族），所以这条写成硬约束。

同理，**签名算法不做长期硬编码**。`xs`/`a_bogus` 会随平台升级换代，硬编码的实现会在某天静默失效。所以 `--browser` 兜底路径必须保留且必须可用 —— 它不是遗留代码，是**签名迭代期的安全阀**。

### 四、门序与账本不动

`newsagg-publish/scripts/publish.py` 的门序（熔断 → 每日上限 → 预检 → 登录预检 → 发布 → 反查 → 账本 verdict）是 ADR-0014/0036/0047/0059/0062 累积下来的，**本次一行不改**。改的只有「执行发布」和「登录预检/反查」这两步的 backend，用 `--backend api|browser` 切换，默认 `api`。`verdict` 的取值集合与账本字段完全保持。

理由：换传输方式不该改变业务规则。如果门序是好的，它应该与「用什么方式发」无关。

## Consequences

**好处**

- 只读业务（看数据、看列表、看登录态）从「要开 Chrome + 扩展 + 8 秒加载」变成「几百毫秒的 HTTPS」。
- 无头/远程/CI 环境下，只读业务第一次成为可能。
- 跨平台：`chromium.py` 覆盖 darwin/linux/win32 三套密钥派生（Keychain / libsecret / DPAPI），不再有 `where`/`%TEMP%` 这类硬编码。
- 底座零第三方依赖，新技能可以直接复用。

**代价与残余风险**

- **纯 Python AES 比 C 实现慢**（Cookie 解密场景可忽略，若将来要批量解密需重估）。
- **签名接口仍必须浏览器**。小红书 `www` 侧、抖音发布侧、B站部分写接口在签名复现前都只能走 `--browser`。这是**已知且已如实暴露**的限制，不是缺陷。
- **Cookie 读取本身会随浏览器升级失效**。Chrome 的 `v20`（App-Bound Encryption）已经会让 `chromium.py` 抛 `CookieError`。届时自动降级到 `--browser` 仍能工作，但「无浏览器」这条路的覆盖面会缩小。**这条需要在 Chrome 大版本升级后复查。**
- **配置了 `--browser` 之外的静默降级只有一处**（`CookieError`）。其他失败（`AuthRequired`/`ApiError`）不降级，因为那说明是账号或参数问题，换浏览器也一样错。
- 四个技能各自的「命令 → 是否需要浏览器」表必须**实测得出**，不能凭代码推断填写。已在各 SKILL.md 中要求。

## 一条更普遍的判据

**当你说「这条路不行，所以要开浏览器」时，先证明「不行」是真的。** 本次调查最反直觉的发现是：三个平台里**只读侧全部可以无浏览器**，包括我以为最难的抖音（`creator.douyin.com/web/api/media/user/info/` 直接 200）。此前把「整个技能」标成「需要浏览器」，是把「写操作的签名难度」错误地推广到了「所有操作」。

把能力边界**按子命令**而不是按技能来划，是这次改造真正的产出。
