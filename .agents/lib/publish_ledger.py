"""发布账本的跨环境读取与归档（ADR-0074）。

## 背景：为什么需要这个模块

`.newsagg/publish-ledger.jsonl` 是**唯一**把平台上的作品关联回本地成稿的
东西——决策文件是运行时状态、成稿标题可能被本人改过，只有账本记着发布
当时发生了什么（GLOSSARY「发布账本」条）。它有两个字段承担这条关联：
`title_on_platform`（平台实际收到的标题）与 `gid`（平台分配的唯一标识）。

但它此前**不入库**（`.gitignore:48` 排除整个 `.newsagg/`），于是：

    本机   from_ledger: 12  → 11 篇全部匹配
    服务器 from_ledger: 0   → 11 篇只匹配 9 篇

两篇对不上的原因不是 bug，是**服务器没有账本**：

1. `outputs/2026-10-14-gemini4-price-war.md` 的 H1 后来被改过（ADR-0026
   循环 B 的改稿飞轮），平台上还是旧标题「模型打五折，未必是好事」；
2. 头条输入框会吞字（ADR-0051），账本里的 `title_on_platform` 才是平台
   真正收到的标题，成稿文件里没有这个信息。

ADR-0027 决策四「本地与服务器各自持有运行时状态」在「主环境从本地切到
服务器」这个场景下暴露了缺口：运行时状态里有一项**不是**可重建的缓存。

## 做法：运行时账本 + 入库归档

- **运行时账本**（`.newsagg/publish-ledger.jsonl`）：仍是不入库的运行时
  状态，是当前环境的第一手事实。
- **归档**（`reports/publish-ledger.jsonl`）：入库的合并视图。`reports/`
  按 ADR-0012 是「冻结成结论的产物」的目录，本来就入库（13 份简报 +
  `performance.jsonl`），语义正合适。

`read_merged(root)` 返回两者的**并集**。读者一律用它，不自己读文件——
ADR-0042 记过「三份 frontmatter 解析器漂移」，这里不会再造第四份账本
解析器。

合并规则（按 `record_id` 去重，取"更新的那份"）：

- `record_id` = 记录里的 `record_id`，没有则退回 `at|slug|mode`（旧记录
  的稳定派生键，两台机器上算出来一样）。
- 同一 id 的两份记录比 `updated_at`（没有则用 `at`）：**大的胜**。
  相等时**运行时账本胜**——当前环境刚写的版本是新鲜的。
- 归档里独有的记录**永远保留**：另一个环境写的、本环境没见过的发布记录
  不该被本地账本抹掉。

这样「本地改了 `deleted_at`」会同步进归档（本地运行时版本 `at` 相同 →
平手 → 运行时胜），而「服务器上发的文章」也会在本地拉取归档后出现。

## 凭证防护

归档要**入库**了，所以入库前必须过一遍凭证扫描。账本里出现过的一次命中
是 `correction_note` 的叙述文字（提到 `page.keyboard.type`），不是真凭证
——但这说明「扫关键字」会有误报，所以扫描分两层：

- **键名层**：`sessionid` / `cookie` / `token` / `password` / `secret` /
  `authorization` / `uid_tt` / `sid_ucp` / `ttwid` / `odin_tt` 等键直接
  拒绝归档整条记录。
- **值层**：`msToken=` / `a_bogus=` / `sessionid=` / `sid_ucp_v1=` /
  `Authorization:` 这类「参数名=值」的形态。

命中就**跳过这条记录**并计数，不是报错——发布本身已经成功了，账本写不进去
不该让发布失败（ADR-0006：外部依赖失败不得让主流程失败）。但跳过这件事
要能被看见，所以计数会随 `sync_archive()` 的返回值交出去。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

RUNTIME_REL = Path(".newsagg") / "publish-ledger.jsonl"
ARCHIVE_REL = Path("reports") / "publish-ledger.jsonl"

# 键名层：命中即跳过整条记录。
_CREDENTIAL_KEYS = (
    "sessionid",
    "sid_ucp",
    "sid_guard",
    "cookie",
    "cookies",
    "token",
    "password",
    "passwd",
    "secret",
    "authorization",
    "uid_tt",
    "ttwid",
    "odin_tt",
    "sso_uid_tt",
)

# 值层：参数名=值 的形态。
_CREDENTIAL_VALUE_RE = re.compile(
    r"(msToken|a_bogus|sessionid|sid_ucp_v1|sid_guard|Authorization)\s*[:=]",
    re.IGNORECASE,
)


def repo_root() -> Path:
    """仓库根目录。

    本模块在 `.agents/lib/` 下，所以根目录是上溯三级（lib → .agents → 根）。
    """
    return Path(__file__).resolve().parents[2]


def runtime_path(root: Path) -> Path:
    return root / RUNTIME_REL


def archive_path(root: Path) -> Path:
    return root / ARCHIVE_REL


def read_records(path: Path) -> list[dict]:
    """读账本记录（每行一条 JSON），坏行跳过。

    容忍坏行是刻意的：账本是追加写的进程被杀可能留下半行，那半行不该让
    整个读者失败（ADR-0006）。
    """
    path = Path(path)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def record_id(rec: dict) -> str:
    """记录的唯一键。

    优先用记录自带的 `record_id`；旧记录没有，用 `at|slug|mode` 派生。
    派生键在两台机器上算出来一样——`at` 是 ISO 时间戳、`slug` 是文件名、
    `mode` 是发布方式，都是内容决定的，不是环境决定的。
    """
    rid = rec.get("record_id")
    if rid:
        return str(rid)
    return "|".join(
        str(rec.get(k) or "") for k in ("at", "slug", "mode")
    )


def _recency(rec: dict) -> str:
    """这条记录有多新。`updated_at` 优先，没有就用 `at`。"""
    return str(rec.get("updated_at") or rec.get("at") or "")


def _walk_keys(obj: Any):
    """递归取出一个结构里所有的键名。

    为什么要递归：账本记录里可能有嵌套结构（`snapshot` 是一个 dict、
    `api_write` 也是一个 dict），凭证藏在嵌套层里同样会进 git。
    """
    if isinstance(obj, dict):
        for key, val in obj.items():
            yield str(key)
            yield from _walk_keys(val)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


def has_credentials(rec: dict) -> bool:
    """这条记录里有没有凭证。"""
    for key in _walk_keys(rec):
        if key.lower() in _CREDENTIAL_KEYS:
            return True
    try:
        blob = json.dumps(rec, ensure_ascii=False)
    except (TypeError, ValueError):
        return True
    return bool(_CREDENTIAL_VALUE_RE.search(blob))


def merge(runtime: list[dict], archive: list[dict]) -> list[dict]:
    """合并运行时账本与归档，按 `record_id` 去重。

    规则见模块 docstring。要点：归档独有的记录永远保留；同一 id 取更新的
    那份；平手时运行时胜。
    """
    merged: dict[str, dict] = {}
    for rec in archive:
        rid = record_id(rec)
        merged[rid] = rec
    for rec in runtime:
        rid = record_id(rec)
        old = merged.get(rid)
        if old is None or _recency(rec) >= _recency(old):
            merged[rid] = rec
    return sorted(merged.values(), key=lambda r: str(r.get("at") or ""))


def read_merged(root: Path) -> list[dict]:
    """读**合并后**的账本：运行时 + 归档。

    这是读者应该用的唯一入口。四个读者（style_learn / triage /
    performance / brief）都改用它。
    """
    root = Path(root)
    return merge(
        read_records(runtime_path(root)),
        read_records(archive_path(root)),
    )


def sync_archive(root: Path) -> dict:
    """把合并后的账本写回归档。返回统计。

    幂等：同样的输入写出来的文件一样。可以安全地反复调用。
    """
    root = Path(root)
    runtime = read_records(runtime_path(root))
    archive = read_records(archive_path(root))
    merged = merge(runtime, archive)

    kept: list[dict] = []
    skipped = 0
    for rec in merged:
        if has_credentials(rec):
            skipped += 1
            continue
        # 归档里写明 `record_id`：合并键就不再依赖 `at|slug|mode` 的派生。
        # 派生键本身够用（`at` 不会变），但显式写出来更耐改——万一将来
        # 补录记录时 `at` 写错一次，派生键会分裂成两个 id。
        kept.append(dict(rec, record_id=make_record_id(rec)))

    target = archive_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rec, ensure_ascii=False) for rec in kept]
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    return {
        "ok": True,
        "runtime_records": len(runtime),
        "archive_records_before": len(archive),
        "merged": len(merged),
        "written": len(kept),
        "skipped_for_credentials": skipped,
        "path": str(target),
    }


def make_record_id(rec: dict) -> str:
    """给一条记录补上稳定的 `record_id`（就地修改并返回）。

    写账本时调用它，让后续合并不必依赖派生键。
    """
    rid = rec.get("record_id")
    if not rid:
        rid = record_id(rec)
        rec["record_id"] = rid
    return str(rid)


__all__ = [
    "ARCHIVE_REL",
    "RUNTIME_REL",
    "archive_path",
    "has_credentials",
    "make_record_id",
    "merge",
    "read_merged",
    "read_records",
    "record_id",
    "repo_root",
    "runtime_path",
    "sync_archive",
]
