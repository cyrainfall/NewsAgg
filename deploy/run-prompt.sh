#!/usr/bin/env bash
# 跑一条 prompt（文件）通过 dsh headless，并把输出与退出码记到日志。
#
# 为什么是 systemd timer 而不是 DSH 的 schedule 域（ADR-0077）：
# DSH 的 schedule 域只在 web-app bundle 里，headless/base bundle 不含
# dsh-schedule → `dsh headless` 建不了定时任务。systemd 是服务器自己的
# 调度器，不依赖 GUI，服务器重启也能续上。
#
# 用法：run-prompt.sh <prompt 文件> [日志目录]
set -uo pipefail

PROMPT_FILE="${1:?用法: run-prompt.sh <prompt 文件> [日志目录]}"
LOG_DIR="${2:-/opt/newsagg/logs}"
REPO="/opt/newsagg/repo"

if [[ ! -f "${PROMPT_FILE}" ]]; then
  echo "[!] prompt 文件不存在：${PROMPT_FILE}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
STAMP="$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)"
LOG="${LOG_DIR}/$(basename "${PROMPT_FILE}" .md)-${STAMP}.log"

# dsh 在 /usr/local/node/bin，不在非交互 shell 的 PATH 里（ADR-0052 教训）
export PATH="/usr/local/node/bin:${PATH}"
export HOME="${HOME:-/root}"
export TZ="Asia/Shanghai"

# 仓库内的 DSH 技能目录要能被识别：以仓库为 cwd（DSH 技能目录约定见
# packages/skill/skill-filesystem/src/index.ts:244-264）
cd "${REPO}" || exit 1

{
  echo "=== 开始 ${STAMP} ==="
  echo "prompt: ${PROMPT_FILE}"
  echo "---"
} >> "${LOG}"

# 任务文本从 stdin 读（`dsh headless -`），避免超长 prompt 变成命令行参数
if dsh --profile headless - < "${PROMPT_FILE}" >> "${LOG}" 2>&1; then
  RC=0
else
  RC=$?
fi

{
  echo "---"
  echo "=== 结束 ${STAMP}，退出码 ${RC} ==="
} >> "${LOG}"

echo "日志：${LOG}"
exit "${RC}"
