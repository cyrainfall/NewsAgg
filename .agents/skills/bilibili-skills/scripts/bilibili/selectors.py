"""B站创作中心投稿页选择器常量。

B站创作中心（member.bilibili.com）用的是自研的 BCC 组件库（Vue 2），类名形如
``bcc-button`` / ``select-item-cont``，**不带 hash 后缀**，所以这里可以适度依赖
``class*='前缀'`` 匹配，比抖音（Semi Design + hash 类名）宽松一些。

但仍然遵守同样的两条纪律：

1. 每个常量都是**候选列表（按优先级排序，第一个命中即用）**。改版时**追加**候选，
   不要删掉旧的 —— B站会做灰度发布，同一时期不同账号的 DOM 可能不一样。
2. **不写死整串类名**，只用稳定的语义前缀 / 属性（placeholder / accept /
   contenteditable）和可见文本。

⚠️ 关于可信度，这里必须说实话：下表是根据 B站 2021 与 2025 两版公开自动化方案、
以及投稿页的公开结构信息起草的，**尚未在真实投稿页上逐条验证**。第一次使用前请按
``SKILL.md`` 的"选择器自检"跑一次 ``python scripts/cli.py probe``，把输出里
``selector_report`` 中为 ``false`` 的组补上真实选择器。这是唯一需要人工做一次的
事情，之后就能长期复用。
"""

# ========== 投稿页：视频文件上传 ==========
#
# 页面上的 input[type=file] 不止一个（封面也有一个），所以 UPLOAD_INPUTS 只作为
# probe 报告用；真正的定位由 publish_video._find_video_input 用 JS 按 accept
# 里的视频扩展名筛选，并在找不到时**拒绝**退化成"随便选一个 file input"。
UPLOAD_INPUTS: tuple[str, ...] = (
    "#video-upload",
    'input[type="file"][accept*="mp4"]',
    'input[type="file"][accept*="video"]',
    'input[type="file"][accept*="mov"]',
    'input[type="file"][accept*="mkv"]',
    "div[class*='upload'] input[type='file']",
)

# ========== 投稿页：稿件标题 ==========
#
# 实测：placeholder = "请输入稿件标题"，class="input-val"，宽 670px。
#
# ⚠️ 刻意**不**把 `input.input-val` 放进来：标签输入框的 class 也是 `input-val`
#    （同一套样式复用），按它定位有几率命中标签框 —— 那会把标题写进标签里，
#    而且两者长得像、报错也看不出来。placeholder 是区分它俩的可靠特征。
TITLE_INPUTS: tuple[str, ...] = (
    'input[placeholder*="稿件标题"]',
    'input[placeholder*="标题"]',
    ".video-title-container input",
    ".title-container input",
    "input[class*='title']",
)

# ========== 投稿页：稿件简介 ==========
#
# B站简介编辑器有两个时代：
#   - 新版：Quill 富文本，可编辑区是 .ql-editor[contenteditable=true]
#   - 旧版：自研富文本，根节点带 editor_id="desc_at_editor"（2021 教程用它定位）
#   - 更早：普通 textarea
# 三种都留候选。注意带 data-placeholder 的候选要排后 —— placeholder 只在编辑器为
# 空时存在，填过内容就会消失，排前面会导致第二次填写找不到编辑器。
DESCRIPTION_EDITORS: tuple[str, ...] = (
    ".ql-editor[contenteditable='true']",
    "div[editor_id='desc_at_editor']",
    "div[class*='desc'][contenteditable='true']",
    'div[contenteditable="true"][data-placeholder*="简介"]',
    'div[data-placeholder*="简介"]',
    "div[contenteditable='true']",
)

DESCRIPTION_TEXTAREAS: tuple[str, ...] = (
    'textarea[placeholder*="简介"]',
    'textarea[placeholder*="描述"]',
    'textarea[placeholder*="介绍"]',
    ".desc-textarea textarea",
)

# ========== 投稿页：分区（一级 / 二级） ==========
#
# ⚠️ 公开资料（2021 教程）给的是 `.pre-item-content` / `.item-main`，实测在**当前的
#    B站投稿页上完全不存在** —— 照抄的结果是分区面板永远校验不过、分区根本选不了。
#    下面是真机 dump 出来的结构：
#
#     div.select-controller                        ← 触发器（226px，文本 = 当前分区名）
#       └── p.select-item-cont[-inserted]          ← 内部只负责显示分区名的小块（28px）
#
#     div.drop-list-v2-container.human-type-list   ← 展开后的面板
#       └── div.drop-list-v2-content-wrp
#             └── div.drop-list-v2-item             ← 条目（29 个一级分区，各 40px 高）
#                   └── div.drop-list-v2-item-cont
#                         └── div.item-cont-main   ← 分区名
#             · 当前选中的条目额外带 drop-list-v2-item-selected
#
# 触发器的顺序不能反：先命中那个 28px 的小块，真实鼠标点击会打在小块的坐标上，
# 面板根本不展开（实测点下去毫无反应，然后报"找不到一级分区"）。
CATEGORY_TRIGGER_SELECTORS: tuple[str, ...] = (
    "div[class*='select-controller']",
    ".select-controller",
)

# 分区面板**展开后才会出现**的元素，用来确认"面板真的开了"。
# 只能放面板独有的类名；`select-item-cont` 在面板关闭时也存在（就是那个 28px 的小块），
# 拿它判断等于永远为真，校验形同虚设。
CATEGORY_PANEL_SELECTORS: tuple[str, ...] = (
    "div[class*='drop-list-v2-item']",
    "div[class*='drop-list-v2-content-wrp']",
)

# 一级 / 二级条目用的是同一个组件，所以共用一个候选池。
# 二级面板是这样出现的：点掉一级分区之后，同一位置再渲染出该一级下的二级列表。
#
# ⚠️ 叶子元素是 `<p class="item-cont-main">`，**不是 div**。写成
#    `div[class*='item-cont-main']` 会一个都匹配不到（实测踩到，表现是
#    "未在面板中找到一级分区"）。所以这里刻意不加标签前缀。
CATEGORY_ITEM_POOL = (
    "[class*='item-cont-main'], [class*='drop-list-v2-item'], [class*='drop-list-v2-item-cont']"
)

# 触发器上的占位文案：出现它就说明还没选分区
CATEGORY_PLACEHOLDER_TEXTS: tuple[str, ...] = ("请选择分区", "选择分区", "请选择")

# ========== 投稿页：创作声明（必填） ==========
#
# 实测结构（真机 dump）：
#
#   div.statement-main > div.statement-content
#     └── div.bcc-select                                  ← 控件
#           ├── div.bcc-select-input-wrap
#           │     ├── input.bcc-select-input-inner         ← 只读框
#           │     │      placeholder = 「请选择符合您视频内容的创作声明」
#           │     └── i.bcc-icon-ic_drop-down              ← 箭头，pointer-events: none
#           └── div.bcc-select-list-wrap                   ← 收起时 height 0
#                 └── ul.bcc-select-option-list
#                       └── article.vu-hover-tips.option-hover-tips
#                             └── li.bcc-option > span     ← 选项，点它
#
# 实测的选项文本（可据此做别名映射）：
#   内容无需标注 / 含AI生成内容 / 含虚构演绎内容 / 内容含营销信息 /
#   个人观点，仅供参考 / 内容为转载 / 内容为自制：未经作者允许，禁止转载
#
# ⚠️ 交互陷阱（真实踩过，浪费了大量时间）：这个控件在一个**被程序化反复操作过的**
#    页面上会失去响应 —— 可信点击、pointerdown、focus、事件序列都送达了，
#    但 Vue 状态就是不切到展开。**重新加载投稿页后立刻就能打开**。
#    所以打不开时不要怀疑选择器，先让用户刷新页面。
DECLARATION_TRIGGER_SELECTORS: tuple[str, ...] = (
    "input.bcc-select-input-inner",
    ".statement-content input",
)

# 展开后才会出现的面板（收起时 height 为 0，所以用它判断"真的开了"）
DECLARATION_PANEL_SELECTOR = "ul.bcc-select-option-list"
# 选项条目。叶子是 `li.bcc-option > span`，外层套着 tooltip 用的 article。
DECLARATION_OPTION_POOL = "li[class*='bcc-option'], [class*='bcc-option']"

# 用户可能传的简写 → 页面上的实际选项文案。
DECLARATION_TEXTS: dict[str, tuple[str, ...]] = {
    "AI": ("含AI生成内容",),
    "含AI": ("含AI生成内容",),
    "虚构": ("含虚构演绎内容",),
    "营销": ("内容含营销信息",),
    "观点": ("个人观点，仅供参考",),
    "转载": ("内容为转载",),
    "自制": ("内容为自制",),
    "无": ("内容无需标注",),
    "无需标注": ("内容无需标注",),
}

# ========== 投稿页：标签 ==========
#
# 实测来源（多个教程一致）：placeholder 为「按回车键Enter创建标签」，输入后回车创建。
TAG_INPUTS: tuple[str, ...] = (
    'input[placeholder*="按回车键"]',
    'input[placeholder*="标签"]',
    ".tag-input input",
    "input[class*='tag']",
)

# 「已选标签」所在的区域。校验标签有没有真的创建，必须**限定在这个区域里查** ——
# 全页搜文本会被「推荐标签」里的同名条目骗到。
TAG_SELECTED_REGION_SELECTORS: tuple[str, ...] = (".tag-pre-wrp", ".tag-input-wrp")

# 已选标签的单个条目。实测结构（BCC 的 label 组件）：
#
#   div.tag-pre-wrp
#     └── div.label-item-v2-container
#           ├── p.label-item-v2-content   ← 标签文字
#           └── svg                       ← 删除按钮（不含文字）
#
# ⚠️ 千万别用 `div[class*='tag-item']` / `div[class*='tag-list'] span`：那两个命中的是
#    **推荐标签**（hot-tag-item / tag-list），不是已选标签。用它们做校验会同时产生
#    两种错误 —— 实测都真实发生过：
#      · 漏报：已选中的「科普」「人工智能」查不到 → 明明成功却报失败，还会去"清理残留"
#      · 误报：推荐列表里的「代码」「编程」被当成已选 → 明明没成功却报成功
TAG_ITEM_SELECTORS: tuple[str, ...] = (
    ".label-item-v2-content",
    "div[class*='label-item'] p",
    "div[class*='label-item-v2-container']",
)

# 已选标签条目上的**删除按钮**（点它移除该标签）。结构：
#   div.label-item-v2-container > (p.label-item-v2-content, svg)
#
# ⚠️ 这些选择器是**相对于单个标签条目**的，不要加进来当全局选择器用，
#    因此也不要放进 SELECTOR_CANDIDATES（probe 会拿它去全页 querySelector）。
#    用途见 publish_video._remove_all_tags。
TAG_DELETE_SELECTORS: tuple[str, ...] = (
    "svg",
    "[class*='close']",
    "[class*='icon-close']",
)

# ========== 投稿页：按钮 ==========
#
# 已公开文案：「立即投稿」（2021 教程用 button[text()="立即投稿"]）。
PUBLISH_BUTTON_TEXTS: tuple[str, ...] = ("立即投稿", "提交投稿", "发布")
DRAFT_BUTTON_TEXTS: tuple[str, ...] = ("存草稿", "保存草稿", "暂存")
NEXT_STEP_BUTTON_TEXTS: tuple[str, ...] = ("下一步",)

# 投稿确认弹窗里的按钮。刻意只放**专属文案**，不放通用的「确定」——
# 页面上任何弹窗（封面裁剪、协议提示）都有「确定」，点错一个就可能把没填完的稿件发出去。
PUBLISH_CONFIRM_TEXTS: tuple[str, ...] = ("确认投稿", "继续投稿")

# 按文本找按钮时的候选池。
#
# ⚠️ `span[class*='submit']` / `div[class*='submit']` 是实测补上的：B站的
#    「立即投稿」「存草稿」既不是 <button> 也不是 .bcc-button，而是
#    `span.submit-add` / `span.submit-draft`，外面套一层 `div.submit-container`。
#    只按 button / .bcc-button 找的话，永远找不到投稿按钮（实测死等到超时）。
BUTTON_POOL = (
    "button, .bcc-button, [role='button'], div[class*='button'], "
    "span[class*='submit'], div[class*='submit']"
)

# ========== 投稿页：封面 ==========
#
# ⚠️ 封面是**必填**的，不是可选项。实测没设封面时点「立即投稿」会被拦下并提示
#    「请先上传封面」（那条 toast 几秒后自己消失，很容易被误判成"点了没反应"）。
#    页面会给出几张「系统推荐封面」，用户点一张即可；脚本也可以走 --cover 自己传。
# 实测入口文案是「添加封面」（公开资料里写的"上传封面/编辑封面"在这个版本上没有出现）。
COVER_ENTRY_TEXTS: tuple[str, ...] = ("添加封面", "上传封面", "更换封面", "编辑封面")

# 页面上出现这个文案，就说明还没设置封面。和入口文案是同一句话 —— 它既是"没封面"
# 的证据，也是点击入口。
COVER_MISSING_TEXTS: tuple[str, ...] = ("添加封面",)
COVER_INPUTS: tuple[str, ...] = (
    'input[type="file"][accept*="image"]',
    'input[type="file"][accept*="png"]',
    'input[type="file"][accept*="jpg"]',
    "div[class*='cover'] input[type='file']",
)
# 实测封面编辑器的确认按钮是 `div.button.submit`，文案「完成」（旁边是「取消」）。
COVER_CONFIRM_TEXTS: tuple[str, ...] = ("完成", "确定", "确认", "保存")
# ⚠️ 候选池必须包含 `div[class*='button']`：封面编辑器的按钮不是 <button>，
#    而是 `div.button.submit`，只写 `button` 一个都匹配不到（实测踩到）。
#    另外这个按钮常常在**视口外**（y 超出视口高度），需要滚动才能点到 ——
#    click_element 会先 scrollIntoView，必要时还要靠扩展里的祖先滚动。
COVER_DIALOG_POOL = (
    "div[class*='cover-editor'] div[class*='button'], "
    "div[class*='bcc-dialog'] button, div[class*='bcc-dialog'] div[class*='button'], "
    "div[class*='modal'] button, div[class*='dialog'] button, div[class*='button']"
)

# ========== 投稿页：定时发布 ==========
SCHEDULE_RADIO_TEXTS: tuple[str, ...] = ("定时发布",)
SCHEDULE_DATETIME_INPUTS: tuple[str, ...] = (
    'input[placeholder*="时间"]',
    'input[placeholder*="日期"]',
    "div[class*='bcc-date-picker'] input",
    "input[class*='date']",
)

# ========== 页面状态文本 ==========
#
# 判断「视频是否已就绪」优先用结构事实（表单是否渲染、按钮是否可点击），
# 其次用这些**完整短语**。不要用单个短词做 class 匹配 —— 见 CLAUDE.md 里
# 抖音踩过的"progress 类名命中播放器进度条"的坑，B站同样有播放器。
# ⚠️ 「上传完成」**不要**当作硬性就绪信号。实测它会同时出现在两个地方：
#    - 分P 列表里那一行的状态（真正的完成信号）
#    - 页面底部常驻的提示"信息填完后，就可投稿！**不需等待上传完成**哦~"
#    后者在上传还没开始时就已经在页面上了，按文本匹配会立刻为真。所以这里只把它
#    作为"给人看的证据"，就绪判断一律走结构事实（见 publish_video._detect_ready_layout）。
UPLOAD_DONE_TEXTS: tuple[str, ...] = ("上传完成", "视频上传完成", "转码完成")
UPLOADING_TEXTS: tuple[str, ...] = ("正在上传", "上传中", "视频转码中", "正在转码")

UPLOAD_FAILURE_KEYWORDS: tuple[str, ...] = (
    "上传失败",
    "转码失败",
    "格式不支持",
    "视频损坏",
    "上传出错",
)

# 投稿结果文案。「投稿成功」类关键词要足够具体：B站页面上常驻着创作规范的
# 提示文案（含"违规"之类的字眼），用短词会把每一次投稿都误判成风控失败。
PUBLISH_SUCCESS_KEYWORDS: tuple[str, ...] = (
    "投稿成功",
    "稿件投递成功",
    "稿件已提交",
    "已提交审核",
    "投稿完成",
)

# 「投稿失败」不放在风控里：它是通用失败，放进风控会让本来就只是网络抖动的
# 失败被报成"账号被风控"，把用户引到完全错误的方向。
# 「提交中」状态。点掉「立即投稿」后按钮文案会变成「提交中...」（class 加 btn-loading），
# 这是**正在提交**的正常中间态。要认它，否则会把"正在提交"误判成"什么都没发生"，
# 报出"未捕获到明确结果"这种没用的结论。
SUBMITTING_TEXTS: tuple[str, ...] = ("提交中", "投稿中", "发布中")

PUBLISH_FAILURE_KEYWORDS: tuple[str, ...] = (
    *UPLOAD_FAILURE_KEYWORDS,
    "投稿失败",
    "投稿出错",
    "提交失败",
)

# 只看真正的账号级限制。同样必须是完整短语 —— B站每篇稿件旁边都挂着创作规范
# 提示（含"违规"字样），用短词会让每一次投稿都被误判成风控。
RISK_KEYWORDS: tuple[str, ...] = (
    "账号异常",
    "禁止投稿",
    "审核未通过",
    "违反社区规范",
    "稿件未通过",
    "账号已被封禁",
)

# ========== 登录态判断 ==========
#
# 已登录：创作中心左侧导航一定存在这些文案。
LOGGED_IN_TEXTS: tuple[str, ...] = (
    "投稿管理",
    "稿件管理",
    "数据中心",
    "粉丝管理",
    "收益管理",
    "互动管理",
    "创作中心",
)

# 未登录：登录页（passport.bilibili.com）上的文案。
# 注意不要放"登录"这种两个字以内的词，否则会命中"退出登录"、「登录后查看评论」等无关文案。
LOGGED_OUT_TEXTS: tuple[str, ...] = (
    "扫描二维码登录",
    "二维码登录",
    "密码登录",
    "短信登录",
    "请先登录",
    "立即登录",
    "登录后查看",
)

# ========== 登录页：二维码 ==========
QRCODE_SELECTORS: tuple[str, ...] = (
    "div[class*='qrcode'] canvas",
    "div[class*='qrcode'] img",
    "div[class*='login-scan'] canvas",
    "div[class*='login-scan'] img",
    "canvas[class*='qrcode']",
    "img[class*='qrcode']",
    "canvas",
    "img[src^='data:image']",
)

# ========== 登录页：短信验证码登录 ==========
PHONE_INPUTS: tuple[str, ...] = (
    'input[placeholder*="手机号"]',
    'input[placeholder*="手机"]',
    'input[name="tel"]',
    'input[type="tel"]',
)

CODE_INPUTS: tuple[str, ...] = (
    'input[placeholder*="验证码"]',
    'input[placeholder*="短信"]',
    'input[name="smsCode"]',
)

SEND_CODE_BUTTON_TEXTS: tuple[str, ...] = ("获取验证码", "发送验证码", "获取短信验证码")
LOGIN_SUBMIT_TEXTS: tuple[str, ...] = ("登录", "立即登录", "确认登录")

AGREE_CHECKBOX_SELECTORS: tuple[str, ...] = (
    "span[class*='checkbox']",
    "div[class*='agree'] span[class*='box']",
    "input[type='checkbox']",
)

PHONE_LOGIN_TAB_TEXTS: tuple[str, ...] = ("短信登录", "验证码登录", "短信验证码登录")

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
    "DESCRIPTION_TEXTAREAS",
    "TAG_INPUTS",
    "TAG_ITEM_SELECTORS",
    "CATEGORY_TRIGGER_SELECTORS",
    "DECLARATION_TRIGGER_SELECTORS",
    "SCHEDULE_DATETIME_INPUTS",
    "COVER_INPUTS",
    "QRCODE_SELECTORS",
    "PHONE_INPUTS",
    "CODE_INPUTS",
    "AGREE_CHECKBOX_SELECTORS",
)
