#!/usr/bin/env python3
"""
repo-flow-viz 事实层工具库 —— 给某个仓库写 build_facts.py 时 import 它。

设计原点：这份可视化只有在"读者相信片段是真的"时才有价值。
一旦读者怀疑某行是模型编的，整份图就废了。所以这里的每个 helper
都服务于一个目标：**让摘录不可能悄悄失真**。

  lines_of(path)              读源文件（带缓存）
  ex(path, start, end)        按真实行号切片
  at(path, anchor, before, after)  按字面锚点定位，命中错直接 die()
  fn(path, defline, take)     从函数定义行起切一段
  jsonview(path, keys)        JSON 的结构化视图（非线性摘录，会标明）
  jsonline(path, index)       JSONL 的某一行

两条铁律：
1. 找不到就 die()，绝不返回空、绝不静默跳过。宁可脚本崩，也不要产出
   一份"看起来完整但其实是编的"的页面 —— 那种错误要到读者吃亏时才暴露。
2. 非线性摘录（JSON 重排过）必须标 start=end=None，让渲染层显示"结构化
   视图"而不是假的行号。伪装成行号切片，比不显示更难被发现。

用法：
    sys.path.insert(0, "<skill>/scripts")
    from lib_facts import *
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

__all__ = [
    "set_root", "die", "lines_of", "ex", "at", "fn",
    "jsonview", "jsonline", "textslice", "count_dir", "ROOT",
]

ROOT: Path = Path.cwd()
_CACHE: dict[str, list[str]] = {}


def set_root(root: str | Path) -> Path:
    """在 import 后把仓库根指过去。必须在任何摘录调用之前。"""
    global ROOT
    ROOT = Path(root).resolve()
    return ROOT


def die(msg: str):
    """致命退出。不要捕获它，不要给它默认值 —— 它就是用来停下来的。"""
    print(f"[FATAL] {msg}", file=sys.stderr)
    sys.exit(1)


def lines_of(path: str) -> list[str]:
    if path not in _CACHE:
        p = ROOT / path
        if not p.exists():
            die(f"文件不存在：{path}（cwd={ROOT}）")
        try:
            _CACHE[path] = p.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            die(f"不是 UTF-8 文本，别切它：{path}")
    return _CACHE[path]


def _clip(ls: list[str], start: int, end: int) -> tuple[int, int]:
    if start < 1:
        die(f"行号从 1 起算，收到 start={start}")
    if start > len(ls):
        die(f"行号越界：start={start} 但文件只有 {len(ls)} 行")
    return start, min(end, len(ls))


def ex(path: str, start: int, end: int | None = None, note: str = "") -> dict:
    """按真实行号切片（1-based，闭区间）。这是最常用、也最可信的一种摘录。"""
    ls = lines_of(path)
    if end is None:
        end = start
    s, e = _clip(ls, start, end)
    text = "\n".join(ls[s - 1 : e])
    if not text.strip():
        die(f"切出来是空白：{path} 第 {s}-{e} 行。多半差一行，往上/下挪一格试试。")
    return {
        "path": path, "start": s, "end": e,
        "note": note, "total": len(ls), "text": text,
    }


def at(path: str, anchor: str, before: int = 0, after: int = 0, note: str = "") -> dict:
    """按字面子串定位首处命中，取上下文。锚点必须是行内**稳定**的字面文本。

    比行号好的地方：改了文件它也跟着走。比行号坏的地方：一旦改名就 die()，
    而这正是我们要的 —— 它会提醒你"这里变了，去看一眼"，而不是让你继续
    展示一段已经不存在的行为。
    """
    ls = lines_of(path)
    for i, line in enumerate(ls):
        if anchor in line:
            return ex(path, max(1, i + 1 - before), i + 1 + after, note)
    die(f"锚点未命中：{path} :: {anchor!r}")


def fn(path: str, defline: str, take: int = 18, note: str = "") -> dict:
    """从 def/class 首行起切一段。take 宁小勿大 —— 展示整个 200 行函数等于没展示。"""
    return at(path, defline, before=0, after=take - 1, note=note or f"函数体开头：{defline}")


def jsonview(path: str, keys: list[str] | None, note: str = "") -> dict:
    """把 JSON 的指定顶层键渲染成结构化视图（**不是**原文切片，会标明）。

    keys=None 时列出全部顶层键，list 值折叠成 "…(N 项)" —— 一个 161 元素的
    候选列表全量展开会淹没真正要看的字段，所以这里主动丢掉细节，由 note
    交代清楚丢掉了什么。
    """
    p = ROOT / path
    if not p.exists():
        die(f"文件不存在：{path}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"JSON 解析失败：{path} :: {e}")
    if keys is None:
        sub = {k: (f"…({len(v)} 项)" if isinstance(v, list) else v) for k, v in data.items()}
    else:
        missing = [k for k in keys if k not in data]
        if missing:
            die(f"JSON 缺少键 {missing}：{path}（现有键：{list(data)[:12]}）")
        sub = {k: data[k] for k in keys}
    return {
        "path": path, "start": None, "end": None,
        "note": note or "结构化视图（非原文切片）",
        "total": len(p.read_text(encoding="utf-8").splitlines()),
        "text": json.dumps(sub, ensure_ascii=False, indent=2),
    }


def jsonline(path: str, index: int = -1, note: str = "") -> dict:
    """取 JSONL 的某一行（默认最后一行），格式化后展示。

    注意营业期限：start/end 故意留 None。文本是 json.dumps(indent=2) 出来的，
    已经不是文件里那一行的样子了，标行号就是在撒谎。改用 loc 字段说明情况。
    """
    p = ROOT / path
    if not p.exists():
        die(f"文件不存在：{path}")
    ls = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not ls:
        die(f"空文件：{path}")
    n = len(ls)
    i = index if index >= 0 else n + index
    if i < 0 or i >= n:
        die(f"JSONL 行号越界：index={index} 共 {n} 行 :: {path}")
    try:
        obj = json.loads(ls[i])
    except json.JSONDecodeError as e:
        die(f"JSONL 第 {i+1} 行解析失败：{path} :: {e}")
    return {
        "path": path, "start": None, "end": None,
        "loc": f"第 {i + 1} 行 / 共 {n} 行（JSON 已格式化便于阅读）",
        "note": note, "total": n,
        "text": json.dumps(obj, ensure_ascii=False, indent=2),
    }


def textslice(path: str, start: int, end: int | None = None, note: str = "") -> dict:
    """ex 的别名。给非代码文件（Markdown / YAML / 报告）用，读起来更贴语义。"""
    return ex(path, start, end, note)


def count_dir(glob: str) -> tuple[int, list[str]]:
    """数某个 glob 下有多少个文件，顺带返回文件名。给 header 的统计条用。

    统计值直接进页面顶部，所以它必须是从磁盘数出来的，不能是手写的常数 ——
    手写的常数三个月后就是错的，而页面上不会有任何痕迹提示它过期了。
    """
    files = sorted(p.name for p in ROOT.glob(glob) if p.is_file())
    return len(files), files
