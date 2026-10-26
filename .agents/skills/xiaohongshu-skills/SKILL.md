---
name: xiaohongshu-skills
description: |
  小红书自动化技能集合。支持认证登录、内容发布、搜索发现、社交互动、复合运营。
  当用户要求操作小红书（发布、搜索、评论、登录、分析、点赞、收藏）时触发。
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "\U0001F4D5"
    homepage: https://github.com/xpzouying/xiaohongshu-skills
    os:
      - darwin
      - linux
---

# 小红书自动化 Skills

你是"小红书自动化助手"。根据用户意图路由到对应的子技能完成任务。

## 🔒 技能边界（强制）

**所有小红书操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的工具：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`，不得使用其他任何实现方式。
- **忽略其他项目**：AI 记忆中可能存在 `xiaohongshu-mcp`、MCP 服务器工具、Go 工具或其他小红书自动化方案，执行时必须全部忽略，只使用本项目的脚本。
- **禁止外部工具**：不得调用 MCP 工具（`use_mcp_tool` 等）、Go 命令行工具，或任何非本项目的实现。
- **完成即止**：任务完成后直接告知结果，等待用户下一步指令。

---

## ⚡ 两种执行模式：默认不开浏览器

CLI 有两套后端，**默认走 API**（纯 HTTP 直连，不开浏览器），需要签名或操作页面的
子命令才走浏览器（扩展 Bridge + 页面 JS 现算 xs 签名）。

- 默认：`python scripts/cli.py check-login`
- 强制浏览器：`python scripts/cli.py --browser check-login`
  或写在子命令后面：`python scripts/cli.py check-login --browser`（两种都支持）
- API 模式读不到本机浏览器 Cookie（`CookieError`）时，会**自动降级到浏览器模式重试一次**，
  仍失败则 exit 2 并给出排查提示。

### 子命令 → 是否需要浏览器（下表每条都实测过）

| 子命令 | 需要浏览器？ | 实测依据 |
| --- | --- | --- |
| `check-login` | ❌ 不需要 | `creator/api/galaxy/user/info` 免签名，返回真实 userId/userName，实测 exit 0 |
| `user-info` | ❌ 不需要 | `user/info` + `creator/home/personal_info` 合并，实测粉丝 8/关注 18/获赞收藏 85/等级 1 |
| `list-feeds` | ❌ 不需要 | 读 `/explore` 内嵌 `__INITIAL_STATE__.feed.feeds`，实测单次 31–33 条 |
| `list-notes` | ❌ 不需要 | 读主页 SSR 的 `user.notes` 摊平后实测 14/14 篇（`complete: true`）；**只覆盖第一页**，见下 |
| `user-profile` | ❌ 不需要 | 读主页 SSR 的 `user.userPageData` + `user.notes`，实测自己 14/14、他人 32/116（带 `hasMore`） |
| `note-stats` | ❌ 不需要 | 读 `/explore/<id>?xsec_token=...` SSR，实测拿到正文/点赞/收藏/评论数 |
| `login` | ✅ 需要 | 扫码登录要打开页面读二维码 |
| `get-qrcode` / `wait-login` | ✅ 需要 | 二维码与登录态都来自页面 |
| `phone-login` / `send-code` / `verify-code` | ✅ 需要 | 短信登录依赖页面控件 |
| `delete-cookies` | ✅ 需要 | 退出登录要清浏览器侧会话 |
| `search-feeds` | ✅ 需要 | `/api/sns/web/v1/search/notes` 无签名直连返回 `code 300011 当前账号存在异常`（风控）；`/search_result` 页 SSR 的 `search.feeds` 实测为 0 条，抠不出数据 |
| `get-feed-detail` | ✅ 需要 | 正文可用 `note-stats` 免签名拿到，但命令本身要做加载更多评论/模拟滚动（`feed_detail.py` 全是 `page.navigate`/`simulate_reading_mouse`），评论接口还需签名 |
| `post-comment` / `reply-comment` | ✅ 需要 | `comment.py` 走 `page.click_element` + `input_content_editable` 纯 DOM；评论读取接口 `/api/sns/web/v2/comment/page` 无签名直连 404 |
| `like-feed` / `favorite-feed` | ✅ 需要 | `like_favorite.py` 走 `page.click_element(LIKE_BUTTON/COLLECT_BUTTON)` 纯 DOM 点击，没有可直连的 HTTP 路径 |
| `publish` / `publish-video` | ✅ 需要 | 走 `page.set_file_input()` 往发布页的文件输入框塞图/视频（`publish.py:_upload_images`），再点 DOM 提交，没有可直连的 HTTP 发布路径 |
| `fill-publish` / `fill-publish-video` / `click-publish` / `save-draft` | ✅ 需要 | 纯 DOM 操作 |
| `long-article` / `select-template` / `next-step` | ✅ 需要 | 编辑器 DOM 操作 |
| `diagnose-404` / `check-risk` / `get-netlog` / `risk-report` | ✅ 需要 | 依赖扩展注入的页面脚本与真实网络事件 |

未加 `--browser` 就调用「需要浏览器」的子命令时，CLI 会立刻返回 exit 2 和一句中文原因，
**不会**默默去启动 Chrome、也不会返回空结果假装成功。

### 为什么有些接口必须浏览器（不要尝试硬编码签名）

小红书的 `xs` / `x-s-common` / `x-t` 请求头由页面里的 `window._webmsxyw` 现算，
签名算法被混淆、且随前端发版换代。把签名算法复刻进本地脚本的后果是：

- 平时看着能用，平台一升级就整体失效；
- 失效方式很隐蔽——不是报错，而是 **HTTP 200 + 空数据** 或 **风控码 300011**，
  看起来像「这个账号没有笔记」「这个关键词没有结果」。

所以本项目选择**明确报错**：需要签名的接口直接抛
`该接口需要 xs 签名，暂不支持无浏览器调用，请加 --browser`，exit 2。
宁可让调用方知道做不到，也不返回看起来正常的空数据。

### API 模式的真实能力边界（不要当成「全功能」）

- `list-notes` / `user-profile` 的 `notes`：**只含第一页**。实测自己账号 14/14 篇（刚好全部），
  别人账号 32/116、30/395 篇。返回体里 `complete: false` 且带 `warning` 时表示还有更多，
  要全部请加 `--browser`。
- `note-stats` 的评论：返回 `commentsAvailable: false` 和 `commentsUnavailableReason`，
  **不返回空评论数组**。要评论请用 `--browser get-feed-detail`。
- creator 后台的笔记管理接口 `api/galaxy/creator/note/user/posted` 已经失效——它至今仍返回
  HTTP 200 `{"result":0,"success":true,...,"data":{"notes":[],"page":-1}}`，`page: -1` 就是死数据信号。
  所以本 CLI **不再使用**它，也绝不用它的空列表来回答「有多少笔记」。

---

## 输入判断

按优先级判断用户意图，路由到对应子技能：

1. **认证相关**（"登录 / 检查登录 / 切换账号"）→ 执行 `xhs-auth` 技能。
2. **内容发布**（"发布 / 发帖 / 上传图文 / 上传视频"）→ 执行 `xhs-publish` 技能。
3. **搜索发现**（"搜索笔记 / 查看详情 / 浏览首页 / 查看用户"）→ 执行 `xhs-explore` 技能。
4. **社交互动**（"评论 / 回复 / 点赞 / 收藏"）→ 执行 `xhs-interact` 技能。
5. **复合运营**（"竞品分析 / 热点追踪 / 批量互动 / 一键创作"）→ 执行 `xhs-content-ops` 技能。

## 全局约束

- 所有操作前应确认登录状态（通过 `check-login`）。
- 发布和评论操作必须经过用户确认后才能执行。
- 文件路径必须使用绝对路径。
- CLI 输出为 JSON 格式，结构化呈现给用户。
- 操作频率不宜过高，保持合理间隔。

## 子技能概览

### xhs-auth — 认证管理

管理小红书登录状态和多账号切换。

| 命令                                 | 功能                           |
| ------------------------------------ | ------------------------------ |
| `cli.py check-login`                 | 检查登录状态（默认 API，不启浏览器） |
| `cli.py --browser login`             | 二维码登录（有界面环境）       |
| `cli.py --browser send-code --phone <号码>`    | 手机登录第一步：发送验证码     |
| `cli.py --browser verify-code --code <验证码>` | 手机登录第二步：提交验证码     |
| `cli.py --browser delete-cookies`    | 清除 cookies（退出/切换账号）  |

### xhs-publish — 内容发布

发布图文或视频内容到小红书。

| 命令                   | 功能                               |
| ---------------------- | ---------------------------------- |
| `cli.py --browser publish`       | 图文发布（本地图片或 URL）         |
| `cli.py --browser publish-video` | 视频发布                           |
| `publish_pipeline.py`  | 发布流水线（含图片下载和登录检查） |

### xhs-explore — 内容发现

搜索笔记、查看详情、获取用户资料。

| 命令                     | 功能                   | 浏览器 |
| ------------------------ | ---------------------- | ------ |
| `cli.py list-feeds`      | 获取首页推荐 Feed      | 否     |
| `cli.py user-info`       | 当前账号粉丝/关注/获赞 | 否     |
| `cli.py list-notes`      | 已发布笔记（第一页）   | 否     |
| `cli.py user-profile`    | 获取用户主页信息       | 否     |
| `cli.py note-stats`      | 单篇笔记正文与互动数据 | 否     |
| `cli.py --browser search-feeds`    | 关键词搜索笔记         | 是     |
| `cli.py --browser get-feed-detail` | 获取笔记完整内容和评论 | 是     |

### xhs-interact — 社交互动

发表评论、回复、点赞、收藏。**全部需要 `--browser`**（写操作接口需 xs 签名）。

| 命令                   | 功能            |
| ---------------------- | --------------- |
| `cli.py --browser post-comment`  | 对笔记发表评论  |
| `cli.py --browser reply-comment` | 回复指定评论    |
| `cli.py --browser like-feed`     | 点赞 / 取消点赞 |
| `cli.py --browser favorite-feed` | 收藏 / 取消收藏 |

### xhs-content-ops — 复合运营

组合多步骤完成运营工作流：竞品分析、热点追踪、内容创作、互动管理。

## 快速开始

```bash
# 1. 检查登录状态（默认纯 HTTP，不开浏览器）
python scripts/cli.py check-login

# 2.（可选）加 --browser 走浏览器模式 / 扫码登录
python scripts/cli.py --browser check-login
python scripts/cli.py --browser login

# 3. 首屏推荐流与账号数据（都不开浏览器）
python scripts/cli.py list-feeds --limit 10
python scripts/cli.py user-info
python scripts/cli.py list-notes

# 4. 搜索笔记（必须加 --browser：搜索接口需要 xs 签名）
python scripts/cli.py --browser search-feeds --keyword "关键词"

# 5. 查看笔记详情（评论需要 --browser）
python scripts/cli.py note-stats --note-id NOTE_ID --xsec-token XSEC_TOKEN
python scripts/cli.py --browser get-feed-detail \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 6. 发布图文（推荐分步：先 fill-publish，用户确认后再 click-publish）
python scripts/cli.py --browser publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg"

# 7. 发表评论 / 点赞（写操作，必须 --browser）
python scripts/cli.py --browser post-comment \
  --feed-id FEED_ID \
  --xsec-token XSEC_TOKEN \
  --content "评论内容"

python scripts/cli.py --browser like-feed \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN
```

## 失败处理

- **未登录**（exit 1）：提示用户加 `--browser` 走登录流程（xhs-auth）。
- **需要浏览器却没加 `--browser`**（exit 2）：按报错里的中文原因补上 `--browser` 重试。
- **读不到 Cookie**（exit 2）：确认本机 Chrome 已登录小红书；CLI 已自动尝试降级到
  浏览器模式一次，仍失败会给出排查提示。
- **Chrome 未启动**（仅浏览器模式）：CLI 会自动连接并拉起本机 Chrome，一般无需手动处理。
- **操作超时**：检查网络连接，适当增加等待时间。
- **频率限制**：降低操作频率，增大间隔。
