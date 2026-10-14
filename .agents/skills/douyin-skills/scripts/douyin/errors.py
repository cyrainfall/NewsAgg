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
