# vendored wewrite 4.2.1 —— 来源与校验

这份目录是**别人写的代码**，以源码形式入仓（ADR-0015 Decision 二）。本文件记录它从哪来、怎么验、怎么升级。

## 来源

| 项 | 值 |
|---|---|
| 上游 | https://github.com/imraywang/wewrite |
| 版本 | 4.2.1 |
| 许可证 | MIT（见同目录 `LICENSE`） |
| 分发形式 | `wewrite-4.2.1-py3-none-any.whl`（纯 Python，无编译扩展） |
| 引入日期 | 2026-10-25（源码从本机 uv 缓存的同一份 wheel 提取，与当时实际运行的代码一致） |
| 提取自 | `.uv-tools/wewrite/lib/python3.12/site-packages/wewrite/`（uv 按 `==4.2.1` 安装的解包结果） |

## 校验

`vendor/wewrite/wewrite/` 的树摘要（`cd` 进该目录后对每个文件 `shasum -a 256`，按路径排序后整体再哈希——这样摘要与仓库放在哪无关）：

```
555eac19c6d40d9d4d5a0227c34f4e3986ac9ef4455b0c8f41f534c485fa4306
```

复算方式：

```bash
cd vendor/wewrite/wewrite \
  && find . -type f -print0 | sort -z \
     | xargs -0 shasum -a 256 | shasum -a 256 | cut -d' ' -f1
```

第一次算这个摘要时我用的是「从仓库根找 `vendor/wewrite/wewrite`」，得到 `6b3d70…`——那个值是错的，因为 `shasum` 的输出**包含文件路径**，摘要因此与仓库的绝对位置绑定，换台机器就复算不出来。`scripts/bootstrap-wewrite.sh --check` 一上来就把它撞出来了。

与源码同时入仓的还有 `.uv-tools/wewrite/lib/python3.12/site-packages/wewrite-4.2.1.dist-info/RECORD` 之外的最小重建：`pyproject.toml` 按上游 `METADATA` 重写，只负责「能从这份源码装出来」，不含本项目的任何修改。

## 安装

不要跑上游的 `install.sh`——它会把 skill 链到 `~/.claude/skills/` 等全局目录并迁移旧状态，与「仓内副本 + 钉版本」冲突（ADR-0015 Context 其二）。

用仓内的：

```bash
bash scripts/bootstrap-wewrite.sh
```

它做四件事：建 `.uv-tools/wewrite` 虚拟环境（Python 3.12）、按 `requirements.lock` 装依赖、用 `--no-deps` 从本目录装 wewrite、把入口软链到 `.uv-bin/wewrite`。

## 升级

升级是**一次有意的动作**，不是 `uv pip install -U`：

1. 取新版本的 wheel，解开，替换 `vendor/wewrite/wewrite/`。
2. 同步改 `pyproject.toml` 的 `version` 与 `METADATA` 里变了的字段。
3. 更新本文件的版本、日期、树摘要。
4. 重跑 `bash scripts/bootstrap-wewrite.sh` 与 `python3 -m unittest discover -s tests -t .`。
5. 校验契约没有变（ADR-0004：换版本要重新校验契约）。

## 为什么不改这里的代码

改了就无法与上游 diff，ADR-0015 Consequences 已经点明这个代价。本项目对 wewrite 的行为要求一律落在调用侧 `.agents/skills/` 与 `.wewrite/style.yaml`；确实需要改上游时，改为给上游提 PR，而不是在这里改一份私有分支。
