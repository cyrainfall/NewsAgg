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


# 发布账本的唯一读取入口（ADR-0074）：运行时账本 + 入库归档的合并视图。
# 服务器上没有 `.newsagg/publish-ledger.jsonl`（它不入库），只读运行时账本
# 会让风格学习拿不到发布快照 → 基线错位（ADR-0036 那类故障的跨环境版本）。
_LIB_DIR = repo_root() / ".agents" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from publish_ledger import read_merged, read_records as _read_records  # noqa: E402


def git(*args: str, cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:200]}")
    return proc.stdout


def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def read_ledger(ledger: Path) -> list[dict]:
    """读发布账本（每行一条记录）。"""
    return _read_records(ledger)


def find_publish_snapshot(root: Path, rel_path: str, slug: str) -> tuple[str, str] | None:
    """取发布快照（发布那一刻的成稿内容），作为风格 diff 的基线。

    ⚠️ ADR-0036：**不能**用「该文件最后一次提交」推断快照。
    本人改稿并 push 后，最后一次提交就变成改稿本身，基线随之错位 ——
    diff 恒为空、风格学习静默失效（本项目实测踩到，见 ADR-0036）。

    优先级：
    1. 发布账本里记录的 `snapshot.git_blob`（发布时写入，权威）
    2. 回退：账本记录的发布时间之前的最后一次提交（兼容 ADR-0036 之前的旧记录）
    """
    recs = [r for r in read_merged(root)
            if r.get("slug") == slug and r.get("mode") == "publish"]
    if not recs:
        return None
    rec = recs[-1]
    published_at = rec.get("at")

    # ── 路径 1：账本记录了 git blob（ADR-0036 之后）──
    blob = (rec.get("snapshot") or {}).get("git_blob")
    if blob:
        try:
            content = git("cat-file", "-p", blob, cwd=root)
            return f"blob:{blob[:8]}", content
        except RuntimeError:
            pass  # blob 可能已被 GC，落到路径 2

    # ── 路径 2：回退，取发布时间之前的最后一次提交 ──
    if not published_at:
        return None
    try:
        log = git("log", "--format=%H", "-1", f"--before={published_at}",
                  "--", rel_path, cwd=root).strip()
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
        snap = find_publish_snapshot(root, rel, path.stem)
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
        # ── 抑制 learn-edits 的范文库自动增长（ADR-0036）──
        # learn_edits.py:468-481 会把本次的「终稿」自动收进范文库，凡
        # quality_score>=50 即入库并标 ownership=user / authenticity=user_edited。
        # 两个实测缺陷：
        #   ① 它无法分辨「本人真实定稿」与「测试夹具」——一次模拟修改的
        #      临时终稿曾被永久登记为"用户亲笔范文"，污染写作风格；
        #   ② detect_category 的 STORY_MARKERS 含「我」，中文技术观点文
        #      必然命中，实测把技术文判成 story-emotional。
        # 范文属风格资产，其档次与类别需人工把关，故自动增长一律丢弃，
        # 由显式步骤（见 ADR-0036）按正确类别添加。
        ex_dir = root / ".wewrite" / "exemplars"
        ex_index = ex_dir / "index.yaml"
        ex_before = {p.name for p in ex_dir.glob("*.md")} if ex_dir.exists() else set()
        ex_index_before = ex_index.read_text(encoding="utf-8") if ex_index.exists() else None

        proc = subprocess.run(
            ["wewrite", "learn-edits", "--draft", str(draft_p), "--final", str(path)],
            capture_output=True, text=True, env=env, cwd=root,
        )
        ok = proc.returncode == 0

        # 还原范文库：删除新增文件，恢复 index.yaml
        ex_after = {p.name for p in ex_dir.glob("*.md")} if ex_dir.exists() else set()
        auto_added = sorted(ex_after - ex_before)
        for name in auto_added:
            (ex_dir / name).unlink(missing_ok=True)
        if ex_index_before is not None:
            ex_index.write_text(ex_index_before, encoding="utf-8")
        elif ex_index.exists():
            ex_index.unlink()

        exemplars_purged = auto_added
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
            "exemplars_purged": exemplars_purged,
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
        "note": "基线取发布账本记录的快照（ADR-0036）；不取工作区、也不取最后一次提交",
    }, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] != "failed" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
