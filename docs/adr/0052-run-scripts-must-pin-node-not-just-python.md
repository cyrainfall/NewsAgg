# 编排器必须自己钉住 node，不能指望调度器的 PATH

## Context

2026-10-21 21:00 主流程首次按新赛道（ADR-0050）跑完整三段，出现两处失败，
事后查明**根因是同一个**：

1. 第 3 段发布直接崩：`publish.py:156` `subprocess.run` 抛
   `FileNotFoundError: [Errno 2] No such file or directory: 'node'`；
2. 第 1 段 ②b 表现数据打印「（表现数据输出无法解析——本次读回失败，不影响
   本轮运行）」，然后照常往下跑。

两处都不是代码缺陷，是**环境假设**：node 由 nvm 装在 `~/.nvm/versions/node/`，
不在 `/opt/homebrew/bin` 或任何系统 PATH 里。交互式 shell 里 `node` 可用，
是因为 `~/.zshrc` 里的 nvm init 脚本改过 PATH；**DSH 定时任务的 shell 不加载
这段 init**，所以那里没有 node。

前几次定时任务之所以成功，是因为当时那个调度器 shell 恰好带着 nvm 的 PATH——
也就是说，这条依赖一直是**碰巧成立**的，没人把它写下来。

## 发现

### 一、同一个缺口被两次不同的方式掩盖

- 发布：崩得很响（`FileNotFoundError`），当天就能发现；
- 表现数据：`performance()` 里是
  `python3 performance.py > /tmp/newsagg-perf.json 2>&1 || true`，
  后面 `json.load` 失败后打印一句「无法解析」并 `SystemExit(0)`。
  traceback 被写进 `/tmp/newsagg-perf.json`，**没有任何地方会去看那个文件**。

于是同一个根因，一处立刻暴露，一处**连续两天静默丢弃整天的表现数据**，
而且日志措辞（「无法解析」）暗示这是数据格式问题，把排查引向错误方向。

### 二、这是 ADR-0039 已经解决过的问题的第二个实例

ADR-0039 把 **python 解释器**钉死（`COVER_PY`），理由写得非常清楚：

> 定时任务不经过交互式 shell，没有「记得换个 python」的机会，所以这里把解释器钉死。

同一句话对 node 完全成立，但当时只钉了 python。ADR-0040 的教训
（「端到端验证必须用生产形态」）也指向同一件事：**只要某一步依赖环境，
就必须让脚本自己建立这个环境，而不是假设调用者的 shell 是什么样。**

### 三、「永不致命」不等于「可以不说出原因」

`performance()` 被设计成永不致命是对的（ADR-0006：表现数据读不回来不能
拖垮整轮，否则看守无法区分「系统没跑」与「数据读不到」）。但它把
「失败的原因」一起吞掉了。永不致命应该是**降级但不失明**，不是**降级且闭嘴**。

## Decision

### 一、`run_all.sh` 在开头钉住 node（与 `COVER_PY` 同型）

```bash
node_dir() {
  if [[ -x "$HOME/.nvm/versions/node/v22.23.3/bin/node" ]]; then
    printf '%s' "$HOME/.nvm/versions/node/v22.23.3/bin"; return 0
  fi
  local c last=""
  for c in "$HOME"/.nvm/versions/node/*/bin; do
    [[ -x "$c/node" ]] && last="$c"
  done
  [[ -n "$last" ]] && { printf '%s' "$last"; return 0; }
  return 1
}
if ! command -v node >/dev/null 2>&1; then
  if NODE_DIR="$(node_dir)"; then export PATH="$NODE_DIR:$PATH"
  else echo "警告：PATH 与 ~/.nvm 下都找不到 node——发布与表现数据会失败（ADR-0052）" >&2
  fi
fi
```

两档选择，不猜：**优先 v22.23.3**（2026-10 之前每一次成功发布都跑在这个版本上），
否则取 nvm 目录下排序最后一版。PATH 里已经有 node 时不动它——不覆盖调用者的
选择，只补缺失的那一档。

### 二、降级必须自带原因

`performance()` 的 `except` 分支现在会打印输出文件的**最后一行**：

```
（表现数据输出无法解析——本次读回失败，不影响本轮运行）
  输出末行：FileNotFoundError: [Errno 2] No such file or directory: 'node'
```

一行就够——它把「数据格式问题」和「命令根本没跑起来」区分开，
而这正是这两天缺失的信息。

## Consequences

1. **定时任务不再依赖调度器的 PATH**：`run_all.sh` 是唯一入口，它在开头就把
   node 放好，任何调用方式（DSH 定时、手动 `bash run_all.sh`、cron）都一致。
2. **`node_dir()` 会在 nvm 升级后仍指向 v22.23.3**，即使新版本已装。这是有意的：
   发布链跑在一个验证过的版本上，比自动跟随最新版安全。升级 node 时应同步改这
   一行——**这是一个需要人记得的常量**，不是自动发现。
3. **仍然是「脚本钉环境」而不是「环境自己可靠」**：如果哪天 nvm 整个消失，
   警告会打印但发布仍会失败。彻底的做法是把 node 也 vendor 进仓（像
   `.uv-tools` 那样），代价是仓库体积与升级流程；当前接受这个残余风险。
4. **`performance.py` 的其他失败路径仍未区分**：`d.get("ok")` 为假时打印
   `stage`/`reason` 是对的，但**解析成功而字段缺失**（比如 CLI 改了输出形状）
   仍会安静地少打几行。同类问题需要在消费端逐个补，本 ADR 只修了最外层的
   「不是 JSON」。
5. **这是第三次「静默降级掩盖缺陷」**（ADR-0040 的 `git_pull`、ADR-0045 的
   反查、本条的 node）。共性不是某一处写错，而是**`|| true` + `2>&1` 之后
   没人读那个文件**。以后凡是这么写的降级分支，都必须至少把末行带进日志。
