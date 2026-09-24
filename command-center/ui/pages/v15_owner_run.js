import { api } from '../api.js';
import { h, actionButton, field, input, toastOk, toastError } from '../components.js';
import { panel, pageHead, pill, tile } from './_ui.js';

function stateTone(value) {
  const s = String(value || '').toUpperCase();
  if (['RUNNING','PASS','VERIFIED','COMPLETE_QUARANTINED','SELF_IMPROVEMENT_PROCESS_STARTED'].some(x => s.includes(x))) return 'ok';
  if (['FAIL','BLOCKED','ERROR'].some(x => s.includes(x))) return 'err';
  if (['OWNER_REQUIRED','PARTIAL','PENDING','STARTING'].some(x => s.includes(x))) return 'warn';
  return 'idle';
}

const Page = {
  id: 'v15-owner-run',
  title: 'Bossman 1.5',
  icon: 'bolt',
  nav: 'primary',
  section: 'brains',

  async render(ctx) {
    let s = {}, autonomy = {}, repairs = {};
    try {
      const rows = await Promise.allSettled([
        api.raw('/api/v15/owner-run/status'),
        api.raw('/api/v15/autonomy/status'),
        api.raw('/api/v15/self-repair/inbox?limit=20'),
      ]);
      if (rows[0].status === 'rejected') throw rows[0].reason;
      s = rows[0].value || {};
      autonomy = rows[1].status === 'fulfilled' ? (rows[1].value || {}) : {};
      repairs = rows[2].status === 'fulfilled' ? (rows[2].value || {}) : {};
    } catch (e) {
      return h('div.bx-page', pageHead('Bossman 1.5', 'Самостоятельный режим'), h('p', String(e)));
    }

    const repo = input({ class: 'input mono', value: s.repo || '', placeholder: 'Путь к clean Bossman checkout (можно оставить пустым, если задан BOSSMAN_SELF_IMPROVE_REPO)' });
    const cycles = input({ class: 'input mono', type: 'number', min: '1', max: '20', value: '8' });
    const paid = h('input', { type: 'checkbox' });

    const controls = panel('Запуск',
      h('div.stack.sm',
        field('Исходники для самоулучшения', repo, 'Twitch может работать без repo; self-improve требует clean checkout.'),
        field('Циклов максимум', cycles, 'Bossman останавливается раньше при STOP/серии провалов/лимитах.'),
        h('label.row.gap-sm', paid, h('span', 'Разрешить ограниченный платный финализатор (free-first остаётся обязательным)')),
        h('div.row.gap-sm',
          actionButton('⚡ Quick Test', async () => {
            try {
              const out = await api.raw('/api/v15/owner-run/quick-test', { method: 'POST', body: {} });
              out.status === 'READY' ? toastOk('1.5 Quick Test: READY') : toastError(new Error((out.problems || []).join(', ')), '1.5 требует подготовки');
              ctx.refresh();
            } catch (e) { toastError(e, 'Quick Test не выполнен'); }
          }, { cls: 'btn btn-secondary', iconName: 'check' }),
          actionButton('▶ Запустить 1.5', async () => {
            try {
              await api.raw('/api/v15/owner-run/start', { method: 'POST', body: {
                repo: repo.value || null, cycles: Number(cycles.value) || 8,
                allow_glm: !!paid.checked, cadence: 15,
              }});
              toastOk('Bossman 1.5 запущен'); ctx.refresh();
            } catch (e) { toastError(e, 'Не удалось запустить'); }
          }, { cls: 'btn btn-primary', iconName: 'bolt' }),
          actionButton('⛔ STOP', async () => {
            try {
              await api.raw('/api/v15/owner-run/stop', { method: 'POST', body: {} });
              toastOk('STOP запрошен'); ctx.refresh();
            } catch (e) { toastError(e, 'STOP не подтверждён'); }
          }, { cls: 'btn btn-danger', iconName: 'close' }),
          actionButton('↻ Обновить', () => ctx.refresh(), { cls: 'btn btn-secondary', iconName: 'retry' })
        )
      ));

    const market = s.market || {};
    const si = s.self_improvement || {};
    const evo = si.evolution || {};
    const tiles = h('div.bx-cards',
      tile({ title: 'Twitch OI/CVD', sub: 'Сбор + VERIFIED анализ + Telegram', statusNode: pill(market.state || 'NOT_STARTED', { tone: stateTone(market.state) }),
             body: [h('p.xsmall.dim', market.last ? `последнее: ${market.last.status || '—'} · попыток: ${market.attempts_this_run ?? '—'}` : 'ещё нет наблюдений')] }),
      tile({ title: 'Self-Improve', sub: 'ошибка → patch → tests → verifier → lesson → unseen transfer',
             statusNode: pill(evo.status || (s.processes?.self_improve?.alive ? 'RUNNING' : 'NOT_STARTED'), { tone: stateTone(evo.status || s.status) }),
             body: [h('p.xsmall.dim', `цикл: ${evo.cycle ?? '—'} · halt: ${evo.halt_reason || '—'}`)] }),
      tile({ title: 'Данные владельца', sub: 'Bossman спрашивает недостающие поля через Telegram',
             statusNode: pill(String(s.owner_inputs_pending ?? '—'), { tone: (s.owner_inputs_pending || 0) ? 'warn' : 'ok' }),
             body: [h('p.xsmall.dim', 'Значения подставляет runtime; submit/ToS остаются отдельным ASK.')] }),
      tile({ title: 'Self-repair inbox', sub: 'реальные code/harness ошибки → isolated candidates',
             statusNode: pill(String((repairs.items || []).length), { tone: (repairs.items || []).length ? 'warn' : 'ok' }),
             body: [h('p.xsmall.dim', 'Stable не меняется автоматически. DONE требует executable verifier.')] }),
      tile({ title: 'Autonomy kernel', sub: 'persistent roles + operating graph + cost/quality routing',
             statusNode: pill(autonomy.wired ? 'WIRED' : 'UNKNOWN', { tone: autonomy.wired ? 'ok' : 'idle' }),
             body: [h('p.xsmall.dim', `roles: ${(autonomy.roles || []).length} · graph: ${autonomy.graph?.nodes ?? '—'} nodes / ${autonomy.graph?.edges ?? '—'} edges`)] })
    );

    const head = pageHead('Bossman 1.5',
      'Самостоятельный оператор: работает, учится, чинит candidate, наблюдает рынок и зовёт владельца только за недостающими данными/подтверждением.',
      { pills: [
        pill(s.running ? 'работает' : 'остановлен', { tone: s.running ? 'ok' : 'idle' }),
        pill('торговля OFF', { tone: 'ok' }),
        pill('внешний аудитор не нужен runtime', { tone: 'info' }),
      ]});

    return h('div.bx-page', head, controls, panel('Сейчас', tiles),
      panel('Правило 1.5', h('p.small',
        'Обычная ошибка не заканчивает задачу: Bossman должен диагностировать, исправить в изолированном candidate, прогнать verifier и unseen transfer, сохранить доказанный урок и продолжить. Stable/release не переписывается автоматически.')));
  },

  onEvent(ev) { return String(ev.kind || '').startsWith('v15.') || String(ev.kind || '').startsWith('owner.input_'); },
};

export default Page;
