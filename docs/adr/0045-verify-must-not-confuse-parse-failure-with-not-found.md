# 一次成功发布被自己的反查报成失败：stdout 被截断，解析失败被当成未找到

## Context

2026-10-18 21:07，首次在自然日由定时任务触发的主流程走到第 3 段。`publish.py`
调用 toutiao-ops 发布文章，CLI 返回：

```json
{"success": true, "action": "published",
 "title": "拼豆App三个月43美元：AI小工具死在留存",
 "url": "https://mp.toutiao.com/profile_v4/graphic/articles"}
```

随后反查 `content list`，得到：

```json
{"verify_code": 0,
 "verify_result": "title_not_found",
 "verdict": "not_published_needs_review"}
```

`publish.py` 以退出码 1 结束，`run_all.sh` 走 `fail_soft`，跳过 ⑥ 认知写回，
写 `run-status.ok = false`，简报 ① 段写「本轮运行未完成」。

但文章**确实发出去了**：人工核对 `content list`，该标题存在（gid
`1879393099729987`，`itemStatus 20`），后台显示已发布。

即：**一次成功的发布，被系统自己判定为「结果未知、需要人工核实」。**

这一次的代价是可控的（人看见简报去核实一遍）。真正的危险在另一侧：ADR-0011 定下
「查不到 → 不重发」，但如果有人据此重发，就是同一篇文章发两遍——重复发布是平台
明令禁止的，其代价远大于漏发。

## 发现

### 一、`content list` 的输出在管道里被截断

实测对照（同一命令、同一会话、同一账号）：

| 捕获方式 | 字节数 | `json.loads` |
| --- | --- | --- |
| `> file`（文件描述符） | 103296 | 成功 |
| `capture_output=True`（管道） | 64788 | **失败**：`Unterminated string starting at: line 226 column 42 (char 60298)` |

截断点落在**一个 JSON 字符串中间**——不是数组末尾、不是对象边界，是 `articleURL`
这类长 URL 的中段。所以拿到的不是「少了几条作品」，而是一段**语法上不合法**的文本。

根因在 vendored CLI 的 runner：

```js
const result = await fn(opts);
await updateCheck;
console.log(JSON.stringify(result, null, 2));
process.exit(0);          // ← 紧接着退出
```

`console.log` 对管道是**异步写**：数据先进入 Node 的缓冲区，由 libuv 在事件循环
下轮 flush。`process.exit(0)` 立即终止进程，缓冲区里没来得及 flush 的部分被丢弃。
写文件描述符时 `write(2)` 是同步的，所以 `> file` 不受影响。

这个缺陷在本项目里**唯独在成功路径上显现**：失败时输出很短，缓冲一瞬即 flush；
只有 `content list --limit 20` 这种 100KB 量级的输出才会被截。也就是说，
**它只在你做对了一切的时候咬你。**

### 二、反查把「解析失败」当成「未找到」

原来那段代码是：

```python
try:
    payload = json.loads(cout)
    ...
except json.JSONDecodeError:
    record["verify_parse_error"] = cout[:200]
record["verify_result"] = found or "title_not_found"
```

`except` 里只记了一笔错误信息，`found` 仍是 `None`，于是往下走到
`title_not_found`，进而 `not_published_needs_review`。

而 `newsagg-publish/SKILL.md` 的四种反查结果里**明写着**：

| 查不到 | 视为未发出，**不重发**，标为待处理 |
| 解析失败 | **不可判为"未找到"**，必须报告（静默误判会诱使重发） |

代码违反了自己文档里的规则。这不是设计没想到——是**想到了、写下来了、没实现**。
规则写在技能文档里，靠人阅读来生效；而这条路径无人值守。

### 三、简报里「待发布成功后写回」是一句固定文案

`section_assets()` 对 `action == "continue"` 一律输出：

```
- **修订**：<card_file>（待发布成功后写回，带未复核标记）
```

它不检查写回到底发生了没有。于是发布成功、写回也成功之后，简报仍然写着「待写回」。
2026-10-18 实测：卡文件里 `last_written: 2026-10-18`、`covered_events` 有 1 条、
修订史有 1 条（含 3 条新增依据），而简报说它还没写。

这与 ADR-0040 的截断同属一类：**简报是唯一的人机接口（ADR-0019），它说的每一句
都必须是从事实推出来的，不能是固定文案。**

## Decision

1. **`run_toutiao` 一律把 stdout/stderr 重定向到临时文件，不用管道捕获。**
   文件描述符是同步写，不受 `process.exit` 影响。`run_toutiao` 是全项目唯一调用
   CLI 的入口（ADR-0030 的串行化也建立在这一点上），所以修一处即覆盖全部命令。
   临时目录用 `tempfile.TemporaryDirectory`，退出即清理；不引入持久日志。
2. **解析失败单独成一个结论 `verify_parse_failed` / `unknown_needs_review`。**
   它既不判成功（没证据）、也不判未找到（会诱使重发）、也不熔断（那是平台侧问题）。
   按 ADR-0011「结果未知不猜」，交人工核实。
3. **`section_assets` 检查写回是否真的落地。** 判据是卡文件里是否含
   `来源 <slug>`——那是 `writeback.py` 写修订时的固定格式，也是它自己的幂等判据，
   所以这个检查不引入第二套约定。`action == "new"` 时同时检查卡文件是否存在。
4. **不改 vendored CLI。** 修 CI 里的 `process.exit` 需要 vendor 补丁，而上游更新会
   覆盖它；把「用文件而不是管道」作为调用方纪律更稳，且一次覆盖所有命令。
   这个取舍记在这里，而不是等下一次踩到时重新讨论。

## Consequences

**「管道 vs 文件」是一条容易忘的纪律。** 任何人以后在这里加一条新命令，只要它输出
较大、且调用的 CLI 会在 `console.log` 后立刻退出，就会再踩一次。缓解是把原因写进
`run_toutiao` 的 docstring（已写），并让 `run_toutiao` 成为唯一入口（已是）。

**反查的四种结果现在是五种**（成功 / reviewing / rejected / 查不到 / 解析失败）。
文档与实现已对齐，但这也意味着人工处置的动作多了一档：「去后台核实一下」。
这是诚实的代价——原来那一档被伪装成了「查不到」。

**账本可以被人工更正，但必须留下痕迹。** 2026-10-18 那条记录已从
`not_published_needs_review` 改为 `title_found`，并附 `corrected_at` 与
`correction_note` 说明原判据为何失效。**不覆盖原始记录**——账本是事实的来源，
不是可以随意抹平的状态栏。

**这一类缺陷的共同形态值得记住。** ADR-0039 是段与段的接缝，ADR-0040 是同一接缝，
ADR-0043 是源与源的差异，本条是**捕获方式与输出体量的耦合**。它们的共同点都不是
逻辑写错，而是**某个隐含前提在特定条件下不成立**（工作区是干净的 / 输出是完整的 /
阈值是通用的）。无人值守系统的失败，多数不是因为它做错了，而是因为它**默认了
一件没人保证过的事**。
