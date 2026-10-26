# douyin-skills

抖音自动化 Skills。**默认不需要浏览器**：从你本机浏览器只读取一次登录 Cookie，用纯标准库
HTTP 直接调[抖音创作服务平台](https://creator.douyin.com/) 的接口。需要页面、需要签名的能力
用 `--browser` 显式兜底，走浏览器扩展在**你自己的 Chrome** 里执行真实点击与输入。

支持 OpenClaw 及所有兼容 `SKILL.md` 格式的 AI Agent 平台（如 Claude Code）。

> **⚠️ 使用建议**：本项目使用真实的用户浏览器和账号环境，但仍建议**控制发布频率**。
> 短时间内批量发布容易触发账号风控。

## 功能概览

| 技能               | 说明               | 核心能力                                                     | 模式             |
| ------------------ | ------------------ | ------------------------------------------------------------ | ---------------- |
| **douyin-auth**    | 认证与账号         | 登录检查、账号信息、创作者信息                                | 默认无浏览器     |
| **douyin-auth**    | 认证（交互式）     | 扫码登录、手机验证码登录                                      | `--browser`      |
| **douyin-works**   | 作品管理（只读）   | 已发布作品列表、上传前置检查                                  | 默认无浏览器     |
| **douyin-publish** | 视频发布           | 上传视频、标题/简介/#话题、可见范围、定时发布、草稿、分步预览  | `--browser` 必须 |

两条路的分工：

- **API 模式（默认）** —— 只读本机浏览器里已登录的 Cookie，用标准库 `urllib` 请求
  `creator.douyin.com`。**不启动浏览器、不开窗口、不接管标签页**，适合脚本和定时任务。
- **浏览器模式（`--browser`）** —— Python CLI 把命令发给本地 bridge server，bridge server
  转发给浏览器扩展，扩展在你**自己的 Chrome** 里执行真实点击与输入。因此不需要无头浏览器，
  也不会伪造指纹。

两种模式都**不打印、不落盘任何 Cookie 值**，账号的私密字段（打码手机号、上传用 STS 凭证）
一律不回传。

## 子命令 → 是否需要浏览器（实测）

下表的「实测结果」是在本仓库机器上真跑出来的（Chrome 里有已登录的 `creator.douyin.com`
Cookie，全程没有启动浏览器），不是照文档填的。

| 子命令               | 需要浏览器 | 实测结果                                                       |
| -------------------- | ---------- | -------------------------------------------------------------- |
| `check-login`        | ❌          | exit 0，返回真实昵称、uid、粉丝数                               |
| `user-info`          | ❌          | exit 0，昵称/抖音号/粉丝 1/关注 29/作品 1/头像 URL              |
| `creator-info`       | ❌          | exit 0，`dashboard_opend_date`、`is_registered` 等              |
| `list-works`         | ❌          | exit 0，`total=1`，返回作品 id/文案/发布时间/时长/封面          |
| `upload-preflight`   | ❌          | exit 0，拿到 VOD 域名与凭证可用性（只报布尔值，不回传凭证）      |
| `login`              | ✅          | 二维码由页面渲染，且要轮询页面登录态                            |
| `get-qrcode`         | ✅          | 二维码是页面里的图片，必须渲染后截图                            |
| `wait-login`         | ✅          | 需要轮询页面登录态                                              |
| `send-code`          | ✅          | 发送验证码依赖页面控件                                          |
| `verify-code`        | ✅          | 填写验证码依赖页面控件                                          |
| `fill-publish-video` | ✅          | 要向页面注入文件、填表单，依赖真实 DOM 与前端状态               |
| `publish-video`      | ✅          | 发布接口需要浏览器内实时签名（见下）                            |
| `click-publish`      | ✅          | 只是点页面上的按钮                                              |
| `save-draft`         | ✅          | 草稿接口同样需要浏览器内签名                                    |
| `probe`              | ✅          | 抓当前页面元素，必须有页面                                      |
| `page-info`          | ✅          | 读当前标签页 url/title                                          |

没加 `--browser` 就跑「✅」的子命令，会**立刻**返回一句中文提示（JSON 里带
`requiresBrowser: true`）+ 退出码 2，不会悄悄去启动 Chrome。

### 为什么发布类必须浏览器（诚实说明）

发布 / 草稿相关接口在平台前端被标记为 `needSign: true` + `withSecondVerify: true`：

```
POST /web/api/media/aweme/create_v2/   ← 发布
POST /web/api/media/aweme/draft        ← 存草稿
POST /web/api/media/aweme/update_v2/   ← 改作品
```

真正加签的是前端的 secure-consumer-sdk。它劫持
`XMLHttpRequest.prototype.setRequestHeader`，在浏览器内按**设备/证书上下文 + 时间 +
请求体**现算并注入 `bd-ticket-guard-*` 系列请求头。这段逻辑与浏览器环境绑定，而且
**随平台发版迭代** —— 硬编码一份签名实现，平台一升级就会静默失效；那时最危险的表现不是
报错，而是「看起来发了、其实没发」或直接触发风控。

所以本技能**不做本地签名**：这些子命令必须 `--browser`，并且**绝不会返回假的发布成功**
（未能确认发布成功就以退出码 2 结束）。

视频上传那一半（VOD 的 `ApplyUploadInner` / `CommitUploadInner`）用的是标准
AWS4-HMAC-SHA256 + STS 临时凭证，理论上纯标准库能实现；但后面 `create_v2` 这一步拿不到
签名，传上去只会留下孤儿对象。因此 `publish_video()` 在**发出任何网络请求之前**就报错，
`upload-preflight` 只做只读探测，不上传任何文件。

## 安装

### 只读命令（默认无浏览器）

不需要安装任何东西 —— 纯标准库 + 仓库自带的 `.agents/lib/browserless`。

前置条件只有一个：**本机 Chrome / Edge 里有已登录的 `creator.douyin.com`**
（手工登录一次即可，登录态留在浏览器里）。脚本只读 Cookie，不清除、不改写、不上传。

### 浏览器命令（`--browser`）

1. **Python >= 3.11** 与 [uv](https://docs.astral.sh/uv/)（或直接用 pip）：

   ```bash
   cd douyin-skills
   uv sync
   # 或者：pip install requests websockets
   ```

2. **安装浏览器扩展**：
   1. 打开 Chrome，地址栏输入 `chrome://extensions/`
   2. 右上角开启**开发者模式**
   3. 点击**加载已解压的扩展程序**，选择本项目的 `extension/` 目录
   4. 确认扩展 **Douyin Bridge** 已启用

3. **在浏览器中登录抖音**：手工打开 <https://creator.douyin.com/> 登录一次即可。

4. **启动 bridge server**（CLI 会自动尝试拉起，也可手动）：

   ```bash
   python scripts/bridge_server.py
   ```

   bridge server 监听 `ws://localhost:9334`。**这个端口是本技能专用的**，与
   [xiaohongshu-skills](https://github.com/autoclaw-cc/xiaohongshu-skills) 的 9333 分开，
   因此两个扩展可以同时安装、互不干扰；如果两个技能共用端口，命令可能被路由到错误的站点。

## 使用方式

### 作为 AI Agent 技能使用（推荐）

安装到 skills 目录后，直接用自然语言与 Agent 对话即可：

> "检查一下抖音登录状态"

> "我发过哪些抖音作品？"

> "把 `/Users/me/Desktop/vlog.mp4` 发到抖音，标题写……"

> "这段视频明天中午 12 点定时发布"

### 作为 CLI 工具使用

所有功能也可直接调用，输出 JSON。

```bash
# 只读：默认无浏览器，秒出
python scripts/cli.py check-login
python scripts/cli.py user-info
python scripts/cli.py creator-info
python scripts/cli.py list-works --count 10
python scripts/cli.py upload-preflight

# 扫码登录（需要浏览器）
python scripts/cli.py --browser get-qrcode
python scripts/cli.py --browser wait-login

# 手机验证码登录（需要浏览器）
python scripts/cli.py --browser send-code --phone 13800138000
python scripts/cli.py --browser verify-code --code 123456

# 分步发布（推荐）：先填表 → 用户确认 → 再发布
python scripts/cli.py --browser fill-publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "旅行" "川西自驾"
python scripts/cli.py --browser click-publish

# 用户取消 → 存草稿
python scripts/cli.py --browser save-draft

# 一步发布
python scripts/cli.py --browser publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4"

# 带定时发布与可见范围
python scripts/cli.py --browser publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --schedule-at "2026-03-10T12:00" \
  --visibility "公开"
```

`--browser` 写在子命令前后都等价：`cli.py --browser check-login` ==
`cli.py check-login --browser`。

## CLI 命令参考

| 子命令               | 浏览器 | 说明                                         |
| -------------------- | ------ | -------------------------------------------- |
| `check-login`        | -      | 检查登录状态，返回真实昵称、uid、粉丝数       |
| `user-info`          | -      | 账号信息（昵称/抖音号/粉丝/关注/作品/头像）   |
| `creator-info`       | -      | 创作者信息（开通日期/实名状态/权限位）        |
| `list-works`         | -      | 已发布作品列表                                |
| `upload-preflight`   | -      | 只读检查上传前置条件（不打印任何凭证）        |
| `get-qrcode`         | ✅     | 截取登录二维码并保存到本地（非阻塞）          |
| `wait-login`         | ✅     | 等待扫码完成                                  |
| `login`              | ✅     | 扫码登录（阻塞）                              |
| `send-code`          | ✅     | 验证码登录第一步：发送验证码                  |
| `verify-code`        | ✅     | 验证码登录第二步：提交验证码                  |
| `fill-publish-video` | ✅     | 上传视频 + 填写表单（不发布）                 |
| `publish-video`      | ✅     | 一步发布视频                                  |
| `click-publish`      | ✅     | 点击「发布」并等待反馈                        |
| `save-draft`         | ✅     | 保存为草稿                                    |
| `probe`              | ✅     | 抓取页面元素，用于修复选择器                  |
| `page-info`          | ✅     | 输出当前标签页 url / title                    |

退出码：`0` 成功 · `1` 未登录 · `2` 错误（含「需要浏览器」「读不到 Cookie」「需要签名」）

只读命令的 JSON 里带 `"mode": "api"`；浏览器专属命令被拦下时 JSON 里带
`"mode": "browser"` 与 `"requiresBrowser": true`。

### 读不到 Cookie 时会发生什么

如果当前环境访问不到浏览器数据目录（例如在容器里跑、或换了用户），CLI 会：

1. 打印一句中文提示「无法从本机浏览器读取抖音 Cookie……」
2. **自动降级到浏览器模式重试一次**
3. 仍失败 → 退出码 2，并提示检查：是否用 Chrome 登录过、当前用户能否访问浏览器数据目录、
   或直接用 `--browser`

未登录（平台返回 `status_code: 8`「用户未登录」）**不会**降级 —— 那种情况该去登录，
而不是偷偷开浏览器。

## 项目结构

```
douyin-skills/
├── extension/                      # Chrome 扩展（Bridge）
│   ├── manifest.json
│   ├── background.js               # 命令路由：导航 / JS 执行 / 真实点击 / 上传
│   ├── popup.html
│   └── popup.js
├── scripts/                        # Python 自动化引擎
│   ├── douyin/                     # 核心自动化包
│   │   ├── api.py                  # 无浏览器 HTTP 客户端（默认路径）
│   │   ├── bridge.py               # 扩展通信客户端（BridgePage）
│   │   ├── dom.py                  # 文本/语义定位辅助
│   │   ├── selectors.py            # 选择器候选列表（集中管理）
│   │   ├── login.py                # 登录 + 登录状态（浏览器路径）
│   │   ├── publish_video.py        # 视频发布（浏览器路径）
│   │   ├── probe.py                # 页面元素探测
│   │   ├── types.py                # 数据类型
│   │   ├── errors.py               # 异常体系
│   │   ├── human.py                # 行为模拟
│   │   └── urls.py                 # URL 常量
│   ├── cli.py                      # 统一 CLI 入口（API / 浏览器模式分流）
│   └── bridge_server.py            # 本地通信服务
├── skills/                         # Skills 定义
│   ├── douyin-auth/SKILL.md
│   └── douyin-publish/SKILL.md
├── tests/                          # 静态一致性 + 离线测试（都不联网）
│   ├── test_static_consistency.py  # page.* 方法、bridge 端口、选择器候选
│   └── test_browserless_api.py     # 模式分流、签名缺口、绝不假成功
├── SKILL.md                        # 技能统一入口（路由到子技能）
├── CLAUDE.md                       # 项目开发指南
├── pyproject.toml
└── README.md
```

## 开发

```bash
uv sync                        # 安装依赖
uv run --extra dev pytest      # 全部测试（不需要浏览器、不联网）
uv run ruff check .            # Lint
uv run ruff format .           # 格式化
```

测试分两类，**都不联网**：

- `test_static_consistency.py` —— 核对 `page.*` 调用是否存在于 `BridgePage`、Python 侧发出的
  桥接方法是否都被扩展实现、五处 bridge 端口是否一致、选择器候选列表是否为空。
- `test_browserless_api.py` —— 用假 Session 核对：子命令该走 API 却走了浏览器（或反过来）、
  发布类没加 `--browser` 是否被拦住、发布/草稿/上传是否**返回假成功**（这条最关键）、
  未登录是否被误判成功、Cookie 缺失是否降级一次、端点路径是否被改成猜的。

这些都是"只有跑到某一步才会暴露"的错误，放在本地挡住成本最低。真实的联网验证见上面
「子命令 → 是否需要浏览器」表，由人跑，不进单测（CI 上不可能有登录态）。

## 前端改版了怎么办

抖音创作服务平台的前端会不定期改版，分两种情况：

**1. 只读命令报 `status_code` 非 0** —— 接口路径变了。端点清单集中在
`scripts/douyin/api.py` 顶部的常量里，用浏览器 DevTools 的 Network 面板重新核对后更新。
不要凭猜改路径。

**2. 浏览器命令报"未找到 XXX 元素"** —— 脚本不依赖混淆类名，而是用「可见文本 + 语义属性」
定位元素；用 `probe` 抓真实元素再修候选列表：

```bash
# 排查上传区：probe 会自己导航到发布页
python scripts/cli.py --browser probe --url "https://creator.douyin.com/creator-micro/content/upload"

# 排查表单字段（标题 / 简介 / 发布按钮）：抓当前页面
python scripts/cli.py --browser probe
```

对比输出更新 `scripts/douyin/selectors.py` 中对应的候选列表（新增而不是替换），再跑
`uv run --extra dev pytest` 确认没写坏，然后重试。

> ⚠️ **发布页是分阶段渲染的**：刚打开时页面上只有一个 `input[type=file]` 和上传区说明，
> **没有标题框、没有简介编辑器、没有发布按钮** —— 这些要等视频上传、转码完成后才渲染。
> 所以排查表单字段时必须在上传完成后 `probe`，否则会得到"页面上没有标题框"的错误结论。

## License

MIT
