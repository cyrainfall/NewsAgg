#!/usr/bin/env python3
"""
repo-flow-viz 渲染层 —— facts JSON → 单文件 HTML。

这一层**对仓库完全无知**：它不认识 NewsAgg，也不认识任何路径。所有内容、
分组、统计都来自 facts JSON。同一个 renderer 给十个仓库产出十个页面，
代码一行不用改。

用法：
    python3 render_html.py facts.json [-o out.html]

facts.json 的 schema 见 ../references/facts-schema.md。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 工作过程可视化</title>
<style>
:root{
  --bg:#0d1117; --bg2:#12181f; --panel:#161b22; --panel2:#1c232b;
  --line:#2b333d; --line2:#3a444f;
  --fg:#e6edf3; --fg2:#9aa7b4; --fg3:#6e7b8a;
  --accent:#4a9eff; --accent2:#7ee787;
  --warn:#d29922; --danger:#f85149; --purple:#bc8cff; --pink:#ff7b9c;
  --mono:'SF Mono',ui-monospace,'JetBrains Mono',Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:14px;line-height:1.6;overflow:hidden}
#app{display:grid;grid-template-rows:auto 1fr;height:100vh}
header{
  display:flex;align-items:center;gap:18px;padding:10px 18px;
  background:var(--bg2);border-bottom:1px solid var(--line);flex-wrap:wrap;
}
header h1{font-size:15px;font-weight:600;letter-spacing:.02em;white-space:nowrap}
header h1 span{color:var(--accent)}
.sub{font-size:11.5px;color:var(--fg3);white-space:nowrap}
.spacer{flex:1}
.stats{display:flex;gap:14px;font-size:11.5px;color:var(--fg2);font-family:var(--mono);flex-wrap:wrap}
.stats b{color:var(--accent2);font-weight:600}
.searchbox{position:relative}
#search{
  background:var(--panel);border:1px solid var(--line2);color:var(--fg);
  padding:6px 10px 6px 28px;border-radius:6px;font-size:12.5px;width:230px;
  font-family:var(--sans);outline:none;
}
#search:focus{border-color:var(--accent)}
.searchbox svg{position:absolute;left:8px;top:50%;transform:translateY(-50%);opacity:.5}
main{display:grid;grid-template-columns:250px minmax(0,1fr) minmax(0,1.15fr);overflow:hidden;min-height:0}
.col{overflow-y:auto;overflow-x:hidden;min-height:0}
.col::-webkit-scrollbar{width:9px}
.col::-webkit-scrollbar-thumb{background:var(--line2);border-radius:5px}
.col::-webkit-scrollbar-track{background:transparent}
#rail{background:var(--bg2);border-right:1px solid var(--line)}
#detail{background:var(--bg);border-right:1px solid var(--line)}
#viewer{background:var(--panel)}
.group{padding:12px 14px 4px}
.group-t{
  font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--fg3);font-weight:600;margin-bottom:7px;
}
.node-i{
  display:grid;grid-template-columns:26px 1fr;gap:8px;align-items:start;
  padding:6px 8px;border-radius:6px;cursor:pointer;margin-bottom:2px;
  border:1px solid transparent;
}
.node-i:hover{background:var(--panel2)}
.node-i.on{background:var(--panel2);border-color:var(--line2)}
.node-i.hide{display:none}
.node-i .step{
  font-family:var(--mono);font-size:11px;color:var(--fg3);
  text-align:right;padding-top:1px;white-space:nowrap;
}
.node-i[data-layer=collect] .step{color:var(--accent)}
.node-i[data-layer=triage] .step{color:var(--purple)}
.node-i[data-layer=write] .step{color:var(--accent2)}
.node-i[data-layer=publish] .step{color:var(--pink)}
.node-i[data-layer=gate] .step{color:var(--warn)}
.node-i[data-layer=ci] .step{color:var(--fg2)}
.node-i[data-layer=sediment] .step{color:var(--purple)}
.node-i[data-layer=orchestrate] .step{color:var(--fg2)}
.node-i[data-layer=file] .step{color:var(--fg3)}
.file-i .nm{font-family:var(--mono);font-size:11.5px}
.node-i .nm{font-size:12.5px;line-height:1.35;word-break:break-word}
.node-i .nm small{display:block;color:var(--fg3);font-size:10.5px;font-family:var(--sans);margin-top:1px}
/* ── detail ── */
.d-head{padding:16px 20px 12px;border-bottom:1px solid var(--line);background:var(--bg2)}
.d-step{
  font-family:var(--mono);font-size:11px;color:var(--accent);
  letter-spacing:.08em;margin-bottom:3px;
}
.d-title{font-size:17px;font-weight:600;margin-bottom:8px;line-height:1.35}
.d-meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.tag{
  font-size:11px;padding:2px 7px;border-radius:4px;
  background:var(--panel2);color:var(--fg2);border:1px solid var(--line);
}
.tag.who{color:var(--accent2)}
.tag.when{color:var(--warn)}
.d-one{font-size:13px;color:var(--fg2);line-height:1.65}
.d-body{padding:14px 20px 40px}
.sec{
  font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--fg3);
  font-weight:600;margin:16px 0 7px;padding-bottom:4px;border-bottom:1px solid var(--line);
}
ul.list{list-style:none}
ul.list li{
  position:relative;padding-left:15px;margin-bottom:6px;
  font-size:12.5px;line-height:1.65;color:var(--fg);
}
ul.list li::before{content:'·';position:absolute;left:3px;color:var(--accent);font-weight:700}
ul.list.why li::before{content:'?';color:var(--fg3)}
ul.list.why li{color:var(--fg2)}
.adr{
  font-family:var(--mono);font-size:11px;color:var(--warn);
  background:rgba(210,153,34,.12);padding:1px 4px;border-radius:3px;
}
.fgroup{margin-top:10px}
.fg-t{
  font-size:11.5px;color:var(--fg2);margin-bottom:6px;
  display:flex;align-items:center;gap:6px;
}
.cnt{font-family:var(--mono);font-size:10.5px;color:var(--fg3)}
.fcard{
  border:1px solid var(--line);border-radius:6px;padding:7px 9px;margin-bottom:5px;
  cursor:pointer;background:var(--bg2);transition:border-color .12s,background .12s;
}
.fcard:hover{border-color:var(--line2);background:var(--panel2)}
.fcard.on{border-color:var(--accent);background:var(--panel2)}
.fc-top{display:flex;align-items:center;gap:6px}
.fc-ico{font-size:9px;color:var(--fg3);width:10px;flex-shrink:0}
.fc-path{
  font-family:var(--mono);font-size:11px;flex:1;
  word-break:break-all;line-height:1.4;
}
.fc-lines{color:var(--accent);font-family:var(--mono);font-size:11px}
.badge-rw{
  font-size:9.5px;padding:1px 5px;border-radius:3px;flex-shrink:0;
  font-family:var(--mono);
}
.badge-rw.r{background:rgba(74,158,255,.15);color:var(--accent)}
.badge-rw.w{background:rgba(126,231,135,.15);color:var(--accent2)}
.fc-note{font-size:11px;color:var(--fg3);margin-top:3px;padding-left:16px;line-height:1.45}
/* ── viewer ── */
.v-empty{
  height:100%;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:8px;color:var(--fg3);font-size:12.5px;text-align:center;padding:20px;
}
.v-empty kbd{
  font-family:var(--mono);font-size:10.5px;background:var(--panel2);
  border:1px solid var(--line2);border-radius:3px;padding:1px 5px;
}
.v-head{padding:12px 16px;border-bottom:1px solid var(--line);background:var(--bg2);position:sticky;top:0;z-index:2}
.v-path{font-family:var(--mono);font-size:12px;color:var(--accent);word-break:break-all;margin-bottom:5px}
.v-note{font-size:12px;color:var(--fg2);line-height:1.55;margin-bottom:6px}
.v-tags{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:5px}
.v-src{font-size:10.5px;color:var(--fg3);line-height:1.5}
pre.code{
  font-family:var(--mono);font-size:11.5px;line-height:1.65;
  padding:10px 0 40px;overflow-x:auto;
}
pre.code .row{display:flex;padding:0 12px;white-space:pre}
pre.code .row:hover{background:rgba(255,255,255,.03)}
pre.code .row.hl{background:rgba(210,153,34,.13)}
pre.code .ln{
  width:52px;flex-shrink:0;text-align:right;padding-right:12px;
  color:var(--fg3);user-select:none;opacity:.65;
}
pre.code .tx{white-space:pre;color:var(--fg)}
pre.code .hit{background:rgba(210,153,34,.32);border-radius:2px;color:#fff}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>__TITLE__ · <span>工作过程可视化</span></h1>
    <div class="sub" id="metaline"></div>
    <div class="spacer"></div>
    <div class="stats" id="statsline"></div>
    <div class="searchbox">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M11.7 10.3a6 6 0 1 0-1.4 1.4l3.3 3.3 1.4-1.4-3.3-3.3ZM7 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z"/></svg>
      <input id="search" placeholder="搜索环节 / 文件 / 内容…" autocomplete="off">
    </div>
  </header>
  <main>
    <section class="col" id="rail"></section>
    <section class="col" id="detail"></section>
    <section class="col" id="viewer"></section>
  </main>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const DATA = JSON.parse(document.getElementById('payload').textContent);
const NODES = DATA.nodes, META = DATA.meta || {}, GROUPS = DATA.groups || [];
const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const hlRef = s => esc(s).replace(/(ADR-\d{4})/g, '<span class="adr">$1</span>');

// 分组完全由 facts 决定：没写 groups 就按 nodes 原顺序平铺成一组。
// 这样事实层可以表达"白天已跑过三次"这种时间倒错的真实顺序，
// 而不必迁就代码里的声明顺序。
const G = GROUPS.length ? GROUPS : [{title: '执行流', ids: NODES.map(n => n.id)}];
const byId = new Map(NODES.map(n => [n.id, n]));
const placed = new Set();

document.title = (META.title || 'Repo') + ' · 工作过程可视化';
document.getElementById('metaline').textContent =
  `${META.date || '—'} 实例 · 摘录实读自仓库，行号为真实行号`;
document.getElementById('statsline').innerHTML =
  (META.stats || []).map(([k, v]) => `${esc(k)} <b>${esc(String(v))}</b>`).join(' · ');

// ── rail ──────────────────────────────────────────────────
const rail = document.getElementById('rail');
const order = [];
G.forEach(g => {
  const ns = g.ids.map(id => byId.get(id)).filter(Boolean);
  ns.forEach(n => { order.push(n.id); placed.add(n.id); });
  if (!ns.length) return;
  const box = document.createElement('div');
  box.className = 'group';
  box.innerHTML = `<div class="group-t">${esc(g.title)}</div>`;
  ns.forEach(n => box.appendChild(nodeEl(n)));
  rail.appendChild(box);
});
// 没被任何 group 收走的节点单独兜底 —— 漏配分组不该让节点凭空消失
const orphans = NODES.filter(n => !placed.has(n.id));
if (orphans.length) {
  const box = document.createElement('div');
  box.className = 'group';
  box.innerHTML = `<div class="group-t">其他环节</div>`;
  orphans.forEach(n => box.appendChild(nodeEl(n)));
  rail.appendChild(box);
}

function hay(n) {
  return [n.step, n.title, n.one_liner, n.actor, n.when,
    ...(n.does || []), ...(n.why || [])].filter(Boolean).join(' ').toLowerCase();
}
function nodeEl(n) {
  const d = document.createElement('div');
  d.className = 'node-i';
  d.dataset.layer = n.layer || '';
  d.dataset.id = n.id;
  d.dataset.search = hay(n);
  d.innerHTML = `<div class="step">${esc(n.step || '·')}</div>
    <div class="nm">${esc(n.title)}<small>${esc((n.when || '').split('（')[0])}</small></div>`;
  d.onclick = () => select(n.id);
  return d;
}

// ── 文件索引（反向视图：这个文件被谁用）────────────────────
// 正向视图回答"这一环节读了什么"，反向视图回答"这个文件归谁管"。
// 后者在排查"改这里会影响什么"时才真正被需要，成本却只是多建一个 Map。
const FILEMAP = new Map();
NODES.forEach(n => {
  [...(n.reads || []), ...(n.writes || []), ...(n.files || [])].forEach(f => {
    if (!FILEMAP.has(f.path)) FILEMAP.set(f.path, {file: f, nodes: []});
    const e = FILEMAP.get(f.path);
    if (!e.nodes.includes(n.id)) e.nodes.push(n.id);
  });
});
const FILELIST = [...FILEMAP.entries()].sort((a, b) => a[0].localeCompare(b[0]));
(function () {
  const box = document.createElement('div');
  box.className = 'group';
  box.innerHTML = `<div class="group-t">文件索引 · ${FILELIST.length} 个文件</div>`;
  FILELIST.forEach(([path, e]) => {
    const d = document.createElement('div');
    d.className = 'node-i file-i';
    d.dataset.layer = 'file';
    d.dataset.path = path;
    d.dataset.search = path.toLowerCase();
    const base = path.split('/').pop();
    const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
    d.innerHTML = `<div class="step">◦</div>
      <div class="nm">${esc(base)}<small>${esc(dir)} · ${e.nodes.length} 处引用</small></div>`;
    d.onclick = () => {
      document.querySelectorAll('.node-i').forEach(x => x.classList.remove('on'));
      d.classList.add('on');
      openFile(e.file);
    };
    box.appendChild(d);
  });
  rail.appendChild(box);
})();

// ── detail ────────────────────────────────────────────────
const detail = document.getElementById('detail');
let current = null;

function fileCard(f, rw, key) {
  const where = f.start
    ? `${f.path}<span class="fc-lines">:${f.start}${f.end !== f.start ? '-' + f.end : ''}</span>`
    : `${f.path}<span class="fc-lines">· ${f.loc ? f.loc.split(' / ')[0] : '结构化'}</span>`;
  return `<div class="fcard" data-key="${key}">
    <div class="fc-top">
      <span class="fc-ico">${rw === 'r' ? '◂' : rw === 'w' ? '▸' : '·'}</span>
      <span class="fc-path">${esc(where)}</span>
      ${rw === '-' ? '' : `<span class="badge-rw ${rw}">${rw === 'r' ? '读' : '写'}</span>`}
    </div>
    <div class="fc-note">${esc(f.note || '')}</div>
  </div>`;
}

function renderDetail(id) {
  const n = byId.get(id);
  if (!n) return;
  current = n;
  // 给每张卡发一个序号当身份，避免后面按 path 找回时抓错同一文件的另一段
  const pool = [];
  const groupsHtml = ['r', 'w'].map(rw => {
    const arr = (rw === 'r' ? (n.reads || []) : (n.writes || []));
    if (!arr.length) return '';
    const cards = arr.map(f => {
      pool.push(f);
      return fileCard(f, rw, pool.length - 1);
    }).join('');
    return `<div class="fgroup"><div class="fg-t">${rw === 'r' ? '依赖的输入' : '产出的输出'}
      <span class="cnt">${arr.length} 项</span></div>${cards}</div>`;
  }).join('');
  const exCards = (n.files || []).map(f => {
    pool.push(f);
    return fileCard(f, '-', pool.length - 1);
  }).join('');

  detail.innerHTML = `
    <div class="d-head">
      <div class="d-step">步骤 ${esc(n.step || '·')}</div>
      <div class="d-title">${esc(n.title)}</div>
      <div class="d-meta">
        ${n.actor ? `<span class="tag who">${esc(n.actor)}</span>` : ''}
        ${n.when ? `<span class="tag when">${esc(n.when)}</span>` : ''}
      </div>
      ${n.one_liner ? `<div class="d-one">${esc(n.one_liner)}</div>` : ''}
    </div>
    <div class="d-body">
      ${(n.does || []).length ? `<h3 class="sec">这一步做了什么</h3>
        <ul class="list">${n.does.map(d => `<li>${hlRef(d)}</li>`).join('')}</ul>` : ''}
      ${(n.why || []).length ? `<h3 class="sec">为什么这样设计</h3>
        <ul class="list why">${n.why.map(d => `<li>${hlRef(d)}</li>`).join('')}</ul>` : ''}
      ${groupsHtml}
      ${(n.files || []).length ? `<div class="fgroup">
        <div class="fg-t">点开看原文 <span class="cnt">${n.files.length} 段摘录</span></div>
        ${exCards}</div>` : ''}
    </div>`;
  detail.querySelectorAll('.fcard').forEach(c => c.onclick = () => {
    const f = pool[+c.dataset.key];
    openFile(f);
    detail.querySelectorAll('.fcard').forEach(x => x.classList.toggle('on', x === c));
  });
  detail.scrollTop = 0;
  document.querySelectorAll('.node-i').forEach(x => x.classList.toggle('on', x.dataset.id === id));
}

// ── viewer ────────────────────────────────────────────────
const viewer = document.getElementById('viewer');
function renderCode(text, startLine, hits) {
  return text.split('\n').map((t, i) => {
    const ln = startLine ? startLine + i : null;
    let tx = esc(t);
    hits.forEach(h => {
      const re = new RegExp('(' + h.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&') + ')', 'gi');
      tx = tx.replace(re, '<span class="hit">$1</span>');
    });
    return `<div class="row${hits.length && tx.includes('class="hit"') ? ' hl' : ''}">
      <span class="ln">${ln ?? ''}</span><span class="tx">${tx}</span></div>`;
  }).join('');
}
function openFile(f) {
  const hits = searchTerm ? [searchTerm] : [];
  const isRaw = f.start !== null && f.start !== undefined;
  const entry = FILEMAP.get(f.path);
  const refs = entry ? entry.nodes
    .map(id => byId.get(id))
    .filter(Boolean)
    .map(n => `${n.step} ${n.title}`) : [];
  viewer.className = 'col open';
  viewer.innerHTML = `
    <div class="v-head">
      <div class="v-path">${esc(f.path)}</div>
      ${f.note ? `<div class="v-note">${hlRef(f.note)}</div>` : ''}
      <div class="v-tags">
        <span class="tag">${isRaw
          ? `第 ${f.start}–${f.end} 行 / 共 ${f.total} 行`
          : `结构化视图 · 共 ${f.total} 行`}</span>
        ${isRaw && f.total ? `<span class="tag">偏移 ${((f.start - 1) / f.total * 100).toFixed(1)}%</span>` : ''}
      </div>
      <div class="v-src">来源：仓库真实文件，由 ${esc(META.source_script || 'build_facts.py')} 切片${
        refs.length ? ' ｜ 被 ' + refs.length + ' 个环节引用：' + refs.join('、') : ''}</div>
    </div>
    <pre class="code">${renderCode(f.text, isRaw ? f.start : 1, hits)}</pre>`;
  viewer.scrollTop = 0;
  if (hits.length) {
    const h = viewer.querySelector('.row.hl');
    if (h) h.scrollIntoView({block: 'center'});
  }
}
viewer.innerHTML = `<div class="v-empty">
  <div style="font-size:32px;opacity:.35">◲</div>
  <div>点任一文件卡片，这里显示它的真实原文</div>
  <div><kbd>←</kbd> <kbd>→</kbd> 切换环节 · <kbd>/</kbd> 搜索</div>
</div>`;

// ── search ────────────────────────────────────────────────
let searchTerm = '';
const searchBox = document.getElementById('search');
searchBox.addEventListener('input', () => {
  searchTerm = searchBox.value.trim().toLowerCase();
  filter();
});
function filter() {
  const t = searchTerm;
  document.querySelectorAll('.node-i').forEach(el => {
    if (el.classList.contains('file-i')) {
      const e = FILEMAP.get(el.dataset.path);
      const body = e ? (e.file.path + ' ' + (e.file.note || '') + ' ' + e.file.text).toLowerCase() : '';
      el.classList.toggle('hide', !!t && !body.includes(t));
      return;
    }
    const n = byId.get(el.dataset.id);
    if (!n) return;
    const body = hay(n) + ' ' + [...(n.reads || []), ...(n.writes || []), ...(n.files || [])]
      .map(f => f.path + ' ' + (f.note || '') + ' ' + f.text).join(' ').toLowerCase();
    el.classList.toggle('hide', !!t && !body.includes(t));
  });
  if (!t) return;
  // 搜到内容时自动跳到第一个真正命中**正文**的片段：
  // 用户搜的是现象，他想看的是代码里那一行，不是"哪个环节提到了这个词"
  for (const n of NODES) {
    const f = [...(n.reads || []), ...(n.writes || []), ...(n.files || [])]
      .find(f => f.text.toLowerCase().includes(t));
    if (f) { select(n.id); openFile(f); break; }
  }
}
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== searchBox) { e.preventDefault(); searchBox.focus(); }
  if (e.key === 'Escape') { searchBox.value = ''; searchTerm = ''; filter(); searchBox.blur(); }
  if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && document.activeElement !== searchBox && current) {
    const i = order.indexOf(current.id);
    if (i < 0) return;
    const j = (i + (e.key === 'ArrowRight' ? 1 : -1) + order.length) % order.length;
    e.preventDefault(); select(order[j]);
  }
});

function select(id) {
  renderDetail(id);
  const el = document.querySelector(`.node-i[data-id="${id}"]`);
  if (el) el.scrollIntoView({block: 'nearest'});
}
select(order[0] || NODES[0].id);
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="facts JSON → 单文件 HTML")
    ap.add_argument("facts", help="facts JSON 路径")
    ap.add_argument("-o", "--out", help="输出 HTML 路径（默认与 facts 同目录的 index.html）")
    args = ap.parse_args()

    facts_path = Path(args.facts).resolve()
    if not facts_path.exists():
        print(f"[FATAL] 找不到 facts：{facts_path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(facts_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[FATAL] facts 不是合法 JSON：{e}", file=sys.stderr)
        return 1

    nodes = data.get("nodes")
    if not nodes:
        print("[FATAL] facts 里没有 nodes —— 什么也画不出来", file=sys.stderr)
        return 1

    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        print(f"[FATAL] node id 重复：{dupes}", file=sys.stderr)
        return 1
    for n in nodes:
        for miss in ("id", "title"):
            if not n.get(miss):
                print(f"[FATAL] 有节点缺 {miss}：{n}", file=sys.stderr)
                return 1

    # 分组里引用了不存在的 id 就报错而不是静默丢弃 ——
    # 拼错一个 id 会让整个分组空掉，页面上表现为"这个阶段没事发生"
    for g in data.get("groups", []):
        for i in g.get("ids", []):
            if i not in ids:
                print(f"[FATAL] 分组「{g.get('title')}」引用了不存在的节点 id：{i}", file=sys.stderr)
                return 1

    payload = json.dumps(data, ensure_ascii=False)
    if "</script" in payload:
        print("[FATAL] payload 里含 </script，会截断页面", file=sys.stderr)
        return 1
    html = HTML.replace("__PAYLOAD__", payload).replace(
        "__TITLE__", (data.get("meta") or {}).get("title", "Repo"))

    out = Path(args.out).resolve() if args.out else facts_path.parent / "index.html"
    out.write_text(html, encoding="utf-8")
    nfiles = len({f["path"] for n in nodes
                  for k in ("reads", "writes", "files")
                  for f in (n.get(k) or [])})
    print(f"OK {out}  {out.stat().st_size / 1024:.0f} KB  "
          f"节点 {len(nodes)} · 文件 {nfiles}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
