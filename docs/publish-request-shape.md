# 头条发布请求的真实形状（2026-10-27 抓包实测）

> 这份记录是 `docs/adr/0073-pure-http-write-path-is-not-viable.md` 的证据附件。
> 它回答一个具体问题：**纯 HTTP 发一篇头条，body 长什么样？**
> 抓包方式：Playwright 在 route 层 abort 所有写请求，**零写入到达平台**
> （无文章、无草稿、无图片上传）。
> 证据强度：47 个 webpack chunk 全量扫描 + 4 个真实 body 抓包 + 平台 bundle
> 的 builder 注册表枚举，三路一致。

## 0. 结论摘要

| 问题 | 答案 |
|---|---|
| 五个疑点字段（`coverList`/`slaveItems`/`subCoverList`/`position`/`infoSource`）在真实请求里存在吗 | **一个都不存在**。真身分别是 `pgc_feed_covers`+`draft_form_data.coverType`、服务端返回字段、`extra.is_multi_title`+`extra.sub_titles`、`extra.manual_selected_city`、`extra.info_source` |
| 嵌套值要不要 stringify | **要**。`extra` / `pgc_feed_covers` / `draft_form_data` / `search_creation_info` 都是 JSON 字符串 |
| body 是什么编码 | `application/x-www-form-urlencoded;charset=UTF-8`，扁平 `k=encodeURIComponent(v)` 用 `&` 拼接，**无 wrapper 字段、无 multipart、不是 JSON body** |
| 写路径有风控闸吗 | **有，且只针对写路径**。`_signature`/`msToken`/`a_bogus` + `x-secsdk-csrf-token`；`byted_acrawler` 的 `enablePathList` 只拦 publish 端点 |

## 1. 请求行与头

```
POST https://mp.toutiao.com/mp/agw/article/publish
      ?source=mp&type=article&aid=1231&mp_publish_ab_val=0
      &_signature=<redacted>&msToken=<redacted>&a_bogus=<redacted>

content-type: application/x-www-form-urlencoded;charset=UTF-8
x-secsdk-csrf-token: <redacted>
```

签名三件套由 `byted_acrawler.init({aid:1231, intercept:!0,
enablePathList:["/mp/agw/article/publish"]})` 产出；CSRF 由
`secsdk.csrf.setProtectedHost({host:{POST:/^(?!\/bcs\/notice\/boxes\/)/, GET:[…]}})`
保护。**读路径没有这两层**。

## 2. 真实 publish body（save=1）解码后 31 个字段

```
source=29
extra={"content_source":100000000402,"content_word_cnt":29,"is_multi_title":0,
       "sub_titles":[],"gd_ext":{"entrance":"","from_page":"publisher_mp",
       "enter_from":"PC","device_platform":"mp","is_message":0},
       "tuwen_wtt_trans_flag":"0","info_source":{"source_type":-1}}
content=<p data-track="1">…</p>
title=<标题>
search_creation_info={"searchTopOne":0,"abstract":"","clue_id":""}
title_id=1793037894563_1876864527563788
ic_uri_list=
appid_list=
stock_ids=
concern_list=
mp_editor_stat={}
is_refute_rumor=0
save=1
entrance=main
timer_status=0
timer_time=
educluecard=
draft_form_data={"coverType":1}
pgc_feed_covers=[]
article_ad_type=3
is_fans_article=0
govern_forward=0
praise=1
disable_praise=0
tree_plan_article=0
star_order_id=
star_order_name=
customer_nick_name=
activity_tag=0
trends_writing_tag=0
claim_exclusive=0
```

变体：
- `save=0`（草稿/预览）→ 27 个字段，少 4 个 id 列表；`extra` 用
  `tuwen_wtt_transfer_switch` 且**不含 `info_source`**（`source_info` builder
  只在 PUBLISH 触发）。
- 预览 → 多一个 `is_app_preview=1`。
- `save=1` + `entrance=main` = 发布；`save=0` + `entrance=""` = 草稿/预览。

`title_id` 的形状是 `<毫秒时间戳>_<accountId>`。

## 3. 第三种 body 形态：草稿自动保存用括号式序列化

本地草稿自动保存走**同一个** publish 端点，但 body 形态不同：

```
extra[content_word_cnt]=21
search_creation_info[abstract]=
media_id=…
abstract=…
```

bundle 模块 86826 有通用括号序列化器（数组→`key[0]`，对象→`key[a]`）。
→ **启用 `save_draft()` 必须单独确认，不能假定与 publish 同形态。**

## 4. 封面的真实结构（这个仓库抄对了）

只读 GET `/mp/agw/article/edit?pgc_id=7700961633502085632` 回读平台已发布文章：

```json
"pgc_feed_covers": [{
  "extra": {"from_content":"0","from_content_idx":"0","from_content_uri":""},
  "origin_uri": "tos-cn-i-6w9my0ksvp/c14914bc1070421a865fdde0503dd30d",
  "thumb_height": 675,
  "thumb_url": "tos-cn-i-6w9my0ksvp/c14914bc1070421a865fdde0503dd30d",
  "thumb_width": 1200,
  "url": "https://p3-sign.toutiaoimg.com/tos-cn-i-6w9my0ksvp/c14914bc…~tplv-tt-cover-v2.image?…"
}]
```

- 封面类型枚举在 `draft_form_data.coverType`：`NONE:1 / SINGLE:2 / MULTI:3`
  （**值是数字**，不是字符串 "SINGLE"）。
- `origin_uri` 与 `thumb_url` 是 `tos-cn-i-…/<hash>` 形态；`url` 才是签名 CDN 链接。
- 仓库的封面元素键集与这段**逐字一致**——那是 `build_publish_form()` 唯一
  抄对的部分。错的只是外层字段名与 `coverType` 的位置与类型。
- 平台 bundle 里的 `camels2snake` 实现在
  `common_vendors_e1e8394409d520df.js`。

## 5. content 是 ProseMirror 形态的 HTML

平台已发布文章的 content（`/mp/agw/article/edit` 只读拉取）：

```html
<h1 class="pgc-h-forward-slash" data-track="1">被裁半年降薪15%：四条原因里没有AI</h1>
<p data-track="2">…</p>
```

bundle 的 editor builder：`g = e.getHTML({mergeEmpty:!1}) || ""`，再 replace 掉
`typo` span 与 `&amp;`、去掉 `<img>`。

**浏览器路径**（`reference/toutiao-ops/cli/src/publish-article.js` 的
`pasteMarkdownAsRichText`）是 `marked.parse(md, {breaks:true, gfm:true})` 粘进
contenteditable，让平台自己 `getHTML()` 重新序列化 → 产出的才是平台原生形态。

**纯 HTTP 路径**只能手搓近似版（`publish.py:_markdown_to_html` 产出
`<p>/<h2>/<h3>/<blockquote>/<ul><li>/<strong>/<a href>`，没有 `data-track`、
没有那个 class、标题层级用 h2/h3）。

## 6. 仓库 `build_publish_form()` 与真实请求的逐条差异

**名字对得上的 9 个**：`source` `title` `content` `save` `entrance`
`timer_status` `timer_time` `praise` `disable_praise`

**仓库发了但真实没有的 26 个**：
`article_type` `pgc_id` `titleId`（真身 `title_id`）`textCount`
`externalLinkChecked` `externalLink` `claimOrigin` `copyRightChecked`
`addThirdTitle` `hasSubTitle` `subTitle` `subCoverList` `subCoverType`
`errPattern` `position` `coverList` `coverType` `articleAdType`（真身
`article_ad_type`）`isFansArticle`（真身 `is_fans_article`）`visibility_level`
`timingStatus`/`timingTime`（真身 `timer_status`/`timer_time`）`slaveItems`
`isMultiTitle`（真身 extra 内）`isStoryArticle` `infoSource`

**真实有但仓库缺的 22 个**：
`extra`（最重要）`search_creation_info` `title_id` `ic_uri_list`
`appid_list` `stock_ids` `concern_list` `mp_editor_stat` `is_refute_rumor`
`educluecard` `draft_form_data` `pgc_feed_covers` `article_ad_type`
`is_fans_article` `govern_forward` `tree_plan_article` `star_order_id`
`star_order_name` `customer_nick_name` `activity_tag` `trends_writing_tag`
`claim_exclusive`

**名字对但值相反**：`praise` 仓库 0 / 真实 1；`disable_praise` 仓库 1 / 真实 0；
`content_source=100000000402` 仓库放顶层 / 真实在 `extra` 内部。

## 7. 为什么会抄错

模块 12306 是**客户端表单状态默认值对象**，不是请求字段表。发布请求由若干
builder 拼成，每个只负责一段：

| 模块 | 职责 |
|---|---|
| index 模块 16294 | 请求封装（`E0` = urlencoded，跳过 undefined 值；`v_` = JSON body） |
| 模块 22702 | API 层 |
| publish 模块 25379 | payload 构建器注册表 |
| 模块 12306 | 表单状态默认值（**被误当成字段表的那个**） |
| 5967 模块 69156 | 右侧设置栏 builder 真正的出处 |
| 模块 86826 | 通用括号序列化器（草稿路径用） |

bundle 里的字段名是 camelCase，线上请求是 snake_case，转换由 `camels2snake`
完成。

## 8. 其它观察

- **定时发布走另一个端点**：`/mp/agw/article/timer_publish/post_now?gid=…&aid=1231`
  —— 仓库完全没实现。
- **写路径错误码**：2222 =「可信浏览器校验」、3001/3002 = 实名/验证弹窗、
  3022 = 账号未注册。仓库无处理分支。
- **本地产物污染**：发布页把表单写进 `localStorage['mpdraft']`（含完整正文），
  任何写操作实验都会在 profile 留痕（含未 publish 的草稿正文）。
- **写操作不可逆**，验证它需要一次真实的发布或至少一次可撤回的验证。

## 9. 置信度

| 结论 | 置信度 |
|---|---|
| 五个疑点字段在真实请求里不存在 | ≈95%（抓包 + builder 枚举 + 47 chunk 字段名扫描三路一致） |
| body 编码是 urlencoded 扁平 | 高 |
| 嵌套值统一 stringify | 高 |
| 封面元素键集 | 高（服务端只读回读逐字一致） |
| content 是 HTML/ProseMirror | 高 |
| `save=0` 草稿路径 body 形态 | **中**（三种形态并存未完全厘清） |
| 非空 `pgc_feed_covers` 在 publish 请求里的落法 | **中高**（形状由服务端回读 + bundle 确定；没实拍过「带封面」的 publish body——要实拍必须真上传图片或选正版图，两者都越界） |

## 10. 证据来源

- 发布页 `https://mp.toutiao.com/profile_v4/graphic/publish` 是 Garfish 壳，
  真实应用是 `graphic` 微应用
  `https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/graphic/index.ab4194ac9d.js`
  + 动态 chunk（`publish.02df493d3e.js`、`5967.777eecc8a9.js`）。
- 只读接口：`GET /mp/agw/article/list`、`GET /mp/agw/article/edit?pgc_id=…`、
  `GET /mp/agw/statistic/v2/item/info?item_id=…&type=1&app_id=1231`。
- 抓包 scratch 都在 `/tmp`（`/tmp/ttjs/*.js`、`/tmp/tt_capture.log` 等），
  不入库。
