# Theme Presets

Use one theme for one image package. Do not mix palettes across pages unless the user explicitly asks for a deliberate multi-chapter system.

## Series Theme Rotation (multi-article accounts)

One theme per package is still the rule. But when the same account publishes article after article, reusing the same mode + palette + shape set every time is what makes a reader feel "I have seen this card before, I already know it." Rotate at the **series** level, not inside a package.

### The six palettes are six distinct identities

An earlier revision of this section claimed the palettes cluster, and that switching inside a cluster is not a rotation. **That was wrong**, and it was wrong for a specific reason worth recording: it clustered on `--paper` alone, while the rendered identity is carried by paper **and** text ink **and** accent together.

Measured from the rendered pixels — `local-tests/p4-verify/palette-probe.cjs` samples the actual PNGs, it does not read CSS tokens:

| Palette | dominant tone | share | text ink | identity |
| --- | --- | --- | --- | --- |
| Ink Classic | `#f0f0e0` | 78% | `#000000` pure black | neutral-warm paper, **black** accent |
| Indigo Porcelain | `#f0f0f0` | 78% | `#001030` navy | the only cool paper, navy accent |
| Forest Ink | `#f0f0e0` | 78% | `#102010` green-black | same paper as Ink Classic, **green** accent |
| Kraft Paper | `#e0d0c0` | 88% | `#201010` brown-black | tan paper, burnt-orange accent |
| Dune | `#f0e0d0` | 78% | `#101010` neutral | sand paper, khaki accent |
| Midnight Ink | `#000000` | 78% | `#e0e0c0` cream | inverted |

Ink Classic and Forest Ink genuinely share a paper tone (quantized to the same bucket; exact ΔRGB 2/1/0). **They are still not the same look**, because the accent is black in one and green in the other, and the text ink is neutral-black vs green-black. A categorical accent difference is not a tonal one — nobody mistakes green for black.

Measured on paper **and** ink together, the closest pair in the set is 64 Δ apart, and every pair is distinguishable. **Changing the palette is a real change, every time.**

### The one real family boundary is the mode

There is exactly one genuine family boundary in this skill: **Editorial vs Swiss.** Different typeface, different grid, different component vocabulary (`ledger` / `marginalia` / `pull-quote` vs `matrix` / `kpi-tower` / `h-bar`), and `style-system.md` forbids mixing them. Every palette sits inside one of those two.

So `Editorial → Swiss` is the largest change available for a returning account, and the one worth planning for deliberately. Palette rotation inside a mode is a smaller change — but still a real one.

### What actually makes a series feel repetitive

Not the palette. Measured across the seven real tech decks in `local-tests/`:

- **39 pages, 0 with text weight on the right** — the composition anchor is effectively constant (see `variation-engine.md` §2.A).
- The same shape family carries most of every set; R10 reports this on 7/7 decks.
- `.h-xl` appears 4-6 times per set.

The fatigue comes from **layout and composition repeating**, not from colour. Rotate the palette by all means, but the load-bearing rotation is the mode, the shape families, and the composition anchors.

### The other rotation axes

- **Mode**: an all-Editorial series should not stay all-Editorial forever. Swiss is a different visual personality entirely, and for data-shaped content it is usually the better fit (see `references/category-cookbook.md` → 技术 · Tech / Developer). Rotating the mode changes more than any palette swap can.
- **Light / dark**: once a series runs past six posts, let a dark package appear. Midnight Ink is the only dark Editorial palette; reach for it on night-debug, infra, security, incident, and cinematic content rather than as a change of scenery.
- **Shape families**: the rhythm rules in `SKILL.md` Step 4 govern one package. Across packages, rotate which shape is the signature — a series where every post opens with a full-bleed cover, or every post closes on a numbered turnaround, reads as monotone even when each package passes R10.

Pick the rotation from editorial intent, not for novelty: a debug story and a release note from the same account *should* look different from each other.

### Not adopted: the warm-paper ban

The frontend-skill family this file borrows the rotation idea from goes further and **bans** the warm-paper family outright (`#f5f1ea`, `#f7f5f1`, `#efeae0` …) as the "second-most-recurring AI tell". Note that Forest Ink's `#f5f1e8` is one hex digit away from the banned `#f5f1ea`.

**The ban is not adopted.** That palette family is the Editorial identity, deliberately restored in v0.12, and it corresponds to a real reference (Monocle / Kinfolk / Cereal) rather than to a landing-page trend. What transfers is the **rotation requirement** above, not the ban.

Verification artifact: `local-tests/p4-verify/` renders the same page in all six palettes (`index.html` → `output/*.png`) and tiles them into `output/compare-strip.png`. The identity table above was **measured from those PNGs by `palette-probe.cjs`**, not read off CSS tokens — the earlier, wrong clustering came from reading tokens, and the difference between tokens and render is exactly what the first version got wrong.


## Editorial Magazine x E-ink Palettes

These palettes are tuned for static Rednote and WeChat images in the electronic-magazine mode.

### Ink Classic

Use for business commentary, AI essays, product thinking, and neutral editorial posts.

```css
:root {
  --paper: #f3f0e8;
  --paper-2: #ebe6da;
  --ink: #0a0a0b;
  --muted: #68625a;
  --line: rgba(10,10,11,.22);
  --accent: #111111;
  --accent-soft: #d8d2c6;
}
```

### Indigo Porcelain

Use for technology, research, data, AI infrastructure, and calm analytical writing.

```css
:root {
  --paper: #f2f4f5;
  --paper-2: #e5ebef;
  --ink: #0a1f3d;
  --muted: #5f6d78;
  --line: rgba(10,31,61,.20);
  --accent: #315d93;
  --accent-soft: #d7e1ec;
}
```

### Forest Ink

Use for hiking, outdoor, nature, sustainability, personal field notes, and grounded lifestyle posts.

```css
:root {
  --paper: #f5f1e8;
  --paper-2: #e8dfcf;
  --ink: #16251b;
  --muted: #5d665d;
  --line: rgba(22,37,27,.22);
  --accent: #2e6b4f;
  --accent-soft: #d4dfd2;
}
```

### Kraft Paper

Use for memory, craft, personal essays, old objects, creator notes, and warm low-tech topics.

```css
:root {
  --paper: #eedfc7;
  --paper-2: #dfc9a8;
  --ink: #2a1e13;
  --muted: #755f49;
  --line: rgba(42,30,19,.24);
  --accent: #9b5a2e;
  --accent-soft: #d5b58f;
}
```

### Dune

Use for design, object studies, portfolio-like covers, gallery tone, and restrained aesthetic posts.

```css
:root {
  --paper: #f0e6d2;
  --paper-2: #ded0b7;
  --ink: #1f1a14;
  --muted: #6f6557;
  --line: rgba(31,26,20,.22);
  --accent: #8f7650;
  --accent-soft: #d4c2a4;
}
```

### Midnight Ink

The **only** official dark Editorial palette. Use for game key art, night photography, cinematic covers, dark-themed cultural pieces — content whose source imagery is already dark and would be diminished by paper backgrounds. Do not improvise other dark palettes; if Midnight Ink does not fit, pick a different mode (Editorial dark is not a universal switch).

```css
:root {
  --paper: #0e0d0c;
  --paper-2: #1a1714;
  --ink: #ece2cf;
  --muted: #9a8c75;
  --line: rgba(236,226,207,.22);
  --accent: #d4a04a;
  --accent-soft: #3a2a14;
}
```

Midnight Ink **must** also override two background layers — light-paper math does not carry over:

```css
[data-theme="midnight-ink"] .grain {
  opacity: .26;
  mix-blend-mode: screen;
  background-image: radial-gradient(rgba(255,244,214,.10) 1px, transparent 1px);
}
[data-theme="midnight-ink"] .paper-wash {
  background:
    radial-gradient(80% 50% at 28% 16%, rgba(212,160,74,.12), transparent 64%),
    radial-gradient(70% 60% at 80% 86%, rgba(60,40,20,.20), transparent 72%),
    linear-gradient(180deg, rgba(236,226,207,.02), rgba(0,0,0,.32));
}
[data-theme="midnight-ink"] .frame-img {
  background: #18120f;
  box-shadow: 0 0 0 1px rgba(236,226,207,.10);
}
```

The seed `template-editorial-card.html` ships these overrides — just switch `data-theme` and they apply automatically.

Magazine palette rules:

- Use `--paper` as the main background and `--ink` as primary type.
- Use `--accent` sparingly: section marker, page number, pull quote rule, or one highlighted phrase.
- `--paper-2` can support photo wells, issue strips, or checklist bands.
- Light palettes (the first five): do not turn into beige-on-beige. Maintain real contrast.
- Midnight Ink: do not stack opaque cards or fills on the page. Dark Editorial relies on photo bleeds + warm gilt accent for hierarchy, not background blocks.

## Swiss International Palettes

These accents anchor the Swiss International mode.

### IKB Blue

Default for AI, technology, product updates, design, and engineering topics.

```css
:root {
  --paper: #fafaf8;
  --ink: #0a0a0a;
  --grey-1: #f0f0ee;
  --grey-2: #d4d4d2;
  --grey-3: #737373;
  --accent: #002FA7;
  --accent-on: #ffffff;
}
```

### Lemon Yellow

Use for young, consumer, active, retail, sporty, or playful information.

```css
:root {
  --paper: #fafaf8;
  --ink: #0a0a0a;
  --grey-1: #f0f0ee;
  --grey-2: #d4d4d2;
  --grey-3: #737373;
  --accent: #FFD500;
  --accent-on: #0a0a0a;
}
```

### Lemon Green

Use for ecology, future, emerging tech, health, and highlighter-like contemporary topics.

```css
:root {
  --paper: #fafaf8;
  --ink: #0a0a0a;
  --grey-1: #f0f0ee;
  --grey-2: #d4d4d2;
  --grey-3: #737373;
  --accent: #C5E803;
  --accent-on: #0a0a0a;
}
```

### Safety Orange

Use for industrial, warning, urgency, risk, decision points, and sharp corrections.

```css
:root {
  --paper: #fafaf8;
  --ink: #0a0a0a;
  --grey-1: #f0f0ee;
  --grey-2: #d4d4d2;
  --grey-3: #737373;
  --accent: #FF6B35;
  --accent-on: #ffffff;
}
```

Swiss palette rules:

- Use exactly one `--accent`.
- Do not use gradients, shadows, glass, or mixed accent colors.
- If the accent is yellow or green, text on accent must use `--accent-on: #0a0a0a`.
- Prefer pure blocks, hairline rules, and grid rhythm.
