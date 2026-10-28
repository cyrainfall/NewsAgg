#!/usr/bin/env bash
# 确保本地热榜服务（DailyHotApi）在 6688 上可用。
#
# 为什么需要它：采集层的前置条件是本地 DailyHotApi（ADR-0029——公开实例已不可用）。
# 四条定时任务都依赖它，而「怎么起这个服务」此前只写在 SKILL.md 里一行手工命令，
# 没人保证它在你关机/重启后还活着。这里把它变成一句可判定的调用，避免四条任务
# 各写一份启动命令然后漂移（本仓库已因「多份副本漂移」吃过三次亏：
# ADR-0042 / ADR-0050 / ADR-0077）。
#
# 判据：已在跑就立刻返回 0；没在跑就 detached 启动（脱离调用者生命周期，
# 任务结束后服务继续活着），并在最多 ~21 秒内等根路径 200。
#
# 用法：bash scripts/ensure-hot.sh

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${NEWSAGG_HOT_BASE:-http://127.0.0.1:6688}"
DIR="$REPO/reference/DailyHotApi"

up() {
  # 用 curl（Git for Windows 自带），不经管道——ADR-0028：退出码要能判。
  curl -fsS -m 3 -o /dev/null "$BASE/" >/dev/null 2>&1
}

if up; then
  echo "hot: 已在运行（${BASE}）"
  exit 0
fi

if [[ ! -d "$DIR" ]]; then
  echo "hot: 找不到 ${DIR}（reference/ 不入库，需重新 clone，ADR-0012）" >&2
  exit 1
fi

TSX="$DIR/node_modules/tsx/dist/cli.mjs"
if [[ ! -f "$TSX" ]]; then
  echo "hot: 找不到 ${TSX}（node_modules 不入库，需在 ${DIR} 里装依赖）" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "hot: PATH 里找不到 node（ADR-0052 同型：定时任务 shell 没有 nvm init）" >&2
  exit 1
fi

# NODE_ENV=development 是硬要求：不设它服务根本不起（DESIGN.md 阶段 0 第 7f 条实测）。
cd "$DIR" || exit 1
export PORT=6688
export NODE_ENV=development

# detached：任务跑完不要把服务带走（nohup + & + 不管 stdin）。
nohup node "$TSX" src/index.ts >> dailyhot.out.log 2>> dailyhot.err.log < /dev/null &
disown 2>/dev/null || true

for _ in $(seq 1 30); do
  sleep 0.7
  if up; then
    echo "hot: 已启动（${BASE}）"
    exit 0
  fi
done

echo "hot: 启动后约 21 秒仍不通，错误日志见 ${DIR}/dailyhot.err.log" >&2
exit 1
