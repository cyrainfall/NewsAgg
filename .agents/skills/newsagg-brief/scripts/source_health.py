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

## 上游漂移：v0 → v1.6.0（2026-10-31 实测，ADR-0089）

ADR-0021 早就写了「它是活跃依赖、不钉版本，上游变更会影响本项目」。2026-10-31 装上
真身（`Panniantong/Agent-Reach` v1.6.0）后，漂移一次到齐：

1. `doctor --json` 这个参数**没了**（`unrecognized arguments: --json`）——本文件原来
   调的就是它，装上之后仍然会报「输出无法解析」，等于白装。
2. `doctor` 变成**纯本地状态**（`no network`），实测连通的是 `check`。
   两者语义不同：doctor 回答「装没装、缺什么工具」，check 回答「此刻连不连得上」。
   ADR-0021 要的是后者（信源变窄），所以主路径改成 `check`，doctor 作兜底。
3. 渠道名也变了：ADR-0021 列的 `rss` / `exa_search` 在 v1.6.0 的渠道表里**不存在**
   （Exa 只是 `web search` 的后端）。名字对不上，旧 `TIER0` 就永远算不满分母。

这也是「装上 ≠ 接上」的第二个例子：前一个是 ADR-0059（ADR 写了、代码没有）。

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
from datetime import datetime
from pathlib import Path

# tier-0 = 装好即用、无需配置。ADR-0021 列举的是 v0 时代的六个名字
# （web / rss / exa_search / github / v2ex / youtube）；v1.6.0 把它们收成了四个：
# `rss` 与 `exa_search` 已不是渠道（前者没有、后者是 `web search` 的后端）。
# 保留旧名字会让分母永远算不满、第 ⑤ 段天天挂着两条「假的失效」。
TIER0 = ("web", "github", "v2ex", "youtube")

# `check` 是**实测**：每个 action 一次外部请求。`--timeout 8` 把单次请求压到 8 秒，
# 但 jina 那条读请求会吃满 20 秒硬超时（上游不听 `--timeout`，实测 21 秒），
# 所以真正管用的是**每个渠道一个外层超时**：慢的只拖自己，不会把整轮拖成「读不到」。
# 典型耗时（2026-10-31 实测）：web ≈25s、github ≈2s、v2ex ≈2s、youtube ≈3s。
CHECK_TIMEOUT_PER_CHANNEL = 30
CHECK_ACTION_TIMEOUT = 8

# `doctor` 不走网络，只读本地状态，所以给它一个很短的超时。
DOCTOR_TIMEOUT = 30

# 采集层真正在用的源（ADR-0029 的七源）。Agent-Reach 的渠道名与它们不是一一对应，
# 但 github / web 这两个 tier-0 渠道直接覆盖了 DailyHotApi 之外的深度信源，
# 它们失效才是「采集面变窄」。

# 部署级渠道策略文件（ADR-0078）。渠道可用性是**部署属性**，不是代码属性：
# 同一份代码在本机能用 Jina Reader，在不装代理的服务器上不能用。写在代码里
# 等于把「本机的网络」硬编码进整个系统。
POLICY_REL = Path("deploy") / "channel-policy.txt"

# 上一次判定的快照（2026-10-31 加）。为什么要它：
# 每天的渠道状态基本不变（`web` 的 jina 读长期 20 秒超时），若不比较上一次，
# 第 ⑤ 段每天印同样四条——而「每天必然出现的告警」会训练人整段略过
# （ADR-0006/0021/0031 反复要避免的「狼来了」）。
# 有快照之后，只在**状态发生变化**时展开细节，其余折成一行计数。
HEALTH_SNAPSHOT_REL = Path(".newsagg") / "source-health.json"

# 设为任意非空值时 `read_health()` 直接返回 `skipped`，不执行任何探测。
# 用途：简报的单元测试（不该为了验一句文案去联网几十秒）、离线运行、CI。
SKIP_HEALTH_ENV = "NEWSAGG_SKIP_SOURCE_HEALTH"


def read_policy(root: Path) -> dict[str, dict]:
    """读部署级渠道策略，返回 {渠道名: {"status": ..., "reason": ...}}。

    格式见 deploy/channel-policy.txt：一行 `<渠道名> | <状态> | <理由>`。
    文件不存在 → 空 dict（没有声明就是没有禁用，不报错——本机没这个文件
    是正常的，它只在服务器那份部署里有）。
    """
    path = root / POLICY_REL
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        name, status = parts[0], parts[1]
        reason = parts[2] if len(parts) > 2 else ""
        out[name] = {"status": status, "reason": reason}
    return out


def agent_reach_cmd(root: Path) -> list[str] | None:
    """找到 agent-reach 的可执行入口。

    ADR-0021 决定「只安装 CLI，不注册 SKILL.md，不 vendor 入仓」。那句话写在只装
    过 `reference/` 源码的年代，于是假定它**不在 PATH 里**。2026-10-31 装 v1.6.0
    实测：`uv tool install` 会把一个 shim 链接到 `~/.local/bin`，**那个目录在这台
    机器的 PATH 上**——所以最后那条 `shutil.which` 是真能找到的。包本体仍在仓内
    `.uv-tools/agent-reach`（gitignore），只有启动器在 PATH 上，与「不 vendor」
    的意图不冲突，但「不入 PATH」这个表述已经过期（ADR-0089）。

    找不到就返回 None，调用方按「读不到」处理。
    """
    cands = [
        root / ".uv-bin" / "agent-reach",
        root / ".uv-tools" / "agent-reach" / "bin" / "agent-reach",
        # Windows：uv tool 把 console script 装进 `Scripts/`（POSIX 是 `bin/`）。
        # 2026-10-31 实测——**漏了这一条，装好了也永远报「找不到可执行文件」**，
        # 与 ADR-0079/0080 记的那几处「同一份代码在 Windows 上少一条路径」同型。
        root / ".uv-tools" / "agent-reach" / "Scripts" / "agent-reach.exe",
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
    # 策略先读：即使体检跑不起来，已声明关掉的渠道也要报出来
    # （否则「体检挂了」会把「这个渠道我们本来就不用」这件事盖住）。
    policy = read_policy(root)
    disabled = {name: entry["reason"]
                for name, entry in policy.items()
                if entry.get("status") == "disabled"}
    effective = [k for k in TIER0 if k not in disabled]

    def _unavailable(reason: str) -> dict:
        return {"status": "unavailable", "channels": {}, "tier0_ok": 0,
                "tier0_total": len(effective), "policy_disabled": disabled,
                "reason": reason}

    cmd = agent_reach_cmd(root)
    if not cmd:
        return _unavailable("找不到 agent-reach 可执行文件（ADR-0021：只装 CLI、不入 PATH）")

    # 显式跳过开关。为什么需要它：`read_health()` 会真的跑一次联网实测（几十秒），
    # 而简报的单元测试也会走这里——一个只测「cookie 快过期那句话有没有印出来」的
    # 用例，不该因此等 75 秒（2026-10-31 实测：加健康检查后该用例从 0.02s 变 74.74s）。
    # 离线跑简报、以及 CI 里不想打网络时也用同一个开关。
    if os.environ.get(SKIP_HEALTH_ENV):
        return {"status": "skipped", "channels": {}, "tier0_ok": 0,
                "tier0_total": len([k for k in TIER0 if k not in disabled]),
                "policy_disabled": dict(disabled), "reason": f"{SKIP_HEALTH_ENV} 已设置"}

    # 一、主路径：`check` 实测（ADR-0021 要的「信源此刻能不能连上」）。
    # v1.6.0 的 `doctor` 是纯本地状态（no network），拿它当「信源健康」等于用
    # 「装没装」冒充「连不连得上」——这正是本文件要消灭的那类失真。
    #
    # 逐渠道跑而不是一条命令跑四个：实测一条 `check web github v2ex --timeout 8`
    # 就要 47 秒（jina 那条读请求会吃满 20 秒硬超时），四个渠道串起来会顶到
    # 外层超时，**一个慢渠道把整轮观测拖成「读不到」**。逐渠道跑之后，慢的那个
    # 只影响它自己。
    channels: dict[str, dict] = {}
    for name in TIER0:
        if name in disabled:
            continue
        data = _run_json(cmd, ["check", name, "--timeout", str(CHECK_ACTION_TIMEOUT)],
                         timeout=CHECK_TIMEOUT_PER_CHANNEL, cwd=root)
        if not data:
            continue
        got = _channels_from_check(data, disabled)
        if name in got:
            channels[name] = got[name]

    # 二、兜底：本地的 `doctor` 只补「没实测到」的那几个 tier-0。
    # 不用它覆盖已经实测到的结果（实测 > 本地状态），也**不把 tier-1/2 拉进来**
    # （见模块文档「边界二」：那会把第 ⑤ 段堆成与本项目无关的噪声）。
    observed = "check"
    pending = [n for n in TIER0 if n not in disabled and n not in channels]
    if pending:
        local = _run_json(cmd, ["doctor"], timeout=DOCTOR_TIMEOUT, cwd=root)
        if local and isinstance(local.get("result"), dict):
            for name, row in _channels_from_doctor(local["result"], disabled).items():
                if name in pending:
                    channels[name] = row
            observed = "check+doctor" if len(channels) > len(pending) else "doctor"

    if not channels:
        return _unavailable(
            "agent-reach 的 check 与 doctor 都读不到（ADR-0089：接口在 v1.6.0 已变）")

    return _with_delta(_verdict(channels, disabled, source=observed), root)


def _load_health_snapshot(root: Path) -> dict | None:
    """读上一次的判定快照；没有/坏了都返回 None（当作首次运行）。"""
    path = root / HEALTH_SNAPSHOT_REL
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_health_snapshot(root: Path, health: dict) -> None:
    """落盘本次判定。写不下去也不能影响简报（ADR-0006：简报必须无条件存在）。"""
    path = root / HEALTH_SNAPSHOT_REL
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "observed_by": health.get("observed_by"),
        "channels": {k: v.get("status")
                     for k, v in (health.get("channels") or {}).items()},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass


def _with_delta(health: dict, root: Path) -> dict:
    """把「与上一次相比变了什么」算进判定，并落盘本次快照。

    三种情况要分开：
    - **首次运行**（没有快照）：全部当成「新出现」，逐条展开——否则人不知道
      基线是什么。
    - **有变化**：只展开变了的渠道。
    - **没变化**：不展开，只留一行计数。

    注意比较的是**状态本身**（ok/warn/off/disabled），不是 message——
    「web 还是 warn，但原因从 jina 超时变成 429」不会触发这一轮的展开。
    这是刻意的：原因天天微调，展开它等于把逐条展开退化成每天都印。
    """
    previous = _load_health_snapshot(root)
    current = {k: v.get("status") for k, v in (health.get("channels") or {}).items()}

    if previous is None:
        health["delta"] = {"first_run": True, "changed": sorted(current),
                           "unchanged_bad": []}
    else:
        old = previous.get("channels") or {}
        names = set(old) | set(current)
        changed = sorted(k for k in names if old.get(k) != current.get(k))
        unchanged_bad = sorted(
            k for k in current
            if current[k] in ("warn", "off", "error") and k not in changed
        )
        health["delta"] = {
            "first_run": False,
            "changed": changed,
            "unchanged_bad": unchanged_bad,
            "previous_at": previous.get("at"),
        }

    _save_health_snapshot(root, health)
    return health


def _run_json(cmd: list[str], args: list[str], *, timeout: float, cwd: Path) -> dict | None:
    """跑一条 agent-reach 子命令并解析它输出的 JSON。失败一律返回 None（不抛）。"""
    try:
        # ⚠️ 必须显式 `encoding="utf-8"`：`text=True` 会用**系统 locale** 解码，
        # 而本机是 GBK——agent-reach 的 JSON 里有中文描述，GBK 解码会抛
        # `UnicodeDecodeError`（在 reader 线程里抛，主线程只看到空 stdout），
        # 于是「装上、能跑、接口也对」依然被判成不可用。2026-10-31 实测撞到。
        proc = subprocess.run(cmd + args, capture_output=True, timeout=timeout,
                              cwd=str(cwd), encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    start = raw.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(raw[start:])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# `doctor` 的状态词表 → 本文件的三态。词表来自 v1.6.0 实测输出：
#   github "unverified"（从没 check 过）、youtube "needs_setup"（缺 ffmpeg）、
#   未启用的渠道 "not_added"。
# `unverified` 记 `warn` 而不是 `ok`：它的字面意思就是「我们不知道」，
# 冒充健康会让「看不出」变成「看着没事」——与 ADR-0084 同一条教训。
_DOCTOR_STATUS = {
    "ok": "ok",
    "needs_setup": "warn",
    "unverified": "warn",
    "not_added": "off",
}


def _channels_from_check(data: dict, disabled: dict[str, str]) -> dict[str, dict]:
    """把 `check` 的逐 action 结果按渠道聚成三态。

    一个渠道有多个 action（例：`web` 的 search 走 exa、read 走 jina/exa），
    所以「部分通过」是真实存在的一档，不能折成通过或全挂：
    - 全部 action 通过 → `ok`
    - 部分通过         → `warn`（还能用，但有后端在退）
    - 全部不通过       → `off`
    """
    per: dict[str, list[dict]] = {}
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("channel") or "")
        if name and name not in disabled:
            per.setdefault(name, []).append(row)

    out: dict[str, dict] = {}
    for name, rows in per.items():
        passed = [r for r in rows if r.get("passed")]
        if len(passed) == len(rows):
            status = "ok"
        elif passed:
            status = "warn"
        else:
            status = "off"
        msgs = []
        for r in rows:
            if r.get("passed"):
                continue
            err = r.get("error") or {}
            bits = f"{r.get('action')}/{r.get('backend')}"
            code = err.get("code") or "FAIL"
            msg = (err.get("message") or "").strip()
            msgs.append(f"{bits}：{code}{(' ' + msg) if msg else ''}")
        out[name] = {"status": status, "name": name, "message": "；".join(msgs[:3])}
    return out


def _channels_from_doctor(result: dict, disabled: dict[str, str]) -> dict[str, dict]:
    """把 `doctor` 的本地状态聚成三态（兜底路径）。**只取 tier-0。**"""
    out: dict[str, dict] = {}
    for row in result.get("channels") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("channel") or "")
        if not name or name in disabled or name not in TIER0:
            continue
        raw_status = str(row.get("status") or "unknown")
        problems = [str(p) for p in (row.get("problems") or []) if str(p).strip()]
        hint = str(row.get("hint") or "").strip()
        message = "；".join(problems) if problems else hint
        out[name] = {
            "status": _DOCTOR_STATUS.get(raw_status, "warn"),
            "name": name,
            "message": message or f"本地状态 {raw_status}（未实测）",
            "raw": raw_status,
        }
    return out


def _verdict(channels: dict[str, dict], disabled: dict[str, str], *, source: str) -> dict:
    """按 tier-0 账算总判，并把部署级策略声明关掉的渠道标出来（ADR-0078）。"""
    for name, reason in disabled.items():
        channels[name] = {"status": "disabled", "name": name, "message": reason}

    effective = [k for k in TIER0 if k not in disabled]
    # 渠道表里根本没有的 tier-0（上游改名/移除），单列出来——它不是「坏了」，
    # 是「我们数的那几个名字已经不存在」，两者的处理方式不同（见 ADR-0089）。
    missing = [k for k in effective if k not in channels]
    stats = {"ok": 0, "warn": 0, "off": 0}
    for k in effective:
        st = channels.get(k, {}).get("status")
        if st in stats:
            stats[st] += 1

    return {
        "status": "ok" if (stats["ok"] == len(effective) and not missing) else "degraded",
        "channels": channels,
        "tier0_ok": stats["ok"],
        "tier0_partial": stats["warn"],
        "tier0_off": stats["off"],
        "tier0_total": len(effective),
        "policy_disabled": {k: v for k, v in disabled.items()},
        "missing_channels": missing,
        "observed_by": source,
        "reason": "",
    }


def render(health: dict) -> list[str]:
    """把体检结果渲染成简报第 ⑤ 段的条目。

    只在「有问题」时返回非空——健康时不占第 ⑤ 段的位置。

    **两种「不可用」必须分开说**（ADR-0078）：
    - `disabled` = **我们主动关掉的**（部署级策略声明过）。这不是故障，
      是一条需要读者知道的部署事实：今天这个渠道不参与，正文得从别处取。
    - `off`/`error`/`warn` = **它自己坏了**。这是需要人介入的事。

    混在一起说会犯两个错：把「我们关的」说成「它坏了」，人去查一个根本没
    坏的东西；把「它坏了」混进「我们关的」，人以为已经处理过了。
    """
    if not health:
        return []
    status = health.get("status")
    disabled = health.get("policy_disabled") or {}
    channels = health.get("channels") or {}

    out: list[str] = []

    # 一、主动关掉的渠道：即使体检全绿也要报——读者需要知道今天的采集面
    # 少了一块，以及少的那块是怎么补的。
    if disabled:
        out += ["**本次部署主动关掉了这些信源渠道**（ADR-0078，不是故障）："]
        for name, reason in disabled.items():
            out += [f"- ○ {name}：{reason}"]
        out += ["  关掉的渠道不参与采集，正文请从其余渠道取；",
                "  这一条不熔断，也不计入「可用率」的分母。", ""]

    if status == "ok":
        return out
    if status == "skipped":
        # 主动跳过不是「读不到」，不该占第 ⑤ 段。
        return out
    if status == "unavailable":
        out += ["**信源健康观测不可用**（ADR-0021）：" + str(health.get("reason") or "未知原因"),
                "  这不等于「信源都健康」——它等于我们**失去了观察能力**：",
                "  采集面悄悄变窄时不会有任何信号。看守只能发现「系统没跑」，",
                "  发现不了「信源挂了但系统还在跑」。",
                ""]
        return out

    # 上游把渠道改名/并入之后，`TIER0` 里的旧名字会「查无此渠道」。
    # 它既不进 channels，也不该被当成「坏了」——单列一条，否则这件事会静默消失，
    # 而「我们数的名字不存在了」恰恰是 ADR-0021 那类失真里最贵的一种。
    missing = list(health.get("missing_channels") or [])
    if missing:
        out += [f"**tier-0 渠道名在上游已不存在**（ADR-0089）：{'、'.join(missing)}",
                "  这不是「它坏了」，是我们数的名字过期了（可能被改名或并入别的渠道）。",
                "  在改 `TIER0` 之前，别把这一条当成信源失效。", ""]

    if health.get("observed_by") == "doctor":
        out += ["  （本轮只拿到**本地状态**：`check` 实测没跑通。"
                "下面这些说的是「装没装 / 缺什么工具」，不是「此刻连不连得上」。）", ""]

    broken = []
    for key, val in channels.items():
        st = val.get("status")
        if st in ("off", "error", "warn"):
            broken.append((key, val))
    if not broken:
        return out

    # 只展开「与上次相比变了」的那几个（见 HEALTH_SNAPSHOT_REL 的说明）。
    # 没有 delta（老调用方/测试手造的 dict）时退回「全部展开」，行为与改造前一致。
    delta = health.get("delta") or {}
    only_changes = bool(delta) and not delta.get("first_run")
    if only_changes:
        changed = set(delta.get("changed") or [])
        shown = [(k, v) for k, v in broken if k in changed]
        hidden = [k for k, _ in broken if k not in changed]
    else:
        shown, hidden = broken, []

    if shown:
        out += [f"**信源渠道失效**（ADR-0021，tier-0 {health.get('tier0_ok')}/{health.get('tier0_total')} 全通"
                f"｜部分可用 {health.get('tier0_partial', 0)} 个"
                f"、全挂 {health.get('tier0_off', 0)} 个）："]
        for key, val in shown:
            mark = "✗" if val.get("status") in ("off", "error") else "!"
            out += [f"- {mark} {val.get('name') or key}：{val.get('message') or '（无说明）'}"]
    if hidden and shown:
        # 变了的那几个已经展开，其余压成一行——它们昨天前天都是同一句。
        out += [f"  另有 {len(hidden)} 个渠道**状态未变**、不重复展开：{'、'.join(hidden)}"]
    elif hidden:
        # 今天什么都没变：一行说清「还在坏、但没新情况」，不占版面。
        out += [f"**信源仍有 {len(hidden)} 个渠道异常，状态与上次相同**："
                f"{'、'.join(hidden)}",
                "  （不展开细节——同样的原因连印几天会让人整段略过；"
                "要看具体原因，读 `.newsagg/source-health.json` 或跑 `agent-reach check`。）"]
    out += ["  这**不熔断**（ADR-0022 第五条）：采集面变窄不等于不能发文，",
            "  停发代价过大。它只报告，由你判断今天要不要换个选题。", ""]
    return out
