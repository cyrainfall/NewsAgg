# 采集层实测：公开热榜实例不可用，必须本地部署；源清单已验证

## Context

按 ADR-0027 阶段 0，实际验证采集层（ADR-0016 定的"DailyHotApi + 定向 RSS"）。实测推翻了 ADR-0016 的一处隐含假设，并给出了源清单的实测结果。

## 实测发现

### 一、公开热榜实例 `api-hot.imsyy.top` 已不可用

```
api-hot.imsyy.top : HTTP 000（连接失败，0.08s）
toutiao.com       : HTTP 200
github.com        : HTTP 200
```

`DailyHotApi` 项目 README 推荐的示例站点（`https://api-hot.imsyy.top/`）**不可达**。因此**不能依赖公开实例**，必须本地部署 `reference/DailyHotApi`。

这否定了"直接调外部热榜 API"的简化路径，但也带来了好处：本地部署意味着**源列表、缓存策略、请求频率全部可控**——而 ADR-0016 要求的"赛道预筛"恰好需要稳定的原始条目与固定字段，本地部署比调外部实例更契合。

### 二、本地部署可行且已跑通

`reference/DailyHotApi`（v2.0.8，pnpm 布局已就绪，node 24 满足 `>=20`，hono + cheerio 齐备）：

```bash
PORT=6688 npx tsx src/index.ts     # 即起，根路径 200
```

输出形状与 ADR-0016 预期一致：

```json
{"code":200,"name":"toutiao","title":"今日头条","type":"热榜","total":50,
 "fromCache":false,"updateTime":"...",
 "data":[{"id":"7696...","title":"...","cover":"https://...","hot":...,"url":"..."}]}
```

`fromCache` 与 `updateTime` 字段对 ADR-0006 的信源健康监控有直接价值（可据此判断数据新鲜度）。

### 三、赛道相关源实测（AI 与开发者工具）

本地起服后逐一实测，全部返回实时数据且内容与赛道吻合：

| 源 | 状态 | total | 实测首条（节选） |
|---|---|---|---|
| `ithome` | 200 | 48 | 苹果国行版备案大模型编号 |
| `36kr` | 200 | 50 | 刚刚，Gemini 4 全球上线 |
| `juejin` | 200 | 50 | 前端如何生成 10000 页 PDF |
| `csdn` | 200 | 30 | 【AI智能体】Codex 浏览器操作自动化 |
| `sspai` | 200 | 40 | App+1｜专注星空 |
| `github` | 200 | 12 | （trending 仓库） |

**结论**：ADR-0016 的源选型成立，且 `36kr`/`juejin`/`csdn`/`ithome` 四个源的实测内容与"AI 与开发者工具"赛道高度重合，可直接作为预筛的起点。

### 四、绕开 DailyHotApi 的直达路径也已验证

即便不启本地服务，原始接口也可直接调用：

```bash
curl -H "User-Agent: Mozilla/5.0 ..." \
  "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
# → {"data":[{"ClusterId":...,"Title":...,"LabelUrl":...,"Url":...}]}
```

这与 `DailyHotApi/src/routes/toutiao.ts` 的实现完全一致。它的价值是**兜底**：当本地服务未启动或某条 route 失效时，可直达原始接口。

## Decision

**一、采集层依赖本地部署的 DailyHotApi**（`PORT` 可配，默认 6688），不依赖公开实例。它作为后台常驻进程运行，与 ADR-0002 的"技能 + 调度"形态兼容。

**二、源清单起步即为实测通过的六个赛道相关源**：`36kr`、`juejin`、`csdn`、`ithome`、`sspai`、`github`，加上 `toutiao` 本身作为主榜。这与 Q64 的"先用一组保守的默认源，跑一周再调"一致——但该组已由实测（而非猜测）确定。

**三、原始接口作为兜底**，在本地服务不可用时使用。两者返回形状不同（`ClusterId`/`Title` vs `id`/`title`），因此需要一个归一化步骤，不能混用。

**四、`fromCache` 与 `updateTime` 纳入信源健康信号**，与 ADR-0021 的 `agent-reach watch` 并列，共同支撑"信源悄悄失效"的检测。

## Consequences

- **本地 DailyHotApi 成为一个需要常驻的进程**，与"2GB 内存"的现实叠加。但其资源占用远小于 Chrome（Node + hono，无浏览器），因此不构成 ADR-0023 级别的问题。它也需要纳入看守：**热榜服务挂掉会使采集层静默失去全部输入**，而简报仍会正常发出（弃权理由会是"无选题"，与真实原因不符）。
- 本地部署意味着**抢缓存与频率都由我们控制**，这对风控友好（可拉长间隔）；但也意味着我们需要自己负责源失效时的修复——上游 `DailyHotApi` 更新 route 的频率未知。
- 六源实测通过不等于长期稳定：热榜源的接口易变，且部分源（如 `github` trending）可能有速率限制。因此"源失效"应被当作常态而非异常，这正是第四条信源健康信号存在的理由。
- 归一化步骤是必要的技术债：两种形状（DailyHotApi 的 `id`/`title` 与原始接口的 `ClusterId`/`Title`）若在代码里混用，会在切换兜底路径时产生静默错误。
