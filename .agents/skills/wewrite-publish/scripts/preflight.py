#!/usr/bin/env python3
"""wewrite-publish 的发布前预检 —— **不需要浏览器**。

为什么有这个脚本
----------------
`wewrite publish` 走的是微信公众号官方 HTTP API（`POST /cgi-bin/draft/add`），
不是浏览器自动化。真正让发布失败的事情几乎都不发生在 API 调用那一刻，而是发生在
此前：封面用了微信不支持的格式、正文图超过 uploadimg 的 1MB 上限、摘要按「字」数
没超但按 **UTF-8 字节** 算超了、表格 5 列在手机端被挤成看不清。这些一旦提交就只能
靠人去草稿箱里翻，所以要在调用 API **之前** 用确定性代码卡住。

本脚本只做校验与说明，**不发送任何网络请求、不调微信 API、不写 wewrite 状态**
（唯一例外是读文件）。发布逻辑仍然是 wewrite 的职责，重复实现会分叉。

设计约束
--------
* 只用标准库。`yaml` 存在就用（本机有 PyYAML），否则退回内置迷你解析器 ——
  本脚本要在系统 python3（3.9）下直接跑，不能要求 pip install。
* **绝不输出 secret**，appid 也只输出前 6 位加掩码。凭证中心只回答
  「configured: true/false」。
* 退出码：0=全部通过，1=有警告但可以继续，2=硬失败（不要发布）。
* 图片尺寸优先用标准库解析 PNG/JPEG/GIF/BMP 头；解析不了就跳过该检查，
  **不硬依赖 Pillow**。

用法
----
    python3 scripts/preflight.py --article <article.md> [--cover <cover.png>]
    python3 scripts/preflight.py --article <article.md> --cover <c.png> --publish
    python3 scripts/preflight.py --article <article.md> --digest "..." --author "..."

`--publish` 表示「这一轮真的要推草稿箱」，此时凭证与封面从「警告」升级为「硬失败」，
并且会读任务状态核对 `permissions.publish` / `flags.skip_publish`。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
try:
    REPO_ROOT = Path(__file__).resolve().parents[4]
except IndexError:  # pragma: no cover - 目录层级异常时退化
    REPO_ROOT = SKILL_ROOT

PASS = "pass"
WARN = "warn"
FAIL = "fail"

EXIT_OK, EXIT_WARN, EXIT_FAIL = 0, 1, 2

# 每一项都注明出处。数字不许「感觉差不多」——
# 微信的限值和 wewrite 的限值并不完全一样，混用会得出错的结论。
LIMITS = {
    # 官方 draft/add：title「总长度不超过32个字」、author「不超过16个字」
    "title_max_chars": 32,
    "author_max_chars": 16,
    # SKILL.md 预检写「摘要不超过 120 字节」；官方写「120个字」；
    # toolkit/converter.py:_generate_digest 按 UTF-8 字节截断（max_bytes=120）。
    # 三者取最严的字节口径。
    "digest_max_bytes": 120,
    # SKILL.md 预检：正文 200-20000 字
    "body_min_chars": 200,
    # 官方 draft/add：content「必须少于2万字符，小于1M」
    "body_max_chars": 20000,
    "body_max_bytes": 1024 * 1024,
    # 官方 draft/add 与 wechat-constraints.md：最多 10 张图片（视频算 1 张）
    "images_max": 10,
    # wechat-constraints.md 2.3：表格建议列数 ≤4
    "table_cols_max": 4,
    # 官方 add_material：图片（image）10M，支持 bmp/png/jpeg/jpg/gif
    "cover_max_bytes": 10 * 1024 * 1024,
    # 官方 add_material 注意事项：uploadimg 只支持 jpg/png 且 1MB 以下
    "body_image_max_bytes": 1024 * 1024,
    # draft/add cover_info.crop_percent_list.ratio（news）只支持这两个
    "cover_ratios": [[2.35, 1.0], [1.0, 1.0]],
    # 封面过小会糊；微信没有硬下限，这里用经验值给出警告而不是失败
    "cover_min_side": 300,
}

COVER_FORMATS = ("png", "jpeg", "gif", "bmp")          # add_material 支持
BODY_IMAGE_FORMATS = ("png", "jpeg")                   # uploadimg 只支持 jpg/png

# WECHAT_* / WEWRITE_* 这些环境变量会覆盖 config.yaml。
# config.py 里的判断是 `os.environ.get(env_var) is None` 才跳过，
# 所以**空字符串也会覆盖**成空值 —— 这是最容易静默踩到的坑，单独报出来。
ENV_OVERRIDE_VARS = (
    "WECHAT_APPID",
    "WECHAT_SECRET",
    "WECHAT_AUTHOR",
    "WEWRITE_HOME",
    "WEWRITE_THEME",
    "WEWRITE_RUN_ID",
)

MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
HTML_IMAGE_RE = re.compile(r"""<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
HTML_TABLE_RE = re.compile(r"<table\b", re.I)
HTML_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
HTML_CELL_RE = re.compile(r"<t[dh]\b", re.I)


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
class Report:
    """收集检查项，最后一起算 verdict。"""

    def __init__(self) -> None:
        self.checks = []

    def add(self, name: str, status: str, detail: str, **extra) -> None:
        item = {"check": name, "status": status, "detail": detail}
        if extra:
            item.update(extra)
        self.checks.append(item)

    def counts(self):
        c = {PASS: 0, WARN: 0, FAIL: 0}
        for item in self.checks:
            c[item["status"]] = c.get(item["status"], 0) + 1
        return c

    def verdict(self) -> str:
        c = self.counts()
        if c.get(FAIL):
            return FAIL
        if c.get(WARN):
            return WARN
        return PASS

    def exit_code(self) -> int:
        return {PASS: EXIT_OK, WARN: EXIT_WARN, FAIL: EXIT_FAIL}[self.verdict()]


# --------------------------------------------------------------------------
# YAML：优先 PyYAML，缺失时用内置迷你解析器（只覆盖 config.yaml 的形态）
# --------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    """去掉行尾注释，但别把引号里的 # 当注释。"""
    out = []
    quote = None
    for idx, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            out.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (idx == 0 or line[idx - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _unquote(value: str):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _mini_yaml(text: str) -> dict:
    """够用就好的 YAML 子集：只解析两层 `key:` / `  key: value`。

    不追求完备 —— 只为在没装 PyYAML 的机器上仍能读出 wechat.appid 这类标量。
    遇到列表/多行块一律跳过，绝不猜。
    """
    data = {}
    current = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = _unquote(key)
        value = value.strip()
        if indent == 0:
            if value:
                data[key] = _unquote(value)
                current = None
            else:
                data.setdefault(key, {})
                current = key
        elif indent > 0 and current and isinstance(data.get(current), dict) and value:
            data[current][key] = _unquote(value)
    return data


def load_yaml_file(path: Path):
    """返回 (dict, parser_name, error_or_None)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, None, "read failed: %s" % exc
    try:
        import yaml  # noqa: WPS433 - 可选依赖，故意延迟导入
    except ImportError:
        return _mini_yaml(text), "builtin-mini", None
    try:
        parsed = yaml.safe_load(text)
    except Exception as exc:  # PyYAML 对坏 YAML 抛 yaml.YAMLError
        try:
            return _mini_yaml(text), "builtin-mini", "pyyaml parse failed: %s" % exc
        except Exception:
            return {}, None, "pyyaml parse failed: %s" % exc
    if parsed is None:
        return {}, "pyyaml", None
    if not isinstance(parsed, dict):
        return {}, "pyyaml", "top-level YAML is not a mapping"
    return parsed, "pyyaml", None


# --------------------------------------------------------------------------
# 状态目录 / 凭证
# --------------------------------------------------------------------------
def resolve_environment() -> dict:
    """按 wewrite 的真实代码路径解析 WEWRITE_HOME 与 config.yaml。

    对齐的两处源码：
      paths.py:17   home() = $WEWRITE_HOME or ~/.wewrite
      config.py:24  CONFIG_SEARCH_ORDER = [home()/config.yaml,
                                           ~/.config/wewrite/config.yaml]
    """
    env_home_raw = os.environ.get("WEWRITE_HOME")
    home_from_env = bool(env_home_raw)

    if home_from_env:
        home = Path(env_home_raw).expanduser()
    else:
        home = Path.home() / ".wewrite"

    home_exists = home.exists()
    home_is_dir = home.is_dir()

    candidates = [
        {"path": str(home / "config.yaml"), "role": "主位置（$WEWRITE_HOME/config.yaml）"},
        {
            "path": str(Path.home() / ".config" / "wewrite" / "config.yaml"),
            "role": "历史兼容位置",
        },
    ]
    effective_cfg = None
    for cand in candidates:
        exists = Path(cand["path"]).is_file()
        cand["exists"] = exists
        if exists and effective_cfg is None:
            effective_cfg = cand["path"]

    # 仓库内 .wewrite 与 $WEWRITE_HOME 是两件事，必须说清楚。
    repo_state_dirs = []
    for label, base in (("repo_root", REPO_ROOT), ("cwd", Path.cwd())):
        candidate = base / ".wewrite"
        if candidate.is_dir():
            style_file = candidate / "style.yaml"
            repo_style_theme = None
            if style_file.is_file():
                repo_style, _p, _e = load_yaml_file(style_file)
                theme = repo_style.get("theme")
                if isinstance(theme, str):
                    repo_style_theme = theme
            repo_state_dirs.append(
                {
                    "via": label,
                    "path": str(candidate),
                    "is_effective_home": _same_path(candidate, home),
                    "has_config_yaml": (candidate / "config.yaml").is_file(),
                    "has_style_yaml": style_file.is_file(),
                    "style_theme": repo_style_theme,
                }
            )

    style_path = home / "style.yaml"
    style_theme = None
    if style_path.is_file():
        style, _parser, _err = load_yaml_file(style_path)
        theme = style.get("theme")
        if isinstance(theme, str):
            style_theme = theme

    env_state = {}
    for var in ENV_OVERRIDE_VARS:
        raw = os.environ.get(var)
        if raw is None:
            env_state[var] = {"set": False}
        else:
            env_state[var] = {
                "set": True,
                "empty": raw == "",
                # 只对路径类变量展示值；凭证类一律只报是否为空
                "value": raw if var in ("WEWRITE_HOME", "WEWRITE_THEME", "WEWRITE_RUN_ID") else None,
            }

    return {
        "wewrite_home": {
            "from_env_override": home_from_env,
            "env_raw": env_home_raw if home_from_env else None,
            "effective": str(home),
            "exists": home_exists,
            "is_dir": home_is_dir,
            "default_when_unset": str(Path.home() / ".wewrite"),
        },
        "config_search": {
            "candidates": candidates,
            "effective": effective_cfg,
            "loaded": effective_cfg is not None,
        },
        "repo_state_dirs": repo_state_dirs,
        "style_yaml": {
            "path": str(style_path),
            "exists": style_path.is_file(),
            "theme": style_theme,
        },
        "env": env_state,
    }


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def resolve_credentials(env_info: dict) -> dict:
    """解析 wechat.appid / secret / author 的**生效值**，但对外只暴露脱敏信息。

    覆盖顺序对齐 toolkit/config.py:43-69：先读文件，再用 env 覆盖
    （env 存在即为准，哪怕是空字符串）。
    """
    cfg_info = env_info["config_search"]
    cfg_path = cfg_info.get("effective")
    cfg = {}
    parser = None
    error = None
    if cfg_path:
        cfg, parser, error = load_yaml_file(Path(cfg_path))

    wechat = cfg.get("wechat") if isinstance(cfg.get("wechat"), dict) else {}

    def effective(key: str, env_var: str):
        file_val = wechat.get(key)
        file_val = "" if file_val is None else str(file_val)
        env_raw = os.environ.get(env_var)
        if env_raw is not None:
            return env_raw, "env", file_val
        return file_val, "config.yaml" if file_val else "unset", file_val

    appid, appid_src, appid_file = effective("appid", "WECHAT_APPID")
    secret, secret_src, secret_file = effective("secret", "WECHAT_SECRET")
    author, author_src, author_file = effective("author", "WECHAT_AUTHOR")

    return {
        "config_file": cfg_path,
        "parser": parser,
        "parse_error": error,
        "appid": {
            "configured": bool(appid.strip()),
            # 只给前 6 位 + 掩码，绝不整串
            "masked": mask_appid(appid),
            "source": appid_src,
            "in_config_file": bool(appid_file.strip()),
        },
        "secret": {
            "configured": bool(secret.strip()),
            "length": len(secret.strip()),
            "source": secret_src,
            "in_config_file": bool(secret_file.strip()),
            # 值永不输出（连长度以外的任何信息都不给）
        },
        "author": {
            "configured": bool(author.strip()),
            "source": author_src,
            "in_config_file": bool(author_file.strip()),
        },
        "_secret_value": secret.strip(),  # 仅供末尾自检用，绝不进输出
    }


def mask_appid(appid: str) -> str:
    appid = (appid or "").strip()
    if not appid:
        return ""
    if len(appid) <= 6:
        return appid[:2] + "*" * max(0, len(appid) - 2)
    return appid[:6] + "****" + "(len=%d)" % len(appid)


# --------------------------------------------------------------------------
# 任务状态（权限门）
# --------------------------------------------------------------------------
def resolve_run_state(env_info: dict, explicit_run) -> dict:
    """定位 runs/<id>/state.yaml 并读出发布权限门。

    对齐 runs.py：显式参数 → $WEWRITE_RUN_ID → current_run 指针文件。
    """
    home = Path(env_info["wewrite_home"]["effective"])
    run_id = None
    source = None

    if explicit_run:
        run_id, source = explicit_run, "arg --run"
    elif os.environ.get("WEWRITE_RUN_ID"):
        run_id, source = os.environ["WEWRITE_RUN_ID"], "env WEWRITE_RUN_ID"
    else:
        pointer = home / "current_run"
        if pointer.is_file():
            try:
                text = pointer.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if text:
                run_id, source = text, "current_run 指针文件"

    if not run_id:
        return {"found": False, "reason": "没有任务状态（任务外文章）"}

    state_path = home / "runs" / run_id / "state.yaml"
    if not state_path.is_file():
        return {
            "found": False,
            "run_id": run_id,
            "resolved_by": source,
            "reason": "state.yaml 不存在：%s" % state_path,
        }

    state, parser, error = load_yaml_file(state_path)
    permissions = state.get("permissions") if isinstance(state.get("permissions"), dict) else {}
    flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
    return {
        "found": True,
        "run_id": run_id,
        "resolved_by": source,
        "path": str(state_path),
        "parser": parser,
        "parse_error": error,
        "publish_permission": permissions.get("publish"),
        "skip_publish": flags.get("skip_publish"),
        "artifacts": state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {},
    }


# --------------------------------------------------------------------------
# 主题是否真的存在
# --------------------------------------------------------------------------
def _wewrite_package_dirs():
    """定位**实际会被 import 的** wewrite 包目录。

    注意区分两件事，混在一起会得出错误的结论：
      * 实际生效的包：解释器真正 `import wewrite` 会加载的那一个。
        wewrite 内置主题是 `Path(__file__).parent / "themes"`，
        所以只有**这个**包的 toolkit/themes 参与搜索。
      * 源码副本：仓库里可能有 `vendor/wewrite/`，但它是安装来源，
        不是运行时加载位置。它有没有主题，**不代表**运行时能不能找到主题。
        把它算进搜索路径会给出假的 PASS —— 这正是我第一版犯的错。

    预检跑在系统 python3（3.9）下，那个解释器没装 wewrite，不能
    `import wewrite`，所以按文件系统找：
      1. 环境变量 WEWRITE_PKG_DIR（显式指定，便于测试）
      2. 仓库内 .uv-tools/*/lib/python*/site-packages/wewrite
      3. 当前解释器 site-packages（万一就是用 venv 跑的）
    vendor/ 只作为「源码副本」单独报告，不参与搜索。
    """
    installed = []
    vendored = []

    explicit = os.environ.get("WEWRITE_PKG_DIR")
    if explicit:
        candidate = Path(explicit).expanduser()
        if (candidate / "toolkit").is_dir():
            installed.append(candidate)

    for pattern in (
        ".uv-tools/*/lib/python*/site-packages/wewrite",
        ".venv/lib/python*/site-packages/wewrite",
    ):
        for candidate in sorted(REPO_ROOT.glob(pattern)):
            if (candidate / "toolkit").is_dir() and candidate not in installed:
                installed.append(candidate)

    try:  # 万一就是用装了 wewrite 的 venv 跑的
        import importlib.util

        spec = importlib.util.find_spec("wewrite")
        if spec and spec.origin:
            candidate = Path(spec.origin).parent
            if (candidate / "toolkit").is_dir() and candidate not in installed:
                installed.append(candidate)
    except (ImportError, ValueError):
        pass

    for candidate in sorted(REPO_ROOT.glob("vendor/wewrite/wewrite")):
        if (candidate / "toolkit").is_dir():
            vendored.append(candidate)

    return installed, vendored


def resolve_theme(env_info, explicit_theme):
    """按 theme.py 的真实查找顺序确认主题 yaml 存在。

    对齐 theme.py `_theme_search_dirs()` —— 只有两级：
      [user_themes_dir(), <实际生效包>/toolkit/themes]
    其中 paths.py `user_themes_dir() = home()/"themes"`，
    home() 就是 $WEWRITE_HOME（默认 ~/.wewrite）。

    为什么值得单独查：主题缺失时 `load_theme()` 直接抛
    `FileNotFoundError`，preview 和 publish **都在加载主题这一步就退出**，
    根本走不到微信 API。而 `wewrite diagnose` 并不检查主题，
    所以它会显示一切正常 —— 这个盲区必须由预检补上。
    """
    home = Path(env_info["wewrite_home"]["effective"])
    user_themes = home / "themes"

    pkg_dirs, vendored_dirs = _wewrite_package_dirs()
    builtin_dirs = [p / "toolkit" / "themes" for p in pkg_dirs]

    style_theme = env_info.get("style_yaml", {}).get("theme")
    env_theme = os.environ.get("WEWRITE_THEME")
    name = explicit_theme or style_theme or env_theme or "professional-clean"
    if explicit_theme:
        source = "cli --theme"
    elif style_theme:
        source = "style.yaml"
    elif env_theme:
        source = "env WEWRITE_THEME"
    else:
        source = "默认值（cli.py: cfg.get('theme','professional-clean')）"

    search = [user_themes] + builtin_dirs
    hit = None
    for directory in search:
        candidate = directory / ("%s.yaml" % name)
        if candidate.is_file():
            hit = candidate
            break

    available = []
    for directory in search:
        if directory.is_dir():
            for yaml_file in sorted(directory.glob("*.yaml")):
                if yaml_file.stem not in available:
                    available.append(yaml_file.stem)

    vendored_report = [
        {
            "path": str(d),
            "count": len(list((d / "toolkit" / "themes").glob("*.yaml")))
            if (d / "toolkit" / "themes").is_dir() else 0,
            "in_search_path": any(
                (d / "toolkit" / "themes") == Path(s) for s in search
            ),
        }
        for d in vendored_dirs
    ]

    return {
        "name": name,
        "name_source": source,
        "resolved_path": str(hit) if hit else None,
        "found": hit is not None,
        "search_order": [str(d) for d in search],
        "user_themes_dir": {
            "path": str(user_themes),
            "exists": user_themes.is_dir(),
        },
        "builtin_themes_dirs": [
            {
                "path": str(d),
                "exists": d.is_dir(),
                "count": len(list(d.glob("*.yaml"))) if d.is_dir() else 0,
            }
            for d in builtin_dirs
        ],
        "wewrite_package_dirs": [str(d) for d in pkg_dirs],
        "vendored_copies": vendored_report,
        "available_themes": available,
        "available_count": len(available),
    }


# --------------------------------------------------------------------------
# 图片探测（标准库解析文件头，不依赖 Pillow）
# --------------------------------------------------------------------------
def sniff_image(path: Path):
    """返回 (format, width, height, size_bytes, error)。解析不了 format=None。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, None, None, None, "stat failed: %s" % exc
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
            fmt, width, height = _parse_image_head(handle, head)
    except OSError as exc:
        return None, None, None, size, "read failed: %s" % exc
    return fmt, width, height, size, None


def _parse_image_head(handle, head: bytes):
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        width, height = struct.unpack(">II", head[16:24])
        return "png", width, height

    if head[:2] == b"\xff\xd8":
        handle.seek(2)
        while True:
            marker = handle.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                return "jpeg", None, None
            code = marker[1]
            if code in (0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
                continue
            length_raw = handle.read(2)
            if len(length_raw) < 2:
                return "jpeg", None, None
            length = struct.unpack(">H", length_raw)[0]
            # SOF0..SOF15，剔除 DHT(C4)/JPG(C8)/DAC(CC)
            if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
                body = handle.read(5)
                if len(body) < 5:
                    return "jpeg", None, None
                height, width = struct.unpack(">HH", body[1:5])
                return "jpeg", width, height
            if length < 2:
                return "jpeg", None, None
            handle.seek(length - 2, os.SEEK_CUR)

    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        width, height = struct.unpack("<HH", head[6:10])
        return "gif", width, height

    if head[:2] == b"BM" and len(head) >= 26:
        width, height = struct.unpack("<ii", head[18:26])
        return "bmp", width, abs(height)

    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", None, None

    return None, None, None


def describe_ratio(width, height):
    if not width or not height:
        return None
    return width / float(height)


def ratio_label(width, height):
    if not width or not height:
        return "unknown"
    return "%.2f_1" % (width / float(height))


def matches_wechat_ratio(width, height, tol: float = 0.03):
    """返回命中的微信裁切比例标签（`2.35_1` / `1_1`），都不命中返回空串。"""
    ratio = describe_ratio(width, height)
    if ratio is None:
        return ""
    for target in LIMITS["cover_ratios"]:
        want = target[0] / target[1]
        if abs(ratio - want) <= want * tol:
            return "%g_%g" % (target[0], target[1])
    return ""


# --------------------------------------------------------------------------
# Markdown 分析
# --------------------------------------------------------------------------
def find_title(markdown_text: str) -> str:
    """对齐 converter.py:_extract_title —— 第一个 `# ` 行（不含 `## `）。"""
    for line in markdown_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def strip_h1(markdown_text: str) -> str:
    kept = []
    for line in markdown_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue
        kept.append(line)
    return "\n".join(kept)


def _strip_fenced_code(markdown_text: str) -> str:
    return re.sub(r"```.*?```", "\n", markdown_text, flags=re.S)


def plain_text(markdown_text: str) -> str:
    """把 markdown 粗化成可数「字」的纯文本。够用即可，不做完整渲染。"""
    text = markdown_text
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-+*]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~`]{1,3}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def count_chars(text: str) -> int:
    """按字符计。CJK 一字一字符，与微信「字」的口径最接近。"""
    return len(text)


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def truncate_to_bytes(text: str, max_bytes: int) -> str:
    """对齐 converter._generate_digest 的截断方式（UTF-8 边界安全）。"""
    ellipsis = "..."
    target = max_bytes - byte_len(ellipsis)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:target].decode("utf-8", errors="ignore").rstrip() + ellipsis


def _table_cells(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _table_cells(line)
    if not cells:
        return False
    for cell in cells:
        if not re.match(r"^:?-{2,}:?$", cell):
            return False
    return True


def scan_tables(markdown_text: str):
    """扫 markdown 表格，返回 [{line, columns, rows, has_separator}]。跳过代码块。"""
    tables = []
    in_fence = False
    lines = markdown_text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence or line.count("|") < 2:
            index += 1
            continue
        start = index
        block = []
        while index < len(lines) and lines[index].count("|") >= 2 and lines[index].strip():
            block.append(lines[index])
            index += 1
        if len(block) >= 2 and _is_separator_row(block[1]):
            tables.append(
                {
                    "line": start + 1,
                    "columns": len(_table_cells(block[0])),
                    "rows": len(block) - 2,
                    "has_separator": True,
                }
            )
        elif len(block) >= 2:
            tables.append(
                {
                    "line": start + 1,
                    "columns": len(_table_cells(block[0])),
                    "rows": len(block),
                    "has_separator": False,
                }
            )
    return tables


def scan_html_tables(markdown_text: str):
    results = []
    for match in HTML_TR_RE.finditer(markdown_text):
        cells = len(HTML_CELL_RE.findall(match.group(1)))
        if cells:
            results.append(cells)
    return results


def scan_body_images(markdown_text: str, base_dir: Path):
    """返回去重后的图片引用列表（保留出现顺序）。"""
    seen = []
    seen_set = set()
    for regex in (MD_IMAGE_RE, HTML_IMAGE_RE):
        for match in regex.finditer(markdown_text):
            ref = match.group(1).strip().strip("<>")
            if not ref or ref in seen_set:
                continue
            seen_set.add(ref)
            seen.append(ref)
    out = []
    for ref in seen:
        if ref.startswith(("http://", "https://", "//", "data:")):
            out.append({"ref": ref, "kind": "remote"})
            continue
        candidate = Path(ref)
        if candidate.is_absolute():
            resolved = candidate
        else:
            near = base_dir / ref
            resolved = near if near.exists() else Path.cwd() / ref
        out.append({"ref": ref, "kind": "local", "path": str(resolved), "exists": resolved.is_file()})
    return out


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_preflight(args) -> dict:
    report = Report()
    env_info = resolve_environment()
    creds = resolve_credentials(env_info)
    run_state = resolve_run_state(env_info, args.run)
    theme_info = resolve_theme(env_info, getattr(args, "theme", None))

    article_path = Path(args.article).expanduser()
    cover_path = Path(args.cover).expanduser() if args.cover else None

    # 中文摘要别走命令行：shell 编码、引号、换行都能把长相正常的摘要毁掉。
    # --digest-file 给一条安全通道，和 --digest 互斥（同时给算用法错误）。
    digest_arg = getattr(args, "digest", None)
    digest_file = getattr(args, "digest_file", None)
    if digest_file:
        if digest_arg is not None:
            report.add(
                "digest.source",
                FAIL,
                "--digest 与 --digest-file 只能给一个（摘要来源不明确，不知道该用哪个）",
            )
            digest_arg = None
        else:
            try:
                digest_arg = Path(digest_file).expanduser().read_text(encoding="utf-8")
                # 编辑器存的文件几乎总是带尾换行；它不该算进 120 字节里。
                digest_arg = digest_arg.rstrip("\r\n")
                args.digest_origin = "cli --digest-file"
                report.add(
                    "digest.source",
                    PASS,
                    "摘要来自文件 %s（尾换行已去掉）" % Path(digest_file).expanduser(),
                )
            except OSError as exc:
                report.add(
                    "digest.source",
                    FAIL,
                    "摘要文件不可读：%s（%s）" % (Path(digest_file).expanduser(), exc),
                )
                digest_arg = None
    args.digest = digest_arg

    # ---- 文章本体 ----
    markdown_text = None
    if not article_path.is_file():
        report.add("article.exists", FAIL, "文章不存在：%s" % article_path)
    else:
        try:
            markdown_text = article_path.read_text(encoding="utf-8")
            report.add("article.exists", PASS, "已读取 %d 字节" % article_path.stat().st_size)
        except OSError as exc:
            report.add("article.exists", FAIL, "文章不可读：%s" % exc)

    title = ""
    body = ""
    body_plain = ""
    if markdown_text is not None:
        title = find_title(markdown_text)
        body = strip_h1(markdown_text)
        body_plain = plain_text(_strip_fenced_code(body))

    # ---- 标题 ----
    title_info = {"value": title, "chars": count_chars(title), "bytes": byte_len(title)}
    if markdown_text is not None:
        if not title:
            # wewrite 的 _extract_title 取不到标题时 title 为空，
            # draft/add 会直接报错，所以这是硬失败。
            report.add("title.present", FAIL, "没有一级标题（`# ` 开头的第一行）")
        elif count_chars(title) > LIMITS["title_max_chars"]:
            report.add(
                "title.length",
                FAIL,
                "标题 %d 字，超过微信上限 %d 字" % (count_chars(title), LIMITS["title_max_chars"]),
            )
        elif count_chars(title) > LIMITS["title_max_chars"] - 2:
            report.add(
                "title.length",
                WARN,
                "标题 %d 字，已贴近上限 %d 字（留点余量更稳）"
                % (count_chars(title), LIMITS["title_max_chars"]),
            )
        else:
            report.add("title.length", PASS, "%d 字" % count_chars(title))
        if "\n" in title or "\r" in title:
            report.add("title.compliance", FAIL, "标题含换行")
        elif re.search(r"<[^>]+>", title):
            report.add("title.compliance", FAIL, "标题含 HTML 标签")
        elif title != title.strip():
            report.add("title.compliance", WARN, "标题首尾有空白")
        else:
            report.add("title.compliance", PASS, "无换行/无 HTML 标签")

    # ---- 正文长度 ----
    body_info = {
        "raw_markdown_chars": count_chars(body),
        "plain_chars": count_chars(body_plain),
        "plain_bytes": byte_len(body_plain),
    }
    if markdown_text is not None:
        chars = body_info["plain_chars"]
        if chars < LIMITS["body_min_chars"]:
            report.add(
                "body.length",
                FAIL,
                "正文 %d 字，低于下限 %d 字" % (chars, LIMITS["body_min_chars"]),
            )
        elif chars > LIMITS["body_max_chars"]:
            report.add(
                "body.length",
                FAIL,
                "正文 %d 字，超过上限 %d 字" % (chars, LIMITS["body_max_chars"]),
            )
        elif body_info["plain_bytes"] > LIMITS["body_max_bytes"]:
            report.add(
                "body.length",
                FAIL,
                "正文 %d 字节，超过 1MB" % body_info["plain_bytes"],
            )
        else:
            report.add("body.length", PASS, "%d 字 / %d 字节" % (chars, body_info["plain_bytes"]))

    # ---- 摘要 ----
    if args.digest is not None:
        digest = args.digest
        digest_info = {"source": getattr(args, "digest_origin", "cli --digest"), "value": digest,
                       "chars": count_chars(digest), "bytes": byte_len(digest)}
        if byte_len(digest) > LIMITS["digest_max_bytes"]:
            report.add(
                "digest.bytes",
                FAIL,
                "摘要 %d 字节，超过 %d 字节（中文 3 字节/字，120 字节约 40 个汉字）"
                % (byte_len(digest), LIMITS["digest_max_bytes"]),
            )
        else:
            report.add("digest.bytes", PASS, "%d 字节" % byte_len(digest))
    elif markdown_text is not None:
        candidate = truncate_to_bytes(body_plain, LIMITS["digest_max_bytes"])
        digest_info = {
            "source": "auto（按 converter._generate_digest 的口径预算）",
            "value": candidate,
            "chars": count_chars(candidate),
            "bytes": byte_len(candidate),
            "truncated": body_plain != candidate,
        }
        report.add(
            "digest.bytes",
            PASS,
            "自动摘要 %d 字节（上限 %d），%s"
            % (
                byte_len(candidate),
                LIMITS["digest_max_bytes"],
                "已截断" if digest_info["truncated"] else "未截断",
            ),
        )
    else:
        digest_info = {"source": "unavailable"}

    # ---- 正文图片 ----
    images_info = {"count": 0, "items": []}
    if markdown_text is not None:
        base_dir = article_path.parent
        images = scan_body_images(body, base_dir)
        images_info["count"] = len(images)
        images_info["items"] = images
        if len(images) > LIMITS["images_max"]:
            report.add(
                "images.count",
                FAIL,
                "%d 张，超过上限 %d 张" % (len(images), LIMITS["images_max"]),
            )
        else:
            report.add("images.count", PASS, "%d 张" % len(images))

        bad = []
        for item in images:
            if item["kind"] == "remote":
                report.add(
                    "images.remote",
                    WARN,
                    "外链图会被微信过滤，需先 uploadimg 转存：%s" % item["ref"][:80],
                )
                continue
            if not item.get("exists"):
                bad.append("%s（文件不存在）" % item["ref"])
                continue
            fmt, width, height, size, err = sniff_image(Path(item["path"]))
            item.update(
                {"format": fmt, "width": width, "height": height, "bytes": size}
            )
            if err:
                bad.append("%s（%s）" % (item["ref"], err))
                continue
            if size is not None and size > LIMITS["body_image_max_bytes"]:
                bad.append(
                    "%s（%.2fMB，uploadimg 上限 1MB）"
                    % (item["ref"], size / 1048576.0)
                )
            elif fmt and fmt not in BODY_IMAGE_FORMATS:
                bad.append("%s（%s，uploadimg 只收 jpg/png）" % (item["ref"], fmt))
        if bad:
            status = FAIL if args.publish else WARN
            report.add(
                "images.uploadable",
                status,
                "正文图会传不上去：" + "；".join(bad[:5]),
            )
        elif images:
            report.add("images.uploadable", PASS, "全部为本地可读 jpg/png 且 <1MB")
        else:
            report.add("images.uploadable", PASS, "无正文图")

    # ---- 表格 ----
    tables_info = {"count": 0, "max_columns": 0, "items": [], "html_table_columns": []}
    if markdown_text is not None:
        tables = scan_tables(body)
        tables_info["items"] = tables
        tables_info["count"] = len(tables)
        if tables:
            tables_info["max_columns"] = max(t["columns"] for t in tables)
        over = [t for t in tables if t["columns"] > LIMITS["table_cols_max"]]
        if over:
            report.add(
                "tables.columns",
                FAIL,
                "%d 张表超过 %d 列（首个在第 %d 行，%d 列）"
                % (len(over), LIMITS["table_cols_max"], over[0]["line"], over[0]["columns"]),
            )
        elif tables:
            report.add(
                "tables.columns",
                PASS,
                "%d 张表，最多 %d 列" % (len(tables), tables_info["max_columns"]),
            )
        else:
            report.add("tables.columns", PASS, "无表格")

        html_cols = scan_html_tables(body)
        tables_info["html_table_columns"] = html_cols
        if html_cols and max(html_cols) > LIMITS["table_cols_max"]:
            report.add(
                "tables.columns_html",
                WARN,
                "内联 HTML 表格最多 %d 列，超过建议的 %d 列"
                % (max(html_cols), LIMITS["table_cols_max"]),
            )

    # ---- 封面 ----
    cover_info = {"path": str(cover_path) if cover_path else None}
    if cover_path is None:
        if args.publish:
            report.add(
                "cover.present",
                FAIL,
                "发布草稿必须有封面（draft/add 的 thumb_media_id 必填，"
                "wewrite publisher.create_draft 会直接抛 ValueError）",
            )
        else:
            report.add("cover.present", WARN, "未提供封面（只做本地预览可以没有）")
    else:
        fmt, width, height, size, err = sniff_image(cover_path)
        cover_info.update(
            {"format": fmt, "width": width, "height": height, "bytes": size, "error": err}
        )
        if not cover_path.is_file():
            report.add("cover.present", FAIL, "封面不存在：%s" % cover_path)
        elif err:
            report.add("cover.present", FAIL, "封面不可读：%s" % err)
        else:
            report.add("cover.present", PASS, "可读，%d 字节" % (size or 0))
            if fmt is None:
                report.add(
                    "cover.format",
                    WARN,
                    "无法识别图片格式（无法解析文件头），跳过格式判定",
                )
            elif fmt not in COVER_FORMATS:
                report.add(
                    "cover.format",
                    FAIL,
                    "%s 不是 add_material 支持的格式（%s）"
                    % (fmt, "/".join(COVER_FORMATS)),
                )
            else:
                report.add("cover.format", PASS, fmt)
            if size is not None and size > LIMITS["cover_max_bytes"]:
                report.add(
                    "cover.size",
                    FAIL,
                    "%.2fMB 超过 add_material 的 10MB 上限" % (size / 1048576.0),
                )
            elif size is not None:
                report.add("cover.size", PASS, "%.2fMB" % (size / 1048576.0))
            if width and height:
                cover_info["ratio"] = round(width / float(height), 3)
                if min(width, height) < LIMITS["cover_min_side"]:
                    report.add(
                        "cover.dimensions",
                        WARN,
                        "%dx%d 偏小（建议短边 ≥%d）"
                        % (width, height, LIMITS["cover_min_side"]),
                    )
                matched = matches_wechat_ratio(width, height)
                if matched:
                    report.add(
                        "cover.dimensions",
                        PASS,
                        "%dx%d，比例 %s 与微信裁切比例吻合"
                        % (width, height, ratio_label(width, height)),
                    )
                else:
                    report.add(
                        "cover.dimensions",
                        WARN,
                        "%dx%d 比例 %s，微信 draft/add 只认 2.35_1 与 1_1 两种裁切比例，"
                        "封面会被居中裁切" % (width, height, ratio_label(width, height)),
                    )
            else:
                report.add(
                    "cover.dimensions",
                    WARN,
                    "无法解析尺寸（%s），跳过尺寸检查" % (fmt or "unknown format"),
                )

    # ---- 凭证 ----
    creds_blocked = False
    if not creds["appid"]["configured"] and not creds["secret"]["configured"]:
        creds_blocked = True
        # 别把原因写死成「config.yaml 里没有」—— 也可能是文件里有值、但被
        # 空字符串的环境变量覆盖掉了（config.py 只在 is None 时跳过）。
        # 这两种情况的修法完全相反，所以必须按实际情况分别说清楚。
        shadowed = [
            field
            for field in ("appid", "secret")
            if creds[field]["in_config_file"] and creds[field]["source"] == "env"
        ]
        if shadowed:
            detail = (
                "未配置：config.yaml 里其实**有** %s，但被空字符串环境变量覆盖成了空值"
                "（%s）。config.py 只在 `is None` 时跳过 env，空串照样覆盖 —— "
                "`unset WECHAT_APPID WECHAT_SECRET` 即可恢复文件里的值。发布不可用，只能本地预览。"
                % (
                    "/".join("wechat." + f for f in shadowed),
                    " ".join("WECHAT_" + f.upper() for f in shadowed),
                )
            )
        else:
            detail = (
                "未配置：config.yaml 里没有 wechat.appid/secret，环境变量 WECHAT_APPID/"
                "WECHAT_SECRET 也没设。发布不可用，只能本地预览。"
            )
    elif not creds["appid"]["configured"]:
        creds_blocked = True
        detail = "缺 appid（secret 已有）"
    elif not creds["secret"]["configured"]:
        creds_blocked = True
        detail = "缺 secret（appid 已有）"
    else:
        detail = "appid 与 secret 均已配置"
    report.add(
        "credentials.present",
        FAIL if (args.publish and creds_blocked) else
        (WARN if creds_blocked else PASS),
        detail,
    )

    if not creds["author"]["configured"]:
        report.add(
            "credentials.author",
            WARN,
            "wechat.author 未配置（可选字段，但不填则草稿作者为空）",
        )
    else:
        report.add("credentials.author", PASS, "author 已配置")

    if creds["parse_error"]:
        report.add("credentials.parse", WARN, "config.yaml 解析有问题：%s" % creds["parse_error"])
    if not creds["config_file"]:
        report.add(
            "credentials.config_file",
            WARN,
            "config.yaml 不存在（查找位置见 environment.config_search）",
        )
    else:
        report.add("credentials.config_file", PASS, creds["config_file"])

    # 空字符串 env 覆盖的坑：config.py 用 `is None` 判断，空串会覆盖成空值
    empty_env = [
        var for var, state in env_info["env"].items()
        if state.get("set") and state.get("empty")
    ]
    if empty_env:
        report.add(
            "env.empty_overrides",
            WARN,
            "这些环境变量已设置但为空：%s —— config.py 只在 `is None` 时跳过，"
            "空串会把 config.yaml 的值覆盖成空" % ", ".join(empty_env),
        )

    # ---- 权限门 ----
    if run_state.get("found"):
        if args.publish:
            if run_state.get("publish_permission") is not True:
                report.add(
                    "permissions.publish",
                    FAIL,
                    "任务 permissions.publish=%r，未授权；先跑 "
                    "`wewrite run permission publish allow`"
                    % (run_state.get("publish_permission"),),
                )
            else:
                report.add("permissions.publish", PASS, "已授权 publish=true")
            if run_state.get("skip_publish") is True:
                report.add(
                    "flags.skip_publish",
                    FAIL,
                    "任务 flags.skip_publish=true（例如凭证缺失被降级），不要发布",
                )
            else:
                report.add(
                    "flags.skip_publish",
                    PASS,
                    "skip_publish=%r" % (run_state.get("skip_publish"),),
                )
        else:
            report.add(
                "permissions.publish",
                PASS,
                "本轮未要求发布；任务 permissions.publish=%r、skip_publish=%r"
                % (run_state.get("publish_permission"), run_state.get("skip_publish")),
            )
    else:
        report.add(
            "permissions.publish",
            PASS,
            "无任务状态，权限门不适用（%s）" % run_state.get("reason", ""),
        )

    # ---- 主题 ----
    # 主题缺失会让 load_theme() 抛 FileNotFoundError，preview 与 publish
    # 都在这一步就退出，压根到不了微信 API。而且 `wewrite diagnose`
    # 没有主题检查项，会显示一切正常 —— 所以这里必须自己查。
    if theme_info["found"]:
        report.add(
            "theme.available",
            PASS,
            "主题 %s 已找到（来源：%s）：%s"
            % (theme_info["name"], theme_info["name_source"], theme_info["resolved_path"]),
        )
    else:
        hint = ""
        if theme_info["available_count"]:
            hint = "目前可用的主题：%s" % ", ".join(theme_info["available_themes"][:8])
        else:
            hint = (
                "一个主题都没有：$WEWRITE_HOME/themes 为空，且 wewrite 包内"
                " toolkit/themes 目录不存在（安装时漏了 package-data）。"
                "从 vendor 源码安装时 pyproject 的 [tool.setuptools.package-data] "
                "只写了 data/*，没包含 toolkit/themes/*.yaml。"
            )
        report.add(
            "theme.available",
            FAIL,
            "主题 %s 找不到（来源：%s）。preview 与 publish 都会在 "
            "load_theme() 处抛 FileNotFoundError 直接退出。查找位置：%s。%s"
            % (
                theme_info["name"],
                theme_info["name_source"],
                " / ".join(theme_info["search_order"]),
                hint,
            ),
        )

    # ---- 状态目录一致性 ----
    # 仓库里常有 `.wewrite/`（style.yaml 住在这儿），但 wewrite 真正读的是
    # $WEWRITE_HOME（默认 ~/.wewrite）。两者不是一回事：不 export WEWRITE_HOME
    # 就跑去 publish，会看到「style.yaml 明明有，却说没配置」。
    effective_home = env_info["wewrite_home"]["effective"]
    foreign = [d for d in env_info["repo_state_dirs"] if not d["is_effective_home"]]
    if foreign:
        detail = "；".join(
            "%s（config.yaml=%s, style.yaml=%s, theme=%s）"
            % (
                d["path"],
                "有" if d["has_config_yaml"] else "无",
                "有" if d["has_style_yaml"] else "无",
                d.get("style_theme") or "未读到",
            )
            for d in foreign
        )
        if not env_info["wewrite_home"]["exists"]:
            report.add(
                "state_dir.repo_vs_home",
                WARN,
                "仓库内有 .wewrite/ 但当前生效的 WEWRITE_HOME 是 %s（不存在）。"
                "wewrite 只读后者，不会读仓库里的 .wewrite。现有：%s"
                % (effective_home, detail),
            )
        else:
            report.add(
                "state_dir.repo_vs_home",
                PASS,
                "生效 WEWRITE_HOME=%s；另发现仓库内 .wewrite/（不参与本次发布）：%s"
                % (effective_home, detail),
            )
    else:
        report.add(
            "state_dir.repo_vs_home",
            PASS,
            "WEWRITE_HOME=%s，与仓库内 .wewrite 一致或仓库内无该目录" % effective_home,
        )

    # ---- 组装 ----
    payload = {
        "tool": "wewrite-publish/preflight",
        "version": 1,
        "browser_required": False,
        "browserless_note": (
            "本预检不联网、不启动浏览器。wewrite publish 走微信官方 HTTP API "
            "POST /cgi-bin/draft/add，见 scripts/no_browser_check.py。"
        ),
        "mode": "publish" if args.publish else "preview",
        "inputs": {
            "article": str(article_path),
            "cover": str(cover_path) if cover_path else None,
            "digest_provided": args.digest is not None,
            "author_provided": args.author is not None,
        },
        "environment": env_info,
        "credentials": {k: v for k, v in creds.items() if not k.startswith("_")},
        "run_state": run_state,
        "theme": theme_info,
        "content": {
            "title": title_info,
            "body": body_info,
            "digest": digest_info,
            "images": images_info,
            "tables": tables_info,
            "cover": cover_info,
        },
        "limits": LIMITS,
        "checks": report.checks,
        "summary": report.counts(),
        "verdict": report.verdict(),
        "exit_code": report.exit_code(),
    }

    # 最后一道保险：任何输出里都不许出现 secret 原文
    secret_value = creds.get("_secret_value") or ""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if secret_value and secret_value in serialized:
        serialized = serialized.replace(secret_value, "[REDACTED]")
        payload = json.loads(serialized)
        payload["checks"].append(
            {"check": "security.no_secret_in_output", "status": FAIL,
             "detail": "输出里出现了 secret 原文，已强制打码（这本身是 bug）"}
        )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="wewrite-publish 发布前预检（无浏览器、不联网、不调微信 API）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--article", required=True, help="待发布/预览的 Markdown 文件")
    parser.add_argument("--cover", help="封面图片（发布草稿时必填）")
    parser.add_argument("--digest", help="摘要（短文本可用）；不给则按 converter 口径自动预算")
    parser.add_argument(
        "--digest-file",
        help="从文件读摘要（推荐：中文摘要走文件，别内联到命令行；末尾换行会被去掉）",
    )
    parser.add_argument("--author", help="作者（只记录，不校验长度以外的内容）")
    parser.add_argument("--publish", action="store_true",
                        help="本轮真的要推草稿箱：凭证/封面/权限门升级为硬失败")
    parser.add_argument("--theme", help="主题名（默认取 style.yaml 的 theme，再退 professional-clean）")
    parser.add_argument("--run", help="任务 id（默认 $WEWRITE_RUN_ID 或 current_run）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_preflight(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
