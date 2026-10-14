"""静态一致性检查：抓住"只会在真机跑到那一步才暴露"的接线错误。

这些错误都不需要浏览器，读源码就能查出来，而且失败代价很高（要连上浏览器、
登录、上传视频才能走到出错的那一行）：

1. `page.xxx(...)` 调用了 BridgePage 上不存在的方法 —— 纯笔误；
2. Python 侧发给扩展的方法名，扩展 background.js 里没有实现（或反过来是残留）；
3. 各处写死的 bridge 端口不一致 —— 会导致扩展连不上或连错服务；
4. `selectors.py` 的候选列表为空 / 混进文案常量 —— 报错会指向"页面改版"而不是真原因；
5. 文档里写的 CLI 子命令根本不存在 —— Agent 照着文档执行会直接失败。

所以这里刻意不做任何 mock，直接对真实源码做静态核对。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PACKAGE = SCRIPTS / "bilibili"
BACKGROUND_JS = ROOT / "extension" / "background.js"
POPUP_JS = ROOT / "extension" / "popup.js"

# 扩展里所有 `case "method":` 分支（handleCommand / mainWorldExecutor / domExecutor 三者之和）
EXTENSION_CASE_RE = re.compile(r'case\s+"([a-z_]+)"')
# bridge.py 里所有 `_call("method", ...)`
BRIDGE_CALL_RE = re.compile(r'_call\(\s*"([a-z_]+)"')
# ws://localhost:<port>
WS_PORT_RE = re.compile(r"ws://localhost:(\d+)")
# cli.py 里的 add_parser("name", ...)
SUBCOMMAND_RE = re.compile(r'add_parser\(\s*"([a-z][a-z0-9-]*)"')
# 文档里出现的 `cli.py <子命令>`（`<子命令>` 这种占位符以 < 开头，不会匹配）
DOC_COMMAND_RE = re.compile(r"cli\.py\s+([a-z][a-z0-9-]*)")

# 允许出现在文档里、但不是 cli.py 子命令的写法（例如 bridge-server 的启动方式）
DOC_COMMAND_ALLOWLIST: set[str] = set()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    """取 background.js 里某个顶层函数的源码（到下一个顶层函数声明为止）。"""
    rest = source[source.index(f"function {name}") :]
    match = re.search(r"\n(?:async )?function \w+", rest[1:])
    return rest[: match.start() + 1] if match else rest


def _routing_cases(source: str, name: str) -> set[str]:
    """取某个函数里所有 `case "xxx":` 的名字。"""
    return set(EXTENSION_CASE_RE.findall(_function_source(source, name)))


def test_modules_import_cleanly() -> None:
    """所有模块都要能 import。

    这一条能挡住 `from .selectors import XXX` 里的名字笔误 —— 那种错误在真机上
    表现为"跑了几分钟才炸"，在本地是一秒钟的事。
    """
    import bilibili.bridge
    import bilibili.dom
    import bilibili.errors
    import bilibili.human
    import bilibili.login
    import bilibili.probe
    import bilibili.publish_video
    import bilibili.selectors
    import bilibili.types
    import bilibili.urls

    assert bilibili.selectors.SELECTOR_CANDIDATES


def test_page_calls_exist_on_bridge_page() -> None:
    """`page.<name>` 必须都是 BridgePage 上的真实属性。"""
    from bilibili.bridge import BridgePage

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


def test_bridge_call_methods_are_reachable() -> None:
    """`_call("method")` 里的方法必须真的**能被路由到**。

    这条测试是补上一次真实的 bug：`get_iframes` 实现被加进了 `mainWorldExecutor`，
    但 `handleCommand` 的路由表里漏了它，于是掉进 `default:` 分支，被 domExecutor
    报成"未知 DOM 命令"。只查"方法名在文件里出现过"的上一版测试完全放过了它。

    可达路径有两条：
    - `handleCommand` 里显式 `case "xxx"` → 转发给 mainWorldExecutor；
    - `handleCommand` 没写，落进 `default:` → 转发给 domExecutor。

    所以 mainWorldExecutor 实现的方法必须在路由表里显式列出，否则永远不会被调用。
    """
    source = _read(BACKGROUND_JS)
    called = set(BRIDGE_CALL_RE.findall(_read(PACKAGE / "bridge.py")))
    called.discard("ping_server")

    routed = _routing_cases(source, "handleCommand")
    reachable_via_default = _routing_cases(source, "domExecutor")
    main_world = _routing_cases(source, "mainWorldExecutor")

    unreachable = sorted(called - routed - reachable_via_default)
    assert not unreachable, (
        "以下方法 Python 侧会调用，但 handleCommand 的路由表里没有它、"
        "domExecutor 也不处理 —— 调用只会得到「未知 DOM 命令」：\n" + "\n".join(unreachable)
    )

    unrouted = sorted(main_world - routed)
    assert not unrouted, (
        "以下方法由 mainWorldExecutor 实现，但没有列进 handleCommand 的路由表，"
        "会掉进 default 分支（那是给 domExecutor 的），实际永远调用不到：\n" + "\n".join(unrouted)
    )


def test_extension_has_no_orphan_methods() -> None:
    """反向检查：扩展实现的桥接方法都应该有调用方（否则多半是改名后的残留）。"""
    implemented = set(EXTENSION_CASE_RE.findall(_read(BACKGROUND_JS)))
    called = set(BRIDGE_CALL_RE.findall(_read(PACKAGE / "bridge.py")))

    orphans = sorted(implemented - called)
    assert not orphans, (
        "扩展实现了但没有任何调用方的方法"
        "（若是改名残留请清理，若确实需要请在 bridge.py 里补上调用）：\n" + "\n".join(orphans)
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
        ("scripts/bilibili/bridge.py", bridge_ports),
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


def test_bridge_port_does_not_collide_with_sibling_skills() -> None:
    """端口不能和同目录下的姊妹技能撞车。

    三个技能各自装一个浏览器扩展，端口撞了会导致命令被路由到错误的站点 ——
    表现是"扩展明明连上了，元素却全部找不到"，极难排查。
    """
    our_port = WS_PORT_RE.findall(_read(PACKAGE / "bridge.py"))[0]

    siblings = ROOT.parent
    collisions: list[str] = []
    if siblings.is_dir():
        for other in sorted(siblings.iterdir()):
            if not other.is_dir() or other.resolve() == ROOT.resolve():
                continue
            for path in other.rglob("extension/background.js"):
                ports = set(WS_PORT_RE.findall(_read(path)))
                if our_port in ports:
                    collisions.append(f"{other.name} 也使用 {our_port}")

    assert not collisions, "bridge 端口与姊妹技能冲突：\n" + "\n".join(collisions)


def test_selector_candidates_list_points_at_real_selectors() -> None:
    """`SELECTOR_CANDIDATES` 必须指向真实的 CSS 选择器候选列表。

    probe 会拿这个清单去页面里逐个 querySelector，所以清单里只能放真正的选择器：
    放进文案常量（如 PUBLISH_BUTTON_TEXTS）会让 probe 抛出选择器语法错误，
    排查改版时反而多一个报错。
    """
    from bilibili import selectors

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
    from bilibili import selectors

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


def test_js_templates_escape_their_braces() -> None:
    """注入页面的 JS 模板必须能安全地 `.format()` 出来。

    这些模板里有大量 JS 花括号，而参数是用 `str.format()` 注入的 —— 少转义一个
    `}` 就直接抛 ValueError，多转义一个则会生成 `{{` 这样的语法错误 JS，
    两种都只在"真机上点到那一步"时才炸。这里一次性把两种都查掉。
    """
    from bilibili import dom

    rendered = {
        "_MARK_BY_JS": dom._MARK_BY_JS.format(mark="data-x", finder="return null;"),
        "_FIND_BY_TEXT": dom._FIND_BY_TEXT.format(texts="[]", exact="false", pool="'x'"),
    }

    for name, js in rendered.items():
        assert "{{" not in js and "}}" not in js, (
            f"{name} 渲染后仍残留转义花括号，生成的 JS 会有语法错误：\n{js[:200]}"
        )

    # 反向确认：finder 是"被替换进去的值"，它自己带的 JS 花括号不应该被当成占位符。
    # 这正是 `_find_button` / `_find_video_input` 依赖的性质。
    marker = dom._MARK_BY_JS.format(mark="data-x", finder="if (a) { b(); } return null;")
    assert "if (a) { b(); }" in marker


def test_click_is_guarded_against_overlays_and_offscreen() -> None:
    """真实点击前必须拦住两种"点了等于没点"的情况。

    真实鼠标事件只认坐标、不认"你想点谁"，两种静默失效都真实踩过：

    - **被遮挡**：目标被弹窗/浮层盖住，同样的坐标打在浮层上 —— 页面毫无反应，
      实际上点了别的东西（分区面板没关，点标签框点到了面板条目，把分区改掉了）。
    - **坐标在视口外**：元素在一个没滚到位的可滚动容器里（B站分区面板就是
      「外层 absolute + 内层 overflow:auto」），点击被浏览器直接丢弃。而且
      `elementFromPoint` 会返回 null，旧代码写了 `if (hit && ...)`，等于把这种
      情况整个放过去了。

    所以两条检查都必须存在，而且都必须在派发点击**之前**执行。
    """
    body = _function_source(_read(BACKGROUND_JS), "cmdClickViaDebugger")

    assert "pos.blocked" in body, "cmdClickViaDebugger 里缺少遮挡检查（pos.blocked）"
    assert "pos.offscreen" in body, (
        "cmdClickViaDebugger 里缺少「坐标在视口外」的检查（pos.offscreen）"
    )
    # 只判 hit 非空是不够的：hit 为 null 时同样不能点
    assert "!hit" in body, "elementFromPoint 返回 null 的情况没有被拦下（那是「点下去也白点」）"
    for guard in ("pos.blocked", "pos.offscreen"):
        assert body.index(guard) < body.index("_dispatchRealClickAt("), (
            f"{guard} 检查必须在 _dispatchRealClickAt 之前执行，否则已经点错了"
        )


def test_navigation_wait_has_both_guards() -> None:
    """导航等待必须同时防"过早返回"和"白等到超时"。

    这两件事都真实发生过：

    - 过早返回：标签页刚 update 完还是上一次导航的 "complete"，直接放行会让后续
      DOM 查询落在**旧页面**上（"元素找不到，但页面上明明有"）。
    - 白等到超时：导航在注册监听之前就结束了，loading 事件被错过，于是只能等满
      超时。实测一次导航白等 60s，紧接着 wait_for_load 再等 180s —— 240 秒全程
      静默，用户看到的就是"卡死"。

    所以 `sawLoading`（防早返回）和宽限期（防白等）缺一不可。
    """
    source = _read(BACKGROUND_JS)
    body = _function_source(source, "waitForTabComplete")

    assert "sawLoading" in body, "waitForTabComplete 缺少 sawLoading，会过早返回旧页面"
    assert "LOAD_NOT_STARTED_GRACE_MS" in body, (
        "waitForTabComplete 缺少 未观察到 loading 时的宽限期，会一路白等到超时"
    )

    # 目标 URL 与当前相同时必须显式 reload：location.href 赋相同值可能被当成 no-op，
    # 那样页面还是旧状态（带着上次上传的视频和已填文案），脚本却以为已经在新页面上了。
    assert "chrome.tabs.reload" in _function_source(source, "cmdNavigate"), (
        "cmdNavigate 在目标 URL 与当前相同时应显式 reload"
    )


def test_publish_result_is_detected_via_archive_list() -> None:
    """投稿成功的判据必须是稿件列表，不能只看页面文案。

    实测（这条视频就是）：B站 的投稿请求花了 **8.5 分钟**才落地，全程页面停在
    「提交中...」，既不跳转也不弹任何提示。只看页面的话只能得出"未捕获到明确结果"
    的错误结论，而稿件其实早就投出去了 —— 用户收到的是假警报。

    另外默认超时不能太短，B站 提交本来就要几分钟。
    """
    source = _read(PACKAGE / "publish_video.py")

    assert "_find_new_archive" in source, "缺少基于稿件列表的投稿结果判定"
    assert "ARCHIVE_POLL_SECONDS" in source, "稿件列表轮询没有限流，会打爆接口"

    match = re.search(
        r"def click_publish_video_button\(\s*page[^)]*timeout: float = ([\d.]+)", source, re.S
    )
    assert match, "没找到 click_publish_video_button 的 timeout 默认值"
    assert float(match.group(1)) >= 300, (
        f"click-publish 默认超时只有 {match.group(1)}s，B站 提交可能要几分钟，会误报失败"
    )


def test_declaration_is_wired_into_publish_gate() -> None:
    """创作声明是必填项，必须真的能设置、且纳入提交前检查。

    实测不设声明时点「立即投稿」会被 B站 拦下，用户看到的是"按钮点了没反应" ——
    所以 `assert_publish_ready` 必须查它，否则又回到"没有解释的卡住"。
    """
    from bilibili import selectors

    assert selectors.DECLARATION_TRIGGER_SELECTORS, "没有定义创作声明控件"
    assert "DECLARATION_TRIGGER_SELECTORS" in selectors.SELECTOR_CANDIDATES, (
        "创作声明控件没进 SELECTOR_CANDIDATES，probe 就不会报告它的命中情况"
    )

    source = _read(PACKAGE / "publish_video.py")
    assert "_read_declaration" in source and "_set_declaration" in source, "创作声明没有实现"

    start = source.index("def assert_publish_ready")
    rest = source[start + 10 :]
    ready_body = rest[: rest.index("\ndef ")]
    assert "_read_declaration" in ready_body, "assert_publish_ready 没有检查创作声明"

    # 面板开合必须看尺寸：收起时 ul.bcc-select-option-list 仍在 DOM 里（height 为 0），
    # 用 has_element 判断等于永远为真，后面的"点选项"会落在 0 高度元素上。
    panel_body = source[
        source.index("def _declaration_panel_open") : source.index("def _set_declaration")
    ]
    assert "getBoundingClientRect" in panel_body, (
        "创作声明的面板开合判断必须看尺寸，不能只看 DOM 里有没有"
    )

    assert "--declaration" in _read(SCRIPTS / "cli.py"), "CLI 没有暴露 --declaration"


def test_tag_verification_is_scoped_to_selected_region() -> None:
    """标签校验必须限定在「已选标签」区域内。

    标签区旁边就是「推荐标签」列表，两者文字会重合。只在全页搜文本的话，
    推荐里有同名条目就会被当成"已经加上了"（实测误报），而已选中的标签反而查不到
    （实测漏报）。所以区域限定不能少。
    """
    from bilibili import selectors

    assert selectors.TAG_SELECTED_REGION_SELECTORS, "没有定义已选标签区域"
    region_hits = [s for s in selectors.TAG_SELECTED_REGION_SELECTORS if "tag-pre-wrp" in s]
    assert region_hits, "已选标签区域应包含实测确认过的 .tag-pre-wrp"

    # 实测确认的条目类名是 label-item-v2-content（BCC 的 label 组件）；
    # 用 tag-item / tag-list 会命中推荐标签，属于已知错误写法。
    joined = " ".join(selectors.TAG_ITEM_SELECTORS)
    assert "label-item" in joined, "TAG_ITEM_SELECTORS 应指向 label-item 系列类名"
    assert "tag-list" not in joined, "TAG_ITEM_SELECTORS 不应包含 tag-list（那是推荐标签）"


def test_spawned_processes_do_not_inherit_stdout() -> None:
    """cli.py 里拉起的常驻进程不能继承 CLI 的标准输出。

    bridge server 和 Chrome 都是长期存活的进程。只要有一个继承了 stdout，CLI 自己
    退出之后管道依然不会关闭，于是 `cli.py ... | tail`、IDE 的输出捕获、任何按行读
    输出的调用方都会**永远等下去** —— 表现为"命令卡死"，而 CLI 其实早就跑完了。

    这个坑真实踩过（第一次跑 check-login 就中招），所以专门守一条。
    """
    source = _read(SCRIPTS / "cli.py")
    tree = ast.parse(source)

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def enclosing_function(node: ast.AST) -> ast.AST | None:
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur
            cur = parents.get(cur)
        return None

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"):
            continue

        keywords = {kw.arg for kw in node.keywords}
        if "stdout" in keywords:
            continue
        # 允许用 `**kwargs` 传，但那份 dict 必须就在同一个函数里、且含 "stdout"
        if None in keywords:
            owner = enclosing_function(node)
            body = ast.get_source_segment(source, owner) if owner is not None else ""
            if body and '"stdout"' in body:
                continue

        problems.append(f"cli.py:{node.lineno} subprocess.Popen 没有重定向 stdout")

    assert not problems, (
        "拉起的进程会继承 CLI 的 stdout，导致调用方永远等不到管道关闭（看起来像卡死）：\n"
        + "\n".join(problems)
    )


def test_documented_cli_commands_exist() -> None:
    """文档里出现的 CLI 子命令必须真实存在于 cli.py。

    文档是 Agent 唯一的执行依据：写着一个不存在的子命令，Agent 会照抄执行并拿到
    argparse 的报错，然后开始自己发挥 —— 这正是"技能边界"最容易被绕过的地方。
    """
    registered = set(SUBCOMMAND_RE.findall(_read(SCRIPTS / "cli.py")))

    docs = [ROOT / "SKILL.md", ROOT / "README.md", *sorted((ROOT / "skills").glob("*/SKILL.md"))]
    problems: list[str] = []
    for doc in docs:
        for command in sorted(set(DOC_COMMAND_RE.findall(_read(doc)))):
            if command in registered or command in DOC_COMMAND_ALLOWLIST:
                continue
            problems.append(f"{doc.relative_to(ROOT)}: 提到了 cli.py {command}，但它不是子命令")

    assert not problems, "文档与 CLI 不一致：\n" + "\n".join(problems)
