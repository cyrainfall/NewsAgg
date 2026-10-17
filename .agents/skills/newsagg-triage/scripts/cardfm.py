"""观点卡 frontmatter 的唯一解析实现（ADR-0042）。

**为什么是共享模块而不是三份拷贝。** 这个解析器原本在 `triage.py` / `build_brief.py` /
`writeback.py` 里各有一份。三份拷贝漂移了：`triage.py` 把「空值」记成空字符串 `""`，
而另外两份记成空列表 `[]`。后果是同一张卡在不同阶段被解析成不同形状——
`writeback.py` 写回 `covered_events` 时把空列表展开成多行列表项，此后
`triage.py` 读到的是 `""` 而不是列表，于是**分拣层永远看不到「已覆盖事件」**，
而 `build_brief.py` 看得到。分拣输入包里那张卡的 `covered_events` 因此恒为空。

这与 ADR-0030 关于浏览器锁的教训是同一条：**同一份契约的两份实现会漂移，
且漂移是静默的**（两处都「能跑」，只有对照才看得出来）。所以这里只留一份实现。

调用方通过同目录导入（`sys.path` 里加上本文件所在目录），见三个脚本顶部的
`_load_cardfm()`。
"""

from __future__ import annotations

import re


def parse_frontmatter(text: str) -> tuple[dict, str, str]:
    """解析 `---` 包裹的 frontmatter，返回 `(fm, body, 原始 frontmatter 文本)`。

    极简实现：只处理标量与简单列表（`- item`），不引入 yaml 依赖——本系统的
    frontmatter 只用到这两种形态。

    **空值的处理是本模块存在的理由**，三种写法都要正确：

    - `covered_events: []`  → `[]`
    - `covered_events:` 后跟 `- item` 列表项 → 逐项追加（空值在出现列表项时**升级为列表**）
    - `covered_events:` 后无列表项 → 保持 `""`（由调用方 `or []` 兜底）

    旧实现把第二种也记成 `""`，列表项就被静默丢弃。这里改为**由后续行决定类型**，
    因此不再需要预先猜「空值到底是空字符串还是空列表」。
    """
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text, ""
    raw = m.group(1)
    fm: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*-\s+", line) and key:
            # 列表项：若当前值不是列表，则把空值升级为列表（关键：不丢列表项）
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(line.strip()[2:].strip().strip('"'))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key, v = k.strip(), v.strip()
            if v == "[]":
                fm[key] = []
            elif v == "":
                fm[key] = ""  # 可能被后续列表项升级为列表
            elif v.lower() in ("null", "~"):
                fm[key] = None
            else:
                fm[key] = v.strip('"')
    return fm, m.group(2), raw
