/* ============================================================
   self_improve.js — только чтение: цикл самоулучшения роя.
   Endpoints: GET /api/self-improve/state, GET /api/self-improve/botmap.
   Статусы PASS / FAIL / BLOCKED / NOT_RUN / N/A показываются буквально.
   Здесь нет ни одной кнопки записи: применение возможно только токеном
   владельца из терминала.
   ============================================================ */

import { api } from '../api.js';
import { h } from '../components.js';
import { panel, pageHead, errorNote, tag, stat } from './_ui.js';

const lit = (v) => (v === undefined || v === null || v === '' ? 'N/A' : String(v));

const SelfImprovePage = {
  id: 'self-improve',
  title: 'Самоулучшение',
  icon: 'info',
  nav: 'more',
  section: 'brains',

  async render(ctx) {
    const [stR, mapR] = await Promise.allSettled([
      api.raw('/api/self-improve/state'), api.raw('/api/self-improve/botmap'),
    ]);
    const head = pageHead('Самоулучшение роя',
      'Режим «только чтение»: боты находят повторяющиеся ошибки и предлагают правки, проверка идёт на отложенных задачах '
      + 'независимым проверяющим. Ничего не применяется без токена владельца. Слово «улучшено» — только после PASS.');
    if (stR.status === 'rejected') return h('div.bx-page', head, errorNote(stR.reason, () => ctx.refresh()));
    const s = stR.value;

    const claim = panel('Что можно утверждать', h('p', lit(s.gain_claim)),
      { aside: tag(lit(s.status || 'OK'), { accent: true }) });

    const journal = s.journal || {};
    const top = panel('Журнал и стоимость', h('div.bx-stats',
      stat('Журнал (хеш-цепочка)', journal.ok === true ? 'цел' : (journal.ok === false ? `НАРУШЕН: ${lit(journal.reason)}` : 'N/A')),
      stat('Записей', lit(journal.entries)),
      stat('Наблюдений / сбоев', `${lit((s.observations || {}).total)} / ${lit((s.observations || {}).failures)}`),
      stat('Потрачено, USD', lit((s.cost || {}).total_usd)),
      stat('Лимит затрат, USD', lit((s.cost || {}).cap_usd))));

    const hyp = panel(`Гипотезы · ${(s.hypotheses || []).length}`,
      (s.hypotheses || []).length ? table(['ID', 'Состояние', 'Агент', 'Класс ошибки', 'Сбоев', 'Черновик'],
        s.hypotheses.map((x) => [x.id, x.state, x.agent_id, x.error_class, x.count, x.draft_id])) : h('p.muted', 'NOT_RUN: гипотез пока нет'));

    const res = panel(`Результаты на отложенных задачах · ${(s.results || []).length}`,
      (s.results || []).length ? table(['Гипотеза', 'Статус', 'База', 'Кандидат', 'Регрессий', 'USD', 'Причина'],
        s.results.map((r) => [r.hypothesis_id, r.status, (r.baseline || {}).pass_rate, (r.candidate || {}).pass_rate,
          (r.regressions || []).length, r.cost_usd, r.reason])) : h('p.muted', 'NOT_RUN: экспериментов не было'));

    const owner = panel(`Нужно решение владельца · ${(s.owner_decision_required || []).length}`,
      (s.owner_decision_required || []).length ? table(['Гипотеза', 'Черновик', 'Как применить'],
        s.owner_decision_required.map((x) => [x.id, x.draft_id, 'токен владельца: python -m bcc.self_improve issue-token']))
        : h('p.muted', 'N/A: решений не ожидается'));

    const rb = panel('Откат и отказы', h('div',
      (s.rollback || []).length ? table(['Применение', 'Откат'], s.rollback.map((x) => [x.apply_id, x.rollback || x.last]))
        : h('p.muted', 'N/A: ничего не применялось'),
      h('p.muted', `Отказов без токена: ${(s.refusals || []).length}`)));

    let mapPanel;
    if (mapR.status === 'fulfilled' && Array.isArray(mapR.value.agents) && mapR.value.agents.length) {
      const m = mapR.value;
      mapPanel = panel(`Карта ботов · версия ${lit(m.map_version)}`, table(['ID', 'Имя', 'Состояние', 'Инструменты', 'Задач'],
        m.agents.map((a) => [a.id, a.name, a.state, (a.tools || []).join(', ') || '—', a.tasks_seen])));
    } else {
      mapPanel = panel('Карта ботов', h('p.muted', 'NOT_RUN: карта не сгенерирована (python -m bcc.self_improve botmap)'));
    }
    return h('div.bx-page', head, claim, top, hyp, res, owner, rb, mapPanel);
  },
};

function table(cols, rows) {
  return h('div', { style: { overflowX: 'auto' } }, h('table.table', h('thead', h('tr', cols.map((c) => h('th', c)))),
    h('tbody', rows.map((r) => h('tr', r.map((c) => h('td', lit(c))))))));
}

export default SelfImprovePage;
