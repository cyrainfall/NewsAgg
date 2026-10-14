# Handoff — xhs-card-skill

最后更新：2026-09-27 · 版本：v0.26（R5 门槛退休改为纯报告；新增 R13 构图锚点检查，锚点轴首次可测）

这份文档只记录**事实**：当前 skill 由哪些文件组成、每个文件管什么、本轮更新改了什么、怎么验证它还能跑。想读"为什么这样做"或"未来要怎么走"，去看 `PRODUCT.md`。

> **读 §3 历史前请注意**：版本历史记录的是本 skill 的完整沿革，v0.15 及更早的段落会提到当时存在、现已不在本仓库中的测试目录（`demo-*` / `social-card-*` / `smoke-*` / `test-*` 等）与外部工具路径。那些是历史事实，不代表当前结构——当前结构以 §1 为准。

---

## 1. 目录结构（事实版）

```
xhs-card-skill/
├── SKILL.md                   # 7 步工作流入口
├── HANDOFF.md                 # 本文件
├── PRODUCT.md                 # 产品思考
├── assets/
│   ├── template-editorial-card.html   # 杂志风种子
│   ├── template-swiss-card.html       # 瑞士风种子
│   ├── magazine-bg-webgl.js           # WebGL 墨流背景
│   └── screenshot-backgrounds/        # 9 张 WebP 截图舞台底
│       ├── style-a/                   #   5 张 Editorial（dune / forest-ink / indigo-porcelain / kraft-paper / monocle-classic）
│       └── style-b/                   #   4 张 Swiss（ikb-dot / lemon-green-dot / lemon-grid / safety-orange）
├── references/                # 子规范，按需读取
│   ├── platform-specs.md      平台 × 分辨率 × 命名
│   ├── style-system.md        两种风格的硬规则与反模式
│   ├── theme-presets.md       6 套杂志 palette（含 Midnight Ink）+ 4 套 Swiss accent
│   ├── layout-recipes.md      版式骨架（M01-M16 + S01-S12 + WeChat）
│   ├── variation-engine.md    变体：构图锚点 / 背景模式 / Second-Read / 分布规则
│   ├── components.md          字体 / 卡片 / 间距 / 图标 / 标题字数表
│   ├── background-systems.md  墨流 / 网格 / 纸纹层
│   ├── portrait-fill.md       3:4 板的留白对策
│   ├── content-planning.md    钩子 / 拆页 / 文案压缩
│   ├── explainer-sequence.md  讲原理：设计读 / 六拍 / 类比闸门 / 术语闸门
│   ├── production-workflow.md 渲染管线与命令
│   ├── image-overlay.md       文字压图：选图 / 局部 tint / 主体映射
│   ├── screenshot-treatment.md 截图处理
│   ├── map-component.md       Mapbox / OSM / schematic 地图
│   ├── title-shortener.md     公众号 1:1 短标题
│   ├── category-cookbook.md   小红书 11 个品类 + 技术品类的版式路由
│   ├── live-photo-production.md Live Photo 生产、拼图、长视频与发布规范
│   ├── anti-ai-tells.md       反 AI 感：文案自查 / 数据 / 排版 tells
│   └── qa-checklist.md        交付前 checklist
├── agents/
│   └── openai.yaml            # 子代理配置（备用）
├── scripts/
│   ├── check-skill-docs.mjs       # Skill 文档约束测试
│   ├── make-video-contact-sheet.py # Live Photo 视频抽帧 contact sheet
│   ├── package-live-photo.py      # JPG+MOV 打包为 .pvt 的入口
│   └── *.swift                    # Apple Live Photo metadata helper
├── validate-social-deck.mjs   # R1-R10 渲染后校验器
├── package.json / package-lock.json / node_modules   # 仅 playwright
├── .gitignore                 # 忽略 local-tests / node_modules / 根目录散落产物
└── local-tests/               # 本地任务产物（git 忽略，不随 skill 分发）
    └── <slug>/                # 每个任务一个目录：index.html + render.* + assets/ + output/
```

`local-tests/` 整目录在 `.gitignore` 中，commit 只跟踪 skill 内容。当前保留的是技术类文章的实际任务目录（`ai-second-brain-pitfalls` / `clash-tailscale-502` / `docker-cicd-pitfalls` / `github-actions-ci-troubleshooting` / `tailscale-aliyun-netfilter` / `tailscale-force-443` / `tailscale-packet-path`），同时兼作 `validate-social-deck.mjs` 的回归样本。

---

## 2. 数据规模与覆盖面

| 维度 | 数量 |
| --- | --- |
| 工作流步骤 | 7 步（Intake → Extract → Style → Plan → Seed → Build → Image → Deliver） |
| 风格模式 | 2 个（Editorial Magazine × E-ink / Swiss International） |
| 主题色 | Editorial 6 套（5 浅 + 1 暗 Midnight Ink）+ Swiss 4 套 = 10 套；像素实测 6 套**各自可区分**，真正的家族边界只有 Editorial vs Swiss 这个**模式**之分 |
| 版式骨架 | Editorial 16 个（M01-M16）+ Swiss 12 个（S01-S12）= 28 个 |
| 骨架 HTML | 28 个 recipe **全部**带可复制骨架（29 段，M16 占 2 段），全部按模板真实类名实测 |
| 版式节奏 | 4 条硬规则（逐页声明 recipe / 同 recipe ≤2 / 相邻不同族 / 7+ 页 ≥5 形状）+ validator R10 |
| 讲原理路线 | `explainer-sequence.md`：设计读 / 一页一问 / 六拍序列 / 类比闸门 / 术语闸门 / 动作→recipe 表 / 6 种母题；1 条可自动化（形状多样性），2 条人工闸门 |
| 反 AI 感 | `anti-ai-tells.md`：挤压测试 / 文案 tells + 逐字自查 / 数据 tells / 排版 tells / 密度冲突裁决；validator **R12** 扫禁用措辞 |
| 变体引擎 | `variation-engine.md`：构图锚点 10 种（per page）+ 分布规则（≥3 种 / 不连续 / A ≤2）+ 背景模式 + Second-Read Moment（全set 1 处）；与 R10 正交 |
| 形状族 | 12 类（image-full · image-well · map · matrix · chart · pipeline · rows · ledger · statement · split · card-grid · text） |
| 校验规则 | R1–R12（R10 / R11 / R12 为 WARN 级；R3 仅 Swiss）——R10 形状族 · R11 圆角尺度 · R12 禁用措辞 |
| 平台 × 比例 | 小红书 3:4 / 1:1，微信 21:9 + 1:1 配对，方封面 1:1 |
| 小红书品类覆盖 | 11 个品类中 7 个完整支持，4 个需用户提供素材，4 个明确不接；另有 技术 · 开发者 独立路由 |
| 种子模板 | 2 份（Editorial / Swiss），单文件即含全部 class 与字体 |
| 已交付案例 | 7 个 demo（2 个 image-cover + 5 个 kit） |
| Live Photo 支持 | 小红书 `5s` / 微信公众号 `3s`；单视频、二宫格、三宫格、四宫格、三连拼图、长视频低成本诊断 |

---

## 3. 版本历史

仅记录"什么时候新增/变更了哪些可见资产"，不记录文风调整。

### v0.26 · 2026-09-27（本轮）

主题：**R5 门槛退休（改为纯报告）+ 新增 R13 构图锚点检查**。

#### 1. R5 改为纯报告（不是下调门槛，是退休）

v0.25 修掉纸纹层 bug 后，R5 会在几乎每一页报警。按 `validate-social-deck.mjs` 自己在 R12 注释里定的标准——*"a noisy check gets ignored, which is worse than no check"*——一个用从未被验证过的 74.5% 去否决每一页的检查，比没有检查更糟。

**处理方式**：
- **删除** `total < 0.745` 的密度警告。密度数字改为表头常显：`density: xhs-01:48% xhs-02:60% ...`——**信息保留，判决撤销**。
- **保留**中空检测（相邻两段都 <15%）。它不依赖那个未校准的全局门槛，"海报中间出现一个洞"是真实缺陷。确认：修复后只有刻意做坏的 `r12-fixture` / `skeleton-smoke-ed2` 触发它，报的是 `bands 2+3 both under-filled (0% / 0%)`。
- 代码里写明了退休理由和实测区间（真实 deck 45-71%，平均 53%；内容盒上限 87%），供以后有人想重新设定门槛时使用。

**待定**：74.5% 该不该被一个校准过的值取代（比如实测的 55-60%），是产品决定，本轮没有代为设定。

#### 2. 新增 R13 构图锚点（advisory）

`variation-engine.md` §0 的表格里，⌈Anchor distribution⌋ 一行此前是 **manual only**——"≥3 种锚点、相邻不重复、锚点 A 最多两次"这条规则**从来没有测量过**。R13 是它的第一个脚本。

**刻意不假装能识别具名锚点**：§2 的十个锚点里，"off-grid offset" 和 "image-canvas safe area" 无法从几何推导。R13 只测两项可审计的东西：

| 轴 | 取值 | 来源 |
| --- | --- | --- |
| 纵向 | `top` / `mid` / `bottom` | 文字质量的面积加权纵向重心 |
| 横向 | `centered` / `right` / `indented` / `full` / `left` | 面积加权横向重心 + 文字带两侧内缩量 |
| 媒体 | 追加 `+media` | 媒体覆盖 ≥34% 画面 |

输出 `anchors:` 序列 + `anchorMix:` 计数 + 三类警告（种类不足 / 锚点 A 超过两次 / 相邻同锚点）。忠实于 §2.B——**只对锚点 A 的代理计数设上限**，不对其它锚点设，因为这正是文档写的那条。

**实测（`local-tests/` 八套 deck）**：

| deck | 锚点分布 |
| --- | --- |
| `github-actions-ci-troubleshooting` | `mid-full×5` — **只有 1 种** |
| `tailscale-aliyun-netfilter` | `bottom-full×5` — **只有 1 种** |
| `clash-tailscale-502` | `top-full×5  mid-full×2` |
| `tailscale-packet-path` | `mid-full×3  top-full×2  bottom-full×1` — 3 种 |
| `ai-second-brain-pitfalls` | `bottom-full×2  mid-full×2  top-full×2` — 3 种且均衡，**R13 全部通过** |
| `r10-baseline`（R10 七族正例） | `top-full×4  mid-full×3` — 2 种 |

两条结论：
- **R13 有判别力，不是恒报警**：`ai-second-brain-pitfalls` 零 R13 警告。
- **R10 与 R13 互相独立，这是本轴存在的理由，现在有了证据**：`r10-baseline` 用 7 种形状族通过 R10，锚点却只有 2 种。横向轴（`-full`）在全部八套 deck 上**从未变化过**。

#### 3. 文档与检查同步

- `variation-engine.md`：§0 表格改为 R13 可自动化；§0 补上代理的说明、实测锚点分布、以及"质心判据永久弃用、但 R13 用的是面积加权质量位置"的区别；§2.B 补 R13 的对应关系与那处偏离。
- `qa-checklist.md`：新增 **`### Composition Anchor Check (whole set, not per page)`**（签名取值表 + 三条通过条件 + "R13 与你的眼睛冲突时以眼睛为准"）。
- `scripts/check-skill-docs.mjs`：新增 4 条断言（R5 保持 report-only、R13 存在、两处文档章节），90 → **94** 条。

**验证**：`npm run test:docs` 94 条通过；`r10-baseline` 7 页全 clean / 0 fails / R10 干净；全量 15 个目录回归无新增 fails（既有 fails 仅在 `r11-fixture` / `r12-fixture` / `skeleton-smoke-editorial` 等刻意做坏的样本上）。

### v0.25 · 2026-09-27（本轮）

主题：**版本控制基线 + R10 正例 + 一个让 R5 永久静默的 bug**。三件事按用户指定的顺序做：先让改动可回退，再补 R10 的正例，最后校准判断。

---

#### 1. 版本控制基线（此前本技能完全不受版本控制）

ybrain 仓库根 `.gitignore` 第 11 行忽略整个 `.agents`，所以 P0-P4 四轮改动**无法 diff、无法回退**。本轮在技能目录内单独建立 git 仓库。

- `git init` + 初始提交 `3059532`，48 个文件、2.5MB。只跟踪技能本体（`SKILL.md` / 19 份 references / 2 份种子模板 + 9 张纹理素材 / 5 个脚本 / validator / 项目文档）。
- `local-tests/`（29M，含用户真实产物与未打码截图）与 `node_modules/`（17M）由技能自身 `.gitignore` 排除，**已验证暂存区 0 命中**。
- 回退能力实测：改动被跟踪文件 → `git diff` 正确检出 → `git checkout` 正确还原。

#### 2. R10 正例：`local-tests/r10-baseline/`

修正上一轮的记录错误：**R10 本来就有一个正例**（`tailscale-packet-path`，6 页 4 族）。但那是"够用"，不是"验证"——本轮的 deck 是**刻意按 R10 判定结果反推设计**的 7 页正例。

关键发现：**配方与族不是一一对应**，必须按 R10 的判定顺序反推，而不是按配方名选：

| recipe | 骨架里实际的 class | R10 判成 |
| --- | --- | --- |
| S01 | `grid-2-9` + `card-outlined` + `card-ink` | **split**（不是 text） |
| S05 | `card-outlined` ×3，编号写作 "Trap 01" | **card-grid**（"Trap 01" 不匹配 `^0?[1-9]$`） |
| S06 | `card-fill` ×3 + 编号 `01/02/03` | **rows**（无 pipeline 类，编号先命中） |
| S07 / S11 | `stacked-ledger` | **pipeline** |
| S10 | `h-bar-chart` | **chart** |
| S12 | `matrix-fill` | **matrix** |

结果：`text → chart → pipeline → matrix → rows → split → card-grid`，**7 页 7 个互不相同的族**，无相邻重复、无族超过两次。`node validate-social-deck.mjs local-tests/r10-baseline` 的 `rules:` 行**不含 R10**——R10 的通过路径第一次被真实跑通。

- `build.cjs` 从种子拼出 `index.html`（"禁止从零写 HTML"这条规则的字面实现，也让正例能随种子更新）；`render.cjs` 逐页截图。
- **踩到的坑**：种子里字符串 `<!-- POSTERS_HERE -->` 出现两次——第 36 行的说明注释里也有。`indexOf` 匹配到注释那处，把整个 `<style>` 截掉了，产出的文件仍有 7 个 poster 但完全无样式。改用 `lastIndexOf`，并加了"head 必须含有 `--accent:` / `.poster.xhs` / `.h-bar-chart`"的断言——这类 splice 错误产出的文件"看起来是构建成功的"，必须断言。

#### 3. R5 一直被自己的纸纹层骗过（本轮最重的发现）

在正例上跑 R5 得到 48-78% 的密度，而真实 deck 报 **100%**。一个带 `grow` 空档的封面不可能每像素行都被内容覆盖，所以去查 R5 的判定：

```js
const isImg = tag === "IMG" || tag === "CANVAS" || tag === "SVG"
            || (cs.backgroundImage && cs.backgroundImage !== "none");
const hasFill = cs.backgroundColor && !TRANSPARENT && !== "transparent";
```

**任何带非透明背景色或背景图的元素都算"内容"。** 而 Editorial deck 有 `.grain`（纸纹，`position:absolute`，铺满 100%）——它标记了每一像素行。

写 `local-tests/density-probe.cjs --explain` 验证：7 套 deck 的 7 个 `.grain` 全部命中"覆盖 ≥85% 面积"。

**所以 R5 从未真正生效过**：它报的 100% 是纸纹，不是内容。这条规则在 `portrait-fill.md` 与 `qa-checklist.md` 里都写着"内容覆盖 ≥75% 画布高度"，但从来没有一次真的检查过。

**R8 里早就有这个排除**（`if (cs.position === "absolute" && r.width * r.height >= posterArea * 0.85) continue;`），R5 漏了。已补上同一行排除。

修复后测出的**真实**密度：

| deck | band1 | band2 | band3 | band4 | 平均 |
| --- | --- | --- | --- | --- | --- |
| clash-tailscale-502 | 35-48% | 56-100% | 55-100% | 19-43% | **53%** |
| r10-baseline（正例） | 31-56% | 70-100% | 59-100% | 21-62% | **64%** |

理论上限：内容盒 `1440 - 96 - 96 = 1248px` → 即使块间完全无间距也只有 **87%**。要达到 74.5%，全部块间距之和必须 ≤175px。

**⚠️ 遗留决策（需要判断，不适合我单方面改）**：R5 修好之后会在**几乎每一页**上报警（7/7 deck）。按技能的 R12 注释自己的标准——"a noisy check gets ignored, which is worse than no check"——74.5% 这个门槛很可能是在 bug 期间定的、从未被真实数据校准过。是保留 74.5%、按下调成实测值、还是改成纯报告，需要决定。

#### 3.5 锚点判断的像素级校准（`local-tests/anchor-verify.cjs`）

`variation-engine.md` §2.A 里的"**39 页里 0 页文字重量偏右**"是 `anchor-probe.cjs` 得出的。新写的 `anchor-verify.cjs` 按 **bbox 面积加权**重测同一批 39 页，并把两种指标并列打印。

**结论：数字复现，但判据本身不敏感，而且有一句话写过头了。**

| 检验 | 结果 |
| --- | --- |
| 原判据：非加权质心 > 55%W | **0 / 39**（完全复现，原始测量不是编的） |
| 面积加权质心 > 55%W | **0 / 39** |
| ≥50% 文字面积贴左边缘的页 | **27 / 39** ← 即 12 页**不是**左对齐主导 |
| >25% 文字面积落在 60%W 右侧的页 | **34 / 39** |
| rightZone 分布（39 页真实 deck） | 最大 42% / 中位 **37%** / 最小 10% |
| rightZone（对照组故意右对齐的 `skeleton-smoke-ed2/xhs-03`） | **81%** |
| 两种质心算法差 ≥8 个点的页 | 11 / 39 |

- **"0/39"低敏感**：左对齐的**满宽**段落质心天然在 50% 附近，所以">55%"这个门槛只有真正的右对齐块才会触发。这个判据在满宽版式上**不可能失败**——0 是必然，不是发现。面积加权也救不回来（11 页偏移 ≥8 点，但仍全部落在 40-53% 区间）。
- **真正有判别力的是 rightZone**：真实 deck 最大 42%，对照组 81%，**分离度 39 个点**。质心的 50% → 51% 无法分离。对照组"9 页里 2 页偏右"也复现（`xhs-03` 加权 69%、`xhs-09` 非加权 58%）。
- **"每一页都是左对齐块"写过头了**：仅 27/39 页过半文字面积贴左边缘，另外 12 页因为卡片／ledger 缩进而在左侧留白。诚实的表述是"**没有一页是右对齐的，但也不是每一页都左对齐**"。
- **真正恒定的是外轮廓，不是对齐**：一条从左起步、撑满宽度的文字带，配不同的 recipe 反复出现。

改动：`variation-engine.md` §0 与 §2.A 按上表校准；§2.A 补上"median 37% 文字面积在右侧"这个容易误解的数字，并指出 ~55-60% 的 rightZone 阈值可以分辨真正的右对齐而不会误伤正文（**列为候选，未采纳为规则**——§2.B 仍决定该轴为人工）。

#### 4. 新增检查

- `scripts/check-skill-docs.mjs` 加一条断言，锁住 R5 的整幅背景层排除（防止有人再次删掉它，让规则静默失效）。

#### 5. 本轮新增的本地工具（均在 `local-tests/`，gitignored）

| 文件 | 用途 |
| --- | --- |
| `r10-baseline/` | R10 七族正例 + `build.cjs`（从种子拼装）+ `render.cjs` |
| `density-probe.cjs` | 按 R5 算法打印每页四段占用，`--explain` 列出覆盖 ≥85% 的元素 |
| `anchor-verify.cjs` | 面积加权的锚点测量，与 `anchor-probe.cjs` 的非加权质心并列对比 |

### v0.24 · 2026-09-27

主题：**文档与实现的一致性收口**。P0-P4 连续几轮改下来，规则散落在 `SKILL.md` / 15 份 references / 两份种子模板 / validator / 文档测试五个位置，本轮做了一次系统性交叉核对，抓到三类"规则只在一边成立"的问题。

**核查方法与抓到的问题**：

1. **引用死角扫描**（写脚本扫全部 28 个文档的文件引用与章节锚点）：
   - `references/production-workflow.md` 给 agent 指了三个**不存在的**任务目录（`social-card-doubao-input` / `social-card-hiking-outfit` / `wechat-ai-card-skill-cover`）。这是上一轮清理漏掉的文件——同类的失效引用在 `category-cookbook.md` / `components.md` / `style-system.md` / 模板注释里清过，`production-workflow.md` 漏了。
   - 修法不是换成另外三个真实 slug：**`local-tests/` 是 gitignored 的本地目录，随技能分发任何具体 slug 都是错的**。改为「命名模式 + 说明它属于本机路径」。
   - `SKILL.md` 用 `→ Shape Families` 指 `qa-checklist.md`，但那边是粗体标签不是标题，锚点不稳。已提升为 `### Shape Families`。

2. **文档／实现对照**：
   - `qa-checklist.md` 的形状族表列了 **12 个族**，validator 只会产出 **11 个**——`ledger` 是死行。原因：validator 按**结构**识别编号行（`rows`，3 个以上编号叶子节点），不按种子的 `.ledger-row` 类名，所以 `ledger` 永远不会被输出。人工照文档数出的序列会和 validator 不一致。已删除该行并写明 `ledger` 就是 `rows`。
   - 形状数阈值三处不一致：`SKILL.md` / `qa-checklist.md` 只写了「7+ 页要 5、5-6 页要 4」，validator 还有「3-4 页要 3」。已补齐文档。

3. **负向测试**（新检查必须证明能抓到东西，否则等于没有）：

**新增检查**（`npm run test:docs` 86 → **89** 条）：
- **`every referenced reference file exists on disk`** —— 通用检查，遍历全部随技能分发的文档，验证每个 `` `references/*.md` `` 链接在磁盘上真实存在。文本断言抓不到这个（失效链接和有效链接长得一模一样），必须查文件系统。这类检查本可以抓到上一轮清理漏掉的那些引用。
- **`mustNotMatch` qa-checklist 的 `ledger` 族行** —— 锁住文档与 validator 的族清单一致。
- **`mustNotMatch` production-workflow 的具体 slug** —— 锁住"本地路径只能用占位符"。

**负向测试记录**：临时注入三类错误（伪造一条指向不存在文件的 reference 链接、加回 `ledger` 族行、给 production-workflow 塞具体 slug），三条新检查**全部正确 FAIL**；随后逐字节还原（`cmp` 验证），89 条恢复通过。

> 附带发现：那条文件存在性检查在第一次运行时就被自己触发了——本文档最初用反引号包了一个假的 `.md` 文件名来举例，检查把它当成了真实链接。改为散文表述即可。**举例时不要写出可被解析的假路径**，这条对以后写文档同样适用。

**同时核实通过、无需改动的**：
- **28/28 recipe 都有 HTML 骨架**（严格数 ```` ```html ```` 围栏，M16 有 2 段）。v0.17 那轮"补骨架"是真做完了——这是"版式漂移"的根因，现已闭环。
- `variation-engine.md` 存在且 §2.A 锚点正确（`### 2.A The overused default`），本轮新增的几处引用无误。

**遗留（未做，供决策）**：
- ~~**R10 仍无正例**~~ —— **此条有误，见 v0.25**。当时说"没有一套 deck 满足节奏规则"，实际 `tailscale-packet-path`（6 页）在阈值放宽为"5-6 页要 4 个族"之后就已经通过：`text / card-grid / rows / pipeline / card-grid / rows`，4 族、无相邻重复、无族超过两次。R10 的通过路径并非"只在逻辑上成立"。
- **本技能不受版本控制**。ybrain 仓库根 `.gitignore` 第 11 行忽略整个 `.agents`，所以 P0-P4 全部改动无法 diff、无法回退。若要隔离，需在技能目录内单独 `git init`。
- **`HANDOFF.md` §3 历史段落**仍提到已不存在的 `demo-*` / `live-photo-best-suite` 等目录（§3 开头已有说明，属历史事实，未逐条改写）。

### v0.23 · 2026-09-27

主题：**排版与配色家族校准（P4，最后一层）**。这是五层里唯一一层**改的是美学地基**，也是唯一一层 validator 验不了的——R1-R12 全是几何/密度/文本检查，把 Editorial 标题字重从 500 改成 300，它们会全部 PASS。

**动手前先做了两件事，都改变了原计划**：

**1. 查种子模板是否真有排版缺陷 —— 结论是没有。**
原计划里"斜体降部检查"我以为会像 v0.16 的 ledger margin 一样挖出一个真 bug。建 `local-tests/italic-probe.cjs` 实测：`.h-sub` 行高比 **1.20**、`.pullquote` **1.28**，都高于 1.1 的下限。`body` 未设 line-height，所以种子安全。**因此本轮没有修任何 CSS**——规则是给未来 task CSS 设的护栏，不是修 bug。如实记录，不制造修复。

**2. 配色分类：我先给了一个错结论，被用户目视纠正后又用像素实测推翻了自己。**

第一版结论是按 `--paper` 单一变量聚类，得出"6 套聚成 5 个家族，`ink-classic → forest-ink` 不算换主题"。用户看了对比图后指出"六张感觉是六个不同的家族，因为颜色深浅不一样"。

写 `local-tests/p4-verify/palette-probe.cjs` 从**渲染 PNG 的像素**（而非 CSS token）重新实测，用户是对的：

| Palette | 主色（占画布 78%） | 文字墨色 | 强调色 |
| --- | --- | --- | --- |
| Ink Classic | `#f0f0e0` | `#000000` 纯黑 | 黑 |
| Indigo Porcelain | `#f0f0f0` | `#001030` 藏青 | 藏青 |
| Forest Ink | `#f0f0e0` | `#102010` 墨绿黑 | **绿** |
| Kraft Paper | `#e0d0c0` | `#201010` 棕黑 | 焦橙 |
| Dune | `#f0e0d0` | `#101010` 中性黑 | 卡其 |
| Midnight Ink | `#000000` | `#e0e0c0` 米色 | 米色 |

关键数字：**只算纸色，最小成对差 = 0**（Ink Classic ↔ Forest Ink 量化后同桶，精确值 Δ 2/1/0）；**算上墨色后，最小成对差 = 64**，六张全部可区分。

**我的错误是拿一个变量算三个变量的结论**：Ink Classic 与 Forest Ink 确实同纸，但一个是黑强调、一个是绿强调 —— 这是**类别差异**不是色调差异，没人会把绿看成黑。

**这个纠正反而让诊断更清晰**：配色从来不是疲劳感的来源。真正的来源是版式与构图——39 页里 0 页文字重量偏右、同一形状族撑满每套、`.h-xl` 每套出现 4-6 次。这也和 P2 的结论合流：**调色板一直在换，换不动的是锚点。**

**改动（含对第一版错误结论的撤回）**：

- **`theme-presets.md`**：把第一版的 `### Rotate families, not palette names` **撤回**，改为 **`### The six palettes are six distinct identities`**（含上表 + 单变量/多变量两个最小 Δ 的对照，并把"为什么第一版会错"写进正文）；新增 **`### The one real family boundary is the mode`**（Editorial vs Swiss 才是唯一真家族边界）；新增 **`### What actually makes a series feel repetitive`**。保留 **`### Not adopted: the warm-paper ban`**（该条不受影响：上游把暖纸家族整体禁用，Forest Ink 的 `#f5f1e8` 与禁用值 `#f5f1ea` 只差一位十六进制；本技能拒绝该禁令）。
- **`components.md`**：新增三节。`### Typeface is identity, not decoration`（含 **3 条明确拒绝的外部排版规则 + 理由表**——"衬线默认禁用"与"Inter 默认禁用"会直接删掉 Editorial / Swiss 两个模式）；`### Emphasis stays inside the family`（同族强调，禁止跨族塞字）；`### Italic descender clearance`（≥1.1em，附实测比值与"种子无需修"的结论）。
- **`style-system.md`**：Shared Rules 加一条"模式的字型是身份不是风格选项"，并指向 components.md 的拒绝记录。
- **`SKILL.md`**：Step 3 末尾改为"**Rotate the mode first, the palette second**"，并点明"系列疲劳来自版式与构图重复，不是配色"。

**验证**：
- 渲染门禁**已执行且真的抓到了错**：`local-tests/p4-verify/` 6 套配色同内容并列 → `output/compare-strip.png`；再由 `palette-probe.cjs` 采样像素得出上表。第一版结论是读 token 得出的，第二版是读像素得出的——**差别正是第一版错的地方**。
- `npm run test:docs`：78 → **85 → 86** 条（断言同步改为新章节名 `### The six palettes are six distinct identities` / `### The one real family boundary is the mode`）。
- 全量 deck 回归无变化（本轮不改任何 CSS，几何行为预期不变）。

**P0-P4 五层至此全部完成。**

### v0.22 · 2026-09-27

主题：**截图多镜纪律 + 圆角尺度锁定（P3）**。五层里风险最低的一层——只动一份 reference 和一条 WARN 级规则，不碰模板、不碰任何既有 CSS。

**先修正了上一轮的一个误判**：我曾以为 `screenshot-treatment.md` 缺很多设备框规则。实读后该文件已有 6 参数系统、`.device-browser` / `.device-phone` chrome、安全区裁切、风格速查表。**真实缺口只有 4 条**，因此本轮是增量而非重写。

**`screenshot-treatment.md` 新增**：
- **`## Multi-shot discipline`**：一套设备风格 / 设备尺度一致 / 同页多设备等距对齐 / 框不贴画布边且外缘留白均匀 / 内容优先（边框不许压过里面的 UI）。来自 mobile §10 / §13 / §27。
- **`### One corner scale per composition`**：一套里的 `.frame-shot` 只用一种圆角尺度，**最多一个已记录的例外**。附 R11 作用域表——明确列出哪些元素计入（`.frame-shot.corners-*`）与哪些排除（device 内层、bezel 本身、`50%` 圆点、`.map-pin .card` 的 2px）。
- **`### Device mockup is opt-in, not default`**：**明确拒绝** mobile §9 的"默认加设备框"。理由是成本可量化——在 1080×1440 上，bezel 加内圆角要吃掉约 30-40px 横向画布和一块纵向预算；卡片本身已是小画框，再套一层框是用内容换装饰，而 Editorial Identity Test 会把读起来像装饰的页判为失败。写出拒绝记录，防止后来者"顺手补上"。
- 安全区补充一条反向用法：整屏保留时**不要把卡片文字压在状态栏 / home indicator / 导航栏上**——它们会被眼睛跳过，压在上面看起来像层级错误而非说明。
- `## Validator` 一节从"暂无截图专属规则"更新为 R11。

**新增 validator R11**（corner-radius lock，WARN 级）：
- **作用域刻意收窄**到 `.frame-shot` 且**排除 device chrome 内部**。理由：`corners-*` 是作者的选择，而 bezel 的圆角是 chrome 决定的——不排除的话每个含手机框的 deck 都会误报。
- 阈值 3：种子文档自己允许"默认 + 一个例外"，所以 2 个取值合法，第 3 个才是混用。
- **双向验证**：新建 `local-tests/r11-fixture/`——3 页分别用 `corners-sq/sm/md`（0/6/14px）应触发，另加 1 页 `.device-phone > .frame-shot` 应被排除。实测输出 `3 corner radii in one set (0px / 6px / 14px) across **3** frames`——**"3 frames" 证明 chrome 内层确实被排除**（否则会是 4 且混入 24px）。
- **零误报**：在其余 12 个 fixture/deck 上命中 0 次。实测也确认现有 deck 几乎不用 `.frame-shot`（7 篇合计仅 2 处），所以 R11 是**为截图变多之后设的护栏**，不是当前的常规告警。

**接入**：`SKILL.md` 路由表（补多镜纪律与"opt-in 非默认"）+ Step 5 校验规则清单加 R11；`qa-checklist.md` → Images 加 2 条（一套一设备风格一圆角尺度、框不贴边）。

**断言**：73 → **78** 条。

**下一轮（P4，最后一层）**：`components.md` 排版校准（只收 2 条：同族强调禁令、斜体降部检查；3 条外部规则明确拒绝）+ `theme-presets.md` 把 v0.16 的 Series Theme Rotation 升级为**带家族定义**的版本。**风险最高**——动的是美学地基，且 validator 测不出美学回退（R1-R12 全是几何/密度/文本检查，字重从 500 变 300 它们全部 PASS）。**必须设渲染目视门禁。**

### v0.21 · 2026-09-27

主题：**变体引擎（P2）**——补上 R10 看不见的那半个轴。

**要解决的问题**：`clash-tailscale-502` 这类 deck 每张都能过 R1-R12，但整套看起来像一个版式重复七次。R10 量的是**形状族**（这是 ledger 还是 pipeline），量化不了另一半：**视觉重量落在哪里**。同一 recipe 可以是左压、居中、右侧栏——形状族相同，读起来却是不同的页。

**新增 `references/variation-engine.md`**：
- 开头立起分工：**R10 检测，本文件生成**。并放进四层对照表（explainer-sequence 管顺序 / anti-ai-tells 管像不像机器 / variation-engine 管页与页之间差异 / R10 管形状）。
- **§0 执行表**：唯一可自动化的是形状族（R10）；构图锚点是人工。**重心法自动化被实测否决**（见下）。
- **§1 两级分离**：set level（主题家族 / 套系形状 / 签名 chrome / Second-Read，一套一个值，中途不许漂）vs page level（构图锚点 / 背景模式 / 收尾处理，逐页变）。两者失效方向相反——set 级漂 = 不一致，page 级重复 = 单调。
- **§2 构图锚点（10 种，逐页）**：A 顶左堆叠 / B 居中宣言 / C 顶左主 + 右下支撑 / D 底部左侧压图 / E 居中偏下 / F 左窄标 + 右宽内容 / G 右侧栏 + 左主体 / H 离格编辑偏移 / I 图像即画布 + 安全区 / J 收尾簇右对齐。并给出每条自然适配的 recipe。
- **§2.A 点名过用默认**：上游点名 "left-text / right-image" 是最过用的 AI 版式；**卡片的等价物是锚点 A**——因为 flex 列在没人做决定时就是顶左堆叠。A 允许用，但**不是兜底**。
- **§2.B 分布硬规则**：5+ 页至少 3 种锚点；不得连续同锚点；**A 全set 最多 2 次**。与 R10 复合而非替代：7 页需要 **≥5 形状族（R10）且 ≥3 锚点（本文件）**。并指出**改锚点通常比改 recipe 更省力**。
- §3 背景模式（从上游 12 种**筛出**卡片合法子集；渐变 / 双色调 / 径向渐晕明确拒绝并给理由）
- §4 收尾块变体（M07 自带 4 种变体，跨套轮换）
- §5 签名 chrome（set 级，一次选定，中途不改）
- **§6 Second-Read Moment（全set恰好 1 处）**：5 种母题；并补了上游没写的**放置指导**——放在 60-75% 处（注意力回落点），不放首页（会和钩子争），且必须有助于扫读顺序或记忆，否则砍掉
- §7 一份 7 页分布示例（6 形状族 / 7 锚点 / 4 背景模式 / 1 个 Second-Read）
- §8 明确不采纳的轴：Typography Character（与 Style Identity Test 冲突）、Decorative Asset Set（与非自造装饰禁令直接冲突，写下来防止被重新引入）、Motion-Implied（静态无关，留给 Live Photo 分支）、Narrative Spine（已在 explainer-sequence §7，避免两份竞争清单）
- Provenance

**本轮的核心验证：一个被否决的自动化**

不是猜测，是建了探针 `local-tests/anchor-probe.cjs` 实测：

| 数据 | 结果 |
| --- | --- |
| 7 篇真实 deck，共 39 页 | **偏右 0 页**；偏左/居中大致各半，切分点正好压在 45% 阈值上 |
| 失败原因 | 卡片上文字几乎都是满宽左对齐块（88px 边距起、右边距折行），重心天然落在 45-50%，**分不出锚点 A 和锚点 B** |
| 唯一有区分力的信号 | 对照组（刻意按锚点搭的 `skeleton-smoke-ed2`）9 页里有 2 页偏右 |

结论写进 §0：**一个永远通过的检查比没有检查更糟——它会教人忽略报告**。锚点轴因此**按决定保留为人工**，被否决的自动化留在记录里。

顺带得到一个对用户有用的量化事实：**39 页里没有一页把文字重量放在右侧**——结构各异的 recipe，构图上是同一张页。这正是本轴要补的洞，也正是 R10 看不见的洞。

**接入**：`SKILL.md` 路由表 + **Step 4 计划模板加锚点字段** + 新增 **Anchor rhythm (hard)** 三条规则；`qa-checklist.md` 新增 `## Variation (per set)`（8 条）。

**断言**：64 → **73** 条。

**下一轮（P3）**：`screenshot-treatment.md` 多镜纪律 + validator **R11 圆角锁定**（作用域限定 `.frame-shot`，已确认不会误报：受控圆角只有 Editorial {0,6,14} / Swiss {0,4,10}，其余是固定 chrome 与 50% 圆点）。风险最低，可独立先做。

### v0.20 · 2026-09-27

主题：**反 AI 感（P1）**。用户目标收敛为"讲原理、让普通用户听懂"后，先补讲解层（v0.19），本轮补"一眼看出是 AI 做的"这一层。

**先划边界，再写内容**。`style-system.md` 已有 Bad Smells / Style Identity Test / Anti-Patterns A-D，`SKILL.md` 的 Non-Negotiables 已有装饰物与假数据禁令。所以本轮**不重复**这些，而是明确两条不同的轴：

- `style-system.md` 问的是：**这是否还像 Editorial / Swiss？**
- `anti-ai-tells.md` 问的是：**陌生人会不会猜这是机器做的？**

一张卡可以过 Style Identity Test 但仍然像机器做的，也可以风格不达标却完全不像 AI。两个测试都需要。

**新增 `references/anti-ai-tells.md`**：
- **§0 挤压测试**：遮住所有文字，还能看出这一页来自哪篇文章吗？看不出 → 版式是通用的，下面所有 tells 都是它的症状。这是本文件的核心判据，也是"过了 R1-R12 仍然像机器做的"的解释。
- **§1 文案 tells（最高价值，此前全技能无人覆盖）**：禁用措辞（中文 `赋能/解锁/重塑/颠覆/无缝/极致/一站式/全新升级/下一代/革命性` 等 + 英文对应）、假精确数字、假品牌名、冒号式标题反射；**逐字自查**四类红旗（机翻感 / 指代不清 / 可爱但错 / LLM 硬装深刻）；**一页一个文案声区**（mono 数据 / 散文 / 营销腔不许混）。
- §2 数据 tells（三个相同 stat 列、图表不承载论点、数字无单位无基线）
- §3 排版 tells（巨大标题 + 微弱小字、一页多种字体情绪、全大写滥用、渐变标题当"高级"）
- §4 布局 tells（无限居中、结构重复、假复杂无层级、空洞装饰留白）
- §5 材质 tells（点明已有禁令背后的**原因**）
- **§6 密度冲突裁决**：R5 要求"≥75% 填满"，web 反 slop 要求"不要塞满"。两者不矛盾——**目标是填满但不拥挤**。R5 防的是"意外空洞"，反 slop 防的是"刻意塞满"，两者的病根相同：内容不够，而修法是机械的而不是编辑的。
- §7 执行表 + R12 的实测行为与误报说明
- §8 拒绝记录（米色黄铜禁令 / 衬线与 Inter 禁令 / marquee 那类动效 slop）
- Provenance：中文禁用措辞是**重新写的**而非翻译——上游给的是英文营销动词，而本技能产出以中文为主。

**新增 validator R12**（banned copy phrases，WARN 级）：
- `BANNED_COPY` 列表与 §1.A 镜像；扫每页可见文本，**排除 footer chrome**（`.issue-strip` / `.foot` / `.chrome-min` 承载页码元数据，不是文案）。
- **双向验证**（这是本轮最重要的验证）：新建 `local-tests/r12-fixture/` 作正/负样本——正样本 4+3 命中，干净文案静默，放进 `.issue-strip` 的禁用词也静默。**在 7 篇真实 deck 上命中 0 次**，说明这些 deck 的文案本身就干净，同时说明 R12 是"底线"而非常态告警。
- 文档里写明：**R12 是底线不是过滤器**。它匹配固定词表，无法判断一句避开词表的空话；过 R12 只代表"没有禁用词"，不代表"文案好"。也写明误报是预期的（`解锁` 在账号解锁语境下是字面意思），命中是"请看一眼"而非判决。

**接入**：`SKILL.md` 路由表（并写明与 `style-system.md` 的轴分工、禁止合并）+ Step 5 校验规则清单；`qa-checklist.md` 新增 `## Anti-AI-Tells` 一节（挤压测试 + 9 条）。

**断言**：55 → **64** 条。

### v0.19 · 2026-09-27

主题：**讲原理路线（P0 讲解层）**。起因是用户澄清真实用法——"生成技术相关的 HTML 页面再截图分享，目的是形象生动地讲透技术原理，让普通用户也能听懂"。

**评估外部技能的结论（用于解释为什么这一层是自写的）**：
用户点名要集成的三个技能（`.codebuddy/skills/` 下的 `design-taste-frontend` 87KB / `imagegen-frontend-web` 37KB / `imagegen-frontend-mobile` 40KB）分别覆盖**前端审美**、**网站设计稿图像方向**、**App 截屏设计**。**没有一个覆盖"怎么解释"**。而用户的目标是教学——这是三个技能都没有的轴。因此本轮的讲解层是自写，只从两个技能借了两个结构性想法（见文件末尾 Provenance）。

**另一个必须记下的矛盾**：`content-planning.md` 的主线是 Compression Ladder（为信息流可扫描性服务，工具是"砍"），而教学需要"加"（铺垫、类比、因果、边界）。两者不冲突的前提是**按层分开**——单页仍然压缩，解释由**序列顺序**承载。这一条写进了新文件的开头。

**新增 `references/explainer-sequence.md`**：
- **§0 强制机制表**：每条规则标注执行点与自动化程度。这一节是刻意加的——本该文件的病根就是"规则写在文件里但没有执行点"（`portrait-fill.md` 的 Page Rhythm 规则正是如此，直到 v0.16 才补上执行点）。表内明确承认只有 §6 有脚本可查，两条闸门**只能人工**。
- §1 设计读（一行声明：读者 / 原理 / 母题 / 拍数 / 密度）
- §2 一页一问（判断标准从"是否拥挤"改成"回答了几个问句"）
- §3 六拍渐进披露：现象 / **误解** / 机制 / 证据 / 边界 / 记忆点。其中**"误解拍不许省"**是硬规则——没有误解就没有理解，只是"讲了一遍"
- §4 类比闸门（硬）：每个机制拍至少一个类比，**且必须标注在哪一步失效**；不许用技术概念解释技术概念；不许多页复用同一类比；封面不放类比；三个用烂的类比点名禁用（网络=水管 / AI=大脑 / 缓存=冰箱）
- §5 术语闸门（硬）：首次出现同页翻译；**按功能定义而非按结构定义**；一页最多一个新术语；封面/标题不得含未解释术语
- §6 解释动作 → recipe 对照表（因果链 / 状态对比 / 分层拆解 / 证据 / 常见误解 / 量级 / 提问 / 单句断言 / 收束 / 呼吸），并硬规则要求**至少三种不同动作**（与 R10 形状族配对）
- §7 六种叙述母题
- §8 一份完整示例计划（7 拍，含每页问句与 recipe）
- §9 明确不覆盖的范围（含"哪一步值得做动效"**故意未定义**，留待分支 B 决定）
- §10 拒绝记录（三条：每段一张独立横图 / 自由调 dial / 默认加设备框）

**接入点**：
- `SKILL.md` 路由表新增该文件；Step 2 "Extract The Story" 增加教学路线分支，并点明两条覆盖规则（一页一问、误解拍不许省）
- `qa-checklist.md` 新增 `## Teaching Sets` 一节（13 条勾选 + "序列朗读测试"：只读各页问句，若讲不出故事就是序列坏了，**不许靠加文案修**）

**断言**：46 → **55** 条。

**本轮未做**：
- 分支 B（静态 + 动效）所需的"哪一步值得做动效"判断规则，以及"HTML 动效 → Live Photo"的管线文档。`live-photo-production.md` 现有内容是为**拍摄素材**写的，不是为**自制动效**写的，这块需要新增而非修改。
- P1（反 AI 感）/ P2（变体引擎）/ P3（设备框 + R11 圆角锁定）/ P4（排版色彩校准，需渲染回归）。P3 风险最低、可独立先做。

### v0.18 · 2026-09-27

主题：**骨架补齐第二轮 —— 剩余 11 个 recipe 全部补上，至此 28 个 recipe 都有可复制的 HTML 骨架**。

**新增 11 个骨架**：M01、M02、M03、M04、M05、M06、M09、M10、M11（Editorial）+ S01、S03（Swiss）。骨架段数 18 → **29**（M16 占 2 段：Mode A / Mode D）。

**结构调整**：M16 的两个骨架原先挂在 `### Photo qualification (gate before choosing M16)` 下，被算作独立章节。该标题降为 `####`，使其真正归属 M16 —— 骨架与 recipe 的对应关系不再有歧义。

**第二轮实测发现（均已写回文档）**：

1. **R5 有一个盲区，必须看图**。`.marginalia .mg-col` 和任何带 `border-left` 的分栏，其"边框盒"占满整列高度，R5 会把这一列整列算成已覆盖。结果是 **M03 / M11 下半页大片空白仍然 PASS**（实测 M03 和 M11 都出现过）。文档里对这两条 recipe 都加了硬提示：不要相信绿灯，必须目视。
2. **`justify-content:center` 是 M09 的陷阱**。居中会同时留下上方和下方各一个空带（>216px），页面读起来像"PPT 漏排"。改为 `space-between`（kicker 顶 / 宣言居中偏上 / 注释+细线沉底），四个横带全部有内容。
3. **M06 的尺寸窗口很窄**。`r-4x3` 欠填约 190px；`r-1x1` + 默认 `gap-3`(36px) + `.col-2` 的 48px 行距会溢出 footer 26px。可用组合只有 `r-1x1` + 32px content gap + 32px row-gap（32px 仍在 R9 要求的 28px 之上）。
4. **`.mag-bg` 是静默失败点**。种子模板第 846 行引用 `<script src="assets/magazine-bg-webgl.js">`，这个文件**必须拷进任务目录的 `assets/`**。漏拷时 canvas 渲染为空，整页退回平纸 —— 而 `style-system.md` 的 Editorial Identity Test 正好把"平纸 + 衬线标题"判为失败。已在 M01 段落写成硬要求。
5. **无头渲染 WebGL**：没有 GPU 的环境需要 `--use-angle=swiftshader --enable-unsafe-swiftshader`（`validate-social-deck.mjs` 已经带了，但任务自己的 `render.cjs` 很容易漏，漏了导出图里氛围层就是空的）。本机实测两种参数下画布都正常绘制（单独截图 66KB，非空白），该参数是为无 GPU 环境兜底。

**新增测试样本（本地，git 忽略）**：
- `local-tests/skeleton-smoke-ed2/` — M01-M06 / M09-M11 共 9 张，全部 PASS，形状族分散为 text / image-well / statement / rows / split。
- `local-tests/skeleton-smoke-sw2/` — S01 / S03，0 FAIL。
- 两套 `render.cjs` 均已带 swiftshader 参数；`skeleton-smoke*` 的 Editorial 目录已补 `assets/magazine-bg-webgl.js`。

**断言**：42 → **46** 条。骨架计数阈值上调到 ≥29，并新增 4 条针对上述实测结论的断言。

### v0.17 · 2026-09-27

主题：**给 19 个无骨架 recipe 补上其中最常用的 7 个，并修掉它们暴露的种子模板缺陷**。起因是追查"真实 deck 为什么不用 recipe 类名"。

**核查到的事实（漂移根因）**：
- `layout-recipes.md` 里 29 个 recipe 只有 **9 个**带可复制的 HTML 骨架（M12 M13 M14 M15 S08 S09 S10 S11 S12），其余 19 个只有散文结构描述。
- 对照 `local-tests/` 真实 deck：类名**正确**的恰好是 `pipeline-v`(M14)、`beforeafter`(M15)、`stacked-ledger`(S11) —— 全在有骨架的 9 个里；被自定义类名重新发明的 `.tk-nb` / `.ws-row` / `.meas-row` / `.stat-nb` —— 全在无骨架的 19 个里。
- 结论：**不是 agent 主动自造类名，是文档没给骨架，只能手搓 markup 并自编类名**。v0.16 加的技术品类路由（S02/S04/S05/S06/S07）当时全在无骨架名单里，所以那条路由实际上落不了地。

**新增：7 个 HTML 骨架（Editorial 2 + Swiss 5）**
- M07 Closing Note、M08 Tall Ledger、S02 Two Signals、S04 Interface / Browser Mock、S05 Trap Rows、S06 Pipeline / Architecture、S07 Takeaway Ledger。
- 全部只用种子模板里真实存在的类名，并**逐个实测**（1080×1440 渲染 + `validate-social-deck.mjs`），把实测出的约束写回各 recipe 的注释。
- 带骨架的 recipe：9 → **16**。`check-skill-docs.mjs` 新增一条计数断言（≥16）。

**实测发现的两个真缺陷（已修）**：
1. **`.ledger-title` / `.ledger-note` 没有 margin 重置**。`<h3>` 的 UA 默认 margin（1em ≈ 42px/边）从未被重置，每行多出约 128px 死空间 —— 导致 **M07 即使只放 4 行也溢出 187px**。同一缺陷之前只在 `.step-title` 上修过（种子模板 header patch #1），ledger 家族漏了。本次作为 **patch #5** 补上，`margin: 0 0 16px`（16px 是 validator R9 的下限）。
2. **`.grow` 的语义被误用**。flex 列不会自动拉伸子项，grid 会。把这当成"欠填"的解药会得到两种错误：只给容器加 `.grow` → 空白pooled 在底部（S06 只有 53% 密度）；给每张卡加 `.grow` → 卡片被拉成空壳（S05 内部大片死白）。正确做法：**grid 容器加 `.grow`，flex 行则给每一行加 `.grow`**。

**实测上限（写进 recipe 注释，均以种子模板 + patch #5 为准）**：

| recipe | 配置 | 结果 |
| --- | --- | --- |
| M07 | 4 行 + closing block | PASS |
| M07 | 5 行 + closing block | R2 FAIL（callout 压过 footer 92px） |
| M07 | 6 行 + closing block | R1/R8 FAIL（溢出 161px） |
| M08 | 5 行 / 6 行 | PASS / PASS |
| M08 | 7 行 | R1/R8 FAIL（溢出 162px） |
| S02 / S04 / S05 / S06 / S07 | 见各自骨架 | 0 FAIL；R5 见下 |

**已知的 R5 度量局限（写进文档，不要当成 bug 追）**：
- S05 用 `.card-outlined`（透明底），而 R5 的像素覆盖只统计有背景填充的块和文字框，**天然低估透明卡片**：一个正确的 S05 页面只报 ~57%，永远低于 74.5% 的 advisory 阈值。
- S07 报 ~69%（bands 56/100/74/47）：墨块宣言面板留白是这一页的设计意图，不要为了凑数字加行。
- S02 骨架若去掉外层 `.grow` 会掉到 37% 密度 —— `.grow` 在这几条 Swiss 骨架里是承重结构，不是装饰。

**新增测试样本（本地，git 忽略）**：
- `local-tests/skeleton-smoke/` — Swiss 5 张（S02/S04/S05/S06/S07），`render.cjs` 可重渲。
- `local-tests/skeleton-smoke-editorial/` — M07 四/五/六行 + M08 五/六/七行共 6 张，用来复现上面的上限表。

**其他**：
- 删除 `.agents/skills/guizang-social-card-skill/`（上游重复副本，2.3 MB，未被任何软链引用；`.agents` 整个目录在仓库根 `.gitignore` 中，不受 git 跟踪）。
- `check-skill-docs.mjs` 断言 36 → **42** 条。

### v0.16 · 2026-09-27

主题：**版式节奏（Recipe rhythm）收口 + 技术品类路由**。起因是用户反馈"每次生成技术文章样式都差不多，审美疲劳"。核查 `local-tests/` 下 7 篇真实技术文章（约 45 张 3:4 卡）后确认是规则空洞，不是执行失误。

**核查到的事实**：
- 7 篇里 `.h-xl` 每篇出现 4-6 次；`.matrix-fill` / `.h-bar-chart` / `.kpi-tower-row` / `.image-hero` 出现 **0 次**；`.frame-shot` 全 7 篇合计 **1 次**。
- 唯一带 `Hard rule` 的节奏约束（`content-planning.md` → Image-Led Sequence "never two M16 in a row"）**明确不适用于 News / tutorial / data / review**，即技术内容被规则性豁免。
- `portrait-fill.md` 的 Page Rhythm 规则存在，但不在 7 步工作流里，`qa-checklist.md` 没查，validator 也没查 —— 规则没有执行点。
- `category-cookbook.md` 里**没有技术 / 开发者品类**（grep `技术|开发|程序员|developer` 零命中），技术内容无路由，只能落到最省力的编号 ledger。
- 真实 deck 大量使用任务级自定义类名（`.tk-nb` / `.ws-nb` / `.stat-nb` / `.meas-row`）而非种子模板的 `.ledger-row`。

**新增**
- `SKILL.md` Step 4 加 **Recipe rhythm (hard)** 四条硬规则：每页必须声明 recipe；同一 recipe ≤2 次；相邻页不得同形状族；7+ 页至少 5 种形状 / 5-6 页至少 4 种。并加 **The default failure mode is shape collapse** 段与 counter-shapes 对照表。
- `references/category-cookbook.md` 加 **技术 · Tech / Developer** 品类路由：首选 Swiss × IKB（S04 终端/日志、S02 对比、S05 踩坑、S06 链路、S10 图表、S09 指标塔、S03 配置字段），Editorial 侧只推荐 M14 / M04 / M01 / M06；含 5 条 pitfalls（首要一条是"每页都变成编号清单"）。能力圈小结同步加入技术品类。
- `references/qa-checklist.md` 加 **Recipe Rhythm Check (whole set, not per page)** 段：Shape Families 分类表（image-full / image-well / map / matrix / chart / pipeline / rows / ledger / statement / split / card-grid / text）+ 三条通过条件。
- `validate-social-deck.mjs` 加 **R10 recipe rhythm**（package 级、仅 WARN，不影响退出码）：按形状族统计整套，报"一个族撑满整套""相邻同族""形状数不足"三类问题；表头新增 `shapes:` 序列行。形状族按**结构**识别不按类名，因此自定义 `.tk-nb` 这类编号也计入 `rows`；只统计 3:4 卡，排除微信 21:9 / 1:1 封面。
- `references/theme-presets.md` 加 **Series Theme Rotation (multi-article accounts)**：多篇连载账号在"系列级"轮换 palette / 模式 / 明暗 / 签名形状，避免单篇合规但整体趋同。

**修改**
- `scripts/check-skill-docs.mjs` 新增 10 条断言，锁住上述规则（现共 36 条）。

**已知副作用**：
- R10 是启发式的，`text` 族偏粗（"大标题 + 段落"都归入此类），因此相邻同族的 WARN 有误报可能。它是 advisory，不改变退出码。
- 在 `local-tests/` 现有 7 篇上跑 R10，7/7 都会报 WARN —— 这是历史产物的真实状态，不是新规则坏了。

**同一轮的文档清理**：
- 删除 `COMMERCIAL_LICENSING.md`（上游作者的商业授权招商文档，介绍的是另外三个项目，与本 skill 功能无关）。
- 清掉全部失效引用：`/Users/<原作者>/...` 绝对路径（原 §4 的验证命令已改为本机可跑的形式）、已不存在的 `local-tests/demo-*` / `smoke-ai-tools` / `demo-showcase` / `.codepilot-uploads` 引用（散落在 `category-cookbook.md` / `components.md` / `style-system.md` / 两份种子模板的注释里）。
- 项目身份统一：`README.md` / `README.en.md` / `PRODUCT.md` / `HANDOFF.md` / `SKILL.md` 的标题与目录树、`AGENT.md` 的自述都改为 `xhs-card-skill`；去掉上游 GitHub 徽章、上游效果展示图、姊妹项目链接、`.trae/skills/` 这个错误路径（实际是 `.agents/skills/`）。
- **保留（合规必需，不是遗漏）**：`LICENSE`（AGPL-3.0 全文）；`README.md` / `README.en.md` 文末 `## License` 段里的单一出处与署名；两份种子模板头部的修改声明（AGPL 要求修改过的文件保留"已修改 + 日期"提示）。这些合并到唯一位置，其余位置一律改为引用。

### v0.15 · 2026-07-01

主题：**Live Photo 从一次测试能力收口成 Skill 规则**。本轮把两天聊天里关于平台限制、拼图版式、长视频处理、品类判断、发布路径和执行失误的结论写回文档，并补上文档测试。

**新增**
- `references/live-photo-production.md`：Live Photo 生产规范。覆盖 `.pvt` 打包、AirDrop/iPhone 测试、小红书 `5s`、微信公众号 `3s`、信息量判断、三连拼图、material-first 拼图、长视频低成本诊断、contact sheet、平台发布提醒和常见故障。
- `scripts/check-skill-docs.mjs`：文档约束测试。当前卡住根目录产物、Live Photo 拼图能力、M16 图片压字规则、可见文案规则、非模板装饰禁令、平台发布提醒、产品复盘和交接记录。
- `scripts/make-video-contact-sheet.py`：把视频按少量时间点抽成 contact sheet，用于长视频或 Live Photo 视觉检查，避免默认密集抽帧。
- `scripts/package-live-photo.py` 及 Swift helper：把 JPG + MOV 写入 Apple Live Photo metadata 并打包成 `.pvt`，用于 iPhone/AirDrop 测试。

**修改**
- `SKILL.md` 增加 Live Photo 支持范围：短 motion card、三连 Live Photo、material-first 单视频/二宫格/三宫格/四宫格拼图、长视频 intake。明确用户提供视频是正常路径，网页素材只用于 demo / promo / 测试。
- `SKILL.md` 加硬规则：生成产物必须在任务文件夹，默认 `local-tests/<slug>/`；禁止把结果放在 Skill 根目录。
- `SKILL.md` 加可见文案规则：卡面文案必须描述用户真实场景，不能把 `3s`、`5s`、`Live Photo`、`三连拼图`、`信息量`、`长视频处理`、`倍速`、`高光检测` 等内部制作词写成标题或正文。
- `SKILL.md` 加 material-first 规则：单视频加字必须走 M16 / image-overlay 的图片压字逻辑；如果只有一个标题，就用断行、字号、字重、字距、对齐和位置做排版，不发明 kicker、meta、hairline、线条、刻度、徽章、字幕或说明文字。
- `references/category-cookbook.md` 加 Live Photo Scene Library。它不是固定模板清单，而是判断素材在 `3s` / `5s` 内能承载多少信息，以及适合单视频、三连、拼图、修剪还是不做 Live Photo。
- `references/platform-specs.md` 加小红书 / 微信公众号 Live Photo 平台说明：小红书 `5s`，微信文章内 `3s`，发布路径以手机端为准。
- `PRODUCT.md` 加 `Live Photo 复盘（2026-07-01）`，记录这次需求、产品判断、执行问题和验收标准。

**本轮测试素材与输出**
- 用户素材目录：`local-tests/live-photo-best-suite/视频素材/`
- 统一测试目录：`local-tests/live-photo-best-suite/puzzle-layouts/`
- 四个测试视频输出：
  - `IMG_XHS_PUZZLE_SINGLE_TEXT_5S_LIVE.MOV`
  - `IMG_XHS_PUZZLE_TWO_STACK_5S_LIVE.MOV`
  - `IMG_XHS_PUZZLE_THREE_STACK_5S_LIVE.MOV`
  - `IMG_XHS_PUZZLE_FOUR_GRID_5S_LIVE.MOV`
- 规格：全部 `1080x1440` / `5s` / `24fps` / `120 frames`。本地测试目录被 `.gitignore` 忽略，不随 Skill 分发。

**线程里暴露的问题，已转成规则**
- 不要把制作要求当成观众可见内容。之前错误例子包括把"三连拼图"、"5 秒看完完整幅度"、"别硬塞"写到画面上。
- 不要自造主题、背景色、线条、刻度字或装饰组件。没有在 `theme-presets.md` / `layout-recipes.md` / `image-overlay.md` 出现，就不能为了"显得设计过"而加。
- 不要为了通过密度警告给 material-first 拼图加字。二宫格、三宫格、四宫格可以无字；单视频也只能加用户场景需要的短标题。
- 长视频不要默认做精准高光检测。只能先做稀疏抽帧和候选段诊断，再让用户选择修剪、倍速、拆成三连或指定时间段。
- 网页下载视频不是用户工作流。那只服务我们自己的测试和宣传案例。

### v0.14 · 2026-05-28（本轮）

主题：**截图美化素材从 PPT skill 迁移**。v0.11 当时只 port 了规则文档（`references/screenshot-treatment.md` 154 行）和工具类（`.frame-shot.*` + `.device-browser` / `.device-phone`），但 PPT skill 的真实材质 WebP 背景**整目录没拷过来**，导致 `.bg-paper / bg-grid / bg-dot` 这些舞台底全是纯 CSS 实色，截图密度高的卡片视觉偏薄。本轮把素材补齐。

**改动事实**：
- 新增 `assets/screenshot-backgrounds/`，整目录 1.8 MB / 9 张 WebP：
  - `style-a/` 5 张 Editorial：`dune.webp` / `forest-ink.webp` / `indigo-porcelain.webp` / `kraft-paper.webp` / `monocle-classic.webp`
  - `style-b/` 4 张 Swiss：`ikb-dot-gradient.webp` / `lemon-green-dot-shadow.webp` / `lemon-grid.webp` / `safety-orange-halftone.webp`
- `assets/template-editorial-card.html` 加 5 条 `.frame-shot.bg-asset-*` 规则，指向 `style-a/` 五张图（带 fallback 实色防止素材未加载时透明）
- `assets/template-swiss-card.html` 加 4 条 `.frame-shot.bg-asset-*` 规则，指向 `style-b/` 四张图
- `references/screenshot-treatment.md` 加 "Solid `bg-*` vs asset `bg-asset-*`" 小节：两张对照表 + 4 条 rules of thumb（不混 accent / 不叠 `shadow-ed` / 一致使用 / 高比例板不用）+ 路径说明
- 同文件 Style cheat-sheet 加两条 recipe：Editorial hero with real texture / Swiss hero with brand-aligned stage
- `HANDOFF.md` 目录结构段加 `screenshot-backgrounds/` 子目录视图

**重要约定**：
- 素材路径用相对 URL `../assets/screenshot-backgrounds/style-{a|b}/{name}.webp`，假设 deck 的 `index.html` 在 `local-tests/<task>/` 下（比 `assets/` 低一层）。放别处需要在 deck 里本地 override 路径
- Swiss 资产**强 accent 绑定**：`bg-asset-ikb-dot` 只能在 `data-accent="ikb"` 的 deck 用，不允许混色
- 不要在 asset 背景上叠 `shadow-ed`——`1px` 描边会和材质冲突，读起来像 SaaS 框
- `r-3x4` / `r-1x1` 高比例板仍优先用 solid `bg-*`，asset 材质在过裁切时会变怪
- 这是**素材迁移**，不是规则迁移：PPT skill 原版 `screenshot-framing.md` 的 7 参数语义系统未引入（本 skill 走 CSS 工具类路线，决策更轻）

### v0.13 · 2026-05-27（上一轮）

主题：**git 初始化 + 仓库瘦身**。把所有本地测试 / demo / 上传图 / 旧脚本统一搬到 `local-tests/`，写 `.gitignore` 忽略整个测试目录与 `node_modules/`。skill 本身只保留：`SKILL.md` / `HANDOFF.md` / `PRODUCT.md` / `assets/` / `references/` / `agents/` / `validate-social-deck.mjs` / `package.json` 与锁文件。

**改动事实**：
- 新建 `local-tests/`，搬入 28 个测试目录（demo-* 16 个 / social-card-* 6 个 / smoke-* 1 个 / test-* 1 个 / Image / 全图封面参考 / .codepilot-uploads）以及 3 个旧脚本（`render.cjs` / `debug-bg.cjs` / `probe-webgl.cjs`）
- 新增 `.gitignore`：忽略 `local-tests/` / `node_modules/` / `.DS_Store` / `*.log` / `.vscode/` / `.idea/`
- `HANDOFF.md` 目录结构段同步更新，列入 `local-tests/` 子目录视图
- skill 自身文件结构未变，模板与规则文档一律未改

**重要约定**：`local-tests/` 不进 git，不分发，不放入 skill 包。`demo-showcase/editorial.html` 和 `demo-showcase/swiss.html` 仍然是字号/字距/版式还原的 source-of-truth——它们移到了 `local-tests/demo-showcase/`，路径变了但作用未变。所有后续 demo / 冒烟测试都应在 `local-tests/` 下新建子目录。

### v0.12 · 2026-05-27

主题：**Editorial 美学回退 + 第 6 个杂志主题 Midnight Ink + 图片裁切策略 + 交付工作流调整**。本轮起因是用户对 v0.11 冒烟测试的"质量回退"反馈——v0.10/v0.11 在把 showcase 模板化进 seed 的过程中，把 Editorial 的字重/字距/正文字族无意中改成了"信息图横幅"风（900 字重 / 负字距 / sans 正文），与 `demo-showcase/editorial.html` 的"克制磁带感"完全偏离。同时 Codex 外部测试 `social-card-wukong-breakout` 暴露了缺暗色主题、自造 `wukong-night` 不合规的问题。

**新增**
- `assets/template-editorial-card.html` 增加 `[data-theme="midnight-ink"]` 暗色 palette + 三段必随覆盖（`.grain` 翻成 screen-mode 暖光斑 / `.paper-wash` 改成金辉 + 阴影 vignette / `.frame-img` 加深底色 + 1px 暖边）。Editorial palette 从 5 套变 6 套。
- `references/theme-presets.md` 增 **Midnight Ink** 段，含色值 + 必随覆盖代码 + 使用场景（游戏 key art / 夜景 / 影调封面）。明确"暗色 Editorial 只有这一套，不要自造第二套"。
- `test-dark-theme-elden-ring/` — Midnight Ink 验证 deck（4 张 1080×1440，《艾尔登法环》新手指南）。图源 Steam 官方 8 张截图，记录在 `assets/`。验证 P1 全幅 hero-bleed + 黄金 accent、P2 M14 pipeline 4 步 + 21:9 atmosphere、P3 M07 ledger 4 行 + 战斗 atmosphere、P4 quote-card + 4:3 静态时刻。

**修改：Editorial 美学回退（核心）**
- `assets/template-editorial-card.html` 全部 Editorial 排版默认值恢复 showcase 美学：
  - `.h-display` 124px **weight 500** letter-spacing **+.04em**（v0.10/v0.11 误改为 900 / −.01em）
  - `.h-xl` 88px weight 500 +.03em（曾经 700 / −.01em）
  - `.h-md` 56px weight 500 +.02em
  - `.lead` 28px **font-family: var(--serif-zh)**（曾经被切到 sans-zh，是塌掉杂志感的主因）
  - `.body` 24px serif-zh（同上）
  - `.kicker` 21px letter-spacing **+.22em**
  - `.meta` / `.label` 18px +.20em
  - `.ledger-row .ledger-title` 42px weight 500，`.ledger-note` 22px serif-zh
  - `.pipeline-v .step-title` 40px weight 500，`.step-desc` 24px serif-zh
- `references/components.md` Editorial 字号表完整改写为上述值，加一段 **Restored 2026-05-27** 警示，写明"v0.10/v0.11 错改值是什么，为何会塌掉，showcase 是 source-of-truth"。
- `references/style-system.md` Mode A 加 **Typography stance — "the larger, the lighter"** 段：Editorial display 必须 500、serif body、宽字距；反模式（700-900 + sans + 负字距）= "infographic banner" 塌方。

**修改：图片裁切策略**
- `assets/template-editorial-card.html` `.frame-img img` 默认 `object-position` 从 `center top` 改为 `center 50%`。Editorial 之前的 `center top` 是默认值意外，导致竖图主体被切掉；showcase 实际上是逐图 inline 覆盖。
- `references/components.md` Image Containers 段加 **主体感知裁切表**（主体位置 → object-position 取值）+ 硬规则"每张图都要 inline 写 object-position，默认值是 fallback 不是推荐"。
- `SKILL.md` Step 6 Image and Screenshot Handling **Crop discipline** 改写为强制每张图 inline 写 `object-position`，带具体取值示例（`center 25%` 天空多 / `center 50%` 居中 / `center 62%` 半身 / `center 70%` 前景物）。

**修改：交付工作流（"show user first, ask before validating"）**
- `SKILL.md` Step 7 Deliver 重写：
  - 默认 **不再** 渲完自动跑 validator。
  - 渲完先把图给用户看，问一句"先你自己看，还是我先自动核查一遍？"。
  - 用户说"自己看"→ 停下来等反馈；说"你查吧"→ 才跑 `validate-social-deck.mjs`。
- 起因：用户反馈每次渲完自动跑 validator 让交付窗口变得很长，用户大多数时候眼睛比脚本更快。
- 代价：自动校验的"安全网"被撤掉一格——但脚本仍然可用、用户随时可调。

**修改：已有 demo 同步**
- `demo-kyoto-trip/index.html` — 字号回退到 showcase 值，P4 ledger 去掉 03（岚山）解决 3:4 溢出，title 改"四条建议"。
- `demo-smoke-editorial-travel/index.html` — 字号回退到 showcase 值。P1 hero 从 `r-16x10` 改 `r-3x2` + `object-position:center 62%`（hero-grassland 主体在画面中下）。P5 camp-tarp 加 `center 55%`。两份 demo 都 4-5 张 1080×1440 重渲过，无溢出。

**Codex `social-card-wukong-breakout` 诊断**
- 状态：未由本 skill 修复。Codex 在 v0.11 规则下做的测试。
- 根因：Codex 实际**遵守**了规则——错的是规则本身。`components.md` 当时还在写 900/700/sans-body 默认值，Codex 照搬出来必然塌；Codex 自造的 `wukong-night` 是"规则没暗色"的合理外推。
- 修复路径：v0.12 已落地 Midnight Ink 官方暗色 + 美学回退；后续 Codex 任务直接 `data-theme="midnight-ink"` 即可，无需自造。

**未做**
1. 截图美化资产 port（仍在短期清单）
2. 地图组件（同上）
3. 1:1 短标题生成器（同上）
4. 多平台一次出包（同上）
5. `social-card-wukong-breakout` 未重新生成——v0.12 是规则修复，不回灌历史 Codex 测试

### v0.11 · 2026-05-27

主题：**短期规划 Phase A 收口 — validator + Editorial 冒烟测试**。短期规划 6 件事完成第 1、2 件，把 v0.10 留下的 Editorial 长内容欠债（M11/M14 在 3:4 上从未单独验证）也清掉。

**新增**
- `validate-social-deck.mjs` — 单文件 Node 脚本，接受 task-dir 或 index.html，对每个 `<section class="poster …">` 跑 6 条规则：R1 overflow / R2 footer 碰撞 / R3 Swiss 粗体 display / R4 字号下限 / R5 4 横带密度（pixel-row union 覆盖率）/ R6 `.h-xl` 行/字 cap。`--style=swiss|editorial` 可显式指定，默认按 `data-theme/data-accent` 或 serif 字族 fallback 自动判断。退出码 1 仅在 FAIL 时；WARN 是 advisory。
- `package.json` `scripts.validate` — 把 `node validate-social-deck.mjs` 包到 `npm run validate <task-dir>`。
- `demo-smoke-editorial-travel/` — Editorial 冒烟测试 5 张 1080×1440：M01 cover + M11 Marginalia Essay（3 段正文 + 7 行 marginalia）+ M07 Field Ledger（5 行账本）+ M14 Vertical Pipeline（5 步）+ M16 Image-Led Cover 收束。全部 PASS validator。
- `references/components.md` § Editorial `.h-xl` Hard Caps — 实测表：xhs 92px / square 78px / wide 96px；附 M11/M14/M07 各自的 ledger / paragraph / step 数硬上限。

**修改**
- `SKILL.md` Step 5 Build And Render 加：每次 render 后必须跑 validator，FAIL 必修。Step 7 Deliver 加：交付前再跑一次；WARN 在交付消息里报告让用户决定。
- `validate-social-deck.mjs` 修两轮 false positive：
  - R2 footer collision：原版把 `.content` 容器 + 全幅 `.mag-bg` 误判碰底；改成只看 leaf text/media + 跳过 ≥95% 全幅 absolute 节点。
  - R4 min font：原版按 class 选择器命中所有 `.lead`，但 Swiss `.role-card .lead` 是子组件文本。改成只查 leaf text 节点的角色。
- `references/components.md` § Image Containers 加：figcaption 类名硬规则——Editorial 用 `.img-cap`、Swiss 用 `.swiss-img-caption`、**没有共享 `.cap`**。冒烟测试中我把它写成 `.cap` 触发 R4，已固化为警示。

**回归测试**（在 5 个 demo 上跑过 validator）
- `demo-smoke-editorial-travel`：5 pass / 0 fail / 0 warn
- `demo-zzz-miyabi`：4 pass / 1 R5 warn（p4 H-Bar 底部 trailing whitespace，可接受）
- `demo-eyeliner`：3 pass / 1 R5 warn（xhs-03 底带 13%，可接受）
- `smoke-ai-tools`：发现一处历史 R1 overflow（xhs-03 +101px）—— 之前漏看，已暴露
- `demo-showcase/swiss.html` + `editorial.html`：共暴露 39 + 46 条 R4/R5/R6 warn，主要是 lead/body/caption 在数据密集页里被压到 18-22px、几条 h-statement 行数超 cap。下一轮可逐张回修。

**未做（同短期规划清单）**
1. 截图美化资产 port（下一件）
2. 地图组件
3. 1:1 短标题生成器
4. 多平台一次出包

### v0.10 · 2026-05-26

主题：**内容密度硬规则 + 风格-内容类型解绑 + 全版式 showcase**。两件事并轨：(1) 解决 3:4 卡内容欠填——把"杂志留白逻辑"和"社交卡逐张刷"这两件事在文档层面拆清；(2) 把"Editorial 只接生活类 / Swiss 只接产品类"的隐含绑定明确解除，让用户用同一篇内容自由选风格。

**新增**
- `demo-eyeliner/` — 4 张 3:4 美妆类 Editorial 案例（kraft-paper），P1 M16 全幅产品图，P2 M03 翻车叙事，P3 M01 双头机制，P4 M07 收束。验证密度规则在"图+文+ledger"混合卡上的落地。
- `demo-showcase-editorial/` — 16 张 1080×1440 案例，逐张覆盖 M01-M16，循环用 5 套杂志 palette。
- `demo-showcase-swiss/` — 12 张 1080×1440 案例，逐张覆盖 S01-S12，循环用 4 套 Swiss accent。两套 showcase 演示「内容类型与风格脱钩」——美食内容也可走 Swiss、AI 工具也可走 Editorial。

**修改**
- `SKILL.md` Step 3 重写："Editorial 用于 / Swiss 用于" 的硬绑定改成"两种风格是 visual stance，不是 content category；任何内容都可走任一风格；选风格按 editorial intent（feature story 还是 release note），不按 topic lookup。"
- `SKILL.md` Non-Negotiables 增加 **3:4 内容密度硬规则**：内容 ≥75% 画布高度；>15% 空白带需"留白理由"；禁用 `<div style="flex: 1"></div>` 上下夹击；杂志留白逻辑不适用于社交卡。
- `references/style-system.md` 顶部增 **"Style ↔ content type are decoupled"** 段；Mode A / Mode B 的"Use for"硬列表改成"Good fits"软建议（含明确反例：workplace essay 也可走 Editorial、travel ledger 也可走 Swiss）。
- `references/layout-recipes.md`：
  - Editorial 和 Swiss recipe 总览段都加了"这些结构适用于任何 topic"的前言。
  - **新增 Content density rule (hard)** 段，写在 Editorial recipe 列表前。
  - **M03 / M04 / M07 / M13 各加 Minimum density 段**：
    - M03：title + 3 段 或 title + 2 段 + 编号 footer 列表。title 单独是 M04 不是 M03。
    - M04：唯一允许 ≤60% 内容的 recipe，但必须有 3 个 anchor（source row / kicker / hairline）。
    - M07：title + ≥4 ledger items with sub-lines + closing block。"3 短 ledger 行"是已知失败模式。
    - M13：必备 anchor（top kicker + bottom prompt + bottom metadata + 可见 WebGL bg）。
- `references/qa-checklist.md`：新增 **4 横带密度检查**（Filled / Justified empty / Under-filled 分类，>15% 画布的 under-filled 带判失败；通过门槛：Filled+Justified ≥100%、Filled ≥75%、相邻两带不能都 justified）。
- `PRODUCT.md` 决策 1 重写：风格不再绑定内容品类。删除"Editorial × 人文/Swiss × 工程"的硬对应；明确"两种风格是视觉立场不是内容门"。

**修复路径（落到代码）**
- 杂志留白逻辑→社交卡的对策：用 `flex-grow: 1` + `justify-content: space-between` 让"内容块本身撑开"，禁用空 `<div style="flex:1"></div>` 作为占位。`demo-eyeliner` P2/P4 两次重渲染后通过 4 横带检查。
- M07 spec 从 "2-3 rules" 改成 "4-6 ledger items with sub-lines"，每行 100-140px 垂直。

**未变动**
- Recipe 计数：仍是 M01-M16 + S01-S12 = 28 个。
- Theme palette 计数：5 + 4 = 9 套。
- 工作流步数仍 7 步；种子模板未动。
- 小红书品类能力圈未变；只是不再把"风格-品类"硬绑定。

**对比验证**
- 旧 P2/P4（v0.9 demo-eyeliner 第一版）：内容散在画布中段，底部 25%-35% 是空白带，缩略图刷过去像 PPT 漏排。
- 新 P2/P4（v0.10）：内容撑满到 ~92% 画布高度，引语+footer 紧贴底缘；FALLOUT 四条 a-d 均匀分布到引语之上。
- showcase 两套：28 个 recipe 每个出 1 张 1080×1440，全部通过 4 横带检查、覆盖全 9 调色、内容类型故意打散（户外 / AI / 食谱 / 数据 / 节气 / 影评等都既出现在 Editorial 也出现在 Swiss）。

### v0.9 · 2026-05-26

主题：**M16 编辑杂志化重写**。v0.8 的 M16 借了"游戏 key art"那套（全幅黑色 vertical falloff + 132px 黑体感字 + 纯白文字），出来像旅游海报、不像 Kinfolk / Cereal。本轮按 8 张参考的共同语言推倒重来：**选图是主要杠杆，遮罩是兜底；字小、字细、字色偏纸**。

**修改**
- `references/image-overlay.md` **Rule 1 重写**："选图先，遮罩后，禁全幅 falloff"四步法。Step 1 选图门（quiet-zone 测试 + light 测试），Step 2 默认不加 mask 直接验，Step 3 兜底用 localized + image-toned 软渐变（peak α ≤ 0.30），Step 4 缩略图测试。Banned 段把"全幅 vertical falloff"和"纯黑 mask 色"明确点名禁掉。
- `references/image-overlay.md` Rule 2 增 **Composition discipline** 段：4 条编辑杂志构图硬规（非对称、≥60% 留白、标题只占一个 quiet zone、绝不跨越主体轮廓）。
- `references/image-overlay.md` Checklist 重写：把"≥60% → 强制 mask"翻成"先看选图通不通过，mask 仅在 Step 4 失败时"。
- `references/layout-recipes.md` **M16 重写**：
  - 顶部加 **Photo qualification gate**——quiet-zone test + light test 都过才能走 M16，否则退回 M01。
  - Modes A/B/C/D 表里"Mask"列改成"Tint (only if Step 4 fails)"，明确"先无 mask、不行再局部图调色软渐变"。
  - **字号字色字体彻底改**：标题 88-108px（v0.8 是 108-132px），Noto Serif SC 400-500（v0.8 没限制字重，default 700+），色 `#f5f1e8` 纸米（v0.8 是 var(--paper) 容易跑成 #fff），letter-spacing 0.10-0.15em。
  - 加 **Forbidden in M16 type** 段：禁 inline 700+ 字重、禁纯白 `#fff`、禁标题 > 120px、禁拉丁 sans 作主标题、禁 D 模式标题居中。
  - HTML skeleton 两段重写：默认不加 mask、用 `Noto Serif SC weight 500 + #f5f1e8`，全 inline 字号（不依赖 `.h-display`，因为种子模板 `.h-display` 强制 `color: var(--ink)` 在暗背景上失效）。
- `demo-m16-camping/index.html` **P1 封面重做**：Mode A（顶 kicker + 底标题），无全幅 mask，仅底部 22%x84% 苔绿色 (`rgba(20,32,22,...)`) 软 radial 在标题区，标题 92px Noto Serif SC weight 500 / tracking 0.12em / 色 `#f5f1e8`。HTML 注释加 photo qualification 记录。

**未变动**
- Editorial recipe 计数：仍是 M01-M16。
- Swiss recipe / theme / 工作流 / 种子模板没动。
- demo-m16-camping P2 (ledger) / P3 (field notes) 没动——它们用的是 M05/M02 文字主导版式，没踩 M16 的坑。

**对比验证**
- 旧 P1（v0.8）：黑色 78% 侧栏 scrim + 132px 字 + 整页压重。读起来像旅游打卡。
- 新 P1（v0.9）：林荫透出、字小克制、底部苔绿软渐渍只够支撑标题对比。读起来像 Kinfolk camping 专题。
- 缩略图测试（360px wide）：标题 / kicker / 底部 mono strip 全部可读，无糊。

### v0.8 · 2026-05-26

主题：**图先序列 + 满图封面 recipe**。给"生活化、吃图片"的内容一个明确的 P1 图占满 + P2-3 数据的版式。

**新增**
- `references/layout-recipes.md` 新增 **M16 Image-Led Cover (Full-Bleed Hero)**：填补 M01"图+文分块"到"图占满 + 文字压上"的空白。含 4 种文字位置（顶压底沉 / 侧栏立柱 / 角落徽章 / 下沉条带，按 subject map 选）、字号 spec 表、标题长度上限表、1:1 / 21:9 适配、两段完整 HTML 骨架（Mode A + Mode D）。Editorial 模式专用，Swiss 不用。Editorial recipe 数：15 → 16。
- `references/content-planning.md` 新增 **Image-Led Sequence** 段：P1 M16 满图 → P2 文字数据卡（S11/M05/M10）→ P3 M02 副图 → … 硬规则"绝不连续两张 M16"。

**修改**
- `references/category-cookbook.md`：
  - 旅行 / 家居 / 美食（食谱）路由首选改为 M16 → S11/数据卡 → M02 的图先序列。
  - 情感不强推图先序列（M16 不适合 quiet documentary 的 "窗 / 椅 / 手"——继续走 M04/M09/M11 文字主导）。
- `SKILL.md` Seed 引用范围 `M01-M15` → `M01-M16`。

**未变动**
- 工作流步数与种子模板。
- Swiss 12 个 recipe / 9 套 palette / image-overlay 规则。
- M16 强依赖既有 image-overlay.md（mask + subject map + crop），没有放宽任何安全检查。

### v0.7 · 2026-05-26（上一轮）

主题：**Editorial 图源扩展**。没有新增 layout，没有改种子。

**修改**
- `SKILL.md` Step 6 web-sourced 图源从 3 条扩到 5 条：新增 **Pexels**（#2，原生支持中文搜索，补 Unsplash 在国内场景的缺口）与 **Flickr CC**（#3，`license=2,3,4,5,6,9` 过滤，补"纪实感真实感"的缺口）。加了取图顺序：Pexels（中文/国内）→ Unsplash → Flickr CC（纪实）→ direct search。明确"全部免版权图库，不接 视觉中国 / Getty / 站酷海洛 等付费源"。Swiss 模式继续不走图库。
- `references/category-cookbook.md`：
  - 旅行：Pexels 优先用于国内目的地（中文关键词）；Flickr CC 用于"真实旅程"的纪实质感。
  - 美食：Pexels 用于中式菜品 / 国内餐厅；Unsplash 标注为"西式 stock 感"，仅做兜底。
  - 情感：Flickr CC 升为主推（窗 / 椅 / 手这类纪实质感最契合）；Pexels 补国内语境；Unsplash 仍然可用但避开"moodcore" gradient-light stock。
- `references/qa-checklist.md`：图源 checklist 改为枚举 Pexels / Unsplash / Flickr CC / Wallhaven / direct search，并提示 Flickr CC 在用户同意标注时要保留作者署名。

**新增**
- `SKILL.md` Step 1 Intake 加"三选一"入口：用户只给文字时问一次（A. 自己提供照片 推荐 / B. 我帮你找 / C. AI 生成），按选择走 Step 6，不二次劝导。
- `SKILL.md` Step 6 "When the user has no images" 改为依赖 Step 1 gate，明确不再自动跳到 B/C。

**未变动**
- 工作流步数（仍为 7 步，三选一是 Step 1 内的子项）与种子模板。
- 9 套 palette 与 27 个 recipe。
- 不接付费图库（视觉中国 / Getty）是产品边界（见 `PRODUCT.md` 决策 4）。

### v0.6 · 2026-05-26（上一轮）

主题：**冒烟测试 + 种子模板硬化**。没有新增 layout，没有改工作流。

**新增**
- `smoke-ai-tools/` — 完整冒烟测试样例。主题"程序员一周 AI 工具使用周记"。用 5 张 1080×1440 跑通 S01/S07/S10/S11/S12 五个 recipe。Klein Blue accent。
- `HANDOFF.md` · `PRODUCT.md`（本文件 + 产品文档）

**修改**
- `assets/template-swiss-card.html`：为 `.poster.xhs` 补全 display 字号 override。
  - `.h-hero` 168px · `.h-statement` 124px · `.h-xl` 96px · `.num-mega` 168px · `.num-xl` 120px
  - `.matrix-fill` 改为 `grid-auto-rows: min-content`、`min-height: 0`、`gap: var(--sp-4)`
  - `.matrix-cell .cell-title` 在 xhs 下降到 24px
  - `.hero-stat-bottom` 在 xhs 下紧凑 margin / padding，`num-mega` 128px
- `references/components.md`：新增两张实测对照表。
  - "Swiss `.h-xl` Hard Caps per Board" — 每板最多几行、单行最多几字、超限会怎样
  - "Matrix + hero-stat-bottom on `.poster.xhs`" — cell 数对应的搭配建议

**已知遗留**
- Editorial 模板（`template-editorial-card.html`）**未做同样的冒烟测试**。M14 纵向 Pipeline 5 步在 3:4 板上是否溢出，未验证。
- 冒烟测试只覆盖 5 个 Swiss recipe（S01/S07/S10/S11/S12）。其余 S02-S06/S08/S09 未跑过真实小例子。

### v0.5 · 2026-05-26 早些时候

主题：**image-overlay 规则上线 + 文字压图验证**。

**新增**
- `references/image-overlay.md` — 文字压图规则：蒙版强制 + 多模态主体映射 + 缩略图测试
- `demo-image-02-wukong/` — 用《黑神话:悟空》主视觉验证 image-overlay 规则

**修改**
- `SKILL.md` Step 6 加入文字压图小节
- `references/style-system.md` 新增 Anti-Pattern D（封面照片裸跑无蒙版）

### v0.4 · 之前

主题：**模板种子 + 9 个新版式补全**（对齐已成熟的 PPT skill）。

**新增**
- `assets/template-editorial-card.html` · `assets/template-swiss-card.html` 两份种子
- `references/components.md` 字体/卡片/间距/Lucide token 表
- `layout-recipes.md` 加 M12-M15（Editorial 缺失节奏型）+ S08-S12（Swiss 5 个 port from PPT）

**修改**
- `SKILL.md` 加 Step 4.5（拷贝种子模板）
- Required References 加 `components.md`

### v0.3 及更早

`SKILL.md` + 9 份 reference 的初始体系。小红书品类能力圈（`category-cookbook.md`）。Editorial 11 个版式 + Swiss 7 个版式。

---

## 4. 如何验证 skill 还能跑

在 skill 目录下跑文档层验证：

```bash
cd <skill-dir>          # 本副本位于 <repo>/.agents/skills/xhs-card-skill/
npm run test:docs       # 文档约束测试，当前 36 条
```

`check-skill-docs.mjs` 检查规则是否还在：根目录不产出、版式节奏、技术品类路由、Live Photo 拼图、M16 图片压字、可见文案、发布提醒、产品复盘和交接记录。改了 `SKILL.md` / `references/` 的措辞后必须重跑——字符串断言会直接失败。`SKILL.md` 的 frontmatter / name / description 合法性属基础结构检查，不在这个脚本范围内。

渲染层与版式回归用 `local-tests/` 里现成的技术文章任务目录，不要新开目录：

```bash
cd <skill-dir>
node validate-social-deck.mjs local-tests/tailscale-force-443
node validate-social-deck.mjs local-tests/clash-tailscale-502
```

输出应包含 `shapes:` 形状序列行（R1-R10 全套检查）。退出码只在 FAIL 时非 0，R10 是 advisory。

尺寸核对与视觉验收：

```bash
sips -g pixelWidth -g pixelHeight local-tests/<slug>/output/*.png   # 3:4 应为 1080×1440
```

用 `Read` 工具直接读 `output/*.png` 做视觉验收。

---

## 5. 工作流要点（给接手者的极简版）

按 `SKILL.md` 第 7 步走，**不要跳步**。最易踩坑的两步：

- **Step 3 不要混风格**：Editorial 用 serif + ledger/marginalia；Swiss 用 Inter + card-fill/matrix。两套 class 命名空间隔离，模板间不能跨用。
- **Step 4 先定节奏再写 HTML**：逐页声明 recipe；同一 recipe ≤2 次；相邻两页不同形状族；7+ 页至少 5 种形状。计划里 `h-xl` 出现在超过两页，就是没排好——先改计划，别改图。
- **Step 4.5 必须拷种子**：从零写 HTML 是 v0.4 之前的工作流，已废弃。这一条也是版式趋同的防线：种子里的 recipe 类名是校验器和 R10 能识别版式的前提，自己发明类名（`.tk-*` / `.ws-*` 之类）会让整套卡片脱离规则体系。改种子模板时，**每个补丁都要写进文件头部的 Local fork notice**（已 5 条），否则下次同步会被静默覆盖。
- **欠填先查 `.grow`，不要先加装饰**：grid 容器会拉伸子项，flex 列不会。Swiss 页欠填通常是少了 `.grow`（grid 容器一个、flex 行每行一个），不是内容不够。只有 `.grow` 补过仍欠填，才去加真实内容或换 recipe。

文字压图的页（封面 / 大图位 / 全幅图）四步：先 `Read` 图、写 HTML 注释记录主体位置、选 `object-position`、做 360px 缩略图检查。默认先不加遮罩；只有缩略图不可读时才加局部、取自图片色调的 tint。

Live Photo 页先当静态社交卡做：先确认第一帧、裁切、字体、可见文案和模板匹配，再渲 MOV / `.pvt`。单视频加字走 M16 / image-overlay；二宫格、三宫格、四宫格默认无字；三连拼图只放能并行理解的三个短片段。

---

## 6. 已知风险与限制（事实层面）

- **校验脚本是按需兜底**。`validate-social-deck.mjs` 已可检查溢出、页脚碰撞、字号下限、密度、标题行数、figure 默认 margin 漂移与整套版式节奏（R10）；默认交付流先给用户看，用户要求 auto-check 时再跑。
- **R10 是启发式的**。形状族按结构识别（编号行不看类名），`text` 族偏粗，相邻同族的 WARN 有误报可能。它是 advisory，不影响退出码；当成提示读，不要当成判决。
- **没有跨平台一次出包工具**。一篇文章要同时出小红书 3:4 + 微信 21:9+1:1 + 方封面 1:1，目前要手工开两个任务文件夹。
- **品类能力圈未硬编码**。`category-cookbook.md` 写明了 4 个不接的品类，但 skill 不会主动拒绝；需要执行者在 Step 1 自己读到并主动告知用户。
- **图片来源签名机制是"用户决定"**。`SOURCES.md` 总会写，但是否在图上加标注由用户回答。这不是技术限制是产品决策（见 `PRODUCT.md`）。
- **Live Photo 高光选择只是低成本诊断**。默认只做稀疏抽帧 / contact sheet / 时间段建议，不承诺精准识别转场、空镜、黑场或最佳高光。
- **Live Photo 发布依赖手机端路径**。交付时必须提醒小红书 `5s`、微信公众号 `3s`，并提醒 `.pvt` AirDrop 到 iPhone 后从对应 App / 手机端编辑路径发布。

---

## 7. 不要做的事

- 不要改其他 skill 的本体、模板或 reference。本 skill **独立维护**。
- 不要把 `local-tests/` 里的任务目录删掉来"清理"。它们是回归测试样本。
- 不要在两份种子模板里塞动效系统、键盘翻页、ESC 索引——那是演示文稿的需求，社交卡是静态 PNG。
- 不要把 components.md 的硬上限表"调宽"以接更长的标题。先压文案。
- 不要把内部制作词写到卡面上，例如 `三连拼图`、`5 秒看完`、`信息量`、`Live Photo`、`长视频处理`。这些词只能出现在计划、文件名、QA 或交付说明里。
- 不要用自造背景色、线条、刻度、标签、徽章去"补排版"。先找既有模板和 reference；没有规则支持就不要加。
