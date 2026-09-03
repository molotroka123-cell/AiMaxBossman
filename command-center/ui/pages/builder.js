/* ============================================================
   builder.js — Workflow Builder: n8n-подобный канвас миссии.
   Endpoints: GET /api/workflow/missions, GET /api/workflow/missions/{id}.
   Мутации — существующие ручки: /api/missions/{id}/start|stop, /api/approvals/{id}.
   Данные реальные (граф выводится из tasks/task_runs/approvals/checkpoints);
   ничего не рисуется «для красоты» — если узла нет в данных, его нет на холсте.
   Свой CSS: ui/builder.css, инжектится один раз через <link>.
   ============================================================ */

import { api } from '../api.js';
import {
  h, icon, statusBadge, empty, fmtClock, fmtDuration, fmtTokens, fmtCost,
  toastOk, toastError, confirmDialog, select,
} from '../components.js';
import { panel, errorBanner, pct } from './_shared.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** Локальная SVG-фабрика: h() знает не все теги (animateMotion, mpath, marker…). */
function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, String(tag).split('.')[0]);
  const cls = String(tag).split('.').slice(1);
  if (cls.length) el.setAttribute('class', cls.join(' '));
  if (attrs && !attrs.nodeType && typeof attrs === 'object' && !Array.isArray(attrs)) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') { el.setAttribute('class', [...cls, v].join(' ')); continue; }
      if (k.startsWith('on') && typeof v === 'function') { el.addEventListener(k.slice(2).toLowerCase(), v); continue; }
      el.setAttribute(k, v === true ? '' : String(v));
    }
  } else if (attrs !== undefined) {
    children.unshift(attrs);
  }
  for (const c of children.flat(4)) {
    if (c === null || c === undefined || c === false || c === true) continue;
    el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

/* Иконки узлов канваса (path-данные в системе координат 24×24). */
const NODE_ICONS = {
  play: 'M7 4.8 19 12 7 19.2V4.8z',
  schedules: 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 7.5V12l3 1.8',
  models: 'M4 4h16v16H4zM9 9h6v6H9z',
  cloud: 'M7 18h10a4 4 0 0 0 .3-8 6 6 0 0 0-11.6 1.6A3.5 3.5 0 0 0 7 18z',
  cpu: 'M8 8h8v8H8zM4 10h4M4 14h4M16 10h4M16 14h4M10 4v4M14 4v4M10 16v4M14 16v4',
  browser: 'M3 5h18v14H3zM3 9h18M6.5 7h.01M9 7h.01',
  terminal: 'M4 5h16v14H4zM7.5 9.5 10 12l-2.5 2.5M12.5 15h4',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5',
  shield: 'M12 3 4 6.5v5c0 4.6 3.2 8.4 8 9.5 4.8-1.1 8-4.9 8-9.5v-5L12 3zM9 12l2.2 2.2L15.5 10',
  skills: 'M12 3 4 7.5v9L12 21l8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12 4 7.5',
  agents: 'M12 4.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8zM4.5 20c0-3.6 3.4-6 7.5-6s7.5 2.4 7.5 6',
  database: 'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v5M12 7.6v.6',
};

const TONE = {
  success: 'var(--wf-success)', running: 'var(--wf-running)', pending: 'var(--wf-pending)',
  queued: 'var(--wf-queued)', waiting: 'var(--wf-waiting)', failed: 'var(--wf-failed)',
  stopped: 'var(--wf-stopped)',
};
const STATUS_TEXT = {
  success: 'Готово', running: 'Идёт', pending: 'Ожидает', queued: 'В очереди',
  waiting: 'Ждёт', failed: 'Ошибка', stopped: 'Остановлено',
};
const SERIES = ['var(--wf-s1)', 'var(--wf-s2)', 'var(--wf-s3)', 'var(--wf-s4)'];

function ensureCss() {
  if (document.getElementById('bcc-builder-css')) return;
  const link = document.createElement('link');
  link.id = 'bcc-builder-css';
  link.rel = 'stylesheet';
  link.href = 'builder.css';
  document.head.appendChild(link);
}

const BuilderPage = {
  id: 'builder',
  title: 'Конструктор миссий',
  icon: 'bolt',
  nav: 'primary',

  async render(ctx, params) {
    ensureCss();
    const st = ctx.state.builder || (ctx.state.builder = {
      missionId: null, tab: 'builder', selected: null, view: null,
    });
    if (params && params.mission) st.missionId = Number(params.mission);

    let missions = [];
    try { missions = await api.raw('/api/workflow/missions'); } catch (e) { return errorBanner(e, ctx); }
    if (!Array.isArray(missions) || !missions.length) {
      return h('div.wf-root',
        h('section.panel', empty({
          iconName: 'empty',
          title: 'Миссий пока нет',
          hint: 'Схема собирается из настоящей миссии — её плана, задач, запусков и подтверждений. Создайте миссию, и схема появится сама.',
          action: h('button.btn.btn-primary', { type: 'button', onClick: () => ctx.navigate('missions') }, 'К миссиям'),
        })));
    }
    if (!missions.some((m) => m.id === st.missionId)) st.missionId = missions[0].id;

    let data = null;
    let loadError = null;
    // Ошибка одной миссии не должна съедать всю страницу: шапка и выбор миссии
    // остаются на месте, иначе владелец не может переключиться на другую.
    try { data = await api.raw(`/api/workflow/missions/${st.missionId}`); } catch (e) { loadError = e; }

    const root = h('div.wf-root', buildHead(ctx, st, missions, data));
    if (loadError) {
      root.appendChild(errorBanner(loadError, ctx));
      return root;
    }
    if (st.tab === 'runs') {
      root.appendChild(runsTab(data));
    } else {
      root.appendChild(h('div.wf-main', canvasPanel(ctx, st, data), rightRail(ctx, st, data)));
      root.appendChild(h('div.wf-bottom', queuePanel(data), timelinePanel(data), metricsPanel(data)));
      if ((data.approvals || []).length) root.appendChild(approvalsPanel(ctx, data));
    }
    startTicker(root, data);
    return root;
  },

  onEvent(ev) {
    return /^(mission|task|run|approval|checkpoint|worker)\./.test(String(ev.kind || ''));
  },
};

/* ---------------- Шапка ---------------- */

function buildHead(ctx, st, missions, data) {
  const m = data.mission;
  const running = String(m.status) === 'running';

  const sel = select(missions.map((x) => ({ value: x.id, label: `${x.title} · ${x.status}` })),
    { value: st.missionId, style: { maxWidth: '320px' } });
  sel.addEventListener('change', () => {
    st.missionId = Number(sel.value); st.selected = null; st.view = null; ctx.refresh();
  });

  const tab = (id, label) => h('button.wf-tab', {
    type: 'button', role: 'tab', 'aria-selected': String(st.tab === id),
    onClick: () => { st.tab = id; ctx.refresh(); },
  }, label);

  const runBtn = running
    ? h('button.btn.btn-sm', { type: 'button', onClick: () => stopMission(ctx, m) },
      icon('stop', 15), 'Остановить')
    : h('button.btn.btn-primary.btn-sm', { type: 'button', onClick: () => startMission(ctx, m) },
      icon('play', 15), 'Запустить миссию');

  return h('div.wf-head',
    h('div.wf-crumb',
      h('span.wf-crumb-root', 'Миссии'), h('span.wf-crumb-root', '/'), sel,
      statusBadge(m.status, { live: running })),
    h('div.spacer'),
    h('div.wf-tabs', { role: 'tablist' }, tab('builder', 'Схема'), tab('runs', 'Запуски')),
    runBtn);
}

async function startMission(ctx, m) {
  try {
    await api.raw(`/api/missions/${m.id}/${String(m.status) === 'paused' ? 'resume' : 'start'}`, { method: 'POST' });
    toastOk('Миссия запущена', m.title);
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось запустить миссию'); }
}

async function stopMission(ctx, m) {
  const ok = await confirmDialog({
    title: 'Остановить миссию?',
    text: `«${m.title}» — все идущие сейчас задачи будут остановлены.`,
    okText: 'Остановить', danger: true,
  });
  if (!ok) return;
  try {
    await api.raw(`/api/missions/${m.id}/stop`, { method: 'POST' });
    toastOk('Миссия остановлена');
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось остановить миссию'); }
}

/* ---------------- Канвас ---------------- */

function canvasPanel(ctx, st, data) {
  const g = data.graph;
  const wrap = h('div.wf-canvas-wrap');
  const layer = s('g');
  const svg = s('svg.wf-canvas', {
    viewBox: `0 0 ${g.width} ${g.height}`, preserveAspectRatio: 'xMidYMid meet',
    role: 'img', 'aria-label': `Граф миссии: ${g.nodes.length} узлов`,
  }, s('defs'), layer);

  for (const e of g.edges) layer.appendChild(edgeGroup(e, g));
  for (const n of g.nodes) layer.appendChild(nodeGroup(n, st, ctx));

  /* pan/zoom: сохраняем в state, чтобы перерисовка по WS не сбрасывала вид */
  const view = st.view || (st.view = { k: 1, x: 0, y: 0 });
  const zoomLabel = h('div.wf-zoom-label', `${Math.round(view.k * 100)}%`);
  const applyView = () => {
    layer.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`);
    zoomLabel.textContent = `${Math.round(view.k * 100)}%`;
  };
  applyView();

  /* viewBox по высоте графа и пропорции панели: узлы рисуются в натуральную
     величину (как в n8n), а не сжимаются вместе со всем графом. */
  const box = { w: g.width, h: g.height };
  const sizeToPanel = () => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    box.h = g.height;
    box.w = Math.max(360, Math.round(g.height * (rect.width / rect.height)));
    svg.setAttribute('viewBox', `0 0 ${box.w} ${box.h}`);
  };

  const zoomAt = (factor, cx, cy) => {
    const k = Math.min(2.4, Math.max(0.35, view.k * factor));
    const ratio = k / view.k;
    view.x = cx - (cx - view.x) * ratio;
    view.y = cy - (cy - view.y) * ratio;
    view.k = k;
    applyView();
  };

  /** Показать узел (по умолчанию — первый живой) по центру видимой области. */
  const focus = (node) => {
    const target = node || g.nodes.find((n) => n.status === 'running')
      || g.nodes.find((n) => n.status === 'waiting') || g.nodes[0];
    if (!target) return;
    view.k = 1;
    view.x = Math.min(0, box.w / 2 - (target.x + target.w / 2));
    view.y = 0;
    applyView();
  };
  const fit = () => {
    view.k = Math.min(1, box.w / g.width);
    view.x = 0; view.y = 0;
    applyView();
  };

  svg.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const pt = svgPoint(svg, ev);
    zoomAt(ev.deltaY < 0 ? 1.12 : 1 / 1.12, pt.x, pt.y);
  }, { passive: false });

  /* Панорама и выбор узла — на одних и тех же указателях: pointer capture
     перенацеливает click на <svg>, поэтому выбор делаем сами на pointerup,
     если указатель не уехал дальше порога. */
  let drag = null;
  svg.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    const pt = svgPoint(svg, ev);
    const node = ev.target && ev.target.closest ? ev.target.closest('.wf-node') : null;
    drag = { x: pt.x - view.x, y: pt.y - view.y, id: ev.pointerId, moved: false,
      sx: ev.clientX, sy: ev.clientY, node };
    svg.setPointerCapture(ev.pointerId);
    svg.classList.add('is-panning');
  });
  svg.addEventListener('pointermove', (ev) => {
    if (!drag || ev.pointerId !== drag.id) return;
    if (!drag.moved && Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy) < 4) return;
    const pt = svgPoint(svg, ev);
    view.x = pt.x - drag.x; view.y = pt.y - drag.y; drag.moved = true;
    applyView();
  });
  const endDrag = (ev) => {
    if (!drag || (ev && ev.pointerId !== drag.id)) return;
    const { moved, node } = drag;
    drag = null;
    svg.classList.remove('is-panning');
    if (!moved && node && ev && ev.type === 'pointerup') {
      const id = node.getAttribute('data-node-id');
      st.selected = st.selected === id ? null : id;
      ctx.refresh();
    }
  };
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);

  const rail = h('div.wf-rail',
    h('button.wf-rail-btn', { type: 'button', title: 'Приблизить', 'aria-label': 'Приблизить', onClick: () => zoomAt(1.2, box.w / 2, box.h / 2) }, '+'),
    zoomLabel,
    h('button.wf-rail-btn', { type: 'button', title: 'Отдалить', 'aria-label': 'Отдалить', onClick: () => zoomAt(1 / 1.2, box.w / 2, box.h / 2) }, '−'),
    h('button.wf-rail-btn', { type: 'button', title: 'Весь граф', 'aria-label': 'Показать весь граф', onClick: fit }, icon('empty', 15)),
    h('button.wf-rail-btn', { type: 'button', title: 'К активному узлу', 'aria-label': 'К активному узлу', onClick: () => focus(null) }, icon('bolt', 15)));

  const legend = h('div.wf-canvas-legend',
    ...['success', 'running', 'waiting', 'pending', 'failed'].map((k) => h('span.wf-legend-item',
      { style: { color: TONE[k] } },
      h('span.wf-legend-swatch'), h('span', { style: { color: 'var(--dim)' } }, STATUS_TEXT[k]))));

  wrap.append(rail, svg, legend);

  /* Размер панели известен только после монтирования. */
  requestAnimationFrame(() => {
    sizeToPanel();
    if (st.viewInit === data.mission.id) applyView(); else { st.viewInit = data.mission.id; focus(null); }
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(() => {
        if (!document.body.contains(wrap)) { ro.disconnect(); return; }
        sizeToPanel();
      });
      ro.observe(wrap);
    }
  });
  return wrap;
}

function svgPoint(svg, ev) {
  const rect = svg.getBoundingClientRect();
  const vb = svg.viewBox.baseVal;
  const scale = Math.min(rect.width / vb.width, rect.height / vb.height) || 1;
  const offX = (rect.width - vb.width * scale) / 2;
  const offY = (rect.height - vb.height * scale) / 2;
  return {
    x: (ev.clientX - rect.left - Math.max(0, offX)) / scale,
    y: (ev.clientY - rect.top - Math.max(0, offY)) / scale,
  };
}

function edgePath(edge, byId, g) {
  const a = byId.get(edge.source); const b = byId.get(edge.target);
  if (!a || !b) return null;
  const back = b.x < a.x;
  const x1 = back ? a.x : a.x + g.node_w;
  const y1 = a.y + g.node_h / 2;
  const x2 = back ? b.x + g.node_w : b.x;
  const y2 = b.y + g.node_h / 2;
  const dx = Math.max(38, Math.abs(x2 - x1) * 0.45) * (back ? -1 : 1);
  return { d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
    mx: (x1 + x2) / 2, my: (y1 + y2) / 2 };
}

function edgeGroup(edge, g) {
  const byId = g._byId || (g._byId = new Map(g.nodes.map((n) => [n.id, n])));
  const p = edgePath(edge, byId, g);
  if (!p) return s('g');
  const suffix = edge.kind === 'approved' ? '--approved' : edge.kind === 'rejected' ? '--rejected' : '';
  const pathId = `wfp-${edge.id.replace(/[^\w-]/g, '_')}`;
  const parts = [s('path', { class: `wf-edge ${suffix ? `wf-edge${suffix}` : ''}`, id: pathId, d: p.d })];

  if (edge.active) {
    parts.push(s('path', { class: `wf-edge-flow ${suffix ? `wf-edge-flow${suffix}` : ''}`, d: p.d }));
    const packet = s('circle', { class: `wf-packet ${suffix ? `wf-packet${suffix}` : ''}`, r: 3 });
    const motion = s('animateMotion', { dur: '1.9s', repeatCount: 'indefinite', rotate: 'auto' },
      s('mpath', { href: `#${pathId}` }));
    packet.appendChild(motion);
    parts.push(packet);
  }

  if (edge.label) {
    const w = edge.label.length * 5.6 + 14;
    parts.push(s('rect.wf-edge-label-bg', { x: p.mx - w / 2, y: p.my - 8, width: w, height: 16, rx: 8 }));
    parts.push(s('text.wf-edge-label.wf-svg-text', { x: p.mx, y: p.my + 0.5 }, edge.label));
  }
  return s('g', parts);
}

function nodeGroup(n, st, ctx) {
  const tone = TONE[n.status] || TONE.pending;
  const selected = st.selected === n.id;
  const g = s(`g.wf-node.wf-node--${n.status}${selected ? ' is-selected' : ''}`, {
    transform: `translate(${n.x} ${n.y})`, tabindex: '0', role: 'button',
    'data-node-id': n.id,
    'aria-label': `${n.title}: ${STATUS_TEXT[n.status] || n.status}`,
    onKeydown: (ev) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      ev.preventDefault();
      st.selected = selected ? null : n.id;
      ctx.refresh();
    },
  });

  g.appendChild(s('rect.wf-node-box', { width: n.w, height: n.h, rx: 14 }));
  g.appendChild(s('rect', { x: 1, y: 12, width: 3, height: n.h - 24, rx: 2, fill: tone }));

  if (n.status === 'running') {
    g.appendChild(s('circle.wf-pulse', { cx: 29, cy: n.h / 2, r: 15, fill: 'none', stroke: tone, 'stroke-width': 1.6 }));
  }
  g.appendChild(s('rect', { x: 14, y: n.h / 2 - 15, width: 30, height: 30, rx: 9,
    fill: `color-mix(in srgb, ${tone} 16%, transparent)`, stroke: `color-mix(in srgb, ${tone} 42%, transparent)` }));
  g.appendChild(s('path.wf-node-icon', {
    d: NODE_ICONS[n.icon] || NODE_ICONS.info, stroke: tone,
    transform: `translate(${14 + 15 - 9} ${n.h / 2 - 9}) scale(0.75)`,
  }));

  g.appendChild(s('text.wf-node-title.wf-svg-text', { x: 54, y: n.h / 2 - 4 }, n.title));
  g.appendChild(s('text.wf-node-sub.wf-svg-text', { x: 54, y: n.h / 2 + 12 }, n.subtitle || ''));

  g.appendChild(s('circle', { cx: n.w - 14, cy: 15, r: 3.5, fill: tone }));
  g.appendChild(s('text.wf-node-status.wf-svg-text', { x: n.w - 23, y: 18.5, fill: tone },
    STATUS_TEXT[n.status] || n.status));

  if (Number.isFinite(n.elapsed_ms) && n.elapsed_ms !== null) {
    g.appendChild(s('text.wf-node-elapsed.wf-svg-text', {
      x: n.w - 12, y: n.h - 12, 'data-elapsed-from': n.status === 'running' ? String(n.elapsed_ms) : '',
    }, `⏱ ${fmtDuration(n.elapsed_ms)}`));
  }
  return g;
}

/* ---------------- Правая колонка ---------------- */

function rightRail(ctx, st, data) {
  const run = data.run;
  const nodes = data.graph.nodes;
  const selected = nodes.find((n) => n.id === st.selected);

  const overview = panel('Обзор запуска', h('div.stack',
    h('div',
      h('div.row', h('span.small.dim', 'Прогресс'), h('div.spacer'),
        h('span.small', { style: { fontWeight: 600 } }, pct(run.progress))),
      h('div.wf-progress', { style: { marginTop: '6px' } },
        h('div.wf-progress-fill', { style: { width: `${Math.min(100, (run.progress || 0) * 100)}%` } }))),
    h('dl.wf-kv',
      h('dt', 'Задачи'), h('dd', `${run.tasks_done} / ${run.tasks_total}`),
      h('dt', 'Старт'), h('dd', run.started_at ? fmtClock(run.started_at, true) : '—'),
      h('dt', 'ETA'), h('dd', Number.isFinite(run.eta_seconds) && run.eta_seconds !== null
        ? fmtDuration(run.eta_seconds * 1000) : '—'),
      h('dt', 'Токены'), h('dd', `${fmtTokens(run.tokens_in)} / ${fmtTokens(run.tokens_out)}`),
      h('dt', 'Стоимость'), h('dd', run.budget_usd
        ? `${fmtCost(run.cost_usd)} из ${fmtCost(run.budget_usd)}` : fmtCost(run.cost_usd)))),
  { tight: true });

  const agents = panel(`Активные агенты (${run.active_agents.length})`,
    run.active_agents.length
      ? h('div', run.active_agents.map((a) => h('div.wf-agent-row',
        h('span.dot.dot-run', { style: { background: TONE.running } }),
        h('span.wf-agent-name', a.title),
        h('div.spacer'),
        a.model_alias ? h('span.xsmall.dim.mono', a.model_alias) : null,
        h('span.xsmall.mono', { 'data-elapsed-from': String(a.elapsed_ms) }, fmtDuration(a.elapsed_ms)))))
      : h('div.small.dim', 'Сейчас никто не выполняется.'), { tight: true });

  const log = panel('Журнал выполнения', data.log.length
    ? h('div.wf-log', { ref: (el) => queueMicrotask(() => { el.scrollTop = el.scrollHeight; }) },
      data.log.map((e) => h(`div.wf-log-row${e.level === 'error' ? '.is-error' : e.level === 'warn' ? '.is-warn' : ''}`,
        h('span.wf-log-time', fmtClock(e.ts, true)),
        h('span.wf-log-msg', e.message || e.kind))))
    : h('div.small.dim', 'Событий ещё нет — журнал наполняется во время выполнения.'), { tight: true });

  const inspector = selected ? panel('Узел', nodeInspector(selected, ctx), { tight: true }) : null;

  return h('div.stack', overview, inspector, agents, log);
}

function nodeInspector(n, ctx) {
  const meta = n.meta || {};
  const rows = [];
  const add = (k, v) => { if (v !== null && v !== undefined && v !== '') rows.push(h('dt', k), h('dd', String(v))); };
  add('Статус', STATUS_TEXT[n.status] || n.status);
  add('Задача', meta.task_id ? `#${meta.task_id}` : null);
  add('Ранн', meta.run_id ? `#${meta.run_id}` : null);
  add('Модель', meta.model_alias);
  add('Попытка', meta.attempt);
  add('Тип', meta.kind);
  add('Раннов', meta.runs);
  add('Токенов', Number.isFinite(meta.tokens) ? fmtTokens(meta.tokens) : null);
  add('Стоимость', Number.isFinite(meta.cost_usd) && meta.cost_usd ? fmtCost(meta.cost_usd) : null);
  add('Чекпойнтов', meta.checkpoints);
  add('На проверке', meta.pending);
  add('Решений роутера', meta.decisions);

  return h('div.stack.sm',
    h('div.wf-inspect-title',
      h('span.dot', { style: { background: TONE[n.status] || TONE.pending } }), n.title),
    h('div.small.dim', n.subtitle || ''),
    rows.length ? h('dl.wf-kv', rows) : null,
    meta.error ? h('div.small', { style: { color: 'var(--err)', overflowWrap: 'anywhere' } }, meta.error) : null,
    meta.task_id ? h('button.btn.btn-sm', { type: 'button', onClick: () => ctx.navigate('tasks') }, 'Открыть задачи') : null);
}

/* ---------------- Нижний ряд ---------------- */

function queuePanel(data) {
  const rows = data.queue || [];
  return panel('Очередь запусков', rows.length
    ? h('div.mini-list', rows.map((r) => h('div.mini-row',
      statusBadge(r.status, { live: r.status === 'running' }),
      h('span.name', r.title),
      r.attempt ? h('span.badge', `попытка ${r.attempt + 1}`) : null,
      h('div.spacer'),
      h('span.xsmall.dim.mono', r.duration_ms !== null ? fmtDuration(r.duration_ms) : '—'))))
    : h('div.small.dim', 'Запусков ещё не было.'), { tight: true });
}

function timelinePanel(data) {
  const tl = data.timeline;
  if (!tl.rows.length) {
    return panel('Таймлайн выполнения',
      h('div.small.dim', 'Появится, когда пойдут первые запуски задач.'), { tight: true });
  }
  const span = tl.span_ms || 1;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => fmtDuration(Math.round(span * f)));

  const rows = tl.rows.map((row) => h('div.wf-gantt-row',
    h('div.wf-gantt-label', { title: row.label }, row.label),
    h('div.wf-gantt-track', row.segments.map((seg) => h('div', {
      class: `wf-gantt-bar is-${seg.status}`,
      title: `${seg.status} · ${fmtDuration(seg.end_ms - seg.start_ms)}${seg.model_alias ? ` · ${seg.model_alias}` : ''}`,
      style: {
        left: `${(seg.start_ms / span) * 100}%`,
        width: `${Math.max(0.8, ((seg.end_ms - seg.start_ms) / span) * 100)}%`,
      },
    })))));

  const legend = h('div.wf-canvas-legend', {
    style: { position: 'static', background: 'transparent', border: 0, padding: '2px 0 0', backdropFilter: 'none' },
  }, ['success', 'running', 'waiting', 'failed', 'stopped'].map((k) => h('span.wf-legend-item',
    { style: { color: TONE[k] } },
    h('span.wf-legend-swatch'), h('span', { style: { color: 'var(--dim)' } }, STATUS_TEXT[k]))));

  return panel('Таймлайн выполнения', h('div.stack.sm',
    h('div.wf-gantt', rows),
    h('div.wf-gantt-axis', h('span'), h('div.wf-gantt-ticks', ticks.map((t) => h('span', t)))),
    legend), { tight: true });
}

function metricsPanel(data) {
  const m = data.metrics;
  return panel('Метрики запуска', h('div.stack.sm',
    h('dl.wf-kv',
      h('dt', 'Всего'), h('dd', fmtDuration(m.total_ms)),
      h('dt', 'Работа агентов'), h('dd', fmtDuration(m.agent_ms)),
      h('dt', `Загрузка пула (${m.workers})`), h('dd', pct(m.agent_share)),
      h('dt', 'Простой пула'), h('dd', `${fmtDuration(m.idle_ms)} · ${pct(m.idle_share)}`),
      h('dt', 'Токены in/out'), h('dd', `${fmtTokens(m.tokens_in)} / ${fmtTokens(m.tokens_out)}`),
      h('dt', 'Стоимость'), h('dd', fmtCost(m.cost_usd))),
    h('div.wf-metric-bar', {
      title: `Загрузка ${pct(m.agent_share)} · простой ${pct(m.idle_share)} `
        + `(ёмкость = время × ${m.workers} воркеров)`,
    },
      h('span.is-agent', { style: { width: `${(m.agent_share || 0) * 100}%` } }),
      h('span.is-idle', { style: { width: `${(m.idle_share || 0) * 100}%` } })),
    donut(m)), { tight: true });
}

/** Донат «Токены по моделям»: 3 валидированных слота + нейтральное «Прочее».
    Легенда всегда с прямыми подписями значений — это и есть relief для
    светлой темы (контраст aqua < 3:1 к светлой поверхности). */
function donut(m) {
  const series = (m.by_model || []).filter((x) => x.tokens > 0);
  if (!series.length) return h('div.small.dim', 'Токены ещё не расходовались.');

  const r = 52; const sw = 17; const C = 2 * Math.PI * r; const gap = 2.5;
  let acc = 0;
  const segs = series.map((x, i) => {
    const len = Math.max(0, C * (x.share || 0) - gap);
    const el = s('circle.wf-donut-seg', {
      cx: 70, cy: 70, r, fill: 'none', stroke: SERIES[i] || SERIES[3], 'stroke-width': sw,
      'stroke-dasharray': `${len} ${C - len}`, 'stroke-dashoffset': -acc,
      transform: 'rotate(-90 70 70)',
    });
    acc += C * (x.share || 0);
    return el;
  });

  const svg = s('svg.wf-donut', { viewBox: '0 0 140 140', width: 140, height: 140,
    role: 'img', 'aria-label': 'Токены по моделям' },
  s('circle', { cx: 70, cy: 70, r, fill: 'none', stroke: 'var(--line)', 'stroke-width': sw }),
  segs,
  s('text.wf-donut-center.wf-donut-value.wf-svg-text', { x: 70, y: 68 }, fmtTokens(m.tokens_total)),
  s('text.wf-donut-center.wf-donut-label.wf-svg-text', { x: 70, y: 84 }, 'ТОКЕНОВ'));

  const legend = h('div.wf-donut-legend', series.map((x, i) => h('div.wf-donut-legend-row',
    h('span.wf-legend-swatch', { style: { color: SERIES[i] || SERIES[3] } }),
    h('span.wf-legend-name', { title: x.label }, x.label),
    h('span.wf-legend-val', `${fmtTokens(x.tokens)} · ${pct(x.share)}`))));

  return h('div.stack.sm',
    h('div.small.dim', 'Токены по моделям'),
    h('div.wf-donut-wrap', svg, legend));
}

function approvalsPanel(ctx, data) {
  return panel(`Approvals (${data.approvals.length})`, h('div',
    data.approvals.map((a) => h('div.wf-approval',
      h('div.wf-approval-body',
        h('div.row', h('b', a.kind), h('div.spacer'),
          h('span.xsmall.dim', a.age_seconds !== null ? `ждёт ${fmtDuration(a.age_seconds * 1000)}` : '')),
        a.task_title ? h('div.small.dim', `${a.task_title} · задача #${a.task_id}`) : null,
        a.preview ? h('div.wf-approval-preview', a.preview) : null),
      h('div.wf-approval-actions',
        h('button.btn.btn-sm.btn-primary', { type: 'button', title: 'Одобрить',
          'aria-label': `Одобрить: ${a.kind}`,
          onClick: () => decide(ctx, a, true) }, icon('check', 15)),
        h('button.btn.btn-sm', { type: 'button', title: 'Отклонить',
          'aria-label': `Отклонить: ${a.kind}`,
          onClick: () => decide(ctx, a, false) }, icon('close', 15)))))), { tight: true });
}

async function decide(ctx, a, approve) {
  try {
    await api.decideApproval(a.id, approve, 'builder');
    toastOk(approve ? 'Одобрено' : 'Отклонено', a.kind);
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось применить решение'); }
}

/* ---------------- Вкладка «Запуски» ---------------- */

function runsTab(data) {
  const rows = data.queue || [];
  if (!rows.length) {
    return h('section.panel', empty({ iconName: 'empty', title: 'Запусков ещё не было',
      hint: 'Запустите миссию — здесь появятся все её раны с попытками, моделью и длительностью.' }));
  }
  return panel('Все запуски миссии', h('table.table',
    h('thead', h('tr', ['Run', 'Задача', 'Статус', 'Попытка', 'Модель', 'Старт', 'Длительность']
      .map((t) => h('th', t)))),
    h('tbody', rows.map((r) => h('tr',
      h('td.mono', `#${r.run_id}`),
      h('td', r.title),
      h('td', statusBadge(r.status, { live: r.status === 'running' })),
      h('td', String((r.attempt || 0) + 1)),
      h('td.mono.small', r.model_alias || '—'),
      h('td.small.dim', r.started_at ? fmtClock(r.started_at, true) : '—'),
      h('td.mono.small', r.duration_ms !== null ? fmtDuration(r.duration_ms) : '—'))))), { flush: true });
}

/* ---------------- Живой счётчик времени ----------------
   Между перерисовками (WS-события приходят не каждую секунду) тикаем
   локально: только элементы с data-elapsed-from и только пока узел в DOM. */

function startTicker(root, data) {
  const base = Date.now();
  const timer = setInterval(() => {
    if (!document.body.contains(root)) { clearInterval(timer); return; }
    const delta = Date.now() - base;
    for (const el of root.querySelectorAll('[data-elapsed-from]')) {
      const from = Number(el.getAttribute('data-elapsed-from'));
      if (!Number.isFinite(from) || !el.getAttribute('data-elapsed-from')) continue;
      const text = fmtDuration(from + delta);
      el.textContent = el.classList.contains('wf-node-elapsed') ? `⏱ ${text}` : text;
    }
  }, 1000);
  if (String(data.mission.status) !== 'running') clearInterval(timer);
}

export default BuilderPage;
