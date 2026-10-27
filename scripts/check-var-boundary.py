# -*- coding: utf-8 -*-
"""找出所有「$var 紧跟非 ASCII 字符」的位置。

在 `set -u` 且 `LC_ALL=C` 的 shell 里，全角字符的首字节会被吃进变量名，
于是报 `PY<字节>: unbound variable`。同一个坑已经在四个文件里出现过
（ADR-0060 / ADR-0065 / ADR-0076 / 本次），所以这次写个扫描器，
让「写完新 shell 脚本顺手扫一遍」成为习惯。
"""
import re
import sys
from pathlib import Path

VAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
targets = [Path(p) for p in sys.argv[1:]]
hits = 0
for path in targets:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] {path}: {exc}")
        continue
    for i, line in enumerate(text.split("\n"), start=1):
        for m in VAR.finditer(line):
            tail = line[m.end():]
            if tail and ord(tail[0]) > 127:
                hits += 1
                print(f"{path}:{i}: ${{{m.group(0)[1:]}}} 紧跟非 ASCII → {line.strip()[:90]}")
print(f"--- 共 {hits} 处")
sys.exit(1 if hits else 0)
