/* ============================================================
   healing.js — Feature 14: Self-Healing.
   Endpoints: GET /api/healing/attempts, GET/PATCH /api/healing/rules.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, field, input, fmtClock } from '../components.js';
import { panel, pageHead, errorNote, statusText } from './_ui.js';

const HealingPage = {
  id: 'healing',
  title: 'Восстановление',
  icon: 'check',
  nav: 'more',

  async render(ctx) {
    const [rulesR, attemptsR] = await Promise.allSettled([
      api.raw('/api/healing/rules'), api.raw('/api/healing/attempts?limit=100'),
    ]);
    const rules = rulesR.status === 'fulfilled' ? rulesR.value : null;
    const items = attemptsR.status === 'fulfilled' ? (Array.isArray(attemptsR.value) ? attemptsR.value : []) : [];

    const head = pageHead('Самовосстановление',
      'Если модель перестала отвечать или другая часть системы дала сбой, BOSSMAN сам пытается её вернуть в строй.');

    const rulesPanel = rulesR.status === 'rejected' ? errorNote(rulesR.reason, () => ctx.refresh()) : buildRulesPanel(rules, ctx);

    const feed = attemptsR.status === 'rejected'
      ? errorNote(attemptsR.reason, () => ctx.refresh())
      : panel(`Попытки вернуть в строй · ${items.length}`,
        items.length
          ? h('div.log', items.map(attemptRow))
          : h('div.log-empty', 'Пока всё работает стабильно — восстанавливать ничего не приходилось.'));

    return h('div.bx-page', head, rulesPanel, feed);
  },

  onEvent(ev) { return ev.kind.startsWith('recovery.') || ev.kind === 'model.degraded'; },
};

function attemptRow(a) {
  const tone = { started: 'lv-warn', completed: '', escalated: 'lv-error' }[a.status] || '';
  return h('div.log-line', { class: tone },
    h('span.log-ts', fmtClock(a.created_at)),
    h('span.log-msg',
      h('b', `${a.target_kind}:${a.target_id ?? '—'}`), ' — ',
      h('span.small', statusText(a.status).word), ' ', h('span.mono', a.action), a.failure ? ` · ${a.failure}` : ''));
}

function buildRulesPanel(rules, ctx) {
  const r = rules || { window_seconds: 300, error_threshold: 3, attempt_limit: 3 };
  const winEl = input({ type: 'number', min: '10', value: String(r.window_seconds ?? 300), class: 'input mono' });
  const errEl = input({ type: 'number', min: '1', value: String(r.error_threshold ?? 3), class: 'input mono' });
  const limitEl = input({ type: 'number', min: '1', value: String(r.attempt_limit ?? 3), class: 'input mono' });

  return panel('Когда восстанавливать', h('div.stack.sm',
    h('div.grid.cols-3',
      field('За сколько секунд считать', winEl, 'В каком промежутке времени учитывать ошибки подряд.'),
      field('Сколько ошибок — тревога', errEl, 'После скольких сбоев подряд считать, что модель «упала».'),
      field('Сколько раз пробовать', limitEl, 'Если не помогло — BOSSMAN позовёт вас.')),
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
