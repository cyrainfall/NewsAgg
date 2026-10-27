#!/usr/bin/env bash
# 把 deploy/ 里的 systemd 单元装到服务器上。
#
# 用法（在服务器上）：bash deploy/install-timers.sh [--enable-only]
#
# 装什么：3 个采集 timer（08/13/18）+ 1 个主流程 timer（21:00）。
# 为什么用 systemd 而不是 DSH 的 schedule 域：DSH 的 schedule 域只在
# web-app bundle 里，headless 建不了（ADR-0077）。
set -uo pipefail

REPO="/opt/newsagg/repo"
DEPLOY="${REPO}/deploy"
UNIT_DIR="/etc/systemd/system"
LOG_DIR="/opt/newsagg/logs"

UNITS=(
  newsagg-collect-0800.service newsagg-collect-0800.timer
  newsagg-collect-1300.service newsagg-collect-1300.timer
  newsagg-collect-1800.service newsagg-collect-1800.timer
  newsagg-main-pipeline.service newsagg-main-pipeline.timer
)

if [[ ! -d "${DEPLOY}" ]]; then
  echo "[!] 找不到 ${DEPLOY}——请先在服务器上 git pull" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"

# 模型凭证：把 DSH 凭证库里的 key 导出成一个 0600 的 EnvironmentFile。
# 为什么不在 unit 里写 Environment=DEEPSEEK_API_KEY=...：unit 文件在
# /etc/systemd/system 下是全局可读的，而那是账号凭证。
# 为什么不能让 dsh headless 自己读：headless 跑非交互任务时不读 DSH 的
# 凭证库（实测报 MISSING_CREDENTIAL: llm-deepseek），所以必须显式导出。
CRED_FILE="/etc/newsagg/dsh-credentials.env"
if [[ ! -f "${CRED_FILE}" ]]; then
  mkdir -p /etc/newsagg
  if [[ -f /root/.dsh/.credentials.yaml ]]; then
    python3 - > "${CRED_FILE}" <<'PY'
import re, sys
text = open("/root/.dsh/.credentials.yaml", encoding="utf-8").read()
m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", text)
if not m:
    sys.exit("凭证库里找不到 DEEPSEEK_API_KEY")
print(f"DEEPSEEK_API_KEY={m.group(1)}")
PY
    chmod 600 "${CRED_FILE}"
    echo "已生成 ${CRED_FILE}（0600）"
  else
    echo "[!] 找不到 /root/.dsh/.credentials.yaml，无法自动导出模型凭证" >&2
    echo "    请手动创建 ${CRED_FILE}，内容一行：DEEPSEEK_API_KEY=<你的 key>" >&2
    exit 1
  fi
fi

for u in "${UNITS[@]}"; do
  install -m 0644 "${DEPLOY}/${u}" "${UNIT_DIR}/${u}" || exit 1
done
chmod +x "${DEPLOY}/run-prompt.sh"

systemctl daemon-reload || exit 1

if [[ "${1:-}" != "--enable-only" ]]; then
  # 启 Xvfb：浏览器只在发布那一刻起来，但 Xvfb 要一直在（ADR-0023）
  if ! pgrep -f "Xvfb :99" > /dev/null 2>&1; then
    nohup Xvfb :99 -screen 0 1440x900x24 -nolisten tcp > "${LOG_DIR}/xvfb.log" 2>&1 &
    sleep 2
    echo "Xvfb :99 已启动"
  else
    echo "Xvfb :99 已在运行"
  fi
fi

for t in newsagg-collect-0800 newsagg-collect-1300 newsagg-collect-1800 newsagg-main-pipeline; do
  systemctl enable --now "${t}.timer" || exit 1
done

echo "--- 已启用的定时器 ---"
systemctl list-timers --no-pager 'newsagg-*' || exit 1
echo "--- 日志目录：${LOG_DIR} ---"
