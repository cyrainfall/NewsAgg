"""B站（哔哩哔哩）创作中心自动化包。

模块划分：
    urls       —— URL 常量
    errors     —— 异常体系
    types      —— 数据类型
    human      —— 人类行为模拟（随机延迟）
    selectors  —— 页面选择器候选列表（改版时只改这里）
    bridge     —— 与浏览器扩展通信的 BridgePage
    dom        —— 按可见文本 / 语义属性定位元素
    login      —— 登录状态、扫码登录、短信验证码登录
    publish_video —— 视频投稿（上传 / 填表 / 立即投稿 / 存草稿）
    probe      —— 页面元素探测（排查改版）

刻意不定义 ``__all__``：这个包只以 ``import bilibili.xxx`` 的形式使用子模块，
没有人会 ``from bilibili import *``，多一份清单只是多一处需要同步维护的地方。
"""
