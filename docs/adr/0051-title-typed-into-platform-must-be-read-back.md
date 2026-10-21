# 标题敲进平台后必须读回：逐字输入会吞字，而吞字会让一次真实发布被判成没发

## Context

2026-10-21 21:00 主流程第一次按新赛道（ADR-0050）真发，成稿标题是
「老板叫停AI裁员，中间层岗位已经换成外包」（19 字，符合 2~30 字限制）。

`publish.py` 的返回是 `success: true, action: "published"`，但门 5 的反查
（`content list --limit 20`）判 `title_not_found`，`verify_kinds` 两项都是 0，
于是整轮被判 `not_published_needs_review`、退出码 1、写回与简报结论全部走失败分支。

人工核实（`content list` 原始输出）后发现：**文章已经发出去了**，gid
`7699135881185149480`、`publishTime 1792594802`（= 2026-10-21T23:00:02）、
`itemStatus 20`。但平台上挂的标题是：

> 老板叫停AI裁员，**中间层位**已经换成外包

比成稿少了一个「岗」字。反查是按 `publish.py` 手里的 `title`（成稿标题、正确的
那一份）去查的，而平台上的标题已经不同，所以查不到。

## 发现

### 一、`page.keyboard.type()` 逐字敲中文，会被平台输入框吞字

`reference/toutiao-ops/cli/src/publish-article.js:46`（改前）：

```js
await page.click(titleSelector, { force: true });
await page.keyboard.type(title, { delay: 50 + Math.random() * 80 });
```

`keyboard.type` 对每个字符依次派发 keydown/keypress/input/keyup。平台的标题
输入框有字数统计与联想/防抖逻辑，在高速连续输入时会吞掉个别字符——**而且全程
不抛异常**。`keyboard.type` 的返回是 `void`，它只保证「按键派出去了」，
不保证「输入框里留下的是这个字符串」。

### 二、CLI 把本地变量当成平台上的事实回报

改前 `publishArticle()` 返回 `{ success: true, action: 'published', title }`，
其中 `title` 是函数入参、经过长度截断的**本地变量**。也就是说：

- CLI 自己不知道平台上是什么标题；
- 调用方（`publish.py`）按这个 `title` 去反查；
- 反查查不到 → 判「没发出」→ 导向「人工核实是否重发」。

这是本项目第四次出现同一个形状（ADR-0034 / 0045 / 0046 / 0047 之后）：
**动作执行了，但没人验证它产生了期望的效果**。ADR-0047 把它总结成
「动作成功 ≠ 结果正确」，这条 ADR 是第五个实例，也是最贵的一个——
它同时污染了三件事：发布成败判定、`verify_kinds` 计数、以及平台上真实的标题。

### 三、反查失败的代价是不对称的

反查判「查不到」时，`publish.py` 走 `not_published_needs_review` 并且**不重发**
（ADR-0011）。这个保守选择本身是对的：宁可漏发不可重发。但它的前提是
「查不到」真的意味着「没发出」。当标题被吞字时，这个前提不成立，
系统就会每天把一次成功发布报成失败，并在简报里要求人去后台核实——
而人到了后台，按简报里的标题去搜，同样搜不到。

## Decision

### 一、输入标题后**读回输入框的值**，不一致就重试并回落 `fill`

新增 `typeTitleVerified(page, selector, title)`：

1. 逐字敲 → `page.inputValue(selector)` 读回 → 与预期一致则返回；
2. 不一致则清空（`Meta+A`/`Control+A` + `Backspace`）重敲一次；
3. 两次都不一致则 `page.fill(selector, title)`——`fill` 是一次性赋值，
   不经过按键路径，不会被输入法吞；
4. 无论结果如何，**返回实际读到的值**，由调用方决定。

### 二、CLI 返回平台实际标题，并区分「预期标题」

`publishArticle()` 的返回值改为：

```js
title,                      // 平台实际收到的标题（读回值）
expected_title,             // 我们本来要发的标题
title_truncated_by_platform: titleOnPage !== title,
```

字段名保持 `title` 指「平台上的事实」，因为反查、计数、简报全都消费这个字段，
语义必须是「平台上是什么」而不是「我们想要什么」。

### 三、反查与计数一律按**平台实际标题**

`publish.py` 从 CLI 的 stdout 里取 `title` 作为 `title_on_platform`，写进账本，
并用它做三件事：

- `content list` 的 `in blob` 判定；
- `count_title_kinds(payload, title_on_platform)`——按错标题数，两项都会是 0；
- 简报里的提醒。

### 四、账本与简报必须显式说出「平台标题 ≠ 成稿标题」

`record["title_on_platform"]` 落库；`brief.py` 在 ① 段追加一行：

> ⚠ 平台实际标题是《…》——成稿标题「…」在输入框里被吞了字（ADR-0051）。
> 到后台按**实际标题**查。

不写这一行，简报会把人指向一个后台搜不到的标题。

## Consequences

1. **不再误判**：吞字时反查按实际标题查得到，`verify_kinds` 也能数对，
   不会再出现「真实发布被判成未发出」。
2. **吞字本身仍会发生**：`fill` 是回落路径，不是根治。平台输入框的行为不受
   我们控制；本 ADR 保证的是「吞了字我们知道、且账本记的是真实值」，
   不是「不会吞字」。
3. **平台上可能挂着缺字的标题**：本次 10-21 的标题就少了「岗」字。系统不会
   自动修改已发布内容（ADR-0017），是否重发/改标题由人决定。
4. **`title_truncated_by_platform` 是新增的失败模式标签**：它出现时说明
   输入路径有问题，应当在简报 ⑤ 段被看到，而不是被当成正常。
5. **同类风险仍在**：正文走 `pasteMarkdownAsRichText`（剪贴板粘贴），
   微头条走 `type`、视频标题/话题/简介也都走 `type`。本次只修了文章标题，
   **其余输入点未读回校验**——这是已知的、明确接受的残余风险。
