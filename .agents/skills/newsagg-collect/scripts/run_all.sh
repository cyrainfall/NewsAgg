#!/usr/bin/env bash
# NewsAgg 全流程编排（ADR-0026 的时序：① pull ② 风格学习 ③ 采集→分拣 ④ 写作 ⑤ 发布 ⑥ 写回 ⑦ 简报）
#
# 用法：
#   bash .agents/skills/newsagg-collect/scripts/run_all.sh            # 默认 dry-run
#   bash .agents/skills/newsagg-collect/scripts/run_all.sh --publish  # 真发
#
# 设计要点：
# - 时序不可随意调换：风格学习必须在写作之前（否则今天学的今天用不上）；
#   认知写回必须在发布成功之后（否则会把未发出的判断写进资产）—— ADR-0026
# - 任一关键阶段失败即停，并给出可操作的下一步
# - ADR-0028：命令成功与否一律看退出码，不经管道（管道会掩码退出码）

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO"

MODE="dry-run"
AFTER_TRIAGE=""
for arg in "$@"; do
  case "$arg" in
    --publish)      MODE="publish" ;;
    --after-triage) AFTER_TRIAGE="1" ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

DATE="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
HOT_BASE="${NEWSAGG_HOT_BASE:-http://127.0.0.1:6688}"
export WEWRITE_HOME="$REPO/.wewrite"
export PATH="$REPO/.uv-bin:$PATH"

log() { printf '\n=== %s ===\n' "$*"; }
die() { printf '\n[X] %s\n' "$*" >&2; exit 1; }

# ── 前置：热榜服务必须可用（ADR-0029）──
log "前置检查：热榜服务"
if ! curl -s -o /dev/null --max-time 8 "$HOT_BASE/"; then
  die "热榜服务不可达（$HOT_BASE）。启动：cd reference/DailyHotApi && PORT=6688 npx tsx src/index.ts"
fi
echo "热榜服务可用"

# ── ② 风格学习（ADR-0026：必须早于写作）──
log "② 风格学习（若你改过教学副本）"
# 简化实现：有教学副本且与当前内容不一致时才学（版本去重由 wewrite learn-edits 自身与
# 我们的哈希记录共同保证，见 ADR-0026）。此处先只做提示，不自动学——
# 因为自动学习需要 diff 发布快照，涉及 git 历史读取，留待下一轮实现。
echo "（跳过：风格学习待接入 git 快照 diff；当前不影响写作阶段）"

# ── ③ 采集 ──
log "③ 采集"
python3 .agents/skills/newsagg-collect/scripts/collect.py --base "$HOT_BASE"

# ── ③b 分拣准备（组装输入包）──
log "③b 分拣：组装输入包"
python3 .agents/skills/newsagg-triage/scripts/triage.py

# ── 分拣判断由模型完成（不在脚本内）──
cat <<EOF

────────────────────────────────────────────────────────
分拣的**判断**由模型完成（ADR-0025：技能装判断）。请：

  1. 读 .newsagg/triage/${DATE}-input.json
  2. 按 newsagg-triage 的硬规则产出决策，写入
     .newsagg/triage/${DATE}-decision.json
  3. 跑校验：
     python3 .agents/skills/newsagg-triage/scripts/triage.py \\
       --validate .newsagg/triage/${DATE}-decision.json

校验通过后，继续执行：
  bash $0 --after-triage
────────────────────────────────────────────────────────
EOF

if [[ -z "$AFTER_TRIAGE" ]]; then
  exit 0
fi

# ── ④ 写作（选题包 → brief）──
log "④ 写作准备：生成 brief"
DEC=".newsagg/triage/${DATE}-decision.json"
[[ -f "$DEC" ]] || die "缺决策文件：$DEC"
python3 .agents/skills/newsagg-triage/scripts/build_brief.py --decision "$DEC"

# 判断是否弃权
ACTION="$(python3 -c "
import json
d=json.load(open('$DEC')); d=d.get('decision',d); print(d.get('action',''))
")"
if [[ "$ACTION" == "abstain" ]]; then
  log "弃权：跳过写作与发布"
else
  log "④ 写作"
  # llm-write 未配置 key 时退出码 3 = 设计上的「降级为 agent 自写」
  set +e
  wewrite llm-write --brief ".newsagg/briefs/${DATE}.md" \
    --output "outputs/${DATE}-draft.md" 2>&1 | tail -3
  RC=${PIPESTATUS[0]}
  set -e
  if [[ $RC -eq 3 ]]; then
    echo "→ 写作模型未配置（退出码 3），按设计降级：由 agent 自写"
    echo "  请让模型读 .newsagg/briefs/${DATE}.md 并写成 outputs/<slug>.md"
  elif [[ $RC -ne 0 ]]; then
    die "写作失败（退出码 $RC）"
  fi
fi

log "⑤ 发布（${MODE}）"
if [[ "$MODE" == "publish" ]]; then PUB_FLAG="--mode publish"; else PUB_FLAG=""; fi

cat <<EOF

发布是**不可逆动作**，需先有：
  · 成稿 outputs/<slug>.md（首行一级标题，2~30 字）
  · 封面 outputs/covers/<slug>.png（生成：
    .uv-tools/wewrite/bin/python .agents/skills/newsagg-publish/scripts/make_cover.py --slug <slug>）

然后执行：
  python3 .agents/skills/newsagg-publish/scripts/publish.py \\
    --slug <slug> --cover outputs/covers/<slug>.png $PUB_FLAG

EOF

# ── ⑦ 简报 ──
log "⑦ 简报"
python3 .agents/skills/newsagg-brief/scripts/brief.py

echo
echo "完成。简报：reports/${DATE}.md"
