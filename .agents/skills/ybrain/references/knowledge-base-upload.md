# IMA知识库上传操作相关

> **ybrain 环境注记**：文件上传一律走 `core-rules.md` 的「文件上传安全门」（`scripts/preflight-check.cjs` + `scripts/cos-upload.cjs`）；原文档中的 ima 沙箱工具 upload_file.py 一节已移除（本环境不可用）。

## 接口详情

### 导入URL到知识库

`/openapi/wiki/v1/import_urls`

**触发场景**：批量导入URL链接到知识库，仅支持网页链接的导入。

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 知识库ID",
  "folder_id": "string, 可选, 文件夹ID（省略则添加到根目录）",
  "urls": ["string, URL链接1", "string, URL链接2", "string, URL链接3（最多支持10个URL）"]
}
```

返回结构：

```json
{
  "results": {
    "url1": {
      "url": "string, URL链接",
      "ret_code": "int32, 返回码",
      "media_id": "string, 成功导入后的媒体ID（失败时为空）"
    },
    "url2": {
      "url": "string, URL链接",
      "ret_code": "int32, 返回码",
      "media_id": "string, 成功导入后的媒体ID（失败时为空）"
    }
  }
}
```

### 添加知识内容

`/openapi/wiki/v1/add_knowledge`

**触发场景**：向知识库中添加知识内容，该接口仅当添加笔记到知识库时需要单独调用

请求体结构：

```json
{
  "media_type": "int32, 必填, 媒体类型ID， 笔记类型直接填写11",
  "title": "string, 必填, 标题",
  "knowledge_base_id": "string, 必填, 知识库ID",
  "folder_id": "string, 可选, 文件夹ID（省略则添加到根目录）",
  "note_info": {
    "content_id": "string, 必填, 笔记的标识ID, 一般名为note_id, 未明确时, 可通过本目录 notes.md 的 search_note 获取"
  }
}
```

返回结构：

```json
{
  "media_id": "string, 成功添加后的媒体ID"
}
```
