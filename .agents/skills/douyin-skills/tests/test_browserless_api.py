"""无浏览器 API 层与 CLI 模式分流的离线测试（不打任何网络请求）。

这些测试**刻意不联网**：CI、别人的机器、Cookie 过期的机器上都应该能跑。它们锁住的是
「接线是否正确」，也就是几类只在真机上跑到某一步才暴露的问题：

1. 子命令该走 API 却走了浏览器（或反过来）—— 表现为莫名其妙地开浏览器、或直接失败；
2. 发布类子命令没加 ``--browser`` 却没被拦住 —— 会静默去起浏览器；
3. 发布 / 草稿 / 上传在无浏览器模式下返回「成功」—— 这是最严重的：用户会以为发出去了；
4. Cookie 读不到时没有降级提示；
5. 把未登录（HTTP 200 + status_code 8）误当成成功；
6. 接口路径被随手改成猜的端点。

真实联网的验证（真昵称、真作品列表）在 SKILL.md 的实测表里，由人跑，不放进单测。
"""

from __future__ import annotations

import argparse
import ast
import sys

import cli
import pytest

from douyin import api as api_module
from douyin.api import DouyinApi
from douyin.errors import NotLoggedInError, SignatureRequiredError

# ─── 测试替身 ────────────────────────────────────────────────────────────────


class _FakeSession:
    """记录请求、按预置表返回假响应；不碰网络、不碰 Cookie。"""

    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._responses = responses or {}

    def _payload(self, url: str) -> dict:
        for suffix, payload in self._responses.items():
            if suffix in url:
                return payload
        return {"status_code": 0}

    def get_json(self, url: str) -> dict:
        self.calls.append(("GET", url))
        return self._payload(url)

    def post_json(self, url: str, data: dict | None = None) -> dict:
        self.calls.append(("POST", url))
        return self._payload(url)


def _api_with(session: _FakeSession) -> DouyinApi:
    """构造一个不读浏览器 Cookie 的 DouyinApi（直接把 session 塞进去）。"""
    api = DouyinApi()
    api._session = session
    return api


def _namespace(**kwargs) -> argparse.Namespace:
    base = {"command": "check-login", "browser": False, "bridge_url": "ws://localhost:9334"}
    base.update(kwargs)
    return argparse.Namespace(**base)


# ─── 模式分流 ────────────────────────────────────────────────────────────────


def test_api_commands_do_not_use_browser() -> None:
    """登记在 _API_COMMANDS 的子命令，默认都不该走浏览器。"""
    for command in sorted(cli._API_COMMANDS):
        args = _namespace(command=command)
        assert cli._use_browser(args) is False, f"{command} 默认走了浏览器"


def test_unknown_command_falls_back_to_browser() -> None:
    """未登记的新子命令必须落到原来的 bridge 行为，不能被误判成「已支持无浏览器」。"""
    args = _namespace(command="some-future-command")
    assert cli._use_browser(args) is True


def test_browser_flag_forces_browser_mode() -> None:
    """--browser 对 API 子命令也必须生效。"""
    args = _namespace(command="check-login", browser=True)
    assert cli._use_browser(args) is True


def test_bridge_port_unchanged_by_browser_flag() -> None:
    """加了 --browser 之后 CLI 仍然指向同一个 bridge 端口（静态一致性测试的补充）。"""
    parser = cli.build_parser()
    args = parser.parse_args(["check-login", "--browser"])
    assert args.bridge_url == "ws://localhost:9334"


def test_browser_flag_accepted_before_and_after_subcommand() -> None:
    """--browser 写在子命令前后都要能识别（argparse 全局选项默认不支持后者）。"""
    parser = cli.build_parser()
    for argv in (["--browser", "check-login"], ["check-login", "--browser"]):
        args = parser.parse_args(argv)
        args.browser = bool(getattr(args, "browser", False))
        assert args.browser is True, f"{argv} 里的 --browser 没生效"
        assert cli._use_browser(args) is True


def test_every_browser_only_command_has_a_reason() -> None:
    """浏览器专属子命令必须写明原因 —— 否则提示信息会退化成「不知道为什么」。"""
    for command, reason in cli._BROWSER_ONLY_COMMANDS.items():
        assert isinstance(reason, str) and reason.strip(), f"{command} 没有写原因"


def test_browser_only_commands_are_not_in_api_commands() -> None:
    """同一个子命令不能既登记成 API 又登记成浏览器专属（否则行为取决于判断顺序）。"""
    overlap = set(cli._BROWSER_ONLY_COMMANDS) & set(cli._API_COMMANDS)
    assert not overlap, f"同时登记在两张表里：{sorted(overlap)}"


def test_browser_only_registry_covers_all_registered_commands() -> None:
    """所有子命令都必须被登记：要么在 API 表，要么在浏览器专属表。

    这样新增子命令时漏登记会立刻被抓住，而不是默认落进某个不确定的分支。
    """
    parser = cli.build_parser()
    registered: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            registered = set(action.choices)

    known = set(cli._API_COMMANDS) | set(cli._BROWSER_ONLY_COMMANDS)
    unregistered = sorted(registered - known)
    assert not unregistered, f"以下子命令没有登记 API/浏览器归属：{unregistered}"

    stale = sorted(known - registered)
    assert not stale, f"登记了但 parser 里不存在的子命令（改名残留？）：{stale}"


# ─── 发布类必须被拦住 & 绝不假成功 ──────────────────────────────────────────


@pytest.mark.parametrize("command", ["publish-video", "save-draft", "click-publish"])
def test_publish_commands_blocked_without_browser_flag(command: str, capsys) -> None:
    """发布类子命令没加 --browser 必须立刻 exit 2，而不是悄悄去起浏览器。"""
    argv = [command]
    if command == "publish-video":
        argv += ["--video", "/tmp/definitely-not-a-real-video.mp4"]

    parser = cli.build_parser()
    args = parser.parse_args(argv)
    args.browser = bool(getattr(args, "browser", False))

    with pytest.raises(SystemExit) as exc:
        cli._require_browser_mode(args)
    assert exc.value.code == 2

    out = capsys.readouterr().out
    assert "必须使用浏览器模式" in out
    assert "--browser" in out
    assert "requiresBrowser" in out


def test_publish_api_raises_instead_of_faking_success() -> None:
    """publish / draft / upload 在 API 模式下必须抛 SignatureRequiredError。

    这是整个改造最重要的一条：宁可失败，也绝不返回假成功。
    """
    api = DouyinApi()
    for method in ("publish_video", "save_draft", "upload_video"):
        with pytest.raises(SignatureRequiredError):
            getattr(api, method)()


def test_publish_api_raises_before_any_network_call() -> None:
    """必须在发请求之前就失败：否则视频已传到 VOD，留下孤儿对象。"""
    session = _FakeSession()
    api = _api_with(session)
    with pytest.raises(SignatureRequiredError):
        api.publish_video(title="t", video_path="/tmp/x.mp4")
    assert session.calls == [], f"抛错前不该发出任何请求，实际发了：{session.calls}"


def test_upload_video_does_not_upload() -> None:
    """upload_video 单独被调用时也不能真传文件。"""
    session = _FakeSession()
    api = _api_with(session)
    with pytest.raises(SignatureRequiredError):
        api.upload_video(path="/tmp/x.mp4")
    assert session.calls == []


def test_signature_error_message_is_actionable() -> None:
    """报错信息要能直接告诉用户怎么办（加 --browser），而不是只有一句「失败」。"""
    api = DouyinApi()
    with pytest.raises(SignatureRequiredError) as exc:
        api.publish_video()
    message = str(exc.value)
    assert "--browser" in message
    assert "create_v2" in message


# ─── 业务码解析 ─────────────────────────────────────────────────────────────


def test_not_logged_in_status_code_8_becomes_not_logged_in() -> None:
    """平台用 HTTP 200 + status_code 8 表示未登录，不能当成功。"""
    session = _FakeSession(
        {"/web/api/media/user/info/": {"status_code": 8, "status_msg": "用户未登录"}}
    )
    api = _api_with(session)
    with pytest.raises(NotLoggedInError):
        api.check_login()


def test_other_error_code_becomes_api_error() -> None:
    """非 0 且非 8 的业务码要带上原始 status_msg，便于排查。"""
    session = _FakeSession({"item/list": {"status_code": 5, "status_msg": "参数不合法"}})
    api = _api_with(session)
    with pytest.raises(api_module.DouyinApiError) as exc:
        api.list_works()
    assert "参数不合法" in str(exc.value)


def test_check_login_returns_nickname_from_api() -> None:
    """check_login 要把昵称透出来，并标记 mode=api。"""
    session = _FakeSession(
        {
            "/web/api/media/user/info/": {
                "status_code": 0,
                "user": {"nickname": "测试昵称", "uid": "1", "sec_uid": "s"},
            }
        }
    )
    result = _api_with(session).check_login()
    assert result["logged_in"] is True
    assert result["nickname"] == "测试昵称"
    assert result["mode"] == "api"


def test_list_works_requires_count_and_order_by() -> None:
    """count / order_by 是必传参数：少了平台会返回 status_code 5。"""
    session = _FakeSession({"item/list": {"status_code": 0, "items": []}})
    _api_with(session).list_works(count=7, order_by=1)
    _, url = session.calls[0]
    assert "count=7" in url
    assert "order_by=1" in url


def test_list_works_normalizes_items() -> None:
    """作品字段要收敛成稳定结构，不把平台的临时字段直出给调用方。"""
    session = _FakeSession(
        {
            "item/list": {
                "status_code": 0,
                "total": 1,
                "has_more": False,
                "items": [
                    {
                        "id": "123",
                        "description": "d",
                        "create_time": 1700000000,
                        "type": 3,
                        "review": {"status": 2},
                        "video_info": {"duration": 1000},
                        "cover": {"url_list": ["https://example.invalid/c.jpg"]},
                    }
                ],
            }
        }
    )
    result = _api_with(session).list_works()
    item = result["items"][0]
    assert item["id"] == "123"
    assert item["review_status"] == 2
    assert item["duration_ms"] == 1000
    assert item["cover"] == "https://example.invalid/c.jpg"


# ─── Cookie 缺失降级 ────────────────────────────────────────────────────────


def test_cookie_error_falls_back_to_browser_once(monkeypatch) -> None:
    """读不到 Cookie 时自动降级到浏览器模式重试一次，并打印中文提示。"""
    from browserless.chromium import CookieError

    called: list[str] = []

    def boom(_api):
        raise CookieError("读不到 Chrome 的 Cookie（测试）")

    def fallback(_args):
        called.append("fallback")
        cli._output({"logged_in": True, "mode": "browser"}, exit_code=0)

    monkeypatch.setattr(api_module.DouyinApi, "__init__", lambda self, *a, **k: None)

    with pytest.raises(SystemExit) as exc:
        cli._run_api(_namespace(), boom, fallback=fallback)

    assert exc.value.code == 0
    assert called == ["fallback"], "应该降级到浏览器模式恰好一次"


def test_cookie_error_without_fallback_exits_2(monkeypatch, capsys) -> None:
    """没有可用降级路径时，必须 exit 2 并给出排查提示。"""
    from browserless.chromium import CookieError

    def boom(_api):
        raise CookieError("读不到 Cookie（测试）")

    with pytest.raises(SystemExit) as exc:
        cli._run_api(_namespace(), boom)
    assert exc.value.code == 2

    out = capsys.readouterr().out
    assert "无法从本机浏览器读取抖音 Cookie" in out
    assert "--browser" in out


def test_cookie_error_does_not_fallback_when_browser_flag_set(monkeypatch) -> None:
    """显式 --browser 时不再降级（否则会无限绕），直接报错 exit 2。"""
    from browserless.chromium import CookieError

    def boom(_api):
        raise CookieError("读不到 Cookie（测试）")

    def fallback(_args):  # pragma: no cover - 不该被调用
        raise AssertionError("--browser 模式下不应再次降级")

    with pytest.raises(SystemExit) as exc:
        cli._run_api(_namespace(browser=True), boom, fallback=fallback)
    assert exc.value.code == 2


def test_not_logged_in_does_not_fall_back(monkeypatch) -> None:
    """「未登录」不该降级到浏览器 —— 那该让用户去登录，而不是偷偷开浏览器。"""

    def boom(_api):
        raise NotLoggedInError()

    def fallback(_args):  # pragma: no cover - 不该被调用
        raise AssertionError("未登录时不应降级到浏览器模式")

    with pytest.raises(NotLoggedInError):
        cli._run_api(_namespace(), boom, fallback=fallback)


# ─── 端点路径不能是猜的 ─────────────────────────────────────────────────────


def test_api_paths_are_the_verified_ones() -> None:
    """端点路径必须是逆查 bundle 得到的那几个；改名/臆造会在这里暴露。"""
    paths = set(api_module.iter_paths())
    expected = {
        "/web/api/media/user/info/",
        "/aweme/v1/creator/user/info/",
        "/web/api/creator/item/list",
        "/web/api/media/upload/config",
        "/web/api/media/upload/auth/v5/",
        "/web/api/media/aweme/create_v2/",
        "/web/api/media/aweme/draft",
    }
    assert paths == expected


def _api_source() -> str:
    """读 douyin/api.py 源码（用 context manager，避免句柄泄漏）。"""
    path = api_module.__file__
    assert path, "拿不到 douyin/api.py 的路径"
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_api_module_does_not_import_browser_bridge() -> None:
    """分层纪律：API 模块不许 import 本技能自己的浏览器模块（bridge / publish_video）。"""
    source = _api_source()
    forbidden = ("from .bridge", "import bridge", "from .publish_video", "import publish_video")
    hits = [token for token in forbidden if token in source]
    assert not hits, f"douyin/api.py 不该依赖浏览器模块，命中：{hits}"


def test_api_module_parses_as_python_39() -> None:
    """3.9 兼容：用 ``ast.parse(feature_version=(3, 9))`` 真解析一遍。

    比"扫字符串找 ``| None``"可靠得多 —— 只有真语法不兼容（如 `match`、`X | Y` 用在运行时
    表达式里）才会失败，而在 ``from __future__ import annotations`` 下的类型注解是合法的。
    """
    source = _api_source()
    assert "from __future__ import annotations" in source
    ast.parse(source, filename="douyin/api.py", feature_version=(3, 9))


def test_cli_module_parses_as_python_39() -> None:
    """cli.py 同样要能在 3.9 上解析（判据是系统 python3 跑 check-login）。"""
    path = cli.__file__
    assert path, "拿不到 cli.py 的路径"
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    ast.parse(source, filename="cli.py", feature_version=(3, 9))


def test_python_version_is_supported() -> None:
    """环境说明里承诺 3.9 可跑；低于 3.9 直接跳过该承诺没有意义。"""
    assert sys.version_info >= (3, 9)
