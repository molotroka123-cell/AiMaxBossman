/* ============================================================
   agentmap.js — Feature 06: Visual Agent Map.
   Endpoints: GET /api/agentmap?orchestra_id=, GET /api/orchestras.
   На мобильных (<700px) — список по ролям вместо SVG-графа (см. CSS ниже).
   ============================================================ */

import { api } from '../api.js';
import { h, statusBadge, select, field } from '../components.js';
import { panel, pageHead, errorNote } from './_ui.js';

const AgentMapPage = {
  id: 'agentmap',
  title: 'Карта агентов',
  icon: 'agents',
  nav: 'more',

  async render(ctx) {
    const [orchR] = await Promise.allSettled([api.raw('/api/orchestras')]);
    const orchestras = orchR.status === 'fulfilled' && Array.isArray(orchR.value) ? orchR.value : [];

    const state = ctx.state.agentmap || (ctx.state.agentmap = { orchestraId: '' });

    const orchSelect = select(
      [{ value: '', label: 'Все агенты' },
        ...orchestras.map((o) => ({ value: o.id, label: `${o.name} · ${(o.members || []).length} чел.` }))],
      { value: state.orchestraId },
    );
    orchSelect.addEventListener('change', () => { state.orchestraId = orchSelect.value; ctx.refresh(); });

    let graph = null; let err = null;
    try {
      const qs = state.orchestraId ? `?orchestra_id=${encodeURIComponent(state.orchestraId)}` : '';
      graph = await api.raw(`/api/agentmap${qs}`);
    } catch (e) { err = e; }

    const head = pageHead('Карта агентов', 'Кто кому передаёт работу — по реальным задачам и командам, в реальном времени.');
    const controls = h('div.row', field('Команда', orchSelect), h('div.spacer'));

    if (err) return h('div.bx-page', head, controls, errorNote(err, () => ctx.refresh()));

    const nodes = graph.nodes || [];
    const edges = graph.edges || [];

    if (!nodes.length) {
      return h('div.bx-page', head, controls, h('section.bx-panel',
        h('div.bx-panel-body', h('div.small.dim', 'Агентов нет — создайте хотя бы одного на странице «Агенты».'))));
    }

    const rows = classify(nodes, edges);

    const styleTag = h('style', `
      .agentmap-graph { display: block; }
      .agentmap-roles { display: none; }
      @media (max-width: 700px) {
        .agentmap-graph { display: none; }
        .agentmap-roles { display: block; }
      }
    `);

    return h('div.bx-page',
      head, controls, styleTag,
      h('div.agentmap-graph', panel('Схема связей', h('div', { style: { overflowX: 'auto' } }, buildSvg(rows, edges)))),
      h('div.agentmap-roles.stack', rows.map((row) => panel(row.label, h('div.mini-list', row.items.map(nodeRow))))));
  },

  onEvent(ev) { return ev.kind.startsWith('agent.') || ev.kind.startsWith('task.'); },
};

function nodeRow(n) {
  return h('div.mini-row',
    statusBadge(n.status || 'idle', { live: n.status === 'working' }),
    h('span.name', n.label),
    n.model ? h('span.xsmall.dim.mono', n.model) : null,
    n.task ? h('span.badge', `#${n.task}`) : null);
}

function classify(nodes, edges) {
  const targets = new Set(edges.map((e) => e.target));
  const sources = new Set(edges.map((e) => e.source));
  const reviewerTargets = new Set(edges.filter((e) => e.kind === 'reviews').map((e) => e.target));
  const workerTargets = new Set(edges.filter((e) => e.kind !== 'reviews').map((e) => e.target));

  const managers = nodes.filter((n) => sources.has(n.id) && !targets.has(n.id));
  const reviewers = nodes.filter((n) => reviewerTargets.has(n.id));
  const workers = nodes.filter((n) => workerTargets.has(n.id) && !reviewerTargets.has(n.id));
  const classified = new Set([...managers, ...reviewers, ...workers].map((n) => n.id));
  const rest = nodes.filter((n) => !classified.has(n.id));

  const rows = [];
  if (managers.length) rows.push({ label: 'Главные', items: managers });
  if (workers.length) rows.push({ label: 'Помощники', items: workers });
  if (rest.length) rows.push({ label: managers.length || workers.length || reviewers.length ? 'Прочие' : 'Агенты', items: rest });
  if (reviewers.length) rows.push({ label: 'Проверяющие', items: reviewers });
  return rows;
}

function buildSvg(rows, edges) {
  const maxCols = Math.max(1, ...rows.map((r) => r.items.length));
  const colW = 180;
  const rowH = 118;
  const width = Math.max(560, maxCols * colW);
  const height = rows.length * rowH + 40;

  const pos = new Map();
  rows.forEach((row, ri) => {
    const y = 44 + ri * rowH;
    const n = row.items.length;
    row.items.forEach((node, ci) => {
      const x = ((ci + 0.5) / n) * width;
      pos.set(node.id, { x, y });
    });
  });

  const lines = edges
    .filter((e) => pos.has(e.source) && pos.has(e.target))
    .map((e) => {
      const a = pos.get(e.source); const b = pos.get(e.target);
      return h('line', {
        x1: a.x, y1: a.y + 26, x2: b.x, y2: b.y - 26,
        stroke: 'var(--line)', 'stroke-width': 1.5,
        'stroke-dasharray': e.kind === 'reviews' ? '4 3' : null,
      });
    });

  const boxes = rows.flatMap((row) => row.items.map((node) => {
    const p = pos.get(node.id);
    const tone = { ok: 'var(--ok)', info: 'var(--info)', warn: 'var(--warn)', err: 'var(--err)', idle: 'var(--idle)' }[
      { online: 'ok', ok: 'ok', working: 'info', running: 'info', queued: 'warn', failed: 'err', error: 'err', idle: 'idle' }[node.status] || 'idle'
    ];
    return h('g', { transform: `translate(${p.x - 78}, ${p.y - 26})` },
      h('rect', { width: 156, height: 52, rx: 10, fill: 'var(--panel-2)', stroke: tone, 'stroke-width': 1.4 }),
      h('circle', { cx: 14, cy: 14, r: 4.5, fill: tone }),
      h('text', { x: 26, y: 19, fill: 'var(--fg)', 'font-size': 12, 'font-weight': 600 }, truncateLabel(node.label, 16)),
      h('text', { x: 26, y: 35, fill: 'var(--dim)', 'font-size': 10 }, node.model || node.status || ''),
    );
  }));

  return h('svg', { viewBox: `0 0 ${width} ${height}`, width: '100%', height: Math.min(height, 480), style: { display: 'block' } },
    lines, boxes);
}

function truncateLabel(s, n) {
  const str = String(s || '');
  return str.length > n ? `${str.slice(0, n - 1)}…` : str;
}

export default AgentMapPage;
