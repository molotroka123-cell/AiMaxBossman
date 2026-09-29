/* ============================================================
   rave.js — Agentic Rave (Bossman 1.9, workstream G).
   Один prompt → несколько агентов, у каждого своя git-копия проекта.
   Та же логика, что `bossman rave …` в терминале: GET/POST /api/rave,
   /api/rave/{id}/pause|resume|stop {agent?}, /agents/{name}/diff, /stop-all.
   Бэкенд — bcc.rave.engine; страница ничего не решает сама.
   ============================================================ */

import { api, listOf } from '../api.js';
import { h, toast, toastOk, toastError, openModal, input, textarea } from '../components.js';
import { pageHead, panel, errorNote, btn, codeBlock, statusPill } from './_ui.js';

const state = { selected: '' };

async function act(path, body, okText, ctx) {
  try {
    await api.raw(path, { method: 'POST', body: body || {} });
    if (okText) toastOk(okText);
  } catch (e) { toastError(e); }
  ctx.refresh();
}

async function showDiff(rid, name) {
  const modal = openModal({ title: `Diff агента ${name} · ${rid}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  try {
    const d = await api.raw(`/api/rave/${rid}/agents/${encodeURIComponent(name)}/diff`);
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small.dim', d.stat || '(нет изменений)'));
    if (d.patch) modal.body.appendChild(codeBlock(d.patch));
  } catch (e) { modal.body.textContent = String(e.message || e); }
}

function agentRow(rid, a, ctx) {
  const tests = a.tests ? (a.tests.passed ? 'PASS' : 'FAIL') : '—';
  const text = ['failed', 'blocked', 'interrupted', 'stopped'].includes(a.status) && a.error ? a.error : (a.answer || a.step_label || '');
  const live = ['running', 'queued', 'pausing'].includes(a.status);
  return h('tr',
    h('td', h('b', a.name)),
    h('td.small', `${a.provider || '—'} / ${a.model || '—'}`),
    h('td.small', a.auth || '—'),
    h('td', statusPill(a.status), a.pause_reason === 'recovered_after_restart' ? h('span.small.dim', ' после перезапуска') : ''),
    h('td.small', `${a.step || 0}/${a.steps_total || '?'}`),
    h('td.small', String((a.changed_files || []).length)),
    h('td.small', tests),
    h('td.small', { style: 'max-width:28rem;white-space:pre-wrap' }, String(text).slice(0, 400)),
    h('td',
      live ? btn('Пауза', () => act(`/api/rave/${rid}/pause`, { agent: a.name }, `пауза: ${a.name}`, ctx), { size: 'sm' }) : '',
      a.status === 'paused' || (a.status === 'blocked') || a.status === 'interrupted'
        ? btn('Продолжить', () => act(`/api/rave/${rid}/resume`, { agent: a.name }, `продолжение: ${a.name}`, ctx), { size: 'sm' }) : '',
      !['done', 'failed', 'stopped', 'blocked', 'interrupted'].includes(a.status)
        ? btn('STOP', () => act(`/api/rave/${rid}/stop`, { agent: a.name }, `STOP: ${a.name}`, ctx), { size: 'sm', variant: 'danger' }) : '',
      btn('Diff', () => showDiff(rid, a.name), { size: 'sm' })));
}

function raveView(rave, ctx) {
  const rid = rave.id;
  const table = h('table.table',
    h('thead', h('tr', ...['Агент', 'Провайдер / модель', 'Вход', 'Статус', 'Шаг', 'Файлы', 'Тесты', 'Ответ / ошибка', ''].map((t) => h('th', t)))),
    h('tbody', ...(rave.agents || []).map((a) => agentRow(rid, a, ctx))));
  const conflicts = (rave.conflicts || []).filter((c) => c.file);
  return panel(`Рейв ${rid} · ${rave.status}`, h('div',
    h('div.small.dim', `prompt: ${rave.prompt}`),
    h('div.small.dim', `base ${String(rave.base_commit || '').slice(0, 10)} · ${rave.scratch ? 'scratch-проект' : rave.repo}`),
    h('div', { style: 'margin:.5rem 0' },
      btn('Пауза всех', () => act(`/api/rave/${rid}/pause`, {}, 'пауза рейва', ctx), { size: 'sm' }),
      btn('Продолжить всех', () => act(`/api/rave/${rid}/resume`, {}, 'продолжение рейва', ctx), { size: 'sm' }),
      btn('STOP рейва', () => act(`/api/rave/${rid}/stop`, {}, 'STOP рейва', ctx), { size: 'sm', variant: 'danger' })),
    table,
    conflicts.length ? h('div', h('b', `Конфликты (${conflicts.length}) — все версии сохранены:`),
      ...conflicts.map((c) => h('div.small', `${c.file}: ${(c.agents || []).join(' × ')} · ${c.auto_mergeable ? 'сливается автоматически' : 'нужно решение'} · ${c.artifacts}`)))
      : h('div.small.dim', 'Конфликтов нет'),
    h('div.small.dim', 'Применить результат агента в проект — только с разрешением владельца: bossman rave apply <id> <агент>.')));
}

const RavePage = {
  id: 'rave',
  title: 'Agentic Rave',
  icon: 'agents',
  nav: 'more',
  section: 'studio',

  async render(ctx) {
    let items = []; let err = null; let rave = null;
    try { items = listOf(await api.raw('/api/rave'), 'items'); } catch (e) { err = e; }
    const head = pageHead('Agentic Rave', 'Один prompt — несколько агентов, у каждого своя изолированная копия проекта.', {
      actions: [btn('STOP всех рейвов', () => act('/api/rave/stop-all', {}, 'STOP всех рейвов', ctx), { variant: 'danger' })],
    });
    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));
    const rid = state.selected && items.some((i) => i.id === state.selected) ? state.selected : (items[0] || {}).id;
    if (rid) { try { rave = await api.raw(`/api/rave/${rid}`); } catch (e) { rave = null; } }

    const promptEl = textarea({ rows: 3, placeholder: 'Задача для всех агентов' });
    const agentsEl = input({ value: 'mock:a,mock:b', placeholder: 'mock:a,local:qwen,claude:c,codex:x' });
    const startForm = panel('Новый рейв', h('div',
      promptEl, agentsEl,
      btn('Запустить', async () => {
        const prompt = promptEl.value.trim();
        if (!prompt) {          // no request and no console error for an empty task: say what is missing
          toast('Сначала введите задачу для агентов', { type: 'info' });
          promptEl.focus();
          return;
        }
        try {
          const r = await api.raw('/api/rave', { method: 'POST', body: { prompt, agents: [agentsEl.value] } });
          state.selected = r.id; toastOk(`Рейв ${r.id} запущен`);
        } catch (e) { toastError(e); }
        ctx.refresh();
      }, { variant: 'primary' })));

    const list = panel('Рейвы', items.length ? h('div', ...items.map((i) => h('div.small',
      h('a', { href: '#', onClick: (ev) => { ev.preventDefault(); state.selected = i.id; ctx.refresh(); } }, i.id),
      ` · ${i.status} · ${i.prompt} · ${Object.entries(i.agents || {}).map(([k, v]) => `${k}:${v}`).join(', ')}`))) : h('div.small.dim', 'Рейвов пока нет'));
    return h('div.bx-page', head, startForm, rave ? raveView(rave, ctx) : '', list);
  },

  onEvent(ev) { return typeof ev.kind === 'string' && ev.kind.startsWith('rave.'); },
};

export default RavePage;
