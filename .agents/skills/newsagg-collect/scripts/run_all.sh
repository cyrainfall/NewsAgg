#!/usr/bin/env bash
# NewsAgg 全流程编排（无人值守）
#
# 设计依据：
# - ADR-0026 九步时序：① pull ② 风格学习 ③ 采集→分拣 ④ 写作 ⑤ 发布 ⑥ 写回 ⑦⑧ commit+push ⑨ 简报
# - ADR-0018 所有权与同步：运行前 pull --rebase、运行后 commit+push；冲突熔断且**不自行合并**
# - ADR-0038 无人值守接线：分三段调用（分拣判断与写作由 agent 完成）；**任何失败都必须出简报**
# - ADR-0028：命令成功与否一律看退出码，不经管道（管道会掩码退出码）
#
# 无人值守的调用顺序（定时任务按此驱动）：
#
#   白天 3 次（08/13/18）——纯脚本，不碰 git：
#     bash .agents/skills/newsagg-collect/scripts/run_all.sh --collect-only
#
#   21:00 主流程第 1 段：
#     bash .agents/skills/newsagg-collect/scripts/run_all.sh --no-collect --publish
#       ← agent 读 .newsagg/triage/<date>-input.json，产出 decision.json，跑 --validate
#
#   主流程第 2 段：
#     bash .agents/skills/newsagg-collect/scripts/run_all.sh --after-triage --publish
#       ← 若写作模型未配置（退出码 3），agent 写 outputs/<slug>.md
#
#   主流程第 3 段：
#     bash .agents/skills/newsagg-collect/scripts/run_all.sh --after-write --publish
#
# ⚠️ **三段都必须带 `--publish`**，否则全程 dry-run。MODE 默认 dry-run，且会被
#    写进每段结尾打印的 handoff 命令里（`$([[ "$MODE" == "publish" ]] && echo --publish)`），
#    所以漏了第一段就会一路漏到底——三段全部只存草稿，而每段都报"成功"。
#    这也意味着 dry-run 与真发的差别**只由调用者决定**，脚本自身不记得上一段用了什么。
#
# 测试用开关：--no-git（跳过 pull 与 commit/push）、--no-collect、--date <YYYY-MM-DD>

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO"

SELF=".agents/skills/newsagg-collect/scripts/run_all.sh"

MODE="dry-run"
COLLECT_ONLY=""
AFTER_TRIAGE=""
AFTER_WRITE=""
NO_GIT=""
NO_COLLECT=""
DATE_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --publish)      MODE="publish" ;;
    --collect-only) COLLECT_ONLY="1" ;;
    --after-triage) AFTER_TRIAGE="1" ;;
    --after-write)  AFTER_WRITE="1" ;;
    --no-git)       NO_GIT="1" ;;
    --no-collect)   NO_COLLECT="1" ;;
    --date)         DATE_OVERRIDE="${2:-}"; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

DATE="${DATE_OVERRIDE:-$(TZ=Asia/Shanghai date +%Y-%m-%d)}"
HOT_BASE="${NEWSAGG_HOT_BASE:-http://127.0.0.1:6688}"
CAND=".newsagg/candidates/${DATE}.json"
DEC=".newsagg/triage/${DATE}-decision.json"
BRIEF="reports/${DATE}.md"

# ── 凭证（ADR-0015/0018/0081：`.env` 不入库，`.env.example` 入库）──────
# 定时任务由调度器直接拉起，不经过交互式 shell，因此不能依赖你手动 export。
# 在 set -u 生效前读入，缺失时静默跳过（写作模型未配置 → llm-write 退出码 3
# → 按设计降级为 agent 自写，不是错误）。
if [[ -f "$REPO/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO/.env"
  set +a
fi

# 运行时目录先建好：第 2 段末尾 agent 要写 .newsagg/covers/<date>.json（封面论据，
# ADR-0044），第 3 段才读它。目录不存在会让 agent 的写入静默失败或被迫自己建。
mkdir -p .newsagg/covers .newsagg/briefs
export WEWRITE_HOME="$REPO/.wewrite"
export PATH="$REPO/.uv-bin:$PATH"

# ── 封面用解释器（ADR-0039）──────────────────────────────────────────
# make_cover.py 依赖 Pillow，而 Pillow 只装在 wewrite 的 venv 里；用系统
# python3 跑它会以「未安装 Pillow」失败，且这个失败发生在第 3 段——那时
# 稿子已经写好、简报还没出，等于每天白跑一次。定时任务不经过交互式 shell，
# 没有"记得换个 python"的机会，所以这里把解释器钉死。
# 退化路径：venv 不存在时回落到 python3，让错误在封面这一步显式暴露，
# 而不是静默跳过封面（无封面会被发布门 1 拦下）。
if [[ -x "$REPO/.uv-tools/wewrite/bin/python" ]]; then
  COVER_PY="$REPO/.uv-tools/wewrite/bin/python"
else
  COVER_PY="python3"
fi

log() { printf '\n=== %s ===\n' "$*"; }

# ── 运行状态（简报 ⑤ 段与看守的输入）──────────────────────────────
# ADR-0038：`.newsagg/` 不入库，故看守读不到它；但简报可以，所以每次运行
# 无论成败都先落一份 run-status，再由简报把它呈现给人。
write_status() {  # $1 ok(1/0)  $2 stage  $3 reason  $4 detail
  ST_OK="$1" ST_STAGE="$2" ST_REASON="$3" ST_DETAIL="${4:-}" \
  ST_DATE="$DATE" ST_MODE="$MODE" \
  python3 - <<'PY'
import json, os
from datetime import datetime, timedelta, timezone
CST = timezone(timedelta(hours=8))
p = ".newsagg/run-status.json"
os.makedirs(os.path.dirname(p), exist_ok=True)
rec = {
    "ok": os.environ["ST_OK"] == "1",
    "at": datetime.now(CST).isoformat(),
    "date": os.environ["ST_DATE"],
    "mode": os.environ["ST_MODE"],
    "stage": os.environ["ST_STAGE"],
    "reason": os.environ["ST_REASON"],
    "detail": (os.environ.get("ST_DETAIL") or "")[-2000:],
}
with open(p, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False, indent=2)
print(json.dumps({k: rec[k] for k in ("ok", "stage", "reason")}, ensure_ascii=False))
PY
}

brief_now() {
  python3 .agents/skills/newsagg-brief/scripts/brief.py --date "$DATE" \
    || echo "[X] 简报生成失败——看守会因此告警（ADR-0006：简报必须无条件每日存在）" >&2
}

sync_push() {  # $1 = 附加到提交信息的一行
  if [[ -n "$NO_GIT" ]]; then echo "（--no-git：跳过 commit/push）"; return 0; fi
  git add -A
  if git diff --cached --quiet; then
    echo "无改动可提交"
  else
    if git commit -q -m "NewsAgg 无人值守运行 ${DATE}（${MODE}）" -m "${1:-}"; then
      echo "已提交 $(git rev-parse --short HEAD)"
    else
      echo "[X] commit 失败" >&2; return 1
    fi
  fi
  if git push origin main >/tmp/newsagg-push.out 2>&1; then
    echo "已推送 origin/main"
    return 0
  fi
  echo "[X] push 失败：$(tail -3 /tmp/newsagg-push.out)" >&2
  return 1
}

fail_soft() {  # $1 stage  $2 reason  $3 detail  $4 allow_push(1/0)
  local stage="$1" reason="$2" detail="${3:-}" allow_push="${4:-1}"
  printf '\n[X] %s：%s\n' "$stage" "$reason" >&2
  write_status 0 "$stage" "$reason" "$detail"
  brief_now
  if [[ "$allow_push" == "1" ]]; then
    sync_push "运行在「${stage}」阶段失败：${reason}" \
      || echo "（产出仍留在本地，未同步到远端）" >&2
  else
    echo "（未 push：远端状态不明或工作区不干净，交人工处置）" >&2
  fi
  exit 1
}

dec_field() {  # $1 = 键路径；先在 decision 对象里找，再回退到顶层（abstain_reason 等）
  DEC="$DEC" FIELD="$1" python3 - <<'PY'
import json, os

raw = json.load(open(os.environ["DEC"], encoding="utf-8"))
d = raw.get("decision", raw)


def walk(obj, path):
    cur = obj
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


cur = walk(d, os.environ["FIELD"])
if cur is None and isinstance(raw, dict):
    cur = walk(raw, os.environ["FIELD"])
if isinstance(cur, list):
    cur = ",".join(str(x) for x in cur)
print("" if cur is None else cur)
PY
}

find_slug() {  # 今日成稿 slug（按修改时间取最新，排除 -draft.md）
  local f
  while IFS= read -r f; do
    case "$f" in *-draft.md) continue ;; esac
    basename "$f" .md
    return 0
  done < <(ls -1t "outputs/${DATE}-"*.md 2>/dev/null || true)
  return 1
}

# ── ① git pull --rebase（ADR-0018/0026）────────────────────────────
# 必须在风格学习之前：风格回路依赖「你本地改完 push、系统 pull 到」。
# 冲突即熔断、不自行合并——自动解决冲突可能静默丢弃你在风格或合规规则上的修改。
git_pull() {
  if [[ -n "$NO_GIT" ]]; then echo "（--no-git：跳过 ① git pull）"; return 0; fi
  log "① git pull --rebase（取得你的编辑）"
  local err=/tmp/newsagg-pull.err
  if git pull --rebase >/tmp/newsagg-pull.out 2>"$err"; then
    echo "已同步到 $(git rev-parse --short HEAD)"
    return 0
  fi
  git rebase --abort >/dev/null 2>&1 || true
  local detail; detail="$(tail -15 "$err")"
  if grep -qiE 'CONFLICT|Automatic merge failed|could not apply' "$err"; then
    python3 - <<'PY' || true
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location(
    "pub_shared", ".agents/skills/newsagg-publish/scripts/publish.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
Path(".newsagg").mkdir(parents=True, exist_ok=True)
m.Breaker(Path(".newsagg/breaker.json")).trip(
    "git_conflict",
    {"detail": "git pull --rebase 出现合并冲突；已 abort，未自行合并（ADR-0018）"})
PY
    fail_soft "git_pull" "合并冲突（已 abort，未自行合并；已熔断）" "$detail" 0
  fi
  fail_soft "git_pull" "拉取失败（工作区可能不干净）" "$detail" 0
}

# ── ② 风格学习（ADR-0026：必须早于写作）──────────────────────────
style_learn() {
  log "② 风格学习（若你改过教学副本）"
  python3 .agents/skills/newsagg-triage/scripts/style_learn.py > /tmp/newsagg-style.json 2>&1 || true
  python3 - <<'PY'
import json
try:
    d = json.load(open("/tmp/newsagg-style.json", encoding="utf-8"))
except Exception:
    print("（风格学习输出无法解析，跳过）"); raise SystemExit(0)
ch = d.get("changed", 0)
print(f"检查 {d.get('checked',0)} 篇成稿，需学习 {ch} 篇")
for r in d.get("results", []):
    if r.get("status") != "unchanged":
        print(f"  [{r['status']}] {r['file']}")
if ch:
    print("  → learn-edits 只记录 diff；pattern 提取由 agent 完成：")
    print("    python3 .agents/skills/newsagg-triage/scripts/style_patterns.py --prepare")
PY
}

# ── ②b 表现数据（ADR-0010 循环 C）────────────────────────────────
# 必须在分拣之前：分拣把它当排序参考。**必须永不致命**——表现数据读不回来
# 不能拖垮整轮运行，否则看守无法区分「系统没跑」与「数据读不到」（ADR-0006）。
performance() {
  log "②b 表现数据（循环 C：读回 + 自身基准 + 降频判定）"
  python3 .agents/skills/newsagg-performance/scripts/performance.py > /tmp/newsagg-perf.json 2>&1 || true
  python3 - <<'PY'
import json
try:
    d = json.load(open("/tmp/newsagg-perf.json", encoding="utf-8"))
except Exception:
    print("（表现数据输出无法解析——本次读回失败，不影响本轮运行）")
    raise SystemExit(0)
if not d.get("ok"):
    print(f"  读回失败（阶段 {d.get('stage')}）：{d.get('reason')}")
    print("  → 排序参考与降频判据沿用旧值；简报 ⑤ 段会呈现本项")
    raise SystemExit(0)
v = d.get("verdict", {})
print(f"  平台作品 {d.get('articles_on_platform')} 篇，"
      f"其中本账号 {d.get('articles_matched')} 篇，新增记录 {d.get('new_records')} 条")
if v.get("status") == "cold_start":
    print(f"  基准：{v.get('note')}")
else:
    print(f"  基准阅读率 {v.get('baseline_read_rate')}，阈值 {v.get('threshold')}，"
          f"连续低迷 {v.get('low_streak')} 篇 → {v.get('status')}")
f = d.get("frequency", {})
if f.get("downshifted"):
    print(f"  ⚠ 系统已降频：{f.get('why')}")
PY
}

# ── ②c 降频跳过日（ADR-0010：自动降频，恢复需人工确认）────────────
skip_day_gate() {
  local skip
  skip="$(python3 .agents/skills/newsagg-performance/scripts/performance.py --check-skip 2>/dev/null \
    | python3 -c 'import json,sys
try: print("1" if json.load(sys.stdin).get("skip") else "0")
except Exception: print("0")' 2>/dev/null || echo 0)"
  if [[ "$skip" == "1" ]]; then
    log "②c 降频跳过日：今日不发文"
    echo "系统处于自动降频状态（ADR-0010），今日为跳过日——只出简报，不采集不写作不发布。"
    echo "解除：人工确认表现已恢复后删除 .newsagg/frequency.json。"
    write_status 1 "downshift_skip" "降频跳过日（设计行为，非故障）" ""
    brief_now
    sync_push "降频跳过日" || true
    exit 0
  fi
}

# ── ③ 采集 ──────────────────────────────────────────────────────
collect_now() {
  log "③ 采集（同日多次采集会累积合并，保留事件发酵过程）"
  if ! python3 .agents/skills/newsagg-collect/scripts/collect.py --base "$HOT_BASE"; then
    fail_soft "collect" "采集失败（全部源不可达）" "热榜服务 $HOT_BASE" 1
  fi
}

# ── ③b 分拣准备（组装输入包）────────────────────────────────────
triage_input() {
  log "③b 分拣：组装输入包"
  [[ -f "$CAND" ]] || fail_soft "triage_input" "找不到候选文件" "$CAND" 1
  python3 .agents/skills/newsagg-triage/scripts/triage.py || \
    fail_soft "triage_input" "组装分拣输入包失败" "$CAND" 1
}

# ── 采集-only（白天 3 次，纯脚本，不碰 git）───────────────────────
if [[ -n "$COLLECT_ONLY" ]]; then
  collect_now
  echo
  echo "采集完成（${DATE}）。候选池：${CAND}"
  exit 0
fi

# ══ 主流程第 1 段 ═══════════════════════════════════════════════
# ① pull 只在整轮运行的**开头**执行一次。
# 续跑段（--after-triage / --after-write）**不得**再 pull：整轮运行中途工作区
# 必然是脏的——reports/performance.jsonl 是入库文件（ADR-0018/0037），第 1 段的
# 表现数据读回就会改写它。在脏工作区上 `pull --rebase` 必然报
# 「您有未暂存的变更」而失败，但那不是合并冲突、是**预期状态**；把它当熔断
# 会让每一轮无人值守运行都死在第 2 段，且第 1 段的产出永不落地。
# 实测：2026-10-17 首次无人值守运行即因此失败（接线后第一次真实触发）。
if [[ -z "$AFTER_TRIAGE" && -z "$AFTER_WRITE" ]]; then
  git_pull
  style_learn
  performance
  skip_day_gate

  # 21:00 主流程通常 --no-collect（白天已采 3 次）；但若候选池不存在
  # （机器白天没开机等），则自愈式补采一次。
  if [[ -n "$NO_COLLECT" ]]; then
    if [[ -f "$CAND" ]]; then
      log "③ 采集：跳过（--no-collect，今日已有候选池）"
    else
      log "③ 采集：今日候选池不存在，补采一次（自愈）"
      collect_now
    fi
  else
    collect_now
  fi

  triage_input

  cat <<EOF

────────────────────────────────────────────────────────
分拣的**判断**由模型完成（ADR-0025：技能装判断）。请：

  1. 读 ${CAND%.json} 对应的输入包
     .newsagg/triage/${DATE}-input.json
  2. 按 newsagg-triage 的硬规则产出决策，写入
     ${DEC}
     · action=new 时，你还必须**新建观点卡**
       knowledge/cards/<file>.md（五节结构：当前判断 / 依据事实 /
       修订史 / 可接住的事件类型 / 边界与反方），并把文件名填进
       decision.card_file
  3. 跑校验：
     python3 .agents/skills/newsagg-triage/scripts/triage.py \\
       --validate ${DEC}

校验通过后，继续执行：
  bash ${SELF} --after-triage $([[ "$MODE" == "publish" ]] && echo --publish)
────────────────────────────────────────────────────────
EOF
  exit 0
fi

# ══ 主流程第 2 段：④ 写作准备 ═══════════════════════════════════
if [[ -n "$AFTER_TRIAGE" && -z "$AFTER_WRITE" ]]; then
  log "④ 写作准备：生成 brief"
  [[ -f "$DEC" ]] || fail_soft "write" "缺决策文件（分拣判断未完成）" "$DEC" 1
  # 显式 --out 到 ${DATE}：build_brief 默认按"今天"命名，跨零点续跑或重放历史
  # 决策时会让 handoff 指引指向一个不存在的文件。三段必须共享同一日期。
  python3 .agents/skills/newsagg-triage/scripts/build_brief.py --decision "$DEC" \
    --out ".newsagg/briefs/${DATE}.md" \
    || fail_soft "write" "生成选题包失败" "$DEC" 1

  ACTION="$(dec_field action)"
  if [[ "$ACTION" == "abstain" ]]; then
    log "弃权：跳过写作与发布（这是合法结果，不是故障）"
    write_status 1 "abstain" "今日弃权：$(dec_field abstain_reason)" ""
    brief_now
    sync_push "弃权：$(dec_field abstain_reason)" || true
    echo
    echo "完成（弃权）。简报：${BRIEF}"
    exit 0
  fi

  log "④ 写作"
  # llm-write 未配置 key 时退出码 3 = 设计上的「降级为 agent 自写」
  set +e
  wewrite llm-write --brief ".newsagg/briefs/${DATE}.md" \
    --output "outputs/${DATE}-draft.md" 2>&1 | tail -3
  RC=${PIPESTATUS[0]}
  set -e
  if [[ $RC -eq 3 ]]; then
    echo "→ 写作模型未配置（退出码 3），按设计降级：由 agent 自写"
  elif [[ $RC -ne 0 ]]; then
    fail_soft "write" "写作失败（退出码 $RC）" "" 1
  else
    echo "→ 初稿已生成 outputs/${DATE}-draft.md（审稿后另存为 outputs/<slug>.md）"
  fi

  cat <<EOF

────────────────────────────────────────────────────────
请让模型读 .newsagg/briefs/${DATE}.md，把成稿写成
  outputs/<slug>.md        （slug 形如 ${DATE}-<短标题>，首行一级标题 2~30 字）

封面：信息流里标题就印在封面旁边，复述标题的封面不增加信息，缩到 320px 宽
时还会读不出来（ADR-0044）。所以封面要承载**一个视觉论据**，写到
  .newsagg/covers/${DATE}.json
形如 {"layout":"number","kicker":"Meta 开源","hero":"8160","hero_unit":"亿参数",
"payoff":"模型免费，运维不免费"}；无强数字时用
{"layout":"contrast","kicker":"…","line1":"…","line2":"…"}。
留空则由标题自动推导（不一定最优，但不会出错）。写完后执行：
  bash ${SELF} --after-write $([[ "$MODE" == "publish" ]] && echo --publish)
────────────────────────────────────────────────────────
EOF
  exit 0
fi

# ══ 主流程第 3 段：⑤ 发布 ⑥ 写回 ⑦ 状态 ⑧ 简报 ⑨ commit+push ═══
log "④b 封面"
SLUG="$(find_slug)" || fail_soft "cover" "找不到今日成稿" "outputs/${DATE}-*.md" 1
echo "成稿：outputs/${SLUG}.md"
COVER_SPEC=".newsagg/covers/${DATE}.json"
COVER_SPEC_ARG=()
[[ -f "$COVER_SPEC" ]] && COVER_SPEC_ARG=(--spec "$COVER_SPEC")
# ⚠️ 必须写 ${A[@]+"${A[@]}"}：bash 3.2（macOS 自带）在 set -u 下展开**空数组**
# 会报 "A[@]: unbound variable" 并终止脚本。没有 spec 时（最常见的情形）
# 这一步会直接失败——一个只在"没写封面 spec"时才触发的静默故障。
"$COVER_PY" .agents/skills/newsagg-publish/scripts/make_cover.py --slug "$SLUG" \
  ${COVER_SPEC_ARG[@]+"${COVER_SPEC_ARG[@]}"} \
  || fail_soft "cover" "封面生成失败（解释器：${COVER_PY}；spec：${COVER_SPEC}）" "$SLUG" 1

log "⑤ 发布（${MODE}）"
if [[ "$MODE" == "publish" ]]; then PUB_FLAG="--mode publish"; else PUB_FLAG=""; fi
# ADR-0037：把承接的观点卡一并写进发布账本。循环 C 读回表现数据后要按卡
# 归因（"哪些卡跑出了数据"），而账本是唯一的关联点——文章标题会被你改，
# slug 不会，卡名只能在这一刻从决策文件里取到。dry-run 也记，便于验证。
CARD="$(dec_field card_file)"
set +e
python3 .agents/skills/newsagg-publish/scripts/publish.py \
  --slug "$SLUG" --cover "outputs/covers/${SLUG}.png" \
  ${CARD:+--card-file "$CARD"} $PUB_FLAG
PUB_RC=$?
set -e
if [[ $PUB_RC -ne 0 ]]; then
  fail_soft "publish" "发布未成功（退出码 ${PUB_RC}；熔断与反查结果见发布账本）" "$SLUG" 1
fi

# ── ⑥ 认知写回（ADR-0026：必须在发布成功之后）────────────────────
if [[ "$MODE" == "publish" ]]; then
  if [[ -n "$CARD" ]]; then
    log "⑥ 认知写回（追加未复核修订到 ${CARD}）"
    WB_ARGS=(--card "$CARD" --event "$(dec_field event.summary)" --article "$SLUG" --date "$DATE")
    [[ -n "$(dec_field whats_new)" ]] && WB_ARGS+=(--judgment "$(dec_field whats_new)")
    [[ -n "$(dec_field event.key_evidence)" ]] && WB_ARGS+=(--evidence "$(dec_field event.key_evidence)")
    if python3 .agents/skills/newsagg-triage/scripts/writeback.py "${WB_ARGS[@]}"; then
      echo "写回完成（带未复核标记，你可推翻）"
    else
      echo "[X] 写回失败——认知资产本轮未生长（不阻塞发布，但须人工补）" >&2
      write_status 1 "writeback_failed" "写回失败" "$CARD"
    fi
  else
    echo "（决策未给出 card_file，跳过写回）"
  fi
else
  echo "（dry-run：跳过 ⑥ 认知写回——写回只发生在真实发布成功之后）"
fi

log "⑦⑧ 简报与同步"
write_status 1 "complete" "运行完成" "slug=${SLUG} mode=${MODE}"
brief_now
if ! sync_push "发布 ${SLUG}（${MODE}）"; then
  printf '\n> ⚠ 本次运行结束时 push 失败：产出仍留在本地，未同步到远端。看守会因此告警。\n' >> "$BRIEF"
  sync_push "push 重试" || true
fi

echo
echo "完成。简报：${BRIEF}"
