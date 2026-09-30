/* ============================================================
   chat/panel.js — правая панель «Thinking & Actions».

   Показывает ТОЛЬКО краткие объяснения, план, действия, проверку и
   источники одного хода. Скрытые рассуждения модели (run.reasoning_delta,
   <think>) здесь не показываются никогда: state.js их отбрасывает, и этот
   модуль их не получает.

   Кратко    — почему этот агент и модель: причина допуска из ответа
               отправки и /api/router/explain (если у задачи есть прогон);
   План      — только настоящий план (миссия); иначе честно «не строится»
               и фактическая лента шагов из task.progress;
   Действия  — вызовы инструментов (run.tool_use / run.tool_result);
   Проверка  — evaluation.completed и решения по подтверждениям;
   Источники — память (memory.recalled), адреса из результатов
               инструментов, вложения хода.
   ============================================================ */

import { h, icon, iconButton, clear } from './dom.js';
import { fmtDuration, localityBadge, billingBadge } from './format.js';
import { toolCounts } from './state.js';

function section(id, title, iconName, counter, collapsed, onToggle, body) {
  const open = !collapsed.has(id);
  return h('section.pnl-sec', { dataset: { sec: id } },
    h('button.pnl-sec-head', { type: 'button', 'aria-expanded': open ? 'true' : 'false',
      'aria-label': `${title}: ${open ? 'свернуть' : 'развернуть'}`, onClick: () => onToggle(id) },
    icon(iconName, 17), h('span.pnl-sec-title', title), counter ? h('span.pnl-count', counter) : null,
    icon(open ? 'chevronDown' : 'chevronRight', 16, 'pnl-chev')),
    open ? h('div.pnl-sec-body', body) : null);
}

function dim(text) { return h('p.pnl-dim', text); }

function briefBody(data) {
  const { turn, brief } = data;
  if (!turn) return dim('Отправьте сообщение — здесь появится, кто его выполняет и почему.');
  const rows = [];
  const agent = brief && brief.agent;
  const model = (brief && brief.model) || null;
  if (agent) rows.push(h('div.pnl-kv', h('span', 'Агент'), h('b', agent.name || `#${agent.id}`)));
  const alias = (model && model.alias) || turn.model || (turn.run && turn.run.model_alias) || '';
  if (alias) {
    const lb = localityBadge((model && model.locality) || turn.locality);
    const bb = billingBadge((model && model.billing) || turn.billing);
    rows.push(h('div.pnl-kv', h('span', 'Модель'), h('b', alias),
      lb ? h('span.badge', { dataset: { tone: lb.tone }, title: lb.title }, lb.text) : null,
      bb ? h('span.badge', { dataset: { tone: bb.tone } }, bb.text) : null));
  }
  const adm = brief && brief.admission;
  if (adm && (adm.reason || adm.code)) {
    rows.push(h('div.pnl-why', h('span.pnl-why-label', adm.ok === false ? 'Почему не запущено' : 'Почему этот исполнитель'),
      h('span', [adm.reason, adm.code ? `(${adm.code})` : ''].filter(Boolean).join(' '))));
  }
  const reasons = brief && brief.route && Array.isArray(brief.route.reasons) ? brief.route.reasons : [];
  if (reasons.length) {
    rows.push(h('div.pnl-why', h('span.pnl-why-label', 'Маршрутизатор'),
      h('ul.pnl-list', reasons.slice(0, 8).map((r) => h('li', typeof r === 'string' ? r : JSON.stringify(r))))));
  }
  const fallbacks = [...turn.notes.values()].filter((n) => n.title === 'Запасная модель');
  for (const f of fallbacks) rows.push(h('div.pnl-why', h('span.pnl-why-label', 'Запасная модель'), h('span', f.detail || '')));
  if (!rows.length) rows.push(dim('Сервер пока не сообщил, кто выполняет ход.'));
  return rows;
}

function planBody(data) {
  const { turn, plan } = data;
  const out = [];
  if (plan && plan.items && plan.items.length) {
    out.push(h('ol.pnl-plan', plan.items.map((p) => h('li', { dataset: { state: p.state || '' } },
      h('span.plan-dot', { 'aria-hidden': 'true' }), h('span', p.title), p.status ? h('span.pnl-dim-inline', p.status) : null))));
    return out;
  }
  out.push(dim('Для простого запроса план не строится. Ниже — шаги, которые Bossman действительно выполнил.'));
  if (!turn || !turn.progress.length) {
    out.push(dim('Шагов пока нет.'));
    return out;
  }
  out.push(h('ol.pnl-steps', turn.progress.map((p) => h('li',
    h('span.pnl-step-n', `Шаг ${p.step}${p.maxSteps ? ` из ${p.maxSteps}` : ''}`),
    p.model ? h('span.pnl-dim-inline', p.model) : null,
    p.tools && p.tools.length ? h('span.pnl-tools', p.tools.slice(0, 4).map((t) => h('code', t))) : null,
    p.waiting ? h('span.badge', { dataset: { tone: 'lan' } }, 'ждёт решения') : null))));
  return out;
}

function actionsBody(data) {
  const { turn } = data;
  if (!turn || !turn.tools.size) return dim('Инструменты в этом ходе не вызывались.');
  return h('ul.pnl-actions', [...turn.tools.values()].map((c) => h('li', { dataset: { state: c.ok === true ? 'ok' : c.ok === false ? 'fail' : 'run' } },
    h('span.pnl-act-status', icon(c.ok === true ? 'checkCircle' : c.ok === false ? 'failCircle' : 'clock', 16)),
    h('div.pnl-act-main',
      h('div.pnl-act-title', h('span', c.tool), c.durationMs !== null && c.durationMs !== undefined ? h('span.pnl-dim-inline', fmtDuration(c.durationMs)) : null),
      h('div.pnl-act-meta', h('span.tool-chip', c.chip), c.argsSummary ? h('code.pnl-args', c.argsSummary) : null),
      c.ok === false && c.summary ? h('div.pnl-act-err', c.summary) : null))));
}

function checkBody(data) {
  const { turn } = data;
  if (!turn) return dim('Проверок пока нет.');
  const out = [];
  for (const e of turn.evaluations) {
    out.push(h('div.pnl-check', h('span.badge', { dataset: { tone: e.verdict === 'PASS' ? 'local' : e.verdict === 'FAIL' ? 'danger' : 'lan' } }, e.verdict || '—'),
      h('span', e.reasons || 'без пояснения')));
  }
  for (const a of turn.approvals.values()) {
    out.push(h('div.pnl-check', h('span.badge', { dataset: { tone: a.status === 'approved' ? 'local' : a.status === 'pending' ? 'lan' : 'danger' } },
      a.status === 'pending' ? 'ждёт' : a.status), h('span', `Подтверждение ${a.kind || ''}`.trim())));
  }
  if (!out.length) out.push(dim('Сервер не прислал проверок для этого хода.'));
  return out;
}

function sourcesBody(data) {
  const { turn, memorySources } = data;
  if (!turn) return dim('Источников пока нет.');
  const items = [...(memorySources || []), ...turn.sources, ...(turn.attachments || []).map((a) => ({ kind: 'file', label: a.name }))];
  const out = [];
  if (turn.memory && turn.memory.state === 'skipped') out.push(dim(`Память не подключена к ходу: ${turn.memory.message || ''}`));
  if (!items.length) {
    out.push(dim('Ход не опирался на память, файлы или адреса из интернета.'));
    return out;
  }
  out.push(h('ul.pnl-sources', items.map((s) => h('li', { dataset: { kind: s.kind } },
    icon(s.kind === 'memory' ? 'memory' : s.kind === 'file' ? 'file' : 'link', 15),
    s.href ? h('a', { href: s.href, target: '_blank', rel: 'noopener noreferrer' }, s.label) : h('span', s.label),
    s.detail ? h('span.pnl-dim-inline', s.detail) : null))));
  return out;
}

export function sourcesCount(data) {
  const t = data.turn;
  if (!t) return 0;
  return (data.memorySources || []).length + t.sources.length + (t.attachments || []).length;
}

/**
 * data: {turn, brief:{agent, model, admission, route}, plan:{items}|null,
 * memorySources, collapsed:Set, onToggle(id), onClose()}.
 */
export function renderPanel(root, data) {
  clear(root);
  const { turn, collapsed, onToggle } = data;
  const tc = turn ? toolCounts(turn) : { done: 0, total: 0 };
  const steps = turn ? turn.progress.length : 0;
  const maxSteps = turn && turn.progress.length ? turn.progress[turn.progress.length - 1].maxSteps : null;
  const checks = turn ? turn.evaluations.length + turn.approvals.size : 0;
  const src = sourcesCount(data);
  root.appendChild(h('header.pnl-head',
    h('div',
      h('h2.pnl-title', 'Thinking & Actions'),
      h('p.pnl-sub', 'Планирование, действия и источники')),
    iconButton('close', 'Закрыть панель Thinking & Actions (Ctrl+.)', data.onClose, { id: 'chat-panel-close' })));
  root.appendChild(h('p.pnl-note', icon('lock', 13), 'Только краткие объяснения и факты. Скрытые рассуждения модели не показываются.'));
  root.appendChild(section('brief', 'Кратко', 'spark', '', collapsed, onToggle, briefBody(data)));
  root.appendChild(section('plan', 'План', 'list', data.plan && data.plan.items && data.plan.items.length
    ? `${data.plan.items.filter((p) => p.state === 'done').length}/${data.plan.items.length}`
    : (steps ? `${steps}${maxSteps ? `/${maxSteps}` : ''}` : ''), collapsed, onToggle, planBody(data)));
  root.appendChild(section('actions', 'Действия', 'play', tc.total ? `${tc.done}/${tc.total}` : '', collapsed, onToggle, actionsBody(data)));
  root.appendChild(section('check', 'Проверка', 'shield', checks ? String(checks) : '', collapsed, onToggle, checkBody(data)));
  root.appendChild(section('sources', 'Источники', 'link', src ? String(src) : '', collapsed, onToggle, sourcesBody(data)));
}
