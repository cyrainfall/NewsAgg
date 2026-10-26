# 同一轮里兜底成功的源不算失败

## Context

2026-10-26 18:00 的第三次采集，头条的日榜聚合接口返回 `HTTP Error 500: Internal Server Error`。`collect.py` 里有一条早就写好的兜底：头条除了聚合接口，还有原始接口，于是它改用原始接口取回了数据——**10 条头条条目正常进池**，候选池总数从 125 涨到 161。

但候选池里同时写下了这组数字：

```
counts.sources_ok     = 7
counts.sources_failed = 1
failed_sources        = ["toutiao"]
```

七个源，算出八个结果。而那个「失效」的源，实际上供了 10 条。简报②段会照抄这组数字，印出「源：7 成功 / 1 失败」和「失效源：toutiao」——**一次假的失效告警**。人会去查一个并没有坏的信源。

这不是新问题，是 ADR-0047 修过一遍的同一个坑，只是修在了另一半上。ADR-0047 当时写的是：

> `collect.py` 把已恢复的源永久留在 `failures` → 加 `recovered` 排除

但那段代码排除的是**跨轮**恢复：

```python
recovered = {m["source"] for m in fresh.get("source_meta", []) if m.get("ok")}
failures += [f for f in existing.get("failures", [])
             if f["source"] not in fresh_failed and f["source"] not in recovered]
```

它只作用在 `existing["failures"]`（旧记录）上。**同轮**的兜底成功不经过这段——`collect()` 在 `failures` 里就地写上 `recovered_by: raw_endpoint`，然后这条记录原样进 `fresh["failures"]`。

## Decision

**把「真失败」与「同轮兜底成功」在产生的地方就分开，各存一个字段。**

- 新增 `split_failures(raw_failures)`：按 `recovered_by` 字段切成 `(failures, recoveries)`。
- `main()` 里 `counts.sources_failed` 与 `failed_sources` 只数 `failures`；`recoveries` 单独写进候选池。
- `merge_candidates` 按源去重合并 `recoveries`（同源只留最新一条）——同一个源一天抖三次，简报不需要印三行。
- 简报②段新增一行「**源兜底成功**（聚合接口出错、已改用原始接口，数据是齐的）：toutiao（HTTP Error 500 → raw_endpoint）」。

## 为什么不直接丢掉

兜底成功本身是有信息量的：它说明**聚合接口在抖**。今天聚合接口 500 而原始接口正常，明天可能是聚合接口挂更久、原始接口也变慢。丢掉这条记录，就等于把「这个源的接入方式正在退化」这个早期信号也丢了。

但它和「失效」是两种事，混在一个字段里才是错的。所以分开存，而不是舍弃。

## 为什么这一行必须进简报

按 ADR-0062 的判据：**加一个字段时，必须同时回答「谁会读它，读不到会怎样」。** 如果只写 `recoveries` 而不印，它就是第八次「记录了但没有消费者」——只是这次缺陷会在还没出生时就被 ADR-0062 拦下。

读者是简报②段，读不到会怎样：聚合接口持续 500、一直靠原始接口兜底，而人以为一切正常。

## 一处边界

兜底**也失败**时，`fallback_error` 会被写上，但 `recovered_by` 不会——所以它留在 `failures` 里，仍然算失败。这是对的：如果没有这个判据，真正的失效会被「曾经尝试过兜底」这件事掩盖。`test_fallback_that_also_failed_stays_a_failure` 钉住了这一条。

## Consequences

- **数字自洽了**：`sources_ok + sources_failed` 恒等于源总数。之前那个 7+1=8 的组合在任何一天都肉眼可见，只是没人把它当成 bug——它是「看起来合理所以没人算」的典型。
- **假告警消失**：头条聚合接口 500 而原始接口正常的日子，简报不再把头条列为失效源。这类日子不少（10-14、10-19、10-20、10-26 都出现过）。
- **修复前已经写坏的那份候选池被就地修好**：`.newsagg/candidates/2026-10-26.json` 里那条 toutiao 记录从 `failures` 挪进 `recoveries`，`sources_failed` 改回 0。今晚 21:00 的主流程读的是修好的这份。
- **仍未解决的**：`stale_sources` 与 `recoveries` 目前都是「人看一眼」，没有连续多天都靠兜底就升级的判据。如果聚合接口连续一周 500，简报会连续一周印同一行，而系统不会把它变成一个需要修的事项——这与残余风险 59（⑤段没有紧急程度分级）同型。今天不做，因为一天的数据不足以定阈值。
