#!/usr/bin/env bash
# 从仓内 vendored 源码装 wewrite（ADR-0015、ADR-0060）
#
# 为什么要有这个脚本：ADR-0015 决定「wewrite 以源码形式 vendor 入仓并自行
# 安装，不运行上游 install.sh」，但这件事从 2026-10-14 到 2026-10-25 一直
# 只写在 ADR 里——实际装法是 `uv tool install wewrite==4.2.1`，从 PyPI 拉。
# 也就是说「源码入仓」这件事直到 2026-10-25 才真正做（ADR-0059 记录了这个
# 缺口，本脚本是它的实现）。
#
# 装出来的布局与原来的 uv tool 安装一致，所以调用侧（COVER_PY、
# .uv-bin/wewrite）不需要改：
#   .uv-tools/wewrite/bin/python   ← COVER_PY 用它跑 make_cover.py（Pillow）
#   .uv-tools/wewrite/bin/wewrite  ← console script
#   .uv-bin/wewrite                ← 软链到上面那个
#
# 用法：
#   bash scripts/bootstrap-wewrite.sh            # 装到 .uv-tools/wewrite
#   bash scripts/bootstrap-wewrite.sh --check    # 只校验已装环境，不改动
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/.uv-tools/wewrite"
SRC="$REPO/vendor/wewrite"
PYVER="3.12"
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

say() { printf '%s\n' "$*" >&2; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

# 找 uv：不能只靠 PATH。定时任务与 CI 的 shell 不 source ~/.zshrc，
# 而 uv 装在 ~/.local/bin 时 `command -v uv` 会失败——与 ADR-0052 同型
# （那次是 node 在 nvm 里、定时任务 shell 没有 nvm init，导致
# FileNotFoundError: 'node' 静默丢了两天表现数据）。
find_uv() {
  local c
  if c="$(command -v uv 2>/dev/null)" && [[ -x "$c" ]]; then printf '%s' "$c"; return 0; fi
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
           /opt/homebrew/bin/uv /usr/local/bin/uv "$REPO/.uv-bin/uv"; do
    [[ -x "$c" ]] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# ── 0. 前置检查 ────────────────────────────────────────────────
[[ -d "$SRC/wewrite" ]] || die "找不到 vendored 源码 $SRC/wewrite（ADR-0015）"
[[ -f "$SRC/requirements.lock" ]] || die "找不到 $SRC/requirements.lock"

# 树摘要校验：vendored 源码一旦被手改，这里会发现（PROVENANCE.md 记的期望值）
want="$(sed -n 's/^\([0-9a-f]\{64\}\)$/\1/p' "$SRC/PROVENANCE.md" | head -1)"
if [[ -n "$want" ]]; then
  # 必须 cd 进包目录再算：shasum 的输出包含文件路径，用绝对路径会把
  # 摘要绑定到「仓库放在哪」，换台机器就复算不出来。
  got="$( cd "$SRC/wewrite" && find . -type f -print0 | sort -z \
         | xargs -0 shasum -a 256 | shasum -a 256 | cut -d' ' -f1 )"
  if [[ "$got" != "$want" ]]; then
    die "vendored 源码树摘要不符（期望 ${want}，实际 ${got}）。vendor/ 下的代码不应被手改——见 vendor/wewrite/PROVENANCE.md"
  fi
  say "✓ vendored 源码树摘要一致"
fi

# ── 1. --check：只验已装环境 ────────────────────────────────────
if [[ "$CHECK_ONLY" == "1" ]]; then
  [[ -x "$VENV/bin/python" ]] || die "未安装：$VENV/bin/python 不存在（去掉 --check 安装）"
  "$VENV/bin/python" - <<'PY' || die "已装 wewrite 与 vendor 不一致"
import importlib.metadata as md, pathlib, sys
v = md.version("wewrite")
print(f"✓ wewrite=={v}")
loc = pathlib.Path(md.distribution("wewrite").locate_file("wewrite")).resolve()
print(f"  安装位置：{loc}")
try:
    import wewrite.cli  # noqa: F401
    print("✓ import wewrite.cli 正常")
except Exception as e:
    print(f"✗ import 失败：{e}"); sys.exit(1)
try:
    import PIL  # noqa: F401
    print(f"✓ Pillow {md.version('Pillow')}（make_cover.py 依赖）")
except Exception as e:
    print(f"✗ Pillow 缺失：{e}"); sys.exit(1)
PY
  [[ -x "$VENV/bin/wewrite" ]] || die "console script 缺失：$VENV/bin/wewrite"
  [[ -L "$REPO/.uv-bin/wewrite" ]] || say "! .uv-bin/wewrite 不是软链，检查是否需要重建"
  "$VENV/bin/wewrite" --version 2>/dev/null || true
  say "✓ 环境检查通过"
  exit 0
fi

# ── 2. 建环境并安装 ────────────────────────────────────────────
UV="$(find_uv)" || die "需要 uv：PATH、~/.local/bin、~/.cargo/bin、/opt/homebrew/bin 都没找到"
say "用 uv：${UV}"

if [[ -d "$VENV" ]]; then
  say "已存在 ${VENV}，先删掉重建（旧的可能是从 PyPI 装的，不是从 vendor 装的）"
  rm -rf "$VENV"
fi

say "建虚拟环境（Python ${PYVER}）……"
"$UV" venv --python "$PYVER" "$VENV"

say "装锁定依赖……"
"$UV" pip install --python "$VENV/bin/python" -r "$SRC/requirements.lock"

say "从 vendored 源码装 wewrite（--no-deps：依赖已按 lock 装好）……"
"$UV" pip install --python "$VENV/bin/python" --no-deps "$SRC"

# setuptools 会在**源目录里**就地留下 build/ 与 *.egg-info/（不是装到 venv 里）。
# 它们不该入库（.gitignore 已排除），但也不该留在工作树上让 `git status` 变脏——
# `run_all.sh` 的 git_pull 在脏工作树上会直接熔断（ADR-0040 与 2026-10-25 那次
# `error: cannot pull with rebase: You have unstaged changes.` 就是这条路径）。
rm -rf "$SRC/build" "$SRC"/*.egg-info

# ── 3. 入口软链 ────────────────────────────────────────────────
mkdir -p "$REPO/.uv-bin"
ln -sfn "$VENV/bin/wewrite" "$REPO/.uv-bin/wewrite"
say "✓ .uv-bin/wewrite -> $VENV/bin/wewrite"

# ── 4. 安装后自检 ──────────────────────────────────────────────
bash "${BASH_SOURCE[0]}" --check
