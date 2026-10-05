import { api } from '../api.js';
import { h, toastError, toastOk } from '../components.js';
import { btn, pageHead, panel, pill } from './_ui.js';
import { createScene } from './capability_tree_scene.js';

/* Дерево развития: живая карта Bossman (сцена — capability_tree_scene.js).
   Цвет листа = статус доказательства из карты (не PASS): зелёный — сохранённый прогон,
   синий — код, янтарный — отдельная ветка/подготовлено, фиолетовый — идея, красный — блокер,
   серебро — запись. Маяк — над чем работает цикл; орбитальная искра — зона в работе. */
const STATUS = {
  reported: ['сохранённый прогон', 'ok', '#5dff8f'], code: ['код', 'info', '#4fa8ff'],
  branch: ['отдельная ветка', 'warn', '#ffb547'], prepared: ['подготовлено', 'warn', '#ffd166'],
  idea: ['идея', 'idle', '#b98bff'], blocked: ['блокер', 'err', '#ff5470'],
  recorded: ['запись', 'idle', '#c9d4e5'], mixed: ['смешано', 'idle', '#ffe8a3'],
};
const COLORS = Object.fromEntries(Object.entries(STATUS).map(([k, v]) => [k, v[2]]));
const LEGEND = ['reported', 'code', 'branch', 'idea', 'blocked', 'recorded'];
const JOB_TONE = { running: 'info', completed: 'ok', failed: 'err', blocked: 'err', started: 'info' };
const DONE = ['completed', 'failed', 'blocked'];
let selectedId = null;
let workerChoice = 'openrouter-free';
let pollTimer = null;

function statusPill(value) {
  const row = STATUS[value] || [value || 'нет данных', 'idle'];
  return pill(row[0], { tone: row[1] });
}

function detail(node, note, save, work, jobs, workers) {
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
  const worker = h('select.input', { 'data-testid': 'zone-worker', onChange: (e) => { workerChoice = e.target.value; } },
    ...(workers.length ? workers : [{ id: 'local', label: 'Локальная модель Bossman' }]).map(w =>
      h('option', { value: w.id, selected: w.id === workerChoice }, w.label)));
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
        ` · ${job.task_id} · ${job.worker_label || ''}`) : null,
      h('label.field', h('span.field-label', 'Исполнитель (через Bossman)'), worker),
      h('label.field', h('span.field-label', 'Пожелание'), wish),
      btn(running ? 'Bossman уже работает здесь' : '🚀 Bossman, работай здесь', () => work(node.id, wish.value, worker.value),
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
    h('span', j.label), h('span.xsmall.dim', `${j.task_id} · ${j.worker_label || 'локально'} · ${(j.changed_files || []).length} файл(ов) · ${j.created_at || ''}`))));
}

const Page = {
  id: 'capability-tree', title: 'Дерево развития', icon: 'agents', nav: 'primary', section: 'work',
  async render(ctx) {
    let payload;
    try { payload = await api.raw('/api/capability-tree'); }
    catch (e) { return h('div.bx-page', pageHead('Дерево развития', 'Не удалось прочитать карту'), h('p', String(e))); }
    const nodes = (payload.tree || {}).nodes || [];
    const workers = payload.workers || [];
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
    let scene = null;
    let save;
    let work;
    let poll;
    const renderDetail = () => details.replaceChildren(detail(selected, notes[selected?.id], save, work, jobs, workers));
    const pick = (node) => { selected = node; selectedId = node.id; renderDetail(); scene?.update(marks()); };
    save = async (nodeId, text, state) => {
      try {
        const out = await api.raw('/api/capability-tree/note', { method: 'POST', body: { node_id: nodeId, text, state } });
        notes = { ...notes, [nodeId]: out.note };
        if (!out.note) delete notes[nodeId];
        toastOk('Запись сохранена'); renderDetail();
      } catch (e) { toastError(e, 'Не удалось сохранить запись'); }
    };
    work = async (nodeId, wish, worker) => {
      try {
        const out = await api.raw('/api/capability-tree/work', { method: 'POST',
          body: { node_id: nodeId, instruction: wish || '', worker: worker || 'local' } });
        toastOk(`Bossman начал работу в зоне: задача ${out.task?.id || out.job?.task_id} · ${out.job?.worker_label || ''}`);
        jobs = [...jobs.filter(j => j.node_id !== nodeId), out.job];
        workBox.replaceChildren(workList(jobs)); renderDetail(); scene?.update(marks());
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
    scene = createScene({ nodes, colors: COLORS, onPick: pick });
    scene.update(marks());
    const legend = h('div.ct-legend', ...LEGEND.map(k => h('span.ct-legend-row',
      h('i', { style: `background:${STATUS[k][2]};color:${STATUS[k][2]}` }), `${STATUS[k][0]} · ${statusCounts[k] || 0}`)),
    h('span.ct-legend-note', 'цвет = уровень доказательства, не PASS'));
    const stage = h('div.ct-stage', scene.el, legend);
    stage.ctScene = scene;  // automation hook (smoke tests): the same pick path as a click
    renderDetail();
    campaignBox.replaceChildren(campaignBody(activity, control));
    workBox.replaceChildren(workList(jobs));
    poll = async () => {
      if (!stage.isConnected) { clearInterval(pollTimer); pollTimer = null; scene.destroy(); return; }
      try {
        const lite = await api.raw('/api/capability-tree?lite=1');
        activity = lite.activity || activity;
        jobs = lite.work || jobs;
        campaignBox.replaceChildren(campaignBody(activity, control));
        workBox.replaceChildren(workList(jobs));
        scene.update(marks());
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
          h('button', { type: 'button', class: 'cap-tree-family-title', 'data-node': f.id, onClick: () => pick(f) }, f.label),
          h('div.cap-tree-leaves', ...nodes.filter(n => n.parent === f.id).map(n => h('button', {
            type: 'button', class: `cap-tree-leaf is-${n.status}`, 'data-node': n.id, title: n.detail || '', onClick: () => pick(n),
          }, h('span.cap-tree-dot'), h('span', n.label)))))))));
  },
  // The page polls a light view itself: a re-render on every event would restart the animation.
  onEvent() { return false; },
};

export default Page;
