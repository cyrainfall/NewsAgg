# 推翻「发布链路自建」：复用 toutiao-ops，NewsAgg 只包一层

## Context

此前 Q33-A 决定"头条发布链路 NewsAgg 自建"，理由是"工作区里没有头条发布代码"。**该结论是错的**——它只查看了 `.agents/skills/`，漏了项目根目录。

项目根目录下的 `toutiao-ops/`（npm 包 `@openclaw-cn/toutiao-ops`，[jiulingyun/toutiao-ops](https://github.com/jiulingyun/toutiao-ops)，MIT）是一个完整的今日头条创作者平台运营技能包，覆盖：

| 能力 | 对应此前的决策 | 状态 |
|---|---|---|
| 多账号登录、QR 扫码、会话持久化 | Q84 首次人工扫码 | ✅ 已实现 |
| `auth check` 登录态检测 | Q66 发布前预检登录态 | ✅ 已实现 |
| 文章发布（Markdown 富文本、封面、声明） | Q65 发布技能职责 | ✅ 已实现 |
| `--declaration` 支持 `引用AI` | Q71 AI 标识 | ✅ 原生支持 |
| `analytics works` / `content-detail` | Q41 数据读回 | ⚠️ **接口存在但数据不可用**（ADR-0037 实测修正） |
| `content list`（含 title/status） | Q134 发布反查验证 | ✅ 可用，**且每篇自带 `itemCounter`**——循环 C 的唯一数据源（ADR-0037） |
| `inspiration`（创作活动 + 热点推荐） | 采集侧 | ⚠️ 未纳入（见下） |

> **能力表的两处修正（ADR-0037 实测）**：`analytics works` 只返回**账号级聚合**且 `source: dom_scrape`（`metrics` 里混着省份名与频道来源百分比）；`analytics content-detail` 的上游 API `/mp/agw/statistic/v2/item/info` 返回 `{code: 1003, item_data: {}, message: "数据开小差啦~请稍后再试"}`，**空数据**。而 `content list` 的每篇作品自带 `itemCounter`（展现/阅读/点赞/评论/转发），循环 C 因此**不需要** `content-detail`。
>
> 教训：**本表是"接口存在"的证据，不是"数据可用"的证据**。✅ 只表示命令能跑通并返回 200，不表示返回的内容是所声称的东西。

其技术方案比本 ADR 原设想更成熟：`playwright-extra` + `puppeteer-extra-plugin-stealth` 隐藏自动化特征、`launchPersistentContext` 保留真实指纹、逐字输入带随机延迟、主动关闭弹窗与拒绝地理位置权限。数据类操作走**浏览器内 `fetch`/API 拦截**（复用登录 session），而非直接 POST ——这正是规避风控的正确做法。

**因此 Q33-A 与 Q47 被推翻**：既不需要自建发布链路，也不需要复用 `xiaohongshu-skills` 的 CDP 层（该包的 `bridge.py` + `XHS Bridge` 扩展本就是小红书专用，其 `manifest.json` 的 `host_permissions` 只含 `*.xiaohongshu.com`）。

## Decision

**复用 `toutiao-ops` 作为头条发布与数据读回的执行层**，NewsAgg 在其上包一层，只补它缺的三件事：

**一、发布后反查验证（防静默假成功）。** 其发布路径的返回是**乐观的**：点完「确认发布」后只 `sleep` 便 `return {success: true}`，且 `confirmPublish.click().catch(() => {})` 吞掉点击失败。因此**不信任其发布返回值**，发布后调用 `toutiao-ops content list` 核实该标题确实存在于作品列表；不存在则视为未发出，走 ADR-0011 的核实流程（**不重发**）。

> **受 ADR-0028 更正**：原判断"它连失败都吞掉"仅适用于**发布路径**。CLI 层其余命令的退出码是正确的（`run()` 在 catch 中 `process.exit(1)`）；先前观察到"报错但退出 0"是**管道掩码**造成的观测错误。因此本条的适用范围收窄为"发布结果的返回值不可信"，而调用其它命令时**应当检查退出码**。

**二、内存参数注入（2GB 约束）。** 其 `LAUNCH_ARGS` 仅含四个参数（`--disable-blink-features=AutomationControlled`、`--no-first-run`、`--no-default-browser-check`、`--disable-infobars`），**缺少服务器必需项**。补入 `--disable-dev-shm-usage`、`--no-sandbox`、`--renderer-process-limit=1`、`--disable-gpu`；因 CLI 不透传环境变量，此项需**最小改动的 fork 入仓**（与 ADR-0015 的钉版本策略一致）。

> **受 ADR-0028 更正**：此处原写"复用系统 Chromium，阻止其 `postinstall` 另行下载"，**实测证明不可行**——Playwright 需要其版本对应的特定构建（本机 1.64.1 要求 chromium revision 1248），无法指向系统 Chrome。已改为接受 Playwright 安装匹配构建（约 150MB）。同时"状态与浏览器缓存需以 `HOME` 重定向"也是实测新增的必要条件。

**三、把两步发布作为读回校验点（Q118）。** 它的流程是「预览并发布」→「确认发布」两步，恰好是校验插入的位置：第一步之后、第二步之前读回表单内容与预期比对。

**不纳入 `inspiration`。** 它的创作灵感（创作活动 + 热点推荐）与本项目自建采集层职责重叠，而 ADR-0004 已确立"采集与分拣全部自建"（因为事件聚簇需要原始条目与稳定字段）。

## Considered Options

- **维持自建**：完全可控，但要重写已验证的 stealth 反检测与持久化指纹——而这两者恰恰是最难做对的部分。
- **fork 后自行维护**：可控性与复用兼得，但与钉版本策略叠加后需自行跟进上游修复。实际采用"最小改动 fork"，改动面应尽量小以降低跟进成本。

## Consequences

- **「发布成功」的含义被重新定义**：不再指"命令返回 success"，而指"`content list` 中能查到该标题"。这是无人值守下唯一可信的判据。
- **发现一个新状态：`reviewing`（审核中）。** `content list` 的 `--status` 支持 `published` / `reviewing` / `rejected`。这意味着发布后内容**不一定立即可见**，可能处于审核中。因此反查验证必须把"查到但状态为 reviewing"视为**已成功提交**（不是失败），而 `rejected` 必须告警——它是内容违规的信号，直接关系到 ADR-0013 的信用分风险。
- **上游耦合**：`toutiao-ops` 是活跃的第三方包（`postinstall` 会下载浏览器、CLI 有 `update-check`），其选择器依赖头条页面结构。头条改版会导致其失效，因此**它的失效模式是"选择器漂移"**，这正是 ADR-0020 的读回校验要拦的东西——两者互补。
- 该包的 `update-check.js` 会访问 npm registry，在代理分流下需确认规则（属境外流量）。
- `toutiao-ops` 未 vendor 时的状态与 ADR-0015 的钉版本意图一致：既然要改（注入内存参数），就必须入仓锁定，不能依赖 `npm install -g` 的漂移版本。
- 本 ADR 修正了一处此前的判断错误：**"工作区没有头条发布代码"的结论来自只看 `.agents/skills/` 的不完整检索**。教训是范围性的：`reference/` 之外的顶层目录同样需要检索。
