# xiaohongshu-skills

小红书自动化 Claude Code Skills，使用用户的真实浏览器和账号信息操作小红书。

## Git 工作流

- 所有代码修改必须在分支上进行，禁止直接推送 main 分支
- 分支开发完成后通过 PR 合入 main

## 开发命令

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```

## 架构

双层结构：`scripts/` 是 Python 自动化引擎，`skills/` 是 Claude Code Skills 定义（SKILL.md 格式）。

- `scripts/xhs/` — 核心自动化库（模块化，每个功能一个文件）
- `scripts/xhs/api.py` — **纯 HTTP 客户端（不开浏览器）**，读本机浏览器 Cookie 后直连
- `scripts/cli.py` — 统一 CLI 入口，JSON 结构化输出；默认走 API，`--browser` 走浏览器
- `scripts/bridge_server.py` — 本地通信服务（连接 CLI 与浏览器扩展）
- `extension/` — Chrome 扩展，在用户的真实浏览器中执行操作
- `skills/*/SKILL.md` — 指导 Claude 如何调用 scripts/

### 调用方式

```bash
# 默认：纯 HTTP，不开浏览器（读本机 Chrome 的登录 Cookie）
python scripts/cli.py check-login
python scripts/cli.py user-info
python scripts/cli.py list-feeds --limit 10
python scripts/cli.py list-notes
python scripts/cli.py note-stats --note-id ID --xsec-token TOKEN

# 需要签名或页面操作时加 --browser
python scripts/cli.py --browser search-feeds --keyword "关键词"
python scripts/cli.py --browser publish --title-file t.txt \
  --content-file c.txt --images pic.jpg
```

> API 模式读不到本机浏览器 Cookie 时会自动降级到浏览器模式重试一次；仍失败 exit 2。
> 需要浏览器的子命令若没加 `--browser`，会立即 exit 2 并给出中文原因，不会静默启动浏览器。
> `--browser` 写在子命令前或后都可以（`cli.py --browser login` / `cli.py login --browser`）。
> 完整的「子命令 → 是否需要浏览器」实测表见 `SKILL.md`。

### 不做本地 xs 签名（重要设计决策）

小红书的 `xs` / `x-s-common` / `x-t` 由页面 `window._webmsxyw` 现算，算法混淆且随前端发版换代。
硬编码复刻会在平台升级后失效，且失效表现为 **HTTP 200 + 空数据** 或风控码 `300011`——
比直接报错危险得多。因此 `xhs/api.py` 对需要签名的能力一律抛
`SignatureRequiredError`（中文消息 + 提示加 `--browser`），**绝不返回空列表假装成功**。
新增 API 能力前必须先实测；无法免签名就明确报错。

## 代码规范

- 行长度上限 100 字符
- 完整 type hints，使用 `from __future__ import annotations`
- 异常继承 `XHSError`（`xhs/errors.py`）
- CLI exit code：0=成功，1=未登录，2=错误
- 用户可见错误信息使用中文
- JSON 输出 `ensure_ascii=False`

### 安全约束

- 发布类操作必须有用户确认机制
- 文件路径必须使用绝对路径
- 敏感内容通过文件传递，不内联到命令行参数

## CLI 子命令对照表

| CLI 子命令           | 对应 MCP 工具         | 分类                    |
| -------------------- | --------------------- | ----------------------- |
| `check-login`        | check_login_status    | 认证                    |
| `login`              | get_login_qrcode      | 认证                    |
| `phone-login`        | —                     | 认证                    |
| `delete-cookies`     | delete_cookies        | 认证                    |
| `list-feeds`         | list_feeds            | 浏览                    |
| `search-feeds`       | search_feeds          | 浏览                    |
| `get-feed-detail`    | get_feed_detail       | 浏览                    |
| `user-profile`       | user_profile          | 浏览                    |
| `post-comment`       | post_comment_to_feed  | 互动                    |
| `reply-comment`      | reply_comment_in_feed | 互动                    |
| `like-feed`          | like_feed             | 互动                    |
| `favorite-feed`      | favorite_feed         | 互动                    |
| `publish`            | publish_content       | 发布                    |
| `publish-video`      | publish_with_video    | 发布                    |
| `fill-publish`       | —                     | 分步发布（图文填写）    |
| `fill-publish-video` | —                     | 分步发布（视频填写）    |
| `click-publish`      | —                     | 分步发布（点击发布）    |
| `long-article`       | —                     | 长文发布（填写+排版）   |
| `select-template`    | —                     | 长文发布（选择模板）    |
| `next-step`          | —                     | 长文发布（下一步+描述） |
| `get-netlog`         | —                     | 风控数据                |
| `risk-report`        | —                     | 风控数据                |
