---
name: xhs-pipeline
description: 小红书图文一条龙：调度 content-strategy-engine / xhs-tech-writer 写笔记、xhs-card-skill 生成配图、xiaohongshu-skills 发布。当用户要端到端创作/发布小红书技术分享图文（技术点、踩坑、优化、复盘、方案对比）时调用。
---

# 小红书发布流水线（XHS Pipeline）

把三个技能编排成一条带人工确认门的流水线：**写 → 配图 → 发布**。你是流水线的调度者和质量负责人，不要自己重写任何一个技能的内部逻辑。

## 依赖技能（按顺序调用，名称必须精确）

| 阶段   | 技能                                       | 调用名                                                                                                   |
| ------ | ------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| ① 写作 | 内容策略引擎（总控/路由）                  | `content-strategy-engine`                                                                                |
| ① 写作 | 技术笔记写作（真正给规则）                 | `xhs-tech-writer`                                                                                        |
| ① 写作 | 按需：选题 / 标题诊断 / 合规 / 去痕 / 评分 | `xhs-topic-planner` · `xhs-title-analyzer` · `xhs-compliance-check` · `xhs-humanizer` · `content-scorer` |
| ② 配图 | 小红书社交卡片                             | `xhs-card-skill`                                                                                         |
| ③ 发布 | 小红书自动化                               | `xiaohongshu-skills`                                                                                     |

### 技能在哪 / 找不到怎么办（2026-09-28 校正）

> **真实位置**：技能内容在**仓库** `<repo>/.agents/skills/<name>/`；`~/.agents/skills/<name>` 是指向它的**符号链接**，而 `use_skill` 只认 `~/.agents/skills/` 下的入口。
> 文档旧版本写的 `.trae/skills/` **没有** xhs 系列（那里只有设计类技能），别再去那里找；旧版引用的 `xhs-content-skills-LICENSE.txt` 也已不存在，不用再找。

**Stage 0 先跑一次定位（10 秒）：**

```bash
ls -1 ~/.agents/skills | grep -E '^xhs-|^content-|^xiaohongshu'   # 入口清单
readlink ~/.agents/skills/<name>                                  # 确认真实目录
```

**`use_skill <name>` 报 `Skill <name> not found` 时**（本次实测 `content-strategy-engine`、`content-scorer` 就缺入口，其余 11 个正常），两条兜底任选：

1. 直接读 `<repo>/.agents/skills/<name>/SKILL.md`——内容与技能一致，规则照用（推荐，零副作用）；
2. 补入口（与其余技能保持一致）：`ln -s <repo>/.agents/skills/<name> ~/.agents/skills/<name>`。

**不要**为此去装插件、找 `xiaohongshu-mcp` / MCP 实现或改用别的项目。

> **`content-strategy-engine` 只是总控索引**（只返回 8 个模块清单，本身不含任何写作规则）。真正给规则的是 `xhs-tech-writer`，必须单独调用；需要标题诊断时再补 `xhs-title-analyzer`。
>
> **两者冲突时以 `xhs-tech-writer` 为准**：索引里的"每篇有'我'""CTA 用二选一提问"是种草号规则，技术笔记按 tech-writer 的**娓娓道来 + 全文无第一人称**执行。

## 铁律

1. **四个人工确认门，缺一不可**：文案定稿后、**配图选型（进入渲染前）**、配图渲染后、点击发布前，分别用 `AskUserQuestion` 拿到明确确认才能进入下一阶段。
2. **配图选型必须先列全清单再问**：调用 `xhs-card-skill` 渲染前，必须把该技能支持的**全部选型**（2 套视觉体系、10 套主题/强调色、交付形态）完整列给用户，由用户选定后才能复制模板、写 HTML、跑渲染。用户没明确说"你决定"之前，不得替用户选风格。
3. **发布操作必须经用户本人确认**（xiaohongshu-skills 硬约束）。用户在预览环节取消时，必须执行 `save-draft`，禁止直接关页面。
4. **卡片内禁止 emoji**；emoji 只允许出现在帖子标题/正文里，不进配图。
5. 每阶段产物落盘，路径用绝对路径，不依赖对话上下文传递。
6. **开工先定位技能**（见上一节）。`use_skill` 找不到时走兜底；不要把"找不到技能"当成"技能不存在"而自己重写规则。
7. **背景前置**：标题、首图、正文首段要让"完全不了解这个工具/概念的人"看懂它是什么、能得到什么。内部机制型表述（如"11 步流程"）必须补背景，或改成"需求 + 结果 + 数字"。**这条在文案定稿前过**——过了确认门再改标题会连带改封面与内页，白跑一轮渲染。

---

## Stage 0 · 收集输入

用一次 `AskUserQuestion`（最多 4 问）收集，能从上下文推断的不问：

1. 主题/素材（或直接给草稿/链接/经历）
2. 视觉风格倾向（可选先问，**不构成最终选型**；正式选型在 Stage 2.0 列全清单后确认。缺省推荐：Swiss International + safety-orange）
3. 页数与比例（缺省：5 页 3:4，封面 + 内页 + 总结）
4. **目标受众与语气**：受众人群 + 语气偏好（客观 / 娓娓道来 / 风趣）+ 是否回避第一人称（"我""你们"）。**技术笔记默认：娓娓道来 + 回避第一人称**，用户未明确指定时按此执行。

> **语气默认值已固定**：技术笔记默认娓娓道来、全文无第一人称（"我/我们"）。只有用户明确要求其他语气时才切换，避免"去掉主观称谓""改成娓娓道来"反复返工。
>
> **受众背景**：若素材是"某工具/某机制的内部原理"，第 4 问的回答要顺带确认一句——"读者是已经知道这工具的人，还是完全没听过的人？" 后者按铁律 7 处理（首段先交代它是干什么的）。

确定一个全流程复用的 slug：小写英文+连字符，如 `docker-cicd-pitfalls`。

目录约定：

- 配图任务：`<repo>/.agents/skills/xhs-card-skill/local-tests/<slug>/`（该技能硬性要求产物落在自己目录下的 `local-tests/<slug>/`，禁止建在别处），内含 `index.html`、`render.cjs`、`assets/`、`output/`
- 发布文案：`/tmp/xhs-<slug>-post/{title.txt,content.txt,pinned-comment.txt}`
- 提示：`.agents/` 已被仓库 gitignore，卡片产物不会污染 `git status`（别为此以为是没写盘）

## Stage 1 · 写作

先调 `content-strategy-engine` 拿模块索引（找不到就按上文兜底直读文件），再调 `xhs-tech-writer` 拿规则，然后按 writer 的规则产出：**标题、正文、封面文案建议、置顶评论、话题标签**。

要求（来自 xhs-tech-writer，逐条核对）：

- **标题计数口径以脚本为准：每个常规字符（含 ASCII）计 1，emoji 计 2**（脚本里的 `max(1, …)` 就是这个含义；旧写法"ASCII 每 2 个计 1"与脚本不符，容易误判）。用脚本核算，禁止凭感觉：

  ```bash
  python3 -c 'import math;t=open("/tmp/xhs-<slug>-post/title.txt").read().strip();print(sum(max(1,math.ceil(len(c.encode("utf-16-be"))/2)) for c in t))'
  ```

  **英文品牌名很贵**：`HTTP Shortcuts` 一个词就吃掉 14 个字符，只剩 6 个字符给钩子。标题装不下"品牌 + 机制 + 结果"时，按铁律 7 优先保"需求 + 结果 + 数字"，把品牌放到封面、正文首段与话题标签里（本次最终标题：`安卓分享 0.5 秒进自建笔记库`）。

- 标题前 10 字含核心搜索词；emoji 可选（技术笔记信息优先，放则放开头、每篇不重复）；标题里的数字/结论正文必须兑现。
- 标题定稿前**逐词扫废词黑名单**：`浅谈` `初探` `漫谈` `随想` `那些事` `一篇就够了` `深度好文` `万字长文` `干货` `宝藏` `炸裂` `封神` `YYDS` `必看` `揭秘`。这些词零搜索量或零信息增量，只占字数。
- 正文按 8 段结构走完完整路径（问题 → 方案步骤 → 结果数据 → 踩坑边界 → 原理 → 清单），必须有环境/版本、可复现步骤、改前改后数据，并写清适用与不适用场景；全篇至少 1 个一级或 2 个二级信息增量。
- 正文 ≤1000 字（平台硬限制）；代码只留最短可运行片段（3-10 行），长代码/日志转配图卡或截图。
- **正文字数必须按平台口径量**（本次踩坑：按"去空白"量到 939 照样被平台拒绝，平台报 `1003/1000`）。平台把**末行标签**转成话题芯片后不计入，**其余字符（含换行与空行）全算**——即"去掉末行标签后的全部字符数"：

  ```bash
  python3 -c 'import pathlib;t=pathlib.Path("/tmp/xhs-<slug>-post/content.txt").read_text(encoding="utf-8").rstrip("\n").split("\n");print(len("\n".join(t[:-1])))'
  ```

  目标 **≤985**，留 15 字余量。超额时按"删冗余段 > 压语义重复的句子"的顺序减，不要删事实（改前改后数据、版本号、报错原文优先保留）。

- **语气落地（默认娓娓道来 + 无第一人称）**：全文不得出现"我""我们""你们""大家"等主观称谓，用"这次排查""关键改动""实测下来"承载叙事；结尾不要"评论区聊聊"式 CTA，改用收束句或一个真问题。用户明确指定其他语气时才切换。
- 正文段落间空一行；标签放最后一行，形如 `#标签1 #标签2`，含主搜索词；不晒收入、不放二维码/微信号、不用"扣1/免费送"。
- 把定稿写入 `/tmp/xhs-<slug>-post/` 的三个文件。

**确认门 1**：向用户展示完整标题+正文（可折叠呈现长文），确认或修改后才进入配图。**被要求加背景/改标题时**，先判断是否连带影响配图（标题或首图语义变了 → Stage 2 的两页要一起改，并重渲染重校验），一次性改完再回到本门。

## Stage 2 · 配图（xhs-card-skill）

### 2.0 选型确认门（阻塞；未确认不得渲染）

**先把下面这张完整清单原样贴给用户**，再用 `AskUserQuestion` 收口。清单必须列全，不得只给一两个例子。

**A. 视觉体系（二选一，决定后续所有版式骨架）**

| 体系                                   | 观感                                                               | 适合                                                    |
| -------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------- |
| Editorial Magazine × E-ink（电子杂志） | 衬线/宋体标题 + 纸张墨色 + 氛围背景层，慢、叙事、有手排感          | 商业评论、AI 长文、个人随笔、旅行、设计器物、游戏主视觉 |
| Swiss International（瑞士国际）        | Inter 细体 + 严格左对齐网格 + 单一高饱和强调色，工程感、量化、果断 | 技术、产品更新、测评、数据回顾、教程                    |

**B. 主题 / 强调色（由体系决定，选定后写进 `index.html` 的 `<html>` 属性）**

Editorial（`data-theme`，6 套）：

| 值                 | 名称                   | 适合                          |
| ------------------ | ---------------------- | ----------------------------- |
| `ink-classic`      | 墨水经典               | 商业评论、AI 长文、中性议题   |
| `indigo-porcelain` | 靛蓝瓷                 | 技术、研究、数据、AI 基础设施 |
| `forest-ink`       | 森林墨                 | 旅行、户外、自然、可持续      |
| `kraft-paper`      | 牛皮纸                 | 记忆、手作、个人随笔、旧物    |
| `dune`             | 沙丘                   | 设计、器物、作品集、画廊感    |
| `midnight-ink`     | 午夜墨（唯一官方暗色） | 游戏主视觉、夜景、电影感      |

Swiss（`data-accent`，4 套）：

| 值              | 名称   | 适合                                 |
| --------------- | ------ | ------------------------------------ |
| `ikb`           | IKB 蓝 | AI、技术、产品、设计、工程（最常用） |
| `lemon-yellow`  | 柠檬黄 | 年轻、消费、运动、零售               |
| `lemon-green`   | 柠檬绿 | 生态、健康、新兴科技                 |
| `safety-orange` | 安全橙 | 工业、风险、告警、纠错               |

**C. 交付形态**

| 形态                 | 说明                                                                             |
| -------------------- | -------------------------------------------------------------------------------- |
| 静态图文卡组（默认） | 1080×1440 / 3:4，1 封面 + 4–8 内页 + 可选总结页                                  |
| 含 Live Photo 动态卡 | 需用户提供视频素材；小红书 ≤5s；可选单视频 / 二宫格 / 三宫格 / 四宫格 / 三连拼图 |
| 追加公众号封面对     | 21:9 + 1:1 成对输出（需要时追加）                                                |

**问法**：`AskUserQuestion` 单问最多 4 个选项，按此拆：

1. 第一轮问「视觉体系」（2 项）+「交付形态」（正交，可同轮；多选）。
2. 第二轮问「主题/强调色」，给第一轮所选体系下的主题；主题数超过 4 项时（Editorial 6 套），把全部主题写进问题文本，选项给最贴近内容的 4 个，其余让用户用 Other 输入主题值。

**确认后必须回显并落盘**：向用户复述选定的体系 + 主题值并确认；该选择落在任务目录的 `index.html`（`<html data-theme=…>` / `<html data-accent=…>`）本身即为记录。用户回答"你决定/都行"时，采用缺省推荐（Swiss International + `safety-orange`）并明确告知最终选择。

> 选型确认前**禁止**复制种子模板、写 HTML、跑渲染——风格选错会导致整组图重做。

### 2.1 复制种子模板（含依赖资源，一步做完）

- Editorial：`cp <skill>/assets/template-editorial-card.html <task>/index.html`
- Swiss：`cp <skill>/assets/template-swiss-card.html <task>/index.html`
- **必须同时把 `<skill>/assets/magazine-bg-webgl.js` 复制到 `<task>/assets/`**。种子模板用 `<script src="assets/magazine-bg-webgl.js">` 相对引用，漏复制会导致 ink-flow 背景不渲染（页面看似正常，但少了整层氛围背景）。

> **本地补丁以技能副本自己的 fork notice 为准，别照抄旧清单（2026-09-28 校正）**。开工前先看当次副本的真实补丁：
>
> ```bash
> grep -n "Local fork notice" -A 8 <skill>/assets/template-swiss-card.html
> ```
>
> 本次实测：`.agents/skills/xhs-card-skill` 的 notice 是「2026-09-27：`.pair-preview` 宽度 2400px → 3324px」，而旧清单里的三条**并不都在**——`.frame-shot.bg-asset-*` 仍写着 `url("../assets/screenshot-backgrounds/…")`，那是相对**任务目录**解析的（会落到 `local-tests/assets/`，不是技能的 `assets/`）。
>
> **硬规则**：要用 `.frame-shot.bg-asset-*` 这几个 class，必须把 `<skill>/assets/screenshot-backgrounds/` 复制到 `<task>/../assets/`（即 `local-tests/assets/`）或在自己的任务级 CSS 里改写路径；**不想踩就回避这几个 class**（本次做法，Swiss 技术卡组本来也不需要照片底）。

### 2.2 写 HTML

- 1080×1440 固定尺寸；只替换 `<!-- POSTERS_HERE -->` 区域，自定义 CSS 收进一个任务级命名块（`/* @task <slug> — task-scoped components only */`）。
- **只用种子真实存在的 `.span-*`：2 / 3 / 4 / 6 / 8 / 9 / 12——没有 `.span-5`、`.span-1`**（写之前 `grep -o "^\s*\.span-[0-9]*" <task>/index.html | sort -u` 核一遍）。写了不存在的 span **不报错**，格子会静默掉进单列轨道，表现为"中文一个字一行 + 右侧大片空白"，只在**渲染后**才看得见。要别的列宽就写任务级 CSS，或改文案长度去适配现有 span；**动过列宽必须重渲染 + 复跑校验器**。
- Editorial 禁忌：无 emoji、无渐变、无上下夹击式 flex 留白；内容覆盖 ≥75% 高度。
- Swiss 禁忌：无 emoji、无 border-radius、无 box-shadow、无 linear-gradient；正文不小于技能规定的字号下限。
- 页面配方按 `references/layout-recipes.md` 的 S01–S12 / M01–M16 选，**每页挂一个配方**，并让整组呈现 ≥5 种形状家族（见 2.4）。

### 2.3 渲染

写 `render.cjs` 用 Playwright 按 section id 逐个截图到 `output/`：

- 截图前必须 `await page.evaluate(() => document.fonts.ready)` 并 `waitForTimeout(1200)`，等字体和冻结的 ink-flow canvas 画完，否则可能截到空白背景。
- 文件名用固定的页序映射（`xhs-01-cover.png`、`xhs-02-*.png`…），**不要直接拿 section id 当文件名**（会得到 `xhs-01-xhs-01.png` 这种重复命名）。
- `render.cjs` 放任务目录、`require("playwright")` 由技能根的 `node_modules` 解析（任务目录在技能内，向上查找即可），不必在任务目录再装一次。

### 2.4 自查（先自查再交付）

- **逐页 Read PNG**（必须真的看图）。校验器的边界要认清：**R8 会报底部空白**（本次实测 `bottom whitespace 406px`），但**中部欠填 R5 只作参考、不判 FAIL**——所以"没有 FAIL"不等于"版面满了"，底部空白带必须肉眼确认。
  - 封面欠填 → 在底部加一行"页序索引"（3 列 mono 编号 + 名称），顺带 teasing 内页内容。
  - 内页欠填（本次实测有效的两种改法）→ ① 给行组加任务级 `justify-content: space-between` 把行均匀撑开；② 在底部补一条**回答本页问题**的结论条（本次用 `.card-accent`）。**不要塞装饰条**。
- 再跑技能自带校验器：

  ```bash
  node validate-social-deck.mjs local-tests/<slug>   # 在 xhs-card-skill 技能根目录执行
  ```

  必须 **0 FAIL**，WARN 逐条判断：
  - **R5 密度**：report-only。顶部 96px 安全留白使第一带天然难达 75%，平均 69% 属正常。
  - **R8 底部空白**：真缺陷，按上面两种改法修。
  - **R10 形状家族**：7 页需要 **≥5 种**家族。家族由 DOM 判定：`.matrix-fill`→matrix、`.kpi-tower-row`/`.h-bar-chart`→chart、`.stacked-ledger`/`.pipeline-v`→pipeline、`.grid-2-9`→split、`.card-*`→card-grid、≥3 个"纯数字叶子节点"→rows、`.pullquote`→statement。**换一页的配方**是最省的补法（本次把"变量系统"页从 `.stacked-ledger` 换成 `.matrix-fill` 即补齐）。
  - **R13 锚点**：面积加权的文字重心代理（top/mid/bottom × centered/right/indented/full/left），需 **≥3 种**；它只随"文字质量分布"变化，**相邻页同锚点属提示性警告，别为它硬掰版面**。
  - 密度探针（想拿客观数字时）：`node density-probe.cjs local-tests/<slug>`（在 `local-tests/` 下执行）。

- M14 纵向流水线配方若与当次副本的补丁不符，以 `grep "Local fork notice"` 的实测结果为准（见 2.1）。
- 依赖故障手册：
  - 校验器报 `Cannot find package 'playwright'`：在 xhs-card-skill 技能根目录 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install`。
  - 渲染报 `Executable doesn't exist ... chromium_headless_shell-XXXX`：Playwright 包版本与缓存浏览器不匹配，在任务目录执行 `npx playwright install chromium` 后重渲染。
  - 改了 HTML 必须重跑 `render.cjs`，不允许拿旧图交付。

**确认门 2**：内联展示全部图片绝对路径 + 一句话设计说明，用户认可后才进入发布。

## Stage 3 · 发布（xiaohongshu-skills）

所有操作只通过该技能的 `python3 scripts/cli.py` 完成（在该技能目录下执行）：

1. `check-login` 确认登录；未登录则走其 xhs-auth 登录流程（需要运行中的 Chrome）。返回 `{"logged_in": true}` 即可继续。
   - **macOS 自带没有 `timeout` 命令**（写了会 `command not found: timeout`），别给 CLI 套超时包装。
   - 系统 `python3` 直接可用，无需 venv；`fill-publish` 可重复执行（重跑即覆盖表单），失败重来不会留下半张表单。
2. **分步发布，禁止默认一步发布**：

   ```bash
   python3 scripts/cli.py fill-publish \
     --title-file /tmp/xhs-<slug>-post/title.txt \
     --content-file /tmp/xhs-<slug>-post/content.txt \
     --images "<abs>/output/xhs-01-cover.png" "<abs>/output/xhs-02-*.png" ...
   ```

   中文文本一律走文件参数，不内联在命令行。图片按封面在前的阅读顺序传。
   - **字数校验在填表最后一步**：若报 `当前输入长度为 N ，最大长度为 1000`，说明 Stage 1 的口径量错了（别按"去空白"量），回到 Stage 1 压字后重跑 `fill-publish`，不要在这一步手工删字。

3. **确认门 3**：提示用户在 Chrome 里检查预览（图序、文案、标签、可见范围），用 `AskUserQuestion` 等明确答复。
   - 确认 → `click-publish`
   - 取消 → 必须先 `save-draft`
4. **回执解读（重要）**：`click-publish` 的 JSON 里 `"status": "发布完成"` **不代表已发布**，它只表示按钮已点击；同一时刻 stdout 往往还会打 `15s 内未捕获到任何发布反馈`。必须让用户肉眼确认浏览器出现成功提示/跳转，并如实告知"脚本只证明点了按钮，上线以页面为准"；表单仍在原地则按其所见排查，必要时存草稿。**别因为"没抓住反馈"就重复点发布**（有重复投稿风险）。
5. **客观复核（推荐）**：发布后用标题搜自己的笔记，命中即已上线，顺带拿到互动/置顶要用的 id 与 token：

   ```bash
   python3 scripts/cli.py search-feeds --keyword "<完整标题>"
   # 返回条目里的 id（feed-id）与 xsecToken（xsec-token）
   ```

   不保证立刻进索引（**搜不到不等于没发出去**），但**搜到就是铁证**。

## Stage 4 · 发布后

- 置顶评论：Stage 1 已备稿 `/tmp/xhs-<slug>-post/pinned-comment.txt`。`post-comment` 需要 `--feed-id` + `--xsec-token`（CLI 没有"URL → feed-id/token"的解析命令），获取路径按优先级：① Stage 3 第 5 步的 `search-feeds` 结果（首选）；② 请用户把笔记链接贴过来。
- **评论字数**：原稿常在 500 字符以上，很可能超评论上限——先压一版 **≤300 字符**的短版（本次做法：完整步骤清单压成一行 + 两个易误解点），超长会被拒。发完要提示用户：**脚本没有置顶能力**，需去 App 里点"置顶"。
- 给出最终交付清单：笔记标题、**全部**图片绝对路径、任务目录、文案目录、后续"改文案重发 / 改图重渲染"的命令。
- 如实交代限制：发布反馈缺失、R13 类提示性警告、为过字数上限做过的删改（逐条列明）。
- 完成即止，不追加未被要求的运营动作。
