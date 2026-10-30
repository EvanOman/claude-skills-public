#!/usr/bin/env node
// SPDX-License-Identifier: MIT
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const kinds = ['request', 'return', 'data', 'control'];
const colors = ['teal', 'blue', 'amber', 'violet', 'rose', 'slate'];
const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function validate(m) {
  const errors = [], warnings = [];
  const fail = (p, message) => errors.push(`${p}: ${message}`);
  const obj = (v, p) => { if (!v || typeof v !== 'object' || Array.isArray(v)) { fail(p, 'expected object'); return false; } return true; };
  const str = (v, p) => { if (typeof v !== 'string' || !v.trim()) { fail(p, 'expected nonempty string'); return false; } return true; };
  const arr = (v, p, min = 0) => { if (!Array.isArray(v)) { fail(p, 'expected array'); return []; } if (v.length < min) fail(p, `need at least ${min} item(s)`); return v; };
  const fields = (v, p, keys) => keys.forEach(k => str(v[k], `${p}.${k}`));
  const choice = (v, p, allowed) => { if (!allowed.includes(v)) fail(p, `expected ${allowed.join(' | ')}`); };
  const url = (v, p) => {
    if (!str(v, p)) return;
    try { const u = new URL(v); if (u.protocol !== 'https:' || u.username || u.password || /^(localhost|127\.|\[::1\])/.test(u.hostname)) fail(p, 'use a public HTTPS URL without credentials'); }
    catch { fail(p, 'invalid URL'); }
  };
  const collection = (v, p, min = 1) => {
    const a = arr(v, p, min), ids = new Set();
    a.forEach((x, i) => {
      if (!obj(x, `${p}[${i}]`)) return;
      if (typeof x.id !== 'string' || !/^[a-z][a-z0-9-]*$/.test(x.id)) fail(`${p}[${i}].id`, 'use a lowercase slug starting with a letter');
      else if (ids.has(x.id)) fail(`${p}[${i}].id`, `duplicate ID ${x.id}`);
      else ids.add(x.id);
    });
    return {a:a.filter(x => x && typeof x === 'object'), ids};
  };
  const refs = (v, p, ids, min = 0) => arr(v, p, min).forEach((id, i) => { if (!ids.has(id)) fail(`${p}[${i}]`, `unknown ID ${id}`); });
  const state = (v, p) => arr(v, p, 1).forEach((s, i) => { if (obj(s, `${p}[${i}]`)) fields(s, `${p}[${i}]`, ['owner','detail']); });
  if (!obj(m, 'document')) return {errors, warnings};
  if (m.version !== 1) fail('version', 'supported version is 1');
  fields(m, 'document', ['title','subtitle','question','scope']);
  if (obj(m.project, 'project')) {
    fields(m.project, 'project', ['name','version','license']);
    url(m.project.repository, 'project.repository');
    if (!/^[a-f0-9]{40}$/.test(m.project.revision)) fail('project.revision', 'pin a full 40-character Git commit');
  }
  const sources = collection(m.sources, 'sources');
  sources.a.forEach((s, i) => {
    str(s.label, `sources[${i}].label`); url(s.url, `sources[${i}].url`);
    if (!/\/[a-f0-9]{40}(?:\/|#|$)/.test(s.url || '') && !s.note) fail(`sources[${i}].note`, 'unpinned documentation needs an explicit live-documentation note');
    if (s.note) str(s.note, `sources[${i}].note`);
  });
  const claims = collection(m.claims, 'claims');
  claims.a.forEach((c, i) => { str(c.text, `claims[${i}].text`); choice(c.kind, `claims[${i}].kind`, ['source','inference']); refs(c.sources, `claims[${i}].sources`, sources.ids, 1); });
  const roles = collection(m.roles, 'roles');
  roles.a.forEach((r, i) => { str(r.label, `roles[${i}].label`); choice(r.color, `roles[${i}].color`, colors); });
  arr(m.takeaways, 'takeaways', 1).forEach((t, i) => { if (obj(t, `takeaways[${i}]`)) {str(t.text, `takeaways[${i}].text`); refs(t.claims, `takeaways[${i}].claims`, claims.ids, 1);} });
  arr(m.limits, 'limits', 1).forEach((s, i) => str(s, `limits[${i}]`));
  if (m.navigation !== undefined) arr(m.navigation, 'navigation').forEach((n,i) => {
    if (!obj(n, `navigation[${i}]`)) return;
    str(n.label, `navigation[${i}].label`);
    if (typeof n.url !== 'string' || (!/^(\.\/|\.\.\/|#|\/(?!\/))/.test(n.url) && !/^https:\/\//.test(n.url))) fail(`navigation[${i}].url`, 'use a relative path, anchor or public HTTPS URL');
    else if (n.url.startsWith('https:')) url(n.url, `navigation[${i}].url`);
  });
  if (!m.map && !m.sequence) fail('document', 'provide at least a map or sequence');
  if (m.map && obj(m.map, 'map')) {
    const map = m.map;
    fields(map, 'map', ['title','description']);
    ['width','height'].forEach(k => { if (!Number.isFinite(map[k]) || map[k] <= 0) fail(`map.${k}`, 'expected positive finite number'); });
    const nodes = collection(map.nodes, 'map.nodes');
    nodes.a.forEach((n, i) => {
      const p = `map.nodes[${i}]`; fields(n, p, ['label','detail']);
      if (!roles.ids.has(n.role)) fail(`${p}.role`, `unknown role ${n.role}`);
      ['x','y','w','h'].forEach(k => { if (!Number.isFinite(n[k]) || n[k] < (k === 'w' || k === 'h' ? 1 : 0)) fail(`${p}.${k}`, 'invalid geometry'); });
      if (n.x+n.w > map.width || n.y+n.h > map.height) fail(p, 'node exceeds map bounds');
    });
    const edges = collection(map.edges, 'map.edges', 0);
    edges.a.forEach((e, i) => {
      const p = `map.edges[${i}]`; str(e.label, `${p}.label`); choice(e.kind, `${p}.kind`, kinds);
      ['from','to'].forEach(k => { if (!nodes.ids.has(e[k])) fail(`${p}.${k}`, `unknown node ${e[k]}`); });
      if (e.from === e.to) fail(p, 'map self-edges are unsupported; use a sequence local event');
      if (e.via !== undefined) arr(e.via, `${p}.via`).forEach((v,j) => { if (obj(v, `${p}.via[${j}]`)) ['x','y'].forEach(k => { if (!Number.isFinite(v[k]) || v[k] < 0 || v[k] > map[k === 'x'?'width':'height']) fail(`${p}.via[${j}].${k}`, 'waypoint outside map'); }); });
    });
    for (const key of ['lenses','intents']) collection(map[key], `map.${key}`, key === 'lenses' ? 1 : 0).a.forEach((s, i) => {
      const p = `map.${key}[${i}]`; fields(s, p, ['label','title','body']);
      refs(s.nodes, `${p}.nodes`, nodes.ids); refs(s.edges, `${p}.edges`, edges.ids); refs(s.claims, `${p}.claims`, claims.ids, 1);
      if (!s.nodes?.length && !s.edges?.length) fail(p, 'selection highlights nothing');
    });
    if (m.walkthrough && obj(m.walkthrough, 'walkthrough')) {
      fields(m.walkthrough, 'walkthrough', ['title','intro']);
      collection(m.walkthrough.steps, 'walkthrough.steps').a.forEach((s, i) => {
        const p = `walkthrough.steps[${i}]`; fields(s, p, ['title','body','why','boundary']);
        refs(s.nodes, `${p}.nodes`, nodes.ids); refs(s.edges, `${p}.edges`, edges.ids); refs(s.claims, `${p}.claims`, claims.ids, 1); state(s.state, `${p}.state`);
        if (!s.nodes?.length && !s.edges?.length) fail(p, 'step highlights nothing');
      });
    }
  } else if (m.walkthrough) fail('walkthrough', 'requires a map');
  if (m.sequence && obj(m.sequence, 'sequence')) {
    fields(m.sequence, 'sequence', ['title','intro']);
    const lanes = collection(m.sequence.lanes, 'sequence.lanes');
    lanes.a.forEach((l, i) => { str(l.label, `sequence.lanes[${i}].label`); if (!roles.ids.has(l.role)) fail(`sequence.lanes[${i}].role`, `unknown role ${l.role}`); });
    collection(m.sequence.scenarios, 'sequence.scenarios').a.forEach((s, i) => {
      const p = `sequence.scenarios[${i}]`; fields(s, p, ['label','summary']);
      arr(s.assumptions, `${p}.assumptions`, 1).forEach((a,j) => str(a, `${p}.assumptions[${j}]`));
      collection(s.events, `${p}.events`).a.forEach((e, j) => {
        const q = `${p}.events[${j}]`; fields(e, q, ['label','body']); choice(e.kind, `${q}.kind`, kinds);
        ['from','to'].forEach(k => { if (!lanes.ids.has(e[k])) fail(`${q}.${k}`, `unknown lane ${e[k]}`); });
        refs(e.claims, `${q}.claims`, claims.ids, 1); state(e.state, `${q}.state`);
        if (e.kind === 'request' && e.from !== e.to && !s.events.slice(j+1).some(r => r.from === e.to && r.to === e.from)) warnings.push(`${q}: no later reverse exchange; check whether a response is intentionally omitted`);
        if (e.kind === 'return' && (e.from === e.to || !s.events.slice(0,j).some(r => r.kind === 'request' && r.from === e.to && r.to === e.from))) warnings.push(`${q}: return has no preceding opposite request; use control for local work or explain the condensed call`);
      });
    });
  }
  return {errors, warnings};
}

function textAlternative(m) {
  const p = s => `<p>${escape(s)}</p>`;
  const state = a => `<ul>${a.map(s=>`<li>${escape(s.owner)}: ${escape(s.detail)}</li>`).join('')}</ul>`;
  let html = `<h1>${escape(m.title)}</h1>${p(m.subtitle)}${p(m.question)}${p(m.scope)}`;
  if (m.map) html += `<h2>${escape(m.map.title)}</h2>${p(m.map.description)}<ul>${m.map.nodes.map(n=>`<li>${escape(n.label)}: ${escape(n.detail)}</li>`).join('')}</ul><ul>${m.map.edges.map(e=>`<li>${escape(e.from)} → ${escape(e.to)}: ${escape(e.label)} (${escape(e.kind)})</li>`).join('')}</ul>` + [...m.map.lenses,...m.map.intents].map(s=>`<h3>${escape(s.title)}</h3>${p(s.body)}`).join('');
  if (m.walkthrough) html += `<h2>${escape(m.walkthrough.title)}</h2><ol>${m.walkthrough.steps.map(s=>`<li><h3>${escape(s.title)}</h3>${p(s.body)}${p(s.why)}${p(s.boundary)}${state(s.state)}</li>`).join('')}</ol>`;
  if (m.sequence) html += `<h2>${escape(m.sequence.title)}</h2>` + m.sequence.scenarios.map(s=>`<h3>${escape(s.label)}</h3>${p(s.summary)}${s.assumptions.map(p).join('')}<ol>${s.events.map(e=>`<li>${escape(e.from)} → ${escape(e.to)}: ${escape(e.label)}${p(e.body)}${state(e.state)}</li>`).join('')}</ol>`).join('');
  return html + '<h2>Claims and sources</h2>' + m.claims.map(c=>`<p>${escape(c.text)} (${escape(c.kind)}) ${c.sources.map(id=>{const s=m.sources.find(x=>x.id===id);return `<a href="${escape(s.url)}">${escape(s.label)}</a>`;}).join(' · ')}</p>`).join('') + '<h2>Limits</h2>'+m.limits.map(p).join('');
}

export async function build(model, output) {
  const result = validate(model);
  if (result.errors.length) throw new Error(result.errors.join('\n'));
  const [css, js, theme, license] = await Promise.all([...['walkthrough.css','walkthrough.js','theme-init.js'].map(f=>readFile(join(root,'assets',f),'utf8')),readFile(join(root,'LICENSE'),'utf8')]);
  const data = JSON.stringify(model).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
  const nav = model.navigation?.length ? `<nav class="sw-site-nav" aria-label="Site">${model.navigation.map(n=>`<a href="${escape(n.url)}">${escape(n.label)}</a>`).join('')}</nav>` : '';
  const html = `<!doctype html>
<!-- System Walkthrough runtime and newly authored content. Third-party sources retain their own terms.
${license.replace(/--/g,'—')}-->
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="${escape(model.subtitle)}"><meta name="color-scheme" content="dark light"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:"><title>${escape(model.title)} · System Walkthroughs</title><script>${theme}</script><style>html{background:var(--sw-bg,#101719);color:var(--sw-ink,#eaf0ec)}body{margin:0}${css}</style></head>
<body>${nav}<main id="walkthrough"></main><noscript><article class="sw">${textAlternative(model)}</article></noscript><script id="walkthrough-model" type="application/json">${data}</script><script>${js}</script><script>SystemWalkthrough.mount(document.getElementById('walkthrough'),JSON.parse(document.getElementById('walkthrough-model').textContent));</script></body></html>\n`;
  await mkdir(output, {recursive:true});
  await writeFile(join(output,'index.html'),html);
  return {...result, bytes:Buffer.byteLength(html)};
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [command, input, output] = process.argv.slice(2);
  try {
    if (!['validate','build','init'].includes(command) || !input) throw new Error('Usage: node walkthrough.mjs init model.json | validate model.json | build model.json output-directory');
    if (command === 'init') {
      await writeFile(resolve(input),await readFile(join(root,'examples','starter.json')), {flag:'wx'});
      console.log(`Created ${input}. Replace the synthetic example with your source-grounded model.`);
    } else {
      const model = JSON.parse(await readFile(resolve(input),'utf8'));
      const result = command === 'build' ? await build(model, resolve(output || 'walkthrough-output')) : validate(model);
      result.warnings.forEach(w=>console.warn(`Warning: ${w}`));
      if (result.errors.length) throw new Error(result.errors.join('\n'));
      console.log(command === 'build' ? `Built ${output || 'walkthrough-output'}/index.html (${result.bytes} bytes)` : 'Model valid');
    }
  } catch (e) { console.error(e.message); process.exitCode = 1; }
}
