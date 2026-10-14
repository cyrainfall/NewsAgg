# 复用 wewrite-* 作为写作引擎，NewsAgg 只负责采集与分拣

## Context

设计到中途才发现，本工作区的 `.agents/skills/` 里已经有一套完整的公众号内容流水线：`wewrite-style`（交互式风格访谈 → `style.yaml`）、`wewrite-write`（文章任务书 / 主张与证据 / 初稿，含"禁止编造亲历"纪律）、`wewrite-learn`（改稿飞轮 + 范文库）、`wewrite-review`（编辑审稿门）、`content-scorer`（发布前评分）、`wewrite-stats`（数据复盘）。

这些恰好覆盖了本项目需要设计的多个环节，且已经过验证。同时它们并不覆盖本项目真正的缺口：采集、事件聚簇、机器分拣、观点卡、头条发布。

关键约束：`wewrite` 是一个**外部第三方项目**（[imraywang/wewrite](https://github.com/imraywang/wewrite)，MIT，发布在 PyPI），本工作区里只有它 `skills/` 目录的副本，而每个 skill 都通过 shell 调用 `wewrite` CLI。该 CLI **当前并未安装**（无 PATH 命令、无 `~/.wewrite`），必须另行安装（`uv tool install wewrite` 或其 `install.sh`）。因此复用是一个真实的、需要显式安装的外部依赖，不是零成本。

它的官方架构是三层解耦："prompt 负责判断，Python 负责确定性"——Prompt 层（`skills/`）、Runtime 层（`wewrite` CLI，pip 包）、State 层（`~/.wewrite/`，可由 `WEWRITE_HOME` 覆盖）。

两处能力与本项目自建层重叠，需明确取舍：`wewrite hotspots` 已能抓取微博+头条+百度热搜；`wewrite-topic` 已能做 10 选题评分与历史去重。

## Decision

复用 `wewrite-*` 作为写作引擎，NewsAgg 只新建两件事：**采集层**（热榜/RSS → 事件聚簇）与**分拣层**（事件聚簇 → 观点卡匹配 → 选题信号 → 三态决策）。头条发布链路复用项目根目录的 `toutiao-ops`（见 ADR-0024，已推翻此前"自建"的决定）。

边界取"只要成稿"：NewsAgg 从 wewrite 只取审过的成稿这一个产物。不在 wewrite 内部扩展平台，因为其发布链路面向公众号草稿箱，且其权限模型与无人值守正面冲突（见下）。

## Consequences

- **必须刻意推翻 wewrite 的权限门。** 其状态契约默认 `permissions.publish: false`，规则是"只有用户明确要求发布才为 true"，`--mode publish` 亦要求用户已明确授权。无人值守要求该门在无人时也为真。这是一个有意的偏离，不是疏漏；实现时必须显式处理，否则流水线会静默停在待授权状态。
- `wewrite` 的默认人格与预设面向公众号，需要按赛道（AI 与开发者工具）重新配置 `style.yaml`。
- `wewrite` 的 rewrite 模块只有 `douyin` / `xiaohongshu` 两套平台预设，**没有头条**。由于本项目现阶段只发头条且不产出平台稿（Q40 已删除该概念），该模块短期内不被使用；头条所需的标题/分类/标签/封面规范由本项目自建平台配置承担。
- `wewrite` 状态默认在 `~/.wewrite`（仓外），但其官方支持 `WEWRITE_HOME` 覆盖该位置。因此本项目决定把 `WEWRITE_HOME` 指向仓内，使风格/历史/范文成为 git 跟踪的资产（与 Q28 一致），同时避免同一份风格被两处各自维护。这是使用官方支持的机制，不是绕过它。
- **安装前置**：`wewrite` CLI 需先安装（uv/pipx）。它同时把 10 个 skill 链接到 `~/.claude/skills/` 与 `~/.agents/skills/`；本工作区已有一份 skill 副本，安装时需确认二者不会互相覆盖或分叉。
- 存在能力重叠，需明确取舍：`wewrite hotspots`（抓微博/头条/百度热搜）与本项目采集层重叠；`wewrite-topic`（10 选题评分 + 历史去重）与本项目分拣层重叠。本项目自建层的独有价值在于**事件聚簇**与**观点卡匹配**，这是 wewrite 不具备的（它的去重基于「历史选题」，不认识认知资产）。取舍结果：**采集与分拣全部自建**，不调用 `wewrite hotspots` 与 `wewrite-topic`——因为事件聚簇需要原始条目与稳定字段，而这两个命令的产出是面向人类选题的聚合结果。本项目自建采集用 DailyHotApi + 定向 RSS。
- **依赖钉版本入仓**：wewrite 的 10 个 skill 与其 CLI 版本一并钉死入仓，使部署可重现、不受上游突变影响。代价是本项目需自行跟进上游的安全与功能修复；升级成为一次有意的、需要重新校验契约的动作，而不是自动发生的事。
- 升级耦合：`wewrite` 演进会影响到本项目，因此只依赖其稳定 CLI 契约与"成稿"产物，不依赖其内部状态文件结构。
