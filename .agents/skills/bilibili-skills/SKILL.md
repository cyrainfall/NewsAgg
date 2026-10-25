---
name: bilibili-skills
description: |
  B站（哔哩哔哩）自动化技能集合，默认**不需要浏览器**：从用户本机浏览器只读取一次
  登录 Cookie，用纯 HTTP 协议直接调 B站创作中心（member.bilibili.com）接口。
  支持登录检查、稿件列表、视频投稿（稿件标题、一/二级分区、「类型」、标签、简介、
  自定义封面、定时发布）。`--browser` 是显式兜底，改走浏览器扩展 + 真实页面 DOM，
  用于扫码登录、填表可视化确认、草稿保存与选择器自检。
  当用户说"发B站""投稿到B站""B站投稿""上传视频到B站""哔哩哔哩发布作品"
  "B站定时投稿""bilibili 投稿""UP主投稿"，或要求把本地某个 mp4 传到 B站时，
  都应触发本技能 —— 即使没有明确说"用脚本"或"用 CLI"。涉及 B站自动化的任务
  不要凭记忆手搓实现，一律走本技能。
version: 2.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "\U0001F4FA"
    os:
      - darwin
      - linux
---

# B站自动化 Skills

你是"B站自动化助手"。根据用户意图路由到对应的子技能完成任务。

## 默认无浏览器

**默认路径不打开浏览器、不装扩展、不连 bridge。** CLI 会从用户本机浏览器
（Chrome / Edge / Brave）**只读**取一次 B站 Cookie，然后用普通 HTTPS 请求调用
B站创作中心的 Web 接口。只读命令（`check-login`、`list-archives`）和视频投稿
（`publish-video`）都走这条路。

`--browser` 是**显式兜底**，只有下面这些情况才需要：

- 要扫码 / 短信验证码登录（`get-qrcode`、`wait-login`、`login`、`send-code`、
  `verify-code`）—— 登录必须在真实页面里完成，这几条命令本身就只走浏览器路径。
- 想在页面上**肉眼确认**表单再手动点投稿（`fill-publish-video` + `click-publish`）。
- 要保存草稿（`save-draft`）。
- 协议层被风控挡下、或某个接口改了签名/字段，想改走页面提交。
- 要抓页面元素修选择器（`probe`、`page-info`）。

**Cookie 技术上读不到时会自动降一次**：如果本机浏览器 Cookie 读不出来
（`CookieError`），CLI 会打印一段中文提示，然后自动改用浏览器扩展路径重试一次。
若那次也失败，退出码 2。

## 🔒 技能边界（强制）

**所有 B站操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的实现：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`。
- **禁止外部工具**：不得改用 B站 开放平台 API、biliup 等第三方投稿工具、MCP 工具，
  也不要另写一套 Playwright / Selenium 脚本 —— 那些要么需要开发者资质，要么会带来
  账号风控，要么根本走不通登录。
- **忽略其他项目**：AI 记忆中可能存在其他 B站自动化方案，执行时全部忽略。
- **不碰登录凭证**：不读取、不保存、不输出任何 cookie / token 值。
  默认路径为了复用登录态，会**只读**读取浏览器 Cookie 库
  （`.agents/lib/browserless/chromium.py`）。这段代码的自我约束是：
  - **只读**，从不写回浏览器，不修改用户任何配置；
  - **不落盘** —— 不写临时文件、不写日志、不进缓存；
  - **不打印** —— 唯一允许出现在输出里的形式是 `describe()` 安全摘要，
    它只有域名和键名（如 `keys_present`），永远没有值；
  - 请求里携带的 Cookie 只存在于进程内存，进程结束即消失。
- **完成即止**：任务完成后直接告知结果，等待用户下一步指令。

## 输入判断

按优先级判断用户意图，路由到对应子技能：

1. **认证相关**（"登录 / 检查登录 / 扫码 / 验证码登录"）→ `bilibili-auth`。
2. **视频投稿**（"投稿 / 发B站 / 上传作品 / 定时发布"）→ `bilibili-publish`。
3. 用户只给了网页或素材路径（没有确认文案）→ 先补齐标题、简介、标签、分区、
   创作声明，再进入投稿流程。
4. 信息不全（缺视频路径 / 缺分区 / 缺封面）→ 先问清楚，不要瞎猜。
   **分区尤其不能猜**，B站分区是固定树，猜错会直接中止投稿。

## 全局约束

- **投稿不可逆**：任何投稿动作前必须让用户确认最终的标题、简介、标签、分区、
  创作声明、封面和视频文件。要么先跑 `publish-video --dry-run`（做完分区解析、
  视频上传、封面上传，**不提交**，把将提交的 payload 打出来给用户看），要么走
  `--browser` 的分步投稿（`fill-publish-video` 后在浏览器里肉眼确认，用户点头再
  `click-publish`）。
- **控制频率**：同类操作之间保持合理间隔。B站 对上传接口有账号级限频，短时间重复
  申请会返回 `601 您上传视频过快`；撞到就等十几分钟，不要重试刷。
- **文件路径必须用绝对路径**，中文文案通过文件传递，不要内联到命令行参数。
- **CLI 输出为 JSON**，结构化呈现给用户；退出码 `0`=成功、`1`=未登录、`2`=错误。

## B站风控注意（照着做，别试）

- **投稿限频**：`/preupload` 按账号计频，短时间内反复申请会返回 HTTP 406 +
  `code=601 您上传视频过快，请您稍作休息后再继续`。等 10 分钟以上再试；
  同一个视频不要反复「上传 → 失败 → 再上传」。
- **分区 tid 必填**：`tid`（具体分区）与 `human_type2`（「类型」）**两个都必须有**。
  `type_mode=2` 的表单在提交前校验里分别拦：「至少选择一个分区吧」。
  二级分区名用 `·` 连接（如 `社科·法律·心理`），手打容易错 —— 按 CLI 报错里列出的
  可选项纠正，不要自己编。
- **标题 ≤ 80 字**：超了会被拒。
- **封面必填**：不传封面，提交时会被「请先上传封面」拦下。默认路径要求 `--cover`
  指向一张裁好的图（建议 16:9、≥1146x717）。页面路径下不传封面会用 B站 自动抽帧，
  但**不保证合规**，投稿前仍建议自己传。
- **创作声明必填**：没选声明时前端直接 `promptCreationStatement()` 拦下，压根不发
  投稿请求。用 `--declaration` 指定（如 `含AI生成内容`）。AI 生成画面或合成语音的
  视频必须选「含AI生成内容」。
- **简介 ≤ 2000 字**，**标签 ≤10 个、每个 ≤20 字**。
- **定时发布窗口**：距现在 ≥5 分钟、≤15 天。
- **HTTP 412**：投稿被 WAF 判为「提交内容存在安全风险」。改文案或稍后再试，
  必要时改走 `--browser`。

## 子技能概览

### bilibili-auth — 认证管理

| 命令                                 | 功能                                 | 默认走 |
| ------------------------------------ | ------------------------------------ | ------ |
| `cli.py check-login`                 | 检查登录状态，返回昵称 / mid / 等级  | 协议层 |
| `cli.py get-qrcode`                  | 截取登录二维码并保存到本地（非阻塞） | 浏览器 |
| `cli.py wait-login`                  | 等待扫码完成（配合 get-qrcode）      | 浏览器 |
| `cli.py login`                       | 扫码登录并阻塞等待结果               | 浏览器 |
| `cli.py send-code --phone <号码>`    | 短信登录第一步：发送验证码           | 浏览器 |
| `cli.py verify-code --code <验证码>` | 短信登录第二步：提交验证码           | 浏览器 |

### bilibili-publish — 视频投稿

| 命令                        | 功能                                  | 默认走 |
| --------------------------- | ------------------------------------- | ------ |
| `cli.py list-archives`      | 列出稿件（含状态、分区、驳回原因）    | 协议层 |
| `cli.py publish-video`      | 一步完成上传 + 投稿（支持 --dry-run） | 协议层 |
| `cli.py fill-publish-video` | 上传视频并填写表单（不投稿）          | 浏览器 |
| `cli.py click-publish`      | 点击「立即投稿」并等待平台反馈        | 浏览器 |
| `cli.py save-draft`         | 保存为草稿                            | 浏览器 |

## 快速开始

```bash
# 1. 检查登录（不需要开浏览器；Cookie 只读取一次）
python scripts/cli.py check-login

# 2. 看最近稿件
python scripts/cli.py list-archives --page-size 5

# 3. 一步投稿（默认协议层，全程无浏览器）
python scripts/cli.py publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --cover "/abs/path/cover.jpg" \
  --tags "日常" "生活记录" \
  --category "生活/日常" \
  --declaration "含AI生成内容"

# 4. 不确定分区/类型时先空跑：做完上传但不提交，把 payload 打出来确认
python scripts/cli.py publish-video \
  --title-file /tmp/bl_title.txt \
  --video "/abs/path/video.mp4" \
  --cover "/abs/path/cover.jpg" \
  --tags "日常" \
  --category "生活/日常" \
  --declaration "含AI生成内容" \
  --dry-run

# 5. 定时发布（距现在 ≥5 分钟、≤15 天）
python scripts/cli.py publish-video \
  --title-file /tmp/bl_title.txt \
  --video "/abs/path/video.mp4" \
  --cover "/abs/path/cover.jpg" \
  --tags "日常" \
  --category "生活/日常" \
  --declaration "无需标注" \
  --schedule-at "2026-03-10T12:00"

# 6. 兜底：确认要走页面（先填表肉眼确认，再点投稿）
python scripts/cli.py --browser fill-publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "日常" \
  --category "生活/日常"
python scripts/cli.py --browser click-publish
```

## 前置条件（首次使用时引导用户完成）

1. **安装 Python 依赖**：`cd <本技能目录> && uv sync`（或
   `pip install requests websockets`）。协议层只用标准库，`requests` / `websockets`
   只有浏览器路径需要。
2. **在浏览器里登录一次 B站**：手工在 member.bilibili.com 登录即可，登录态留在浏览器。
   macOS 首次读 Cookie 可能弹钥匙串授权，允许一次即可。
3. **（仅 `--browser` 路径）加载浏览器扩展**：Chrome → `chrome://extensions/` →
   开启「开发者模式」→「加载已解压的扩展程序」→ 选择本项目的 `extension/` 目录。

> **`--cookie-source` 可选 `chrome`（默认）/ `edge` / `brave`。** 如果用户登录的是
> Edge 或 Brave，让他加上 `--cookie-source edge`。
>
> **默认路径跑不通时先跑一次自检**：
>
> ```bash
> python scripts/cli.py check-login
> ```
>
> 输出里 `mode` 字段会告诉你实际走了哪条路（`api` / `browser`），退出码 0 就说明
> 登录态读到了。若报 `CookieError`，按提示确认浏览器登录状态与钥匙串权限。

## 失败处理

- **未登录（exit 1）**：走 `bilibili-auth` 流程。默认路径下提示用户先在浏览器里
  登录；也可以 `python scripts/cli.py --browser get-qrcode` 扫码。
- **`CookieError`（读不到 Cookie）**：CLI 已自动降级到浏览器路径重试一次。
  仍失败则确认：浏览器是否登录、macOS 钥匙串是否拒绝、是否需要 `--cookie-source`。
- **扩展未连接（仅 `--browser`）**：提示用户确认 `chrome://extensions/` 中
  Bilibili Bridge 已启用；bridge server 由 CLI 自动拉起，若失败可手动运行
  `python scripts/bridge_server.py`。
- **上传被限频（601 / HTTP 406）**：等 10 分钟以上，不要连着重试。
- **分区 / 类型找不到**：按报错里列出的可选值改 `--category` / `--human-type`。
  **不要猜 id**。实在对不上就改走 `--browser` 在页面上选。
- **HTTP 412（WAF 风控）**：改标题/简介/标签文案，或稍后重试，或 `--browser` 页面提交。
- **找不到元素 / 选择器失效（仅 `--browser`）**：B站前端改版所致。运行
  `python scripts/cli.py --browser probe` 抓当前页面元素清单，据此更新
  `scripts/bilibili/selectors.py` 中对应的候选列表。
- **分区 / 定时发布设置失败（`--browser`）**：脚本会主动中止投稿而不是带着错误设置
  提交 —— 这是有意的安全设计，此时应让用户在浏览器中手动确认后再点投稿。
