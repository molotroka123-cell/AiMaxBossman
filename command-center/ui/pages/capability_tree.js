import { api } from '../api.js';
import { h, toastError, toastOk } from '../components.js';
import { btn, pageHead, panel, pill } from './_ui.js';

/* Дерево развития: живая карта Bossman.
   Цвет листа = статус доказательства из карты (не PASS): зелёный — сохранённый прогон,
   синий — код, янтарный — отдельная ветка/подготовлено, фиолетовый — идея, красный — блокер,
   серебро — запись. Пульсирует то, над чем сейчас работает цикл; искра — зона в работе. */
const STATUS = {
  reported: ['сохранённый прогон', 'ok', '#5dff8f'], code: ['код', 'info', '#4fa8ff'],
  branch: ['отдельная ветка', 'warn', '#ffb547'], prepared: ['подготовлено', 'warn', '#ffd166'],
  idea: ['идея', 'idle', '#b98bff'], blocked: ['блокер', 'err', '#ff5470'],
  recorded: ['запись', 'idle', '#c9d4e5'], mixed: ['смешано', 'idle', '#ffe8a3'],
};
const LEGEND = ['reported', 'code', 'branch', 'idea', 'blocked', 'recorded'];
const JOB_TONE = { running: 'info', completed: 'ok', failed: 'err', blocked: 'err', started: 'info' };
const DONE = ['completed', 'failed', 'blocked'];
const SVGNS = 'http://www.w3.org/2000/svg';
const W = 1600, H = 880, TX = 800, TY = 540;
let selectedId = null;
let pollTimer = null;

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, String(v));
  for (const c of children.flat()) if (c != null) el.append(typeof c === 'string' ? document.createTextNode(c) : c);
  return el;
}

function rng(seed) {
  let x = 2166136261;
  for (const ch of String(seed)) x = Math.imul(x ^ ch.charCodeAt(0), 16777619);
  return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return ((x >>> 0) % 100000) / 100000; };
}

function bez(p0, p1, p2, p3, t) {
  const u = 1 - t;
  return [u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]];
}

function statusPill(value) {
  const row = STATUS[value] || [value || 'нет данных', 'idle'];
  return pill(row[0], { tone: row[1] });
}

function scene(nodes, state, onPick) {
  const families = nodes.filter(n => n.parent === 'bossman')
    .sort((a, b) => (a.id === 'jeff' ? -1 : b.id === 'jeff' ? 1 : 0));
  const kids = new Map();
  nodes.forEach(n => { if (!kids.has(n.parent)) kids.set(n.parent, []); kids.get(n.parent).push(n); });
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ct-svg', role: 'img',
    'aria-label': 'Дерево развития Bossman: ветви — направления, листья — возможности' });
  svg.append(s('defs', {},
    s('filter', { id: 'ct-glow', x: '-50%', y: '-50%', width: '200%', height: '200%' },
      s('feGaussianBlur', { stdDeviation: '3.2', result: 'b' }),
      s('feMerge', {}, s('feMergeNode', { in: 'b' }), s('feMergeNode', { in: 'SourceGraphic' }))),
    s('filter', { id: 'ct-soft', x: '-30%', y: '-30%', width: '160%', height: '160%' },
      s('feGaussianBlur', { stdDeviation: '7' })),
    s('linearGradient', { id: 'ct-bark', x1: '0', y1: '1', x2: '0', y2: '0' },
      s('stop', { offset: '0', 'stop-color': '#6f7fa8' }), s('stop', { offset: '.55', 'stop-color': '#dfe9ff' }),
      s('stop', { offset: '1', 'stop-color': '#ffffff' })),
    s('radialGradient', { id: 'ct-halo', cx: '.5', cy: '.62', r: '.55' },
      s('stop', { offset: '0', 'stop-color': '#ffe7a8', 'stop-opacity': '.42' }),
      s('stop', { offset: '1', 'stop-color': '#ffe7a8', 'stop-opacity': '0' }))));
  const sky = s('g', { class: 'ct-sky' });
  const r0 = rng('stars');
  for (let i = 0; i < 90; i++) {
    sky.append(s('circle', { cx: (r0() * W).toFixed(1), cy: (r0() * H * 0.75).toFixed(1), r: (r0() * 1.4 + 0.3).toFixed(2),
      class: 'ct-star', style: `animation-delay:${(r0() * 6).toFixed(2)}s` }));
  }
  svg.append(sky, s('ellipse', { cx: TX, cy: TY - 40, rx: 560, ry: 300, fill: 'url(#ct-halo)', opacity: '.55' }));
  const roots = s('g', { class: 'ct-roots' });
  for (let i = 0; i < 9; i++) {
    const dx = (i - 4) * 70;
    roots.append(s('path', { d: `M ${TX + dx * 0.15} ${H - 70} C ${TX + dx * 0.5} ${H - 40}, ${TX + dx} ${H - 30}, ${TX + dx * 1.6} ${H - 8}`,
      class: 'ct-root' }));
  }
  svg.append(roots);
  const trunkD = `M ${TX - 80} ${H - 50} C ${TX - 40} ${H - 160}, ${TX - 70} ${TY + 110}, ${TX - 26} ${TY} `
    + `L ${TX + 26} ${TY} C ${TX + 70} ${TY + 110}, ${TX + 40} ${H - 160}, ${TX + 80} ${H - 50} Z`;
  svg.append(s('path', { d: trunkD, class: 'ct-trunk-glow', filter: 'url(#ct-soft)' }), s('path', { d: trunkD, class: 'ct-trunk' }),
    s('text', { x: TX + 4, y: TY + 175, class: 'ct-trunk-label', transform: `rotate(-84 ${TX + 4} ${TY + 175})` }, 'BOSSMAN'));
  const leafEls = new Map();
  const n = families.length;
  families.forEach((fam, fi) => {
    // Crown: the angle fans across the top; branches leave the trunk at staggered heights,
    // rise first and then reach out, and each forks once so the crown reads as a tree, not a fan.
    const a = Math.PI * (0.93 - (0.86 * fi) / Math.max(1, n - 1));
    const side = Math.cos(a);
    const end = [TX + 530 * side, Math.max(78, TY - 60 - 400 * Math.sin(a))];
    const p0 = [TX + side * 18, TY + 30 - Math.abs(side) * 70];
    const p1 = [TX + side * 90, TY - 150 - 40 * Math.sin(a)];
    const p2 = [TX + (end[0] - TX) * 0.62, end[1] + 60 + 30 * Math.abs(side)];
    const d = `M ${p0[0]} ${p0[1]} C ${p1[0]} ${p1[1]}, ${p2[0]} ${p2[1]}, ${end[0]} ${end[1]}`;
    const forkFrom = bez(p0, p1, p2, end, 0.55);
    const forkEnd = [forkFrom[0] + side * 150 + (side >= 0 ? 30 : -30), forkFrom[1] + (Math.abs(side) > 0.5 ? 70 : -60)];
    const fk1 = [forkFrom[0] + (forkEnd[0] - forkFrom[0]) * 0.3, forkFrom[1] - 30];
    const fk2 = [forkFrom[0] + (forkEnd[0] - forkFrom[0]) * 0.75, forkEnd[1] - 10];
    const fd = `M ${forkFrom[0]} ${forkFrom[1]} C ${fk1[0]} ${fk1[1]}, ${fk2[0]} ${fk2[1]}, ${forkEnd[0]} ${forkEnd[1]}`;
    const g = s('g', { class: `ct-family${fam.id === 'jeff' ? ' is-jeff' : ''}`,
      style: `transform-origin:${TX}px ${TY}px;animation-duration:${7 + (fi % 5)}s;animation-delay:${-fi * 0.7}s` });
    g.append(s('path', { d, class: 'ct-branch-glow', filter: 'url(#ct-soft)' }), s('path', { d, class: 'ct-branch' }),
      s('path', { d: fd, class: 'ct-branch-glow ct-fork', filter: 'url(#ct-soft)' }), s('path', { d: fd, class: 'ct-branch ct-fork' }));
    const leaves = kids.get(fam.id) || [];
    const r = rng(fam.id);
    const spread = Math.min(95, 26 + Math.sqrt(leaves.length) * 6);
    const leafGroup = s('g', { filter: 'url(#ct-glow)' });
    leaves.forEach((leaf, li) => {
      const onFork = li % 3 === 2;
      const curve = onFork ? [forkFrom, fk1, fk2, forkEnd] : [p0, p1, p2, end];
      const t = Math.min(1, (onFork ? 0.15 : 0.4) + (onFork ? 0.85 : 0.6) * ((li + r()) / Math.max(1, leaves.length)));
      const [bx, by] = bez(...curve, t);
      const [qx, qy] = bez(...curve, Math.min(1, t + 0.01));
      const len = Math.hypot(qx - bx, qy - by) || 1;
      const off = (r() - 0.5) * 2 * spread * (0.35 + t * 0.65);
      const x = bx + (-(qy - by) / len) * off;
      const y = by + ((qx - bx) / len) * off;
      if (li % 3 === 0) {
        g.append(s('path', { d: `M ${bx.toFixed(1)} ${by.toFixed(1)} Q ${((bx + x) / 2 + 6).toFixed(1)} ${((by + y) / 2 - 8).toFixed(1)} ${x.toFixed(1)} ${y.toFixed(1)}`,
          class: 'ct-twig' }));
      }
      const color = (STATUS[leaf.status] || STATUS.mixed)[2];
      const el = s('ellipse', { cx: x.toFixed(1), cy: y.toFixed(1), rx: 6.2, ry: 3.1,
        transform: `rotate(${Math.round(r() * 180)} ${x.toFixed(1)} ${y.toFixed(1)})`, fill: color,
        class: `ct-leaf is-${leaf.status}${li % 4 === 0 ? ' twinkle' : ''}`, 'data-node': leaf.id, tabindex: '0',
        style: `animation-delay:${(r() * 5).toFixed(2)}s` },
      s('title', {}, `${leaf.label} · ${(STATUS[leaf.status] || [leaf.status])[0]}`));
      el.addEventListener('click', () => onPick(leaf));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(leaf); } });
      leafEls.set(leaf.id, { el, x, y });
      leafGroup.append(el);
    });
    g.append(leafGroup);
    const anchor = end[0] < TX - 40 ? 'end' : end[0] > TX + 40 ? 'start' : 'middle';
    const lx = end[0] + (anchor === 'end' ? -14 : anchor === 'start' ? 14 : 0);
    const label = s('text', { x: lx.toFixed(1), y: (end[1] - 12).toFixed(1), 'text-anchor': anchor,
      class: `ct-family-label${fam.id === 'jeff' ? ' is-jeff' : ''}`, tabindex: '0', 'data-node': fam.id },
    String(fam.label || fam.id).split('·')[0].trim().toUpperCase(), s('title', {}, String(fam.label || fam.id)));
    label.addEventListener('click', () => onPick(fam));
    label.addEventListener('keydown', (e) => { if (e.key === 'Enter') onPick(fam); });
    leafEls.set(fam.id, { el: label, x: end[0], y: end[1] });
    g.append(label);
    svg.append(g);
  });
  const fx = s('g', { class: 'ct-fireflies' });
  const rf = rng('fireflies');
  for (let i = 0; i < 26; i++) {
    fx.append(s('circle', { cx: (TX + (rf() - 0.5) * 1200).toFixed(1), cy: (TY + (rf() - 0.2) * 300).toFixed(1),
      r: (1.2 + rf() * 1.8).toFixed(2), class: 'ct-firefly',
      style: `animation-delay:${(rf() * 9).toFixed(2)}s;animation-duration:${(7 + rf() * 6).toFixed(2)}s` }));
  }
  const marks = s('g', { class: 'ct-marks' });
  svg.append(fx, marks);
  const paint = (st) => {
    marks.replaceChildren();
    leafEls.forEach(({ el }) => el.classList.remove('is-active', 'is-selected', 'is-working'));
    const add = (id, cls) => {
      const hit = leafEls.get(id);
      if (!hit) return;
      hit.el.classList.add(cls);
      if (cls === 'is-active') marks.append(s('circle', { cx: hit.x, cy: hit.y, r: 9, class: 'ct-pulse' }));
      if (cls === 'is-selected') marks.append(s('circle', { cx: hit.x, cy: hit.y, r: 12, class: 'ct-ring' }));
      if (cls === 'is-working') {
        marks.append(s('g', { class: 'ct-orbit', style: `transform-origin:${hit.x}px ${hit.y}px` },
          s('circle', { cx: hit.x + 15, cy: hit.y, r: 3, class: 'ct-spark' })));
      }
    };
    (st.active || []).forEach(id => add(id, 'is-active'));
    (st.working || []).forEach(id => add(id, 'is-working'));
    if (st.selected) add(st.selected, 'is-selected');
  };
  paint(state);
  return { svg, paint };
}

function detail(node, note, save, work, jobs) {
  if (!node) return h('p.dim', 'Выберите ветку или лист дерева.');
  const text = h('textarea.input', { rows: '4', placeholder: 'Что уже есть, что сейчас делаем, что проверить…' }, note?.text || '');
  const state = h('select.input',
    h('option', { value: 'note', selected: !note || note.state === 'note' }, 'заметка'),
    h('option', { value: 'working', selected: note?.state === 'working' }, 'сейчас делаем'),
    h('option', { value: 'blocked', selected: note?.state === 'blocked' }, 'заблокировано'),
    h('option', { value: 'done', selected: note?.state === 'done' }, 'собрано (не является PASS)'));
  const sources = (node.sources || []).map(src => h('p.xsmall',
    h('a', { href: src.url, target: '_blank', rel: 'noopener noreferrer' }, src.path),
    ` · ${src.branch} · ${String(src.sha || '').slice(0, 12)}`));
  const wish = h('textarea.input', { rows: '2', placeholder: 'Что именно улучшить в этой зоне (необязательно)…' });
  const job = jobs.find(j => j.node_id === node.id);
  const running = !!job && !DONE.includes(job.status);
  return h('div.stack.sm', h('div.row.gap-sm', statusPill(node.status), note ? pill(note.state, { tone: note.state === 'blocked' ? 'err' : note.state === 'done' ? 'ok' : 'warn' }) : null),
    h('h3', node.label), h('p.small', node.detail || 'Описание не записано.'),
    node.next_action ? h('p.small.dim', `Следующий шаг: ${node.next_action}`) : null, ...sources,
    h('label.field', h('span.field-label', 'Рабочая запись'), text),
    h('label.field', h('span.field-label', 'Метка владельца'), state),
    btn('Сохранить в Bossman', () => save(node.id, text.value, state.value), { variant: 'primary' }),
    h('div.ct-work-box',
      h('b', 'Bossman в этой зоне'),
      h('p.xsmall.dim', 'Одна задача в изолированной копии: правки только в файлах зоны, проверка тестами Bossman, '
        + 'в проект — только после вашего подтверждения. Отчёты придут сюда и в Telegram-пульт.'),
      job ? h('p.small', 'Последняя задача: ', pill(job.status, { tone: JOB_TONE[job.status] || 'idle', live: running }),
        ` · ${job.task_id}`) : null,
      h('label.field', h('span.field-label', 'Пожелание'), wish),
      btn(running ? 'Bossman уже работает здесь' : '🚀 Bossman, работай здесь', () => work(node.id, wish.value),
        { variant: 'primary', disabled: running })));
}

function campaignBody(activity, control) {
  const campaign = activity.campaign || {};
  const cycle = campaign.cycle || {};
  const phase = cycle.phase || '—';
  const v15 = activity.campaign_source === 'v15_owner_run';
  return h('div.stack.sm',
    h('p.small.dim', `Кампания ${campaign.campaign_id || '—'} · ${v15 ? 'Bossman 1.5 owner-run' : 'Evolution Loop'} · ${campaign.status || 'NO_CAMPAIGN'}`),
    h('p', `Задача: ${cycle.task || 'ещё не выбрана'}`),
    h('p.small', `Цикл ${cycle.index ?? '—'} · фаза ${phase}`),
    h('p.small.dim', campaign.last_action?.text || campaign.halt_reason || 'Ожидаем состояние цикла.'),
    h('div.row.gap-sm',
      v15 ? h('span.small.dim', 'Пауза: у 1.5 owner-run есть только STOP')
        : btn('Пауза', () => control('/api/evolution/pause'), { variant: 'secondary' }),
      btn('STOP', () => control(v15 ? '/api/v15/owner-run/stop' : '/api/evolution/stop'), { variant: 'danger' })));
}

function workList(jobs) {
  if (!jobs.length) return h('p.dim', 'Пока ни одной зоны в работе. Выберите лист и нажмите «Bossman, работай здесь».');
  return h('div.stack.sm', ...jobs.slice().reverse().slice(0, 12).map(j => h('div.row.gap-sm.ct-job',
    pill(j.status, { tone: JOB_TONE[j.status] || 'idle', live: !DONE.includes(j.status) }),
    h('span', j.label), h('span.xsmall.dim', `${j.task_id} · ${(j.changed_files || []).length} файл(ов) · ${j.created_at || ''}`))));
}

const Page = {
  id: 'capability-tree', title: 'Дерево развития', icon: 'agents', nav: 'primary', section: 'work',
  async render(ctx) {
    let payload;
    try { payload = await api.raw('/api/capability-tree'); }
    catch (e) { return h('div.bx-page', pageHead('Дерево развития', 'Не удалось прочитать карту'), h('p', String(e))); }
    const nodes = (payload.tree || {}).nodes || [];
    const byId = new Map(nodes.map(n => [n.id, n]));
    let activity = payload.activity || {};
    let notes = payload.notes || {};
    let jobs = payload.work || [];
    const firstActive = (activity.active_matches || [])[0]?.node_id;
    let selected = byId.get(selectedId) || byId.get(firstActive) || byId.get('bossman') || nodes[0];
    const details = h('div');
    const campaignBox = h('div');
    const workBox = h('div');
    const marks = () => ({ active: (activity.active_matches || []).map(x => x.node_id), selected: selected?.id,
      working: jobs.filter(j => !DONE.includes(j.status)).map(j => j.node_id) });
    let paint = () => {};
    let save;
    let work;
    const renderDetail = () => details.replaceChildren(detail(selected, notes[selected?.id], save, work, jobs));
    const pick = (node) => { selected = node; selectedId = node.id; renderDetail(); paint(marks()); };
    let poll;
    save = async (nodeId, text, state) => {
      try {
        const out = await api.raw('/api/capability-tree/note', { method: 'POST', body: { node_id: nodeId, text, state } });
        notes = { ...notes, [nodeId]: out.note };
        if (!out.note) delete notes[nodeId];
        toastOk('Запись сохранена'); renderDetail();
      } catch (e) { toastError(e, 'Не удалось сохранить запись'); }
    };
    work = async (nodeId, wish) => {
      try {
        const out = await api.raw('/api/capability-tree/work', { method: 'POST', body: { node_id: nodeId, instruction: wish || '' } });
        toastOk(`Bossman начал работу в зоне: задача ${out.task?.id || out.job?.task_id}`);
        jobs = [...jobs.filter(j => j.node_id !== nodeId), out.job];
        workBox.replaceChildren(workList(jobs)); renderDetail(); paint(marks());
      } catch (e) { toastError(e, 'Bossman не смог начать работу в зоне'); }
    };
    const control = async (path) => {
      try { await api.raw(path, { method: 'POST', body: {} }); toastOk('Команда передана циклу'); await poll(); }
      catch (e) { toastError(e, 'Цикл не принял команду'); }
    };
    const scan = async (sourceRepo) => {
      try {
        const out = await api.raw('/api/capability-tree/scan', { method: 'POST', body: sourceRepo ? { source_repo: sourceRepo } : {} });
        toastOk(`Сканер завершён: ${out.unmapped_capability_files?.length || 0} файлов требуют сверки`); ctx.refresh();
      } catch (e) {
        const candidates = e.detail?.candidates || [];
        if (!sourceRepo && e.code === 'CAPABILITY_SCAN_SOURCE_REQUIRED' && candidates.length > 1) {
          const pickRepo = window.prompt(`Найдено несколько чекаутов Bossman. Какой проверить?\n${candidates.join('\n')}`, candidates[0]);
          if (pickRepo && pickRepo.trim()) return scan(pickRepo.trim());
          return;
        }
        toastError(e, 'Сканер не завершён');
      }
    };
    const statusCounts = {};
    nodes.forEach(n => { statusCounts[n.status] = (statusCounts[n.status] || 0) + 1; });
    const built = scene(nodes, marks(), pick);
    paint = built.paint;
    const legend = h('div.ct-legend', ...LEGEND.map(k => h('span.ct-legend-row',
      h('i', { style: `background:${STATUS[k][2]};color:${STATUS[k][2]}` }), `${STATUS[k][0]} · ${statusCounts[k] || 0}`)),
    h('span.ct-legend-note', 'цвет = уровень доказательства, не PASS'));
    const stage = h('div.ct-stage', built.svg, legend);
    renderDetail();
    campaignBox.replaceChildren(campaignBody(activity, control));
    workBox.replaceChildren(workList(jobs));
    poll = async () => {
      if (!stage.isConnected) { clearInterval(pollTimer); pollTimer = null; return; }
      try {
        const lite = await api.raw('/api/capability-tree?lite=1');
        activity = lite.activity || activity;
        jobs = lite.work || jobs;
        campaignBox.replaceChildren(campaignBody(activity, control));
        workBox.replaceChildren(workList(jobs));
        paint(marks());
      } catch { /* the next tick retries; a failed poll never blanks the page */ }
    };
    clearInterval(pollTimer);
    pollTimer = setInterval(poll, 4000);
    const scanInfo = payload.scan;
    const campaign = activity.campaign || {};
    return h('div.bx-page.ct-page',
      pageHead('Дерево развития', 'Живая карта Bossman: что существует, что сейчас меняется и чем это доказано.', {
        pills: [pill(campaign.status || 'NO_CAMPAIGN', { tone: campaign.loop_running ? 'ok' : campaign.status === 'BLOCKED' ? 'err' : 'idle', live: !!campaign.loop_running }),
          pill(`${nodes.length} узлов`, { tone: 'info' })],
        actions: [btn('Проверить репозиторий без ИИ', () => scan(), { variant: 'secondary', iconName: 'search' })],
      }),
      stage,
      h('div.ct-grid',
        panel('Сейчас улучшается', campaignBox),
        panel('Выбранная зона', details),
        panel('Работа по зонам', workBox),
        panel('Автосканер', scanInfo
          ? h('div.stack.sm', h('p', `${scanInfo.branch_count} веток · ${scanInfo.union_path_count} путей · без ИИ`),
            h('p.small', `${scanInfo.unmapped_capability_files?.length || 0} файлов требуют классификации; ${scanInfo.new_since_previous?.length || 0} новых с прошлого прогона.`),
            h('p.xsmall.dim', `fingerprint ${String(scanInfo.fingerprint || '').slice(0, 16)}`))
          : h('p.dim', 'Сканер ещё не запускался. Первый запуск создаст честную базовую точку.'))),
      h('details.ct-list', h('summary', 'Список всех узлов (поиск и клавиатура)'),
        h('div.cap-tree-crown', ...nodes.filter(n => n.parent === 'bossman').map(f => h('section.cap-tree-family',
          h('button', { type: 'button', class: 'cap-tree-family-title', onClick: () => pick(f) }, f.label),
          h('div.cap-tree-leaves', ...nodes.filter(n => n.parent === f.id).map(n => h('button', {
            type: 'button', class: `cap-tree-leaf is-${n.status}`, title: n.detail || '', onClick: () => pick(n),
          }, h('span.cap-tree-dot'), h('span', n.label)))))))));
  },
  // The page polls a light view itself: a re-render on every event would restart the animation.
  onEvent() { return false; },
};

export default Page;
