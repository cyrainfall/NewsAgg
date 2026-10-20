# 照片层：只从 CC0 / 公有领域取图，许可证必须入库，且只有写作阶段能提出查询

## Context

2026-10-20 发布后，用户提出一个假设：「阅读量没有上来还是因为 cover 不够吸引人」，
并建议改用 `google-images` 技能取真实照片做封面。

我先把两件事分开：**封面是否够吸引人**与**阅读量为什么低**是两个问题。
当日实测数据指向后者才是主要矛盾——四篇文章展现 706 / 248 / 134 / 198，阅读 1 / 0 / 0 / 0，
账号昨日整体展现 417、阅读 0。在 0.1% 量级的点击率上，封面即使做到最好，
也只能把个位数阅读变成两位数，离「获得关注」差两个数量级。

但封面确实有一个**可验证的、独立成立的**问题：它没有给出标题以外的信息。
ADR-0044 已经把「封面承载一个视觉论据」定为规矩，而 2026-10-20 那张封面
是 `Codex搬走Cursor配置` 与 `Codex搬走Cursor配置：/选AI编程工具的标准变了` 的逐字重复。
那部分在第 1 步（提交 `396c723`）已修。

本 ADR 处理第 2 步：**要不要给封面加照片，如果加，照片从哪来。**

## 发现

### 一、`google-images` 技能不能用于本项目

读它的 `SKILL.md` 全文后，三条硬伤，任何一条都足以否决：

1. **它不做版权判断**。交付时只说「images may be copyrighted — the source list lets
   the user check licensing」。而本系统是无人值守的：**没有「用户去查许可证」这个环节**。
   这与 ADR-0013「不使用新闻原图」是同一类风险，但更糟——原图至少知道是谁的，
   这里连来源都要人肉核。忘一次就是一次侵权。
2. **本机不可达**。它的流程要求真浏览器打开
   `https://www.google.com/search?q=...&tbm=isch&tbs=isz:lt,islt:8mp`，
   本机实测返回 **302**（正是技能自己描述的 consent / sorry 场景）。
   云服务器在 cn-shenzhen，更不可达；而走代理又与 ADR-0022「头条流量必须直连」冲突。
3. **目标错位**。它交付的是长边 ≥3840px 的 4K 壁纸。搜「AI」得到的是芯片、
   机器人、蓝色光网这类通用素材——用它们配一篇讲具体事件的文章，等于用装饰替代论据。

### 二、CC0 / 公有领域的供给，比想象中稀薄

用 Wikimedia Commons API 在 10 个**真实话题**上统计尺寸合格（≥1000×560 位图）的候选：

| 查询 | 合格数 | CC0/PD | CC BY（非 SA） | CC BY-SA |
| --- | --- | --- | --- | --- |
| `large language model` | 16 | 1 | 9 | 4 |
| `DeepSeek` | **0** | 0 | 0 | 0 |
| `artificial intelligence model` | 12 | 6 | 3 | 3 |
| `AI coding assistant` | 22 | 1 | 0 | 15 |
| `computer programming` | 11 | 2 | 0 | 9 |
| `developer workstation` | 4 | 0 | 1 | 1 |
| `open source LLM` | 26 | 4 | 11 | 10 |
| `Nvidia H100` | 29 | 0 | 29 | 0 |
| `transformer neural network` | 17 | 0 | 17 | 0 |
| `API server` | 11 | 9 | 2 | 0 |
| **合计** | | **23** | **72** | **42** |

三个结论：

- **CC0/PD 只占 17%**。只取 CC0 会把可选池砍到六分之一，而且相关性更差。
- **最相关的话题反而没有 CC0**。搜 `DeepSeek` 合格 0 条：返回的是
  `DeepSeek chatbot example screenshot (ru).png`、`DeepSeek – VR China und Taiwan, heikel.png`
  （中台地图，政治风险）、《DEEPSEEK锐评江油事件.png》（935×1142，尺寸不足）、
  `DeepSeek-icon.svg`（MIT 许可证）。
- **CC BY 供给多但需要署名**，而署名同样需要「有人记得写」——无人值守没有这个环节。

### 三、照片铺满画布会让文字读不出来

拿 `File:Technician with laptop working on server rack at NERSC.jpg`
（CC0，4288×2848）做三种合成，逐区测亮度：

| 方案 | 整图亮像素 | 整图亮度均值 | kicker 区均值/标准差 | line1 区均值/标准差 |
| --- | --- | --- | --- | --- |
| A 纯模板（无照片） | 12.6% | 35.8 | 30.9 / 43.5 | 80.7 / 107.6 |
| B 照片 + 左黑渐变遮罩 | 28.9% | 84.2 | 75.0 / 50.8 | **119.5 / 90.9** |
| C 照片 + 全幅压暗 86% | — | — | 41.8 / 39.8 | 88.6 / 102.8 |
| D 照片上 62% + 下方实心黑块 | — | 65.1 | — | — |

- **B 最差**：橙色副标题压在格子衬衫上，局部对比度塌陷。看图一眼就能看出文字变糊。
- **C 文字够读，但照片只剩模糊剪影**——花了复杂度，换一张几乎无效的底图。
- **D 是唯一同时成立的方案**：照片完整可见、文字区与无照片封面完全一致。

## Decision

1. **照片只从 Wikimedia Commons 取，且只接受 CC0 / 公有领域。**
   `fetch_photo.py` 的 `FREE_PREFIXES = ("cc0", "public domain", "pd-", "pd ")`、
   `FREE_IDS = {"cc0", "cc-zero", "pd", "publicdomain"}`。
   理由不是 CC BY 不能用，而是**无人值守没有「记得署名」这个环节，忘一次就是违约一次**。

2. **许可证与来源全部写入 `.newsagg/photos/ledger.jsonl`。**
   字段：`at / query / score / hits / file_title / license / license_url / artist /
   credit / descriptionurl / width / height / sha256 / bytes / local /
   candidates_total / candidates_qualified`。
   理由：「我们当时以为它是 CC0」不是抗辩理由，出处必须可回查。

3. **排版用方案 D**：照片占上方 `PHOTO_BAND = 0.50`，中间 `PHOTO_DIVIDER = 6` px 蓝线，
   下方实心 `INK` 黑块承载全部文字。照片**居中裁切、不拉伸**
   （`scale = max(W/src_w, band_h/src_h)` 后 crop），只做 `Brightness(0.94)` 的轻微压暗
   ——文字区已是纯黑，照片带不需要为可读性让路。

4. **只有写作阶段能提出照片查询**：`run_all.sh` 仅在封面 spec 里存在 `photo_query`
   字段时才调 `fetch_photo.py`。
   理由：只有写作阶段知道这篇文章讲的是什么，也只有它能判断
   「机房照片配一篇讲 Codex 迁移的文章」算不算图文无关。
   **没有 query 就说明没人做过这个判断**——此时加一张来路不明的照片，
   只是把「图文无关」变成系统自动决定的。

5. **取不到照片不是错误**：`fetch_photo.py` 取不到也返回退出码 0，
   编排器回退到纯模板封面，但必须向 stderr 打一行
   `注意：未能取到合格照片（查询：…），回退到纯模板封面`。
   回退是设计行为，静默回退不是。

6. **风险词表排除政治与人物题材**：`RISK_WORDS` 含 `map / flag / logo / portrait /
   face / nude / protest / riot / war / military / weapon / soldier / election /
   political / president / minister / parliament / taiwan / tibet / xinjiang /
   hong kong / screenshot / screengrab / meme / trademark / poster / cover art / album`。
   **`china` 故意不在表里**：我们赛道就是中国 AI 公司，列进去会筛掉绝大多数合法候选；
   政治风险由 `map / taiwan / tibet / xinjiang / hong kong / political` 承担
   （实测中那张中台地图就是被 `taiwan` 命中的）。

7. **User-Agent 必须带联系 URL**：`NewsAggBot/1.0 (https://github.com/cyrainfall/NewsAgg) python-urllib`。
   实测 UA 写成 `NewsAgg/1.0 (personal news digest)` 时连续请求返回 **HTTP 429**，
   换成带 URL 的之后恢复 200。

8. **`hero_ratio` 的分母改为文字区高度**（有照片时是 `panel_height`，无照片时等于整图）。
   否则同一个 `≥25%` 阈值会随照片有无而漂移，同一张设计在不同路径下得到不同判定。

## Consequences

- **照片层的实际收益，目前小于它的复杂度。** 实测三个查询成功、两个失败
  （`DeepSeek` 与 `GPU server rack` 均无合格 CC0 候选）。
  最相关的话题往往恰好没有 CC0 素材——这是 Commons 的供给结构，不是选图参数能修的。
  因此照片层应视为**可选增强**，纯模板封面仍是默认形态。

- **「图文无关」仍是未解问题。** 现在只做到「有人提 query 才取图」，
  但 query 与文章的语义相关性没有任何自动检查。若某天写作阶段随手写了个
  泛化的 query（如 `technology`），系统会照单全收。
  这个风险被记录在 `DESIGN.md` 残余风险里，不假装解决了。

- **许可证账本只增不改。** 已下载的照片即使后来发现许可证有问题，
  也不能从账本里删——账本是「当时依据什么做的决定」的记录，删了就失去审计价值。
  要撤回应当在账本里追加一条更正记录。

- **但账本本身不入库。** `.newsagg/photos/ledger.jsonl` 位于 `.newsagg/` 之下，
  而该目录按 ADR-0027 是双环境各自持有的运行时状态、已被 `.gitignore` 排除。
  也就是说**这份审计记录只存在于当前那台机器上**，服务器迁移或清空 `.newsagg/`
  会一并丢失。这是有意的取舍（照片文件本身也不入库，只入库封面成品且其 PNG 亦被排除），
  但必须写明：**它不是一份可用来应对侵权主张的档案，只是一份「当时为什么这么选」的本地日志。**
  真要留档，应当在需要时把账本复制进 `reports/` 冻结。

- **`google-images` 这类「交给用户查版权」的技能，与本项目的前提不相容。**
  凡是需要「有人在发布前看一眼」的环节，都不能进入无人值守链路。
  这条比本 ADR 的具体决定更重要。
