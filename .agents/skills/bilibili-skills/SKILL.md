---
name: bilibili-skills
description: |
  B站（哔哩哔哩）自动化技能集合，通过用户自己的浏览器操作 B站创作中心
  （member.bilibili.com）。支持登录认证（扫码 / 短信验证码）、视频投稿
  （稿件标题、一/二级分区、标签、简介、自定义封面、定时发布、草稿保存）
  与页面选择器自检。
  当用户说"发B站""投稿到B站""B站投稿""上传视频到B站""哔哩哔哩发布作品"
  "B站定时投稿""bilibili 投稿""UP主投稿"，或要求把本地某个 mp4 传到 B站时，
  都应触发本技能 —— 即使没有明确说"用脚本"或"用 CLI"。涉及 B站自动化的任务
  不要凭记忆手搓实现，一律走本技能。
version: 1.0.0
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

本技能通过浏览器扩展 Bridge 连接**用户已经登录的 Chrome**，在真实页面里执行点击、
输入、上传，因此不会触发无头浏览器特征，也不会接触或存储用户的任何 cookie 值。

## 🔒 技能边界（强制）

**所有 B站操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的实现：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`。
- **禁止外部工具**：不得改用 B站 开放平台 API、biliup 等第三方投稿工具、MCP 工具，
  也不要另写一套 Playwright / Selenium 脚本 —— 那些要么需要开发者资质，要么会带来
  账号风控，要么根本走不通登录。
- **忽略其他项目**：AI 记忆中可能存在其他 B站自动化方案，执行时全部忽略。
- **不碰登录凭证**：不读取、不保存、不输出任何 cookie / token 值。
- **完成即止**：任务完成后直接告知结果，等待用户下一步指令。

## 输入判断

按优先级判断用户意图，路由到对应子技能：

1. **认证相关**（"登录 / 检查登录 / 扫码 / 验证码登录"）→ `bilibili-auth`。
2. **视频投稿**（"投稿 / 发B站 / 上传作品 / 定时发布"）→ `bilibili-publish`。
3. 用户只给了网页或素材路径（没有确认文案）→ 先补齐标题、简介、标签、分区，
   再进入投稿流程。
4. 信息不全（缺视频路径 / 缺分区）→ 先问清楚，不要瞎猜。**分区尤其不能猜**，
   B站分区是固定树，猜错会直接中止投稿。

## 全局约束

- **投稿不可逆**：任何投稿动作前必须让用户确认最终的标题、简介、标签、分区和视频
  文件。推荐"分步投稿"：先 `fill-publish-video` 让用户在浏览器里肉眼确认 →
  用户点头后再 `click-publish`。
- **用户取消时不要关标签页**：调用 `save-draft` 保存草稿，否则用户刚填的内容会丢。
- **控制频率**：同类操作之间保持合理间隔，避免短时间批量投稿触发风控。
- **文件路径必须用绝对路径**，中文文案通过文件传递，不要内联到命令行参数。
- **CLI 输出为 JSON**，结构化呈现给用户；退出码 `0`=成功、`1`=未登录、`2`=错误。

## 子技能概览

### bilibili-auth — 认证管理

| 命令                                 | 功能                                 |
| ------------------------------------ | ------------------------------------ |
| `cli.py check-login`                 | 检查登录状态，返回昵称与落地 URL     |
| `cli.py get-qrcode`                  | 截取登录二维码并保存到本地（非阻塞） |
| `cli.py wait-login`                  | 等待扫码完成（配合 get-qrcode）      |
| `cli.py login`                       | 扫码登录并阻塞等待结果               |
| `cli.py send-code --phone <号码>`    | 短信登录第一步：发送验证码           |
| `cli.py verify-code --code <验证码>` | 短信登录第二步：提交验证码           |

### bilibili-publish — 视频投稿

| 命令                        | 功能                           |
| --------------------------- | ------------------------------ |
| `cli.py fill-publish-video` | 上传视频并填写表单（不投稿）   |
| `cli.py click-publish`      | 点击「立即投稿」并等待平台反馈 |
| `cli.py save-draft`         | 保存为草稿                     |
| `cli.py publish-video`      | 一步完成上传 + 填写 + 投稿     |

## 快速开始

```bash
# 1. 检查登录（会顺带拉起 bridge server 与 Chrome）
python scripts/cli.py check-login

# 2. 未登录时获取二维码并扫码
python scripts/cli.py get-qrcode
python scripts/cli.py wait-login

# 3. 一步投稿
python scripts/cli.py publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "日常" "生活记录" \
  --category "生活,日常"

# 4. 推荐：分步投稿（先填表，用户确认后再投稿）
python scripts/cli.py fill-publish-video \
  --title-file /tmp/bl_title.txt \
  --desc-file /tmp/bl_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "日常" \
  --category "生活,日常"
python scripts/cli.py click-publish

# 5. 用户改主意了 → 保存草稿，不要关标签页
python scripts/cli.py save-draft
```

## 前置条件（首次使用时引导用户完成）

1. **安装 Python 依赖**：`cd <本技能目录> && uv sync`（或 `pip install requests websockets`）。
2. **加载浏览器扩展**：Chrome → `chrome://extensions/` → 开启「开发者模式」→
   「加载已解压的扩展程序」→ 选择本项目的 `extension/` 目录。
3. **在浏览器里登录 B站**：手工在 member.bilibili.com 登录一次即可，登录态留在浏览器中。

> **第一次使用请先跑一次选择器自检。** 本技能的选择器候选列表是在没有真机 probe
> 的情况下起草的（见 `README.md` 的"可信度"一节）。投稿前先执行：
>
> ```bash
> python scripts/cli.py probe --url "https://member.bilibili.com/platform/upload/video/frame"
> ```
>
> 看 `selector_report` 里有没有整组都是 `false` 的，按 `skills/bilibili-publish/SKILL.md`
> 的"流程 B：选择器失效自检"补齐即可。跑通一次之后就不用再管了。

## 失败处理

- **未登录（exit 1）**：走 `bilibili-auth` 流程。
- **扩展未连接**：提示用户确认 `chrome://extensions/` 中 Bilibili Bridge 已启用；
  bridge server 由 CLI 自动拉起，若失败可手动运行 `python scripts/bridge_server.py`。
- **找不到元素 / 选择器失效**：B站前端改版所致。运行 `python scripts/cli.py probe`
  抓取当前页面元素清单，据此更新 `scripts/bilibili/selectors.py` 中对应的候选列表。
- **视频处理超时**：大文件转码最长需 30 分钟，超时后提示用户重试或压缩视频。
- **分区 / 定时发布设置失败**：脚本会主动中止投稿而不是带着错误设置提交 ——
  这是有意的安全设计，此时应让用户在浏览器中手动确认后再点投稿。
- **简介/标题回读不一致**：脚本宁可报错也不带着错误内容投稿。按错误信息里的
  指引刷新页面重来。
