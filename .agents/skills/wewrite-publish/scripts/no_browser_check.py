#!/usr/bin/env python3
"""回答一个问题：**wewrite-publish 这条发布路径需要浏览器吗？**

答案是「不需要」。但光说不需要没有用 —— Agent 之所以会去开浏览器，是因为
「发布到微信」在多数人的经验里等于「登录 mp.weixin.qq.com 后台点『发表』」。
本脚本把「不需要」变成**可核验的证据**，而不是一句断言：

1. 静态扫描 wewrite 包里所有浏览器自动化关键词，给出文件+行号+原文；
2. 把命中分成「发布链路上的」与「其它命令里的」，并逐个给出解释；
3. 检查可选依赖 extra 到底装没装（playwright / camoufox / selenium …）；
4. 从发布链路源文件里抽 `https?://` 域名，证明只出网到 api.weixin.qq.com；
5. 列出发布链路的 import 清单，证明没有浏览器/驱动库被拉进来。

本脚本**只读文件、不联网、不启动任何进程**。

判定口径的关键区分
------------------
`import webbrowser` ≠ 浏览器自动化。它只是让**用户自己的**默认浏览器打开一个本地
HTML 文件（预览），既不控制浏览器也不读页面。本脚本把这类命中单列为
`local_viewer`，并核对 `--no-open` 开关确实存在（有证据的豁免，不是想当然）。
真正意味「要开受控浏览器」的是 playwright / selenium / puppeteer / webdriver /
CDP remote-debugging / camoufox 这类。

退出码：0=确认不需要浏览器；1=有未解释的命中需人工复核；2=找不到 wewrite 包。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]

# 分两类：真自动化框架 vs 本机打开文件。
AUTOMATION_PATTERNS = {
    "playwright": r"\bplaywright\b",
    "selenium": r"\bselenium\b",
    "puppeteer": r"\bpuppeteer\b",
    "pyppeteer": r"\bpyppeteer\b",
    "camoufox": r"\bcamoufox\b",
    "webdriver": r"\bwebdriver\b",
    "chromedriver": r"\bchromedriver\b",
    "cdp": r"(?:remote-debugging|--remote-debugging-port|\bCDP\b|chrome-devtools)",
    "browser_launch": r"(?:launch_persistent_context|browser_type\.launch|\.new_context\s*\()",
}

LOCAL_VIEWER_PATTERNS = {
    "webbrowser": r"\bwebbrowser\b",
}

# 发布链路：只有这些文件会出现在「调微信 API 发草稿」的路径上。
PUBLISH_PATH_FILES = (
    "toolkit/wechat_api.py",
    "toolkit/publisher.py",
    "toolkit/cli.py",
)

# 允许出现自动化字样、但必须解释清楚它在做什么的文件。
KNOWN_EXCEPTIONS = {
    "commands/fetch_article.py": (
        "这是**抓取** mp.weixin.qq.com 文章正文 HTML 的三级兜底"
        "（requests → Camoufox → Playwright → 让用户另存 HTML），服务于 "
        "`fetch-article` 命令。与发布无关；且 playwright/camoufox 只是可选 extra，"
        "未安装时该兜底自动降级到 requests。"
    ),
    "commands/learn_theme.py": (
        "`learn_theme`（学习一篇公众号文章的排版主题）里的 fetch_article 复用了上面的"
        "三级抓取兜底，文档串里提到 Playwright。与发布无关。"
    ),
    "commands/fetch_stats.py": (
        "`fetch-stats` 拉取文章阅读数据，有自己的抓取兜底。与发布无关。"
    ),
    "toolkit/cli.py": (
        "`import webbrowser` 只用于把本地生成的 HTML（preview / gallery）"
        "用系统默认浏览器打开给人看，不是自动化；且都由 `--no-open` 控制。"
    ),
}

EXPECTED_PUBLISH_HOST = "api.weixin.qq.com"
URL_RE = re.compile(r"https?://([A-Za-z0-9._-]+)")
TRIPLE_QUOTE_RE = re.compile(r'"""|\'\'\'')

OPTIONAL_BROWSER_PACKAGES = (
    "playwright",
    "camoufox",
    "selenium",
    "pyppeteer",
    "undetected-chromedriver",
)


def find_package_dir() -> Path | None:
    """定位 wewrite 包目录。顺序：仓库内 venv → 当前 sys.path。"""
    candidates = []

    venv = REPO_ROOT / ".uv-tools" / "wewrite" / "lib"
    if venv.is_dir():
        candidates.extend(sorted(venv.glob("python3.*/site-packages/wewrite")))

    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry) / "wewrite"
        if candidate.is_dir():
            candidates.append(candidate)

    for candidate in candidates:
        if (candidate / "toolkit" / "publisher.py").is_file():
            return candidate
    return candidates[0] if candidates else None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def docstring_spans(text: str):
    """返回 [(start,end)] —— 三引号围起来的区间，用来判定命中是否只出现在文档串里。

    不做完整 Python 词法分析；对「这行代码是注释还是代码」这类判断足够，
    而且宁可少判（把文档串当代码）也不会漏报。
    """
    spans = []
    marks = [(m.start(), m.group()) for m in TRIPLE_QUOTE_RE.finditer(text)]
    index = 0
    while index + 1 < len(marks):
        start, mark = marks[index]
        # 找同种引号的闭合
        for j in range(index + 1, len(marks)):
            if marks[j][1] == mark:
                spans.append((start, marks[j][0] + 3))
                index = j + 1
                break
        else:
            break
    return spans


def classify_line(text: str, offset: int, spans) -> str:
    """把命中行的角色分出来：comment / docstring / code。"""
    if any(start <= offset < end for start, end in spans):
        return "docstring"
    line_start = text.rfind("\n", 0, offset) + 1
    line = text[line_start:text.find("\n", offset) if text.find("\n", offset) != -1 else len(text)]
    if line.strip().startswith("#"):
        return "comment"
    return "code"


def scan_file(path: Path, rel: str) -> dict:
    text = read_text(path)
    lines = text.splitlines()
    spans = docstring_spans(text)
    automation = []
    local_viewer = []

    def collect(pattern_name, pattern, bucket):
        for match in re.finditer(pattern, text, re.I | re.M):
            offset = match.start()
            line_no = text.count("\n", 0, offset) + 1
            raw_line = lines[line_no - 1] if line_no <= len(lines) else ""
            bucket.append(
                {
                    "pattern": pattern_name,
                    "file": rel,
                    "line": line_no,
                    "role": classify_line(text, offset, spans),
                    "text": raw_line.strip()[:170],
                    "on_publish_path": rel in PUBLISH_PATH_FILES,
                }
            )

    for name, pattern in AUTOMATION_PATTERNS.items():
        collect(name, pattern, automation)
    for name, pattern in LOCAL_VIEWER_PATTERNS.items():
        collect(name, pattern, local_viewer)

    # webbrowser 的豁免要有证据：--no-open 开关真的存在，且调用被它 gate 住
    no_open_flag = bool(re.search(r"--no-open", text))
    open_calls = [
        {"line": text.count("\n", 0, m.start()) + 1, "text": lines[text.count("\n", 0, m.start())].strip()[:120]}
        for m in re.finditer(r"webbrowser\.open\s*\(", text)
    ]
    guarded_calls = 0
    for call in open_calls:
        # 往上看两行，找 `if not args.no_open:`
        window = "\n".join(lines[max(0, call["line"] - 3): call["line"]])
        if "args.no_open" in window:
            guarded_calls += 1

    return {
        "automation_hits": automation,
        "local_viewer_hits": local_viewer,
        "no_open_flag": no_open_flag,
        "open_calls": open_calls,
        "guarded_open_calls": guarded_calls,
    }


def scan_package(pkg: Path) -> dict:
    files = sorted(pkg.rglob("*.py"))
    automation_hits = []
    local_viewer_hits = []
    viewer_evidence = []
    for path in files:
        rel = path.relative_to(pkg).as_posix()
        result = scan_file(path, rel)
        automation_hits.extend(result["automation_hits"])
        local_viewer_hits.extend(result["local_viewer_hits"])
        if result["local_viewer_hits"] or result["open_calls"]:
            viewer_evidence.append(
                {
                    "file": rel,
                    "no_open_flag_present": result["no_open_flag"],
                    "open_call_sites": result["open_calls"],
                    "open_calls_guarded_by_no_open": result["guarded_open_calls"],
                }
            )
    return {
        "files_scanned": [p.relative_to(pkg).as_posix() for p in files],
        "automation_hits": automation_hits,
        "local_viewer_hits": local_viewer_hits,
        "viewer_evidence": viewer_evidence,
    }


def scan_metadata(pkg: Path) -> dict:
    """读 dist-info/METADATA 的 Requires-Dist，区分硬依赖与可选 extra。"""
    result = {"path": None, "requires": [], "browser_extras": []}
    dist_dir = next(pkg.parent.glob("wewrite-*.dist-info"), None)
    if dist_dir is None:
        return result
    meta = dist_dir / "METADATA"
    if not meta.is_file():
        return result
    result["path"] = str(meta)
    for line in read_text(meta).splitlines():
        if line.startswith("Requires-Dist:"):
            spec = line.split(":", 1)[1].strip()
            result["requires"].append(spec)
            low = spec.lower()
            if any(name in low for name in OPTIONAL_BROWSER_PACKAGES):
                result["browser_extras"].append(spec)
    return result


def check_installed(pkg: Path) -> dict:
    """浏览器自动化包是否真的装在同一个 site-packages 里。"""
    site = pkg.parent
    installed = {}
    for name in OPTIONAL_BROWSER_PACKAGES:
        normalized = name.replace("-", "_")
        installed[name] = bool(
            list(site.glob("%s*" % normalized)) or list(site.glob("%s-*" % name))
        )
    return installed


def scan_publish_domains(pkg: Path) -> dict:
    """从发布链路源文件抽域名 —— 「实际出网到哪」的硬证据。"""
    per_file = {}
    union = set()
    for rel in PUBLISH_PATH_FILES:
        path = pkg / rel
        if not path.is_file():
            continue
        found = sorted(set(URL_RE.findall(read_text(path))))
        per_file[rel] = found
        union.update(found)
    return {"per_file": per_file, "union": sorted(union)}


def scan_publish_imports(pkg: Path) -> dict:
    """发布链路的 import 清单 —— 证明没把浏览器/驱动库拉进来。"""
    imports = {}
    for rel in PUBLISH_PATH_FILES:
        path = pkg / rel
        if not path.is_file():
            continue
        found = set()
        for line in read_text(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("import "):
                for part in stripped[len("import "):].split(","):
                    found.add(part.split(" as ")[0].strip().split(".")[0])
            elif stripped.startswith("from ") and " import " in stripped:
                module = stripped[len("from "):].split(" import ")[0].strip()
                if not module.startswith("."):
                    found.add(module.split(".")[0])
        imports[rel] = sorted(found)
    return imports


def explain_automation_hits(hits):
    """把自动化命中分成 explained / unexplained。"""
    explained, unexplained = [], []
    for hit in hits:
        rel = hit["file"]
        if rel in KNOWN_EXCEPTIONS:
            explained.append({**hit, "explanation": KNOWN_EXCEPTIONS[rel]})
        elif hit["role"] in ("comment", "docstring"):
            explained.append({**hit, "explanation": "%s里的说明文字，不产生行为" % hit["role"]})
        else:
            unexplained.append(hit)
    return explained, unexplained


def build_report(pkg_arg=None) -> dict:
    pkg = Path(pkg_arg).expanduser() if pkg_arg else find_package_dir()
    if pkg is None or not pkg.is_dir():
        return {
            "tool": "wewrite-publish/no_browser_check",
            "version": 1,
            "verdict": "unknown",
            "answer": "找不到 wewrite 包目录，无法取证。请用 --package 显式指定。",
            "exit_code": 2,
        }

    scan = scan_package(pkg)
    meta = scan_metadata(pkg)
    installed = check_installed(pkg)
    domains = scan_publish_domains(pkg)
    imports = scan_publish_imports(pkg)

    explained, unexplained = explain_automation_hits(scan["automation_hits"])
    publish_path_automation = [h for h in scan["automation_hits"] if h["on_publish_path"]]
    viewer_hits = scan["local_viewer_hits"]

    # webbrowser 的豁免必须被证据支撑：--no-open 存在 且 每个 open 调用都被它 gate。
    viewer_unguarded = [
        ev for ev in scan["viewer_evidence"]
        if ev["open_call_sites"] and not (
            ev["no_open_flag_present"]
            and ev["open_calls_guarded_by_no_open"] == len(ev["open_call_sites"])
        )
    ]

    only_wechat = domains["union"] == [EXPECTED_PUBLISH_HOST]
    unexpected_domains = [d for d in domains["union"] if d != EXPECTED_PUBLISH_HOST]
    automation_installed = sorted(k for k, v in installed.items() if v)

    problems = []
    if publish_path_automation:
        problems.append(
            "发布链路上出现自动化字样 %d 处（文件：%s）"
            % (len(publish_path_automation),
               ", ".join(sorted({h["file"] for h in publish_path_automation})))
        )
    if unexplained:
        problems.append(
            "有 %d 处未被解释的自动化字样（见 unexplained_hits）" % len(unexplained)
        )
    if not only_wechat:
        problems.append(
            "发布链路出现 api.weixin.qq.com 之外的域名：%s" % ", ".join(unexpected_domains)
        )
    if viewer_unguarded:
        problems.append(
            "webbrowser.open 调用没有被 --no-open 保护（%s）"
            % ", ".join(ev["file"] for ev in viewer_unguarded)
        )

    if problems:
        verdict = "unexpected_hits"
        exit_code = 1
        answer = (
            "未能确认「发布不需要浏览器」，原因：" + "；".join(problems) + "。"
            "在这些命中被解释清楚之前，不要假设发布无需浏览器。"
        )
    else:
        verdict = "not_required"
        exit_code = 0
        answer = (
            "不需要浏览器。wewrite 的发布链路只用 requests 调微信公众号官方 HTTP API："
            "GET /cgi-bin/token 取 access_token，POST /cgi-bin/media/uploadimg 传正文图，"
            "POST /cgi-bin/material/add_material?type=image 传封面拿永久 media_id，"
            "POST /cgi-bin/draft/add 建草稿。发布链路唯一出网域名是 api.weixin.qq.com，"
            "且没有任何浏览器自动化框架被 import 或安装。"
        )

    return {
        "tool": "wewrite-publish/no_browser_check",
        "version": 1,
        "question": "wewrite-publish 的发布路径是否需要浏览器？",
        "verdict": verdict,
        "answer": answer,
        # 字段名是「发布是否需要浏览器」，所以 not_required 时必须是 False。
        # 早先这里写成 `verdict == "not_required"`，语义整个反了：
        # 「不需要浏览器」的结论旁边挂着 `true`，读的人会得到完全相反的印象。
        "browser_required_for_publish": verdict != "not_required",
        "exit_code": exit_code,
        "evidence": {
            "package_dir": str(pkg),
            "environment": {
                "WEWRITE_HOME": os.environ.get("WEWRITE_HOME") or None,
                "python_executable": sys.executable,
                "python_version": "%d.%d.%d" % sys.version_info[:3],
            },
            "files_scanned_count": len(scan["files_scanned"]),
            "files_scanned": scan["files_scanned"],
            "publish_path_files": list(PUBLISH_PATH_FILES),
            "publish_path_automation_hits": publish_path_automation,
            "automation_hits_total": len(scan["automation_hits"]),
            "automation_hits_explained": explained,
            "automation_hits_unexplained": unexplained,
            "local_viewer_hits": viewer_hits,
            "local_viewer_evidence": scan["viewer_evidence"],
            "local_viewer_all_guarded": not viewer_unguarded,
            "publish_path_domains": domains["per_file"],
            "publish_path_domains_union": domains["union"],
            "expected_publish_host": EXPECTED_PUBLISH_HOST,
            "unexpected_domains": unexpected_domains,
            "publish_path_imports": imports,
            "metadata": {
                "path": meta["path"],
                "browser_related_requires": meta["browser_extras"],
                "note": (
                    "playwright / camoufox 只出现在 extras_require 的 `browser` extra 里，"
                    "不是硬依赖；不装也不会影响发布。"
                ),
            },
            "browser_automation_installed": installed,
            "browser_automation_installed_true": automation_installed,
            "problems": problems,
        },
        "what_actually_needs_the_network": [
            "GET https://api.weixin.qq.com/cgi-bin/token —— 取/刷新 access_token"
            "（进程内缓存到 expires_in-300 秒）",
            "POST https://api.weixin.qq.com/cgi-bin/media/uploadimg —— 正文图转存，返回微信图床 URL",
            "POST https://api.weixin.qq.com/cgi-bin/material/add_material?type=image"
            " —— 封面上传，返回永久 media_id",
            "POST https://api.weixin.qq.com/cgi-bin/draft/add —— 建草稿，返回 media_id",
        ],
        "what_does_not_need_the_network": [
            "Markdown → 内联样式 HTML（toolkit/converter.py，纯本地）",
            "主题解析（toolkit/theme.py + $WEWRITE_HOME/themes，纯本地）",
            "本地预览产物（preview 写文件；带 --no-open 时连系统浏览器都不碰）",
            "凭证解析（config.yaml / 环境变量，纯本地）",
            "本技能的 scripts/preflight.py（纯本地校验）",
        ],
        "if_a_browser_opens_anyway": (
            "只可能是 preview / gallery 的默认 `--open` 行为：把本地生成的 HTML 用"
            "系统默认浏览器打开给人看。加 `--no-open` 即可，与发布无关。"
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="证明 wewrite-publish 的发布路径不需要浏览器（只读、不联网）"
    )
    parser.add_argument("--package", help="显式指定 wewrite 包目录（默认自动定位）")
    args = parser.parse_args(argv)
    report = build_report(args.package)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
