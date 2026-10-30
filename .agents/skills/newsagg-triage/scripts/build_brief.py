#!/usr/bin/env python3
"""选题包生成：把分拣决策 + 观点卡 拼成 wewrite 可用的 brief（NewsAgg → wewrite 的接口）。

设计依据：
- Q46 / ADR-0004：边界取"只要成稿"，接口是「一个字符串 + 一篇卡」
- ADR-0026 循环 A：文章必须**引用或推进**卡中判断，不得凭空另起
- llm-write 的 brief 契约：选题 / 框架 / 真实素材锚点 / 人格 / 目标字数

本脚本只做拼接（确定性工作），写作与判断由 wewrite / 编排 agent 完成。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_cardfm():
    """加载同目录的 `cardfm`（观点卡 frontmatter 的唯一解析实现，ADR-0042）。

    用 sys.path 注入而不是直接 `import cardfm`：本脚本既可能被 `python3 <路径>`
    直接执行（脚本目录自动在 sys.path 上），也可能被 importlib 按路径加载
    （那时脚本目录不在 sys.path 上）。两种方式都要能工作。
    """
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import cardfm
    return cardfm


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """委托给同目录 `cardfm`（ADR-0042：三份拷贝漂移过一次，只留一份实现）。"""
    fm, body, _ = _load_cardfm().parse_frontmatter(text)
    return fm, body


def _load_title_rules():
    """加载发布层的 `title_rules`（标题规则的唯一实现，ADR-0069）。

    规则本体在 `platforms/title-rules.md`（归本人，ADR-0018），判定逻辑在发布层。
    这里不复制一份——ADR-0042 的教训：契约的两份实现会漂移，且漂移是静默的。
    """
    import importlib.util
    path = repo_root() / ".agents" / "skills" / "newsagg-publish" / "scripts" / "title_rules.py"
    spec = importlib.util.spec_from_file_location("title_rules_shared", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_prose_rules():
    """加载发布层的 `prose_rules`（正文规则的唯一实现）。

    与 `_load_title_rules` 同一套做法：规则本体在 `platforms/prose-rules.md`，
    判定逻辑在发布层。这里只读不复制——ADR-0042 的教训：两份实现会静默漂移。
    """
    import importlib.util
    path = repo_root() / ".agents" / "skills" / "newsagg-publish" / "scripts" / "prose_rules.py"
    spec = importlib.util.spec_from_file_location("prose_rules_shared", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_card(path: Path) -> dict:
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "file": path.name,
        "issue": fm.get("issue") or path.stem,
        "covered_events": fm.get("covered_events") or [],
        "last_written": fm.get("last_written"),
        "body": body.strip(),
    }


def extract_section(body: str, name: str) -> str:
    """从卡片正文中抽取一节（## 名称 ... 到下一个 ## 或文末）。"""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def build_brief(decision: dict, card: dict | None, style: dict, target_words: str) -> str:
    """拼装 brief。素材锚点只放**输入里真实存在**的内容，不编造。"""
    ev = decision.get("event") or {}
    members = ev.get("members") or []
    evidence = ev.get("key_evidence") or []

    lines: list[str] = []
    lines.append("# 写作任务书（由 NewsAgg 自动生成）")
    lines.append("")
    lines.append(f"生成时间：{datetime.now(CST).isoformat()}")
    lines.append("")

    # ── 选题 ──
    lines.append("## 选题")
    if card:
        lines.append(f"**议题**：{card['issue']}")
        lines.append("")
        lines.append(f"**本次事件**：{ev.get('summary', '（未提供）')}")
        lines.append("")
        if decision.get("whats_new"):
            lines.append(f"**推进方向**：{decision['whats_new']}")
            lines.append("")
    else:
        lines.append(f"**议题**：{decision.get('new_issue', '（未提供）')}")
        lines.append("")
        lines.append(f"**本次事件**：{ev.get('summary', '（未提供）')}")
        lines.append("")

    # ── 已有认知（ADR-0026：必须引用或推进，不得另起）──
    if card:
        lines.append("## 已有认知（必须引用或推进，不得另起）")
        cur = extract_section(card["body"], "当前判断")
        if cur:
            lines.append("**当前判断**：")
            lines.append(cur)
            lines.append("")
        facts = extract_section(card["body"], "依据事实")
        if facts:
            lines.append("**已有依据**：")
            lines.append(facts)
            lines.append("")
        if card["covered_events"]:
            lines.append("**已覆盖事件**（不要重复叙述）：")
            for e in card["covered_events"]:
                lines.append(f"- {e}")
            lines.append("")
        boundary = extract_section(card["body"], "边界与反方")
        if boundary:
            lines.append("**边界与反方**（写作时须避免过度断言）：")
            lines.append(boundary)
            lines.append("")

    # ── 钱落点（ADR-0058）──
    # 分拣每天都在决策里写这一段（2026-10-21 起，300+ 字，具体到角色），
    # 但它此前**没有任何消费者**：写作阶段拿不到，简报也不印。等于模型每天
    # 认真做了一次「这事跟谁的什么处境有关」的判断，然后扔掉了。
    # 这一段是「分拣的判断」到「写作的落点」之间唯一的桥，必须显式传过去，
    # 而不是让写作阶段自己从素材里重新猜一遍（那会得到另一套角色划分）。
    money_point = (decision.get("money_point") or "").strip()
    if money_point:
        lines.append("## 钱落点（分拣已判定，写作必须落到这些角色上）")
        lines.append("**这是分拣阶段做的判断，不是建议。文章的钱落点必须与之一致；**")
        lines.append("**若你认为它错了，不要静默改写，而是在正文里给出更准的角色划分并说明理由。**")
        lines.append("")
        lines.append(money_point)
        lines.append("")

    # ── 真实素材锚点（只放输入里真实存在的）──
    lines.append("## 真实素材锚点")
    lines.append("**只使用以下素材。不得补写未提供的数字、引述、案例或个人经历。**")
    lines.append("")
    if evidence:
        for e in evidence:
            lines.append(f"- {e}")
        lines.append("")
    if members:
        lines.append("**报道来源**：")
        for mm in members:
            lines.append(f"- {mm}")
        lines.append("")

    # ── 人格与字数 ──
    lines.append("## 写作要求")
    lines.append(f"- **账号**：{style.get('name', '')}（{style.get('industry', '')}）")
    lines.append(f"- **目标读者**：{style.get('target_audience', '')}")
    lines.append(f"- **语气**：{style.get('tone', '')}")
    lines.append(f"- **人称**：{style.get('voice', '')}")
    lines.append(f"- **人格**：{style.get('writing_persona', '')}")
    lines.append(f"- **目标字数**：{target_words}")
    lines.append("")
    black = style.get("blacklist") or {}
    if black.get("words"):
        lines.append(f"- **禁用词**：{'、'.join(black['words'])}")
    if black.get("topics"):
        lines.append(f"- **禁写话题**：{'、'.join(black['topics'])}")
    lines.append("")
    lines.append("## 内容要求")
    # 2026-10-28 复盘（docs/reviews/2026-10-28-geekpark-model-review.md）改了这一条。
    # 原文是「在结尾给出锐利但不极端的**收束**」——「收束」这个词把结尾往回收的方向推，
    # 12 篇里有 9 篇因此以「什么情况下我会改口」结束，读者读完没有下一步。
    # 收束是对内的（作者把话说圆），行动是对外的（读者能做点什么）。要后者。
    lines.append("1. 必须有一个明确的核心判断（放在前 1/3），**结尾给读者一件今天就能做的事**，")
    lines.append("   不要用「什么情况下我会改口」这类收束当结尾。")
    lines.append("2. 区分事实、推断与个人意见；无法证实的数字不得写入。")
    lines.append("3. 不得编造个人经历（未提供亲历材料时，'我'只能用于表达判断）。")
    lines.append("")
    lines.append("### 标题（头条硬约束）")
    lines.append("4. 标题需在 2~30 字内，口语化、不含禁用词。")
    lines.append("5. **标题必须同时含「明确对象」与「读者利益点」**：")
    lines.append("   - 明确对象 = 具体主体（公司/产品/技术/人群），**不得泛指**。")
    lines.append("     反例：`模型打五折，未必是好事`（「模型」是谁？）")
    lines.append("     正例：`Gemini 4 打五折：做应用的人在省钱，做模型的人在挨打`")
    lines.append("   - 读者利益点 = 读者 1 秒内能判断「这与我何干」。")
    lines.append("     反例：`未必是好事`（悬念式，读者拿不到任何信息）")
    lines.append("   - 直白优于悬念。本账号发布在**推荐流**，读者无前置上下文，")
    lines.append("     标题是唯一的筛选器；悬念式标题在冷启动分发中会被跳过。")
    lines.append("")

    # ── 标题规则（`platforms/title-rules.md`，ADR-0069）──
    # 这里渲染的是**同一份**规则文件，发布门也用它判。不各抄一份。
    tr = _load_title_rules()
    rules = tr.load_rules(repo_root())
    if not rules.get("loaded"):
        lines.append("> ⚠️ 标题规则文件读不上（发布门会按平台硬长度限制放行）："
                     f"{rules.get('error')}")
        lines.append("")
    else:
        hard = rules["hard"]
        observe = rules.get("observe") or {}
        lines.append("### 标题规则（`platforms/title-rules.md`，发布门会按同一条拒发）")
        lines.append("")
        lines.append("**硬下限（不满足会被拒发，平台审核会把夸张标题退回、")
        lines.append("而退回的文章再改也不会被推荐）**：")
        if hard.get("forbid_words"):
            lines.append(f"- 不得出现这些词：{'、'.join(hard['forbid_words'])}")
        if hard.get("forbid_ending_chars"):
            lines.append(f"- 不得以这些字符结尾：{' '.join(hard['forbid_ending_chars'])}")
        lines.append(f"- 长度 {hard.get('min_len')}~{hard.get('max_len')} 字")
        lines.append("")
        if observe:
            lines.append("**观察项（不拦发，但下次要拿它们检验）：**")
            landing = observe.get("reader_side_landing") or {}
            if landing:
                lines.append(f"- {landing.get('desc','')}")
                if landing.get("role_markers"):
                    lines.append(f"  角色词示例：{'、'.join(landing['role_markers'][:8])}…")
                if landing.get("anti_markers"):
                    lines.append(f"  这些是**反面**信号（我们的 0 阅读文章都收在它们上）："
                                 f"{'、'.join(landing['anti_markers'])}")
            contrast = observe.get("contrast_shape") or {}
            if contrast:
                lines.append(f"- {contrast.get('desc','')}")
            number = observe.get("concrete_number") or {}
            if number:
                lines.append(f"- {number.get('desc','')}")
            lines.append("")
            lines.append("> 这些是**待检验的假设**（样本 n=10，展现量与账号权重共线），")
            lines.append("> 所以它们只打标签、不拦发。但写标题时请尽量命中——")
            lines.append("> 下一次我们靠它们判断「哪一条值得升为硬规则」。")
            lines.append("")
    lines.append("### 结构（读者视角，非作者视角）")
    lines.append("6. **开头第一段就给读者利益或核心判断**，不要用元叙事铺垫")
    lines.append("   （「按惯例，这类发布有两种读法」「过去我倾向于」）。")
    lines.append("7. **H2 小标题必须是读者利益句，不是作者思路节点**：")
    lines.append("   反例：`先看这次到底降了什么`、`我怎么看这一轮的定性`")
    lines.append("   正例：`降的到底是售价，还是成本`、`省下来的时间，为什么没回到你手上`")
    lines.append("   （曾经的「正例」`我的结论，和什么情况下我会改口` 已撤——它被 6 篇原样复用，")
    lines.append("   从差异化变成了套路，且把最后一节固定成自我限定。见第 1 条与第 14 条。）")
    lines.append("8. **核心判断只出现在前 1/3，且只下一遍结论**。不要写「我原来怎么想 →")
    lines.append("   如何修正 → 现在怎么想」的演化叙事；读者要的是结论，不是你的思考过程。")
    lines.append("   若确需保留谨慎，只在文末并列**一条**边界声明，不得分散地在多处自我稀释。")
    lines.append("   **唯一例外（第 15 条）**：允许用一句话引用自己上一篇的结论作为证据。")
    lines.append("9. **抽象数字必须给可执行换算**：说「成本降到六成」就要给出")
    lines.append("   「过去每月推理花 1000 元，现在大约 600 元」这类读者能对号入座的口径，")
    lines.append("   并紧跟一行边界说明（是换算还是实测、前提是什么）。")
    lines.append("10. 至少给一个具体场景（谁在什么情况下会这样用/受影响），")
    lines.append("    不能通篇只有抽象名词。")
    lines.append("")
    lines.append("### 反差句要给对照（2026-10-28 复盘新增）")
    lines.append("11. **写「不是 X，是 Y」时，X 必须是「多数人会怎么读」，不能是稻草人。**")
    lines.append("    反例：`但这不是重点，重点是计价单位换了`（谁以为重点在别处？没说）")
    lines.append("    正例：`热榜上的读法是「DeepSeek 又降价了」。重点不是降价，是计价单位换了`")
    lines.append("    判据：读者读完 X 应该想「对，我本来也这么想」，而不是「谁这么想？」")
    lines.append("    （全库 38 处反转句，绝大多数缺 X 的来源，导致非共识藏成了暗示。）")
    lines.append("")
    lines.append("### 纵深（2026-10-28 复盘新增）")
    lines.append("12. **如果要给一个判断，尽量给一段「同样的机制上一次出现在哪」。**")
    lines.append("    不是「十年前大家都还不相信」这种空回望，要具体到一次可类比的迁移：")
    lines.append("    正例见 `2026-10-19` 那篇——「检索增强刚流行时自己搭向量库……后来搜索能力")
    lines.append("    直接进了 API，那段工程就从核心竞争力变成了一行参数。今天是同一件事的第二遍」。")
    lines.append("    判据：这一段应能替换掉一半的解释性段落，而不是再加一段。没有可类比的先例就跳过，")
    lines.append("    不要编——编出来的历史类比比没有更差。")
    lines.append("")
    lines.append("### 收尾（2026-10-28 复盘新增，优先级最高）")
    lines.append("13. **边界声明要在末尾倒数第二段，最后一段必须是行动句。**")
    lines.append("    顺序是「承认不确定 → 但有一件现在就能做的事」，不是反过来。")
    lines.append("    反例（12 篇里 9 篇的形状）：整篇结束在「什么情况下我会改口」。")
    lines.append("    正例见 `2026-10-24` 末段「把返工率记下来，那才是你的账」、")
    lines.append("    `2026-10-25` 末段「先别写代码。找一个你猜会付钱的人，问他上一次遇到这个问题")
    lines.append("    是什么时候、当时花了多少钱」。")
    lines.append("    行动句要**具体到动作**（问谁、记什么、算哪一项），不要「建议持续关注」。")
    lines.append("14. **不要用「Why not」这个英文词，也不要写口号式召唤。**本账号的读者是算术读者，")
    lines.append("    不是信仰读者。第 13 条要的是「一件具体的小事」，不是情绪推力。")
    lines.append("")
    lines.append("### 认知累积（2026-10-28 复盘新增）")
    lines.append("15. **如果这张卡之前写过，允许用一句话引自己上一篇的结论，再说明今天推进了什么。**")
    lines.append("    这是第 8 条的唯一例外，且严格限一句：")
    lines.append("    正例：`两个月前我写这张卡时说过「被压缩的是编码环节」。今天这条证据要往后挪半格：")
    lines.append("    被压缩的是编码环节的**定价**，不是环节本身。`")
    lines.append("    反例：整段复盘自己怎么想的。读者要的是「你上次说错了什么」这一个信息点，")
    lines.append("    不是你的思考日志。判据：这句引用能不能被删掉而不损失读者的行动依据？")
    lines.append("    能删 = 不要写。")
    lines.append("")
    lines.append("### 钱落点（2026-10-21 改赛道，ADR-0050）")
    lines.append("16. **全文必须回答一个问题：这件事跟读者的钱或工作处境有什么关系。**")
    lines.append("    答案是**具体到角色**的，不是「对行业有影响」：")
    lines.append("    反例：`这标志着 AI 行业进入新阶段`（谁的钱/工作受影响了？没有答案）")
    lines.append("    正例：`接外包的前端，这类单子的报价这半年在被压`")
    lines.append("17. **技术细节是证据，不是主题。** 允许、而且鼓励写模型参数、API 计费、")
    lines.append("    迁移成本这些硬细节——但它们的作用是**支撑钱的判断**，不能变成")
    lines.append("    技术综述。判据：删掉技术段落，钱落点的结论还站得住吗？")
    lines.append("    站不住 = 技术是主题（违反本方向）；站得住 = 技术是证据（正确）。")
    lines.append("18. 读者是「正在为钱或工作处境焦虑的技术人」。不要写给「想了解技术进展的人」")
    lines.append("    ——后者在头条推荐流里根本不会点开（站内搜 Codex/MCP 零相关结果）。")
    lines.append("")
    lines.append("### 开头的追问（2026-10-28 复盘新增）")
    lines.append("19. **开篇可以在事实之前加一个「为什么是它 / 为什么是现在」的设问，")
    lines.append("    但不写人物小传。**")
    lines.append("    正例见 `outputs/2026-10-23-ai-video-cost-savings-captured-by-model-vendors.md:17`")
    lines.append("    的「上游为什么敢涨价」——把它从 H2 提到开篇就是这个写法。")
    lines.append("    判据：这个设问必须由**本篇的数据**回答得掉，否则它只是个钩子，")
    lines.append("    而钩子和推荐流读者之间没有信任可透支（第 6 条仍要求第一段就给判断）。")
    lines.append("    注意这是**设问**，不是「创业者的 Why」（模型 1 / 启发式 1 明确不做，见规则文件第三节）。")
    lines.append("")
    lines.append("### 平台格式（2026-10-31 实测，n=7，`platforms/toutiao-format.md`）")
    lines.append("20. **首段压到 60 字以内，只放一句判断。** 平台样本首段中位 38 字，我们 15 篇中位 97 字。")
    lines.append("    首段的作用是把人从信息流拽进正文，不是交代背景——背景挪到第二段。")
    lines.append("    第 6 条（第一段就给判断）不变，这条只是把它压短：判断在前，理由在后。")
    lines.append("21. **小标题 0~2 个。** 平台 7 篇里 6 篇**完全没有小标题**（唯一有的那篇是 2 个），")
    lines.append("    我们 15 篇中位 5 个。小标题一多，1500 字的稿子会变成汇报材料，而推荐流的读者是竖着滑的。")
    lines.append("    仍要小标题时，按第 7 条写成读者利益句。")
    lines.append("22. **篇幅往短端写。** 目标仍是 1200–1800，但平台正文中位 1504 字、我们中位 2343 字。")
    lines.append("    写到 1800 以内就停，不要为了「讲透」继续堆——删掉技术段落后钱落点仍站得住（第 17 条）。")
    lines.append("23. **不要写「如图」「见下图」这类占位。** 平台正文中位 2 张内文图，而本系统目前**没有**")
    lines.append("    正文插图能力（只有封面，见 `platforms/toutiao-format.md` 的已知缺口），写了就是空指。")
    lines.append("")

    # ── 正文观察项（`platforms/prose-rules.md`）──
    # 只说明"会被记什么"，**不印标记词**。这是刻意的：把「先别」「记下来」这些词
    # 印给写作方，写作方只要在末段塞一个就满足了标签，而标签本该检测结构而不是口令。
    # 标题规则那边可以印（标题短、词表即规则本身），正文这边印了就等于教人作弊。
    lines.append("### 发布后会记下的正文标签（`platforms/prose-rules.md`，不拦发）")
    lines.append("")
    pr = _load_prose_rules()
    prose = pr.load_rules(repo_root())
    if not prose.get("loaded"):
        lines.append(f"> ⚠️ 正文规则文件读不上，本篇发布时不打正文标签：{prose.get('error')}")
    else:
        lines.append("发布时会对成稿正文打四个标签、记进发布账本、次日在简报 ③ 段印出来。")
        lines.append("它们**不拦发**——正文结构没有「拒发」的正确阈值，拦错一次损失一篇产能。")
        lines.append("但它们是上面第 11–15 条是否被执行的唯一证据：")
        for key, spec in (prose.get("tags") or {}).items():
            lines.append(f"- `{key}`：{spec.get('desc','')}")
        lines.append("")
        lines.append("换句话说：**上面那些条不是建议，是第二天会被看到的东西。**")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 wewrite brief（选题包）")
    ap.add_argument("--decision", required=True, help="分拣决策 JSON")
    ap.add_argument("--out", default=None, help="brief 输出路径")
    ap.add_argument("--words", default="1200-1800", help="目标字数")
    args = ap.parse_args()

    root = repo_root()
    raw = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    decision = raw.get("decision", raw)

    # 载入对应卡（continue/skip 时）
    card = None
    cf = decision.get("card_file")
    if cf:
        cpath = root / "knowledge" / "cards" / cf
        if cpath.exists():
            card = load_card(cpath)
        else:
            print(f"警告：决策引用的卡不存在：{cf}", file=sys.stderr)

    # 载入风格（yaml 极简解析，只取需要的标量）
    style: dict = {}
    spath = root / ".wewrite" / "style.yaml"
    if spath.exists():
        txt = spath.read_text(encoding="utf-8")
        for k in ("name", "industry", "target_audience", "tone", "voice", "writing_persona"):
            m = re.search(rf'^{k}:\s*"?([^"\n]+)"?', txt, re.M)
            if m:
                style[k] = m.group(1).strip()

        # 黑名单必须**限定在 blacklist 块内**解析。
        # 注意：顶层还有 content 方向的 `topics:`，若不加限定会误取成"禁写话题"
        # ——那会让写作模型避开我们最想写的方向。
        bl = re.search(r"^blacklist:\s*$((?:\n[ \t]+.*|\n)*)", txt, re.M)
        blk = bl.group(1) if bl else ""
        wm = re.search(r"^[ \t]+words:\s*\n((?:[ \t]+-[ \t]*.+\n)+)", blk, re.M)
        tm = re.search(r"^[ \t]+topics:\s*\n((?:[ \t]+-[ \t]*.+\n)+)", blk, re.M)
        style["blacklist"] = {
            "words": [l.strip()[2:].strip().strip('"') for l in wm.group(1).splitlines()] if wm else [],
            "topics": [l.strip()[2:].strip().strip('"') for l in tm.group(1).splitlines()] if tm else [],
        }

    brief = build_brief(decision, card, style, args.words)

    now = datetime.now(CST)
    out = Path(args.out) if args.out else root / ".newsagg" / "briefs" / f"{now:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(brief, encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out),
        "card": card["file"] if card else None,
        "issue": (card or {}).get("issue") or decision.get("new_issue"),
        "chars": len(brief),
        "evidence_count": len((decision.get("event") or {}).get("key_evidence") or []),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
