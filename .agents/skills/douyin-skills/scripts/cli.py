"""抖音自动化 CLI 入口（Extension Bridge 版）

通过浏览器扩展 Bridge 连接用户已打开的 Chrome，使用真实的登录态和浏览器环境
操作抖音创作服务平台，不使用无头浏览器、不伪造指纹。

前置条件：
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
from typing import TYPE_CHECKING

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
    from douyin.login import check_login_status, get_nickname

    browser, page = _connect(args)
    try:
        logged_in = check_login_status(page)
        if not logged_in:
            _output(
                {
                    "logged_in": False,
                    "hint": "未登录。运行 `python scripts/cli.py get-qrcode` 获取二维码，"
                    "或 `python scripts/cli.py send-code --phone <手机号>` 走验证码登录。",
                },
                exit_code=1,
            )
        _output({"logged_in": True, "nickname": get_nickname(page), "url": page.get_url()})
    finally:
        browser.close()


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
        description="抖音创作服务平台自动化 CLI（Extension Bridge 版）",
    )
    parser.add_argument(
        "--bridge-url",
        default="ws://localhost:9334",
        help="Bridge server WebSocket 地址 (default: ws://localhost:9334)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sub = subparsers.add_parser("check-login", help="检查登录状态")
    sub.set_defaults(func=cmd_check_login)

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

    try:
        args.func(args)
    except Exception as e:
        from douyin.errors import NotLoggedInError

        if isinstance(e, NotLoggedInError):
            logger.error("未登录: %s", e)
            _output({"success": False, "error": str(e), "logged_in": False}, exit_code=1)
        logger.error("执行失败: %s", e, exc_info=True)
        _output({"success": False, "error": str(e)}, exit_code=2)


if __name__ == "__main__":
    main()
