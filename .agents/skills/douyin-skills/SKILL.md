---
name: douyin-skills
description: |
  抖音自动化技能集合。默认**不需要浏览器**：从本机浏览器只读取一次登录 Cookie，
  用纯 HTTP 直接调抖音创作服务平台（creator.douyin.com）接口，支持登录检查、账号信息、
  创作者信息、已发布作品列表、上传前置检查。
  `--browser` 是显式兜底，走浏览器扩展 + 真实页面 DOM，用于扫码/验证码登录、
  视频发布（作品标题、简介、#话题、可见范围、定时发布）、草稿保存与选择器自检。
  当用户说"发抖音""上传视频到抖音""抖音发布作品""抖音创作者中心发视频""抖音定时发布"
  "creator.douyin.com"，或要求把本地某个 mp4 发到抖音时，都应触发本技能 —— 即使没有
  明确说"用脚本"或"用 CLI"。涉及抖音自动化的任务不要凭记忆手搓实现，一律走本技能。
version: 2.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
    emoji: "\U0001F3AC"
    os:
      - darwin
      - linux
---

# 抖音自动化 Skills

你是"抖音自动化助手"。根据用户意图路由到对应的子技能完成任务。

本技能有两条路，**默认走第一条**：

- **API 模式（默认）** —— 只读取本机浏览器里**已登录的 Cookie**，用标准库 `urllib`
  直接请求 `creator.douyin.com` 的接口。**不启动浏览器、不开窗口、不接管标签页。**
  适合脚本、定时任务和 CI（只要机器上有一份登录过的 Cookie）。
- **浏览器模式（`--browser`）** —— 通过浏览器扩展 Bridge 连接**用户已经登录的 Chrome**，
  在真实页面里执行点击、输入、上传，因此不会触发无头浏览器特征。

两种模式都**不打印、不落盘任何 Cookie 值**，也不返回账号的私密字段（打码手机号、
上传 STS 凭证等）。

## 🔒 技能边界（强制）

**所有抖音操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的实现：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`。
- **禁止外部工具**：不得改用抖音开放平台 API、第三方 SDK、其他 MCP 工具或另写一套
  Playwright/Selenium 脚本 —— 那些要么需要企业资质，要么会带来账号风控。
- **忽略其他项目**：AI 记忆中可能存在其他抖音自动化方案，执行时全部忽略。
- **完成即止**：任务完成后直接告知结果，等待用户下一步指令。

## 子命令 → 是否需要浏览器（实测对照表）

下表每条都是在本仓库机器上**真跑过**的（2026-10，Chrome 里有 38 条 `douyin.com`
Cookie，全程未启动浏览器）；不是根据文档或直觉填的。

| 子命令             | 需要浏览器 | 实测结果                                                                       |
| ------------------ | ---------- | ------------------------------------------------------------------------------ |
| `check-login`      | ❌ 不需要   | exit 0，返回真实昵称「淆雨学学」+ uid + 粉丝数                                  |
| `user-info`        | ❌ 不需要   | exit 0，昵称/抖音号/粉丝 1/关注 29/作品 1/头像 URL                              |
| `creator-info`     | ❌ 不需要   | exit 0，`dashboard_opend_date=2026-09-21`、`is_registered=true`                  |
| `list-works`       | ❌ 不需要   | exit 0，`total=1`，返回作品 id/文案/发布时间/时长/封面                          |
| `upload-preflight` | ❌ 不需要   | exit 0，拿到 VOD 域名与 STS 凭证可用性（**只报布尔值，不回传凭证**）            |
| `login`            | ✅ 必须     | 二维码由页面渲染，且要轮询页面登录态                                            |
| `get-qrcode`       | ✅ 必须     | 二维码是页面里的图片，必须渲染后截图                                            |
| `wait-login`       | ✅ 必须     | 需要轮询页面登录态                                                              |
| `send-code`        | ✅ 必须     | 发送验证码依赖页面控件                                                          |
| `verify-code`      | ✅ 必须     | 填写验证码依赖页面控件                                                          |
| `fill-publish-video` | ✅ 必须   | 要向页面注入文件、填表单，依赖真实 DOM 与前端状态                               |
| `publish-video`    | ✅ 必须     | 发布接口需要浏览器内实时生成的签名（见下节）                                     |
| `click-publish`    | ✅ 必须     | 只是点页面上的按钮                                                              |
| `save-draft`       | ✅ 必须     | 草稿接口同样需要浏览器内签名                                                    |
| `probe`            | ✅ 必须     | 抓当前页面元素，必须有页面                                                      |
| `page-info`        | ✅ 必须     | 读当前标签页 url/title                                                          |

没加 `--browser` 就跑上表里「✅ 必须」的子命令，会**立刻**返回一句中文提示 + 退出码 2，
不会悄悄去启动 Chrome，也不会返回假的成功。

### 为什么发布类必须浏览器（签名缺口，诚实说明）

发布 / 草稿相关接口在平台前端被标记为 `needSign: true` + `withSecondVerify: true`：

```
POST /web/api/media/aweme/create_v2/   ← 发布
POST /web/api/media/aweme/draft        ← 存草稿
POST /web/api/media/aweme/update_v2/   ← 改作品
```

真正加签的是前端的 secure-consumer-sdk。它劫持 `XMLHttpRequest.prototype.setRequestHeader`，
在浏览器内按**设备/证书上下文 + 时间 + 请求体**现算并注入 `bd-ticket-guard-*` 系列请求头。
这段逻辑与浏览器环境绑定，而且**随平台发版迭代**——硬编码一份签名实现，平台一升级就会
静默失效，那时最危险的表现不是报错，而是「看起来发了、其实没发」或触发风控。

所以本技能选择**不做本地签名**：这些子命令必须 `--browser`，并且**绝不会返回假的发布成功**
（未能确认发布成功就以退出码 2 结束）。

视频上传那一半（VOD 的 `ApplyUploadInner` / `CommitUploadInner`）用的是标准
AWS4-HMAC-SHA256 + STS 临时凭证，理论上纯标准库能实现；但后面 `create_v2` 这一步拿不到
签名，传上去只会留下孤儿对象。因此 `publish_video()` 在**发出任何网络请求之前**就报错，
`upload-preflight` 只做只读探测，不上传任何文件。

## 输入判断

按优先级判断用户意图，路由到对应子技能：

1. **只读查状态**（"检查登录 / 我的抖音号 / 粉丝多少 / 我发过哪些作品"）→ 直接跑
   `check-login` / `user-info` / `creator-info` / `list-works`，**不需要 `--browser`**。
2. **认证相关**（"登录 / 扫码 / 验证码登录"）→ `--browser` + `douyin-auth` 流程。
3. **视频发布**（"发布视频 / 上传作品 / 发抖音 / 定时发布"）→ `--browser` + `douyin-publish`。
4. 用户只给了网页或素材路径（没有确认文案）→ 先补齐标题、简介、话题，再进入发布流程。
5. 信息不全（缺视频路径）→ 先问清楚，不要瞎猜路径。

## 全局约束

- **发布不可逆**：任何发布动作前必须让用户确认最终的标题、简介、话题和视频文件。
  推荐"分步发布"：先 `fill-publish-video` 让用户在浏览器里肉眼确认 → 用户点头后再
  `click-publish`。
- **不要为了"验证"真发一条作品**：`publish-video` / `click-publish` 只在用户明确要求发布
  时使用。任何只读的自检请用 `check-login` / `user-info` / `list-works`。
- **用户取消时不要关标签页**：调用 `save-draft` 保存草稿，否则用户刚填的内容会丢。
- **控制频率**：同类操作之间保持合理间隔，避免短时间批量发布触发风控。
- **文件路径必须用绝对路径**，中文文案通过文件传递，不要内联到命令行参数。
- **CLI 输出为 JSON**，结构化呈现给用户；退出码 `0`=成功、`1`=未登录、`2`=错误。

## 子技能概览

### douyin-auth — 认证与账号

| 命令                                 | 浏览器   | 功能                                       |
| ------------------------------------ | -------- | ------------------------------------------ |
| `cli.py check-login`                 | 不需要   | 检查登录状态，返回真实昵称、uid、粉丝数     |
| `cli.py user-info`                   | 不需要   | 账号信息（昵称/抖音号/粉丝/关注/作品/头像） |
| `cli.py creator-info`                | 不需要   | 创作者信息（开通日期/实名状态/权限位）      |
| `cli.py get-qrcode`                  | ✅ 必须  | 截取登录二维码并保存到本地（非阻塞）        |
| `cli.py wait-login`                  | ✅ 必须  | 等待扫码完成（配合 get-qrcode）             |
| `cli.py login`                       | ✅ 必须  | 扫码登录并阻塞等待结果                      |
| `cli.py send-code --phone <号码>`    | ✅ 必须  | 验证码登录第一步：发送验证码                |
| `cli.py verify-code --code <验证码>` | ✅ 必须  | 验证码登录第二步：提交验证码                |

### douyin-works — 作品管理（只读）

| 命令                            | 浏览器 | 功能                                                    |
| ------------------------------- | ------ | ------------------------------------------------------- |
| `cli.py list-works --count 10`  | 不需要 | 已发布作品列表（id/文案/发布时间/时长/审核状态/封面）    |
| `cli.py upload-preflight`       | 不需要 | 只读检查 VOD 域名与 STS 凭证可用性（不回传任何凭证）     |

### douyin-publish — 视频发布（全部需要浏览器）

| 命令                        | 浏览器  | 功能                         |
| --------------------------- | ------- | ---------------------------- |
| `cli.py fill-publish-video` | ✅ 必须 | 上传视频并填写表单（不发布） |
| `cli.py click-publish`      | ✅ 必须 | 点击「发布」并等待平台反馈   |
| `cli.py save-draft`         | ✅ 必须 | 保存为草稿                   |
| `cli.py publish-video`      | ✅ 必须 | 一步完成上传 + 填写 + 发布   |

## 快速开始

```bash
# 1. 检查登录 —— 默认无浏览器，秒出（不需要先装扩展、不用开 Chrome）
python scripts/cli.py check-login

# 2. 看账号和作品（同样无浏览器）
python scripts/cli.py user-info
python scripts/cli.py creator-info
python scripts/cli.py list-works --count 10

# 3. 未登录时（exit 1）才需要浏览器：获取二维码并扫码
python scripts/cli.py --browser get-qrcode
python scripts/cli.py --browser wait-login

# 4. 一步发布视频（发布必须走浏览器）
python scripts/cli.py --browser publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "生活记录" "旅行"

# 5. 推荐：分步发布（先填表，用户确认后再发布）
python scripts/cli.py --browser fill-publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "生活记录"
python scripts/cli.py --browser click-publish

# 6. 用户改主意了 → 保存草稿，不要关标签页
python scripts/cli.py --browser save-draft
```

`--browser` 写在子命令前后都行（`cli.py --browser check-login` 和
`cli.py check-login --browser` 等价）。

## 前置条件（首次使用时引导用户完成）

**只读命令（check-login / user-info / creator-info / list-works）**：

- 本机 Chrome / Edge 里有**已登录的 `creator.douyin.com`**（手工登录一次即可，登录态留在
  浏览器里）。脚本只读 Cookie，不清除、不改写、不上传。
- 不需要 `uv sync`、不需要装依赖：纯标准库 + 仓库自带的 `.agents/lib/browserless`。

**浏览器命令（`--browser`）**：

1. **安装 Python 依赖**：`cd <本技能目录> && uv sync`（或 `pip install requests websockets`）。
2. **加载浏览器扩展**：Chrome → `chrome://extensions/` → 开启「开发者模式」→
   「加载已解压的扩展程序」→ 选择本项目的 `extension/` 目录。
3. **在浏览器里登录抖音**：手工在 creator.douyin.com 登录一次即可。

## 失败处理

- **未登录（exit 1）**：说明浏览器里没有有效登录态（平台返回 `status_code: 8`
  「用户未登录」）。走 `--browser` + `douyin-auth` 流程重新登录。
- **读不到 Cookie（exit 2，提示"无法从本机浏览器读取抖音 Cookie"）**：CLI 会**自动降级到
  浏览器模式重试一次**；仍失败时提示检查：是否用 Chrome 登录过、当前用户能否访问浏览器
  数据目录、或直接用 `--browser`。
- **`需要浏览器`（exit 2，`requiresBrowser: true`）**：该子命令没有无浏览器实现（登录/发布/
  草稿/页面类）。按提示加 `--browser`。
- **扩展未连接**：提示用户确认 `chrome://extensions/` 中 Douyin Bridge 已启用；
  bridge server 由 CLI 自动拉起，若失败可手动运行 `python scripts/bridge_server.py`。
- **找不到元素 / 选择器失效**：抖音前端改版所致。运行
  `python scripts/cli.py --browser probe` 抓取当前页面元素清单，据此更新
  `scripts/douyin/selectors.py` 中对应的候选列表。
- **视频处理超时**：大文件转码最长需 30 分钟，超时后提示用户重试或压缩视频。
- **可见范围 / 定时发布设置失败**：脚本会主动中止发布而不是退化成公开立即发布 ——
  这是有意的安全设计，此时应让用户在浏览器中手动确认后再点发布。
- **接口路径失效（只读命令报 status_code 非 0）**：平台改版。端点清单集中在
  `scripts/douyin/api.py` 顶部常量里，用浏览器 DevTools 的 Network 面板重新核对后更新；
  不要凭猜改路径。
