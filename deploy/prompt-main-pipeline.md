NewsAgg 每日主流程（21:00，全自动无人值守）。工作目录：/opt/newsagg/repo

按顺序执行三段，中途需要你做两处**模型判断**（这是设计的一部分：技能装判断，脚本装确定性，ADR-0025）。

═══ 第 1 段：采集与分拣准备 ═══
cd /opt/newsagg/repo
bash .agents/skills/newsagg-collect/scripts/run_all.sh --no-collect --publish

（白天 08/13/18 已采过三次并累积到同一个候选池，故 --no-collect；若它报告"候选池不存在，补采一次"，那是自愈，照常继续。）
本段会跑：git pull --rebase（冲突即熔断，不要自行合并）、风格学习、表现数据读回与降频判定、分拣输入包组装。若它是"降频跳过日"会直接结束——那是设计行为，报告一句即可。

═══ 第 2 段：分拣判断（模型做）═══
读 .newsagg/triage/<今天>-input.json，按 newsagg-triage 技能（.agents/skills/newsagg-triage/SKILL.md）的硬规则产出决策，写入 .newsagg/triage/<今天>-decision.json。

要点：冷却期优先于排序；历史表现只作排序参考不作打分；action=new 时你必须**新建观点卡** knowledge/cards/<file>.md（五节结构：当前判断 / 依据事实 / 修订史 / 可接住的事件类型 / 边界与反方）并把文件名填进 card_file；允许弃权（无值得写的东西就弃权，这是合法结果）。
写完跑校验，必须通过：
/opt/newsagg/repo/.uv-tools/wewrite/bin/python .agents/skills/newsagg-triage/scripts/triage.py --validate .newsagg/triage/<今天>-decision.json

**赛道已于 2026-10-21 改为「AI 变现与开发者职业」（ADR-0050，读 docs/adr/0050-track-pivots-to-ai-monetization-keeping-technical-depth.md）。** 排序时优先选能回答「这跟读者的钱或工作处境有什么关系」的聚簇——但这是**排序偏好不是硬规则**，不要因为某天只有纯技术新闻就系统性弃权。技术细节是证据，不是主题。

═══ 第 3 段：写作 → 封面 → 发布 → 写回 → 简报 ═══
bash .agents/skills/newsagg-collect/scripts/run_all.sh --after-triage --publish

它会生成选题包 .newsagg/briefs/<今天>.md。写作模型未配置时 wewrite 返回退出码 3，此时**由你按选题包自写**成稿到 outputs/<slug>.md（slug 形如 <今天>-<短标题>，首行一级标题 2~30 字）。
写作务必遵守选题包里的标题与结构要求，尤其：标题须含**明确对象 + 读者利益点**；开篇第一段直接给利益/判断，不写元叙事；H2 用读者利益句；核心判断只在前 1/3 出现一次；抽象数字给可执行的换算并紧跟边界说明；至少一个具体场景。

**新方向的硬要求（ADR-0050）：全文必须回答「这件事跟读者的钱或工作处境有什么关系」，答案要具体到角色（「接外包的前端」「做小程序的独立开发者」），不是「对行业有影响」。技术细节鼓励写，但判据是：删掉技术段落，钱落点的结论还站得住吗？站不住 = 技术变成了主题，要改。**
读者是**第一次见到这个话题的人**——头条是推荐流，无关注关系、无前置上下文。

写完后继续跑：
bash .agents/skills/newsagg-collect/scripts/run_all.sh --after-write --publish

本段会做：封面（模板拼图，本地 Pillow）、发布到今日头条（真发）、发布成功后认知写回观点卡（带未复核标记）、生成简报 reports/<今天>.md、commit + push。

═══ 完成后报告 ═══
用 5-10 行汇总：分拣结论（写了什么/为什么弃权）、标题、发布结果（verdict）、写回是否成功、简报路径、以及任何熔断或异常。
注意：任何失败都不要"自行重试发布"——发布绝不自动重试（重复发文是平台明令禁止的）。失败时报告原文与简报 ⑤ 段的内容即可。

═══ 本机与服务器的区别（2026-10-27 起，ADR-0077）═══
- 工作目录是 **/opt/newsagg/repo**（不是 /Users/cyx/repo/NewsAgg）。所有相对路径都相对于它。
- 解释器用 **.uv-tools/wewrite/bin/python**（3.12），不是系统的 python3（服务器系统 python3 是 3.6.8，跑不了本项目的脚本）。
- 头条 cookie 在 **.newsagg/toutiao-cookies.json**（编排器会自动指过去）。它约 23 天有效，过期前简报第 ⑤ 段会提醒重新导出。
- 浏览器登录态在 toutiao-ops 的 Playwright profile 里（ADR-0075 拷过去的），也约 23 天有效。过期时发布路径会报未登录。
- **服务器上没有代理**：`r.jina.ai` 直连不通，所以 Jina Reader 渠道是坏的（ADR-0078 已声明禁用）。不要用 Jina 取正文；用本机的 collect.py 已落盘的候选摘要与掘金/36kr 等国内源。
- 写作模型（wewrite 的 writer key）没配置 → llm-write 退出码 3 → **由你自写**，这是设计内的路径，不是错误。
