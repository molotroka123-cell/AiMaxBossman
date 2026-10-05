/* ============================================================
   rave.js — Agentic Rave (Bossman 1.9, workstream G).
   Один prompt → несколько агентов, у каждого своя git-копия проекта.
   Та же логика, что `bossman rave …` в терминале: GET/POST /api/rave,
   /api/rave/{id}/pause|resume|stop {agent?}, /events?after=, /agents/{name}/diff,
   /agents/{name}/apply (+ разрешение владельца), /stop-all, /prune,
   /connectors (вход CLI, версия, opt-in) и /pool (пул СВОИХ аккаунтов, по умолчанию выключен).
   Бэкенд — bcc.rave.engine; страница ничего не решает сама.
   ============================================================ */

import { api, listOf } from '../api.js';
import { h, toast, toastOk, toastError, openModal, confirmDialog, input, textarea, select, checkbox } from '../components.js';
import { pageHead, panel, errorNote, btn, codeBlock, pill, field } from './_ui.js';

const CONNECTORS_TTL_MS = 15000;
const EVENTS_KEEP = 400;
const EVENTS_SHOWN = 60;

const state = {
  selected: '',
  connectors: null, connectorsAt: 0, connectorsErr: null,
  pool: null, poolErr: null,
  events: {},                          // rid -> { cursor, items }
  form: { prompt: '', agents: 'mock:a,mock:b', repo: '', allow: '', test: '' },
  prune: { days: '14', report: null },
};

/* ---------------------------------------------------------------- подписи */

const AGENT_STATUS = {
  queued: ['в очереди', 'warn'], running: ['работает', 'info'], pausing: ['ставится на паузу', 'warn'],
  paused: ['на паузе', 'warn'], stopping: ['останавливается', 'warn'], done: ['готово', 'ok'],
  failed: ['ошибка', 'err'], stopped: ['остановлен', 'idle'], blocked: ['заблокирован', 'warn'],
  interrupted: ['прерван', 'warn'],
};
const FINAL = ['done', 'failed', 'stopped', 'blocked', 'interrupted'];
const LIVE = ['running', 'queued', 'pausing'];

const EVENT_LABEL = {
  created: 'рейв создан', agent_started: 'агент запущен', workspace_ready: 'копия проекта готова',
  step_started: 'шаг начат', step_done: 'шаг завершён', agent_paused: 'пауза', agent_pausing: 'ставится на паузу',
  agent_resumed: 'продолжение', agent_rerun: 'повторный запуск', suspended: 'процессы приостановлены',
  agent_done: 'агент закончил', agent_failed: 'агент: ошибка', agent_stopped: 'агент остановлен',
  agent_blocked: 'агент заблокирован', agent_interrupted: 'агент прерван', conflict: 'конфликт файлов',
  stop: 'STOP', recovered: 'восстановлено после перезапуска', apply_requested: 'запрошено разрешение на применение',
  apply_conflict: 'применение отклонено: конфликт', applied: 'применено в проект', pruned: 'рабочие копии удалены',
  pool_account: 'аккаунт выбран', pool_limited: 'лимит аккаунта исчерпан', pool_switch: 'ПЕРЕКЛЮЧЕНИЕ аккаунта',
  pool_exhausted: 'нет готового аккаунта', pool_optin_requested: 'запрошено разрешение на пул',
  pool_enabled: 'пул включён', pool_changed: 'пул изменён',
};

const POOL_STATE = {
  ready: ['готов', 'ok'], limited: ['лимит', 'warn'], needs_login: ['нужен вход', 'err'],
  not_subscription: ['вход не по подписке', 'err'], error: ['ошибка', 'err'], unknown: ['не проверен', 'idle'],
};
const POOL_EVENT = {
  added: 'добавлен аккаунт', removed: 'убран аккаунт', limited: 'лимит исчерпан', switch: 'ПЕРЕКЛЮЧЕНИЕ',
  pool_enabled: 'пул включён', pool_disabled: 'пул выключен', account_enabled: 'аккаунт включён',
  account_disabled: 'аккаунт выключен', limit_cleared: 'отметка лимита снята',
};

const APPLY_ERRORS = {
  CONFLICT: 'Файлы проекта уже не совпадают с базой рейва (например, применён другой агент). Ничего не записано, обе версии сохранены.',
  NOT_ELIGIBLE: 'У этого агента нет законченного результата, который можно применить.',
  ALREADY_APPLIED: 'Результат этого агента уже применён в проект.',
  APPROVAL_INVALID: 'Разрешение не одобрено, уже использовано или выдано на другое действие. Запросите новое.',
  PRUNED: 'Рабочие копии этого рейва удалены (очистка): применять нечего.',
  TAMPERED: 'Агент изменил .git своей копии: брать результат из неё нельзя.',
};

const splitList = (text) => String(text || '').split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
const clock = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString('ru-RU') : '--:--:--');
const mb = (n) => `${((Number(n) || 0) / 1048576).toFixed(1)} МБ`;
const errCode = (e) => (e && (e.code || (e.detail && e.detail.code))) || '';

function errText(e, table) {
  const code = errCode(e);
  let msg = (table && table[code]) || (e && e.message) || 'Не удалось выполнить операцию';
  const files = e && e.detail && Array.isArray(e.detail.files) ? e.detail.files : [];
  if (files.length) msg += ` Файлы: ${files.join(', ')}.`;
  return msg;
}

async function act(path, body, okText, ctx) {
  try {
    const res = await api.raw(path, { method: 'POST', body: body || {} });
    if (okText) toastOk(okText);
    return res;
  } catch (e) { toastError(e); } finally { ctx.refresh(); }
  return null;
}

/* ---------------------------------------------------------------- глобальный STOP с подтверждённым счётом */

async function stopAll(ctx) {
  try {
    const r = await api.raw('/api/rave/stop-all', { method: 'POST', body: {} });
    if (r && r.ok === false) {
      toast(`STOP не подтверждён: ещё работает ${r.remaining}`, { type: 'err', timeout: 9000,
        hint: (r.remaining_agents || []).join(', ') || 'подробности — в журнале рейва' });
    } else {
      toastOk(r && r.stopped_count ? `STOP: остановлено агентов — ${r.stopped_count}` : 'STOP: активных рейвов не было',
        'Подтверждено: активных агентов не осталось.');
    }
  } catch (e) { toastError(e); } finally { ctx.refresh(); }
}

/* ---------------------------------------------------------------- diff и полный ответ агента */

async function showDiff(rid, name) {
  const modal = openModal({ title: `Diff агента ${name} · ${rid}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  try {
    const d = await api.raw(`/api/rave/${rid}/agents/${encodeURIComponent(name)}/diff`);
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small.dim', d.note ? `${d.stat || '(нет изменений)'} · ${d.note}` : (d.stat || '(нет изменений)')));
    if (d.patch) modal.body.appendChild(codeBlock(d.patch));
  } catch (e) { modal.body.textContent = String(e.message || e); }
}

function showAgent(rave, a) {
  const files = (a.changed_files || []).map((f) => `${f.status} ${f.path}`);
  const tests = a.tests ? `${a.tests.passed ? 'PASS' : 'FAIL'} · ${a.tests.command || ''} · exit ${a.tests.exit_code}${a.tests.timed_out ? ' · таймаут' : ''}` : '';
  openModal({
    title: `Агент ${a.name} · ${rave.id}`, wide: true,
    body: h('div',
      h('div.small.dim', `${a.provider || '—'} / ${a.model || '—'} · вход: ${a.auth || '—'}${a.account ? ` · аккаунт ${a.account_label || a.account}` : ''}`),
      h('div.small.dim', `статус: ${(AGENT_STATUS[a.status] || [a.status])[0]} · шаг ${a.step || 0}/${a.steps_total || '?'} · ветка ${a.branch || '—'} · результат ${a.result_commit || '—'}`),
      h('div.small.dim', `копия: ${a.workspace || '—'}`),
      a.answer ? h('div', h('b', 'Ответ агента'), codeBlock(a.answer)) : null,
      a.error ? h('div', h('b', 'Ошибка / причина'), codeBlock(a.error)) : null,
      h('div', h('b', `Изменённые файлы (${files.length})`), codeBlock(files.join('\n') || '—')),
      a.diff_stat ? codeBlock(a.diff_stat) : null,
      tests ? h('div', h('b', 'Тесты'), h('div.small.dim', tests), codeBlock(String(a.tests.output_tail || '').slice(-3000))) : null,
      a.meta && Object.keys(a.meta).length ? h('div.small.dim', `meta: ${JSON.stringify(a.meta).slice(0, 600)}`) : null),
    footer: h('div'),
  });
}

/* ---------------------------------------------------------------- применение результата: разрешение владельца */

/** Решение владельца в окне: тот же POST /api/approvals/{id}, что и в «Подтверждениях». */
async function decide(approvalId, approve) {
  const row = await api.decideApproval(approvalId, approve, 'ui');
  const got = row && row.status ? String(row.status) : '';
  if (got && got !== (approve ? 'approved' : 'rejected')) {
    toast(`Решение уже принято: ${got}`, { type: 'warn', hint: row.decided_by ? `решил: ${row.decided_by}` : '' });
    return false;
  }
  return true;
}

function askApproval({ title, lead, preview, approveText, onApproved }) {
  const modal = openModal({
    title, wide: true,
    body: h('div', h('div.small.dim', lead), codeBlock(preview)),
    footer: () => [
      h('div.spacer'),
      btn('Позже', () => modal.close()),
      btn('Отклонить', async () => {
        try { if (await decide(modal.approvalId, false)) toast('Отклонено', { type: 'info' }); modal.close(); } catch (e) { toastError(e, 'Не удалось записать решение'); }
      }),
      btn(approveText, async () => {
        try {
          if (!(await decide(modal.approvalId, true))) return;
        } catch (e) { toastError(e, 'Не удалось записать решение'); return; }
        await onApproved(modal.approvalId, modal);
      }, { variant: 'primary' }),
    ],
  });
  return modal;
}

async function applyAgent(rid, name, ctx) {
  let res;
  try {
    res = await api.raw(`/api/rave/${rid}/agents/${encodeURIComponent(name)}/apply`, { method: 'POST', body: {} });
  } catch (e) {
    toast(errText(e, APPLY_ERRORS), { type: 'err', timeout: 9000 });
    ctx.refresh();
    return;
  }
  if (res && res.state === 'APPLIED') {
    toastOk(`Применено в проект: ${(res.files || []).join(', ')}`, 'Файлы записаны в рабочее дерево, без commit и push.');
    ctx.refresh();
    return;
  }
  const modal = askApproval({
    title: `Применить результат агента ${name} в проект?`,
    lead: `Нужно ваше разрешение #${res.approval_id}. Файлы будут записаны в рабочее дерево проекта (без commit и push); `
      + 'если файл в проекте уже изменился с момента базы рейва — применение откажет и ничего не запишет.',
    preview: res.preview || '',
    approveText: 'Одобрить и применить',
    onApproved: async (approvalId, m) => {
      try {
        const done = await api.raw(`/api/rave/${rid}/agents/${encodeURIComponent(name)}/apply`,
          { method: 'POST', body: { approval_id: approvalId } });
        m.close();
        toastOk(`Применено в проект: ${(done.files || []).join(', ')}`, 'Файлы записаны в рабочее дерево, без commit и push.');
      } catch (e) {
        toast(errText(e, APPLY_ERRORS), { type: 'err', timeout: 9000 });
        if (['APPROVAL_INVALID', 'CONFLICT', 'ALREADY_APPLIED', 'NOT_ELIGIBLE', 'PRUNED'].includes(errCode(e))) m.close();
      }
      ctx.refresh();
    },
  });
  modal.approvalId = res.approval_id;
}

/* ---------------------------------------------------------------- агенты и рейв */

function agentStatus(a) {
  const [word, tone] = AGENT_STATUS[a.status] || [a.status, 'idle'];
  return pill(word, { tone, live: LIVE.includes(a.status) });
}

function agentRow(rave, a, ctx) {
  const rid = rave.id;
  const tests = a.tests ? (a.tests.passed ? 'PASS' : 'FAIL') : '—';
  const text = ['failed', 'blocked', 'interrupted', 'stopped'].includes(a.status) && a.error ? a.error : (a.answer || a.step_label || '');
  const live = LIVE.includes(a.status);
  const applied = (rave.applied || {})[a.name];
  const canApply = ['done', 'stopped', 'failed'].includes(a.status) && a.result_commit && (a.changed_files || []).length && !applied && !rave.pruned_at;
  return h('tr',
    h('td', h('b', a.name)),
    h('td.small', `${a.provider || '—'} / ${a.model || '—'}`),
    h('td.small', a.auth || '—'),
    h('td', agentStatus(a), a.pause_reason === 'recovered_after_restart' ? h('span.small.dim', ' после перезапуска') : '',
      applied ? h('div.small.dim', `применён (разрешение #${applied.approval_id})`) : ''),
    h('td.small', `${a.step || 0}/${a.steps_total || '?'}`),
    h('td.small', String((a.changed_files || []).length)),
    h('td.small', tests),
    h('td.small', { style: 'max-width:26rem;white-space:pre-wrap' }, String(text).slice(0, 160) + (String(text).length > 160 ? '…' : '')),
    h('td',
      live ? btn('Пауза', () => act(`/api/rave/${rid}/pause`, { agent: a.name }, `пауза: ${a.name}`, ctx), { size: 'sm' }) : '',
      a.status === 'paused' || a.status === 'blocked' || a.status === 'interrupted'
        ? btn('Продолжить', () => act(`/api/rave/${rid}/resume`, { agent: a.name }, `продолжение: ${a.name}`, ctx), { size: 'sm' }) : '',
      !FINAL.includes(a.status)
        ? btn('STOP', () => act(`/api/rave/${rid}/stop`, { agent: a.name }, `STOP: ${a.name}`, ctx), { size: 'sm', variant: 'danger' }) : '',
      btn('Ответ', () => showAgent(rave, a), { size: 'sm', title: 'Полный ответ, ошибка, файлы и тесты агента' }),
      btn('Diff', () => showDiff(rid, a.name), { size: 'sm' }),
      canApply ? btn('Применить', () => applyAgent(rid, a.name, ctx), { size: 'sm', variant: 'primary',
        title: 'Записать результат в проект — только с вашего разрешения' }) : ''));
}

function raveView(rave, ctx) {
  const rid = rave.id;
  const table = h('div', { style: 'overflow-x:auto' }, h('table.table',
    h('thead', h('tr', ...['Агент', 'Провайдер / модель', 'Вход', 'Статус', 'Шаг', 'Файлы', 'Тесты', 'Ответ / ошибка', ''].map((t) => h('th', t)))),
    h('tbody', ...(rave.agents || []).map((a) => agentRow(rave, a, ctx)))));
  const conflicts = (rave.conflicts || []).filter((c) => c.file);
  return panel(`Рейв ${rid} · ${rave.status}`, h('div',
    h('div.small.dim', `prompt: ${rave.prompt}`),
    h('div.small.dim', `base ${String(rave.base_commit || '').slice(0, 10)} · ${rave.scratch ? 'scratch-проект' : rave.repo}${rave.pruned_at ? ' · рабочие копии удалены' : ''}`),
    h('div', { style: 'margin:.5rem 0' },
      btn('Пауза всех', () => act(`/api/rave/${rid}/pause`, {}, 'пауза рейва', ctx), { size: 'sm' }),
      btn('Продолжить всех', () => act(`/api/rave/${rid}/resume`, {}, 'продолжение рейва', ctx), { size: 'sm' }),
      btn('STOP рейва', () => act(`/api/rave/${rid}/stop`, {}, 'STOP рейва', ctx), { size: 'sm', variant: 'danger' })),
    table,
    conflicts.length ? h('div', h('b', `Конфликты (${conflicts.length}) — все версии сохранены:`),
      ...conflicts.map((c) => h('div.small', `${c.file}: ${(c.agents || []).join(' × ')} · ${c.auto_mergeable ? 'сливается автоматически' : 'нужно решение'} · ${c.artifacts}`)))
      : h('div.small.dim', 'Конфликтов нет'),
    h('div.small.dim', 'Результат агента попадает в проект только кнопкой «Применить» и только с разрешением владельца (то же, что `bossman rave apply`).')));
}

/* ---------------------------------------------------------------- журнал событий */

function eventDetail(ev) {
  const extra = Object.entries(ev).filter(([k, v]) => !['seq', 'ts', 'kind', 'agent'].includes(k) && v !== null && v !== '' && !(Array.isArray(v) && !v.length));
  if (ev.kind === 'step_started') return `шаг ${ev.step}/${ev.total} · ${ev.label}`;
  if (ev.kind === 'pool_switch') return `${ev.account} → ${ev.to} (${ev.reason || 'лимит'})`;
  if (ev.kind === 'pool_account') return `${ev.account} «${ev.label || ''}»`;
  if (['agent_failed', 'agent_blocked', 'agent_stopped', 'agent_interrupted'].includes(ev.kind)) return String(ev.error || '');
  return extra.map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ');
}

async function loadEvents(rid) {
  const entry = state.events[rid] || { cursor: 0, items: [] };
  try {
    const page = await api.raw(`/api/rave/${rid}/events?after=${entry.cursor}`);
    entry.items.push(...(page.events || []));
    if (entry.items.length > EVENTS_KEEP) entry.items.splice(0, entry.items.length - EVENTS_KEEP);
    entry.cursor = page.cursor || entry.cursor;
  } catch (e) { /* журнал — справка: его отсутствие не ломает страницу */ }
  state.events[rid] = entry;
  return entry;
}

function eventsPanel(rid, entry) {
  const shown = entry.items.filter((e) => e.kind !== 'step_done').slice(-EVENTS_SHOWN).reverse();
  return panel(`Журнал событий · ${rid}`, shown.length
    ? h('div', { style: 'max-height:16rem;overflow:auto' }, ...shown.map((ev) => h('div.small',
      h('code', clock(ev.ts)), ` ${ev.agent || 'рейв'} · `, h('b', EVENT_LABEL[ev.kind] || ev.kind),
      eventDetail(ev) ? ` · ${String(eventDetail(ev)).slice(0, 240)}` : '')))
    : h('div.small.dim', 'Событий пока нет'));
}

/* ---------------------------------------------------------------- подключения агентов (CLI) */

async function loadConnectors(force) {
  if (!force && state.connectors && Date.now() - state.connectorsAt < CONNECTORS_TTL_MS) return;
  try {
    state.connectors = await api.raw(`/api/rave/connectors${force ? '?refresh=1' : ''}`);
    state.connectorsErr = null;
    state.connectorsAt = Date.now();
  } catch (e) { state.connectorsErr = e; }
}

function connectorLine(key, c) {
  if (key === 'claude' || key === 'codex') {
    const ready = c.installed !== false && c.logged_in && c.subscription && (key !== 'claude' || c.version_ok !== false);
    const parts = [
      c.installed === false ? 'CLI не найден' : (c.logged_in ? `вход: ${c.auth}` : 'вход не выполнен'),
      c.plan ? `план ${c.plan}` : '',
      c.version ? `CLI ${c.version}` : '',
      c.optin ? 'разрешение владельца есть' : 'разрешение владельца: нет (агент его попросит при запуске)',
    ].filter(Boolean);
    return h('div',
      h('div', pill(ready ? 'готов' : 'не готов', { tone: ready ? 'ok' : 'warn' }), ' ', h('b', key), h('span.small.dim', ` · ${parts.join(' · ')}`)),
      c.version_problem ? h('div.small', { style: 'color:var(--bx-rose)' }, c.version_problem) : null,
      !c.logged_in && c.login_step ? h('div.small.dim', `как войти: ${c.login_step}`) : null,
      !c.logged_in && c.reason ? h('div.small.dim', c.reason) : null);
  }
  if (key === 'local') return h('div', pill('готов', { tone: 'ok' }), ' ', h('b', 'local'), h('span.small.dim', ` · ${c.endpoint} · модель по умолчанию ${c.default_model}`));
  return h('div', pill('готов', { tone: 'ok' }), ' ', h('b', 'mock'), h('span.small.dim', ' · скриптовый агент для проверок, не модель'));
}

function connectorsPanel(ctx) {
  const c = state.connectors;
  const body = state.connectorsErr && !c
    ? h('div.small.dim', `Состояние подключений недоступно: ${state.connectorsErr.message || state.connectorsErr}`)
    : h('div', { style: 'display:grid;gap:.5rem' },
      ...['mock', 'local', 'claude', 'codex'].map((k) => connectorLine(k, (c || {})[k] || {})),
      c && c.api_key_path ? h('div.small.dim', `путь по API-ключу: ${c.api_key_path.enabled ? 'ВКЛЮЧЁН' : 'выключен'} (${c.api_key_path.how})`) : null);
  return panel('Подключения агентов', body, {
    aside: btn('Проверить вход', async () => { await loadConnectors(true); ctx.refresh(); }, { size: 'sm', title: 'Спросить сами CLI (claude auth status, codex login status, --version)' }),
  });
}

/* ---------------------------------------------------------------- пул аккаунтов */

async function loadPool() {
  try { state.pool = await api.raw('/api/rave/pool'); state.poolErr = null; } catch (e) { state.poolErr = e; }
}

async function poolCall(path, method, body, okText, ctx) {
  try {
    const res = await api.raw(path, { method, body: method === 'DELETE' ? undefined : (body || {}) });
    if (okText) toastOk(okText);
    return res;
  } catch (e) { toast(errText(e), { type: 'err', timeout: 9000 }); } finally { ctx.refresh(); }
  return null;
}

function poolAccountRow(a, ctx) {
  const [word, tone] = POOL_STATE[a.state] || [a.state, 'idle'];
  const until = a.state === 'limited' && a.limited_until ? ` до ${new Date(a.limited_until * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : '';
  const p = `/api/rave/pool/accounts/${encodeURIComponent(a.id)}`;
  return h('tr',
    h('td', h('b', a.label), h('div.small.dim', `${a.id} · ${a.tool}`)),
    h('td', pill(word + until, { tone }), a.login_detail ? h('div.small.dim', a.login_detail) : '',
      a.state === 'needs_login' ? h('div.small.dim', { style: 'max-width:24rem;white-space:pre-wrap' }, a.login_step) : ''),
    h('td.small', a.approved ? 'разрешён' : 'ждёт разрешения'),
    h('td.small', { style: 'max-width:16rem;overflow-wrap:anywhere' }, a.profile_dir || 'обычный вход CLI'),
    h('td.small', a.last_used ? `${clock(a.last_used)} · запусков ${a.runs || 0}` : 'не использовался'),
    h('td',
      btn('Проверить', () => poolCall(`${p}/check`, 'POST', {}, `${a.label}: состояние обновлено`, ctx), { size: 'sm' }),
      btn(a.enabled === false ? 'Включить' : 'Отключить', () => poolCall(`${p}/enabled`, 'POST', { enabled: a.enabled === false }, null, ctx), { size: 'sm' }),
      a.state === 'limited' ? btn('Снять лимит', () => poolCall(`${p}/clear-limit`, 'POST', {}, 'отметка лимита снята', ctx), { size: 'sm' }) : '',
      btn('Убрать', async () => {
        if (await confirmDialog({ title: `Убрать аккаунт ${a.label}?`, text: 'Каталог профиля CLI и вход в нём Bossman не трогает — убирается только запись в пуле.', okText: 'Убрать', danger: true })) {
          await poolCall(p, 'DELETE', undefined, 'аккаунт убран из пула', ctx);
        }
      }, { size: 'sm', variant: 'danger' })));
}

function addAccountModal(ctx) {
  const tool = select([{ value: 'claude', label: 'Claude Code (подписка Claude)' }, { value: 'codex', label: 'Codex (подписка ChatGPT)' }]);
  const label = input({ placeholder: 'Имя для себя, например «Claude запасной»' });
  const dir = input({ placeholder: 'пусто — Bossman создаст каталог профиля сам' });
  const useDefault = checkbox('Это мой обычный вход CLI (без отдельного каталога профиля)', false);
  const modal = openModal({
    title: 'Добавить аккаунт в пул',
    body: h('div', { style: 'display:grid;gap:.6rem' },
      field('Инструмент', tool), field('Имя аккаунта', label),
      field('Каталог профиля CLI', dir, 'Свой каталог на каждый аккаунт (CLAUDE_CONFIG_DIR / CODEX_HOME). Вход в него делаете вы сами.'),
      useDefault),
    footer: () => [h('div.spacer'), btn('Отмена', () => modal.close()), btn('Добавить', async () => {
      const name = label.value.trim();
      if (!name) { toast('Введите имя аккаунта', { type: 'info' }); label.focus(); return; }
      const asDefault = useDefault.querySelector('input').checked;
      const res = await poolCall('/api/rave/pool/accounts', 'POST', { tool: tool.value, label: name,
        profile_dir: asDefault ? null : (dir.value.trim() || null), use_default: asDefault }, null, ctx);
      if (!res) return;
      modal.close();
      const acc = res.account;
      const step = ((res.pool.accounts || []).find((x) => x.id === acc.id) || {}).login_step || '';
      openModal({ title: `Аккаунт ${acc.label} добавлен`, wide: true,
        body: h('div', h('div.small.dim', 'Bossman вход не выполняет и токены не читает. Войдите в этот аккаунт сами:'), codeBlock(step),
          h('div.small.dim', 'Затем нажмите «Проверить». Пока пул не включён (и новый аккаунт не разрешён), он не используется.')),
        footer: h('div') });
    }, { variant: 'primary' })],
  });
}

function enablePoolFlow(ctx) {
  return (async () => {
    let res;
    try { res = await api.raw('/api/rave/pool/enable', { method: 'POST', body: {} }); } catch (e) { toast(errText(e), { type: 'err', timeout: 9000 }); ctx.refresh(); return; }
    if (res.state === 'ENABLED') { toastOk('Пул включён'); ctx.refresh(); return; }
    const modal = askApproval({
      title: 'Включить пул аккаунтов?',
      lead: `Нужно ваше разрешение #${res.approval_id}: при исчерпании лимита следующий запуск агента пойдёт на следующий аккаунт (только между запусками; каждое переключение видно здесь).`,
      preview: res.preview || '', approveText: 'Одобрить и включить',
      onApproved: async (approvalId, m) => {
        try {
          await api.raw('/api/rave/pool/enable', { method: 'POST', body: { approval_id: approvalId } });
          m.close();
          toastOk('Пул включён');
        } catch (e) {
          toast(errText(e, APPLY_ERRORS), { type: 'err', timeout: 9000 });
          if (errCode(e) === 'APPROVAL_INVALID') m.close();
        }
        ctx.refresh();
      },
    });
    modal.approvalId = res.approval_id;
  })();
}

function poolJournalLine(j) {
  const who = j.to ? `${j.account} → ${j.to}` : (j.account || '');
  const why = j.reason ? ` · ${String(j.reason).slice(0, 160)}` : '';
  return h('div.small', h('code', clock(j.at)), ` ${j.tool || ''} · `, h('b', POOL_EVENT[j.event] || j.event), who ? ` · ${who}` : '', why);
}

function poolPanel(ctx) {
  const pool = state.pool;
  if (!pool) {
    return panel('Пул аккаунтов', h('div.small.dim', state.poolErr ? `Недоступно: ${state.poolErr.message || state.poolErr}` : 'Загрузка…'));
  }
  const rows = pool.accounts || [];
  return panel('Пул аккаунтов', h('div', { style: 'display:grid;gap:.6rem' },
    h('div', pill(pool.active ? 'включён' : 'выключен', { tone: pool.active ? 'ok' : 'idle' }), ' ',
      h('span.small.dim', pool.active
        ? 'Агент claude/codex идёт на первом готовом аккаунте; при лимите следующий запуск агента — на следующем.'
        : 'По умолчанию выключен: агенты используют обычный вход CLI. Включение — только с вашего разрешения.')),
    h('div.small', { style: 'border-left:3px solid var(--bx-amber, #d9a400);padding-left:.6rem' }, pool.terms_note),
    rows.length ? h('div', { style: 'overflow-x:auto' }, h('table.table',
      h('thead', h('tr', ...['Аккаунт', 'Состояние', 'Разрешение', 'Профиль CLI', 'Использование', ''].map((t) => h('th', t)))),
      h('tbody', ...rows.map((a) => poolAccountRow(a, ctx)))))
      : h('div.small.dim', 'Аккаунтов пока нет. Добавьте свой аккаунт, войдите в него сами командой из подсказки и нажмите «Проверить».'),
    (pool.waiting_approval || []).length && pool.enabled
      ? h('div.small', `Ждут разрешения (не используются, пока пул не будет включён заново): ${pool.waiting_approval.join(', ')}`) : null,
    h('div',
      btn('Добавить аккаунт', () => addAccountModal(ctx), { size: 'sm' }),
      pool.enabled
        ? btn('Выключить пул', () => poolCall('/api/rave/pool/disable', 'POST', {}, 'Пул выключен', ctx), { size: 'sm' })
        : btn('Включить пул', () => enablePoolFlow(ctx), { size: 'sm', variant: 'primary', disabled: !rows.length,
          title: rows.length ? 'Нужно разрешение владельца' : 'Сначала добавьте аккаунт' })),
    (pool.journal || []).length
      ? h('div', h('b', 'Журнал переключений'), h('div', { style: 'max-height:12rem;overflow:auto' }, ...pool.journal.slice().reverse().slice(0, 25).map(poolJournalLine)))
      : h('div.small.dim', 'Переключений не было.')));
}

/* ---------------------------------------------------------------- очистка */

function prunePanel(ctx) {
  const days = input({ value: state.prune.days, type: 'number', min: '0', style: 'width:6rem' });
  days.addEventListener('input', () => { state.prune.days = days.value; });
  const report = state.prune.report;
  const run = async (dry) => {
    const n = Number(days.value);
    if (!Number.isFinite(n) || n < 0) { toast('Введите число дней ≥ 0', { type: 'info' }); days.focus(); return; }
    try {
      state.prune.report = await api.raw('/api/rave/prune', { method: 'POST', body: { older_than_days: n, dry_run: dry, include_blocked: false } });
      if (!dry) toastOk(`Удалено рабочих копий: ${state.prune.report.items.length} (${mb(state.prune.report.freed_bytes)})`);
    } catch (e) { toastError(e); }
    ctx.refresh();
  };
  return panel('Очистка старых рейвов', h('div', { style: 'display:grid;gap:.5rem' },
    h('div.small.dim', 'Удаляет рабочие копии завершённых рейвов без активности дольше N дней (записи и журнал остаются). Рейвы, где что-то ещё работает, не трогаются. Сначала — пробный прогон.'),
    h('div', field('Дней без активности', days),
      btn('Показать, что будет удалено', () => run(true), { size: 'sm' }),
      btn('Удалить', async () => {
        if (!report || !report.dry_run || !report.items.length) { toast('Сначала сделайте пробный прогон: там должны быть рейвы к удалению', { type: 'info' }); return; }
        const lost = report.items.filter((i) => (i.unapplied || []).length).map((i) => `${i.id}: ${i.unapplied.join(', ')}`);
        if (await confirmDialog({ title: `Удалить рабочие копии ${report.items.length} рейвов?`, danger: true, okText: 'Удалить',
          text: `Освободится ${mb(report.freed_bytes)}. Применить результаты из удалённых копий будет нельзя.${lost.length ? ` НЕ применены в проект: ${lost.join('; ')}.` : ''}` })) await run(false);
      }, { size: 'sm', variant: 'danger' })),
    report ? h('div.small', `${report.dry_run ? 'Будет удалено' : 'Удалено'}: ${report.items.length} рейвов, ${mb(report.freed_bytes)}`,
      ...report.items.map((i) => h('div', `${i.id} · ${i.status} · ${mb(i.bytes)}${(i.unapplied || []).length ? ` · не применено: ${i.unapplied.join(', ')}` : ''}`)),
      ...(report.skipped || []).map((s) => h('div.dim', `пропущен ${s.id}: ${s.reason}`))) : null));
}

/* ---------------------------------------------------------------- страница */

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
      actions: [btn('STOP всех рейвов', () => stopAll(ctx), { variant: 'danger' })],
    });
    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));
    const rid = state.selected && items.some((i) => i.id === state.selected) ? state.selected : (items[0] || {}).id;
    if (rid) { try { rave = await api.raw(`/api/rave/${rid}`); } catch (e) { rave = null; } }
    const [events] = await Promise.all([rid && rave ? loadEvents(rid) : null, loadConnectors(false), loadPool()]);

    const promptEl = textarea({ rows: 3, placeholder: 'Задача для всех агентов', value: state.form.prompt });
    const agentsEl = input({ value: state.form.agents, placeholder: 'mock:a,local:qwen,claude:c,codex:x' });
    const repoEl = input({ value: state.form.repo, placeholder: 'путь к git-проекту в разрешённых корнях (пусто — чистый scratch-проект)' });
    const allowEl = input({ value: state.form.allow, placeholder: 'пути, где local-агенту можно писать (через запятую; пусто — весь проект)' });
    const testEl = input({ value: state.form.test, placeholder: 'команда проверки в каждой готовой копии, например: python -m pytest -q' });
    for (const [key, el] of [['prompt', promptEl], ['agents', agentsEl], ['repo', repoEl], ['allow', allowEl], ['test', testEl]]) {
      el.addEventListener('input', () => { state.form[key] = el.value; });
    }
    const startBody = (prompt) => ({
      prompt, agents: [agentsEl.value], repo: repoEl.value.trim() || undefined,
      allow: splitList(allowEl.value), test: testEl.value.trim() || undefined,
    });
    const startForm = panel('Новый рейв', h('div', { style: 'display:grid;gap:.5rem' },
      promptEl, agentsEl,
      h('div.small.dim', 'Агент: <коннектор>:<имя>[@модель][?ключ=значение]; коннекторы mock, local, claude, codex (claude и codex — только под вашим входом и с вашего разрешения).'),
      repoEl, allowEl, testEl,
      btn('Запустить', async () => {
        const prompt = promptEl.value.trim();
        if (!prompt) {          // no request and no console error for an empty task: say what is missing
          toast('Сначала введите задачу для агентов', { type: 'info' });
          promptEl.focus();
          return;
        }
        try {
          const r = await api.raw('/api/rave', { method: 'POST', body: startBody(prompt) });
          state.selected = r.id; state.form.prompt = ''; toastOk(`Рейв ${r.id} запущен`);
        } catch (e) { toastError(e); }
        ctx.refresh();
      }, { variant: 'primary' })));

    const list = panel('Рейвы', items.length ? h('div', ...items.map((i) => h('div.small',
      h('a', { href: '#', onClick: (ev) => { ev.preventDefault(); state.selected = i.id; ctx.refresh(); } }, i.id),
      ` · ${i.status} · ${i.prompt} · ${Object.entries(i.agents || {}).map(([k, v]) => `${k}:${v}`).join(', ')}`))) : h('div.small.dim', 'Рейвов пока нет'));
    return h('div.bx-page', head, connectorsPanel(ctx), startForm, rave ? raveView(rave, ctx) : '',
      rave && events ? eventsPanel(rid, events) : '', list, poolPanel(ctx), prunePanel(ctx));
  },

  onEvent(ev) { return typeof ev.kind === 'string' && ev.kind.startsWith('rave.'); },
};

export default RavePage;
