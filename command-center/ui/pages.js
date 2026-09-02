/* ============================================================
   pages.js — страницы Command Center.
   Каждая страница: {id, title, icon, render(ctx) -> Node, onEvent(ev, ctx)}
   Данные — только через api.js (контракт раздела 6 архитектуры).
   ============================================================ */

import { api, listOf, pick, ApiError } from './api.js';
import {
  h, append, clear, replace, icon, field, input, textarea, select, checkbox, toggle,
  dot, badge, statusBadge, statusLabel, statusTone, empty, meter, sparkline,
  toast, toastOk, toastError, openModal, confirmDialog, actionButton, kv, maskSecret,
  fmtClock, fmtDateShort, fmtRelative, fmtElapsed, fmtNum, fmtGb,
  fmtTokens, fmtContext, fmtCost,
} from './components.js';
import * as ui from './pages/_ui.js';

/* ============================================================
   Общие помощники
   ============================================================ */

const PROVIDER_KIND_LABEL = {
  openai_compat: 'OpenAI-совместимый',
  anthropic: 'Anthropic',
};

const TASK_STATUSES = [
  { id: 'running', title: 'Выполняются' },
  { id: 'queued', title: 'В очереди' },
  { id: 'waiting_approval', title: 'Ждут подтверждения' },
  { id: 'paused', title: 'На паузе' },
  { id: 'failed', title: 'Ошибки' },
  { id: 'completed', title: 'Завершены' },
  { id: 'stopped', title: 'Остановлены' },
];

const PRIORITIES = [
  { value: 0, label: 'Высокий приоритет' },
  { value: 5, label: 'Обычный приоритет' },
  { value: 9, label: 'Низкий приоритет' },
];

function kindLabel(kind) {
  return PROVIDER_KIND_LABEL[kind] || kind || '—';
}

/** '' -> null, '12' -> 12, 'abc' -> 'abc' (id может быть и строкой) */
function idVal(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) && String(n) === String(v).trim() ? n : v;
}

function modelLabel(m) {
  return pick(m, ['alias', 'name'], `модель #${pick(m, ['id'], '?')}`);
}

function capsList(caps) {
  if (!caps) return [];
  if (Array.isArray(caps)) return caps.map(String);
  if (typeof caps === 'object') return Object.entries(caps).filter(([, v]) => !!v).map(([k]) => k);
  return [];
}

const CAP_LABEL = { vision: 'vision', tools: 'tools', reasoning: 'reasoning', coding: 'coding' };
const CAP_KEYS = ['vision', 'tools', 'reasoning', 'coding'];

function titleFromPrompt(prompt) {
  const line = String(prompt || '').trim().split('\n')[0].trim();
  if (!line) return 'Задача';
  return line.length > 80 ? `${line.slice(0, 77)}…` : line;
}

function agentSelect(agents, value, attrs = {}) {
  return select(
    [{ value: '', label: agents.length ? '— выберите агента —' : 'агентов ещё нет' },
      ...agents.map((a) => ({ value: pick(a, ['id']), label: pick(a, ['name'], 'без имени') }))],
    { value: value === null || value === undefined ? '' : String(value), ...attrs },
  );
}

function modelSelect(models, value, { allowEmpty = false, emptyLabel = '— не выбрана —' } = {}) {
  return select(
    [...(allowEmpty ? [{ value: '', label: emptyLabel }] : (models.length ? [] : [{ value: '', label: 'моделей ещё нет' }])),
      ...models.map((m) => ({ value: pick(m, ['id']), label: modelLabel(m) }))],
    { value: value === null || value === undefined ? '' : String(value) },
  );
}

/** Секция панели с заголовком. */
function panel(title, bodyNode, { actions, flush = false, tight = false } = {}) {
  return h('section.panel',
    title ? h('div.panel-head',
      h('h2', title),
      h('div.spacer'),
      actions || null) : null,
    h('div', { class: 'panel-body' + (flush ? ' flush' : tight ? ' tight' : '') }, bodyNode));
}

/* ============================================================
   Модалка: новая задача (используется на Home, Tasks и в палитре)
   ============================================================ */

export async function openTaskModal(ctx, { prompt = '', agentId = null } = {}) {
  let agents = ctx.state.agents;
  if (!agents || !agents.length) {
    try { agents = listOf(await api.agents(), 'agents'); ctx.state.agents = agents; } catch (e) { agents = []; }
  }

  const promptEl = textarea({ rows: 5, placeholder: 'Что должен сделать BOSSMAN?', value: prompt });
  const agentEl = agentSelect(agents, agentId);
  const prioEl = select(PRIORITIES, { value: '5' });
  const retriesEl = input({ type: 'number', min: '0', max: '10', value: '2', class: 'input mono' });

  const modal = openModal({
    title: 'Новая задача',
    body: h('div.stack',
      field('Задача', promptEl),
      h('div.grid.cols-2',
        field('Агент', agentEl),
        field('Приоритет', prioEl)),
      field('Повторов при ошибке', retriesEl, 'Сколько раз воркер попробует ещё раз.')),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Запустить', async () => {
        const text = promptEl.value.trim();
        if (!text) { toast('Опишите задачу', { type: 'warn' }); promptEl.focus(); return; }
        if (!agentEl.value) { toast('Выберите агента', { type: 'warn', hint: 'Агент задаёт модель и system prompt.' }); return; }
        try {
          await api.createTask({
            title: titleFromPrompt(text),
            prompt: text,
            agent_id: idVal(agentEl.value),
            priority: Number(prioEl.value) || 0,
            max_retries: Number(retriesEl.value) || 0,
            run_now: true,
          });
          handle.close();
          toastOk('Задача поставлена в очередь');
          ctx.navigate('tasks');
        } catch (e) { toastError(e, 'Не удалось создать задачу'); }
      }, { cls: 'btn btn-primary', iconName: 'play' }),
    ],
  });
  return modal;
}

/* ============================================================
   HOME
   ============================================================ */

const HomePage = {
  id: 'home',
  title: 'Главная',
  icon: 'home',

  async render(ctx) {
    const [systemR, modelsR, agentsR, tasksR, approvalsR, activityR] = await Promise.allSettled([
      api.system(), api.models(), api.agents(), api.tasks(), api.approvals('pending'), api.activity(),
    ]);

    const sys = systemR.status === 'fulfilled' ? normalizeSystem(systemR.value) : null;
    const models = modelsR.status === 'fulfilled' ? listOf(modelsR.value, 'models') : [];
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    const tasks = tasksR.status === 'fulfilled' ? listOf(tasksR.value, 'tasks') : [];
    const approvals = approvalsR.status === 'fulfilled' ? listOf(approvalsR.value, 'approvals') : [];
    const activity = activityR.status === 'fulfilled' ? listOf(activityR.value, 'events', 'activity') : [];

    ctx.state.models = models;
    ctx.state.agents = agents;
    ctx.setBadge('approvals', approvals.length);

    const firstError = [systemR, modelsR, agentsR, tasksR].find((r) => r.status === 'rejected');
    const running = tasks.filter((t) => String(t.status) === 'running');
    const queued = tasks.filter((t) => String(t.status) === 'queued');
    const onlineModels = models.filter((m) => String(m.status) === 'online').length;

    /* --- статус-строка --- */
    const healthTone = sys ? sys.overall : 'idle';
    const statusline = h('div.statusline',
      h('div.sl-item', h('span.sl-num', String(agents.length)), h('span.sl-label', 'агентов')),
      h('div.sep'),
      h('div.sl-item', h('span.sl-num', String(models.length)), h('span.sl-label',
        models.length ? `моделей · ${onlineModels} online` : 'моделей')),
      h('div.sep'),
      h('div.sl-item', dot(healthTone === 'ok' ? 'online' : healthTone === 'warn' ? 'warning' : healthTone === 'err' ? 'error' : 'unknown'),
        h('span.sl-label', sys
          ? (healthTone === 'ok' ? 'система в норме' : healthTone === 'warn' ? 'есть предупреждения' : 'сбой компонентов')
          : 'метрики недоступны')),
      h('div.spacer'),
      h('div.sl-item.small.dim', running.length
        ? `${running.length} в работе · ${queued.length} в очереди`
        : queued.length ? `${queued.length} в очереди` : 'очередь пуста'));

    /* --- Quick Task --- */
    const qtText = textarea({ rows: 3, placeholder: 'Что должен сделать BOSSMAN?', class: 'textarea composer-input' });
    const qtAgent = agentSelect(agents, agents.length === 1 ? pick(agents[0], ['id']) : null);
    const quick = h('section.panel',
      h('div.panel-head', h('h2', 'Быстрая задача'), h('div.spacer'),
        h('span.xsmall.dim', 'Ctrl/⌘ + Enter — запустить')),
      h('div.composer',
        qtText,
        h('div.composer-controls',
          field('Агент', qtAgent),
          h('div.spacer'),
          actionButton('Запустить', () => submitQuick(), { cls: 'btn btn-primary', iconName: 'play' }))));

    async function submitQuick() {
      const text = qtText.value.trim();
      if (!text) { toast('Опишите задачу', { type: 'warn' }); qtText.focus(); return; }
      if (!qtAgent.value) {
        toast('Выберите агента', { type: 'warn', hint: agents.length ? 'Агент задаёт модель и system prompt.' : 'Сначала создайте агента на странице «Агенты».' });
        return;
      }
      try {
        await api.createTask({
          title: titleFromPrompt(text),
          prompt: text,
          agent_id: idVal(qtAgent.value),
          priority: 5,
          run_now: true,
        });
        qtText.value = '';
        toastOk('Задача поставлена в очередь');
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось создать задачу'); }
    }
    qtText.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submitQuick(); }
    });

    /* --- карточки --- */
    const runningCard = h('section.panel',
      h('div.panel-head', h('h2', 'Активные задачи'), h('div.spacer'),
        h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('tasks') }, 'Все задачи')),
      h('div.panel-body',
        h('div.big-num' + (running.length ? '.accent' : ''), String(running.length)),
        h('div.small.dim', { style: { marginTop: '2px' } },
          queued.length ? `и ещё ${queued.length} в очереди` : 'в очереди пусто')),
      running.length
        ? h('div.mini-list', running.slice(0, 4).map((t) => h('div.mini-row.clickable', {
          onClick: () => ctx.navigate('tasks', { task: pick(t, ['id']) }),
        },
        dot('running', { live: true }),
        h('span.name', pick(t, ['title'], titleFromPrompt(t.prompt))),
        h('span.xsmall.dim.num', fmtElapsed(pick(t, ['started_at', 'updated_at', 'created_at']))))))
        : null);

    const modelsCard = h('section.panel',
      h('div.panel-head', h('h2', 'Модели'), h('div.spacer'),
        h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('models') }, 'Реестр')),
      models.length
        ? h('div.mini-list', models.slice(0, 6).map((m) => h('div.mini-row.clickable', {
          onClick: () => ctx.navigate('models'),
        },
        dot(m.status),
        h('span.name', modelLabel(m)),
        h('span.badge', String(m.kind) === 'cloud' ? 'облако' : 'локальная'))))
        : h('div.panel-body', h('div.small.dim', 'Ни одной модели. Добавьте провайдера и модель на странице «Модели».')));

    const approvalsCard = h('section.panel',
      h('div.panel-head', h('h2', 'Подтверждения'), h('div.spacer'),
        h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('approvals') }, 'Очередь')),
      h('div.panel-body',
        h('div', { class: 'big-num' + (approvals.length ? ' warn' : '') }, String(approvals.length)),
        h('div.small.dim', { style: { marginTop: '2px' } },
          approvals.length ? 'ждут вашего решения' : 'ничего не ждёт решения')));

    const systemCard = h('section.panel',
      h('div.panel-head', h('h2', 'Система'), h('div.spacer'),
        h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('system') }, 'Метрики')),
      h('div.panel-body', h('div.stack.sm',
        sys
          ? [
            meter('CPU', sys.cpu ?? 0, 100, sys.cpu === null ? '—' : `${Math.round(sys.cpu)}%`),
            meter('RAM', sys.ramUsed ?? 0, sys.ramTotal ?? 0,
              sys.ramTotal ? `${fmtGb(sys.ramUsed)} / ${fmtGb(sys.ramTotal)} ГБ` : '—'),
            sys.gpus.length
              ? meter(`GPU · ${sys.gpus[0].name || 'ускоритель'}`, sys.gpus[0].util ?? 0, 100,
                sys.gpus[0].util === null ? '—' : `${Math.round(sys.gpus[0].util)}%`)
              : h('div.small.dim', 'GPU: недоступно'),
          ]
          : h('div.small.dim', 'Метрики недоступны — сервер не ответил.'))));

    /* --- лента активности --- */
    const activityPanel = h('section.panel',
      h('div.panel-head', icon('activity', 15), h('h2', 'Последние события')),
      activity.length
        ? h('div.feed', activity.slice(0, 14).map(activityRow))
        : h('div.panel-body', h('div.small.dim', 'Событий пока нет. Запустите задачу — лента наполнится.')));

    return h('div.stack.lg',
      firstError ? errorBanner(firstError.reason, ctx) : null,
      statusline,
      quick,
      h('div.grid.cols-4', runningCard, modelsCard, approvalsCard, systemCard),
      activityPanel);
  },

  onEvent(ev) {
    return ev.kind.startsWith('task.') || ev.kind.startsWith('approval.')
      || ev.kind === 'model.status' || ev.kind === 'system.metrics';
  },
};

function activityRow(e) {
  const kind = pick(e, ['kind', 'type'], 'event');
  const data = e.data && typeof e.data === 'object' ? e.data : {};
  const text = pick(e, ['message', 'text', 'title'])
    || pick(data, ['message', 'title', 'prompt'])
    || (Object.keys(data).length ? JSON.stringify(data).slice(0, 160) : '');
  return h('div.feed-item',
    h('span.feed-time', fmtClock(pick(e, ['ts', 'created_at']))),
    h('span.feed-kind', kind),
    h('span.feed-text', text || '—'));
}

function errorBanner(err, ctx) {
  return h('section.panel', { style: { borderColor: 'color-mix(in srgb, var(--err) 40%, transparent)' } },
    h('div.panel-body', h('div.row',
      dot('error'),
      h('div', { style: { flex: '1' } },
        h('div.small', err && err.message ? err.message : 'Часть данных не загрузилась'),
        err && err.hint ? h('div.xsmall.dim', err.hint) : null),
      h('button.btn.btn-sm', { type: 'button', onClick: () => ctx.refresh() }, 'Повторить'))));
}

/* ============================================================
   MODELS
   ============================================================ */

const ModelsPage = {
  id: 'models',
  title: 'Модели',
  icon: 'models',

  async render(ctx) {
    const [modelsR, providersR] = await Promise.allSettled([api.models(), api.providers()]);
    const models = modelsR.status === 'fulfilled' ? listOf(modelsR.value, 'models') : [];
    const providers = providersR.status === 'fulfilled' ? listOf(providersR.value, 'providers') : [];
    ctx.state.models = models;
    ctx.state.providers = providers;

    const byId = new Map(providers.map((p) => [String(pick(p, ['id'])), p]));

    const head = h('div.row',
      h('div',
        h('div.section-title', { style: { margin: 0 } }, 'Список моделей'),
        h('div.small.dim', `${models.length} моделей · ${providers.length} поставщиков`)),
      h('div.spacer'),
      actionButton('Проверить все', async () => {
        if (!models.length) return;
        await Promise.allSettled(models.map((m) => api.checkModel(pick(m, ['id']))));
        toastOk('Проверка запущена');
        ctx.refresh();
      }, { cls: 'btn', iconName: 'retry' }),
      actionButton('Найти локальные', () => openDiscoveryModal(ctx), { cls: 'btn', iconName: 'search' }),
      h('button.btn.btn-primary', { type: 'button', onClick: () => openModelWizard(ctx) },
        icon('plus', 14), h('span', 'Добавить модель')));

    const body = modelsR.status === 'rejected'
      ? errorBanner(modelsR.reason, ctx)
      : models.length
        ? h('div.grid.auto-lg', models.map((m) => modelCard(m, byId.get(String(m.provider_id)), ctx)))
        : h('section.panel', empty({
          iconName: 'models',
          title: 'Моделей пока нет',
          hint: 'Подключите модель на своём компьютере (llama.cpp, Ollama, LM Studio) или облачную — и она появится здесь со своим состоянием.',
          action: h('button.btn.btn-primary', { type: 'button', onClick: () => openModelWizard(ctx) },
            icon('plus', 14), h('span', 'Добавить модель')),
        }));

    return h('div.stack.lg', head, body);
  },

  onEvent(ev) { return ev.kind === 'model.status'; },
};

function modelCard(m, provider, ctx) {
  const id = pick(m, ['id']);
  const caps = capsList(m.caps);
  const bench = m.bench && typeof m.bench === 'object' ? m.bench : null;
  const statusDetail = pick(m, ['status_detail']);

  const testOut = h('div');

  return h('div.card',
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', modelLabel(m)),
        h('div.card-sub',
          `${pick(m, ['name'], '—')} · ${provider ? pick(provider, ['name'], kindLabel(provider.kind)) : 'поставщик не найден'}`)),
      statusBadge(m.status || 'unknown')),

    h('div.row.tight',
      badge(String(m.kind) === 'cloud' ? 'облако' : 'локальная', String(m.kind) === 'cloud' ? 'info' : 'accent'),
      m.context_window ? badge(`контекст ${fmtContext(m.context_window)}`) : null,
      ...caps.map((c) => badge(CAP_LABEL[c] || c))),

    statusDetail ? h('div.xsmall.dim.wrap-any', statusDetail) : null,

    (bench || m.last_check) ? h('div.stat-strip',
      bench && bench.gen_tps ? h('div', { title: 'скорость ответа, слов в секунду' }, h('span.s-label', 'Ответ'), h('span.s-value', `${fmtNum(bench.gen_tps, 1)} сл/с`)) : null,
      bench && bench.prompt_tps ? h('div', { title: 'скорость чтения запроса' }, h('span.s-label', 'Чтение'), h('span.s-value', `${fmtNum(bench.prompt_tps, 1)} сл/с`)) : null,
      bench && bench.latency_ms ? h('div', h('span.s-label', 'Задержка'), h('span.s-value', `${fmtNum(bench.latency_ms)} мс`)) : null,
      m.last_check ? h('div', h('span.s-label', 'Проверка'), h('span.s-value', fmtRelative(m.last_check))) : null,
    ) : null,

    testOut,

    h('div.card-actions',
      actionButton('Проверить', async () => {
        try {
          const r = await api.checkModel(id);
          const st = pick(r || {}, ['status'], 'unknown');
          toast(`${modelLabel(m)}: ${statusLabel(st)}`, {
            type: statusTone(st) === 'ok' ? 'ok' : statusTone(st) === 'err' ? 'err' : 'info',
            hint: pick(r || {}, ['status_detail', 'detail', 'message'], ''),
          });
          ctx.refresh();
        } catch (e) { toastError(e, 'Проверка не удалась'); }
      }, { cls: 'btn btn-sm', iconName: 'retry', title: 'Проверить, на связи ли модель' }),

      actionButton('Проба', async () => {
        replace(testOut, h('div.small.dim', 'Пробуем короткий запрос…'));
        try {
          const r = await api.testModel(id) || {};
          const b = (r.bench && typeof r.bench === 'object') ? r.bench : r;
          replace(testOut, h('div.stack.sm',
            h('div.stat-strip',
              h('div', { title: 'скорость ответа, слов в секунду' }, h('span.s-label', 'Ответ'), h('span.s-value', b.gen_tps ? `${fmtNum(b.gen_tps, 1)} сл/с` : '—')),
              h('div', { title: 'скорость чтения запроса' }, h('span.s-label', 'Чтение'), h('span.s-value', b.prompt_tps ? `${fmtNum(b.prompt_tps, 1)} сл/с` : '—')),
              h('div', h('span.s-label', 'Задержка'), h('span.s-value', b.latency_ms ? `${fmtNum(b.latency_ms)} мс` : '—'))),
            pick(r, ['output', 'text', 'sample', 'result'])
              ? h('pre.block', String(pick(r, ['output', 'text', 'sample', 'result'])).slice(0, 600))
              : null));
          toastOk(`${modelLabel(m)}: тест пройден`);
        } catch (e) {
          replace(testOut, h('div.small', { style: { color: 'var(--err)' } }, e.message || 'тест не прошёл'));
          toastError(e, 'Тест модели не прошёл');
        }
      }, { cls: 'btn btn-sm', iconName: 'bolt', title: 'Быстрая проба: короткий запрос, скорость и задержка' }),

      h('button.btn.btn-sm', { type: 'button', onClick: () => openModelEdit(ctx, m) }, icon('edit', 13), h('span', 'Изменить')),

      h('button.btn.btn-sm.btn-danger', {
        type: 'button',
        onClick: async () => {
          const ok = await confirmDialog({
            title: 'Удалить модель?',
            text: `${modelLabel(m)} будет убрана из реестра. Агенты, использующие её, останутся без модели.`,
            okText: 'Удалить', danger: true,
          });
          if (!ok) return;
          try { await api.deleteModel(id); toastOk('Модель удалена'); ctx.refresh(); }
          catch (e) { toastError(e, 'Не удалось удалить модель'); }
        },
      }, icon('trash', 13), h('span', 'Удалить'))));
}

/* --- мастер добавления модели: провайдер → модель --- */

/* Обнаружение локальных моделей: опрос известных портов + gguf-файлы на диске. */
async function openDiscoveryModal(ctx) {
  const modal = openModal({ title: 'Локальные модели', wide: true, body: h('div'), footer: h('div') });
  append(modal.footer, [h('div.spacer'),
    h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть')]);
  append(modal.body, h('div.small.dim', 'Опрашиваю известные порты (llama.cpp, Ollama, LM Studio, vLLM…) и сканирую диск…'));

  let result;
  try { result = await api.discoverModels(); }
  catch (err) {
    clear(modal.body);
    append(modal.body, h('div.small', (err && err.message) || 'Не удалось выполнить поиск'));
    return;
  }
  const endpoints = listOf(result.endpoints, 'endpoints');
  const files = listOf(result.files, 'files');
  clear(modal.body);

  const addModel = async (ep, name) => {
    try {
      let providerId = null;
      const providers = listOf(await api.providers(), 'providers');
      const existing = providers.find((p) => (p.base_url || '').replace(/\/+$/, '') === ep.base_url.replace(/\/+$/, ''));
      if (existing) providerId = pick(existing, ['id']);
      else {
        const created = await api.createProvider({ name: ep.label, kind: 'openai_compat', base_url: ep.base_url });
        providerId = pick(created, ['id']) ?? pick(created.provider || {}, ['id']);
      }
      const model = await api.createModel({
        provider_id: providerId, name, alias: name, kind: 'local',
        context_window: 8192, caps: { tools: true, coding: true },
      });
      const mid = pick(model, ['id']) ?? pick(model.model || {}, ['id']);
      if (mid != null) await api.checkModel(mid).catch(() => {});
      toastOk(`Модель «${name}» добавлена`);
      ctx.refresh();
    } catch (err) {
      toast((err && err.message) || 'Не удалось добавить', { type: 'warn', hint: err && err.hint });
    }
  };

  const epRows = endpoints.map((ep) => h('div.card', { style: { padding: '10px 12px' } },
    h('div.row',
      dot(ep.ok ? 'online' : 'offline'),
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div', ep.label, ' ', h('span.small.dim.mono', ep.base_url)),
        ep.ok
          ? h('div.small.dim', `отвечает за ${ep.latency_ms} мс · моделей: ${ep.models.length}`)
          : h('div.small.dim', ep.detail || 'не отвечает')),
      ep.registered ? h('span.badge', 'уже подключён') : null),
    ep.ok && ep.models.length ? h('div.stack', { style: { marginTop: '8px' } },
      ep.models.map((name) => h('div.row',
        h('span.mono.small', name), h('div.spacer'),
        h('button.btn.btn-sm', { type: 'button', onClick: () => addModel(ep, name) },
          icon('plus', 12), h('span', 'Добавить'))))) : null));

  const fileRows = files.length
    ? h('div.stack', files.slice(0, 30).map((f) => h('div.row.small',
        h('span.mono', f.path), h('div.spacer'), h('span.dim', `${f.size_gb} ГБ`))))
    : h('div.small.dim', `gguf-файлов не найдено (искал в: ${(result.scanned_dirs || []).join(', ')})`);

  append(modal.body, h('div.stack',
    h('div.section-title', { style: { margin: 0 } }, `Запущенные endpoint'ы · онлайн: ${result.online}`),
    h('div.stack', epRows),
    h('div.section-title', { style: { margin: '8px 0 0' } }, 'Файлы моделей на диске'),
    fileRows));
}

async function openModelWizard(ctx) {
  let kinds = ['openai_compat', 'anthropic'];
  let providers = [];
  try { kinds = listOf(await api.providerKinds(), 'kinds') || kinds; } catch { /* дефолт */ }
  if (!kinds.length) kinds = ['openai_compat', 'anthropic'];
  try { providers = listOf(await api.providers(), 'providers'); } catch { providers = []; }

  const draft = {
    mode: providers.length ? 'existing' : 'new',
    provider_id: providers.length ? pick(providers[0], ['id']) : null,
    p_name: '', p_kind: kinds[0], p_base_url: '', p_api_key: '',
    m_name: '', m_alias: '', m_kind: 'local', m_context: 8192,
    caps: { vision: false, tools: true, reasoning: false, coding: true },
  };

  const modal = openModal({ title: 'Новая модель', wide: true, body: h('div'), footer: h('div') });
  renderStep(1);

  function stepsBar(step) {
    return h('div.steps',
      h('div', { class: 'step' + (step === 1 ? ' on' : '') }, h('i', '1'), h('span', 'Провайдер')),
      icon('chevron', 12),
      h('div', { class: 'step' + (step === 2 ? ' on' : '') }, h('i', '2'), h('span', 'Модель')));
  }

  function renderStep(step) {
    clear(modal.body); clear(modal.footer);
    if (step === 1) renderProviderStep(); else renderModelStep();
  }

  function renderProviderStep() {
    const seg = h('div.seg',
      h('button', {
        type: 'button', class: draft.mode === 'existing' ? 'on' : '',
        disabled: !providers.length,
        onClick: () => { draft.mode = 'existing'; renderStep(1); },
      }, 'Существующий'),
      h('button', {
        type: 'button', class: draft.mode === 'new' ? 'on' : '',
        onClick: () => { draft.mode = 'new'; renderStep(1); },
      }, 'Новый провайдер'));

    let form;
    if (draft.mode === 'existing') {
      const sel = select(providers.map((p) => ({
        value: pick(p, ['id']),
        label: `${pick(p, ['name'], 'провайдер')} · ${kindLabel(p.kind)}${p.base_url ? ` · ${p.base_url}` : ''}`,
      })), { value: draft.provider_id === null ? '' : String(draft.provider_id) });
      sel.addEventListener('change', () => { draft.provider_id = idVal(sel.value); });
      form = field('Провайдер', sel);
    } else {
      const nameEl = input({ placeholder: 'Local llama.cpp', value: draft.p_name });
      const kindEl = select(kinds.map((k) => ({ value: k, label: kindLabel(k) })), { value: draft.p_kind });
      const urlEl = input({ placeholder: 'http://127.0.0.1:8080/v1', value: draft.p_base_url, class: 'input mono' });
      const keyEl = input({ type: 'password', placeholder: 'sk-… (если нужен)', value: draft.p_api_key, class: 'input mono', autocomplete: 'new-password' });
      nameEl.addEventListener('input', () => { draft.p_name = nameEl.value; });
      kindEl.addEventListener('change', () => { draft.p_kind = kindEl.value; });
      urlEl.addEventListener('input', () => { draft.p_base_url = urlEl.value; });
      keyEl.addEventListener('input', () => { draft.p_api_key = keyEl.value; });
      form = h('div.stack',
        h('div.grid.cols-2', field('Название', nameEl), field('Тип', kindEl)),
        field('Base URL', urlEl, 'Для Ollama обычно http://127.0.0.1:11434/v1, для llama.cpp — http://127.0.0.1:8080/v1.'),
        field('API-ключ', keyEl, 'Хранится зашифрованным; в интерфейсе показывается только маска.'));
    }

    append(modal.body, h('div.stack', stepsBar(1), seg, form));
    append(modal.footer, [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'),
      h('button.btn.btn-primary', {
        type: 'button',
        onClick: () => {
          if (draft.mode === 'existing' && (draft.provider_id === null || draft.provider_id === '')) {
            toast('Выберите провайдера', { type: 'warn' }); return;
          }
          if (draft.mode === 'new') {
            if (!draft.p_name.trim()) { toast('Укажите название провайдера', { type: 'warn' }); return; }
            if (draft.p_kind === 'openai_compat' && !draft.p_base_url.trim()) {
              toast('Укажите Base URL', { type: 'warn', hint: 'OpenAI-совместимому провайдеру нужен адрес endpoint.' }); return;
            }
          }
          renderStep(2);
        },
      }, h('span', 'Далее'), icon('chevron', 14)),
    ]);
  }

  function renderModelStep() {
    const nameEl = input({ placeholder: 'qwen2.5-coder:14b', value: draft.m_name, class: 'input mono' });
    const aliasEl = input({ placeholder: 'qwen-coder', value: draft.m_alias, class: 'input mono' });
    const ctxEl = input({ type: 'number', min: '1024', step: '1024', value: String(draft.m_context), class: 'input mono' });
    const kindSeg = h('div.seg',
      h('button', { type: 'button', class: draft.m_kind === 'local' ? 'on' : '', onClick: () => { draft.m_kind = 'local'; syncKind(); } }, 'Локальная'),
      h('button', { type: 'button', class: draft.m_kind === 'cloud' ? 'on' : '', onClick: () => { draft.m_kind = 'cloud'; syncKind(); } }, 'Облачная'));
    function syncKind() {
      Array.from(kindSeg.children).forEach((b, i) => b.classList.toggle('on', (i === 0) === (draft.m_kind === 'local')));
    }

    nameEl.addEventListener('input', () => {
      draft.m_name = nameEl.value;
      if (!aliasEl.dataset.touched) { aliasEl.value = draft.m_name.replace(/[:\/\s]+/g, '-').toLowerCase(); draft.m_alias = aliasEl.value; }
    });
    aliasEl.addEventListener('input', () => { aliasEl.dataset.touched = '1'; draft.m_alias = aliasEl.value; });
    ctxEl.addEventListener('input', () => { draft.m_context = Number(ctxEl.value) || 0; });

    const capsRow = h('div.row', CAP_KEYS.map((c) => checkbox(CAP_LABEL[c], draft.caps[c], {
      onChange: (e) => { draft.caps[c] = e.target.checked; },
    })));

    append(modal.body, h('div.stack',
      stepsBar(2),
      h('div.grid.cols-2',
        field('Имя у провайдера', nameEl, 'Точно как модель называется в endpoint.'),
        field('Alias', aliasEl, 'Короткое уникальное имя в реестре.')),
      h('div.grid.cols-2',
        field('Тип', kindSeg),
        field('Окно контекста (токенов)', ctxEl)),
      field('Возможности', capsRow)));

    append(modal.footer, [
      h('button.btn', { type: 'button', onClick: () => renderStep(1) }, 'Назад'),
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'),
      actionButton('Сохранить', save, { cls: 'btn btn-primary', iconName: 'check' }),
    ]);
  }

  async function save() {
    if (!draft.m_name.trim()) { toast('Укажите имя модели', { type: 'warn' }); return; }
    if (!draft.m_alias.trim()) { toast('Укажите alias', { type: 'warn' }); return; }
    try {
      let providerId = draft.provider_id;
      if (draft.mode === 'new') {
        const payload = { name: draft.p_name.trim(), kind: draft.p_kind };
        if (draft.p_base_url.trim()) payload.base_url = draft.p_base_url.trim();
        if (draft.p_api_key) payload.api_key = draft.p_api_key;
        const created = await api.createProvider(payload);
        providerId = pick(created && (created.provider || created) || {}, ['id']);
        if (providerId === undefined || providerId === null) throw new ApiError('Провайдер создан, но сервер не вернул id', { hint: 'Обновите страницу «Модели» и добавьте модель к существующему провайдеру.' });
      }
      await api.createModel({
        provider_id: providerId,
        name: draft.m_name.trim(),
        alias: draft.m_alias.trim(),
        kind: draft.m_kind,
        context_window: draft.m_context || null,
        caps: { ...draft.caps },
      });
      modal.close();
      toastOk('Модель добавлена', 'Нажмите Check, чтобы проверить доступность.');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось сохранить модель'); }
  }
}

function openModelEdit(ctx, m) {
  const caps = { vision: false, tools: false, reasoning: false, coding: false };
  for (const c of capsList(m.caps)) if (c in caps) caps[c] = true;

  const nameEl = input({ value: pick(m, ['name'], ''), class: 'input mono' });
  const aliasEl = input({ value: pick(m, ['alias'], ''), class: 'input mono' });
  const ctxEl = input({ type: 'number', min: '1024', step: '1024', value: String(pick(m, ['context_window'], '') || ''), class: 'input mono' });
  const kindEl = select([{ value: 'local', label: 'Локальная' }, { value: 'cloud', label: 'Облачная' }],
    { value: String(pick(m, ['kind'], 'local')) });
  const capsRow = h('div.row', CAP_KEYS.map((c) => checkbox(CAP_LABEL[c], caps[c], {
    onChange: (e) => { caps[c] = e.target.checked; },
  })));

  const modal = openModal({
    title: `Модель · ${modelLabel(m)}`,
    body: h('div.stack',
      h('div.grid.cols-2', field('Имя у провайдера', nameEl), field('Alias', aliasEl)),
      h('div.grid.cols-2', field('Тип', kindEl), field('Окно контекста', ctxEl)),
      field('Возможности', capsRow)),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Сохранить', async () => {
        try {
          await api.updateModel(pick(m, ['id']), {
            name: nameEl.value.trim(),
            alias: aliasEl.value.trim(),
            kind: kindEl.value,
            context_window: Number(ctxEl.value) || null,
            caps: { ...caps },
          });
          handle.close();
          toastOk('Модель обновлена');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось обновить модель'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
  return modal;
}

/* ============================================================
   AGENTS
   ============================================================ */

const AgentsPage = {
  id: 'agents',
  title: 'Агенты',
  icon: 'agents',

  async render(ctx) {
    const [agentsR, modelsR] = await Promise.allSettled([api.agents(), api.models()]);
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    const models = modelsR.status === 'fulfilled' ? listOf(modelsR.value, 'models') : [];
    ctx.state.agents = agents;
    ctx.state.models = models;

    const modelById = new Map(models.map((m) => [String(pick(m, ['id'])), m]));
    const on = agents.filter((a) => a.enabled !== false).length;

    const head = ui.pageHead('Агенты',
      agents.length
        ? `${ui.plural(agents.length, 'агент', 'агента', 'агентов')} · ${ui.plural(on, 'готов', 'готовы', 'готовы')} к работе`
        : 'Агент — это помощник с ролью и характером: чем он занимается, каким тоном отвечает и на какой модели работает.',
      { actions: [ui.btn('Новый агент', () => openAgentModal(ctx, null, models), { variant: 'primary', iconName: 'plus' })] });

    const body = agentsR.status === 'rejected'
      ? ui.errorNote(agentsR.reason, () => ctx.refresh())
      : agents.length
        ? h('div.bx-cards', agents.map((a) => agentCard(a, modelById, models, ctx)))
        : ui.blank({
          iconName: 'agents',
          title: 'Агентов пока нет',
          hint: 'Агент — это помощник с ролью, характером и моделью, на которой он думает. Создайте первого, чтобы поручать ему задачи.',
          action: ui.btn('Создать агента', () => openAgentModal(ctx, null, models), { variant: 'primary', iconName: 'plus' }),
        });

    return h('div.bx-page', head, body);
  },

  onEvent(ev) { return ev.kind.startsWith('agent.'); },
};

function agentCard(a, modelById, models, ctx) {
  const id = pick(a, ['id']);
  const primary = modelById.get(String(pick(a, ['model_id'])));
  const fallback = modelById.get(String(pick(a, ['fallback_model_id'])));
  const enabled = a.enabled !== false;

  const tags = [
    primary ? ui.tag(modelLabel(primary), { accent: true }) : ui.tag('модель не выбрана'),
    fallback ? ui.tag(`запасная: ${modelLabel(fallback)}`) : null,
  ].filter(Boolean);

  const promptText = a.system_prompt
    ? String(a.system_prompt).slice(0, 180) + (String(a.system_prompt).length > 180 ? '…' : '')
    : null;

  const facts = [];
  if (a.budget_usd) facts.push(ui.stat('Лимит трат', fmtCost(a.budget_usd)));
  const spent = a.spend_usd ?? a.cost_usd;
  if (spent !== undefined && spent !== null) facts.push(ui.stat('Потрачено', fmtCost(spent)));
  if (a.max_steps) facts.push(ui.stat('Шагов на задачу', a.max_steps));

  return ui.tile({
    accent: 'var(--bx-azure)',
    iconName: 'agents',
    muted: !enabled,
    title: pick(a, ['name'], 'без имени'),
    sub: pick(a, ['role'], 'чем занимается — не указано'),
    statusNode: ui.pill(enabled ? 'готов к работе' : 'выключен',
      { tone: enabled ? 'ok' : 'idle', live: false }),
    tags,
    body: [
      promptText ? h('p.bx-tile-text', promptText) : null,
      facts.length ? h('div.bx-stats', facts) : null,
    ],
    actions: [
      ui.btn('Поручить задачу', () => openTaskModal(ctx, { agentId: id }), { variant: 'primary', size: 'sm', iconName: 'play' }),
      ui.btn('Изменить', () => openAgentModal(ctx, a, models), { variant: 'secondary', size: 'sm', iconName: 'edit' }),
      ui.btn('Удалить', async () => {
        const ok = await confirmDialog({
          title: 'Удалить агента?',
          text: `«${pick(a, ['name'], 'агент')}» будет удалён. История его задач останется.`,
          okText: 'Удалить', danger: true,
        });
        if (!ok) return;
        try { await api.deleteAgent(id); toastOk('Агент удалён'); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось удалить агента'); }
      }, { variant: 'subtle', size: 'sm', iconName: 'trash' }),
    ],
  });
}

export async function openAgentModal(ctx, agent = null, models = null) {
  let list = models || ctx.state.models;
  if (!list || !list.length) {
    try { list = listOf(await api.models(), 'models'); ctx.state.models = list; } catch { list = []; }
  }
  const editing = !!agent;

  const nameEl = input({ placeholder: 'Например: Исследователь', value: pick(agent || {}, ['name'], '') });
  const roleEl = input({ placeholder: 'Ищет и сверяет факты', value: pick(agent || {}, ['role'], '') });
  const promptEl = textarea({ rows: 7, placeholder: 'Ты — аккуратный исследователь. Отвечай кратко, ссылайся на источники…', value: pick(agent || {}, ['system_prompt'], '') });
  const modelEl = modelSelect(list, pick(agent || {}, ['model_id'], null));
  const fbEl = modelSelect(list, pick(agent || {}, ['fallback_model_id'], null), { allowEmpty: true, emptyLabel: '— без запасной —' });
  const stepsEl = input({ type: 'number', min: '1', max: '100', value: String(pick(agent || {}, ['max_steps'], 8)), class: 'input mono' });
  const retriesEl = input({ type: 'number', min: '0', max: '10', value: String(pick(agent || {}, ['max_retries'], 2)), class: 'input mono' });
  const enabledEl = h('input', { type: 'checkbox', checked: agent ? agent.enabled !== false : true });

  const modal = openModal({
    title: editing ? `Агент · ${pick(agent, ['name'], '')}` : 'Новый агент',
    wide: true,
    body: h('div.stack',
      h('div.grid.cols-2',
        field('Имя', nameEl),
        field('Чем занимается', roleEl, 'Короткое описание зоны ответственности.')),
      field('Характер и правила', promptEl, 'Постоянная инструкция агенту: как себя вести, что можно, чего нельзя.'),
      h('div.grid.cols-2',
        field('Основная модель', modelEl),
        field('Запасная модель', fbEl, 'Подключится, если основная недоступна.')),
      h('div.grid.cols-3',
        field('Шагов на задачу', stepsEl, 'Сколько действий подряд может сделать.'),
        field('Повторов при ошибке', retriesEl),
        field('Состояние', h('label.check', enabledEl, h('span', 'включён')))),
    ),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton(editing ? 'Сохранить' : 'Создать', async () => {
        if (!nameEl.value.trim()) { toast('Укажите имя агента', { type: 'warn' }); nameEl.focus(); return; }
        if (!modelEl.value) { toast('Выберите основную модель', { type: 'warn', hint: list.length ? '' : 'Сначала добавьте модель на странице «Модели».' }); return; }
        const payload = {
          name: nameEl.value.trim(),
          role: roleEl.value.trim(),
          system_prompt: promptEl.value,
          model_id: idVal(modelEl.value),
          fallback_model_id: idVal(fbEl.value),
          max_steps: Number(stepsEl.value) || null,
          max_retries: Number(retriesEl.value) || 0,
          enabled: enabledEl.checked,
        };
        try {
          if (editing) await api.updateAgent(pick(agent, ['id']), payload);
          else await api.createAgent(payload);
          handle.close();
          toastOk(editing ? 'Агент обновлён' : 'Агент создан');
          ctx.refresh();
        } catch (e) { toastError(e, editing ? 'Не удалось обновить агента' : 'Не удалось создать агента'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
  return modal;
}

/* ============================================================
   TASKS
   ============================================================ */

const taskState = {
  filter: 'all',
  openId: null,
  detail: null,       // {task, runs, run}
  logEl: null,
  logRunId: null,
  logLastId: 0,
  logEmpty: null,
};

const TasksPage = {
  id: 'tasks',
  title: 'Задачи',
  icon: 'tasks',

  async enter(_ctx, params) {
    if (params && params.task !== undefined && params.task !== null && params.task !== '') {
      taskState.openId = params.task;
      taskState.filter = 'all';
      delete params.task;   // применяем deep-link один раз, дальше — состояние страницы
    }
  },

  async render(ctx) {
    const [tasksR, agentsR] = await Promise.allSettled([
      taskState.filter === 'all' ? api.tasks() : api.tasks(taskState.filter),
      api.agents(),
    ]);
    const tasks = tasksR.status === 'fulfilled' ? listOf(tasksR.value, 'tasks') : [];
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    ctx.state.agents = agents;
    ctx.state.tasks = tasks;

    const agentById = new Map(agents.map((a) => [String(pick(a, ['id'])), a]));

    /* --- композер --- */
    const promptEl = textarea({ rows: 3, class: 'textarea composer-input', placeholder: 'Что должен сделать BOSSMAN?' });
    const agentEl = agentSelect(agents, agents.length === 1 ? pick(agents[0], ['id']) : null);
    const prioEl = select(PRIORITIES, { value: '5' });

    async function start(runNow) {
      const text = promptEl.value.trim();
      if (!text) { toast('Опишите задачу', { type: 'warn' }); promptEl.focus(); return; }
      if (!agentEl.value) {
        toast('Выберите агента', { type: 'warn', hint: agents.length ? '' : 'Сначала создайте агента на странице «Агенты».' });
        return;
      }
      if (!runNow) {
        openScheduleModal(ctx, agents, { prompt: text, agentId: idVal(agentEl.value) });
        return;
      }
      try {
        await api.createTask({
          title: titleFromPrompt(text),
          prompt: text,
          agent_id: idVal(agentEl.value),
          priority: Number(prioEl.value) || 0,
          run_now: true,
        });
        promptEl.value = '';
        toastOk('Задача поставлена в очередь');
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось создать задачу'); }
    }
    promptEl.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); start(true); }
    });

    const composer = h('section.panel',
      h('div.panel-head', h('h2', 'Task Composer'), h('div.spacer'),
        h('span.xsmall.dim', 'Ctrl/⌘ + Enter — запустить сейчас')),
      h('div.composer',
        promptEl,
        h('div.composer-controls',
          field('Агент', agentEl),
          field('Приоритет', prioEl),
          h('div.spacer'),
          h('button.btn', { type: 'button', onClick: () => start(false) }, icon('schedules', 14), h('span', 'По расписанию…')),
          actionButton('Запустить', () => start(true), { cls: 'btn btn-primary', iconName: 'play' }))));

    /* --- фильтры --- */
    const counts = { all: tasks.length };
    for (const s of TASK_STATUSES) counts[s.id] = tasks.filter((t) => String(t.status) === s.id).length;

    const chips = h('div.filters',
      chip('Все', 'all', counts.all),
      ...TASK_STATUSES.map((s) => chip(s.title, s.id, counts[s.id])));

    function chip(label, id, count) {
      return h('button', {
        type: 'button',
        class: 'filter-chip' + (taskState.filter === id ? ' on' : ''),
        onClick: () => { taskState.filter = id; ctx.refresh(); },
      }, id !== 'all' ? dot(id === 'queued' ? 'queued' : id) : null,
      h('span', label),
      h('span.cnt', String(count === undefined ? 0 : count)));
    }

    /* --- доска --- */
    let boardNode;
    if (tasksR.status === 'rejected') {
      boardNode = errorBanner(tasksR.reason, ctx);
    } else if (!tasks.length) {
      boardNode = h('section.panel', empty({
        iconName: 'tasks',
        title: taskState.filter === 'all' ? 'Задач ещё нет' : 'В этом статусе пусто',
        hint: taskState.filter === 'all'
          ? 'Опишите задачу в композере выше, выберите агента и нажмите «Запустить».'
          : 'Смените фильтр, чтобы увидеть остальные задачи.',
      }));
    } else {
      const groups = taskState.filter === 'all'
        ? TASK_STATUSES.map((s) => ({ ...s, items: tasks.filter((t) => String(t.status) === s.id) }))
          .filter((g) => g.items.length)
        : [{ id: taskState.filter, title: (TASK_STATUSES.find((s) => s.id === taskState.filter) || {}).title || taskState.filter, items: tasks }];

      const unknown = taskState.filter === 'all'
        ? tasks.filter((t) => !TASK_STATUSES.some((s) => s.id === String(t.status)))
        : [];
      if (unknown.length) groups.push({ id: 'other', title: 'Прочее', items: unknown });

      boardNode = h('div.board', groups.map((g) => h('div.column',
        h('div.column-head', dot(g.id), h('span.t', g.title), h('span.c', String(g.items.length))),
        g.items.map((t) => taskCard(t, agentById, ctx)))));
    }

    return h('div.stack.lg', composer, chips, boardNode);
  },

  onEvent(ev, ctx) {
    if (ev.kind === 'run.log') { appendLiveLog(ev); return false; }
    if (ev.kind.startsWith('task.')) return true;
    return false;
  },
};

function taskCard(t, agentById, ctx) {
  const id = pick(t, ['id']);
  const isOpen = String(taskState.openId) === String(id);
  const agent = agentById.get(String(pick(t, ['agent_id'])));
  const status = String(t.status || 'draft');

  const bodyEl = h('div.task-body');

  const meta = h('div.task-meta',
    statusBadge(status, { live: status === 'running' }),
    agent ? h('span', pick(agent, ['name'], '')) : null,
    h('span.num', fmtRelative(pick(t, ['updated_at', 'created_at']))));

  const main = h('div.task-main',
    h('div.task-title', pick(t, ['title'], titleFromPrompt(t.prompt))),
    meta);

  const card = h('div', { class: 'task' + (isOpen ? ' open' : '') });

  const head = h('div.task-head', {
    onClick: () => {
      if (String(taskState.openId) === String(id)) {
        taskState.openId = null;
        card.classList.remove('open');
        bodyEl.remove();
      } else {
        taskState.openId = id;
        ctx.refresh();
      }
    },
  }, h('span.task-caret', icon('chevron', 13)), main);

  card.appendChild(head);

  if (isOpen) {
    card.appendChild(bodyEl);
    append(bodyEl, h('div.small.dim', 'Загрузка деталей…'));
    loadTaskDetail(id, bodyEl, ctx);
  }
  return card;
}

async function loadTaskDetail(id, bodyEl, ctx) {
  let data;
  try {
    data = await api.task(id);
  } catch (e) {
    replace(bodyEl, h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось загрузить задачу'));
    return;
  }

  const task = (data && data.task) ? data.task : (data || {});
  const runs = listOf(data && (data.runs || data.task_runs) ? (data.runs || data.task_runs) : [], 'runs');
  let run = runs.length
    ? runs.slice().sort((a, b) => (Number(pick(a, ['attempt', 'id'], 0)) - Number(pick(b, ['attempt', 'id'], 0)))).pop()
    : null;

  /* если сервер вернул только ссылку на run — дочитываем его отдельно */
  const runRef = pick(data || {}, ['run_id', 'last_run_id']) ?? pick(task, ['run_id', 'last_run_id']);
  if (!run && runRef !== undefined && runRef !== null) {
    try {
      const r = await api.run(runRef);
      run = (r && r.run) ? r.run : r;
      if (run) runs.push(run);
    } catch { /* деталей run не будет — не критично */ }
  }

  taskState.detail = { task, runs, run };

  const status = String(task.status || 'draft');
  const result = pick(data || {}, ['result']) || (run ? pick(run, ['result']) : null);
  const error = run ? pick(run, ['error']) : pick(task, ['error']);
  const started = run ? pick(run, ['started_at']) : pick(task, ['created_at']);
  const finished = run ? pick(run, ['finished_at']) : null;
  const tokensIn = run ? pick(run, ['tokens_in'], 0) : 0;
  const tokensOut = run ? pick(run, ['tokens_out'], 0) : 0;

  const info = kv([
    ['агент', pick(task, ['agent_name']) || (ctx.state.agents || []).filter((a) => String(pick(a, ['id'])) === String(pick(task, ['agent_id']))).map((a) => pick(a, ['name']))[0] || `#${pick(task, ['agent_id'], '—')}`],
    ['модель', run ? pick(run, ['model_alias'], '—') : '—'],
    ['попытка', run ? `${pick(run, ['attempt'], 1)} из ${pick(task, ['max_retries'], 0) + 1 || 1}` : '—'],
    ['время', (status === 'running' || status === 'paused') ? fmtElapsed(started) : fmtElapsed(started, finished)],
    ['токены', `${fmtTokens(tokensIn)} / ${fmtTokens(tokensOut)}`],
    ['стоимость', run && run.cost_usd !== undefined && run.cost_usd !== null ? fmtCost(run.cost_usd) : '—'],
    ['создана', fmtDateShort(pick(task, ['created_at']))],
  ]);

  /* кнопки по статусу */
  const actions = h('div.row.tight');
  const act = async (action, label) => {
    try { await api.taskAction(pick(task, ['id']), action); toastOk(label); ctx.refresh(); }
    catch (e) { toastError(e, `Не удалось выполнить «${label}»`); }
  };
  if (status === 'running') {
    actions.appendChild(actionButton('Пауза', () => act('pause', 'Задача поставлена на паузу'), { cls: 'btn btn-sm', iconName: 'pause' }));
    actions.appendChild(actionButton('Остановить', () => act('stop', 'Задача остановлена'), { cls: 'btn btn-sm btn-danger', iconName: 'stop' }));
  } else if (status === 'queued' || status === 'waiting_approval') {
    actions.appendChild(actionButton('Остановить', () => act('stop', 'Задача остановлена'), { cls: 'btn btn-sm btn-danger', iconName: 'stop' }));
  } else if (status === 'paused') {
    actions.appendChild(actionButton('Продолжить', () => act('resume', 'Задача продолжена'), { cls: 'btn btn-sm btn-ok', iconName: 'play' }));
    actions.appendChild(actionButton('Остановить', () => act('stop', 'Задача остановлена'), { cls: 'btn btn-sm btn-danger', iconName: 'stop' }));
  } else if (status === 'failed' || status === 'stopped') {
    actions.appendChild(actionButton('Повторить', () => act('retry', 'Перезапуск задачи'), { cls: 'btn btn-sm btn-primary', iconName: 'retry' }));
  } else if (status === 'completed') {
    actions.appendChild(actionButton('Повторить', () => act('retry', 'Перезапуск задачи'), { cls: 'btn btn-sm', iconName: 'retry' }));
  } else if (status === 'draft') {
    actions.appendChild(actionButton('Запустить', () => act('run', 'Задача поставлена в очередь'), { cls: 'btn btn-sm btn-primary', iconName: 'play' }));
  }
  if (status === 'waiting_approval') {
    actions.appendChild(h('button.btn.btn-sm', { type: 'button', onClick: () => ctx.navigate('approvals') },
      icon('approvals', 13), h('span', 'В очередь подтверждений')));
  }
  /* P1: цепочка Task → Session → Diff → Merge должна быть достижима из задачи */
  if (status === 'running' || status === 'completed' || status === 'paused') {
    actions.appendChild(h('button.btn.btn-sm', { type: 'button', onClick: () => ctx.navigate('coding') },
      icon('edit', 13), h('span', 'Diff / Merge')));
  }

  /* лог */
  const logEl = h('div.log');
  const logEmpty = h('div.log-empty', 'Логов пока нет.');
  logEl.appendChild(logEmpty);

  taskState.logEl = logEl;
  taskState.logEmpty = logEmpty;
  taskState.logRunId = run ? pick(run, ['id']) : null;
  taskState.logLastId = 0;

  replace(bodyEl,
    h('div.stack',
      task.prompt ? h('div',
        h('div.section-title', 'Задание'),
        h('pre.block', String(task.prompt))) : null,
      info,
      actions,
      error ? h('div',
        h('div.section-title', 'Ошибка'),
        h('pre.block', { style: { color: 'var(--err)' } }, String(error))) : null,
      result ? h('div',
        h('div.section-title', 'Результат'),
        h('pre.block', String(result))) : null,
      h('div',
        h('div.row', { style: { marginBottom: '6px' } },
          h('div.section-title', { style: { margin: 0 } }, 'Live-лог'),
          h('div.spacer'),
          run ? h('span.xsmall.dim.num', `run #${pick(run, ['id'], '?')}`) : null),
        logEl),
      runs.length > 1 ? h('div',
        h('div.section-title', 'Попытки'),
        h('div.mini-list', runs.map((r) => h('div.mini-row',
          dot(r.status),
          h('span.name', `попытка ${pick(r, ['attempt'], '?')} · ${statusLabel(r.status)}`),
          h('span.xsmall.dim.num', fmtElapsed(pick(r, ['started_at']), pick(r, ['finished_at']))))))) : null));

  if (run) loadRunEvents(pick(run, ['id']));
}

async function loadRunEvents(runId) {
  try {
    const data = await api.runEvents(runId);
    const events = listOf(data, 'events');
    if (taskState.logRunId !== runId || !taskState.logEl) return;
    if (events.length && taskState.logEmpty) { taskState.logEmpty.remove(); taskState.logEmpty = null; }
    for (const e of events) {
      taskState.logEl.appendChild(logLine(e));
      const eid = Number(pick(e, ['id'], 0));
      if (eid > taskState.logLastId) taskState.logLastId = eid;
    }
    taskState.logEl.scrollTop = taskState.logEl.scrollHeight;
  } catch (e) {
    if (taskState.logEl && taskState.logRunId === runId) {
      replace(taskState.logEl, h('div.log-empty', `Логи не загрузились: ${e.message || 'ошибка'}`));
    }
  }
}

function logLine(e) {
  const level = String(pick(e, ['level'], 'info')).toLowerCase();
  const msg = pick(e, ['message', 'text'], '')
    || (e.data ? JSON.stringify(e.data).slice(0, 400) : '');
  return h('div', { class: `log-line lv-${level}` },
    h('span.log-ts', fmtClock(pick(e, ['ts', 'created_at']), true)),
    h('span.log-msg', `${pick(e, ['kind']) ? `[${pick(e, ['kind'])}] ` : ''}${msg}`));
}

function appendLiveLog(ev) {
  if (!taskState.logEl || !taskState.logRunId) return;
  const runId = pick(ev, ['run_id', 'run']);
  if (runId !== undefined && runId !== null && String(runId) !== String(taskState.logRunId)) return;
  const eid = Number(pick(ev, ['id'], 0));
  if (eid && eid <= taskState.logLastId) return;
  if (eid) taskState.logLastId = eid;
  if (taskState.logEmpty) { taskState.logEmpty.remove(); taskState.logEmpty = null; }
  const nearBottom = taskState.logEl.scrollHeight - taskState.logEl.scrollTop - taskState.logEl.clientHeight < 40;
  taskState.logEl.appendChild(logLine(ev));
  while (taskState.logEl.childElementCount > 800) taskState.logEl.removeChild(taskState.logEl.firstChild);
  if (nearBottom) taskState.logEl.scrollTop = taskState.logEl.scrollHeight;
}

/** Остановить все активные задачи (используется в командной палитре). */
export async function stopAllRunning(ctx) {
  let tasks = [];
  try { tasks = listOf(await api.tasks(), 'tasks'); }
  catch (e) { toastError(e, 'Не удалось получить список задач'); return; }

  const active = tasks.filter((t) => ['running', 'queued', 'paused'].includes(String(t.status)));
  if (!active.length) { toast('Активных задач нет', { type: 'info' }); return; }

  const ok = await confirmDialog({
    title: 'Остановить все активные задачи?',
    text: `Будут остановлены: ${active.map((t) => pick(t, ['title'], `#${pick(t, ['id'])}`)).slice(0, 8).join(', ')}${active.length > 8 ? ` и ещё ${active.length - 8}` : ''}.`,
    okText: `Остановить (${active.length})`, danger: true,
  });
  if (!ok) return;

  const results = await Promise.allSettled(active.map((t) => api.taskAction(pick(t, ['id']), 'stop')));
  const failed = results.filter((r) => r.status === 'rejected').length;
  if (failed) toast(`Остановлено ${results.length - failed} из ${results.length}`, { type: 'warn', hint: 'Часть задач не приняла команду — обновите список.' });
  else toastOk(`Остановлено задач: ${results.length}`);
  ctx.refresh();
}

/* ============================================================
   SCHEDULES
   ============================================================ */

const SchedulesPage = {
  id: 'schedules',
  title: 'Расписания',
  icon: 'schedules',

  async render(ctx) {
    const [schedR, agentsR] = await Promise.allSettled([api.schedules(), api.agents()]);
    const schedules = schedR.status === 'fulfilled' ? listOf(schedR.value, 'schedules') : [];
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    ctx.state.agents = agents;

    const head = h('div.row',
      h('div',
        h('div.section-title', { style: { margin: 0 } }, 'Расписания'),
        h('div.small.dim', `${schedules.length} правил · тик планировщика раз в 30 с`)),
      h('div.spacer'),
      h('button.btn.btn-primary', { type: 'button', onClick: () => openScheduleModal(ctx, agents) },
        icon('plus', 14), h('span', 'Новое расписание')));

    const body = schedR.status === 'rejected'
      ? errorBanner(schedR.reason, ctx)
      : schedules.length
        ? h('div.grid.auto-lg', schedules.map((s) => scheduleCard(s, ctx)))
        : h('section.panel', empty({
          iconName: 'schedules',
          title: 'Расписаний пока нет',
          hint: 'Расписание запускает задачу само: разово в заданное время, каждый день или через интервал.',
          action: h('button.btn.btn-primary', { type: 'button', onClick: () => openScheduleModal(ctx, agents) },
            icon('plus', 14), h('span', 'Новое расписание')),
        }));

    return h('div.stack.lg', head, body);
  },

  onEvent(ev) { return ev.kind.startsWith('schedule.') || ev.kind === 'task.created'; },
};

function scheduleKindLabel(s) {
  const kind = String(pick(s, ['kind'], ''));
  if (kind === 'once') return `Разово · ${fmtDateShort(pick(s, ['at_time']))}`;
  if (kind === 'daily') return `Ежедневно в ${String(pick(s, ['daily_time'], '')).slice(0, 5) || '—'}`;
  if (kind === 'interval') return `Каждые ${fmtNum(pick(s, ['interval_minutes'], 0))} мин`;
  return kind || '—';
}

function scheduleCard(s, ctx) {
  const id = pick(s, ['id']);
  const enabled = s.enabled !== false;
  const tpl = s.task_template && typeof s.task_template === 'object' ? s.task_template : {};

  return h('div.card',
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', pick(s, ['name'], 'без имени')),
        h('div.card-sub', scheduleKindLabel(s))),
      toggle(enabled, async (val) => {
        try { await api.updateSchedule(id, { enabled: val }); toastOk(val ? 'Расписание включено' : 'Расписание выключено'); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось переключить расписание'); ctx.refresh(); }
      }, enabled ? 'включено' : 'выключено')),

    kv([
      ['следующий запуск', enabled ? (pick(s, ['next_run_at']) ? `${fmtDateShort(s.next_run_at)} · ${fmtRelative(s.next_run_at)}` : '—') : 'выключено'],
      ['последний запуск', pick(s, ['last_fired_at']) ? fmtDateShort(s.last_fired_at) : 'ещё не запускалось'],
    ]),

    tpl.prompt ? h('div.xsmall.dim.wrap-any',
      String(tpl.prompt).slice(0, 160) + (String(tpl.prompt).length > 160 ? '…' : '')) : null,

    h('div.card-actions',
      h('button.btn.btn-sm.btn-danger', {
        type: 'button',
        onClick: async () => {
          const ok = await confirmDialog({
            title: 'Удалить расписание?',
            text: `«${pick(s, ['name'], 'расписание')}» больше не будет создавать задачи. Уже созданные задачи останутся.`,
            okText: 'Удалить', danger: true,
          });
          if (!ok) return;
          try { await api.deleteSchedule(id); toastOk('Расписание удалено'); ctx.refresh(); }
          catch (e) { toastError(e, 'Не удалось удалить расписание'); }
        },
      }, icon('trash', 13), h('span', 'Удалить'))));
}

export async function openScheduleModal(ctx, agents = null, preset = {}) {
  let list = agents || ctx.state.agents;
  if (!list || !list.length) {
    try { list = listOf(await api.agents(), 'agents'); ctx.state.agents = list; } catch { list = []; }
  }

  const nameEl = input({ placeholder: 'Утренний обзор', value: preset.name || '' });
  const promptEl = textarea({ rows: 4, placeholder: 'Что делать при срабатывании…', value: preset.prompt || '' });
  const agentEl = agentSelect(list, preset.agentId ?? (list.length === 1 ? pick(list[0], ['id']) : null));

  let kind = 'daily';
  const atEl = input({ type: 'datetime-local' });
  const dailyEl = input({ type: 'time', value: '09:00' });
  const intervalEl = input({ type: 'number', min: '1', value: '60', class: 'input mono' });

  const kindHolder = h('div');
  const seg = h('div.seg',
    segBtn('Разово', 'once'), segBtn('Ежедневно', 'daily'), segBtn('Интервал', 'interval'));

  function segBtn(label, value) {
    return h('button', {
      type: 'button', class: kind === value ? 'on' : '',
      onClick: () => { kind = value; syncSeg(); },
    }, label);
  }
  function syncSeg() {
    Array.from(seg.children).forEach((b) => b.classList.toggle('on', b.textContent === ({ once: 'Разово', daily: 'Ежедневно', interval: 'Интервал' })[kind]));
    clear(kindHolder);
    if (kind === 'once') append(kindHolder, field('Дата и время', atEl, 'Локальное время вашей машины.'));
    else if (kind === 'daily') append(kindHolder, field('Время запуска', dailyEl, 'Каждый день в это время.'));
    else append(kindHolder, field('Интервал, минут', intervalEl, 'Первый запуск — через указанный интервал.'));
  }
  syncSeg();

  const modal = openModal({
    title: 'Новое расписание',
    wide: true,
    body: h('div.stack',
      field('Название', nameEl),
      field('Задача', promptEl),
      field('Агент', agentEl),
      field('Тип', seg),
      kindHolder),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Создать', async () => {
        const name = nameEl.value.trim();
        const prompt = promptEl.value.trim();
        if (!name) { toast('Укажите название', { type: 'warn' }); nameEl.focus(); return; }
        if (!prompt) { toast('Опишите задачу', { type: 'warn' }); promptEl.focus(); return; }
        if (!agentEl.value) { toast('Выберите агента', { type: 'warn' }); return; }

        const payload = {
          name, kind, enabled: true,
          task_template: {
            title: titleFromPrompt(prompt),
            prompt,
            agent_id: idVal(agentEl.value),
            priority: 5,
          },
        };
        if (kind === 'once') {
          if (!atEl.value) { toast('Укажите дату и время', { type: 'warn' }); return; }
          const d = new Date(atEl.value);
          if (Number.isNaN(d.getTime())) { toast('Некорректная дата', { type: 'warn' }); return; }
          payload.at_time = d.toISOString();
        } else if (kind === 'daily') {
          if (!dailyEl.value) { toast('Укажите время', { type: 'warn' }); return; }
          payload.daily_time = dailyEl.value;
        } else {
          const m = Number(intervalEl.value);
          if (!m || m < 1) { toast('Интервал — минимум 1 минута', { type: 'warn' }); return; }
          payload.interval_minutes = m;
        }

        try {
          await api.createSchedule(payload);
          handle.close();
          toastOk('Расписание создано');
          ctx.navigate('schedules');
        } catch (e) { toastError(e, 'Не удалось создать расписание'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
  return modal;
}

/* ============================================================
   APPROVALS
   ============================================================ */

const ApprovalsPage = {
  id: 'approvals',
  title: 'Ждут решения',
  icon: 'approvals',

  async render(ctx) {
    let items = [];
    let err = null;
    try { items = listOf(await api.approvals('pending'), 'approvals'); }
    catch (e) { err = e; }

    ctx.setBadge('approvals', items.length);

    const head = ui.pageHead('Ждут вашего решения',
      items.length
        ? `${ui.plural(items.length, 'действие', 'действия', 'действий')} нельзя выполнить без вашего «да»`
        : 'Здесь появляются шаги, которые агент не делает без вашего разрешения.');

    const body = err
      ? ui.errorNote(err, () => ctx.refresh())
      : items.length
        ? h('div.bx-cards.is-wide', items.map((a) => approvalCard(a, ctx)))
        : ui.blank({
          iconName: 'approvals',
          title: 'Пока ничего не ждёт решения',
          hint: 'Сюда попадут действия агентов, на которые нужно ваше согласие: отправить письмо, опубликовать пост, сделать дорогой платный вызов.',
        });

    return h('div.bx-page', head, body);
  },

  onEvent(ev) { return ev.kind.startsWith('approval.'); },
};

function approvalCard(a, ctx) {
  const id = pick(a, ['id']);
  const decide = async (approve) => {
    try {
      await api.decideApproval(id, approve, 'ui');
      toastOk(approve ? 'Разрешено' : 'Отклонено');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось отправить решение'); }
  };

  const source = [
    a.task_id ? `задача #${a.task_id}` : null,
    a.run_id ? `запуск #${a.run_id}` : null,
  ].filter(Boolean).join(' · ');

  return ui.tile({
    accent: 'var(--bx-amber)',
    iconName: 'approvals',
    title: pick(a, ['kind'], 'действие'),
    sub: `появилось ${fmtRelative(pick(a, ['created_at']))}${source ? ` · ${source}` : ''}`,
    statusNode: ui.statusPill(pick(a, ['status'], 'pending')),
    body: [
      ui.codeBlock(String(pick(a, ['preview'], 'Предпросмотр не предоставлен'))),
    ],
    actions: [
      ui.btn('Разрешить', () => decide(true), { variant: 'primary', size: 'sm', iconName: 'check', accent: 'var(--bx-mint)' }),
      ui.btn('Отклонить', () => decide(false), { variant: 'subtle', size: 'sm', iconName: 'close' }),
      a.task_id ? ui.btn('Открыть задачу', () => ctx.navigate('tasks', { task: a.task_id }), { variant: 'ghost', size: 'sm' }) : null,
    ].filter(Boolean),
  });
}

/* ============================================================
   SYSTEM
   ============================================================ */

/** Приведение ответа /api/system к единой форме. */
function normalizeSystem(data) {
  const d = data && typeof data === 'object' ? data : {};
  const cur = (d.current && typeof d.current === 'object') ? d.current
    : (d.now && typeof d.now === 'object') ? d.now
      : (d.metrics && typeof d.metrics === 'object' && !Array.isArray(d.metrics)) ? d.metrics
        : d;

  const num = (v) => (v === null || v === undefined || v === '' ? null : Number(v));

  const history = listOf(d.history || d.samples || d.metrics_history, 'history', 'samples');

  /* health: объект-карта {db:'ok'} или массив [{name,status,detail}] */
  const rawHealth = d.health || d.components || d.checks || {};
  let health = [];
  if (Array.isArray(rawHealth)) {
    health = rawHealth.map((c) => ({
      name: pick(c, ['name', 'component', 'id'], '—'),
      status: pick(c, ['status', 'state'], 'unknown'),
      detail: pick(c, ['detail', 'message', 'error'], ''),
    }));
  } else if (rawHealth && typeof rawHealth === 'object') {
    health = Object.entries(rawHealth).map(([name, v]) => {
      if (v && typeof v === 'object') {
        return { name, status: pick(v, ['status', 'state'], 'unknown'), detail: pick(v, ['detail', 'message', 'error'], '') };
      }
      if (typeof v === 'boolean') return { name, status: v ? 'ok' : 'down', detail: '' };
      return { name, status: String(v), detail: '' };
    });
  }

  /* gpu: null | объект | массив */
  const rawGpu = cur.gpu !== undefined ? cur.gpu : d.gpu;
  const gpuArr = rawGpu === null || rawGpu === undefined ? []
    : Array.isArray(rawGpu) ? rawGpu : [rawGpu];
  const gpus = gpuArr.filter(Boolean).map((g) => ({
    name: pick(g, ['name', 'model', 'device'], 'GPU'),
    util: num(pick(g, ['util_pct', 'utilization', 'load', 'gpu_pct'])),
    memUsed: num(pick(g, ['mem_used_mb', 'memory_used_mb', 'vram_used_mb'])),
    memTotal: num(pick(g, ['mem_total_mb', 'memory_total_mb', 'vram_total_mb'])),
    /* сколько из занятой VRAM приходится на вычислительные процессы: без этой
       цифры «занято 9 ГБ» нельзя списать на модель — там ещё браузер и рабочий стол */
    memProcs: num(pick(g, ['vram_procs_mb'])),
    procs: Array.isArray(g.procs) ? g.procs : [],
    temp: num(pick(g, ['temp_c', 'temperature', 'temp'])),
  }));

  const tones = health.map((c) => statusTone(c.status));
  /* P1 no-fake-green: нет данных, unknown или idle-статус компонента — это НЕ «в норме».
     Разрешённые честные состояния: ok / warn (degraded|unknown|empty) / err (offline|down). */
  const overall = tones.includes('err') ? 'err'
    : (!health.length || tones.includes('warn') || tones.includes('idle')) ? 'warn'
      : 'ok';

  return {
    cpu: num(pick(cur, ['cpu_pct', 'cpu', 'cpu_percent'])),
    ramUsed: num(pick(cur, ['ram_used_mb', 'mem_used_mb'])),
    ramTotal: num(pick(cur, ['ram_total_mb', 'mem_total_mb'])),
    diskUsed: num(pick(cur, ['disk_used_gb'])),
    diskTotal: num(pick(cur, ['disk_total_gb'])),
    ts: pick(cur, ['ts', 'time', 'timestamp']),
    gpus, history, health, overall,
    raw: d,
  };
}

const sysState = { cpu: [], ram: [], node: null };

/* ---------- PASS3: Provider Cache Economics + Cognitive Reuse Intelligence ----------
   Панели различают три уровня достоверности: «измерено» (из usage провайдера или
   БД), «оценка» (контрфактический расчёт), «неизвестно» (данных нет — не заявляем).
   Hit rate показывается как диагностика, не как KPI. */
const EVIDENCE_TONE = { measured: 'ok', estimated: 'warn', unknown: 'dim' };
const EVIDENCE_WORD = { measured: 'измерено', estimated: 'оценка', unknown: 'неизвестно' };

function evidenceHead(level, text) {
  return h('div.row', h('b.small', text), h('div.spacer'),
    ui.pill(EVIDENCE_WORD[level], { tone: EVIDENCE_TONE[level] }));
}

function kvRow(label, value) {
  const v = value === null || value === undefined || value === '' ? '—' : String(value);
  return h('div.row.xsmall', h('span.dim', label), h('div.spacer'), h('span.num', v));
}

function fmtUsd(v) { return typeof v === 'number' ? `$${v.toFixed(4)}` : null; }

async function fetchCachePanels() {
  const settled = await Promise.allSettled([api.cacheEconomics(), api.cacheIntelligence()]);
  return settled.map((r) => (r.status === 'fulfilled' && r.value && typeof r.value === 'object'
    ? r.value
    : { available: false, reason: String((r.reason && r.reason.message) || r.reason || 'нет ответа') }));
}

function cacheEconomicsPanel(econ) {
  if (!econ || !econ.available) {
    return ui.panel('Экономика кэша провайдера',
      h('div.small.dim', `Нет наблюдений кэша: ${econ && econ.reason ? econ.reason : 'сервер не прислал данные'}.`),
      { icon: 'system' });
  }
  const m = econ.measured || {}; const est = econ.estimated || {}; const unk = econ.unknown || {};
  const counts = m.counts || {}; const tok = m.tokens || {};
  const states = ['HIT', 'WRITE', 'MISS', 'BYPASS', 'UNKNOWN', 'DEGRADED'];
  const body = h('div.stack.sm',
    econ.warning ? ui.pill(econ.warning, { tone: 'err' }) : null,
    evidenceHead('measured', 'По usage провайдера'),
    h('div.row', states.map((st) => h('span.xsmall', `${st} ${counts[st] ?? 0}`))),
    kvRow('Запросов с кэшем', m.eligible_requests),
    kvRow('Hit rate (диагностика, не KPI)', m.hit_rate_percent === null || m.hit_rate_percent === undefined
      ? null : `${m.hit_rate_percent}%`),
    kvRow('Токены fresh / read / write', `${tok.fresh ?? 0} / ${tok.cache_read ?? 0} / ${tok.cache_write ?? 0}`),
    kvRow('Фактическая стоимость', fmtUsd(m.actual_cost_usd)),
    kvRow('Degraded / unknown событий', `${m.degraded_events ?? 0} / ${m.unknown_events ?? 0}`),
    evidenceHead('estimated', 'Контрфактический расчёт'),
    kvRow('База «всё fresh»', fmtUsd(est.baseline_cost_usd)),
    kvRow('Экономия', est.saved_usd === null || est.saved_usd === undefined
      ? 'не может быть заявлена' : fmtUsd(est.saved_usd)),
    evidenceHead('unknown', 'Без доказательства'),
    kvRow('Запросов без цены', unk.cost_requests),
    kvRow('cache_control без usage', unk.cache_control_without_usage),
    kvRow('Отброшено невалидных', unk.dropped_invalid),
    econ.by_route ? kvRow('По маршрутам', Object.entries(econ.by_route).map(([k, v]) => `${k}:${v}`).join(' · ')) : null);
  return ui.panel('Экономика кэша провайдера', body, { icon: 'system' });
}

function cacheIntelligencePanel(intel) {
  if (!intel || !intel.available) {
    return ui.panel('Когнитивное переиспользование',
      h('div.small.dim', `Нет данных: ${intel && intel.reason ? intel.reason : 'сервер не прислал данные'}.`),
      { icon: 'system' });
  }
  const m = intel.measured || {}; const unk = intel.unknown || {};
  const lc = intel.learning_candidates || {}; const flags = intel.flags || {};
  const rate = typeof m.verified_success_rate === 'number' ? `${Math.round(m.verified_success_rate * 100)}%` : null;
  const body = h('div.stack.sm',
    evidenceHead('measured', 'Из базы данных и наблюдений'),
    kvRow('Проверок / подтверждённых', `${m.evaluations ?? 0} / ${m.verified_evaluations ?? 0}`),
    kvRow('VerifiedSuccess', rate),
    kvRow('Завершённых задач', m.completed_tasks),
    kvRow('Устаревший / degraded кэш', m.stale_or_degraded_cache_events),
    evidenceHead('unknown', 'Не измерено'),
    h('div.stack.xs', Object.entries(unk).map(([k, v]) => kvRow(k, v))),
    evidenceHead('measured', 'Кандидаты обучения'),
    kvRow('Продвинуто / откачено / карантин', `${lc.promoted ?? 0} / ${lc.rolled_back ?? 0} / ${lc.quarantined ?? 0}`),
    h('div.row', Object.entries(flags).map(([k, v]) => ui.pill(`${k.replace('BOSSMAN_', '')}: ${v ? 'вкл' : 'выкл'}`,
      { tone: v ? 'warn' : 'dim' }))),
    kvRow('Сигналы лишнего контекста', intel.waste_signals === null ? 'выключено флагом' : (intel.waste_signals || []).length),
    kvRow('Советы по кэшу (только рекомендации)', intel.advice === null ? 'выключено флагом' : (intel.advice || []).length),
    intel.advice && intel.advice.length
      ? h('div.stack.xs', intel.advice.slice(0, 4).map((a) => h('div.xsmall.dim', `${a.action || a.kind || '—'}: ${a.reason || ''}`)))
      : null);
  return ui.panel('Когнитивное переиспользование', body, { icon: 'system' });
}

const SystemPage = {
  id: 'system',
  title: 'Система',
  icon: 'system',

  async render(ctx) {
    let sys = null; let err = null;
    try { sys = normalizeSystem(await api.system()); }
    catch (e) { err = e; }

    if (err) return h('div.stack.lg', errorBanner(err, ctx));
    const [econ, intel] = await fetchCachePanels();

    /* серии для спарклайнов: из истории сервера, дальше дополняются по WS */
    sysState.cpu = sys.history.map((s) => Number(pick(s, ['cpu_pct', 'cpu'], NaN))).filter(Number.isFinite);
    sysState.ram = sys.history.map((s) => {
      const used = Number(pick(s, ['ram_used_mb'], NaN));
      const total = Number(pick(s, ['ram_total_mb'], sys.ramTotal || NaN));
      return Number.isFinite(used) && Number.isFinite(total) && total > 0 ? (used / total) * 100 : NaN;
    }).filter(Number.isFinite);
    if (sys.cpu !== null) sysState.cpu.push(sys.cpu);
    if (sys.ramTotal) sysState.ram.push((sys.ramUsed / sys.ramTotal) * 100);

    const cpuSpark = h('div', sparkline(sysState.cpu, { min: 0, max: 100 }));
    const ramSpark = h('div', sparkline(sysState.ram, { min: 0, max: 100 }));

    const cpuPanel = ui.panel('Загрузка процессора', h('div.stack.sm',
      h('div.row', h('span.bx-bignum.is-accent', sys.cpu === null ? '—' : `${Math.round(sys.cpu)}%`),
        h('div.spacer'), h('span.bx-pagehead-sub', { style: { margin: 0 } }, 'за последние 15 минут')),
      cpuSpark), { icon: 'system' });

    const ramPct = sys.ramTotal ? (sys.ramUsed / sys.ramTotal) * 100 : 0;
    const ramPanel = ui.panel('Оперативная память', h('div.stack.sm',
      h('div.row',
        h('span.bx-bignum', sys.ramTotal ? `${fmtGb(sys.ramUsed)}` : '—'),
        h('span.bx-pagehead-sub', { style: { margin: '0 0 0 6px' } }, sys.ramTotal ? `из ${fmtGb(sys.ramTotal)} ГБ занято` : ''),
        h('div.spacer'),
        h('span.bx-pagehead-sub', { style: { margin: 0 } }, sys.ramTotal ? `${Math.round(ramPct)}%` : '')),
      ramSpark));

    const diskPanel = ui.panel('Диск', sys.diskTotal
      ? ui.meter('Занято', sys.diskUsed || 0, sys.diskTotal, `${fmtNum(sys.diskUsed, 1)} / ${fmtNum(sys.diskTotal, 1)} ГБ`)
      : h('div.small.dim', 'Данных по диску нет.'));

    const gpuPanel = ui.panel('Видеокарта (GPU)', sys.gpus.length
      ? h('div.stack.sm', sys.gpus.map((g) => h('div.stack.sm',
        h('div.row', h('b.small', g.name), h('div.spacer'),
          g.temp !== null ? h('span.bx-pagehead-sub', { style: { margin: 0 } }, `${Math.round(g.temp)} °C`) : null),
        g.util !== null ? ui.meter('Загрузка', g.util, 100, `${Math.round(g.util)}%`) : null,
        g.memTotal ? ui.meter('Память видеокарты занята', g.memUsed || 0, g.memTotal,
          `${fmtGb(g.memUsed)} / ${fmtGb(g.memTotal)} ГиБ`) : null,
        g.memProcs !== null && g.memTotal
          ? ui.meter('из них под модели', g.memProcs, g.memTotal, `${fmtGb(g.memProcs)} ГиБ`) : null,
        g.procs.length
          ? h('div.stack.xs', g.procs.slice(0, 4).map((p) => h('div.row.xsmall.dim',
            h('span', `${p.name || 'процесс'} · ${p.pid ?? '—'}`), h('div.spacer'),
            h('span.num', `${fmtGb(p.vram_used_mb)} ГиБ`))))
          : null)))
      : h('div.small.dim', 'Видеокарта не найдена — сервер не сообщает её данные.'));

    const overallWord = sys.overall === 'ok' ? 'всё в норме'
      : sys.overall === 'warn' ? 'есть предупреждения' : 'есть сбой';
    const healthPanel = ui.panel('Из чего состоит система',
      sys.health.length
        ? h('div.bx-list', sys.health.map((c) => {
          const st = ui.statusText(c.status);
          return h('div.bx-list-row',
            h('div', { style: { minWidth: 0 } },
              h('div.bx-list-name', HEALTH_LABEL[c.name] || c.name),
              c.detail ? h('div.bx-list-note', String(c.detail).slice(0, 90)) : null),
            h('span.bx-list-end', ui.pill(st.word, { tone: st.tone, live: !!st.live })));
        }))
        : h('div.small.dim', 'Сервер не прислал состав системы.'),
      { icon: 'system', aside: ui.pill(overallWord, { tone: sys.overall === 'ok' ? 'ok' : sys.overall === 'warn' ? 'warn' : 'err' }) });

    const node = h('div.bx-page',
      ui.pageHead('Состояние системы', 'Что происходит с сервером прямо сейчас: нагрузка, память, диск и все части системы.'),
      h('div.bx-row', cpuPanel, ramPanel),
      h('div.bx-row', diskPanel, gpuPanel),
      healthPanel,
      h('div.bx-row', cacheEconomicsPanel(econ), cacheIntelligencePanel(intel)),
      sys.ts ? h('div.bx-pagehead-sub', { style: { margin: 0 } }, `Данные на ${fmtDateShort(sys.ts)}`) : null);

    sysState.node = { cpuSpark, ramSpark };
    return node;
  },

  onEvent(ev) {
    if (ev.kind !== 'system.metrics') return false;
    const s = normalizeSystem(ev.data && typeof ev.data === 'object' ? ev.data : ev);
    if (s.cpu !== null) { sysState.cpu.push(s.cpu); if (sysState.cpu.length > 120) sysState.cpu.shift(); }
    if (s.ramTotal) { sysState.ram.push((s.ramUsed / s.ramTotal) * 100); if (sysState.ram.length > 120) sysState.ram.shift(); }
    if (sysState.node) {
      replace(sysState.node.cpuSpark, sparkline(sysState.cpu, { min: 0, max: 100 }));
      replace(sysState.node.ramSpark, sparkline(sysState.ram, { min: 0, max: 100 }));
    }
    return false;
  },
};

const HEALTH_LABEL = {
  db: 'База данных',
  database: 'База данных',
  worker: 'Обработчик задач',
  queue_worker: 'Обработчик задач',
  scheduler: 'Планировщик по расписанию',
  queue: 'Очередь задач',
  event_bus: 'Обмен событиями',
  events: 'Обмен событиями',
  metrics: 'Сбор показателей',
  models: 'Модели',
  disk: 'Диск',
  memory: 'Память',
};

/* ============================================================
   SETTINGS
   ============================================================ */

const SettingsPage = {
  id: 'settings',
  title: 'Настройки',
  icon: 'settings',

  async render(ctx) {
    let providers = []; let provErr = null;
    try { providers = listOf(await api.providers(), 'providers'); }
    catch (e) { provErr = e; }

    /* --- тема --- */
    const themeSeg = h('div.seg',
      h('button', { type: 'button', class: ctx.getTheme() === 'dark' ? 'on' : '', onClick: () => { ctx.setTheme('dark'); ctx.refresh(); } }, 'Тёмная'),
      h('button', { type: 'button', class: ctx.getTheme() === 'light' ? 'on' : '', onClick: () => { ctx.setTheme('light'); ctx.refresh(); } }, 'Светлая'));

    const appearance = panel('Внешний вид',
      h('div.row', h('div', h('div.small', 'Тема интерфейса'), h('div.xsmall.dim', 'Выбор сохраняется в этом браузере.')),
        h('div.spacer'), themeSeg));

    /* --- провайдеры --- */
    const providersPanel = h('section.panel',
      h('div.panel-head', icon('key', 15), h('h2', 'Провайдеры и ключи'), h('div.spacer'),
        h('button.btn.btn-sm', { type: 'button', onClick: () => ctx.navigate('models') }, 'К моделям')),
      provErr
        ? h('div.panel-body', h('div.small.dim', provErr.message))
        : providers.length
          ? h('div.mini-list', providers.map((p) => h('div.mini-row',
            h('div.name',
              h('div', h('b', pick(p, ['name'], 'провайдер')),
                h('span.xsmall.dim', ` · ${kindLabel(p.kind)}`)),
              p.base_url ? h('div.xsmall.dim.mono.truncate', String(p.base_url)) : null),
            h('span.badge.mono', maskSecret(pick(p, ['api_key_masked', 'api_key_mask', 'api_key', 'key_masked'], ''))),
            h('button.btn.btn-sm.btn-danger', {
              type: 'button',
              onClick: async () => {
                const ok = await confirmDialog({
                  title: 'Удалить провайдера?',
                  text: `«${pick(p, ['name'], '')}» и его ключ будут удалены. Модели этого провайдера перестанут работать.`,
                  okText: 'Удалить', danger: true,
                });
                if (!ok) return;
                try { await api.deleteProvider(pick(p, ['id'])); toastOk('Провайдер удалён'); ctx.refresh(); }
                catch (e) { toastError(e, 'Не удалось удалить провайдера'); }
              },
            }, icon('trash', 13)))))
          : h('div.panel-body', h('div.small.dim', 'Провайдеров нет. Добавьте первого при создании модели.')));

    /* --- доступ --- */
    const access = panel('Доступ',
      h('div.stack.sm',
        h('div.row',
          h('div',
            h('div.small', 'Сессия этого браузера'),
            h('div.xsmall.dim', ctx.hasSession()
              ? 'активна · ключ сессии хранится в HttpOnly-cookie и недоступен скриптам'
              : 'нет активной сессии')),
          h('div.spacer'),
          h('button.btn.btn-danger', { type: 'button', onClick: () => ctx.logout() },
            icon('logout', 14), h('span', 'Выйти'))),
        h('div.xsmall.dim', 'Выход завершает сессию на сервере, а не только в этом браузере. Сам сервер продолжит работать, задачи не прерываются.')));

    const about = panel('О системе',
      h('div.stack.sm',
        h('div.xsmall.dim', 'BOSSMAN AI Command Center — локальный control plane: модели, агенты, задачи, расписания, метрики.'),
        h('div.xsmall.dim', 'Интерфейс работает офлайн: без CDN, без сборки, только Control API этого же сервера.')));

    return h('div.stack.lg',
      h('div.grid.cols-2', appearance, access),
      providersPanel,
      about);
  },
};

/* ============================================================
   Экспорт
   ============================================================ */

export const PAGES = [
  HomePage, ModelsPage, AgentsPage, TasksPage, SchedulesPage, ApprovalsPage, SystemPage, SettingsPage,
];

export { openModelWizard };
