#!/usr/bin/env python3
"""
NewsAgg 工作过程事实提取器。

从仓库里实读每个环节真正执行的代码与它读/写的文件，切片成带行号的摘录，
产出 <viz>/facts/repo-facts.json，供 build_html.py 生成可视化页面。

原则：
- 摘录必须真实来自文件，行号必须真实；anchor 命中失败即报错退出，不静默降级。
- 描述性文字（做了什么 / 为什么）人手-approved，逐条对齐 ADR。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = HERE / "repo-facts.json"
CST = timezone(timedelta(hours=8))
DATE = "2026-10-26"


def die(msg: str):
    print(f"[FATAL] {msg}", file=sys.stderr)
    sys.exit(1)


_CACHE: dict[str, list[str]] = {}


def lines_of(path: str) -> list[str]:
    if path not in _CACHE:
        p = ROOT / path
        if not p.exists():
            die(f"文件不存在：{path}")
        _CACHE[path] = p.read_text(encoding="utf-8").splitlines()
    return _CACHE[path]


def ex(path: str, start: int, end: int | None = None, note: str = "") -> dict:
    """按真实行号切片（1-based, 闭区间）。"""
    ls = lines_of(path)
    if end is None:
        end = start
    end = min(end, len(ls))
    if start < 1 or start > len(ls):
        die(f"行号越界：{path} start={start} (共 {len(ls)} 行)")
    return {
        "path": path,
        "start": start,
        "end": end,
        "note": note,
        "total": len(ls),
        "text": "\n".join(ls[start - 1 : end]),
    }


def at(path: str, anchor: str, before: int = 0, after: int = 0, note: str = "") -> dict:
    """按正则定位首处命中，取上下文。"""
    ls = lines_of(path)
    for i, line in enumerate(ls):
        if anchor in line:
            s = max(1, i + 1 - before)
            e = min(len(ls), i + 1 + after)
            return ex(path, s, e, note)
    die(f"锚点未命中：{path} :: {anchor!r}")


def fn(path: str, defline: str, take: int = 18, note: str = "") -> dict:
    """从 def/函数首行起切一段。"""
    return at(path, defline, before=0, after=take - 1, note=note)


def jsonview(path: str, keys: list[str] | None, note: str = "") -> dict:
    """把 JSON 的指定顶层键渲染成结构化视图（非线性摘录，标注清楚）。"""
    p = ROOT / path
    if not p.exists():
        die(f"文件不存在：{path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if keys is None:
        sub = {k: ("…(%d 项)" % len(v) if isinstance(v, list) else v)
               for k, v in data.items()}
    else:
        sub = {k: data.get(k) for k in keys if k in data}
    return {
        "path": path,
        "start": None,
        "end": None,
        "note": note or "结构化视图（非原文切片）",
        "total": len(p.read_text(encoding="utf-8").splitlines()),
        "text": json.dumps(sub, ensure_ascii=False, indent=2),
    }


def jsonline(path: str, index: int = -1, note: str = "") -> dict:
    """取 JSONL 的某一行（默认最后一行）。"""
    p = ROOT / path
    ls = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    n = len(ls)
    i = index if index >= 0 else n + index
    obj = json.loads(ls[i])
    return {
        "path": path,
        "start": None,
        "end": None,
        "loc": f"第 {i + 1} 行 / 共 {n} 行（JSON 已格式化便于阅读）",
        "note": note,
        "total": n,
        "text": json.dumps(obj, ensure_ascii=False, indent=2),
    }


RUN = ".agents/skills/newsagg-collect/scripts/run_all.sh"
COLLECT = ".agents/skills/newsagg-collect/scripts/collect.py"
TRIAGE = ".agents/skills/newsagg-triage/scripts/triage.py"
CARDFM = ".agents/skills/newsagg-triage/scripts/cardfm.py"
BBRIEF = ".agents/skills/newsagg-triage/scripts/build_brief.py"
WRITEBACK = ".agents/skills/newsagg-triage/scripts/writeback.py"
STYLEL = ".agents/skills/newsagg-triage/scripts/style_learn.py"
STYLEP = ".agents/skills/newsagg-triage/scripts/style_patterns.py"
BRIEF = ".agents/skills/newsagg-brief/scripts/brief.py"
PUB = ".agents/skills/newsagg-publish/scripts/publish.py"
COVER = ".agents/skills/newsagg-publish/scripts/make_cover.py"
PHOTO = ".agents/skills/newsagg-publish/scripts/fetch_photo.py"
PERF = ".agents/skills/newsagg-performance/scripts/performance.py"
WATCHDOG = ".github/workflows/newsagg-watchdog.yml"
TESTW = ".github/workflows/newsagg-tests.yml"

CAND = f".newsagg/candidates/{DATE}.json"
INPUT = f".newsagg/triage/{DATE}-input.json"
DECISION = f".newsagg/triage/{DATE}-decision.json"
BRIEFF = f".newsagg/briefs/{DATE}.md"
REPORT = f"reports/{DATE}.md"
DRAFT = f"outputs/{DATE}-draft.md"
OUTPUT = f"outputs/{DATE}-layoff-reasons-without-ai.md"
COVERSPEC = f".newsagg/covers/{DATE}.json"
LEDGER = ".newsagg/publish-ledger.jsonl"
RUNSTATUS = ".newsagg/run-status.json"
PERFSTATUS = ".newsagg/performance-status.json"
CARD = "knowledge/cards/ai-replacing-developers-misreads-the-work.md"
STYLE = ".wewrite/style.yaml"
NICHE = "platforms/niche.txt"


def build() -> dict:
    nodes = [
        # ── ① ─────────────────────────────────────────────────────────
        {
            "id": "n01-pull",
            "step": "①",
            "title": "git pull --rebase",
            "layer": "orchestrate",
            "when": "21:00 主流程第 1 段开头（续跑段不再 pull）",
            "actor": "脚本",
            "one_liner": "先把你自己机器上改过的东西拉下来，再开始今天的活。",
            "does": [
                "显式按「当前分支」取远端分支，不用裸 git pull（裸命令依赖 upstream，新分支会直接失败）。",
                "远端还没有这个分支时视为正常状态，跳过同步继续跑，收尾用 -u 建到远端。",
                "冲突 → git rebase --abort + 熔断（写 .newsagg/breaker.json）+ fail_soft，绝不自行合并。",
                "传输层抖动（HTTP2 framing 等窄表）重试 3 次；网络不可达（宽表）改为「降级继续、收尾不 push」。",
                "失败时把 git status --porcelain 的前 20 项补进 detail，因为 git 只说「有未暂存变更」不说哪些文件。",
            ],
            "why": [
                "ADR-0018：运行前 pull、运行后 commit+push；冲突熔断且**不自行合并**——自动解决冲突可能静默丢弃你在风格或合规规则上的修改。",
                "ADR-0061：远端不可达 ≠ 工作区脏。前者只要等网络好，后者要人动手；把前者判成后者会让一次代理抖动抹掉整天的产出。",
                "ADR-0065：编排器不再假定你在 main 上——2026-10-26 切到特性分支后，写死的 origin main 与裸 pull 同时失效，当晚整轮死在第①步。",
            ],
            "reads": [],
            "writes": [
                at(RUN, "PULL_DEGRADED=1", before=6, after=2,
                   note="网络不可达 → 降级继续，收尾不 push"),
                ex(PUB, 50, 80, note="Breaker：冲突时由本类的 trip() 写 .newsagg/breaker.json"),
            ],
            "files": [
                ex(RUN, 1, 33, note="头注释：九步时序与四段调用约定"),
                at(RUN, 'if grep -qiE \'CONFLICT|Automatic merge failed', before=2, after=10,
                   note="冲突即熔断的实现"),
                at(RUN, "current_branch() {", before=8, after=10,
                   note="ADR-0065：一律按当前分支取远端"),
            ],
        },
        # ── ② ─────────────────────────────────────────────────────────
        {
            "id": "n02-style",
            "step": "②",
            "title": "风格学习（循环 B）",
            "layer": "sediment",
            "when": "① 之后、分拣之前",
            "actor": "脚本 + 物主",
            "one_liner": "对比「发布那一刻的快照」与「你后来改过的教学副本」，把你的修改学成风格偏好。",
            "does": [
                "从 git 历史取发布快照做基线（不取 draft，避免把审稿环节的 AI 改动当成你的偏好）。",
                "按内容 sha256 去重，同一版本只学一次——否则你分几天改同一篇会被计多次，凭空击穿 wewrite 的「重复≥2 次升级为硬规则」阈值。",
                "脚本只记录 diff；pattern 提取（这次修改反映了什么偏好）是判断，留给物主/agent。",
            ],
            "why": [
                "ADR-0026 循环 B：时刻必须早于写作——这一轮学到的东西今天就要用上。",
                "ADR-0008：人从「发布前审批」退到「发布后反馈」；你的修改是唯一真实信号源。",
            ],
            "reads": [
                jsonline(LEDGER, -1, note="发布账本：风格 diff 的基线与 NyA指针"),
                jsonview(RUNSTATUS, None, note="当日运行状态"),
            ],
            "writes": [
                jsonview(".newsagg/style-learning.json", None, note="去重状态：哪一篇的哪个哈希已经学过"),
            ],
            "files": [
                ex(STYLEL, 1, 13, note="设计依据三条：基线、去重、时机"),
                at(STYLEL, "def find_publish_snapshot", after=14, note="从 git 取发布快照"),
                ex(STYLEP, 1, 22, note="为什么还需要第二步：learn-edits 只记 diff，不提 pattern"),
                at(RUN, "style_learn() {", after=12, note="编排器里的调用点"),
            ],
        },
        # ── ②b ────────────────────────────────────────────────────────
        {
            "id": "n03-performance",
            "step": "②b",
            "title": "表现数据读回（循环 C）",
            "layer": "sediment",
            "when": "② 之后、分拣之前",
            "actor": "脚本（经 node + 浏览器会话）",
            "one_liner": "把每篇文章的展现/阅读读回来，与它自己的历史基准比，作为分拣的排序参考。",
            "does": [
                "用 gid（平台内容 ID）而非标题做归因——标题是软的，slug 才稳；老账本没 gid 时标题兜底。",
                "同一天重跑是「就地更新」而不是追加：否则同一篇在基准里算两遍。",
                "自身基準取历史中位数；连续 N 篇低于基准的一定比例才判持续低迷。",
                "**必须永不致命**：读不回来只写 performance-status.json + 简报呈现，不拖垮整轮。",
            ],
            "why": [
                "ADR-0010/0037：表现数据是**软信号**——不据此自动降权或退役观点卡。每日一篇的样本量下，把噪音升成硬规则会适得其反。",
                "ADR-0006：表现读不回来 ≠ 系统没跑，看守必须能区分这两者。",
                "ADR-0053：账本记录 gid，循环 C 才能按身份 join 回文章与卡。",
            ],
            "reads": [
                jsonline(LEDGER, -1, note="账本里的 gid 是 join 回文章的唯一身份"),
            ],
            "writes": [
                jsonview(PERFSTATUS, None, note="读回结果（成功/失败原因都要写，失败须自带原因）"),
                ex(REPORT, 1, 1, note="reports/performance.jsonl 是入库文件，跨环境保留"),
            ],
            "files": [
                ex(PERF, 45, 57, note="阈值常量与解除方式"),
                at(PERF, "def evaluate(", after=20, note="自身基准 + 持续低迷判定"),
                at(RUN, "performance() {", after=14, note="编排器：失败时把输出末行带出来（ADR-0052）"),
            ],
        },
        # ── ②c ────────────────────────────────────────────────────────
        {
            "id": "n04-skipday",
            "step": "②c",
            "title": "降频跳过日闸门",
            "layer": "gate",
            "when": "②b 之后",
            "actor": "脚本",
            "one_liner": "若系统处于自动降频状态且今天是跳过日 → 只出简报，不采集不写作不发布。",
            "does": [
                "读 .newsagg/frequency.json 判定今天是不是跳过日。",
                "命中则写 run-status（stage=downshift_skip）、出简报、commit+push、exit 0。",
            ],
            "why": [
                "ADR-0010：降频是自动的，**恢复必须人工确认**——删除 .newsagg/frequency.json 才解除。",
                "跳过日是设计行为，不是故障；但必须显式写进简报，让看守不把它当成「今日无选题」。",
            ],
            "reads": [],
            "writes": [],
            "files": [
                at(PERF, "def is_skip_day(", after=14, note="跳过日判定"),
                at(PERF, "HOW_TO_CLEAR = (", after=4, note="解除方式"),
                at(RUN, "skip_day_gate() {", after=14, note="编排器里的分支"),
            ],
        },
        # ── ③ ─────────────────────────────────────────────────────────
        {
            "id": "n05-collect",
            "step": "③",
            "title": "采集",
            "layer": "collect",
            "when": "白天 08:00 / 13:00 / 18:00 各一次（纯脚本，不碰 git）；21:00 若候选池不存在则自愈补采",
            "actor": "脚本",
            "one_liner": "从本地 DailyHotApi 拉 7 个源的榜，归一化，按赛道词预筛，落到候选池。",
            "does": [
                "每次拉取都全量重取该源的榜单，并按 (source,id) 与已有候选合并。",
                "同日多次采集是**累积合并**不是覆盖：保留 first_seen，热度轨迹进 hot_history，掉榜的标 seen_in_last_run:false。",
                "每次采集追加一条 collection_runs，其中 kept_new 按「first_seen == 本次时刻」计数——它不等于本次 kept。",
                "每个源按自己的 TTL×1.5 判陈旧（不是统一阈值），github 这种 24h TTL 的源不会被误判。",
            ],
            "why": [
                "ADR-0006：白天采多次是为了捕捉**发酵过程**，只看最后一个快照就丢掉了这一维。",
                "ADR-0039：第二次采集时预筛仍会「保留很多条」，但可能全是旧的；取 kept 会把「没有新东西」显示成「采到很多新的」，所以 kept_new 单独算。",
                "ADR-0029：公开实例 api-hot.imsyy.top 实测 HTTP 000 已不可用 → 改依赖本地部署。",
                "ADR-0056：观测同时记 hot 与 rank/rank_total，位次跨度才是可比的趋势信号（hot 跨源不可比）。",
            ],
            "reads": [
                ex(NICHE, 1, 16, note="赛道词典：命中任一关键词才成为候选（180 行，人为维护）"),
            ],
            "writes": [
                jsonview(CAND, ["collected_at", "counts", "source_meta"], note="候选池元信息与计数"),
            ],
            "files": [
                at(COLLECT, "DEFAULT_SOURCES = [", before=2, after=4, note="7 个默认源"),
                at(COLLECT, "STALE_TTL_FACTOR = 1.5", before=8, after=2, note="陈旧系数 1.5 是经验值，非推导值"),
                at(COLLECT, "def merge_candidates(", before=14, after=16, note="同日累积合并规则"),
                jsonview(CAND, ["collection_runs"], note="当天三次采集的真实记录"),
                at(RUN, "collect_now() {", after=6, note="编排器调用点；全源不可达才算失败"),
            ],
        },
        # ── ③b ────────────────────────────────────────────────────────
        {
            "id": "n06-triage-input",
            "step": "③b",
            "title": "组装分拣输入包",
            "layer": "triage",
            "when": "21:00 第 1 段，采集之后",
            "actor": "脚本",
            "one_liner": "把候选条目、全部 active 卡正文、冷却状态、限额、位次趋势、历史表现装进一个大包。",
            "does": [
                "8 张 active 卡**全文**进包（含当前判断/依据事实/修订史/边界与反方五节）——只看卡名判不了匹配度。",
                "每张卡的 covered_events 由 cardfm.py 解析，这是全仓唯一实现。",
                "给每条候选算位次趋势 trend（state/delta/observations/position_source）。",
                "trend_summary 单独给出覆盖率；participant：unknown+absent 占大半时明确说「本轮 trend 没有信息量」。",
            ],
            "why": [
                "ADR-0042：两份契约的两份实现一定会漂移，且漂移是静默的。三份 parse_frontmatter 漂移后，分拣层看不到「这张卡写过什么」而写作层看得到——同一轮里一个知道一个不知道。",
                "ADR-0056：刻度必须是同源位次百分位。池内名次会把退步读成爬升（rank 22→25 曾被判 climbing）。",
                "ADR-0007：covered_events 是防重复发文的唯一依据，读不到就等于没有。",
            ],
            "reads": [
                ex(CARD, 1, 22, note="观点卡：frontmatter + 五节结构"),
                jsonview(CAND, ["counts"], note="候选池"),
            ],
            "writes": [
                jsonview(INPUT, ["prepared_at", "instruction", "limits", "trend_summary"], note="输入包（161 条候选 / 8 张卡）"),
            ],
            "files": [
                ex(CARDFM, 1, 17, note="为什么必须是共享模块：三份拷贝漂移的后果"),
                at(TRIAGE, "def load_cards(", before=20, after=10, note="卡正文全文进包"),
                at(TRIAGE, "TREND_CLIMB_DELTA", after=5, note="趋势阈值常量"),
                jsonview(INPUT, ["trend_summary"], note="这一轮趋势信号的真实覆盖率"),
                at(RUN, "triage_input() {", after=6, note="编排器调用点"),
            ],
        },
        # ── ③c ────────────────────────────────────────────────────────
        {
            "id": "n07-triage-judge",
            "step": "③c",
            "title": "分拣判断（模型）",
            "layer": "triage",
            "when": "第 1 段结束 ↔ 第 2 段开始之间",
            "actor": "模型（你）",
            "one_liner": "读输入包，把描述同一事件的条目聚成簇，判断接得住哪张卡，产出三态决策。",
            "does": [
                "聚簇：同一现实事件的多条报道合并；单条报道不足以支撑判断。",
                "匹配：逐张读卡**正文**，不是只看标题。",
                "三态：skip / continue / new，或 abstain；action=new 时必须先写好卡文件再产出决策。",
                "低置信度的簇不参与竞争，但要写进 low_confidence_skipped；无卡可接的写进 no_card_available。",
                "校验：triage.py --validate 检查结构 + 冷却期 + 限额 + card_file 是否存在。",
            ],
            "why": [
                "ADR-0025：技能装判断，插件装强制——这层是纯判断，脚本只做确定性外壳。",
                "「新进展」的唯一判据是引入新事实或改变判断；纯重复报道必须 skip。这是全系统防重复发文的**唯一**判定点，判错代价最贵。",
                "ADR-0041：建卡与决策是同一个动作。缺 card_file 时 ⑥ 的写回被静默跳过，新卡永远拿不到 covered_events——认知资产停止生长且不报错。",
                "冷却期优先于排序：它的目的是防重复，不是惩罚其他选题。",
            ],
            "reads": [
                jsonview(INPUT, ["instruction", "limits"], note="要你照着做的规则"),
            ],
            "writes": [
                jsonview(DECISION, ["date"], note="决策文件（本 action=continue）"),
            ],
            "files": [
                ex(".agents/skills/newsagg-triage/SKILL.md", 29, 56, note="三态决策表 + 八条硬规则"),
                jsonview(DECISION, ["low_confidence_skipped"], note="被置信度/冷却期挡下的簇"),
                at(TRIAGE, "def validate_decision(", after=16, note="校验器：结构 + 冷却 + 限额 + 卡存在"),
                ex(RUN, 545, 566, note="第 1 段结尾打印的 handoff 指引"),
            ],
        },
        # ── ④ 之一 ────────────────────────────────────────────────────
        {
            "id": "n08-brief-pack",
            "step": "④",
            "title": "生成写作选题包",
            "layer": "write",
            "when": "第 2 段开头",
            "actor": "脚本",
            "one_liner": "把决策 + 承接卡的认知 + 风格配置拼成一份任务书，交给写作引擎。",
            "does": [
                "从卡里抽出「当前判断 / 依据事实 / 已覆盖事件 / 边界与反方」四块，明确要求引用或推进、不得另起。",
                "把 style.yaml 的账号、目标读者、语气、人称、人格与黑名单一并注入。",
                "--out 显式写到当日文件名：编排器三段必须共享同一日期，跨零点续跑时默认「今天」会指向不存在的文件。",
            ],
            "why": [
                "ADR-0026：写作必须引用或推进已有卡，不得另起炉灶——这是沉淀闭环的关键一步。",
                "ADR-0039：build_brief 默认按今天命名，会在重放历史决策时静默指向错误文件。",
            ],
            "reads": [
                jsonview(DECISION, ["decision"], note="决策（含 whats_new / money_point）"),
                ex(STYLE, 1, 20, note="风格配置"),
            ],
            "writes": [
                ex(BRIEFF, 1, 26, note="选题包实际产物"),
            ],
            "files": [
                at(BBRIEF, "def build_brief(", before=6, after=10, note="素材锚点只放输入里真实存在的内容，不编造"),
                at(BBRIEF, "cur = extract_section(card[\"body\"], \"当前判断\")", before=3, after=12,
                   note="卡的四块内容怎么进任务书"),
                at(BBRIEF, "spath = root / \".wewrite\" / \"style.yaml\"", before=2, after=12, note="风格注入"),
                at(RUN, 'python3 .agents/skills/newsagg-triage/scripts/build_brief.py --decision',
                   before=6, after=6, note="显式 --out 到当日"),
            ],
        },
        # ── ④ 之二 ────────────────────────────────────────────────────
        {
            "id": "n09-write",
            "step": "④",
            "title": "写作 → 成稿",
            "layer": "write",
            "when": "第 2 段",
            "actor": "写作引擎（wewrite）+ 模型",
            "one_liner": "按选题包写出初稿；模型未配置时按设计降级为本人自写。",
            "does": [
                "wewrite llm-write 生成 outputs/<date>-draft.md；退出码 3 表示写作模型未配置 → 降级为自写，不是错误。",
                "审稿后的成稿另存为 outputs/<slug>.md，slug 形如 <date>-<短标题>，首行一级标题 2~30 字（头条硬限制）。",
                "写作阶段同时产出封面论据 .newsagg/covers/<date>.json（一个视觉论据，而不是复述标题）。",
            ],
            "why": [
                "ADR-0004：复用 wewrite 作为写作引擎，不重写。",
                "ADR-0044：信息流里标题就印在封面旁边，复述标题的封面不增加信息，缩到 320px 时还读不出来——所以封面要承载一个论据。缺 spec 时会回退到标题推导，而这条回退路径曾静默产出一张顶部小字与正文重复的封面。",
            ],
            "reads": [
                ex(BRIEFF, 1, 26, note="任务书"),
            ],
            "writes": [
                ex(OUTPUT, 1, 18, note="成稿（首行一级标题即发布标题）"),
                jsonview(COVERSPEC, None, note="封面论据（layout=number）"),
            ],
            "files": [
                at(RUN, "wewrite llm-write --brief", before=4, after=14, note="退出码 3 的降级分支"),
                ex(RUN, 600, 627, note="第 2 段结尾给模型的指引（含封面论据格式）"),
                jsonview(COVERSPEC, None, note="这一天的封面论据 spec"),
            ],
        },
        # ── ④b ────────────────────────────────────────────────────────
        {
            "id": "n10-cover",
            "step": "④b",
            "title": "封面（含可选照片）",
            "layer": "publish-prep",
            "when": "第 3 段开头",
            "actor": "脚本（Pillow / wewrite venv 的 python）",
            "one_liner": "按 spec 画封面；spec 里写了 photo_query 才去取 CC0 照片，并把自检结果留档给简报。",
            "does": [
                "只有 spec 写了 photo_query 才取图——谁来负责任：只有写作阶段知道文章讲什么、也才知道「机房照片配一篇讲迁移的文章」算不算图文无关。",
                "照片必须来自 CC0/公有领域，许可证与来源全部记进 .newsagg/photos/ledger.jsonl。",
                "画出后做安全区与边缘自检，warnings/problems/drawn 元素高度写进 .newsagg/covers/<date>.report.json。",
                "封面样式按日期哈希从 8 种里确定性选出，且不与前一天撞款。",
            ],
            "why": [
                "ADR-0048：没有 query 就说明没人做过图文相关的判断，此时加一张来路不明的照片只是把「图文无关」变成系统自动决定。",
                "ADR-0049：首页信息流会把图裁成 3:2，元素必须落在安全区内。",
                "ADR-0044 发现六：无人值守时没有第二双眼睛，自检警告必须落到简报里，否则等于没检查。",
                "ADR-0039/0052：make_cover 依赖 Pillow，只装在 wewrite 的 venv 里，所以解释器被钉死为 $REPO/.uv-tools/wewrite/bin/python。",
            ],
            "reads": [
                jsonview(COVERSPEC, None, note="封面论据"),
            ],
            "writes": [
                jsonview(f".newsagg/covers/{DATE}.report.json", None, note="自检留档（简报 ⑤ 段读取）"),
            ],
            "files": [
                at(COVER, "FEED_CARD_ASPECT = 1.5", before=4, after=4, note="安全区由 3:2 裁切推导"),
                at(COVER, "def style_for_date(", after=12, note="确定性选样式且避开前一天"),
                at(COVER, "def layout_number(", after=14, note="number 布局：hero 数字 + payoff 论点"),
                ex(PHOTO, 1, 26, note="照片层：许可证必须记账"),
                at(RUN, "注意：未找到封面论据", before=4, after=6, note="缺 spec 时的回退告警"),
            ],
        },
        # ── ⑤ ─────────────────────────────────────────────────────────
        {
            "id": "n11-publish",
            "step": "⑤",
            "title": "发布（六道门 + 反查）",
            "layer": "publish",
            "when": "第 3 段",
            "actor": "脚本 → vendor 的 toutiao-ops（node）",
            "one_liner": "过门后才发；发出后再读回平台确认，把结果与承接卡写进账本。",
            "does": [
                "门 0 熔断：.newsagg/breaker.json 存在即拒绝发布，人工修复后删文件解除。",
                "门 0.5 每日上限：今天已成功发布 ≥1 篇即拒绝（dry-run 不消耗额度）。需补发/重发时须显式 --allow-daily-override 并写理由。",
                "门 1 输入：成稿存在、有一级标题、标题 2~30 字、封面文件存在（头条必填）。",
                "门 2 登录预检：只读，最多 3 次；判定为 login_lost 即熔断。",
                "门 3 内存预检：可用内存低于下限即熔断（ADR-0023）。",
                "发布命令显式带 --no-weitoutiao（平台默认是勾选的，同发会造成内容重复）。",
                "门 5 反查：按平台实际收到的标题去作品列表里查；咽字时以平台标题为准。",
                "全程 FileLock 串行锁；锁带 pid，用 kill(pid,0) 判断持有者是否存活，崩惯不会留下永久锁。",
            ],
            "why": [
                "ADR-0014：dry-run 默认开启；dry-run 只对人民日报 NanA 留真实草稿，也要 Methodist草稿不算发布。",
                "ADR-0024：复用 vendor 的 toutiao-ops，本脚本只加安全门与验证。",
                "ADR-0051：平台实际标题可能被输入框咽字，按原标题反查会把真实发布误判成未发布。",
                "ADR-0034：一次运行只产一篇文章，同发微头条会造成内容重复且不受每日上限与冷却期约束。",
                "ADR-0037：把承接卡写进账本——循环 C 要按卡归因回答「哪些卡跑出了数据」。",
            ],
            "reads": [
                jsonview(DECISION, ["decision"], note="card_file 从这里取"),
                ex(OUTPUT, 1, 3, note="成稿标题"),
            ],
            "writes": [
                jsonline(LEDGER, -1, note="账本最新一行（本次真实发布）"),
            ],
            "files": [
                at(PUB, "# ── 门 0：熔断状态", after=12, note="门 0"),
                at(PUB, "# ── 门 0.5：每日发布上限", after=14, note="门 0.5"),
                at(PUB, "# ── 门 3：内存预检", after=8, note="门 3"),
                at(PUB, "# ── 门 2：登录预检", after=16, note="门 2"),
                at(PUB, "# 并把真实值放在 `title` 字段", before=3, after=8, note="平台实际标题"),
                at(PUB, "# ── 门 5：反查验证", after=12, note="门 5"),
                ex(PUB, 82, 100, note="FileLock 的陈旧锁处理"),
            ],
        },
        # ── ⑥ ─────────────────────────────────────────────────────────
        {
            "id": "n12-writeback",
            "step": "⑥",
            "title": "认知写回",
            "layer": "sediment",
            "when": "发布成功之后（仅真实发布，dry-run 跳过）",
            "actor": "脚本",
            "one_liner": "把这一轮的事件、新进展、依据追加回承接卡，让认知资产生长。",
            "does": [
                "追加一条 covered_events（含日期与事件描述）——这是下一步防重复发文的依据。",
                "把 whats_new 作为未复核修订追加进修订史，带来源文章 slug，你可以推翻。",
                "同日 + 同事件前缀已存在则幂等跳过，避免一次事件写回两次。",
                "更新 frontmatter 的 last_written 日期。",
            ],
            "why": [
                "ADR-0007：观点卡只追加不重建，是认知资产的组成单位。",
                "ADR-0026：写回必须在发布成功之后——没发出去的文章不能占用「已覆盖」的位置。",
                "这是 ③c 将来能判清 skip/continue 的唯一数据来源；写不进去就等于这一轮白写。",
            ],
            "reads": [
                jsonview(DECISION, ["decision"], note="card_file / whats_new / key_evidence"),
            ],
            "writes": [
                at(CARD, "covered_events:", before=4, after=8, note="写回后的 frontmatter"),
            ],
            "files": [
                ex(WRITEBACK, 1, 13, note="设计依据：covered_events 与 last_written 是关键字段"),
                at(WRITEBACK, "def append_covered_event(", after=12, note="空列表展开的处理"),
                at(WRITEBACK, "# 判据：同日 + 同事件（前缀匹配）已存在于 covered_events。", before=4, after=8,
                   note="幂等保护"),
                at(RUN, "log \"⑥ 认知写回", after=16, note="编排器：失败不阻塞发布，但须人工补"),
            ],
        },
        # ── ⑦⑧ ⑨ ─────────────────────────────────────────────────────
        {
            "id": "n13-brief-push",
            "step": "⑦⑧⑨",
            "title": "状态 + 简报 + commit/push",
            "layer": "orchestrate",
            "when": "第 3 段结尾（成功与失败都走）",
            "actor": "脚本",
            "one_liner": "把这一轮的成败写成 run-status，再渲染成人看的五段式简报，最后提交推送。",
            "does": [
                "run-status.json：无论成败都先落一份「这一轮跑到哪、为什么停」。",
                "brief.py 读当日全部产物渲染五段：① 今日结论 ② 采集分拣概览 ③ 本篇与发布结果 ④ 资产变动 ⑤ 需你介入的事项。",
                "缺哪个阶段的文件就按「该阶段未运行」呈现，不报错——简报必须无条件每日存在。",
                "sync_push：git add -A → commit → push 当前分支；push 走代理失败时直连重试一次。",
                "降级轮不 push：不知道远端有没有新提交，盲目 push 可能被拒。",
            ],
            "why": [
                "ADR-0038：任何失败都必须出简报。`.newsagg/` 不入库、看守读不到它，所以成败由简报呈现给人。",
                "ADR-0006：简报是心跳不是可选报告。一份写明「本轮运行未完成」的简报比没有简报更有信息量。",
                "ADR-0019：五段结构的存在是为了让「今日无选题」「弃权」「系统没跑」三者可区分。",
            ],
            "reads": [
                jsonview(RUNSTATUS, None, note="运行状态（① 段优先读它）"),
                jsonview(PERFSTATUS, None, note="表现读回结果（⑤ 段）"),
                jsonline(LEDGER, -1, note="发布账本（③ 段）"),
            ],
            "writes": [
                ex(REPORT, 1, 20, note="reports/<日期>.md：看守读的就是它"),
            ],
            "files": [
                at(BRIEF, "def section_conclusion(", after=22, note="① 段：运行失败优先于一切"),
                at(BRIEF, "    out = [\"## ② 采集与分拣概览\", \"\"]", after=10, note="② 段"),
                at(BRIEF, "out = Path(args.out) if args.out else root / \"reports\"", before=4, after=6,
                   note="落盘与摘要输出"),
                at(RUN, "sync_push() {", after=20, note="提交与推送，含降级不 push 分支"),
            ],
        },
        # ── 失败通道 ──────────────────────────────────────────────────
        {
            "id": "n14-failsoft",
            "step": "✗",
            "title": "失败通道 fail_soft",
            "layer": "gate",
            "when": "任意环节失败",
            "actor": "脚本",
            "one_liner": "任何一步失败：写失败状态 → 出简报 → 提交推送 → exit 1。绝不静默跳过。",
            "does": [
                "write_status(ok=0, stage, reason, detail)：detail 保留最后 2000 字。",
                "degraded 是第三种状态：跑完了但有让步，不让看守告警但必须让人看见。",
                "brief_now 无条件执行；allow_push=0 时（远端状态不明/工作区脏）不 push，交人工处置。",
            ],
            "why": [
                "ADR-0038：失败必须出简报，否则看守无法区分「系统没跑」与「跑了一半」。",
                "ADR-0028：命令成功与否一律看退出码，不经管道——管道会掩码退出码。",
            ],
            "reads": [],
            "writes": [
                jsonview(RUNSTATUS, None, note="失败样本字段"),
            ],
            "files": [
                at(RUN, "fail_soft() {", after=16, note="统一失败出口"),
                at(RUN, "write_status() {", after=18, note="第三态 degraded"),
                at(RUN, "brief_now() {", after=4, note="简报无条件存在"),
            ],
        },
        # ── 外围：看守与测试 ──────────────────────────────────────────
        {
            "id": "n15-ci",
            "step": "∞",
            "title": "看守与测试（GitHub Actions）",
            "layer": "ci",
            "when": "看守：每天 UTC 03:00（北京 11:00）；测试：每次 push / PR",
            "actor": "CI（跑在与主机不同的机器上）",
            "one_liner": "独立机器上的两条流水线：一条判系统还活着，一条判这次改动有没有弄坏确定性逻辑。",
            "does": [
                "看守：checkout 后找近两日的 reports/<date>.md；存在再校验五段标题 ①②③④⑤ 齐备。",
                "测试：python 3.9 与 3.12 双版本矩阵跑 python3 -m unittest discover；3.9 是下限（服务器上的版本）。",
                "两条流水线都不依赖主系统所在的机器，因此能发现「主系统整体失效」。",
            ],
            "why": [
                "ADR-0006：主系统「完全没跑」它自己永远发现不了，而「今天没有值得写的」是合法结果——两者从外部不可区分，必须有独立看守。",
                "ADR-0038：看守改读仓库里的 reports/<日期>.md。原先按 Issue 是否存在判定，而简报从未开过 Issue，于是每天一封假警报，把唯一的推送通道占满——比没有告警更糟。",
                "ADR-0057：一份没人跑的测试与没有测试等价，而且更糟——它让人以为有回归保护。",
            ],
            "reads": [
                ex(REPORT, 1, 6, note="看守读的目标文件"),
            ],
            "writes": [],
            "files": [
                ex(WATCHDOG, 1, 13, note="设计依据：为什么它必须跑在另一台机器上"),
                at(WATCHDOG, "YESTERDAY=$(TZ=Asia/Shanghai date -d 'yesterday' +%Y-%m-%d)", before=8, after=8, note="检查窗口：昨天与今天任一存在即存活"),
                at(WATCHDOG, 'for marker in "①" "②" "③" "④" "⑤"; do', before=4, after=8, note="五段齐备校验"),
                ex(TESTW, 1, 13, note="为什么单独建这个工作流"),
                at(TESTW, 'python-version: ["3.9", "3.12"]', before=8, after=4, note="两个真实解释器都要测"),
            ],
        },
    ]

    # ── 附录： browse all files referenced ──
    return nodes


def main():
    nodes = build()
    adr = sorted(p.name[:-3] for p in (ROOT / "docs/adr").glob("*.md"))
    tests = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
    cards = sorted(p.name for p in (ROOT / "knowledge/cards").glob("*.md"))
    outputs = sorted(p.name for p in (ROOT / "outputs").glob("*.md"))
    reports = sorted(p.name for p in (ROOT / "reports").glob("*.md"))
    led = [json.loads(l) for l in (ROOT / LEDGER).read_text(encoding="utf-8").splitlines() if l.strip()]

    data = {
        "groups": [
            {"title": "\u4e3b\u6d41\u7a0b\u7b2c 1 \u6bb5 \u00b7 21:00 \u8d77", "ids": ["n01-pull", "n02-style", "n03-performance", "n04-skipday"]},
            {"title": "\u5728\u6b64\u4e4b\u524d \u00b7 \u767d\u5929\u5df2\u8dd1\u4e09\u6b21\uff08\u6b65\u9aa4 \u2462\uff09", "ids": ["n05-collect"]},
            {"title": "\u7b2c 1\u21922 \u6bb5\u4e4b\u95f4 \u00b7 \u6a21\u578b\u5224\u65ad", "ids": ["n07-triage-judge"]},
            {"title": "\u4e3b\u6d41\u7a0b\u7b2c 2 \u6bb5 \u00b7 \u5199\u4f5c", "ids": ["n06-triage-input", "n08-brief-pack", "n09-write"]},
            {"title": "\u4e3b\u6d41\u7a0b\u7b2c 3 \u6bb5 \u00b7 \u53d1\u5e03\u7ed3\u7b97", "ids": ["n10-cover", "n11-publish", "n12-writeback", "n13-brief-push"]},
            {"title": "\u5f02\u5e38\u4e0e\u5916\u56f4", "ids": ["n14-failsoft", "n15-ci"]},
        ],
        "meta": {
            "title": "NewsAgg",
            "root": str(ROOT),
            "source_script": "viz/facts/build_facts.py",
            "generated_at": datetime.now(CST).isoformat(),
            "date": DATE,
            "note": "全部摘录实读自仓库源文件；行号为真实行号。",
            "stats": [
            ["ADR", len(adr)], ["\u6d4b\u8bd5", len(tests)], ["\u89c2\u70b9\u5361", len(cards)],
            ["\u6210\u7a3f", len(outputs)], ["\u7b80\u62a5", len(reports)],
            ["\u53d1\u5e03", len([r for r in led if not r.get("accidental")])],
            ["\u8bef\u53d1", len([r for r in led if r.get("accidental")])],
            ],
        },
        "nodes": nodes,
    }
    for n in nodes:
        for key in ("reads", "writes", "files"):
            for f in n[key]:
                if f["start"] is None:
                    continue
                if f["path"] not in _CACHE:
                    lines_of(f["path"])
                total = len(_CACHE[f["path"]])
                if f["end"] > total or f["start"] < 1:
                    die(f"行号越界 {f['path']} {f['start']}-{f['end']} / {total}")
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {OUT.relative_to(ROOT)}  nodes={len(nodes)}  {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
