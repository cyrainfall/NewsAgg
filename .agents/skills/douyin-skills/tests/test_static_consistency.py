"""静态一致性检查：抓住"只会在真机跑到那一步才暴露"的接线错误。

这三类错误都不需要浏览器，读源码就能查出来，而且失败代价很高（要连上浏览器、
登录、上传视频才能走到出错的那一行）：

1. `page.xxx(...)` 调用了 BridgePage 上不存在的方法 —— 纯笔误；
2. Python 侧发给扩展的方法名，扩展 background.js 里没有实现；
3. 各处写死的 bridge 端口不一致 —— 会导致扩展连不上或连错服务。

所以这里刻意不做任何 mock，直接对真实源码做静态核对。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PACKAGE = SCRIPTS / "douyin"
BACKGROUND_JS = ROOT / "extension" / "background.js"
POPUP_JS = ROOT / "extension" / "popup.js"

# 扩展里所有 `case "method":` 分支（handleCommand / mainWorldExecutor / domExecutor 三者之和）
EXTENSION_CASE_RE = re.compile(r'case\s+"([a-z_]+)"')
# bridge.py 里所有 `_call("method", ...)`
BRIDGE_CALL_RE = re.compile(r'_call\(\s*"([a-z_]+)"')
# ws://localhost:<port>
WS_PORT_RE = re.compile(r"ws://localhost:(\d+)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_calls_exist_on_bridge_page() -> None:
    """`page.<name>` 必须都是 BridgePage 上的真实属性。"""
    from douyin.bridge import BridgePage

    api = {name for name in dir(BridgePage) if not name.startswith("__")}
    missing: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "page"
                and node.attr not in api
            ):
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno} page.{node.attr}")

    assert not missing, "以下 page.* 调用在 BridgePage 上不存在：\n" + "\n".join(missing)


def test_bridge_call_methods_are_implemented_by_extension() -> None:
    """`_call("method")` 里的方法名必须都在扩展里有对应实现。"""
    implemented = set(EXTENSION_CASE_RE.findall(_read(BACKGROUND_JS)))
    called = set(BRIDGE_CALL_RE.findall(_read(PACKAGE / "bridge.py")))

    # ping_server 由 bridge_server 直接应答，不转发给扩展，因此扩展里没有它
    called.discard("ping_server")

    missing = sorted(called - implemented)
    assert not missing, (
        "以下方法 Python 侧会调用，但 extension/background.js 未实现：\n" + "\n".join(missing)
    )


def test_extension_has_no_orphan_methods() -> None:
    """反向检查：扩展实现的桥接方法都应该有调用方（否则多半是改名后的残留）。"""
    implemented = set(EXTENSION_CASE_RE.findall(_read(BACKGROUND_JS)))
    called = set(BRIDGE_CALL_RE.findall(_read(PACKAGE / "bridge.py")))
    # get_url 是留给 probe / 排查用的调试方法，暂时没有 Python 侧调用方
    allowed_orphans = {"get_url"}

    orphans = sorted(implemented - called - allowed_orphans)
    assert not orphans, (
        "扩展实现了但没有任何调用方的方法"
        "（若是改名残留请清理，若确实需要请加入 allowed_orphans）：\n" + "\n".join(orphans)
    )


def test_bridge_port_is_consistent() -> None:
    """扩展、CLI、bridge server 三处的 bridge 端口必须一致。

    端口是最容易"改了一半"的地方 —— 只要有一处没跟着改，表现就是"扩展连不上"，
    而错误信息完全不会提示是端口问题。
    """
    extension_ports = set(WS_PORT_RE.findall(_read(BACKGROUND_JS)))
    popup_ports = set(WS_PORT_RE.findall(_read(POPUP_JS)))
    bridge_ports = set(WS_PORT_RE.findall(_read(PACKAGE / "bridge.py")))
    cli_ports = set(WS_PORT_RE.findall(_read(SCRIPTS / "cli.py")))

    server_default = re.search(
        r'"--port",\s*type=int,\s*default=(\d+)', _read(SCRIPTS / "bridge_server.py")
    )
    assert server_default, "bridge_server.py 里没找到 --port 的默认值，测试需要同步更新"
    server_ports = {server_default.group(1)}

    for label, ports in (
        ("extension/background.js", extension_ports),
        ("extension/popup.js", popup_ports),
        ("scripts/douyin/bridge.py", bridge_ports),
        ("scripts/cli.py", cli_ports),
        ("scripts/bridge_server.py", server_ports),
    ):
        assert ports, f"{label} 里没有找到 bridge 端口"

    all_ports = extension_ports | popup_ports | bridge_ports | cli_ports | server_ports
    assert len(all_ports) == 1, (
        f"bridge 端口不一致：{all_ports}。"
        f"background.js={extension_ports} popup.js={popup_ports} "
        f"bridge.py={bridge_ports} cli.py={cli_ports} bridge_server.py={server_ports}"
    )


def test_selector_candidates_list_points_at_real_selectors() -> None:
    """`SELECTOR_CANDIDATES` 必须指向真实的 CSS 选择器候选列表。

    probe 会拿这个清单去页面里逐个 querySelector，所以清单里只能放真正的选择器：
    放进文案常量（如 PUBLISH_BUTTON_TEXTS）会让 probe 抛出选择器语法错误，
    排查改版时反而多一个报错。
    """
    from douyin import selectors

    # 纯中文且不含任何 CSS 结构字符的，基本可以判定是文案常量而不是选择器。
    # 注意不能简单地"含中文就报错"：`img[alt*='二维码']` 是合法选择器，属性值可以是中文。
    css_structure_chars = set("[]()#.:>*~+|'\" \t")

    problems: list[str] = []
    for name in selectors.SELECTOR_CANDIDATES:
        value = getattr(selectors, name, None)
        if value is None:
            problems.append(f"{name}: selectors.py 里不存在这个常量")
            continue
        if not isinstance(value, tuple) or not value:
            problems.append(f"{name}: 不是非空元组")
            continue
        for item in value:
            if not isinstance(item, str):
                problems.append(f"{name}: 含非字符串项 {item!r}")
                continue
            has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in item)
            if has_cjk and not (set(item) & css_structure_chars):
                problems.append(f"{name}: {item!r} 含中文且无 CSS 结构字符，像是文案常量")

    assert not problems, "SELECTOR_CANDIDATES 有问题：\n" + "\n".join(problems)


def test_selector_candidates_are_non_empty_strings() -> None:
    """selectors.py 的候选列表必须是非空字符串元组。

    空候选会让 `first_present_selector` 静默返回 None，错误信息指向"页面改版"
    而不是"有人改坏了选择器常量"。
    """
    from douyin import selectors

    broken: list[str] = []
    for name in dir(selectors):
        if name.startswith("_") or not name.isupper():
            continue
        value = getattr(selectors, name)
        if isinstance(value, tuple):
            if not value or not all(isinstance(item, str) and item.strip() for item in value):
                broken.append(name)
        elif isinstance(value, dict):
            for key, items in value.items():
                if not isinstance(items, tuple) or not items:
                    broken.append(f"{name}[{key!r}]")

    assert not broken, "以下选择器候选列表为空或含空字符串：\n" + "\n".join(broken)
