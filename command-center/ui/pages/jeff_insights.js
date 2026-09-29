/* ============================================================
   jeff_insights.js — «Jeff · обзор»: панель владельца (Jeff 2.0, модуль insights).
   Endpoints (bcc/features/jeff_insights.py, под /api с обычной auth владельца):
     GET /api/jeff-insights/overview            — участники, пути нарративов, здоровье, тренды
     GET /api/jeff-insights/narratives/{key}    — текст одного нарратива (только владельцу)
     GET /api/jeff-insights/digest              — недельный дайджест (только числа)
     POST /api/jeff-insights/digest/send        — положить дайджест в исходящие Пульта (раз в неделю)
   Участники показаны меткой владельца, Telegram ID и текстов сообщений здесь нет.
   Пустое состояние (нет участников, нет логов) — обычная страница, а не ошибка.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, openModal, sparkline, fmtNum } from '../components.js';
import { panel, pageHead, pill, errorNote, codeBlock } from './_ui.js';

const AVAILABILITY = {
  up: ['Jeff работает', 'ok'], stale: ['Нет свежего heartbeat', 'warn'],
  stopped: ['Jeff остановлен', 'warn'], absent: ['Heartbeat не найден', 'idle'],
};

const SERIES = [
  ['replies', 'Ответов в день', (v) => fmtNum(v)],
  ['ok_rate', 'Доля успешных', (v) => `${Math.round(v * 100)}%`],
  ['avg_latency_ms', 'Средняя задержка', (v) => `${(v / 1000).toFixed(1)} с`],
  ['quality', 'Качество (проверки)', (v) => v.toFixed(2)],
  ['tasks_done', 'Задач выполнено', (v) => fmtNum(v)],
  ['tasks_failed', 'Задач не удалось', (v) => fmtNum(v)],
];

function lastValue(values) {
  for (let i = values.length - 1; i >= 0; i -= 1) if (values[i] !== null && values[i] !== undefined) return values[i];
  return null;
}

function healthPanel(health) {
  const tg = (health.heartbeat || {}).telegram || { availability: 'absent' };
  const win = (health.heartbeat || {}).window || { availability: 'absent' };
  const [tgText, tgTone] = AVAILABILITY[tg.availability] || AVAILABILITY.absent;
  const guard = health.model_guard || { present: false };
  const queue = health.queue;
  const errors = health.recent_errors || { count: 0, kinds: {} };
  const kinds = Object.entries(errors.kinds || {}).map(([k, v]) => `${k}: ${v}`).join(', ');
  const modules = (health.modules || []).map((m) => h(m.breaker_open ? 'span.badge.badge-warn' : 'span.badge', {
    title: m.breaker_open ? 'Модуль временно отключён после сбоев' : 'Модуль работает',
  }, m.name));
  return panel('Здоровье', h('div.stack.sm',
    h('div.row.tight', { style: { flexWrap: 'wrap', gap: '8px' } },
      pill(`Telegram: ${tgText}`, { tone: tgTone }),
      pill(`Окно Jeff: ${(AVAILABILITY[win.availability] || AVAILABILITY.absent)[0]}`,
        { tone: (AVAILABILITY[win.availability] || AVAILABILITY.absent)[1] }),
      pill(queue < 0 ? 'Очередь не читается' : `Очередь: ${queue}`, { tone: queue < 0 || queue > 10 ? 'warn' : 'ok' }),
      guard.present
        ? pill(guard.healthy ? 'model_guard: в порядке' : 'model_guard: проблема', { tone: guard.healthy ? 'ok' : 'err' })
        : pill('model_guard: нет данных', { tone: 'idle', title: 'Модуль ещё не записал состояние' }),
      pill(health.ok ? 'Всё в порядке' : 'Есть на что посмотреть', { tone: health.ok ? 'ok' : 'warn' })),
    h('div.small.dim',
      tg.replies_ok === undefined ? 'Ответы Telegram-Jeff: пока нет данных.'
        : `Ответов: ${tg.replies_ok} успешно, ${tg.replies_failed || 0} с ошибкой; средняя задержка `
          + `${tg.avg_latency_ms ? (tg.avg_latency_ms / 1000).toFixed(1) + ' с' : 'нет данных'}.`),
    errors.count ? h('div.small', `Ошибок в журнале: ${errors.count}${kinds ? ` (${kinds})` : ''}.`) : null,
    modules.length ? h('div.row.tight', { style: { flexWrap: 'wrap', gap: '6px' } }, ...modules)
      : h('div.small.dim', 'Модули Jeff 2.0 ещё не записали состояние.')));
}

function trendsPanel(trends) {
  const days = trends.days || [];
  const tiles = SERIES.map(([name, label, fmt]) => {
    const values = (trends.series || {})[name] || [];
    const last = lastValue(values);
    return h('div.stack.sm', { style: { minWidth: '150px', flex: '1 1 150px' } },
      h('div.small.dim', label),
      h('div.mono', last === null ? 'нет данных' : fmt(last)),
      sparkline(values.map((v) => (v === null ? 0 : v)), { height: 36 }));
  });
  return panel('Тренды за ' + days.length + ' дн.', h('div.row', { style: { flexWrap: 'wrap', gap: '16px' } }, ...tiles));
}

async function showNarrative(person) {
  try {
    const data = await api.raw(`/api/jeff-insights/narratives/${encodeURIComponent(person.key)}`);
    openModal({
      title: `Нарратив: ${person.label}`, wide: true,
      body: h('div.stack.sm',
        h('div.small.dim', `Модель: ${data.model || '—'} · ${data.created_at || 'дата неизвестна'}`),
        h('h3', 'Контекст общения'), h('p', data.context || '—'),
        h('h3', 'Личность и манера'), h('p', data.personality || '—'),
        h('div.small.dim', 'Текст виден только владельцу и не попадает в отчёты и дайджест.')),
    });
  } catch (e) { toastError(e, 'Нарратив не открылся'); }
}

function participantRow(person) {
  const open = actionButton('Нарратив', () => showNarrative(person), {
    cls: 'btn btn-sm', iconName: 'search', disabled: !person.narrative,
    title: person.narrative ? 'Показать нарратив Master Parser (только владельцу)'
      : 'Нарратива ещё нет: запустите Master Parser на странице «Jeff · паспорта»',
  });
  const tasks = Object.entries(person.tasks || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || '—';
  const quality = person.quality && person.quality.n ? `${person.quality.overall.toFixed(2)} (${person.quality.n})` : '—';
  return h('tr',
    h('td', person.label, person.access === 'revoked' ? h('span.badge.badge-warn', { title: 'Доступ закрыт' }, ' закрыт') : null),
    h('td.small', person.consent.memory ? 'память вкл.' : 'память выкл.'),
    h('td.mono.small', String(person.messages)),
    h('td.mono.small', String(person.facts)),
    h('td.small', person.last_activity ? person.last_activity.replace('T', ' ').replace('Z', '') : '—'),
    h('td.small', tasks),
    h('td.mono.small', String(person.reminders_pending)),
    h('td.mono.small', quality),
    h('td', open));
}

function participantsPanel(people) {
  if (!people.length) {
    return panel('Участники', h('div.small.dim',
      'Участников пока нет. Как только кто-то напишет Jeff, он появится здесь.'));
  }
  return panel(`Участники (${people.length})`, h('div', { style: { overflowX: 'auto' } },
    h('table.table',
      h('thead', h('tr', ['УЧАСТНИК', 'ПАМЯТЬ', 'СООБЩЕНИЙ', 'ФАКТОВ', 'ПОСЛЕДНЯЯ АКТИВНОСТЬ', 'ЗАДАЧИ', 'НАПОМИНАНИЙ',
        'КАЧЕСТВО', ''].map((t) => h('th', t)))),
      h('tbody', ...people.map(participantRow)))));
}

function digestPanel(ctx) {
  const box = h('div.stack.sm');
  box.appendChild(h('div.small.dim', 'Дайджест собирается из чисел (участники, ответы, задержка, качество, здоровье) и не содержит текстов участников.'));
  const preview = h('div');
  const show = actionButton('Показать дайджест недели', async () => {
    try {
      const data = await api.raw('/api/jeff-insights/digest');
      preview.textContent = '';
      preview.appendChild(codeBlock(data.text));
      toastOk('Дайджест собран', data.last_sent_week === data.week ? 'Эта неделя уже отправлена в Пульт' : 'Ещё не отправлялся');
    } catch (e) { toastError(e, 'Не удалось собрать дайджест'); }
  }, { cls: 'btn', iconName: 'search' });
  const send = actionButton('Отправить в Пульт', async () => {
    try {
      const res = await api.raw('/api/jeff-insights/digest/send', { method: 'POST', body: {} });
      if (res.already) toastOk('Уже отправлено', `Дайджест недели ${res.week} в Пульте`);
      else if (res.delivered) toastOk('Дайджест положен в Пульт', res.week);
      else toastError(new Error(res.error || 'Пульт не принял дайджест'), 'Не отправлено');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось отправить'); }
  }, { cls: 'btn btn-primary', iconName: 'check', title: 'Один раз за неделю; повторная отправка ничего не дублирует' });
  box.append(h('div.row.tight', { style: { gap: '8px' } }, show, send), preview);
  return panel('Недельный дайджест для Пульта', box);
}

const JeffInsightsPage = {
  id: 'jeff-insights',
  title: 'Jeff · обзор',
  icon: 'agents',
  nav: 'more',
  section: 'brains',

  async render(ctx) {
    const head = pageHead('Jeff · обзор',
      'Кто пишет Jeff, что о них знает Master Parser, здоров ли Jeff и куда идут цифры. Только для владельца; тексты сообщений и нарративов в списках и дайджесте не показываются.',
      { actions: [actionButton('Обновить', async () => { ctx.refresh(); toastOk('Обновлено'); }, { cls: 'btn btn-sm', iconName: 'retry' })] });
    let data;
    try { data = await api.raw('/api/jeff-insights/overview'); }
    catch (err) { return h('div.bx-page', head, errorNote(err, () => ctx.refresh())); }
    const notes = data.configured ? null : panel('Jeff не настроен',
      h('div.small.dim', 'В данных этого Bossman нет настройки Jeff (pit-v1.7/config.json). Страница покажет данные, когда Jeff начнёт работу.'));
    return h('div.bx-page', { dataset: { testid: 'jeff-insights' } }, head, notes,
      healthPanel(data.health || {}), trendsPanel(data.trends || { days: [], series: {} }),
      participantsPanel(data.participants || []), digestPanel(ctx));
  },

  onEvent() { return false; },
};

export default JeffInsightsPage;
