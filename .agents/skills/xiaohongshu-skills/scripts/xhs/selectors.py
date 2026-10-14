"""小红书页面 CSS 选择器常量。"""

# ========== 登录 ==========
LOGIN_STATUS = ".main-container .user .link-wrapper .channel"
QRCODE_IMG = ".qrcode-img"

# ========== 手机号登录 ==========
LOGIN_CONTAINER = ".login-container"
PHONE_INPUT = "label.phone input"
GET_CODE_BUTTON = "span.code-button"
CODE_INPUT = "label.auth-code input"
PHONE_LOGIN_SUBMIT = ".input-container button.submit"
AGREE_CHECKBOX = ".agree-icon .icon-wrapper"
AGREE_CHECKBOX_CHECKED = ".agree-icon .icon-wrapper.agreed"
LOGIN_ERR_MSG = ".err-msg"

# ========== 首页 / 搜索 ==========
FILTER_BUTTON = "div.filter"
FILTER_PANEL = "div.filters-wrapper"

# ========== Feed 详情 ==========
COMMENTS_CONTAINER = ".comments-container"
PARENT_COMMENT = ".parent-comment"
NO_COMMENTS_TEXT = ".no-comments-text"
END_CONTAINER = ".end-container"
TOTAL_COMMENT = ".comments-container .total"
SHOW_MORE_BUTTON = ".show-more"
NOTE_SCROLLER = ".note-scroller"
INTERACTION_CONTAINER = ".interaction-container"

# 页面不可访问容器
ACCESS_ERROR_WRAPPER = ".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"

# ========== 评论输入 ==========
COMMENT_INPUT_TRIGGER = "div.input-box div.content-edit span"
COMMENT_INPUT_FIELD = "div.input-box div.content-edit p.content-input"
COMMENT_SUBMIT_BUTTON = "div.bottom button.submit"
REPLY_BUTTON = ".right .interactions .reply"

# ========== 点赞 / 收藏 ==========
LIKE_BUTTON = ".interact-container .left .like-lottie"
COLLECT_BUTTON = ".interact-container .left .reds-icon.collect-icon"

# ========== 发布页 ==========
UPLOAD_CONTENT = "div.upload-content"
CREATOR_TAB = "div.creator-tab"
UPLOAD_INPUT = ".upload-input"
FILE_INPUT = 'input[type="file"]'
TITLE_INPUT = "div.d-input input"
# 正文编辑器：老版是 Quill（div.ql-editor），现版是 tiptap ProseMirror（.ProseMirror）。
# 2026-09 实测视频发布页只剩 .ProseMirror，两者都列上以免再次改版即失效。
CONTENT_EDITOR = "div.ql-editor, .ProseMirror"
IMAGE_PREVIEW = ".img-preview-area .pr"
PUBLISH_BUTTON = ".publish-page-publish-btn button.bg-red"
# 视频发布页的发布按钮。2026-09 实测 DOM：
#   div.publish-video > div.btn-wrapper > div.btn-inner > span.btn-text（文案「发布笔记」）
# 而 .publish-page-publish-btn 整块已从页面移除（命中数 0），这正是原先"上传成功但永远等不到
# 发布按钮可点、最终超时 10 分钟"的根因。老选择器保留在链尾兜底。
PUBLISH_BUTTON_VIDEO = ".publish-video .btn-inner, .publish-page-publish-btn button.bg-red"

# 标题/正文长度校验
TITLE_MAX_SUFFIX = "div.title-container div.max_suffix"
CONTENT_LENGTH_ERROR = "div.edit-container div.length-error"

# 可见范围
VISIBILITY_DROPDOWN = "div.permission-card-wrapper div.d-select-content"
VISIBILITY_OPTIONS = "div.d-options-wrapper div.d-grid-item div.custom-option"

# 定时发布
SCHEDULE_SWITCH = ".post-time-wrapper .d-switch"
DATETIME_INPUT = ".date-picker-container input"

# 原创声明
ORIGINAL_SWITCH_CARD = "div.custom-switch-card"
ORIGINAL_SWITCH = "div.d-switch"

# 标签联想
TAG_TOPIC_CONTAINER = "#creator-editor-topic-container"
TAG_FIRST_ITEM = ".item"

# 弹窗
POPOVER = "div.d-popover"

# ========== 写长文模式 ==========
# 注意: 长文模式的按钮（写长文、新的创作、一键排版、下一步）通过文本匹配定位
LONG_ARTICLE_TAB_TEXT = "写长文"
NEW_CREATION_BUTTON_TEXT = "新的创作"
AUTO_FORMAT_BUTTON_TEXT = "一键排版"
NEXT_STEP_BUTTON_TEXT = "下一步"

LONG_ARTICLE_TITLE = 'textarea.d-text[placeholder="输入标题"]'
TEMPLATE_CARD = ".template-card"
TEMPLATE_TITLE = ".template-card .template-title"

# ========== 退出登录 ==========
LOGOUT_MORE_BUTTON = "div.information-wrapper"
LOGOUT_MENU_ITEM = 'div.menu-item[data-name="退出登录"]'

# ========== 用户主页 ==========
SIDEBAR_PROFILE = "div.main-container li.user.side-bar-component a.link-wrapper span.channel"
# 登录后导航栏"我"的链接（href 含 /user/profile/<user_id>）
USER_PROFILE_NAV_LINK = ".main-container .user .link-wrapper a.link-wrapper"
# 个人主页真实昵称
USER_NICKNAME = ".user-name"
