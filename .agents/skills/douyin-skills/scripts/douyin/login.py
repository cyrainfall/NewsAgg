"""登录管理：登录状态检查、二维码登录、手机验证码登录。

抖音创作服务平台的登录态就是浏览器里的普通 cookie（sessionid 等）。本模块
不读取也不保存任何 cookie 值，只驱动页面 UI 完成登录，登录态始终留在用户
自己的浏览器里。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import time

from . import dom
from .bridge import BridgePage
from .errors import LoginRequiredActionError, RateLimitError
from .human import sleep_random
from .selectors import (
    AGREE_CHECKBOX_SELECTORS,
    CODE_INPUTS,
    LOGGED_IN_TEXTS,
    LOGGED_OUT_TEXTS,
    LOGIN_SUBMIT_TEXTS,
    PHONE_INPUTS,
    PHONE_LOGIN_TAB_TEXTS,
    QRCODE_SELECTORS,
    SEND_CODE_BUTTON_TEXTS,
)
from .urls import LOGIN_URL, UPLOAD_URL

logger = logging.getLogger(__name__)

_QR_DIR = os.path.join(tempfile.gettempdir(), "douyin")
_QR_FILE = os.path.join(_QR_DIR, "login_qrcode.png")


# ─── 基础探测 ────────────────────────────────────────────────────────────────


def page_text(page: BridgePage, limit: int = 20000) -> str:
    """读取页面可见文本，用于文本标志判断。"""
    return (
        page.evaluate(f"(document.body ? (document.body.innerText || '') : '').slice(0, {limit})")
        or ""
    )


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def is_logged_in(page: BridgePage) -> bool:
    """根据当前页面文本判断是否已登录（不发起导航）。"""
    text = page_text(page)
    if _has_any(text, LOGGED_OUT_TEXTS):
        return False
    return _has_any(text, LOGGED_IN_TEXTS)


def get_nickname(page: BridgePage) -> str:
    """尽力获取当前登录账号昵称，失败返回空字符串。"""
    return (
        page.evaluate(
            """
        (() => {
            const sels = [
                '[class*="nickname"]',
                '[class*="nickName"]',
                '[class*="user-name"]',
                '[class*="username"]',
            ];
            for (const sel of sels) {
                const el = document.querySelector(sel);
                const t = el && (el.innerText || el.textContent || '').trim();
                if (t && t.length <= 40) return t;
            }
            return '';
        })()
        """
        )
        or ""
    )


def check_login_status(page: BridgePage, timeout: float = 20.0) -> bool:
    """导航到发布页并判断登录状态。

    只认两个强信号：出现登录页文案 → 未登录；出现创作服务平台导航文案 → 已登录。
    宁可把已登录误判成未登录（用户重新登录一次即可），也不要反过来 —— 后者会让
    后续流程在未登录状态下继续走，最后报出一个难以定位的错误。
    """
    page.navigate(UPLOAD_URL)
    page.wait_for_load(timeout=120)
    page.wait_for_dom_stable(timeout=8)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = page_text(page)
        if _has_any(text, LOGGED_OUT_TEXTS):
            return False
        if _has_any(text, LOGGED_IN_TEXTS):
            return True
        time.sleep(0.5)

    return False


# ─── 二维码登录 ──────────────────────────────────────────────────────────────


def _find_qrcode_selector(page: BridgePage) -> str | None:
    """找出页面上尺寸足够大的二维码元素选择器。"""
    return page.evaluate(
        f"""
        (() => {{
            const cands = {json.dumps(list(QRCODE_SELECTORS))};
            for (const sel of cands) {{
                for (const el of document.querySelectorAll(sel)) {{
                    const r = el.getBoundingClientRect();
                    if (r.width >= 80 && r.height >= 80) return sel;
                }}
            }}
            return null;
        }})()
        """
    )


def fetch_qrcode(page: BridgePage) -> tuple[bytes, bool]:
    """打开登录页并截取二维码。

    Returns:
        ``(png_bytes, already_logged_in)``。
        已登录时返回 ``(b"", True)``。
    """
    page.navigate(LOGIN_URL)
    page.wait_for_load(timeout=120)
    page.wait_for_dom_stable(timeout=8)
    sleep_random(800, 1500)

    if is_logged_in(page):
        return b"", True

    selector = _find_qrcode_selector(page)
    if not selector:
        # 兜底：整页截图，用户可自行定位二维码
        logger.warning("未定位到二维码元素，改为整页截图")
        selector = "body"

    png_bytes = page.screenshot_element(selector, padding=12)
    if not png_bytes:
        raise LoginRequiredActionError("二维码截图失败，请检查浏览器是否正常工作")
    return png_bytes, False


def save_qrcode_to_file(png_bytes: bytes) -> str:
    """把二维码 PNG 写入临时文件，返回绝对路径。"""
    os.makedirs(_QR_DIR, exist_ok=True)
    with open(_QR_FILE, "wb") as f:
        f.write(png_bytes)
    logger.info("二维码已保存: %s", _QR_FILE)
    return _QR_FILE


def make_qrcode_data_url(png_bytes: bytes, max_chars: int = 20000) -> str:
    """生成 data URL 供直接展示；过大的图返回空字符串（改为看本地文件）。"""
    data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    return data_url if len(data_url) <= max_chars else ""


def open_qrcode_file(path: str) -> None:
    """有桌面环境时用系统默认程序打开二维码图片。"""
    import platform
    import subprocess

    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        logger.debug("无法自动打开二维码文件: %s", path)


def wait_for_login(page: BridgePage, timeout: float = 180.0) -> bool:
    """等待扫码完成。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_logged_in(page):
            logger.info("登录成功")
            return True
        text = page_text(page, limit=2000)
        if "已扫码" in text or "扫码成功" in text:
            logger.info("已扫码，等待确认...")
        time.sleep(1.0)
    return False


# ─── 手机验证码登录 ──────────────────────────────────────────────────────────


def _try_click_text(page: BridgePage, texts: tuple[str, ...]) -> bool:
    """按文本点击第一个命中的元素；命中失败不抛错，返回是否点击成功。"""
    return dom.click_text(page, texts)


def _click_login_tab(page: BridgePage) -> None:
    """切到"验证码登录" tab（若存在）。"""
    _try_click_text(page, PHONE_LOGIN_TAB_TEXTS)
    sleep_random(500, 900)


def _agree_terms(page: BridgePage) -> None:
    """勾选用户协议（未勾选时无法获取验证码）。"""
    clicked = page.evaluate(
        f"""
        (() => {{
            const sels = {json.dumps(list(AGREE_CHECKBOX_SELECTORS))};
            for (const sel of sels) {{
                for (const el of document.querySelectorAll(sel)) {{
                    const input = el.tagName === 'INPUT'
                        ? el
                        : el.querySelector('input[type="checkbox"]');
                    if (input && input.checked) return 'already';
                    const box = el.closest('[class*="checkbox"]') || el;
                    box.click();
                    return 'clicked';
                }}
            }}
            return 'not_found';
        }})()
        """
    )
    if clicked == "clicked":
        sleep_random(200, 400)


def _ensure_code_sent(page: BridgePage, timeout: float = 6.0) -> None:
    """确认验证码真的发出去了。

    出现倒计时说明发送成功；出现"频繁"文案说明被限流。两者都没检测到时只告警不
    中断 —— 因为一次 UI 差异就彻底阻断登录流程，比让用户多看两眼页面糟糕得多。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = page_text(page, limit=4000)
        if "频繁" in text or "过于频繁" in text:
            raise RateLimitError()
        if re.search(r"\d{1,3}\s*[s秒]", text) or "重新获取" in text:
            return
        time.sleep(0.3)
    logger.warning("未检测到验证码倒计时，请确认短信是否已收到")


def send_phone_code(page: BridgePage, phone: str) -> bool:
    """填写手机号并发送短信验证码。

    Returns:
        True = 验证码已发送；False = 已登录，无需登录。
    """
    page.navigate(LOGIN_URL)
    page.wait_for_load(timeout=120)
    page.wait_for_dom_stable(timeout=8)

    if is_logged_in(page):
        return False

    _click_login_tab(page)

    phone_selector = page.first_present_selector(PHONE_INPUTS)
    if not phone_selector:
        raise LoginRequiredActionError("未找到手机号输入框，请手动在浏览器中打开登录页")

    page.click_element(phone_selector)
    sleep_random(200, 400)
    page.type_text(phone, delay_ms=80)
    sleep_random(200, 400)

    _agree_terms(page)

    if not _try_click_text(page, SEND_CODE_BUTTON_TEXTS):
        raise LoginRequiredActionError("未找到「获取验证码」按钮")

    _ensure_code_sent(page)
    logger.info("验证码已发送至 %s****%s", phone[:3], phone[-4:])
    return True


def submit_phone_code(page: BridgePage, code: str) -> bool:
    """填写验证码并提交登录。"""
    code_selector = page.first_present_selector(CODE_INPUTS)
    if not code_selector:
        raise LoginRequiredActionError("未找到验证码输入框")

    page.click_element(code_selector)
    sleep_random(100, 200)
    page.input_text(code_selector, "")
    page.type_text(code, delay_ms=0)
    sleep_random(100, 300)

    if not _try_click_text(page, LOGIN_SUBMIT_TEXTS):
        raise LoginRequiredActionError("未找到「登录」按钮")

    sleep_random(800, 1500)
    return wait_for_login(page, timeout=45.0)
