# IMA知识库读取操作相关

## 接口详情

### 按名称定位知识库、查看知识库详情、列出可写知识库、获取知识库id

`/openapi/wiki/v1/search_knowledge_base`

**触发场景**：用户需要操作知识库时， 通过该接口来搜索knowledge_base_id， 该接口将返回知识库信息列表，并携带用户在此知识库中的身份信息,
仅当用户角色为（创建者/协作成员/管理员）时拥有操作知识库的权限， 当用户在该知识库的权限为普通成员时， 应该拒绝用户的操作知识库请求

请求体结构：

```json
{
  "query": "string, 必填, 搜索关键字, 使用空字符串（注意不是空格）时将展示用户的所有知识库",
  "cursor": "string, 必填, 游标, 首次搜索时传入空字符串, 需要翻页时, 将返回结构中的next_cursor填入此字段",
  "limit": "uint64, 必填, 数量限制（1-20）",
  "knowledge_base_id": "string, 可选, 知识库 ID，如果传了知识库ID，则按照ID拉取知识库信息"
}
```

返回结构：

```json
{
  "info_list": [
    {
      "kb_id": "string, 知识库 ID",
      "kb_name": "string, 知识库名称",
      "member_count": "int, 成员数量",
      "content_count": "int, 内容数量",
      "description": "string, 知识库简介",
      "creator": "string, 创建者昵称",
      "role_type": "string, 在此知识库中的角色",
      "base_type": "string, 该知识库的类型， 分为个人/共享/我创建的订阅/我加入的订阅"
    }
  ],
  "is_end": "bool, 是否到达列表末尾",
  "next_cursor": "string, 下页游标,当需要翻页时, 将该值作为cursor字段的值"
}
```

### 逐级浏览知识库文件夹结构，支持按标签筛选

`/openapi/wiki/v1/get_knowledge_list`

**触发场景**：用户需要浏览知识库中的文件和文件夹列表,支持分页浏览和文件夹导航功能, 支持标签搜索。

请求体结构：

```json
{
  "cursor": "string, 必填, 游标, 首次浏览时为空, 需要翻页时, 将返回结构中的next_cursor填入此字段",
  "limit": "uint64, 必填, 数量限制（1-50）",
  "knowledge_base_id": "string, 必填, 知识库 ID",
  "folder_id": "string, 可选, 文件夹 ID（省略则列出根目录）",
  "knowledge_base_name": "string, 可选, 知识库名称, 用于二次校验",
  "folder_name": "string, 可选, 文件夹名, 用于二次校验",
  "tags": "string[], 可选, 搜索tags"
}
```

返回结构：

```json
{
  "knowledge_list": [
    {
      "media_id": "string, 媒体 ID",
      "title": "string, 标题",
      "parent_folder_id": "string, 所属文件夹 ID",
      "tags": "string[], 标签列表",
      "media_type": "int32, 媒体类型"
    }
  ],
  "is_end": "bool, 是否到达列表末尾",
  "next_cursor": "string, 下页游标,当需要翻页时, 将该值作为cursor字段的值",
  "current_path": [
    {
      "folder_id": "string, 文件夹 ID",
      "name": "string, 文件夹名称"
    }
  ]
}
```

### 按名称定位文件/文件夹（获取位置和类型信息）

`/openapi/wiki/v1/search_knowledge`

**触发场景**：用户需要在指定的知识库中按名称定位文件/文件夹，获取其位置（parent_folder_id）和类型（media_type）信息。按内容搜索应使用 Search/Fetch 工具。

请求体结构：

```json
{
  "query": "string, 必填, 搜索关键词",
  "knowledge_base_id": "string, 必填, 知识库 ID",
  "knowledge_base_name": "string, 可选, 知识库名称, 用于二次校验"
}
```

返回结构：

```json
{
  "info_list": [
    {
      "media_id": "string, 媒体 ID",
      "title": "string, 标题",
      "parent_folder_id": "string, 所属文件夹 ID",
      "media_type": "int32, 媒体类型"
    }
  ]
}
```

### 发现公开广场上的知识库

`/openapi/wiki/v1/search_knowledge_base_in_square`

**触发场景**：用户需要在知识库广场中发现和搜索公开的知识库,通过关键词查找相关知识库并了解其基本信息（如成员数、内容数、创建者等）。与 `search_knowledge_base` 不同,此接口用于发现用户尚未加入的公开知识库。

请求体结构：

```json
{
  "question": "string, 必填, 搜索关键词",
  "cursor": "string, 必填, 游标, 首次搜索时为空, 需要翻页时, 将返回结构中的next_cursor填入此字段",
  "limit": "uint64, 必填, 数量限制（1-20）"
}
```

返回结构：

```json
{
  "items": [
    {
      "kb_name": "string, 知识库名称",
      "kb_id": "string, 知识库ID",
      "member_count": "uint64, 加入人数",
      "content_count": "uint64, 内容数量",
      "creator": "string, 创建者名称",
      "match_reasons": "int32[], 匹配原因列表, 取值: 0-未定义, 1-匹配知识库名称, 2-匹配知识库简介, 3-匹配知识库创建者名称, 4-匹配媒体标题"
    }
  ],
  "next_cursor": "string, 下页游标, 当next_cursor为空时表示没有更多数据"
}
```

> 注意：此接口没有 `is_end` 字段，以 `next_cursor` 为空作为翻页终止条件，与其他接口不同。

### 导出媒体到 workspace

`/openapi/wiki/v1/export_media_for_ima_sandbox`

**触发场景**：用户需要导出知识库中某个媒体的内容,获取媒体的类型及其内容的下载链接、如果是笔记则为笔记内容的下载链接。

请求体结构：

```json
{
  "media_id": "string, 必填, 媒体 ID"
}
```

返回结构：

```json
{
  "media_type": "int32, 媒体类型, 取值: 0-未使用, 1-PDF文件, 2-网页, 3-Word文件, 4-PPT文件, 5-Excel文件, 6-公众号文章, 7-Markdown, 9-图片, 11-笔记, 12-QA对话, 13-TXT文本, 14-Xmind, 15-录音, 16-网页视频, 17-对话, 18-视频, 19-播客, 20-HTML, 21-EPUB文件, 99-文件夹",
  "media_content_url_info": {
    "url": "string, 媒体内容下载链接",
    "headers": "map<string,string>, 访问链接所需header, 如果该值非空，则需要在请求url的时候同时传入header"
  }
}
```
