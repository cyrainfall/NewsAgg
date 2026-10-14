"""抖音自动化数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PublishVideoContent:
    """视频发布内容。

    抖音发布页的字段：
    - 作品标题：独立 input，最长 30 字（可留空）
    - 作品简介：contenteditable，支持 @好友 / #话题，最长 1000 字
    """

    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    video_path: str = ""
    schedule_time: str | None = None  # ISO8601 格式，None 表示立即发布
    visibility: str = ""  # 空=默认（公开）；可选 公开|好友|自己|粉丝


@dataclass
class LoginState:
    """登录状态。"""

    logged_in: bool = False
    nickname: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {"logged_in": self.logged_in, "nickname": self.nickname, "url": self.url}
