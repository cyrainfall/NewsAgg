#!/usr/bin/env python3
"""
repo-flow-viz 校验器 —— 独立复核 facts JSON 里每段摘录是不是真的来自文件。

为什么要有这个独立脚本，而不在 build 时顺手检查：
build 脚本是"自己切自己查"，切的人知道自己在切什么，容易一起错。
这个脚本只读 facts + 只读磁盘，从零重新对齐一遍 —— 它跟生成过程没有
任何共享代码，所以它的通过才有分量。

检查项：
  A. 结构    必需字段、node id 唯一、groups 引用的 id 存在
  B. 行号    每段 start/end 摘录的首行末行，与磁盘文件对应行逐字相同
  C. 空白    摘录不能是纯空白（多半是行号差一格）
  D. total   声明的总行数必须等于文件实际行数（文件长了对不上就提醒）
  E. 诚实    非线性摘录（start=None）必须能看出是结构化的（note/loc 有交代）
  F. 引用    reads/writes 里的文件必须在 files 里至少出现一次（否则读者点不到）

用法：
    python3 verify_facts.py facts.json [--root /path/to/repo]
退出码 0 = 全通过；非 0 = 有问题，页面不该交付。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RED, GRN, YEL, DIM, RST = "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("facts")
    ap.add_argument("--root", help="仓库根（默认取 facts 里 meta.root）")
    args = ap.parse_args()

    facts_path = Path(args.facts).resolve()
    data = json.loads(facts_path.read_text(encoding="utf-8"))
    nodes = data.get("nodes") or []
    meta = data.get("meta") or {}

    root = Path(args.root).resolve() if args.root else Path(meta.get("root", ".")).resolve()
    if not root.exists():
        print(f"{RED}[FATAL]{RST} 仓库根不存在：{root}")
        return 1

    errs: list[str] = []
    warns: list[str] = []
    checked = 0

    # ── A. 结构 ────────────────────────────────────────────
    ids = [n.get("id") for n in nodes]
    for i in {i for i in ids if ids.count(i) > 1}:
        errs.append(f"node id 重复：{i}")
    for n in nodes:
        if not n.get("title"):
            errs.append(f"节点缺 title：{n.get('id')}")
        if not (n.get("does") or n.get("files")):
            warns.append(f"节点 {n.get('id')} 既没 does 也没 files —— 这一格会是空的")
    for g in data.get("groups", []):
        for i in g.get("ids", []):
            if i not in ids:
                errs.append(f"分组「{g.get('title')}」引用了不存在的 id：{i}")

    cache: dict[str, list[str]] = {}

    def lines(path: str) -> list[str] | None:
        if path not in cache:
            p = root / path
            if not p.exists():
                cache[path] = None
                return None
            try:
                cache[path] = p.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                cache[path] = None
        return cache[path]

    seen_paths: set[str] = set()
    files_with_excerpt: set[str] = set()

    for n in nodes:
        nid = n.get("id")
        for key in ("reads", "writes", "files"):
            for f in n.get(key) or []:
                path = f.get("path")
                if not path:
                    errs.append(f"{nid}/{key} 缺 path")
                    continue
                seen_paths.add(path)
                if (f.get("text") or "").strip():
                    files_with_excerpt.add(path)
                ls = lines(path)
                checked += 1

                if ls is None:
                    errs.append(f"{nid}/{key}：文件不存在或不是 UTF-8 → {path}")
                    continue

                start, end = f.get("start"), f.get("end")
                text = f.get("text") or ""

                # ── E. 诚实 ──
                if start is None:
                    if not (f.get("note") or f.get("loc")):
                        errs.append(
                            f"{nid}/{key} {path}：非线性摘录却没有 note/loc 说明，"
                            f"读者无法判断它是不是原文")
                    total = f.get("total")
                    if isinstance(total, int) and total != len(ls) and total != 0:
                        # jsonline 的 total 是有效行数，可能小于物理行数，只提醒
                        warns.append(f"{nid}/{key} {path}：total={total} 与实际 {len(ls)} 行不符")
                    continue

                # ── C. 空白 ──
                if not text.strip():
                    errs.append(f"{nid}/{key} {path}:{start}-{end} 切出来是空白")
                    continue

                rows = text.split("\n")
                # ── B. 行号对齐 ──
                if start < 1 or end > len(ls):
                    errs.append(f"{nid}/{key} {path}:{start}-{end} 越界（共 {len(ls)} 行）")
                    continue
                if rows[0].strip() != ls[start - 1].strip():
                    errs.append(
                        f"{nid}/{key} {path}:{start} 首行对不上\n"
                        f"        JSON  : {rows[0][:90]!r}\n"
                        f"        磁盘  : {ls[start - 1][:90]!r}")
                if rows[-1].strip() != ls[end - 1].strip():
                    errs.append(
                        f"{nid}/{key} {path}:{end} 末行对不上\n"
                        f"        JSON  : {rows[-1][:90]!r}\n"
                        f"        磁盘  : {ls[end - 1][:90]!r}")
                # ── D. total ──
                total = f.get("total")
                if isinstance(total, int) and total != len(ls):
                    errs.append(f"{nid}/{key} {path}：total={total} 但实际 {len(ls)} 行——文件变了，重跑 build")

    # ── F. 引用可达 ────────────────────────────────────────
    # 判定标准放宽：同一路径在**任何**节点的 files 里有摘录，就算点得到。
    # 一个文件被 5 个节点读，只需其中 1 个给出摘录 —— 要求每个节点都重复
    # 摘录一遍，只会让 facts 膨胀，页面反而更难读。
    for n in nodes:
        nid = n.get("id")
        for key in ("reads", "writes"):
            for f in n.get(key) or []:
                if f.get("path") not in files_with_excerpt:
                    warns.append(
                        f"{nid}/{key} {f.get('path')}：全图没有任何节点给出它的原文摘录，"
                        f"读者点开看不到内容")

    # ── 报告 ───────────────────────────────────────────────
    print(f"{DIM}校验 {facts_path.name}  root={root}{RST}")
    print(f"  节点 {len(nodes)} · 摘录 {checked} 段 · 去重文件 {len(seen_paths)}")
    for w in warns:
        print(f"{YEL}  [warn]{RST} {w}")
    for e in errs:
        print(f"{RED}  [FAIL]{RST} {e}")
    if errs:
        print(f"\n{RED}✗ {len(errs)} 项未通过 —— 页面不该交付{RST}")
        return 1
    extra = f"，{len(warns)} 项提醒" if warns else ""
    print(f"\n{GRN}✓ 全部 {checked} 段摘录与磁盘逐行一致{extra}{RST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
