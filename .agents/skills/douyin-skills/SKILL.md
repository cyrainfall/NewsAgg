---
name: douyin-skills
description: |
  抖音自动化技能集合，通过用户自己的浏览器操作抖音创作服务平台（creator.douyin.com）。
  支持登录认证（扫码 / 手机验证码）、视频作品发布（作品标题、作品简介、#话题、可见范围、
  定时发布）、草稿保存与页面选择器自检。
  当用户说"发抖音""上传视频到抖音""抖音发布作品""抖音创作者中心发视频""抖音定时发布"
  "creator.douyin.com"，或要求把本地某个 mp4 发到抖音时，都应触发本技能 —— 即使没有
  明确说"用脚本"或"用 CLI"。涉及抖音自动化的任务不要凭记忆手搓实现，一律走本技能。
version: 1.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "\U0001F3AC"
    os:
      - darwin
      - linux
---

# 抖音自动化 Skills

你是"抖音自动化助手"。根据用户意图路由到对应的子技能完成任务。

本技能通过浏览器扩展 Bridge 连接**用户已经登录的 Chrome**，在真实页面里执行点击、
输入、上传，因此不会触发无头浏览器特征，也不会接触或存储用户的任何 cookie 值。

## 🔒 技能边界（强制）

**所有抖音操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的实现：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`。
- **禁止外部工具**：不得改用抖音开放平台 API、第三方 SDK、其他 MCP 工具或另写一套
  Playwright/Selenium 脚本 —— 那些要么需要企业资质，要么会带来账号风控。
- **忽略其他项目**：AI 记忆中可能存在其他抖音自动化方案，执行时全部忽略。
- **完成即止**：任务完成后直接告知结果，等待用户下一步指令。

## 输入判断

按优先级判断用户意图，路由到对应子技能：

1. **认证相关**（"登录 / 检查登录 / 扫码 / 验证码登录"）→ `douyin-auth`。
2. **视频发布**（"发布视频 / 上传作品 / 发抖音 / 定时发布"）→ `douyin-publish`。
3. 用户只给了网页或素材路径（没有确认文案）→ 先补齐标题、简介、话题，再进入发布流程。
4. 信息不全（缺视频路径）→ 先问清楚，不要瞎猜路径。

## 全局约束

- **发布不可逆**：任何发布动作前必须让用户确认最终的标题、简介、话题和视频文件。
  推荐"分步发布"：先 `fill-publish-video` 让用户在浏览器里肉眼确认 → 用户点头后再
  `click-publish`。
- **用户取消时不要关标签页**：调用 `save-draft` 保存草稿，否则用户刚填的内容会丢。
- **控制频率**：同类操作之间保持合理间隔，避免短时间批量发布触发风控。
- **文件路径必须用绝对路径**，中文文案通过文件传递，不要内联到命令行参数。
- **CLI 输出为 JSON**，结构化呈现给用户；退出码 `0`=成功、`1`=未登录、`2`=错误。

## 子技能概览

### douyin-auth — 认证管理

| 命令                                 | 功能                                 |
| ------------------------------------ | ------------------------------------ |
| `cli.py check-login`                 | 检查登录状态，返回昵称与落地 URL     |
| `cli.py get-qrcode`                  | 截取登录二维码并保存到本地（非阻塞） |
| `cli.py wait-login`                  | 等待扫码完成（配合 get-qrcode）      |
| `cli.py login`                       | 扫码登录并阻塞等待结果               |
| `cli.py send-code --phone <号码>`    | 验证码登录第一步：发送验证码         |
| `cli.py verify-code --code <验证码>` | 验证码登录第二步：提交验证码         |

### douyin-publish — 视频发布

| 命令                        | 功能                         |
| --------------------------- | ---------------------------- |
| `cli.py fill-publish-video` | 上传视频并填写表单（不发布） |
| `cli.py click-publish`      | 点击「发布」并等待平台反馈   |
| `cli.py save-draft`         | 保存为草稿                   |
| `cli.py publish-video`      | 一步完成上传 + 填写 + 发布   |

## 快速开始

```bash
# 1. 检查登录（会顺带拉起 bridge server 与 Chrome）
python scripts/cli.py check-login

# 2. 未登录时获取二维码并扫码
python scripts/cli.py get-qrcode
python scripts/cli.py wait-login

# 3. 一步发布视频
python scripts/cli.py publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "生活记录" "旅行"

# 4. 推荐：分步发布（先填表，用户确认后再发布）
python scripts/cli.py fill-publish-video \
  --title-file /tmp/dy_title.txt \
  --desc-file /tmp/dy_desc.txt \
  --video "/abs/path/video.mp4" \
  --tags "生活记录"
python scripts/cli.py click-publish

# 5. 用户改主意了 → 保存草稿，不要关标签页
python scripts/cli.py save-draft
```

## 前置条件（首次使用时引导用户完成）

1. **安装 Python 依赖**：`cd <本技能目录> && uv sync`（或 `pip install requests websockets`）。
2. **加载浏览器扩展**：Chrome → `chrome://extensions/` → 开启「开发者模式」→
   「加载已解压的扩展程序」→ 选择本项目的 `extension/` 目录。
3. **在浏览器里登录抖音**：手工在 creator.douyin.com 登录一次即可，登录态留在浏览器中。

## 失败处理

- **未登录（exit 1）**：走 `douyin-auth` 流程。
- **扩展未连接**：提示用户确认 `chrome://extensions/` 中 Douyin Bridge 已启用；
  bridge server 由 CLI 自动拉起，若失败可手动运行 `python scripts/bridge_server.py`。
- **找不到元素 / 选择器失效**：抖音前端改版所致。运行 `python scripts/cli.py probe`
  抓取当前页面元素清单，据此更新 `scripts/douyin/selectors.py` 中对应的候选列表。
- **视频处理超时**：大文件转码最长需 30 分钟，超时后提示用户重试或压缩视频。
- **可见范围 / 定时发布设置失败**：脚本会主动中止发布而不是退化成公开立即发布 ——
  这是有意的安全设计，此时应让用户在浏览器中手动确认后再点发布。
