#!/usr/bin/env python3
"""正文标签的**唯一实现**（规则本身在 `platforms/prose-rules.md`）。

**为什么规则在 markdown、代码在这里。** 与 `title_rules.py` 同一套分工：
规则要人工复核（ADR-0018：`platforms/` 归本人），写成一份人能读能改的文档；
判定逻辑只有一份实现，写作阶段（`build_brief.py` 渲染）与发布阶段（`publish.py` 打标签）
都从这里读，不各自抄一份——ADR-0042 的教训是契约的两份实现会漂移，且漂移是静默的。

**它不拒发。** 与标题的三条硬下限不同，正文结构没有「拒发」的正确阈值：
拦错一次就损失一篇产能，而当前样本（n=12，阅读率 0.05%–0.81%）连现有写法都没跑通。
所以这里只打标签，标签进账本，攒够样本再谈阈值（与 ADR-0069 的观察项同构）。

用法：
    rules = load_rules(repo_root())
    tags = tag_body(body, rules)     # 只打标签，不拒发
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 机读块的标记。markdown 里的 HTML 注释不会渲染，但人能看见、能改。
_TAGS_BLOCK = re.compile(r"<!--\s*prose-rules:tags\s*(.*?)\s*-->", re.S)

RULES_REL = "platforms/prose-rules.md"

# 规则文件缺失时的兜底：全是空定义，标签会退化成「什么都没命中」。
# 不自己发明标记词——「没有规则」不等于「可以随便写」，但也绝不能因为
# 文件丢了就默默判成「这篇没有边界声明」（那会把「没读到」伪装成「没有」）。
_FALLBACK: dict = {}


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
    """读 `platforms/prose-rules.md`，返回 `{"tags": {...}, "source": str, "loaded": bool}`。

    规则文件不存在或 JSON 块坏了 → 退回兜底并在 `source` 里标出来，
    调用方据此告诉人「规则没读上」而不是假装判过了（与 title_rules 同款约定）。
    """
    path = rules_path(root)
    out = {"tags": dict(_FALLBACK), "source": str(path), "loaded": False}
    if not path.exists():
        out["error"] = f"正文规则文件不存在：{path}"
        return out
    text = path.read_text(encoding="utf-8")
    tags = _extract_json(text, _TAGS_BLOCK)
    if not tags:
        out["error"] = f"正文规则文件里没有可解析的 `<!-- prose-rules:tags -->` 块：{path}"
        return out
    out["tags"] = tags
    out["loaded"] = True
    return out


def _paragraphs(body: str) -> list[str]:
    """按空行切段，丢掉标题行与过短的块。

    为什么丢标题行：`# 标题` 与 `## 小标题` 不参与「正文结构」判断。
    为什么丢短块：单行强调句（如 `**小结**`）不是一段散文，
    把它当末段会让「末段是行动还是边界」变成随机结果。
    """
    out = []
    for block in re.split(r"\n\s*\n", body or ""):
        b = block.strip()
        if not b or b.startswith("#"):
            continue
        if len(b) < 20:          # 太短，不是一段散文
            continue
        out.append(b)
    return out


def _count_hits(text: str, markers) -> list[str]:
    return [m for m in (markers or []) if m and m in text]


def tag_body(body: str, rules: dict) -> dict:
    """给正文打标签。**只打标签，不拒发。**

    四个标签见 `platforms/prose-rules.md` 第一节。返回的每个值都带
    `hit`（布尔）与命中的原始标记，便于事后复核「这个判定是靠什么打出来的」——
    光有一个 `true` 无法在产品侧被检验，也无法在规则改坏时归因。
    """
    spec = (rules or {}).get("tags") or _FALLBACK
    paras = _paragraphs(body or "")
    text = "\n".join(paras)
    tags: dict = {}

    # ① ending_mode：末段是行动、边界，还是都不是（三态）。
    ending = spec.get("ending_mode") or {}
    if ending:
        last = paras[-1] if paras else ""
        act = _count_hits(last, ending.get("action_markers"))
        bnd = _count_hits(last, ending.get("boundary_markers"))
        # 两者都命中时判边界：末段里出现「改口/边界/不确定」说明
        # 作者在收尾处仍在自我限定，这正是要识别的那一类（见复盘）。
        mode = "boundary" if bnd else ("action" if act else "none")
        tags["ending_mode"] = {
            "mode": mode,
            "action_markers": act,
            "boundary_markers": bnd,
            "tail": last[:80],
        }

    # ② contrast_has_source：反转句有多少句给了「X 的来源」。
    contrast = spec.get("contrast_has_source") or {}
    pat = contrast.get("contrast_pattern")
    if pat:
        try:
            matches = re.findall(pat, text)
        except re.error:
            matches = []
        # findall 若带分组会返回分组内容，这里统一按「句子数」统计来源覆盖。
        sentences = re.split(r"[。！？\n]", text)
        reversed_sents = [s for s in sentences
                          if re.search(pat, s)]
        ours = []
        for s in reversed_sents:
            src = _count_hits(s, contrast.get("source_markers"))
            if src:
                ours.append(src)
        tags["contrast_has_source"] = {
            "reversed_total": len(reversed_sents),
            "with_source": len(ours),
            "ratio": (round(len(ours) / len(reversed_sents), 2)
                      if reversed_sents else None),
            "raw_matches": len(matches),
        }

    # ③ has_precedent：有没有「同样的机制上一次出现在哪」的先例叙述。
    prec = spec.get("has_precedent") or {}
    if prec:
        hits = _count_hits(text, prec.get("precedent_markers"))
        tags["has_precedent"] = {"hit": bool(hits), "markers": hits}

    # ④ quotes_own_prior：有没有引用自己之前的结论。
    prior = spec.get("quotes_own_prior") or {}
    if prior:
        hits = _count_hits(text, prior.get("markers"))
        tags["quotes_own_prior"] = {"hit": bool(hits), "markers": hits}

    return tags
