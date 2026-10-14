# 知识库修改操作相关

## 接口详情

### 创建知识库

`/openapi/wiki/v1/create_knowledge_base`

**触发场景**：用户需要创建一个新的知识库,可以设置知识库名称、封面、简介和推荐问题等基本信息。需要指定知识库类型，个人知识库仅自己可查看，共享知识库可以分享给他人查看，订阅知识库会发布到知识库广场，可被搜索到

请求体结构：

```json
{
  "name": "string, 必填, 知识库名称, 长度限制1-25个字符（首尾不能为空格）",
  "cover_url": "string, 可选, 知识库封面URL, 需要是有效的URI格式",
  "description": "string, 可选, 知识库简介, 最大长度150个字符",
  "recommended_questions": "string[], 可选, 推荐问题列表, 最多3个问题, 每个问题长度1-1000个字符",
  "type": "int, 必填, 在 1001(个人)｜1002(共享)|1004(订阅)中选择一个"
}
```

返回结构：

```json
{
  "id": "string, 新创建的知识库ID",
  "name": "string, 知识库名称"
}
```

### 更新知识库基本信息

`/openapi/wiki/v1/update_knowledge_base_basic_info`

**触发场景**：用户需要修改已有知识库的基本信息,包括名称、封面、简介、推荐问题等。可以选择性更新部分字段。

请求体结构：

```json
{
  "update_fields": "int32[], 必填, 更新字段列表, 至少指定一个, 取值: 1-名称, 2-封面URL, 3-简介, 4-推荐问题",
  "id": "string, 必填, 知识库ID",
  "current_name": "string, 可选, 当前知识库名称, 用于校验是否传错知识库, 长度限制1-25个字符（首尾不能为空格）",
  "name": "string, 可选, 新的知识库名称, 长度限制1-25个字符（首尾不能为空格）, 当update_fields包含1时必填",
  "cover_url": "string, 可选, 新的知识库封面URL, 需要是有效的URI格式, 当update_fields包含2时必填",
  "description": "string, 可选, 新的知识库简介, 最大长度150个字符, 当update_fields包含3时必填",
  "recommended_questions": "string[], 可选, 新的推荐问题列表, 最多3个问题, 每个问题长度1-1000个字符, 当update_fields包含4时必填"
}
```

返回结构：

```json
{}
```

空响应表示成功

### 创建文件夹

`/openapi/wiki/v1/create_folder`

**触发场景**：在指定的知识库中创建新的文件夹, 如果用户未指定知识库和知识, 调用相关查询接口查询后让用户选择。

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 所属知识库 ID",
  "kb_name": "string, 选填, 所属知识库名称, 用于二次校验",
  "name": "string, 必填, 文件夹名称",
  "folder_id": "string, 可选, 所属文件夹 ID（不传则在知识库根目录下创建）",
  "folder_name": "string, 可选, 所属文件夹名称, 用于二次校验"
}
```

返回结构：

```json
{
  "media_id": "string, 新创建的文件夹 ID"
}
```

### 重命名知识条目

`/openapi/wiki/v1/rename_knowledge`

**触发场景**：重命名知识库中的文件或文件夹, 如果用户未指定知识库和知识, 调用相关查询接口查询后让用户选择。

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 所属知识库 ID",
  "kb_name": "string, 选填, 所属知识库名称, 用于二次校验",
  "media_id": "string, 必填, 文件 ID 或文件夹 ID",
  "name": "string, 必填, 新名称"
}
```

返回结构：

```json
{}
```

空响应表示成功

### 内容置顶操作

`/openapi/wiki/v1/set_knowledge_top`

**触发场景**：用户需要对知识库中的特定内容进行置顶或取消置顶操作，支持文件夹内内容管理。 该操作不适用于用户的个人知识库， 即名称为"某某的知识库"的知识库

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 知识库 ID",
  "media_id": "string, 必填, 需要置顶的数据媒体 ID",
  "is_top": "bool, 必填, 置顶状态（true表示置顶，false为取消置顶）",
  "knowledge_base_name": "string, 可选, 知识库名称（传了会额外进行知识库名称匹配验证）"
}
```

返回结构：

```json
{}
```

空响应表示成功

### 内容移动操作

`/openapi/wiki/v1/move_knowledge`

**触发场景**：用户需要将文件从一个知识库移动到另一个知识库，支持批量文件移动操作。
注意：不支持文件夹的移动操作。

请求体结构：

```json
{
  "src_knowledge_base_id": "string, 必填, 原知识库 ID",
  "dst_knowledge_base_id": "string, 必填, 目标知识库 ID",
  "dst_folder_id": "string, 可选, 目标文件夹 ID（不传默认为根目录）",
  "src_knowledge_base_name": "string, 可选, 原知识库名称, 用于二次校验",
  "dst_knowledge_base_name": "string, 可选, 目标知识库名称, 用于二次校验",
  "dst_folder_name": "string, 可选, 目标文件夹名称, 用于二次校验",
  "infos": "MediaInfo[], 必填, 移动数据列表（最多10个）"
}
```

MediaInfo结构：

```json
{
  "media_id": "string, 必填, 知识ID"
}
```

返回结构：

```json
{
  "move_results": {
    "media_id": {
      "ret_code": "int32, 返回码",
      "err_msg": "string, 错误信息"
    }
  }
}
```
