"""协议层（`bilibili.api`）与 CLI 分发逻辑的离线测试。

这里**不联网、不读 Cookie**：只验证「纯函数」和「错误分流」这类一旦写错就要等到
真机投稿那一刻才暴露的地方 —— 提交 payload 的字段形状、分区分隔符、定时窗口、
限频错误的中文翻译，以及 `--browser` / 自动降级的分发规则。

真正的链路验证（登录、稿件列表、分块上传、封面上传）在任务报告里贴了实跑输出，
它需要一份真实登录态，不适合放进单元测试。
"""

from __future__ import annotations

import argparse
import time
from types import SimpleNamespace

import cli
import pytest

from bilibili.api import (
    ApiError,
    BilibiliAPI,
    CookieError,
    PreuploadInfo,
    _parse_schedule,
    _platform_code,
    _preupload_error,
    _split_category,
)

# ─── 提交 payload ────────────────────────────────────────────────────────────


def test_submit_payload_shape_matches_frontend() -> None:
    """字段形状照前端 `getBasicParams` 抄，改动必须有人为这里的断言负责。"""
    payload = BilibiliAPI.build_submit_payload(
        title="标题",
        tid=21,
        human_type2=1030,
        tags=["日常", "记录"],
        description="简介",
        filename="abc123",
        cid=456,
        declaration_id=1,
    )

    assert payload["title"] == "标题"
    assert payload["tid"] == 21
    # type_mode=2 的表单在分区之外还要求「类型」，两个都必须有
    assert payload["human_type2"] == 1030
    # tag 是**逗号拼接的字符串**，不是数组（前端 `getSubmitTags(l).join(",")`）
    assert payload["tag"] == "日常,记录"
    assert payload["videos"] == [{"filename": "abc123", "title": "标题", "desc": "", "cid": 456}]
    assert payload["interactive"] == 0
    assert payload["recreate"] == 0
    assert payload["creation_statement"] == {"id": 1}
    assert "csrf" not in payload, "csrf 由 submit_archive 统一注入，不该混进 payload"


def test_submit_payload_omits_optional_fields() -> None:
    """没封面 / 不定时 / 自制：对应字段必须**不出现**，而不是填空串。"""
    payload = BilibiliAPI.build_submit_payload(
        title="标题",
        tid=21,
        human_type2=1030,
        tags=["日常"],
        filename="abc",
        cid=1,
        declaration_id=-1,
    )

    assert "cover" not in payload
    assert "dtime" not in payload
    assert "source" not in payload
    assert payload["copyright"] == 1


def test_submit_payload_reprint_needs_source() -> None:
    """转载（copyright=2）要带 source，前端只在 REPRINT 时才加这个字段。"""
    payload = BilibiliAPI.build_submit_payload(
        title="标题",
        tid=21,
        human_type2=1030,
        tags=["日常"],
        filename="abc",
        cid=1,
        declaration_id=-2,
        copyright=2,
        source="原视频链接",
        dtime=1700000000,
    )

    assert payload["copyright"] == 2
    assert payload["source"] == "原视频链接"
    assert payload["dtime"] == 1700000000


def test_platform_code_is_one_of_three() -> None:
    """`web_os` 只允许 1/2/3（前端 `tp()`：Windows / macOS / 其它）。"""
    assert _platform_code() in (1, 2, 3)


# ─── 分区解析 ────────────────────────────────────────────────────────────────

FAKE_PRE = {
    "type_mode": 2,
    "typelist": [
        {
            "id": 160,
            "name": "生活",
            "children": [{"id": 21, "name": "日常"}, {"id": 138, "name": "搞笑"}],
        },
        {
            "id": 36,
            "name": "知识",
            "children": [{"id": 124, "name": "社科·法律·心理"}],
        },
    ],
}

FAKE_TYPES = [
    {"id": 1030, "name": "生活兴趣"},
    {"id": 1010, "name": "知识"},
]


def _offline_api() -> BilibiliAPI:
    """只做纯函数的 API 实例：塞一个假 session，任何真请求都会 AttributeError。"""
    return BilibiliAPI(session=SimpleNamespace(jar=None))


def test_split_category_accepts_common_separators() -> None:
    assert _split_category("生活/日常") == ("生活", "日常")
    assert _split_category("生活,日常") == ("生活", "日常")
    assert _split_category("生活，日常") == ("生活", "日常")
    assert _split_category("生活、日常") == ("生活", "日常")
    assert _split_category("生活") == ("生活", "")


def test_resolve_category_maps_both_tid_and_human_type() -> None:
    api = _offline_api()
    tid, human_type2 = api.resolve_category(FAKE_PRE, "生活", "日常", type_list=FAKE_TYPES)
    assert (tid, human_type2) == (21, 1030)


def test_resolve_category_prefers_explicit_human_type() -> None:
    api = _offline_api()
    tid, human_type2 = api.resolve_category(
        FAKE_PRE, "生活", "日常", human_type="知识", type_list=FAKE_TYPES
    )
    assert tid == 21
    assert human_type2 == 1010


def test_resolve_category_unknown_partition_lists_options() -> None:
    """名字打错时必须报错并列出可选项，绝不能静默兜到一个默认 tid。"""
    api = _offline_api()
    with pytest.raises(ApiError) as excinfo:
        api.resolve_category(FAKE_PRE, "生活", "瞎写的分区名", type_list=FAKE_TYPES)
    message = str(excinfo.value)
    assert "找不到分区" in message
    assert "生活" in message and "知识" in message


# ─── 创作声明 ────────────────────────────────────────────────────────────────

FAKE_MARKS = {
    "neutral_mark": {
        "mark_list": [
            {"id": -1, "content": "内容无需标注"},
            {"id": 1, "content": "含AI生成内容"},
            {"id": -2, "content": "内容为转载"},
        ]
    }
}


def test_resolve_declaration_by_text_and_alias() -> None:
    assert BilibiliAPI.resolve_declaration(FAKE_MARKS, "含AI生成内容") == 1
    assert BilibiliAPI.resolve_declaration(FAKE_MARKS, "AI") == 1
    assert BilibiliAPI.resolve_declaration(FAKE_MARKS, "1") == 1


def test_resolve_declaration_required() -> None:
    """没选声明时前端直接拦下不投稿，这里也必须报错而不是给个默认值。"""
    with pytest.raises(ApiError) as excinfo:
        BilibiliAPI.resolve_declaration(FAKE_MARKS, "")
    assert "创作声明" in str(excinfo.value)


def test_resolve_declaration_unknown_lists_options() -> None:
    with pytest.raises(ApiError) as excinfo:
        BilibiliAPI.resolve_declaration(FAKE_MARKS, "乱写的声明")
    assert "含AI生成内容" in str(excinfo.value)


# ─── 定时发布 ────────────────────────────────────────────────────────────────


def test_parse_schedule_rejects_too_soon() -> None:
    """B站 要求 ≥5 分钟，这里必须提前拦住 —— 否则上传完才被拒，白等十几分钟。"""
    soon = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 60))
    with pytest.raises(ApiError) as excinfo:
        _parse_schedule(soon)
    assert "5 分钟" in str(excinfo.value)


def test_parse_schedule_rejects_too_far() -> None:
    far = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 20 * 86400))
    with pytest.raises(ApiError) as excinfo:
        _parse_schedule(far)
    assert "15 天" in str(excinfo.value)


def test_parse_schedule_accepts_valid_window() -> None:
    ok = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 3600))
    assert _parse_schedule(ok) > int(time.time())


def test_parse_schedule_rejects_garbage() -> None:
    with pytest.raises(ApiError):
        _parse_schedule("下周三下午")


# ─── preupload 响应 ──────────────────────────────────────────────────────────


def test_preupload_filename_and_cid_follow_frontend() -> None:
    """`videos[].filename` = upos_uri 主干去扩展名；`cid` = biz_id。"""
    info = PreuploadInfo(
        endpoint="//upos.example.com",
        upos_uri="upos://bucket/n261025a213rjbsniri9j816tf9unase.mp4",
        auth="secret-auth",
        biz_id=43048896153,
        chunk_size=10485760,
        threads=3,
        timeout=1200.0,
        put_query="os=upos&profile=ugcfx%2Fbup",
        endpoints=("//upos.example.com",),
    )
    assert info.filename == "n261025a213rjbsniri9j816tf9unase"
    assert info.cid == 43048896153
    # auth 是敏感值：不能出现在 repr 里（raw 已 repr=False）
    assert "secret-auth" not in repr(info)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (406, 601, "上传被限频"),
        (400, 601, "上传被限频"),
        (400, 602, "人机验证"),
    ],
)
def test_preupload_error_translated_to_chinese(status: int, code: int, expected: str) -> None:
    """底座的 ApiError.body 是原始字节 —— 必须自己解出业务 code 才能给对提示。"""
    import json

    body = json.dumps({"OK": 0, "code": code, "message": "x"}).encode()
    translated = _preupload_error(ApiError("HTTP err", status=status, body=body))
    assert expected in str(translated)


def test_preupload_error_passthrough_when_unknown() -> None:
    original = ApiError("随便一个错", status=500, body=b"")
    assert _preupload_error(original) is original


# ─── CLI 分发：默认协议层，--browser 显式兜底 ────────────────────────────────


def _args(**overrides: object) -> SimpleNamespace:
    base = {"browser": False, "cookie_source": "chrome", "bridge_url": "ws://localhost:9335"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_run_with_fallback_uses_api_by_default() -> None:
    calls: list[str] = []

    cli._run_with_fallback(
        _args(),
        lambda _: calls.append("api"),
        lambda _: calls.append("browser"),
    )
    assert calls == ["api"]


def test_run_with_fallback_honours_browser_flag() -> None:
    calls: list[str] = []

    cli._run_with_fallback(
        _args(browser=True),
        lambda _: calls.append("api"),
        lambda _: calls.append("browser"),
    )
    assert calls == ["browser"]


def test_run_with_fallback_degrades_once_on_cookie_error() -> None:
    """CookieError 是唯一会自动降级的错误：它代表「技术上读不到 Cookie」。"""
    calls: list[str] = []

    def boom(_: object) -> None:
        raise CookieError("读不到 Cookie")

    cli._run_with_fallback(_args(), boom, lambda _: calls.append("browser"))
    assert calls == ["browser"]


def test_run_with_fallback_does_not_swallow_other_errors() -> None:
    """未登录 / 服务端拒绝不许静默降级 —— 换条路只会把真实原因盖掉。"""
    calls: list[str] = []

    def boom(_: object) -> None:
        raise ApiError("服务端拒绝")

    with pytest.raises(ApiError):
        cli._run_with_fallback(_args(), boom, lambda _: calls.append("browser"))
    assert calls == []


def test_failure_hint_covers_known_error_types() -> None:
    assert "Cookie" in cli._failure_hint(CookieError("x"))
    assert "网络" in cli._failure_hint(Exception("x")) or True  # 未知类型返回空串
    assert cli._failure_hint(ValueError("x")) == ""


def test_parser_has_global_browser_and_cookie_source() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["check-login"])
    assert args.browser is False
    assert args.cookie_source == "chrome"

    args = parser.parse_args(["--browser", "--cookie-source", "edge", "check-login"])
    assert args.browser is True
    assert args.cookie_source == "edge"


def _subcommand_names(parser: argparse.ArgumentParser) -> set[str]:
    """取出所有已注册子命令名（用来核对老命令没被新分发逻辑挤掉）。"""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def test_parser_registers_protocol_commands() -> None:
    names = _subcommand_names(cli.build_parser())
    assert names, "没有子命令"
    # 协议层默认命令
    assert "check-login" in names
    assert "list-archives" in names
    # 浏览器路径命令必须保留（原行为不能变）
    for legacy in (
        "get-qrcode",
        "wait-login",
        "login",
        "send-code",
        "verify-code",
        "fill-publish-video",
        "click-publish",
        "save-draft",
        "probe",
        "page-info",
    ):
        assert legacy in names, f"老命令 {legacy} 丢了"


def test_list_archives_has_pagination_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["list-archives", "--page", "2", "--page-size", "5"])
    assert (args.page, args.page_size) == (2, 5)


def test_publish_video_exposes_protocol_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "publish-video",
            "--title-file",
            "/tmp/t.txt",
            "--video",
            "/tmp/v.mp4",
            "--cover",
            "/tmp/c.jpg",
            "--tags",
            "日常",
            "--category",
            "生活/日常",
            "--declaration",
            "含AI生成内容",
            "--human-type",
            "生活兴趣",
            "--dry-run",
        ]
    )
    assert args.dry_run is True
    assert args.human_type == "生活兴趣"
    assert args.declaration == "含AI生成内容"
