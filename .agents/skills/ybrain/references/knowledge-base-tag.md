# IMA 知识库标签管理

> 本文集中记录所有标签相关接口（读 + 写共 5 个）。

## 适用场景速查

| 用户意图                                   | 接口       |
| ------------------------------------------ | ---------- |
| 给文件打标签                               | tag_add    |
| 从文件移除某个标签                         | tag_remove |
| 列出 / 搜索知识库中的所有标签              | tag_list   |
| 重命名标签（所有打了该标签的文件自动迁移） | tag_rename |
| 删除标签（所有文件上的关联自动解除）       | tag_delete |

## 重要约束

1. **写操作权限**：tag_add / tag_remove / tag_delete / tag_rename 是写操作，**普通成员调用会返回 220030 无权限**。调用前必须通过 search_knowledge_base 确认用户在该知识库的角色为 创建者/协作成员/管理员。
2. **不可逆操作**：tag_delete 和 tag_rename 是破坏性操作（删除会解除所有关联，rename 在新名重复时自动合并），**调用前必须向用户显式确认**。
3. **重复操作不报错**:tag_add 重复打、tag_remove 移除不存在的、tag_delete 删除不存在的——均直接返回成功。批量操作前不用先查状态,超时也可放心重试。
4. **kb_id 一致性**：5 个接口都强制传 knowledge_base_id，调用前必须 check 当前 kb_id 与目标知识库一致。
5. 文件夹（media_type=99）不支持打标签。

## 接口详情

### 给文件打标签

`/openapi/wiki/v1/tag_add`

**触发场景**：用户需要给知识库中的文件添加标签。标签不存在时自动创建。

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 所属知识库 ID",
  "kb_name": "string, 可选, 所属知识库名称, 用于二次校验",
  "item_id": "string, 必填, 文件 ID（标签挂在文件上，对应 get_knowledge_list 返回的 media_id）",
  "item_name": "string, 可选, 文件名, 用于二次校验",
  "tag_name": "string, 必填, 标签名称（不存在时自动创建）"
}
```

返回结构：

```json
{}
```

空响应表示成功。

---

### 从文件移除标签

`/openapi/wiki/v1/tag_remove`

**触发场景**：用户需要从某个文件上移除某个标签。**仅解除文件↔标签的关联，不删除标签本身**。

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 所属知识库 ID",
  "kb_name": "string, 可选, 所属知识库名称, 用于二次校验",
  "item_id": "string, 必填, 文件 ID",
  "item_name": "string, 可选, 文件名, 用于二次校验",
  "tag_name": "string, 必填, 要移除的标签名称"
}
```

返回结构：

```json
{}
```

空响应表示成功。

---

### 删除标签

`/openapi/wiki/v1/tag_delete`

**触发场景**：用户需要删除知识库中的某个标签。**所有文件上的该标签关联自动解除**。

⚠️ **不可逆操作**：调用前必须向用户显式确认。

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 所属知识库 ID",
  "kb_name": "string, 可选, 所属知识库名称, 用于二次校验",
  "tag_name": "string, 必填, 要删除的标签名称"
}
```

返回结构：

```json
{}
```

空响应表示成功。

---

### 重命名标签

`/openapi/wiki/v1/tag_rename`

**触发场景**：用户需要修改标签名称。所有已打该标签的文件自动更新为新名称。

⚠️ **特殊行为**：如果新名称已存在，**自动合并**——旧标签关联的文件并入新标签，旧标签删除。这一行为可能导致两组原本不同标签的文件被合并到同一个标签下。**调用前应先用 `tag_list(keyword=新名)` 检查新名是否已存在,若存在必须告知用户合并后果并显式确认。**

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 所属知识库 ID",
  "kb_name": "string, 可选, 所属知识库名称, 用于二次校验",
  "old_tag_name": "string, 必填, 旧标签名称",
  "new_tag_name": "string, 必填, 新标签名称"
}
```

返回结构：

```json
{}
```

空响应表示成功。

---

### 列出 / 搜索标签

`/openapi/wiki/v1/tag_list`

**触发场景**：

- 用户想知道知识库里有哪些标签 → 不传 keyword，分页拉全
- 用户想检查某个标签是否存在 → 传 keyword 做关键词过滤

请求体结构：

```json
{
  "knowledge_base_id": "string, 必填, 知识库 ID",
  "kb_name": "string, 可选, 知识库名称, 用于二次校验",
  "keyword": "string, 可选, 按标签名关键词过滤（不传则列出所有）",
  "cursor": "string, 可选, 分页游标, 首次不传, 需要翻页时将返回结构中的 next_cursor 填入此字段",
  "limit": "uint64, 必填, 每页数量（默认 50, 最大 100）"
}
```

返回结构：

```json
{
  "items": [
    {
      "tag_name": "string, 标签名称"
    }
  ],
  "is_end": "bool, 是否到达列表末尾",
  "next_cursor": "string, 下页游标, 当需要翻页时将该值作为 cursor 字段的值"
}
```
