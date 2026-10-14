"""B站自动化数据类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PublishVideoContent:
    """视频投稿内容。

    对应投稿页「基本设置」区的字段：

    - 稿件标题：独立 input，最长 80 字（必填）
    - 分区：一级 / 二级级联选择（必填，B站要求每个稿件都有分区）
    - 标签：input 内输入后回车创建，最多 10 个、每个最长 20 字
    - 简介：富文本编辑器或 textarea，最长 2000 字
    - 封面：可选，不上传时 B站自动从视频里抽帧
    """

    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    video_path: str = ""
    category_primary: str = ""
    category_secondary: str = ""
    cover_path: str = ""
    schedule_time: str | None = None  # ISO8601 格式，None 表示立即投稿
    # 先清空 B站 已有的标签（含它按视频内容预选的那批）再写入 tags。
    # 默认 False，即"只叠加"——因为 B站 的预选标签有时是有用的，不该悄悄删掉。
    replace_tags: bool = False
    # 创作声明（**必填**）。可传页面上的完整文案，或 selectors.DECLARATION_TEXTS 里的简写。
    # 实测选项：内容无需标注 / 含AI生成内容 / 含虚构演绎内容 / 内容含营销信息 /
    # 个人观点，仅供参考 / 内容为转载 / 内容为自制：未经作者允许，禁止转载
    declaration: str = ""


@dataclass
class LoginState:
    """登录状态。"""

    logged_in: bool = False
    nickname: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {"logged_in": self.logged_in, "nickname": self.nickname, "url": self.url}
