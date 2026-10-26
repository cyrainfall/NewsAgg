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
    lines.append("1. 必须有一个明确的核心判断，并在结尾给出锐利但不极端的收束。")
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
    lines.append("   正例：`降的到底是售价，还是成本`、`我的结论，和什么情况下我会改口`")
    lines.append("8. **核心判断只出现在前 1/3，且只下一遍结论**。不要写「我原来怎么想 →")
    lines.append("   如何修正 → 现在怎么想」的演化叙事；读者要的是结论，不是你的思考过程。")
    lines.append("   若确需保留谨慎，只在文末并列**一条**边界声明，不得分散地在多处自我稀释。")
    lines.append("9. **抽象数字必须给可执行换算**：说「成本降到六成」就要给出")
    lines.append("   「过去每月推理花 1000 元，现在大约 600 元」这类读者能对号入座的口径，")
    lines.append("   并紧跟一行边界说明（是换算还是实测、前提是什么）。")
    lines.append("10. 至少给一个具体场景（谁在什么情况下会这样用/受影响），")
    lines.append("    不能通篇只有抽象名词。")
    lines.append("")
    lines.append("### 钱落点（2026-10-21 改赛道，ADR-0050）")
    lines.append("11. **全文必须回答一个问题：这件事跟读者的钱或工作处境有什么关系。**")
    lines.append("    答案是**具体到角色**的，不是「对行业有影响」：")
    lines.append("    反例：`这标志着 AI 行业进入新阶段`（谁的钱/工作受影响了？没有答案）")
    lines.append("    正例：`接外包的前端，这类单子的报价这半年在被压`")
    lines.append("12. **技术细节是证据，不是主题。** 允许、而且鼓励写模型参数、API 计费、")
    lines.append("    迁移成本这些硬细节——但它们的作用是**支撑钱的判断**，不能变成")
    lines.append("    技术综述。判据：删掉技术段落，钱落点的结论还站得住吗？")
    lines.append("    站不住 = 技术是主题（违反本方向）；站得住 = 技术是证据（正确）。")
    lines.append("13. 读者是「正在为钱或工作处境焦虑的技术人」。不要写给「想了解技术进展的人」")
    lines.append("    ——后者在头条推荐流里根本不会点开（站内搜 Codex/MCP 零相关结果）。")
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
