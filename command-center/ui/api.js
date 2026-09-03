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
  constructor(message, { status = 0, hint = '', actions = null, path = '' } = {}) {
    super(message || 'Неизвестная ошибка');
    this.name = 'ApiError';
    this.status = status;
    this.hint = hint;
    this.actions = actions;
    this.path = path;
  }
  get isAuth() { return this.status === 401 || this.status === 403; }
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

/* Любой 401/403 — сигнал оболочке показать экран входа, откуда бы вызов ни шёл. */
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

async function request(method, path, body, { signal } = {}) {
  const headers = {};
  const csrf = getCsrf();
  if (csrf && UNSAFE.has(method)) headers[CSRF_HEADER] = csrf;
  let payload;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
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
    if (res.status === 401 || res.status === 403) notifyUnauthorized();
    const e = data && typeof data === 'object' ? (data.error || data.detail || null) : null;
    const message = (e && typeof e === 'object' && e.message)
      || (typeof e === 'string' ? e : '')
      || (data && typeof data.message === 'string' ? data.message : '')
      || humanStatus(res.status, path);
    const hint = (e && typeof e === 'object' && e.hint) || hintFor(res.status);
    const actions = (e && typeof e === 'object' && e.actions) || null;
    throw new ApiError(message, { status: res.status, hint, actions, path });
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
