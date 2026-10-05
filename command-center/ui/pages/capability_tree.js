import { api } from '../api.js';
import { h, toastError, toastOk } from '../components.js';
import { btn, pageHead, panel, pill } from './_ui.js';

const STATUS = {
  reported: ['сохранённый прогон', 'ok'], code: ['код', 'info'], idea: ['идея', 'idle'],
  branch: ['отдельная ветка', 'warn'], blocked: ['блокер', 'err'], prepared: ['подготовлено', 'warn'],
  recorded: ['запись', 'idle'], mixed: ['смешано', 'idle'],
};
let refreshTimer = null;

function statusPill(value) {
  const row = STATUS[value] || [value || 'нет данных', 'idle'];
  return pill(row[0], { tone: row[1] });
}

function familyTree(nodes, activity, onPick) {
  const active = new Set((activity.active_matches || []).map(x => x.node_id));
  const families = nodes.filter(n => n.parent === 'bossman');
  const byParent = new Map();
  nodes.forEach(n => {
    if (!byParent.has(n.parent)) byParent.set(n.parent, []);
    byParent.get(n.parent).push(n);
  });
  return h('div.cap-tree',
    h('div.cap-tree-trunk', h('b', 'BOSSMAN'), h('span', 'единый продукт')),
    h('div.cap-tree-crown', ...families.map(f => h('section', {
      class: `cap-tree-family${f.id === 'jeff' ? ' is-jeff' : ''}`,
    },
    h('button', { type: 'button', class: 'cap-tree-family-title', onClick: () => onPick(f) }, f.label),
    h('div.cap-tree-leaves', ...(byParent.get(f.id) || []).map(n => h('button', {
      type: 'button', class: `cap-tree-leaf is-${n.status}${active.has(n.id) ? ' is-active' : ''}`,
      title: n.detail || '', onClick: () => onPick(n),
    }, h('span.cap-tree-dot'), h('span', n.label))))))));
}

function detail(node, note, save) {
  if (!node) return h('p.dim', 'Выберите ветку или лист дерева.');
  const text = h('textarea.input', { rows: '4', placeholder: 'Что уже есть, что сейчас делаем, что проверить…' }, note?.text || '');
  const state = h('select.input',
    h('option', { value: 'note', selected: !note || note.state === 'note' }, 'заметка'),
    h('option', { value: 'working', selected: note?.state === 'working' }, 'сейчас делаем'),
    h('option', { value: 'blocked', selected: note?.state === 'blocked' }, 'заблокировано'),
    h('option', { value: 'done', selected: note?.state === 'done' }, 'собрано (не является PASS)'));
  const sources = (node.sources || []).map(s => h('p.xsmall',
    h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer' }, s.path),
    ` · ${s.branch} · ${String(s.sha || '').slice(0, 12)}`));
  return h('div.stack.sm', h('div.row.gap-sm', statusPill(node.status), note ? pill(note.state, { tone: note.state === 'blocked' ? 'err' : note.state === 'done' ? 'ok' : 'warn' }) : null),
    h('h3', node.label), h('p.small', node.detail || 'Описание не записано.'), ...sources,
    h('label.field', h('span.field-label', 'Рабочая запись'), text),
    h('label.field', h('span.field-label', 'Метка владельца'), state),
    btn('Сохранить в Bossman', () => save(node.id, text.value, state.value), { variant: 'primary' }));
}

const Page = {
  id: 'capability-tree', title: 'Дерево развития', icon: 'agents', nav: 'primary', section: 'work',
  async render(ctx) {
    let payload;
    try { payload = await api.raw('/api/capability-tree'); }
    catch (e) { return h('div.bx-page', pageHead('Дерево развития', 'Не удалось прочитать карту'), h('p', String(e))); }
    const tree = payload.tree || {};
    const nodes = tree.nodes || [];
    const activity = payload.activity || {};
    const campaign = activity.campaign || {};
    const notes = payload.notes || {};
    let selected = nodes.find(n => n.id === (activity.active_matches || [])[0]?.node_id)
      || nodes.find(n => n.id === 'bossman') || nodes[0];
    const details = h('div');
    const save = async (nodeId, text, state) => {
      try {
        await api.raw('/api/capability-tree/note', { method: 'POST', body: { node_id: nodeId, text, state } });
        toastOk('Запись сохранена'); ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось сохранить запись'); }
    };
    const pick = node => { selected = node; details.replaceChildren(detail(selected, notes[selected.id], save)); };
    details.append(detail(selected, notes[selected?.id], save));
    const scan = async (sourceRepo) => {
      try {
        const out = await api.raw('/api/capability-tree/scan', { method: 'POST', body: sourceRepo ? { source_repo: sourceRepo } : {} });
        toastOk(`Сканер завершён: ${out.unmapped_capability_files?.length || 0} файлов требуют сверки`); ctx.refresh();
      } catch (e) {
        const candidates = e.detail?.candidates || [];
        if (!sourceRepo && e.code === 'CAPABILITY_SCAN_SOURCE_REQUIRED' && candidates.length > 1) {
          const pick = window.prompt(`Найдено несколько чекаутов Bossman. Какой проверить?\n${candidates.join('\n')}`, candidates[0]);
          if (pick && pick.trim()) return scan(pick.trim());
          return;
        }
        toastError(e, 'Сканер не завершён');
      }
    };
    clearTimeout(refreshTimer);
    if (campaign.loop_running) refreshTimer = setTimeout(() => ctx.scheduleRefresh(), 3000);
    const cycle = campaign.cycle || {};
    const phase = cycle.phase || '—';
    const scanInfo = payload.scan;
    // Pause/STOP go to the owner control of the campaign actually shown: the
    // 1.5 owner-run has its own STOP and no pause of its own.
    const v15 = activity.campaign_source === 'v15_owner_run';
    const control = async (path) => {
      try { await api.raw(path, { method: 'POST', body: {} }); toastOk('Команда передана циклу'); ctx.refresh(); }
      catch (e) { toastError(e, 'Цикл не принял команду'); }
    };
    return h('div.bx-page',
      pageHead('Дерево развития', 'Живая карта Bossman: что существует, что сейчас меняется и чем это доказано.', {
        pills: [pill(campaign.status || 'NO_CAMPAIGN', { tone: campaign.loop_running ? 'ok' : campaign.status === 'BLOCKED' ? 'err' : 'idle', live: !!campaign.loop_running }),
          pill(`цикл ${cycle.index ?? '—'} · ${phase}`, { tone: campaign.loop_running ? 'info' : 'idle' })],
        actions: [btn('Проверить репозиторий без ИИ', () => scan(), { variant: 'secondary', iconName: 'search' })],
      }),
      panel('Сейчас улучшается', h('div.stack.sm',
        h('p.small.dim', `Кампания ${campaign.campaign_id || '—'} · ${v15 ? 'Bossman 1.5 owner-run' : 'Evolution Loop'}`),
        h('p', `Задача: ${cycle.task || 'ещё не выбрана'}`),
        h('p.small', `Цикл ${cycle.index ?? '—'} · фаза ${phase}`),
        h('p.small.dim', campaign.last_action?.text || campaign.halt_reason || 'Ожидаем состояние цикла.'),
        h('div.row.gap-sm',
          v15 ? h('span.small.dim', 'Пауза: у 1.5 owner-run есть только STOP')
            : btn('Пауза', () => control('/api/evolution/pause'), { variant: 'secondary' }),
          btn('STOP', () => control(v15 ? '/api/v15/owner-run/stop' : '/api/evolution/stop'), { variant: 'danger' })))),
      panel('Карта', familyTree(nodes, activity, pick)),
      panel('Выбранный узел', details),
      panel('Автосканер', scanInfo
        ? h('div.stack.sm', h('p', `${scanInfo.branch_count} веток · ${scanInfo.union_path_count} путей · без ИИ`),
          h('p.small', `${scanInfo.unmapped_capability_files?.length || 0} файлов требуют классификации; ${scanInfo.new_since_previous?.length || 0} новых с прошлого прогона.`),
          h('p.xsmall.dim', `fingerprint ${String(scanInfo.fingerprint || '').slice(0, 16)}`))
        : h('p.dim', 'Сканер ещё не запускался. Первый запуск создаст честную базовую точку.')));
  },
  onEvent(ev) { return String(ev.kind || '').startsWith('evolution.'); },
};

export default Page;
