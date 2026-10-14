#!/usr/bin/env python3
"""风格学习：diff（发布快照 ↔ 你改过的教学副本）→ wewrite learn-edits。

设计依据（ADR-0026 循环 B，三个关键决策）：
1. **基线取发布快照**，不取 draft.md。否则 diff 会混入审稿环节的 AI 改动，
   把本人从未做过的偏好学进去。发布快照由 git 天然提供（发布时那次提交）。
2. **按内容哈希去重**，同一版本只学一次。否则你分几天改同一篇、系统每天 diff 一次，
   同一次修改会被计多次，凭空达到 wewrite 的「重复≥2次升级为硬规则」阈值
   ——恰好击穿它自己的保护。
3. **时机**：挂在每日运行开头、git pull 之后（ADR-0026 时序第 ②步）。

本脚本只做确定性工作：找快照、算哈希、判是否已学、调用 learn-edits。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def git(*args: str, cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:200]}")
    return proc.stdout


def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def find_publish_snapshot(root: Path, rel_path: str) -> tuple[str, str] | None:
    """找该文件在 git 历史中**最后一次被提交**的版本，即发布快照。

    发布时系统会 commit 成稿（ADR-0026），因此"最后一次提交"就是发布内容。
    取 HEAD 提交而非工作区内容——工作区可能已被本人修改。
    """
    try:
        log = git("log", "--format=%H", "-1", "--", rel_path, cwd=root).strip()
    except RuntimeError:
        return None
    if not log:
        return None
    commit = log.splitlines()[0]
    try:
        content = git("show", f"{commit}:{rel_path}", cwd=root)
    except RuntimeError:
        return None
    return commit, content


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"learned": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"learned": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="风格学习（ADR-0026 循环 B）")
    ap.add_argument("--slug", default=None, help="指定成稿 slug；默认检查 outputs/ 下全部")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不调用 learn-edits")
    ap.add_argument("--force", action="store_true", help="忽略哈希去重，强制重新学习")
    args = ap.parse_args()

    root = repo_root()
    state_path = root / ".newsagg" / "style-learning.json"
    state = load_state(state_path)
    learned: dict = state.setdefault("learned", {})

    outs = sorted((root / "outputs").glob("*.md"))
    if args.slug:
        outs = [p for p in outs if p.stem == args.slug]
    if not outs:
        print(json.dumps({"ok": True, "checked": 0, "learned": [], "reason": "outputs/ 下无成稿"},
                         ensure_ascii=False, indent=2))
        return 0

    results = []
    for path in outs:
        rel = str(path.relative_to(root))
        snap = find_publish_snapshot(root, rel)
        if snap is None:
            results.append({"file": rel, "status": "no_snapshot",
                            "why": "该文件尚未被提交，无发布快照可比对"})
            continue
        commit, baseline = snap
        current = path.read_text(encoding="utf-8")

        cur_hash = sha256_text(current)
        base_hash = sha256_text(baseline)

        # 未改动 → 无需学习
        if cur_hash == base_hash:
            results.append({"file": rel, "status": "unchanged", "commit": commit[:8]})
            continue

        # ── 哈希去重（ADR-0026 决策 2）：同一版本只学一次 ──
        rec = learned.get(rel) or {}
        if not args.force and rec.get("last_learned_hash") == cur_hash:
            results.append({
                "file": rel, "status": "already_learned",
                "hash": cur_hash[:12],
                "why": "该版本已学习过（哈希去重，防止同一次修改被重复计数）",
            })
            continue

        # 有差异且未学过 → 交给 wewrite learn-edits
        if args.dry_run:
            results.append({
                "file": rel, "status": "would_learn",
                "commit": commit[:8],
                "baseline_hash": base_hash[:12],
                "current_hash": cur_hash[:12],
            })
            continue

        # 写临时文件给 learn-edits（它要的是文件路径）
        tmp_dir = root / ".newsagg" / "style-diff"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        draft_p = tmp_dir / f"{path.stem}.baseline.md"
        draft_p.write_text(baseline, encoding="utf-8")

        env = {
            **__import__("os").environ,
            "WEWRITE_HOME": str(root / ".wewrite"),
            "PATH": f"{root / '.uv-bin'}:{__import__('os').environ.get('PATH','')}",
        }
        proc = subprocess.run(
            ["wewrite", "learn-edits", "--draft", str(draft_p), "--final", str(path)],
            capture_output=True, text=True, env=env, cwd=root,
        )
        ok = proc.returncode == 0
        if ok:
            learned[rel] = {
                "last_learned_hash": cur_hash,
                "learned_at": datetime.now(CST).isoformat(),
                "baseline_commit": commit,
            }
        results.append({
            "file": rel, "status": "learned" if ok else "failed",
            "commit": commit[:8],
            "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-300:],
            "stderr_tail": proc.stderr[-300:],
        })

    if not args.dry_run:
        save_state(state_path, state)

    changed = [r for r in results if r["status"] in ("learned", "would_learn")]
    print(json.dumps({
        "ok": True,
        "checked": len(outs),
        "changed": len(changed),
        "results": results,
        "note": "基线取发布快照（git 那次提交），不取 draft.md —— 见 ADR-0026",
    }, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] != "failed" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
