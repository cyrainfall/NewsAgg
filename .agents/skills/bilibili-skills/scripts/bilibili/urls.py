"""B站创作中心 URL 常量。"""

# 创作中心首页（用户入口 URL）
CREATOR_HOME_URL = "https://member.bilibili.com/platform/home"

# 视频投稿页（上传入口；本技能的主战场）
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"

# 稿件管理页（确认投稿结果用）
MANAGE_URL = "https://member.bilibili.com/platform/upload-manager/article"

# 统一登录页（未登录时 member.bilibili.com 会 302 到这里）
LOGIN_URL = "https://passport.bilibili.com/login"

# 登录页所在 host：只要当前 URL 落在这个域上，就一定是未登录状态
PASSPORT_HOST = "passport.bilibili.com"
