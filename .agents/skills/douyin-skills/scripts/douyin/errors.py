"""抖音自动化异常体系。"""

from __future__ import annotations


class DouyinError(Exception):
    """抖音自动化基础异常。"""


class NotLoggedInError(DouyinError):
    """未登录。"""

    def __init__(self) -> None:
        super().__init__("未登录，请先扫码或验证码登录抖音创作服务平台")


class LoginRequiredActionError(DouyinError):
    """登录过程中需要人工介入（如收到短信验证码）。"""


class RateLimitError(DouyinError):
    """请求频率过高，验证码获取失败。"""

    def __init__(self) -> None:
        super().__init__("请求太频繁，验证码获取失败，请稍后重试")


class UploadTimeoutError(DouyinError):
    """上传 / 视频处理超时。"""


class PublishError(DouyinError):
    """发布失败。"""


class AccountRiskControlError(PublishError):
    """账号被风控，无法发布。"""

    def __init__(self, code: int | str, msg: str) -> None:
        self.code = code
        self.msg = msg
        super().__init__(f"账号被风控（code={code}）：{msg}")


class TitleTooLongError(PublishError):
    """标题超过长度限制。"""

    def __init__(self, current: str, maximum: str) -> None:
        self.current = current
        self.maximum = maximum
        super().__init__(f"当前标题长度为{current}，最大长度为{maximum}")


class DescriptionTooLongError(PublishError):
    """作品简介超过长度限制。"""

    def __init__(self, current: str, maximum: str) -> None:
        self.current = current
        self.maximum = maximum
        super().__init__(f"当前简介长度为{current}，最大长度为{maximum}")


class ElementNotFoundError(DouyinError):
    """页面元素未找到。"""

    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(f"未找到元素: {selector}")


class BridgeError(DouyinError):
    """Bridge 通信异常（扩展未连接 / 命令超时）。"""


class BrowserRequiredError(DouyinError):
    """该能力只能走浏览器（``--browser``），无浏览器路径不存在。

    与 ``SignatureRequiredError`` 分开，因为处置方式不同：前者是「这个能力压根
    没做无浏览器实现（也没法做）」，后者是「端点存在、但请求签名算不出来」。
    混成一个异常，用户就没法判断该换命令还是该加签名。
    """

    def __init__(self, command: str, reason: str) -> None:
        self.command = command
        self.reason = reason
        super().__init__(
            f"子命令 `{command}` 需要浏览器：{reason}\n"
            f"请加 `--browser` 走扩展桥接（python scripts/cli.py --browser {command} ...）"
        )


class SignatureRequiredError(DouyinError):
    """平台接口需要浏览器内实时生成的请求签名，纯 HTTP 无法复现。

    这是**诚实的缺口**，不是「还没实现」的托词：签名由前端 secure-consumer-sdk
    在浏览器里按设备指纹 + 时间 + 请求体实时计算并注入请求头，算法随平台升级迭代，
    硬编码一份实现必然会随升级静默失效。因此这里宁可抛错，也不返回假成功。
    """

    def __init__(self, endpoint: str, detail: str) -> None:
        self.endpoint = endpoint
        self.detail = detail
        super().__init__(
            f"接口 {endpoint} 需要浏览器内生成的请求签名（{detail}），"
            "纯 HTTP 调用无法复现，暂不支持无浏览器执行。\n"
            "请加 `--browser` 走扩展桥接发布：python scripts/cli.py --browser publish-video ...\n"
            "（绝不会返回假的发布成功——未能确认发布成功就会以退出码 2 结束）"
        )
