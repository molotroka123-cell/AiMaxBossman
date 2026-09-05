/* ============================================================
   control.js — «Пульт владельца» (TRUTH-003 §20).

   Один экран отвечает на восемь вопросов по каждой работе:
   КТО · ГДЕ · КАКАЯ МОДЕЛЬ · ЧТО · СОСТОЯНИЕ ДЕЙСТВИЯ · ПОЧЕМУ
   ЗАБЛОКИРОВАНО · ЦЕНА · ТРЕБУЕТ ВНИМАНИЯ.

   Правило экрана: зелёный COMPLETE показывается только когда сработал
   канонический финализатор. Всё остальное — жёлтое или серое, каким бы
   уверенным ни был ответ модели. Данные — из /api/control-plane
   (durable-источники), страница ничего не досчитывает сама.
   ============================================================ */

import { api } from '../api.js';
import { h, icon, toastError } from '../components.js';
import { panel, pageHead, errorBanner } from './_shared.js';

/* Цвет = уровень доказанности, а не оптимизм. Зелёный — только COMPLETE. */
const STATE_TONE = {
  COMPLETE: 'ok',
  VERIFIED: 'warn',
  OBSERVED: 'warn',
  EXECUTED: 'warn',
  DISPATCHED: 'idle',
  PLACED: 'idle',
  BLOCKED: 'warn',
  FAILED: 'err',
  STOPPED: 'idle',
};

const STATE_HINT = {
  PLACED: 'работа поставлена, исполнение ещё не начиналось',
  DISPATCHED: 'отправлена исполнителю; эффекта пока нет',
  EXECUTED: 'инструмент вызван — это ещё не доказательство эффекта',
  OBSERVED: 'состояние прочитано, проверка не подтвердила ожидание',
  VERIFIED: 'эффект подтверждён свежим чтением; работа ещё не финализирована',
  COMPLETE: 'финализатор закрыл работу: проверки пройдены',
  BLOCKED: 'нужен ответ владельца',
  FAILED: 'исполнение не удалось',
  STOPPED: 'остановлено',
};

function stateChip(row) {
  const tone = STATE_TONE[row.action_state] || 'idle';
  return h('span', {
    class: `badge badge-${tone}`,
    title: STATE_HINT[row.action_state] || '',
  }, row.action_state);
}

function money(v) {
  const n = Number(v || 0);
  return n ? `$${n.toFixed(n < 0.01 ? 6 : 4)}` : '—';
}

function rowNode(r) {
  return h('tr', { class: r.attention ? 'attention' : '' },
    h('td', h('div.mono.xsmall.dim', `#${r.task_id}`), h('div', r.what)),
    h('td', r.who),
    h('td.mono.xsmall', r.where),
    h('td.mono.xsmall', r.model),
    h('td', stateChip(r),
      r.effects ? h('div.xsmall.dim',
        `вызвано ${r.effects.executed} · наблюдалось ${r.effects.observed} · подтверждено ${r.effects.verified}`) : null),
    h('td.small', r.why_blocked || '—'),
    h('td.mono.small', money(r.cost_usd)),
    h('td', r.attention ? h('span.badge.badge-warn', 'да') : h('span.dim', '—')));
}

function table(rows) {
  if (!rows.length) return h('div.dim.small', 'работ пока нет');
  return h('div', { style: { overflowX: 'auto' } },
    h('table.table',
      h('thead', h('tr',
        h('th', 'ЧТО'), h('th', 'КТО'), h('th', 'ГДЕ'), h('th', 'МОДЕЛЬ'),
        h('th', 'СОСТОЯНИЕ'), h('th', 'ПОЧЕМУ ЗАБЛОКИРОВАНО'), h('th', 'ЦЕНА'), h('th', 'ВНИМАНИЕ'))),
      h('tbody', ...rows.map(rowNode))));
}

function facts(body) {
  const t = body.treasury || {};
  const fleet = body.fleet || {};
  const lat = body.latency || {};
  const line = (k, v, title) => h('div.row.tight', h('span.dim.small', k), h('div.spacer'),
    h('span.mono.small', { title: title || '' }, v));
  return h('div.stack',
    line('очередь', Object.entries(body.queue || {}).map(([k, v]) => `${k}:${v}`).join(' · ') || '—'),
    line('расход за час', money(t.burn_rate_usd_per_h)),
    line('остаток бюджета', t.fable && t.fable.status === 'OK' ? money(t.fable.remaining_usd) : (t.fable && t.fable.status) || '—'),
    line('флот', fleet.enabled
      ? `узлов ${(fleet.nodes || []).length} · очередь ${fleet.queue_depth ?? '—'}`
      : 'выключен'),
    line('удалённый транспорт', fleet.enabled ? (fleet.remote_transport_production_ready ? 'ДА' : 'НЕТ (не production)') : '—'),
    line('исполнение p95', lat.execution_ms ? `${lat.execution_ms.p95 ?? '—'} мс` : '—'),
    line('проверка p95', lat.verification_ms ? `${lat.verification_ms.p95 ?? '—'} мс` : '—'));
}

const ControlPage = {
  id: 'control',
  title: 'Пульт',
  icon: 'home',
  nav: 'primary',

  async render(ctx) {
    let body;
    try {
      body = await api.raw('/api/control-plane');
    } catch (e) {
      return errorBanner(e, ctx);
    }
    const view = body.owner_view || { rows: [], rule: '' };
    const rows = view.rows || [];
    const attention = rows.filter((r) => r.attention);
    ctx.setBadge && ctx.setBadge('control', attention.length);

    return h('div.stack',
      pageHead('Пульт владельца',
        'кто, где, какой моделью, что делает, доказано ли, почему стоит и сколько стоило',
        [h('button.btn.btn-sm', {
          type: 'button',
          onClick: async () => { try { await ctx.refresh(); } catch (e) { toastError(e, 'Не удалось обновить'); } },
        }, icon('retry', 12), h('span', 'Обновить'))]),
      attention.length
        ? panel(h('h2', `Требует вашего решения (${attention.length})`), table(attention))
        : null,
      panel('Работы', table(rows)),
      panel('Состояние системы', facts(body)),
      h('div.xsmall.dim', view.rule || ''));
  },

  onEvent(ev) {
    // Перерисовываем только по событиям жизненного цикла — не по каждому логу.
    return ['task.created', 'task.queued', 'task.finalized', 'task.failed', 'task.stopped',
      'approval.created', 'approval.decided', 'verification.result'].includes(ev.kind);
  },
};

export default ControlPage;
