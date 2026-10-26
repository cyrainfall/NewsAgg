#!/usr/bin/env python3
"""把 viz/facts/repo-facts.json 渲染成单文件 HTML。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTS = Path(__file__).resolve().parent / "repo-facts.json"
OUT = Path(__file__).resolve().parent / "index.html"

data = json.loads(FACTS.read_text(encoding="utf-8"))
PAYLOAD = json.dumps(data, ensure_ascii=False)

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NewsAgg 工作过程可视化</title>
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
body{
  background:var(--bg);color:var(--fg);font-family:var(--sans);
  font-size:14px;line-height:1.6;overflow:hidden;
}
/* ── layout ── */
#app{display:grid;grid-template-rows:auto 1fr;height:100vh}
header{
  display:flex;align-items:center;gap:18px;padding:10px 18px;
  background:var(--bg2);border-bottom:1px solid var(--line);flex-wrap:wrap;
}
header h1{font-size:15px;font-weight:600;letter-spacing:.02em;white-space:nowrap}
header h1 span{color:var(--accent)}
.sub{font-size:11.5px;color:var(--fg3);white-space:nowrap}
.spacer{flex:1}
.stats{display:flex;gap:14px;font-size:11.5px;color:var(--fg2);font-family:var(--mono)}
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
/* ── column common ── */
.col{overflow-y:auto;overflow-x:hidden;min-height:0}
.col::-webkit-scrollbar{width:9px}
.col::-webkit-scrollbar-thumb{background:var(--line2);border-radius:5px}
.col::-webkit-scrollbar-track{background:transparent}
#rail{background:var(--bg2);border-right:1px solid var(--line)}
#detail{background:var(--bg);border-right:1px solid var(--line)}
#viewer{background:var(--panel)}
/* ── rail ── */
.group{padding:12px 14px 4px}
.group-t{
  font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--fg3);font-weight:600;margin-bottom:7px;
}
.node-i{
  display:flex;gap:9px;align-items:flex-start;padding:7px 9px;border-radius:6px;
  cursor:pointer;border:1px solid transparent;margin-bottom:2px;transition:.12s;
}
.node-i:hover{background:var(--panel2)}
.node-i.on{background:#1f2b3a;border-color:var(--accent)}
.node-i.hide{display:none}
.step{
  font-family:var(--mono);font-size:11px;color:var(--accent);
  min-width:26px;text-align:center;padding-top:1px;font-weight:600;flex-shrink:0;
}
.node-i[data-layer=gate] .step{color:var(--warn)}
.node-i[data-layer=ci] .step{color:var(--purple)}
.node-i[data-layer=sediment] .step{color:var(--accent2)}
.node-i[data-layer=publish] .step{color:var(--pink)}
.node-i[data-layer=file] .step{color:var(--fg3)}
.file-i .nm{font-family:var(--mono);font-size:11.5px}
.nm{font-size:12.5px;line-height:1.4}
.nm small{display:block;color:var(--fg3);font-size:10.5px;font-family:var(--mono)}
/* ── detail ── */
#detail{padding:0}
.d-head{padding:20px 24px 14px;border-bottom:1px solid var(--line);background:var(--bg2)}
.d-step{font-family:var(--mono);font-size:12px;color:var(--accent);font-weight:600}
.d-title{font-size:20px;font-weight:600;margin:4px 0 8px;letter-spacing:.01em}
.d-meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.tag{
  font-size:11px;padding:2px 8px;border-radius:20px;background:var(--panel2);
  border:1px solid var(--line2);color:var(--fg2);font-family:var(--mono);
}
.tag.who{color:var(--accent2);border-color:#2d5a35;background:#14240f}
.tag.when{color:var(--warn);border-color:#5c4813;background:#241d0a}
.d-one{
  font-size:14px;color:var(--fg);background:var(--panel);border-left:3px solid var(--accent);
  padding:10px 14px;border-radius:0 6px 6px 0;
}
.d-body{padding:18px 24px 60px}
h3.sec{
  font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--fg3);
  margin:22px 0 9px;font-weight:600;
}
h3.sec:first-child{margin-top:0}
ul.list{list-style:none}
ul.list li{
  position:relative;padding:5px 0 5px 16px;font-size:13.2px;color:var(--fg);
}
ul.list li::before{content:'▸';position:absolute;left:0;color:var(--accent);font-size:10px;top:7px}
ul.why li::before{content:'∵';color:var(--warn)}
ul.why li{color:var(--fg2);font-size:12.6px}
.adr{color:var(--purple);font-family:var(--mono);font-size:11.5px}
/* ── file cards ── */
.fgroup{margin-bottom:20px}
.fg-t{
  display:flex;align-items:center;gap:8px;font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--fg3);font-weight:600;margin-bottom:8px;
}
.fg-t .cnt{font-family:var(--mono);color:var(--fg3);text-transform:none;letter-spacing:0}
.fcard{
  background:var(--panel);border:1px solid var(--line);border-radius:7px;
  padding:9px 12px;margin-bottom:7px;cursor:pointer;transition:.12s;
}
.fcard:hover{border-color:var(--accent);background:var(--panel2)}
.fcard.on{border-color:var(--accent);background:#1a2534}
.fc-top{display:flex;align-items:baseline;gap:8px}
.fc-ico{color:var(--fg3);font-size:11px;font-family:var(--mono);flex-shrink:0}
.fc-path{
  font-family:var(--mono);font-size:12px;color:var(--accent2);word-break:break-all;
  flex:1;
}
.fc-lines{font-family:var(--mono);font-size:10.5px;color:var(--fg3);flex-shrink:0}
.fc-note{font-size:11.5px;color:var(--fg2);margin-top:4px;padding-left:19px}
.badge-rw{font-size:10px;padding:1px 6px;border-radius:3px;font-family:var(--mono)}
.badge-rw.r{background:#3b1d1d;color:#ff9d95}
.badge-rw.w{background:#1d3320;color:#8ee57f}
/* ── viewer ── */
.v-empty{
  height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;
  color:var(--fg3);gap:10px;padding:40px;text-align:center;
}
.v-empty kbd{
  font-family:var(--mono);background:var(--panel2);border:1px solid var(--line2);
  padding:2px 7px;border-radius:4px;font-size:11px;color:var(--fg2);
}
.v-head{
  padding:14px 18px;background:var(--bg2);border-bottom:1px solid var(--line);
  position:sticky;top:0;z-index:2;
}
.v-path{font-family:var(--mono);font-size:12.5px;color:var(--accent2);word-break:break-all}
.v-note{font-size:12px;color:var(--fg2);margin-top:5px}
.v-tags{display:flex;gap:6px;margin-top:7px;flex-wrap:wrap}
.v-src{font-family:var(--mono);font-size:10.5px;color:var(--fg3);margin-top:5px}
pre.code{
  font-family:var(--mono);font-size:12px;line-height:1.62;
  padding:14px 0 40px;overflow-x:auto;
}
pre.code .ln{
  display:inline-block;width:52px;text-align:right;padding-right:14px;
  color:#4a5563;user-select:none;flex-shrink:0;
}
pre.code .row{display:flex;white-space:pre}
pre.code .row:hover{background:#1b2430}
pre.code .row.hl{background:#1c2f44}
pre.code .tx{flex:1;padding-right:20px}
.hit{background:#5c4a12;color:#ffdf9e;border-radius:2px}
@media(max-width:1200px){
  main{grid-template-columns:230px 1fr}
  #viewer{position:fixed;inset:0;background:var(--panel);z-index:50;transform:translateX(100%);transition:.2s}
  #viewer.open{transform:none}
}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>NewsAgg · <span>工作过程可视化</span></h1>
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
const NODES = DATA.nodes, META = DATA.meta, STATS = DATA.stats;
const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const hlADR = s => esc(s).replace(/ADR-\d{4}/g, m => `<span class="adr">${m}</span>`);

const GROUPS = [
  ['主流程第 1 段 · 21:00 起', n => ['n01-pull','n02-style','n03-performance','n04-skipday'].includes(n.id)],
  ['在此之前 · 白天已跑三次（步骤 ③）', n => ['collect'].includes(n.layer)],
  ['第 1→2 段之间 · 模型判断', n => n.id === 'n07-triage-judge'],
  ['主流程第 2 段 · 写作', n => ['n06-triage-input','n08-brief-pack','n09-write'].includes(n.id)],
  ['主流程第 3 段 · 发布结算', n => ['n10-cover','n11-publish','n12-writeback','n13-brief-push'].includes(n.id)],
  ['异常与外围', n => ['n14-failsoft','n15-ci'].includes(n.id)],
];
const LAYERS = ['gate','ci','sediment','publish','collect','triage','write','publish-prep','orchestrate'];

// ── header ────────────────────────────────────────────────
document.getElementById('metaline').textContent =
  `${META.date} 实例 · 摘录实读自仓库，行号为真实行号`;
document.getElementById('statsline').innerHTML = [
  ['ADR', STATS.adr], ['测试', STATS.tests], ['观点卡', STATS.cards],
  ['成稿', STATS.outputs], ['简报', STATS.reports],
  ['发布', STATS.real_publishes], ['误发', STATS.accidental],
].map(([k,v]) => `${k} <b>${v}</b>`).join(' · ');

// ── rail ──────────────────────────────────────────────────
const rail = document.getElementById('rail');
const order = [];
GROUPS.forEach(([title, pred]) => {
  const ns = NODES.filter(pred);
  ns.forEach(n => order.push(n.id));
  if (!ns.length) return;
  const g = document.createElement('div');
  g.className = 'group';
  g.innerHTML = `<div class="group-t">${title}</div>`;
  ns.forEach(n => {
    const d = document.createElement('div');
    d.className = 'node-i';
    d.dataset.layer = n.layer;
    d.dataset.id = n.id;
    d.dataset.search = (n.step + ' ' + n.title + ' ' + n.one_liner + ' ' + n.does.join(' ') + ' ' + n.why.join(' ')).toLowerCase();
    d.innerHTML = `<div class="step">${esc(n.step)}</div>
      <div class="nm">${esc(n.title)}<small>${esc(n.when.split('（')[0])}</small></div>`;
    d.onclick = () => select(n.id);
    g.appendChild(d);
  });
  rail.appendChild(g);
});

// ── file index (rail bottom) ──────────────────────────────
const FILEMAP = new Map();   // path -> {file, nodes:[]}
NODES.forEach(n => {
  [...n.reads, ...n.writes, ...n.files].forEach(f => {
    if (!FILEMAP.has(f.path)) FILEMAP.set(f.path, {file: f, nodes: []});
    const e = FILEMAP.get(f.path);
    if (!e.nodes.includes(n.id)) e.nodes.push(n.id);
  });
});
const FILELIST = [...FILEMAP.entries()].sort((a,b) => a[0].localeCompare(b[0]));
(function buildFileIndex(){
  const g = document.createElement('div');
  g.className = 'group';
  g.innerHTML = `<div class="group-t">文件索引 · ${FILELIST.length} 个文件</div>`;
  FILELIST.forEach(([path, e]) => {
    const d = document.createElement('div');
    d.className = 'node-i file-i';
    d.dataset.layer = 'file';
    d.dataset.search = path.toLowerCase();
    const base = path.split('/').pop();
    const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
    d.innerHTML = `<div class="step">◦</div>
      <div class="nm">${esc(base)}<small>${esc(dir)} · ${e.nodes.length} 处引用</small></div>`;
    d.onclick = () => {
      document.querySelectorAll('.node-i').forEach(x => x.classList.remove('on'));
      d.classList.add('on');
      openFile(e.file, e.file.start);
    };
    g.appendChild(d);
  });
  rail.appendChild(g);
})();

// ── detail ────────────────────────────────────────────────
const detail = document.getElementById('detail');
let current = null;

function fileCard(f, rw) {
  const where = f.start ? `${f.path}<span class="fc-lines">:${f.start}${f.end !== f.start ? '-' + f.end : ''}</span>`
                        : `${f.path}<span class="fc-lines">· ${f.loc ? f.loc.split(' / ')[0] : '结构化'}</span>`;
  return `<div class="fcard" data-f="${f.path}" data-s="${f.start ?? ''}">
    <div class="fc-top">
      <span class="fc-ico">${rw === 'r' ? '◂' : '▸'}</span>
      <span class="fc-path">${esc(where)}</span>
      <span class="badge-rw ${rw}">${rw === 'r' ? '读' : '写'}</span>
    </div>
    <div class="fc-note">${esc(f.note || '')}</div>
  </div>`;
}

function renderDetail(id) {
  const n = NODES.find(x => x.id === id);
  current = n;
  const allFiles = [...n.reads.map(f => [f,'r']), ...n.writes.map(f => [f,'w']), ...n.files.map(f => [f,'-'])];
  detail.innerHTML = `
    <div class="d-head">
      <div class="d-step">步骤 ${esc(n.step)}</div>
      <div class="d-title">${esc(n.title)}</div>
      <div class="d-meta">
        <span class="tag who">${esc(n.actor)}</span>
        <span class="tag when">${esc(n.when)}</span>
      </div>
      <div class="d-one">${esc(n.one_liner)}</div>
    </div>
    <div class="d-body">
      <h3 class="sec">这一步做了什么</h3>
      <ul class="list">${n.does.map(d => `<li>${hlADR(d)}</li>`).join('')}</ul>
      <h3 class="sec">为什么这样设计</h3>
      <ul class="list why">${n.why.map(d => `<li>${hlADR(d)}</li>`).join('')}</ul>
      <h3 class="sec">读什么 / 写什么</h3>
      ${['r','w'].map(rw => {
        const arr = allFiles.filter(([f,k]) => k === rw);
        if (!arr.length) return '';
        return `<div class="fgroup"><div class="fg-t">${rw === 'r' ? '依赖的输入' : '产出的输出'}
          <span class="cnt">${arr.length} 项</span></div>
          ${arr.map(([f]) => fileCard(f, rw)).join('')}</div>`;
      }).join('')}
      <div class="fgroup">
        <div class="fg-t">点开看原文 <span class="cnt">${n.files.length} 段摘录</span></div>
        ${n.files.map(f => fileCard(f, '-')).join('')}
      </div>
    </div>`;
  detail.querySelectorAll('.fcard').forEach(c => c.onclick = e => {
    const path = c.dataset.f, start = c.dataset.s;
    const pool = [...n.reads, ...n.writes, ...n.files];
    let f = pool.find(x => x.path === path && String(x.start ?? '') === start);
    if (!f) f = pool.find(x => x.path === path);
    openFile(f, c.dataset.s ? parseInt(c.dataset.s) : null);
    detail.querySelectorAll('.fcard').forEach(x => x.classList.remove('on'));
    c.classList.add('on');
  });
  detail.scrollTop = 0;
  document.querySelectorAll('.node-i').forEach(x => x.classList.toggle('on', x.dataset.id === id));
}

// ── viewer ────────────────────────────────────────────────
const viewer = document.getElementById('viewer');
function renderCode(text, startLine, hits) {
  const rows = text.split('\n');
  return rows.map((t, i) => {
    const ln = startLine ? startLine + i : null;
    let tx = esc(t);
    if (hits.length) {
      hits.forEach(h => {
        const re = new RegExp('(' + h.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
        tx = tx.replace(re, '<span class="hit">$1</span>');
      });
    }
    return `<div class="row${hits.length && tx.includes('class="hit"') ? ' hl' : ''}">
      <span class="ln">${ln ?? ''}</span><span class="tx">${tx}</span></div>`;
  }).join('');
}
function openFile(f, forceStart) {
  const hits = searchTerm ? [searchTerm] : [];
  const start = forceStart || f.start || 1;
  const isRaw = f.start !== null;
  const entry = FILEMAP.get(f.path);
  const refs = entry ? entry.nodes.map(id => NODES.find(x => x.id === id).step + ' ' + NODES.find(x => x.id === id).title) : [];
  viewer.className = 'col open';
  viewer.innerHTML = `
    <div class="v-head">
      <div class="v-path">${esc(f.path)}</div>
      <div class="v-note">${hlADR(f.note || '')}</div>
      <div class="v-tags">
        <span class="tag">${isRaw ? `第 ${f.start}–${f.end} 行 / 共 ${f.total} 行` : `结构化视图 · 共 ${f.total} 行`}</span>
        ${isRaw ? `<span class="tag">偏移 ${((f.start-1)/f.total*100).toFixed(1)}%</span>` : ''}
      </div>
      <div class="v-src">来源：仓库真实文件，由 viz/facts/build_facts.py 切片${refs.length ? ' ｜ 被 ' + refs.length + ' 个环节引用：' + refs.join('、') : ''}</div>
    </div>
    <pre class="code">${renderCode(f.text, isRaw ? f.start : 1, hits)}</pre>`;
  viewer.scrollTop = 0;
  // 首个命中滚动到位
  if (hits.length) {
    const h = viewer.querySelector('.row.hl');
    if (h) h.scrollIntoView({block:'center'});
  }
}
viewer.innerHTML = `<div class="v-empty">
  <div style="font-size:32px;opacity:.35">◲</div>
  <div>点右侧任一文件，这里显示它的真实原文</div>
  <div><kbd>←</kbd> <kbd>→</kbd> 切换环节 · <kbd>/</kbd> 搜索</div>
</div>`;

// ── search ────────────────────────────────────────────────
let searchTerm = '';
const searchBox = document.getElementById('search');
searchBox.addEventListener('input', () => {
  searchTerm = searchBox.value.trim().toLowerCase();
  filter();
});
function escapeRe(s){return s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}
function filter() {
  const t = searchTerm;
  document.querySelectorAll('.node-i').forEach(el => {
    const n = NODES.find(x => x.id === el.dataset.id);
    if (!n) { // 文件索引条目
      const path = el.dataset.search;
      const e = FILEMAP.get([...FILEMAP.keys()].find(k => k.toLowerCase() === path));
      el.classList.toggle('hide', !!t && !(path.includes(t) || (e && e.file.text.toLowerCase().includes(t))));
      return;
    }
    const hay = el.dataset.search + ' ' + [...n.reads, ...n.writes, ...n.files]
      .map(f => f.path + ' ' + (f.note||'') + ' ' + f.text).join(' ').toLowerCase();
    el.classList.toggle('hide', !!t && !hay.includes(t));
  });
  if (t) {  // 自动打开第一个含.body 命中的文件
    for (const n of NODES) {
      const f = [...n.reads, ...n.writes, ...n.files].find(f => f.text.toLowerCase().includes(t));
      if (f) { select(n.id); openFile(f, f.start); 
        detail.querySelectorAll('.fcard').forEach(c => c.classList.toggle('on', c.dataset.f === f.path && c.dataset.s === String(f.start ?? '')));
        break;
      }
    }
  }
}
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== searchBox) { e.preventDefault(); searchBox.focus(); }
  if (e.key === 'Escape') { searchBox.value=''; searchTerm=''; filter(); searchBox.blur(); }
  if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && document.activeElement !== searchBox && current) {
    const i = order.indexOf(current.id);
    const j = (i + (e.key === 'ArrowRight' ? 1 : -1) + order.length) % order.length;
    e.preventDefault(); select(order[j]);
  }
});

// ── boot ──────────────────────────────────────────────────
function select(id) {
  renderDetail(id);
  const el = document.querySelector(`.node-i[data-id="${id}"]`);
  if (el) el.scrollIntoView({block:'nearest'});
}
select(NODES[0].id);
</script>
</body>
</html>
"""

OUT.write_text(HTML.replace("__PAYLOAD__", PAYLOAD), encoding="utf-8")
print(f"OK {OUT.relative_to(ROOT)}  {OUT.stat().st_size/1024:.0f} KB")
