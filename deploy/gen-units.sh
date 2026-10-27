#!/usr/bin/env bash
# 生成 systemd 单元（service + timer）。
#
# 为什么要生成器而不是手写四份 unit：
# 2026-10-27 装定时器时手写的那批 unit 出了两个错——
#   ① OnCalendar 写成 `*-*-* 0800:00:00`（systemd 要的是 `08:00:00`），
#      报 "Failed to parse calendar specification"；
#   ② 13:00 与 18:00 两个 service 的 ExecStart 都指向
#      prompt-collect-0800.md（复制粘贴时漏改）。
# 两个都是「复制四份再手改」这种做法特有的错。生成器把它们变成
# 「写一份表 + 跑一次」。
#
# 用法：bash deploy/gen-units.sh   （在仓库根跑）
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${REPO}/deploy"

# 时刻 | prompt 文件 | service 名后缀
TASKS=(
  "08:00|prompt-collect-0800.md|collect-0800"
  "13:00|prompt-collect-1300.md|collect-1300"
  "18:00|prompt-collect-1800.md|collect-1800"
)

gen_collect() {
  local when="$1" prompt="$2" tag="$3"
  local hh="${when%%:*}" mm="${when##*:}"
  local svc="newsagg-${tag}.service"
  local tmr="newsagg-${tag}.timer"

  cat > "${OUT}/${svc}" <<EOF
[Unit]
Description=NewsAgg 定时采集（${when}）
Documentation=file:///opt/newsagg/repo/docs/adr/0077-server-becomes-primary-environment.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/newsagg/repo
Environment=TZ=Asia/Shanghai
Environment=PATH=/usr/local/node/bin:/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
Environment=HOME=/root
# 模型凭证：headless 不读 DSH 凭证库，必须显式给（ADR-0077）。
# 这个文件是 0600，内容不打印。
EnvironmentFile=-/etc/newsagg/dsh-credentials.env
ExecStart=/opt/newsagg/repo/deploy/run-prompt.sh /opt/newsagg/repo/deploy/${prompt}
TimeoutStartSec=1800
EOF

  cat > "${OUT}/${tmr}" <<EOF
[Unit]
Description=NewsAgg 定时采集（${when}，Asia/Shanghai）

[Timer]
OnCalendar=*-*-* ${hh}:${mm}:00
Persistent=true
AccuracySec=1min
Unit=${svc}

[Install]
WantedBy=timers.target
EOF
}

for row in "${TASKS[@]}"; do
  IFS='|' read -r when prompt tag <<< "${row}"
  gen_collect "${when}" "${prompt}" "${tag}"
  echo "生成 newsagg-${tag}.service / .timer（${when}，${prompt}）"
done

# 主流程：时刻固定 21:00，prompt 是三段式那份
cat > "${OUT}/newsagg-main-pipeline.service" <<EOF
[Unit]
Description=NewsAgg 每日主流程（21:00：采集→分拣→写作→发布→写回→简报）
Documentation=file:///opt/newsagg/repo/docs/adr/0077-server-becomes-primary-environment.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/newsagg/repo
Environment=TZ=Asia/Shanghai
Environment=PATH=/usr/local/node/bin:/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
Environment=HOME=/root
# 模型凭证：headless 不读 DSH 凭证库，必须显式给（ADR-0077）。
# 这个文件是 0600，内容不打印。
EnvironmentFile=-/etc/newsagg/dsh-credentials.env
ExecStart=/opt/newsagg/repo/deploy/run-prompt.sh /opt/newsagg/repo/deploy/prompt-main-pipeline.md
# 主流程要跑模型（分拣判断 + 写作），比采集慢得多，给足时间
TimeoutStartSec=7200
EOF

cat > "${OUT}/newsagg-main-pipeline.timer" <<EOF
[Unit]
Description=NewsAgg 每日主流程（21:00，Asia/Shanghai）

[Timer]
OnCalendar=*-*-* 21:00:00
Persistent=true
AccuracySec=1min
Unit=newsagg-main-pipeline.service

[Install]
WantedBy=timers.target
EOF

echo "生成 newsagg-main-pipeline.service / .timer（21:00）"

# 自检：时刻必须是 HH:MM（systemd 的 OnCalendar 只认这个形式），
# 且每个 service 的 ExecStart 指向的 prompt 文件必须存在。
RC=0
for f in "${OUT}"/newsagg-*.timer; do
  line="$(grep -m1 '^OnCalendar=' "${f}" || true)"
  hhmm="${line#OnCalendar=*-*-* }"
  hhmm="${hhmm%%:*}"
  if [[ ! "${hhmm}" =~ ^[0-9][0-9]$ ]]; then
    echo "[!] ${f}: OnCalendar 的小时必须是两位（${line}）" >&2
    RC=1
  fi
done
for f in "${OUT}"/newsagg-*.service; do
  pf="$(grep -m1 '^ExecStart=' "${f}" | awk '{print $NF}')"
  base="$(basename "${pf}")"
  if [[ ! -f "${OUT}/${base}" ]]; then
    echo "[!] ${f}: ExecStart 指向的 prompt 不存在：${base}" >&2
    RC=1
  fi
done
[[ "${RC}" -eq 0 ]] && echo "自检通过：时刻格式与 prompt 指向都对"
exit "${RC}"
