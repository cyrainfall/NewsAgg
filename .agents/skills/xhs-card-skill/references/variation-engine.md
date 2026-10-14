# Variation Engine

Why a set of cards can be correct on every individual check and still feel like the same card seven times.

`validate-social-deck.mjs` **R10** already measures **shape family** — ledger, pipeline, image, statement. That catches structural repetition. It cannot catch the other half: two pages can be the same shape family and still differ completely in **where the visual weight sits**. A left-weighted ledger and a centered ledger with a side rail are the same shape family and read as different pages.

R10 detects. This file generates.

**Where it sits among the other layers:**

| Layer | Question it answers |
| --- | --- |
| `explainer-sequence.md` | is the *order* teaching anything? |
| `anti-ai-tells.md` | does a page read as machine-made? |
| **`variation-engine.md`** | **do the pages differ from each other?** |
| **R10** | do the *shapes* differ? (the automatable half of the same question) |

---

## 0. What Has Teeth

| Axis | Level | Enforced at | Kind |
| --- | --- | --- | --- |
| Composition Anchor (§2) | per page | `SKILL.md` Step 4 plan, then QA list | process |
| Anchor distribution — ≥3 distinct, no adjacent repeat (§2) | per set | **validator R13** + QA list | **automatable (proxy)** |
| Background Mode (§3) | per page | `background-systems.md`, QA list | process |
| Closing-block variety (§4) | per set | recipe specs, QA list | process |
| Signature chrome set (§5) | per set | `content-planning.md` → Captions And Metadata | process |
| Second-Read Moment (§6) | per set | `SKILL.md` Step 4 plan, then QA list | **manual only** |
| Shape-family diversity | per set | **validator R10** | **automatable** |

**Three rows have a script: R10, R13, and the R5 density readout.** R13 does not name one of the ten anchors above — several of them (an off-grid offset, an image-canvas safe area) are not derivable from geometry. It measures where the page's **text mass** sits, area-weighted, on two axes (top / mid / bottom × centered / right / indented / full / left) plus a media flag, and warns when a set has fewer than three distinct signatures, when the top-left default appears more than twice, or when two adjacent pages share a signature. Treat a WARN as "go look", not a verdict.

A centroid-based check was first built and **rejected** — that history is worth keeping. `local-tests/anchor-probe.cjs` classified each page by the unweighted mean of its text-element centres. `local-tests/anchor-verify.cjs` re-measured the same 39 pages with two stronger metrics (bbox-area weighting, and the share of text area lying right of 60% of the board). The measurement, not a hunch:

- Across **39 pages in the seven real tech decks**, the centroid classified roughly half the pages "left" and half "centred", with the split landing almost exactly on the 45% threshold. The cause: on a card, text is nearly always a full-width left-aligned block (start at the 88px margin, wrap at the right margin), so its centroid sits at 45-50% no matter where the composition's weight actually is. **The metric cannot separate anchor A from anchor B.** Weighting by area does not rescue it — 11 of the 39 pages shift by ≥8 points between the two weightings, and both still land in the same 40-53% band.
- **The "0 of 39 pages put text mass on the right" figure is true and reproducible** (0 under both weightings), but read it as a low-sensitivity floor rather than a finding. The criterion only fires on a genuinely right-anchored block: a left-aligned *full-width* paragraph already has a centroid near 50%, so ">55%" is not reachable by any full-width page. It cannot come out any other way.
- **What does separate is the right-zone share.** Fraction of text area lying right of 60% of the board width: across the 39 real pages the maximum is **42%** and the median **37%**
  — while the deliberately right-anchored control page in `local-tests/skeleton-smoke-ed2` (`xhs-03`) measures **81%**. The control set's "2 of 9 on the right" also reproduces under the original centroid criterion (`xhs-03` weighted 69%, `xhs-09` unweighted 58%). A 42% → 81% gap is a usable separation; the centroid's 50% → 51% is not.
- **"Every page is a left-aligned block" is too strong.** Only **27 of the 39** pages have a majority of their text area starting on the left margin; the other 12 carry most of their text away from it, because card and ledger layouts indent their content. The honest form of the observation is: *no page is right-anchored, but not every page is left-anchored either.*
- Because a full-width paragraph alone puts ~37% of its area in the right zone, a threshold around 55-60% would flag genuine right-anchoring without firing on ordinary body copy. That is a report-only candidate, not an adopted rule — see §2.B for why the axis is still manual.

**The centroid is still not used, and never will be.** What R13 uses instead is the area-weighted mass position plus the text band's insets — a coarse signature (top-full, mid-full, bottom-full, …) rather than a named anchor. It is deliberately weak: it can see that a set never moves its mass off the top, which is the failure mode that matters; it cannot see an off-grid offset.

A check that always passes is worse than no check — it teaches you to ignore the report. R13 is worth its noise only because it discriminates: across the eight decks in `local-tests/`, the anchor tally ranges from **1 distinct signature** (`github-actions-ci-troubleshooting`: `mid-full` ×5; `tailscale-aliyun-netfilter`: `bottom-full` ×5) to **3** (`ai-second-brain-pitfalls`, `tailscale-packet-path`). A deck that passes R10 with seven distinct shape families can still land on **2** anchors — `local-tests/r10-baseline` does exactly that, which is this axis's whole argument, measured rather than asserted.

---

## 1. Two Levels, Do Not Mix Them

Variation happens at two levels, and they have different budgets:

- **Set level — one value for the whole set.** Theme family, set shape, the signature chrome, the Second-Read Moment. Changing these mid-set breaks the set's identity; that is what the Style Identity Test protects.
- **Page level — one value per page, deliberately varied.** Composition anchor, background mode, closing treatment. Changing these is what stops a set from reading as one card repeated.

A set where the *set-level* values drift reads as inconsistent. A set where the *page-level* values repeat reads as monotonous. Both failures are common, and they have opposite fixes.

---

## 2. Composition Anchor (per page)

Adapted from the `imagegen-frontend-web` composition-anchor axis, re-cut for a fixed 1080×1440 card.

Assign one anchor to every page **at plan time**, alongside its recipe. The recipe decides the structure; the anchor decides where the mass sits.

| Anchor | On a card | Natural recipes |
| --- | --- | --- |
| **A · Top-left stacked** | kicker → headline → content, all flush left, stacked top-down | any |
| **B · Centered statement** | everything centred, one idea, nothing competing | M09, M13, M04 |
| **C · Top-left lead, support bottom-right** | headline top-left; the evidence or stat block anchors bottom-right | M05, M08, S11 |
| **D · Bottom-left text over image** | image owns the canvas; text rides in a lower-left band | M16 Mode A, M02 |
| **E · Centered low** | text in the lower 40%; upper area is image or atmosphere | M16 Mode D, M10 |
| **F · Left-third label, right-two-thirds content** | narrow label rail left, wide content right | S03, M11 |
| **G · Right-third marginal, left-two-thirds content** | content left, annotated rail right | M11, M03 |
| **H · Off-grid editorial offset** | content deliberately breaks the grid margin by one step | M01, M03 |
| **I · Image-as-canvas, text in a safe area** | photo is the page; text sits in a documented quiet zone | M16, S08 |
| **J · Closing cluster flush-right** | the set's closing block sits right instead of left | M07, S07 |

### 2.A The overused default

In the source material the most-overused pattern is "left-text / right-image", and the instruction is to never open on it and never use it twice in a row. **The card equivalent is anchor A.** Every set defaults to it, because stacking top-left is what a flex column does when nobody decides otherwise.

Anchor A is allowed. It is not the fallback. If the plan does not name an anchor, the page becomes A by default — which is exactly how a set of seven A-pages happens.

**Measured on the seven real decks in `local-tests/`:** not one of their 39 pages places its text mass on the right — 0 under both the original centroid criterion and area weighting (`local-tests/anchor-verify.cjs`). But state the finding precisely, because the loose version overstates it:

- The text still *reaches* the right: the median page puts **37% of its text area right of 60% of the board width**. "No mass on the right" means the centre of gravity never crosses the middle, not that the right side is empty.
- And "left-aligned block" is not universal: only **27 of 39** pages have a majority of their text area on the left margin. The other 12 indent via cards and ledgers.

What is genuinely constant is the **outer shape**, not the alignment: a left-starting band of full-width text, page after page, regardless of recipe. Structurally different recipes, compositionally the same page. That is the gap this axis exists to close, and it is invisible to R10 because R10 counts shape families, not weight.

### 2.B Distribution rules

For a set of 5+ pages:

- **At least 3 distinct anchors.**
- **No anchor twice in a row.**
- **Anchor A at most twice in the set.**

These compose with R10 rather than replacing it. A set of 7 pages needs **≥5 shape families (R10) AND ≥3 distinct anchors (here)**. Shape family is the coarser of the two, so it needs the higher count.

**R13 reports on all three of these** (advisory, WARN only) using the geometric proxy described in §0. The one deviation: §2.B caps anchor A at twice, so R13 only counts the top-left default — it does not cap every other anchor at two, which would be stricter than this rule.

Note the interaction: one recipe can carry several anchors. The same M07 ledger is a different page as anchor A, anchor C, and anchor G. **Changing the anchor is often a cheaper way to break monotony than changing the recipe** — and it is the move that R10 cannot prompt you to make.

---

## 3. Background Mode (per page)

Filtered from the source list to what a static card can legally carry (Swiss bans gradients, shadows, and radius; Editorial has paper/ink palettes). Card-legal modes:

| Mode | Use |
| --- | --- |
| Solid surface with an inline asset | the safe default for content pages |
| Texture / paper / grid as background | via `background-systems.md` |
| Full-bleed image with tonal control | cover and evidence pages; see `image-overlay.md` |
| Editorial side-image split | 50/50, 60/40, 40/60 — invertible |
| Image as the entire visual, text in a safe area | M16 / S08 territory |
| Flat colour block + small detail crop as accent | Swiss; one accent only |
| Micro-noise over solid | Editorial paper depth, not flashy |

**Vary across the set.** A set where every page is "solid surface + inline asset" is flat even when every recipe differs. Two or three modes per set is normal; seven is chaos.

Rejected for cards: cinematic tonal gradients and duotone treatments (Swiss bans gradients outright; Editorial's paper/ink system does not carry them), and soft radial vignettes over a product crop (the "premium lifestyle" look this skill exists to avoid — see `anti-ai-tells.md` §5).

---

## 4. Closing-Block Variety (per set)

The closing block is the smallest high-signal element a set repeats. M07's own spec already offers the variants — pull quote, signature line, price/CTA, marginalia — and picking a different one each set is free variety.

If two sets in a row close with the same pull-quote treatment, the sets feel like one template. Rotate it the same way the theme rotates.

---

## 5. Signature Chrome Set (per set)

Pick a small set of recurring page furniture and keep it identical across the set: kicker style, page number, issue strip, corner label, hairline placement. This is the set-level value in §1 — it is what makes seven pages read as one issue rather than seven cards.

`content-planning.md` → Captions And Metadata already specifies the vocabulary (`AI TOOL / UPDATE / 2026`, `03 / 08`, `DO / DON'T / WHY / NOTE`). What this file adds is the constraint: **choose the set once, at plan time, and change it for the next set** — not mid-set.

---

## 6. Second-Read Moment (exactly one per set)

The strongest single lever against a set feeling like a template. Pick **one** unobvious-but-legible motif and place it deliberately, once:

| Motif | On a card |
| --- | --- |
| Asymmetric bleed that still respects hierarchy | one element extends to the canvas edge while the reading order stays intact |
| One oversized numeral serving structure | a number large enough to *be* the layout, not decorate it |
| A single unexpected material switch | one page flips to a different ground — e.g. the one Midnight Ink page in a light set |
| A narrow vertical side-rail note | a real annotation rail, borrowed from M11's `.mg-col` |
| A macro crop carrying the accent | an extreme close-up where the accent colour appears naturally |

Rules:

- **Exactly one.** Two moments is decoration, and decoration is what `anti-ai-tells.md` exists to catch.
- **Place it where attention dips** — usually 60-75% through the set, after the mechanism is established and before the closing. Not on page 1, where it competes with the hook.
- **It must aid scan order or recall.** If its only defence is "it looks nice", cut it.

This is the one axis in this file that has no equivalent anywhere else in the skill. It is also the cheapest way to make a set feel authored.

---

## 7. Worked Distribution

Seven-page teaching set, using axes from this file on top of the v0.19 example plan:

```text
Page  Beat  Shape family (R10)  Anchor (§2)  Background (§3)   Closing
P1    现象  image-full          D bottom-L   full-bleed image   —
P2    现象  rows                C support-BR solid              —
P3    误解  statement           B centered   solid + noise      pull quote
P4    机制  pipeline            F label-rail solid              —
P5    机制  split               G rail-right paper texture      —
P6    证据  image-well          I image-canv full-bleed         —
P7    记忆  rows                J flush-right solid             signature  ← Second-Read (§6)
```

Checks: 6 distinct shape families (R10 needs ≥5) · 7 distinct anchors — A never appears · 4 background modes · one Second-Read Moment, on the last page where a signature line lands as authored rather than as decoration.

Note that no page needed a different recipe to break the rhythm — the anchors carried it. That is the point of the axis.

---

## 8. Axes Deliberately Not Taken

- **Typography Character** (web §2, mobile §22). Free font-family choice conflicts with the Style Identity Test — a set must load one type system, and "rotate the typeface per set" would break the identity rules the skill enforces. The type *stance* (weight, tracking, size ladder) is already fixed per mode in `components.md`.
- **Decorative Asset Set** (mobile §22: starbursts, sticker accents, orbit lines, badge glyphs). Directly conflicts with `SKILL.md` → Non-Negotiables, which bans decorative shapes. Documented here so nobody re-imports it.
- **Motion-Implied Language** (both). Static cards have no motion. Deferred to the Live Photo branch — see `live-photo-production.md`.
- **Narrative Spine** (web §2). Already absorbed into `explainer-sequence.md` §7; duplicating it here would create two competing lists.
- **Hero Scale / Hero Architecture** (web §2). Already expressed as the cover choice among M01 / M16 / M13 / M09 / S01.

---

## Provenance

Authored for this skill. Two axes are adapted from the frontend-skill family in `.codebuddy/skills/`, re-cut for a fixed-size static card:

- **Composition Anchor** ← `imagegen-frontend-web` §2, with the card meaning of each anchor rewritten and the "top-left stacked" default named as the card equivalent of their overused left-text/right-image pattern
- **Second-Read Moment** ← `imagegen-frontend-web` §2, plus the placement guidance (once, at 60-75%, must aid scan order) which the source does not specify

Background Mode is a **filtered subset** of their background list, not a translation. Theme Paradigm / Palette Logic / Structure Bias / Signature Component Set were left to `theme-presets.md`, `explainer-sequence.md`, and `content-planning.md` rather than duplicated.
