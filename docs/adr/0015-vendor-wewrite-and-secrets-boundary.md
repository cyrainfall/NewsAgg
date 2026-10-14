# wewrite 源码 vendor 入仓，密钥排除，成稿存 markdown

## Context

三条收尾决策由一次机械核对发现，而非设计推导。

**其一，密钥与仓内状态冲突。** `wewrite` 的 `config.yaml`（住在 `WEWRITE_HOME` 内）装有 API key（图片生成 provider、微信 appid/secret）。而 Q42 决定把 `WEWRITE_HOME` 指向仓内以便风格与历史入 git，Q81 又要求密钥放 ECS `.env` 且不入仓。两条同时执行会让密钥进入仓库。

**其二，安装方式。** 上游 `install.sh` 会把 skill 链接到 `~/.claude/skills/` 等全局目录，并迁移旧状态——这与"仓内副本 + 钉版本"的策略相冲。

**其三，成稿格式。** 教学飞轮（ADR-0008）依赖对成稿做 diff，而 HTML 快照的 diff 不可读。

## Decision

**密钥排除**：`WEWRITE_HOME` 仍指向仓内，但 `.gitignore` 排除其中的 `config.yaml`；仓内提供 `config.example.yaml` 说明需要哪些键。风格、历史、范文入 git，密钥不入。

**源码 vendor 入仓**：`wewrite` 以源码形式 vendor 入仓并自行安装，不运行上游 `install.sh`。版本完全锁定，代价是自行跟进上游。

**成稿存 markdown**：成稿以 markdown 入仓（wewrite 原生输出），入仓版即教学副本、可被 diff；发布时由脚本转换为头条编辑器所需形式。

**附带简化**：`wewrite` 已内置 10 个图片 provider 与自动 fallback（"按顺序尝试，第一个失败自动切换下一个"），因此封面兜底链（ADR-0013）只需在其之上补"模板拼图"一级，不必自建 provider 故障转移。

## Consequences

- vendor 入仓意味着仓库体积增大，且上游的安全与功能修复需要人工跟进。升级成为一次有意的动作，并需重新校验契约（与 ADR-0004 一致）。
- `.gitignore` 的排除规则成为**安全边界**：一旦 `config.yaml` 被误提交，密钥即进入 git 历史，仅靠后续删除无法挽回，必须按泄露处理（轮换密钥）。因此首次提交前的检查是必要步骤，不是可选项。
- 成稿存 markdown 使教学飞轮可行，但也意味着**仓内版本与线上版本之间隔着一层转换**。若转换有损（如富组件、小标签、步骤卡在 markdown 中无对应表示），diff 出的差异会混入转换噪声，削弱学习信号的质量。
- vendor 的 wewrite 源码与仓内 `skills/` 副本需保持同源，否则会出现"skill 描述的行为"与"实际 CLI 行为"不一致这一难查的问题。
