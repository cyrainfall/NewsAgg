#!/usr/bin/env python3
"""信源健康：把 Agent-Reach 的渠道体检接进简报第 ⑤ 段（ADR-0021 Decision 四）。

## 为什么要有这一个文件

ADR-0021 第四条写的是：「用 `agent-reach watch` 作为信源健康信号，该命令专为
定时任务设计（"designed for scheduled tasks. Only outputs problems"），其输出
接入简报第 ⑤ 段。」

这条承诺写进了 ADR、`DESIGN.md` 的核心取舍表和 `WALKTHROUGH.md`，但**没有任何
代码消费它**：全仓 `grep -rn "agent-reach" .agents/` 零命中。这是本仓库第五次
「记录了但没有消费者」（前四次：ADR-0038 的 hot_history、ADR-0042 的三份
frontmatter 解析器、ADR-0057 的 tests/、ADR-0058 的 money_point）。

前四次都是「做了判断、记下来了、没人看」。这一次更隐蔽：它连中间产物都没有——
ADR 承诺的是一个**命令的输出**直接进简报，中间没有落地文件，所以既没有
「实现了但没接上」，也没有「接上了但没生效」，而是**从来没被调用过**。

## 它补的是哪个盲区

看守（ADR-0006）只能发现「系统完全没跑」，熔断（ADR-0011）只能发现「发布失败」。
但**信源悄悄失效**会让系统照常运行、简报照常发出、只是采集面变窄——在 ADR-0021
写下的那天，这个状态完全不可见。

## 两条边界

**一、信源变窄不熔断。** 采集面变窄不等于不能发文（可能仍有足够信号），停发代价
过大。它只进入简报等人工判断（ADR-0021 第四条、ADR-0022 第五条）。

**二、只报 tier-0。** tier-1/2 渠道（小红书/推特/Reddit/LinkedIn/Boss 等）需要
登录态或专用浏览器，ADR-0021 的 Consequences 已写明「第一阶段只发头条，这些
渠道用不上，因此不构成阻塞」。把 11 个「未安装」当成信源故障报上去，会让
第 ⑤ 段每天都堆满与本项目无关的噪声——而第 ⑤ 段是「需你介入的事项」，堆噪声
等于让这一段被无视。tier-0 才是本项目真在用的那批。

## 失败也不能阻塞

`agent-reach` 是**活跃依赖**（ADR-0021 不钉版本），它挂掉、升级、或者根本没装，
都不该让简报出不来——简报必须无条件每日存在（ADR-0006）。所以本文件的所有
失败路径都返回「读不到」，而不是抛异常。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# tier-0 = 装好即用、无需配置。ADR-0021 列举的那批：
# web(Jina Reader) / rss / exa_search / github / v2ex / youtube
TIER0 = ("web", "rss", "exa_search", "github", "v2ex", "youtube")

# 采集层真正在用的源（ADR-0029 的七源）。Agent-Reach 的渠道名与它们不是一一对应，
# 但 github / web / rss 这几个 tier-0 渠道直接覆盖了 DailyHotApi 之外的深度信源，
# 它们失效才是「采集面变窄」。
TIMEOUT = 120  # 秒。doctor 会逐个渠道探测，16 个渠道全跑要十几秒。


def agent_reach_cmd(root: Path) -> list[str] | None:
    """找到 agent-reach 的可执行入口。

    ADR-0021 决定「只安装 CLI，不注册 SKILL.md，不 vendor 入仓」。所以它**不在
    PATH 里**——那是本项目的刻意设计，不是缺陷。实测路径是
    `.uv-tools/agent-reach/bin/agent-reach`，仓内还有一个薄封装 `.uv-bin/agent-reach`。

    找不到就返回 None，调用方按「读不到」处理。
    """
    cands = [
        root / ".uv-bin" / "agent-reach",
        root / ".uv-tools" / "agent-reach" / "bin" / "agent-reach",
    ]
    for c in cands:
        if c.exists() and os.access(c, os.X_OK):
            return [str(c)]
    found = shutil.which("agent-reach")
    return [found] if found else None


def read_health(root: Path) -> dict:
    """跑一次 Agent-Reach 体检，返回结构化结果。

    返回形状：
    ```
    {"status": "ok" | "degraded" | "unavailable",
     "channels": {"github": {"status": "warn", "tier": 0, "name": "...", "message": "..."}, ...},
     "tier0_ok": 2, "tier0_total": 6,
     "reason": "..."}
    ```
    `status` 的含义：
    - `ok`：tier-0 全部可用。
    - `degraded`：tier-0 有掉队的（这是**要报给人看**的状态）。
    - `unavailable`：体检跑不起来（命令缺失、输出不可解析）——这本身也要报，
      因为它意味着「我们失去了对信源健康的观察能力」，而不是「信源健康」。

    ⚠️ 不使用 `agent-reach watch` 而是 `agent-reach doctor --json`：watch 的输出
    是给人看的文本（每条问题带多行安装指引），而简报需要结构化判断「哪些渠道是
    tier-0、哪些是 warn、哪些是 off」。doctor --json 给出同样的渠道状态，字段是
    `status/name/message/tier/backends/active_backend`，可以直接判 tier。
    两个命令探测的渠道集合相同。
    """
    cmd = agent_reach_cmd(root)
    if not cmd:
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": "找不到 agent-reach 可执行文件（ADR-0021：只装 CLI、不入 PATH）"}

    try:
        proc = subprocess.run(
            cmd + ["doctor", "--json"],
            capture_output=True, text=True, timeout=TIMEOUT,
            cwd=str(root),
        )
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": f"agent-reach doctor 超时（>{TIMEOUT}s）"}
    except OSError as exc:
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": f"agent-reach doctor 无法执行：{exc}"}

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": f"agent-reach doctor 退出码 {proc.returncode}"
                          + (f"：{tail[-1][:200]}" if tail else "")}

    raw = (proc.stdout or "").strip()
    start = raw.find("{")
    if start < 0:
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": "agent-reach doctor 未输出 JSON"}
    try:
        data = json.loads(raw[start:])
    except json.JSONDecodeError as exc:
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": f"agent-reach doctor 输出无法解析：{exc}"}

    if not isinstance(data, dict):
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(TIER0),
                "reason": "agent-reach doctor 输出不是对象"}

    # 只保留 tier-0（见模块文档的「边界二」）。
    channels: dict[str, dict] = {}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        if val.get("tier") != 0:
            continue
        channels[str(key)] = {
            "status": str(val.get("status") or "unknown"),
            "tier": val.get("tier"),
            "name": str(val.get("name") or key),
            "message": str(val.get("message") or ""),
        }

    tier0_total = len(TIER0)
    tier0_ok = sum(1 for k in TIER0
                   if channels.get(k, {}).get("status") == "ok")
    return {
        "status": "ok" if tier0_ok == tier0_total else "degraded",
        "channels": channels,
        "tier0_ok": tier0_ok,
        "tier0_total": tier0_total,
        "reason": "",
    }


def render(health: dict) -> list[str]:
    """把体检结果渲染成简报第 ⑤ 段的条目。

    只在「有问题」时返回非空——健康时不占第 ⑤ 段的位置。
    """
    if not health:
        return []
    status = health.get("status")
    if status == "ok":
        return []

    out: list[str] = []
    if status == "unavailable":
        out += ["**信源健康观测不可用**（ADR-0021）：" + str(health.get("reason") or "未知原因"),
                "  这不等于「信源都健康」——它等于我们**失去了观察能力**：",
                "  采集面悄悄变窄时不会有任何信号。看守只能发现「系统没跑」，",
                "  发现不了「信源挂了但系统还在跑」。",
                ""]
        return out

    broken = []
    for key, val in (health.get("channels") or {}).items():
        st = val.get("status")
        if st in ("off", "error", "warn"):
            broken.append((key, val))
    if not broken:
        return []

    out += [f"**信源渠道失效**（ADR-0021，tier-0 "
            f"{health.get('tier0_ok')}/{health.get('tier0_total')} 可用）："]
    for key, val in broken:
        mark = "✗" if val.get("status") in ("off", "error") else "!"
        out += [f"- {mark} {val.get('name') or key}：{val.get('message') or '（无说明）'}"]
    out += ["  这**不熔断**（ADR-0022 第五条）：采集面变窄不等于不能发文，",
            "  停发代价过大。它只报告，由你判断今天要不要换个选题。", ""]
    return out
