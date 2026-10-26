---
name: wewrite-publish
description: |
  WeWrite 排版发布模块：把 Markdown 做成微信预览，或在用户明确授权后推入公众号草稿箱；
  也支持主题画廊和图片帖。只处理微信公众号。
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
---

# wewrite-publish — 排版、预览、草稿箱

## 前置

用户指定文章时使用该文件；否则 `wewrite run show`。如果
`artifacts.illustrated_article` 存在且非空就使用它，否则使用 `artifacts.article`；读取标题、
摘要、封面、预览路径、发布权限和降级标记。已完成文章可以直接排版或发布，不要恢复写作
任务。主题取 style.yaml 的 `theme`，默认 `professional-clean`。

排版和发布不得自动调用 `wewrite-visual`。没有封面时照常生成本地预览；只有用户另行要求
配图时才进入视觉模块。

用户本轮明确要求“推到草稿箱/发布”时，有任务先运行
`wewrite run permission publish allow`；用户撤回时运行 `wewrite run permission publish deny`。
只排版、预览或“完整制作”都不能授予发布权限。

有任务时进入：

```bash
wewrite run step publish in_progress
```

## 为什么不需要浏览器

**这条链路不打开、不控制任何浏览器。** 这不是猜测，是可以跑出来的结论：

```bash
python3 scripts/no_browser_check.py
```

它静态扫描 wewrite 包，证据分四块：

1. **发布走官方 HTTP API。** `wewrite publish` 的实现是
   `toolkit/wechat_api.py` + `toolkit/publisher.py`，全部用 `requests` 打这四个接口：
   - `GET  https://api.weixin.qq.com/cgi-bin/token` —— 取 access_token（进程内缓存到 `expires_in - 300` 秒）
   - `POST https://api.weixin.qq.com/cgi-bin/media/uploadimg` —— 正文图转存，换微信图床 URL
   - `POST https://api.weixin.qq.com/cgi-bin/material/add_material?type=image` —— 上传封面，拿永久 `media_id`
   - `POST https://api.weixin.qq.com/cgi-bin/draft/add` —— 建草稿，返回 `media_id`

2. **发布链路的 import 里没有任何浏览器/驱动库**，出网域名只有 `api.weixin.qq.com` 一个。
   脚本会把这条硬核对一遍；一旦哪天出现别的域名或自动化框架，它 exit 1 报警。

3. **`draft/add` 是官方接口，不是模拟点击。** 所以不需要登录后的 Cookie、不需要扫码、
   不需要保持一个浏览器窗口活着。**唯一需要联网的只有 access_token 的取用与刷新**，
   而 token 是 `appid + secret` 换来的，跟浏览器无关。

4. **凭证在 `config.yaml`，不在浏览器里。** 解析顺序（对齐 `toolkit/config.py`）：
   先读 `$WEWRITE_HOME/config.yaml`（默认 `~/.wewrite/config.yaml`），读不到再退到
   `~/.config/wewrite/config.yaml`；然后环境变量 `WECHAT_APPID` / `WECHAT_SECRET` /
   `WECHAT_AUTHOR` 覆盖同名项。**注意 `config.py` 只在 `is None` 时跳过，所以设成空
   字符串也会把文件里的值覆盖成空** —— `preflight.py` 会单独报这个坑。

包内确实有浏览器字样，但**都不在发布路径上**：`commands/fetch_article.py` 用
Playwright/Camoufox 抓取文章正文（是**读**公众号文章，不是发），
`toolkit/cli.py:13` 的 `import webbrowser` 只是把本地预览 HTML 交给系统默认浏览器打开
（`--no-open` 可关）。发布用不到这两个。

⚠️ **`$WEWRITE_HOME` 与仓库里的 `.wewrite/` 不是一回事。** 仓库内 `.wewrite/` 通常只放
`style.yaml`（写作风格），而 wewrite 读的是 `$WEWRITE_HOME`（默认 `~/.wewrite`）。本仓的
采集脚本会 `export WEWRITE_HOME="$REPO/.wewrite"`；如果你在别的 shell 里直接发布，那个
环境变量不在，就会变成「style.yaml 明明有，却说没配置」。`preflight.py` 会把这个差异
显式打印出来。

⚠️ **本仓当前装出来的 wewrite 缺内置主题。** `vendor/wewrite/` 里有 18 个主题 yaml，
但 `.uv-tools/wewrite/.../wewrite/toolkit/themes/` **目录不存在** —— 因为
`vendor/wewrite/pyproject.toml` 的 `[tool.setuptools.package-data]` 只写了 `data/*`，
没包含 `themes/*.yaml`。后果：`load_theme()` 抛
`FileNotFoundError: Theme file not found: professional-clean.yaml`，
**preview 和 publish 都在加载主题这一步就退出**，根本走不到微信 API。
`wewrite diagnose` 不检查主题，所以它会显示一切正常。

补救（两种都行，`preflight.py` 的 `theme.available` 会告诉你当前能不能找到）：

```bash
# 放到用户主题目录（load_theme 优先查这里），不动安装产物
mkdir -p "${WEWRITE_HOME:-$HOME/.wewrite}/themes"
cp vendor/wewrite/wewrite/toolkit/themes/*.yaml "${WEWRITE_HOME:-$HOME/.wewrite}/themes/"
```

或修 `vendor/wewrite/pyproject.toml` 的 package-data 后重跑
`bash scripts/bootstrap-wewrite.sh`（注意 vendor 下的代码默认不该手改，
见 `vendor/wewrite/PROVENANCE.md`；这条属于配置疏漏而非改上游代码）。

## 预检

**在 `wewrite preview` 之前先跑预检**（纯本地、不联网、不调微信 API）：

```bash
# 只做本地预览
python3 scripts/preflight.py --article {article} --cover {cover} --theme {theme}

# 这一轮真的要推草稿箱（凭证/封面/权限门升级为硬失败）
python3 scripts/preflight.py --article {article} --cover {cover} --theme {theme} --publish

# 摘要别内联到命令行：中文过 shell 容易被引号/编码搞坏，写成文件传
python3 scripts/preflight.py --article {article} --cover {cover} \
  --theme {theme} --digest-file {digest.txt} --publish
```

输出 JSON，看 `verdict` 与 `exit_code`：

| exit | verdict  | 怎么办                                     |
| ---- | -------- | ------------------------------------------ |
| 0    | `pass`   | 继续                                       |
| 1    | `warn`   | 可以继续，但把 `checks` 里 `warn` 的项报告给用户 |
| 2    | `fail`   | **停下**，修掉 `fail` 项，不要发布         |

预检覆盖（每条的限制值都写在输出的 `limits` 里）：

- `WEWRITE_HOME` 解析结果：生效路径、是否存在、`config.yaml` 落在哪、是否来自 env 覆盖；
  以及仓库内 `.wewrite/` 与它的差异
- 凭证：`wechat.appid` / `secret` / `author` 是否已配置（**只报 true/false，绝不输出 secret；
  appid 只显示前 6 位加掩码**）
- 封面：文件存在且可读、格式受支持（`add_material` 收 png/jpeg/gif/bmp）、体积 ≤10MB、
  尺寸能否解析（解析不了就跳过该检查，不硬依赖 Pillow）、比例是否贴合微信的 `2.35_1` / `1_1`
- 标题：存在、≤32 字、无换行/无 HTML 标签
- 摘要：**≤120 字节**（注意是字节不是字符 —— 中文 3 字节/字，120 字节约 40 个汉字）
- 正文：200-20000 字，且 <1MB
- 正文图片：≤10 张、本地可读、**仅 jpg/png 且 <1MB**（`uploadimg` 的限制比封面严）
- 表格：≤4 列
- 任务权限门：`permissions.publish=true`、`flags.skip_publish=false`
- 主题：`load_theme()` 能不能真的找到那个 yaml（见上面「缺内置主题」一节；
  `wewrite diagnose` 查不出这个，只有预检查）

预检通过的标准之外，这些人工判断仍然成立：

- 发布草稿必须有封面。没有封面不能假设微信会补默认图，改走本地预览。
- 发布必须同时满足：`permissions.publish=true`、`flags.skip_publish=false`、用户本轮没有撤回。

## 动作

始终先生成本地预览，有任务时保存到 `artifacts.preview`；任务外文章保存到原文件旁：

```bash
wewrite preview {article} --theme {theme} --no-open -o {preview}
```

**加 `--no-open`**：不加的话 wewrite 会用系统默认浏览器打开预览 HTML 给你看。这不是
发布需要，只是方便人眼检查；在 Agent 里跑一律带上它，免得平白弹一个窗口。

确认预览产物存在且通过兼容性校验。若没有明确发布权限，流程到此结束；“写一篇”“完整制作”
都不等于允许发布。

只有用户明确要求草稿箱、且 `preflight.py --publish` exit 0/1 时执行：

```bash
wewrite publish {article} --cover {cover} --theme {theme} --title "{title}" --digest "{digest}"
```

发布失败时保留预览，不自动重试产生外部动作。有任务时更新 `publish.preview_html`；成功时再写
`publish.media_id`，随后：

```bash
wewrite run step publish completed
```

## 辅助功能

| 用户说   | 动作                                                       |
| -------- | ---------------------------------------------------------- |
| 看主题   | `wewrite gallery --no-open`（不跑预检，纯本地）            |
| 换主题   | 重新 preview；只有再次明确要求才重新 publish               |
| 做图片帖 | 先确认用户确实要推草稿箱，再执行 `wewrite image-post`      |
| 只排版   | 只 preview                                                 |
