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

# 同目录的姊妹模块（toutiao_api.py）要能被 import。用显式 sys.path 注入而不是
# 依赖「当前工作目录」，因为这个脚本既可能被 run_all.sh 从仓根调用，也可能被
# performance.py 用 importlib 从任意 cwd 加载。
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# 账本的跨环境读取与归档（ADR-0074）。发布层是账本的**写者**，所以这里要
# 能给它写的记录补 `record_id`。
_LIB_DIR = Path(__file__).resolve().parents[4] / ".agents" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import publish_ledger as ledger_mod  # noqa: E402

CST = timezone(timedelta(hours=8))

# 两个后端的适用范围（2026-10-25 实测后定稿）：
#
#   api     —— 登录预检、反查。都是只读，都已实测可用，不需要浏览器。
#   browser —— 发布 / 存草稿。走 vendored 的 toutiao-ops（Playwright DOM）。
#
# 为什么写路径不给 api（ADR-0073 实测后定稿）：
#
# 早先这里写着「字段名已经从真实 bundle 里逐个核对过，只是嵌套字段的序列化形式
# 读不出来」——**那句话是错的**。2026-10-27 抓包实测：真实 publish 请求 31 个
# 字段，仓库的 build_publish_form 有 35 个键，**只有 9 个名字对得上**；另外
# 写路径还有平台自己的签名与风控闸（_signature / msToken / a_bogus /
# x-secsdk-csrf-token），纯 HTTP 客户端一个都不处理。
#
# 所以 api 写路径默认拒绝，要显式 --allow-unverified-write 才动，账本会记下
# 这一点。详见 docs/adr/0073-pure-http-write-path-is-not-viable.md 与
# docs/publish-request-shape.md（真实请求形状的事实记录）。
BACKENDS = ("api", "browser")
VERIFY_BACKENDS = ("auto", "api", "browser")

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
    """追加一条发布记录到运行时账本。

    ADR-0074：账本分两份——运行时（`.newsagg/`，不入库，当前环境的第一手
    事实）与归档（`reports/`，入库，跨环境的合并视图）。这里只写运行时；
    归档由 `sync_archive` 合并写入。

    每条记录都带 `record_id`，跨环境合并靠它去重。
    """
    ledger_mod.make_record_id(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_ledger(path: Path) -> list[dict]:
    """读全部发布账本记录（容忍坏行）。"""
    return ledger_mod.read_records(path)


def repo_root_of(ledger_path: Path) -> Path:
    """从账本路径反推仓库根。

    账本路径固定是 `<root>/.newsagg/publish-ledger.jsonl`，所以上溯两级就是根。
    之所以不直接用 `repo_root()`：这个函数是 publish.py 的既有风格（`root`
    由 main() 传进来），从路径反推能保证「算的就是这个账本所在的那棵树的
    归档」。
    """
    return Path(ledger_path).resolve().parent.parent.parent


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
    for rec in ledger_mod.read_merged(repo_root_of(ledger)):
        if not str(rec.get("at", "")).startswith(date):
            continue
        if rec.get("accidental"):
            continue
        verdict = str(rec.get("verdict") or "")
        if verdict in PUBLISHED_VERDICTS:
            today.append(rec)
    return today


# ── 后端适配 ────────────────────────────────────────────────────────────
# 2026-10-25 起，登录预检与反查可以走纯 HTTP（api 后端），不再需要浏览器。
# 门序、verdict 取值、账本字段都保持原样 —— 这里只做「同一个动作换实现」。


def _load_api_module():
    """懒加载 toutiao_api。browser 后端跑得好好的时候不该因为 import 失败而崩。"""
    try:
        import toutiao_api  # noqa: F401  （同目录，_SCRIPT_DIR 已在 sys.path 里）
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"api 后端不可用：导入 toutiao_api 失败（{exc}）") from exc
    return toutiao_api


def _api_client(account: str):
    """建一个 api 客户端。

    ## 关于 `--account`

    浏览器后端（toutiao-ops）用 `--account` 在 `.home` 下选一个 profile；api 后端
    没有这个概念 —— 它用的是从本机浏览器里读出来的那一份 cookie，也就是**当前
    登录的那个号**。两者不一致时不该装作一致，所以：`--account` 不是 default 时
    打一条警告，让「我以为在发小号」这种事不可能悄悄发生。
    """
    if account and account != "default":
        print(
            f"⚠️ api 后端不支持 --account={account}：它用的是浏览器里当前登录的账号。"
            f"要按 profile 发请用 --backend browser。",
            file=sys.stderr,
        )
    mod = _load_api_module()
    return mod, mod.Client.from_browser()


def _login_probe(backend: str, account: str, env: dict) -> tuple[bool, str, int]:
    """一次登录预检。返回 `(ok, kind, code)`，`kind` 复用 classify_failure 的词。"""
    if backend == "browser":
        code, out, err = run_toutiao(["auth", "check", "--account", account],
                                     env, timeout=180)
        # 注意：这个子串判据是 toutiao-ops 的输出格式，不能改（ADR-0030）
        return (code == 0 and '"logged_in": true' in out,
                classify_failure(out + err), code)

    # 这里不直接 import browserless：CookieError 的类型由 toutiao_api 那一侧
    # 定义（它自己负责把 .agents/lib 注入 sys.path）。从它的模块对象上取类型，
    # publish.py 就不必重复做一次 sys.path 拼装，也不会出现「两处 Inject 顺序
    # 不同、拿到两个不同的 CookieError 类导致 isinstance 失效」的坑。
    try:
        mod, client = _api_client(account)
        st = client.login_status()
    except Exception as exc:  # noqa: BLE001
        cookie_error = getattr(_load_api_module(), "CookieError", ())
        if cookie_error and isinstance(exc, cookie_error):
            # 读不到 cookie = 没登录过 / cookie 库锁着。处置与「登录失效」相同：
            # 去浏览器扫码，不是重试。
            return False, "login_lost", 1
        # 网络抖动 → 归 unknown 让上层重试；平台直接说没登录 → login_lost。
        return False, classify_failure(str(exc)), 1
    if st.get("logged_in"):
        return True, "", 0
    return False, "login_lost", 1


def _markdown_to_html(text: str) -> str:
    """把成稿 markdown 粗转成头条编辑器吃的 HTML。

    ## 这是 api 写路径的第三块未验证拼图

    浏览器后端是把 markdown **粘贴进编辑器**，由编辑器自己转 HTML、自己加内联
    样式。api 后端没有编辑器，必须自己产 HTML —— 而头条正文的真实 HTML 形态
    （段落 class、图片 img 的 data 属性、行内样式白名单）我**没有线上样本**，
    所以这里只做最保守的结构转换：标题/段落/引用/列表/粗体/链接。

    换句话说：这段代码产出的 HTML **能拼出合法请求，但不能保证排版与浏览器
    后端一致**。这也是 api 写路径默认被挡住的原因之一。
    """
    out: list[str] = []
    in_list = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{_inline(line[4:].strip())}</h3>")
        elif line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{_inline(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            # 一级标题是文章标题，已经在 title 字段里，正文里不该重复出现
            continue
        elif line.startswith("> "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<blockquote>{_inline(line[2:].strip())}</blockquote>")
        elif line.lstrip().startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line.lstrip()[2:].strip())}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{_inline(line.strip())}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _inline(s: str) -> str:
    """行内格式：`**粗**` → `<strong>`，`[文字](url)` → `<a>`。"""
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    return s


def _api_write(*, client, mode: str, title: str, body: str, cover_path: Path,
               declaration: str, allow_unverified_write: bool) -> tuple[int, dict]:
    """用 api 后端执行发布/存草稿。返回 `(code, info)`。

    ## 两道闸

    `save_draft()` / `publish_article()` 自己会抛 `UnverifiedWrite`；这里再要求
    命令行显式给 `--allow-unverified-write`。一个人为疏忽不该能把没核对过的稿子
    发到线上 —— 发布不可逆，平台还禁止重复内容（ADR-0047）。

    ## 为什么上传封面也算「不可逆」

    上传会在账号的图片库里**新增一张图**（我无权删）。所以它同样归在
    `allow_unverified_write` 之内，不单独放行。
    """
    mod = _load_api_module()
    info: dict = {}
    if not allow_unverified_write:
        raise mod.UnverifiedWrite(
            "api 后端的写路径尚未验证：2026-10-27 抓包实测发现字段名几乎全错"
            "（35 键里只有 9 个对得上），且写路径有平台自己的签名与风控闸"
            "（_signature / msToken / a_bogus / CSRF），纯 HTTP 客户端不处理。"
            "真发请用 --backend browser；确认要试 api 写，加 --allow-unverified-write。"
        )
    img = client.upload_image(str(cover_path), allow_unverified=True)
    info["cover_uri"] = img.get("uri", "")
    info["cover_url"] = img.get("cover_url", "")
    form_kwargs = {
        "title": title,
        "content_html": _markdown_to_html(body),
        "cover_uri": img.get("uri", ""),
        "cover_url": img.get("cover_url", ""),
        "declaration": declaration,
    }
    if mode == "dry-run":
        res = client.save_draft(allow_unverified=True, **form_kwargs)
    else:
        res = client.publish_article(allow_unverified=True, **form_kwargs)
    info["pgc_id"] = res.get("pgc_id", "")
    return 0, info


def _api_verify(account: str, limit: int) -> tuple[int, list[dict], str]:
    """api 后端反查：拉作品列表。返回 `(rc, articles, err)`。

    ## 为什么这里也套一层 (rc, ...) 而不是直接抛

    调用点要和 browser 后端共用同一段判据代码（`if rc == 0: ...` 那一坨）。
    把「取数」与「判据」分开，才能保证两条路径喂进去的是同一个判据函数，
    而不是各写一份 —— 各写一份就是 ADR-0047/0051/0053 那三个坑重演的地方。

    ## 失败不抛、返回 rc != 0

    与 `run_toutiao` 的契约一致：调用方已经写好 `rc != 0` 的处理
    （`verify_result = title_not_found`）。但注意 api 后端下 `rc != 0` 时
    `verify_kinds` 不会被写入 —— 账本会缺这个字段。这是有意的：宁可缺字段，
    也不要写一个「0 条微头条」的假事实（api 数据源本来就看不清微头条）。
    """
    try:
        mod, client = _api_client(account)
        articles = client.list_articles(limit=limit)
        # 防御：结构变了要当失败，不能当「查不到」——后者会诱使人重发（ADR-0011）
        if not isinstance(articles, list):
            return 1, [], f"作品列表返回值不是 list：{type(articles).__name__}"
        return 0, articles, ""
    except Exception as exc:  # noqa: BLE001
        return 1, [], f"{type(exc).__name__}: {exc}"


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
    # ── 后端选择（2026-10-25 去浏览器化）──
    # 默认 api：登录预检与反查都走纯 HTTP，不需要浏览器，反查快一个数量级
    # （实测 node 25.8s → api 0.93s）。写路径（发布/存草稿）仍然默认 browser，
    # 见 _api_write 的两道闸与 SKILL.md 的「api 后端尚未验证的部分」。
    # 默认后端可用环境变量覆盖（2026-10-28，Windows 本机）：
    # api 后端经 `Client.from_browser()` 读本机 Chrome 的 cookie，而本机 Chrome
    # 被占用时读不到、且 Playwright 持久化 profile 在这台机器上不落盘，
    # 于是登录预检必然 login_lost。browser 后端可以用 NEWSAGG_COOKIE_FILE
    # 注入的那份 cookie（reference/toutiao-ops/cli/src/browser.js 的补丁）。
    # 服务器不设这个变量，行为与原来一致。
    ap.add_argument("--backend", choices=list(BACKENDS),
                    default=os.environ.get("NEWSAGG_PUBLISH_BACKEND", "api"),
                    help="api：登录预检+反查走纯 HTTP，写操作需 --allow-unverified-write；"
                         "browser：全部走 vendored toutiao-ops（Playwright）。"
                         "默认 api，可用 NEWSAGG_PUBLISH_BACKEND 覆盖")
    ap.add_argument("--allow-unverified-write", action="store_true",
                    help="允许 api 后端执行写操作（发布/存草稿/上传封面）。"
                         "⚠ 写路径的字段名与平台签名闸都未对齐（ADR-0073），"
                         "且发布不可逆——只在人工强确认时使用，"
                         "不得进入无人值守链路")
    # 反查后端单独可切，是为了不把 ADR-0047 的微头条泄漏检测弄丢：
    # api 的数据源只含图文，看不到微头条。默认 auto 跟随写入后端 ——
    # 写入走 browser（默认情况）时反查也走 browser，安全判据一点不减；
    # 想享受 api 反查的 0.9s 就显式说 --verify-backend api，账本会记下
    # verify_weitoutiao_blind 说明这次没查微头条。
    ap.add_argument("--verify-backend", choices=list(VERIFY_BACKENDS), default="auto",
                    help="反查用哪个后端。auto（默认）= 跟随实际写入后端")
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
        # api 后端下这一步是 `GET /mp/agw/media/user_login_status_api`：
        # 实测 0.69s，且**浏览器关着也能跑**（不需要 Playwright/Chromium）。
        login_ok = False
        for attempt in range(1, 4):
            ok, kind, code = _login_probe(args.backend, args.account, env)
            if ok:
                login_ok = True
                steps.append({"step": "auth_check", "backend": args.backend,
                              "attempt": attempt, "ok": True})
                break
            steps.append({"step": "auth_check", "backend": args.backend,
                          "attempt": attempt, "ok": False, "kind": kind, "code": code})
            if kind == "login_lost":
                break
            time.sleep(5)
        if not login_ok:
            breaker.trip("login_lost", {"slug": args.slug, "steps": steps})
            print(json.dumps({
                "ok": False, "stage": "auth", "message": "登录预检失败，已熔断",
                "steps": steps,
                "how_to_fix": ("在浏览器里登录 mp.toutiao.com 后重试（api 后端读浏览器 cookie）；"
                               "或用 --backend browser 走 node index.js auth login 扫码"
                               "（HOME=<repo>/.home）"),
            }, ensure_ascii=False, indent=2))
            return 1

        # ── 发布或 dry-run ──
        out = ""
        err = ""
        api_info: dict = {}
        write_backend = args.backend
        write_fallback = None
        if args.backend == "api" and not args.allow_unverified_write:
            # api 写路径未验证 → 自动回落 browser。
            #
            # 为什么是「回落」而不是「报错」：`--backend api` 的默认值必须让
            # `run_all.sh`（不能改，调用时不给 --backend）继续按原样工作。
            # 登录预检与反查已经是纯 HTTP 了，只剩写这一步还是要浏览器 ——
            # 这正是本次去浏览器化的真实进度，不该用「默认即坏」来表达。
            # 回落在账本里留痕（write_backend / write_fallback_reason），
            # 事后能查清这一篇到底是哪条路发出去的。
            write_backend = "browser"
            write_fallback = (
                "api 写路径未经验证（字段名与平台签名闸都不对齐，ADR-0073），"
                "已回落 browser 后端执行写操作。"
                "确需试 api 写：加 --allow-unverified-write"
            )
        if write_backend == "api":
            # api 写路径。未给 --allow-unverified-write 时不会走到这里（上面已回落），
            # 给了之后仍由 _api_write 自己再挡一道 —— 两道闸是故意的。
            try:
                _mod, client = _api_client(args.account)
                code, api_info = _api_write(
                    client=client, mode=args.mode, title=title, body=body,
                    cover_path=Path(args.cover).resolve(),
                    declaration=args.declaration,
                    allow_unverified_write=args.allow_unverified_write,
                )
                kind = ""
                out = json.dumps({"title": title, "expected_title": title,
                                  **api_info}, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001
                mod = _load_api_module()
                unverified = isinstance(exc, getattr(mod, "UnverifiedWrite", ()))
                code = 2 if unverified else 1
                kind = classify_failure(str(exc))
                err = f"{type(exc).__name__}: {exc}"
                out = json.dumps({"success": False, "backend": "api",
                                  "unverified_write": unverified,
                                  "error": str(exc)}, ensure_ascii=False)
        else:
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
                      "backend": write_backend,
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

        # 反查后端：auto 跟随实际写入后端（不是 args.backend —— 回落之后
        # args.backend 已经不能代表真实执行路径了）。
        verify_backend = args.verify_backend
        if verify_backend == "auto":
            verify_backend = write_backend

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
            # 去浏览器化的进度必须在账本里可查：这一篇到底哪条路写出去的、
            # 有没有发生回落、反查用的是哪个数据源。否则「api 后端上线了」
            # 就只是一句口号，出事时无从复盘。
            "write_backend": write_backend,
            "verify_backend": verify_backend,
        }
        # ADR-0074：给记录一个稳定的唯一键，跨环境合并时靠它去重。
        # 没有它时合并靠 `at|slug|mode` 派生——那在两台机器上一样，但
        # 同一篇被误发补录过就会是两条不同的记录。
        ledger_mod.make_record_id(record)
        if write_fallback:
            record["write_fallback_reason"] = write_fallback
        if api_info:
            record["api_write"] = api_info

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
            #
            # 两个后端的差别只在「怎么把列表拿回来」，判据（count_title_kinds /
            # find_gid）一个字符都没改：api 后端把归一化结果喂给
            # toutiao_api.as_verify_payload() 伪装成 node 的形状，再交给同一套函数。
            record["verify_backend"] = verify_backend
            time.sleep(8)  # 给平台一点时间
            found = None
            parse_failed = False
            payload: dict = {}
            api_articles: list[dict] = []
            cout = ""
            cerr = ""
            if verify_backend == "api":
                # 实测 0.93s（node 同一动作 25.81s）
                rc, api_articles, verr = _api_verify(args.account, 20)
                if rc == 0:
                    payload = _load_api_module().as_verify_payload(api_articles)
                else:
                    cerr = verr
                    record["verify_error"] = verr
            else:
                rc, cout, cerr = run_toutiao(["content", "list", "--limit", "20",
                                              "--account", args.account], env, timeout=240)
            record["verify_code"] = rc
            if rc == 0:
                try:
                    if verify_backend == "api":
                        # 精确匹配标题（api 那边标题是干净字段，不需要子串搜索）
                        want = title_on_platform.strip()
                        hit = next((a for a in api_articles if a["title"] == want), None)
                        if hit is not None:
                            found = "title_found"
                            suff = _load_api_module().status_suffix(hit)
                            if suff:
                                found = f"title_found:{suff}"
                        # ADR-0047 的微头条计数在 api 后端**看不见**：这个数据源只有
                        # 图文（genre=Pgc）。所以 weitoutiao 恒 0 不代表没泄漏。
                        record["verify_weitoutiao_blind"] = (
                            "api 数据源 /mp/agw/article/list 不含微头条，"
                            "verify_kinds.weitoutiao 不可作为 ADR-0047 泄漏判据；"
                            "需要该判据时用 --backend browser"
                        )
                    else:
                        # 解析放在 try 里：ADR-0045 的 stdout 截断会在这里炸，
                        # 必须走 JSONDecodeError → unknown_needs_review 那条路。
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
                    record["verify_parse_error"] = f"{exc}；原始输出前 200 字：{str(cout)[:200]}"
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
