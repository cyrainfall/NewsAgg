# Explainer Sequence

For posts whose job is to **teach a mechanism** — technical principles, how-it-works, why-it-broke, how-the-pipeline-flows — to readers who do not already know the subject.

This file governs the **sequence**: what each page is doing and in what order. It does not govern what a single page looks like — that is `layout-recipes.md`.

> **The one tension to resolve first.** `content-planning.md` optimizes for *feed scannability* and its main tool is compression. Teaching needs the opposite: added context, added analogy, added causal steps. These are not in conflict once they are separated by layer —
>
> - **Inside one page**: still compress. A 1080×1440 page cannot hold a paragraph.
> - **Across the sequence**: the explanation lives in the order, not in any single page. Progressive disclosure is what makes a compressed card set teach something.
>
> If a set fails to explain, the fix is almost never "add more text to a page". It is "the sequence is missing a beat".

---

## 0. What Has Teeth

A rule with no enforcement point is decoration. This is the map — check that a rule you are about to follow actually has one, and be honest when it has none.

| Rule | Enforced at | Kind |
| --- | --- | --- |
| Design read declared (§1) | `SKILL.md` Step 2, then the QA list | process |
| One page, one question (§2) | `SKILL.md` Step 2, then the QA read-aloud test | process |
| 误解 beat present (§3) | QA list | process |
| ≥3 different explanation moves (§6) | **validator R10** (shape families) + QA list | **automatable** |
| Analogy states where it breaks (§4) | QA list | **manual only** |
| Jargon translated on first use (§5) | QA list | **manual only** |
| Spine declared (§7) | the design read | process |

**Be clear about the limits.** Only §6 has a script behind it, and only because a shape family is measurable. Nothing can automatically tell whether an analogy is *correct*, whether a translation is actually plain, or whether a question is genuinely answered by its page. Those are manual checks, and pretending otherwise would be worse than having no check at all — a green pass that means nothing is how a set ships broken.

---

## 1. Design Read (say it before generating)

Adapted from the one-line declaration in the `imagegen-frontend-web` / `design-taste-frontend` family of skills. State it in one line before planning pages:

```text
读成：给 <读者是谁> 讲 <哪一个原理>，用 <母题> 母题，序列 <N 拍>，密度 <低 | 中 | 高>
```

Examples:

```text
读成：给不会用命令行的读者讲「请求为什么绕回了代理」，用「跟着一个包走一趟」母题，序列 6 拍，密度中
读成：给刚接手这套部署的同事讲「证书续期为什么半夜失败」，用「故障复现」母题，序列 5 拍，密度高
```

Declaring it costs one line and prevents the most common failure: opening the set without deciding who it is for. **"给谁讲" decides the whole sequence** — the same mechanism needs 4 pages for an engineer and 8 for a non-technical reader.

The read is internal unless the user asks to see the plan. Do not put it on a card.

---

## 2. One Page, One Question

Adapted from the "one image per section" rule in the imagegen-frontend-web family, re-cut for teaching.

**Every page answers exactly one question.** Write the question down for each page in the plan. If a page answers two questions, it is two pages.

This test is better than "does it feel crowded", because crowding is subjective and question-count is not:

```text
P1  Q: 我怎么知道出问题了？              → 现象
P2  Q: 我以为是哪儿的问题？              → 误解
P3  Q: 那实际发生在哪一步？              → 机制
P4  Q: 为什么那一步会导致这个结果？       → 机制（因果）
P5  Q: 有什么证据？                      → 证据
P6  Q: 什么时候这个结论不成立？           → 边界
P7  Q: 我该记住什么？                    → 记忆点
```

Two questions on one page is the single most common cause of "看了但没懂". It produces a page where the reader has to hold two unfinished thoughts at once.

**Do not merge beats to shorten the set.** If the set is too long, cut a *beat* (see §4) or move detail into the caption — do not stack two questions on one page.

---

## 3. Progressive Disclosure — the six beats

Default sequence for teaching one mechanism. Six is the default, not a requirement; five and seven both work.

| # | Beat | The page's job | Typical moves |
| --- | --- | --- | --- |
| 1 | **现象** | The reader recognises the problem from their own experience. No jargon. | M01 cover, M13 hero question |
| 2 | **误解** | Name what most people assume, and why that assumption is reasonable. | S05 trap rows, M04 pull quote |
| 3 | **机制** | What actually happens, step by step. Usually 2 pages. | S06 pipeline, M14 vertical pipeline |
| 4 | **证据** | Show the artifact — log, screenshot, measurement. | S04 interface mock, M10 evidence feature, M06 evidence wall |
| 5 | **边界** | When the conclusion does *not* hold. | S02 comparison, M15 before/after |
| 6 | **记忆点** | One sentence the reader could repeat to someone else. | M07 closing note, S07 takeaway ledger |

**The 误解 beat is not optional.** Skipping it produces "I explained it once" rather than "they understood it". Understanding happens when a wrong prior is displaced; if you never name the wrong prior, the reader keeps it and files your explanation next to it.

If you have room for only four beats: 现象 / 机制 / 证据 / 记忆点. If you have room for only three: 现象 / 机制 / 记忆点 — and then the mechanism page must carry the evidence inside it.

---

## 4. The Analogy Gate (hard)

Analogies are the main tool for reaching a non-technical reader, and the main source of confidently wrong mental models. Both failure modes are avoidable.

For every mechanism page block that introduces a new concept:

1. **Give one analogy.** At least one per mechanism beat, not per page.
2. **State where the analogy breaks.** Immediately, in the same block. An analogy without its failure point teaches the analogy, not the mechanism.
3. **Only map onto lived experience.** Home, traffic, post, queues, libraries, keys and locks, plumbing, filing. **Never explain one technical concept with another technical concept** — that is a definition, not an explanation.
4. **One analogy, used once.** Reusing the same comparison across pages wears it down; by the third use the reader has stopped mapping and is just recognising the words.
5. **Not on the cover.** The cover sells the symptom. Analogies belong from page 3 onward, once the reader cares.
6. **Do not reach for a spent analogy.** Before using one, ask whether it still carries information. These three are spent — using them is the same as not explaining:
   - network = water pipes
   - AI = a brain
   - cache = a fridge

**Where it breaks** is the load-bearing half. Two examples of the shape:

```text
类比：这一步像一个门卫，按名单顺序放行。
在哪失效：门卫只看脸，而规则引擎只看目的地；同一个请求换个人发起，结果不变。
```

```text
类比：证书续期像每月交一次房租，平时不用管。
在哪失效：房租迟一天只是罚款，证书迟一天整站 HTTPS 直接不可用。
```

---

## 5. The Jargon Gate (hard)

For a non-technical readership, undefined jargon is where comprehension dies — and it always dies quietly.

- **Every term gets a plain-language translation the first time it appears, in the same page block.** Not a glossary at the end.
- **Define by function, not by structure.** "TUN 是一张虚拟网卡" is structure and means nothing to the reader. "TUN 负责把要进代理的流量先抓过来" is function and means something.
- **One new term per page, maximum.** Two new terms on a page and the reader cannot tell which one the page was about.
- **No unexplained term in a cover, title, or H1.** The cover states the symptom in the reader's own words. If a term has to appear there, it needs its translation in the same headline block — which usually means the headline is wrong.
- **Acronyms do not count as words.** Expanding an acronym is not a translation (`服务器名称指示` explains nothing).

This is the same intent as the existing audience-copy rule in `SKILL.md` Step 4 ("audience-facing copy must describe the user's actual scene"), applied to terminology rather than to phrasing.

---

## 6. Explanation Moves → Recipes

Pick the move by **what the page must do**, then look up its recipe. Do not start from the recipe list — starting from a recipe is how a set ends up monotone (see `SKILL.md` Step 4 → Recipe rhythm, and R10 in `validate-social-deck.mjs`).

| Move | The page must… | Recipes |
| --- | --- | --- |
| **因果链** | show A leading to B leading to C | S06, M14 |
| **状态对比** | put two states side by side | S02, M15 |
| **顺序分层** | show that layer A decides *before* layer B gets a chance — label them `Layer 1 · 先` / `Layer 2 · 后` | S06, M14, S02 |
| **结构拆解** | show the parts of one system (what it is made of, not when it runs) | S12, S09, S03 |
| **证据** | show the real artifact | S04, M10, M06 |
| **常见误解** | name and displace a wrong assumption | S05, M04 |
| **量级** | make a difference in size feel real | S10, S09 |
| **提问引入** | open on the reader's own question | M13, M01 |
| **单句断言** | land one sentence with no competition | M04, M09 |
| **收束** | give one repeatable sentence | M07, S07 |
| **呼吸** | mark a chapter change | M12, M09 |

**A teaching set needs at least three different moves.** A set that is all 因果链 is a procedure, not an explanation — it tells the reader what happens without ever giving them a model to hold.

**顺序分层 is the strongest move for "why does the order matter" mechanisms**, and it is routinely confused with 结构拆解. They answer different questions:

- *顺序分层*: 「谁先做决定」 — label the layers by **time**: `Layer 1 · 先` / `Layer 2 · 后`. The reader's takeaway is a sequence, and the usual finding is that the earlier layer can make the later one unreachable.
- *结构拆解*: 「它由什么组成」 — label the layers by **role**: parts, tiers, modules. The reader's takeaway is a map.

If the mechanism's punchline is "the order decides the outcome", use 顺序分层 and put the ordering words in the layer labels themselves — not in a paragraph underneath them.

---

## 7. Narrative Spine (pick one, thread it)

Pick one framing and let it run through titles, labels, and page order. Mixed spines are the reason a set feels like unrelated cards.

| Spine | The reader's stance | Good for |
| --- | --- | --- |
| **跟着一个包/一条数据走一趟** | companion | request paths, data flow, pipelines |
| **拆解一台机器** | inspector | architecture, layered systems, protocol stacks |
| **排障复现** | witness | incidents, "why it broke", debugging |
| **照着清单加固** | apprentice | hardening, migration, config walkthroughs |
| **对比两种做法** | judge | before/after, old way vs new way |
| **建一栋楼的地基** | builder | foundational concepts taught bottom-up |

Declare the spine in the design read (§1). If you cannot name the spine in one phrase, the set has no spine yet.

---

## 8. Worked Plan

A seven-page teaching set for "why the request routed back into the proxy", non-technical audience:

```text
读成：给不会用命令行的读者讲「请求为什么绕回代理」，用「跟着一个包走一趟」母题，序列 7 拍，密度中

P1  现象     Q 我怎么知道出问题了？        M01     封面：能用，但某个站点永远转圈
P2  现象     Q 换台机器会好吗？            S05     三个"我试过的办法"，都没用
P3  误解     Q 我以为是哪儿的问题？         M04     大多数人以为是证书 —— 不是
P4  机制     Q 那实际发生在哪一步？         S06     包被虚拟网卡抓走 → 交给规则引擎
P5  机制     Q 为什么顺序会导致这个结果？    S02     直连规则在前 vs 在后，两种结果
P6  证据     Q 有什么证据？                S04     控制台状态页截图 + 三个实测数
P7  记忆点   Q 我该记住什么？              S07     一句话：代理没问题，顺序才是
```

Check it against the rules above: every page answers one question (§2); the 误解 beat exists (§3); moves are varied — 封面 / 陷阱 / 断言 / 因果链 / 对比 / 证据 / 收束 (§6); one spine throughout (§7); the analogy (if used) sits on P4/P5, not P1 (§4).

---

## 9. What This File Does Not Cover

- **What a page looks like.** That is `layout-recipes.md`.
- **How many pages the platform tolerates.** That is `platform-specs.md` and `content-planning.md` (5-9 pages for Rednote).
- **Copy compression and cover hooks.** That is `content-planning.md`. This file assumes the copy is already compressed.
- **Motion.** If a mechanism's core is a *transition* rather than a *state*, a still page is the wrong medium and the honest move is a Live Photo card — see `live-photo-production.md`. The judgment rule for "which step deserves motion" is deliberately not specified here yet.

## 10. Deliberately Not Adopted

Kept as a record so a later reader does not "fix the omission":

- **One standalone image per section** (imagegen-frontend-web). Correct for website design references, wrong for cards — the card *is* the image, and the canvas is a fixed 1080×1440.
- **Free dial adjustment for variance / motion / density** (design-taste-frontend §1). Conflicts with this skill's core promise of stable output from a fixed preset set. Only the *declaration ritual* (§1 above) was adopted.
- **Device mockup present by default** (imagegen-frontend-mobile §9). A phone bezel costs canvas that a 3:4 card cannot spare; the mockup stays opt-in. See `screenshot-treatment.md`.

---

## Provenance

Authored for this skill. Two structural ideas are adapted from the frontend-skill family in `.codebuddy/skills/` and re-cut for static teaching cards:

- **one page, one question** ← the "one image per section" hard rule in `imagegen-frontend-web`
- **the one-line design read before generating** ← `design-taste-frontend` §0.B

Everything else (the six beats, both gates, the move→recipe table, the spine list) is written here. The upstream skills do not cover teaching, so nothing further was taken from them.
