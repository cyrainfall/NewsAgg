"""B站自动化异常体系。

分层的意义在于 CLI 的退出码：``NotLoggedInError`` → exit 1（引导去登录），
其余 → exit 2（是真的出错了）。Agent 靠这个区分"该让用户扫码"和"该排查页面"。
"""

from __future__ import annotations


class BilibiliError(Exception):
    """B站自动化基础异常。"""


class NotLoggedInError(BilibiliError):
    """未登录。"""

    def __init__(self) -> None:
        super().__init__("未登录，请先扫码或短信验证码登录 B站")


class LoginRequiredActionError(BilibiliError):
    """登录过程中需要人工介入（如收到短信验证码）。"""


class RateLimitError(BilibiliError):
    """请求频率过高，验证码获取失败。"""

    def __init__(self) -> None:
        super().__init__("请求太频繁，验证码获取失败，请稍后重试")


class UploadTimeoutError(BilibiliError):
    """上传 / 转码超时。"""


class PublishError(BilibiliError):
    """投稿失败。"""


class AccountRiskControlError(PublishError):
    """账号被风控，无法投稿。"""

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
    """简介超过长度限制。"""

    def __init__(self, current: str, maximum: str) -> None:
        self.current = current
        self.maximum = maximum
        super().__init__(f"当前简介长度为{current}，最大长度为{maximum}")


class TagError(PublishError):
    """标签不符合平台规则。"""


class TooManyTagsError(TagError):
    """标签数量超限。"""

    def __init__(self, current: int, maximum: int) -> None:
        self.current = current
        self.maximum = maximum
        super().__init__(f"标签数量为{current}个，最多{maximum}个")


class TagTooLongError(TagError):
    """单个标签过长。"""

    def __init__(self, tag: str, maximum: int) -> None:
        self.tag = tag
        self.maximum = maximum
        super().__init__(f"标签「{tag}」长度为{len(tag)}，单标签最长{maximum}字")


class ElementNotFoundError(BilibiliError):
    """页面元素未找到。"""

    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(f"未找到元素: {selector}")


class BridgeError(BilibiliError):
    """Bridge 通信异常（扩展未连接 / 命令超时）。"""
