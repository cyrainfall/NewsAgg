"""抖音创作服务平台页面选择器常量。

抖音创作服务平台使用 Semi Design（字节自研组件库），生产构建会混淆类名，
因此这里**不使用固定 hash 类名**，而是组合三类稳定的定位方式：

1. 语义属性：``placeholder`` / ``accept`` / ``type`` / ``contenteditable``
2. 结构前缀：``.semi-input``、``.semi-button`` 等 Semi 的稳定前缀
3. 可见文本：按钮、tab 上的人类可读文字

每个常量都是**候选列表（按优先级排序，第一个命中即用）**。抖音改版时优先
新增候选而不是替换，这样可以保持向后兼容。若全部失效，先用
``python scripts/cli.py probe`` 抓取当前页面元素，再更新本文件。
"""

# ========== 发布页：文件上传 ==========

# 已实测：发布页只有一个 input[type=file]，accept 是「video/* + 一长串视频扩展名」，
# 因此第一条候选即可命中；后面几条是改版后的兜底。
UPLOAD_INPUTS: tuple[str, ...] = (
    'input[type="file"][accept*="video"]',
    'input[type="file"][accept*="mp4"]',
    'input[type="file"][accept*="quicktime"]',
    "div[class*='upload'] input[type='file']",
    'input[type="file"]',
)

# ========== 发布页：标题 / 简介 ==========

# 已实测：placeholder = "填写作品标题，为作品获得更多流量"（class: semi-input）
TITLE_INPUTS: tuple[str, ...] = (
    'input[placeholder*="作品标题"]',
    'input[placeholder*="标题"]',
    '.semi-input[placeholder*="标题"]',
    ".title-container input",
)

# 已实测：div.zone-container.editor-kit-container[contenteditable=true]，
#         data-placeholder = "添加作品简介"。
#
# 注意顺序：`data-placeholder` 只在编辑器**为空**时存在，一旦填了内容就被移除，
# 所以带 placeholder 的候选必须排在后面 —— 否则第二次填写就会找不到编辑器。
DESCRIPTION_EDITORS: tuple[str, ...] = (
    ".zone-container[contenteditable='true']",
    ".editor-kit-container[contenteditable='true']",
    'div[data-placeholder*="作品简介"]',
    'div[data-placeholder*="简介"]',
    "div[contenteditable='true'][data-placeholder]",
    "div[contenteditable='true']",
)

# ========== 发布页：按钮文本 ==========

PUBLISH_BUTTON_TEXTS: tuple[str, ...] = ("发布",)
# 已实测：抖音把"存草稿"叫做「暂存离开」。点它会退回上传页，并在页面上出现
# 「你还有上次未发布的视频，是否继续编辑？」——这条提示正是草稿已保存的证据。
# （该提示是行内 form-hint 条，不是遮罩层，不会挡住后续上传。）
DRAFT_BUTTON_TEXTS: tuple[str, ...] = ("暂存离开", "存草稿", "保存草稿")

# 通用可点击元素容器（用于按文本查找按钮）
BUTTON_SELECTOR = "button, .semi-button, div[role='button'], a"

# ========== 发布页：话题（#）联想 ==========
#
# 已实测结构（在编辑器里输入 # 后出现）：
#   div.mention-suggest-mount-dom
#     └── div.mention-suggest-F02Ddw
#         └── div.mention-suggest-item-container-TVOZMl
#             └── div
#                 └── div.tag-dVUDkJ.tag-hash-o0tpyE   ← 条目，文本形如 "#人工智能654.9亿"
#
# 两个坑：
# 1. 条目文本把话题名和播放量拼在一起（"#人工智能654.9亿"），所以只能按
#    "以 #话题名 开头"匹配，不能等值比较。
# 2. 必须精确到**条目**。早先的候选（如 div[class*='suggest'] div[class*='item']）
#    命中的是装所有条目的**容器**，点它不会选中任何话题。
TOPIC_POPUP_SELECTORS: tuple[str, ...] = (
    "div[class*='mention-suggest-mount-dom']",
    "div[class*='mention-suggest-']",
)

TOPIC_ITEM_SELECTORS: tuple[str, ...] = (
    "div[class*='tag-hash']",
    "div[class*='mention-suggest-item-container'] > div > div",
)

# ========== 发布页：「视频已就绪」怎么判断 ==========
#
# 这里刻意**不提供** class 猜测式的"上传中/已完成"标志。
#
# 踩过的坑：曾经用 `div[class*='progress']` 当"还在上传"的标志，结果它命中了预览
# 播放器的进度条滑块（`rc-slider progress-j6LKcd rc-slider-horizontal`）—— 抖音给
# 播放器的 slider 也挂了含 progress 的业务类名。这种宽泛匹配会永久命中，让等待
# 循环永远不结束（表现为"视频明明上传完了，脚本却卡住"）。
#
# 现在的判据只用两个真机验证过的事实（见 publish_video._is_form_ready）：
#   1. 表单已渲染 —— 上传前页面只有一个 file input；标题框 / 简介编辑器都是上传后
#      才出现的，因此它们是"已进入发布页"的可靠信号。
#   2. 「发布」按钮可点击 —— 视频没就绪前抖音不会放开这个按钮。
#
# 若将来确实需要精确的进度标志，请用**文本**（如 body 里出现"上传中"）而不是 class。

# ========== 定时发布 ==========

SCHEDULE_RADIO_TEXTS: tuple[str, ...] = ("定时发布",)
SCHEDULE_DATETIME_INPUTS: tuple[str, ...] = (
    "input[placeholder*='时间']",
    "input[placeholder*='日期']",
    ".semi-datepicker input",
)

# ========== 可见范围（谁可以看） ==========

# 已实测：发布设置区的「谁可以看」选项文案为 公开 / 好友可见 / 仅自己可见
VISIBILITY_TEXTS: dict[str, tuple[str, ...]] = {
    "公开": ("公开",),
    "好友": ("好友可见", "仅好友可见"),
    "自己": ("仅自己可见", "私密"),
    "粉丝": ("仅粉丝可见",),
}

# ========== 登录页 ==========

# 登录态标志：创作服务平台左侧导航出现这些文本即视为已登录。
# 下面四条来自真实 probe 输出，是页面 body.innerText 的前几行，可直接命中。
LOGGED_IN_TEXTS: tuple[str, ...] = (
    "作品发布",
    "内容管理",
    "收入变现",
    "创作服务",
    # 兜底：部分页面正文里会出现"创作者中心"（首页 <title> 也是这个）
    "创作者中心",
)

# 未登录标志（注意不要放"登录"这种过短的词，否则会命中"退出登录"等无关文案）
LOGGED_OUT_TEXTS: tuple[str, ...] = (
    "扫码登录",
    "验证码登录",
    "登录抖音",
    "手机号登录",
    "请先登录",
    "立即登录",
    "登录/注册",
)

QRCODE_SELECTORS: tuple[str, ...] = (
    "img[src^='data:image'][alt*='二维码']",
    "img[alt*='二维码']",
    ".qrcode img",
    "div[class*='qrcode'] img",
    "div[class*='qrcode'] canvas",
    "canvas",
    "img[src^='data:image']",
)

PHONE_INPUTS: tuple[str, ...] = (
    'input[placeholder*="手机号"]',
    'input[placeholder*="手机"]',
    'input[name="mobile"]',
    'input[type="tel"]',
)

CODE_INPUTS: tuple[str, ...] = (
    'input[placeholder*="验证码"]',
    'input[placeholder*="短信"]',
    'input[name="code"]',
)

SEND_CODE_BUTTON_TEXTS: tuple[str, ...] = ("获取验证码", "发送验证码", "获取短信验证码")
LOGIN_SUBMIT_TEXTS: tuple[str, ...] = ("登录", "立即登录", "确认登录")
AGREE_CHECKBOX_SELECTORS: tuple[str, ...] = (
    "span[class*='checkbox']",
    "input[type='checkbox']",
    "div[class*='agree'] span[class*='box']",
)

PHONE_LOGIN_TAB_TEXTS: tuple[str, ...] = ("验证码登录", "手机号登录", "手机登录")

# ========== 供 probe 自检用 ==========
#
# 这里列出所有「CSS 选择器候选列表」，probe 会逐个检查命中情况并打印出来 ——
# 排查改版时一眼就能看出是哪条候选失效了，而不是只知道"没找到元素"。
#
# 只允许放真正的 CSS 选择器；纯文案常量（如 PUBLISH_BUTTON_TEXTS）不要放进来，
# 否则 probe 会把文案当成选择器去 querySelector 而报语法错误。
SELECTOR_CANDIDATES: tuple[str, ...] = (
    "UPLOAD_INPUTS",
    "TITLE_INPUTS",
    "DESCRIPTION_EDITORS",
    "TOPIC_POPUP_SELECTORS",
    "TOPIC_ITEM_SELECTORS",
    "SCHEDULE_DATETIME_INPUTS",
    "QRCODE_SELECTORS",
    "PHONE_INPUTS",
    "CODE_INPUTS",
    "AGREE_CHECKBOX_SELECTORS",
)
