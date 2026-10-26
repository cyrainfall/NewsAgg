# 表现回路改用纯 HTTP 后端，服务器上不再需要 Chrome

## Context

阶段 1（服务器部署）卡在一个具体的技术条件上：服务器 `i-wz93frlza7g5nz9ft5dc`
只有 2GB 内存（实测 `available 789MB`，且已被 DSH web + uvicorn + dockerd +
postgres 占掉大半），而浏览器路径单次读回就要 **1GB+ 内存**：

| 进程 | RSS |
|---|---|
| Playwright Chromium（主） | 403MB |
| Playwright Chromium（渲染） | 231MB |
| Playwright Chromium（GPU） | 207MB |
| Chrome 宿主 | 215MB |

ADR-0023 那套 2GB 内存防御（`--min-free-mb` 预检、不用 `--single-process`、
杀进程回收）存在的唯一原因就是这个数字。

与此同时，ADR-0063 引入的 `feat/browserless-skills` 分支带来了纯 HTTP 客户端
`.agents/skills/newsagg-publish/scripts/toutiao_api.py`，本轮实测它在本机
**真的能用**，而且：

| 动作 | api 后端 | browser 后端 |
|---|---|---|
| 列已发布作品 | **0.93s** | 20.14s（合并后实测） |
| 内存 | 纯 Python 进程 | 1GB+ |
| 逐篇详细指标 | 9.29s（16 篇，0 失败） | 需起浏览器 |

所以问题从「服务器上怎么跑 Chrome」变成了「**表现回路能不能不跑 Chrome**」。

## Decision

**给 `performance.py` 加一个 `--backend` 开关，默认 `api`（纯 HTTP），浏览器后端保留为兜底。**

### 一、两条后端读的是同一组数字

这是整个决策的前提——换后端如果会改变读数，那基准就变了，降频判定跟着变，
「换后端」就不是换实现而是换系统。

逐篇比对（2026-10-27，本机，10 篇）：

| 展现 | 纯 HTTP `impression_count` | 浏览器 `itemCounter.showCount` |
|---|---|---|
| 阅读 | 纯 HTTP `go_detail_count_v2` | 浏览器 `itemCounter.readCount` |

10 篇逐篇**全部相等**（98/1、170/0、421/1、597/4、668/0、18/2、1084/10、
1214/2、1201/0、1303/0）。所以 api 后端不是「降级的近似」，而是同一组数字的
另一种取法。

### 二、为什么不做成「统一成一个信封」

两条路返回的结构完全不同：

- 浏览器：`items[].data[].assembleCell.itemCell.{articleBase,itemCounter}`（深层嵌套）
- 纯 HTTP：`data.articles[]`（扁平对象，60+ 字段）

强行统一成一个形状，等于在解析层塞一个「兼容两种源」的分支——而分支的判据
只能靠「某个键在不在」来猜。这里选择**各自解析、同一出口**：两条路都产出
同一组 `gid/title/published_at/kind/metrics/read_rate` 字段，
后面的匹配、落库、评估完全共用。`tests/test_performance_api.py` 里
`TestParityWithBrowserPath` 钉的就是这个「同一出口」。

### 三、三个必须显式处理的差异

**1. 草稿。** 纯 HTTP 的作品列表**含草稿**（实测 5 条），浏览器那条路不含。
草稿的展现恒为 0，混进去会把「没被展现」当成「被展现了但没人点」。
→ api 路径显式过滤草稿，并在 `meta.drafts_skipped` 里记数。

**2. 覆盖范围。** 实测浏览器后端返回 10 篇，api 后端返回 11 篇已发布
（多出《模型打五折，未必是好事》）。原因未知，可能是浏览器路径的分页参数
或 `status` 语义。**api 更全不是坏事**——但基准的样本集变了这件事必须知道。
（这一条是本决策里唯一没查清根因的。）

**3. 微头条。** 纯 HTTP 列表里 `article_type` 全是 0，**微头条根本不在这个
接口里**。浏览器后端能取到微头条是因为它拦截的是另一个接口。

→ **api 后端在微头条上瞎**。ADR-0047 的泄漏判据（数同标题出现几次）在 api
后端下会漏掉微头条。所以：**api 后端只用于表现数据读取**（我们的内容都是
文章），**发布反查仍然走浏览器后端**（`publish.py --verify-backend` 默认
`auto`，跟随写入后端，写入默认回落浏览器）。

### 四、账号身份核对

api 读**本机真实 Chrome** 的 cookie 库；浏览器后端读 `.home/.toutiao-ops/
accounts/default/browser-data`（Playwright 自己的 profile）。实测这两个
profile 的 cookie **互相解不开**（Playwright profile 的 `Local State` 里
`os_crypt` 是空 dict），所以它们是两个独立的登录态，**可能是两个账号**。

→ 加一道判据：**api 读回来一堆文章却一篇都不是我们的** → 判定为账号不对，
回落浏览器后端再取一次（浏览器那条路的账号是确定的，它就是发布时用的那个）。
这种情况如果不处理，脚本会安静地报「平台上有 11 篇文章，我们 0 篇」，
而真实原因是「登录了另一个号」。

### 五、降级路径

api 失败（网络断、cookie 读不到、平台改结构）→ **回落浏览器后端**，
并在 `fetch_meta` 里记 `fallback: "browser"` 与原始错误。

理由：api 依赖本机浏览器 cookie（服务器上要靠注入，ADR-0063 的实测：
`from_browser()` 在无 Chrome 环境直接抛 `CookieError`），注入坏了是**运维
问题**，不是数据问题；而浏览器后端是更老、更可靠的那条路。

### 六、服务器上的 cookie 从哪来：注入，不是读浏览器

`from_browser()` 依赖本机 Chrome 的 cookie 库，服务器上直接失败：

```
CookieError: 找不到浏览器 chrome 的 profile（未安装或未使用过）
```

（`HOME=/tmp/fakehome-empty` 实测。）

而 toutiao-ops 的 Playwright profile **解不开**：`.home/.toutiao-ops/accounts/
default/browser-data/Default/Cookies` 里 48 条 toutiao cookie 全部
`encrypted_value` 有值、`value` 为空；该 profile 的 `Local State` 里
`os_crypt` 是**空 dict**（没有 `encrypted_key`）。用 `peanuts/saltysalt/1`、
`peanuts/saltysalt/1003`、Chrome Keychain 口令、`""`/`playwright`/`chrome`
口令逐一试 → 全部解出非 ASCII 垃圾。

→ **browserless 与 toutiao-ops 是两套独立的登录态**，服务器上必须**注入**。
新增三层：

| 层 | 位置 | 作用 |
|---|---|---|
| `Session.from_cookie_file(path)` | `.agents/lib/browserless/http.py` | 读 JSON cookie 文件建会话；**权限太宽直接拒绝** |
| `Client.from_cookie_file(path)` | `.agents/skills/newsagg-publish/scripts/toutiao_api.py` | 上层入口 |
| `scripts/export-cookies.py` | 仓根 | 本机导出：`--browser chrome --out <path>` |

**权限检查为什么在这一层**：这个文件是**账号凭证**，它的产生方式注定它会被人
`scp` 上服务器。组或其他用户可读 = 账号被交出去。所以硬性要求
`mode & 0o077 == 0`（即 0600），否则拒绝加载并说清 `chmod 600` 怎么改。
这不是泛泛的安全建议——它防的是一次 `chmod 644` 手滑。

**不提供「从环境变量读 cookie」**：环境变量会进 `ps e`、进 shell history、进
systemd unit 文件。文件 + 0600 + gitignore 是更可控的一条路。
`performance.py` 用环境变量 `NEWSAGG_COOKIE_FILE` 选这条路径（优先级：
环境变量 > 本机 Chrome）。

**导出脚本不打印任何 cookie 值**：只报条数、域、权限位。`Cookie.to_dict()`
的值一进终端就可能被粘进聊天记录、粘进 issue、粘进日志。
`tests/test_browserless.py` 已经为 `Cookie` 的 `repr()` 钉了不泄漏，这里补的
是**导出脚本本身**不泄漏。

### 七、1003 是「无数据」，不是失败

`code == 1003` 且 `--content-type` 合法 → 记 `no_data`，不记失败。理由：
平台规则是**作品发布次日 14:00 后数据才可绪**，当天新发的文章必然读不到。
把它当失败会让「刚发的那篇」拖垮整批。

### 八、逐篇串行，不并发

浏览器版在页面内也是逐个 `await`（`reference/toutiao-ops/cli/src/analytics.js:112`）。
纯 HTTP 的频控阈值未知，沿用保守做法：16 篇约 9.3 秒。**并发被频控挡掉而
错判成「无数据」，比慢更糟**——慢只是慢，错判会让数据静默缺失。

## 实测

本机实跑 `performance.py --backend api --dry-run`：

```
ok True  matched 11  on_platform 11
parse {"backend": "api", "count": 11, "source": "api_article_list", "drafts_skipped": 5}
real 0m7.772s
```

对照浏览器后端（同一台机器）：20.14s 起，且要 1GB+ 内存。

**cookie 注入端到端**（本机真实跑）：

```
python3 scripts/export-cookies.py --browser chrome --out /tmp/c.json
→ 已导出 25 条 cookie，权限 0o600，域 .toutiao.com
python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py --cookie-file /tmp/c.json login
→ {"ok": true, "logged_in": true, ...}
python3 .../toutiao_api.py --cookie-file /tmp/c.json list --limit 3
→ 16 条，含 gid / 标题 / 状态 / 草稿标记
NEWSAGG_COOKIE_FILE=/tmp/c.json performance.py --dry-run --backend api
→ articles_matched 11 / unmatched []
```

权限拒绝路径也验了：`chmod 644` 后 → `{"ok": false, "error": "cookie 文件 …
权限太宽（mode=0o644）… 改成 `chmod 600` 再试。"}`，退出码 1。

## Consequences

- **服务器上的表现回路不再需要 Chrome。** 这是阶段 1 最大的单点障碍解除。
  但**发布**仍然需要 Chrome——api 写路径（`save_draft`/`publish_article`）
  至今未验证，`UnverifiedWrite` 仍然默认拒绝执行。→ **阶段 1 仍然必须装
  Chrome + Xvfb**，只是表现回路这一段不再是内存杀手。
- **cookie 每月要重新导出一次。** 实测主体 cookie 到 2026-11-19（约 23 天），
  `ttwid` 到 2027-10-25。服务器上靠注入（`Cookie.to_dict()` → `CookieJar` →
  `Session`）→ 阶段 1 必须新增「把 cookie 从本机安全送到服务器并定期刷新」
  这件事，今天没有。
- **基准的样本集可能变。** api 比浏览器多返回一篇已发布文章（见上文第 3 点）。
  第一次用 api 跑完，基准的组成会和之前不同。这是**测量方式变了**，不是
  表现变了。落地后要重跑一次并对比 `verdict.baseline_read_rate`。
- **微头条的泄漏判据在 api 路径下不存在。** 已在代码注释里写明「api 只用于
  表现读取，反查走浏览器」。将来若有人给发布也切 api，会静默失去
  ADR-0047 的微头条泄漏检测——所以这条必须留在代码里而不是只写在 ADR 里。
- **`content-detail-batch` 仍是浏览器里 `page.evaluate` 发 fetch**，
  没有跟着改成纯 HTTP。本轮在 `performance.py` 里另写了一份纯 HTTP 版本
  （`fetch_content_details_api`），但 `reference/toutiao-ops/cli/src/analytics.js`
  里的那条命令还是老样子。两份实现**读同一组数字**（`item/info` 接口），
  所以不是「两个真相」——但确实是两处代码。
- **两个后端读数今天逐篇相等，但那是两个独立实现的巧合，不是保证。**
  平台哪天改一个字段的口径，只有一边会变，而基准仍然算得出来（基准就是
  这些数的中位数）——信号会静默偏移。生产链路上**没有「两条路对一遍」这一步**，
  因为默认走 api、浏览器只作兜底。测试里的逐篇对照（`TestParityWithBrowserPath`）
  只在夹具里，不在生产链路上。

