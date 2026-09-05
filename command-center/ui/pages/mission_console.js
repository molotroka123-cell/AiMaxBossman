/* ============================================================
   mission_console.js — «Операторский канал».

   Экран для человека, который ведёт миссию: слева сверху — что за
   миссия и каким маршрутом моделей она идёт, в центре — лента, где
   каждое сообщение системы это карточка своего вида (план, ход
   проверки, запрос решения, маршрут), справа — граф задач, агенты,
   локальная модель и расход, снизу — командная строка.

   Главное требование проекта, из которого выведено всё остальное:
   НИ ОДНОЙ ВЫДУМАННОЙ ЦИФРЫ. Поэтому каждое значение на экране
   проходит через val(): либо оно пришло с сервера и несёт data-src
   с адресом ручки-источника, либо честно написано «нет данных».
   Ноль вместо неизвестного, «—» вместо пропуска и правдоподобная
   заглушка запрещены: их нельзя отличить от настоящего измерения.

   Второе требование: «ход мыслей» здесь — это факты исполнения
   (какой шаг, сколько заняло, какая модель, сколько токенов, что
   проверено), взятые из журнала прогона. Сырых рассуждений модели
   на этом экране нет и быть не может — сервер их наружу не отдаёт.

   Данные: GET /api/missions, /api/tasks, /api/models, /api/agents,
   /api/approvals, /api/system, /api/spend, /api/runs/{id}/events.
   Решения: POST /api/approvals/{id}. Команда: POST /api/tasks.
   Живая лента — общая шина событий (EventStream из ui/api.js).
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, icon, toastOk, toastError,
  fmtDuration, fmtClock, fmtTokens, fmtContext, fmtNum, fmtGb, parseTs,
} from '../components.js';
import { statusText } from './_ui.js';

const CSS_ID = 'bcc-console2030-css';
const NO_DATA = 'нет данных';

/* Адреса-источники: подпись data-src должна совпадать с тем, откуда
   значение реально взято, иначе паспорт цифры превращается в декорацию. */
const SRC = {
  missions: '/api/missions',
  tasks: '/api/tasks',
  models: '/api/models',
  agents: '/api/agents',
  approvals: '/api/approvals',
  system: '/api/system',
  spend: '/api/spend',
  runEvents: (id) => `/api/runs/${id}/events`,
};

/* Состояние самого экрана (не данные): что оператор развернул руками.
   Живёт в модуле, потому что оболочка перерисовывает страницу целиком
   на каждом событии шины, и складывать это в DOM бессмысленно. */
const ui = { verifyOpen: false, draft: '', executorId: '' };

/* ============================================================ стили */

/* Свой CSS подключаем один раз и ДОЖИДАЕМСЯ его: если вставить разметку
   раньше стилей, первый кадр окажется без сетки — на телефоне это видно
   как мгновенная горизонтальная прокрутка. */
function ensureCss() {
  if (document.getElementById(CSS_ID)) return Promise.resolve();
  return new Promise((resolve) => {
    const link = document.createElement('link');
    link.id = CSS_ID;
    link.rel = 'stylesheet';
    link.href = 'console2030.css';
    link.addEventListener('load', resolve, { once: true });
    // Без стилей страница обязана открыться всё равно: отказ CSS — не повод
    // показывать владельцу пустой экран.
    link.addEventListener('error', resolve, { once: true });
    document.head.appendChild(link);
  });
}

/* ============================================================ живой поток */

/* Искрографик агента — это НЕ красивая синусоида, а число событий его
   задач по окнам живой ленты. Поэтому буфер один на приложение и его
   заполняет подписка на ту же шину, что кормит остальной дашборд. */
const PULSE_BUCKET_MS = 5000;
const PULSE_WINDOW = 16;
const pulse = { on: false, byTask: new Map() };

function bucketOf(ms) { return Math.floor(ms / PULSE_BUCKET_MS); }

function ensurePulse(bus) {
  if (pulse.on || !bus || typeof bus.subscribe !== 'function') return;
  pulse.on = true;                       // одна подписка на всё время жизни вкладки
  bus.subscribe((ev) => {
    const kind = String(ev && ev.kind ? ev.kind : '');
    if (!kind || kind.startsWith('ws.') || kind === 'system.metrics' || kind === 'hello') return;
    const taskId = ev.task_id;
    if (taskId === null || taskId === undefined) return;
    const key = String(taskId);
    let series = pulse.byTask.get(key);
    if (!series) { series = new Map(); pulse.byTask.set(key, series); }
    const now = bucketOf(Date.now());
    series.set(now, (series.get(now) || 0) + 1);
    for (const b of series.keys()) if (b < now - PULSE_WINDOW) series.delete(b);
    // Буфер обязан быть ограничен: за сутки работы иначе накапливается
    // ряд на каждую когда-либо виденную задачу.
    if (pulse.byTask.size > 200) pulse.byTask.delete(pulse.byTask.keys().next().value);
  });
}

/** Ряд значений для искрографика или null, если событий ещё не было. */
function pulseSeries(taskIds) {
  const now = bucketOf(Date.now());
  const out = [];
  let total = 0;
  for (let i = PULSE_WINDOW - 1; i >= 0; i -= 1) {
    let sum = 0;
    for (const id of taskIds) {
      const series = pulse.byTask.get(String(id));
      if (series) sum += series.get(now - i) || 0;
    }
    out.push(sum);
    total += sum;
  }
  return total > 0 ? out : null;
}

/* ============================================================ живой счётчик времени */

/* Один таймер на приложение обновляет все элементы с data-mc-since.
   Заводить его на каждую перерисовку — верный способ получить сотню
   таймеров к концу смены. */
let tickerOn = false;
function ensureTicker() {
  if (tickerOn) return;
  tickerOn = true;
  setInterval(() => {
    for (const node of document.querySelectorAll('[data-mc-since]')) {
      const since = Number(node.dataset.mcSince);
      if (Number.isFinite(since)) node.textContent = fmtDuration(Date.now() - since);
    }
  }, 1000);
}

/* ============================================================ честные значения */

/**
 * Значение с паспортом источника.
 * @param {string|null} text  готовая к показу строка или null, если данных нет
 * @param {string} src        адрес ручки, откуда значение пришло
 */
function val(text, src, { mono = false, accent = false, big = false, since = null, title } = {}) {
  const known = text !== null && text !== undefined && text !== '' && Boolean(src);
  const cls = 'mc-val'
    + (known ? '' : ' is-nodata')
    + (mono ? ' is-mono' : '')
    + (accent && known ? ' is-accent' : '')
    + (big ? ' is-big' : '');
  const node = h('span', {
    class: cls,
    'data-src': known ? src : null,
    title: known ? (title || `источник: ${src}`) : 'сервер не дал этих данных',
  }, known ? String(text) : NO_DATA);
  // Счётчик «идёт столько-то» обновляется тикером, не перерисовкой страницы.
  if (known && since) node.dataset.mcSince = String(since);
  return node;
}

/** Подпись + значение — основной кирпич всех панелей. */
function cell(label, node, { cls = 'mc-cell' } = {}) {
  return h('div', { class: cls }, h('span.mc-label', label), node);
}

function mcPill(text, { live = false, alert = false, title } = {}) {
  return h('span', {
    class: 'mc-pill' + (live ? ' is-live' : '') + (alert ? ' is-alert' : ''),
    title: title || null,
  }, h('span.mc-pill-dot'), h('span', text));
}

function bar(fraction, { split = null } = {}) {
  if (fraction === null || fraction === undefined || !Number.isFinite(Number(fraction))) {
    return h('span.mc-bar.is-nodata', { 'aria-hidden': 'true' });
  }
  if (split) {
    const a = Math.max(0, Math.min(100, split * 100));
    return h('span.mc-bar.is-split', { 'aria-hidden': 'true' },
      h('i', { style: { width: `${a}%` } }), h('i', { style: { width: `${100 - a}%` } }));
  }
  const w = Math.max(0, Math.min(100, Number(fraction) * 100));
  return h('span.mc-bar', { 'aria-hidden': 'true' }, h('i', { style: { width: `${w}%` } }));
}

function spark(values) {
  const w = 72;
  const hh = 26;
  const svg = h('svg.mc-spark', {
    viewBox: `0 0 ${w} ${hh}`, preserveAspectRatio: 'none', 'aria-hidden': 'true',
  });
  if (!values || values.length < 2) {
    svg.appendChild(h('line', { class: 'mc-spark-base', x1: 0, y1: hh - 1, x2: w, y2: hh - 1 }));
    return svg;
  }
  const hi = Math.max(...values, 1);
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = hh - 2 - (v / hi) * (hh - 5);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  svg.appendChild(h('polygon', { class: 'mc-spark-area', points: `0,${hh} ${pts.join(' ')} ${w},${hh}` }));
  svg.appendChild(h('polyline', { class: 'mc-spark-line', points: pts.join(' ') }));
  return svg;
}

/* ============================================================ разбор данных */

const settled = (r) => (r && r.status === 'fulfilled' ? r.value : null);

/** Миссия, которую ведёт оператор: сначала живая, потом самая свежая. */
function pickMission(missions) {
  const order = ['running', 'planning', 'queued', 'paused'];
  for (const status of order) {
    const found = missions.find((m) => String(m.status) === status);
    if (found) return found;
  }
  return missions[0] || null;
}

/** Задачи в поле зрения: миссии — её задачи, иначе последние задачи системы.
    Сервер отдаёт список от новых к старым; план и граф читаются сверху вниз,
    поэтому порядок восстанавливаем по возрастанию id — это порядок исполнения. */
function scopeTasks(tasks, mission) {
  const scoped = mission
    ? tasks.filter((t) => Number(t.mission_id) === Number(mission.id))
    : tasks.slice(0, 8);
  return scoped.slice().sort((a, b) => Number(a.id) - Number(b.id));
}

/** Задача, за которой сейчас следят: та, что в работе; иначе последняя с прогоном. */
function pickCurrent(tasks) {
  const live = ['running', 'waiting_approval', 'queued', 'paused'];
  for (const status of live) {
    const found = tasks.find((t) => String(t.status) === status);
    if (found) return found;
  }
  return tasks.find((t) => t.last_run) || tasks[0] || null;
}

function runOf(task) {
  return task && task.last_run && typeof task.last_run === 'object' ? task.last_run : null;
}

function msBetween(from, to) {
  const a = parseTs(from);
  if (!a) return null;
  const b = parseTs(to) || new Date();
  const ms = b.getTime() - a.getTime();
  return ms >= 0 ? ms : null;
}

/** Первая карта GPU из метрик или null. VRAM без карты — не ноль, а неизвестность. */
function gpuOf(system) {
  const metrics = system && system.metrics && typeof system.metrics === 'object' ? system.metrics : null;
  const list = metrics && Array.isArray(metrics.gpu) ? metrics.gpu : null;
  return list && list.length ? list[0] : null;
}

function modelByAlias(models, alias) {
  if (!alias) return null;
  return models.find((m) => String(m.alias) === String(alias)) || null;
}

/* ============================================================ страница */

const MissionConsolePage = {
  id: 'mission_console',
  title: 'Операторский канал',
  icon: 'activity',
  nav: 'primary',
  // Это рабочий экран владельца, а не системная утилита: место ему в
  // «Основном», рядом с миссиями (см. sectionOf в ui/app.js).
  section: 'main',

  async render(ctx) {
    await ensureCss();
    ensureTicker();
    ensurePulse(ctx && ctx.bus);

    /* allSettled, а не all: недоступная ручка обязана превратиться в «нет
       данных» в своей клетке, а не обрушить весь экран оператора. */
    const [missionsR, tasksR, modelsR, agentsR, apprR, systemR, spendR] = await Promise.allSettled([
      api.raw('/api/missions'),
      api.raw('/api/tasks?limit=100'),
      api.raw('/api/models'),
      api.raw('/api/agents'),
      api.raw('/api/approvals?status=pending'),
      api.raw('/api/system'),
      api.raw('/api/spend'),
    ]);

    const missions = listOf(settled(missionsR), 'missions');
    const allTasks = listOf(settled(tasksR), 'tasks');
    const models = listOf(settled(modelsR), 'models');
    const agents = listOf(settled(agentsR), 'agents');
    const approvals = listOf(settled(apprR), 'approvals');
    const system = settled(systemR);
    const spend = settled(spendR);

    const mission = pickMission(missions);
    let tasks = scopeTasks(allTasks, mission);
    let tasksComplete = tasksR.status === 'fulfilled';
    if (mission) {
      tasks = [];
      let before = null;
      tasksComplete = false;
      try {
        for (let page = 0; page < 100; page++) {
          const url = `/api/tasks?mission_id=${encodeURIComponent(mission.id)}&limit=500`
            + (before === null ? '' : `&before_id=${before}`);
          const rows = listOf(await api.raw(url), 'tasks');
          tasks.push(...rows);
          if (rows.length < 500) { tasksComplete = true; break; }
          const next = Math.min(...rows.map(t => Number(t.id)));
          if (!Number.isFinite(next) || (before !== null && next >= before)) break;
          before = next;
        }
        tasks = scopeTasks(tasks, mission);
      } catch { tasksComplete = false; }
    }
    const current = pickCurrent(tasks);
    const run = runOf(current);

    /* Журнал прогона тянем только когда прогон есть: запрос ради пустого
       ответа — лишняя строка в журнале сервера и лишняя секунда ожидания. */
    let runEvents = [];
    if (run && run.id !== null && run.id !== undefined) {
      try { runEvents = listOf(await api.raw(SRC.runEvents(run.id)), 'events'); }
      catch { runEvents = []; }
    }

    const data = { mission, missions, tasks, allTasks, current, run, runEvents,
                   models, agents, approvals, system, spend };

    return h('div.mc2030', { 'data-page': 'mission_console' },
      headerBlock(data),
      !tasksComplete ? h('p', { role: 'status' }, 'Список задач загружен не полностью. Обновите страницу.') : null,
      h('div.mc-body', feedBlock(data, ctx), railBlock(data)),
      commandBlock(data, ctx));
  },

  /* Перерисовываемся только на том, что меняет содержимое этого экрана.
     Оболочка склеивает вызовы debounce'ом, поэтому взрывной поток событий
     не превращается в взрывной поток запросов. */
  onEvent(ev) {
    const kind = String(ev && ev.kind ? ev.kind : '');
    return kind.startsWith('mission.') || kind.startsWith('task.') || kind.startsWith('approval.')
      || kind.startsWith('tool.') || kind.startsWith('router.') || kind.startsWith('resource.')
      || kind === 'run.log' || kind === 'model.status' || kind === 'evaluation.completed';
  },
};

/* ---------------------------------------------------------------- шапка миссии */

function headerBlock({ mission, current, run, models, system }) {
  const status = mission ? statusText(mission.status) : null;
  /* Миссия могла быть ещё не запущена. Тогда честнее считать от создания и
     так и подписать: «с начала» у непущенной миссии — маленькая ложь. */
  const startedAt = mission ? (mission.started_at || null) : null;
  const startedMs = mission ? parseTs(startedAt || mission.created_at) : null;
  const sinceLabel = startedAt ? 'с начала' : 'с создания';

  const title = mission ? String(mission.title || 'Миссия без названия') : 'Миссия не выбрана';
  const goal = mission && mission.goal
    ? String(mission.goal)
    : 'Цель появится здесь, как только миссия будет поставлена. Пока канал слушает систему и ждёт работы.';

  const meta = h('div.mc-head-meta',
    cell('идентификатор', val(mission ? `M-${mission.id}` : null, SRC.missions, { mono: true })),
    cell('состояние', mission
      ? mcPill(status.word, { live: Boolean(status.live) })
      : val(null, null)),
    cell(sinceLabel, val(
      mission && startedMs ? fmtDuration(Date.now() - startedMs.getTime()) : null,
      SRC.missions,
      { since: mission && startedMs ? startedMs.getTime() : null })),
    cell('задач в миссии', val(
      mission && Array.isArray(mission.plan && mission.plan.tasks) ? String(mission.plan.tasks.length) : null,
      SRC.missions)));

  return h('header.mc-head',
    h('div.mc-head-top',
      h('div.mc-head-main',
        h('div.mc-label', 'операторский канал'),
        /* Источник объявляем только когда миссия есть: подписывать источником
           собственную заглушку — то же враньё, что и подставная цифра. */
        h('h1.mc-title', { 'data-src': mission ? SRC.missions : null }, title),
        h('p.mc-goal', { 'data-src': mission && mission.goal ? SRC.missions : null }, goal)),
      meta),
    routeLine({ current, run, models, system }));
}

/** Маршрут моделей: плашки реестра, активная — та, что отвечала в прогоне. */
function routeLine({ run, models }) {
  const activeAlias = run && run.model_alias ? String(run.model_alias) : null;
  const line = h('div.mc-route-line', h('span.mc-label', 'маршрут моделей'));

  if (!models.length) {
    line.appendChild(val(null, null));
    line.appendChild(h('span.mc-hint', 'реестр моделей пуст — маршрут строить не из чего'));
    return line;
  }

  const shown = models.slice(0, 6);
  shown.forEach((m, i) => {
    if (i > 0) line.appendChild(h('span.mc-plate-arrow', { 'aria-hidden': 'true' }, '→'));
    const isActive = activeAlias && String(m.alias) === activeAlias;
    line.appendChild(h('span', {
      class: 'mc-plate' + (isActive ? ' is-active' : ''),
      'data-src': SRC.models,        // имя модели с цифрами — тоже значение с сервера
      title: isActive ? 'на этой модели шёл последний прогон' : `модель реестра: ${m.alias}`,
    },
    h('span.mc-plate-name', String(m.alias || m.name || '')),
    h('span.mc-plate-kind', m.kind === 'cloud' ? 'облако' : 'локально')));
  });
  if (!activeAlias) line.appendChild(h('span.mc-hint', 'активная модель неизвестна: прогонов ещё не было'));
  return line;
}

/* ---------------------------------------------------------------- лента канала */

function feedBlock(data, ctx) {
  const { mission, tasks, approvals } = data;
  const feed = h('section.mc-feed', { 'aria-label': 'Лента операторского канала' });

  if (!mission && !tasks.length && !approvals.length) {
    feed.appendChild(h('div.mc-blank',
      h('div.mc-blank-title', 'Канал молчит'),
      h('p.mc-blank-text',
        'Сейчас в работе нет ни одной миссии и ни одной задачи, поэтому показывать нечего. '
        + 'Это не сбой и не пустая таблица: система просто ждёт работы.'),
      h('p.mc-blank-hint',
        'Поставьте миссию или отправьте команду в строке внизу — карточки плана, проверок '
        + 'и решений появятся здесь сами, как только по ним будут факты.')));
    return feed;
  }

  feed.appendChild(planCard(data));
  feed.appendChild(verifyCard(data, ctx));
  for (const a of approvals.slice(0, 4)) feed.appendChild(approvalCard(a, ctx));
  feed.appendChild(routeCard(data));
  return feed;
}

/* --- карточка плана --- */

const DONE = new Set(['completed', 'finished', 'done']);
const LIVE = new Set(['running', 'waiting_approval', 'queued', 'paused', 'leased']);

function planCard({ mission, tasks }) {
  /* Шаги берём из реальных строк задач, а не из JSON-плана: у задач есть
     статус, а план — это только намерение. Если задач ещё нет, честно
     показываем намерение и так его и называем. */
  const fromTasks = tasks.length > 0;
  const planTasks = mission && mission.plan && Array.isArray(mission.plan.tasks) ? mission.plan.tasks : [];
  const rows = fromTasks ? tasks : planTasks;

  if (!rows.length) {
    return h('article.mc-card', { 'data-card': 'plan' },
      h('div.mc-card-head', h('span.mc-card-title', 'План миссии')),
      h('p.mc-blank-hint', 'План ещё не разложен на шаги — сервер не вернул ни одной задачи.'));
  }

  const currentIndex = fromTasks
    ? Math.max(0, rows.findIndex((t) => !DONE.has(String(t.status))))
    : 0;
  const doneCount = fromTasks ? rows.filter((t) => DONE.has(String(t.status))).length : 0;
  const position = fromTasks
    ? (doneCount >= rows.length ? rows.length : currentIndex + 1)
    : null;

  const src = fromTasks ? SRC.tasks : SRC.missions;
  const steps = h('ol.mc-steps');
  rows.forEach((t, i) => {
    const status = fromTasks ? String(t.status || '') : '';
    const isDone = DONE.has(status);
    const isCurrent = fromTasks && i === currentIndex && !isDone;
    const word = fromTasks ? statusText(status).word : 'в плане';
    const no = val(String(i + 1), src, { mono: true, title: 'номер шага' });
    no.classList.add('mc-step-no');
    /* data-src на всей строке, а не только на номере: название шага тоже
       пришло с сервера и вполне может содержать цифры. */
    steps.appendChild(h('li', {
      class: 'mc-step' + (isDone ? ' is-done' : '') + (isCurrent ? ' is-current' : ''),
      'aria-current': isCurrent ? 'step' : null,
      'data-src': src,
    },
    no,
    h('span.mc-step-title', String(t.title || t.prompt || 'шаг без названия').slice(0, 160)),
    h('span.mc-step-state', word)));
  });

  return h('article', { class: 'mc-card' + (fromTasks ? ' is-live' : ''), 'data-card': 'plan' },
    h('div.mc-card-head',
      h('span.mc-card-title', 'План миссии'),
      h('span.mc-card-spacer'),
      h('span.mc-label', 'шаг'),
      val(position ? `${position} / ${rows.length}` : null, SRC.tasks, { mono: true, accent: true })),
    steps);
}

/* --- карточка хода проверки --- */

/* Слово состояния для строки журнала. Уровень пишет сервер, поэтому
   выдумывать «успешно» там, где его нет, не приходится. */
function eventState(ev, isLast, running) {
  const level = String(ev.level || 'info');
  if (level === 'error') return { word: 'сбой', bad: true };
  if (level === 'warn') return { word: 'с оговоркой', bad: true };
  if (isLast && running) return { word: 'в процессе', live: true };
  return { word: 'проверено', ok: true };
}

function verifyCard({ current, run, runEvents }) {
  const running = current ? LIVE.has(String(current.status)) : false;
  const rows = runEvents.slice(-8);
  const eventsSrc = run ? SRC.runEvents(run.id) : null;

  const list = h('ul.mc-sources');
  if (!rows.length) {
    list.appendChild(h('li.mc-source',
      h('span.mc-source-mark', { 'aria-hidden': 'true' }),
      h('span.mc-source-name', run
        ? 'Прогон начат, но записей проверки сервер ещё не отдал.'
        : 'Проверок не было: ни один прогон по этой задаче не запускался.'),
      h('span.mc-source-state', NO_DATA)));
  } else {
    rows.forEach((ev, i) => {
      const state = eventState(ev, i === rows.length - 1, running);
      /* Текст записи — цитата журнала сервера, поэтому источник объявлен
         на всей строке: цифры внутри неё принадлежат ему, а не нам. */
      list.appendChild(h('li', {
        class: 'mc-source' + (state.ok ? ' is-ok' : '') + (state.live ? ' is-live' : '') + (state.bad ? ' is-bad' : ''),
        'data-src': eventsSrc,
      },
      /* Метка состояния — фигура из CSS, а не символ в тексте: так она не
         попадает в озвучку скринридера и не зависит от шрифта. Само состояние
         рядом написано словом. */
      h('span.mc-source-mark', { 'aria-hidden': 'true' }),
      h('span.mc-source-name', String(ev.message || ev.kind || 'запись прогона').slice(0, 180)),
      h('span.mc-source-state', state.word)));
    });
  }

  const tokens = run && (Number(run.tokens_in) || Number(run.tokens_out))
    ? fmtTokens(Number(run.tokens_in || 0) + Number(run.tokens_out || 0))
    : null;
  const spentMs = run ? msBetween(run.started_at, run.finished_at) : null;

  const facts = h('div.mc-facts',
    cell('токенов', val(tokens, SRC.tasks, { mono: true }), { cls: 'mc-fact' }),
    cell('секунд работы', val(
      spentMs === null ? null : fmtNum(spentMs / 1000, 1), SRC.tasks, { mono: true }), { cls: 'mc-fact' }),
    cell('записей в журнале', val(
      runEvents.length ? String(runEvents.length) : null,
      eventsSrc, { mono: true }), { cls: 'mc-fact' }));

  const details = h('div.mc-details', { hidden: !ui.verifyOpen },
    h('p.mc-details-note',
      'Ниже — факты исполнения из журнала прогона: время, уровень и что произошло. '
      + 'Это не «рассуждения модели»: сырой ход её мыслей сервер наружу не отдаёт и здесь не показывается.'),
    runEvents.length
      ? runEvents.slice(-24).map((ev) => h('div.mc-detail-row', { 'data-src': eventsSrc },
        h('span.mc-detail-time', fmtClock(ev.ts, true)),
        h('span.mc-detail-text', `${ev.kind ? `${ev.kind}: ` : ''}${String(ev.message || '').slice(0, 300)}`)))
      : h('p.mc-details-note', 'Журнал прогона пуст.'));

  const toggle = h('button.mc-disclose', {
    type: 'button',
    'aria-expanded': String(ui.verifyOpen),
    title: 'Показать факты исполнения из журнала прогона',
    onClick: () => {
      ui.verifyOpen = !ui.verifyOpen;
      toggle.setAttribute('aria-expanded', String(ui.verifyOpen));
      details.hidden = !ui.verifyOpen;
      toggle.querySelector('.mc-disclose-text').textContent = discloseText();
    },
  },
  h('span.mc-disclose-caret', { 'aria-hidden': 'true' }, icon('chevron', 12)),
  h('span.mc-disclose-text', discloseText()));

  return h('article', { class: 'mc-card' + (running ? ' is-live' : ''), 'data-card': 'verify' },
    h('div.mc-card-head',
      h('span.mc-card-title', 'Ход проверки'),
      h('span.mc-card-spacer'),
      mcPill(running ? 'идёт' : 'остановлена', { live: running })),
    list, toggle, details, facts);
}

function discloseText() {
  return ui.verifyOpen ? 'Свернуть рассуждения и верификацию' : 'Развернуть рассуждения и верификацию';
}

/* --- карточка запроса подтверждения --- */

/* Что именно затрагивает решение. Ключи — те, которыми сервер заводит
   подтверждения; незнакомый вид показываем как есть, а не «прочее». */
const IMPACT = {
  terminal: 'команда в терминале этой машины',
  browser: 'действие в браузере от вашего имени',
  permissions: 'изменение прав агента',
  skill_permissions: 'права навыка',
  review_escalation: 'спорный результат передан вам',
  healing_escalation: 'самовосстановление не справилось',
  governor: 'присмотр остановил прогон',
};

function approvalCard(a, ctx) {
  const createdMs = parseTs(a.created_at);
  const kind = String(a.kind || '');

  const decide = async (approve) => {
    try {
      await api.raw(`/api/approvals/${encodeURIComponent(a.id)}`, {
        method: 'POST', body: { approve, by: 'ui' },
      });
      toastOk(approve ? 'Разрешено' : 'Отклонено', 'Решение записано, работа продолжится.');
      ctx.refresh();
    } catch (e) {
      toastError(e, 'Не удалось записать решение');
    }
  };

  return h('article.mc-card.is-decision', { 'data-card': 'approval', 'data-approval': String(a.id) },
    h('div.mc-card-head',
      h('span.mc-card-title', 'Запрос подтверждения'),
      h('span.mc-card-spacer'),
      mcPill('требует решения', { alert: true, title: 'без вашего ответа работа стоит' })),
    h('div.mc-decision-body',
      h('p.mc-decision-text', { 'data-src': SRC.approvals },
        String(a.preview || '').slice(0, 600)
        || 'Сервер не приложил описание действия — решение принимайте по виду и задаче.'),
      h('div.mc-decision-meta',
        cell('влияние', val(IMPACT[kind] || kind || null, SRC.approvals)),
        cell('задача', val(a.task_id ? `T-${a.task_id}` : null, SRC.approvals, { mono: true })),
        cell('цена ожидания', val(
          createdMs ? fmtDuration(Date.now() - createdMs.getTime()) : null,
          SRC.approvals,
          { since: createdMs ? createdMs.getTime() : null,
            title: 'столько задача стоит и не двигается' }))),
      h('div.mc-actions',
        h('button.mc-btn.is-primary', {
          type: 'button', title: 'Разрешить действие и продолжить работу',
          onClick: () => decide(true),
        }, 'Подтвердить'),
        h('button.mc-btn.is-quiet', {
          type: 'button', title: 'Запретить действие; задача останется остановленной',
          onClick: () => decide(false),
        }, 'Отклонить'))));
}

/* --- карточка маршрута --- */

function routeCard({ run, models, system }) {
  const alias = run && run.model_alias ? String(run.model_alias) : null;
  const model = modelByAlias(models, alias);
  const gpu = gpuOf(system);
  const route = run && run.route && typeof run.route === 'object' ? run.route : null;
  const reason = route && route.reasons
    ? (Array.isArray(route.reasons) ? route.reasons.join('; ') : String(route.reasons))
    : null;

  return h('article.mc-card', { 'data-card': 'route' },
    h('div.mc-card-head',
      h('span.mc-card-title', 'Маршрут прогона'),
      h('span.mc-card-spacer'),
      h('span.mc-label', 'кто отвечал'),
      val(alias, SRC.tasks, { mono: true, accent: true })),
    h('div.mc-grid-2',
      cell('температура', val(null, null, {
        title: 'сервер не отдаёт температуру прогона — показывать её было бы выдумкой',
      })),
      cell('VRAM карты', val(
        gpu && gpu.vram_used_mb !== null && gpu.vram_used_mb !== undefined && gpu.vram_total_mb
          ? `${fmtGb(gpu.vram_used_mb)} / ${fmtGb(gpu.vram_total_mb)} ГБ`
          : null,
        SRC.system, { mono: true })),
      cell('размер контекста', val(
        model && model.context_window ? fmtContext(model.context_window) : null,
        SRC.models, { mono: true })),
      cell('почему эта модель', val(reason ? reason.slice(0, 120) : null, SRC.tasks))));
}

/* ---------------------------------------------------------------- правая колонка */

function railBlock(data) {
  return h('aside.mc-rail',
    graphPanel(data),
    agentsPanel(data),
    localModelPanel(data),
    spendPanel(data));
}

/* --- граф задач --- */

function graphPanel({ tasks, mission, current }) {
  const panel = h('section.mc-panel', { 'aria-label': 'Граф задач' },
    h('div.mc-panel-head',
      icon('tasks', 14),
      h('span.mc-card-title', mission ? 'Граф задач миссии' : 'Последние задачи'),
      h('span.mc-card-spacer'),
      val(tasks.length ? String(tasks.length) : null, SRC.tasks, { mono: true })));

  if (!tasks.length) {
    panel.appendChild(h('p.mc-blank-hint', 'Задач в работе нет — цепочку строить не из чего.'));
    return panel;
  }

  const graph = h('div.mc-graph');
  for (const t of tasks.slice(0, 10)) {
    const status = String(t.status || '');
    const isCurrent = current && Number(current.id) === Number(t.id);
    const run = runOf(t);
    const ms = run ? msBetween(run.started_at, run.finished_at) : null;
    graph.appendChild(h('div', {
      class: 'mc-node'
        + (DONE.has(status) ? ' is-done' : '')
        + (isCurrent ? ' is-current' : '')
        + (status === 'failed' ? ' is-bad' : ''),
      'data-src': SRC.tasks,
    },
    h('span.mc-node-mark', { 'aria-hidden': 'true' }),
    h('div.mc-node-body',
      h('span.mc-node-title', String(t.title || `Задача ${t.id}`).slice(0, 90)),
      h('div.mc-node-meta',
        h('span', statusText(status).word),
        val(ms === null ? null : fmtDuration(ms), SRC.tasks, { mono: true })))));
  }
  panel.appendChild(graph);
  return panel;
}

/* --- агенты --- */

function agentsPanel({ agents, allTasks }) {
  const panel = h('section.mc-panel', { 'aria-label': 'Активные агенты' },
    h('div.mc-panel-head',
      icon('agents', 14),
      h('span.mc-card-title', 'Активные агенты'),
      h('span.mc-card-spacer'),
      val(agents.length ? String(agents.length) : null, SRC.agents, { mono: true })));

  const live = agents.filter((a) => a.enabled !== false);
  if (!live.length) {
    panel.appendChild(h('p.mc-blank-hint', 'Ни одного включённого агента — работать в канале некому.'));
    return panel;
  }

  for (const a of live.slice(0, 6)) {
    const taskIds = allTasks.filter((t) => Number(t.agent_id) === Number(a.id)).map((t) => t.id);
    const series = pulseSeries(taskIds);
    panel.appendChild(h('div.mc-agent', { 'data-src': SRC.agents },
      h('div',
        h('div.mc-agent-name', String(a.name || `Агент ${a.id}`)),
        h('div.mc-agent-role', String(a.role || 'роль не задана'))),
      series
        ? spark(series)
        : val(null, null, { title: 'событий по задачам этого агента в живой ленте ещё не было' })));
  }
  panel.appendChild(h('p.mc-hint',
    'Искрографик — число событий задач агента по окнам живой ленты; пока событий нет, его нет тоже.'));
  return panel;
}

/* --- локальная модель --- */

function localModelPanel({ models, run, system }) {
  const alias = run && run.model_alias ? String(run.model_alias) : null;
  const model = modelByAlias(models, alias) || models.find((m) => m.kind === 'local') || null;
  const gpu = gpuOf(system);
  const used = gpu && Number.isFinite(Number(gpu.vram_used_mb)) ? Number(gpu.vram_used_mb) : null;
  const total = gpu && Number.isFinite(Number(gpu.vram_total_mb)) ? Number(gpu.vram_total_mb) : null;

  return h('section.mc-panel', { 'aria-label': 'Локальная модель' },
    h('div.mc-panel-head',
      icon('models', 14),
      h('span.mc-card-title', 'Локальная модель'),
      h('span.mc-card-spacer'),
      model ? mcPill(statusText(model.status).word) : val(null, null)),
    h('div.mc-rows',
      h('div.mc-row', h('span.mc-label', 'имя'), h('span.mc-card-spacer'),
        val(model ? String(model.alias || model.name) : null, SRC.models, { mono: true })),
      h('div.mc-row', h('span.mc-label', 'температура'), h('span.mc-card-spacer'),
        val(null, null, { title: 'температура прогона в API не публикуется' })),
      h('div.mc-row', h('span.mc-label', 'контекст'), h('span.mc-card-spacer'),
        val(model && model.context_window ? fmtContext(model.context_window) : null, SRC.models, { mono: true })),
      h('div.mc-meter',
        h('div.mc-meter-top',
          h('span.mc-label', 'VRAM'), h('span.mc-card-spacer'),
          val(used !== null && total ? `${fmtGb(used)} / ${fmtGb(total)} ГБ` : null, SRC.system, { mono: true })),
        bar(used !== null && total ? used / total : null))));
}

/* --- расход токенов --- */

function spendPanel({ tasks, allTasks, spend }) {
  /* Считаем по прогонам, которые сервер уже посчитал: tokens_in/out и
     cost_usd лежат в task_runs. Своей «оценки стоимости» здесь нет —
     она была бы выдумкой поверх чужого учёта. */
  const source = tasks.length ? tasks : allTasks;
  const runs = source.map(runOf).filter(Boolean);

  let tin = 0; let tout = 0; let cost = 0; let ms = 0; let counted = 0;
  for (const r of runs) {
    tin += Number(r.tokens_in || 0);
    tout += Number(r.tokens_out || 0);
    const c = Number(r.cost_usd || 0);
    const d = msBetween(r.started_at, r.finished_at);
    if (c > 0) cost += c;
    if (d !== null && d > 0) { ms += d; counted += 1; }
  }
  const hasTokens = counted > 0 || tin > 0 || tout > 0;
  const perMinute = cost > 0 && ms > 0 ? cost / (ms / 60000) : null;

  const spendEnabled = spend && spend.enabled === true;
  const spendToday = spendEnabled && spend.spent && Number.isFinite(Number(spend.spent.today_usd))
    ? Number(spend.spent.today_usd) : null;

  return h('section.mc-panel', { 'aria-label': 'Расход токенов' },
    h('div.mc-panel-head',
      icon('activity', 14),
      h('span.mc-card-title', 'Расход'),
      h('span.mc-card-spacer'),
      val(spendToday === null ? null : `$${fmtNum(spendToday, 4)} за сутки`, SRC.spend, { mono: true })),
    h('div.mc-rows',
      h('div.mc-row', h('span.mc-label', 'стоимость в минуту'), h('span.mc-card-spacer'),
        val(perMinute === null ? null : `$${fmtNum(perMinute, 4)}`, SRC.tasks,
          { mono: true, title: 'стоимость прогонов, делённая на их длительность' })),
      h('div.mc-row', h('span.mc-label', 'вход'), h('span.mc-card-spacer'),
        val(hasTokens ? fmtTokens(tin) : null, SRC.tasks, { mono: true })),
      h('div.mc-row', h('span.mc-label', 'выход'), h('span.mc-card-spacer'),
        val(hasTokens ? fmtTokens(tout) : null, SRC.tasks, { mono: true })),
      h('div.mc-meter',
        h('div.mc-meter-top',
          h('span.mc-label', 'полоса активности'), h('span.mc-card-spacer'),
          val(hasTokens && (tin + tout) > 0 ? 'вход и выход' : null, SRC.tasks)),
        bar(hasTokens && (tin + tout) > 0 ? 1 : null,
          { split: hasTokens && (tin + tout) > 0 ? tin / (tin + tout) : null }))),
    spendEnabled ? null : h('p.mc-hint',
      'Учёт стоимости на сервере выключен, поэтому дневная сумма не показывается.'));
}

/* ---------------------------------------------------------------- командная строка */

/* Быстрые команды — заготовки текста, а не скрытые действия: нажатие
   подставляет фразу в строку, отправляет её оператор сам. Так на экране
   нет ни одной кнопки, которая делает что-то неожиданное. */
const QUICK = [
  'Доложи состояние миссии',
  'Продолжи план со следующего шага',
  'Проверь источники последнего вывода',
  'Собери короткий отчёт по сделанному',
  'Останови текущую задачу',
];

function commandBlock({ agents, models }, ctx) {
  const eligible = agents.filter((a) => a.enabled !== false && a.enabled !== 0
    && models.some((m) => String(m.id) === String(a.model_id) && m.enabled !== false && m.enabled !== 0));
  // The owner chooses the executor explicitly; never silently select a cloud route.
  if (!eligible.some((a) => String(a.id) === ui.executorId)) ui.executorId = '';
  const executor = h('select.mc-input', { id: 'mc-command-agent', 'aria-label': 'Исполнитель команды', 'data-src': '/api/agents + /api/models',
    onChange: (e) => { ui.executorId = e.target.value; } },
    h('option', { value: '' }, 'Выберите исполнителя — иначе черновик'),
    eligible.map((a) => h('option', { value: String(a.id), selected: String(a.id) === ui.executorId },
      `${a.name || a.id} · ${models.find((m) => String(m.id) === String(a.model_id))?.alias || a.model_id}`)));

  const input = h('input.mc-input', {
    type: 'text',
    id: 'mc-command-input',
    name: 'command',
    autocomplete: 'off',
    spellcheck: 'false',
    placeholder: 'Команда оператора: что сделать дальше',
    'aria-label': 'Команда оператора',
    value: ui.draft,
    onInput: (e) => { ui.draft = e.target.value; },
  });

  const send = h('button.mc-btn.is-primary', {
    type: 'submit',
    title: 'С выбранным исполнителем — в очередь; без исполнителя — в черновики',
  }, 'Отправить');

  const form = h('form.mc-command', {
    onSubmit: async (e) => {
      e.preventDefault();
      const text = String(input.value || '').trim();
      const selected = eligible.find((a) => String(a.id) === executor.value);
      const canRun = Boolean(selected);
      if (!text) { toastError(new Error('Команда пустая'), 'Введите команду'); return; }
      send.disabled = true;
      try {
        const res = await api.raw('/api/tasks', {
          method: 'POST',
          body: { prompt: text, title: text.slice(0, 80), agent_id: selected?.id ?? null, run_now: canRun },
        });
        const id = pick(res && res.task ? res.task : res, ['id']);
        ui.draft = '';
        input.value = '';
        const task = res?.task || res;
        if (task?.status === 'blocked' || res?.ok === false) {
          toastError(new Error(task?.meta?.blocked_reason || res?.reason || 'Нет доступного исполнителя'), 'Задача заблокирована');
        } else toastOk(canRun ? 'Команда принята' : 'Команда сохранена черновиком',
          canRun
            ? `Задача ${id ?? ''} поставлена в очередь.`
            : 'Запускать нечем: нет включённого агента или модели.');
        ctx.refresh();
      } catch (err) {
        toastError(err, 'Команда не принята');
      } finally {
        send.disabled = false;
      }
    },
  },
  executor,
  h('div.mc-command-line', input, send),
  h('div.mc-chips', { role: 'group', 'aria-label': 'Быстрые команды' },
    QUICK.map((text) => h('button.mc-chip', {
      type: 'button',
      title: 'Подставить в строку команды',
      onClick: () => { input.value = text; ui.draft = text; input.focus(); },
    }, text))),
  h('p.mc-hint', 'Выберите исполнителя для запуска. Без выбора команда сохранится черновиком.'));

  return form;
}

export default MissionConsolePage;
