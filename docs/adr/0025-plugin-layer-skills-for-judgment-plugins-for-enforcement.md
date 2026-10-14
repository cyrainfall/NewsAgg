# 引入插件层：技能装判断，插件装强制

## Context

此前 Q48 决定"仓库内 `.agents/skills/` 四个技能，不打包为 DSH bundle"。经核实 DSH 插件机制后重审该决定，发现插件能做一件技能做不到的事。

**`ctx.tools.guard()` 的契约**（引自实时查询的 Service 契约）：

> Register a **monotonic** guard after the extensible `tools/pre-execute` waterfall. A plain-context guard applies globally; one registered through `agent.ctx` applies only to that agent. **Any matching guard may deny by returning a reason, while no guard can force-allow a call another guard denied.**

即：guard 同步、单调、不可被绕过，且**不取决模型是否记得**。

这很重要，因为 ADR-0013 的安全门（合规检查、信用分熔断、提交前读回校验）与 ADR-0011 的"发布绝不自动重试"，在纯技能形态下**全部依赖模型正确阅读并遵守提示词**。技能是"告诉模型该怎么做"，而 guard 是"让它做不到不该做的事"。对于"点击发布"这一**不可逆动作**，后者才是可靠的形式。

因此 Q48 被重审，结论是**混合**：不取代技能，而是各用所长。

## Decision

**一、分工原则：技能装判断，插件装强制。**

| 层 | 承载 | 理由 |
|---|---|---|
| 技能（`.agents/skills/`） | 分拣规则、观点卡匹配判据、三态决策、写作纪律、简报结构 | 这些是**散文**，供模型阅读理解与迭代 |
| 插件（bundle） | 安全门、受限 Tool、配置项、简报面板 | 这些需要**结构性强制**或**在进程内可见** |

插件**不**承载判断逻辑：改一条分拣规则应是改一个 `.md`，而不是重新打包插件。

**二、关键约束：`guard` 是同步的。** 契约明确写着"a guard is **synchronous**, so a decision that must await something, such as asking the user, returns `ask` from `tools/pre-execute`"。因此安全门必须**按同步性分层**：

| 层 | 同步性 | 承载 | 对应此前的门 |
|---|---|---|---|
| `ctx.tools.guard()` | 同步、单调、不可绕过 | 只读**本地状态** | 熔断标记、"结果未知待核实"标记 |
| 发布 Tool 的 `execute` | 异步 | 网络、浏览器、子进程 | 内存预检、登录预检、信用分读取、提交前读回校验、发布后反查验证 |

**异步门放在 Tool 的 `execute` 内**，不放在 `tools/pre-execute` 监听器里：`execute` 本来就是要做这些事的地方，无需额外一层；且其失败会作为错误结果进入会话日志，可溯源。

**三、熔断状态存仓内文件**（如 `.newsagg/breaker.json`），随 git 走。理由：
- guard 需要**同步读取**它——读本地文件是同步的，而读会话日志是异步的。
- ADR-0018 已确立认知/风格资产入仓，熔断状态同属运行状态，入仓一致。
- Q92 要求"人工修复后手动解除熔断"——**文件可由本人直接编辑或删除**，这与仓储内状态天然契合；若存在 Harness 内部存储则无法手工解除。

**四、发布暴露为单一 Tool**（`newsagg_publish`），由它执行完整流程，**不让模型自由构造参数**（标题与正文从仓内成稿读取）。理由：Chrome 生命周期必须与流程同生共死（ADR-0023 的"用完即杀"），拆成多个 Tool 会让 Chrome 跨调用存活，在 2GB 下不可接受。

**五、简报面板补充而非取代 GitHub Issue。** issue 是历史档案与互动界面（且 ADR-0006 的看守靠它判定系统存活），面板是当下状态。两者职责不同。

**六、落地面。** 插件源码入仓 `plugins/newsagg/`（与技能、资产同在 git，可审查可回滚；安装时 `install_bundle` 指向仓内路径）。UI 取"设置页 + 侧边栏面板"两处：`settings.section` 放阈值与状态配置（阈值本就是无值配置项，见 ADR-0016），`sidebar.panellist` 放当前状态。可用插槽已通过实时查询确认存在。

**七、落地节奏：先技能，后插件。** 先按 Q63 的前置链把技能跑通（装 wewrite、风格访谈、种子卡），验证内容质量后再抽安全门做插件。理由：插件解决的是"不可逆风险"，而现在还没到会发布的地步；过早做插件会在未验证的设计上再加一层。

## Considered Options

- **全部改为插件**：一处入口、配置统一，但散文规则写不进 JS，把判断逻辑硬编码后会失去可迭代性。
- **不引入插件**：不新增失败模式与版本耦合，但安全门继续依赖 prompt 遵守，且无状态面板。

## Consequences

- **插件引入一类新的失败模式**：它是 Harness 进程内的 JS，任何 bug 都可能让 slot 变空白或影响该 profile 的所有会话。这是为一个以"无人值守可靠性"为目标的系统新增的风险面，需要以"插件代码尽量小而稳"来对冲。
- **版本耦合加深**：其 peer dependency 是 `@deepseek-ai/cordis`，DSH 明说"Incompatible DSH peer dependencies block installation and activation"。我们已钉 wewrite 与 toutiao-ops 的版本，现在再加一个 DSH 版本钉。
- **同步/异步的分层是硬约束，不是风格选择**：把需要网络的检查写成 guard 会直接失败（guard 无法 await）。实现时必须按上表分层，否则会在集成时才暴露。
- 熔断状态入仓意味着**熔断期间会产生 git 提交**（breaker.json 的变化）。若不想污染历史，可放入 gitignore 的运行时目录——但那会牺牲"随 git 走"的可追溯性。二者需在实现时取舍，本 ADR 倾向入仓以保住 Q92 的手工解除能力。
- 插件与技能的分工带来一个纪律要求：**任何"必须生效"的约束都应放插件，任何"需要判断"的规则都应放技能**。混淆两者（如把合规词表硬编码进插件、或把熔断逻辑写在技能里）会同时失去两侧的优点。

### 已知残余风险：bash 绕过只能靠字符串匹配阻止

发布必须经 `newsagg_publish` 才真正受 guard 约束。为防止模型用 `bash` 直接调用 `toutiao-ops publish` 绕开安全门，guard 需要同时拦截 bash 命令中的相关字样。但必须诚实指出它的局限：

- **guard 是同步的**，只能对 `command` 字符串做模式匹配，**不能真正解析 shell 语义**。`toutiao-ops publish`、`npx toutiao-ops publish`、变量拼接、别名、`sh -c "..."`、base64 编码等变体无法穷尽覆盖。
- 因此它是**降低概率的措施，不是结构性保证**——与"发布 Tool 本身受 guard 约束"这一层不可同日而语。后者是结构性的，前者是启发式的。
- 真正的结构性解法是**不在无人值守会话里给模型 bash 权限**，但整个流水线（wewrite / toutiao-ops / Agent-Reach）都靠 bash 调用外部 CLI，禁用即停摆。因此该风险被接受，而非被消除。
- 缓解方向（未采纳，记录备查）：让发布**只**通过 Tool 暴露，且把 `toutiao-ops` 的可执行文件权限收紧到插件进程可调用、而模型 shell 不可调用。这需要部署层的权限设计，属实现阶段决策。
