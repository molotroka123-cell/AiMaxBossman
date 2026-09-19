/* ============================================================
   api.js — клиент Control API (раздел 6 архитектуры).
   Единственный источник данных UI.

   V2.1 (фаза N): вечный токен в браузере больше не хранится. Логин обменивает
   его на серверную сессию — она приходит HttpOnly-cookie, которую JS прочитать
   не может (и не может украсть XSS). В localStorage лежит только CSRF-токен:
   сам по себе он доступа не даёт, но требуется на изменяющих запросах.
   WebSocket аутентифицируется той же cookie — секрета в URL больше нет.
   ============================================================ */

const CSRF_KEY = 'bcc.csrf';
const CSRF_HEADER = 'X-BCC-CSRF';
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/* ---------------- VIP demo (страховка для показа инвесторам) ----------------
   window.BOSSMAN_VIP_DEMO = true — либо выставляется вручную до загрузки
   app.js, либо включается автоматически по ?demo=vip в адресе. Проверяем это
   максимально рано (при разборе модуля, до первого запроса), чтобы флаг был
   готов до самого первого fetch. Сам режим НЕ меняет обычный путь: он только
   оборачивает его в api.js (см. demoGuard ниже) — при выключенном флаге
   поведение 1:1 совпадает с прежним. */
try {
  if (typeof window !== 'undefined' && new URLSearchParams(location.search).get('demo') === 'vip') {
    window.BOSSMAN_VIP_DEMO = true;
  }
} catch { /* не в браузере / нет location — не критично */ }

export function isVipDemo() {
  try { return typeof window !== 'undefined' && window.BOSSMAN_VIP_DEMO === true; } catch { return false; }
}

/* Событие, которым demoGuard подсвечивает панель «Процесс работы» (thinking.js)
   во время подмены ответа: оболочка (app.js) слушает его и прокидывает в ту же
   шину, что и обычные события run.* и tool.* — так эффект «идёт живая работа»
   рисуется тем же кодом, что и настоящий прогон, без параллельной системы. */
export const VIP_DEMO_EVENT = 'bcc:vip-demo-event';

function emitDemoEvent(ev) {
  try {
    if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
      window.dispatchEvent(new CustomEvent(VIP_DEMO_EVENT, { detail: ev }));
    }
  } catch { /* панель — не критично для самого запроса */ }
}

/* ---------------- Сессия ---------------- */

export function getCsrf() {
  try { return localStorage.getItem(CSRF_KEY) || ''; } catch { return ''; }
}

export function setCsrf(value) {
  try { localStorage.setItem(CSRF_KEY, value || ''); } catch { /* приватный режим */ }
}

export function clearCsrf() {
  try { localStorage.removeItem(CSRF_KEY); } catch { /* приватный режим */ }
}

/** Есть ли похожая на живую сессия (окончательно решает сервер — 401). */
export function hasSession() { return Boolean(getCsrf()); }

/* Совместимость: старые вызовы getToken/clearToken из страниц MVP. Токен
   больше не хранится, поэтому getToken отдаёт пустую строку. */
export function getToken() { return ''; }
export function clearToken() { clearCsrf(); }

/* ---------------- Ошибки ---------------- */

export class ApiError extends Error {
  constructor(message, { status = 0, hint = '', actions = null, path = '', code = '' } = {}) {
    super(message || 'Неизвестная ошибка');
    this.name = 'ApiError';
    this.status = status;
    this.hint = hint;
    this.actions = actions;
    this.path = path;
    this.code = code;
  }
  /* 401 — сессии нет; 403 code=csrf — сессия есть, но CSRF-токен этой вкладки
     потерян или от другого входа: без повторного входа ни один POST не пройдёт. */
  get isAuth() { return this.status === 401 || (this.status === 403 && this.code === 'csrf'); }
  get isOffline() { return this.status === 0; }
}

function humanStatus(status, path) {
  if (status === 0) return 'Сервер не отвечает';
  if (status === 401) return 'Нужен вход';
  if (status === 403) return 'Доступ запрещён';
  if (status === 404) return `Не найдено: ${path}`;
  if (status === 409) return 'Конфликт состояния — обновите страницу';
  if (status === 422) return 'Сервер не принял данные формы';
  if (status >= 500) return 'Сбой на стороне сервера';
  return `Запрос не выполнен (${status})`;
}

function hintFor(status) {
  if (status === 0) return 'Проверьте, что процесс Command Center запущен, и повторите.';
  if (status === 401) return 'Войдите заново: токен печатается в консоли сервера при старте.';
  if (status === 404) return 'Возможно, объект уже удалён — обновите список.';
  if (status === 422) return 'Проверьте обязательные поля.';
  if (status >= 500) return 'Подробности — в логах сервера.';
  return '';
}

/* 401 требует входа; 403 означает отказ в действии при действующей сессии. */
export const UNAUTHORIZED_EVENT = 'bcc:unauthorized';

function notifyUnauthorized() {
  try {
    if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function'
      && typeof CustomEvent === 'function') {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    }
  } catch { /* не критично */ }
}

/* ---------------- Транспорт ---------------- */

/* Совпадающие GET'ы, выпущенные одновременно, склеиваются в один запрос.

   При открытии панели оболочка и страница обзора независимо спрашивают одно и
   то же: /api/system уходил трижды, /api/models, /api/agents и подтверждения —
   дважды. Это не кэш: ответ ждут те же самые вызовы, что ждали бы своего
   запроса, и данные не могут оказаться старее, чем они получили бы сами.
   Как только запрос завершился, запись снимается — следующий вызов пойдёт в
   сеть заново. Изменяющие методы сюда не попадают никогда. */
const inflight = new Map();

async function request(method, path, body, opts = {}) {
  if (!isVipDemo()) return dispatch(method, path, body, opts);
  return demoGuard(method, path, () => dispatch(method, path, body, opts));
}

function dispatch(method, path, body, opts) {
  if (method !== 'GET' || opts.signal) return rawRequest(method, path, body, opts);
  const existing = inflight.get(path);
  if (existing) return existing;
  const p = rawRequest(method, path, body, opts).finally(() => {
    if (inflight.get(path) === p) inflight.delete(path);
  });
  inflight.set(path, p);
  return p;
}

/* ---------------- VIP demo: перехват медленных/упавших запросов ----------------
   Обёртка НАД обычным путём (dispatch), а не вместо него: реальный запрос
   всегда уходит на сервер и, если он успевает уложиться в срок и не падает,
   его настоящий ответ и возвращается. Подмена включается только тогда, когда
   без неё на экране появилась бы сырая ошибка, бесконечный спиннер или сетевой
   алерт — то есть по факту:
     · ответ не пришёл за DEMO_TIMEOUT_MS,
     · сервер ответил статусом ≥ 400,
     · fetch упал (оффлайн, CORS, обрыв соединения).
   Зависший «настоящий» запрос никто не отменяет — он просто больше никого не
   интересует, его результат будет отброшен, когда (если) он придёт. */
const DEMO_TIMEOUT_MS = 2500;
const DEMO_TIMEOUT = Symbol('vip-demo-timeout');

async function demoGuard(method, path, run) {
  let timer = null;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(DEMO_TIMEOUT), DEMO_TIMEOUT_MS); });
  try {
    const result = await Promise.race([run(), timeout]);
    clearTimeout(timer);
    if (result === DEMO_TIMEOUT) return demoFallback(method, path, 'timeout');
    return result;
  } catch (err) {
    clearTimeout(timer);
    if (err && err.name === 'AbortError') throw err; // осознанная отмена — не сбой демо-сценария
    return demoFallback(method, path, (err instanceof ApiError && err.status) || 'network');
  }
}

function demoFallback(method, path, reason) {
  emitDemoActivity(path, reason);
  return demoDataFor(method, path);
}

/* Красивый, ничего не требующий у сети «поток мыслей»: панель «Процесс
   работы» получает те же типы событий, что и настоящий прогон агента, поэтому
   вместо спиннера или тоста инвестор видит привычную живую ленту. */
function emitDemoActivity(path, reason) {
  const label = demoLabel(path);
  const ts = Date.now() / 1000;
  const jitter = () => 160 + Math.round(Math.random() * 260);
  const seq = [
    { kind: 'task.progress', ts, task_id: 'vip-demo', run_id: 'vip-demo', step: 1, max_steps: 2, model: 'bossman-vip', tool_calls: [label] },
    { kind: 'tool.called', ts: ts + 0.05, task_id: 'vip-demo', run_id: 'vip-demo', tool: label, ok: true, duration_ms: jitter() },
    { kind: 'evaluation.completed', ts: ts + 0.1, task_id: 'vip-demo', run_id: 'vip-demo', verdict: 'PASS', reasons: reason === 'timeout' ? 'демо-режим: ответ синтезирован по таймауту' : 'демо-режим: синтетический ответ' },
  ];
  for (const ev of seq) emitDemoEvent(ev);
}

function demoLabel(path) {
  if (path.startsWith('/api/images')) return 'генерация изображения';
  if (path.startsWith('/api/video-studio')) return 'рендер видео';
  if (path.startsWith('/api/system')) return 'проверка системы';
  if (path.startsWith('/api/models')) return 'каталог моделей';
  if (path.startsWith('/api/agents')) return 'агенты';
  if (path.startsWith('/api/tasks')) return 'задачи';
  if (path.startsWith('/api/login')) return 'вход';
  return 'запрос к серверу';
}

function svgTile(id, title, from, to) {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360'>`
    + `<defs><linearGradient id='g${id}' x1='0' y1='0' x2='1' y2='1'>`
    + `<stop offset='0' stop-color='${from}'/><stop offset='1' stop-color='${to}'/></linearGradient></defs>`
    + `<rect width='640' height='360' fill='url(#g${id})'/>`
    + `<circle cx='520' cy='80' r='120' fill='${to}' opacity='0.35'/>`
    + `<circle cx='90' cy='300' r='100' fill='${from}' opacity='0.35'/>`
    + `<text x='32' y='320' font-family='sans-serif' font-size='26' fill='#fff' opacity='0.92'>${title}</text>`
    + `</svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

const DEMO_IMAGE_ASSETS = [
  { id: 'vip-demo-1', title: 'Неоновый город', prompt: 'Футуристический городской пейзаж на закате, неоновые огни, дождь', model_alias: 'mock-image', aspect_ratio: '16:9', width: 1280, height: 720, favorite: true, created_at: new Date().toISOString(), file_url: svgTile(1, 'Неоновый город', '#1b1140', '#ff2fb0') },
  { id: 'vip-demo-2', title: 'Кибер-переулок', prompt: 'Киберпанк-переулок, вывески, отражения в лужах', model_alias: 'mock-image', aspect_ratio: '1:1', width: 1024, height: 1024, favorite: false, created_at: new Date().toISOString(), file_url: svgTile(2, 'Кибер-переулок', '#0b2141', '#12e0c8') },
  { id: 'vip-demo-3', title: 'Неоновый портрет', prompt: 'Портрет в неоновом свете, синтвейв-палитра', model_alias: 'mock-image', aspect_ratio: '3:4', width: 900, height: 1200, favorite: false, created_at: new Date().toISOString(), file_url: svgTile(3, 'Неоновый портрет', '#2a0b3d', '#7b4dff') },
];

/* Точечные, приятные глазу заглушки для узнаваемых путей — то, что реально
   рисуется на экране во время демо. Всё остальное подменяется максимально
   нейтрально (пустой список/объект или {ok:true}), чтобы не ломать код
   страницы, которая ждёт другую форму ответа: код страниц по всему UI и так
   написан терпимо к пустым спискам (listOf, `?? []`, `|| {}`). */
function demoDataFor(method, path) {
  const mutate = UNSAFE.has(method);

  if (path.startsWith('/api/login')) return { ok: true, csrf: 'vip-demo-csrf' };
  if (path === '/api/logout') return { ok: true };

  if (path.startsWith('/api/system')) {
    return {
      current: { cpu_pct: 17, ram_used_mb: 4200, ram_total_mb: 16384 },
      health: { api: 'ok', workers: 'ok', storage: 'ok' },
      overall: 'ok',
    };
  }
  if (path.startsWith('/api/models')) {
    return { items: [
      { id: 'vip-demo-model', alias: 'bossman-vip', name: 'BOSSMAN VIP', status: 'online', provider: 'local' },
    ] };
  }
  if (path.startsWith('/api/agents')) {
    return { items: [
      { id: 'vip-demo-agent', name: 'Демо-агент', role: 'Показ', status: 'idle' },
    ] };
  }
  if (path.startsWith('/api/tasks')) return { items: [] };
  if (path.startsWith('/api/schedules')) return { items: [] };
  if (path.startsWith('/api/approvals')) return { items: [] };
  if (path.startsWith('/api/activity')) return { items: [] };

  if (path.startsWith('/api/images/assets')) return { items: DEMO_IMAGE_ASSETS };
  if (path.startsWith('/api/images/jobs')) {
    return { items: DEMO_IMAGE_ASSETS.map((a, i) => ({
      id: `vip-demo-job-${i + 1}`, status: 'completed', progress: 1, prompt: a.prompt,
      model_alias: a.model_alias, aspect_ratio: a.aspect_ratio,
    })) };
  }
  if (path.startsWith('/api/images/collections')) return { items: [] };
  if (path.startsWith('/api/images/models')) return { items: [{ id: 'mock-image', alias: 'mock-image', name: 'Neon Art (демо)' }] };
  if (path.startsWith('/api/images/storage')) return { used_bytes: 42 * 1024 * 1024, asset_count: DEMO_IMAGE_ASSETS.length };
  if (path.startsWith('/api/images/overview')) return { assets: DEMO_IMAGE_ASSETS.length, favorites: 1, active_jobs: 0, failed_jobs: 0 };

  return mutate ? { ok: true, demo: true } : {};
}

async function rawRequest(method, path, body, { signal } = {}) {
  const headers = {};
  const csrf = getCsrf();
  if (csrf && UNSAFE.has(method)) headers[CSRF_HEADER] = csrf;
  let payload;
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof FormData) {
      payload = body;
    } else {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
  }

  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, signal, cache: 'no-store',
      credentials: 'same-origin' });
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError(humanStatus(0, path), { status: 0, hint: hintFor(0), path });
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
  }

  if (!res.ok) {
    const e = data && typeof data === 'object' ? (data.error || data.detail || null) : null;
    const code = (e && typeof e === 'object' && typeof e.code === 'string') ? e.code : '';
    if (res.status === 401) notifyUnauthorized();
    if (res.status === 403 && code === 'csrf') { clearCsrf(); notifyUnauthorized(); }
    const message = (e && typeof e === 'object' && e.message)
      || (typeof e === 'string' ? e : '')
      || (data && typeof data.message === 'string' ? data.message : '')
      || humanStatus(res.status, path);
    const hint = (e && typeof e === 'object' && e.hint) || hintFor(res.status);
    const actions = (e && typeof e === 'object' && e.actions) || null;
    throw new ApiError(message, { status: res.status, hint, actions, path, code });
  }
  return data;
}

const GET = (p, o) => request('GET', p, undefined, o);
const POST = (p, b, o) => request('POST', p, b === undefined ? {} : b, o);
const PATCH = (p, b, o) => request('PATCH', p, b === undefined ? {} : b, o);
const DEL = (p, o) => request('DELETE', p, undefined, o);

function qs(params) {
  const usable = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  if (!usable.length) return '';
  return '?' + usable.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
}

/* ---------------- Нормализация ответов ----------------
   Бэкенд может отдать как голый массив, так и {items:[...]} / {models:[...]}.
   Этот помощник делает UI устойчивым к обеим формам.               */

export function listOf(data, ...keys) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== 'object') return [];
  for (const key of ['items', 'results', 'data', ...keys]) {
    if (Array.isArray(data[key])) return data[key];
  }
  return [];
}

/** Первое непустое значение по списку возможных имён полей. */
export function pick(obj, keys, fallback = undefined) {
  if (!obj || typeof obj !== 'object') return fallback;
  for (const k of keys) {
    const v = obj[k];
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return fallback;
}

/* ---------------- Методы Control API ---------------- */

export const api = {
  // V2: универсальный вызов для feature-страниц (контракты §8) — свои endpoint'ы
  // фича зовёт через raw, не расширяя этот файл
  raw: (path, { method = 'GET', body } = {}) => request(method, path, body),

  // auth: токен → серверная сессия (cookie); в браузере остаётся только CSRF
  login: async (token) => {
    const res = await POST('/api/login', { token, label: 'ui' });
    if (res && res.csrf) setCsrf(res.csrf);
    return res;
  },
  logout: async () => {
    try { await POST('/api/logout'); } finally { clearCsrf(); }
    return { ok: true };
  },

  // system
  system: (opts) => GET('/api/system', opts),
  cacheEconomics: (opts) => GET('/api/cache/economics', opts),
  cacheIntelligence: (opts) => GET('/api/cache/intelligence', opts),

  // providers
  providerKinds: () => GET('/api/providers/kinds'),
  providers: () => GET('/api/providers'),
  createProvider: (data) => POST('/api/providers', data),
  deleteProvider: (id) => DEL(`/api/providers/${encodeURIComponent(id)}`),

  // models
  models: () => GET('/api/models'),
  createModel: (data) => POST('/api/models', data),
  updateModel: (id, data) => PATCH(`/api/models/${encodeURIComponent(id)}`, data),
  deleteModel: (id) => DEL(`/api/models/${encodeURIComponent(id)}`),
  checkModel: (id) => POST(`/api/models/${encodeURIComponent(id)}/check`),
  discoverModels: (extraUrls) => POST('/api/models/discover', { extra_urls: extraUrls || [] }),
  testModel: (id) => POST(`/api/models/${encodeURIComponent(id)}/test`),

  // agents
  agents: () => GET('/api/agents'),
  createAgent: (data) => POST('/api/agents', data),
  updateAgent: (id, data) => PATCH(`/api/agents/${encodeURIComponent(id)}`, data),
  deleteAgent: (id) => DEL(`/api/agents/${encodeURIComponent(id)}`),

  // tasks
  tasks: (status) => GET('/api/tasks' + qs({ status })),
  createTask: (data) => POST('/api/tasks', data),
  task: (id) => GET(`/api/tasks/${encodeURIComponent(id)}`),
  taskAction: (id, action) => POST(`/api/tasks/${encodeURIComponent(id)}/${action}`),

  // runs
  run: (id) => GET(`/api/runs/${encodeURIComponent(id)}`),
  runEvents: (id, after) => GET(`/api/runs/${encodeURIComponent(id)}/events` + qs({ after })),

  // schedules
  schedules: () => GET('/api/schedules'),
  createSchedule: (data) => POST('/api/schedules', data),
  updateSchedule: (id, data) => PATCH(`/api/schedules/${encodeURIComponent(id)}`, data),
  deleteSchedule: (id) => DEL(`/api/schedules/${encodeURIComponent(id)}`),

  // approvals
  approvals: (status = 'pending') => GET('/api/approvals' + qs({ status })),
  decideApproval: (id, approve, by = 'ui') => POST(`/api/approvals/${encodeURIComponent(id)}`, { approve, by }),

  // activity
  activity: () => GET('/api/activity'),
};

/* ============================================================
   EventStream — WS /api/events?token=… с backoff-переподключением
   ============================================================ */

export class EventStream {
  constructor() {
    this.ws = null;
    this.listeners = new Set();
    this.state = 'idle';          // idle | connecting | open | closed
    this.attempt = 0;
    this.timer = null;
    this.stopped = true;
    this.nextRetryAt = 0;        // epoch ms — когда запланирована следующая попытка (0 = не запланирована)
    this.lastOpenAt = 0;         // epoch ms — последнее успешное подключение
    this.disconnectedAt = 0;     // epoch ms — момент потери соединения (0 = сейчас на связи или ещё не было)
    this._onVisible = () => {
      if (!this.stopped && this.state !== 'open' && document.visibilityState === 'visible') {
        this.reconnectNow();
      }
    };
    document.addEventListener('visibilitychange', this._onVisible);
    window.addEventListener('online', this._onVisible);
  }

  /** cb(event) — event: {kind, ts, ...} либо служебные {kind:'ws.open'|'ws.closed'} */
  subscribe(cb) {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  emit(ev) {
    for (const cb of Array.from(this.listeners)) {
      try { cb(ev); } catch (err) { console.error('event listener failed', err); }
    }
  }

  setState(state) {
    if (this.state === state) return;
    const prev = this.state;
    this.state = state;
    const now = Date.now();
    if (state === 'open') {
      this.nextRetryAt = 0;
      // «Восстановлено» — только если связь ДО этого уже была: первое подключение
      // после входа не является восстановлением и не должно радовать тостом.
      const wasDown = this.disconnectedAt > 0 && this.lastOpenAt > 0;
      const downtimeMs = wasDown ? now - this.disconnectedAt : 0;
      this.lastOpenAt = now;
      this.disconnectedAt = 0;
      this.emit({ kind: 'ws.open', ts: now / 1000, local: true, reconnected: wasDown, downtime_ms: downtimeMs, prev });
      return;
    }
    if (state === 'closed' && !this.disconnectedAt) this.disconnectedAt = this.lastOpenAt || now;
    this.emit({ kind: `ws.${state}`, ts: now / 1000, local: true, prev, attempt: this.attempt, since: this.disconnectedAt });
  }

  /** Немедленная попытка переподключения (кнопка «Переподключить сейчас», возврат вкладки, сеть вернулась). */
  reconnectNow() {
    if (this.stopped) return;
    clearTimeout(this.timer);
    this.timer = null;
    this.nextRetryAt = 0;
    this.attempt = 0;
    this.connect();
  }

  start() {
    this.stopped = false;
    this.attempt = 0;
    this.connect();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    if (this.ws) {
      try { this.ws.onclose = null; this.ws.close(); } catch { /* уже закрыт */ }
    }
    this.ws = null;
    this.nextRetryAt = 0;
    this.setState('closed');
  }

  connect() {
    if (this.stopped) return;
    clearTimeout(this.timer);
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;

    // Cookie сессии уходит с рукопожатием сама — секрет в URL не попадает
    // (иначе он оседал бы в логах прокси и в истории).
    const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    const url = `${proto}${location.host}/api/events`;

    this.setState('connecting');
    let ws;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.setState('open');
    };

    ws.onmessage = (msg) => {
      let data;
      try { data = JSON.parse(msg.data); } catch { return; }
      if (!data || typeof data !== 'object') return;
      if (Array.isArray(data)) { data.forEach((d) => d && d.kind && this.emit(d)); return; }
      if (!data.kind) return;
      this.emit(data);
    };

    ws.onerror = () => { /* закрытие придёт следом в onclose */ };

    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.setState('closed');
      this.scheduleReconnect();
    };
  }

  scheduleReconnect() {
    if (this.stopped) return;
    this.attempt += 1;
    const base = Math.min(1000 * Math.pow(1.6, this.attempt - 1), 15000);
    const delay = Math.round(base * (0.85 + Math.random() * 0.3));
    clearTimeout(this.timer);
    this.nextRetryAt = Date.now() + delay;
    this.timer = setTimeout(() => { this.timer = null; this.nextRetryAt = 0; this.connect(); }, delay);
    // Служебное событие для UI: владелец видит обратный отсчёт до повтора, а не «нет соединения» без объяснений.
    this.emit({ kind: 'ws.retry_scheduled', ts: Date.now() / 1000, local: true, attempt: this.attempt, delay_ms: delay, at: this.nextRetryAt, since: this.disconnectedAt });
  }
}
