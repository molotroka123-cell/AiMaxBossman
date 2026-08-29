/* ============================================================
   governor.js — Feature 03: AI Governor.
   Endpoints: GET /api/governor/interventions, GET/PATCH /api/governor/rules.
   ============================================================ */

import { api, pick } from '../api.js';
import {
  h, toastOk, toastError,
  actionButton, field, input, fmtClock,
} from '../components.js';
import { panel, pageHead, errorNote, statusText } from './_ui.js';

const GovernorPage = {
  id: 'governor',
  title: 'Присмотр',
  icon: 'info',
  nav: 'more',

  async render(ctx) {
    const [rulesR, interR] = await Promise.allSettled([
      api.raw('/api/governor/rules'), api.raw('/api/governor/interventions?limit=100'),
    ]);
    const rules = rulesR.status === 'fulfilled' ? rulesR.value : null;
    const items = interR.status === 'fulfilled' ? (Array.isArray(interR.value) ? interR.value : []) : [];

    const head = pageHead('Присмотр за агентами',
      'BOSSMAN сам останавливает работу, которая зациклилась или зря тратит деньги, и зовёт вас, если что-то застряло.');

    const rulesPanel = rulesR.status === 'rejected'
      ? errorNote(rulesR.reason, () => ctx.refresh())
      : buildRulesPanel(rules, ctx);

    const feed = interR.status === 'rejected'
      ? errorNote(interR.reason, () => ctx.refresh())
      : panel(`Когда пришлось вмешаться · ${items.length}`,
        items.length
          ? h('div.log', items.map(interventionRow))
          : h('div.log-empty', 'Вмешиваться пока не приходилось — всё идёт как надо.'));

    return h('div.bx-page', head, rulesPanel, feed);
  },

  onEvent(ev) { return ev.kind === 'governor.intervention'; },
};

function interventionRow(it) {
  const tone = { paused: 'warn', stopped: 'idle', switched: 'warn', throttled: 'warn', escalated: 'err' }[it.action] || 'idle';
  return h('div.log-line', { class: tone === 'err' ? 'lv-error' : tone === 'warn' ? 'lv-warn' : '' },
    h('span.log-ts', fmtClock(pick(it, ['created_at']))),
    h('span.log-msg',
      h('b', `${it.target_kind}:${it.target_id}`), ' — ',
      h('span', statusText(it.action).word), ' · ', it.reason || '—'));
}

function buildRulesPanel(rules, ctx) {
  const r = rules || { repeated_error_limit: 3, no_progress_steps: 6, max_retries: 5 };
  const errEl = input({ type: 'number', min: '1', value: String(r.repeated_error_limit ?? 3), class: 'input mono' });
  const npEl = input({ type: 'number', min: '1', value: String(r.no_progress_steps ?? 6), class: 'input mono' });
  const retriesEl = input({ type: 'number', min: '0', value: String(r.max_retries ?? 5), class: 'input mono' });

  return panel('Когда вмешиваться', h('div.stack.sm',
    h('div.grid.cols-3',
      field('Повторов одной ошибки', errEl, 'После скольких одинаковых ошибок остановить задачу.'),
      field('Шагов на месте', npEl, 'Сколько одинаковых ответов подряд считать «застрял» и ставить на паузу.'),
      field('Повторов у самой задачи', retriesEl, 'Не вмешиваться, пока задача сама пробует справиться.')),
    h('div.row', h('div.spacer'),
      actionButton('Сохранить', async () => {
        try {
          await api.raw('/api/governor/rules', {
            method: 'PATCH',
            body: {
              repeated_error_limit: Number(errEl.value) || 3,
              no_progress_steps: Number(npEl.value) || 6,
              max_retries: Number(retriesEl.value) || 5,
            },
          });
          toastOk('Пороги обновлены');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось сохранить пороги'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'check' }))));
}

export default GovernorPage;
