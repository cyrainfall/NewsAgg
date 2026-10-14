# douyin-skills

抖音自动化 Skills，使用用户的真实浏览器和账号信息操作抖音创作服务平台。

## 开发命令

```bash
uv sync                        # 安装依赖
uv run --extra dev pytest      # 静态一致性测试
uv run ruff check .            # Lint 检查
uv run ruff format .           # 代码格式化
python scripts/cli.py probe    # 抓取当前页面元素（改版时排查选择器）
```

## 测试

`tests/test_static_consistency.py` 全是**静态**检查，不需要浏览器、不需要 mock，
直接在源码上核对三类"只有连上浏览器才会暴露"的接线错误：

| 检查                                 | 挡住的问题                                   |
| ------------------------------------ | -------------------------------------------- |
| `page.*` 调用是否存在于 `BridgePage` | 方法名笔误（曾真实发生过）                   |
| `_call("...")` 是否都被扩展实现      | Python 侧改名后忘了改 `background.js`        |
| 扩展实现的方法是否有调用方           | 扩展里遗留的改名残留                         |
| 五处 bridge 端口是否一致             | 端口只改了一半，表现是"扩展连不上"           |
| `selectors.py` 候选列表是否为空      | 空候选会让报错指向"页面改版"而不是真正的原因 |

改动 `bridge.py` / `background.js` / 端口 / `selectors.py` 后请先跑 `pytest`。

## 架构

双层结构：`scripts/` 是 Python 自动化引擎，`skills/` 是 Skills 定义（SKILL.md 格式）。

- `scripts/douyin/` — 核心自动化库（模块化，每个功能一个文件）
- `scripts/cli.py` — 统一 CLI 入口，JSON 结构化输出，自动启动 bridge server 和浏览器
- `scripts/bridge_server.py` — 本地通信服务（连接 CLI 与浏览器扩展）
- `extension/` — Chrome 扩展，在用户的真实浏览器中执行操作
- `skills/*/SKILL.md` — 指导 Agent 如何调用 scripts/

### 调用方式

```bash
python scripts/cli.py check-login
python scripts/cli.py publish-video --title-file t.txt --desc-file d.txt --video v.mp4
```

> CLI 会自动检测环境，若桥接服务或浏览器未就绪会尝试自动启动。

## 数据流

```
cli.py → BridgePage._call(method, params)
       → ws://localhost:9334 (bridge_server.py)
       → extension background.js:handleCommand
       → chrome.scripting.executeScript(world:"MAIN") 或 chrome.debugger(CDP)
       → 结果原路返回 JSON
```

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
- 排查选择器：`python scripts/cli.py probe`，看输出的 `selector_report`（逐条候选的命中
  情况）与 `inputs/buttons/...` 清单。

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

| CLI 子命令                            | 分类         |
| ------------------------------------- | ------------ |
| `check-login`                         | 认证         |
| `login` / `get-qrcode` / `wait-login` | 认证         |
| `send-code` / `verify-code`           | 认证         |
| `fill-publish-video`                  | 发布（填写） |
| `publish-video`                       | 发布（一步） |
| `click-publish`                       | 发布（确认） |
| `save-draft`                          | 发布（草稿） |
| `probe` / `page-info`                 | 调试         |
