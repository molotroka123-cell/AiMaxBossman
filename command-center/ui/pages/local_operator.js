/* ============================================================
   local_operator.js — «Локальные модели»: что стоит в Ollama и чат с ними, с управлением компьютером.

   API (bcc/features/local_operator.py, под /api с обычной auth/CSRF):
     GET  /api/local-operator/status — модели Ollama сейчас (с возможностями) и уже созданные локальные агенты
     POST /api/local-operator/sync   — подключить модели к чату: «Локально · модель», «… · ПК», «Bossman Harness»

   Чат идёт в обычном окне /chat.html (те же задачи, подтверждения и STOP): кнопка «Открыть в чате»
   выбирает агента в окне чата и открывает его. Каждое действие на экране — отдельное ваше одобрение.
   Модуль безопасен для импорта в Node: document трогается только внутри render().
   ============================================================ */

import { api } from '../api.js';
import { h } from '../components.js';
import { pageHead, panel, pill, btn, errorNote } from './_ui.js';
import { modelBadges, readiness, syncSummary, humanSize, chatRoute, CHAT_ROUTE_KEY, HARNESS_NAME }
  from './local_operator_model.js';

function openChat(agent) {
  try { localStorage.setItem(CHAT_ROUTE_KEY, chatRoute(agent)); } catch { /* приватный режим: откроется «Авто» */ }
  location.href = '/chat.html';
}

function modelRow(m, agents) {
  const r = readiness(m, agents);
  const actions = [];
  if (r.chat) actions.push(btn('Чат', () => openChat(r.chat), { size: 'sm' }));
  if (r.pc) actions.push(btn('Чат + ПК', () => openChat(r.pc), { size: 'sm', variant: 'primary' }));
  return h('div.log-line',
    h('span.log-msg',
      h('b', m.name), ' ',
      h('span.small.dim', [m.parameter_size, m.quantization, humanSize(m.size_bytes)].filter(Boolean).join(' · ')),
      h('div', modelBadges(m).map((b) => pill(b.text, { tone: b.tone }))),
      h('div.small', r.note)),
    h('span', actions));
}

const LocalOperatorPage = {
  id: 'local-operator',
  title: 'Локальные модели',
  icon: 'agents',
  nav: 'primary',
  section: 'brains',

  async render(ctx) {
    let st = null;
    let err = null;
    try { st = await api.raw('/api/local-operator/status'); } catch (e) { err = e; }
    const note = h('div.small');
    const sync = btn('Взять модели из Ollama', async () => {
      note.textContent = 'Подключаю…';
      let out = null;
      try { out = await api.raw('/api/local-operator/sync', { method: 'POST', body: {} }); } catch (e) {
        out = { ok: false, error: String((e && e.message) || e) };   // 503 body.error = why Ollama did not answer
      }
      note.textContent = syncSummary(out);
      if (out && out.ok) ctx.refresh();
    }, { variant: 'primary' });
    const head = pageHead('Локальные модели',
      'Модели, которые стоят у вас в Ollama. Чат с ними и управление компьютером — на этом ПК, данные не уходят.',
      { actions: [sync] });
    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));
    const oll = st.ollama;
    const agents = st.agents || [];
    const harness = agents.find((a) => a.name === HARNESS_NAME);
    const state = oll.ok
      ? panel(`Ollama ${oll.version || ''} · ${oll.models.length} моделей`,
        oll.models.length ? h('div.log', oll.models.map((m) => modelRow(m, agents)))
          : h('div.log-empty', 'В Ollama нет моделей. Установите: ollama pull qwen3:8b'))
      : panel('Ollama не отвечает', h('div.small', h('p', oll.error), h('p', 'Запустите Ollama и обновите страницу.')));
    const harnessPanel = panel('Bossman Harness', h('div.small',
      harness
        ? [h('p', `Агент #${harness.id}: план → действие → проверка → отчёт.`),
          btn('Открыть Harness в чате', () => openChat(harness), { size: 'sm' }),
          h('p', 'Из терминала с независимой проверкой: ',
            h('code', 'python tools/harness_agent.py run "задача" --verify "команда проверки"'))]
        : h('p', 'Ещё не создан: «Взять модели из Ollama» создаст его на самой крупной локальной модели с инструментами.')));
    const how = panel('Как работает управление компьютером', h('div.small',
      h('p', 'Агент «· ПК» смотрит на экран сам, а каждое действие (клик, ввод, запуск) просит ваше одобрение.'),
      h('p', 'Пароли, платежи, отправка и публикация не выполняются вовсе. STOP останавливает всё сразу.')));
    return h('div.bx-page', head, note, state, harnessPanel, how);
  },
};

export default LocalOperatorPage;
