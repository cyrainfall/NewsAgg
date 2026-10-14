# Anti-AI-Tells

The question this file answers: **would a stranger guess a machine made this?**

That is a different question from the one `style-system.md` answers ("does this still look like Editorial / Swiss?"). A card can pass the Style Identity Test and still read as machine-made, and a card can fail the style test while not reading as AI at all. Both tests are needed.

**Boundary — do not duplicate.** These already exist and are not restated here:

| Already covered | Where |
| --- | --- |
| Decorative blobs, ovals, stickers, meaningless circles, nested cards, generic SaaS cards | `SKILL.md` → Non-Negotiables |
| Flat Editorial paper with no atmosphere; Swiss with a serif | `style-system.md` → Anti-Patterns A-D, Style Identity Test |
| Fake data, fabricated percentages, invented release details | `SKILL.md` → Non-Negotiables |
| Undefined jargon, unstated analogies | `explainer-sequence.md` → The Jargon Gate / Analogy Gate |
| Shape monotony across a set | `validate-social-deck.mjs` → **R10** |
| Banned AI-ish copy phrases | `validate-social-deck.mjs` → **R12** |

What is **not** covered anywhere else, and is the reason this file exists: **the copy axis**, plus the recognition patterns for data, typography, and material.

---

## 0. The Squint Test

Before any checklist, run this on the rendered page:

> Hide every word. Can you still tell **which article** this page came from?

If the remaining composition — blocks, rules, image wells, colour weights — could carry any article on any topic, the layout is generic, and generic layout is the foundation of every AI tell below. The individual tells are symptoms; this is the disease.

The test is cheap and it is the one that catches things no rule list predicts. It is also the reason a card set can pass R1-R12 and still feel machine-made.

---

## 1. Copy Tells

**The highest-value section, and the one nothing else in this skill covers.**

### 1.A Banned phrasing

Generic marketing verbs and constructions that carry no information. If one appears, replace it with the specific fact it was standing in for.

Chinese — the ones that actually land on cards:

```text
赋能 · 解锁 · 重塑 · 颠覆 · 无缝 · 极致 · 一站式 · 全新升级 · 下一代 · 革命性
让你的 X 效率提升 N 倍（没有实测）
从此告别 X
未来可期 · 拥抱变化 · 持续进化
深入浅出地剖析 · 一文读懂 · 彻底搞懂（占位式承诺：承诺了理解，却没给信息）
```

English equivalents, since Swiss cards mix in Latin labels:

```text
unleash · elevate · revolutionize · next-gen · seamless · powerful solution · transformative
```

**Fake-precise numbers.** `提升 300%`, `快 4.1×`, `92% 的用户` — either the number comes from real data the user supplied, or it is banned. If it is illustrative, label it in the copy (`示例`) rather than dressing it as a measurement. This restates the Non-Negotiables ban in the place copy actually gets written.

**Fake brand names.** `Acme` / `NovaCore` / `Flowbit` / `某科技公司` in screenshots and captions. Use the real product or a redacted form the user approves.

**The colon-titling reflex.** `XXX：YYY 的终极指南` / `从 A 到 B：一次讲透`. One colon title in a set is fine; a set of colon titles is a template, not a voice.

### 1.B Copy Self-Audit (run before delivery)

Re-read **every visible string** — headline, kicker, label, body, caption, footer, code comment that shows in a screenshot, alt text. Flag and rewrite any string that is:

1. **Grammatically broken** or machine-translated in feel.
2. **Referentially unclear** — "这样就能保持" without the reader knowing what "这样" is.
3. **AI hallucination** — cute-but-wrong wordplay, a forced metaphor that does not track, "elegant nothing" phrases that sound deep and mean nothing.
4. **An LLM trying to sound thoughtful** — fake humility, fake craftsman labels, mock-poetic micro-meta.

> **AI-flavoured cute copy is worse than boring copy.** When in doubt, replace it with a plain functional sentence.

This audit is mandatory and manual. No script can run it — R12 only catches the phrase list in §1.A.

### 1.C One Copy Register Per Page

Do not mix registers inside one page:

- technical mono (`47 tasks · 0.6 ctx-switches/day`)
- editorial prose (`那是一个安静的下午`)
- marketing punch (`效率直接翻倍`)

Pick the page's register from its beat — a 证据 page is mono, a 现象 page is prose, a 记忆点 page is punch — and keep one per page. Mixed registers on one card is one of the fastest ways to read as machine-assembled, because a human writing that page would have had a single voice.

---

## 2. Data Tells

- **Three identical stat columns.** `99% 满意度 / 省下 30 分钟 / 无限扩容` — the shape is a template even when each number is real. Vary the presentation or drop to two.
- **A chart that shades the layout instead of carrying a claim.** If removing the chart does not remove an argument, the chart is decoration.
- **Stat cards repeated with no product reason** — a number per card because cards were available, not because the numbers compare.
- **Numbers with no unit or no baseline.** `79` means nothing; `79ms，同一台机器` does.

Data tells are structural, so `R10` shape-family checks and the recipe rhythm rules catch some of them. The rest need the eye.

---

## 3. Typography Tells

- **Giant heading + weak tiny subcopy.** A 124px title over 18px grey text is the default AI hierarchy. Either the subcopy is one of the two anchor elements on the page, or it should not be there.
- **Too many font moods on one page.** Three families, or the same family at four unrelated weights, reads as assembled rather than set.
- **Lazy all-caps everywhere.** Uppercase is for labels, kickers, and metadata. If body or headlines are uppercase too, nothing is emphasised.
- **Gradient headline as a shortcut for "premium".** Already banned in Swiss (no gradients); banned here for Editorial too.

Floor rules (minimum readable sizes, Chinese title length bands) live in `components.md` and are enforced by R4 / R6. Those catch *too small*; this section catches *wrong relationship*.

---

## 4. Layout Tells

Reference `SKILL.md` → Non-Negotiables and `style-system.md` → Anti-Patterns. The recognition patterns worth naming:

- **Endless centered sections.** Centering every page removes the asymmetric rhythm that makes a set feel art-directed. See M09's `space-between` note in `layout-recipes.md` for the specific trap.
- **Identical structure repeated section after section.** Enforced by **R10** — this is the one layout tell with a script behind it.
- **Fake complexity without hierarchy.** Off-grid offsets, overlapping blocks, and rotated elements that do not establish reading order. Complexity that does not rank information is noise.
- **Empty decorative space with no purpose.** Distinct from deliberate whitespace: a void with no anchor on either side. `portrait-fill.md` and the 4-band density check govern this.

---

## 5. Material Tells

Already banned elsewhere; named here so the *reason* is on record:

- Default purple/blue AI gradients — banned in Swiss (no gradients) and in Editorial (paper/ink palette).
- Glassmorphism, glowing edges, floating spheres, random futuristic detailing — covered by the Non-Negotiables ban on decorative shapes.
- Over-rendered noise that hides the layout — the Editorial atmosphere layer must sit *behind* the type, never compete with it.

---

## 6. Density: a conflict, resolved

Two rules in this skill point in opposite directions, and both are correct:

| Rule | Says |
| --- | --- |
| 4-band density check, R5 | content must cover **≥75%** of the canvas; a large empty band needs a stated reason |
| web anti-slop §8 "density slop" | no over-packed sections, no trying to fill every empty area, no visually exhausting walls of content |

**Resolution: the target is full, not packed.** R5 exists to stop *accidental* voids — the lower third that goes empty because a ledger was too short. The anti-slop rule exists to stop *deliberate* cramming — filling that same third with a decorative strip. Both failures come from the same place: the content was not enough, and the fix was mechanical instead of editorial.

So: fill the canvas with **content that answers the page's question**. If there is not enough of it, change the recipe or split the page — do not add a strip, and do not leave the void.

---

## 7. Enforcement

| Section | Enforced at | Kind |
| --- | --- | --- |
| §0 Squint test | delivery review | **manual only** |
| §1.A banned phrasing | **validator R12** | automatable |
| §1.B copy self-audit | QA list | **manual only** |
| §1.C one copy register | QA list | **manual only** |
| §2 data tells | R10 (partial) + eye | partial |
| §3 typography tells | R4 / R6 (floors) + eye | partial |
| §4 layout tells | **R10** + 4-band density | automatable |
| §5 material tells | Non-Negotiables + Style Identity Test | process |

**R12 is a floor, not a filter.** It matches a fixed phrase list; it cannot detect a hollow sentence that uses none of those words. Passing R12 means "no banned phrases", not "the copy is good".

**Measured behaviour** (so nobody has to re-derive it):

- Verified in both directions against a fixture at `local-tests/r12-fixture/`: it fires on the banned phrases, stays silent on clean copy, and stays silent on text inside footer chrome (`.issue-strip` / `.foot` / `.chrome-min` are excluded — those carry page metadata, not copy).
- Across the seven real tech decks currently in `local-tests/`, R12 produced **0 hits**. So it rarely fires in practice. That is the expected shape for a floor: it is there to catch copy written in a hurry, not to comment on finished work.
- **False positives are possible and expected** when a word is literal in context — `解锁` in a security post about account unlock, `无缝` describing an actual seam-free material. R12 reports the phrase and its location; you decide. A reported hit is a prompt to look, not a verdict.

---

## 8. Deliberately Not Adopted

- **The premium-consumer beige/brass palette ban** (design-taste-frontend §4.2). The rule is right about the risk but wrong for this skill: the warm-paper palette *is* the Editorial identity, deliberately restored in v0.12. What transfers is the **rotation** requirement, not the ban — handled in `theme-presets.md` → Series Theme Rotation.
- **"Serif is very discouraged as default"** and **"Inter is discouraged"** (design-taste-frontend §4.1). Both are calibrated for generic landing pages. This skill's identity is exactly that pairing; adopting these would delete the Editorial mode.
- **Marquee / carousel / auto-play slop** (web §8). These are motion and interaction patterns; static cards have none.

---

## Provenance

Authored for this skill. Four source lists were consolidated and re-cut for static Chinese-language teaching cards:

- **copy tells + the four flag categories** ← `design-taste-frontend` §4.9 COPY SELF-AUDIT and fake-precise-number rules
- **one copy register per page** ← `design-taste-frontend` §4.9
- **data tells** ← `imagegen-frontend-web` §8 Data / KPI slop
- **typography and material tells** ← `imagegen-frontend-web` §8 Typography / Visual slop, and `imagegen-frontend-mobile` §21

The banned-phrase lists were **translated rather than copied** — the upstream lists are English marketing verbs, and the cards this skill produces are primarily Chinese, so the list had to be rebuilt from phrases that actually appear in that language.
