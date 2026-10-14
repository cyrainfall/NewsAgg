# Layout Recipes

These are static social-image recipes written for this skill. They are not slide templates.

## Editorial Magazine x E-ink Recipes

These structures (ledger / marginalia / pull-quote / photo-well / pipeline-vertical) work for **any topic** that wants a magazine-feature pace — outdoor, AI, finance, food, workplace, gaming, all welcome. The mode is a visual stance, not a content filter. See `style-system.md` "Style ↔ content type are decoupled" for the full reasoning.

Portrait rule:

- Every 3:4 page should occupy the vertical canvas intentionally. If the content only creates a thin table, switch to M08/M09/M10 below or add a large pull quote, evidence image, marginal column, or full-height ledger.

**Content density rule (hard)**: on 1080×1440 cards, content must cover ≥75% of canvas height. Any pure-whitespace band >15% canvas height (>216px) needs a stated reason — hero-image breathing, single-sentence statement, or leading/trailing margins (combined ≤15%). Do NOT use `<div style="flex: 1"></div>` to push content to the vertical centre — Editorial magazines absorb whitespace across opposing pages; social cards are scrolled one at a time and under-filled cards read as "PowerPoint with a missing element." Each recipe below carries a `Minimum density:` line indicating the smallest content set that fills a 3:4 canvas. If your copy doesn't reach that floor, **shorten the canvas (switch to 1:1) or pick a different recipe** — never just publish under-filled.

### M01 Cover: Magazine Issue Cover

Best for Rednote page 1, portrait social cards, or article-card covers.

Structure:

- Top issue row: category, date, ratio, or account label.
- Large serif/Songti-like title, usually 2-4 lines.
- One large image or photo crop occupying 35%-55% of the page.
- Bottom issue strip with 3-5 points.

Style:

- Paper background, deep ink title.
- Photo can bleed inside a large rectangular well.
- Accent appears as one vertical rule, page number, or small label.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-01">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-4">
    <div class="issue-row">
      <span>Vol. 07</span><span class="dot"></span><span>排障手记</span>
    </div>
    <div class="stack gap-2">
      <p class="kicker">封面 · Cover</p>
      <h1 class="h-display">四次排查<br>才找到真凶</h1>
      <p class="h-sub">Four rounds, one ordering bug.</p>
    </div>
    <figure class="frame-img r-3x2">
      <img src="assets/cover.png" alt="封面图" style="object-position:center 40%">
    </figure>
    <p class="lead">从 TUN 网卡到规则顺序，一次把整条链路走通。</p>
  </div>
  <div class="issue-strip">
    <span>Diagnosis · 4 rounds</span>
    <span>—</span>
    <span>01 / 09</span>
  </div>
</section>
```

This is the shape the seed template ships as its own placeholder — it is the safest starting point, so keep the four-beat order: issue row → title block → image well → lead, with `.issue-strip` pinned at the bottom.

`r-3x2` puts the image at ~41% of canvas height, inside the spec's 35-55% band. Going to `r-16x10` drops it under the band and leaves a bottom gap; going to `r-1x1` pushes the page over.

**Every Editorial page needs `assets/magazine-bg-webgl.js` in the task folder.** The seed's `.mag-bg` canvas is wired to that script (referenced near the end of both template files); without the file the canvas renders as nothing and the whole page falls back to flat paper — which the Editorial Identity Test in `style-system.md` treats as a failure. Copy it from the skill's `assets/` into the task's `assets/` alongside the images.

### M02 Field Note Photo

Best for outdoor, object, hardware, or real-world observation.

Structure:

- Large documentary photo.
- Narrow caption column or bottom caption band.
- One short takeaway in large type.

Use when the photo is evidence, not decoration.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-02">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Field Note · 01</p>
    <figure class="frame-img r-4x3">
      <img src="assets/field.png" alt="现场记录" style="object-position:center 30%">
    </figure>
    <p class="img-cap">图 · 抓包当天的第一屏</p>
    <h2 class="h-xl">问题不在证书，在顺序</h2>
    <p class="body">同一台机器，直连通、代理不通，说明链路本身没坏，坏的是判断顺序。</p>
  </div>
  <div class="issue-strip">
    <span>Field · 01</span>
    <span>—</span>
    <span>02 / 09</span>
  </div>
</section>
```

Photo first, then the caption with `.img-cap`, then the takeaway. The takeaway sits **below** the photo on purpose: a field note reads as "I saw this, and here is what it means", not "here is my point, and here is a picture".

One M02 per set is normal; three in a row turns the carousel into a photo album. Pair it with a text page (M05 / M07 / M11) after each one.

Never leave `object-position` at the default. A documentary photo shot at eye level usually wants `center 30%` to `center 62%` depending on where the horizon or subject sits (see the crop table in `components.md`).

### M03 Editorial Essay Split

Best for explaining one idea with nuance.

Structure:

- Left: large title or pull quote.
- Right: 2-3 short paragraphs or numbered fragments.
- Thin rule between columns.

Keep paragraphs short. If it becomes dense, split the page.

**Minimum density (3:4)**: title + 3 short paragraphs OR title + 2 paragraphs + numbered footer list. Title alone is M04, not M03. If you have only a title + 1 paragraph, switch to M04 Pull Quote or fold a marginal column in for M11 Marginalia Essay.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-03">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Essay · 机制</p>
    <div class="col-2-7-5 grow">
      <div class="stack gap-4" style="justify-content:space-between">
        <p class="pullquote">包没丢，<br>是绕回去了。</p>
        <p class="meta">机制 · 三段解释</p>
      </div>
      <div class="stack" style="border-left:1px solid var(--line); padding-left:48px; justify-content:space-between">
        <p class="body">请求发出后先被 TUN 网卡截走，这是虚拟网卡的正常工作方式，不代表出错。</p>
        <p class="body">规则引擎按顺序判断，第一条命中的规则决定去向。顺序在前，直连就先走直连。</p>
        <p class="body">100.64.0.0/10 写进直连列表之后，判断结果指向虚拟网卡自己，包于是绕回起点。</p>
      </div>
    </div>
  </div>
  <div class="issue-strip">
    <span>Essay · Mechanism</span>
    <span>—</span>
    <span>03 / 09</span>
  </div>
</section>
```

The "thin rule between columns" is not a separate element — it is the right column's `border-left` with `padding-left:48px`, set inline. `.col-2-7-5` itself draws no rule.

**`justify-content:space-between` on both columns is load-bearing.** With three paragraphs top-aligned in a stretched column, the lower half of the page goes empty — and validator R5 does *not* catch it, because the column's full-height `border-left` is a bordered box that R5 counts as covered. This is a known R5 blind spot: **always eyeball an M03 render, do not trust a green pass.**

### M04 Pull Quote / Thesis

Best for a core sentence or conclusion.

Structure:

- Large quote across the page.
- Small source/context row.
- Optional tiny note or issue marker.

Use to create rhythm between dense pages.

**Minimum density (3:4)**: this is the **one recipe** where ≤60% canvas content is allowed (hero statement = intentional whitespace). BUT you must add (a) source/context row 18-20px mono ≤15% from bottom, (b) date-stamp or 章号 kicker at top, (c) hairline rule above the source row. Without these three "anchor points," whitespace reads as missing content. Don't use M04 if you can't supply at least one anchor.

HTML skeleton (3:4 — all three anchors wired in):

```html
<section class="poster xhs" id="xhs-04">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3" style="justify-content:space-between">
    <p class="kicker">Chapter 04 · 结论</p>
    <p class="pullquote">代理从来不是问题，<br>规则顺序才是。</p>
    <div class="stack gap-2">
      <hr class="rule">
      <p class="meta">来源 · 同一次测试的 Direct / TUN 两侧结果</p>
    </div>
  </div>
  <div class="issue-strip">
    <span>Quote · Findings</span>
    <span>—</span>
    <span>04 / 09</span>
  </div>
</section>
```

The three anchors map onto this markup exactly: (b) is the `.kicker` at the top, (c) is the `<hr class="rule">`, (a) is the `.meta` source row. `justify-content:space-between` distributes kicker / quote / source row across the canvas so the whitespace is balanced rather than pooled at the bottom.

`.pullquote` is 64px italic serif with `margin:0` in the seed. Do not wrap it in a `<p class="body">` or add your own font-size — the recipe's identity is that it is *not* a display headline.

### M05 Checklist / Buying Guide

Best for Rednote practical content.

Structure:

- Header title.
- 4-6 rows, each with a number, item, and consequence.
- Optional small photo crop or material swatch.

Avoid generic rounded cards; use rows, rules, columns, and issue labels.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-05">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Checklist · 五件事</p>
    <h2 class="h-xl">照着做，不用再查第二遍</h2>
    <div class="ledger">
      <div class="ledger-row">
        <div class="ledger-nb">01</div>
        <div>
          <h3 class="ledger-title">先量测，再动手</h3>
          <p class="ledger-note">记下改动前的连通性与延迟。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">02</div>
        <div>
          <h3 class="ledger-title">只改一处</h3>
          <p class="ledger-note">一次改两处，出问题无法归因。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">03</div>
        <div>
          <h3 class="ledger-title">直连规则排在前面</h3>
          <p class="ledger-note">顺序决定命中，不是数量。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">04</div>
        <div>
          <h3 class="ledger-title">验证三个数</h3>
          <p class="ledger-note">连通性、延迟、路由命中。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">05</div>
        <div>
          <h3 class="ledger-title">回写并提交</h3>
          <p class="ledger-note">临时修改撑不过一次重启。</p>
        </div>
      </div>
    </div>
  </div>
  <div class="issue-strip">
    <span>Checklist · 5 items</span>
    <span>—</span>
    <span>05 / 09</span>
  </div>
</section>
```

**M05 and M08 share the `.ledger` DOM.** The difference is content, not markup: M05 rows are imperative actions with a one-line consequence ("do this"), M08 rows are reference entries with a longer sub-line ("this is what it is"). If your rows need a paragraph of explanation, you are on M08.

M05 is a good "eye rest" page: pair it after a photo page (M02 / M16) or a dense data page. It carries the same ledger-margin dependency as M07/M08 — see the note there.

### M06 Evidence Wall

Best for multiple screenshots, references, or small images.

Structure:

- 2x2 or 3-column image grid.
- Each image has a short caption.
- One larger headline anchors the interpretation.

Use only when supplied images are readable at the final size.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-06">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack" style="gap:32px">
    <p class="kicker">Evidence · 四张图</p>
    <h2 class="h-xl">把过程留成证据</h2>
    <div class="col-2" style="row-gap:32px">
      <div class="stack gap-1">
        <figure class="frame-img r-1x1">
          <img src="assets/wall-1.png" alt="证据一" style="object-position:center top">
        </figure>
        <p class="img-cap">01 · 改动前</p>
      </div>
      <div class="stack gap-1">
        <figure class="frame-img r-1x1">
          <img src="assets/wall-2.png" alt="证据二" style="object-position:center top">
        </figure>
        <p class="img-cap">02 · 命中直连规则</p>
      </div>
      <div class="stack gap-1">
        <figure class="frame-img r-1x1">
          <img src="assets/wall-3.png" alt="证据三" style="object-position:center top">
        </figure>
        <p class="img-cap">03 · 顺序调整后</p>
      </div>
      <div class="stack gap-1">
        <figure class="frame-img r-1x1">
          <img src="assets/wall-4.png" alt="证据四" style="object-position:center top">
        </figure>
        <p class="img-cap">04 · 复查通过</p>
      </div>
    </div>
  </div>
  <div class="issue-strip">
    <span>Evidence · 4 shots</span>
    <span>—</span>
    <span>06 / 09</span>
  </div>
</section>
```

`.col-2` flows four children into a 2×2 grid on its own — do not nest two `.col-2` rows.

**The gaps are 32px on purpose.** At the seed's default `gap-3` (36px) plus `.col-2`'s 48px row gap, four square images overflow the footer by 26px; at `r-4x3` they under-fill by ~190px. `r-1x1` + 32px content gap + 32px row-gap is the measured combination that fits. 32px still clears R9's 28px minimum for an `.h-xl`, so do not go lower.

Each image is a screenshot in a 2×2 wall — this page fails the moment a caption cannot tell the four apart. If two shots look similar, drop to three and widen them.

### M07 Closing Note

Best for final page.

Structure:

- Big takeaway title (≤2 lines).
- **4-6 ledger items**, each 26-30px serif title + 16-18px serif sub-line ("consequence" / "reason" / "example"). Each item row should consume 100-140px vertical (border + padding + content).
- Closing block: pull-quote OR signature line OR price/CTA OR small marginalia (one of these is required).
- Small footer label.

Mood should feel like the end of a magazine feature, not a sales banner.

**Minimum density (3:4)**: title + **≥4 ledger items with sub-lines** + closing block. The previous "2-3 rules" version under-fills 3:4 by ~40%. If you genuinely only have 3 reasons, expand each to "title + 2-line consequence + example/quote" OR pair with a pull-quote closing block. **3 short ledger lines on a 3:4 canvas is a failure mode**; either expand or switch to M04 Pull Quote.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-08">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Closing · 四条结论</p>
    <h2 class="h-xl">四次排查之后，我留下这四条</h2>
    <div class="ledger">
      <div class="ledger-row">
        <div class="ledger-nb">01</div>
        <div>
          <h3 class="ledger-title">先量测，再动手</h3>
          <p class="ledger-note">跳过基线直接改配置，只会把问题从一层挪到另一层。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">02</div>
        <div>
          <h3 class="ledger-title">看规则顺序，不看规则数量</h3>
          <p class="ledger-note">顺序错一条，后面全部失效，加多少条都救不回来。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">03</div>
        <div>
          <h3 class="ledger-title">验证只看三个数</h3>
          <p class="ledger-note">连通性、延迟、路由命中，三个都过才算修好。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">04</div>
        <div>
          <h3 class="ledger-title">改完回写配置</h3>
          <p class="ledger-note">没进版本控制的临时修改，下次重启就消失。</p>
        </div>
      </div>
    </div>
    <div class="callout">
      代理从来不是问题，规则顺序才是。
      <span class="callout-src">— 本次排查结论</span>
    </div>
  </div>
  <div class="issue-strip">
    <span>Chapter · Conclusion</span>
    <span>—</span>
    <span>08 / 09</span>
  </div>
</section>
```

`.callout` is the closing block for this skeleton; the spec's other allowed closings (signature line / price / marginalia) replace it in place, they do not stack with it. `.callout-src` is a `<span>` inside `.callout`, not a sibling.

**Measured limits (1080×1440, seed template with its ledger margin patch):** 4 rows + the closing block renders clean. 5 rows pushes the callout 92px past `.issue-strip` (validator R2, no R1 overflow yet). 6 rows overflows by 161px (R1/R8). So **4 rows plus a closing block is the ceiling**. If you need 5-6 items, drop the closing block and fold that line into the last row's `.ledger-note`, or split the page.

**Requires the seed template's ledger margin patch** (`.ledger-title` / `.ledger-note` margin reset, listed as item 5 in the `template-editorial-card.html` header). Without it the UA-default `h3` margin adds ~128px of dead space per row and **even 4 rows overflows by 187px**. If you write your own ledger CSS instead of using the seed, reset those margins yourself.

### M08 Tall Ledger

Best for lists, roles, pros/cons, gear items, product capabilities, and agent responsibilities.

Structure:

- Header title.
- 4-6 full-width rows.
- Each row minimum 118-170px on a 1080x1440 canvas.
- Left index/marginalia column, right title + consequence.
- Optional vertical accent strip or bottom issue note.

Use when a normal table would become too short. Do not let the ledger occupy only the middle third of the page.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-03">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Ledger · 六个配置项</p>
    <h2 class="h-xl">真正需要改的只有这几项</h2>
    <div class="ledger">
      <div class="ledger-row">
        <div class="ledger-nb">01</div>
        <div>
          <h3 class="ledger-title">server_url</h3>
          <p class="ledger-note">改成控制面的 HTTPS 域名，证书必须覆盖这个主机名。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">02</div>
        <div>
          <h3 class="ledger-title">auth_key</h3>
          <p class="ledger-note">重新签发，旧 key 在换域名后一律失效。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">03</div>
        <div>
          <h3 class="ledger-title">tls_alpn</h3>
          <p class="ledger-note">保留 tls-alpn-01 校验，别为了省事关掉。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">04</div>
        <div>
          <h3 class="ledger-title">dns_mode</h3>
          <p class="ledger-note">切到 HTTPS 记录后，A 记录要同步删除。</p>
        </div>
      </div>
      <div class="ledger-row">
        <div class="ledger-nb">05</div>
        <div>
          <h3 class="ledger-title">route_order</h3>
          <p class="ledger-note">直连规则必须排在代理规则之前。</p>
        </div>
      </div>
    </div>
  </div>
  <div class="issue-strip">
    <span>Config · 5 items</span>
    <span>—</span>
    <span>03 / 09</span>
  </div>
</section>
```

**M08 and M07 share the same `.ledger` DOM.** The difference is intent, not markup: M07 argues toward a conclusion and closes with a block; M08 enumerates and stands alone. If the page ends in a pull quote, it is M07. If the rows are reference material with no narrative, it is M08.

**Measured limits (1080×1440, seed template with its ledger margin patch):** 5 and 6 rows both render clean; 7 rows overflows by 162px (validator R1/R8). So **6 rows is the ceiling on 3:4**. Do not fix an overflow by reducing `.ledger-title` below the components.md floor — split into two pages or switch to M05 Checklist, which allows a shorter row.

Keep the ledger filling the canvas: a 4-row ledger with a large empty lower third is the failure mode this recipe exists to avoid (R5/R8). Because M08 has no closing block, prefer 5-6 rows rather than 4.

Same dependency as M07: this skeleton needs the seed template's ledger margin patch. Rolling your own ledger CSS without resetting the heading margins costs ~128px per row.

### M09 Atmospheric Thesis

Best for sparse but important points.

Structure:

- WebGL/ink-flow background visible across the page.
- One very large thesis or quote.
- 1-2 supporting notes.
- Small issue metadata and a bottom rule.

Use this instead of forcing a small table onto a page with little content.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-07">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3" style="justify-content:space-between">
    <p class="kicker">Thesis · 这一次的结论</p>
    <h1 class="h-display">顺序即策略</h1>
    <div class="stack gap-2">
      <p class="lead">同一条规则，放在第 3 行和第 7 行，结果完全相反。</p>
      <hr class="rule-accent">
      <p class="meta">机制 · 规则引擎只取第一个命中项</p>
    </div>
  </div>
  <div class="issue-strip">
    <span>Thesis · Ordering</span>
    <span>—</span>
    <span>07 / 09</span>
  </div>
</section>
```

**Use `space-between`, never `center`.** Centering this block leaves a void above the kicker and another below the rule; both exceed the 216px under-filled band limit and the page reads as "PowerPoint with a missing element". `space-between` gives the same sparse, deliberate feel with all four bands populated.

This is the one Editorial recipe where the atmosphere layer is the *content*, not the backdrop. That makes two things mandatory: `assets/magazine-bg-webgl.js` must be present in the task folder (see M01), and the flow should be pushed stronger than on a dense content page — a task-scoped opacity/blend block on `.mag-bg` is the intended way to do that, not a new background image.

Difference from M04: M04 is a quote (`.pullquote`, 64px italic, deliberately not a headline) and needs three anchors; M09 is a statement (`.h-display`, 124px) with a supporting lead and rule. If you are reaching for M09 but have nothing to support the statement, use M04 and skip the lead.

### M10 Evidence Feature

Best for supplied screenshots/photos.

Structure:

- Large screenshot/photo occupying 45%-65% of the vertical canvas.
- Headline and lead above or beside it.
- Bottom caption band with 2-3 takeaways.

The image is not an afterthought. If the screenshot is important, it must be big enough to inspect.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-08">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Evidence · 控制台</p>
    <h2 class="h-xl">三行状态，一眼看完</h2>
    <figure class="frame-img r-4x3">
      <img src="assets/evidence.png" alt="控制台首屏" style="object-position:center top">
    </figure>
    <p class="img-cap">图 · 排障当天的控制台首屏</p>
    <div class="col-3">
      <p class="meta">连接 · 直连</p>
      <p class="meta">延迟 · 79ms</p>
      <p class="meta">丢包 · 0</p>
    </div>
  </div>
  <div class="issue-strip">
    <span>Evidence · Console</span>
    <span>—</span>
    <span>08 / 09</span>
  </div>
</section>
```

`r-4x3` at this width lands at ~46% of canvas height, inside the spec's 45-65% band. `r-1x1` (~61%) also fits the band but pushes the page over the footer once the caption band is added.

The bottom `.col-3` band is what makes this M10 rather than M02: the takeaways turn a picture into evidence. Use the user's real numbers only — a fabricated latency is worse than no caption band.

For UI, code, and dashboards set `object-position:center top` so the important rows stay in frame; `center` is for objects and photos. If the screenshot is unreadable at this size, that is the signal to switch to M06 and give it a full-width column instead of forcing M10.

### M11 Marginalia Essay

Best for nuanced explanation with moderate text.

Structure:

- Wide editorial title.
- Main column with 2-3 paragraphs.
- Narrow marginal column containing keywords, quote fragments, or small evidence crops.
- Hairline vertical rule between columns.

Use this when M03 feels too empty but a ledger feels too mechanical.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-09">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Essay · 排障顺序</p>
    <h2 class="h-xl">先问代理，还是先查路由</h2>
    <div class="marginalia grow">
      <div class="stack gap-3">
        <p class="body">排障顺序决定了你要花四个回合还是十分钟。先看规则命中，再谈网络层。</p>
        <p class="body">把控制台的状态页放在第一步，能直接排除掉一半的猜测，剩下的才值得动手。</p>
        <p class="body">每次只改一处，然后立刻复测，改动才有归因价值，否则你只是在碰运气。</p>
        <div class="grow"></div>
        <hr class="rule">
        <p class="meta">结论 · 先看规则命中，再谈网络层</p>
      </div>
      <div class="mg-col">
        <p>TUN · 100.64.0.0/10</p>
        <p>DIRECT 优先于 PROXY</p>
        <p>79ms / timeout</p>
        <p>四回合 · 一次归因</p>
      </div>
    </div>
  </div>
  <div class="issue-strip">
    <span>Essay · Marginalia</span>
    <span>—</span>
    <span>09 / 09</span>
  </div>
</section>
```

`.marginalia` is a `1fr 220px` grid; `.mg-col` supplies the hairline (`border-left`) and `padding-left`, and styles its direct `<p>` children as mono 20px. Both columns must be direct children of `.marginalia`.

The inner `<div class="grow"></div>` plus the closing `.rule` + `.meta` is what fills the main column to the bottom edge. Without them the three paragraphs sit at the top and the lower half of the page goes empty — and R5 will not flag it, because `.mg-col`'s full-height border counts as covered. **Eyeball every M11 render.**

Do not put a fourth paragraph in the main column and do not let the margin column wrap to two lines per entry; `.mg-col` is 220px wide by design. Keep margin entries to short fragments — that narrowness is the whole point of the recipe.

### M12 Section Divider

Best for a mid-carousel breath between dense pages. Drop one between act 1 and act 2 of a 7-9 page Rednote set so the reader gets a beat of silence.

Structure:

- WebGL ink-flow background visible across most of the canvas (Editorial template mounts this by default).
- One mono `kicker` like `Act II` or `Part 2 of 3`.
- One large serif `h-display` naming the section (3-6 Chinese characters).
- One short subtitle in serif italic.
- Optional bottom `issue-strip` with the section's promise.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-04">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-4" style="justify-content:center; align-items:flex-start">
    <p class="kicker">Act II · Part 2</p>
    <h1 class="h-display">实测结果</h1>
    <p class="h-sub">What actually happened.</p>
  </div>
  <div class="issue-strip">
    <span>Section · Findings</span>
    <span>—</span>
    <span>3 pages</span>
  </div>
</section>
```

Adapt for 1:1: drop the subtitle, center the `h-display`, keep the ink-flow background strong. Adapt for 21:9: shift title to the left half, let the WebGL atmosphere occupy the right half.

### M13 Hero Question

Best for the last page of a Rednote carousel or for a sharp pivot mid-set. The question is the entire page.

Structure:

- WebGL ink-flow background.
- Quiet kicker like `The Question` or `留给你的`.
- Big serif question, broken into 2-3 lines on `<br>` at semantic breakpoints (not random wraps).
- One short single-sentence prompt in `lead`, often a hint or invitation to comment.
- Minimal metadata at bottom.

**Minimum density (3:4)**: like M04, this is intentionally airy. Required anchors: top kicker (0-12% y) + bottom prompt + bottom metadata strip. The ink-flow WebGL background **must be visible** (don't let `.content` cover it). If the WebGL background is missing or the kicker is empty, the page reads as under-filled rather than meditative.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-09">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-4" style="justify-content:center">
    <p class="kicker">The Question · 留给你的</p>
    <h1 class="h-display">
      下一次<br>
      你会先<br>
      问 AI 还是问朋友?
    </h1>
    <p class="lead">在评论区告诉我你的答案。</p>
  </div>
  <div class="issue-strip">
    <span>End · Page 9</span>
    <span>—</span>
    <span>欢迎留言</span>
  </div>
</section>
```

Title length cap: 3 lines, each <= 6 Chinese characters at default size. If the question is longer, shorten it; do not shrink the `h-display`.

### M14 Vertical Pipeline

Best for explaining a 3-5 step workflow, decision tree, or recipe. PPT's pipeline is horizontal; for portrait social cards rotate it vertical.

Structure:

- `kicker` + `h-xl` page title.
- 3-5 `.pipeline-v .step` rows. Each row has step number (mono), step title (serif), one-line description (sans).
- Hairline between steps, gap 28-36px.
- Optional small evidence image to the right of the title, or a single bottom caption.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-06">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Workflow · 4 步</p>
    <h2 class="h-xl">我的写作流水线</h2>
    <div class="pipeline-v">
      <div class="step">
        <div class="step-nb">01</div>
        <div>
          <h3 class="step-title">Draft</h3>
          <p class="step-desc">用语音备忘录把想法说出来</p>
        </div>
      </div>
      <div class="step">
        <div class="step-nb">02</div>
        <div>
          <h3 class="step-title">Reorder</h3>
          <p class="step-desc">用 AI 把段落顺序重新排</p>
        </div>
      </div>
      <div class="step">
        <div class="step-nb">03</div>
        <div>
          <h3 class="step-title">Polish</h3>
          <p class="step-desc">人工逐句去 AI 味</p>
        </div>
      </div>
      <div class="step">
        <div class="step-nb">04</div>
        <div>
          <h3 class="step-title">Ship</h3>
          <p class="step-desc">同时发小红书、推特、公众号</p>
        </div>
      </div>
    </div>
  </div>
</section>
```

Hard limit: 3-5 steps. If you have 6+, split into two pages or switch to M05 Checklist.

### M15 Before / After

Best for "old way vs new way", "before AI vs after AI", "rookie vs pro" comparisons. PPT's Before/After is left-right; on 3:4 portrait, stack top/bottom to avoid pinched columns.

Structure:

- `kicker` + `h-xl` page title.
- `.beforeafter` container with two `.ba-block` rows: one with class `before` (dimmed at .68 opacity), one without.
- Each block has its own kicker, mid title, and 3-4 short bullets.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-07">
  <canvas class="mag-bg" data-bg="ink-flow"></canvas>
  <div class="grain"></div>
  <div class="content stack gap-3">
    <p class="kicker">Before · After</p>
    <h2 class="h-xl">写作流的演变</h2>
    <div class="beforeafter">
      <div class="ba-block before">
        <p class="kicker">Before · 旧</p>
        <h3 class="h-md">在 Notion 里反复修</h3>
        <ul class="body" style="margin:0; padding-left:1.2em">
          <li>开头改了 20 遍</li>
          <li>每个段落都想精雕</li>
          <li>一周才发一条</li>
        </ul>
      </div>
      <div class="ba-block">
        <p class="kicker">After · 新</p>
        <h3 class="h-md">先发版,再迭代</h3>
        <ul class="body" style="margin:0; padding-left:1.2em">
          <li>20 分钟出初稿</li>
          <li>评论区做精修</li>
          <li>一天发三条</li>
        </ul>
      </div>
    </div>
  </div>
</section>
```

Adapt for 21:9: switch to left/right split (use `.col-2` grid). Adapt for 1:1: usually too cramped; consider M04 Pull Quote instead.

### M16 Image-Led Cover (Full-Bleed Hero)

Best for **lifestyle, image-heavy** content where the user has 1+ excellent photos: 旅行目的地 / 户外场景 / 自家成菜 / 家居一角 / 情感场景照. Photo fills the canvas; title rides on top with restraint. This is what M01 is **not** — M01 splits the canvas into photo + text blocks. M16 lets the photo own the canvas.

**Reference look**: Kinfolk / Cereal / Apartamento / Monocle covers. Quiet photo, serif title, paper-cream text, generous negative space, asymmetric placement. **NOT** game key-art with heavy black gradient. **NOT** travel poster with chunky bold caption.

**Required**: `references/image-overlay.md` Rule 1 Steps 1-4 must all pass. The first gate (photo selection) is the hardest — most photos fail it.

#### Photo qualification (gate before choosing M16)

Run both tests on the user's photo. Both must pass, or **switch to M01** (split-layout) instead of forcing M16.

1. **Quiet-zone test**: photo has a band of ≥30% canvas (full width × ≥30% height, or full height × ≥30% width) that is low-detail / out-of-focus / uniform. This is where the title will land.
2. **Light test**: photo carries atmospheric/restrained light — overcast, dawn fog, golden hour, forest understory, dusk silhouette, film softness. Reject high-saturation noon shots, on-camera flash, generic tourist snaps.

If only one passes: try M01. If neither: ask user for a different photo, or use M01 with a smaller `.frame-img` slot. **Do not "fix it with a mask."**

**Page sequence convention** (this is the "image-led" pattern):

```
P1 (M16)               ← full-bleed photo + short title — the hook
P2 (S11 / M05 / M10)   ← ledger / checklist / mini-data — text-heavy rest stop
P3 (M02 or M16-small)  ← second photo as field-note evidence, not full-bleed
P4-N (M02 / M11)       ← more field notes or marginalia essays
P_last (M07 / M04)     ← closing note or pull quote
```

Never run two M16 in a row. After a full-bleed photo the eye needs text. P2 must be data/text.

**Four title-placement modes** — pick by subject map (from image-overlay.md Rule 2):

| Mode | Subject location | Title position | Tint (only if Step 4 contrast fails) |
| ---- | ---------------- | -------------- | ------------------------------------ |
| **A · 顶压底沉** (top kicker + bottom title) | Subject in middle third (face, peak, hero object), top + bottom open | Top: kicker 0-12% y. Bottom: 1-2 line title 72-92% y. | Try no tint. If needed: bottom-only radial, image-toned, peak α ≤ 0.30 over title block. |
| **B · 侧栏立柱** (vertical column) | Subject occupies a **clean** vertical column with ≥40% width safe opposite side. Skip B if subject edge is irregular (e.g. tree canopy spilling sideways) — use A or D instead. | Opposite column (~36-40% of width), stacked: kicker → title → subtitle | Try no tint. If needed: one-sided horizontal falloff `radial-gradient(45% 60% at 18% 50%, …)`, image-toned. |
| **C · 角落徽章** (corner badge) | Subject fills most of the frame; one corner is genuinely empty | Small block in the empty corner (≤35% w × ≤25% h): kicker / short title / meta | Light vignette only in that corner — never darken the whole image |
| **D · 下沉条带** (bottom ribbon) | Wide landscape / atmospheric scene / no single subject; abundant negative space at bottom | Bottom band 78-92% y: title + meta strip; align flush-left or flush-right (not centered) | Try no tint. If needed: bottom-only radial, image-toned, peak α 0.20-0.30 |

**Typography spec on 1080×1440 (Editorial × E-ink) — restrained, NOT chunky**:

| Element | Mode A | Mode B | Mode C | Mode D |
| ------- | ------ | ------ | ------ | ------ |
| Kicker  | mono 20-22px, uppercase, tracking 0.18-0.22em | mono 20-22px | mono 18-20px | mono 22-24px |
| Title font | Noto Serif SC **400-500** (NOT 700-900) | same | same | same |
| Title size | **88-108px**. Default 96px for 2-line zh title. Drop to 88px if 2 lines × ≥7 chars. | **84-100px**, wrap on phrase boundary | 56-64px (NOT display) | **96-112px**, single line preferred |
| Title tracking (zh) | 0.10-0.15em | 0.10-0.15em | 0.05-0.10em | 0.12-0.18em |
| Title line-height | 1.10-1.18 | 1.10-1.18 | 1.20 | 1.05-1.10 |
| Subtitle | optional `h-sub` 28-32px italic Playfair, tracking 0 | same | omit | same |
| Issue strip | mono 18-20px bottom, uppercase, tracking 0.20-0.25em, hairline above | omit | omit | mono 20-22px, tracking 0.20-0.25em |
| Text color | `#f5f1e8` (paper-cream, NOT pure white #fff) | same | same | same |
| Hairline color | `rgba(245, 241, 232, 0.35)` (paper at 35% alpha) | same | same | same |

**Forbidden in M16 type**:

- Title weight 700+ inline (e.g. `font-weight: 800` to "make it pop"). M16 wants **regular-medium serif**, not bold.
- Pure white `#fff` text — too clinical, fights the photo's warmth. Always paper-cream.
- Title size > 120px on 1080-wide canvas. We are not making travel posters.
- Latin sans-serif as primary title (Helvetica / Inter for the headline) — M16 belongs to serif. Sans only as kicker / meta / mono caption.
- Centered title on D mode. Always flush-left or flush-right for asymmetric Kinfolk feel.

**Title-length budget** (cut copy if you bust this — never shrink type below 84px):

| Mode | Max Chinese chars | Max lines |
| ---- | ----------------- | --------- |
| A    | 12 (2 × 6) or 14 (2 × 7 at 88px)  | 2 |
| B    | 14 (2 × 7)                         | 2 |
| C    | 8                                  | 1 |
| D    | 10                                 | 1 |

**Square (1:1, 1080×1080) and Wide (21:9, 2100×900) adaptations**:

- **1:1**: Use Mode A or Mode D only. Mode B's column squeeze, Mode C's corner are too tight on square. Shorten title to 6-10 chars.
- **21:9**: Use Mode B (left text column + right photo bleed) or Mode D (full-width bottom band). Mode A works but the bottom title competes with the WeChat caption strip — prefer D.

**HTML skeleton (Mode A — 顶压底沉 on 3:4, no mask)**:

```html
<section class="poster xhs" id="cover-image-led">
  <!-- subject map (cover hero — example: Yading lake at dawn):
       primary focus: snow peak at 50% x 32% y (middle band)
       safe text zone: top band (0-12% y) low-detail sky,
                        bottom band (72-95% y) calm water reflection
       quiet-zone test: PASS — bottom 30% is uniform water
       light test: PASS — dawn overcast, soft saturation
  -->
  <div class="hero-bleed" style="background-image: url('assets/hero-yading.jpg');
                                  background-size: cover;
                                  background-position: center center;
                                  position: absolute; inset: 0;"></div>
  <!-- NO MASK — quiet zone already provides contrast.
       Add only if Step 4 contrast check fails. -->
  <div class="content" style="position: relative; height: 100%;
        color: #f5f1e8; padding: 72px 80px;
        display: flex; flex-direction: column;">
    <p class="kicker" style="color: #f5f1e8; opacity: .85;
        font-family: var(--mono); font-size: 22px;
        letter-spacing: 0.22em; text-transform: uppercase; margin: 0">
      Vol. 04 — 2026 — 户外
    </p>
    <div style="flex: 1"></div>
    <h1 style="font-family: 'Noto Serif SC', serif;
        font-weight: 500; font-size: 96px; line-height: 1.12;
        letter-spacing: 0.12em; color: #f5f1e8;
        margin: 0 0 18px 0">
      在稻城<br/>看见冬天
    </h1>
    <div style="border-top: 1px solid rgba(245,241,232,.35);
         padding-top: 14px; font-family: var(--mono); font-size: 19px;
         letter-spacing: 0.22em; text-transform: uppercase;
         color: #f5f1e8; opacity: .85">
      DAY 1-4 · 4,200 M · −18°C
    </div>
  </div>
</section>
```

**HTML skeleton (Mode D — 下沉条带 on 21:9, for WeChat main cover)**:

```html
<section class="poster wide" id="wechat-21x9-led">
  <!-- subject map: wide atmospheric landscape, mist over lake at dawn
       quiet-zone test: PASS — entire bottom 35% is calm water + mist -->
  <div class="hero-bleed" style="background-image: url('assets/hero-yading-wide.jpg');
        background-size: cover; background-position: center 40%;
        position: absolute; inset: 0;"></div>
  <div class="content" style="position: relative; height: 100%;
        color: #f5f1e8; padding: 0 96px 88px;
        display: flex; flex-direction: column; justify-content: flex-end;
        align-items: flex-start;">
    <h1 style="font-family: 'Noto Serif SC', serif; font-weight: 500;
         font-size: 108px; line-height: 1.05; letter-spacing: 0.14em;
         margin: 0; color: #f5f1e8">
      在稻城看见冬天
    </h1>
    <div style="margin-top: 18px; border-top: 1px solid rgba(245,241,232,.35);
         padding-top: 14px; font-family: var(--mono); font-size: 20px;
         letter-spacing: 0.24em; text-transform: uppercase">
      VOL. 04 — DAY 1-4 — 4,200 M — 35MM
    </div>
  </div>
</section>
```

**Pitfalls**:

- Choosing M16 when the photo fails quiet-zone or light test. The cover will look bad no matter what mask you layer on. Fall back to M01.
- Putting Mode A's bottom title across someone's torso when the subject is in the lower third — re-run subject map and switch to Mode C, or change `object-position: center 70%`.
- Mode C with a heavy backdrop plate behind the corner title — defeats the point of having a great photo. Use vignette only.
- Using Mode B on a photo where the subject's silhouette is **irregular** (trees with sideways branches, scattered objects) — the "side column" won't be wide enough to be a true safe zone. Switch to Mode A or D.
- Adding a full-canvas vertical falloff "just in case." Mask-by-default kills the editorial look. The fix is photo selection, not opacity.
- Setting title weight 700+ or pure-white text — both are anti-pattern in Editorial M16.
- Running M16 on every cover. M16 is for **image-heavy lifestyle**. Product/tech/AI release covers still belong to S01 / S03 / M01.

## Swiss International Recipes

These structures (accent cover / matrix / KPI tower / h-bar / numbered statement) work for **any topic** that wants an engineered, quantified pace — software releases yes, but also travel budgets, fitness logs, recipe portions, reading stats, anything where you'd rather count it than narrate it. The mode is a visual stance, not a content filter.

Accent is a deck-level decision: set it once on `<html data-accent="…">`. The `data-accent` attributes inside the skeletons below only restate the accent those examples assume — do not vary accent page by page in a real deck (the seed's own rule is "one deck — one accent").

Skeletons are measured, not guessed. Every 3:4 skeleton below was rendered at 1080×1440 and checked with `validate-social-deck.mjs`; the constraint notes under each one are what the measurement actually showed. Where `.grow` appears it is load-bearing rather than decorative: **a grid parent stretches its children, a flex column does not**, so an under-filled Swiss page is usually a missing `.grow` on the grid or on each row, not missing copy.

### S01 Accent Cover

Best for Rednote cover.

Structure:

- Full accent or off-white background.
- Big light-weight title.
- Simple abstract system block, comparison, or two-node diagram.
- Bottom metadata strip.

Use one clear concept. No decorative blobs.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-01" data-accent="ikb">
  <div class="content stack gap-9">
    <div class="chrome-min">
      <span>Vol. 07 · Diagnosis</span>
      <span>2026.09</span>
    </div>
    <div class="stack gap-7">
      <p class="t-cat">封面 · Cover</p>
      <h1 class="h-statement">顺序决定结果</h1>
    </div>
    <div class="grid-2-9 grow">
      <div class="card-outlined stack gap-6">
        <p class="t-meta">Direct</p>
        <p class="lead">79 ms 通</p>
      </div>
      <div class="card-ink stack gap-6">
        <p class="t-meta">TUN</p>
        <p class="lead">超时</p>
      </div>
    </div>
    <hr class="hr-accent">
    <p class="lead">四次排查，一次归因：顺序决定结果。</p>
    <div class="row gap-6">
      <p class="t-meta">Issue 07</p>
      <p class="t-meta">/ 排障手记</p>
    </div>
  </div>
</section>
```

The two coloured modules are the "simple abstract system block / two-node diagram" the spec asks for — a comparison, a two-state diagram, or a small stat pair all fit the same markup. Use real values only.

`.grid-2-9 grow` on the module row is load-bearing. A bare `<div class="grow"></div>` spacer instead leaves the band between the title and the footer completely empty (measured: 29% density, one band at 0%); letting the modules stretch fills it (63%, every band populated).

Title is `.h-statement`, which the seed already caps for the `xhs` board (R6 allows 2 lines / 8 characters). Keep the title at or under that — an accent cover with a wrapped 3-line title stops reading as Swiss.

Hard rule from the seed still applies: one accent for the whole deck. The `data-accent` on this `<section>` restates the deck's accent for the example; do not give one page a different colour.

### S02 Two Signals / Comparison

Best for explaining two sources, two options, or two product directions.

Structure:

- Page title.
- Two large rectangular modules.
- One module may be ink-filled; the other paper/outlined.
- Short notes below each.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-04" data-accent="ikb">
  <div class="content stack gap-7">
    <p class="t-cat">Compare · 直连 vs 代理</p>
    <h2 class="h-xl">同一台机器，两种命运</h2>
    <div class="grid-2-9 grow">
      <div class="card-outlined stack gap-6">
        <p class="t-cat">A · Direct</p>
        <p class="h-md">79ms 通</p>
        <p class="body">走物理网卡直连，不经过规则引擎，也没有额外一跳。</p>
        <div class="grow"></div>
        <p class="t-meta">命中 · 物理网卡</p>
      </div>
      <div class="card-ink stack gap-6">
        <p class="t-cat">B · TUN</p>
        <p class="h-md">超时</p>
        <p class="body">100.64.0.1 被当成远程上下文，包又绕回了代理回路。</p>
        <div class="grow"></div>
        <p class="t-meta">命中 · 代理回路</p>
      </div>
    </div>
    <hr class="hr-hairline">
    <p class="t-meta">两侧数据来自同一台机器的同一次测试</p>
  </div>
</section>
```

`.card-ink` already re-colors `.t-cat` / `.t-meta` / `.lead` / `.body` / `.h-md` to paper — use those classes for the text inside it; a bare element or `.h-xl` inside `.card-ink` stays ink-colored and disappears.

`.grow` on the grid makes the two modules stretch to the full canvas height, and the inner `<div class="grow"></div>` pushes each module's mono footer to its bottom edge. **Both are load-bearing**: without the outer `.grow` this page measures 37% canvas density and fails the 4-band check.

Hard limits: exactly two modules. A third signal is S06 or S12, not a third column. Each side gets ≤4 short lines — if the two sides differ in more than four attributes, the reader wants a matrix (S12) or a chart (S10), not two prose blocks. `.grid-2-9` stays two columns on 3:4 by design; do not stack it.

### S03 Data Layer / File Card

Best for Markdown, memory, source-of-truth, database, or state.

Structure:

- Large file-type or object block.
- List of 3-4 properties.
- Strong mono labels.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-03" data-accent="ikb">
  <div class="content stack gap-7">
    <p class="t-cat">Data Layer · 配置文件</p>
    <h2 class="h-xl">这份文件决定一切</h2>
    <div class="card-ink stack gap-6 grow">
      <p class="t-meta">config.yaml</p>
      <p class="h-md">三个字段，一个顺序</p>
      <p class="body">改这里就够了，不用碰代码，也不用重启服务。</p>
    </div>
    <div class="stack gap-5">
      <div class="grid-12">
        <p class="t-meta span-4">server_url</p>
        <p class="body mono span-8">https://control.example.com</p>
      </div>
      <hr class="hr-hairline">
      <div class="grid-12">
        <p class="t-meta span-4">tls_alpn</p>
        <p class="body mono span-8">tls-alpn-01 · http-01</p>
      </div>
      <hr class="hr-hairline">
      <div class="grid-12">
        <p class="t-meta span-4">route_order</p>
        <p class="body mono span-8">DIRECT, PROXY</p>
      </div>
    </div>
  </div>
</section>
```

The `.card-ink` block is the "large file-type or object block"; the `.grid-12` rows below are the property list, each an `.hr-hairline` apart. `.span-4` + `.span-8` fills the 12-column grid exactly.

`.grow` on the ink block absorbs the leftover canvas height. Because it is a flex column, its own children do **not** stretch — the block simply gets taller with the text at the top, which reads as a deliberate dark panel rather than an empty one.

Inside `.card-ink`, only `.t-cat` / `.t-meta` / `.lead` / `.body` / `.h-md` are re-coloured to paper; a plain element or an `.h-xl` stays ink-coloured and disappears. Keep the inner stack at `gap-6` (24px) or larger — `gap-4` (12px) trips R9 on the `.h-md` → `.body` pair.

Use `.mono` for values that must be read character by character (hostnames, keys, paths). It is a font-family utility, so it composes with `.body` — the seed has no `.mono-body` class.

### S04 Interface / Browser Mock

Best for HTML, UI, presentation, interaction, or output layer.

Structure:

- Browser-window style frame with straight edges.
- Inside: one hero content block plus 2-3 functional modules.
- Bottom action or interaction strip.

HTML skeleton (3:4 — real screenshot inside CSS chrome):

```html
<section class="poster xhs" id="xhs-05" data-accent="ikb">
  <div class="content stack gap-7">
    <p class="t-cat">Interface · 控制台</p>
    <h2 class="h-xl">登录之后，先看这三行</h2>
    <div class="device-browser">
      <div class="frame-shot inset-none r-4x3">
        <img src="assets/console-status.png" alt="控制台状态页" style="object-position:center top">
      </div>
    </div>
    <div class="grid-3 grow">
      <div class="card-fill stack gap-3">
        <p class="t-meta">Status</p>
        <p class="lead">connected</p>
      </div>
      <div class="card-fill stack gap-3">
        <p class="t-meta">Endpoint</p>
        <p class="lead">HTTPS 已生效</p>
      </div>
      <div class="card-fill stack gap-3">
        <p class="t-meta">Latency</p>
        <p class="lead">79 ms</p>
      </div>
    </div>
    <div class="chrome-min">
      <span>Console · Status</span>
      <span>原文未重绘</span>
    </div>
  </div>
</section>
```

`.device-browser` draws its chrome bar with `::before` (CSS-only, 32px) and strips the radius from a direct `.frame-shot` child — keep that nesting. `.frame-shot > img` is `object-fit: contain` in Swiss, the right default for UI, code, and dashboards; add `.fit-cover` only for photos. Always set `object-position` inline (see `image-overlay.md`).

Sizing is the part that goes wrong. `r-16x10` leaves the screenshot small and the page under half-full; **`r-4x3` plus the `.grid-3 grow` module row is the measured combination that fills 3:4**. The module row stays at 3 columns because the labels are short — do not let it wrap to 2 columns, which re-opens the bottom gap.

For an HTML-built mock instead of a screenshot, keep the same `.device-browser` shell and swap the `.frame-shot` contents for the hero block plus modules, keeping the `.grid-3 grow` row below.

Before placing: redact tokens, keys, IPs, internal hostnames, and customer names. A visible secret is worse than a missing screenshot.

### S05 Trap / Warning Rows

Best for problems, anti-patterns, and "do not do this" pages.

Structure:

- Big warning title.
- Three horizontal rows.
- Left mono label, right consequence.

Safety Orange works well here if the whole package uses it.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-06" data-accent="safety-orange">
  <div class="content stack gap-7">
    <p class="t-cat">Traps · 三个坑</p>
    <h2 class="h-xl">这三件事别做</h2>
    <div class="stack gap-5 grow">
      <div class="card-outlined grid-12">
        <p class="t-cat span-3">Trap 01</p>
        <div class="span-9 stack gap-4">
          <p class="body">把 100.64.0.0/10 写进直连列表，等于给代理回路开口子。这一段地址本来就是虚拟网卡的地址段，把它当作"局域网直连"，规则引擎和虚拟网卡会互相指向对方。</p>
          <p class="t-meta">现象 · 直连也不通，日志里看不到命中记录</p>
          <p class="t-meta">正确做法 · 只把真实内网段写进直连，100.64.0.0/10 交给代理判断</p>
        </div>
      </div>
      <div class="card-outlined grid-12">
        <p class="t-cat span-3">Trap 02</p>
        <div class="span-9 stack gap-4">
          <p class="body">为了省事关掉 tls-alpn-01，证书续期会在半夜静默失败。校验方式少一种，签发那天才发现域名没通过，而日志只在凌晨写一行。</p>
          <p class="t-meta">现象 · 白天正常，凌晨开始报证书过期</p>
          <p class="t-meta">正确做法 · 保留全部校验方式，续期失败要能推送到手机</p>
        </div>
      </div>
      <div class="card-outlined grid-12">
        <p class="t-cat span-3">Trap 03</p>
        <div class="span-9 stack gap-4">
          <p class="body">改完不回写配置，重启一次全部回到原点。排障时的临时修改看着有效，但它活在内存里，不在配置文件里，也不在版本控制里。</p>
          <p class="t-meta">现象 · 修好了，第二天又坏</p>
          <p class="t-meta">正确做法 · 每次验证通过后立刻回写并提交，再重启确认</p>
        </div>
      </div>
    </div>
  </div>
</section>
```

`.card-outlined` and `.grid-12` combine on the same element (padding plus a 12-column grid); the label column is `.span-3` and the consequence block is `.span-9`. Do not let the consequence span all 12 columns — then the left column stops reading as a label.

**Each row needs three lines, not one.** A single-sentence row leaves the outline box ~60% empty; the measured version above carries trap + 现象 + 正确做法 and fills each row. With one line per row this page measures 32% density.

Two validator notes for this recipe. `R5` under-reads it: `.card-outlined` is transparent, and R5's pixel coverage only counts filled backgrounds and text boxes, so a correct S05 page reports roughly 57% and stays under the 74.5% advisory threshold. Judge this page visually. `R9` requires ≥28px between the `.h-xl` title and the first card, which is why `.content` uses `gap-7` (32px) here rather than `gap-6` (24px).

Hard limits: three rows. Four or more overflow the 3:4 content box — split across two pages, or switch to S07 Takeaway Ledger, which is built for a longer closing list. If the package already uses Safety Orange, this page keeps it; do not swap accent for a single warning page.

### S06 Pipeline / Architecture

Best for workflows and layered systems.

Structure:

- Three rows or columns: source, render, share.
- Each has number, label, action, and consequence.
- Use hairline boxes and neutral grey fills.

HTML skeleton (3:4 — rows, not columns):

```html
<section class="poster xhs" id="xhs-07" data-accent="ikb">
  <div class="content stack gap-7">
    <p class="t-cat">Architecture · 请求路径</p>
    <h2 class="h-xl">包到底走了哪条路</h2>
    <div class="stack gap-5 grow">
      <div class="card-fill grid-12 grow">
        <p class="t-meta span-2">01</p>
        <p class="h-md span-4">客户端</p>
        <div class="span-6 stack gap-3">
          <p class="body">应用发起请求，目标是控制面域名。</p>
          <p class="t-meta">入口 · 用户可见</p>
        </div>
      </div>
      <div class="card-fill grid-12 grow">
        <p class="t-meta span-2">02</p>
        <p class="h-md span-4">TUN 网卡</p>
        <div class="span-6 stack gap-3">
          <p class="body">包被抓进虚拟网卡，交给规则引擎判断。</p>
          <p class="t-meta">拦截 · 不区分应用</p>
        </div>
      </div>
      <div class="card-fill grid-12 grow">
        <p class="t-meta span-2">03</p>
        <p class="h-md span-4">规则引擎</p>
        <div class="span-6 stack gap-3">
          <p class="body">直连规则排在前面就按原路出去，否则绕回代理。</p>
          <p class="t-meta">决策 · 顺序决定结果</p>
        </div>
      </div>
    </div>
    <hr class="hr-hairline">
    <p class="t-meta">实测：第 03 步的顺序决定 79ms 还是超时</p>
  </div>
</section>
```

On 3:4 use **rows**: three columns at this width leave roughly 270px each and pinch the consequence text. Reserve `.grid-3` columns for 21:9, where the horizontal space exists.

`.grow` is required on **each** row, not just the container — `.card-fill grid-12 grow`. A flex column does not stretch its children on its own; only the grid parent does. With `.grow` on the container alone this page measures 53% density; with it on each row, 3:4 renders clean.

Hard limits: 3-5 stages. Six or more means the flow is really a ledger — use S07, or split into two pages. The stage label is `.h-md`; if a Chinese label wraps past two lines, shorten the label rather than shrinking the type.

### S07 Takeaway Ledger

Best for final page.

Structure:

- Big thesis title.
- Three ledger rows with number, phrase, and compressed explanation.
- Dark or ink background can create closure.

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-08" data-accent="ikb">
  <div class="content stack gap-7">
    <p class="t-cat">Takeaway · 三条</p>
    <h2 class="h-xl">如果只记三件事</h2>
    <div class="card-ink stack gap-6 grow">
      <p class="h-md">规则顺序错了，加多少条都救不回来</p>
      <p class="body">三件事里最容易被跳过的是第一件：先量测。</p>
    </div>
    <div class="stacked-ledger">
      <div class="ledger-row">
        <p class="ledger-num">01</p>
        <div class="ledger-lbl">先量测，再动手
          <span class="sub">没有基线，改动无法归因。</span>
        </div>
      </div>
      <div class="ledger-row">
        <p class="ledger-num">02</p>
        <div class="ledger-lbl">看顺序，不看数量
          <span class="sub">一条规则位置错，后面全部失效。</span>
        </div>
      </div>
      <div class="ledger-row">
        <p class="ledger-num">03</p>
        <div class="ledger-lbl">改完回写配置
          <span class="sub">临时修改撑不过一次重启。</span>
        </div>
      </div>
    </div>
  </div>
</section>
```

**S07 and S11 share the `.stacked-ledger` DOM.** The difference is content type, not markup: S07 is ordinal (01/02/03 plus a phrase) and caps at **three** rows; S11 is quantified (amount plus label plus `.ledger-icn`) and runs 4-6 rows. If your rows are amounts, you are on S11.

The `.card-ink` block carries the thesis, not the whole page. The seed has **no poster-level dark background for Swiss**, and `.stacked-ledger .ledger-lbl` is `var(--ink)`-colored — wrapping the ledger itself in `.card-ink` makes the labels vanish. For a fully dark closing board, add one task-scoped background block and invert `.ledger-lbl` explicitly; do not expect `.card-ink` to do it. Omitting `.ledger-icn` is fine — the third grid track collapses.

`.grow` on `.card-ink` is what absorbs the leftover canvas height; without it the three ledger rows sit in the upper half. The measured render lands at ~69% density with bands 56 / 100 / 74 / 47 — below the strict 74.5% advisory, and correct: a large ink panel with breathing room is the intended closing-page look. Do not pad it with invented rows to chase the number.

### S08 Image Hero

Best for the WeChat 21:9 main cover and the Rednote cover when you have one strong photo or product render. The image fills the page; the title sits inside an overlay block; 3 quantified stats anchor the bottom.

Structure:

- `.chrome-min` top single row (category + date).
- `.image-hero` grid: `.hero-img-wrap` with one `.frame-img.r-21x9` photo (or `.r-3x2` on 3:4).
- `.hero-overlay-block` sitting over the upper-left of the image with `t-cat` kicker + `h-statement` title.
- `.hero-stats` row of 3 `.stat-block`s, each with a big `num` (e.g. `132K`) and a mono `lbl`.

HTML skeleton (21:9, the natural format for this recipe):

```html
<section class="poster wide" id="wechat-21x9-hero" data-accent="ikb">
  <div class="content stack gap-7">
    <div class="chrome-min">
      <span class="t-cat">Release · 产品</span>
      <span class="t-meta">2026.05 · v3.0</span>
    </div>
    <div class="image-hero">
      <div class="hero-img-wrap">
        <img src="assets/hero.jpg" alt="hero">
        <div class="hero-overlay-block">
          <p class="t-cat">Cover · 主封面</p>
          <h1 class="h-statement">下一代<br>写作工作流</h1>
        </div>
      </div>
      <div class="hero-stats">
        <div class="stat-block">
          <p class="num">132K</p>
          <p class="lbl">Weekly Writers</p>
        </div>
        <div class="stat-block">
          <p class="num">3.4×</p>
          <p class="lbl">Output Speed</p>
        </div>
        <div class="stat-block">
          <p class="num">98%</p>
          <p class="lbl">Ship Rate</p>
        </div>
      </div>
    </div>
  </div>
</section>
```

Adapt for 3:4: keep the same `.image-hero` structure but swap `.r-21x9` to `.r-3x2`, drop overlay block down to 35% height. Adapt for 1:1: drop the overlay block entirely, push title above the image, keep at most 2 stats in `hero-stats`.

### S09 KPI Tower

Best for product-update pages, release notes, traffic dashboards, growth posts — anywhere 3-4 numbers need to be compared at a glance.

Structure:

- `kicker` + `h-xl` page title.
- `.kpi-tower-row` with 4 `.tower-col`s, each containing a `num`, `lbl`, and a `.bar-tower` whose height encodes the value via `style="--h:Npx"`.
- One column may carry the `.muted` modifier to drop the bar to neutral grey (use for a comparison baseline).

HTML skeleton (3:4 — template auto-collapses to 2 columns):

```html
<section class="poster xhs" id="xhs-data" data-accent="lemon-yellow">
  <div class="content stack gap-7">
    <p class="t-cat">Data · 半年增长</p>
    <h2 class="h-xl">六个月,四组数字</h2>
    <div class="kpi-tower-row">
      <div class="tower-col">
        <p class="num">132K</p>
        <p class="lbl">Subscribers</p>
        <div class="bar-tower" style="--h:320px"></div>
      </div>
      <div class="tower-col">
        <p class="num">3.4M</p>
        <p class="lbl">Reads</p>
        <div class="bar-tower" style="--h:220px"></div>
      </div>
      <div class="tower-col">
        <p class="num">68%</p>
        <p class="lbl">Repeat Visits</p>
        <div class="bar-tower" style="--h:160px"></div>
      </div>
      <div class="tower-col muted">
        <p class="num">8.2%</p>
        <p class="lbl">Conversion</p>
        <div class="bar-tower" style="--h:120px"></div>
      </div>
    </div>
  </div>
</section>
```

`--h` is a real height in px — pick values that proportionally encode the data. Do not invent numbers; if you only have 2 numbers, drop to 2 columns and use S03 File Card instead. Adapt for 21:9: keep 4 columns at the original width (it's where the layout shines). Adapt for 1:1: 2 columns max, taller bars.

### S10 H-Bar Chart

Best for rankings, comparisons of 5-10 items, "top N" lists, before/after pairs at scale. The bar fill encodes magnitude via `style="--w:NN%"`.

Structure:

- `kicker` + `h-xl` page title.
- `.h-bar-chart` grid with 5-10 `.bar-row`s.
- Each row: `.row-lbl` (Chinese item name), `.row-track` containing `.row-fill`, `.row-val` (mono number).

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-rank" data-accent="ikb">
  <div class="content stack gap-7">
    <p class="t-cat">Ranking · TOP 6</p>
    <h2 class="h-xl">本月最受欢迎的工具</h2>
    <div class="h-bar-chart">
      <div class="bar-row">
        <div class="row-lbl">Claude Code</div>
        <div class="row-track"><div class="row-fill" style="--w:94%"></div></div>
        <div class="row-val">94%</div>
      </div>
      <div class="bar-row">
        <div class="row-lbl">Cursor</div>
        <div class="row-track"><div class="row-fill" style="--w:78%"></div></div>
        <div class="row-val">78%</div>
      </div>
      <div class="bar-row">
        <div class="row-lbl">Linear</div>
        <div class="row-track"><div class="row-fill" style="--w:62%"></div></div>
        <div class="row-val">62%</div>
      </div>
      <div class="bar-row">
        <div class="row-lbl">Raycast</div>
        <div class="row-track"><div class="row-fill" style="--w:48%"></div></div>
        <div class="row-val">48%</div>
      </div>
      <div class="bar-row">
        <div class="row-lbl">Notion</div>
        <div class="row-track"><div class="row-fill" style="--w:36%"></div></div>
        <div class="row-val">36%</div>
      </div>
      <div class="bar-row">
        <div class="row-lbl">Obsidian</div>
        <div class="row-track"><div class="row-fill" style="--w:28%"></div></div>
        <div class="row-val">28%</div>
      </div>
    </div>
  </div>
</section>
```

Hard limits: max 6 rows on 1:1, max 10 rows on 3:4, max 8 rows on 21:9. On 3:4 the template stacks `row-lbl` above the track automatically — leave it alone. Always use real percentages or normalize the largest value to 100%; never fabricate.

### S11 Stacked Ledger

Best for shopping lists, expense rollups, agent capability inventories, or any "big number + label + icon" row stack where each item is quantified.

Structure:

- `kicker` + `h-xl` page title.
- `.stacked-ledger` containing 4-6 `.ledger-row`s.
- Each row: `.ledger-num` (large mono-feel sans number), `.ledger-lbl` (Chinese label + optional `.sub` secondary line), `.ledger-icn` (Lucide icon).

HTML skeleton (3:4):

```html
<section class="poster xhs" id="xhs-spend" data-accent="safety-orange">
  <div class="content stack gap-7">
    <p class="t-cat">Spend · 五月账单</p>
    <h2 class="h-xl">这个月我把钱花在哪</h2>
    <div class="stacked-ledger">
      <div class="ledger-row">
        <p class="ledger-num">¥1,280</p>
        <div class="ledger-lbl">订阅 · Subscriptions
          <span class="sub">Claude · Cursor · Linear</span>
        </div>
        <i class="ledger-icn" data-lucide="square-stack"></i>
      </div>
      <div class="ledger-row">
        <p class="ledger-num">¥860</p>
        <div class="ledger-lbl">书 · Books
          <span class="sub">6 本中文 · 2 本英文</span>
        </div>
        <i class="ledger-icn" data-lucide="book-open"></i>
      </div>
      <div class="ledger-row">
        <p class="ledger-num">¥540</p>
        <div class="ledger-lbl">咖啡 · Coffee
          <span class="sub">主要在 Manner 和 Seesaw</span>
        </div>
        <i class="ledger-icn" data-lucide="coffee"></i>
      </div>
      <div class="ledger-row">
        <p class="ledger-num">¥320</p>
        <div class="ledger-lbl">硬件 · Hardware
          <span class="sub">键盘配件 · 一根 USB-C</span>
        </div>
        <i class="ledger-icn" data-lucide="keyboard"></i>
      </div>
    </div>
  </div>
</section>
```

Hard limits: max 6 rows on 3:4, max 4 rows on 1:1, max 8 rows on 21:9. Pick angular Lucide icons (`book-open`, `coffee`, `bolt`, `square-stack`, `keyboard`) — never `heart-filled` or `smile`. If you cannot find numerical evidence, this is the wrong recipe; use S05 Trap Rows or M08 Tall Ledger.

### S12 Matrix + Hero Stat

Best for capability matrices, agent inventories, "this set covers X domains" pages where 8-12 small cells back a single bottom-line number.

Structure:

- `kicker` + `h-xl` page title.
- `.matrix-fill` grid of 8-12 `.matrix-cell`s. Each cell: `.cell-nb` (numbered mono label like `01`) and `.cell-title` (one short Chinese phrase).
- Exactly one cell may carry the `.is-accent` modifier to single out the most important capability.
- `.hero-stat-bottom` below the grid: left column with a kicker + short sentence, right column with one `num-mega` summary stat.

HTML skeleton (3:4 — matrix collapses to 2 columns):

```html
<section class="poster xhs" id="xhs-matrix" data-accent="lemon-green">
  <div class="content stack gap-7">
    <p class="t-cat">Capabilities · 能力清单</p>
    <h2 class="h-xl">一个 Agent,十二件事</h2>
    <div class="matrix-fill">
      <div class="matrix-cell"><p class="cell-nb">01</p><p class="cell-title">读取项目结构</p></div>
      <div class="matrix-cell"><p class="cell-nb">02</p><p class="cell-title">运行测试</p></div>
      <div class="matrix-cell is-accent"><p class="cell-nb">03</p><p class="cell-title">起草 PR 描述</p></div>
      <div class="matrix-cell"><p class="cell-nb">04</p><p class="cell-title">回滚错误改动</p></div>
      <div class="matrix-cell"><p class="cell-nb">05</p><p class="cell-title">迁移 lockfile</p></div>
      <div class="matrix-cell"><p class="cell-nb">06</p><p class="cell-title">检查类型</p></div>
      <div class="matrix-cell"><p class="cell-nb">07</p><p class="cell-title">追踪 issue</p></div>
      <div class="matrix-cell"><p class="cell-nb">08</p><p class="cell-title">阅读日志</p></div>
    </div>
    <div class="hero-stat-bottom">
      <div>
        <p class="t-cat">In total · 累计</p>
        <p class="lead">在 6 个仓库上,持续 4 周。</p>
      </div>
      <p class="num-mega">12</p>
    </div>
  </div>
</section>
```

Hard limits: 8 cells on 3:4 (2×4), 9 cells on 1:1 (3×3), 12 cells on 21:9 (4×3). Max one `is-accent` cell — two breaks the rhythm. The `num-mega` at bottom must agree with the cell count (`12` if 12 cells, `8` if 8); never display a number unsupported by the grid.

## WeChat Adaptation

For 21:9:

- Use M01/S01 as a wide composition, not a crop.
- Put title in the left or center-left safe area.
- Use the right half for photo/object/system visual.
- Enlarge title and subtitle spacing if the center feels empty.

For 1:1:

- Distill the long title into a simple centered title.
- If the user asks for no image, use type only.
- Avoid small subtitles and avoid photos by default.
- Keep the square readable as a thumbnail: one idea, big type, generous margins.

For pair preview:

- Put the `21:9` and `1:1` compositions in one HTML preview frame.
- The preview frame is for review only; do not treat it as a third required WeChat deliverable unless requested.
