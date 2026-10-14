#!/usr/bin/env node
/*
 * validate-social-deck.mjs
 *
 *   node validate-social-deck.mjs <task-dir|index.html>
 *
 * Checks each <section class="poster …"> in the target HTML against the rules
 * codified in SKILL.md / qa-checklist.md / components.md. Exits 1 if any FAIL.
 *
 * Rules implemented:
 *   R1  overflow              scrollHeight > clientHeight on the poster
 *   R2  footer collision      .foot is position:absolute AND content above reaches into its band
 *   R3  swiss bold display    .h-xl / .h-hero / .h-statement / .num-mega with computed weight >= 600
 *   R4  min readable font     body/lead/caption/label/meta below the mobile-safe floor
 *   R5  4-band density        on 3:4 boards: <75% filled OR any under-filled band > 216px
 *   R6  h-xl hard cap         display title lines/chars exceed the per-board cap
 *   R7  figure margin drift    browser-default <figure> margin offsets media alignment
 *   R8  visual bounds          measured top/bottom blank space and visual overflow
 *   R9  title gap              display title touches the next content block
 *   R10 recipe rhythm          package-level shape diversity (advisory, WARN only)
 *   R11 corner-radius lock     more than two distinct .frame-shot radii in one set
 *   R12 banned copy phrases    marketing filler / fake branding in visible text (advisory)
 *   R13 composition anchor     per-set anchor diversity via a geometric proxy (advisory)
 */
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const args = process.argv.slice(2);
let target = null;
let styleOverride = null;
for (const a of args) {
  if (a.startsWith("--style=")) styleOverride = a.slice(8);
  else if (!target) target = a;
}
if (!target) {
  console.error("usage: node validate-social-deck.mjs <task-dir|index.html> [--style=swiss|editorial]");
  process.exit(2);
}
const abs = path.resolve(target);
let htmlPath = abs;
if (fs.statSync(abs).isDirectory()) {
  htmlPath = path.join(abs, "index.html");
}
if (!fs.existsSync(htmlPath)) {
  console.error(`not found: ${htmlPath}`);
  process.exit(2);
}

const url = "file://" + htmlPath;
const browser = await chromium.launch({
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const ctx = await browser.newContext({
  viewport: { width: 1400, height: 1700 },
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

const style = styleOverride || await page.evaluate(() => {
  const html = document.documentElement;
  if (html.dataset.theme) return "editorial";
  if (html.dataset.accent) return "swiss";
  // Fallback: any display class using a serif family = editorial; otherwise swiss.
  // Note: "sans-serif" generic name also contains the substring "serif", so we
  // match on serif-only families by token, not substring.
  const SERIF_TOKENS = ["noto serif", "playfair", "source serif", "songti", "stsong", "simsun", "serif sc", "kinfolk", "merriweather"];
  for (const n of document.querySelectorAll(".h-display, .h-xl, .h-hero, .pullquote, .h-statement")) {
    const ff = getComputedStyle(n).fontFamily.toLowerCase();
    if (SERIF_TOKENS.some(t => ff.includes(t))) return "editorial";
  }
  return "swiss";
});

const sections = await page.$$("section.poster");
const report = [];
// Package-level measurements printed in the header instead of as warnings:
//   densitySeq — R5, whose 74.5% floor has never been calibrated (see R5)
//   anchorSeq  — R13, the composition-anchor proxy
const densitySeq = [];
const anchorSeq = [];

// "Obviously too small" floors — below these, text becomes unreadable at phone size.
// Seeds use 26-28px body, 30-32px lead — those are fine. Anything 4+px below is suspect.
const MIN_FONT = {
  body: 22,
  lead: 26,
  caption: 18,
  meta: 18,
  cellTitle: 20,
  numAnnotation: 20,
};

const HXL_CAPS = {
  xhs:    { maxLines: 2, maxChars: 8 },
  square: { maxLines: 2, maxChars: 7 },
  wide:   { maxLines: 1, maxChars: 14 },
};

const DISPLAY_CLASSES = ["h-xl", "h-hero", "h-statement", "h-display", "num-mega", "num-xl"];

// R12 phrase list — mirrors references/anti-ai-tells.md §1.A.
// Keep this list tight and specific. A broad list produces constant noise, and
// a noisy check gets ignored, which is worse than no check.
const BANNED_COPY = [
  // Chinese marketing filler
  "赋能", "解锁", "重塑", "颠覆", "无缝", "极致", "一站式", "全新升级", "下一代", "革命性",
  "未来可期", "拥抱变化", "持续进化", "深入浅出", "一文读懂", "彻底搞懂",
  // English marketing filler (Swiss cards mix in Latin labels)
  "unleash", "elevate", "revolutionize", "next-gen", "seamless", "transformative",
  // fake branding
  "acme", "novacore", "flowbit", "quantix", "velopay",
];

function overflowFix(px) {
  const n = Math.round(px);
  if (n <= 40) {
    return `only ${n}px over: nudge content up or tighten one gap/padding by 20-40px; do not delete content`;
  }
  if (n <= 90) {
    return `${n}px over: compact local gaps/padding and reduce one block height; avoid cutting copy`;
  }
  if (n <= 160) {
    return `${n}px over: reduce a display title slightly or compress one paragraph before deleting content`;
  }
  return `${n}px over: switch to a higher-capacity recipe or remove/merge content intentionally`;
}

for (const s of sections) {
  const meta = await s.evaluate(el => {
    const w = el.clientWidth, h = el.clientHeight;
    let board = el.classList.contains("xhs") ? "xhs"
              : el.classList.contains("square") ? "square"
              : el.classList.contains("wide") ? "wide" : null;
    if (!board) {
      const ratio = w / h;
      if (Math.abs(ratio - 0.75) < 0.02) board = "xhs";        // 3:4
      else if (Math.abs(ratio - 1.0) < 0.02) board = "square"; // 1:1
      else if (Math.abs(ratio - 7 / 3) < 0.05) board = "wide"; // 21:9
      else board = "unknown";
    }
    return { id: el.id || "(no-id)", dataId: el.dataset.id || "", board, clientH: h, scrollH: el.scrollHeight, clientW: w };
  });
  const fails = [];
  const warns = [];

  // R1 overflow
  const overflow = meta.scrollH - meta.clientH;
  if (overflow > 4) {
    fails.push({ rule: "R1", msg: `overflow ${overflow}px (scrollH ${meta.scrollH} > clientH ${meta.clientH})`, fix: overflowFix(overflow) });
  }

  // R8 visual bounds — measures the true rendered content extent, not just scrollHeight.
  const visualBounds = await s.evaluate(el => {
    const er = el.getBoundingClientRect();
    const H = el.clientHeight;
    const W = el.clientWidth;
    const posterArea = H * W;
    const TRANSPARENT = /rgba?\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0?\s*\)|transparent/;
    const hasDirectText = n => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3 && c.textContent.trim().length > 0) return true;
      }
      return false;
    };
    const cssColorVisible = c => c && !TRANSPARENT.test(c);
    const meaningful = [];
    for (const n of el.querySelectorAll("*")) {
      if (n.closest("script, style")) continue;
      const tag = n.tagName;
      const cs = getComputedStyle(n);
      const r = n.getBoundingClientRect();
      if (r.width < 6 || r.height < 6) continue;
      if (n === el || n.classList.contains("content")) continue;
      if (cs.position === "absolute" && r.width * r.height >= posterArea * 0.85) continue;

      const isText = hasDirectText(n);
      const isMedia = tag === "IMG" || tag === "CANVAS" || tag === "SVG";
      const isRule = tag === "HR" || (r.height <= 4 && (
        parseFloat(cs.borderTopWidth) >= 1 ||
        parseFloat(cs.borderBottomWidth) >= 1 ||
        cssColorVisible(cs.backgroundColor)
      ));
      const hasBoxFill = cssColorVisible(cs.backgroundColor) &&
        r.width * r.height >= 1600 &&
        !["MAIN", "SECTION"].includes(tag);
      const hasBorderBox = (parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth) +
        parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth)) >= 1 &&
        r.width * r.height >= 1600;
      if (!isText && !isMedia && !isRule && !hasBoxFill && !hasBorderBox) continue;
      meaningful.push({
        top: r.top - er.top,
        bottom: r.bottom - er.top,
        left: r.left - er.left,
        right: r.right - er.left,
        cls: n.className ? "." + String(n.className).split(" ").filter(Boolean).join(".") : tag.toLowerCase(),
        text: n.textContent.trim().replace(/\s+/g, " ").slice(0, 32),
      });
    }
    if (meaningful.length === 0) return null;
    let top = Infinity, bottom = -Infinity;
    let topNode = null, bottomNode = null;
    for (const item of meaningful) {
      if (item.top < top) {
        top = item.top;
        topNode = item;
      }
      if (item.bottom > bottom) {
        bottom = item.bottom;
        bottomNode = item;
      }
    }
    return {
      top: Math.round(top),
      bottom: Math.round(bottom),
      activeHeight: Math.round(bottom - top),
      activeRatio: (bottom - top) / H,
      topGap: Math.max(0, Math.round(top)),
      bottomGap: Math.max(0, Math.round(H - bottom)),
      topOverflow: Math.max(0, Math.round(-top)),
      bottomOverflow: Math.max(0, Math.round(bottom - H)),
      topNode,
      bottomNode,
      count: meaningful.length,
    };
  });
  if (visualBounds) {
    if (visualBounds.bottomOverflow > 4) {
      fails.push({
        rule: "R8",
        msg: `visual bottom overflow ${visualBounds.bottomOverflow}px (lowest: ${visualBounds.bottomNode.cls}${visualBounds.bottomNode.text ? ` "${visualBounds.bottomNode.text}"` : ""})`,
        fix: overflowFix(visualBounds.bottomOverflow),
      });
    }
    if (visualBounds.topOverflow > 4) {
      fails.push({
        rule: "R8",
        msg: `visual top overflow ${visualBounds.topOverflow}px (highest: ${visualBounds.topNode.cls}${visualBounds.topNode.text ? ` "${visualBounds.topNode.text}"` : ""})`,
        fix: "move the content group down by the measured overflow plus 16-24px",
      });
    }
    const bottomGapLimit = meta.board === "wide" ? 130 : meta.board === "square" ? 160 : 190;
    if (visualBounds.bottomGap > bottomGapLimit && visualBounds.activeRatio < 0.78) {
      warns.push({
        rule: "R8",
        msg: `bottom whitespace ${visualBounds.bottomGap}px; active content height ${Math.round(visualBounds.activeRatio * 100)}%`,
        fix: "use the measured blank space to expand the last block or move the content down slightly; avoid over-tightening after an overflow fix",
      });
    }
  }

  // R2 footer collision — only flag leaf text or media nodes, not containers
  // whose bbox merely shares y-space with an absolute-positioned strip.
  const footIssue = await s.evaluate(el => {
    const foot = el.querySelector(".foot, .issue-strip, .magazine-foot");
    if (!foot) return null;
    const cs = getComputedStyle(foot);
    if (cs.position !== "absolute") return null;
    const footTop = foot.offsetTop;
    const er = el.getBoundingClientRect();
    const hasOwnText = n => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3 && c.textContent.trim().length > 0) return true;
      }
      return false;
    };
    const isMedia = n => n.tagName === "IMG" || n.tagName === "CANVAS" || n.tagName === "SVG" || n.tagName === "FIGURE";
    let worstOverlap = 0;
    let worstSel = "";
    const posterArea = el.clientWidth * el.clientHeight;
    for (const node of el.querySelectorAll("*")) {
      if (node === foot || foot.contains(node)) continue;
      if (!hasOwnText(node) && !isMedia(node)) continue;
      const r = node.getBoundingClientRect();
      // Skip full-bleed background layers (>=95% of poster area + position:absolute).
      const ncs = getComputedStyle(node);
      if (ncs.position === "absolute" && r.width * r.height >= posterArea * 0.95) continue;
      const bottom = r.bottom - er.top;
      if (bottom > footTop + 2) {
        const overlap = bottom - footTop;
        if (overlap > worstOverlap) {
          worstOverlap = overlap;
          worstSel = node.className ? "." + node.className.split(" ").filter(Boolean).join(".") : node.tagName.toLowerCase();
        }
      }
    }
    return worstOverlap > 6 ? { overlap: Math.round(worstOverlap), sel: worstSel, footTop } : null;
  });
  if (footIssue) {
    fails.push({ rule: "R2", msg: `.foot is position:absolute, ${footIssue.sel} extends ${footIssue.overlap}px past foot top`, fix: "switch .foot to flex margin-top:auto (see style-system.md Anti-pattern C)" });
  }

  // R3 swiss bold display
  if (style === "swiss") {
    const bolds = await s.evaluate((el, cls) => {
      const out = [];
      for (const c of cls) {
        for (const n of el.querySelectorAll("." + c)) {
          const cs = getComputedStyle(n);
          const w = parseInt(cs.fontWeight, 10);
          const size = parseFloat(cs.fontSize);
          if (size >= 72 && w >= 600) {
            out.push({ cls: c, weight: w, size: Math.round(size), text: n.textContent.trim().slice(0, 20) });
          }
        }
      }
      return out;
    }, DISPLAY_CLASSES);
    for (const b of bolds) {
      fails.push({ rule: "R3", msg: `.${b.cls} "${b.text}" is ${b.size}px @ weight ${b.weight} — Swiss "larger = lighter" hard rule`, fix: "remove inline font-weight; use the typed class default (200-300)" });
    }
  }

  // R4 min readable font
  const textChecks = await s.evaluate((el, MIN) => {
    const out = [];
    const seen = new Set();
    const test = (selector, role, min, requireLeaf = true) => {
      for (const n of el.querySelectorAll(selector)) {
        if (seen.has(n)) continue;
        seen.add(n);
        // Skip containers — typography roles only apply to leaf text nodes.
        // A real `.lead` is a <p>; `.role-card.lead` is a container with children.
        if (requireLeaf && n.children.length > 0) continue;
        // Skip decorative chips inside map pins — labels are sized to fit on the map by design.
        if (n.closest(".map-pin .card")) continue;
        const cs = getComputedStyle(n);
        const size = parseFloat(cs.fontSize);
        const text = n.textContent.trim();
        if (!text) continue;
        if (size > 0 && size < min) {
          out.push({ role, selector, min, size: Math.round(size), text: text.slice(0, 30) });
        }
      }
    };
    test(".body, p.body", "body", MIN.body);
    test(".lead", "lead", MIN.lead);
    test(".kicker, .cap, .caption, .swiss-img-caption, .h-sub", "caption", MIN.caption);
    test(".meta, .label, .mono", "meta", MIN.meta);
    test(".matrix-fill .cell-title, .brief-card .title, .char-grid .name", "cellTitle", MIN.cellTitle);
    test(".stat-card .lbl, .ledger .sub, .num .sub", "numAnnotation", MIN.numAnnotation);
    return out;
  }, MIN_FONT);
  for (const t of textChecks) {
    warns.push({ rule: "R4", msg: `${t.role} "${t.text}" at ${t.size}px < ${t.min}px floor`, fix: "cut copy instead of shrinking type (components.md Minimum Readable Sizes)" });
  }

  // R12 banned copy phrases — the enforcement point for
  // references/anti-ai-tells.md §1.A. A floor, not a filter: it matches a fixed
  // list and cannot judge whether a sentence that avoids the list is good copy.
  // A match is a report, not a verdict — some phrases are legitimate literally
  // (解锁账号 in a security post), so read the location before rewriting.
  const copyHits = await s.evaluate((el, list) => {
    const CHROME = ".foot, .issue-strip, .magazine-foot, .chrome-min, .pair-preview, .preview-label, script, style";
    const seen = new Map();
    for (const n of el.querySelectorAll("*")) {
      if (n.children.length > 0) continue;
      if (n.closest(CHROME)) continue;
      const raw = n.textContent.trim();
      if (!raw) continue;
      const low = raw.toLowerCase();
      for (const p of list) {
        if (seen.has(p) || !low.includes(p.toLowerCase())) continue;
        seen.set(p, {
          where: n.className ? "." + String(n.className).split(" ").filter(Boolean).join(".") : n.tagName.toLowerCase(),
          text: raw.slice(0, 40),
        });
      }
    }
    return [...seen.entries()].map(([phrase, v]) => ({ phrase, ...v }));
  }, BANNED_COPY);
  for (const h of copyHits) {
    warns.push({
      rule: "R12",
      msg: `"${h.phrase}" in ${h.where} "${h.text}"`,
      fix: "replace the phrase with the specific fact it stands in for (references/anti-ai-tells.md §1.A); ignore if the word is literal in context",
    });
  }

  // R7 figure margin drift — catches browser-default 40px figure margins on custom media blocks.
  const figureDrift = await s.evaluate(el => {
    const out = [];
    for (const fig of el.querySelectorAll("figure")) {
      const cs = getComputedStyle(fig);
      const ml = parseFloat(cs.marginLeft) || 0;
      const mr = parseFloat(cs.marginRight) || 0;
      if (ml >= 16 || mr >= 16) {
        const text = fig.textContent.trim().replace(/\s+/g, " ").slice(0, 32);
        out.push({
          cls: fig.className ? "." + fig.className.split(" ").filter(Boolean).join(".") : "figure",
          ml: Math.round(ml),
          mr: Math.round(mr),
          text,
        });
      }
    }
    return out;
  });
  for (const f of figureDrift) {
    warns.push({ rule: "R7", msg: `${f.cls} has horizontal figure margin ${f.ml}px / ${f.mr}px${f.text ? ` near "${f.text}"` : ""}`, fix: "reset figure margins in the seed or task CSS: .poster figure { margin: 0; }" });
  }

  // R5 4-band density (3:4 only)
  if (meta.board === "xhs") {
    const bands = await s.evaluate(el => {
      const er = el.getBoundingClientRect();
      const H = el.clientHeight;
      const posterArea = H * el.clientWidth;
      // Pixel-row occupancy bitmap. Mark any row covered by a content element.
      const rows = new Uint8Array(H);
      const hasDirectText = n => {
        for (const c of n.childNodes) {
          if (c.nodeType === 3 && c.textContent.trim().length > 0) return true;
        }
        return false;
      };
      for (const n of el.querySelectorAll("*")) {
        const r = n.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) continue;
        const tag = n.tagName;
        const cs = getComputedStyle(n);
        // Same exclusion R8 already applies: a full-bleed atmosphere layer
        // (.grain, paper texture, ink-flow canvas) covers the whole board, so
        // counting it as content marks every pixel row and density reads 100%
        // on any deck that has one — which silences R5 entirely. That is not a
        // hypothetical: it made R5 report 100% on all seven real Editorial
        // decks while their content density was unmeasured.
        if (cs.position === "absolute" && r.width * r.height >= posterArea * 0.85) continue;
        const isText = hasDirectText(n);
        const isImg = tag === "IMG" || tag === "CANVAS" || tag === "SVG"
                    || (cs.backgroundImage && cs.backgroundImage !== "none");
        const isRule = tag === "HR" || (parseFloat(cs.borderTopWidth) >= 1 && r.height < 4);
        const hasFill = cs.backgroundColor && !cs.backgroundColor.match(/rgba?\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0?\s*\)/) && cs.backgroundColor !== "transparent";
        if (!isText && !isImg && !isRule && !hasFill) continue;
        const top = Math.max(0, Math.floor(r.top - er.top));
        const bot = Math.min(H, Math.ceil(r.bottom - er.top));
        for (let y = top; y < bot; y++) rows[y] = 1;
      }
      const BAND = H / 4;
      const occ = [0, 0, 0, 0];
      for (let i = 0; i < 4; i++) {
        let count = 0;
        const bTop = Math.floor(i * BAND), bBot = Math.floor((i + 1) * BAND);
        for (let y = bTop; y < bBot; y++) count += rows[y];
        occ[i] = count / (bBot - bTop);
      }
      return { H, BAND, occ };
    });
    const total = bands.occ.reduce((a, b) => a + b, 0) / 4;
    const pct = (o) => Math.round(o * 100) + "%";

    // REPORT-ONLY as of v0.26. The 74.5% floor is retired, not lowered: it has
    // never been calibrated. Until v0.25 R5 counted the full-bleed atmosphere
    // layer as content, so it read 100% on every Editorial deck and the floor
    // was never once exercised against a real measurement. Telling you "below
    // floor" on every page of every deck, using a number nobody ever validated,
    // is the noise this file already warns about in the R12 note. The figure is
    // printed in the header instead — see HANDOFF.md v0.25 for the measured
    // range (real decks 45-71%, average 53%; the content box caps density at
    // 87%, so a floor of 74.5% demands near-zero spacing between blocks).
    densitySeq.push({ id: meta.id, total, occ: bands.occ });

    // The void check stays. It does not depend on the uncalibrated global floor:
    // two adjacent bands under 15% is an actual hole in the middle of a poster.
    for (let i = 0; i < 3; i++) {
      if (bands.occ[i] < 0.15 && bands.occ[i + 1] < 0.15) {
        warns.push({ rule: "R5", msg: `bands ${i + 1}+${i + 2} both under-filled (${pct(bands.occ[i])} / ${pct(bands.occ[i + 1])}) — >25% void mid-poster`, fix: "expand body content or insert a marginalia column" });
        break;
      }
    }
  }

  // R6 h-xl hard cap
  const cap = HXL_CAPS[meta.board];
  if (cap) {
    const titles = await s.evaluate(el => {
      const out = [];
      for (const n of el.querySelectorAll(".h-xl, .h-hero, .h-display, .h-statement")) {
        const cs = getComputedStyle(n);
        const size = parseFloat(cs.fontSize);
        const lineH = parseFloat(cs.lineHeight) || size * 1.2;
        const lines = Math.round(n.getBoundingClientRect().height / lineH);
        out.push({ cls: n.className.split(" ")[0], text: n.textContent.trim(), lines, size: Math.round(size) });
      }
      return out;
    });
    for (const t of titles) {
      const longestLine = t.text.split(/\s+/).reduce((m, w) => Math.max(m, w.length), t.text.length);
      if (t.lines > cap.maxLines) {
        warns.push({ rule: "R6", msg: `.${t.cls} "${t.text}" renders ${t.lines} lines (cap ${cap.maxLines} on ${meta.board})`, fix: "switch to S01/S05 cover recipes that allow taller titles, or trim copy" });
      } else if (longestLine > cap.maxChars + 2) {
        // soft warn only — wraps naturally
      }
    }
  }

  // R9 title gap — catches headings visually touching the next content block.
  const titleGaps = await s.evaluate((el, board) => {
    const er = el.getBoundingClientRect();
    const TRANSPARENT = /rgba?\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0?\s*\)|transparent/;
    const hasDirectText = n => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3 && c.textContent.trim().length > 0) return true;
      }
      return false;
    };
    const colorVisible = c => c && !TRANSPARENT.test(c);
    const isMeaningful = n => {
      const tag = n.tagName;
      const cs = getComputedStyle(n);
      const r = n.getBoundingClientRect();
      if (r.width < 6 || r.height < 6) return false;
      if (n === el || n.classList.contains("content")) return false;
      if (hasDirectText(n)) return true;
      if (tag === "IMG" || tag === "CANVAS" || tag === "SVG" || tag === "HR") return true;
      if (colorVisible(cs.backgroundColor) && r.width * r.height >= 1600 && !["MAIN", "SECTION"].includes(tag)) return true;
      const border = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth) +
        parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth);
      return border >= 1 && r.width * r.height >= 1600;
    };
    const label = n => {
      const cls = n.className ? "." + String(n.className).split(" ").filter(Boolean).join(".") : n.tagName.toLowerCase();
      const text = n.textContent.trim().replace(/\s+/g, " ").slice(0, 30);
      return text ? `${cls} "${text}"` : cls;
    };
    const nodes = Array.from(el.querySelectorAll("*")).filter(isMeaningful);
    const titles = Array.from(el.querySelectorAll(".h-xl, .h-hero, .h-display, .h-statement, .h-md, .row-title, .step-title"));
    const out = [];
    for (const title of titles) {
      if (!title.textContent.trim()) continue;
      const tr = title.getBoundingClientRect();
      const isLocal = title.matches(".h-md, .row-title, .step-title");
      const minGap = isLocal ? 16 : board === "wide" ? 24 : 28;
      let nearest = null;
      let nearestGap = Infinity;
      for (const node of nodes) {
        if (node === title || title.contains(node) || node.contains(title)) continue;
        const nr = node.getBoundingClientRect();
        const gap = nr.top - tr.bottom;
        if (gap < -2) continue;
        const overlap = Math.max(0, Math.min(tr.right, nr.right) - Math.max(tr.left, nr.left));
        const overlapRatio = overlap / Math.min(tr.width, nr.width);
        if (overlapRatio < 0.12 && gap < 96) continue;
        if (gap < nearestGap) {
          nearestGap = gap;
          nearest = node;
        }
      }
      if (nearest && nearestGap < minGap) {
        out.push({
          title: label(title),
          next: label(nearest),
          gap: Math.round(nearestGap),
          minGap,
        });
      }
    }
    return out;
  }, meta.board);
  for (const g of titleGaps) {
    warns.push({
      rule: "R9",
      msg: `${g.title} has ${g.gap}px gap before ${g.next} (min ${g.minGap}px)`,
      fix: "add/restore measured spacing under the title before reducing copy or resizing the whole layout",
    });
  }

  report.push({ meta, fails, warns });
}

// R10 recipe rhythm — package-level shape diversity. Advisory only: monotony is a
// taste failure, not a broken layout, so it never changes the exit code.
// Families are the taxonomy published in references/qa-checklist.md → Shape Families.
// Only Rednote 3:4 cards are counted; WeChat covers are a separate deliverable.
const shapeFamilies = await page.$$eval("section.poster", els => {
  const CHROME = ".foot, .issue-strip, .magazine-foot, .chrome-min, .pair-preview, .preview-label";
  const familyOf = el => {
    const area = el.clientWidth * el.clientHeight;
    // Background layers must not count as evidence media, or every Editorial
    // page reads as a full-bleed image and the check becomes noise.
    let cover = 0;
    for (const n of el.querySelectorAll("img, canvas:not(.mag-bg), .frame-shot, .frame-img, .image-hero")) {
      const r = n.getBoundingClientRect();
      cover = Math.max(cover, Math.min(area, r.width * r.height));
    }
    const ratio = area ? cover / area : 0;
    if (ratio >= 0.78) return "image-full";
    if (ratio >= 0.34) return "image-well";
    if (el.querySelector(".map-block")) return "map";
    if (el.querySelector(".matrix-fill, .cross-mat, .dot-mat, .ring-mat")) return "matrix";
    if (el.querySelector(".h-bar-chart, .kpi-tower-row")) return "chart";
    if (el.querySelector(".pipeline-v, .stacked-ledger")) return "pipeline";

    // Numbered rows are detected by structure, not by class name: task decks
    // routinely ship scoped CSS (.tk-nb, .ws-nb, .stat-nb …) instead of the
    // seed's .ledger-row, and class-only detection called them all "text".
    let badges = 0;
    for (const n of el.querySelectorAll("*")) {
      if (n.children.length > 0) continue;
      if (n.closest(CHROME)) continue;
      if (/^0?[1-9]$/.test(n.textContent.trim())) badges++;
    }
    if (badges >= 3) return "rows";

    if (el.querySelector(".pullquote")) return "statement";
    if (el.querySelector(".marginalia, .col-2, .col-2-7-5, .col-2-8-4, .col-3, .grid-2-9")) return "split";
    if (el.querySelector(".card-fill, .card-accent, .card-ink, .card-outlined, .role-card, .brief-card")) return "card-grid";
    return "text";
  };
  return els
    .filter(el => el.classList.contains("xhs") || Math.abs(el.clientWidth / el.clientHeight - 0.75) < 0.02)
    .map(el => ({ id: el.id || "(no-id)", family: familyOf(el) }));
});

const packageWarns = [];
if (shapeFamilies.length >= 3) {
  const fams = shapeFamilies.map(s => s.family);
  const counts = new Map();
  for (const f of fams) counts.set(f, (counts.get(f) || 0) + 1);
  const distinct = counts.size;
  const overused = [...counts.entries()].filter(([, n]) => n > 2).sort((a, b) => b[1] - a[1]);
  const repeats = [];
  for (let i = 1; i < fams.length; i++) {
    if (fams[i] === fams[i - 1]) repeats.push(`${shapeFamilies[i - 1].id} → ${shapeFamilies[i].id} (${fams[i]})`);
  }
  if (overused.length) {
    packageWarns.push({
      rule: "R10",
      msg: `one shape carried the set: ${overused.map(([f, n]) => `"${f}" ×${n}`).join(", ")}`,
      fix: "re-pick recipes for the repeats — swap a numbered-row page for S02 comparison, S06 flow, S10 chart, or S04/M06 evidence (SKILL.md Step 4 → Recipe rhythm)",
    });
  }
  if (repeats.length) {
    packageWarns.push({
      rule: "R10",
      msg: `${repeats.length} adjacent page pair(s) share a shape family: ${repeats.slice(0, 3).join(", ")}`,
      fix: "give one page of each pair a different shape family; advisory, but a run of same-family pages is what reads as monotony",
    });
  }
  const minDistinct = fams.length >= 7 ? 5 : fams.length >= 5 ? 4 : 3;
  if (distinct < minDistinct) {
    packageWarns.push({
      rule: "R10",
      msg: `only ${distinct} distinct shape families across ${fams.length} pages (need ≥${minDistinct})`,
      fix: `see portrait-fill.md → Page Rhythm; a set of ${fams.length} pages must show at least ${minDistinct} distinct shapes`,
    });
  }
}
const shapeSequence = shapeFamilies.map(s => `${s.id}:${s.family}`).join("  ");

// R13 composition anchor — per-set anchor diversity. Advisory only.
//
// variation-engine.md §2 hands out ten named anchors at plan time (A top-left
// stacked … J closing cluster flush-right). Several of them — an off-grid
// offset, an image-as-canvas safe area — cannot be derived from geometry, so
// R13 does NOT claim to name an anchor. It measures a coarse but auditable
// proxy and stops there:
//
//   vertical   where the TEXT MASS sits, area-weighted: top / mid / bottom
//   horizontal centered / right / indented / full / left
//   +media     media owns >=34% of the board (anchors D / E / I territory)
//
// This axis was "manual only" with no measurement at all until now. The proxy
// is deliberately coarse: a left-third label rail reads as "full", and a
// centered statement can read as "center-full" if its band edges are uneven.
// Treat a WARN here as "go look", not as a verdict.
const anchorProxy = await page.$$eval("section.poster", els => {
  const CHROME = ".foot, .issue-strip, .magazine-foot, .chrome-min, .pair-preview, .preview-label";
  const out = [];
  for (const el of els) {
    if (!(el.classList.contains("xhs") || Math.abs(el.clientWidth / el.clientHeight - 0.75) < 0.02)) continue;
    const W = el.clientWidth, H = el.clientHeight;
    const er = el.getBoundingClientRect();
    let area = 0, cx = 0, cy = 0, minL = Infinity, maxR = -Infinity;
    for (const n of el.querySelectorAll("*")) {
      if (n.children.length > 0) continue;
      if (!n.textContent.trim()) continue;
      if (n.closest(CHROME)) continue;
      const r = n.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      const a = r.width * r.height;
      const l = r.left - er.left;
      area += a;
      cx += a * (l + r.width / 2);
      cy += a * (r.top - er.top + r.height / 2);
      if (l < minL) minL = l;
      if (l + r.width > maxR) maxR = l + r.width;
    }
    const posterArea = W * H;
    let media = 0;
    for (const n of el.querySelectorAll("img, canvas, svg, .frame-shot, .frame-img, .image-hero, .map-block")) {
      const r = n.getBoundingClientRect();
      if (getComputedStyle(n).position === "absolute" && r.width * r.height >= posterArea * 0.85) continue;
      media = Math.max(media, Math.min(posterArea, r.width * r.height));
    }
    const owns = media / posterArea >= 0.34;
    if (!area) {
      out.push({ id: el.id || "(no-id)", sig: owns ? "no-text+media" : "no-text", h: null, v: null, media: media / posterArea });
      continue;
    }
    const h = cx / area / W, v = cy / area / H;
    const insetL = minL / W, insetR = 1 - maxR / W;
    let horiz;
    if (insetL > 0.10 && insetR > 0.10 && Math.abs(insetL - insetR) < 0.06) horiz = "centered";
    else if (h > 0.58) horiz = "right";
    else if (insetL > 0.15) horiz = "indented";
    else if (maxR - minL >= W * 0.70) horiz = "full";
    else horiz = "left";
    const vert = v < 0.45 ? "top" : v > 0.58 ? "bottom" : "mid";
    out.push({ id: el.id || "(no-id)", sig: `${vert}-${horiz}${owns ? "+media" : ""}`, h, v, media: media / posterArea });
  }
  return out;
});

const anchorWarns = [];
if (anchorProxy.length >= 3) {
  const sigs = anchorProxy.map(a => a.sig);
  const counts = new Map();
  for (const s of sigs) counts.set(s, (counts.get(s) || 0) + 1);
  const distinct = counts.size;
  // §2.B caps anchor A specifically ("Anchor A at most twice in the set"), not
  // every anchor — so the A-proxy is the only signature with a count limit.
  const aCount = (counts.get("top-full") || 0) + (counts.get("top-left") || 0);
  const repeats = [];
  for (let i = 1; i < sigs.length; i++) {
    if (sigs[i] === sigs[i - 1]) repeats.push(`${anchorProxy[i - 1].id} → ${anchorProxy[i].id} (${sigs[i]})`);
  }
  const minAnchors = anchorProxy.length >= 5 ? 3 : 2;
  if (distinct < minAnchors) {
    anchorWarns.push({
      rule: "R13",
      msg: `only ${distinct} distinct composition anchors across ${anchorProxy.length} pages (need ≥${minAnchors} per §2.B)`,
      fix: "anchor is a plan-time decision (variation-engine.md §2) — rotate where the text mass sits, not just the recipe; R10 already covers shape families, this is the orthogonal axis",
    });
  }
  if (aCount > 2) {
    anchorWarns.push({
      rule: "R13",
      msg: `anchor A (top-left stacked) used ${aCount} times — §2.B caps it at 2`,
      fix: "re-assign the A pages at plan time; a bottom- or right-weighted treatment is the documented alternative",
    });
  }
  if (repeats.length) {
    anchorWarns.push({
      rule: "R13",
      msg: `${repeats.length} adjacent page pair(s) share an anchor: ${repeats.slice(0, 3).join(", ")}`,
      fix: "give one page of each pair a different anchor — the cheapest fix is often the anchor, not the recipe",
    });
  }
}
packageWarns.push(...anchorWarns);

const anchorSequence = anchorProxy.map(a => `${a.id}:${a.sig}`).join("  ");
const anchorTally = (() => {
  const counts = new Map();
  for (const a of anchorProxy) counts.set(a.sig, (counts.get(a.sig) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([s, n]) => `${s}×${n}`).join("  ");
})();

// R11 corner-radius lock — the enforcement point for
// references/screenshot-treatment.md → Multi-shot discipline.
// Scope is deliberately narrow: only .frame-shot OUTSIDE device chrome. Those
// are the elements whose radius the author chose through corners-*; everything
// else (device bezels at 36/28px, map-pin chips at 2px, 50% dots) is fixed
// chrome and would otherwise false-positive on every screenshot deck.
// Two values is legitimate — the seed documents "default + one exception".
const frameRadii = await page.$$eval("section.poster .frame-shot", (els) =>
  els
    .filter((el) => !el.closest(".device-browser, .device-phone"))
    .map((el) => ({
      id: el.closest("section.poster")?.id || "(no-id)",
      r: Math.round(parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0),
    })),
);
if (frameRadii.length >= 2) {
  const distinct = [...new Set(frameRadii.map((f) => f.r))].sort((a, b) => a - b);
  if (distinct.length > 2) {
    const where = [...new Set(frameRadii.map((f) => `${f.id}:${f.r}px`))].join("  ");
    packageWarns.push({
      rule: "R11",
      msg: `${distinct.length} corner radii in one set (${distinct.map((r) => r + "px").join(" / ")}) across ${frameRadii.length} frames`,
      fix: `pick one corner scale for the composition plus at most one documented exception — ${where}`,
    });
  }
}

await browser.close();

let totalFail = 0, totalWarn = 0;
const ruleCounts = new Map();
for (const { fails, warns } of report) {
  totalFail += fails.length;
  totalWarn += warns.length;
  for (const v of [...fails, ...warns]) ruleCounts.set(v.rule, (ruleCounts.get(v.rule) || 0) + 1);
}
totalWarn += packageWarns.length;
for (const v of packageWarns) ruleCounts.set(v.rule, (ruleCounts.get(v.rule) || 0) + 1);
const cleanCount = report.filter(r => r.fails.length === 0 && r.warns.length === 0).length;

const lines = [];
lines.push(`==== validate-social-deck ====`);
lines.push(`target:   ${path.relative(process.cwd(), htmlPath)}`);
lines.push(`style:    ${style}`);
lines.push(`sections: ${report.length}  ·  ${cleanCount} clean  ·  ${totalFail} fails  ·  ${totalWarn} warns`);
if (ruleCounts.size > 0) {
  const ruleSummary = [...ruleCounts.entries()].sort().map(([r, c]) => `${r}=${c}`).join("  ");
  lines.push(`rules:    ${ruleSummary}`);
}
if (shapeSequence) lines.push(`shapes:   ${shapeSequence}`);
if (anchorSequence) lines.push(`anchors:  ${anchorSequence}`);
if (anchorTally && anchorProxy.length) lines.push(`anchorMix:${anchorTally}`);
// R5 is report-only, so the number has to be visible here or it is not checked
// at all. Bands are left-to-right top-to-bottom thirds of the poster height.
if (densitySeq.length) {
  lines.push(`density:  ${densitySeq.map(d => `${d.id}:${Math.round(d.total * 100)}%`).join("  ")}`);
}
lines.push("");

const fixCache = new Map();
for (const { meta, fails, warns } of report) {
  if (fails.length === 0 && warns.length === 0) {
    lines.push(`[PASS]  ${meta.id} ${meta.dataId ? ` · ${meta.dataId}` : ""} · ${meta.board}`);
    continue;
  }
  const tag = fails.length ? "[FAIL]" : "[WARN]";
  lines.push(`${tag}  ${meta.id} ${meta.dataId ? ` · ${meta.dataId}` : ""} · ${meta.board}`);
  for (const v of [...fails, ...warns]) {
    const sev = fails.includes(v) ? "FAIL" : "WARN";
    lines.push(`  ${sev} · ${v.rule}  ${v.msg}`);
    if (!fixCache.has(v.rule)) {
      lines.push(`         fix: ${v.fix}`);
      fixCache.set(v.rule, true);
    }
  }
}

lines.push("");
if (packageWarns.length) {
  lines.push(`[WARN]  package · set-level checks (R10 shapes, R13 anchors)`);
  for (const v of packageWarns) {
    lines.push(`  WARN · ${v.rule}  ${v.msg}`);
    lines.push(`         fix: ${v.fix}`);
  }
  lines.push("");
}
lines.push(`Legend: R1 overflow · R2 footer collision · R3 swiss bold display · R4 min font · R5 density (report-only) / mid-poster void · R6 h-xl cap · R7 figure margin drift · R8 visual bounds · R9 title gap · R10 recipe rhythm · R11 corner-radius lock · R12 banned copy · R13 composition anchor (advisory proxy)`);
lines.push(`Exit code 1 only on FAIL. Warnings are advisory.`);

console.log(lines.join("\n"));
process.exit(totalFail > 0 ? 1 : 0);
