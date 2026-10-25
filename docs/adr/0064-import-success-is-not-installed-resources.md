# `import` 成功不代表资源装上了：一次静默缺 18 个主题文件的 vendor 打包缺陷

## Context

2026-10-25 改造 `wewrite-publish` 为「无浏览器预检」时，写了 `scripts/preflight.py` 做发布前检查。它报了一条 fail：`theme.available`。第一反应是脚本写错了——毕竟 `wewrite diagnose` 全绿，`bash scripts/bootstrap-wewrite.sh --check` 也全绿。

实测直接否掉了这个猜测：

```
$ HOME=/Users/cyx/repo/NewsAgg/.home .uv-bin/wewrite preview \
    outputs/2026-10-25-ai-side-project-earning-odds.md --theme minimal --no-open -o /tmp/pv.html
Error: Theme file not found: minimal.yaml (searched: /Users/cyx/repo/NewsAgg/.home/.wewrite/themes,
       /Users/cyx/repo/NewsAgg/.uv-tools/wewrite/lib/python3.12/site-packages/wewrite/toolkit/themes)
```

根因是两个目录的差异：

| 位置 | `toolkit/themes/` |
|---|---|
| `vendor/wewrite/wewrite/toolkit/themes/` | 18 个 yaml，齐全 |
| `.uv-tools/wewrite/lib/python3.12/site-packages/wewrite/toolkit/themes/` | **目录不存在** |

`grep -c themes .../wewrite-4.2.1.dist-info/RECORD` = **0** —— 上游 wheel 的 RECORD 里其实有这 18 个文件，是本项目的重建丢失了它们。原因在 `vendor/wewrite/pyproject.toml` 的

```toml
[tool.setuptools.package-data]
wewrite = ["data/*"]
```

`data/*` 只覆盖了 `wewrite/data/`，漏了 `wewrite/toolkit/themes/*.yaml`，而 setuptools 默认不打包非 `.py` 文件。

**后果比「预览失败」严重**：`theme.py:37 _theme_search_dirs()` 先查 `$WEWRITE_HOME/themes`（用户自备，空）、再查包内 `toolkit/themes`（不存在），于是 `load_theme()` 抛 `FileNotFoundError`。而 `preview` 和 `publish` **都在加载主题这一步就退出，根本走不到微信 API**。也就是说整个 wewrite 发布链路在本仓从 2026-10-15 起就是坏的，只是没有任何人跑过它。

## Decision

### 一、补上 package-data 的那一条

```toml
wewrite = ["data/*", "toolkit/themes/*.yaml"]
```

这不是「修改上游代码」。ADR-0015/0060 定下的规矩是「`vendor/wewrite/pyproject.toml` 只负责能从这份源码装出来，不含本项目任何行为修改」，而**打包资源文件正是它的职责范围**。上游 wheel 里本来就有这 18 个 yaml（RECORD 可查），这条改动是**对齐上游**，不是给上游加行为。`PROVENANCE.md` 的「为什么不改这里的代码」约束的是 `wewrite/` 包内的 `.py`，不约束构建定义。

### 二、`bootstrap-wewrite.sh --check` 必须点名检查非 .py 资源

原来的自检是：

```python
import wewrite.cli        # ✓ 通过
import PIL                # ✓ 通过
```

这两条在缺 18 个 yaml 时**全部通过**。这是本次最值得记的一点：**`import` 成功只能证明 `.py` 装上了，证明不了数据和模板装上了。**

新增三条检查，直接比对目录而不是信任 import：

```python
theme_dir = tk / "themes"
if not theme_dir.is_dir():                       # 目录缺失
    sys.exit(1)
n_themes = len(list(theme_dir.glob("*.yaml")))
if n_themes == 0:                                # 目录在但空
    sys.exit(1)
load_theme(names[0])                             # 真的加载一次
```

刻意写「目录不存在」和「目录存在但为空」两条独立判断：只查后者时，`glob` 在缺目录上返回空列表，会把「目录缺失」误报成「没有主题」。

### 三、为什么不是「在 SKILL.md 里写补救命令」

`wewrite-dev` 最初的建议是让用户手动 `cp vendor/.../themes/*.yaml "$WEWRITE_HOME/themes/"`。这个做法能让当天的工作继续，但它把「仓库装错了」变成「使用者的一次手工步骤」，下次 `bootstrap` 重建环境后又会复现，而且没人记得要做。

**装出来的东西不对，就修装的过程。** 补救命令只在无法改安装链路时才该出现。

## 验证

```
$ bash scripts/bootstrap-wewrite.sh
...
✓ 内置主题 18 个（toolkit/themes/*.yaml）
✓ load_theme('bauhaus') 可加载
✓ 环境检查通过

$ HOME=... .uv-bin/wewrite preview outputs/2026-10-25-ai-side-project-earning-odds.md \
    --theme minimal --no-open -o /tmp/pv.html
Title: 想靠 AI 做副业的人：年入 10 万美元的只有 2.4%
Output: /tmp/pv.html     # 20547 字节，exit 0
```

`preflight.py` 的 `theme.available` 由 fail 转 pass：

```
"check": "theme.available", "status": "pass",
"detail": "主题 professional-clean 已找到（来源：默认值（cli.py: cfg.get('theme','professional-clean')））：
           .../site-packages/wewrite/toolkit/themes/professional-clean.yaml"
```

`summary {pass:13, warn:5, fail:0}`，`verdict=warn` 只因凭证未配置（`wechat.appid`/`secret` 三项全 false），属预期。

## Consequences

**好处**
- wewrite 的 preview / publish 链路在本仓**第一次真正可运行**。
- bootstrap 自检从此能抓住「非 .py 资源漏打包」这一类缺陷，而不只是「能不能 import」。

**残余风险**
- **这条缺陷从 2026-10-15 潜伏到 10-25，共 10 天无人发现**，因为没有任何测试或自检跑过 `load_theme()`。同类风险仍在：`vendor/wewrite/wewrite/data/` 下目前只有 1 个 md，但如果上游将来的版本增加更多资源目录，`package-data` 仍会漏——**每次升级 wewrite 必须重新比对 `RECORD` 里 vendor 源码树之外的文件**。
- 主题修好了，但**凭证仍未配置**（`~/.wewrite/config.yaml`、`~/.config/wewrite/config.yaml`、仓库 `.wewrite/config.yaml` 三处都不存在，相关 env 也没设）。所以「能预览、不能发布」是当前的真实状态，不是缺陷。
- 另外发现一处状态目录混淆：仓库内有 `.wewrite/style.yaml`，但 `WEWRITE_HOME` 生效值是 `~/.wewrite`（不存在）。**wewrite 只读后者，不会读仓库里的 `.wewrite`。** `preflight.py` 已把这条作为 warn 报出，但没有改动解析逻辑——`paths.py` 属于 vendor。

## 一条更普遍的判据

**「装好了」的判据不是 `import` 通过，是「运行时真正用到的每一个文件都在」。**

本仓已经有了一族「记录了但没有实现」（ADR-0059/0062），这一条是同族的另一种形状：**「装了但没有装全」**。两者的共同点是——系统在某一步之后就不再检查，而失效发生在没人看的地方。区别在于前者缺的是「读字段的那一行代码」，后者缺的是「一个不是 .py 的文件」。

判据的落法是一样的：**给每个「被认为已完成」的步骤配一个能被自动执行、且会失败的检查**。`bootstrap --check` 原来那两条 `import` 之所以漏，是因为它们检查的是「能不能用」，而缺陷在「用到的资源在不在」——这两件事不重叠。
