# ADR-0042：观点卡 frontmatter 只留一份解析实现

## Context

ADR-0007 把「已覆盖事件」（`covered_events`）定为**唯一能防重复发文的地方**，并且是分拣层
判定「跳过 vs 续写」的依据；ADR-0026 又把 `covered_events` 与 `last_written` 称作
「防重复发文的关键字段」。也就是说，这个字段的**读取正确性**直接决定分拣能否看到
「这张卡已经写过什么」。

建卡（2026-10-17，ADR-0041）后做结构验证时发现：同一张卡，**三个消费者读出了不同结果**。
`llm-price-war-signals-commoditization.md` 的 frontmatter 里真实存在 1 条已覆盖事件
（`2026-10-14 Gemini 4 Argon 上线（能力持平 + 成本降至对手 60%）`），而：

```
triage.py      → covered_events = ''
build_brief.py → covered_events = ['2026-10-14 Gemini 4 Argon 上线（能力持平 + 成本降至对手 60%）']
writeback.py   → covered_events = ['2026-10-14 Gemini 4 Argon 上线（能力持平 + 成本降至对手 60%）']
```

## 发现

### 一、三份 `parse_frontmatter` 拷贝漂移了

`parse_frontmatter` 在 `triage.py:33`、`build_brief.py:27`、`writeback.py:29` 各有一份
独立实现（三份都是「极简 YAML，不引入依赖」的同一思路）。其中 `triage.py` 的那份在
标量分支里写的是：

```python
if v in ("", "[]"):
    fm[key] = [] if v == "[]" else ""      # ← 空值记成空字符串 ""
```

而另外两份写的是：

```python
fm[key] = [] if v in ("", "[]") else (...)  # ← 空值记成空列表 []
```

差别只在一个 `if` 的位置，但后果是决定性的。

### 二、写回产生的形态恰好命中这个差别

`writeback.py` 的 `append_covered_event` 把首次写回做成「空值 + 多行列表项」：

```
covered_events:
  - 2026-10-17 Meta 开源 8160 亿参数的 Muse Spark Open（…）
```

这是**唯一**会被实际写出的形态——因为种子卡的 `covered_events: []` 被 `writeback.py`
的「情形一」展开成多行（`writeback.py:65-67`），此后每次追加都走「情形二」续接列表项。
实测最小复现：

```
输入: covered_events:\n  - 2026-10-17 事件甲
triage.py    → ''
writeback.py → ['2026-10-17 事件甲']
```

`triage.py` 读到空字符串后，列表项行因 `isinstance(fm[key], list)` 为假而被**静默丢弃**。

### 三、后果：分拣层永远看不到「已覆盖事件」

`load_cards` 把解析结果原样交给分拣输入包（`triage.py:75`
`"covered_events": fm.get("covered_events") or []`），而 `"" or []` → `[]`。实测
`.newsagg/triage/2026-10-17-input.json` 里，**每张卡的 `covered_events` 都是空列表**——
包括那张真实有 1 条记录的 `open-source-llm-bottleneck-is-ops-not-capability.md`
（`last_written: 2026-10-17`，当天刚写过）。

后果链条：分拣层看不见「这张卡写过什么」→ 无法判「跳过 vs 续写」→
ADR-0007 说的「防重复发文」在**读取侧**失效。而 `build_brief.py` 看得见，所以
**写作阶段**仍在提示「已覆盖事件（不要重复叙述）」——即同一轮运行里，分拣不知道、
写作知道。这不是"两处都坏了"，而是更坏的一种：**一处坏了，另一处正常，于是没有任何
报错，只有静默的判断力缺失**。

同时 `last_written` 因为走的是标量分支且值非空（`2026-10-17`），解析正确，冷却期正常
生效——所以缺陷不会以「冷却期失效」的形式暴露，只会以「分拣以为这张卡从没写过」的形式。

### 四、为什么此前的测试没发现

- 端到端测试用 `--no-git`（ADR-0040 已记这一教训），且**从未断言过分拣输入包里的
  `covered_events` 内容**——测试看的是 `cards_loaded`、`candidate_count` 这类计数。
- 缺陷只在**第一次写回之后**才显现：写回前卡是 `covered_events: []`，三份解析器都读出
  `[]`，一致；写回后才分叉。而首次写回发生在 2026-10-14，此后两轮运行的输入包都是
  错的，只是没人对照过。

## Decision

1. **`parse_frontmatter` 只留一份实现，落在
   `.agents/skills/newsagg-triage/scripts/cardfm.py`。** 三个脚本各自保留同名函数作为
   薄委托（签名不变，调用方无需改动），内部经 `sys.path` 注入同目录后 `import cardfm`。
   用 `sys.path` 注入而不是裸 `import`，是因为脚本既可能被 `python3 <路径>` 直接执行
   （脚本目录自动在 `sys.path` 上），也可能被 importlib 按路径加载（那时不在）。

2. **空值的类型由后续行决定，不预先猜。** 列表项出现时把空值**升级**为列表：

   ```python
   if re.match(r"^\s*-\s+", line) and key:
       if not isinstance(fm.get(key), list):
           fm[key] = []          # ← 关键：空值遇列表项升级，不丢项
       fm[key].append(...)
   ```

   这消除了「空值到底是 `""` 还是 `[]`」这个原本导致漂移的分歧点——两种记法都能正确
   接住后续列表项。同时把 `[]` 的显式写法与空值写法分开处理，语义更清楚。

3. **保留 `writeback.py` 返回原始 frontmatter 文本的第三返回值**（它需要原文做正则替换），
   共享实现统一返回 `(fm, body, raw)`，薄委托按各自签名裁掉不需要的部分。

4. **`cardfm.py` 的模块 docstring 必须写明这次漂移的完整故事**，并把 ADR-0030 的教训
   一并记下：**同一份契约的两份实现会漂移，且漂移是静默的**——两处都「能跑」，只有
   对照才看得出来。这是把「不要复制」从一条经验变成可读到理由的地方。

## Consequences

- **这是 ADR-0030「复用而不是复制」教训的第二个实例。** 第一个是浏览器锁
  （`performance.py` 复用发布层的 `FileLock`，理由写的是「复制一份锁实现就会产生第二把
  锁文件，两把锁互不感知，等于没有串行」）。本 ADR 是同一形状：**复制一份解析器会产生
  第二个语义，两个语义互不感知，等于没有契约**。区别是锁的漂移会导致并发事故（响亮），
  解析器的漂移只导致判断力缺失（沉默）——后者更危险。
- **ADR-0041 的「空 `covered_events` 的卡有真实风险」现在有了第二个来源。** 那条记的是
  「新卡拿不到 `covered_events`」（写回被跳过）；本条记的是「拿得到也读不出来」。两者
  叠加的最坏路径是：写回成功、文件里确实有记录、分拣层却仍以「从未写过」的姿态匹配。
  修复后两条来源都已关闭，但**这个字段的读取正确性应当被断言**，不能只靠对照发现——
  已在本轮把「三处解析结果与文件真实条数一致」加为验证项。
- **共享模块引入了新的加载路径。** `sys.path` 注入是本次为兼容两种调用方式而选的方案，
  代价是模块名 `cardfm` 进全局命名空间。若将来 triage 层脚本变多，应改为显式的包结构
  （`newsagg_triage/` 包 + 相对导入），但那需要先让所有调用方都按包路径调用——当前
  调用方里有 `python3 <脚本路径>` 的直接执行形态（`run_all.sh`），迁移不是无成本的。
- **本 ADR 只修了这一个字段的解析，没有审计其它 frontmatter 字段的分叉。** 实测当前
  三个脚本对 `issue` / `status` / `created` / `last_written` / `source` 的解析结果一致
  （都是标量、值非空）。但「空值 + 列表项」这一形态今后若出现在别的字段上，同一缺陷
  会重演——共享实现已经消除了这个可能，这是只留一份实现的直接收益。
- **验证方式**：三处解析结果必须与文件里的真实条数**逐卡对照**，而不是只看计数。
  本轮实测 8 张卡全部一致（有记录的读到 1 条，无记录的读到 0 条），
  `triage.py --validate` 与 `build_brief.py` 端到端均正常。
