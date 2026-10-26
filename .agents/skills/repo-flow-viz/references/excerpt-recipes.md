# 摘录工具用法（lib_facts.py）

```python
import sys; sys.path.insert(0, '<skill>/scripts')
import lib_facts as F
F.set_root('/abs/path/to/repo')
```

`set_root` 必须在任何摘录调用之前。之后所有 `path` 都是相对它的相对路径。

## ex —— 真实行号切片

```python
F.ex('.agents/.../collect.py', 41, 48, note='默认源清单')
```

1-based，闭区间。**最常用也最可信**。

缺点：文件一改行号就漂移。所以 `ex` 用来切"现在这一刻"的现场，
配 `at` 用来切"这个位置一直都对"的锚点。

切出来是空白会 `die()` —— 多半是行号差一格，往上/下挪。

## at —— 字面子串定位（优先于 ex）

```python
F.at('.github/workflows/newsagg-watchdog.yml', 'YESTERDAY=', before=2, after=8)
```

找首处命中，取上下文。**文件改了它跟着走。**

锚点必须是行内**稳定**的字面文本：

- ✓ `YESTERDAY=` —— 赋值语句，不会变
- ✓ `export const MIN_EVERY_INTERVAL_SECONDS` —— 常量声明
- ✗ `  fi` —— 缩进和上下文都可能变
- ✗ `if (x` —— 太短，容易命中别处

miss 了就 `die()`。这是特性不是 bug：它提醒你"这里变了，去看一眼"，
而不是让你继续展示一段已经不存在的行为。

## fn —— 函数体开头

```python
F.fn('.../runtime.ts', 'private async drive(): Promise<void> {', take=26)
```

`at(..., before=0, after=take-1)` 的糖。`take` **宁小勿大** ——
展示整个 200 行函数等于没展示。默认 18。

## jsonview —— JSON 顶层键视图

```python
F.jsonview('.newsagg/run-status.json', None)        # 列全部键，list 折叠成 "…(N 项)"
F.jsonview('.newsagg/run-status.json', ['stage', 'ok'])
```

`start=null`，页面上标"结构化视图（非原文切片）"。

`keys=None` 时 list 值会折叠成 `…(161 项)` —— 一个 161 元素的候选列表
全量展开会淹没真正要看的字段。折叠是主动丢细节，**note 里要交代丢了什么**。

## jsonline —— JSONL 某一行

```python
F.jsonline('.newsagg/publish-ledger.jsonl', -1)     # 默认最后一行
```

`start=end=null`，`loc` = "第 N 行 / 共 M 行（JSON 已格式化便于阅读）"。

**为什么不给行号：** 文本是 `json.dumps(indent=2)` 出来的，已经不是文件里
那一行的样子。标行号就是撒谎。

## textslice —— ex 的别名

非代码文件（Markdown / YAML / 报告）用它，读起来更贴语义。行为跟 `ex` 完全一致。

## count_dir —— 顶部统计条

```python
n, files = F.count_dir('docs/adr/*.md')
```

返回 `(数量, 文件名列表)`。**统计值必须这么数出来**，不许手写常数。

## die —— 所有失败的出口

文件不存在、不是 UTF-8、锚点 miss、行号越界、切出空白、JSON 缺键 ——
一律 `die()` 打印 `[FATAL] …` 并 `sys.exit(1)`。

**不要捕获它，不要给它默认值。** 它就是用来停下来的。
一个能"容错"继续跑的摘录工具，产出的页面会在最不起眼的地方撒谎。
