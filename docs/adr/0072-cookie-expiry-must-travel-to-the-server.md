# cookie 的有效期必须一路带到服务器上：过期只有症状没有预告

## Context

ADR-0071 把表现数据回路切到纯 HTTP，并留了一条残余风险 69：

> cookie 主体约 23 天有效，每月要重新导出一次。**今天系统里没有任何东西会提醒这件事**，也没有地方记录「上次注入是什么时候」。后果是某天会突然所有读数失败，而报错形态是「接口返回 1003 / 登录态失效」——看起来像平台问题，实际上是自己的 cookie 过期了。简报⑤段将来应该列出剩余有效期，但今天没做。

这条风险写得很具体，所以它可以被执行。但动手时第一件事就撞上了一个更基础的问题：**那份 cookie 文件里根本没有有效期信息。**

## 事实一：Chrome 存的是 WebKit 纪元，不是 Unix 纪元

Chrome 的 cookie 库（`Cookies` 表的 `expires_utc` 列）存的是**从 1601-01-01 起的微秒数**。第一次尝试直接 `datetime.fromtimestamp(那个数)`，报的是：

```
ValueError: year 425885148 is out of range
```

这个荒谬的报错就是发现单位不对的契机。真实值例如 `13439575089088755`，换算后是 2026-11-19。差值是 11644473600 秒。

## 事实二：`to_dict()` 根本没有导出这个字段

更严重的是这个。`Cookie.to_dict()` 原本只导出 `domain/name/value/path/secure/http_only`——**`expires_utc` 被丢掉了**。

所以 ADR-0071 导出的那份 cookie 文件（25 条，`/tmp/test-cookies.json`）里，**没有一条带过期时间**。我在 ADR-0071 的文档里写「主体到 2026-11-19，约 23 天」——那个结论是我当时**用另一种方式**（直接读库）算出来写进文档的，但**导出的文件本身不带这个信息**。也就是说：服务器上拿到那份文件，无从知道它什么时候过期。

于是残余风险 69 说的「提醒剩余有效期」，按当时的实现**根本做不出来**——没有数据源。

## 事实三：过期这件事的症状指向错误的方向

假设不修。某天 cookie 过期，纯 HTTP 链路全部失败，浏览器后端正常（Playwright 的 profile 是另一份登录态，ADR-0071 实测两个 profile 的 cookie 互相解不开）。

那么症状是「纯 HTTP 全部 401 / 登录态失效」，而浏览器后端好好的。这个现象**看起来像代码坏了**，不像凭证过期——于是会花时间去查代码、查接口、查账号，而不是去查那份 JSON 文件的年龄。

这是「只有症状没有预告」的典型形态：预告必须发生在**还能从容处理的时候**，也就是过期之前。

## Decision

### 一、导出时把过期时间写进文件

`Cookie` 增加一个派生属性 `expires_at`（Unix 秒），并把 `expires_utc` 一并写进 `to_dict()`：

```python
@property
def expires_at(self) -> float:
    if not self.expires_utc:
        return 0.0          # 会话 cookie：浏览器退出即失效
    return self.expires_utc / 1_000_000.0 - 11644473600.0
```

同时在导出文件的**顶层**记三个摘要字段，让消费方不必去遍历 cookies 数组：

- `earliest_expires_at` —— 最早过期（这是决定「还能用多久」的那个数）
- `latest_expires_at` —— 最晚过期
- `session_cookie_count` —— 有多少条没有过期时间

### 二、注入时把它读回来

`Session.from_cookie_file` 读到 `expires_at` 就换回内部的 WebKit 微秒表示（`(unix + 11644473600) * 1_000_000`），保持 `Cookie` 内部只有一种表示。**旧格式文件（没有这个字段）照常加载**，`expires_utc` 记 0 = 会话 cookie——向后兼容不破。

### 三、导出脚本当场报有效期

`scripts/export-cookies.py` 打印最早/最晚过期日期与剩余天数；**已经有 cookie 过期时退出码 1**（提示先在浏览器里重新登录）；**不足 7 天时往 stderr 打警告**。

它本来就只打印条数、域、权限位，不打印任何 cookie 值——有效期是元数据，不涉及凭证内容。

### 四、表现层每次读数据都检查一次，快过期就报

`performance.py` 新增 `cookie_expiry_warning()`，读 `NEWSAGG_COOKIE_FILE` 指向的文件的 `earliest_expires_at`，算出剩余天数：

| 剩余 | level | 处置 |
|---|---|---|
| < 0 天 | `expired` | 纯 HTTP 链路会全部失败，请重新登录并导出 |
| < 7 天 | `expiring` | 请在本机重新导出并替换服务器上的文件 |
| ≥ 7 天 | `ok` | 不报 |

结果写进 `.newsagg/performance-status.json` 的 `cookie_expiry` 字段。

**为什么是「快过期」而不是「已过期」**：已过期时请求直接失败，那种情况走 api 失败 → 回落浏览器后端就够了（ADR-0071 已实现）。真正需要提前说的是「还有 3 天就过期了」。

**没有 cookie 文件时返回空 dict**：那是本机直读 Chrome 的场景，cookie 由浏览器自己管，不该在这里提醒。

### 五、简报⑤段呈现

`brief.py` 的第⑤段读 `status.cookie_expiry`，在 `expiring` / `expired` 时输出一段，含重新导出的三条命令（导出 → scp → chmod 600）。

分工沿用 ADR-0021 的模式：**算在产生数据的地方（performance），说在简报里（brief）**。

## 实测

| 场景 | 结果 |
|---|---|
| 重新导出（真实 Chrome） | 25 条，最早到期 **2026-11-19**（约 24 天后），最晚 2027-10-25，权限 0o600 |
| `expires_at` 单位换算 | `Cookie(expires_utc=13439575089088755).expires_at` → 2026-11-19，正确 |
| 导出 → 注入往返 | 过期时间**逐秒相等**（round-trip 测试钉住） |
| 旧格式文件（无 `expires_at`） | 照常加载，`expires_utc == 0`，不报错 |
| `days_left = 3` | `level: expiring`，`message` 含「重新导出」 |
| `days_left = -2` | `level: expired`，`message` 含「已于 … 过期」 |
| `days_left = 30` | `level: ok`，不报 |
| 简报渲染 | ⑤段输出「**注入的 cookie 快过期**（ADR-0071）」+ 三条重导出命令 |

## Consequences

- **残余风险 69 从「没做」变成「做了一半，但只覆盖注入路径」。** 本机直读 Chrome 的场景不经过 cookie 文件，那条路上 cookie 由浏览器管理，不存在「注入文件过期」这个问题——所以覆盖是完整的。
- **换了一个失败形态。** 之前是「某天突然 401，看起来像代码坏了」；现在是「到期前 7 天简报⑤段出现一条提醒」。后者可处理。
- **多了一个需要人做的事**：重新导出后要 scp 到服务器并 chmod 600。提醒里写了这三条命令，但**人必须做**——这是 ADR-0071 引入的运维项，本 ADR 只是让它可见，没有自动化。
- **chrome 的 `expires_utc` 对会话 cookie 记 0**，导出时 `expires_at` 记 0，注入回来也是 0。实测本次 25 条**全部**有明确过期时间（`session_cookie_count = 0`），但换账号后可能不是——导出脚本会单独报出有多少条是会话 cookie。
- **`expires_at` 依赖 chrome 的 cookie 库是 WebKit 纪元**。若某天换浏览器或平台改格式，换算会错。缓解是往返测试（导出→注入→过期时间相等）会立刻发现。
