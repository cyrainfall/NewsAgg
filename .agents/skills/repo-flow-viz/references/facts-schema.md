# facts JSON schema（渲染层的唯一契约）

顶层三个键：`meta` / `groups` / `nodes`。renderer 不认识任何具体路径 ——
页面上的一切都从这三个键来。

## meta

```jsonc
{
  "title": "NewsAgg",                 // 进 <title> 和 header
  "root": "/abs/path/to/repo",        // verify_facts 拿它当 --root 默认值
  "source_script": "viz/facts/build_facts.py",   // 显示在右栏"来源"行
  "stats": [["ADR", 65], ["测试", 6]] // 顶部统计条，顺序即显示顺序
}
```

`stats` 的值**必须磁盘数出来**（`count_dir`）。手写常数三个月后是错的，
而页面上不会有任何痕迹提示它过期。

## groups

```jsonc
{ "title": "主流程第 1 段 · 21:00 起",
  "ids": ["n01-pull", "n02-style"] }
```

- `ids` 里的 id 必须都在 `nodes` 里 —— verify 检查 F。
- 一个 id 可以出现在多个组里（跨阶段共用的节点），但**不要滥用**：
  重复出现会让左栏变长，读者要滚动才能看全流程。
- 组的标题是给读者看的导航，写"这一段解决了什么问题"，不要写"第 3 组"。
- 未被任何组引用的节点不会渲染 —— 静默丢失。改完 facts 看一眼节点数对不对。

## nodes

```jsonc
{
  "id": "n05-collect",            // 唯一；DOM 的 data-id，键盘导航按 groups 顺序用
  "step": "③",                     // 左栏的小徽标，1–2 字符
  "title": "采集",                 // 左栏显示名，短
  "actor": "collect.py",           // 谁在跑
  "when": "08/13/18 各一次",        // 什么时候跑
  "one_liner": "一句话",            // 左栏 hover / 中栏副标题
  "layer": "collect",              // 决定配色，任意字符串
  "does": ["做了什么 1", "…"],      // 中栏"做了什么"
  "why":  ["为什么 1", "…"],        // 中栏"为什么" —— 全页最贵的字段
  "reads": [文件对象…],             // 中栏"读什么"
  "writes": [文件对象…],            // 中栏"写什么"
  "files": [文件对象…]              // 点开看原文的摘录
}
```

`does` / `why` / `reads` / `writes` / `files` 都可省略，但**全空就是一个空节点** ——
verify 会 warn。`files` 决定点开能看到什么；`reads`/`writes` 只是中栏的读写清单，
它们指向的文件仍需在某个节点的 `files` 里有摘录，否则读者点不到内容。

## 文件对象

两种形态，**区别是诚实与否**。

### 原文切片（有真实行号）

```jsonc
{ "path": ".agents/.../collect.py",  // 相对 meta.root
  "start": 41, "end": 48,            // 1-based 闭区间
  "total": 607,                      // 文件实际总行数
  "note": "默认源清单",               // 一句话说明看的是什么
  "text": "DEFAULT_SOURCES = [...]"  // 磁盘上第 41–48 行，逐字
}
```

`start`/`end` 只在确实是原文切片时才填。verify 会把 `text` 的首行末行
与磁盘对应行逐字比对 —— 对不上就是 FAIL。

### 结构化视图（不是原文）

```jsonc
{ "path": ".newsagg/publish-ledger.jsonl",
  "start": null, "end": null,
  "loc": "第 12 行 / 共 12 行（JSON 已格式化便于阅读）",
  "note": "最后一条发布回执",
  "text": "{ … }" }
```

`start === null` 时 renderer 显示 `loc` 而不是行号，verify 要求 `note` 或 `loc`
至少有一个（否则读者无从判断这是不是原文）。

**为什么允许第二种：** `jsonline` 的文本是 `json.dumps(indent=2)` 出来的，
已经不是文件里那一行的样子。标行号就是撒谎。所以宁可 `start=null` +
`loc` 说明，也不要假装它是第 N 行。

## 字段速查

| 字段 | 必填 | 谁在管 |
|---|---|---|
| `path` | ✓ | verify 检查存在性 |
| `text` | ✓ | 页面正文，空则渲染失败 |
| `start` / `end` | 原文切片时 ✓ | verify 逐行比对 |
| `total` | ✓ | 文件行数，变了就提醒重跑 |
| `note` | 建议 | 交代"看的是什么"和"丢了什么" |
| `loc` | 仅 start=null | 替代行号显示 |
