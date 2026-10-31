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

# 「网络不可达」类错误的判据。刻意比「传输层抖动」那条宽——抖动那条用于
# **决定要不要重试**，这条用于**决定重试耗尽后是降级还是熔断**。
# 两者混用会把「TLS 握手失败」这种稳定失败也拿去重试三次（无益），
# 也会把「一次抖动」判成需要人介入（停工）。
NET_ERR_RE='RPC 失败|HTTP2 framing|Could not resolve host|Connection reset|Connection timed out|early EOF|The remote end hung up|SSL_ERROR_SYSCALL|LibreSSL|SSL connect error|Failed to connect|unable to access|Operation timed out|Network is unreachable|Connection refused'
# 「要不要重试」用窄表：只收真正的瞬时抖动。SSL_ERROR_SYSCALL / unable to access
# 这类在代理路径坏掉时是**稳定**失败，重试三次只是白等 4 秒——实测 2026-10-25
# 每次都在同一处失败。它们仍属「网络类」，由上面的宽表负责分类。

MODE="dry-run"
COLLECT_ONLY=""
AFTER_TRIAGE=""
AFTER_WRITE=""
NO_GIT=""
NO_COLLECT=""
DATE_OVERRIDE=""
# ① 没能从远端同步（网络不可达）时置 1：本轮在本地状态下继续，收尾不 push。
# 见 ADR-0061——远端不可达与「工作区脏 / 合并冲突」是两种事，后者要人动手，
# 前者只要等网络好；把前者判成后者会让一次代理抖动抹掉整天的产出。
PULL_DEGRADED=0
DEGRADED_NOTE=""
# 2026-10-25 实测：本机 Clash 对 github 的规则坏掉（走 7980 报 LibreSSL
# SSL_ERROR_SYSCALL），而 baidu 走同一个代理 200、github 直连也 200——
# 也就是「代理没坏，代理里指向 github 的那一跳坏了」。这种时候禁用代理
# 反而是通的，所以网络类失败后补一次直连尝试，避免为别人的节点问题
# 天天攒本地未推送的提交。
PROXY_FALLBACK=0

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

# ── 子进程 stdout 的编码（Windows）──────────────────────────────────
# Python 的 stdout 在被**重定向到文件**时用 locale 编码，Windows 上是 cp936。
# 于是 `performance.py > perf.json` 写出来的是 GBK，调用方再按 UTF-8
# `json.load` 就抛 UnicodeDecodeError —— 表现为「输出无法解析，跳过」，
# 而数据其实是好的（2026-10-28 本机实测，与 ADR-0076 同族：平台假设）。
# 钉成 UTF-8；POSIX 上这个变量本就等于默认值，无副作用。
export PYTHONIOENCODING=utf-8

# ── python 解释器（ADR-0076）────────────────────────────────────────
# **全部** NewsAgg 脚本都要求 Python ≥ 3.9（用了 `from __future__ import
# annotations` 与 `list[dict]` 这类 3.9+ 语法；wewrite 本身要求 ≥ 3.11）。
# 而系统 PATH 里的 `python3` **不保证**是这个版本：
#   - macOS 本机：3.9（恰好能用）
#   - 服务器 ECS：3.6.8（Alibaba Cloud Linux 3 的系统 python）→ 每一句
#     `from __future__ import annotations` 都报
#     `SyntaxError: future feature annotations is not defined`，
#     于是分拣、简报、表现读回**全部**在第一步就崩。
# 这跟 ADR-0039（封面用错解释器）和 ADR-0052（node 不在 PATH）是同一族：
# **定时任务不经过交互式 shell，没有「记得换个解释器」的机会**。
# 所以这里显式找，找不到就在最前面硬失败，而不是让每一段各自炸。
find_python() {
  local c
  for c in \
    "$REPO/.uv-tools/wewrite/bin/python" \
    "$REPO/.uv-bin/python" \
    /usr/bin/python3.11 /usr/bin/python3.12 /usr/bin/python3.10 /usr/bin/python3.9 \
    "$HOME/.local/share/uv/python/cpython-3.12/bin/python3" \
    "$HOME/.local/share/uv/python/cpython-3.11/bin/python3"
  do
    if [[ -x "$c" ]]; then
      # 版本必须 ≥ 3.9，否则下一个 `from __future__ import annotations` 就炸
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  # 兜底：系统 PATH 里的 python3（若版本够新）
  local sys
  sys="$(command -v python3 2>/dev/null || true)"
  if [[ -n "$sys" ]] && "$sys" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    echo "$sys"; return 0
  fi
  return 1
}

if ! PY="$(find_python)"; then
  echo "[X] 找不到 Python ≥ 3.9" >&2
  echo "    系统 PATH 里的 python3 是：$("$(command -v python3 2>/dev/null || echo '（无）')" -V 2>&1)" >&2
  echo "    修法：装一个 ≥ 3.9 的解释器，或先跑 bash scripts/bootstrap-wewrite.sh" >&2
  echo "    （它会在 .uv-tools/wewrite 建 3.12 venv，本脚本优先用那个）" >&2
  exit 1
fi
echo "python：${PY}（$("$PY" -V 2>&1)）"

# ── 封面用解释器（ADR-0039）──────────────────────────────────────────
# make_cover.py 依赖 Pillow，而 Pillow 只装在 wewrite 的 venv 里；用系统
# python 跑它会以「未安装 Pillow」失败，且这个失败发生在第 3 段——那时
# 稿子已经写好、简报还没出，等于每天白跑一次。定时任务不经过交互式 shell，
# 没有"记得换个 python"的机会，所以这里把解释器钉死。
# 退化路径：venv 不存在时回落到 ${PY}，让错误在封面这一步显式暴露，
# 而不是静默跳过封面（无封面会被发布门 1 拦下）。
if [[ -x "$REPO/.uv-tools/wewrite/bin/python" ]]; then
  COVER_PY="$REPO/.uv-tools/wewrite/bin/python"
else
  COVER_PY="$PY"
fi

# ── node 解释器（ADR-0052）──────────────────────────────────────────
# 同一个理由的第二个实例：发布（publish.py）、表现数据（performance.py）都经
# run_toutiao() 调 node，而 node 由 nvm 装在用户目录、不在系统 PATH 里。
# 交互式 shell 里 `node` 能用，是因为 nvm 的 init 脚本改过 PATH；**定时任务的
# shell 没有这段 init**，于是 `FileNotFoundError: 'node'`。
# 2026-10-21 这一天它造成了两次失败：第 3 段发布直接崩，②b 表现数据则被
# `|| true` + `json.load` 兜住，静默丢掉一整天的数据（连着两天没人发现）。
# 退化路径：找不到就把 PATH 原样留着，让错误在调用点显式暴露。
node_dir() {
  # 优先用已验证过的版本（2026-10 之前几次成功发布都跑在 v22.23.3 上），
  # 否则取 nvm 目录下最新的一版。不猜，只在这两档里选。
  if [[ -x "$HOME/.nvm/versions/node/v22.23.3/bin/node" ]]; then
    printf '%s' "$HOME/.nvm/versions/node/v22.23.3/bin"
    return 0
  fi
  local c last=""
  for c in "$HOME"/.nvm/versions/node/*/bin; do
    [[ -x "$c/node" ]] && last="$c"
  done
  [[ -n "$last" ]] && { printf '%s' "$last"; return 0; }
  return 1
}
if ! command -v node >/dev/null 2>&1; then
  if NODE_DIR="$(node_dir)"; then
    export PATH="$NODE_DIR:$PATH"
  else
    echo "警告：PATH 与 ~/.nvm 下都找不到 node——发布与表现数据会失败（ADR-0052）" >&2
  fi
fi

# ── 头条 cookie 文件（ADR-0071/0072）────────────────────────────────
# 表现数据回路默认走纯 HTTP api 后端（不需要 Chrome），而 api 后端要凭据：
# 本机可以直读本机 Chrome 的 cookie 库，**服务器上没装 Chrome**，只能注入。
# 注入文件放在 <repo>/.newsagg/toutiao-cookies.json（ADR-0071 的 .gitignore
# 放行规则保证它不入库），由 `scripts/export-cookies.py` 导出后 scp 过去。
#
# 2026-10-27 服务器实测踩到：不设这个环境变量时，performance.py 回落到
# from_browser() → 「找不到浏览器 chrome 的 profile」→ 表现读回失败，
# 而它被 `|| true` 兜住 → **排序参考与降频判据悄悄沿用旧值**。
# 所以这里：文件存在就自动指过去；不存在就说清楚。
if [[ -z "${NEWSAGG_COOKIE_FILE:-}" && -f "$REPO/.newsagg/toutiao-cookies.json" ]]; then
  export NEWSAGG_COOKIE_FILE="$REPO/.newsagg/toutiao-cookies.json"
fi
if [[ -n "${NEWSAGG_COOKIE_FILE:-}" ]]; then
  if [[ -r "$NEWSAGG_COOKIE_FILE" ]]; then
    echo "cookie：$NEWSAGG_COOKIE_FILE"
  else
    echo "警告：NEWSAGG_COOKIE_FILE 指向 ${NEWSAGG_COOKIE_FILE}，但读不到——表现读回会失败" >&2
  fi
fi

log() { printf '\n=== %s ===\n' "$*"; }

# ── 运行状态（简报 ⑤ 段与看守的输入）──────────────────────────────
# ADR-0038：`.newsagg/` 不入库，故看守读不到它；但简报可以，所以每次运行
# 无论成败都先落一份 run-status，再由简报把它呈现给人。
git_no_proxy() {  # 透传参数给 git，但禁用配置里的代理（空串 = 不用代理）
  git -c http.proxy= -c https.proxy= "$@"
}

# ── 当前分支（ADR-0065）──────────────────────────────────────────
# 直到 2026-10-25，编排器里写死了 `origin main` 与裸 `git pull --rebase`：
# 前者假定「当前分支就是 main」，后者依赖 upstream 跟踪。两个假定在仓库只有
# main 时永远成立，所以几个月没人发现。2026-10-26 仓库切到 feat/browserless-skills
# （无 upstream、远端也没有这个分支），于是：
#   ① 裸 `git pull --rebase` 报 `fatal: no upstream configured`——它既不在
#      冲突正则里，也不在网络正则里 → 走 fail_soft → **当晚整轮死在第①步**；
#   ② 就算活下来，收尾 `git push origin main` 推的是**本地 main**，今天的提交
#      留在特性分支上，而脚本照样打印「已推送 origin/main」。
# 所以：一律按**当前分支**取远端，并在起跑时把分支名说一遍。
current_branch() {
  local b
  b="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ -z "$b" || "$b" == "HEAD" ]]; then echo "main"; else echo "$b"; fi
}

BRANCH="$(current_branch)"
if [[ "$BRANCH" != "main" ]]; then
  echo "分支：${BRANCH}（不是 main）——本轮的所有提交都会落在这个分支上" >&2
  echo "      这与 ADR-0065 有关：编排器不再假定你在 main 上。" >&2
fi

write_status() {  # $1 ok(1/0)  $2 stage  $3 reason  $4 detail  $5 degraded(可空)
  # degraded 是「跑完了但有让步」：与 ok=0 不同，它不该让看守告警，但必须让人看见。
  ST_OK="$1" ST_STAGE="$2" ST_REASON="$3" ST_DETAIL="${4:-}" ST_DEGRADED="${5:-}" \
  ST_DATE="$DATE" ST_MODE="$MODE" \
  "$PY" - <<'PY'
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
_dg = (os.environ.get("ST_DEGRADED") or "").strip()
if _dg:
    rec["degraded"] = _dg[-2000:]
with open(p, "w", encoding="utf-8") as f:
    json.dump(rec, f, ensure_ascii=False, indent=2)
print(json.dumps({k: rec[k] for k in ("ok", "stage", "reason")}, ensure_ascii=False))
PY
}

brief_now() {
  "$PY" .agents/skills/newsagg-brief/scripts/brief.py --date "$DATE" \
    || echo "[X] 简报生成失败——看守会因此告警（ADR-0006：简报必须无条件每日存在）" >&2
}

# ADR-0074：把发布账本同步成入库归档（reports/publish-ledger.jsonl）。
# 账本分两份：运行时（.newsagg/，不入库，当前环境的第一手事实）与归档
# （reports/，入库，跨环境的合并视图）。没有归档，服务器上就没有账本，
# 平台作品与本地成稿的关联会断（实测 11 篇只匹配 9 篇）。
# 这一步幂等，且失败不阻塞——归档写不进去不该让发布失败。
sync_ledger_archive() {
  if [[ -n "$NO_GIT" ]]; then echo "（--no-git：跳过账本归档）"; return 0; fi
  "$PY" - <<'PY'
import sys
sys.path.insert(0, ".agents/lib")
try:
    import publish_ledger as pl
except Exception as exc:
    print(f"[!] 账本归档跳过：模块加载失败（{exc}）")
    raise SystemExit(0)
try:
    res = pl.sync_archive(pl.repo_root())
except Exception as exc:
    print(f"[!] 账本归档跳过：{exc}")
    raise SystemExit(0)
print(f"  账本归档：运行时 {res['runtime_records']} 条 + 归档 {res['archive_records_before']} 条 "
      f"→ 合并 {res['merged']} 条，写入 {res['written']} 条"
      + (f"（跳过 {res['skipped_for_credentials']} 条：含凭证）"
         if res["skipped_for_credentials"] else ""))
PY
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
  # 降级轮不 push：① 没拉到远端，我们不知道远端有没有新提交，push 可能被拒；
  # 也可能把本地「没 rebase 过」的历史直接推上去。本地已提交，产出不会丢，
  # 等你在有网络时 push 一次即可（简报⑤段会写明这件事）。
  if [[ "${PULL_DEGRADED:-0}" == "1" ]]; then
    echo "[!] 跳过 push：本轮未能从远端同步（网络不可达），远端状态未知。改动已本地提交，交人工 push（ADR-0061）" >&2
    return 1
  fi
  if git push -u origin "$BRANCH" >/tmp/newsagg-push.out 2>&1; then
    echo "已推送 origin/${BRANCH}"
    return 0
  fi
  # push 同样可能只是代理的 github 规则坏了：补一次直连。
  if grep -qiE "$NET_ERR_RE" /tmp/newsagg-push.out; then
    printf '[!] push 走代理失败，改用直连重试一次\n' >&2
    if git_no_proxy push -u origin "$BRANCH" >/tmp/newsagg-push.out 2>&1; then
      PROXY_FALLBACK=1
      echo "已推送 origin/${BRANCH}（直连；你的 git 代理对 github 不可用）"
      return 0
    fi
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

dec_field() {  # $1 = 键路径；$2 = 列表分隔符（默认换行）。先在 decision 对象里找，再回退到顶层（abstain_reason 等）
  DEC="$DEC" FIELD="$1" FIELD_SEP="${2-$'\n'}" "$PY" - <<'PY'
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
    # 默认换行分隔：证据/摘要文本自身含英文逗号，用逗号切会切断千分位数字（ADR-0047）
    cur = os.environ.get("FIELD_SEP", "\n").join(str(x) for x in cur)
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
  local attempt=1 max=3 detail

  # ★ 上一轮失败留下的脏工作区会让这一轮**永远**拉不下来（2026-10-27 服务器实测）。
  # 失败的运行会写一份简报（ADR-0006：简报必须无条件每日存在），而收尾的
  # `sync_push` 先 `git add -A` 再 `git commit`；commit 失败（比如服务器上
  # 没有 git 身份）时**索引就留在半暂存状态**。下一轮 ① git pull 撞上它：
  #   error: cannot pull with rebase: You have unstaged changes.
  #   error: additionally, your index contains uncommitted changes.
  # → 又失败 → 又 `git add -A` → **死锁，一轮都跑不动**。第一次失败是偶发，
  # 之后每一轮都必然失败，而原因藏在两轮之前。
  #
  # 处置：**只处理系统自己每天写的路径**（`reports/`），不碰任何可能是
  # 你改的文件（outputs/、knowledge/、platforms/、style/…）。判据是
  # 「这个路径是本系统每天写的」，不是「这个文件我看着像垃圾」——
  # 前者可判定，后者是猜测（ADR-0018：不替人决定什么该丢）。
  # 处置方式是**提交而不是丢弃**：reports/ 按 ADR-0012 本来就该入库
  # （简报是历史档案），它不是垃圾，是当天该留下的东西。
  if [[ -n "$(git status --porcelain -- reports/ 2>/dev/null)" ]]; then
    echo "[!] 工作区有上一轮运行留下的痕迹（reports/），先单独提交再同步：" >&2
    git status --porcelain -- reports/ | head -10 | sed 's/^/    /' >&2
    if git add -A -- reports/ 2>/dev/null && \
       git commit -q -m "NewsAgg 运行痕迹归档（${DATE}）" 2>/dev/null; then
      echo "[✓] 已把 reports/ 单独提交（内容是简报与表现数据）" >&2
    else
      echo "[!] reports/ 无法单独提交（可能与其它改动冲突），继续尝试拉取" >&2
    fi
  fi

  # 显式点名远端分支，不用裸 `git pull`（它依赖 upstream 跟踪，而新分支没有）。
  # 远端还没有这个分支是**正常状态**（本地刚建、还没推过），不是失败：
  # 本轮照常跑，收尾 push 时用 -u 把分支建到远端，下一轮就能同步了。
  if ! git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>"$err"; then
    if grep -qiE "$NET_ERR_RE" "$err"; then
      : # 连不上远端，交给下面的重试与降级逻辑处理
    else
      echo "远端还没有分支 ${BRANCH}（本地新建的分支）——本轮跳过同步，收尾 push 时建立"
      return 0
    fi
  fi

  while true; do
    if git pull --rebase origin "$BRANCH" >/tmp/newsagg-pull.out 2>"$err"; then
      echo "已同步到 $(git rev-parse --short HEAD)"
      return 0
    fi
    git rebase --abort >/dev/null 2>&1 || true
    detail="$(tail -15 "$err")"

    # 冲突不是瞬时错误：重试只会三次撞在同一处，而且每次都动工作区。
    # 冲突即熔断、不自行合并——自动解决可能静默丢弃你在风格或合规规则上的修改。
    if grep -qiE 'CONFLICT|Automatic merge failed|could not apply' "$err"; then
      "$PY" - <<'PY' || true
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

    # ⚠️ 2026-10-20 实测：一次 HTTP/2 framing 抖动
    # （`RPC 失败。curl 16 Error in the HTTP2 framing layer`）就让整天的主流程
    # 中止了，而手工重试**立刻**成功。网络抖动不是「工作区不干净」——
    # 把它判成后者，会让系统在最平常的失败上停工（ADR-0048）。
    # 只对传输层错误重试；认证失败/仓库不存在/无权限重试无益，直接报。
    RETRY_ERR_RE='RPC 失败|HTTP2 framing|Could not resolve host|Connection reset|Connection timed out|early EOF|The remote end hung up'
    if [[ "$attempt" -lt "$max" ]] && grep -qiE "$RETRY_ERR_RE"; then
      echo "拉取遇到传输层错误（第 ${attempt} 次），2 秒后重试……" >&2
      echo "  $(echo "$detail" | head -2 | tr '\n' ' ')" >&2
      attempt=$((attempt + 1))
      sleep 2
      continue
    fi
    break
  done

  # 「您有未暂存的变更」与上面那批错误不是一回事：它意味着**本地有没提交的
  # 改动**，而这在生产形态里几乎总是「上一轮运行没跑完」或「有人正在改」。
  # git 只输出一行英文，不说是哪些文件脏；无人值守时没人能去翻工作区，
  # 所以这里把脏文件清单补进 detail——否则每次只看到
  # 「error: cannot pull with rebase」，不知道该 commit 还是该 stash。
  # 2026-10-25 实测：15:05 那次失败就是这样，我为了修每日 1 篇硬闸而改了
  # publish.py/brief.py 但还没提交，run_all 的 git_pull 撞上未暂存的变更。
  if grep -q 'unstaged changes' "$err"; then
    local dirty
    dirty="$(git status --porcelain | head -20 | sed 's/^/    /')"
    if [[ -n "$dirty" ]]; then
      detail="${detail}

工作区有未提交的改动（前 20 项）：
${dirty}
处置：先 commit 或确认可丢弃后 checkout。无人值守时不自行提交——
ADR-0018：人的改动优先，系统不得替人决定提交什么。"
    fi
  fi

  # 走到这里说明重试已耗尽。分两种，处置完全不同：
  #
  # ① 网络不可达：本地状态是自洽的，只是没拿到远端的最新改动。这种情况下
  #    停掉整天是错的——2026-10-25 实测，本机代理节点挂掉，github.com 直连
  #    超时、走代理 LibreSSL SSL_ERROR_SYSCALL，而国内站点（百度、头条）全部
  #    200。采集、分拣、写作、发布没有一步需要 github，唯一需要它的是
  #    同步你那边的编辑。为了一次代理抖动丢掉整天的产出，代价与收益完全不成比例。
  #    所以：降级继续，用本地状态跑完，收尾不 push（我们不知道远端有没有新提交，
  #    盲目 push 可能被拒、也可能把本地 rebase 到一半的状态推上去）。
  #
  # ② 其余（工作区脏、认证失败、仓库不存在、无权限）：这些要么需要人动手，
  #    要么继续跑也没有意义，保持熔断。
  if grep -qiE "$NET_ERR_RE" "$err"; then
    # 先试一次禁用代理直连：代理对 github 的那一跳坏了、而直连通，是真实场景。
    printf '\n[!] ① 走代理失败（重试 %s 次），改用直连重试一次\n' "$((attempt - 1))" >&2
    if git_no_proxy pull --rebase origin "$BRANCH" >/dev/null 2>>"$err"; then
      PROXY_FALLBACK=1
      DEGRADED_NOTE="① 你配置的 git 代理（$(git config --get http.proxy || echo '未配置')）对 github 不可用，本轮已改走直连完成同步（同步本身没问题）。建议修一下 Clash 的 github 规则，否则每轮都要多试一次才通。"
      printf '[✓] ① 直连成功——问题在代理的 github 规则，不在网络\n' >&2
      return 0
    fi
    PULL_DEGRADED=1
    DEGRADED_NOTE="① 未能从远端同步（网络不可达，已重试 $((attempt - 1)) 次）：本轮用本地状态跑完，收尾未 push。远端若在你的机器上有新提交，本轮看不到它们——风格与教学副本可能是旧的。"
    printf '\n[!] ① 远端不可达（已重试 %s 次）→ 降级继续，收尾不 push\n' "$((attempt - 1))" >&2
    printf '    原因：%s\n' "$(echo "$detail" | head -2 | tr '\n' ' ')" >&2
    printf '    这不熔断：采集/分拣/写作/发布都不需要 github（ADR-0061）\n' >&2
    return 0
  fi

  fail_soft "git_pull" "拉取失败（已重试 $((attempt - 1)) 次；非冲突，见 detail）" "$detail" 0
}

# ── ② 风格学习（ADR-0026：必须早于写作）──────────────────────────
style_learn() {
  log "② 风格学习（若你改过教学副本）"
  # ⚠️ 输出文件**不能**写死 /tmp：那是 POSIX 路径，而这段 Python 里的字符串
  # 不经过 MSYS 路径转换——Windows 上的 Python 会把它解析成 `C:\tmp\...`，
  # 与 bash 的 /tmp 不是同一个目录，于是「文件读不到 → 静默跳过」。
  # 2026-10-28 在本机实测：表现数据明明读回成功（reports/performance.jsonl
  # 有新增），这里却报「无法解析」。改用仓内临时目录，并把路径**作为参数**
  # 传给 Python（argv 会被 MSYS 正确转换）。
  local out="$REPO/.newsagg/tmp/style.json"
  local err="$REPO/.newsagg/tmp/style.err"
  mkdir -p "$REPO/.newsagg/tmp"
  # stderr **单独落盘**：Windows 下 stderr 是 GBK，混进 JSON 会让 json.load 以
  # UnicodeDecodeError 失败，表现成「无法解析」——失败原因却看不见（ADR-0052）。
  "$PY" .agents/skills/newsagg-triage/scripts/style_learn.py > "$out" 2>"$err" || true
  "$PY" - "$out" "$err" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("（风格学习输出无法解析，跳过）")
    try:
        tail = [l for l in open(sys.argv[2], encoding="utf-8",
                               errors="replace").read().splitlines() if l.strip()]
        if tail:
            print(f"  错误末行：{tail[-1][:300]}")
    except Exception:
        pass
    raise SystemExit(0)
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
#
# 后端：默认 `api`（纯 HTTP，不需要 Chrome）。理由是内存——浏览器路径单次读回
# 要 1GB+ 内存（ADR-0071 实测），而服务器只有 2GB。浏览器后端保留为兜底，
# api 失败时脚本内部自动回落。
PERF_BACKEND="${NEWSAGG_PERF_BACKEND:-api}"
performance() {
  log "②b 表现数据（循环 C：读回 + 自身基准 + 降频判定，后端 ${PERF_BACKEND}）"
  # 同 ② 的理由：不写死 /tmp，路径作为参数传给 Python（MSYS 会转换 argv）。
  local out="$REPO/.newsagg/tmp/perf.json"
  local err="$REPO/.newsagg/tmp/perf.err"
  mkdir -p "$REPO/.newsagg/tmp"
  "$PY" .agents/skills/newsagg-performance/scripts/performance.py \
    --backend "${PERF_BACKEND}" > "$out" 2>"$err" || true
  "$PY" - "$out" "$err" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    # ADR-0052：这里原来只打印「无法解析」，把 traceback 留在临时文件里没人看，
    # 于是「node 不在 PATH」被当成「数据读不到」静默丢了整整两天。
    # 现在把最后一行原样带出来——失败必须自带它的原因。
    # 先看 stderr（真正的失败原因在这里），再看 stdout 末行。
    tail = ""
    for p in (sys.argv[2], sys.argv[1]):
        try:
            lines = [l for l in open(p, encoding="utf-8",
                                     errors="replace").read().splitlines() if l.strip()]
        except Exception:
            continue
        if lines:
            tail = lines[-1][:300]
            break
    tail = tail or "(空输出)"
    print("（表现数据输出无法解析——本次读回失败，不影响本轮运行）")
    print(f"  输出末行：{tail}")
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
  skip="$("$PY" .agents/skills/newsagg-performance/scripts/performance.py --check-skip 2>/dev/null \
    | "$PY" -c 'import json,sys
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
  if ! "$PY" .agents/skills/newsagg-collect/scripts/collect.py --base "$HOT_BASE"; then
    fail_soft "collect" "采集失败（全部源不可达）" "热榜服务 $HOT_BASE" 1
  fi
}

# ── ③b 分拣准备（组装输入包）────────────────────────────────────
triage_input() {
  log "③b 分拣：组装输入包"
  [[ -f "$CAND" ]] || fail_soft "triage_input" "找不到候选文件" "$CAND" 1
  "$PY" .agents/skills/newsagg-triage/scripts/triage.py || \
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
     · 注意输入包顶层的 backlog.due（ADR-0083）：里面每个议题都已达
       新建门槛（同一议题第 2 次出现）。对它们只能二选一——这一轮为
       它建卡，或写 no_card_reason 说明为什么还不建。多个议题同时到期
       时 no_card_reason 写成 {"议题名": "理由"}。
     · no_card_available 的每个条目必须带 issue（议题名，台账的计数键）。
       缺了它这一条就进不了台账，下次出现也认不出来是同一件事。
  3. 跑校验：
     "$PY" .agents/skills/newsagg-triage/scripts/triage.py \\
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
  "$PY" .agents/skills/newsagg-triage/scripts/build_brief.py --decision "$DEC" \
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
    fail_soft "write" "写作失败（退出码 ${RC}）" "" 1
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
不写这个文件也能发布（回退到标题推导），但推导只会切分标题、选不出论据——
2026-10-20 那次就因此产出了一张顶部小字与正文重复的封面（ADR-0044 发现五）。
写完后执行：
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
PHOTO_ARG=()
if [[ -f "$COVER_SPEC" ]]; then
  COVER_SPEC_ARG=(--spec "$COVER_SPEC")
else
  # 没有 spec 不等于失败（封面仍会生成），但它是「写作阶段漏了一步」的信号。
  # 2026-10-20 就是静默走了这条路，产出一张复述标题的封面而无人察觉（ADR-0044 发现五）。
  echo "注意：未找到封面论据 ${COVER_SPEC}，回退到标题推导（封面会复述标题的部分内容）" >&2
fi

# ── 照片层（ADR-0048）──────────────────────────────────────────────
#
# 只有 spec 里写了 `photo_query` 才取图。这不是省事，是**责任划分**：
# 只有写作阶段知道这篇文章讲的是什么，也只有它能判断「机房照片配一篇
# 讲 Codex 迁移的文章」算不算图文无关。没有 query 就说明没人做过这个判断，
# 此时加一张来路不明的照片，只是把「图文无关」变成系统自动决定的。
if [[ -f "$COVER_SPEC" ]]; then
  PHOTO_QUERY="$("$PY" - "$COVER_SPEC" <<'PY' || true
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("photo_query") or "")
except Exception:
    print("")
PY
)"
  if [[ -n "$PHOTO_QUERY" ]]; then
    PHOTO_JSON="$(mktemp -t newsagg-photo.XXXXXX)"
    set +e
    "$COVER_PY" .agents/skills/newsagg-publish/scripts/fetch_photo.py \
      --query "$PHOTO_QUERY" > "$PHOTO_JSON" 2>/dev/null
    set -e
    PHOTO_PATH="$("$PY" - "$PHOTO_JSON" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get("local") or "" if d.get("ok") else "")
except Exception:
    print("")
PY
)"
    if [[ -n "$PHOTO_PATH" ]]; then
      PHOTO_ARG=(--photo "$PHOTO_PATH")
      echo "照片：${PHOTO_PATH}（查询：${PHOTO_QUERY}）"
    else
      # 取不到照片不是错误：回退到纯模板封面。但必须说出来——
      # 「今天这张封面本来该有照片」这个信息，只有这里能留下。
      echo "注意：未能取到合格照片（查询：${PHOTO_QUERY}），回退到纯模板封面" >&2
    fi
    rm -f "$PHOTO_JSON"
  fi
fi

# ⚠️ 必须写 ${A[@]+"${A[@]}"}：bash 3.2（macOS 自带）在 set -u 下展开**空数组**
# 会报 "A[@]: unbound variable" 并终止脚本。没有 spec 时（最常见的情形）
# 这一步会直接失败——一个只在"没写封面 spec"时才触发的静默故障。
COVER_JSON="$(mktemp -t newsagg-cover.XXXXXX)"
set +e
"$COVER_PY" .agents/skills/newsagg-publish/scripts/make_cover.py --slug "$SLUG" \
  ${COVER_SPEC_ARG[@]+"${COVER_SPEC_ARG[@]}"} \
  ${PHOTO_ARG[@]+"${PHOTO_ARG[@]}"} > "$COVER_JSON"
COVER_RC=$?
set -e
# 把封面的自检结果打出来。ADR-0044 发现六：封面质量问题的旧形态是**静默**——
# 自检只回报常量、警告没人读。既然无人值守时没有第二双眼睛，
# 这些警告就必须落到运行输出与简报里，否则等于没检查。
"$PY" - "$COVER_JSON" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for w in d.get("warnings") or []:
    print(f"[封面警告] {w}", file=sys.stderr)
for p in d.get("problems") or []:
    print(f"[封面自检未通过] {p}", file=sys.stderr)
drawn = d.get("drawn") or []
if drawn:
    print("封面元素（缩略图 320px 宽下的实际高度）：", file=sys.stderr)
    for e in drawn:
        print(f"  {e['element']:9} {e['size']:>4}px -> {e['px_at_thumb']}px", file=sys.stderr)
PY
# 留一份自检结果给简报。无人值守时 stderr 没人看，而「封面只是在复述标题」
# 恰恰是那种「不报错、只是效果差」的问题——它必须落到 reports/ 里（ADR-0044 发现六）。
mkdir -p .newsagg/covers
cp "$COVER_JSON" ".newsagg/covers/${DATE}.report.json" 2>/dev/null || true
rm -f "$COVER_JSON"
if [[ $COVER_RC -ne 0 ]]; then
  fail_soft "cover" "封面生成失败（解释器：${COVER_PY}；spec：${COVER_SPEC}）" "$SLUG" 1
fi

log "⑤ 发布（${MODE}）"
if [[ "$MODE" == "publish" ]]; then PUB_FLAG="--mode publish"; else PUB_FLAG=""; fi
# ADR-0037：把承接的观点卡一并写进发布账本。循环 C 读回表现数据后要按卡
# 归因（"哪些卡跑出了数据"），而账本是唯一的关联点——文章标题会被你改，
# slug 不会，卡名只能在这一刻从决策文件里取到。dry-run 也记，便于验证。
CARD="$(dec_field card_file)"
PUB_OUT="$(mktemp)"
set +e
"$PY" .agents/skills/newsagg-publish/scripts/publish.py \
  --slug "$SLUG" --cover "outputs/covers/${SLUG}.png" \
  ${CARD:+--card-file "$CARD"} $PUB_FLAG 2>&1 | tee "$PUB_OUT"
PUB_RC=${PIPESTATUS[0]}
set -e
if [[ $PUB_RC -ne 0 ]]; then
  # 闸门不是故障（2026-10-31 实测）：publish.py 拒绝发布会打印
  # `{"ok": false, "stage": "daily_limit", ...}`，而这里原先只看退出码，
  # 于是「今日额度已用」被渲染成「系统运行途中失败了」，简报⑤还教人
  # 「修复后重跑」——重跑仍被拦，照它把闸门去掉就是重复发文（ADR-0059）。
  # 这里只认这一个 stage：它是**设计内的拒绝**，必须与失败分开报。
  if grep -q '"stage":[[:space:]]*"daily_limit"' "$PUB_OUT" 2>/dev/null; then
    rm -f "$PUB_OUT"
    fail_soft "daily_limit" \
      "今日额度已用（ADR-0014：每个日历日最多 1 篇），闸门按设计拒绝发布；成稿已留在本地，未进草稿箱" \
      "$SLUG" 1
  fi
  rm -f "$PUB_OUT"
  fail_soft "publish" "发布未成功（退出码 ${PUB_RC}；熔断与反查结果见发布账本）" "$SLUG" 1
fi
rm -f "$PUB_OUT"

# ── ⑥ 认知写回（ADR-0026：必须在发布成功之后）────────────────────
if [[ "$MODE" == "publish" ]]; then
  if [[ -n "$CARD" ]]; then
    log "⑥ 认知写回（追加未复核修订到 ${CARD}）"
    WB_ARGS=(--card "$CARD" --event "$(dec_field event.summary)" --article "$SLUG" --date "$DATE")
    [[ -n "$(dec_field whats_new)" ]] && WB_ARGS+=(--judgment "$(dec_field whats_new)")
    [[ -n "$(dec_field event.key_evidence)" ]] && WB_ARGS+=(--evidence "$(dec_field event.key_evidence)")
    if "$PY" .agents/skills/newsagg-triage/scripts/writeback.py "${WB_ARGS[@]}"; then
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
# ADR-0074：先同步账本归档，再出简报——简报要读的「今天发了什么」来自
# 合并视图，归档必须在简报之前落好。归档在 reports/，会被同一次 commit 带走。
sync_ledger_archive
write_status 1 "complete" "运行完成" "slug=${SLUG} mode=${MODE}" "$DEGRADED_NOTE"
brief_now
if ! sync_push "发布 ${SLUG}（${MODE}）"; then
  if [[ "${PULL_DEGRADED:-0}" == "1" ]]; then
    printf '\n> ⚠ 本轮**未 push**（设计如此）：未能从远端同步，远端状态未知。产出已本地提交，\n> 有网络后手动 \`git push origin ${BRANCH}\` 一次即可。\n' >> "$BRIEF"
  else
    printf '\n> ⚠ 本次运行结束时 push 失败：产出仍留在本地，未同步到远端。看守会因此告警。\n' >> "$BRIEF"
    sync_push "push 重试" || true
  fi
fi

echo
echo "完成。简报：${BRIEF}"
