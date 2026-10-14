# bilibili-skills

B站（哔哩哔哩）自动化 Skills，直接使用你已登录的浏览器和真实账号，以普通用户的方式操作
[B站创作中心](https://member.bilibili.com/platform/home)。

支持 OpenClaw 及所有兼容 `SKILL.md` 格式的 AI Agent 平台（如 Claude Code）。

> **⚠️ 使用建议**：本项目使用真实的用户浏览器和账号环境，但仍建议**控制投稿频率**。
> 短时间内批量投稿容易触发账号风控。

## 功能概览

| 技能                 | 说明     | 核心能力                                                                  |
| -------------------- | -------- | ------------------------------------------------------------------------- |
| **bilibili-auth**    | 认证管理 | 登录检查、扫码登录、短信验证码登录                                        |
| **bilibili-publish** | 视频投稿 | 上传视频、标题/一二级分区/标签/简介、自定义封面、定时发布、草稿、分步预览 |

工作方式是：Python CLI 把命令发给本地 bridge server，bridge server 转发给浏览器扩展，
扩展在你**自己的 Chrome** 里执行真实点击与输入。因此不需要无头浏览器，也不会伪造指纹，
更不接触你的 cookie 值。

## ⚠️ 可信度（请先读这一段）

这个技能和姊妹项目 `douyin-skills` 有一个重要区别，必须说清楚：

**`douyin-skills` 的选择器是真机逐条验证过的；`bilibili-skills` 的选择器还没有。**

`scripts/bilibili/selectors.py` 里的候选列表是根据 B站 2021 与 2025 两版公开自动化
方案、以及投稿页的公开结构信息起草的，**代码本身逻辑完整、静态测试全绿，但没有在
真实投稿页上跑过一轮**。所以：

- **第一次使用前**，请按 `skills/bilibili-publish/SKILL.md` 的"流程 B：选择器失效自检"
  跑一次 `probe`，把 `selector_report` 里整组为 `false` 的补上。
- 这个过程通常只需要一次。之后 B站不改版就能长期复用。
- 脚本对选择器失效的处理是**主动中止并给出排查指引**，不会带着错误的字段去投稿 ——
  所以最坏结果是"跑不通"，而不是"投出一篇错的稿件"。

如果你跑通了第一轮，欢迎把 `probe` 的真实输出补进 `selectors.py` 的候选列表
（**追加**而不是替换，原因见 `CLAUDE.md`）。

## 安装

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器（或直接用 pip）
- Google Chrome 浏览器

### 第一步：安装 Python 依赖

```bash
cd bilibili-skills
uv sync
# 或者：pip install requests websockets
```

### 第二步：安装浏览器扩展

1. 打开 Chrome，地址栏输入 `chrome://extensions/`
2. 右上角开启**开发者模式**
3. 点击**加载已解压的扩展程序**，选择本项目的 `extension/` 目录
4. 确认扩展 **Bilibili Bridge** 已启用

### 第三步：在浏览器中登录 B站

手工打开 <https://member.bilibili.com/platform/home> 登录一次即可，登录态会留在你
自己的浏览器里（脚本不读取、不保存任何 cookie 值）。

### 第四步：启动 bridge server

CLI 会自动尝试拉起，也可以手动启动：

```bash
python scripts/bridge_server.py
```

bridge server 监听 `ws://localhost:9335`。**这个端口是本技能专用的**，与
xiaohongshu-skills 的 9333、douyin-skills 的 9334 分开，因此三个扩展可以同时安装、
互不干扰；如果共用端口，命令可能被路由到错误的站点。`pytest` 里有一条测试专门守着
这件事（含与姊妹技能的冲突检查）。

## 使用方式

### 作为 AI Agent 技能使用（推荐）

安装到 skills 目录后，直接用自然语言与 Agent 对话即可：

> "把 `/Users/me/Desktop/vlog.mp4` 投到B站，分区生活/日常，标签日常、vlog"

> "检查一下B站登录状态"

> "这段视频明天中午 12 点定时投稿"

### 作为 CLI 工具使用

所有功能也可直接调用，输出 JSON。

```bash
# 检查登录
python scripts/cli.py check-login

# 扫码登录
python scripts/cli.py get-qrcode
python scripts/cli.py wait-login

# 短信验证码登录
python scripts/cli.py send-code --phone 13800138000
python scripts/cli.py verify-code --code 123456

# 分步投稿（推荐）：先填表 → 用户确认 → 再投稿
python scripts/cli.py fill-publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "日常" "生活记录" \
  --category "生活,日常"
python scripts/cli.py click-publish

# 用户取消 → 存草稿
python scripts/cli.py save-draft

# 一步投稿
python scripts/cli.py publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --category "生活,日常"

# 带自定义封面、定时发布
python scripts/cli.py publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --category "生活,日常" \
  --cover "/abs/path/cover.jpg" \
  --schedule-at "2026-03-10T12:00"
```

## CLI 命令参考

| 子命令               | 说明                                 |
| -------------------- | ------------------------------------ |
| `check-login`        | 检查登录状态，返回昵称与落地 URL     |
| `get-qrcode`         | 截取登录二维码并保存到本地（非阻塞） |
| `wait-login`         | 等待扫码完成                         |
| `login`              | 扫码登录（阻塞）                     |
| `send-code`          | 短信登录第一步：发送验证码           |
| `verify-code`        | 短信登录第二步：提交验证码           |
| `fill-publish-video` | 上传视频 + 填写表单（不投稿）        |
| `publish-video`      | 一步投稿视频                         |
| `click-publish`      | 点击「立即投稿」并等待反馈           |
| `save-draft`         | 保存为草稿                           |
| `probe`              | 抓取页面元素，用于修复选择器         |
| `page-info`          | 输出当前标签页 url / title           |

投稿类参数：

| 参数             | 说明                                                         |
| ---------------- | ------------------------------------------------------------ |
| `--title-file`   | 稿件标题文件（UTF-8，≤80 字）                                |
| `--desc-file`    | 稿件简介文件（UTF-8，≤2000 字）                              |
| `--video`        | 视频文件绝对路径                                             |
| `--tags`         | 标签列表（≤10 个，每个 ≤20 字）                              |
| `--replace-tags` | 先清空 B站 已有标签（含预选项）再写 `--tags`                 |
| `--declaration`  | 创作声明（必填），如 `含AI生成内容` / `AI` / `虚构` / `自制` |
| `--category`     | 分区 `"一级,二级"`，分隔符可用 `,` `、` `>` `/`              |
| `--cover`        | 封面图片绝对路径（可选）                                     |
| `--schedule-at`  | 定时发布，ISO8601 如 `2026-03-10T12:00`                      |
| `--skip-upload`  | 只填表单不重新上传（仅 `fill-publish-video`）                |
| `--force`        | 跳过弹窗保护检查（仅投稿类命令）                             |

退出码：`0` 成功 · `1` 未登录 · `2` 错误

## 项目结构

```
bilibili-skills/
├── extension/                      # Chrome 扩展（Bridge）
│   ├── manifest.json
│   ├── background.js               # 命令路由：导航 / JS 执行 / 真实点击 / 上传
│   ├── popup.html
│   └── popup.js
├── scripts/                        # Python 自动化引擎
│   ├── bilibili/                   # 核心自动化包
│   │   ├── bridge.py               # 扩展通信客户端（BridgePage）
│   │   ├── dom.py                  # 文本/语义定位辅助
│   │   ├── selectors.py            # 选择器候选列表（集中管理）
│   │   ├── login.py                # 登录 + 登录状态
│   │   ├── publish_video.py        # 视频投稿
│   │   ├── probe.py                # 页面元素探测
│   │   ├── types.py                # 数据类型
│   │   ├── errors.py               # 异常体系
│   │   ├── human.py                # 行为模拟
│   │   └── urls.py                 # URL 常量
│   ├── cli.py                      # 统一 CLI 入口
│   └── bridge_server.py            # 本地通信服务
├── skills/                         # Skills 定义
│   ├── bilibili-auth/SKILL.md
│   └── bilibili-publish/SKILL.md
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

`pytest` 只跑静态检查，但挡住的都是"只有连上浏览器跑到那一步才会暴露"的错误：
`page.*` 调用是否存在于 `BridgePage`、Python 侧发出的桥接方法是否都被扩展实现
（以及有没有反向残留）、五处 bridge 端口是否一致（含与姊妹技能冲突）、选择器候选列表
是否为空 / 混进文案常量、文档里写的 CLI 子命令是否真的存在。放在本地挡住成本最低。

## 前端改版了怎么办

B站创作中心会不定期改版。脚本不依赖混淆类名，而是用「可见文本 + 语义属性」定位；
一旦报"未找到 XXX 元素"，用 `probe` 抓真实元素再修候选列表：

```bash
# 排查上传区：probe 会自己导航到投稿页
python scripts/cli.py probe --url "https://member.bilibili.com/platform/upload/video/frame"

# 排查表单字段（标题 / 分区 / 标签 / 简介 / 投稿按钮）：抓当前页面
python scripts/cli.py probe
```

对比输出更新 `scripts/bilibili/selectors.py` 中对应的候选列表（**新增而不是替换**），
再跑 `uv run --extra dev pytest` 确认没写坏，然后重试。

> ⚠️ **投稿表单是上传后才渲染的**：刚打开投稿页时页面上只有上传区，
> **没有标题框、没有分区控件、没有简介编辑器、没有投稿按钮**。
> 所以排查表单字段时必须在上传完成后 `probe`，否则会得到"页面上没有标题框"的错误结论。

`probe` 的输出里还有 `iframe_report`：本技能的点击与查询**都不进入 iframe**，
如果元素集体消失，先看这里是不是页面结构变了。

## License

MIT
