# 纯 HTTP 写路径不可启用：字段名、签名、内容形态三处同时不对

## Context

阶段 1 要把主环境迁到 2GB 内存的服务器。服务器上装 Chrome + Xvfb 可以，但
Playwright 单次读回就要 1GB+ 内存（ADR-0071 实测：Chromium 三进程
403+231+207MB，Chrome 宿主 215MB）。所以一个自然的设想是：**读走纯 HTTP，
写也走纯 HTTP，服务器就完全不需要浏览器了。**

读这条路已经通了（ADR-0071）。写这条路只有 `toutiao_api.py` 里一个从未
在线上跑通的 `build_publish_form()`，它被 `UnverifiedWrite` 门挡住
（`allow_unverified=True` 必须显式传，默认抛异常）。

在决定「是否值得把服务器上省掉 Chrome」之前，需要先回答：纯 HTTP 写路径
到底差多远？派了一个只读调查 subagent，约束是所有写请求必须在 route 层
abort、绝不允许任何草稿/发布/图片上传到达平台。

结论：**比预想的远得多。三个独立的理由，任何一个都足以否掉它。**

## Decision

**不启用纯 HTTP 写路径。服务器上的发布仍然需要 Chrome + Xvfb。**

但这不等于「什么都没做」。调查过程产出了三样必须落盘的东西：真实请求的
字段全集、抓到的正文形态、以及平台在写路径上真实存在的闸门。这些变成
两份可执行的产物：一份事实记录（本文），一份代码层面的诚实标注。

### 一、字段名几乎全错（`build_publish_form` 的 docstring 之前是错的）

`build_publish_form()` 的 docstring 写着「字段全集来自 bundle 模块 12306 的
默认值对象，逐个抄的」，并说「字段名可信，嵌套序列化不可信」。

**实测是字段名也不可信。** 抓到的真实 publish body（save=1）解码后有 31
个字段，仓库的 `build_publish_form()` 有 35 个键，**只有 9 个名字对得上**
（`source / title / content / save / entrance / timer_status / timer_time /
praise / disable_praise`）。

仓库发了但真实请求里没有的 **26 个字段**：`article_type`、`pgc_id`、
`titleId`（真实是 `title_id`）、`textCount`、`externalLinkChecked`、
`externalLink`、`claimOrigin`、`copyRightChecked`、`addThirdTitle`、
`hasSubTitle`、`subTitle`、`subCoverList`、`subCoverType`、`errPattern`、
`position`、`coverList`、`coverType`、`articleAdType`、`isFansArticle`、
`visibility_level`、`timingStatus`/`timingTime`、`slaveItems`、
`isMultiTitle`、`isStoryArticle`、`infoSource`。

真实有但仓库缺的 **22 个**，其中最要紧的是 `extra`——它装
`content_source` / `content_word_cnt` / `gd_ext` / `info_source` /
`is_multi_title` / `sub_titles` / `manual_selected_city`；还有
`title_id`、`draft_form_data`、`pgc_feed_covers`、`search_creation_info`、
`mp_editor_stat` 与一批 0/空的 snake_case 开关（`article_ad_type`、
`is_fans_article`、`govern_forward`、`tree_plan_article`、`claim_exclusive`、
`activity_tag`、`trends_writing_tag` …）。

值也有反的：`praise` 仓库 0 / 真实 1；`disable_praise` 仓库 1 / 真实 0；
`content_source=100000000402` 仓库放顶层 / 真实在 `extra` 内部。

**为什么当初会抄错**：模块 12306 是客户端**表单状态默认值对象**，不是请求
字段表。发布页把它当默认值用，但真实请求是若干 builder（timing /
common / title format / editor / 12306 相关）拼出来的，每个 builder 只负责
自己那一段。照抄状态默认值等于把「界面上的当前值」当成「请求里要发的
东西」。

**唯一抄对的是封面元素结构**：`pgc_feed_covers` 的元素键集合
（`origin_uri` / `thumb_url` / `thumb_width` / `thumb_height` /
`extra.from_content` / `extra.from_content_idx` / `extra.from_content_uri`）
与平台只读接口回读的已发布文章逐字一致。错的只是外层字段名
（`coverList` → `pgc_feed_covers`）与配套的
`draft_form_data={"coverType":2}`（枚举 `NONE:1 / SINGLE:2 / MULTI:3`，
值是数字不是 "SINGLE"）。

**调查一开始问的那个问题本身是伪问题**：被问的五个字段
（`coverList` / `slaveItems` / `subCoverList` / `position` / `infoSource`）
在真实请求里**一个都不存在**。`coverList` 的真身是 `pgc_feed_covers` +
`draft_form_data.coverType`；`position` 的真身是 `extra.manual_selected_city`；
`infoSource` 的真身是 `extra.info_source`；`subCoverList` 只存在于客户端
状态；`slaveItems` 根本是**服务端返回字段**（文章列表 API），从来不是请求
字段。

### 二、平台在写路径上有签名与风控闸，仓库一个都不处理

真实 publish 请求带 `_signature`、`msToken`、`a_bogus`（query）与
`x-secsdk-csrf-token`（header），由
`byted_acrawler.init({aid:1231, intercept:!0, enablePathList:["/mp/agw/article/publish"]})`
产出。`enablePathList` 明确只拦 publish 端点——**写路径被单独加固了，读路径
没有**。

`browserless/http.py` 与 `toutiao_api.py` 对这几个参数**一个都不处理**
（grep 全空）。同时 POST 走 `secsdk.csrf.setProtectedHost` 的 CSRF 校验。

仓库在 2026-10-25 试过一次「空 body POST → code=7050 保存失败」。**那次
只证明了空 body 过了闸**，不能证明带真实标题与正文的请求会过闸——风控阈值
通常与内容、频率相关。

错误码方面，仓库也没有处理分支：2222 =「可信浏览器校验」、3001/3002 =
实名/验证弹窗、3022 = 账号未注册。

### 三、正文形态不是平台原生形态

`content` 是 ProseMirror 序列化后的 HTML，段落带 `data-track` 序号、
H1 带 `class="pgc-h-forward-slash"`：

```
<h1 class="pgc-h-forward-slash" data-track="1">被裁半年降薪15%：四条原因里没有AI</h1>
<p data-track="2">…</p>
```

仓库的 `publish.py:_markdown_to_html` 产出的是 `<p>/<h2>/<h3>/<blockquote>/
<ul><li>/<strong>/<a href>`——没有 `data-track`、没有那个 class，标题层级
用的是 h2/h3。

而浏览器路径（`reference/toutiao-ops/cli/src/publish-article.js:
pasteMarkdownAsRichText`）是 `marked.parse(md, {breaks:true, gfm:true})` 粘进
contenteditable，**让平台自己 `getHTML()` 重新序列化** → 产出的才是平台原生
形态。

所以纯 HTTP 路径不只是「少几个字段」，是**内容也是近似版**。

## 为什么不顺手把 `build_publish_form` 改对

因为改对也还是不能开。

改字段名、stringify、content 形态这三件事都有实测依据（抓包 + bundle +
只读回读），可以改。但改完之后：

- `_signature` / `msToken` / `a_bogus` / CSRF token 仍然没有 → 真实标题正文
  很可能被风控挡在门外，**而且失败是静默的**（返回码含义不明）；
- 定时发布（`/mp/agw/article/timer_publish/post_now`）仓库完全没实现；
- 写操作不可逆，而验证它需要一次真实的发布。

在没有「一次可以撤回的验证」之前，把写路径改成「看起来对」比保持
「明确写着没验证过」更危险：后者每次都会撞 `UnverifiedWrite`，前者会开始
在风控阈值上试运气。

**所以本 ADR 的可执行部分只有两条**：

1. `build_publish_form()` 的 docstring 改成据实陈述——把「字段名可信，
   嵌套序列化不可信」换成「字段名与嵌套序列化都未经验证，且实测字段名与
   真实请求不符；写路径被 `UnverifiedWrite` 挡住是正确状态」。
2. 把真实请求的字段全集与闸门清单落进 `reference/toutiao-ops/` 的一份
   事实记录里，让下一个想碰这条路的 agent 不必重新抓一遍包。

## Consequences

- **服务器上的发布仍然需要 Chrome + Xvfb。** 阶段 1 因此仍要装
  `xorg-x11-server-Xvfb` 与 `chromium`（dnf 源里有，见阶段 1 第 9 步的
  勘察记录）。这是本 ADR 最重要的后果：它没有让阶段 1 变简单，只是让
  「能不能变简单」这个问题有了答案。
- **服务器上的表现读回不依赖 Chrome**（ADR-0071 已落地）。所以 2GB 机器上
  的 Chrome 只在「发布」那一刻起来，用完就退——内存压力从「全天占用」
  降为「发布时占用」，这正是 ADR-0023 那套内存防御要解决的问题。
- `UnverifiedWrite` 门保持。`--allow-unverified-write` 保留但**不得在无人
  值守链路里使用**：它绕过的不是「不确定」，是「没有验证过」。
- 事实记录落在 `reference/toutiao-ops/docs/publish-request-shape.md`。
  `reference/` 是 vendored 目录（ADR-0012：参考代码不入库，需要时重新
  clone）——**这份记录会随 clone 一起消失**。所以另有一份同内容的
  `docs/adr/0073-pure-http-write-path-is-not-viable.md` 里的字段表留在
  仓内，可独立阅读。
- 遗留：真实 publish body 里的 `pgc_feed_covers` **非空**时的落法没有实拍
  （要实拍必须真上传图片或选正版图，两者都越界）。已知形状来自服务端
  只读回读 + bundle，置信度中高。`save=0` 草稿路径的 body 形态有第三种
  （括号式序列化 `extra[content_word_cnt]=…`），置信度中。
