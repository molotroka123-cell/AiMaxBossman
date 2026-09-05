import { routeVideoRequest, attachmentInput, attachedFiles } from '../video_chat.js';
/* ============================================================
   home.js — посадочная страница BOSSMAN.

   Идея страницы: BOSSMAN — операционная система для приложений и
   агентов, а не панель разработчика. Отсюда порядок блоков:

     1. кто ты и в каком состоянии система   (hero + пилюли)
     2. что ты хочешь, чтобы она сделала     (командная строка)
     3. твои приложения                       (крупные карточки — главный элемент)
     4. что идёт прямо сейчас                 (миссии, решения, агенты)
     5. на чём это работает                   (вычисления, здоровье)
     6. что происходило                       (лента)

   Карточки приложений строятся из `/api/apps`, который читает
   манифесты в `apps/<имя>/`. Ни одно название приложения здесь не
   зашито: App #3 и App #5 появятся сами, добавив блок `ui:` в свой
   манифест. Захардкоженная карточка разошлась бы с правдой в первый
   же день — а карточка, которая врёт, хуже отсутствующей.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import { h, icon, toast, toastOk, toastError, fmtGb, fmtRelative } from '../components.js';
import { errorBanner } from './_shared.js';
import { statusText } from './_ui.js';
import { appCard, appIcon } from './appcards.js';

/* Человеческие имена частей системы — чтобы на главной не мелькали
   queue_worker и db. */
const HEALTH_NAME = {
  db: 'База данных', database: 'База данных',
  worker: 'Обработчик задач', queue_worker: 'Обработчик задач',
  scheduler: 'Планировщик', queue: 'Очередь задач',
  event_bus: 'Обмен событиями', events: 'Обмен событиями',
  metrics: 'Сбор показателей', models: 'Модели',
  disk: 'Диск', memory: 'Память',
};

const RAW_LOG_KINDS = new Set(['run.log', 'ws.open', 'ws.closed', 'ws.connecting',
  'ws.idle', 'system.metrics']);

const MODES = [
  { id: 'smart', label: 'Умно', hint: 'BOSSMAN сам выберет модель и путь' },
  { id: 'auto', label: 'Авто', hint: 'запустить сразу, без уточнений' },
  { id: 'agents', label: 'С агентами', hint: 'раздать работу нескольким агентам' },
];

const state = { modes: new Set(['smart']), agentId: null };

/* ---------------------------------------------------------------- мелочи */

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return 'Доброй ночи';
  if (hour < 12) return 'Доброе утро';
  if (hour < 18) return 'Добрый день';
  return 'Добрый вечер';
}

function pill(text, { tone = 'idle', value = '', live = false } = {}) {
  return h('span', { class: `bx-pill is-${tone}${live ? ' is-live' : ''}` },
    h('span.bx-pill-dot'),
    h('span', text),
    value !== '' && value !== null && value !== undefined
      ? h('span.bx-pill-val', String(value)) : null);
}

function bar(value, max, accent) {
  const width = max > 0 ? Math.max(0, Math.min(100, (Number(value) / max) * 100)) : 0;
  return h('span.bx-bar', accent ? { style: { '--bx-accent': accent } } : null,
    h('i', { style: { width: `${width}%` } }));
}

function line(name, note, value, extra) {
  return h('div.bx-line',
    h('div', { style: { minWidth: 0 } },
      h('div.bx-line-name', name),
      note ? h('div.bx-line-note', note) : null),
    extra || null,
    value !== null && value !== undefined ? h('span.bx-line-val', String(value)) : null);
}

function panel(title, body, aside) {
  return h('section.bx-panel',
    h('div.bx-panel-head', h('h2', title), h('div.bx-spacer'), aside || null),
    h('div.bx-panel-body', body));
}

/* ---------------------------------------------------------------- страница */

const HomePage = {
  id: 'home-v3',
  title: 'Главная',
  icon: 'home',
  nav: 'primary',
  section: 'main',

  async render(ctx) {
    const [appsR, missionsR, agentsR, modelsR, approvalsR, systemR, resourcesR, mapR, activityR,
           liveTasksR, failedTasksR] =
      await Promise.allSettled([
        api.raw('/api/apps'), api.raw('/api/missions'), api.agents(), api.models(),
        api.approvals('pending'), api.system(), api.raw('/api/resources'),
        api.raw('/api/agentmap'), api.activity(),
        api.tasks('running,waiting_approval,paused,queued'), api.tasks('failed'),
      ]);

    const apps = appsR.status === 'fulfilled' ? listOf(appsR.value, 'apps') : [];
    const missions = missionsR.status === 'fulfilled' ? listOf(missionsR.value, 'missions') : [];
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    const models = modelsR.status === 'fulfilled' ? listOf(modelsR.value, 'models') : [];
    const approvals = approvalsR.status === 'fulfilled' ? listOf(approvalsR.value, 'approvals') : [];
    const sys = systemR.status === 'fulfilled' ? systemR.value : null;
    const resources = resourcesR.status === 'fulfilled' ? resourcesR.value : null;
    const graph = mapR.status === 'fulfilled' ? mapR.value : { nodes: [] };
    const activity = activityR.status === 'fulfilled'
      ? listOf(activityR.value, 'events', 'activity') : [];
    const liveTasks = liveTasksR.status === 'fulfilled' ? listOf(liveTasksR.value, 'tasks') : [];
    const failedTasks = failedTasksR.status === 'fulfilled' ? listOf(failedTasksR.value, 'tasks') : [];

    ctx.state.models = models;
    ctx.state.agents = agents;
    ctx.setBadge('approvals', approvals.length);

    const everythingFailed = systemR.status === 'rejected' && appsR.status === 'rejected';

    return h('div.bx-home',
      everythingFailed ? errorBanner(systemR.reason, ctx) : null,
      buildHero({ sys, apps, agents, graph, online: systemR.status === 'fulfilled' }),
      buildAttention({ approvals, failedTasks, missions, sys, models, liveTasks }, ctx),
      buildNow({ liveTasks, missions, activity }, ctx),
      buildCommandBar(ctx, agents),
      buildApps(apps, appsR, ctx),
      h('div.bx-row',
        buildActiveNow(missions, approvals, ctx),
        buildAgents(agents, graph, ctx),
        buildCompute(sys, resources, ctx),
        buildHealth(sys, models, ctx)),
      buildActivity(activity, ctx));
  },

  onEvent(ev) {
    return ev.kind.startsWith('mission.') || ev.kind.startsWith('task.')
      || ev.kind.startsWith('tool.') || ev.kind === 'router.fallback'
      || ev.kind.startsWith('hook.') || ev.kind === 'evaluation.completed'
      || ev.kind.startsWith('approval.') || ev.kind.startsWith('resource.')
      || ev.kind === 'model.status' || ev.kind.startsWith('agent.');
  },
};

/* ---------------------------------------------------------------- A. hero */

function buildHero({ sys, apps, agents, graph, online }) {
  const working = (graph.nodes || []).filter((n) => n.status === 'working').length;
  const liveApps = apps.filter((a) => a.status === 'LIVE').length;
  const gpu = firstGpu(sys);

  return h('header.bx-hero',
    h('div', { style: { minWidth: 0 } },
      h('h1.bx-hero-title', 'BOSSMAN'),
      h('p.bx-hero-sub', online
        ? `${greeting()} — система работает.`
        : `${greeting()} — сервер не отвечает, данные могут быть неполными.`)),
    h('div.bx-hero-pills',
      pill(online ? 'Система в строю' : 'Нет связи',
        { tone: online ? 'ok' : 'err', live: online }),
      pill('Приложений готово', { tone: liveApps ? 'info' : 'idle',
        value: `${liveApps}/${apps.length}` }),
      pill('Агентов в работе', { tone: working ? 'accent' : 'idle',
        value: `${working}/${agents.length}`, live: working > 0 }),
      gpu ? pill(gpu.label, { tone: 'info', value: gpu.value })
        : cpuPill(sys)));
}

function firstGpu(sys) {
  const raw = sys && (sys.current ? sys.current.gpu : sys.gpu);
  const list = Array.isArray(raw) ? raw : raw ? [raw] : [];
  const g = list[0];
  if (!g) return null;
  const used = Number(pick(g, ['vram_procs_mb', 'vram_used_mb'], NaN));
  const total = Number(pick(g, ['vram_total_mb'], NaN));
  if (Number.isFinite(used) && Number.isFinite(total) && total > 0) {
    return { label: 'GPU', value: `${fmtGb(used)}/${fmtGb(total)} ГиБ` };
  }
  const util = Number(pick(g, ['util_pct'], NaN));
  return Number.isFinite(util) ? { label: 'GPU', value: `${Math.round(util)}%` } : null;
}

function cpuPill(sys) {
  const cur = sys && sys.current ? sys.current : sys || {};
  const cpu = Number(pick(cur, ['cpu_pct', 'cpu'], NaN));
  return pill('Нагрузка', { tone: 'idle', value: Number.isFinite(cpu) ? `${Math.round(cpu)}%` : '—' });
}

/* ---------------------------------------------------------------- B. командная строка */

function buildCommandBar(ctx, agents) {
  const input = h('textarea.bx-command-input', {
    rows: 1, placeholder: 'Что должен сделать BOSSMAN?', spellcheck: 'false',
  });

  // Поле растёт под текст: командная строка на одну строку выглядит как
  // поиск, а не как поручение.
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
  });

  const modeButtons = MODES.map((mode) => {
    const btn = h('button.bx-mode', {
      type: 'button', title: mode.hint,
      'aria-pressed': state.modes.has(mode.id) ? 'true' : 'false',
    }, mode.label);
    btn.addEventListener('click', () => {
      if (state.modes.has(mode.id)) state.modes.delete(mode.id);
      else state.modes.add(mode.id);
      btn.setAttribute('aria-pressed', state.modes.has(mode.id) ? 'true' : 'false');
    });
    return btn;
  });

  const start = h('button.bx-btn.bx-btn-primary.bx-btn-xl', { type: 'button' },
    h('span', 'ЗАПУСТИТЬ'), icon('chevron', 16),
    h('kbd', navigator.platform.startsWith('Mac') ? '⌘↵' : 'Ctrl↵'));

  async function submit() {
    const text = input.value.trim();
    if (!text) { toast('Опишите задачу', { type: 'warn' }); input.focus(); return; }
    start.disabled = true;
    try {
      if (await routeVideoRequest(text, attachedFiles(), ctx)) return;
    } catch (e) { toastError(e, 'Не удалось открыть видеопроект'); return; }
    finally { start.disabled = false; }
    const agent = state.agentId ?? (agents.length === 1 ? pick(agents[0], ['id']) : null);
    if (!agent) {
      toast('Выберите агента', {
        type: 'warn',
        hint: agents.length ? 'Агент задаёт модель и системный промпт.'
          : 'Сначала создайте агента на странице «Агенты».',
      });
      ctx.navigate('agents');
      return;
    }
    start.classList.add('is-loading');
    try {
      const first = text.split('\n')[0].trim();
      await api.createTask({
        title: first.length > 80 ? `${first.slice(0, 77)}…` : first || 'Задача',
        prompt: text, agent_id: agent, priority: 5,
        run_now: state.modes.has('auto') || state.modes.has('smart'),
      });
      input.value = '';
      input.style.height = 'auto';
      toastOk('Задача поставлена в очередь');
      ctx.refresh();
    } catch (e) {
      toastError(e, 'Не удалось создать задачу');
    } finally {
      start.classList.remove('is-loading');
    }
  }

  start.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
  });

  return h('section.bx-command',
    h('div.bx-command-mark', icon('bolt', 22)),
    h('div.bx-command-mid', input, attachmentInput(), h('div.bx-modes', modeButtons)),
    start);
}

/* ---------------------------------------------------------------- C. приложения */

function buildApps(apps, appsR, ctx) {
  if (appsR.status === 'rejected') {
    return panel('Мои приложения',
      h('div.bx-empty',
        h('div', 'Список приложений не загрузился.'),
        h('button.bx-btn.bx-btn-subtle.bx-btn-sm',
          { type: 'button', onClick: () => ctx.refresh() }, 'Повторить')));
  }
  if (!apps.length) {
    return panel('Мои приложения',
      h('div.bx-empty',
        h('div', 'Приложений пока нет.'),
        h('div', { style: { fontSize: '12px' } },
          'Карточка появляется здесь, когда в apps/<имя>/app.manifest.yaml есть блок ui:.')));
  }

  return h('section.bx-panel',
    h('div.bx-panel-head',
      icon('empty', 14), h('h2', 'Мои приложения'), h('div.bx-spacer'),
      h('button.bx-btn.bx-btn-ghost.bx-btn-sm',
        { type: 'button', onClick: () => ctx.navigate('apps') },
        h('span', 'Все приложения'), icon('chevron', 13))),
    h('div.bx-panel-body',
      h('div.bx-apps-grid', apps.map((app) => appCard(app, ctx)))));
}

/* ---------------------------------------------------------------- D. сейчас идёт */

function buildActiveNow(missions, approvals, ctx) {
  const active = missions.filter((m) => ['running', 'planning'].includes(m.status));
  const body = h('div',
    active.length
      ? active.slice(0, 3).map((m) => line(
        pick(m, ['title'], `Миссия #${pick(m, ['id'])}`),
        null, `${Math.round(Number(m.progress || 0) * 100)}%`,
        bar(Number(m.progress || 0) * 100, 100)))
      : h('div.bx-empty', h('div', 'Активных миссий нет.')),
    approvals.length
      ? h('div', { style: { marginTop: '14px' } },
        h('div.bx-line',
          h('div.bx-line-name', 'Ждут вашего решения'),
          h('span.bx-line-val',
            h('span.bx-metric.is-warn', String(approvals.length)))),
        h('button.bx-btn.bx-btn-secondary.bx-btn-sm.bx-btn-block',
          { type: 'button', style: { marginTop: '10px' },
            onClick: () => ctx.navigate('approvals') }, 'Открыть очередь'))
      : null);

  return panel('Сейчас идёт', body,
    h('button.bx-btn.bx-btn-ghost.bx-btn-sm',
      { type: 'button', onClick: () => ctx.navigate('missions') }, 'Миссии'));
}

/* ---------------------------------------------------------------- E. агенты */

function buildAgents(agents, graph, ctx) {
  const statusById = new Map((graph.nodes || [])
    .filter((n) => String(n.id).startsWith('agent:'))
    .map((n) => [String(n.id).slice(6), n]));

  const body = agents.length
    ? h('div', agents.slice(0, 4).map((a) => {
      const node = statusById.get(String(pick(a, ['id']))) || {};
      const status = node.status || 'idle';
      return line(pick(a, ['name'], 'без имени'),
        node.note || (status === 'working' ? 'выполняет задачу' : 'свободен'),
        null,
        pill(status === 'working' ? 'работает' : 'ожидает',
          { tone: status === 'working' ? 'ok' : 'idle', live: status === 'working' }));
    }))
    : h('div.bx-empty', h('div', 'Агентов пока нет.'),
      h('button.bx-btn.bx-btn-subtle.bx-btn-sm',
        { type: 'button', onClick: () => ctx.navigate('agents') }, 'Создать агента'));

  return panel('Агенты', body,
    h('button.bx-btn.bx-btn-ghost.bx-btn-sm',
      { type: 'button', onClick: () => ctx.navigate('agents') }, 'Все'));
}

/* ---------------------------------------------------------------- F. вычисления */

function buildCompute(sys, resources, ctx) {
  const cur = sys && sys.current ? sys.current : sys || {};
  const cpu = Number(pick(cur, ['cpu_pct', 'cpu'], NaN));
  const ramUsed = resources ? resources.used_mb : pick(cur, ['ram_used_mb'], null);
  const ramTotal = resources ? resources.total_mb : pick(cur, ['ram_total_mb'], null);
  const disk = { used: pick(cur, ['disk_used_gb'], null), total: pick(cur, ['disk_total_gb'], null) };
  const gpu = firstGpu(sys);

  return panel('Вычисления', h('div',
    gpu ? line('GPU', null, gpu.value, bar(parseFloat(gpu.value) || 0, 100, 'var(--bx-mint)')) : null,
    Number.isFinite(cpu)
      ? line('CPU', null, `${Math.round(cpu)}%`, bar(cpu, 100, 'var(--bx-azure)'))
      : line('CPU', null, '—'),
    ramTotal
      ? line('RAM', null, `${fmtGb(ramUsed)} / ${fmtGb(ramTotal)} ГиБ`,
        bar(ramUsed || 0, ramTotal, 'var(--bx-violet)'))
      : line('RAM', null, '—'),
    disk.total
      ? line('Диск', null, `${Math.round((disk.used / disk.total) * 100)}%`,
        bar(disk.used, disk.total, 'var(--bx-amber)'))
      : null),
  h('button.bx-btn.bx-btn-ghost.bx-btn-sm',
    { type: 'button', onClick: () => ctx.navigate('resources') }, 'Ресурсы'));
}

/* ---------------------------------------------------------------- G. здоровье */

function buildHealth(sys, models, ctx) {
  const raw = sys && (sys.health || sys.components || sys.checks) || {};
  const items = Array.isArray(raw)
    ? raw.map((c) => ({ name: pick(c, ['name', 'component', 'id'], '—'),
      status: pick(c, ['status', 'state'], 'unknown') }))
    : Object.entries(raw).map(([name, v]) => ({
      name,
      status: v && typeof v === 'object' ? pick(v, ['status', 'state'], 'unknown')
        : typeof v === 'boolean' ? (v ? 'ok' : 'down') : String(v),
    }));

  const online = models.filter((m) => String(m.status) === 'online').length;

  const body = h('div',
    line('Моделей на связи', null, `${online} из ${models.length}`),
    items.length
      ? items.slice(0, 4).map((c) => {
        const st = statusText(c.status);
        return line(HEALTH_NAME[c.name] || c.name, null, null,
          pill(st.word, { tone: st.tone }));
      })
      : h('div.bx-empty', { style: { marginTop: '8px' } },
        h('div', 'Сервер пока не прислал состояние частей системы.')));

  return panel('Состояние системы', body,
    h('button.bx-btn.bx-btn-ghost.bx-btn-sm',
      { type: 'button', onClick: () => ctx.navigate('system') }, 'Подробно'));
}

/* ---------------------------------------------------------------- H. лента */

function buildActivity(activity, ctx) {
  const meaningful = activity.filter((e) => !RAW_LOG_KINDS.has(pick(e, ['kind', 'type'], '')));
  if (!meaningful.length) {
    return panel('Последние события',
      h('div.bx-empty', h('div', 'Заметных событий пока нет.')));
  }

  return h('section.bx-panel',
    h('div.bx-panel-head', icon('activity', 14), h('h2', 'Последние события'),
      h('div.bx-spacer')),
    h('div.bx-panel-body',
      h('div.bx-feed', meaningful.slice(0, 6).map(feedItem))));
}

// Событие приходит как «agent.created» — техническая метка. Owner видит
// человеческую фразу, а сырой kind остаётся в подсказке для отладки.
const EVENT_LABEL = {
  'agent.created': 'Создан агент', 'agent.updated': 'Изменён агент', 'agent.deleted': 'Удалён агент',
  'model.created': 'Добавлена модель', 'model.status': 'Модель сменила состояние',
  'model.degraded': 'Модель отвечает с ошибками',
  'provider.created': 'Добавлен поставщик моделей',
  'mission.created': 'Создана миссия', 'mission.started': 'Миссия запущена',
  'mission.completed': 'Миссия завершена', 'mission.stopped': 'Миссия остановлена',
  'task.created': 'Поставлена задача', 'task.started': 'Задача пошла в работу',
  'task.completed': 'Задача выполнена', 'task.failed': 'Задача завершилась ошибкой',
  'approval.created': 'Ждёт вашего решения', 'approval.decided': 'Решение принято',
  'governor.intervention': 'Сработал присмотр за агентами',
  'session.forked': 'Создано ответвление',
};

function humanKind(kind) {
  if (EVENT_LABEL[kind]) return EVENT_LABEL[kind];
  const head = kind.split('.')[0];
  const byHead = {
    agent: 'Событие агента', model: 'Событие модели', mission: 'Событие миссии',
    task: 'Событие задачи', approval: 'Подтверждение', resource: 'Память и ресурсы',
    recovery: 'Восстановление', governor: 'Присмотр',
  };
  return byHead[head] || 'Событие';
}

function feedItem(e) {
  const kind = String(pick(e, ['kind', 'type'], 'event'));
  const data = e.data && typeof e.data === 'object' ? e.data : {};
  const text = pick(e, ['message', 'text', 'title'])
    || pick(data, ['message', 'title', 'prompt', 'reason']) || '';
  const glyph = kind.startsWith('approval') ? 'approvals'
    : kind.startsWith('agent') ? 'agents'
      : kind.startsWith('model') ? 'models'
        : kind.startsWith('mission') || kind.startsWith('task') ? 'tasks' : 'activity';

  return h('div.bx-feed-item',
    h('span.bx-feed-icon', icon(glyph, 15)),
    h('div.bx-feed-text',
      h('div.bx-feed-title', { title: kind }, humanKind(kind)),
      text ? h('div.bx-feed-note', { title: String(text) }, String(text)) : null,
      h('div.bx-feed-time', fmtRelative(pick(e, ['ts', 'created_at'])))));
}

export default HomePage;
export { appIcon };

/* ---------------------------------------------------------------- A2. «Нужно ваше внимание»

   Владелец не должен обходить 29 страниц, чтобы понять, где он нужен.
   Сюда попадает ТОЛЬКО то, что действительно ждёт его решения или сломано:
   очередь подтверждений, упавшие задачи и миссии, отказавшая подсистема,
   недоступная модель. Ничего «на всякий случай» — иначе блок перестанут
   читать в первый же день.                                              */

const ATTENTION_TONE = { block: 'err', wait: 'warn', warn: 'warn' };

function attentionRow(item, ctx) {
  return h('button.bx-attn-row', {
    type: 'button',
    'data-kind': item.kind,
    'data-severity': item.severity,
    onClick: () => ctx.navigate(item.page, item.params || null),
  },
    h('span', { class: `bx-attn-dot is-${ATTENTION_TONE[item.severity] || 'warn'}` }),
    h('span.bx-attn-main',
      h('span.bx-attn-title', item.title),
      item.note ? h('span.bx-attn-note', item.note) : null),
    item.count > 1 ? h('span.bx-attn-count', String(item.count)) : null,
    h('span.bx-attn-go', 'Открыть →'));
}

/** Собирает список только из реального состояния (никаких выдуманных строк). */
export function collectAttention({ approvals = [], failedTasks = [], missions = [], sys = null,
                                   models = [], liveTasks = [] } = {}) {
  const items = [];

  if (approvals.length) {
    const first = approvals[0] || {};
    items.push({
      kind: 'approval', severity: 'wait', page: 'approvals', count: approvals.length,
      title: approvals.length === 1 ? 'Одно решение ждёт вас' : `${approvals.length} решения ждут вас`,
      note: pick(first, ['title', 'action', 'tool'], '') || 'очередь подтверждений',
    });
  }

  const waiting = liveTasks.filter((t) => String(pick(t, ['status'], '')) === 'waiting_approval');
  if (waiting.length && !approvals.length) {
    items.push({
      kind: 'task-wait', severity: 'wait', page: 'tasks', count: waiting.length,
      title: `Задача ждёт вашего решения`,
      note: pick(waiting[0], ['title'], `#${pick(waiting[0], ['id'])}`),
    });
  }

  if (failedTasks.length) {
    items.push({
      kind: 'task-failed', severity: 'block', page: 'tasks', count: failedTasks.length,
      title: failedTasks.length === 1 ? 'Задача завершилась ошибкой' : `${failedTasks.length} задач с ошибкой`,
      note: pick(failedTasks[0], ['title'], '')
        || (failedTasks[0] && failedTasks[0].last_run && failedTasks[0].last_run.error) || 'откройте задачи',
    });
  }

  const badMissions = missions.filter((m) => ['failed', 'blocked', 'error'].includes(String(pick(m, ['status'], ''))));
  if (badMissions.length) {
    items.push({
      kind: 'mission-failed', severity: 'block', page: 'missions', count: badMissions.length,
      title: badMissions.length === 1 ? 'Миссия остановлена' : `${badMissions.length} миссии остановлены`,
      note: pick(badMissions[0], ['title'], `#${pick(badMissions[0], ['id'])}`),
    });
  }

  const health = (sys && sys.health && typeof sys.health === 'object') ? sys.health : {};
  for (const [name, value] of Object.entries(health)) {
    const status = typeof value === 'string' ? value : (value && (value.status || value.state)) || '';
    if (['error', 'fail', 'failed', 'down', 'critical'].includes(String(status).toLowerCase())) {
      items.push({
        kind: 'health', severity: 'block', page: 'system', count: 1,
        title: `Сбой: ${HEALTH_NAME[name] || name}`,
        note: typeof value === 'object' && value && value.detail ? String(value.detail) : 'подсистема не отвечает',
      });
    }
  }

  const brokenModels = models.filter((m) => ['error', 'offline', 'unavailable']
    .includes(String(pick(m, ['status'], '')).toLowerCase()));
  if (brokenModels.length) {
    items.push({
      kind: 'model', severity: 'warn', page: 'models', count: brokenModels.length,
      title: brokenModels.length === 1 ? 'Модель недоступна' : `${brokenModels.length} моделей недоступны`,
      note: pick(brokenModels[0], ['display_name', 'name', 'remote_id'], ''),
    });
  }

  const order = { block: 0, wait: 1, warn: 2 };
  return items.sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
}

function buildAttention(data, ctx) {
  const items = collectAttention(data);
  if (!items.length) {
    return h('section.bx-panel.bx-attn.is-calm', { id: 'attention' },
      h('div.bx-panel-body.bx-attn-calm',
        icon('check', 16),
        h('span', 'Ничего не ждёт вашего решения.')));
  }
  return h('section.bx-panel.bx-attn', { id: 'attention', 'data-count': String(items.length) },
    h('div.bx-panel-head', h('h2', 'Нужно ваше внимание'), h('div.bx-spacer'),
      h('span.bx-attn-total', String(items.length))),
    h('div.bx-panel-body.bx-attn-list', items.map((i) => attentionRow(i, ctx))));
}

/* ---------------------------------------------------------------- A3. «Сейчас в работе»

   Компактное состояние текущей работы: что за миссия/задача, фаза, чем
   занята прямо сейчас, какая модель, сколько идёт, что проверено, чего
   ждёт. Всё — из уже существующих API и общей шины событий; своей базы
   состояния здесь нет.                                                  */

const NOW_STATUS = {
  running: 'выполняется', queued: 'в очереди', paused: 'на паузе',
  waiting_approval: 'ждёт вашего решения', planning: 'планирует',
};

function nowFacts(task, activity) {
  const id = pick(task, ['id']);
  const runId = pick(task, ['run_id', 'runId'], null);
  const mine = (activity || []).filter((e) => {
    const t = e && (e.task_id ?? e.data?.task_id);
    const r = e && (e.run_id ?? e.data?.run_id);
    return (t != null && String(t) === String(id)) || (runId != null && r != null && String(r) === String(runId));
  });
  const last = (kinds) => mine.find((e) => kinds.includes(String(e.kind || '')));
  const tool = last(['tool.called', 'tool.denied']);
  const evaluation = last(['evaluation.completed']);
  const fallback = last(['router.fallback']);
  return {
    tool: tool ? (tool.tool || tool.data?.tool || '') : '',
    verified: evaluation ? (evaluation.verdict || evaluation.data?.verdict || evaluation.result || '') : '',
    fallback: !!fallback,
  };
}

function buildNow({ liveTasks = [], missions = [], activity = [] }, ctx) {
  const rank = { running: 0, waiting_approval: 1, paused: 2, queued: 3 };
  const task = [...liveTasks].sort((a, b) =>
    (rank[String(pick(a, ['status'], ''))] ?? 9) - (rank[String(pick(b, ['status'], ''))] ?? 9))[0];
  const mission = missions.find((m) => ['running', 'planning'].includes(String(pick(m, ['status'], ''))));

  if (!task && !mission) {
    return h('section.bx-panel.bx-now', { id: 'now-card' },
      h('div.bx-panel-head', h('h2', 'Сейчас в работе'), h('div.bx-spacer')),
      h('div.bx-panel-body', h('div.bx-empty', h('div', 'Сейчас ничего не выполняется.'))));
  }

  const status = String(pick(task || mission, ['status'], 'running'));
  const run = (task && task.last_run && typeof task.last_run === 'object') ? task.last_run : {};
  const started = run.started_at || pick(task || mission, ['started_at', 'created_at'], null);
  const since = started ? Date.parse(String(started).endsWith('Z') || String(started).includes('+')
    ? started : `${started}Z`) : NaN;
  const facts = task ? nowFacts(task, activity) : { tool: '', verified: '', fallback: false };
  const title = pick(task || mission, ['title'], null)
    || (task ? `Задача #${pick(task, ['id'])}` : `Миссия #${pick(mission, ['id'])}`);

  const cell = (label, value) => h('div.bx-now-cell',
    h('span.bx-now-label', label), h('span.bx-now-value', value || '—'));

  const elapsed = h('span.bx-now-value.bx-now-elapsed',
    Number.isFinite(since) ? { 'data-since': String(since) } : null,
    Number.isFinite(since) ? humanElapsed(Date.now() - since) : '—');

  const node = h('section.bx-panel.bx-now', { id: 'now-card', 'data-status': status },
    h('div.bx-panel-head', h('h2', 'Сейчас в работе'), h('div.bx-spacer'),
      h('button.bx-btn.bx-btn-ghost.bx-btn-sm', { type: 'button', id: 'now-open-think',
        title: 'Показать процесс работы (Ctrl+.)',
        onClick: () => (window.__bxThinking ? window.__bxThinking.open() : ctx.navigate('tasks')) },
        'Процесс работы')),
    h('div.bx-panel-body',
      h('div.bx-now-title', title),
      h('div.bx-now-grid',
        cell('состояние', NOW_STATUS[status] || status),
        h('div.bx-now-cell', h('span.bx-now-label', 'идёт'), elapsed),
        cell('модель', run.model_alias || pick(task || mission, ['model', 'model_id'], '')),
        cell('шаг', (() => {
          const step = (run.checkpoint && run.checkpoint.step) ?? pick(task || mission, ['step'], null);
          const max = pick(task || mission, ['max_steps'], null);
          return step ? (max != null ? `${step} из ${max}` : String(step)) : '';
        })()),
        cell('инструмент', facts.tool),
        cell('проверено', facts.verified)),
      status === 'waiting_approval'
        ? h('div.bx-now-wait', icon('approvals', 14),
          h('span', 'Ждёт вашего решения'),
          h('button.bx-btn.bx-btn-primary.bx-btn-sm',
            { type: 'button', onClick: () => ctx.navigate('approvals') }, 'Решить'))
        : null,
      facts.fallback ? h('div.bx-now-note', 'Была смена модели (запасной маршрут).') : null));

  startElapsedTicker(node);
  return node;
}

function humanElapsed(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} мин ${String(s % 60).padStart(2, '0')} с`;
  return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} мин`;
}

/** Таймер живёт ровно столько, сколько его карточка в документе. */
function startElapsedTicker(root) {
  const id = setInterval(() => {
    if (!root.isConnected) { clearInterval(id); return; }
    for (const node of root.querySelectorAll('.bx-now-elapsed[data-since]')) {
      node.textContent = humanElapsed(Date.now() - Number(node.dataset.since));
    }
  }, 1000);
}
