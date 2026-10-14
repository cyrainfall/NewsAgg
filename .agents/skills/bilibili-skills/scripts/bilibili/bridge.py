"""BridgePage - 通过浏览器扩展 Bridge 操作真实浏览器页面。

CLI 通过 WebSocket 把命令发给 bridge_server.py，bridge_server 转发给浏览器
扩展执行，结果原路返回。每次调用都是一次短连接（发一条命令 → 收一条回复），
不需要维护持久连接，也不会残留浏览器状态。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import websockets.sync.client as ws_client

from .errors import BridgeError, ElementNotFoundError

# 9335 是本技能专用端口。xiaohongshu-skills 用 9333、douyin-skills 用 9334，
# 三者分开可以让三个浏览器扩展各自连接自己的 bridge，避免命令被路由到错误的站点。
BRIDGE_URL = "ws://localhost:9335"


class BridgePage:
    """与浏览器扩展通信的页面对象。"""

    def __init__(self, bridge_url: str = BRIDGE_URL) -> None:
        self._bridge_url = bridge_url

    # ─── 内部通信 ───────────────────────────────────────────────

    def _call(self, method: str, params: dict | None = None) -> Any:
        """向 bridge server 发送一条命令并等待结果。"""
        msg: dict[str, Any] = {"role": "cli", "method": method}
        if params:
            msg["params"] = params
        try:
            with ws_client.connect(self._bridge_url, max_size=50 * 1024 * 1024) as ws:
                ws.send(json.dumps(msg, ensure_ascii=False))
                # 比 bridge server 的 300s 上限再宽一点，避免本地先超时丢掉真实错误
                raw = ws.recv(timeout=320)
        except OSError as e:
            raise BridgeError(f"无法连接到 bridge server（{self._bridge_url}）: {e}") from e

        resp = json.loads(raw)
        if resp.get("error"):
            raise BridgeError(f"Bridge 错误: {resp['error']}")
        return resp.get("result")

    # ─── 导航 ───────────────────────────────────────────────────

    def navigate(self, url: str) -> None:
        self._call("navigate", {"url": url})

    def wait_for_load(self, timeout: float = 60.0) -> None:
        self._call("wait_for_load", {"timeout": int(timeout * 1000)})

    def wait_for_dom_stable(self, timeout: float = 10.0, interval: float = 0.5) -> None:
        """等待 DOM 结构稳定（连续两次快照一致），用于 SPA 渲染完成的判断。

        协议侧方法名是 ``wait_dom_stable``，Python 侧统一为 ``wait_for_*`` 风格，
        与 ``wait_for_load`` / ``wait_for_element`` 保持一致。
        """
        self._call(
            "wait_dom_stable",
            {"timeout": int(timeout * 1000), "interval": int(interval * 1000)},
        )

    def get_url(self) -> str:
        return self._call("get_url") or ""

    def get_page_info(self) -> dict:
        """返回当前标签页的 url / title / tabId。"""
        return self._call("get_page_info") or {}

    def get_iframes(self) -> list[dict]:
        """列出页面里的 iframe（probe 用）。

        B站投稿页正常渲染在主文档里，本技能的 DOM 查询与 CDP 操作都不进入
        iframe。这个方法的唯一作用是：当页面结构变化导致元素找不到时，能立刻
        看出"是不是被搬进 iframe 了"，而不是对着空的 probe 输出猜。
        """
        return self._call("get_iframes") or []

    # ─── JavaScript 执行 ────────────────────────────────────────

    def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        return self._call("evaluate", {"expression": expression})

    def evaluate_function(self, function_body: str, *args: Any) -> Any:
        return self._call("evaluate", {"expression": f"({function_body})()"})

    # ─── 元素查询 ───────────────────────────────────────────────

    def has_element(self, selector: str) -> bool:
        return bool(self._call("has_element", {"selector": selector}))

    def get_elements_count(self, selector: str) -> int:
        result = self._call("get_elements_count", {"selector": selector})
        return int(result) if result is not None else 0

    def get_element_text(self, selector: str) -> str | None:
        return self._call("get_element_text", {"selector": selector})

    def get_element_attribute(self, selector: str, attr: str) -> str | None:
        return self._call("get_element_attribute", {"selector": selector, "attr": attr})

    def get_elements_info(self, selector: str, attrs: list[str] | None = None) -> list[dict]:
        """批量取元素信息，用于 probe 调试：``[{text, placeholder, class, ...}]``。"""
        return (
            self._call(
                "get_elements_info",
                {
                    "selector": selector,
                    "attrs": attrs or ["placeholder", "class", "type", "accept"],
                },
            )
            or []
        )

    def wait_for_element(self, selector: str, timeout: float = 30.0) -> None:
        found = self._call(
            "wait_for_selector",
            {"selector": selector, "timeout": int(timeout * 1000)},
        )
        if not found:
            raise ElementNotFoundError(selector)

    def first_present_selector(self, candidates: tuple[str, ...]) -> str | None:
        """返回候选选择器中第一个命中的，用于兼容多种页面版本。"""
        for selector in candidates:
            if self.has_element(selector):
                return selector
        return None

    # ─── 表单值读取 ─────────────────────────────────────────────

    def get_input_value(self, selector: str) -> str:
        """读取 input / textarea 的**当前值**（DOM 属性，不是 HTML 属性）。

        必须读 `el.value`，不能读 `getAttribute('value')`：后者返回的是标签上写死的
        初始值，程序化写入（原生 setter）和用户输入都不会更新它。实测标题明明已经
        写进了输入框，用 getAttribute 读回来却始终是空串，直接触发一次误报。

        走 `evaluate`（页面主 world 里直接取属性）而不是新增一条扩展命令：本技能已经
        有多处依赖 evaluate，多一条不多；而"Python 侧调了、扩展没实现"这类接线错误
        正是最贵的一类 bug，扩展命令越少越好。若将来 B站 收紧 CSP 让 `new Function`
        不可用，再把它改成 background.js 里的专用命令。
        """
        return (
            self.evaluate(
                f"""
                (() => {{
                  const el = document.querySelector({json.dumps(selector)});
                  if (!el) return "";
                  return String(el.value === undefined ? "" : el.value);
                }})()
                """
            )
            or ""
        )

    # ─── 元素操作 ───────────────────────────────────────────────

    def click_element(self, selector: str) -> dict:
        """点击元素（chrome.debugger 派发真实鼠标事件，isTrusted=true）。

        返回扩展报告的落点信息（``x`` / ``y`` / ``tag``）。排查"点了没反应"时这很关键：
        有了坐标才能区分"没点到元素"和"点到了但平台没受理"。
        """
        return self._call("click_element", {"selector": selector}) or {}

    def click_nth_element(self, selector: str, index: int) -> None:
        self._call("click_nth_element", {"selector": selector, "index": index})

    def click_element_by_text(self, selector: str, text: str) -> None:
        """点击文本包含 ``text`` 的第一个元素（真实鼠标事件）。"""
        self._call("click_element_by_text", {"selector": selector, "text": text})

    def click_element_js(self, selector: str) -> None:
        """用 JS ``element.click()`` 点击，作为真实点击失败时的兜底。"""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) {{ el.scrollIntoView({{block: 'center'}}); el.click(); }}
            }})()
            """
        )

    def input_text(self, selector: str, text: str) -> None:
        """向 input / textarea 写入文本（走原生 setter，兼容受控组件）。"""
        self._call("input_text", {"selector": selector, "text": text})

    def input_content_editable(self, selector: str, text: str) -> None:
        """向 contenteditable 写入多行文本。"""
        self._call("input_content_editable", {"selector": selector, "text": text})

    def type_text(self, text: str, delay_ms: int = 50) -> None:
        """逐字符输入（真实键盘事件 / contenteditable 走 execCommand）。"""
        if not text:
            return
        self._call("type_text", {"text": text, "delayMs": delay_ms})

    def press_key(self, key: str) -> None:
        """按下并释放按键，如 Enter / ArrowDown / Backspace。"""
        self._call("press_key", {"key": key})

    def select_all_text(self, selector: str) -> None:
        self._call("select_all_text", {"selector": selector})

    def remove_element(self, selector: str) -> None:
        self._call("remove_element", {"selector": selector})

    def hover_element(self, selector: str) -> None:
        self._call("hover_element", {"selector": selector})

    # ─── 滚动 ───────────────────────────────────────────────────

    def scroll_by(self, x: int, y: int) -> None:
        self._call("scroll_by", {"x": x, "y": y})

    def scroll_to(self, x: int, y: int) -> None:
        self._call("scroll_to", {"x": x, "y": y})

    def scroll_to_bottom(self) -> None:
        self._call("scroll_to_bottom")

    def scroll_element_into_view(self, selector: str) -> None:
        self._call("scroll_element_into_view", {"selector": selector})

    def get_scroll_top(self) -> int:
        result = self._call("get_scroll_top")
        return int(result) if result is not None else 0

    def get_viewport_height(self) -> int:
        result = self._call("get_viewport_height")
        return int(result) if result is not None else 768

    # ─── 文件上传 ────────────────────────────────────────────────

    def set_file_input(self, selector: str, files: list[str]) -> None:
        """通过 chrome.debugger + CDP DOM.setFileInputFiles 上传本地文件。

        必须传绝对路径；扩展侧直接使用这些路径读取磁盘文件，不经过页面 JS。
        """
        abs_paths = [os.path.abspath(path) for path in files]
        self._call("set_file_input", {"selector": selector, "files": abs_paths})

    # ─── 截图 ────────────────────────────────────────────────────

    def screenshot_element(self, selector: str, padding: int = 0) -> bytes:
        result = self._call("screenshot_element", {"selector": selector, "padding": padding})
        if result and result.get("data"):
            return base64.b64decode(result["data"])
        return b""

    # ─── Cookies ─────────────────────────────────────────────────

    def get_cookies(self, domain: str = "bilibili.com") -> list[dict]:
        return self._call("get_cookies", {"domain": domain}) or []

    # ─── 兼容性辅助方法 ──────────────────────────────────────────

    def is_server_running(self) -> bool:
        """检查 bridge server 是否在运行（不要求扩展已连接）。"""
        try:
            with ws_client.connect(self._bridge_url, open_timeout=3) as ws:
                ws.send(json.dumps({"role": "cli", "method": "ping_server"}))
                raw = ws.recv(timeout=5)
            return "result" in json.loads(raw)
        except Exception:
            return False

    def is_extension_connected(self) -> bool:
        """检查浏览器扩展是否已连接到 bridge server。"""
        try:
            with ws_client.connect(self._bridge_url, open_timeout=3) as ws:
                ws.send(json.dumps({"role": "cli", "method": "ping_server"}))
                raw = ws.recv(timeout=5)
            return bool(json.loads(raw).get("result", {}).get("extension_connected"))
        except Exception:
            return False

    @property
    def target_id(self) -> str:
        return "extension-bridge"
