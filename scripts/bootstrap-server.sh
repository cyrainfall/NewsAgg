#!/usr/bin/env bash
# 服务器环境准备（阶段 1 第 9 步的机器可复现版本）。
#
# 这台 ECS 的实测状态（2026-10-27）：
# - Alibaba Cloud Linux 3，内存 **1870MB**（不是 2048），4G swap 已开
# - 磁盘 40G，已用 17G
# - 预装：node v24.22.0（/usr/local/node/bin）、uv 0.12.19（/root/.local/bin）
# - 缺：git、python3.11、Xvfb、Chromium、ms-playwright 缓存
#
# 装完这套之后，服务器上能跑：
#   采集（DailyHotApi，6688 端口）→ 表现读回（纯 HTTP，不需要 Chrome）
#   → 封面生成 → 发布（需要 Chrome + Xvfb）
#
# 用法：
#   bash scripts/bootstrap-server.sh            # 装
#   bash scripts/bootstrap-server.sh --check    # 只看装没装
#
# 本脚本要 root 权限（dnf install）。

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE_NOTE="ECS i-wz93frlza7g5nz9ft5dc / cn-shenzhen"

# ── 工具函数 ──────────────────────────────────────────────────────────
need() {
  # 外部命令必须有显式候选路径兜底（ADR-0060 的教训：uv 不在非交互 shell 的 PATH 里）
  local name="$1"; shift
  local c
  for c in "$@"; do
    if command -v "$c" >/dev/null 2>&1; then echo "$c"; return 0; fi
  done
  return 1
}

find_uv() {
  need uv || need_uv=""
  need_uv="$(command -v uv 2>/dev/null || true)"
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv "$REPO/.uv-bin/uv"; do
    if [[ -x "$c" ]]; then echo "$c"; return 0; fi
  done
  echo ""
}

find_node() {
  local c
  for c in "$HOME/.nvm/versions/node/v22.23.3/bin/node" /usr/local/node/bin/node; do
    if [[ -x "$c" ]]; then echo "$c"; return 0; fi
  done
  command -v node 2>/dev/null || echo ""
}

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

echo "=== 服务器环境检查（${INSTANCE_NOTE}）==="

# ── 1. 系统包 ────────────────────────────────────────────────────────
PKGS_MISSING=()
for p in git python3.11 xorg-x11-server-Xvfb; do
  if ! rpm -q "$p" >/dev/null 2>&1; then PKGS_MISSING+=("$p"); fi
done
# chromium 在 epel，名字是 chromium（可执行文件叫 chromium-browser）
if ! rpm -q chromium >/dev/null 2>&1; then PKGS_MISSING+=("chromium"); fi

if [[ $CHECK_ONLY -eq 1 ]]; then
  echo "[检查] 缺失的系统包：${PKGS_MISSING[*]:-无}"
else
  if [[ ${#PKGS_MISSING[@]} -gt 0 ]]; then
    echo "[装] 系统包：${PKGS_MISSING[*]}"
    dnf install -y "${PKGS_MISSING[@]}"
  else
    echo "[跳过] 系统包齐全"
  fi
fi

# ── 2. 工具路径（写进 .env，供编排器用）────────────────────────────
UV_BIN="$(find_uv)"
NODE_BIN="$(find_node)"
echo "[工具] uv   = ${UV_BIN:-（未找到）}"
echo "[工具] node = ${NODE_BIN:-（未找到）}"

# ── 3. wewrite（从 vendor 装，ADR-0060）──────────────────────────────
if [[ $CHECK_ONLY -eq 1 ]]; then
  if [[ -x "$REPO/.uv-tools/wewrite/bin/python" ]]; then
    echo "[检查] wewrite venv：已就位"
  else
    echo "[检查] wewrite venv：**缺失**"
  fi
else
  echo "[装] wewrite（从 vendor/wewrite/，不跑上游 install.sh）"
  bash "$REPO/scripts/bootstrap-wewrite.sh"
fi

# ── 4. DailyHotApi（采集层的数据源）─────────────────────────────────
DAILYHOT_DIR="${DAILYHOT_DIR:-/opt/newsagg/dailyhot}"
if [[ $CHECK_ONLY -eq 1 ]]; then
  if [[ -d "$DAILYHOT_DIR/node_modules" ]]; then
    echo "[检查] DailyHotApi：已装"
  else
    echo "[检查] DailyHotApi：**缺失**（应装在 ${DAILYHOT_DIR}）"
  fi
else
  if [[ ! -d "$DAILYHOT_DIR/.git" ]]; then
    echo "[装] DailyHotApi → $DAILYHOT_DIR"
    git clone --depth 1 https://github.com/imsyy/DailyHotApi.git "$DAILYHOT_DIR"
  fi
  cd "$DAILYHOT_DIR"
  echo "[装] DailyHotApi 依赖"
  PATH="/usr/local/node/bin:$PATH" npm install --omit=dev
  # tsx 必须用本地 node_modules 里的：npx 会临时下载且进程随即退出
  PATH="/usr/local/node/bin:$PATH" npm install tsx
fi

# ── 5. toutiao-ops 依赖 + Playwright 浏览器 ──────────────────────────
CLI_DIR="$REPO/reference/toutiao-ops/cli"
if [[ $CHECK_ONLY -eq 1 ]]; then
  if [[ -d "$CLI_DIR/node_modules/playwright" ]]; then
    echo "[检查] toutiao-ops 依赖：已装"
  else
    echo "[检查] toutiao-ops 依赖：**缺失**"
  fi
  if [[ -d "$REPO/.home/.cache/ms-playwright" ]]; then
    echo "[检查] Playwright 浏览器缓存：已在仓库 HOME 下"
  else
    echo "[检查] Playwright 浏览器缓存：**缺失**（HOME 重定向的产物，见下）"
  fi
else
  cd "$CLI_DIR"
  echo "[装] toutiao-ops 依赖（postinstall 会下 Playwright 浏览器）"
  PATH="/usr/local/node/bin:$PATH" npm install --omit=dev

  # ★ HOME 重定向的坑（ADR-0028 + 服务器实测）：
  #   publish.py 把 HOME 指到 <repo>/.home，所以 Playwright 找浏览器缓存的
  #   路径是 <repo>/.home/.cache/ms-playwright。npm 装的时候 HOME 还是 /root，
  #   浏览器被下到 /root/.cache/ms-playwright —— **两处对不上，浏览器找不到**。
  #   所以要把缓存复制到仓库 HOME 下。（实测报错原文：
  #   "Executable doesn't exist at /opt/newsagg/repo/.home/.cache/ms-playwright/
  #    chromium-1248/chrome-linux64/chrome"）
  if [[ ! -d "$REPO/.home/.cache/ms-playwright" ]]; then
    echo "[装] 复制 Playwright 浏览器缓存到仓库 HOME"
    mkdir -p "$REPO/.home/.cache"
    cp -r "${PLAYWRIGHT_CACHE_SRC:-$HOME/.cache/ms-playwright}" "$REPO/.home/.cache/"
  fi
fi

# ── 6. 汇总结论 ──────────────────────────────────────────────────────
echo
echo "=== 结论 ==="
MISSING=0
for p in git python3.11 xorg-x11-server-Xvfb chromium; do
  if ! rpm -q "$p" >/dev/null 2>&1; then echo "  缺：$p"; MISSING=1; fi
done
[[ -x "$REPO/.uv-tools/wewrite/bin/python" ]] || { echo "  缺：wewrite venv"; MISSING=1; }
[[ -d "$CLI_DIR/node_modules/playwright" ]] || { echo "  缺：toutiao-ops 依赖"; MISSING=1; }
[[ -d "$REPO/.home/.cache/ms-playwright" ]] || { echo "  缺：Playwright 浏览器缓存"; MISSING=1; }
if [[ $MISSING -eq 0 ]]; then
  echo "  环境齐了。下一步：起 Xvfb，注入 cookie，然后 dry-run。"
else
  echo "  还有缺的。"
fi
