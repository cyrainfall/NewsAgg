#!/usr/bin/env python3
"""标题规则的**唯一实现**（规则本身在 `platforms/title-rules.md`）。

**为什么规则在 markdown、代码在这里。** 规则要人工复核（ADR-0018：`platforms/` 归本人），
写成一份人能读能改的文档最合适；但「长度、禁用词、结尾字符」是**能机械判定的**，
就该由机器判，而不是让模型每次凭印象读一遍文档。所以分成两层：

- `platforms/title-rules.md` —— 规则的**唯一副本**，含机读 JSON 块（`<!-- title-rules:json {...} -->`）
  与观察项（`<!-- title-rules:observe {...} -->`）。人改它。
- 本模块 —— 读那份文档、判标题、给标题打观察项标签。机器用它。

**为什么不能各写一份。** ADR-0042 的教训：契约的两份实现会漂移，而且漂移是静默的
（两处都「能跑」，只有对照才看得出来）。上一次分拣层与写作层各解析一份 frontmatter，
空值一个记 `""` 一个记 `[]`，导致分拣层永远看不到已覆盖事件。
`build_brief.py`（渲染进选题包）与 `publish.py`（拒发）必须判同一件事，
所以两边都从这里读，不各自抄一份规则。

用法：
    rules = load_rules(repo_root())
    problems = check_title("标题", rules)      # 硬下限：不满足即拒发
    tags = tag_title("标题", rules)             # 观察项：只打标签，不拒发
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 机读块的标记。markdown 里的 HTML 注释不会渲染，但人能看见、能改。
_JSON_BLOCK = re.compile(r"<!--\s*title-rules:json\s*(.*?)\s*-->", re.S)
_OBSERVE_BLOCK = re.compile(r"<!--\s*title-rules:observe\s*(.*?)\s*-->", re.S)

RULES_REL = "platforms/title-rules.md"

# 规则文件缺失时的兜底：只保留平台硬限制（长度），
# 不自己发明禁用词——「没有规则」不等于「可以随便写」，
# 但也绝不能因为文件丢了就默默放行标题党词。
_FALLBACK = {
    "min_len": 2,
    "max_len": 30,
    "forbid_words": [],
}


def rules_path(root: Path) -> Path:
    return Path(root) / RULES_REL


def _extract_json(text: str, pattern: re.Pattern) -> dict:
    m = pattern.search(text)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def load_rules(root: Path) -> dict:
    """读 `platforms/title-rules.md`，返回 `{"hard": {...}, "observe": {...}, "source": str}`。

    规则文件不存在或 JSON 块坏了 → 退回兜底并在 `source` 里标出来，
    调用方可以据此告诉人「规则没读上」而不是假装判过了。
    """
    path = rules_path(root)
    out = {"hard": dict(_FALLBACK), "observe": {}, "source": str(path), "loaded": False}
    if not path.exists():
        out["error"] = f"标题规则文件不存在：{path}"
        return out
    text = path.read_text(encoding="utf-8")
    hard = _extract_json(text, _JSON_BLOCK)
    observe = _extract_json(text, _OBSERVE_BLOCK)
    if not hard:
        out["error"] = f"标题规则文件里没有可解析的 `<!-- title-rules:json -->` 块：{path}"
        return out
    merged = dict(_FALLBACK)
    merged.update({k: v for k, v in hard.items() if v is not None})
    out["hard"] = merged
    out["observe"] = observe
    out["loaded"] = True
    return out


def check_title(title: str, rules: dict) -> list[dict]:
    """判硬下限。返回问题列表（空 = 通过）。

    每条问题形如 `{"code": "H3", "message": "..."}`。
    """
    hard = (rules or {}).get("hard") or _FALLBACK
    problems: list[dict] = []

    title = (title or "").strip()
    lo = int(hard.get("min_len", 2))
    hi = int(hard.get("max_len", 30))
    if not (lo <= len(title) <= hi):
        problems.append({
            "code": "H1",
            "message": f"标题长度 {len(title)} 不在 {lo}~{hi} 字内：{title}",
        })

    for w in hard.get("forbid_words") or []:
        if w and w in title:
            problems.append({
                "code": "H3",
                "message": f"标题含禁用词「{w}」：{title}",
            })

    return problems


def tag_title(title: str, rules: dict) -> dict:
    """给标题打观察项标签。**只打标签，不拒发。**

    观察项是待检验的假设（n=10，展现量与账号权重共线），
    所以它的作用是「让每次发布都留下证据」，不是「现在就拦住」。
    """
    observe = (rules or {}).get("observe") or {}
    title = (title or "").strip()
    tags: dict = {}

    landing = observe.get("reader_side_landing") or {}
    if landing:
        reader = [m for m in (landing.get("reader_markers") or []) if m in title]
        role = [m for m in (landing.get("role_markers") or []) if m in title]
        anti = [m for m in (landing.get("anti_markers") or []) if m in title]
        tags["reader_side_landing"] = {
            "reader_markers": reader,
            "role_markers": role,
            "anti_markers": anti,
            "hit": bool(reader or role),
        }
        # 负向那一半单独成标签（2026-10-31 回归）。
        # 此前 `anti_markers` 被算出来、写进账本，却**不参与 hit**——于是复盘时
        # 「reader_side_landing 不成立」被读成「角色词和抽象量都没用」，而实测
        # 只有负向那一半显著：命中 anti_markers 的 4 篇 CTR 0.09%，未命中的
        # 7 篇 0.31%（0.3 倍，p=0.006，n=11）。正向（角色词）1.1 倍、p=0.67。
        # 拆开之后，这两条假设各自积累证据，不再互相掩护。
        tags["ends_on_abstract_metric"] = {
            "markers": anti,
            "hit": bool(anti),
        }

    contrast = observe.get("contrast_shape") or {}
    if contrast:
        markers = [m for m in (contrast.get("markers") or []) if m in title]
        tags["contrast_shape"] = {"markers": markers, "hit": bool(markers)}

    number = observe.get("concrete_number")
    if number is not None:
        has_num = bool(re.search(r"\d", title))
        tags["concrete_number"] = {"hit": has_num}

    pct = observe.get("no_bare_percentage_ending")
    if pct is not None:
        ends_pct = title.endswith(("%", "％"))
        tags["no_bare_percentage_ending"] = {"hit": ends_pct}

    return tags
