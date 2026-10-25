# ADR-0015 的 vendor 落到实处，以及两个「换台机器就复现不出来」的缺陷

## Context

ADR-0059 记录了四处「记录了但没有实现」的缺口，其中一项是 **ADR-0015 Decision 二「wewrite 以源码形式 vendor 入仓并自行安装，不运行上游 install.sh」**。

实测当时的状态：`.gitignore:3` 排除 `.uv-tools/`，`git ls-files | grep -c '^\.uv-tools\|^vendor'` = 0；`git ls-files | grep -ci wewrite` = 41，全是 `.agents/skills/wewrite-*/` 下的 SKILL.md 与 references，**那是技能文档，不是包源码**。也就是说 wewrite 一直是 `uv tool install wewrite==4.2.1` 从 PyPI 装的，源码从未入仓。

ADR-0059 当时只记录、未修，理由是「比原先记录的更难落实」。本 ADR 把它做完。

## Decision

### 一、vendor 的形态：包源码 + 最小重建的构建定义 + 依赖锁定 + 来源说明

`vendor/wewrite/` 四件东西：

| 文件 | 作用 |
|---|---|
| `wewrite/` | 从当时实际运行的 wheel 解包出来的包源码，53 个文件，436K，**逐字节相同**于当时 `.uv-tools/wewrite/.../site-packages/wewrite/` |
| `pyproject.toml` | **不是上游的构建定义**，是按上游 `METADATA` 重建的最小可安装描述（纯 Python、`py3-none-any`）。只负责「能从这份源码装出来」，不含本项目任何修改 |
| `requirements.lock` | 7 个依赖的精确版本，取自当时实际运行的那个 venv |
| `PROVENANCE.md` | 上游地址、版本、许可证、树摘要、升级步骤、以及「为什么不改这里的代码」 |

新增 `scripts/bootstrap-wewrite.sh`：建 `.uv-tools/wewrite` venv（Python 3.12）→ 按 lock 装依赖 → `--no-deps` 从 `vendor/wewrite` 装 wewrite → 软链 `.uv-bin/wewrite` → 自检。装出来的布局与原来的 uv tool 安装一致，所以调用侧（`run_all.sh` 的 `COVER_PY`、`.uv-bin/wewrite`）一行都没改。

### 二、为什么保留 install.sh 的禁用

上游 `install.sh` 会把 skill 链到 `~/.claude/skills/` 等全局目录并迁移旧状态。ADR-0015 的 Context 其二已经写明这与「仓内副本 + 钉版本」冲突。本实现完全不碰它。

### 三、升级是一次有意的动作

写进 `PROVENANCE.md` 的升级清单：取新 wheel 解开替换 → 同步 `pyproject.toml` 的 version 与 METADATA 字段 → 更新树摘要 → 重跑 bootstrap 与测试 → 重新校验契约（ADR-0004）。不是 `uv pip install -U`。

## 两个缺陷

### 缺陷一：树摘要把「仓库放在哪」编了进去

第一次算摘要用的是「从仓库根找 `vendor/wewrite/wewrite`」：

```bash
find vendor/wewrite/wewrite -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

得到 `6b3d70…`，写进了 `PROVENANCE.md`。但 `shasum` 的输出**包含文件路径**，而脚本里 `$SRC` 是绝对路径，于是脚本算出来是 `9f458f…`——同一个包、同一份内容，两个摘要。

改法是 `cd` 进包目录再算，路径变成 `./wewrite/cli.py` 这种与位置无关的形式：

```bash
cd vendor/wewrite/wewrite && find . -type f -print0 | sort -z \
  | xargs -0 shasum -a 256 | shasum -a 256 | cut -d' ' -f1
```

**这个缺陷是被 `--check` 自己撞出来的**，不是我看出来的。如果当时只写摘要不写校验脚本，它会一直错着，而且错的方式是「换台机器就报不符」——在阶段 1 服务器部署时才发作，那时更难查。

### 缺陷二：`uv` 不在非交互 shell 的 PATH 里

`command -v uv` 在脚本里失败，但 `uv` 明明装着。原因是它装在 `~/.local/bin`，而定时任务与 CI 的 shell 不 source `~/.zshrc`。

这与 **ADR-0052 完全同型**（那次是 `node` 在 nvm 里、定时任务 shell 没有 nvm init，导致 `FileNotFoundError: 'node'` 静默丢了两天表现数据）。当时的修法是在 `run_all.sh` 里写 `node_dir()` 显式找候选路径；这次的修法是 `find_uv()`，依次找 PATH / `~/.local/bin` / `~/.cargo/bin` / `/opt/homebrew/bin` / `/usr/local/bin` / `.uv-bin`。

**这是第二次踩同一个坑，所以它不该再被当成个案。** 规律是：**任何由人手动跑过一遍就算验证过的脚本，它的依赖查找都还没有被验证**——人手跑的时候 PATH 是全的。判据应该固化成：脚本里出现外部命令时，用 `command -v` 且**必须**有显式候选路径兜底，并在一份 `PATH=/usr/bin:/bin` 的干净环境里跑过。

### 附带发现：`$VAR` 紧跟中文标点会被当成变量名的一部分

`die "…（期望 $want，实际 $got）…"` 在 `LC_ALL=C` 下报 `want<字节>: unbound variable`——bash 把全角逗号的第一个字节吃进了变量名。修法是所有「`$var` 紧跟非 ASCII 字符」的地方一律写成 `${var}`。

这是 CJK 项目写 shell 的通病，且只在 `LC_ALL=C` 下暴露。`run_all.sh` 里同类写法很多，本次**没有**全面审计——记为残余风险，不在本 ADR 范围内扩大。

## Consequences

- **ADR-0015 Decision 二现在是真的了。** 判据（ADR-0059 那句「决定里出现『X 进 Y』时，必须能在 Y 的代码里找到读 X 的那一行」）在这里的具体形态是：`vendor/wewrite/` 存在、`bootstrap-wewrite.sh --check` 通过、且 `.uv-tools/wewrite` 是从 `vendor/wewrite` 装的（`uv pip install --no-deps vendor/wewrite` 的输出里写着 `from file://…/vendor/wewrite`）。
- **仓库大了 436K。** 这是 ADR-0015 已经接受的代价。
- **上游的安全与功能修复需要人工跟进。** 同样已在 ADR-0015 Consequences 里接受。`PROVENANCE.md` 明确写了不能在这里改代码——改了就无法与上游 diff。
- **仍不覆盖的部分**：依赖的哈希没有记录（`requirements.lock` 只钉版本不钉哈希）。理由写在 lock 文件头：这台机器上拿不到 sdist/wheel 哈希又不装网络依赖，手抄一份哈希比不写更危险（抄错会以「哈希不匹配」的形式静默变成「装不上」）。ADR-0015 真正在意的是 wewrite 本身漂移，而 wewrite 是逐字节 vendor 的。
- **`$VAR` 紧跟中文标点这个坑在 `run_all.sh` 里还有存量。** 本次只修了 `bootstrap-wewrite.sh`，没有全面审计——新增残余风险。
