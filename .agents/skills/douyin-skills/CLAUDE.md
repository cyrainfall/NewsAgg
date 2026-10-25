# douyin-skills

抖音自动化 Skills，操作抖音创作服务平台。**默认不走浏览器**：读取本机浏览器里已登录的
Cookie，用纯标准库 HTTP 调接口；需要页面或需要签名的能力用 `--browser` 兜底，走扩展 + 真实 DOM。

## 开发命令

```bash
uv sync                        # 安装依赖（只有 --browser 路径需要）
uv run --extra dev pytest      # 全部测试（静态检查 + 离线单测，都不联网）
uv run ruff check .            # Lint 检查
uv run ruff format .           # 代码格式化
python scripts/cli.py probe --browser   # 抓取当前页面元素（改版时排查选择器）
```

## 测试

两个文件，**都不联网、不需要浏览器**。

`tests/test_static_consistency.py` 全是**静态**检查，不需要 mock，直接在源码上核对
"只有连上浏览器才会暴露"的接线错误：

| 检查                                 | 挡住的问题                                   |
| ------------------------------------ | -------------------------------------------- |
| `page.*` 调用是否存在于 `BridgePage` | 方法名笔误（曾真实发生过）                   |
| `_call("...")` 是否都被扩展实现      | Python 侧改名后忘了改 `background.js`        |
| 扩展实现的方法是否有调用方           | 扩展里遗留的改名残留                         |
| 五处 bridge 端口是否一致             | 端口只改了一半，表现是"扩展连不上"           |
| `selectors.py` 候选列表是否为空      | 空候选会让报错指向"页面改版"而不是真正的原因 |

`tests/test_browserless_api.py` 用假 Session 核对 API/浏览器分流与"诚实降级"：

| 检查                                              | 挡住的问题                                     |
| ------------------------------------------------- | ---------------------------------------------- |
| 子命令归属登记是否完整（`_API_COMMANDS` / `_BROWSER_ONLY_COMMANDS`） | 新增子命令默认落进不确定分支        |
| 发布类没加 `--browser` 是否 exit 2                | 悄悄去启动 Chrome 而不是明确报错               |
| `publish_video` / `save_draft` / `upload_video` 是否抛签名错 | **返回假发布成功**（最严重）         |
| 抛签名错前是否**没发任何请求**                    | 视频已上传留下孤儿对象、只差最后一步失败       |
| `status_code: 8` 是否被当成未登录                 | HTTP 200 + 业务码错误被误判成功                |
| Cookie 读不到是否降级**恰好一次**、未登录是否**不**降级 | 无限绕 / 该登录却偷偷开浏览器            |
| `iter_paths()` 是否等于逆查得到的端点集合         | 端点被改成"猜的"路径                           |

改动 `bridge.py` / `background.js` / 端口 / `selectors.py` / `api.py` / `cli.py` 后请先跑
`pytest`。**不要为了让测试过而放宽断言** —— 这些断言挡的都是真实踩过的坑。

## 架构

双层结构：`scripts/` 是 Python 自动化引擎，`skills/` 是 Skills 定义（SKILL.md 格式）。
执行路径有两条，`cli.py` 按子命令分流（`_use_browser()`）：

- `scripts/douyin/api.py` — **默认路径**。纯标准库 HTTP 客户端，只依赖
  `.agents/lib/browserless`（`chromium.py` 读 Cookie、`http.py` 发请求）。
- `scripts/douyin/bridge.py` + `publish_video.py` + `login.py` — `--browser` 路径。
- `scripts/cli.py` — 统一 CLI 入口，JSON 结构化输出，按需自动启动 bridge server 和浏览器
- `scripts/bridge_server.py` — 本地通信服务（连接 CLI 与浏览器扩展）
- `extension/` — Chrome 扩展，在用户的真实浏览器中执行操作
- `skills/*/SKILL.md` — 指导 Agent 如何调用 scripts/

**分层纪律**：`api.py` 不许 import 本技能自己的浏览器模块（bridge / publish_video / login），
这条由 `test_api_module_does_not_import_browser_bridge` 守着。底座（`.agents/lib/browserless`）
是共享的，**本技能不改它**。

### 调用方式

```bash
python scripts/cli.py check-login          # 无浏览器
python scripts/cli.py list-works --count 10
python scripts/cli.py --browser publish-video --title-file t.txt --desc-file d.txt --video v.mp4
```

> `--browser` 写在子命令前后等价。没加 `--browser` 跑浏览器专属子命令会立刻 exit 2
> （JSON 带 `requiresBrowser: true`），不会去启动 Chrome。

## 数据流

API 模式（默认）：

```
cli.py → _use_browser(args) == False
       → DouyinApi(session=Session.from_browser("chrome", ["douyin.com"]))
       → urllib: creator.douyin.com/web/api/...（带 Cookie header + referer）
       → 业务码校验（0=成功 / 8=未登录 / 其它=DouyinApiError）→ JSON
```

浏览器模式（`--browser`）：

```
cli.py → BridgePage._call(method, params)
       → ws://localhost:9334 (bridge_server.py)
       → extension background.js:handleCommand
       → chrome.scripting.executeScript(world:"MAIN") 或 chrome.debugger(CDP)
       → 结果原路返回 JSON
```

### 签名缺口（改代码前必须知道）

发布 / 草稿接口（`/web/api/media/aweme/create_v2/`、`/draft`、`/update_v2/`）在前端标了
`needSign: true`，由 secure-consumer-sdk 在浏览器内现算 `bd-ticket-guard-*` 请求头
（劫持 `XMLHttpRequest.prototype.setRequestHeader` 注入）。这依赖设备证书上下文 + 时间 +
请求体，**纯 urllib 复现不了**，而且随平台发版迭代。

所以 `api.py` 里 `publish_video()` / `save_draft()` / `upload_video()` 一律
`raise SignatureRequiredError`（中文提示 + 让用户加 `--browser`），**且在发任何网络请求之前
就抛**。不要试图"先传上去再说" —— 那只会留下孤儿 VOD 对象。同理，`upload-preflight`
只做只读探测，不上传文件。

> **绝对不许**用"返回空成功"或"伪造发布成功"来让某条路径看起来跑通了。

## 代码规范

- 行长度上限 100 字符
- 完整 type hints，使用 `from __future__ import annotations`
- 异常继承 `DouyinError`（`douyin/errors.py`）
- CLI exit code：0=成功，1=未登录，2=错误
- 用户可见错误信息使用中文
- JSON 输出 `ensure_ascii=False`
- 每个函数只做一件事；主流程读起来应是"happy path"，兜底细节下沉到小函数

## 选择器策略（重要）

抖音创作服务平台是 React + Semi Design，生产构建会混淆类名。因此：

- **不要写死 hash 类名**，用「可见文本 + 语义属性（placeholder/accept/contenteditable）」定位。
- `selectors.py` 里每个常量都是**候选列表**，改版时**追加**候选而不是替换。
- `douyin/dom.py` 负责按文本打分定位（优先可点击元素、优先最内层元素），选中后打临时
  `data-dy-target` 标记，再交给扩展派发真实鼠标事件。
- 排查选择器：`python scripts/cli.py --browser probe`，看输出的 `selector_report`（逐条候选的
  命中情况）与 `inputs/buttons/...` 清单。

### 用 class 猜状态的坑（真实踩过）

`div[class*='progress']` 曾被当作"视频还在上传"的标志，结果它命中了**预览播放器的进度条
滑块**（`rc-slider progress-j6LKcd`）—— 抖音给播放器的 slider 也挂了含 `progress` 的业务
类名。这种宽泛匹配会永久命中，表现为"视频明明上传完了，脚本却一直卡住"。

结论：判断页面状态优先用**结构事实**（表单是否渲染、按钮是否可点击）或**可见文本**，
不要用宽泛的 class 子串匹配。

### 文案匹配的坑（真实踩过）

`RISK_KEYWORDS` 里放了 `"违规"`，结果命中了页面上常驻的「发文助手」文案「…降低违规风险」——
每次发布会立刻被误判成风控失败。风控/失败关键词必须是**足够具体的短语**（如"账号异常"
"审核未通过"），不能用单个短词。

## 抖音作品简介编辑器（重点，别踩）

编辑器根节点是 `.zone-container.editor-kit-container[contenteditable=true]`，行节点是
`.ace-line`。它有一套**自己的行模型与光标模型**，实测行为：

| 行为                                           | 结果                                                          |
| ---------------------------------------------- | ------------------------------------------------------------- |
| `document.execCommand("selectAll") + "delete"` | **清不掉** —— 旧内容会被 React 渲染回来，新内容只是叠加在前面 |
| 程序化设置 DOM 选区（Range）                   | **被忽略** —— 光标不跟随，文字会插到编辑器自己的光标处        |
| `execCommand("insertParagraph")`               | 无效，行数不变                                                |
| 真实 Enter（CDP 键盘事件）                     | 行数**也不变**                                                |
| `execCommand("insertHTML")` 传块级标签         | 块结构被压平成一行                                            |
| `execCommand("insertText", "a\nb")`            | 换行被吞掉                                                    |
| 一次性 `insertText` 写入整段                   | ✅ 文字内容与顺序都正确                                       |

因此 `_fill_description` 的策略是：**只在编辑器为空时写、且一次写完**（多行压成一整段），
非空时直接报错而不是叠加。写简介的文案要保证压平后仍然通顺（每行以标点结尾）。

话题（`#`）联想下拉是 `div.mention-suggest-mount-dom`，条目是 `div[class*='tag-hash']`，
条目文本把话题名和播放量拼在一起（`#人工智能654.9亿`），所以按"以 `#话题名` 开头"匹配。
点选必须精确到**条目**而不是外层容器，并且用**页面内派发鼠标事件**而不是坐标真实点击
（浮层是 React 渲染的，坐标点击的三次往返之间元素会被重渲染）。点选后要**轮询校验**是否
真的产生了对应的话题节点（创建是异步的）。

## 扩展改动的生效方式

改了 `extension/background.js` 之后**必须手动重载扩展**（`chrome://extensions` → Douyin
Bridge → 刷新图标），否则 Chrome 仍在跑旧代码。判断方法：在页面里装一个 `keydown` 监听再
调 `press_key`，收不到事件就说明还是旧版本。

## 安全约束

- 发布类操作必须有用户确认机制（分步发布 fill → click）
- 可见范围 / 定时发布设置失败时**必须中止发布**，不得退化为公开立即发布
- 文件路径必须使用绝对路径
- 敏感内容（标题、简介）通过文件传递，不内联到命令行参数
- **不读取、不保存、不输出任何 cookie / token 值**，登录态只留在用户浏览器中

## CLI 子命令对照表

「模式」列就是 `cli.py` 顶部 `_API_COMMANDS` / `_BROWSER_ONLY_COMMANDS` 两张表的真相，
新增子命令**必须**登记到其中一张，否则 `test_browserless_registry_covers_all_commands`
会失败（有意为之：不许有"默认落进某个不确定分支"的子命令）。

| CLI 子命令                            | 分类         | 模式               |
| ------------------------------------- | ------------ | ------------------ |
| `check-login`                         | 认证         | API（无浏览器）    |
| `user-info`                           | 账号         | API（无浏览器）    |
| `creator-info`                        | 账号         | API（无浏览器）    |
| `list-works`                          | 作品（只读） | API（无浏览器）    |
| `upload-preflight`                    | 作品（只读） | API（无浏览器）    |
| `login` / `get-qrcode` / `wait-login` | 认证         | `--browser` 必须   |
| `send-code` / `verify-code`           | 认证         | `--browser` 必须   |
| `fill-publish-video`                  | 发布（填写） | `--browser` 必须   |
| `publish-video`                       | 发布（一步） | `--browser` 必须   |
| `click-publish`                       | 发布（确认） | `--browser` 必须   |
| `save-draft`                          | 发布（草稿） | `--browser` 必须   |
| `probe` / `page-info`                 | 调试         | `--browser` 必须   |
