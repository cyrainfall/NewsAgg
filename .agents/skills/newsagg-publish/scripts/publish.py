#!/usr/bin/env python3
"""发布包装层：串行锁 + 前置条件检查 + 发布 + 反查验证 + 熔断。

设计依据：
- ADR-0024 发布后反查验证（不信任 toutiao-ops 的返回值）
- ADR-0030 浏览器命令必须串行；并发冲突不算失败
- ADR-0011 两类重试：只读可重试，写操作（发布）绝不自动重试
- ADR-0013 / 0020 四道门 + 发布后反查（第五道）
- ADR-0023 2GB 内存约束：发布前内存预检、Chrome 用完即杀
- ADR-0014 dry-run：dryRun 默认 True

本脚本是**包装层**，不重新实现发布逻辑：它调用 vendored 的 toutiao-ops CLI。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

# ADR-0030：并发冲突的识别特征（瞬时竞态，可重试，不算失败）
CONCURRENCY_MARKERS = [
    "Target page, context or browser has been closed",
    "正在现有的浏览器会话中打开",
    "ProcessSingleton",
]

# ADR-0030：登录失效的识别特征
LOGIN_MARKERS = [
    "未登录",
    "not logged in",
    "auth/page/login",
    "请先执行: toutiao auth login",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_title_rules():
    """加载同目录的 `title_rules`（标题规则的唯一实现）。

    与 `cardfm.py` 同一套做法：规则本身在 `platforms/title-rules.md`（归本人，ADR-0018），
    判定逻辑在这里（一份实现，`build_brief.py` 也从这里读——ADR-0042 的教训：
    契约的两份实现会漂移，且漂移是静默的）。
    """
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import title_rules
    return title_rules


class Breaker:
    """熔断状态（ADR-0025：存仓内文件，便于人工解除、guard 可同步读）。"""

    def __init__(self, path: Path):
        self.path = path

    def state(self) -> dict:
        if not self.path.exists():
            return {"tripped": False}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"tripped": False, "unreadable": True}

    def trip(self, reason: str, detail: dict | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "tripped": True,
                    "reason": reason,
                    "detail": detail or {},
                    "tripped_at": datetime.now(CST).isoformat(),
                    "how_to_clear": "人工修复后删除本文件即可解除（ADR-0011）",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class FileLock:
    """跨进程串行锁（ADR-0030）。

    关于陈旧锁：不能只检查文件存在性——进程崩溃会留下永久锁。
    因此记录 pid，并用 kill(pid, 0) 判断持有者是否仍存活。
    """

    def __init__(self, path: Path, timeout: float = 300.0, poll: float = 2.0):
        self.path = path
        self.timeout = timeout
        self.poll = poll

    def _holder_alive(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return False  # 无法解析 → 视为陈旧
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(
                    fd,
                    json.dumps({"pid": os.getpid(), "at": datetime.now(CST).isoformat()}).encode(),
                )
                os.close(fd)
                return self
            except FileExistsError:
                if not self._holder_alive():
                    try:
                        self.path.unlink()
                        continue  # 陈旧锁，立即重试
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"等待浏览器锁超时（{self.timeout}s）：{self.path}")
                time.sleep(self.poll)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def run_toutiao(args: list[str], env: dict, timeout: int = 300) -> tuple[int, str, str]:
    """调用 vendored toutiao-ops CLI。返回 (退出码, stdout, stderr)。

    ADR-0028：必须检查退出码，不经管道（管道会掩码退出码）。
    ADR-0030：必须经由本函数串行调用。
    ADR-0045：**必须把 stdout/stderr 重定向到文件，不能用管道捕获**。
        toutiao-ops 的 `run()` 在 `console.log(...)` 之后**立刻** `process.exit(0)`；
        Node 对管道写入是异步的，进程退出时尚未 flush 的部分会被丢掉。
        `content list --limit 20` 的输出约 103KB，走管道只回收到约 64KB，
        而且是在一个 JSON 字符串**中间**截断 —— 调用方 json.loads 失败，
        反查被判成「查不到」。实测对照：同一命令 `> file` 得 103296 字节且合法，
        `capture_output=True` 得 64788 字节且非法。
        文件描述符是同步写，不受进程提前退出影响。
    """
    with tempfile.TemporaryDirectory(prefix="newsagg-tt-") as td:
        out_path = Path(td) / "stdout"
        err_path = Path(td) / "stderr"
        with out_path.open("wb") as fo, err_path.open("wb") as fe:
            proc = subprocess.run(
                ["node", "index.js", *args],
                cwd=env["TT_CLI_DIR"],
                env=env["ENVIRON"],
                stdout=fo,
                stderr=fe,
                timeout=timeout,
            )
        return (
            proc.returncode,
            out_path.read_text(encoding="utf-8", errors="replace"),
            err_path.read_text(encoding="utf-8", errors="replace"),
        )


def classify_failure(text: str) -> str:
    """把失败分类，用于决定"可重试"还是"熔断"（ADR-0011 + ADR-0030）。"""
    low = text.lower()
    if any(m.lower() in low for m in CONCURRENCY_MARKERS):
        return "concurrency"  # 瞬时竞态 → 可重试
    if any(m.lower() in low for m in LOGIN_MARKERS):
        return "login_lost"  # 登录失效 → 熔断
    return "unknown"


def memory_check(min_free_mb: int) -> tuple[bool, str]:
    """发布前内存预检（ADR-0023）。macOS/Linux 各自的取值方式。"""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
            page = 4096
            free = inactive = 0
            for line in out.splitlines():
                if "page size of" in line:
                    m = re.search(r"page size of (\d+)", line)
                    if m:
                        page = int(m.group(1))
                if line.startswith("Pages free"):
                    free = int(re.search(r"(\d+)", line).group(1))
                if line.startswith("Pages inactive"):
                    inactive = int(re.search(r"(\d+)", line).group(1))
            avail_mb = (free + inactive) * page / 1024 / 1024
        else:
            with open("/proc/meminfo") as f:
                info = f.read()
            avail = re.search(r"MemAvailable:\s+(\d+) kB", info)
            avail_mb = int(avail.group(1)) / 1024 if avail else 0
    except Exception as exc:  # noqa: BLE001
        return True, f"内存检测失败（跳过预检）：{exc}"

    if avail_mb < min_free_mb:
        return False, f"可用内存不足：{avail_mb:.0f}MB < {min_free_mb}MB"
    return True, f"可用内存 {avail_mb:.0f}MB ≥ {min_free_mb}MB"


def count_title_kinds(payload: dict, title: str) -> dict:
    """数一数这个标题下有几条文章、几条微头条（ADR-0047）。

    反查原本只问「标题出现没有」，因此量不出「多发了一条微头条」——
    10-14 与 10-18 各发生一次，两次都被判成 `title_found` 而无人知晓。
    这里补上计数，让重复出现在账本里变成可查证的事实。

    与 `performance.py:classify_kind` 同一套判据（gid 位数 / URL 形态 /
    `content` 是否非空），因为它和循环 C 必须对「什么算一篇文章」有同一答案。
    """
    kinds = {"article": 0, "weitoutiao": 0}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return kinds
    for wrapper in items:
        data = wrapper.get("data") if isinstance(wrapper, dict) else None
        if not isinstance(data, list):
            continue
        for cell in data:
            base = ((cell.get("assembleCell") or {}).get("itemCell") or {}).get("articleBase") \
                if isinstance(cell, dict) else None
            if not isinstance(base, dict):
                continue
            if str(base.get("title") or "").strip() != title.strip():
                continue
            gid = str(base.get("gidStr") or "")
            url = str(base.get("articleURL") or base.get("displayURL") or "")
            content = str(base.get("content") or "").strip()
            votes = 0
            if gid and len(gid) <= 17:
                votes += 1
            if "/a" in url and "/item/" not in url:
                votes += 1
            if content:
                votes += 1
            kinds["weitoutiao" if votes >= 2 else "article"] += 1
    return kinds


def find_gid(payload: dict, title: str) -> str | None:
    """反查时顺手把这篇的 gid 记下来（ADR-0053）。

    gid 是平台分配的、不受输入路径影响的唯一标识。没有它，循环 C 只能用
    **标题**把平台作品关联回本地 slug，而标题恰好会被输入框吞字（ADR-0051）——
    2026-10-21 那篇就因为「中间层岗位」被收成「中间层位」而判成 `matched: false`，
    既不进基准序列也不消耗冷启动豁免。账本从这一天起记 gid，但**历史记录没有**，
    所以 `performance.py` 仍是「gid 优先、标题兜底」，不能只靠 gid。

    同一标题下若有多条（微头条泄漏，ADR-0047），优先返回**文章**那条。
    """
    fallback = None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None
    for wrapper in items:
        data = wrapper.get("data") if isinstance(wrapper, dict) else None
        if not isinstance(data, list):
            continue
        for cell in data:
            base = ((cell.get("assembleCell") or {}).get("itemCell") or {}).get("articleBase") \
                if isinstance(cell, dict) else None
            if not isinstance(base, dict):
                continue
            if str(base.get("title") or "").strip() != title.strip():
                continue
            gid = str(base.get("gidStr") or "").strip()
            if not gid:
                continue
            url = str(base.get("articleURL") or base.get("displayURL") or "")
            content = str(base.get("content") or "").strip()
            votes = 0
            if len(gid) <= 17:
                votes += 1
            if "/a" in url and "/item/" not in url:
                votes += 1
            if content:
                votes += 1
            if votes < 2:      # 文章优先
                return gid
            fallback = fallback or gid
    return fallback


def read_publish_snapshot(out_dir: Path, slug: str) -> tuple[str, str] | None:
    """读成稿（标题 + 正文）。成稿由写作阶段写入 outputs/。"""
    md = out_dir / f"{slug}.md"
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8")
    title = ""
    body = text
    m = re.match(r"^#\s+(.+)$", text, re.M)
    if m:
        title = m.group(1).strip()
        body = text[m.end():].lstrip("\n")
    return title, body


def snapshot_fingerprint(md_path: Path) -> dict:
    """记录发布那一刻成稿的指纹（ADR-0036）。

    风格回路需要「发布快照」做 diff 基线。不能用「该文件最后一次提交」推断——
    本人改稿并 push 之后，最后一次提交就变成了**改稿**，基线随之错位，
    diff 恒为空、风格学习静默失效（实测踩到）。
    因此发布时把指纹写进账本，让快照成为**记录下来的事实**而非事后推断。
    """
    text = md_path.read_text(encoding="utf-8")
    fp = {
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "published_at": datetime.now(CST).isoformat(),
    }
    try:
        # 必须带 -w：只算哈希而不写对象时，该 blob 在 ODB 中并不存在。
        # 那样只有在「事后有人把同样内容提交进 git」时才碰巧可解析——
        # 而发布与提交是两个独立步骤（发布后由编排 agent commit），
        # 提交失败或内容再改一次，快照就永久取不到，静默退回路径 2。
        # -w 让快照在发布那一刻就独立于后续任何操作而存在。
        proc = subprocess.run(["git", "hash-object", "-w", str(md_path)],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            fp["git_blob"] = proc.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return fp


def append_ledger(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_ledger(path: Path) -> list[dict]:
    """读全部发布账本记录（容忍坏行）。"""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ADR-0014 锁定的运行参数：每日发布上限 1 篇。
MAX_PUBLISH_PER_DAY = 1

# 算作「已发布」的 verdict。draft_saved 是 dry-run 的草稿——它没发出去，
# 不该消耗当天的额度（否则一次 dry-run 验证就会把当天堵死）。
PUBLISHED_VERDICTS = ("title_found", "title_found:reviewing", "title_found:published")


def count_published_today(ledger: Path, date: str) -> list[dict]:
    """今天已经真发出去几篇（ADR-0014：每日发布上限 1 篇）。

    ## 为什么这道闸必须在脚本层

    「每日 1 篇」此前只存在于两处：分拣输入包的 `limits.max_articles_per_day`
    和 `newsagg-triage/SKILL.md` 的纪律条文。两者都是**约定**，不是**闸门**：
    约定只在有人（或模型）遵守时有效，而发布是这条流水线上唯一不可撤销的
    写操作——重复发文是平台明令禁止的（ADR-0047 记录过两次因此被删不掉的
    微头条）。

    实测：`grep "max_articles_per_day" run_all.sh` 零命中。脚本层根本没有这个
    概念，全靠上游自觉。无人值守运行时，唯一的执行者是 agent——而 agent
    可能因为续跑、补写、或者流程被中断后重跑而调用发布两次。

    ## 判据：账本里今天的真发布记录数

    用账本而不是「今天有没有成稿」——成稿可以写好几篇，发布才是消耗额度的事。
    用 `verdict` 而不是「命令有没有返回 0」——dry-run 存草稿也算返回 0，但它没发。

    ## 误发不占额度

    标了 `accidental: true` 的记录**不计入**额度：那是操作失误或测试跑漏，不是
    当天的正常产出。若计入，一次误发就会把当天剩下的正常发文也堵死——用一个
    已经发生的错误去惩罚正确的那一次，没有道理。

    但误发仍然**必须**留在账本里：删掉它会让「今天到底发了几篇」与平台实际状态
    不符，而账本的意义正是成为唯一可信的本地记录。
    """
    today = []
    for rec in read_ledger(ledger):
        if not str(rec.get("at", "")).startswith(date):
            continue
        if rec.get("accidental"):
            continue
        verdict = str(rec.get("verdict") or "")
        if verdict in PUBLISHED_VERDICTS:
            today.append(rec)
    return today


def main() -> int:
    ap = argparse.ArgumentParser(description="NewsAgg 发布包装层")
    ap.add_argument("--slug", required=True, help="成稿 slug（对应 outputs/<slug>.md）")
    ap.add_argument("--cover", required=True, help="封面图路径（头条必填）")
    ap.add_argument("--mode", choices=["dry-run", "publish"], default="dry-run",
                    help="dry-run 只填表不发布（默认）；publish 真发")
    # 作品声明（ADR-0013 / ADR-0046）。默认「个人观点，仅供参考」。
    # ⚠️ 已知合规风险：正文由 AI 生成，而《人工智能生成合成内容标识办法》
    # （2025-09-01 施行）第十条禁止"隐匿"标识，用户侧义务是发布时主动声明。
    # 用户于 2026-10-18 明确选择不用「引用AI」，改标「个人观点」，并已知悉
    # 该风险。见 docs/adr/0046-declaration-is-personal-opinion-not-ai.md。
    ap.add_argument("--declaration", default="个人观点",
                    help="作品声明（ADR-0013 / ADR-0046）：取材网络,引用站内,个人观点,引用AI,虚构演绎")
    ap.add_argument("--min-free-mb", type=int, default=800, help="发布前可用内存下限（ADR-0023）")
    ap.add_argument("--lock-timeout", type=float, default=300.0)
    ap.add_argument("--account", default="default")
    ap.add_argument("--card-file", default="",
                    help="本篇承接的观点卡文件名（ADR-0037：循环 C 靠它把表现数据归因回卡）")
    ap.add_argument("--allow-daily-override", action="store_true",
                    help="越过「每日 1 篇」上限（ADR-0014）。仅用于人工补发/重发，"
                         "调用方必须同时提供 --daily-override-reason 说明为什么")
    ap.add_argument("--daily-override-reason", default="",
                    help="越过每日上限的理由（会写进账本，便于事后回看为什么今天发了两篇）")
    args = ap.parse_args()

    root = repo_root()
    now = datetime.now(CST)
    breaker = Breaker(root / ".newsagg" / "breaker.json")
    ledger = root / ".newsagg" / "publish-ledger.jsonl"
    today = now.strftime("%Y-%m-%d")

    # ── 门 0：熔断状态（ADR-0025：这是 guard 要同步读的部分）──
    st = breaker.state()
    if st.get("tripped"):
        print(json.dumps({
            "ok": False, "stage": "breaker",
            "message": "熔断中，拒绝发布。修复后删除 .newsagg/breaker.json 解除。",
            "reason": st.get("reason"), "tripped_at": st.get("tripped_at"),
        }, ensure_ascii=False, indent=2))
        return 1

    # ── 门 0.5：每日发布上限（ADR-0014：每日 1 篇）──
    # 只在真发模式下查。dry-run 不消耗额度（存草稿不算发布），否则一次
    # 例行验证就会把当天堵死。
    # 覆盖参数存在，是因为「今天已经发过一篇」不等于「这一篇一定不该发」：
    # 上一篇可能是误发（ADR-0047 的微头条泄漏、ADR-0045 的截断误判），
    # 需要人工补发或重发。这时应当由人显式说一句，而不是让系统替人决定。
    if args.mode == "publish" and not args.allow_daily_override:
        done = count_published_today(ledger, today)
        if len(done) >= MAX_PUBLISH_PER_DAY:
            print(json.dumps({
                "ok": False, "stage": "daily_limit",
                "message": (f"今天（{today}）已发布 {len(done)} 篇，"
                            f"达到 ADR-0014 的每日上限 {MAX_PUBLISH_PER_DAY} 篇。"
                            "若确需补发/重发，加 --allow-daily-override 并写明理由。"),
                "published_today": [{"slug": r.get("slug"), "at": r.get("at"),
                                     "gid": r.get("gid"), "verdict": r.get("verdict")}
                                    for r in done],
            }, ensure_ascii=False, indent=2))
            return 1

    # ── 成稿存在性 ──
    snap = read_publish_snapshot(root / "outputs", args.slug)
    if snap is None:
        print(json.dumps({"ok": False, "stage": "input",
                          "message": f"找不到成稿：outputs/{args.slug}.md"},
                         ensure_ascii=False, indent=2))
        return 1
    title, body = snap
    if not title:
        print(json.dumps({"ok": False, "stage": "input",
                          "message": "成稿缺少一级标题（# 标题）"}, ensure_ascii=False, indent=2))
        return 1

    # ── 标题硬下限（`platforms/title-rules.md`，规则唯一副本）──
    # 为什么在这里拦而不是让写作阶段自觉：头条的审核把「标题夸张」与敏感词同列为退回项，
    # 而**退回的文章再改也不会被推荐**——下行风险是「这一篇归零」而不是「少一点点击」。
    # 对一个月发 1 篇的账号，保守是划算的（ADR-0069）。
    tr = _load_title_rules()
    rules = tr.load_rules(root)
    if not rules.get("loaded"):
        # 规则读不上 ≠ 可以随便发。但也不能因此让整轮停摆——
        # 记下来，让简报告诉人（ADR-0062 的判据：谁会读它，读不到会怎样）。
        print(json.dumps({"ok": True, "stage": "title_rules", "warning": rules.get("error"),
                          "message": "标题规则文件读不上，本次只按平台硬长度限制放行"},
                         ensure_ascii=False, indent=2))
    else:
        problems = tr.check_title(title, rules)
        if problems:
            print(json.dumps({
                "ok": False, "stage": "title_rules",
                "message": "标题不满足 `platforms/title-rules.md` 的硬下限",
                "problems": problems,
                "source": rules.get("source"),
            }, ensure_ascii=False, indent=2))
            return 1
    if not Path(args.cover).exists():
        print(json.dumps({"ok": False, "stage": "input",
                          "message": f"封面不存在：{args.cover}（头条必填，ADR-0013）"},
                         ensure_ascii=False, indent=2))
        return 1

    # ── 门 3：内存预检（ADR-0023）──
    mem_ok, mem_msg = memory_check(args.min_free_mb)
    if not mem_ok:
        breaker.trip("memory_insufficient", {"detail": mem_msg, "slug": args.slug})
        print(json.dumps({"ok": False, "stage": "memory", "message": mem_msg},
                         ensure_ascii=False, indent=2))
        return 1

    # ── 环境：ADR-0028 的 HOME 重定向（状态与浏览器缓存一起）──
    ws_home = root / ".home"
    ws_home.mkdir(parents=True, exist_ok=True)
    env = {
        "TT_CLI_DIR": str(root / "reference" / "toutiao-ops" / "cli"),
        "ENVIRON": {
            **os.environ,
            "HOME": str(ws_home),                 # 重定向状态 + Playwright 缓存
            "PATH": f"{root / '.uv-bin'}:{os.environ.get('PATH', '')}",
        },
    }

    steps: list[dict] = []

    with FileLock(root / ".newsagg" / "browser.lock", timeout=args.lock_timeout):
        # ── 门 2：登录预检（只读，可重试；ADR-0011 + ADR-0030）──
        login_ok = False
        for attempt in range(1, 4):
            code, out, err = run_toutiao(["auth", "check", "--account", args.account],
                                         env, timeout=180)
            kind = classify_failure(out + err)
            if code == 0 and '"logged_in": true' in out:
                login_ok = True
                steps.append({"step": "auth_check", "attempt": attempt, "ok": True})
                break
            steps.append({"step": "auth_check", "attempt": attempt, "ok": False,
                          "kind": kind, "code": code})
            if kind == "login_lost":
                break
            time.sleep(5)
        if not login_ok:
            breaker.trip("login_lost", {"slug": args.slug, "steps": steps})
            print(json.dumps({
                "ok": False, "stage": "auth", "message": "登录预检失败，已熔断",
                "steps": steps, "how_to_fix": "重新扫码：node index.js auth login（HOME=<repo>/.home）",
            }, ensure_ascii=False, indent=2))
            return 1

        # ── 发布或 dry-run ──
        cmd = ["publish", "article",
               "--title", title,
               "--content-file", str(root / "outputs" / f"{args.slug}.md"),
               "--cover", str(Path(args.cover).resolve()),
               "--cover-mode", "single",
               "--declaration", args.declaration,
               "--account", args.account,
               # ADR-0034：头条发布页默认勾选「同时发布微头条」，必须显式取消。
               # 本项目一次运行只产出一篇文章；同发微头条会造成内容重复
               # （平台禁止批量重复内容），且无法被每日上限与冷却期约束。
               "--no-weitoutiao"]
        if args.mode == "dry-run":
            cmd.append("--draft")   # 存草稿而非发布（最接近"停在发布前"）

        code, out, err = run_toutiao(cmd, env, timeout=420)
        kind = classify_failure(out + err)
        steps.append({"step": "publish" if args.mode == "publish" else "draft",
                      "mode": args.mode, "code": code, "kind": kind})

        # ADR-0051：平台实际收到的标题可能被吞字。toutiao-ops 现在会读回输入框
        # 并把真实值放在 `title` 字段、把我们的原值放在 `expected_title`。
        # 反查必须按**平台实际标题**去查：按原标题查不到会把一次真实发布
        # 误判成 not_published_needs_review（2026-10-21 实测）。
        title_on_platform = title
        try:
            published = json.loads(out[out.find("{"):]) if "{" in out else {}
            if isinstance(published, dict):
                got = str(published.get("title") or "").strip()
                if got and got != title.strip():
                    title_on_platform = got
        except (json.JSONDecodeError, ValueError):
            pass

        record = {
            "at": now.isoformat(), "slug": args.slug, "title": title,
            "title_on_platform": title_on_platform,
            "mode": args.mode, "steps": steps, "stdout_tail": out[-800:],
            # ADR-0014：如果这一篇是越过「每日 1 篇」上限发的，把理由记进账本。
            # 事后回看时，「今天为什么有两篇」不该靠回忆来回答。
            "daily_override": (
                {"reason": args.daily_override_reason or "（未说明理由）"}
                if args.allow_daily_override else None
            ),
            # ADR-0037：把「这篇属于哪张卡」记下来。没有它，循环 C 读回来的
            # 表现数据只能停在文章粒度，无法回答"哪些卡跑出了数据"——
            # 而那正是 ADR-0010 要的排序参考。空值写 null 而不是省略：
            # 省略与"决策未给卡"在消费端不可区分。
            "card_file": args.card_file or None,
            # ADR-0036：发布快照指纹，供风格回路做 diff 基线
            "snapshot": snapshot_fingerprint(root / "outputs" / f"{args.slug}.md"),
            # ADR-0069：把标题的观察项标签记进账本。
            # 观察项**不拦发**（n=10，展现量与账号权重共线，样本不支持升为硬规则），
            # 但每次发布都留一份标签，样本攒够了才能判断哪一条值得升为硬下限。
            # 写 null 而不是省略：省略与"没打标签"在消费端不可区分。
            "title_tags": (tr.tag_title(title, rules)
                           if rules.get("loaded") else None),
        }

        # ── 门 5：反查验证（ADR-0024，仅真发时）──
        if args.mode == "publish":
            if code != 0:
                if kind == "concurrency":
                    # 并发冲突是瞬时竞态，但发布是写操作 → 不自动重试（ADR-0011）
                    record["verdict"] = "unknown_needs_review"
                    record["note"] = "发布遇并发冲突，结果未知；按 ADR-0011 不重发，需回后台核实"
                elif kind == "login_lost":
                    breaker.trip("login_lost", {"slug": args.slug})
                    record["verdict"] = "login_lost"
                else:
                    record["verdict"] = "publish_failed"
                append_ledger(ledger, record)
                print(json.dumps({"ok": False, "stage": "publish", **record},
                                 ensure_ascii=False, indent=2)[:4000])
                return 1

            # 反查：按**平台实际标题**在作品列表中查找（ADR-0051）。
            # 用原标题查会在被吞字时找不到，把真实发布报成未发出。
            time.sleep(8)  # 给平台一点时间
            rc, cout, cerr = run_toutiao(["content", "list", "--limit", "20",
                                          "--account", args.account], env, timeout=240)
            record["verify_code"] = rc
            found = None
            parse_failed = False
            if rc == 0:
                try:
                    payload = json.loads(cout)
                    blob = json.dumps(payload, ensure_ascii=False)
                    if title_on_platform in blob:
                        found = "title_found"
                        # 尽力判断状态（reviewing / rejected / published）
                        for status in ("rejected", "reviewing", "published"):
                            if status in blob:
                                found = f"title_found:{status}"
                                break
                    # ADR-0047：数一数这个标题下有几条**文章**、几条微头条。
                    # 一次发布动作本该只产出一篇文章；若同时出现微头条，
                    # 那是 --no-weitoutiao 没生效——这在 10-14 和 10-18 各发生过一次，
                    # 而反查只查标题、量不出「多了一条」，所以两次都没被发现。
                    kinds = count_title_kinds(payload, title_on_platform)
                    record["verify_kinds"] = kinds
                    # ADR-0053：把 gid 记进账本。循环 C 用它关联平台作品与本地 slug，
                    # 比标题可靠——标题会被输入框吞字（ADR-0051）。
                    gid = find_gid(payload, title_on_platform)
                    if gid:
                        record["gid"] = gid
                    if kinds.get("weitoutiao", 0) > 0:
                        record["weitoutiao_leak"] = (
                            f"同一标题下发现 {kinds['weitoutiao']} 条微头条："
                            "`--no-weitoutiao` 未生效（ADR-0047）"
                        )
                except json.JSONDecodeError as exc:
                    # SKILL.md：解析失败**不可判为"未找到"**——两者会导向完全不同的
                    # 人工动作（前者去后台核实，后者可能诱使重发）。
                    # 2026-10-18 实测：CLI 的 stdout 被 process.exit 截断，
                    # 这里曾静默落入 title_not_found，把一次**成功发布**报成失败。
                    parse_failed = True
                    record["verify_parse_error"] = f"{exc}；原始输出前 200 字：{cout[:200]}"
            record["verify_result"] = found or (
                "verify_parse_failed" if parse_failed else "title_not_found"
            )

            if parse_failed:
                # 结果未知 → 不重发、不判成功、不熔断，交人工核实（ADR-0011）
                record["verdict"] = "unknown_needs_review"
                append_ledger(ledger, record)
                print(json.dumps({"ok": False, "stage": "verify", **record},
                                 ensure_ascii=False, indent=2)[:4000])
                return 1
            if found is None:
                # 查不到 → 视为未发出，但不重发（ADR-0011）
                record["verdict"] = "not_published_needs_review"
                append_ledger(ledger, record)
                print(json.dumps({"ok": False, "stage": "verify", **record},
                                 ensure_ascii=False, indent=2)[:4000])
                return 1
            if found.endswith(":rejected"):
                breaker.trip("content_rejected", {"slug": args.slug, "title": title})
                record["verdict"] = "rejected"
                append_ledger(ledger, record)
                print(json.dumps({"ok": False, "stage": "verify", **record},
                                 ensure_ascii=False, indent=2)[:4000])
                return 1
            record["verdict"] = found  # title_found / title_found:reviewing / :published
        else:
            record["verdict"] = "draft_saved" if code == 0 else "draft_failed"

        append_ledger(ledger, record)
        print(json.dumps({"ok": code == 0, **record}, ensure_ascii=False, indent=2)[:4000])
        return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
