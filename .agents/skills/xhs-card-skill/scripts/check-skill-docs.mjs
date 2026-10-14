#!/usr/bin/env node
import fs from "node:fs";

const checks = [];

function fileText(file) {
  return fs.readFileSync(file, "utf8");
}

function mustInclude(file, needle, label) {
  checks.push(() => {
    const ok = fileText(file).includes(needle);
    return {
      ok,
      label,
      detail: ok ? `${file} includes "${needle}"` : `${file} must include "${needle}"`,
    };
  });
}

function mustNotMatch(file, pattern, label) {
  checks.push(() => {
    const ok = !pattern.test(fileText(file));
    return {
      ok,
      label,
      detail: ok ? `${file} does not match ${pattern}` : `${file} must not match ${pattern}`,
    };
  });
}

// Every doc that ships and is read at runtime. A reference doc naming a path
// that does not exist is worse than silence: the agent will try to read it.
const SHIPPED_DOCS = [
  "SKILL.md",
  "AGENT.md",
  "README.md",
  "README.en.md",
  "PRODUCT.md",
  "HANDOFF.md",
  ...fs
    .readdirSync("references")
    .filter((f) => f.endsWith(".md"))
    .map((f) => `references/${f}`),
];

// A stale `references/*.md` link is invisible to a text assertion — it looks
// exactly like a valid one — so check the filesystem instead. This is the check
// that would have caught category-cookbook.md pointing at deleted demo folders.
checks.push(() => {
  const missing = [];
  for (const file of SHIPPED_DOCS) {
    for (const m of fileText(file).matchAll(/`(references\/[a-z0-9-]+\.md)`/g)) {
      if (!fs.existsSync(m[1])) missing.push(`${file} → ${m[1]}`);
    }
  }
  return {
    ok: missing.length === 0,
    label: "every referenced reference file exists on disk",
    detail: missing.length
      ? `missing: ${missing.join(", ")}`
      : `all ${SHIPPED_DOCS.length} shipped docs resolve their reference links`,
  };
});

mustInclude(
  "SKILL.md",
  "Generated work must live in a task folder, not in the skill root.",
  "root output guardrail in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "local-tests/<slug>/",
  "default task folder in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "Triple Live Photo",
  "triple Live Photo capability in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "material-first Live Photo puzzle layouts",
  "material-first puzzle capability in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "use the M16 Image-Led Cover / text-on-image rules",
  "single-video text uses M16 overlay rules",
);
mustInclude(
  "SKILL.md",
  "Do not invent extra kicker, meta, hairlines",
  "single-video text must not invent extra overlay copy",
);
mustInclude(
  "SKILL.md",
  "platform limits (`5s` Xiaohongshu, `3s` WeChat Official Account)",
  "platform publishing reminder in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "Audience-facing copy must describe the user's actual scene",
  "audience-facing copy guardrail in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "Do not add non-template ornaments just to satisfy an automated density warning",
  "non-template ornament guardrail in SKILL.md",
);
mustNotMatch(
  "SKILL.md",
  /Create a task folder in the current workspace/,
  "no root-level task-folder instruction in SKILL.md",
);

mustInclude(
  "SKILL.md",
  "**Recipe rhythm (hard).**",
  "recipe rhythm hard rule in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "No single recipe appears more than **twice**",
  "recipe repeat cap in SKILL.md",
);
mustInclude(
  "SKILL.md",
  "The default failure mode is shape collapse",
  "shape-collapse failure mode in SKILL.md",
);

mustInclude(
  "references/category-cookbook.md",
  "## 技术 · Tech / Developer",
  "tech / developer category route in cookbook",
);
mustInclude(
  "references/category-cookbook.md",
  "Every page becomes a numbered list",
  "tech route records the numbered-list failure mode",
);

mustInclude(
  "references/qa-checklist.md",
  "### Recipe Rhythm Check (whole set, not per page)",
  "recipe rhythm check in QA checklist",
);
mustInclude(
  "references/qa-checklist.md",
  "### Shape Families",
  "shape family taxonomy in QA checklist",
);
// The taxonomy is the doc side of R10. The validator never emits `ledger` — it
// detects numbered rows structurally as `rows` — so a `ledger` row here would
// make a human's count disagree with the validator's.
mustNotMatch(
  "references/qa-checklist.md",
  /\|\s*`ledger`\s*\|/,
  "QA checklist must not declare a `ledger` family the validator cannot emit",
);
// local-tests/ is gitignored and machine-specific. A named task folder in a
// runtime instruction doc points the agent at a path that may not exist.
mustNotMatch(
  "references/production-workflow.md",
  /`local-tests\/[a-z0-9-]+`/,
  "production-workflow must describe slug patterns, not concrete task folders",
);

mustInclude(
  "references/theme-presets.md",
  "## Series Theme Rotation (multi-article accounts)",
  "series theme rotation guidance",
);

mustInclude(
  "validate-social-deck.mjs",
  "R10 recipe rhythm",
  "R10 recipe-rhythm rule implemented in the validator",
);
// R5 must not count a full-bleed atmosphere layer (.grain / paper texture /
// ink-flow canvas) as content. It did until v0.25: the layer marks every pixel
// row, so density read 100% on every Editorial deck and R5 never warned once.
mustInclude(
  "validate-social-deck.mjs",
  "a full-bleed atmosphere layer",
  "R5 excludes full-bleed background layers from the density measurement",
);
// R5's 74.5% floor was retired in v0.26 — it had never been calibrated (the
// full-bleed bug made it read 100% everywhere). Density is now printed in the
// header instead. If someone reintroduces a density FAIL/WARN, this catches it.
mustInclude(
  "validate-social-deck.mjs",
  "REPORT-ONLY as of v0.26",
  "R5 density stays report-only rather than warning on an uncalibrated floor",
);
mustInclude(
  "validate-social-deck.mjs",
  "R13 composition anchor",
  "R13 composition-anchor check implemented in the validator",
);
mustInclude(
  "references/qa-checklist.md",
  "### Composition Anchor Check (whole set, not per page)",
  "composition anchor check in QA checklist",
);
mustInclude(
  "references/variation-engine.md",
  "**R13 reports on all three of these**",
  "variation-engine records the R13 enforcement point",
);

mustInclude(
  "references/layout-recipes.md",
  "Skeletons are measured, not guessed.",
  "layout recipes state that skeletons are measured",
);

mustInclude(
  "SKILL.md",
  "references/explainer-sequence.md",
  "teaching route is reachable from SKILL.md",
);
mustInclude(
  "SKILL.md",
  "The 误解 beat is not optional.",
  "SKILL.md carries the misconception-beat rule",
);
mustInclude(
  "references/explainer-sequence.md",
  "## 0. What Has Teeth",
  "explainer rules state their enforcement point",
);
mustInclude(
  "references/explainer-sequence.md",
  "## 4. The Analogy Gate (hard)",
  "analogy gate present",
);
mustInclude(
  "references/explainer-sequence.md",
  "## 5. The Jargon Gate (hard)",
  "jargon gate present",
);
mustInclude(
  "references/explainer-sequence.md",
  "states where it breaks",
  "analogy must declare its failure point",
);
mustInclude(
  "references/explainer-sequence.md",
  "## 10. Deliberately Not Adopted",
  "explainer file records what was rejected from upstream",
);
mustInclude(
  "references/explainer-sequence.md",
  "## Provenance",
  "explainer file records where its ideas came from",
);
mustInclude(
  "references/qa-checklist.md",
  "## Teaching Sets",
  "QA checklist covers teaching sets",
);

mustInclude(
  "SKILL.md",
  "references/anti-ai-tells.md",
  "anti-AI-tells route is reachable from SKILL.md",
);
mustInclude(
  "references/anti-ai-tells.md",
  "## 0. The Squint Test",
  "anti-AI-tells opens with the squint test",
);
mustInclude(
  "references/anti-ai-tells.md",
  "## 1. Copy Tells",
  "anti-AI-tells covers the copy axis",
);
mustInclude(
  "references/anti-ai-tells.md",
  "**AI-flavoured cute copy is worse than boring copy.**",
  "copy self-audit states its decision rule",
);
mustInclude(
  "references/anti-ai-tells.md",
  "**R12 is a floor, not a filter.**",
  "R12's limits are documented rather than implied",
);
mustInclude(
  "references/anti-ai-tells.md",
  "## 6. Density: a conflict, resolved",
  "the R5-vs-anti-slop conflict is resolved in writing",
);
mustInclude(
  "validate-social-deck.mjs",
  "R12 banned copy phrases",
  "R12 implemented in the validator",
);
mustInclude(
  "validate-social-deck.mjs",
  "const BANNED_COPY",
  "R12 phrase list is present",
);
mustInclude(
  "references/qa-checklist.md",
  "## Anti-AI-Tells",
  "QA checklist covers anti-AI-tells",
);

mustInclude(
  "SKILL.md",
  "references/variation-engine.md",
  "variation-engine route is reachable from SKILL.md",
);
mustInclude(
  "SKILL.md",
  "**Anchor rhythm (hard).**",
  "anchor rhythm is a hard gate in SKILL.md Step 4",
);
mustInclude(
  "SKILL.md",
  "Anchor A (top-left stacked) at most twice per set.",
  "SKILL.md names the overused default and caps it",
);
mustInclude(
  "references/variation-engine.md",
  "R10 detects. This file generates.",
  "variation engine states its relationship to R10",
);
mustInclude(
  "references/variation-engine.md",
  "## 2. Composition Anchor (per page)",
  "composition anchor axis present",
);
mustInclude(
  "references/variation-engine.md",
  "## 6. Second-Read Moment (exactly one per set)",
  "second-read moment axis present",
);
mustInclude(
  "references/variation-engine.md",
  "## 8. Axes Deliberately Not Taken",
  "variation engine records rejected axes",
);
mustInclude(
  "references/variation-engine.md",
  "## Provenance",
  "variation engine records where its axes came from",
);
mustInclude(
  "references/qa-checklist.md",
  "## Variation (per set)",
  "QA checklist covers variation",
);

mustInclude(
  "references/screenshot-treatment.md",
  "## Multi-shot discipline",
  "screenshot treatment covers multi-shot discipline",
);
mustInclude(
  "references/screenshot-treatment.md",
  "### One corner scale per composition",
  "corner-scale lock documented",
);
mustInclude(
  "references/screenshot-treatment.md",
  "### Device mockup is opt-in, not default",
  "the upstream mockup-by-default rule is explicitly rejected",
);
mustInclude(
  "validate-social-deck.mjs",
  "R11 corner-radius lock",
  "R11 implemented in the validator",
);
mustInclude(
  "references/qa-checklist.md",
  "Validator **R11**",
  "QA checklist references R11",
);

mustInclude(
  "references/theme-presets.md",
  "### The six palettes are six distinct identities",
  "palette identities are documented from measured pixels",
);
mustInclude(
  "references/theme-presets.md",
  "### The one real family boundary is the mode",
  "the real family boundary is named",
);
mustInclude(
  "references/theme-presets.md",
  "### Not adopted: the warm-paper ban",
  "the upstream palette ban is explicitly rejected",
);
mustInclude(
  "references/components.md",
  "### Typeface is identity, not decoration",
  "typeface-as-identity rule present",
);
mustInclude(
  "references/components.md",
  "### Emphasis stays inside the family",
  "same-family emphasis rule present",
);
mustInclude(
  "references/components.md",
  "### Italic descender clearance",
  "italic descender clearance rule present",
);
mustInclude(
  "references/components.md",
  "explicitly rejected**, with reasons recorded",
  "rejected upstream typography rules are recorded",
);
mustInclude(
  "references/style-system.md",
  "is its identity, not a style option",
  "style system states the typeface is identity",
);
mustInclude(
  "references/layout-recipes.md",
  "Requires the seed template's ledger margin patch",
  "M07/M08 document the ledger margin dependency",
);
mustInclude(
  "references/layout-recipes.md",
  "Each row needs three lines, not one.",
  "S05 documents its per-row content floor",
);
mustInclude(
  "references/layout-recipes.md",
  "`.grow` is required on **each** row",
  "S06 documents the per-row grow requirement",
);
mustInclude(
  "assets/template-editorial-card.html",
  ".ledger-row .ledger-title / .ledger-note margin reset",
  "seed template records the ledger margin patch",
);
checks.push(() => {
  const n = (fileText("references/layout-recipes.md").match(/HTML skeleton/g) || []).length;
  return {
    ok: n >= 29,
    label: `layout-recipes.md ships ${n} HTML skeletons (need ≥29)`,
    detail: `${n} skeletons found; every M/S recipe except S03's neighbours must carry one`,
  };
});

mustInclude(
  "references/layout-recipes.md",
  "Every Editorial page needs `assets/magazine-bg-webgl.js` in the task folder.",
  "Editorial atmosphere-layer asset requirement is documented",
);
mustInclude(
  "references/layout-recipes.md",
  "**Use `space-between`, never `center`.**",
  "M09 documents the centering failure mode",
);
mustInclude(
  "references/layout-recipes.md",
  "always eyeball an M03 render, do not trust a green pass",
  "M03 records the R5 blind spot",
);
mustInclude(
  "references/layout-recipes.md",
  "The gaps are 32px on purpose.",
  "M06 records its measured gap values",
);

mustInclude(
  "references/production-workflow.md",
  "Create a task folder under `local-tests/` by default",
  "production workflow uses local-tests",
);
mustInclude(
  "references/production-workflow.md",
  "Do not create generated task folders or rendered assets in the skill root",
  "production workflow forbids skill-root outputs",
);

mustInclude(
  "references/live-photo-production.md",
  "## Live Photo Information Budget",
  "Live Photo information budget section",
);
mustInclude(
  "references/live-photo-production.md",
  "## Triple Live Photo Collage",
  "triple collage section",
);
mustInclude(
  "references/live-photo-production.md",
  "## Material-First Puzzle Layouts",
  "material-first puzzle section",
);
mustInclude(
  "references/live-photo-production.md",
  "Do not render single-video text as generic subtitles.",
  "single-video text is not subtitle-style",
);
mustInclude(
  "references/live-photo-production.md",
  "Do not invent extra kicker, meta, hairlines",
  "single-video overlay must not invent extra copy",
);
mustInclude(
  "references/live-photo-production.md",
  "Four-grid",
  "four-grid puzzle guidance",
);
mustInclude(
  "references/live-photo-production.md",
  "## Long Video Intake",
  "long video intake section",
);
mustInclude(
  "references/live-photo-production.md",
  "Web-sourced free videos are only for making our own demo/promo cases",
  "user-supplied video is the normal path",
);
mustInclude(
  "references/live-photo-production.md",
  "Audience-facing copy should name the real scene in the video.",
  "Live Photo visible-copy guardrail",
);

mustInclude(
  "references/category-cookbook.md",
  "## Live Photo Scene Library",
  "category scene library section",
);
mustInclude(
  "references/category-cookbook.md",
  "This is not a fixed template list.",
  "category library stays heuristic",
);

mustInclude(
  "PRODUCT.md",
  "## 10. Live Photo 复盘（2026-07-01）",
  "product doc records Live Photo retrospective",
);
mustInclude(
  "PRODUCT.md",
  "把内部制作要求写成观众可见文案",
  "product doc records visible-copy failure mode",
);
mustInclude(
  "HANDOFF.md",
  "### v0.15 · 2026-07-01",
  "handoff records Live Photo version history",
);
mustInclude(
  "HANDOFF.md",
  "不要把制作要求当成观众可见内容",
  "handoff records Live Photo execution pitfall",
);

let failed = 0;
for (const run of checks) {
  const result = run();
  const marker = result.ok ? "PASS" : "FAIL";
  console.log(`${marker} ${result.label}`);
  if (!result.ok) {
    failed += 1;
    console.log(`  ${result.detail}`);
  }
}

if (failed > 0) {
  console.error(`\n${failed} skill doc check(s) failed.`);
  process.exit(1);
}

console.log(`\n${checks.length} skill doc checks passed.`);
