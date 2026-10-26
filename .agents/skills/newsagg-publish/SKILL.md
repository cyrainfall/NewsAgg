---
name: newsagg-publish
description: |
  NewsAgg 发布层：把成稿发到今日头条，含熔断、串行锁、反查验证与 dry-run。
  当需要发布文章到头条、验证发布链路、或处理发布失败时使用。
  复用 vendored 的 toutiao-ops；本技能只加安全门与验证，不重写发布逻辑。
---

# newsagg-publish — 发布层

把 `outputs/` 里的成稿发到今日头条。设计依据 [ADR-0024](../../../docs/adr/0024-reuse-toutiao-ops-for-publishing.md)、[ADR-0011](../../../docs/adr/0011-bounded-review-loop-and-layered-recovery.md)、[ADR-0032](../../../docs/adr/0032-dry-run-creates-real-drafts-and-verification-limits.md)。

## 前置

成稿需满足（包装层会校验，不满足直接拒绝）：

1. 位于 `outputs/<slug>.md`，且**第一行是一级标题**（`# 标题`）
2. 标题长度 **2~30 字**（头条硬限制）
3. 封面图存在（头条必填；由 `make_cover.py` 模板拼图生成，ADR-0013）

## 封面：承载一个视觉论据，不复述标题（ADR-0044）

信息流里标题就印在封面旁边，**复述标题的封面不增加信息**，还会在 320px 缩略图下
把字号压到读不出来。旧版实测：1200x675 上亮像素仅 4.7%，缩到 320px 宽时正文约 8px 高。

封面只放**一个**元素，且必须能在 320px 宽下读出：

```bash
# 自动推导（从标题里找数字/对比/冒号切分）——能保证不出错，但选不出论据
.uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py --slug <slug>

# 显式指定视觉论据（推荐：由写作阶段判断"这篇文章的视觉论据是什么"）
.uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py \
  --slug <slug> --spec .newsagg/covers/<日期>.json
```

**没写 spec 就走自动推导，而自动推导只会切分标题、回答不了「这篇文章最值得被记住的
事实是什么」**。2026-10-20 那次漏写 spec，封面渲染成顶部 `Codex搬走Cursor配置`、
正文 `Codex搬走Cursor配置：/ 选AI编程工具的标准变了`——顶部小字与正文逐字重复，
且 `problems: []` 全绿，无人察觉（ADR-0044 发现五）。所以发布前要确认
`.newsagg/covers/<日期>.json` 存在；编排器在没有 spec 时会向 stderr 打一行提醒，
但那不是阻断。

三种布局：

| `layout` | 用途 | 字段 |
| --- | --- | --- |
| `number` | 有强数字（金额/比例/参数） | `kicker` `hero` `hero_unit` `payoff` |
| `contrast` | 两个短句相撞（A 成立 / B 不成立） | `kicker` `line1` `line2` |
| `statement` | 无数字、无论点对撞，只有一句判断 | `kicker` `statement` |

**数字要拆成 `hero` + `hero_unit`**：`hero` 放大到占满画面（`8160`），`hero_unit` 缩到
三分之一（`亿参数`）。把两者塞进同一个字符串会让主字号被压小。

## 封面样式：每天随机一种，有限目录（ADR-0067）

样式是**材质**——底色、墨色、强调色、显示字体的组合（取材 reference/hallmark 的
主题观），**不是排版**。三种布局与样式正交：同一论据在任何样式下都走同一套测量、
同一组自检，样式不允许引入新的豁免。

**每天用哪种由日期哈希从 8 种目录里确定性地选出**：同一天重跑结果一致（无状态
文件，崩溃/重跑都安全），相邻两天自动错开。目录：`ink`（原版深夜蓝黑）/`paper`
（报纸白纸宋体）/`slate`（石板薄荷）/`cream`（米纸朱砂）/`cobalt`（钴蓝鎏金）/
`forest`（墨绿暖橙）/`mono`（黑白等宽）/`blush`（暖灰珊瑚）。

- 不指定 → 每天自动轮换（推荐，无人值守默认）
- spec 里写 `"style": "paper"` 或 `--style paper` → 显式钉死一种
- **未知名字硬错退出 1**，绝不静默回退——静默回退会让「我明明指定了样式」
  变成一句无人验证的假设

信息 dict 新增 `style`（当天实际用的样式名）与 `style_pinned`（是否显式钉死），
简报层可据此呈现「今日封面样式」。

## 可选照片层：只有你能提出查询时才取图（ADR-0048）

封面可以在上半幅放一张真实照片（下半幅仍是实心黑块承载文字，中间一条蓝分隔线）。
**照片只在 spec 里写了 `photo_query` 时才取**：

```json
{
  "layout": "contrast", "kicker": "AI 编程工具",
  "line1": "配置能带走了", "line2": "迁移没到零",
  "photo_query": "data center servers"
}
```

`run_all.sh` 读到这个字段才调 `fetch_photo.py` 去 Wikimedia Commons 取图；
没有这个字段就**不取**——这不是省事，是责任划分：**只有写作阶段知道这篇文章讲的是什么，
也只有它能判断「机房照片配一篇讲 Codex 迁移的文章」算不算图文无关。**
没有 query 就说明没人做过这个判断，此时加一张来路不明的照片，
只是把「图文无关」变成系统自动决定的（ADR-0048 决定 4）。

**照片只接受 CC0 / 公有领域，不接 CC BY。** 理由不是 CC BY 不能用，而是
**无人值守没有「记得署名」这个环节，忘一次就是违约一次**。许可证与来源全部写进
`.newsagg/photos/ledger.jsonl`（`license` / `license_url` / `artist` / `descriptionurl` /
`sha256`）——「我们当时以为它是 CC0」不是抗辩理由。

**取不到图是设计行为，不是错误。** `fetch_photo.py` 取不到也返回退出码 0，
编排器回退到纯模板封面并向 stderr 打一行 `注意：未能取到合格照片（查询：…）`。
实测 10 个真实话题里 `DeepSeek` 与 `GPU server rack` 合格 CC0 候选为 0——
最相关的话题往往恰好没有免费素材，这是 Commons 的供给结构。**多数日子会回退，这是正常的。**

单独试取（不进流水线）：

```bash
.uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/fetch_photo.py --query "data center servers"
```

## 封面自检：量的是画布上的像素，不是设计常量（ADR-0044 发现六、七）

`make_cover.py` 在每个**实际绘制点**记录元素字号（`drawn`），然后按
**320px 缩略图**（平台下发 640x360 是 2x 资源，保守按 1x 算）换算实测像素：

- `READABLE_PX = 11.0`——汉字在缩略图下小于这个高度就读不出来（正文最小可读约 11-12px）。
- `MIN_READABLE = 42` 画布像素（`math.ceil(11.0 / (320/1200))`）。**必须向上取整**：
  `int(11.0/0.2667)=41`，而 `41 × 0.2667 = 10.9px`——一个自己都通不过自己检查的下限。
- `MIN_MUTED = 42`（原先是 30，换算到缩略图只有 8px）。

任何元素实测低于 `READABLE_PX` 即进 `problems`，退出码 1，编排器不发布。
另有一条 `hero_ratio`（主元素占画布高度）低于 25% 的 `warnings`——画面太空。

**输出里要读三个字段**：`drawn`（每个元素在缩略图下的实际高度）、`restates_title`、
`warnings`。`restates_title` 为真表示「封面说的和标题是同一句话」——它是
**警告不是错误**（`derive_parts()` 原理上只能切分标题、造不出论据，判 fatal 会让
缺 spec 那天直接发不出去），但必须处理：去写 `.newsagg/covers/<日期>.json`。

`run_all.sh` 把整份自检结果落到 `.newsagg/covers/<日期>.report.json`，
`brief.py` ⑤ 段会渲染成「封面自检」条目（`- ✗` 问题 / `- !` 警告）。
**无人值守时没有第二双眼睛，所以警告必须走这条路进简报**——只打 stderr 等于没打。

**推导来的 kicker 过长（>16 字）会被整条省略，而不是截断。** 截断只会留下残句
（实测 `DeepSeek API 每 1000 次请求 5 元` → `DeepSeek API 每`），残句既不是标签、
又在复述标题。显式传进来的 kicker 才按分隔符截断。

**溢出会被挡住**：`make_cover.py` 用像素检查内容是否侵入左右边距带，溢出时
`problems` 非空且退出码 1，编排器据此不发布。一张溢出画布的封面比没有封面更糟——
它会被发出去，而没人看见。注意这只挡「放不下」，挡不住「放得下但读不出」——
后者由上面的缩略图可读性检查挡。

**封面上不印任何脚注。** 作品声明走平台的字段（`publish.py --declaration`），
不在图上的像素里重复。在图上再印一行脚注要占掉 7.5% 的画布高度——封面上每一寸
都该留给论据。`--note` 默认空，需要时才传。

## 3:2 安全区：信息流会裁掉左右各 93px（ADR-0049）

**这是比「溢出」更隐蔽的一类失败：画在画布内，但平台不给看。**

用 Playwright 在桌面 `https://www.toutiao.com/` 信息流实测：正文卡片的图片槽
恒为 **156×104（宽高比 1.495）**、`object-fit: cover`，而图片的自然尺寸五花八门
（1035×582 / 1280×720 / 938×619 / 640×360 …）——平台不做统一，靠 CSS 裁。

我们出 1200×675（16:9），在 3:2 的槽里被**居中裁切**：
安全区宽 = 675 × 1.5 = **1012px**，左右各丢 **93px**（7.8%）。

实测 5 张历史封面**全部有内容落进裁切区**（0.17% – 0.48% 的墨点），
2026-10-14 那张最直观：「做应用的人省钱」的「钱」字被切掉一半。

因此排版不再用画布宽度：

- `SAFE_INSET = 93`（`max(0, int((W - H × 1.5) / 2))`）
- `text_bounds(W)` 返回 `(left, max_w)`，`left = SAFE_INSET + margin`，
  **`max_w` 从 1044 收到 858（-18%）**。代价是字要变小，换来「看到的字一个都不缺」。
- 所有居中（hero / payoff / 两行正文 / 橙色短线）改以**安全区中线**
  `margin + max_w / 2` 居中，不是画布中线 `W/2`。

**自检四段，全在量像素或量实际字号**：

| 段 | 查什么 | 判 fatal？ |
| --- | --- | --- |
| 一 `edge_check` | 有没有画到画布之外 | 是 |
| 二 缩略图可读性（320px） | `drawn` 的实际字号 × 320/1200 < 11px | 是 |
| 三 `safe_zone_check` | 落在 3:2 裁切区里的墨点占比 > 0.1% | 是 |
| 四 最差卡片尺寸（156px） | 主元素字号 × 156/1200 < 11px | **否，只警告** |

第四段只警告的理由：能不能达标主要由**内容**决定——858px 安全区里一行 10 个
汉字最多排到 ~85px，想更大必须把句子改短，那是写作阶段的选择，排版修不了。
判 fatal 会让「句子稍长」直接发不出去，代价大于收益。

**`safe_zone_check` 的分母是亮像素（墨），不是总像素。** 用总像素作分母时
1200×675 的 0.2% 阈值等于 1620 个像素，5 张旧封面全降到 0.1% 以下、检查一声
不响——这就是 ADR-0044 发现六的同一个错误：**分母选错，检查就会变成装饰**。
换用亮像素后新排版 0.00%、旧产物 0.18% 起，0.1% 阈值能干净分开两者。

## 封面改变不了「谁会看到」（ADR-0049）

以上四条规矩都只能影响**「看到之后点不点」**。实测数据说明瓶颈不在这里：

- 6 条作品合计**展现 1782、阅读 2，整体点击率 0.11%**；渠道分布
  **推荐频道 92.63%** → 系统确实在推，只是推了没人点。
- 曝光本身在爬升（最新一篇 0.1 天就有 51 次展现，折 464/天）。
- 头条站内搜索实测：`Codex` 与 `MCP` **零相关结果**；`AI 赚钱` / `程序员 失业` /
  `大模型 降价` 才有真实互动（最高 22 评论）。
- 头条主榜跨 10-17 至 10-20 进池 18 条，属于原赛道（「AI 与开发者工具」）的 **0 条**。

**所以：不要因为阅读量低就去反复改封面。** 封面对了也只能把个位数阅读变成
两位数。瓶颈是「谁会看到」——**这个判断已经做出**：赛道于 2026-10-21 改为
「AI 变现与开发者职业」（[ADR-0050](../../../docs/adr/0050-track-pivots-to-ai-monetization-keeping-technical-depth.md)），
选题落点从技术主题改为钱与职业处境。

**但这不改变封面这一层该做的事**：改赛道影响的是「推荐流会不会推给对的人」，
封面影响的是「推到了之后点不点」。ADR-0050 明确不承诺阅读量会立刻上升
（账号第 7 天、0 粉丝、全部文章 `isOriginal: false`），所以**在新方向跑够数据之前，
仍然不要靠反复改封面去追阅读量**。

## 执行

```bash
# dry-run（默认）：填表 → 存草稿，不公开
python3 .agents/skills/newsagg-publish/scripts/publish.py \
  --slug <成稿slug> --cover <封面路径>

# 真发
python3 .agents/skills/newsagg-publish/scripts/publish.py \
  --slug <成稿slug> --cover <封面路径> --mode publish
```

| 参数 | 说明 |
| --- | --- |
| `--mode` | `dry-run`（默认，存草稿）/ `publish`（真发） |
| `--backend` | `api`（默认）/ `browser`。见下节「两种后端」 |
| `--verify-backend` | `auto`（默认，跟随写入后端）/ `api` / `browser` |
| `--allow-unverified-write` | 允许 api 后端做写操作。**默认关**，只在人工强确认时开 |
| `--declaration` | 作品声明，默认 `个人观点`（ADR-0046；**含已知合规风险**，见该 ADR） |
| `--min-free-mb` | 发布前可用内存下限，默认 800（ADR-0023） |
| `--lock-timeout` | 等待浏览器锁的秒数，默认 300 |
| `--account` | 头条账号名，默认 `default` |
| `--card-file` | 本篇承接的观点卡文件名（ADR-0037）。由 `run_all.sh` 从决策文件传入 |

> **`--card-file` 为什么要记进账本**：循环 C 读回表现数据后要回答"**哪些卡**跑出了数据"，
> 而账本是唯一的关联点。文章标题会被本人改、决策文件属运行时状态（`.newsagg/` 不入库，
> ADR-0027 可能被清理或跨环境不同步），只有发布那一刻记下的 `card_file` 是可靠归因。
> 归因读取顺序是**账本优先、决策文件兜底**。

## 两种后端（api / browser）

发布链路拆成三段，两段已经不需要浏览器：

| 环节 | api 后端 | browser 后端 |
| --- | --- | --- |
| 登录预检 | `GET /mp/agw/media/user_login_status_api`，**0.69s，浏览器关着也能跑** | `node index.js auth check`，要起 Playwright/Chromium |
| 反查 | `GET /mp/agw/article/list`，**0.93s**，还能看见草稿 | `node index.js content list`，**25.8s**，看不见草稿 |
| 写（发布/存草稿/上传封面） | 端点与字段名已核对，**但序列化形态未线上验证** | Playwright DOM 自动化，**已验证** |

**默认 `--backend api`** 的真实含义是：登录预检与反查走纯 HTTP，
写这一步**自动回落** `browser`。回落会在账本里留下
`write_backend` / `write_fallback_reason`，事后可查这一篇到底是哪条路发出去的。
之所以选「回落」而不是「直接报错」：`run_all.sh` 调用本脚本时不传 `--backend`，
默认值必须保持它原来能跑。

实测数字（2026-10-25，同一账号、同一次反查）：
- 登录预检：api 0.69s vs browser（起浏览器 + node）数十秒
- 反查：api 0.93s vs node 25.81s（约 28 倍）
- 一致性：node 的 10 条作品与 api 的 15 条（10 已发布 + 5 草稿）逐条比对，
  `gid` 与 `title` **10/10 完全相同**

### 真实发布前的确认要求

1. **默认就是安全的。** 不传 `--mode publish` 只存草稿；不传 `--allow-unverified-write`
   写操作一定走 browser 后端（已验证那条路）。
2. **想用 api 真发，必须先自己确认三件事**（下一节逐条列了缺什么），
   然后显式加 `--allow-unverified-write`。这个 flag 是"我知道我在赌"，
   不是"跳过检查"。
3. **发布不可逆，且平台禁止重复内容（ADR-0047）。** 一旦 api 写路径真发成功过一次，
   要立刻把结果记进 ADR，并把该路径从 beta 提升为已验证 —— 而不是默默继续用。

### api 后端尚未验证的部分（写路径为什么默认被挡）

端点 URL、query 参数、字段名都是从发布页真实 bundle 里逐个核对的
（`publish.b8c90341ac.js` 模块 12306 的表单默认值、`timing` builder 的
`save` 映射、`common` builder 的 `source:29` / `extra` JSON 字符串）。
共 35 个字段，`build_publish_form()` 按真实名字拼。**但有三处拿不到线上证据：**

1. **`coverList` 的嵌套序列化。** JS 里它是数组对象，提交时必有某层做了
   stringify，那一层在压缩产物里看不出来。这里选择写成 JSON 字符串
   （头条系接口的常见形态），但没有线上证据。
2. **`slaveItems` 同上。**
3. **正文 `content` 的 HTML 形态。** browser 后端是把 markdown **粘贴进编辑器**，
   由编辑器自己转 HTML、自己加内联样式；api 后端没有编辑器，只能自己产 HTML。
   现在只做标题/段落/引用/列表/粗体/链接的最保守转换，
   **排版不可能与 browser 后端一致**。

另外两件已实测但要记住的事：

- **发布端点拦不拦无签名请求，结论是「不确定」。** 发布页注册了
  `window.byted_acrawler.init({aid:1231, intercept:!0, enablePathList:["/mp/agw/article/publish"]})`，
  而 `acrawler.js` 是 vm 混淆的字节码，静态读不出签名算法。
  实测**空 body** 请求返回的是业务层 `code 7050 保存失败`（不是 `2222 可信浏览器校验`），
  说明空请求穿过了签名闸 —— 但空请求能过不等于带真实内容能过（反爬常按内容分层）。
  这一步只能靠一次真实写入确认，而本次任务禁止真发，所以停在这里。
- **`verify_kinds.weitoutiao` 在 api 后端恒为 0，且不可信。**
  `/mp/agw/article/list` 只含图文（genre=Pgc），微头条不在这个数据源里。
  账本会写 `verify_weitoutiao_blind` 说明这次没查微头条。
  **ADR-0047 的泄漏检测必须用 `--backend browser`**（它拦 `/pgc/ma/content/...`，含微头条）。

### api 后端可以放心用的命令

```bash
# 登录状态（浏览器关闭即可）
python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py login

# 作品列表（含草稿，比 node 多 5 个字段：is_draft/is_passed/is_unpass/status_desc/info_source）
python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py list --limit 20

# 草稿列表 / 草稿数
python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py drafts
python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py draft-count
```

这四个都是**只读**，已实测可用（2026-10-25）。

### 服务器上没有 Chrome 时：用注入的 cookie（ADR-0071）

服务器上 `from_browser()` 直接失败（`CookieError: 找不到浏览器 chrome 的 profile`），
而 toutiao-ops 的 Playwright profile **解不开**（它的 `Local State` 里 `os_crypt`
是空 dict）——browserless 与 toutiao-ops 是**两套独立的登录态**。
→ 服务器上必须**注入**：

```bash
# 本机导出（写出即 0600，不打印任何 cookie 值）
python3 scripts/export-cookies.py --browser chrome --out .newsagg/toutiao-cookies.json

# 传到服务器并收紧权限（这是账号凭证，用 scp，不要进 git）
scp .newsagg/toutiao-cookies.json root@<ip>:/root/.newsagg/toutiao-cookies.json
ssh root@<ip> 'chmod 600 /root/.newsagg/toutiao-cookies.json'

# 服务器上使用
python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py \
  --cookie-file /root/.newsagg/toutiao-cookies.json login
```

`Session.from_cookie_file` 会**拒绝**组/其他可读的文件（`mode & 0o077 != 0`），
并说清 `chmod 600` 怎么改。这不是泛泛的建议——它防的是一次 `chmod 644` 手滑。

cookie 主体约 23 天有效（实测 2026-10-27 导出，主体到 2026-11-19）→
**每月要重新导出一次**。今天没有任何东西会提醒这件事（见 DESIGN 残余风险 69）。

## 安全门（按执行顺序）

| 门 | 内容 | 不过时 |
| --- | --- | --- |
| 0 | **熔断状态**（`.newsagg/breaker.json`） | 拒绝，提示如何解除 |
| 0.5 | **每日上限 1 篇**（`.newsagg/publish-ledger.jsonl`，ADR-0014） | 拒绝，除非 `--allow-daily-override` |
| 1 | 成稿存在 + 标题 2~30 字 + 封面存在 | 拒绝 |
| 2 | **内存预检**（ADR-0023） | 熔断 + 告警 |
| 3 | **登录预检**（只读，可重试 3 次） | 登录失效 → 熔断 |
| 4 | 发布 / 存草稿 | — |
| 5 | **反查验证**（仅真发，ADR-0024） | 见下 |

门 3 与门 5 的实际实现取决于 `--backend`：`api` 走纯 HTTP（不需要浏览器），
`browser` 走 vendored toutiao-ops。**门序、判据函数、`verdict` 取值一个字符都没改** ——
api 后端只是把列表数据翻译成 node 的形状（`toutiao_api.as_verify_payload()`），
再交给同一个 `count_title_kinds()` / `find_gid()` 去判。

## 硬规则

1. **绝不信任发布返回值。** `toutiao-ops` 的发布是乐观返回（点完按钮即 `success: true`）。判定"发出去了没有"只认 `content list` 的反查结果。

1b. **反查必须按「平台实际标题」，不是成稿标题**（ADR-0051）。`publish-article.js`
   用 `page.keyboard.type()` 逐字敲标题，平台输入框会**吞字**且不抛异常；
   2026-10-21 实测「中间层**岗**位」被收成「中间层位」，于是按成稿标题反查查不到、
   一次真实发布被判成 `not_published_needs_review`，`verify_kinds` 也数成 0/0。
   现在 CLI 会读回输入框、不一致就重试、仍不一致回落 `fill`，并返回
   `title`（平台实际值）+ `expected_title`（原值）；`publish.py` 落库
   `title_on_platform` 并用它反查与计数。**若两者不同，简报 ① 段会显式提醒
   ——到后台按实际标题查，不要按成稿标题搜。**

2. **两类重试严格区分**（ADR-0011）：
   - **只读探测可重试**（登录检查、信用分、表现数据）——重试无副作用。
   - **发布绝不自动重试**——重发会造成重复发文，而平台明令禁止"批量发布重复内容"。

3. **发布结果未知时不猜。** 若点击发布后失败或超时，先回后台列表核实该标题是否已存在；**核实不清则熔断等人**，不重发。

4. **并发冲突不算失败。** 同一账号的浏览器命令不能并发（ADR-0030）。若失败信息含"Target page, context or browser has been closed"或"正在现有的浏览器会话中打开"，那是**瞬时竞态**，不是平台拒绝——但本包装层已用串行锁避免，故出现即说明锁失效，应报告而非静默重试。

5. **dry-run 有副作用**（ADR-0032）：它走 `--draft`，会在真实账号上**创建真实草稿**。它不是无痕验证，每次都会留痕，需人工到 `mp.toutiao.com` 草稿箱清理。**草稿无法被 `content list` 反查**（`--status` 只有 published/reviewing/rejected），因此 dry-run 只靠退出码判定。

6. **反查的四种结果**（`verdict` 取值）：

| 反查结果 | `verdict` | 判定 |
| --- | --- | --- |
| 查到 `published` | `title_found:published` | 成功 |
| 查到 `reviewing` | `title_found:reviewing` | **视为已成功提交**（审核中，不是失败） |
| 查到 `rejected` | `rejected` | **熔断 + 告警**——内容违规信号，直接关系信用分（ADR-0013） |
| 查不到 | `not_published_needs_review` | 视为未发出，**不重发**，标为待处理 |
| 输出解析失败 | `unknown_needs_review` | **不可判为"未找到"**，交人工核实（静默误判会诱使重发） |

**`run_toutiao` 必须把 stdout 重定向到文件，不能用管道捕获**（ADR-0045）。toutiao-ops 的 runner 在 `console.log` 之后**立刻** `process.exit(0)`，Node 对管道的写是异步的，未 flush 的部分会被丢掉：`content list --limit 20` 的 103KB 输出走管道只回收到 64KB，且断在一个 JSON 字符串中间。写文件描述符是同步的，不受影响。2026-10-18 就因为这一点，把一次**成功发布**报成了 `title_not_found`。

## 绝不附带发微头条（ADR-0047）

`--no-weitoutiao` 每次都必须传。它看起来是个多余的防御，其实是必需的：
**头条发布页的「发布得更多收益」（= 同时发布微头条）默认是未勾选的**，
而 `toutiao-ops` 的 `uncheckWeitoutiao()` 曾经无条件点它一下 ——
那一次点击不是取消，而是**勾上**，于是文章和微头条各发一条。
10-14 与 10-18 各发生过一次，两次都被反查判成 `title_found`、无人知晓。

上游已修（先读 class 里的 `byte-checkbox-checked`，只在确实已勾选时才点，
点后再验一次）。**但这条规则仍然必须保留**：发布页是平台页面，
它的默认值随时会变，而我们唯一可靠的姿态是「每次都显式要求不要」。

反查会数同一标题下有几条文章、几条微头条，写进账本 `verify_kinds`；
出现微头条时置 `weitoutiao_leak`，简报 ⑤ 段会列出来等你处置
（本 CLI **没有删除能力**，发出去就删不掉）。

处置有两个出口，都在账本 `.newsagg/publish-ledger.jsonl` 的那条记录上写（ADR-0055）：

- `"leak_resolved_at": "<日期>"` —— 你**删掉了**那条微头条
- `"leak_accepted_at": "<日期>"` —— 你**决定保留**它（这是同样合法、且更常见的终态）

**不要用 `leak_resolved_at` 去表达「决定保留」。** 两个字段都停提醒，但含义相反：
账本是复盘时唯一的事实来源，把「保留」记成「已删除」会让半年后的回看无法分辨
那两条微头条到底还在不在平台上。写了 `leak_accepted_at` 的只在做决定当天确认一次，
之后不再出现——提醒的价值是「提醒你做决定」，不是「替你记住决定」。

## 作品声明：当前是「个人观点」，这是有意的偏离（ADR-0046）

`--declaration` 默认 **`个人观点`**（平台显示为「个人观点，仅供参考」），
**不再标 `引用AI`**。

⚠️ **这是用户明确要求的偏离，并且带有已知合规风险**：《人工智能生成合成内容标识
办法》自 2025-09-01 施行，第十条禁止「恶意删除、篡改、伪造、隐匿」标识，用户侧
义务是「发布 AI 生成合成内容时应当主动声明」。本系统正文由 AI 生成。

该决定已记录在 ADR-0046，并登记为 DESIGN.md 的残余风险。**不要在无人知晓的情况下
把它改回 `引用AI`，也不要把它当成"已经合规"**——它是被知悉并接受的风险，不是被
解决的问题。

## 熔断

状态存 `.newsagg/breaker.json`（仓内，便于人工解除；guard 可同步读，ADR-0025）。

**解除方式**：人工修复后删除该文件即可（ADR-0011 的"人工介入"）。

熔断触发条件：登录失效、内存不足、内容被拒（`rejected`）。

## 输出

- `.newsagg/publish-ledger.jsonl`：每次发布的台账（追加）
- stdout：结构化结果（`verdict` 字段是判定结论）

## 与其他阶段的接口

- **上游**：写作阶段产出的 `outputs/<slug>.md` + 封面图。
- **下游**：成功发布后触发**认知写回**（向对应观点卡追加修订，带未复核标记，ADR-0026）与**教学副本**交接。
- 反查所需的 `content list` 输出形状见 ADR-0032：作品在 `items[*].data.msg_list[*]`。
