# douyin-skills

抖音自动化 Skills，直接使用你已登录的浏览器和真实账号，以普通用户的方式操作
[抖音创作服务平台](https://creator.douyin.com/creator-micro/home?enter_from=dou_web)。

支持 OpenClaw 及所有兼容 `SKILL.md` 格式的 AI Agent 平台（如 Claude Code）。

> **⚠️ 使用建议**：本项目使用真实的用户浏览器和账号环境，但仍建议**控制发布频率**。
> 短时间内批量发布容易触发账号风控。

## 功能概览

| 技能               | 说明     | 核心能力                                                      |
| ------------------ | -------- | ------------------------------------------------------------- |
| **douyin-auth**    | 认证管理 | 登录检查、扫码登录、手机验证码登录                            |
| **douyin-publish** | 视频发布 | 上传视频、标题/简介/#话题、可见范围、定时发布、草稿、分步预览 |

工作方式是：Python CLI 把命令发给本地 bridge server，bridge server 转发给浏览器扩展，
扩展在你**自己的 Chrome** 里执行真实点击与输入。因此不需要无头浏览器，也不会伪造指纹。

## 安装

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器（或直接用 pip）
- Google Chrome 浏览器

### 第一步：安装 Python 依赖

```bash
cd douyin-skills
uv sync
# 或者：pip install requests websockets
```

### 第二步：安装浏览器扩展

1. 打开 Chrome，地址栏输入 `chrome://extensions/`
2. 右上角开启**开发者模式**
3. 点击**加载已解压的扩展程序**，选择本项目的 `extension/` 目录
4. 确认扩展 **Douyin Bridge** 已启用

### 第三步：在浏览器中登录抖音

手工打开 <https://creator.douyin.com/creator-micro/home?enter_from=dou_web> 登录一次即可，
登录态会留在你自己的浏览器里（脚本不读取、不保存任何 cookie 值）。

### 第四步：启动 bridge server

CLI 会自动尝试拉起，也可以手动启动：

```bash
python scripts/bridge_server.py
```

bridge server 监听 `ws://localhost:9334`。**这个端口是本技能专用的**，与
[xiaohongshu-skills](https://github.com/autoclaw-cc/xiaohongshu-skills) 的 9333 分开，
因此两个扩展可以同时安装、互不干扰；如果两个技能共用端口，命令可能被路由到错误的站点。

## 使用方式

### 作为 AI Agent 技能使用（推荐）

安装到 skills 目录后，直接用自然语言与 Agent 对话即可：

> "把 `/Users/me/Desktop/vlog.mp4` 发到抖音，标题写……"

> "检查一下抖音登录状态"

> "这段视频明天中午 12 点定时发布"

### 作为 CLI 工具使用

所有功能也可直接调用，输出 JSON。

```bash
# 检查登录
python scripts/cli.py check-login

# 扫码登录
python scripts/cli.py get-qrcode
python scripts/cli.py wait-login

# 手机验证码登录
python scripts/cli.py send-code --phone 13800138000
python scripts/cli.py verify-code --code 123456

# 分步发布（推荐）：先填表 → 用户确认 → 再发布
python scripts/cli.py fill-publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "旅行" "川西自驾"
python scripts/cli.py click-publish

# 用户取消 → 存草稿
python scripts/cli.py save-draft

# 一步发布
python scripts/cli.py publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4"

# 带定时发布与可见范围
python scripts/cli.py publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --schedule-at "2026-03-10T12:00" \
  --visibility "公开"
```

## CLI 命令参考

| 子命令               | 说明                                 |
| -------------------- | ------------------------------------ |
| `check-login`        | 检查登录状态，返回昵称与落地 URL     |
| `get-qrcode`         | 截取登录二维码并保存到本地（非阻塞） |
| `wait-login`         | 等待扫码完成                         |
| `login`              | 扫码登录（阻塞）                     |
| `send-code`          | 验证码登录第一步：发送验证码         |
| `verify-code`        | 验证码登录第二步：提交验证码         |
| `fill-publish-video` | 上传视频 + 填写表单（不发布）        |
| `publish-video`      | 一步发布视频                         |
| `click-publish`      | 点击「发布」并等待反馈               |
| `save-draft`         | 保存为草稿                           |
| `probe`              | 抓取页面元素，用于修复选择器         |
| `page-info`          | 输出当前标签页 url / title           |

退出码：`0` 成功 · `1` 未登录 · `2` 错误

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
│   │   ├── bridge.py               # 扩展通信客户端（BridgePage）
│   │   ├── dom.py                  # 文本/语义定位辅助
│   │   ├── selectors.py            # 选择器候选列表（集中管理）
│   │   ├── login.py                # 登录 + 登录状态
│   │   ├── publish_video.py        # 视频发布
│   │   ├── probe.py                # 页面元素探测
│   │   ├── types.py                # 数据类型
│   │   ├── errors.py               # 异常体系
│   │   ├── human.py                # 行为模拟
│   │   └── urls.py                 # URL 常量
│   ├── cli.py                      # 统一 CLI 入口
│   └── bridge_server.py            # 本地通信服务
├── skills/                         # Skills 定义
│   ├── douyin-auth/SKILL.md
│   └── douyin-publish/SKILL.md
├── tests/                          # 静态一致性测试（无需浏览器）
│   └── test_static_consistency.py
├── SKILL.md                        # 技能统一入口（路由到子技能）
├── CLAUDE.md                       # 项目开发指南
├── pyproject.toml
└── README.md
```

## 开发

```bash
uv sync                        # 安装依赖
uv run --extra dev pytest      # 静态一致性测试（不需要浏览器）
uv run ruff check .            # Lint
uv run ruff format .           # 格式化
```

`pytest` 只跑静态检查：核对 `page.*` 调用是否存在于 `BridgePage`、Python 侧发出的
桥接方法是否都被扩展实现、五处 bridge 端口是否一致、选择器候选列表是否为空。
这些都是"只有连上浏览器跑到那一步才会暴露"的错误，放在本地挡住成本最低。

## 前端改版了怎么办

抖音创作服务平台的前端会不定期改版。脚本不依赖混淆类名，而是用「可见文本 + 语义属性」
定位元素；一旦报"未找到 XXX 元素"，用 `probe` 抓真实元素再修候选列表：

```bash
# 排查上传区：probe 会自己导航到发布页
python scripts/cli.py probe --url "https://creator.douyin.com/creator-micro/content/upload"

# 排查表单字段（标题 / 简介 / 发布按钮）：抓当前页面
python scripts/cli.py probe
```

对比输出更新 `scripts/douyin/selectors.py` 中对应的候选列表（新增而不是替换），再跑
`uv run --extra dev pytest` 确认没写坏，然后重试。

> ⚠️ **发布页是分阶段渲染的**：刚打开时页面上只有一个 `input[type=file]` 和上传区说明，
> **没有标题框、没有简介编辑器、没有发布按钮** —— 这些要等视频上传、转码完成后才渲染。
> 所以排查表单字段时必须在上传完成后 `probe`，否则会得到"页面上没有标题框"的错误结论。

## License

MIT
