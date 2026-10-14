# IMA 核心规则（ybrain 内置，蒸馏自 ima-skills v1.1.10）

本文件收录 ybrain 工作流依赖的通用规则与接口细节，是调用任何 API 前的必读。扩展面接口（建库/移动/标签/块编辑等）见同目录其他 reference。

## 传输与响应

- 统一入口：`node "$SKILL_DIR/scripts/ima_api.cjs" <apiPath> <json-body> [opts]`（`$SKILL_DIR` = 本技能目录）
- 凭证自动解析：环境变量 `IMA_OPENAPI_CLIENTID` / `IMA_OPENAPI_APIKEY` → `~/.config/ima/client_id`、`api_key`。已配置好，无需手工传 opts
- 凭证获取：未配置时**引导用户到 https://ima.qq.com/agent-interface 领取 clientId / apiKey**，配好后重跑当次操作；不要猜测凭证、不要反复重试、不要绕过脚本直接构造请求
- 脚本执行错误：进程非 0 退出，stderr 为 `{"code":-100,"msg":"..."}`，`msg` 直接展示用户
- 业务响应：stdout JSON `{"code":0,"msg":"...","data":{...}}`；`code≠0` 直接把 `msg` 展示给用户，不自行翻译
- 凭证只发往 `ima.qq.com`；COS 上传用 `create_media` 返回的临时凭证，禁止记录、复用或发往他处

## UTF-8 强制校验（notes 写入前必做）

`import_doc` / `append_doc` 的 `content`、`title` 必须是合法 UTF-8，否则 IMA 中乱码且不可修复：

- 来自文件：先检测编码，转 UTF-8 再读入
- 来自 WebFetch / HTTP：可能是 GBK/Latin-1，必须转码
- 变量拼接：清洗非法 UTF-8 字节

PowerShell 5.1 下**所有**请求 Body 必须显式转 UTF-8 字节数组（PS 5.1 会静默转 ANSI 导致中文乱码且无报错）；PS 7+ 无需处理。文件上传是二进制，不转码。

## 笔记要点

- `content_format`：写入固定 `1`（Markdown）；读取用 `export_note`（见 notes.md）
- `folder_id` 不可为 `"0"`；根目录 ID 从 `list_notebook` 返回中 `folder_type=1` 的条目获取
- `import_doc` 第一行文本自动成为笔记标题
- 笔记正文有大小上限：超限返回 `210009`，拆分多次 `append_doc`
- 分页不统一：`list_notebook` 首页 cursor 传 `"0"`；`list_note` 首页传 `""`（偏移量翻页："0","20","40"...）；`search_note` 用 `start`/`end`（差值 ≤20）
- 常见错误码：`210001` 参数错误、`210005` 非笔记作者、`210009` 内容过大、`210030` 笔记本重名、`210035` 笔记本不存在、`210038` block_id 不存在、`210039` 块不可编辑；`20002` 限频、`20004` 鉴权失败

## 图片写入笔记

只走 `scripts/note-images.cjs`，不得调用知识库接口代替：

- 原始文件严格小于 3 MiB，PNG/JPEG/WebP，校验真实文件签名；不得压缩转码规避
- 混合 Markdown 按原顺序分批：连续文本合并，每张图单独一批；任一批失败即停，已成功批次不回滚

```bash
node "$SKILL_DIR/scripts/note-images.cjs" --note-id <id> --file content.md [--dry-run]
```

## 知识库要点

- 分页全部游标式：首次 `cursor: ""`，`is_end=true` 停止，翻页用 `next_cursor`
- 面向用户只展示名称，永不暴露 `kb_id` / `media_id` / `folder_id` / `note_id`
- 常见错误码：`100001` 参数错误、`100005` 无权限、`100009` 超限、`310001` 笔记本不存在、`220030` 接口无权限；`20002` 限频、`20004` 鉴权失败

### MediaType 枚举与大小限制

| 值  | 类型       | 值  | 类型                     |
| --- | ---------- | --- | ------------------------ |
| 1   | PDF        | 11  | 笔记                     |
| 2   | 网页       | 12  | AI 会话                  |
| 3   | Word       | 13  | TXT                      |
| 4   | PPT        | 14  | Xmind                    |
| 5   | Excel      | 15  | 录音                     |
| 6   | 公众号文章 | 16  | 网页视频（skill 不支持） |
| 7   | Markdown   | 20  | HTML                     |
| 9   | 图片       | 21  | EPUB                     |
| 99  | 文件夹     |     |                          |

| 文件类型                          | 最大大小 |
| --------------------------------- | -------- |
| Excel、TXT、Xmind、Markdown、HTML | 10 MB    |
| 图片                              | 30 MB    |
| EPUB                              | 50 MB    |
| PDF、Word、PPT、音频及其他        | 200 MB   |

网页（2/6）、笔记（11）等非文件类型无大小限制；音频额外限制最长 2 小时。

### 文件上传安全门（顺序不可乱，任一失败即停）

1. `node "$SKILL_DIR/scripts/preflight-check.cjs" --file <path>` → `pass=false` 立即终止并展示 `reason`，不问用户是否继续
2. `check_repeated_names` → 重名只可选保留副本（文件名追加 `_YYYYMMDDHHmmss`）或取消，不支持覆盖
3. `create_media` → 取 `media_id` 与 `cos_credential`
4. `node "$SKILL_DIR/scripts/cos-upload.cjs"`（参数取自 create_media 返回的 cos_credential；大文件加 `--timeout 300000`）→ 非零退出立即停止
5. `add_knowledge`（`title` 必须原样等于 `file_name`，含扩展名）→ `code=0` 才算完成

### URL 类型检测（用户提供 URL 时）

- `text/html`：匹配 `mp.weixin.qq.com/s` → 公众号文章；其他 → 普通网页；均用 `import_urls`
- B站/YouTube 视频页、`file://` → 不支持，告知「仅支持在 ima 桌面端内添加进知识库」
- 文件型（按 Content-Type 判断）→ 下载到临时目录 → 走文件上传安全门

### get_media_info（查看/导出知识库条目原文）

`POST openapi/wiki/v1/get_media_info {"media_id":"..."}`，分支处理：

- `media_type=11` 且 `notebook_ext_info.notebook_id` 存在 → 作为 note_id 走笔记读取
- `url_info.url` 非空 → 带 `headers`（如有）请求原文
- `url_info` 为空或请求失败 → 提示「请使用 ima 客户端查看原文」

强制下载：在 URL 后追加 `response-content-type=application/octet-stream&response-content-disposition=attachment;filename="<文件名>"`。

### get_knowledge_base

`POST openapi/wiki/v1/get_knowledge_base {"ids":["<kb_id>"]}`（1-20 个 ID，单个也需数组）。
