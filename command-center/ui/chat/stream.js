/* ============================================================
   chat/stream.js — живой поток событий одной задачи (SSE через fetch).

   GET /api/events/stream?task_id=<id>&after=<seq> отдаёт кадры
   `id: <seq>\ndata: <json>\n\n` и комментарий `: keepalive` раз в 15 с.
   EventSource не подходит: он переподключается к ТОМУ ЖЕ адресу, а сервер
   читает курсор только из `after` — получились бы повторы или пропуски.
   Поэтому поток читается fetch() + ReadableStream, курсор (последний seq)
   хранится здесь, повторы отбрасываются по seq, обрыв лечится
   переподключением с after=<последний seq> и растущей паузой 0.5 → 8 с.

   При импорте модуль ничего не делает: fetch, таймеры и AbortController
   берутся только при start(), поэтому разбор и логика переподключения
   проверяются в node с подменённым fetch.

   retryNow() — «Сейчас» в баннере потери связи, возврат сети (online) и
   возврат к окну: отменяет ждущую паузу и подключается один раз. Если
   паузы нет (подключение уже идёт или поток открыт), ничего не делает —
   второго соединения не бывает.
   ============================================================ */

import { backoffDelay } from './format.js';
import { TERMINAL_KINDS } from './state.js';

/**
 * Разбор SSE по кускам произвольной длины (кусок может оборвать строку или
 * пару \r\n). push(chunk) → [{id, event, data}] законченных кадров;
 * комментарии возвращаются как {comment}.
 */
export function createSseParser() {
  let buf = '';
  let data = [];
  let id = null;
  let event = '';
  const take = (line, out) => {
    if (line === '') {
      if (data.length) out.push({ id, event: event || 'message', data: data.join('\n') });
      data = [];
      id = null;
      event = '';
      return;
    }
    if (line[0] === ':') { out.push({ comment: line.slice(1).trim() }); return; }
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value[0] === ' ') value = value.slice(1);
    if (field === 'data') data.push(value);
    else if (field === 'id') { if (!value.includes('\0')) id = value; }
    else if (field === 'event') event = value;
  };
  return {
    push(chunk) {
      const out = [];
      buf += String(chunk ?? '');
      /* одинокий \r в конце куска может оказаться началом \r\n — ждём следующий кусок */
      let hold = '';
      if (buf.endsWith('\r')) { hold = '\r'; buf = buf.slice(0, -1); }
      const lines = buf.replace(/\r\n?/g, '\n').split('\n');
      buf = lines.pop() + hold;
      for (const line of lines) take(line, out);
      return out;
    },
    /** Конец потока: незавершённый кадр без пустой строки не отдаётся (как у EventSource). */
    reset() { buf = ''; data = []; id = null; event = ''; },
  };
}

export function parseFrame(frame) {
  if (!frame || typeof frame.data !== 'string') return null;
  try {
    const ev = JSON.parse(frame.data);
    return ev && typeof ev === 'object' && !Array.isArray(ev) ? ev : null;
  } catch {
    return null;
  }
}

/**
 * Поток событий одной задачи.
 *
 * onEvent(ev) — каждое событие ровно один раз (seq-дедупликация);
 * onState(state, info) — 'connecting' | 'open' | 'reconnecting' | 'auth' |
 * 'done' | 'failed' | 'closed'.
 */
export class TaskStream {
  constructor({ taskId, after = 0, onEvent = () => {}, onState = () => {}, fetchImpl = null,
    setTimer = null, clearTimer = null, random = Math.random, idleMs = 45000, lingerMs = 1200 } = {}) {
    this.taskId = taskId;
    this.lastSeq = Math.max(0, Number(after) || 0);
    this.onEvent = onEvent;
    this.onState = onState;
    this.fetchImpl = fetchImpl;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.random = random;
    this.idleMs = idleMs;
    this.lingerMs = lingerMs;
    this.attempt = 0;
    this.stopped = true;
    this.finished = false;
    this.ctrl = null;
    this.retryTimer = null;
    this.retryToken = 0;
    this.idleTimer = null;
    this.lingerTimer = null;
    this.state = 'idle';
    this.connections = 0;
  }

  _fetch(...args) { return (this.fetchImpl || globalThis.fetch)(...args); }
  _set(fn, ms) { return (this.setTimer || globalThis.setTimeout)(fn, ms); }
  _clear(t) { if (t !== null && t !== undefined) (this.clearTimer || globalThis.clearTimeout)(t); }

  _setState(state, info = {}) {
    this.state = state;
    try { this.onState(state, info); } catch { /* наблюдатель не ломает поток */ }
  }

  url() {
    return `/api/events/stream?task_id=${encodeURIComponent(this.taskId)}&after=${this.lastSeq}`;
  }

  start() {
    this.stopped = false;
    this.finished = false;
    this._connect();
    return this;
  }

  /** Остановка владельцем чата (переход в другой тред, выход): без переподключений. */
  stop() {
    this.stopped = true;
    this.retryToken += 1;
    this._clear(this.retryTimer);
    this._clear(this.idleTimer);
    this._clear(this.lingerTimer);
    this.retryTimer = null;
    this.idleTimer = null;
    this.lingerTimer = null;
    if (this.ctrl) { try { this.ctrl.abort(); } catch { /* уже закрыт */ } }
    this.ctrl = null;
  }

  _armIdle() {
    this._clear(this.idleTimer);
    /* keepalive приходит раз в 15 с; тишина втрое дольше — связь потеряна */
    this.idleTimer = this._set(() => {
      this.idleTimer = null;
      if (this.ctrl) { try { this.ctrl.abort(); } catch { /* уже закрыт */ } }
    }, this.idleMs);
  }

  async _connect() {
    if (this.stopped) return;
    this.connections += 1;
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    this.ctrl = ctrl;
    this._setState(this.connections === 1 ? 'connecting' : 'reconnecting', { attempt: this.attempt });
    let res;
    try {
      res = await this._fetch(this.url(), {
        method: 'GET', signal: ctrl ? ctrl.signal : undefined, cache: 'no-store',
        credentials: 'same-origin', headers: { Accept: 'text/event-stream' },
      });
    } catch {
      this._scheduleReconnect();
      return;
    }
    if (this.stopped) return;
    if (res.status === 401 || res.status === 403) {
      this.stopped = true;
      this._setState('auth', { status: res.status });
      return;
    }
    if (res.status === 404 || res.status === 422) {
      this.stopped = true;
      this._setState('failed', { status: res.status, message: 'задача не найдена' });
      return;
    }
    if (!res.ok || !res.body || typeof res.body.getReader !== 'function') {
      this._scheduleReconnect();
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    const parser = createSseParser();
    this._armIdle();
    let opened = false;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        this._armIdle();
        const text = decoder.decode(value, { stream: true });
        for (const frame of parser.push(text)) {
          if (frame.comment !== undefined) continue;      // keepalive
          const ev = parseFrame(frame);
          if (!ev) continue;
          if (!opened && ev.kind === 'stream.open') {
            opened = true;
            this.attempt = 0;
            this._setState('open');
            continue;
          }
          this._handle(ev);
          if (this.stopped) break;
        }
        if (this.stopped) break;
      }
    } catch {
      /* обрыв чтения (сеть, abort по тишине) — ниже решаем, переподключаться ли */
    }
    this._clear(this.idleTimer);
    this.idleTimer = null;
    try {
      const pending = reader.cancel();
      if (pending && typeof pending.catch === 'function') pending.catch(() => {});
    } catch { /* уже закрыт */ }
    if (this.stopped || this.finished) return;
    this._scheduleReconnect();
  }

  _handle(ev) {
    const kind = String(ev.kind || '');
    if (kind === 'stream.replayed') {
      const c = Number(ev.cursor);
      if (Number.isFinite(c) && c > this.lastSeq) this.lastSeq = c;
      return;
    }
    if (kind === 'stream.lagged') {
      /* сервер отстегнул отставшего клиента: переподключаемся с его курсора */
      const c = Number(ev.cursor);
      if (Number.isFinite(c) && c > this.lastSeq) this.lastSeq = c;
      if (this.ctrl) { try { this.ctrl.abort(); } catch { /* уже закрыт */ } }
      return;
    }
    if (typeof ev.seq === 'number' && Number.isFinite(ev.seq)) {
      if (ev.seq <= this.lastSeq) return;         // уже получено (повтор истории/переподключение)
      this.lastSeq = ev.seq;
    }
    try { this.onEvent(ev); } catch { /* ошибка отрисовки не рвёт поток */ }
    if (TERMINAL_KINDS.has(kind) && !this.finished) {
      this.finished = true;
      /* короткая выдержка: следом приходят evaluation.completed и run.usage */
      this.lingerTimer = this._set(() => {
        this.lingerTimer = null;
        this.stop();
        this._setState('done', { kind });
      }, this.lingerMs);
    }
  }

  _scheduleReconnect() {
    if (this.stopped) return;
    this.attempt += 1;
    const delay = backoffDelay(this.attempt, this.random);
    this._setState('reconnecting', { attempt: this.attempt, delay_ms: delay });
    this._clear(this.retryTimer);
    this.retryToken += 1;
    const token = this.retryToken;
    this.retryTimer = this._set(() => {
      /* отменённая пауза (retryNow, stop) не подключается даже если таймер всё же сработал */
      if (token !== this.retryToken) return;
      this.retryTimer = null;
      this._connect();
    }, delay);
  }

  /**
   * Подключиться сейчас, не дожидаясь конца паузы. true — подключение
   * начато; false — паузы не было (поток открыт, подключается, закончен
   * или остановлен), и ничего не сделано.
   */
  retryNow() {
    if (this.stopped || this.finished || this.retryTimer === null) return false;
    this.retryToken += 1;
    this._clear(this.retryTimer);
    this.retryTimer = null;
    this._connect();
    return true;
  }
}
