# QA Checklist

Run this before final delivery.

## Dimensions

- Rednote images are `1080 x 1440`.
- WeChat 21:9 cover is `2100 x 900`.
- WeChat 1:1 cover is `1080 x 1080`.
- WeChat pair preview exists in the same HTML when WeChat covers are requested.
- File names are stable and ordered.

## Text

- No text overflows or touches the edge.
- No content collides with `.foot` / `.issue-strip`. If body copy or a closing line is overlapping the page footer, the footer is probably `position: absolute` while content above grew. Switch to flex `margin-top: auto` (see Anti-pattern C in `style-system.md`).
- Cover title is readable on a phone at thumbnail size.
- Body text is not smaller than it needs to be.
- Chinese line breaks are intentional.
- Important product names, English terms, and code snippets are spelled correctly.
- Captions match the source text; no invented facts.

## Teaching Sets

Run this section **only** when the set's job is to explain a mechanism (see `explainer-sequence.md`). Skip it for lists, reviews, roundups, and news.

- Design read was declared before planning: audience / mechanism / spine / beats / density.
- **Every page answers exactly one question.** Write the question for each page. Any page with two questions is two pages.
- **The 误解 beat exists.** Without it the set is a procedure, not an explanation.
- At least **three different explanation moves** appear (因果链 / 状态对比 / 分层拆解 / 证据 / 常见误解 / 量级 / 提问 / 单句断言 / 收束 / 呼吸).
- Every new term has a plain-language translation in the **same page block**, defined by function rather than structure.
- No unexplained term in the cover, title, or any headline.
- At most one new term per page.
- Every mechanism beat has an analogy, and each analogy **states where it breaks**.
- No analogy maps a technical concept onto another technical concept.
- The same analogy is not reused across pages.
- No spent analogy: network = pipes, AI = a brain, cache = a fridge.
- The last page gives one sentence the reader could repeat to someone else.

**Sequence read-aloud test.** Read the set aloud using only the page questions. If the questions alone do not tell the story, the sequence is broken — fix the order or split a page. Do not fix it by adding copy to a page.

## Layout

- Each page has one clear focal point.
- Pages do not all use the same structure.
- Lower empty space is either intentional or fixed.
- Images and screenshots align to the grid.
- Screenshot pages give the screenshot enough area.
- 1:1 WeChat cover uses a simplified short title and is composed separately, not blindly cropped.
- If a page overflows, measure the overflow before editing. Do not remove content until the measured overflow requires it.
- Titles need visible breathing room before the next content block. Main display titles should usually keep at least 28px below; local headings should keep at least 16px.

### Overflow Correction Ladder

Use validator output before changing the layout:

- `1-40px` over: nudge the content group up or tighten one gap/padding by 20-40px. Do not delete content.
- `40-90px` over: compact local gaps/padding and reduce one block height. Avoid cutting copy.
- `90-160px` over: reduce a display title slightly or compress one paragraph before deleting content.
- `160px+` over: switch to a higher-capacity recipe, merge modules, or remove content intentionally.

After fixing overflow, check for over-correction:

- If `R8` reports large bottom whitespace, restore some spacing, move the content down slightly, or expand the final block.
- Do not accept a fix that turns a small overflow into a large empty lower band.
- Prefer one measured adjustment per render pass, then re-run validation.

### 4-band Density Check (3:4 only — run after render)

Open the rendered PNG. Mentally divide it into 4 horizontal bands of 360px each (0-25%, 25-50%, 50-75%, 75-100%). For each band, classify as:

- **Filled** — contains text, image, data, or rule.
- **Justified empty** — empty for a stated reason: hero-image breathing (M01/M16), single-sentence statement (M04/M13), leading/trailing margin (top 8% / bottom 8%).
- **Under-filled** — empty with no reason. **Any single under-filled band > 15% canvas height (>216px) is a fail.**

A poster passes when:

1. Total Filled + Justified empty ≥ 100%.
2. Filled bands cover ≥ 75% canvas height (≥ 1080px of 1440px).
3. No two adjacent bands are both "justified empty" — that creates a >25% void mid-poster.

If failing: don't shrink the canvas, don't add decorative blobs. Either expand copy (more ledger items, longer paragraphs, supporting evidence row, marginalia column) or switch recipe (M07 → M04 for genuinely-short content; M03 → M11 to add a marginal column; thin ledger → M08 Tall Ledger with bigger rows).

### Recipe Rhythm Check (whole set, not per page)

Run this across the package after the per-page checks. `validate-social-deck.mjs` reports it as `R10` (advisory).

### Shape Families

Classify each poster by its dominant structure, not by its recipe ID. This table is the taxonomy `validate-social-deck.mjs` R10 implements — if the two ever disagree, the validator is what runs, so fix this table.

| Family | Marker |
| --- | --- |
| `image-full` | a media element covers ≥78% of the canvas |
| `image-well` | media covers 34-78% |
| `map` | `.map-block` |
| `matrix` | `.matrix-fill` / `.cross-mat` / `.dot-mat` / `.ring-mat` |
| `chart` | `.h-bar-chart` / `.kpi-tower-row` |
| `pipeline` | `.pipeline-v` / `.stacked-ledger` |
| `rows` | 3+ numbered leaf badges (`01`, `02`, …) outside the footer chrome — detected structurally, so scoped class names like `.tk-nb` / `.ws-nb` count too |
| `statement` | `.pullquote` with no numbered rows |
| `split` | `.marginalia` / `.col-2*` / `.col-3` / `.grid-2-9` |
| `card-grid` | `.card-fill` / `.card-outlined` / `.card-accent` / `.card-ink` |
| `text` | none of the above |

There is no separate `ledger` family. A ledger **is** a set of numbered rows, and the validator detects it structurally as `rows` rather than by the seed's `.ledger-row` class — task decks routinely ship scoped class names (`.tk-nb`, `.ws-nb`, `.stat-nb`). Families are checked in the order listed, so the first match wins.

Pass conditions:

- No family appears more than **twice** in one set.
- No two **adjacent** posters share a family.
- The set shows at least **5 distinct families** on 7+ pages, **4** on 5-6 pages, **3** on 3-4 pages.

Only Rednote 3:4 cards count — WeChat `21:9` / `1:1` covers are a separate deliverable and are excluded.

If failing: do not add decorative filler to break up the rhythm. Re-pick recipes — swap one numbered-row page for a comparison (S02), a flow (S06), a chart (S10), or an evidence page (S04 / M06). See "Recipe rhythm" in `SKILL.md` Step 4.

### Composition Anchor Check (whole set, not per page)

The orthogonal axis to shape family, and the one that stays broken after you fix the shapes. `validate-social-deck.mjs` reports it as `R13` (advisory) with an `anchors:` sequence and an `anchorMix:` tally.

A set can pass R10 with seven distinct shape families and still hold **two** composition anchors — `local-tests/r10-baseline` is exactly that deck. Different structures, same page.

R13's signature is a geometric proxy, not one of the ten named anchors:

| Axis | Values | Measured from |
| --- | --- | --- |
| vertical | `top` / `mid` / `bottom` | area-weighted vertical centre of the page's text mass |
| horizontal | `centered` / `right` / `indented` / `full` / `left` | area-weighted horizontal centre + the text band's insets from both margins |
| media | `+media` appended | media covering ≥34% of the board |

Pass conditions (mirrors `variation-engine.md` §2.B):

- At least **3 distinct anchors** on 5+ pages, **2** on 3-4 pages.
- **Anchor A** (the `top-full` / `top-left` proxy) used at most **twice**.
- No two **adjacent** pages share an anchor.

Where R13 disagrees with your eye, your eye wins — it is a proxy, and it cannot see an off-grid offset or an image-canvas safe area. Treat a WARN as "go look".

### Programmatic Layout Checks

`validate-social-deck.mjs` reports:

- `R1` / `R8`: DOM overflow and visual overflow, including measured pixel amount and the lowest overflowing element.
- `R8`: bottom whitespace and active content height, which catches "fixed too much" layouts.
- `R9`: title-to-content gap, which catches titles visually touching the next block.

Use these numbers as the first diagnosis. Multimodal inspection is still useful for taste, but overflow, empty lower bands, and title gaps should be handled from measured output first.

## Style

- Package uses one visual system.
- Swiss identity test passes for every Swiss poster (see `style-system.md` "Style Identity Test"). In particular: every display title >=72px uses a typed class with weight <=300; no serif font is loaded; only one accent color is used. A bold 90px headline is not Swiss.
- Editorial identity test passes for every Editorial poster: at least one atmosphere layer beyond a flat fill (paper grain / ink wash / WebGL); serif display family on the title; at least one of large photo well, serif pull quote, marginalia column, or true ledger structure. A flat paper with serif title + mono pills everywhere is Swiss-in-disguise.
- Swiss uses one accent color only.
- Magazine style uses restrained paper/ink tones.
- No random SVG blobs, ovals, drops, circles, stickers, or decorative gradients.
- No nested cards.
- No excessive rounded corners.

## Images

- Supplied images are not distorted.
- Faces, hardware, product UI, and key text are not accidentally cropped.
- Screenshots remain readable.
- Screenshots use **one device style and one corner scale** across the set — a mixed set reads as assembled rather than designed. Validator **R11** reports three or more distinct `.frame-shot` radii; if mixing `.device-browser` and `.device-phone`, the frame width and outer margins are identical.
- No framed shot touches the canvas edge, and the outer margins read as even. See `screenshot-treatment.md` → Multi-shot discipline.
- Generated images do not contain unwanted text, logos, page numbers, or poster borders.
- Photo crops feel intentional.
- For any image fetched from the web (Pexels / Unsplash / Flickr CC / Wallhaven / direct search): the source URL is recorded in `assets/SOURCES.md`, and the user has been asked whether to add an in-image attribution caption. The user's answer is honored. Flickr CC attribution preserves the author name when the user opts in.

## Text-On-Image (when applicable)

Run these only for posters where text touches a photo (full-bleed cover, large image well, generated overlay). See `references/image-overlay.md`.

- Image area ≥60% of canvas → the photo passes the quiet-zone + light tests; no-mask composition was tried first; any tint is localized, image-toned, and only used if the thumbnail check fails.
- Subject map is documented as an HTML comment next to the hero block (face / focal feature location + safe zones).
- No display title (≥72 px) overlaps a face, hand, or key product feature.
- `object-position` was chosen from the subject-location table, not left at default for face shots.
- Thumbnail test: downscale the rendered PNG to 360 px wide and confirm the title is still legible without strain.

## Variation (per set)

Full detail in `references/variation-engine.md`. R10 covers shape families; these cover what R10 cannot see.

- Every page named a **composition anchor** at plan time.
- At least **3 distinct anchors** in a set of 5+ pages.
- **No anchor twice in a row.**
- **Anchor A (top-left stacked) appears at most twice** in the set.
- At least two **background modes** appear, not one repeated across every page.
- The closing block is not the same treatment as the previous set's.
- The **signature chrome** (kicker style, page number, issue strip, hairline placement) is identical across the set — set-level values do not drift.
- Exactly **one Second-Read Moment** (§6), placed at roughly 60-75% through the set, and it aids scan order or recall rather than only looking nice.

## Anti-AI-Tells

Run this before delivery on every set. Full detail in `references/anti-ai-tells.md`.

**The squint test** — hide every word; can you still tell which article this page came from? If the remaining composition could carry any article, the layout is generic and the tells below are symptoms of it.

- **Copy self-audit.** Re-read every visible string. Flag and rewrite anything that is machine-translated in feel, referentially unclear, cute-but-wrong wordplay, or an LLM straining to sound thoughtful. AI-flavoured cute copy is worse than boring copy.
- **No banned phrasing** — `赋能 / 解锁 / 重塑 / 颠覆 / 无缝 / 极致 / 一站式 / 全新升级 / 下一代 / 革命性`, and their English equivalents. Validator **R12** reports these; read each hit in context before rewriting.
- **No fake-precise numbers.** Every percentage or multiplier either comes from data the user supplied or is labelled as illustrative.
- **No fake brand names** (`Acme` / `NovaCore` / `Flowbit`) in copy, captions, or screenshot content.
- **One copy register per page.** Mono data, editorial prose, and marketing punch do not share a card.
- **No three identical stat columns.** A repeated stat shape is a template even when every number is real.
- **No giant heading over weak tiny subcopy.** Either the subcopy is one of the page's two anchors, or it goes.
- **No fake complexity.** Off-grid offsets and overlaps that do not establish reading order.
- **Density is full, not packed.** If a band is empty, add content that answers the page's question — do not add a decorative strip, and do not leave the void (see `anti-ai-tells.md` §6).

## Final Response

- Include the output path.
- Show the main cover inline when useful.
- Mention verification performed.
- Mention any limitations, such as low-resolution source assets.
