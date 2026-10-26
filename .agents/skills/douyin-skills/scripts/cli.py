"""抖音自动化 CLI 入口（API 优先，浏览器兜底）

两种模式：

* **API 模式（默认）** —— 只读取本机浏览器里**已登录的 Cookie**，用纯 HTTP 直接请求
  ``creator.douyin.com`` 的接口，**不启动、不接管浏览器**。启动快、无窗口、可放进
  脚本/CI（只要有已登录的 Cookie）。
* **浏览器模式（``--browser``）** —— 通过浏览器扩展 Bridge 连接用户已打开的 Chrome，
  使用真实的登录态和浏览器环境在页面上操作，不使用无头浏览器、不伪造指纹。

哪个子命令走哪条路，见下面 ``_API_COMMANDS`` 与 ``_BROWSER_ONLY_COMMANDS``，
以及 SKILL.md 里那张实测出来的对照表。

为什么不能全部走 API
--------------------
发布 / 存草稿这几条接口在平台前端被标记了 ``needSign: true`` + ``withSecondVerify:
true``，由 secure-consumer-sdk 在浏览器内按设备上下文 + 时间 + 请求体实时计算
``bd-ticket-guard-*`` 签名头。这段逻辑与浏览器环境绑定、且随平台发版迭代，纯 HTTP
无法复现。所以这些子命令**必须** ``--browser``；没加时会立刻以中文提示 + 退出码 2
失败，**绝不会**悄悄起浏览器、更不会返回假的发布成功。

浏览器模式前置条件：
    1. 运行 ``python scripts/bridge_server.py``（CLI 会自动尝试拉起）
    2. 在 Chrome 的 chrome://extensions 中加载本项目的 extension/ 目录
    3. 浏览器中已登录抖音创作服务平台

输出：JSON（ensure_ascii=False）
退出码：0=成功, 1=未登录, 2=错误
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from douyin.types import PublishVideoContent

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("douyin-cli")


# ─── 能力划分：哪些子命令能免浏览器 ──────────────────────────────────────────

#: 默认走纯 HTTP API 的子命令（每一条都实测过，见 SKILL.md 的对照表）。
#: 只有在这里登记过的才走 API —— 新增子命令若忘了登记，会落到原来的 bridge 行为，
#: 不会被误判成「已支持无浏览器」。
_API_COMMANDS: set[str] = {
    "check-login",
    "user-info",
    "creator-info",
    "list-works",
    "upload-preflight",
}

#: 必须浏览器的子命令 → 原因（没加 ``--browser`` 时据此给出明确中文提示）。
_BROWSER_ONLY_COMMANDS: dict[str, str] = {
    "login": "扫码登录需要打开页面读取/轮询二维码状态",
    "get-qrcode": "二维码由页面渲染，需要浏览器",
    "wait-login": "需要轮询页面的登录态",
    "send-code": "发送验证码依赖页面控件",
    "verify-code": "填写验证码依赖页面控件",
    "fill-publish-video": "向页面注入文件、填写表单，依赖真实 DOM 与前端状态",
    "publish-video": (
        "发布接口 /web/api/media/aweme/create_v2/ 在前端标记 needSign=true，"
        "由 secure-consumer-sdk 在浏览器内现算 bd-ticket-guard-* 签名头，纯 HTTP 无法复现"
    ),
    "click-publish": "点击页面上的发布按钮",
    "save-draft": ("草稿接口 /web/api/media/aweme/draft 同样 needSign=true，需要浏览器内签名"),
    "probe": "抓取当前页面的元素结构，必须有页面",
    "page-info": "读取当前标签页信息，必须有页面",
}


# ─── 输出工具 ────────────────────────────────────────────────────────────────


def _output(data: dict, exit_code: int = 0) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _fill_qrcode_result(result: dict, png_bytes: bytes) -> None:
    """把二维码写入本地文件并尽力打开，并把路径写回 JSON 结果。"""
    from douyin.login import make_qrcode_data_url, open_qrcode_file, save_qrcode_to_file

    path = save_qrcode_to_file(png_bytes)
    open_qrcode_file(path)
    result["qrcode_path"] = path
    data_url = make_qrcode_data_url(png_bytes)
    if data_url:
        result["qrcode_image_url"] = data_url


# ─── 模式判定 ────────────────────────────────────────────────────────────────


def _use_browser(args: argparse.Namespace) -> bool:
    """该子命令本次是否走浏览器模式。

    默认是 API；只有 ``_API_COMMANDS`` 里明确实测过能免签名的才走 API。
    """
    if getattr(args, "browser", False):
        return True
    return getattr(args, "command", "") not in _API_COMMANDS


def _require_browser_mode(args: argparse.Namespace) -> None:
    """浏览器专属子命令在没加 ``--browser`` 时报错退出，而不是悄悄去起浏览器。

    这样「我忘了加 --browser」会立刻得到一句明确的中文提示 + exit 2，而不是让用户
    等二十秒看它去开 Chrome、最后还因为缺签名失败。
    """
    command = getattr(args, "command", "")
    reason = _BROWSER_ONLY_COMMANDS.get(command)
    if reason is None or getattr(args, "browser", False):
        return
    _output(
        {
            "success": False,
            "mode": "browser",
            "requiresBrowser": True,
            "reason": reason,
            "error": f"子命令 {command} 必须使用浏览器模式：{reason}。请加 --browser 重试。",
            "hint": f"python scripts/cli.py --browser {command} ...",
        },
        exit_code=2,
    )


def _cookie_error_hint(exc: Exception) -> dict:
    """读不到本机浏览器 Cookie 时的结构化提示（不含任何 Cookie 值）。"""
    return {
        "success": False,
        "mode": "api",
        "error": str(exc),
        "hint": (
            "无法从本机浏览器读取抖音 Cookie（只读、不上传）。请确认："
            "1) 已用 Chrome 登录 https://creator.douyin.com/；"
            "2) 当前系统用户能访问浏览器数据目录；"
            "3) 或用 --browser 走扩展桥接。"
        ),
    }


# ─── API 模式执行 ────────────────────────────────────────────────────────────


def _run_api(
    args: argparse.Namespace,
    action: Callable[[Any], Any],
    *,
    fallback: Callable[[argparse.Namespace], None] | None = None,
) -> Any:
    """在 API 模式下跑一次 ``action(api)``。

    ``action`` 抛 ``CookieError``（读不到本机浏览器 Cookie）时，自动降级到浏览器模式
    重试**一次**；降级也失败则打印中文提示并以 exit 2 结束。

    只对 ``CookieError`` 降级。「未登录」不降级 —— 那该让用户去登录，悄悄起浏览器
    反而更慢更迷惑；「需要签名」也不降级，那必须由用户显式加 ``--browser``，
    否则会在没告知的情况下替用户开浏览器发布作品。
    """
    # 从 douyin.api 导入：它会先把 .agents/lib 加进 sys.path，直接 import
    # browserless.* 会因路径没配好而 ModuleNotFoundError
    from douyin.api import COOKIE_ERRORS, DouyinApi

    try:
        return action(DouyinApi())
    except COOKIE_ERRORS as exc:
        if fallback is not None and not getattr(args, "browser", False):
            logger.warning("读不到浏览器 Cookie，自动降级到浏览器模式重试一次")
            try:
                fallback(args)
            except COOKIE_ERRORS as retry_exc:
                data = _cookie_error_hint(retry_exc)
                data["error"] = f"{exc}；降级到浏览器模式后仍失败：{retry_exc}"
                _output(data, exit_code=2)
            raise  # fallback 正常都会 _output 退出；走到这里说明它没退出
        _output(_cookie_error_hint(exc), exit_code=2)
        raise  # 供类型检查；_output 已 sys.exit


def _api_or_output(
    args: argparse.Namespace,
    action: Callable[[Any], Any],
    *,
    fallback: Callable[[argparse.Namespace], None] | None = None,
) -> None:
    """``_run_api`` 的便捷包装：把 action 的返回值直接输出为成功结果。"""
    _output(_run_api(args, action, fallback=fallback))


# ─── Bridge 连接 ──────────────────────────────────────────────────────────────


class _DummyBrowser:
    """空 browser 对象，保持与旧代码的兼容性。"""

    def close(self) -> None:
        pass

    def close_page(self, page) -> None:
        pass


def _open_chrome() -> None:
    """尝试启动 Chrome 浏览器。"""
    import subprocess

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            subprocess.Popen([path])
            return
    for cmd in [["open", "-a", "Google Chrome"], ["google-chrome"], ["chromium-browser"]]:
        try:
            subprocess.Popen(cmd)
            return
        except FileNotFoundError:
            continue
    logger.warning("找不到 Chrome，请手动打开浏览器")


def _ensure_bridge_ready(bridge_url: str) -> None:
    """确保 bridge server 在运行、浏览器扩展已连接；未就绪时自动启动。"""
    import subprocess
    import time
    from pathlib import Path

    from douyin.bridge import BridgePage

    page = BridgePage(bridge_url)

    if not page.is_server_running():
        logger.info("Bridge server 未运行，正在启动...")
        scripts_dir = Path(__file__).parent
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, str(scripts_dir / "bridge_server.py")], **kwargs)
        for _ in range(10):
            time.sleep(1)
            if page.is_server_running():
                logger.info("Bridge server 已启动")
                break
        else:
            logger.warning("Bridge server 启动超时，请手动运行 bridge_server.py")
            return

    if page.is_extension_connected():
        return

    logger.info("浏览器扩展未连接，正在打开 Chrome...")
    _open_chrome()
    for _ in range(20):
        time.sleep(1)
        if page.is_extension_connected():
            logger.info("浏览器扩展已连接")
            return
    logger.warning("等待扩展连接超时，请确认 Chrome 已加载并启用 Douyin Bridge 扩展")


def _connect(args: argparse.Namespace):
    """返回 (browser, page)。"""
    from douyin.bridge import BridgePage

    bridge_url = getattr(args, "bridge_url", "ws://localhost:9334")
    _ensure_bridge_ready(bridge_url)
    return _DummyBrowser(), BridgePage(bridge_url)


# ─── 认证相关 ────────────────────────────────────────────────────────────────


def cmd_check_login(args: argparse.Namespace) -> None:
    """检查登录状态。默认走 API（不开浏览器）；``--browser`` 时保持原页面流程。"""
    if not _use_browser(args):
        _api_or_output(args, lambda api: api.check_login(), fallback=_check_login_browser)
        return
    _check_login_browser(args)


def _check_login_browser(args: argparse.Namespace) -> None:
    """浏览器模式下的登录检查（原行为，未改动）。"""
    from douyin.login import check_login_status, get_nickname

    browser, page = _connect(args)
    try:
        logged_in = check_login_status(page)
        if not logged_in:
            _output(
                {
                    "logged_in": False,
                    "mode": "browser",
                    "hint": "未登录。运行 `python scripts/cli.py get-qrcode` 获取二维码，"
                    "或 `python scripts/cli.py send-code --phone <手机号>` 走验证码登录。",
                },
                exit_code=1,
            )
        _output(
            {
                "logged_in": True,
                "nickname": get_nickname(page),
                "url": page.get_url(),
                "mode": "browser",
            }
        )
    finally:
        browser.close()


def cmd_user_info(args: argparse.Namespace) -> None:
    """账号信息（昵称 / 抖音号 / 粉丝数 / 头像）。纯 HTTP，不需要浏览器。"""
    _api_or_output(args, lambda api: api.user_info())


def cmd_creator_info(args: argparse.Namespace) -> None:
    """创作者信息（开通日期 / 实名状态 / 权限位）。纯 HTTP，不需要浏览器。"""
    _api_or_output(args, lambda api: api.creator_info())


def cmd_list_works(args: argparse.Namespace) -> None:
    """已发布作品列表。纯 HTTP，不需要浏览器。"""
    _api_or_output(
        args,
        lambda api: api.list_works(count=args.count, order_by=args.order_by),
    )


def cmd_upload_preflight(args: argparse.Namespace) -> None:
    """只读检查上传前置条件（VOD 域名 + 能否拿到 STS 凭证，不打印凭证本身）。"""
    _api_or_output(args, lambda api: api.upload_preflight())


def cmd_get_qrcode(args: argparse.Namespace) -> None:
    from douyin.login import fetch_qrcode

    browser, page = _connect(args)
    try:
        png_bytes, already = fetch_qrcode(page)
        if already:
            _output({"logged_in": True, "message": "已登录，无需重新扫码"})

        result: dict = {
            "logged_in": False,
            "login_method": "qrcode",
            "message": "二维码已生成并保存到本地文件。扫码后运行 wait-login 等待登录结果。",
        }
        _fill_qrcode_result(result, png_bytes)
        _output(result, exit_code=1)
    finally:
        browser.close()


def cmd_wait_login(args: argparse.Namespace) -> None:
    from douyin.login import wait_for_login

    browser, page = _connect(args)
    try:
        success = wait_for_login(page, timeout=args.timeout)
        message = "登录成功" if success else "等待超时，请重新运行 get-qrcode 获取新二维码"
        _output({"logged_in": success, "message": message}, exit_code=0 if success else 2)
    finally:
        browser.close()


def cmd_login(args: argparse.Namespace) -> None:
    """扫码登录（阻塞等待完成）。"""
    from douyin.login import fetch_qrcode, wait_for_login

    browser, page = _connect(args)
    try:
        png_bytes, already = fetch_qrcode(page)
        if already:
            _output({"logged_in": True, "message": "已登录"})

        result: dict = {"login_method": "qrcode"}
        _fill_qrcode_result(result, png_bytes)
        logger.info("二维码已生成，等待扫码...")

        success = wait_for_login(page, timeout=args.timeout)
        result["logged_in"] = success
        result["message"] = "登录成功" if success else "等待超时"
        _output(result, exit_code=0 if success else 2)
    finally:
        browser.close()


def cmd_send_code(args: argparse.Namespace) -> None:
    from douyin.login import send_phone_code

    browser, page = _connect(args)
    try:
        sent = send_phone_code(page, args.phone)
        if not sent:
            _output({"logged_in": True, "message": "已登录，无需重新登录"})
        _output(
            {
                "status": "code_sent",
                "message": (
                    f"验证码已发送至 {args.phone[:3]}****{args.phone[-4:]}，"
                    "请运行 verify-code --code <验证码>"
                ),
            }
        )
    finally:
        browser.close()


def cmd_verify_code(args: argparse.Namespace) -> None:
    from douyin.login import submit_phone_code

    browser, page = _connect(args)
    try:
        success = submit_phone_code(page, args.code)
        _output(
            {"logged_in": success, "message": "登录成功" if success else "验证码错误或超时"},
            exit_code=0 if success else 2,
        )
    finally:
        browser.close()


# ─── 发布相关 ────────────────────────────────────────────────────────────────


def _read_text_file(path: str, label: str) -> str:
    if not path:
        return ""
    if not os.path.exists(path):
        _output({"success": False, "error": f"{label}文件不存在: {path}"}, exit_code=2)
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _build_video_content(args: argparse.Namespace) -> PublishVideoContent:
    from douyin.types import PublishVideoContent

    return PublishVideoContent(
        title=_read_text_file(args.title_file, "标题"),
        description=_read_text_file(args.desc_file, "简介"),
        tags=args.tags or [],
        video_path=args.video,
        schedule_time=args.schedule_at,
        visibility=args.visibility or "",
    )


def cmd_fill_publish_video(args: argparse.Namespace) -> None:
    from douyin.publish_video import fill_publish_video_form

    content = _build_video_content(args)
    browser, page = _connect(args)
    try:
        fill_publish_video_form(page, content, skip_upload=args.skip_upload)
        _output(
            {
                "success": True,
                "title": content.title,
                "video": content.video_path,
                "status": (
                    "已在当前发布页填写表单（未重新上传视频）"
                    if args.skip_upload
                    else "视频表单已填写，等待用户确认后发布"
                ),
            }
        )
    finally:
        browser.close()


def cmd_publish_video(args: argparse.Namespace) -> None:
    from douyin.publish_video import publish_video_content

    content = _build_video_content(args)
    browser, page = _connect(args)
    try:
        result = publish_video_content(page, content)
        result.update({"title": content.title, "video": content.video_path})
        _output(result)
    finally:
        browser.close()


def cmd_click_publish(args: argparse.Namespace) -> None:
    from douyin.publish_video import click_publish_video_button

    browser, page = _connect(args)
    try:
        _output(click_publish_video_button(page))
    finally:
        browser.close()


def cmd_save_draft(args: argparse.Namespace) -> None:
    from douyin.publish_video import save_as_draft

    browser, page = _connect(args)
    try:
        save_as_draft(page)
        _output({"success": True, "status": "内容已保存到草稿箱"})
    finally:
        browser.close()


# ─── 调试相关 ────────────────────────────────────────────────────────────────


def cmd_probe(args: argparse.Namespace) -> None:
    from douyin.probe import probe_page

    browser, page = _connect(args)
    try:
        _output(probe_page(page, args.url))
    finally:
        browser.close()


def cmd_page_info(args: argparse.Namespace) -> None:
    browser, page = _connect(args)
    try:
        _output(page.get_page_info())
    finally:
        browser.close()


# ─── 参数解析 ────────────────────────────────────────────────────────────────


def _add_publish_args(sub: argparse.ArgumentParser, *, video_required: bool = True) -> None:
    sub.add_argument("--title-file", default="", help="作品标题文件路径（UTF-8）")
    sub.add_argument("--desc-file", default="", help="作品简介文件路径（UTF-8）")
    sub.add_argument("--video", required=video_required, help="视频文件绝对路径")
    sub.add_argument("--tags", nargs="*", help="#话题标签列表（不含 #）")
    sub.add_argument("--schedule-at", help="定时发布时间，ISO8601 如 2026-03-10T12:00")
    sub.add_argument("--visibility", help="可见范围: 公开|好友|自己|粉丝")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douyin-cli",
        description=(
            "抖音创作服务平台自动化 CLI（默认走纯 HTTP API，需要页面/签名的能力用 --browser）"
        ),
    )
    parser.add_argument(
        "--bridge-url",
        default="ws://localhost:9334",
        help="Bridge server WebSocket 地址 (default: ws://localhost:9334)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="强制使用浏览器模式（扩展 Bridge + 页面操作）。默认走纯 HTTP API",
    )

    # ``--browser`` 既要能写在子命令之前（全局），也要能写在子命令之后 —— 后者更符合
    # 直觉，但 argparse 的全局选项默认不支持，故给每个子命令也补一份同名的
    # store_true，用 ``default=argparse.SUPPRESS`` 保证它不覆盖全局那份的值。
    class _SubParserFactory:
        """给每个子命令自动补一个 ``--browser``。"""

        def __init__(self, real: Any) -> None:
            self._real = real

        def add_parser(self, *args: Any, **kwargs: Any) -> Any:
            sub = self._real.add_parser(*args, **kwargs)
            sub.add_argument(
                "--browser",
                action="store_true",
                default=argparse.SUPPRESS,
                help="强制使用浏览器模式（等价于写在子命令前面的 --browser）",
            )
            return sub

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers = _SubParserFactory(subparsers)  # type: ignore[assignment]

    sub = subparsers.add_parser("check-login", help="检查登录状态")
    sub.set_defaults(func=cmd_check_login)

    sub = subparsers.add_parser("user-info", help="账号信息（昵称/抖音号/粉丝数）")
    sub.set_defaults(func=cmd_user_info)

    sub = subparsers.add_parser("creator-info", help="创作者信息（开通日期/权限位）")
    sub.set_defaults(func=cmd_creator_info)

    sub = subparsers.add_parser("list-works", help="已发布作品列表")
    sub.add_argument("--count", type=int, default=10, help="每页条数 (default: 10)")
    sub.add_argument("--order-by", type=int, default=0, help="0=按发布时间倒序 (default: 0)")
    sub.set_defaults(func=cmd_list_works)

    sub = subparsers.add_parser("upload-preflight", help="只读检查上传前置条件（不打印任何凭证）")
    sub.set_defaults(func=cmd_upload_preflight)

    sub = subparsers.add_parser("login", help="扫码登录（阻塞等待）")
    sub.add_argument("--timeout", type=float, default=180.0)
    sub.set_defaults(func=cmd_login)

    sub = subparsers.add_parser("get-qrcode", help="获取登录二维码（非阻塞）")
    sub.set_defaults(func=cmd_get_qrcode)

    sub = subparsers.add_parser("wait-login", help="等待扫码登录完成")
    sub.add_argument("--timeout", type=float, default=180.0)
    sub.set_defaults(func=cmd_wait_login)

    sub = subparsers.add_parser("send-code", help="手机验证码登录第一步：发送验证码")
    sub.add_argument("--phone", required=True, help="手机号")
    sub.set_defaults(func=cmd_send_code)

    sub = subparsers.add_parser("verify-code", help="手机验证码登录第二步：提交验证码")
    sub.add_argument("--code", required=True, help="短信验证码")
    sub.set_defaults(func=cmd_verify_code)

    sub = subparsers.add_parser("fill-publish-video", help="填写视频表单（不发布）")
    _add_publish_args(sub, video_required=False)
    sub.add_argument(
        "--skip-upload",
        action="store_true",
        help="假定当前标签页已经是上传完成的发布页，只填表单、不重新上传（调试选择器时用）",
    )
    sub.set_defaults(func=cmd_fill_publish_video)

    sub = subparsers.add_parser("publish-video", help="视频一步发布")
    _add_publish_args(sub)
    sub.set_defaults(func=cmd_publish_video)

    sub = subparsers.add_parser("click-publish", help="点击发布按钮（用户确认后调用）")
    sub.set_defaults(func=cmd_click_publish)

    sub = subparsers.add_parser("save-draft", help="保存为草稿")
    sub.set_defaults(func=cmd_save_draft)

    sub = subparsers.add_parser("probe", help="抓取当前页面元素，用于修复选择器")
    sub.add_argument("--url", help="先导航到该 URL 再抓取")
    sub.set_defaults(func=cmd_probe)

    sub = subparsers.add_parser("page-info", help="输出当前标签页 url/title")
    sub.set_defaults(func=cmd_page_info)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 子命令上的 --browser 用 argparse.SUPPRESS，未出现时不写回 namespace，
    # 因此全局那份的值会保留下来；这里只做一次兜底归一化。
    args.browser = bool(getattr(args, "browser", False))

    # 浏览器专属子命令在没加 --browser 时立刻报错（而不是悄悄去启动 Chrome）
    _require_browser_mode(args)

    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        from douyin.errors import NotLoggedInError

        # 未登录用 exit 1 与其他错误（2）区分开，便于脚本判断
        if isinstance(e, NotLoggedInError):
            logger.error("未登录: %s", e)
            _output(
                {
                    "success": False,
                    "logged_in": False,
                    "mode": "browser" if getattr(args, "browser", False) else "api",
                    "error": str(e),
                },
                exit_code=1,
            )
        logger.error("执行失败: %s", e)
        _output({"success": False, "error": str(e)}, exit_code=2)


if __name__ == "__main__":
    main()
