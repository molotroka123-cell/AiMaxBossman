/* ============================================================
   healing.js — Feature 14: Self-Healing.
   Endpoints: GET /api/healing/attempts, GET/PATCH /api/healing/rules.
   ============================================================ */

import { api } from '../api.js';
import { h, statusLabel, toastOk, toastError, actionButton, field, input, fmtClock } from '../components.js';
import { panel, pageHead, errorBanner } from './_shared.js';

const HealingPage = {
  id: 'healing',
  title: 'Self-Healing',
  icon: 'check',
  nav: 'more',

  async render(ctx) {
    const [rulesR, attemptsR] = await Promise.allSettled([
      api.raw('/api/healing/rules'), api.raw('/api/healing/attempts?limit=100'),
    ]);
    const rules = rulesR.status === 'fulfilled' ? rulesR.value : null;
    const items = attemptsR.status === 'fulfilled' ? (Array.isArray(attemptsR.value) ? attemptsR.value : []) : [];

    const head = pageHead('Self-Healing', 'Автовосстановление упавших endpoint’ов моделей и других подсистем');

    const rulesPanel = rulesR.status === 'rejected' ? errorBanner(rulesR.reason, ctx) : buildRulesPanel(rules, ctx);

    const feed = attemptsR.status === 'rejected'
      ? errorBanner(attemptsR.reason, ctx)
      : panel(`Попытки восстановления (${items.length})`,
        items.length
          ? h('div.log', items.map(attemptRow))
          : h('div.log-empty', 'Пока всё стабильно — попыток восстановления не было.'));

    return h('div.stack.lg', head, rulesPanel, feed);
  },

  onEvent(ev) { return ev.kind.startsWith('recovery.') || ev.kind === 'model.degraded'; },
};

function attemptRow(a) {
  const tone = { started: 'lv-warn', completed: '', escalated: 'lv-error' }[a.status] || '';
  return h('div.log-line', { class: tone },
    h('span.log-ts', fmtClock(a.created_at)),
    h('span.log-msg',
      h('b', `${a.target_kind}:${a.target_id ?? '—'}`), ' — ',
      statusBadgeInline(a.status), ' ', h('span.mono', a.action), a.failure ? ` · ${a.failure}` : ''));
}

function statusBadgeInline(status) {
  return h('span.small', statusLabel(status));
}

function buildRulesPanel(rules, ctx) {
  const r = rules || { window_seconds: 300, error_threshold: 3, attempt_limit: 3 };
  const winEl = input({ type: 'number', min: '10', value: String(r.window_seconds ?? 300), class: 'input mono' });
  const errEl = input({ type: 'number', min: '1', value: String(r.error_threshold ?? 3), class: 'input mono' });
  const limitEl = input({ type: 'number', min: '1', value: String(r.attempt_limit ?? 3), class: 'input mono' });

  return panel('Пороги', h('div.stack.sm',
    h('div.grid.cols-3',
      field('Окно ошибок, сек', winEl, 'В течение какого окна считаем сетевые ошибки подряд.'),
      field('Порог ошибок', errEl, 'Сколько сетевых ошибок в окне — сигнал деградации.'),
      field('Лимит попыток', limitEl, 'После скольких попыток — эскалация человеку.')),
    h('div.row', h('div.spacer'),
      actionButton('Сохранить', async () => {
        try {
          await api.raw('/api/healing/rules', {
            method: 'PATCH',
            body: { window_seconds: Number(winEl.value) || 300, error_threshold: Number(errEl.value) || 3, attempt_limit: Number(limitEl.value) || 3 },
          });
          toastOk('Пороги обновлены');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось сохранить'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'check' }))));
}

export default HealingPage;
