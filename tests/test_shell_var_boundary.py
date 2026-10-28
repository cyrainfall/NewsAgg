# -*- coding: utf-8 -*-
"""shell 脚本的「变量紧跟非 ASCII」检查（ADR-0076 的延续）。

## 为什么要有这个测试

在 `set -u` 且 `LC_ALL=C` 的 shell 里，`$var` 后面紧跟全角字符时，
全角字符的**首字节**会被 bash 当成变量名的一部分：

    $ echo "python：$PY（…）"
    /path/run_all.sh: line 149: PY（: unbound variable

中文标点的首字节是 0xEF / 0xE3 之类，在 C locale 下被当作标识符字符，
于是变量名变成 `PY（`，shell 认为你引用了一个没定义的变量。

这个坑已经在四个文件里出现过：
- ADR-0060：`bootstrap-wewrite.sh` 的 `die "…（期望 $want，实际 $got）…"`
- ADR-0065：`run_all.sh` 的 `echo "远端还没有分支 $BRANCH（本地新建的分支）…"`
- ADR-0076：`bootstrap-server.sh` 的 `$INSTANCE_NOTE）` 与 `$DAILYHOT_DIR）`
- 2026-10-27：ADR-0076 自己写的 `echo "python：$PY（…）"` —— 修完别人的
  却在同一个文件里自己犯了一次

**记住规则没有用**：这四次里有三次是「刚修完同一个坑，过一会儿又犯」。
所以规则必须变成一个能跑的检查，写完 shell 脚本顺手跑一遍。

## 判据

`$` + 标识符 + **紧跟一个非 ASCII 字符** → 报出来。

为什么只报「紧跟」而不是「出现过」：中文文案里写 `$PY` 是完全正常的
（前面是空格或标点），只有紧贴非 ASCII 时才会被吃进变量名。所以这个
检查是精确的，不会因为「文件里有很多中文」就满屏报警。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")

# 会跑 shell 的脚本。逐个列出来而不是扫全仓：扫描范围应当是「会被执行
# 的 shell」，不是「所有 .sh 文件」（有些是文档片段，故意用 $var 的写法）。
SHELL_SCRIPTS = [
    ".agents/skills/newsagg-collect/scripts/run_all.sh",
    "scripts/bootstrap-wewrite.sh",
    # 四条定时任务的第一句（采集与主流程都要先确保热榜服务在跑）。
    # ADR-0076 残余风险 81：新增 shell 脚本必须手动加进这个列表，
    # 否则它不在检查范围内——而「不在检查范围内」正是那个坑复发的方式。
    "scripts/ensure-hot.sh",
]


def find_offenders(path: Path) -> list[tuple[int, str]]:
    """返回 (行号, 该行) 列表：变量紧跟非 ASCII 的位置。

    注释行也算——注释里的写法会被复制到代码里，而这次四处里有三处
    正是「注释里的写法」被人照抄进代码。
    """
    out: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        for m in VAR.finditer(line):
            tail = line[m.end():]
            if tail and ord(tail[0]) > 127:
                out.append((i, line.strip()))
                break
    return out


class VarBoundaryTest(unittest.TestCase):
    def test_no_var_butts_against_non_ascii(self):
        for rel in SHELL_SCRIPTS:
            path = REPO / rel
            self.assertTrue(path.exists(), f"{rel} 不存在")
            offenders = find_offenders(path)
            self.assertEqual(
                [], offenders,
                f"{rel} 有 $var 紧跟非 ASCII 的写法："
                + "；".join(f"第 {n} 行 {s[:70]}" for n, s in offenders)
                + "。修法：写成 ${var}。",
            )

    def test_scanner_actually_catches_it(self):
        """扫描器本身要能抓到问题——否则上面那个测试是空的。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.sh"
            p.write_text('echo "值 $PY（后面）"\necho "正常 $PY 的用法"\n', encoding="utf-8")
            offenders = find_offenders(p)
            self.assertEqual(1, len(offenders))
            self.assertEqual(1, offenders[0][0])


if __name__ == "__main__":
    unittest.main()
