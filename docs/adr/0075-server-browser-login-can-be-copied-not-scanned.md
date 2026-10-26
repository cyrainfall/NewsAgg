# 服务器的浏览器登录态可以拷过去，不必人工扫码

## Context

阶段 1 第 9 步要把发布链路搬到 2GB 内存的 ECS（`i-wz93frlza7g5nz9ft5dc`）。ADR-0073 判定纯 HTTP 写路径不可启用，所以**服务器上的发布仍然要起浏览器**——浏览器要的是 toutiao-ops 自己那份 Playwright profile 里的登录态。

第一次在服务器上跑 `node index.js auth check` 得到：

```json
{"logged_in": false, "account": "default", "message": "未登录，请执行 auth login 扫码登录"}
```

这意味着每台新机器、每次重建环境，都要有人守在服务器前扫码一次。而 ADR-0072 已经在「cookie 每月要手动续一次」这件事上吃过亏——多一个人工步骤就多一次忘记的机会。

当时的判断是「Playwright profile 的 cookie 解不开，所以只能扫码」。

## 那个判断只对了一半

ADR-0071 当时的实测是：`.home/.toutiao-ops/accounts/default/browser-data/Default/Cookies` 里 48 条 toutiao cookie **全部 `encrypted_value` 有值、`value` 为空**；用 `peanuts/saltysalt/1`、`peanuts/saltysalt/1003`、Chrome Keychain 口令、`""`/`playwright`/`chrome` 口令逐一试 → **全部解出非 ASCII 垃圾**；该 profile 的 `Local State` 里 `os_crypt` 是**空 dict**。

所以**用 SQLite 直读那份 profile 是行不通的**。但那句话被读成了「Playwright profile 的 cookie 拿不出来」。

实际上，Playwright 启动 Chromium 之后，**通过 CDP 拿到的是已经解开的明文 cookie**——那份密文是给「直接开 SQLite 的人」看的障碍，对「把浏览器起起来」的人不成立。

实测（2026-10-27，本机）：

```js
const ctx = await chromium.launchPersistentContext(profileDir, {headless: true, ...});
const cookies = await ctx.cookies();   // 明文，47 条 toutiao cookie
```

47 条里带 `sessionid`（32 字符，`.toutiao.com`）、`sid_tt`、`uid_tt`、`sid_ucp_v1`、`passport_csrf_token`、`sso_uid_tt`、`toutiao_sso_user`、`sid_guard`、`ttwid`、`odin_tt`、`tt_anti_token`、`csrftoken`——**登录态所需的那一套都在**。

## Decision

**把「读 Playwright profile 的 cookie」做成一对脚本，让服务器能靠拷文件获得登录态。**

### 一、导出：`scripts/export-pw-cookies.mjs`

- 从 `HOME/.toutiao-ops/accounts/<account>/browser-data` 起一个 headless persistent context，`ctx.cookies()` 拿明文。
- 过滤 `domain` 含 `toutiao` 的，输出格式与既有的 `scripts/export-cookies.py` **完全一致**（`{cookies:[{domain,name,value,path,secure,http_only,expires}]}`），所以两边的消费方可以互换。
- 写盘后 `chmod 600`；**只打印条数与到期日，绝不打印值**（沿用 ADR-0071 的理由：值一进终端就可能被粘进聊天记录或日志）。
- 没有 `sessionid` 就退出码 1 并说明「该账号还没登录过，或登录态在别的 account 下」。

### 二、注入：`scripts/import-pw-cookies.mjs`

- 在目标机器上起 persistent context，`ctx.addCookies(...)`，关掉时 Playwright 会把它落进 profile 的 SQLite。
- 之后 `node index.js auth check` 就能读到——**这一步是真正的验证，不是「写入成功」的假象**。
- 同样不打印值。

### 三、到期日要分两个口径报

第一版只报「全部 cookie 里最早到期」，实测是 **2026-10-28**——但那是 `gd_random` 这类一次性 cookie。而真正承载登录态的 `sessionid` / `uid_tt` / `sid_ucp_v1` / `passport_csrf_token` 到 **2026-12-13**（约 47 天），`sid_guard` 到 2027-10-09、`ttwid` 到 2027-10-27、`odin_tt` 到 2027-11-18。

**报一个「2026-10-28 过期」会让人以为这份 cookie 三天后就没了**，从而做一次不必要的重新扫码。所以现在同时报 `earliest_expiry`（全部）与 `earliest_auth_expiry`（登录态那批），后者才是续期依据。

### 四、这条路不取代人工扫码

它是「把人工做过的扫码结果搬个家」，不是「凭空造出登录态」。本机的 Playwright profile 之所以有 47 条 cookie，是因为**人在这台机器上扫过一次码**。新环境第一次用仍然要扫一次，之后就能互相拷贝。

## 实测

| 步骤 | 结果 |
|---|---|
| 本机 `export-pw-cookies.mjs` | 47 条、`has_session: true`、`earliest_auth_expiry 2026-11-13`（`sessionid` 实际到 2026-12-13） |
| 本机 `import-pw-cookies.mjs` → `auth check` | `logged_in: true`、`username: "淆雨学学"`、`userId 111091683370` |
| 上传 11.6KB cookie 文件到服务器 → `import-pw-cookies.mjs` | `injected: 47` |
| **服务器** `auth check` | `logged_in: true`、`username: "淆雨学学"`、`userId 111091683370` |
| **服务器** `content list --type article --status published` | 返回真实文章列表（含 `AI视频省下的成本，正被模型厂商收走`，`user_name: 淆雨学学`） |
| 服务器上删除中转的 cookie 文件 | 已删（`/opt/newsagg/pw-cookies.json`），凭证不留在 `/opt` 根目录 |

服务器内存：跑完 Xvfb + 浏览器后 `available 910MB`（起浏览器前 891MB）→ **Chrome 用完就退**，内存压力从「全天占用」降为「发布时占用」。

## Consequences

- **服务器不再需要人工扫码**（前提是至少有一台机器登录过）。这直接解掉了「阶段 1 换机要人守着」这件事。
- **多了一条登录态的传播路径**。`export-cookies.py`（本机 Chrome → 纯 HTTP 用）与 `export-pw-cookies.mjs`（Playwright profile → 浏览器用）现在都会产出同格式的 cookie 文件。**两者不能混用**：前者解的是 Chrome 的密文、后者读的是 Playwright 的明文，而它们的用途不同（纯 HTTP 读回 vs 浏览器写路径）。ADR-0071 说「两套独立的登录态」现在要精确化成：**两份 cookie 文件，指向同一个账号**。
- **每次迁移都过一遍明文文件**，所以传输环节必须用 scp / workbench upload，且落地后 `chmod 600`。`.gitignore` 的 `*cookie*.json` 规则覆盖这两种文件名。
- **服务器上仍然没有任何东西记录「上次注入是什么时候」**（ADR-0072 残余风险 69 的一半）。现在有两条提醒路径，但都只在有人打开简报时才生效。
- ADR-0071 那句「browserless 读不到 Playwright 自己的 profile」需要勘误：读不到的是**那份 SQLite 的密文**，读得到的是**浏览器进程里的明文**。
