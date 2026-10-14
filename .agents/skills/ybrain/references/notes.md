---
name: ybrain-notes-reference
description: 管理用户的笔记和笔记本（新建、追加、编辑笔记内容、推送、重命名、移动笔记，按标题定位笔记，按笔记本浏览笔记列表，导出笔记到 workspace，创建/重命名笔记本），当用户提到笔记、备忘录、记事，且涉及以上意图时，使用此 skill。按内容搜索笔记或阅读笔记正文应使用 Search / Fetch 工具，不触发此 skill。
---

# 笔记 API 参考（ybrain 内置）

> **ybrain 环境注记**：本文档作为 ybrain 的笔记 API 参考内置。其中 Search / Fetch 工具与 `ima_cos_util` 是 ima 沙箱环境能力，本环境不可用：内容检索用 `search_note` / `search_knowledge`，笔记原文用 `export_note`，长内容写入拆分多次 `append_doc`（见 ybrain SKILL.md）。

通过 IMA OpenAPI 管理用户个人笔记，覆盖完整的读写和笔记本管理场景。

## API 调用模板

所有请求统一为 **HTTP POST + JSON Body**，Base URL 为 `https://ima.qq.com/openapi/note/v1`。

```bash
curl -s -X POST "https://ima.qq.com/openapi/note/v1/{endpoint}" \
  -H "ima-openapi-clientid: $IMA_OPENAPI_CLIENTID" \
  -H "ima-openapi-apikey: $IMA_OPENAPI_APIKEY" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

所有响应的外层结构统一为：

```json
{"code": 0, "msg": "success", "data": { ... }}
```

通过 `code` 字段判断是否成功：`code=0` 为成功，非零为错误码（常见错误码见下方约束速记，完整错误码表见 `notes-api.md`）。

## 关键概念

在调用任何接口之前，需要理解三个核心概念——它们贯穿所有接口：

| 概念             | 说明                                        | 获取方式                                                    |
| ---------------- | ------------------------------------------- | ----------------------------------------------------------- |
| `note_id`        | 笔记唯一 ID，所有针对单篇笔记的操作都需要它 | `search_note` 或 `list_note` 返回（详见各接口的返回说明）   |
| `folder_id`      | 笔记本 ID，不传时默认查全部笔记             | `list_notebook` 返回（取 `folder_type=0` 即用户自建的条目） |
| `content_format` | 文本格式枚举                                | **写入固定传 `1`**（Markdown，API 写入端只支持这一种）      |

> 展示给用户时**只用标题来指代笔记**，不暴露 note_id / folder_id——这些是内部标识，用户不需要看到。

## 规则

1. **操作失败时给出明确原因和建议** —— 如权限不足（210005）提示用户确认是否为笔记作者，内容过大（210009）建议拆分写入
2. **遇到以下情况时查阅 `notes-api.md`**（日常调用不需要加载它）：
   - 遇到未知错误码，需要查错误码表
   - 需要解析嵌套返回结构（如 `note_ext_info`、`highlightInfo` 的完整字段定义）
   - 需要确认公共结构体（NoteBookInfo、NoteFolderInfo 等）的完整字段列表
   - 需要了解游标翻页的详细规范
3. **按内容搜索笔记或阅读笔记正文时，不应加载此 skill** —— Search 工具提供关键词+向量+重排序的融合搜索，Fetch 工具可直接读取笔记内容，两者在内容搜索和阅读场景下效果更优。此 skill 的读取能力聚焦于：按标题定位笔记（获取 note_id 及笔记本归属）、按笔记本浏览笔记列表、导出笔记到 workspace。
4. **当前 API 不支持任何删除操作** —— 无法删除笔记、笔记本或任何已写入的内容。如果用户要求删除，直接告知需要在 IMA 客户端中手动操作，**不要尝试寻找或构造删除接口，不要重试**。
5. **所有写入操作不可逆** —— 由于没有删除 API，一旦创建（笔记、笔记本）或写入内容（import_doc、append_doc、push_note），就无法通过 API 撤回。因此：当写入操作不是由用户显式/隐式要求时，必须先向用户确认再执行。

## 接口速查

### 读取类

#### search_note — 按标题定位笔记

`/openapi/note/v1/search_note`

**触发场景**：按标题定位笔记，获取 note_id 及笔记本归属信息。按正文内容搜索应优先使用 Search 工具，此处仅作 fallback。

请求体结构：

```json
{
  "search_type": "int, 必填, 0=标题检索, 1=正文检索",
  "query_info": {
    "title": "string, search_type=0 时必填, 标题关键词",
    "content": "string, search_type=1 时必填, 正文关键词"
  },
  "start": "int, 必填, 翻页起始位置",
  "end": "int, 必填, 翻页结束位置, 与 start 相差不超过 20"
}
```

返回要点：

- `data.search_note_infos[]` —— 结果列表，每条含 `note_book_info`（NoteBookInfo，含 `note_id`、`title`、`summary`、`note_ext_info.folder_id/folder_name`）和 `highlightInfo`（`doc_title` 含 `<em>` 高亮标签）
- `data.is_end` —— bool，是否最后一页
- `data.total_hit_num` —— 命中总数

#### list_note — 浏览笔记列表

`/openapi/note/v1/list_note`

**触发场景**：用户说「最近的笔记」「看看xx笔记本里有什么」。没有指明笔记本时，`folder_id` 留空查全部。

请求体结构：

```json
{
  "folder_id": "string, 可选, 空字符串=全部笔记, 指定则只查该笔记本",
  "sort_type": "int, 可选, 0=修改时间(默认), 1=创建时间, 2=标题, 3=大小",
  "cursor": "string, 必填, 首次传空字符串, 翻页传已获取的条目总数(如首页 limit=20 则第二页传 '20')",
  "limit": "int, 必填, 每页数量, 最大 20"
}
```

返回要点：

- `data.note_book_list[]` —— 笔记列表（NoteBookInfo，含 `note_id`、`title`、`summary`、`create_time`、`modify_time`、`note_ext_info.folder_id/folder_name`）
- `data.is_end` —— bool，是否最后一页

#### export_note — 导出笔记到 workspace

`/openapi/note/v1/export_note`

**触发场景**：导出笔记原文, 获取笔记的markdown格式, 获取笔记的json格式, 获取笔记的纯文本。也用于 Fetch 工具返回生成内容而非原文时的 fallback：先 export 获取原文再做后续处理。

请求体结构：

```json
{
  "note_id": "string, 必填, 目标笔记 ID",
  "target_content_format": "int, 必填, 推荐传 1 (Markdown)"
}
```

- 返回 `data.content_url`，为带签名的 COS 下载链接（非内容本身），需二次 `curl` 请求该链接获取实际笔记内容。签名有时效（约3小时）
- 下载内容的格式取决于 `target_content_format`：`0` → 纯文本（.txt），`1` → Markdown（.md），`2` → JSON（Slate 编辑器 AST 结构，.json）
- 需要作者权限

### 写入类

#### import_doc — 新建笔记

`/openapi/note/v1/import_doc`

**触发场景**：用户说「新建笔记」「把这段内容保存为笔记」。

请求体结构：

```json
{
  "content_format": "int, 必填, 固定传 1 (Markdown)",
  "content": "string, 必填, Markdown 格式笔记内容。第一行文本会被自动提取为笔记标题",
  "folder_id": "string, 可选, 目标笔记本 ID, 不传则新建到默认位置",
  "folder_name": "string, 可选, 目标笔记本名称"
}
```

- 返回 `data.note_id`，后续可用于 append_doc

#### append_doc — 追加内容到已有笔记

`/openapi/note/v1/append_doc`

**触发场景**：用户说「在这篇笔记末尾加点内容」「把XX添加到笔记里」。适合短内容直接追加；长内容应走 COS 上传 + `push_note`（传 note_id）路径。

请求体结构：

```json
{
  "note_id": "string, 必填, 目标笔记 ID",
  "content_format": "int, 必填, 固定传 1 (Markdown)",
  "content": "string, 必填, 追加的内容"
}
```

- 需要作者权限
- 返回 `data.note_id`

### 编辑笔记

编辑已有笔记内容需要两步：先用 `export_note_blocks` 获取块结构，再用 `update_note` 按 block_id 执行修改。

#### export_note_blocks — 导出笔记 block 结构

`/openapi/note/v1/export_note_blocks`

**触发场景**：作为 `update_note` 的前置步骤，获取笔记的块结构以定位要修改的内容。不单独使用。

请求体结构：

```json
{
  "note_id": "string, 必填, 目标笔记 ID"
}
```

返回要点：

- `data.blocks[]` —— 有序块列表，每条含：
  - `block_id`：块 ID（普通块为 UUID，列表整体为 `首ID..尾ID`，虚拟锚点为 `_begin_` / `_end_`）
  - `content`：块的 Markdown 文本（供定位用，虚拟块为空）
  - `editable`：bool，是否可 EDIT/DELETE（图片/录音/附件/链接卡片/AI 生成块为 false）
- `editable=false` 的块只能作为 APPEND 锚点，不能 EDIT / DELETE
- `_begin_` / `_end_` 是虚拟块，仅用于在文档首尾插入内容

#### update_note — 按 block 编辑笔记内容

`/openapi/note/v1/update_note`

**触发场景**：用户说「修改笔记中的某段」「把笔记里的xx改成yy」「在某段后面加点内容」。

前置步骤：必须先调 `export_note_blocks` 获取 block 结构。

请求体结构：

```json
{
  "note_id": "string, 必填, 目标笔记 ID",
  "user_request_id": "string, 必填, 本轮用户请求的 UUID v4（复用规则见下方）",
  "updates": [
    {
      "block_id": "string, 必填, 目标块 ID（来自 export_note_blocks）",
      "action": "int, 必填, 1=EDIT 替换 / 2=DELETE 删除 / 3=APPEND 在该块后插入",
      "new_content": "string, EDIT/APPEND 必填, Markdown 文本"
    }
  ]
}
```

**`user_request_id` 生成/复用规则**（用于后台版本合并）：

- **响应同一条用户消息期间**，无论调用多少次 `update_note`（例如先改标题、再追加段落），**必须复用同一个 UUID**
- **收到新的用户消息时**，生成新的 UUID
- 未传或为空时后台会拒绝请求，返回 `210001`，msg: `UpdateNote userRequestId is empty`

action 取值：

| 值  | 含义                            | new_content |
| --- | ------------------------------- | ----------- |
| 1   | EDIT — 整体替换目标块内容       | 必填        |
| 2   | DELETE — 删除目标块             | 忽略        |
| 3   | APPEND — 在目标块之后插入新内容 | 必填        |

返回要点：

- `data.results[]` —— 与请求 `updates` 一一对应，每条含 `block_id`、`success`(bool)、`error_code`、`error_msg`
- 部分成功语义：失败的操作不影响文档，后续操作基于当前文档状态继续执行
- 错误码：`210038`=BLOCK_NOT_FOUND，`210039`=BLOCK_NOT_EDITABLE

#### 编辑工作流

1. `search_note` → 按标题找到目标笔记，获取 `note_id`
2. `export_note_blocks(note_id)` → 获取 block 结构列表
3. 根据 `content` 定位目标 block，记录 `block_id` 和 `editable`
4. **生成 `user_request_id`**：本轮用户请求首次调用 `update_note` 时生成一个 UUID v4，后续同一用户请求内的所有 `update_note` 调用都复用这个 ID
5. 组装 `updates[]`：
   - 改内容 → `action=1(EDIT)`, `new_content=<新Markdown>`
   - 删内容 → `action=2(DELETE)`
   - 在某块后插入 → `action=3(APPEND)`, `new_content=<新Markdown>`
   - 插入到最前 → `block_id="_begin_"`, `action=3`
   - 追加到最末 → `block_id="_end_"`, `action=3`
6. `update_note(note_id, user_request_id, updates)` → 发送修改请求
7. 检查 `results[i].success`；失败的看 `error_code` 决定是否重试或告知用户

#### 注意事项

- `update_note` 依赖 `export_note_blocks` 返回的 `block_id`，不可凭空构造
- **`user_request_id` 必须在同一用户 prompt 内所有 `update_note` 调用间复用**（每次都换新 ID 会导致后台无法合并版本，产生冗余的历史记录）
- `editable=false` 的块（图片/录音/附件/链接卡片/AI 生成块）只能用作 APPEND 锚点，对其下发 EDIT/DELETE 会返回 210039
- 一次调用可混合多种 action，按数组顺序执行
- 列表整体（`id1..idN`）作为一个逻辑块：EDIT 替换整个列表，DELETE 删除整个列表
- 批量操作部分成功时需逐条检查 `results[].success`，单条失败不影响其他操作

#### push_note — 智能推送（新建或追加）

`/openapi/note/v1/push_note`

**触发场景**：用户说「帮我记一下」「保存这段内容」。当不确定笔记是否已存在时用 —— note_id 为空则新建，不为空则追加。支持通过 `ima_cos_util -f <文件路径>` 上传获取 cosKey，再传 `content_cos_key` 代替 `content`，长内容场景推荐此方式（见「长内容写入策略」章节）。

请求体结构：

```json
{
  "note_id": "string, 可选, 为空则新建笔记, 不为空则追加到该笔记",
  "content": "string, 可选, Markdown 格式笔记内容, 与 content_cos_key 不能同时为空",
  "content_cos_key": "string, 可选, 内容的 COS 对象路径, 当 content 为空时生效"
}
```

- note_id 不为空时需要作者权限
- 返回 `data.note_id`（新建或追加的笔记 ID）

#### rename_note — 重命名笔记

`/openapi/note/v1/rename_note`

请求体结构：

```json
{
  "note_id": "string, 必填, 目标笔记 ID",
  "title": "string, 必填, 新标题"
}
```

- 需要作者权限
- 返回空消息体，成功即可

#### move_notes — 批量移动笔记

`/openapi/note/v1/move_notes`

**触发场景**：用户说「把笔记移到xx笔记本」「整理一下笔记」。

请求体结构：

```json
{
  "note_ids": ["string, 必填, 笔记 ID 数组, 最多 100 条"],
  "target_folder_id": "string, 必填, 目标笔记本 ID",
  "target_folder_name": "string, 可选, 目标笔记本名称"
}
```

- 返回 `data.result` map：key 为 note_id，value 为 true/false —— 可能部分成功，需逐条检查

### 笔记本管理类

#### list_notebook — 查看笔记本列表

`/openapi/note/v1/list_notebook`

请求体结构：

```json
{
  "cursor": "string, 必填, 首页传 '0' (注意和 list_note 不同), 翻页传返回的 next_cursor",
  "limit": "int, 必填, 数量限制, 最大 20"
}
```

返回要点：

- `data.note_folder_infos[]` —— 笔记本列表（含 `folder_id`、`name`、`note_number`、`folder_type`），取 `folder_type=0`（用户自建）的条目获取 `folder_id`
- `data.next_cursor` —— 翻页游标，传入下次请求的 `cursor`
- `data.is_end` —— bool，是否最后一页

#### add_notebook — 创建笔记本

`/openapi/note/v1/add_notebook`

请求体结构：

```json
{
  "folder_name": "string, 必填, 笔记本名称"
}
```

- 返回 `data.folder_id` 和 `data.folder_name`

#### rename_notebook — 重命名笔记本

`/openapi/note/v1/rename_notebook`

请求体结构：

```json
{
  "folder_id": "string, 必填, 目标笔记本 ID",
  "new_folder_name": "string, 必填, 新名称"
}
```

- 返回空消息体，成功即可

## 长内容写入策略

根据内容规模选择写入方式：

| 内容规模                                                       | 推荐方式                                                               |
| -------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **短内容**（几句话，无特殊字符）                               | 直接 `-d '{...}'` 内联                                                 |
| **中等内容**（几百字以内，结构简单）                           | `file_write` 写 JSON 请求体到临时文件 → `curl -d @文件路径`            |
| **长内容**（几百字以上，或含表格/emoji/引号/反斜杠等特殊字符） | 写 `.md` 文件 → `ima_cos_util` 上传 → `push_note` 的 `content_cos_key` |

> 不要把长内容直接嵌入 curl `-d '{...}'` 的内联 JSON 中——特殊字符会导致失败。中等内容可通过 `file_write` 写 JSON 请求体再 `curl -d @文件路径` 发送；长内容推荐 COS 上传路径，可以绕过 JSON 序列化，最为可靠。具体操作示例见下方「多步工作流」。

## 多步工作流

以下场景需要串联多个接口。所有 curl 命令均省略公共 header（见「API 调用模板」）。

### 新建笔记到指定笔记本（长内容）

1. `add_notebook`（或 `list_notebook` 查找已有笔记本）→ 获取 `folder_id`
2. `file_write` 将内容写为 `.md` 文件
3. `ima_cos_util -f <文件路径>` → 获取 cosKey
4. `push_note` → `-d '{"content_cos_key":"<cosKey>"}'` → 获取 `note_id`
5. `move_notes` → `-d '{"note_ids":["<note_id>"],"target_folder_id":"<folder_id>"}'`

### 按标题定位 → 追加内容

1. `search_note` → `-d '{"search_type":0,"query_info":{"title":"关键词"},"start":0,"end":20}'`
2. 从 `data.search_note_infos[].note_book_info.note_id` 取目标笔记 ID
3. 写入追加内容（按内容规模选择，见「长内容写入策略」）：
   - **短内容**：直接 `append_doc` + `-d '{...}'` 内联
   - **中等内容**：`file_write` 写 JSON 请求体 → `append_doc` + `curl -d @文件路径`
   - **长内容**：`file_write` 写 `.md` 文件 → `ima_cos_util` 上传获取 cosKey → `push_note` + `-d '{"note_id":"<note_id>","content_cos_key":"<cosKey>"}'`

### 指定笔记本 → 浏览笔记

1. `list_notebook` → `-d '{"cursor":"0","limit":20}'`
2. 从 `data.note_folder_infos` 中找 `folder_type=0` 的条目，取 `folder_id`
3. `list_note` → `-d '{"folder_id":"<folder_id>","cursor":"","limit":20}'`

## 关键约束速记

- **`folder_id` 不可传 `"0"`** —— "0" 不是有效的根目录 ID，根目录的真实 ID 需从 `list_notebook` 返回中 `folder_type=1` 的条目获取
- **写入固定 Markdown** —— 写入接口的 `content_format` 固定传 `1`
- **需要作者权限的接口** —— `append_doc`、`rename_note`、`export_note`、`push_note`（仅 note_id 非空时），否则返回 `210005`
- **时间字段是 Unix 毫秒时间戳** —— 展示时转为用户可读的日期格式
- **翻页策略不统一** —— `list_notebook` 首页 cursor 传 `"0"`，`list_note` 首页 cursor 传空字符串 `""`，`search_note` 用 `start`/`end` 偏移量。三者不可混用
- **常见错误码速查** —— `210001` 参数错误、`210005` 非笔记作者、`210009` 内容过大需拆分、`210030` 笔记本名称重复、`210035` 笔记本不存在、`210038` block_id 不存在、`210039` 目标块不可编辑

> 完整的公共结构体字段定义、枚举值、游标翻页规范、全部错误码表见 `notes-api.md`。日常调用不需要加载它——只在遇到未知错误码、需要解析嵌套返回结构、或确认完整字段定义时再查阅。
