# 部署在云服务器，浏览器以虚拟显示 + 有头模式运行

**Status**: 受 ADR-0024 大幅修正——本 ADR 的"自建 CDP 层"前提已被推翻。项目根目录下的 `toutiao-ops` 是一个完整的头条运营技能包，它用 **Playwright + stealth 插件 + 持久化上下文**实现发布与数据读回，比本 ADR 原设想的"Xvfb + 裸 CDP + `xiaohongshu-skills` 的 cdp.py"更成熟。**浏览器方案以 ADR-0024 为准**；本 ADR 保留的是其中仍然成立的部分：**有头模式优于无头**这一判断，以及反指纹需要持续维护这一认识（并受 ADR-0023 的内存约束校正）。

## Context

发布链路要求接管一个已登录的真实浏览器（ADR-0001）。而部署目标是云服务器（阿里云 ECS），云服务器没有显示器，且头条后台据调研有 argus 指纹风控，会挑战非浏览器的 TLS/JS 指纹。

这构成一个真实的两难：无头浏览器（headless Chrome）资源占用最低、配置最简单，但它的自动化特征（`navigator.webdriver`、缺失的 GPU/插件信号）最明显——而本项目的发布行为本身已经处于平台协议之外（ADR-0001），因此"被检测到是自动化"与"被检测到在违反协议"是同一件事的两面。使用可被轻易识别的无头模式，会把风险从"接受的尾部风险"推向"高概率被发现"。

## Decision

浏览器在云服务器上以 **Xvfb 虚拟显示 + 有头模式**运行，并施加反指纹处理。不直接使用 headless 模式。

**浏览器实现以 ADR-0024 为准**：采用 `toutiao-ops` 的 Playwright + `puppeteer-extra-plugin-stealth` + `launchPersistentContext` 方案，而非本 ADR 原先设想的"裸 CDP + `xiaohongshu-skills` 的 `cdp.py`"。

原方案中关于 `xiaohongshu-skills` 的复用判断作废（该包是"Chrome 扩展 + bridge server"架构，其扩展 `host_permissions` 只含 `*.xiaohongshu.com`，属小红书专用）。其中 `human.py` 的拟人节奏常量仍是可借鉴的参考，但 `toutiao-ops` 已自带等价实现（逐字输入 + 随机延迟）。

## Considered Options

- **直接 headless Chrome**：最省资源，但自动化特征明确，与"已知有风险仍要做"叠加后风险不可接受。
- **商业浏览器云 + 住宅代理**：指纹与 IP 最真实，效果最好，但引入付费第三方与额外凭证依赖。

## Consequences

- 云服务器需额外安装并维护 Xvfb 与有头 Chrome，资源占用与启动复杂度都高于 headless。
- 数据中心 IP 段本身是可被标记的信号，虚拟显示只解决了浏览器特征，没有解决 IP 特征。若实测发现 IP 是主要触发因素，则需要改为住宅网络出口（即本 ADR 的 Considered Options 之一）。
- 反指纹处理是一项持续对抗：平台更新检测手段时需要跟进，因此这不是一次配置，而是一项长期维护成本。
- 该决定使"完全无人"与"降低识别概率"同时成立，但代价是部署复杂度上升；首次部署与浏览器登录的运维流程成为关键路径。
