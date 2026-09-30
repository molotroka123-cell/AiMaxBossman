/* ============================================================
   autonomy.js — контур автономии: цели, улики, панель релиза.
   Endpoints: GET /api/autonomy/status, GET /api/autonomy/goals,
              GET /api/autonomy/goals/{id},
              POST /api/autonomy/goals/{id}/apply|confirm|reject|revise.

   Страница ничего не решает сама. Apply НЕ сливает и НЕ пушит: бэкенд
   записывает решение владельца (привязанное к SHA и хэшу diff) и отдаёт
   точные команды fast-forward и отката. «Выпущено» — только после того,
   как владелец сам выполнил команды и нажал «Подтвердить выпуск».
   Та же логика в терминале: bossman autonomy status|goals|journal verify.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, openModal } from '../components.js';
import { pageHead, panel, pill, errorNote, blank, btn, codeBlock, statusPill } from './_ui.js';

const state = { selected: '' };

const HEAD_SUB = 'Цели самоулучшения, их улики и решение владельца. Конституция и журнал — под контролем.';

function short(sha) { return String(sha || '').slice(0, 10) || '—'; }

function showCommands(title, cmds) {
  openModal({
    title, wide: true,
    body: h('div',
      h('div.small.dim', 'Bossman ничего не сливает и не пушит сам. Выполните в своём терминале:'),
      codeBlock((cmds.release || []).join('\n')),
      h('div.small.dim', `Ожидаемый HEAD: ${cmds.expect_head || ''}. Если что-то пошло не так — откат:`),
      codeBlock((cmds.rollback || []).join('\n'))),
  });
}

async function post(path, body, okText, ctx) {
  try {
    const r = await api.raw(path, { method: 'POST', body: body || {} });
    if (okText) toastOk(okText);
    ctx.refresh();
    return r;
  } catch (e) { toastError(e); ctx.refresh(); return null; }
}

function actionBtn(label, action, onClick, opts = {}) {
  const allowed = !!(action && action.allowed);
  return btn(label, allowed ? onClick : null, {
    size: 'sm', variant: opts.variant, disabled: !allowed,
    title: allowed ? (opts.title || label) : ((action && action.reason) || 'недоступно'),
  });
}

function releasePanel(view, ctx) {
  const gid = view.goal_id;
  const cand = (view.record && view.record.candidate) || {};
  const bound = { sha: cand.sha, diff_sha256: cand.diff_sha256 };
  const a = view.actions || {};
  return panel('Панель релиза', h('div',
    h('div.small.dim', `кандидат ${short(cand.sha)} · diff ${short(cand.diff_sha256)} · ` +
      `одобрения: ${(view.approvals || []).join(', ') || 'нет'} · staging: ${view.staging_passed ? 'PASS' : '—'}`),
    h('div', { style: 'display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem' },
      actionBtn('Apply', a.apply, async () => {
        const r = await post(`/api/autonomy/goals/${gid}/apply`, bound, 'решение записано', ctx);
        if (r && r.commands) showCommands(`Команды релиза · ${gid}`, r.commands);
      }, { variant: 'primary', title: 'Записать решение и получить команды fast-forward / отката' }),
      actionBtn('Подтвердить выпуск', a.confirm,
        () => post(`/api/autonomy/goals/${gid}/confirm`, bound, 'выпуск подтверждён', ctx),
        { title: 'Нажмите только после того, как сами выполнили команды релиза' }),
      actionBtn('Revise', a.revise, () => post(`/api/autonomy/goals/${gid}/revise`, { note: '' }, 'отправлено на доработку', ctx)),
      actionBtn('Reject', a.reject, () => post(`/api/autonomy/goals/${gid}/reject`, { note: '' }, 'отклонено', ctx),
        { variant: 'danger' })),
    view.commands ? h('div', { style: 'margin-top:.5rem' },
      btn('Показать команды', () => showCommands(`Команды релиза · ${gid}`, view.commands), { size: 'sm' })) : ''));
}

function evidencePanel(view) {
  const rows = (view.evidence || []).slice(-60).reverse();
  return panel(`Улики (${(view.evidence || []).length})`, rows.length
    ? h('table.table',
      h('thead', h('tr', ...['#', 'Время', 'Событие', 'Детали'].map((t) => h('th', t)))),
      h('tbody', ...rows.map((e) => h('tr',
        h('td.small', String(e.seq)),
        h('td.small', String(e.ts || '').replace('T', ' ').slice(0, 19)),
        h('td.small', e.kind),
        h('td.small', { style: 'max-width:36rem;white-space:pre-wrap;word-break:break-word' },
          JSON.stringify(e.payload || {}).slice(0, 400))))))
    : h('div.small.dim', 'Улик пока нет'));
}

function goalView(view, ctx) {
  return h('div',
    panel(`${view.goal_id} · ${view.state}`, h('div',
      h('div', view.problem),
      h('div.small.dim', `уровень риска: ${view.risk_tier} · целевая метрика: ${view.target_metric}`),
      view.blocked_reason ? h('div.small', { style: 'color:var(--bx-rose)' }, `BLOCKED: ${view.blocked_reason}`) : '')),
    releasePanel(view, ctx),
    evidencePanel(view));
}

const AutonomyPage = {
  id: 'autonomy',
  title: 'Автономия',
  icon: 'check',
  nav: 'more',
  section: 'system',

  async render(ctx) {
    const [statusR, listR] = await Promise.allSettled([
      api.raw('/api/autonomy/status'), api.raw('/api/autonomy/goals'),
    ]);
    if (listR.status === 'rejected') {
      return h('div.bx-page', pageHead('Автономия', HEAD_SUB), errorNote(listR.reason, () => ctx.refresh()));
    }
    const st = statusR.status === 'fulfilled' ? statusR.value : null;
    const items = (listR.value && Array.isArray(listR.value.items)) ? listR.value.items : [];
    const pills = [];
    if (st) {
      pills.push(pill(st.loop === 'READY' ? 'конституция закреплена' : 'BLOCKED',
        { tone: st.loop === 'READY' ? 'ok' : 'err', title: st.reason || '' }));
      pills.push(pill('уровень', { tone: 'idle', value: st.level }));
      pills.push(pill('журнал', { tone: st.journal && st.journal.ok ? 'ok' : 'err', value: st.journal ? st.journal.entries : 0 }));
      if (st.lease) pills.push(pill('пишет', { tone: 'warn', value: st.lease.holder, live: true }));
    }
    const head = pageHead('Автономия', HEAD_SUB, { pills });
    const notice = st && st.loop !== 'READY'
      ? panel('Цикл остановлен', h('div.small', `${st.reason} Закрепить может только владелец: bossman autonomy constitution pin (в своём терминале).`))
      : '';
    if (!items.length) {
      return h('div.bx-page', head, notice, blank({
        iconName: 'empty', title: 'Целей пока нет',
        hint: 'Цель появится, когда Jev предложит измеримое улучшение с бюджетом и тестами приёмки.',
      }));
    }
    const gid = state.selected && items.some((i) => i.goal_id === state.selected) ? state.selected : items[0].goal_id;
    let view = null; let viewErr = null;
    try { view = await api.raw(`/api/autonomy/goals/${encodeURIComponent(gid)}`); } catch (e) { viewErr = e; }
    const list = panel('Цели', h('table.table',
      h('thead', h('tr', ...['Цель', 'Статус', 'Риск', 'SHA', 'Одобрения'].map((t) => h('th', t)))),
      h('tbody', ...items.map((i) => h('tr',
        h('td', h('a', { href: '#', onClick: (ev) => { ev.preventDefault(); state.selected = i.goal_id; ctx.refresh(); } },
          i.goal_id), h('div.small.dim', String(i.problem || '').slice(0, 120))),
        h('td', statusPill(i.state)),
        h('td.small', i.risk_tier),
        h('td.small', short(i.sha)),
        h('td.small', (i.approvals || []).join(', ') || '—'))))));
    return h('div.bx-page', head, notice, list,
      view ? goalView(view, ctx) : (viewErr ? errorNote(viewErr, () => ctx.refresh()) : ''));
  },

  onEvent(ev) { return typeof ev.kind === 'string' && ev.kind.startsWith('autonomy.'); },
};

export default AutonomyPage;
