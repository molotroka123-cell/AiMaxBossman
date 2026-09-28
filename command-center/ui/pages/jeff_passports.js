/* ============================================================
   jeff_passports.js — Jeff · паспорта: кнопка Master Parser.
   Endpoints: GET/POST /api/pit/master-parse, GET /api/pit/master-parse/report.
   Собирает все разговоры Jeff (Telegram, окно Jeff, выгрузки Telegram Desktop)
   в один корпус и обновляет паспорта участников. Источники не меняются и не
   удаляются; каждый добавленный факт можно отменить одним запуском.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, actionButton, field, input } from '../components.js';
import { panel, pageHead, errorNote, stat } from './_ui.js';

const POLL_MS = 1500;

const JeffPassportsPage = {
  id: 'jeff-passports',
  title: 'Jeff · паспорта',
  icon: 'agents',
  nav: 'primary',
  section: 'brains',

  async render(ctx) {
    let state;
    try { state = await api.raw('/api/pit/master-parse'); }
    catch (err) { return h('div.bx-page', head(), errorNote(err, () => ctx.refresh())); }
    let report = null;
    if (state.report && !state.running) {
      try { report = await api.raw('/api/pit/master-parse/report'); } catch { report = null; }
    }
    const root = h('div.bx-page', head(),
      state.configured ? startPanel(ctx, state) : panel('Jeff не настроен',
        h('div.small.dim', 'В этих данных Bossman нет настройки Jeff (pit-v1.7/config.json).')),
      progressPanel(ctx, state),
      report ? resultPanels(report) : panel('Результат', h('div.small.dim', 'Master Parser ещё не запускался.')));
    if (state.running) schedulePoll(ctx, root);
    return root;
  },

  onEvent() { return false; },
};

function head() {
  return pageHead('Jeff · паспорта',
    'Master Parser собирает все разговоры Jeff в один корпус и дополняет паспорта участников. '
    + 'Исходные данные только читаются и никогда не удаляются; участники без согласия на память не анализируются.');
}

function startPanel(ctx, state) {
  const who = input({ placeholder: 'все участники (или tg:123 / web:5)', class: 'input mono' });
  const since = input({ placeholder: 'за всё время (или 7d / 2026-09-01)', class: 'input mono' });
  const start = (dryRun) => async () => {
    try {
      await api.raw('/api/pit/master-parse', {
        method: 'POST',
        body: { participant: who.value.trim(), since: since.value.trim(), dry_run: dryRun },
      });
      toastOk(dryRun ? 'Пробный прогон запущен' : 'Master Parser запущен', 'Ход виден ниже');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось запустить'); }
  };
  return panel('Master Parser', h('div.stack.sm',
    h('div.grid.cols-2',
      field('Участник', who, 'Пусто — все участники.'),
      field('Только новее', since, 'Пусто — вся история; повторный запуск берёт только новое.')),
    h('div.row',
      actionButton('Запустить Master Parser', start(false),
        { cls: 'btn btn-primary', iconName: 'bolt', disabled: !!state.running }),
      actionButton('Пробный прогон', start(true),
        { cls: 'btn', iconName: 'search', disabled: !!state.running,
          title: 'Показать, что добавится, ничего не записывая' }),
      h('div.spacer'),
      h('span.small.dim', 'Команда: bossman pit master-parse · в пульте: /parse'))));
}

function progressText(s) {
  if (!s || s.state === 'never') return 'Ещё не запускался.';
  const phase = { collect: 'сбор разговоров', analyze: 'анализ паспортов', done: 'готово' }[s.phase] || s.phase;
  const state = { running: 'идёт', done: 'завершён', failed: 'ошибка', interrupted: 'прерван' }[s.state] || s.state;
  return `${state} · ${phase} · новых сообщений: ${s.collected_new ?? 0} · участники ${s.persons_done ?? 0}/${s.persons_total ?? 0}`
    + ` · сообщения ${s.messages_done ?? 0}/${s.messages_total ?? 0} · ${s.rate_msgs_per_s ?? 0} сообщ/с`
    + ` · фактов +${s.facts_added ?? 0} · конфликтов ${s.conflicts ?? 0}`;
}

function progressPanel(ctx, state) {
  const s = state.status || {};
  const line = h('div.mono.small', { 'data-mp-progress': '1' }, progressText(s));
  const err = state.last_error ? h('div.small.lv-error', `Последняя ошибка: ${state.last_error}`) : null;
  return panel(state.running ? 'Идёт Master Parser' : 'Последний запуск', h('div.stack.sm', line, err));
}

function schedulePoll(ctx, root) {
  const tick = async () => {
    if (!root.isConnected) return;
    let state;
    try { state = await api.raw('/api/pit/master-parse'); } catch { setTimeout(tick, POLL_MS * 2); return; }
    const line = root.querySelector('[data-mp-progress]');
    if (line) line.textContent = progressText(state.status);
    if (state.running) setTimeout(tick, POLL_MS);
    else ctx.refresh();
  };
  setTimeout(tick, POLL_MS);
}

function resultPanels(r) {
  const t = r.totals || {};
  const tiles = h('div.grid.cols-4',
    stat('Участников', t.participants ?? 0),
    stat('Сообщений в корпусе', r.corpus_messages ?? 0),
    stat('Фактов добавлено', t.facts_added ?? 0),
    stat('Конфликтов на проверку', t.conflicts ?? 0));
  const meta = h('div.small.dim',
    `${r.dry_run ? 'Пробный прогон — ничего не записано. ' : ''}Запуск ${r.run_id} · `
    + `проанализировано ${t.messages_analyzed ?? 0} сообщений за ${r.analysis_seconds ?? 0} с `
    + `(${t.messages_per_second ?? 0} сообщ/с) · уже было в паспортах: ${t.facts_known ?? 0}`
    + ` · не возвращено по воле участника: ${t.blocked_by_participant ?? 0}`);
  const revert = r.revert ? h('div.small', 'Отменить этот запуск: ', h('code', r.revert)) : null;
  const people = (r.participants || []).map(personBlock);
  return h('div.stack', panel('Результат', h('div.stack.sm', tiles, meta, revert)),
    panel(`Участники · ${people.length}`, people.length ? h('div.stack', people)
      : h('div.small.dim', 'Разговоров пока нет.')));
}

function personBlock(p) {
  const status = p.status === 'OK' ? '' : ' · без согласия на память — не анализировался';
  const facts = (p.facts_added || []).map((f) => h('div.log-line',
    h('span.log-msg', h('b', f.value), ` — ${f.category}/${f.key}`,
      (f.evidence || []).slice(0, 2).map((e) => h('div.small.dim', `«${e.snippet}» · ${e.source} · ${e.at}`)))));
  const conflicts = (p.conflicts || []).map((c) => h('div.log-line.lv-warn',
    h('span.log-msg', `${c.category}/${c.key}: было «${c.existing}», в разговоре «${c.proposed}»`)));
  return h('div.stack.sm',
    h('div', h('b', p.label), h('span.small.dim',
      ` · сообщений ${p.messages_total} · +${(p.facts_added || []).length} фактов`
      + ` · конфликтов ${(p.conflicts || []).length}${status}`)),
    facts.length ? h('div.log', facts) : null,
    conflicts.length ? h('div.log', conflicts) : null);
}

export default JeffPassportsPage;
