# 发布快照必须被记录、范文库不得自动增长

## Context

首次真实发布后的第一次改稿学习（本人重写教学副本并 push）暴露了三个缺陷。它们共同的特征是：**系统看起来在正常运行，实际产出的是错误结果**——没有报错，没有告警，只有静默的错误。

其中第一个缺陷直接**推翻 ADR-0035 的发现一**。

## 发现

### 一、发布快照不能靠"最后一次提交"推断（推翻 ADR-0035 发现一）

ADR-0035 断言「该文件的最后一次提交内容就是发布内容」，并在 `style_learn.py` 中据此实现基线获取：

```python
git log --format=%H -1 -- outputs/<slug>.md
```

该断言**只在本人尚未改稿时成立**。本人改稿并 push 之后，"最后一次提交"变成了**改稿本身**：

```bash
git log --format=%H -1 -- outputs/2026-10-14-gemini4-price-war.md
# → 5139bc8 改稿飞轮：重写首篇教学副本...   ← 这是改稿，不是发布快照
```

于是 `baseline == current`，diff 恒为空：

```json
{"file": "outputs/2026-10-14-gemini4-price-war.md", "status": "unchanged"}
```

**风格学习静默失效**：报了 `status: unchanged`，退出码 0，简报看不出任何异常。而"本人改稿"正是风格回路唯一的输入信号——它恰好在信号产生的那一刻失效。

根因不是实现错误，是**设计假设错误**：发布快照被当作"可以从历史中推断出来的事实"，但它实际是"只在发布那一刻才知道的事实"。

### 二、`learn-edits` 会自动把终稿收进范文库，无法分辨真稿与测试夹具

`learn_edits.py:468-481` 有一段未在文档中申明的行为：

```python
# Auto-grow exemplar library from edited finals
exemplar = extract_exemplar(..., ownership="user", authenticity="user_edited")
if exemplar["quality_score"] >= 50:
    save_exemplar(...)
```

凡 `quality_score >= 50` 即入库，并**硬编码**标注 `ownership: user` / `authenticity: user_edited`。

实测后果：阶段 0 做风格回路端到端验证时，用**模拟修改**造的临时终稿（`.wewrite/exemplars/story-emotional-001.md`）被 auto-grow 收进范文库，并以「用户亲笔范文」的元数据永久留存在仓中。该提交的收尾说明声称「已删除 2 个 lesson、diff 临时文件、学习状态」，**未提 exemplar 被创建**——清理清单遗漏了它。

该文件的固有问题是**它的正文不是任何真实版本**。三方对比同一段：

| 来源 | 文本 |
|---|---|
| 原稿 `f2f48c2` | 「按惯例，这类发布会有两种读法。一种是"技术进步了…"；另一种是"又打价格战了…"。过去我倾向于第二种…」 |
| 伪造 exemplar 001 | 「这类发布通常有两种读法。/ 一种说技术进步了，做应用的人有福。另一种说又在打价格战，能力卷不动了。/ 我以前站第二种。」 |
| 本人现稿 `5139bc8` | 「降价通常有两种读法。一种说技术进步、成本下降…」 |

该段在 `git log --all -S "这类发布通常有两种读法"` 中**只命中该 exemplar 文件自身**，任何真实成稿版本都不含这句。

危害路径已核实：wewrite CLI **不自动注入范文**（`llm_write.py` 中 `exemplar` 命中数为 0，全包无消费者），唯一的注入路径是 `wewrite-write/SKILL.md:79-83` —— agent 写作时按技能指示读取 `ownership=user` 的范文来"校准声音和结构"。因此一篇 AI 生成的伪造稿会被当作**本人声音的样本**注入未来每一篇写作。范文库的引入没有任何 ADR 或技能文档背书，也无配置项可关闭 auto-grow。

### 三、分类器把技术观点文判成 `story-emotional`

`extract_exemplar.py:116-127` 的分类打分：

```python
scores = {"tech-opinion": data_count * 2, "story-emotional": story_count * 1.5, ...}
```

`STORY_MARKERS` 含 `"我"`、`"我们"`、`"那天"` 等词。中文技术/观点文中「我」极为常见（表达判断时 `style.yaml` 的 voice 明确要求第一人称），因此技术文必然误判。实测现稿 `data_count=5`（→10.0）、`story_count=9`（→13.5，命中「我」×8、「我们」×1）→ **story-emotional 胜出**。

这是**类别标签本身的缺陷**，不是单篇偶然：任何第一人称的中文技术文章都会被标成情绪故事。

## Decision

**一、发布快照改为"记录下来的事实"**。`publish.py` 新增 `snapshot_fingerprint(md_path)`，在发布时把指纹写进账本：

```python
{"content_sha256": sha256(text), "published_at": ISO8601+08:00, "git_blob": <git hash-object -w>}
```

`git hash-object` **必须带 `-w`**。不带 `-w` 只计算哈希、不在 ODB 中创建对象；那样只有在"事后有人把同样内容提交进 git"时才碰巧可解析。而发布与提交是两个独立步骤（发布由 `publish.py` 执行，提交由编排 agent 随后完成），因此提交失败或内容再改一次，快照就永久取不到、静默退回路径 2。`-w` 让快照在发布那一刻就独立于后续任何操作而存在（实测：探针内容未提交时，`-w` 生成的对象可 `git cat-file -p` 读出，无 `-w` 则 `could not get object info`）。

账本记录新增 `"snapshot"` 字段。`style_learn.py` 的 `find_publish_snapshot(root, rel_path, slug)` 改按两条路径取基线：

1. **首选**：读账本中该 slug 的 `snapshot.git_blob`，用 `git cat-file -p <blob>` 取出发布内容；
2. **回退**（兼容本 ADR 之前的旧账本记录，其无 `snapshot` 字段）：`git log --format=%H -1 --before=<published_at> -- <rel_path>` 再 `git show`。

回退路径经实测命中正确快照：`f2f48c2`（2026-10-14 22:28:28），其首行 `# 模型打五折，未必是好事` 与账本记录的标题一致，blob `5e1072810c4fc156fc23041f43f3c410762c507f`。

**二、范文库的自动增长一律丢弃**。范文属风格资产，其类别与档次需人工把关，不接受启发式自动登记。`style_learn.py` 在调用 `learn-edits` 前后对 `.wewrite/exemplars/` 拍快照并还原：删除新增文件、恢复 `index.yaml` 原文；被清除的文件名记入输出的 `exemplars_purged` 字段（不静默）。

实测生效：`--force` 重跑时输出 `"exemplars_purged": ["story-emotional-001.md"]`，目录还原正确。

**三、内容级的选择**：
- **伪造的 001 删除**（不做"留作反面样本"的保留——它标着 `ownership: user`，保留即持续污染）。
- **真实的 002 重新归档为 `tech-opinion-001.md`**：类别改 `tech-opinion`，`index.yaml` 同步。它是本人真实改稿，值得留作范文。
- **范文的使用纪律不改**：`wewrite-write/SKILL.md:79-83` 已规定第三方范文只能参考节奏结构、不得复用观点与句子，此约束继续有效。

**四、ADR-0035 的发现一作废**，其余部分（pattern 提取是必须补的一环、回路在无人值守下不是全自动的）继续有效。

## Consequences

- 发布链路与风格回路之间多了一个**契约**：`publish.py` 必须写 `snapshot`，`style_learn.py` 才能精确取基线。旧记录靠 `--before` 回退可用，但**回退路径依赖"发布时刻早于改稿提交时刻"**这一时序假设；新记录不再依赖任何时序假设。
- **新契约的载体是运行时状态，不入库。** 账本在 `.newsagg/publish-ledger.jsonl`，按 `.gitignore:40` 排除（ADR-0027：本地与服务器各自持有运行时状态）。这与"发布快照必须被记录"存在张力：账本一旦丢失（换机、清空运行时、服务器重建），快照就退回路径 2 的时序假设——而那正是本 ADR 要消除的失效模式。当前可接受，因为 ADR-0027 保证同一时刻只有一个环境发布，且账本与该环境的 git 仓库同生共死；但**不要把账本当可重建的缓存**。若要跨环境共享，需把快照指纹移入入库的产物（如 `reports/` 或 `outputs/` 侧的 sidecar），代价是多一个需要保持同步的文件。
- 风格学习现在**第一次真实闭环**：本次运行产出 `.wewrite/lessons/2026-10-15-diff.yaml`（`char_diff: -38`、`title_changed: true`），并由 agent 提取出 **6 条 pattern**（`title_actor_and_benefit`、`opening_benefit_first`、`h2_reader_benefit`、`no_self_referential_trail`、`thesis_early_not_diluted`、`abstract_number_to_bill`）。`wewrite learn-edits --summarize` 确认 `total_lessons 1 / total_patterns 6`，每条 `occ=1 conf=3.0 hard=false`——与契约一致（单次修改只作软参考）。
- **pattern 提取这一步没有自动化**：`learn-edits` 硬编码 `"patterns": []`，需 agent 读 diff 后产出。ADR-0035 已记录该缺口，本 ADR 不解决它，只是确认回路在有人（或 agent）参与时可用。
- 范文库现在只有 1 篇且类别正确。但**分类器的缺陷仍在 wewrite 包内**：`extract_exemplar.py` 的 `detect_category` 未修（属 vendor 代码，改动需遵循 ADR-0024 的最小改动原则）。当前靠"不自动入库 + 人工指定 `--category`"绕过。若将来启用自动入库，必须先修分类器。
- `quality_score >= 50` 这个门槛过低（满分 100），几乎任何通顺文章都会入库。本 ADR 通过"一律丢弃"规避，未改门槛值。
