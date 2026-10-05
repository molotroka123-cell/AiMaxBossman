/* ============================================================
   autonomy.js — контур автономии: цели, улики, панель релиза.
   Endpoints: GET /api/autonomy/status, GET /api/autonomy/goals,
              GET /api/autonomy/goals/{id},
              POST /api/autonomy/goals/{id}/apply|confirm|reject|revise,
              POST /api/autonomy/stop|resume (аварийный STOP цикла).

   Страница ничего не решает сама. Apply НЕ сливает и НЕ пушит: бэкенд
   записывает решение владельца (привязанное к SHA и хэшу diff) и отдаёт
   точные команды fast-forward и отката. «Выпущено» — только после того,
   как владелец сам выполнил команды и нажал «Подтвердить выпуск».
   Та же логика в терминале: bossman autonomy status|goals|journal verify|stop|resume.
   Опыт цикла — контекст для поиска (WEIGHTS_UNCHANGED): веса модели не меняются.
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

function evaluationNote(view) {
  const ev = view.evaluation;
  if (!ev) return '';
  const verdict = ev.decision || (ev.accepted ? 'ACCEPT' : 'REJECT');
  const reasons = (ev.reasons || []).join('; ');
  return h('div.small.dim', { style: 'margin-top:.5rem' },
    `оценка кандидата до одобрения: ${verdict} · измерено: ${ev.measured_on || '—'}` + (reasons ? ` · ${reasons}` : ''));
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
    evaluationNote(view),
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
      const pinned = st.constitution ? !!st.constitution.ok : st.loop === 'READY';
      pills.push(pill(pinned ? 'конституция закреплена' : 'BLOCKED',
        { tone: pinned ? 'ok' : 'err', title: pinned ? '' : (st.reason || '') }));
      pills.push(pill('уровень', { tone: 'idle', value: st.level }));
      if (st.mode) {
        const off = st.mode.autonomous_apply === 'OFF';
        pills.push(pill('автоприменение', { tone: off ? 'ok' : 'err', value: off ? 'ВЫКЛ' : st.mode.autonomous_apply,
          title: st.mode.reason || '' }));
      }
      if (st.stop) {
        pills.push(pill('STOP', { tone: st.stop.active ? 'err' : 'ok', value: st.stop.active ? 'включён' : 'нет',
          title: st.stop.reason || '' }));
      }
      pills.push(pill(st.weights || 'WEIGHTS_UNCHANGED', { tone: 'idle',
        title: 'Опыт цикла сохраняется как контекст для поиска (retrieval_context). Веса модели не меняются.' }));
      if (st.promotion) {
        pills.push(pill('чистых циклов', { tone: 'idle', value: `${st.promotion.clean_cycles}/${st.promotion.required}`,
          title: 'Только чтение: уровень этим не повышается.' }));
      }
      if (st.budget && st.budget.used && st.budget.limits) {
        pills.push(pill('бюджет дня', { tone: 'idle',
          value: `${st.budget.used.cycles}/${st.budget.limits.cycles_per_day} циклов`, title: st.budget.day || '' }));
      }
      pills.push(pill('журнал', { tone: st.journal && st.journal.ok ? 'ok' : 'err', value: st.journal ? st.journal.entries : 0 }));
      if (st.lease) pills.push(pill('пишет', { tone: 'warn', value: st.lease.holder, live: true }));
    }
    const stopOn = !!(st && st.stop && st.stop.active);
    const actions = [
      btn('STOP автономии', () => post('/api/autonomy/stop', { reason: 'owner STOP (панель)' }, 'STOP записан', ctx),
        { size: 'sm', variant: 'danger', title: 'Остановить цикл: записать STOP и завершить процессы писателя' }),
      btn('Снять STOP', () => post('/api/autonomy/resume', {}, 'STOP автономии снят', ctx),
        { size: 'sm', disabled: !stopOn,
          title: stopOn ? 'Снять STOP автономии (общий STOP владельца снимается отдельно)' : 'STOP автономии не установлен' }),
    ];
    const head = pageHead('Автономия', HEAD_SUB, { pills, actions });
    const notice = st && st.loop === 'BLOCKED'
      ? panel('Цикл остановлен', h('div.small', `${st.reason} Закрепить может только владелец: bossman autonomy constitution pin (в своём терминале).`))
      : (st && st.loop === 'STOPPED'
        ? panel('Цикл остановлен', h('div.small', `${st.reason}. Снять: кнопка «Снять STOP» или bossman autonomy resume.`))
        : '');
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
