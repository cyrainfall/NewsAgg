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
    args = ap.parse_args()

    root = repo_root()
    now = datetime.now(CST)
    breaker = Breaker(root / ".newsagg" / "breaker.json")
    ledger = root / ".newsagg" / "publish-ledger.jsonl"

    # ── 门 0：熔断状态（ADR-0025：这是 guard 要同步读的部分）──
    st = breaker.state()
    if st.get("tripped"):
        print(json.dumps({
            "ok": False, "stage": "breaker",
            "message": "熔断中，拒绝发布。修复后删除 .newsagg/breaker.json 解除。",
            "reason": st.get("reason"), "tripped_at": st.get("tripped_at"),
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
    # 头条标题硬限制 2~30 字（ADR-0024）
    if not (2 <= len(title) <= 30):
        print(json.dumps({"ok": False, "stage": "input",
                          "message": f"标题长度 {len(title)} 不在 2~30 字内：{title}"},
                         ensure_ascii=False, indent=2))
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

        record = {
            "at": now.isoformat(), "slug": args.slug, "title": title,
            "mode": args.mode, "steps": steps, "stdout_tail": out[-800:],
            # ADR-0037：把「这篇属于哪张卡」记下来。没有它，循环 C 读回来的
            # 表现数据只能停在文章粒度，无法回答"哪些卡跑出了数据"——
            # 而那正是 ADR-0010 要的排序参考。空值写 null 而不是省略：
            # 省略与"决策未给卡"在消费端不可区分。
            "card_file": args.card_file or None,
            # ADR-0036：发布快照指纹，供风格回路做 diff 基线
            "snapshot": snapshot_fingerprint(root / "outputs" / f"{args.slug}.md"),
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

            # 反查：按标题在作品列表中查找
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
                    if title in blob:
                        found = "title_found"
                        # 尽力判断状态（reviewing / rejected / published）
                        for status in ("rejected", "reviewing", "published"):
                            if status in blob:
                                found = f"title_found:{status}"
                                break
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
