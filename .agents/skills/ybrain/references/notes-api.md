---
name: IMA笔记 API 深度参考
---

# IMA笔记 API 深度参考

本文档是 SKILL.md 的补充参考，包含公共结构体完整字段定义、枚举值、游标翻页规范和错误码表。

日常调用只看 SKILL.md 即可。以下场景需要查阅本文档：

- 遇到未知错误码，需要查错误码表
- 需要解析嵌套返回结构（如 `note_ext_info`、`highlightInfo` 的完整字段定义）
- 需要了解游标翻页的详细规范

---

## 公共结构体

#### NoteBookInfo（笔记信息）

```jsonc
{
  "note_id": "7446767201161519", // string, 笔记唯一 ID
  "title": "会议纪要", // string, 标题
  "summary": "本周需要完成的事情...", // string, 简介
  "create_time": 1775447655232, // int64, 创建时间（Unix 毫秒）
  "modify_time": 1775447854032, // int64, 修改时间（Unix 毫秒）
  "cover_image": "", // string, 封面缩略图 URL
  "note_ext_info": {
    // NoteExtinfo, 扩展字段
    "folder_id": "foldera67e14c4", //   string, 所属笔记本 ID
    "folder_name": "工作笔记", //   string, 所属笔记本名称
  },
}
```

---

#### NoteFolderInfo（笔记本信息）

```jsonc
{
  "folder_id": "foldera67e14c494e51bc5", // string, 笔记本唯一 ID
  "name": "工作笔记", // string, 笔记本名称
  "create_time": 1775208672325, // int64, 创建时间（Unix 毫秒）
  "modify_time": 1775208672813, // int64, 修改时间（Unix 毫秒）
  "note_number": 2, // int64, 笔记本内笔记数量
  "parent_folder_id": "", // string, 上级笔记本 ID（支持嵌套）
  "folder_type": 0, // FolderType: 0=USER_CREATE(用户自建), 1=TOTAL(全部笔记), 2=UN_CATEGORIZED(未分类)
}
```

---

#### QueryInfo（搜索条件）

```jsonc
{
  "title": "关键词", // string, 标题搜索关键词
  "content": "关键词", // string, 正文搜索关键词
}
```

---

#### SearchNoteInfo（搜索结果条目）

```jsonc
{
  "note_book_info": {
    /* NoteBookInfo, 见上方 */
  },
  "highlightInfo": {
    // map<string, string>, 高亮匹配
    "doc_title": "包含<em>高亮词</em>的标题", //   key: doc_title, value: 含 <em> 标签的文本
  },
}
```

---

## 枚举值

### `ContentFormat`（文本类型）

| 值  | 名称      | 说明          |
| --- | --------- | ------------- |
| `0` | PLAINTEXT | 纯文本        |
| `1` | MARKDOWN  | Markdown 格式 |
| `2` | JSON      | JSON 格式     |

### `SearchType`（检索方式）

| 值  | 名称        | 说明             |
| --- | ----------- | ---------------- |
| `0` | DOC_TITLE   | 标题检索（默认） |
| `1` | DOC_CONTENT | 正文检索         |

### `SortType`（排序方式）

| 值  | 名称        | 说明             |
| --- | ----------- | ---------------- |
| `0` | MODIFY_TIME | 更新时间（默认） |
| `1` | CREATE_TIME | 创建时间         |
| `2` | TITLE       | 标题             |
| `3` | SIZE        | 大小             |

### `FolderType`（笔记本类型）

| 值  | 名称           | 说明               |
| --- | -------------- | ------------------ |
| `0` | USER_CREATE    | 用户自建           |
| `1` | TOTAL          | 全部笔记（根目录） |
| `2` | UN_CATEGORIZED | 未分类             |

---

## 游标翻页使用规范

### 笔记本列表（ListNoteFolder）

1. 首次请求：`cursor` 传 `"0"`
2. 检查返回的 `is_end`：`false` 表示还有更多数据
3. 将返回的 `next_cursor` 作为下次请求的 `cursor`
4. `is_end = true` 时停止翻页

### 笔记列表（ListNote）

1. 首次请求：`cursor` 传空字符串 `""`
2. 响应不返回 cursor 字段，翻页采用偏移量方式：下一页的 `cursor` 传已获取的条目总数（如首页 limit=20 则第二页传 `"20"`，第三页传 `"40"`）
3. `is_end = true` 时停止翻页

### 搜索（SearchNote）

1. 首次请求：`start: 0, end: 20`
2. 翻页时递增 start/end
3. `is_end = true` 时停止

---

## 错误码（ErrorCode 枚举）

| 错误码 | 名称                   | 说明                                                           |
| ------ | ---------------------- | -------------------------------------------------------------- |
| 0      | OK                     | 成功                                                           |
| 210001 | PARAM_ERROR            | 参数错误                                                       |
| 210002 | REQ_WITH_INVALID_UID   | 携带无效的 UID                                                 |
| 210003 | SERVICE_ERROR          | 服务器内部错误（服务端不可用，无需重试，直接告知用户稍后再试） |
| 210004 | SPACE_NOT_ENOUGH       | 用户空间不够                                                   |
| 210005 | NOTE_NOT_OWNER         | 不是笔记的作者                                                 |
| 210006 | NOTE_IS_DELETE         | 笔记已被删除                                                   |
| 210007 | COS_CRED_ERROR         | 获取 COS 上传凭证出错                                          |
| 210008 | VERSION_CONFLICT       | 版本冲突                                                       |
| 210009 | CONTENT_SIZE_OVERLOAD  | 单篇笔记超过最大限制                                           |
| 210010 | EXIST_GUIDE            | 新手引导笔记添加重复                                           |
| 210011 | SHARE_DOC_NOPERM       | 共享知识库的笔记无权访问                                       |
| 210012 | USER_IS_DELETE         | 用户已注销                                                     |
| 210030 | notebook_NAME_EXIST    | 笔记本名称重复                                                 |
| 210031 | notebook_NUM_LIMIT     | 笔记本数量达到上限                                             |
| 210032 | BATCH_EXEC_FAIL        | 批量操作部分失败                                               |
| 210033 | BATCH_EXEC_ALL_FAIL    | 批量操作全部失败                                               |
| 210034 | PRIVATE_NOTE_NOT_OWNER | 笔记私有且不是作者                                             |
| 210035 | FOLDER_NOT_EXIST       | 笔记本不存在                                                   |
| 210036 | ADD_KNOWLEDGE_FAIL     | 笔记添加知识库失败                                             |
| 20002  | —                      | apiKey 超过最大限频                                            |
| 20004  | —                      | apiKey 鉴权失败                                                |
