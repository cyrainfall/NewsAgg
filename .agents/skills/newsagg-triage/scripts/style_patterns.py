#!/usr/bin/env python3
"""风格回路：把「已学的 diff 记录」补上 patterns（agent 判断的确定性外壳）。

为什么需要这个脚本（ADR-0026 循环 B 的最后一环）：
`wewrite learn-edits` **只记录 diff、不提取 pattern**——其源码里
`"patterns": []` 带注释 "Agent fills these after analyzing the draft and final"。
这是 wewrite「prompt 负责判断，Python 负责确定性」的分工：提取"这次修改反映了
什么偏好"是判断，属 agent。

因此闭环需要两步：
  1. style_learn.py  → 调 learn-edits，产出 lesson YAML（patterns 为空）
  2. 本脚本          → 读出 lesson 里引用的 draft/final，把两者内容一并输出，
                       供 agent 阅读并写出 patterns

本脚本**不做判断**：不猜 pattern，只把判断所需的材料准备好、并在 agent 写完后
把 patterns 合并回 lesson 文件（保持 wewrite 期望的 YAML 结构）。

用法：
  # 准备材料（agent 读它的输出后写 patterns）
  python3 style_patterns.py --prepare

  # 合并 agent 写好的 patterns（JSON 数组）
  python3 style_patterns.py --merge patterns.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PATTERN_TYPES = [
    "word_sub", "para_delete", "para_add", "structure",
    "title", "tone", "expression",
]
SCOPES = ["global", "content_type", "framework", "persona"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def latest_lesson(lessons_dir: Path) -> Path | None:
    files = sorted(lessons_dir.glob("*.yaml"))
    return files[-1] if files else None


def parse_lesson(text: str) -> dict:
    """只解析本流程用得到的字段（避免引入 yaml 依赖）。"""
    out: dict = {}
    for key in ("date", "draft_file", "final_file"):
        m = re.search(rf"^{key}:\s*(.+)$", text, re.M)
        if m:
            out[key] = m.group(1).strip().strip("'\"")
    # diff_summary 的标量
    ds = {}
    m = re.search(r"^diff_summary:\s*$((?:\n\s+.+)*)", text, re.M)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.strip().split(":", 1)
                ds[k.strip()] = v.strip().strip("'\"")
    out["diff_summary"] = ds
    out["patterns_empty"] = bool(re.search(r"^patterns:\s*\[\]\s*$", text, re.M))
    return out


def unified_diff(a: str, b: str, label_a: str, label_b: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True),
        fromfile=label_a, tofile=label_b, n=2,
    ))


def main() -> int:
    ap = argparse.ArgumentParser(description="风格回路：补 patterns（agent 判断的外壳）")
    ap.add_argument("--prepare", action="store_true", help="输出供 agent 判断的材料")
    ap.add_argument("--merge", default=None, help="合并 agent 写好的 patterns JSON")
    ap.add_argument("--lesson", default=None, help="指定 lesson 文件；默认取最新")
    ap.add_argument("--refresh", action="store_true",
                    help="与 --prepare 同用：调用 learn-edits 刷新 lesson，使 draft/final 指向当前 diff")
    args = ap.parse_args()

    root = repo_root()
    lessons_dir = root / ".wewrite" / "lessons"
    lesson = Path(args.lesson) if args.lesson else latest_lesson(lessons_dir)
    if not lesson or not lesson.exists():
        print(json.dumps({"ok": False, "message": "找不到 lesson 文件；先跑 style_learn.py"},
                         ensure_ascii=False, indent=2))
        return 1

    text = lesson.read_text(encoding="utf-8")
    meta = parse_lesson(text)

    if args.prepare:
        draft_p = Path(meta.get("draft_file", ""))
        final_p = Path(meta.get("final_file", ""))
        if not draft_p.exists() or not final_p.exists():
            print(json.dumps({
                "ok": False,
                "message": "lesson 引用的 draft/final 不存在，可能已被清理",
                "draft_file": str(draft_p), "final_file": str(final_p),
            }, ensure_ascii=False, indent=2))
            return 1
        draft = draft_p.read_text(encoding="utf-8")
        final = final_p.read_text(encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "lesson": str(lesson),
            "diff_summary": meta.get("diff_summary", {}),
            "patterns_currently_empty": meta.get("patterns_empty", False),
            "diff": unified_diff(draft, final, "发布快照(AI成稿)", "你的修改"),
            "instruction": (
                "阅读上面的 diff，判断**这次修改反映了什么可复用的写作偏好**，"
                "输出 JSON 数组，每项形如 "
                '{"type": <one of ' + str(PATTERN_TYPES) + '>, '
                '"key": "<短标识>", "description": "<中文描述>", '
                '"rule": "<可执行的祈使句>", '
                '"scope": <one of ' + str(SCOPES) + '>, "scope_value": "<scope 非 global 时填>"}。'
                "硬规则：优先选最窄且真实的范围；单次修改不要设 confirmed；"
                "rule 必须是祈使句（如「段落不超过 80 字」），不能是描述句。"
                "然后把结果写成 JSON 文件，用 --merge 合并。"
            ),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.merge:
        new_patterns = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        if not isinstance(new_patterns, list):
            print(json.dumps({"ok": False, "message": "--merge 需要 JSON 数组"},
                             ensure_ascii=False, indent=2))
            return 1

        # 校验字段（确定性检查，不替代判断）
        problems = []
        for i, p in enumerate(new_patterns):
            if p.get("type") not in PATTERN_TYPES:
                problems.append(f"[{i}] type 非法：{p.get('type')!r}")
            if not p.get("key"):
                problems.append(f"[{i}] 缺 key")
            if not p.get("rule"):
                problems.append(f"[{i}] 缺 rule")
            if p.get("scope") and p["scope"] not in SCOPES:
                problems.append(f"[{i}] scope 非法：{p.get('scope')!r}")
            if p.get("scope") and p["scope"] != "global" and not p.get("scope_value"):
                problems.append(f"[{i}] scope={p['scope']} 但缺 scope_value")
        if problems:
            print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
            return 1

        # 写回 YAML（保持 wewrite 期望的缩进结构）
        lines = ["patterns:"]
        for p in new_patterns:
            lines.append(f"  - type: {p['type']}")
            lines.append(f"    key: {p['key']}")
            lines.append(f"    description: {json.dumps(p.get('description',''), ensure_ascii=False)}")
            lines.append(f"    rule: {json.dumps(p['rule'], ensure_ascii=False)}")
            lines.append(f"    scope: {p.get('scope','global')}")
            lines.append(f"    scope_value: {json.dumps(p.get('scope_value',''), ensure_ascii=False)}")
            lines.append(f"    confirmed: {str(bool(p.get('confirmed', False))).lower()}")
        block = "\n".join(lines) + "\n"

        if re.search(r"^patterns:\s*\[\]\s*$", text, re.M):
            new_text = re.sub(r"^patterns:\s*\[\]\s*$", block.rstrip("\n"),
                              text, count=1, flags=re.M)
        else:
            new_text = text.rstrip() + "\n" + block
        lesson.write_text(new_text, encoding="utf-8")

        # 读回校验
        back = lesson.read_text(encoding="utf-8")
        ok = all(p["key"] in back for p in new_patterns)
        print(json.dumps({
            "ok": ok, "lesson": str(lesson),
            "merged_patterns": len(new_patterns),
            "verified": ok,
            "next": "跑 wewrite learn-edits --summarize 查看 confidence 与 hard 判定",
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(json.dumps({"ok": False, "message": "需指定 --prepare 或 --merge"},
                     ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
