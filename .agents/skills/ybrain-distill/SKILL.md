---
name: ybrain-distill
description: |
  ybrain 外脑提炼指令：把收存的材料蒸馏成结构化要点，持续追加进知识包。
  Use when 用户运行 /ybrain-distill 或 @ybrain-distill，说"提炼这篇/把 X 做成笔记/蒸馏一下/做成知识包"，要把某份材料提炼入知识包时。
---

# ybrain-distill — 提炼（Distill）

ybrain 主技能的提炼指令入口：把材料蒸馏成结构化要点，写入双层结构中思想层的知识包。

## 前置：主技能

本指令是 ybrain 主技能的入口，不自带脚本与 API 文档，依赖同目录安装的主技能：

- 主技能目录 = `<本技能目录>/../ybrain`（两者总是同目录安装）；也可直接以技能名 `ybrain` 唤起主技能
- 传输统一走 `node "<主技能目录>/scripts/ima_api.cjs" <apiPath> <json-body>`（凭证自动从 ~/.config/ima 读取；未配置时引导用户到 https://ima.qq.com/agent-interface 获取）⛔ 禁止绕过它直接 curl 构造 IMA 请求
- 调用 API 前按主技能 SKILL.md「定位与硬边界」读对应 references/ 文档
- 每会话首次执行前，按主技能 SKILL.md 完成 ID 解析与 Bootstrap（缺结构一次确认后自动建齐）
- 本指令与主技能描述有出入时，以主技能 SKILL.md 为准

## 流程

1. 取原文（按材料类型三分支）：
   - 知识库条目 → `get_media_info` 分支（references/core-rules.md；⛔ `export_media_for_ima_sandbox` 当前凭证无权限 220030，禁止使用）
   - 笔记 → `export_note`（target_content_format=1，下载 content_url 得 Markdown）
   - 外部网页 → WebFetch
2. 蒸馏成结构化要点；可问用户一句"有什么想记的共鸣或想法？"（可选不强制——知识库条目无法附注，共鸣只能在提炼时记入笔记）。
3. 定位知识包：`search_note` 标题 `【项目】X` 或 `【领域】X`。存在 → 向用户确认后 `append_doc` 追加新章节（修订旧章节用 `export_note_blocks` + `update_note`）；不存在 → 与用户确认标题与前缀后 `import_doc` 新建。
4. 短专注标注：新章节末尾标注「可用于：项目 X / 暂无」，供长专注项目汇聚素材。

## 硬规则

- **同一项目/主题持续追加到同一篇知识包**，蒸馏增值，不要每次新建。
- 新建知识包属于范围外写入，标题与前缀必须先与用户确认。
- 长文本写入笔记不走命令行内联传参（PowerShell 会静默丢失特殊标点，类 Unix shell 的引号转义会破坏 JSON），按主技能 references 的安全写入方式落盘，写后回读校验。

## 常见错误

| 错误                                         | 纠正                                                               |
| -------------------------------------------- | ------------------------------------------------------------------ |
| 知识包每次提炼新建一篇                       | 同一项目/主题持续追加到同一篇                                      |
| 试图用 `export_media_for_ima_sandbox` 取原文 | 220030 无权限；用 `get_media_info`（条目）或 `export_note`（笔记） |
| 命令行内联 JSON 传正文导致 —— → 等字符丢失   | 写入走文件 body 方式，写后 `export_note` 下载回读校验              |
| 绕过 ima_api.cjs 直接 curl IMA 接口          | 传输统一走主技能内置脚本，凭证与错误分层复用                       |
