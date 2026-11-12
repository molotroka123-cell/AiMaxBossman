/* Bossman — the phone chat with ONE local PC operator (owner 11.11: «просто open code в телефоне»).
   One screen: header (Bossman, honest status, STOP), one continuous conversation, composer (Фото, Видео, Отправить).
   No model / provider / mode choice on screen: agent_id and mode come only from GET /m/api/operator.
   Every server string reaches the DOM through textContent (strict CSP, never HTML parsing). */
import { renderMarkdown } from './md.js';

const CFG = Object.assign({ pollMs: 5000, silenceMs: 20000, approvalsMs: 8000, reconnectBaseMs: 1000, stopPollMs: 30000,
  queueFollowMs: 1000 }, (typeof window !== 'undefined' && window.__opCfg) || {});
const TERMINAL = new Set(['completed', 'failed', 'stopped', 'blocked', 'cancelled', 'missing']);
const END_KIND = { 'task.completed': 'completed', 'task.failed': 'failed', 'task.stopped': 'stopped', 'task.blocked': 'blocked' };
const SVGNS = 'http://www.w3.org/2000/svg';

const S = {
  csrf: '', conn: true, op: null, opErr: '', warm: 'idle', warmT0: 0, warmErr: '', warmP: null,
  stop: { active: false }, threadId: null, turns: new Map(), run: null, sending: false, files: [], queue: [],
  atBottom: true, patched: new Set(), started: false, timers: [],
};
window.__opDebug = S;

/* ------------------------------------------------------------------ small DOM helpers */
const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function btn(cls, text, aria, onClick) {
  const b = el('button', cls, text);
  b.type = 'button';
  if (aria) b.setAttribute('aria-label', aria);
  if (onClick) b.addEventListener('click', onClick);
  return b;
}
const ICON = {
  photo: ['M4 7.5A2.5 2.5 0 0 1 6.5 5h2l1.5-2h4l1.5 2h2A2.5 2.5 0 0 1 20 7.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z', 'M12 15.5a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z'],
  video: ['M3.5 7.5A2 2 0 0 1 5.5 5.5h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2z', 'm15.5 10.5 5-3v9l-5-3'],
  send: ['M12 19V5', 'M5.5 11.5 12 5l6.5 6.5'],
  down: ['M12 5v13', 'M6.5 12.5 12 18l5.5-5.5'],
  x: ['M6 6l12 12', 'M18 6 6 18'],
};
function icon(name) {
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('class', 'ic');
  for (const d of ICON[name] || []) {
    const p = document.createElementNS(SVGNS, 'path');
    p.setAttribute('d', d); s.appendChild(p);
  }
  return s;
}
const store = {
  get(k) { try { return window.localStorage.getItem('bm.op.' + k); } catch { return null; } },
  set(k, v) { try { if (v == null) window.localStorage.removeItem('bm.op.' + k); else window.localStorage.setItem('bm.op.' + k, String(v)); } catch { /* private mode */ } },
};
const enc = encodeURIComponent;
const fmtSec = (s) => (s < 60 ? `${s < 10 ? s.toFixed(1) : Math.round(s)} с` : `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с`);
const fmtTok = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
function fmtAgo(iso) {
  const t = Date.parse(iso || ''); if (!t) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'только что';
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
  return `${Math.floor(s / 86400)} дн назад`;
}

/* ------------------------------------------------------------------ secrets never reach the screen in tool steps */
export function mask(input) {
  return String(input == null ? '' : input)
    .replace(/("(?:password|passwd|pwd|pass|token|access_token|refresh_token|api[_-]?key|apikey|key|secret|client_secret|seed|seed_phrase|mnemonic|private[_-]?key|authorization|cookie)"\s*:\s*)"[^"]*"/gi, '$1"•••"')
    .replace(/\b((?:password|passwd|pwd|pass|token|access_token|api[_-]?key|apikey|key|secret|seed|seed_phrase|mnemonic|private[_-]?key|authorization|cookie)\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;&"']+)/gi, '$1•••')
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}/g, '$1 •••')
    .replace(/\b(?:sk|pk|rk|ghp|gho|ghs|ghu|github_pat|xox[abpr]|hf|glpat|AKIA)[-_]?[A-Za-z0-9_-]{12,}/g, '•••')
    .replace(/\b[0-9a-fA-F]{24,}\b/g, (m) => `${m.slice(0, 4)}…•••`)
    .replace(/[A-Za-z0-9_-]{32,}/g, (m) => (/\d/.test(m) && /[a-z]/.test(m) && /[A-Z]/.test(m) ? `${m.slice(0, 4)}…•••` : m))
    .replace(/[A-Za-z0-9+/]{40,}={0,2}/g, (m) => (/[+/]/.test(m) && /\d/.test(m) && !/\/[a-z]+\//.test(m) ? `${m.slice(0, 4)}…•••` : m));
}

/* ------------------------------------------------------------------ network */
class ApiError extends Error {
  constructor(status, body) {
    const d = (body && (body.detail || body.error)) || {};
    super(typeof d === 'string' ? d : (d.message || (status ? `Ошибка ПК (HTTP ${status})` : 'Нет связи с ПК')));
    this.status = status; this.code = (d && typeof d === 'object' && d.code) || ''; this.hint = (d && d.hint) || '';
  }
}
const errText = (e) => (e && e.message ? e.message + (e.hint ? ` — ${e.hint}` : '') : 'Неизвестная ошибка');
function setConn(ok) { if (S.conn !== ok) { S.conn = ok; renderStatus(); } }

async function api(method, path, body, retried) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-BM-CSRF'] = S.csrf;
  let res;
  try {
    res = await fetch('/m/api' + path, { method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    setConn(false);
    throw new ApiError(0, null);
  }
  setConn(true);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && path !== '/login') { showLogin(); throw new ApiError(401, data); }
  // a newer login (another tab, the pairing link) replaced the cookie: take the current CSRF token and repeat ONCE
  if (res.status === 403 && !retried && method !== 'GET' && ((data && data.detail) || {}).code === 'csrf') {
    try {
      const s = await api('GET', '/session');
      if (s && s.csrf) { S.csrf = s.csrf; return api(method, path, body, true); }
    } catch { /* fall through to the original error */ }
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

/** Streamed upload with progress (fetch has no upload progress). Resolves with {id,name,kind,size,mime}. */
function uploadFile(file, onProgress, retried) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const q = `filename=${enc(file.name || 'file')}${S.threadId ? `&thread_id=${enc(S.threadId)}` : ''}`;
    xhr.open('POST', `/m/api/chat/upload?${q}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('X-BM-CSRF', S.csrf);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    if (xhr.upload) xhr.upload.onprogress = (e) => { if (e.lengthComputable && e.total) onProgress(e.loaded / e.total); };
    xhr.onload = async () => {
      let d = null;
      try { d = JSON.parse(xhr.responseText || 'null'); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300 && d && d.attachment && d.attachment.id) { resolve(d.attachment); return; }
      if (xhr.status === 401) { showLogin(); reject(new ApiError(401, d)); return; }
      if (xhr.status === 403 && !retried && ((d && d.detail) || {}).code === 'csrf') {
        try { const s = await api('GET', '/session'); if (s && s.csrf) { S.csrf = s.csrf; resolve(await uploadFile(file, onProgress, true)); return; } } catch { /* below */ }
      }
      reject(new ApiError(xhr.status, d));
    };
    xhr.onerror = () => { setConn(false); reject(new ApiError(0, null)); };
    xhr.send(file);
  });
}

/* ------------------------------------------------------------------ login */
function showLogin(msg) {
  $('app').hidden = true; $('login').hidden = false;
  $('login-error').textContent = msg || '';
}
async function login(token) {
  let res;
  try {
    res = await fetch('/m/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ token }) });
  } catch { throw new Error('Нет связи с ПК — проверьте, что Bossman на ПК запущен и телефон в той же сети.'); }
  let data = null;
  try { data = await res.json(); } catch { /* */ }
  if (!res.ok) {
    const d = (data && data.detail) || {};
    throw new Error(res.status === 429 ? `Слишком много попыток. Подождите ${d.retry_after_s || 60} с.` : (d.message || 'Неверный код'));
  }
  S.csrf = data.csrf;
}

/* ------------------------------------------------------------------ status (header) */
function renderStatus() {
  const dot = $('status-dot'); const txt = $('status-text');
  if (!dot || !txt) return;
  let cls = 'ok'; let t = 'готов';
  const run = S.run && !S.run.done ? S.run : null;
  if (!S.conn) { cls = 'off'; t = 'нет связи с ПК'; }
  else if (S.stop && S.stop.active) { cls = 'err'; t = 'остановлен'; }
  else if (run && run.turn.waitApproval) { cls = 'wait'; t = 'ждёт подтверждения'; }
  else if (S.warm === 'loading') { cls = 'busy'; t = `модель загружается ${Math.round((Date.now() - S.warmT0) / 1000)} с`; }
  else if (run) { cls = 'busy'; t = run.turn.statusShort || 'работает'; }
  else if (S.warm === 'error') { cls = 'err'; t = 'ошибка загрузки'; }
  else if (S.opErr && !S.op) { cls = 'err'; t = 'ошибка'; }
  else if (S.op && !S.op.ready) { cls = 'wait'; t = 'не готов'; }
  dot.className = `dot ${cls}`;
  if (txt.textContent !== t) txt.textContent = t;
  $('status').setAttribute('aria-label', `Состояние: ${t}`);
}

/* the banner area under the header: STOP card, operator problems, model loading errors */
function renderBanner() {
  const box = $('banner'); box.replaceChildren();
  if (S.stop && S.stop.active) {
    const c = el('div', 'card stopcard'); c.setAttribute('role', 'alert');
    c.append(el('strong', '', 'STOP активен'), el('span', 'muted', 'Действия на ПК остановлены, пока вы не нажмёте «Продолжить».'));
    const b = btn('btn primary', 'Продолжить', 'Продолжить работу после STOP', resumeStop);
    c.appendChild(b); box.appendChild(c);
  }
  if (S.warm === 'error') {
    const c = el('div', 'card warn');
    c.append(el('span', '', `Модель оператора не загрузилась: ${S.warmErr}`), btn('btn', 'Повторить', 'Повторить загрузку модели', () => warm()));
    box.appendChild(c);
  } else if (S.op && !S.op.ready && S.op.reason) {
    box.appendChild(el('div', 'card warn', `Оператор не готов: ${S.op.reason}`));
  } else if (!S.op && S.opErr) {
    const c = el('div', 'card warn');
    c.append(el('span', '', `Не удалось узнать оператора: ${S.opErr}`), btn('btn', 'Повторить', 'Повторить', () => loadOperator()));
    box.appendChild(c);
  }
  box.hidden = !box.children.length;
}

/* ------------------------------------------------------------------ operator + model warm-up (no UI to choose) */
async function loadOperator() {
  try { S.op = await api('GET', '/operator'); S.opErr = ''; }
  catch (err) { if (err.status !== 401) S.opErr = errText(err); }
  renderStatus(); renderBanner();
  if (S.op && S.op.model && S.op.model.loaded === false) warm();
  return S.op;
}
function warm() {
  if (S.warmP) return S.warmP;
  S.warm = 'loading'; S.warmT0 = Date.now(); S.warmErr = '';
  renderStatus(); renderBanner();
  S.warmP = api('POST', '/operator/warm').then((r) => {
    if (r && r.ok && r.loaded !== false) { S.warm = 'ok'; if (S.op && S.op.model) S.op.model.loaded = true; }
    else { S.warm = 'error'; S.warmErr = (r && r.error) || 'модель не загрузилась'; }
  }, (err) => {
    if (err.code === 'BM_OPERATOR_UNSUPPORTED') S.warm = 'idle';     // an older Bossman loads the model on the first message
    else { S.warm = 'error'; S.warmErr = errText(err); }
  }).finally(() => { S.warmP = null; renderStatus(); renderBanner(); });
  return S.warmP;
}
const opAgent = () => (S.op && S.op.agent && S.op.agent.id != null ? S.op.agent.id : null);

/* ------------------------------------------------------------------ STOP */
async function refreshStop() {
  try { S.stop = await api('GET', '/stop'); } catch { /* keep the last known state */ }
  renderStatus(); renderBanner();
}
async function stopAll() {
  const b = $('btn-stop'); if (b.disabled) return;
  b.disabled = true; b.textContent = 'STOP…';
  const tid = S.threadId; const running = S.run && !S.run.done ? S.run : null;
  const jobs = [api('POST', '/stop', { confirm: true })];
  if (running && tid) jobs.push(api('POST', `/threads/${enc(tid)}/stop`));
  const [all, chat] = await Promise.allSettled(jobs);
  if (all.status === 'fulfilled') S.stop = (all.value && all.value.stop) || { active: true };
  else if (chat && chat.status === 'fulfilled') toast(`STOP всего не дошёл (${errText(all.reason)}), задача этого чата остановлена`);
  else toast(`STOP не дошёл: ${errText(all.reason)}`);
  b.disabled = false; b.textContent = 'STOP';
  renderStatus(); renderBanner();
  if (running) setTimeout(() => pollTask(running), 300);
}
async function resumeStop(e) {
  const b = e && e.currentTarget; if (b) b.disabled = true;
  try { const r = await api('POST', '/stop/resume'); S.stop = { active: !!(r && r.active) }; toast('Работа продолжена'); }
  catch (err) { toast(errText(err)); }
  if (b) b.disabled = false;
  renderStatus(); renderBanner();
}

/* ------------------------------------------------------------------ transcript */
function stick(force) {
  const log = $('log');
  if (force || S.atBottom) { log.scrollTop = log.scrollHeight; $('to-bottom').hidden = true; }
  else $('to-bottom').hidden = false;
}
function showEmpty(on) { $('empty').hidden = !on; }

function mediaKind(f) {
  const m = String(f.mime || f.type || '').toLowerCase(); const n = String(f.name || '').toLowerCase(); const k = String(f.kind || '').toLowerCase();
  if (m.startsWith('image/') || k === 'image' || /\.(png|jpe?g|gif|webp|heic|heif|avif)$/.test(n)) return 'image';
  if (m.startsWith('video/') || k === 'video' || /\.(mp4|mov|m4v|webm|3gp)$/.test(n)) return 'video';
  return 'file';
}
function mediaNode(f) {
  const id = String(f.id || '');
  if (!/^[0-9a-zA-Z_-]{6,64}$/.test(id)) return el('span', 'filepill', f.name || 'файл');
  const url = `/m/api/chat/attachments/${enc(id)}/file?inline=true`;
  const kind = mediaKind(f);
  if (kind === 'image') {
    const a = el('a', 'media'); a.href = url; a.target = '_blank'; a.rel = 'noopener';
    a.setAttribute('aria-label', `Открыть фото ${f.name || ''}`.trim());
    const img = el('img'); img.alt = f.name || 'фото'; img.loading = 'lazy'; img.decoding = 'async'; img.src = url;
    img.addEventListener('load', () => stick());
    a.appendChild(img); return a;
  }
  if (kind === 'video') {
    const v = el('video', 'media'); v.controls = true; v.preload = 'metadata';
    v.setAttribute('playsinline', ''); v.setAttribute('aria-label', `Видео ${f.name || ''}`.trim()); v.src = url;
    return v;
  }
  const a = el('a', 'filepill', f.name || 'файл'); a.href = `/m/api/chat/attachments/${enc(id)}/file`;
  a.setAttribute('download', f.name || ''); a.setAttribute('aria-label', `Скачать ${f.name || 'файл'}`);
  return a;
}

/** One exchange: the owner's message + the operator's work (timeline, answer, notices, footer). */
function newTurn(text, atts, opts = {}) {
  showEmpty(false);
  const root = el('article', 'turn');
  const u = el('div', 'u');
  if (atts && atts.length) { const strip = el('div', 'u-media'); atts.forEach((f) => strip.appendChild(mediaNode(f))); u.appendChild(strip); }
  if (text) u.appendChild(el('div', 'bubble', text));
  const a = el('div', 'a');
  const tl = el('ol', 'tl'); tl.setAttribute('aria-label', 'Ход выполнения');
  const ans = el('div', 'answer md');
  const notes = el('div', 'notes');
  const files = el('div', 'a-files'); files.hidden = true;
  const foot = el('div', 'foot');
  a.append(tl, ans, files, notes, foot);
  root.append(u, a);
  $('log').appendChild(root);
  const turn = { root, u, a, tl, ans, notes, files, foot, text: '', prompt: text, atts: atts || [], lastSeq: 0, cards: new Map(),
    reasons: new Map(), t0: opts.historic ? 0 : performance.now(), tokIn: 0, tokOut: 0, ctx: 0, live: null, liveLabel: '',
    taskId: null, waitApproval: false, statusShort: 'работает', paintT: 0, ended: false };
  stick(!opts.historic);
  return turn;
}
function liveRow(turn) {
  if (!turn.live) {
    const li = el('li', 'tl-row live'); li.setAttribute('role', 'status'); li.setAttribute('aria-live', 'polite');
    const spin = el('span', 'spin'); spin.setAttribute('aria-hidden', 'true');
    const label = el('span', 'tl-label'); const time = el('span', 'tl-time');
    li.append(spin, label, time); turn.live = { li, label, time };
  }
  turn.tl.appendChild(turn.live.li);            // always the last row of the timeline
  return turn.live;
}
function setLive(turn, label, short) {
  if (turn.ended) return;
  turn.liveLabel = label; if (short) turn.statusShort = short;
  const r = liveRow(turn); r.label.textContent = label; tickTurn(turn);
  renderStatus(); stick();
}
function tickTurn(turn) {
  if (!turn.live || turn.ended) return;
  const R = S.run && S.run.turn === turn ? S.run : null;
  const sec = turn.t0 ? (performance.now() - turn.t0) / 1000 : 0;
  if (R && Date.now() - R.lastAt >= CFG.silenceMs && !turn.waitApproval) {
    turn.live.label.textContent = `ПК работает, ответа пока нет (${Math.round((Date.now() - R.lastAt) / 1000)} с)`;
    turn.live.li.classList.add('quiet');
  } else {
    turn.live.label.textContent = turn.liveLabel; turn.live.li.classList.remove('quiet');
  }
  turn.live.time.textContent = sec >= 1 ? fmtSec(sec) : '';
}
function endLive(turn) { if (turn.live) { turn.live.li.remove(); turn.live = null; } turn.ended = true; }
function phaseRow(turn, label, cls) {
  const li = el('li', `tl-row phase ${cls || ''}`.trim());
  const mark = el('span', 'mark', cls === 'bad' ? '✕' : '✓'); mark.setAttribute('aria-hidden', 'true');
  li.append(mark, el('span', 'tl-label', label));
  turn.tl.appendChild(li);
  if (turn.live) turn.tl.appendChild(turn.live.li);
  return li;
}

const TOOL_NAMES = {
  'terminal.run': 'Команда в терминале', 'shell.run': 'Команда в терминале', 'terminal.exec': 'Команда в терминале',
  'computer.observe': 'Смотрит на экран', 'computer.screenshot': 'Снимок экрана', 'computer.act': 'Действие на экране',
  'computer.click': 'Нажатие на экране', 'computer.type': 'Ввод текста', 'computer.key': 'Нажатие клавиш',
  'code.read': 'Читает файл', 'fs.read': 'Читает файл', 'file.read': 'Читает файл',
  'code.write': 'Записывает файл', 'fs.write': 'Записывает файл', 'file.write': 'Записывает файл',
  'code.patch': 'Правит файл', 'fs.list': 'Смотрит папку', 'code.search': 'Ищет в коде', 'fs.search': 'Ищет файлы',
  'web.search': 'Ищет в интернете', 'web.fetch': 'Открывает страницу', 'browser.open': 'Открывает страницу',
  'app.launch': 'Запускает программу', 'process.list': 'Смотрит процессы', 'photo.edit': 'Правит фото',
  'chat.share_file': 'Передаёт файл', 'memory.search': 'Вспоминает', 'memory.write': 'Запоминает',
};
function humanTool(t) {
  const name = String(t || '');
  if (TOOL_NAMES[name]) return TOOL_NAMES[name];
  const pre = name.split('.')[0];
  return ({ terminal: 'Команда в терминале', shell: 'Команда в терминале', computer: 'Действие на ПК', fs: 'Работа с файлами',
    file: 'Работа с файлами', code: 'Работа с кодом', browser: 'Браузер', web: 'Интернет', app: 'Программа' })[pre] || name || 'Инструмент';
}
function argsLine(args) {
  if (!args || typeof args !== 'object') return '';
  const main = args.command || args.cmd || args.path || args.url || args.query || args.text || args.app || args.target;
  const line = typeof main === 'string' ? main
    : Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' ');
  return mask(line).replace(/\s+/g, ' ').slice(0, 160);
}
function toolCard(turn, ev) {
  const key = String(ev.call_id || `x${ev.seq || turn.cards.size}`);
  let c = turn.cards.get(key);
  if (c) return c;
  const li = el('li', 'tl-row tool');
  const d = el('details', 'toolcard');
  const s = el('summary');
  const mark = el('span', 'mark run'); mark.setAttribute('aria-hidden', 'true');
  const main = el('span', 'tool-main');
  const name = el('span', 'tool-name', humanTool(ev.tool));
  const sub = el('span', 'tool-sub', argsLine(ev.args));
  main.append(name, sub);
  const state = el('span', 'tool-state', '…');
  s.append(mark, main, state);
  const body = el('div', 'tool-body');
  body.appendChild(el('div', 'kv', `Инструмент: ${ev.tool || '—'}`));
  if (ev.args && typeof ev.args === 'object' && Object.keys(ev.args).length) {
    body.appendChild(el('div', 'kv', 'Аргументы'));
    body.appendChild(el('pre', 'mono', mask(JSON.stringify(ev.args, null, 2)).slice(0, 8000)));
  }
  d.append(s, body); li.appendChild(d);
  turn.tl.appendChild(li);
  if (turn.live) turn.tl.appendChild(turn.live.li);
  c = { li, d, s, mark, sub, state, body, done: false };
  turn.cards.set(key, c);
  stick();
  return c;
}
function toolResult(turn, ev) {
  const c = toolCard(turn, ev);
  if (c.done) return;
  c.done = true;
  const ok = ev.ok !== false;
  c.mark.className = `mark ${ok ? 'ok' : 'bad'}`; c.mark.textContent = ok ? '✓' : '✕';
  c.state.textContent = `${ok ? '' : 'ошибка'}${ev.duration_ms != null ? `${ok ? '' : ' · '}${fmtSec(ev.duration_ms / 1000)}` : ''}` || (ok ? 'готово' : 'ошибка');
  c.li.classList.add(ok ? 'is-ok' : 'is-bad');
  if (ev.summary) c.sub.textContent = mask(ev.summary).replace(/\s+/g, ' ').slice(0, 160);
  if (ev.summary || ev.preview) {
    c.body.appendChild(el('div', 'kv', 'Результат'));
    if (ev.summary) c.body.appendChild(el('div', 'tool-summary', mask(ev.summary)));
    if (ev.preview) c.body.appendChild(el('pre', 'mono', mask(ev.preview).slice(0, 20000) + (ev.truncated ? '\n… (обрезано)' : '')));
  }
}
function toolDenied(turn, ev) {
  const c = toolCard(turn, { call_id: ev.call_id || `denied-${ev.seq || Date.now()}`, tool: ev.tool, args: {} });
  c.done = true; c.mark.className = 'mark bad'; c.mark.textContent = '✕'; c.state.textContent = 'запрещено';
  c.li.classList.add('is-bad');
  if (ev.reason) { c.sub.textContent = mask(ev.reason).slice(0, 160); c.body.appendChild(el('div', 'tool-summary', mask(ev.reason))); }
}
const APPROVAL_KIND = { tool: 'действие инструмента', terminal: 'команда в терминале', computer: 'действие на ПК', file_write: 'запись файла',
  budget: 'продление лимита', cloud: 'отправка в облако', self_improve: 'самоулучшение' };
function approvalCard(turn, ev) {
  const key = `ap${ev.id}`;
  if (turn.cards.has(key)) return turn.cards.get(key);
  const li = el('li', 'tl-row approval');
  const box = el('div', 'card apcard'); box.setAttribute('role', 'group'); box.setAttribute('aria-label', 'Нужно ваше подтверждение');
  box.appendChild(el('strong', '', 'Нужно ваше подтверждение'));
  const kind = ev.approval_kind || ev.kind_name || '';
  if (kind) box.appendChild(el('span', 'muted', APPROVAL_KIND[kind] || kind));
  if (ev.preview) box.appendChild(el('pre', 'mono', mask(ev.preview).slice(0, 4000)));
  const row = el('div', 'row');
  const err = el('div', 'aperr'); err.hidden = true;
  const decide = async (approve) => {
    yes.disabled = no.disabled = true; err.hidden = true;
    try {
      await api('POST', `/approvals/${enc(ev.id)}`, { approve });
      decided(turn, ev.id, approve);
    } catch (e) {
      yes.disabled = no.disabled = false; err.textContent = errText(e); err.hidden = false;
    }
  };
  const no = btn('btn', 'Отклонить', 'Отклонить действие', () => decide(false));
  const yes = btn('btn primary', 'Разрешить', 'Разрешить действие', () => decide(true));
  row.append(no, yes); box.append(row, err); li.appendChild(box);
  turn.tl.appendChild(li);
  if (turn.live) turn.tl.appendChild(turn.live.li);
  const c = { li, row, box, pending: true };
  turn.cards.set(key, c);
  turn.waitApproval = true;
  if (S.run && S.run.turn === turn) setLive(turn, 'Ждёт вашего подтверждения', 'ждёт подтверждения');
  stick();
  return c;
}
function decided(turn, id, approve) {
  const c = turn.cards.get(`ap${id}`); if (!c || !c.pending) return;
  c.pending = false;
  c.row.replaceWith(el('div', approve ? 'done ok' : 'done bad', approve ? '✓ Разрешено' : '✕ Отклонено'));
  turn.waitApproval = [...turn.cards.entries()].some(([k, v]) => k.startsWith('ap') && v.pending);
  if (S.run && S.run.turn === turn && !turn.waitApproval) setLive(turn, approve ? 'Выполняет…' : 'Действие отклонено, думает дальше…', 'работает');
  renderStatus();
}
function reasonBlock(turn, ev) {
  const key = String(ev.step || 0);
  let r = turn.reasons.get(key);
  if (!r) {
    const li = el('li', 'tl-row reason');
    const d = el('details', 'reasoncard'); const s = el('summary', '', 'Размышление'); const body = el('div', 'reason-text');
    d.append(s, body); li.appendChild(d); turn.tl.appendChild(li);
    if (turn.live) turn.tl.appendChild(turn.live.li);
    r = { body, text: '' }; turn.reasons.set(key, r);
  }
  r.text += ev.text || ev.delta || '';
  r.body.textContent = r.text.slice(-6000);
}

const mdOpts = { copy: (code, b) => copyText(code, b) };
function paintAnswer(turn, now) {
  if (now) {
    if (turn.paintT) { clearTimeout(turn.paintT); turn.paintT = 0; }
    renderMarkdown(turn.ans, turn.text, mdOpts); stick(); return;
  }
  if (turn.paintT) return;
  turn.paintT = setTimeout(() => { turn.paintT = 0; renderMarkdown(turn.ans, turn.text, mdOpts); stick(); }, 50);
}

/** One stream / history event. Dedupe by seq per turn, so a reconnect, a history replay and the live stream never
    render the same delta twice; cards are keyed by call_id / approval id. */
function applyEvent(turn, ev, live) {
  if (!ev || typeof ev !== 'object') return false;
  if (typeof ev.seq === 'number' && ev.seq > 0) {
    if (ev.seq <= turn.lastSeq) return false;
    turn.lastSeq = ev.seq;
  }
  switch (ev.kind) {
    case 'task.queued': if (live) setLive(turn, 'В очереди на ПК…', 'в очереди'); break;
    case 'task.started': if (live) setLive(turn, 'Планирует…', 'думает'); break;
    case 'task.paused': if (live) setLive(turn, 'Пауза…', 'пауза'); break;
    case 'task.progress': if (live && ev.text) setLive(turn, mask(ev.text).slice(0, 120), 'работает'); break;
    case 'run.usage':
      if (ev.step_tokens_in) turn.tokIn = ev.step_tokens_in;
      if (ev.step_tokens_out) turn.tokOut += ev.step_tokens_out;
      if (ev.context_window) turn.ctx = ev.context_window;
      if (live) renderFoot(turn);
      break;
    case 'run.reasoning_delta': reasonBlock(turn, ev); if (live) setLive(turn, 'Планирует…', 'думает'); break;
    case 'run.assistant_delta':
    case 'run.answer_delta': {
      const t = ev.text != null ? ev.text : ev.delta;
      if (!t) break;
      turn.text += t;
      if (live) { setLive(turn, 'Пишет ответ…', 'пишет ответ'); paintAnswer(turn); } else paintAnswer(turn, true);
      break;
    }
    case 'run.answer_reset': turn.text = ''; paintAnswer(turn, true); break;
    case 'run.assistant_message':
      if (ev.text && ev.text !== turn.text) { turn.text = ev.text; paintAnswer(turn, !live); }
      break;
    case 'run.tool_use':
      toolCard(turn, ev);
      if (live) setLive(turn, `Выполняет: ${humanTool(ev.tool)}…`, `выполняет: ${humanTool(ev.tool).toLowerCase()}`);
      break;
    case 'run.tool_result': toolResult(turn, ev); if (live) setLive(turn, 'Проверяет результат…', 'проверяет'); break;
    case 'tool.denied': toolDenied(turn, ev); break;
    case 'approval.created': approvalCard(turn, ev); break;
    case 'approval.decided': decided(turn, ev.id, !(ev.approve === false || ev.status === 'rejected')); break;
    default: break;
  }
  return true;
}

function renderFoot(turn, final) {
  const bits = [];
  if (final && turn.t0) bits.push(fmtSec((performance.now() - turn.t0) / 1000));
  if (turn.tokIn || turn.tokOut) bits.push(`↑${fmtTok(turn.tokIn)} ↓${fmtTok(turn.tokOut)} ток.`);
  if (turn.tokIn && turn.ctx) bits.push(`контекст ${Math.min(100, Math.round((turn.tokIn / turn.ctx) * 100))}%`);
  turn.foot.textContent = bits.join(' · ');
}
function noticeRow(turn, cls, text, retry) {
  const n = el('div', `notice ${cls}`); n.setAttribute('role', cls === 'err' ? 'alert' : 'status');
  n.appendChild(el('span', 'notice-text', text));
  if (retry) n.appendChild(btn('btn', 'Повторить', 'Повторить запрос', () => { n.remove(); retry(); }));
  turn.notes.appendChild(n); stick();
  return n;
}
const ruStatus = { failed: 'задача завершилась с ошибкой', blocked: 'Bossman не допустил задачу', cancelled: 'задача отменена', missing: 'задача не найдена на ПК' };

function finishTurn(turn, t, opts = {}) {
  const status = (t && t.status) || 'completed';
  const result = t && typeof t.result === 'string' ? t.result : '';
  if (result.trim() && result !== turn.text) turn.text = result;
  if (turn.text) paintAnswer(turn, true); else if (turn.paintT) { clearTimeout(turn.paintT); turn.paintT = 0; }
  endLive(turn);
  turn.waitApproval = false;
  [...turn.cards.values()].forEach((c) => { if (c.mark && !c.done) { c.mark.className = 'mark'; c.state.textContent = '—'; } });
  const retry = turn.prompt && !opts.historic ? () => sendMessage(turn.prompt, turn.atts) : (turn.prompt ? () => sendMessage(turn.prompt, turn.atts) : null);
  if (status === 'completed') {
    if (!turn.text.trim()) noticeRow(turn, 'err', 'Модель вернула пустой ответ', retry);
    else if (!opts.historic) phaseRow(turn, 'Готово', 'good');
  } else if (status === 'stopped') {
    noticeRow(turn, 'muted', 'Остановлено');
  } else {
    const why = (t && (t.failure_summary || t.error)) || ruStatus[status] || `задача: ${status}`;
    noticeRow(turn, 'err', `Ошибка: ${typeof why === 'string' ? why : JSON.stringify(why)}`, retry);
    if (!opts.historic) phaseRow(turn, 'Ошибка', 'bad');
  }
  renderFoot(turn, !opts.historic);
}

/* ------------------------------------------------------------------ following a running task */
function stopFollow(R) {
  if (!R) return;
  if (R.es) { try { R.es.close(); } catch { /* closed */ } R.es = null; }
  clearInterval(R.poll); clearInterval(R.apTimer); clearTimeout(R.retryT); R.retryT = 0;
}
function follow(turn, taskId) {
  if (S.run && !S.run.done) { stopFollow(S.run); }
  const R = { turn, taskId, done: false, es: null, poll: 0, apTimer: 0, retryT: 0, backoff: 0, lastAt: Date.now(), polling: false, sseErr: 0 };
  S.run = R;
  turn.taskId = taskId; turn.root.dataset.task = String(taskId); S.turns.set(String(taskId), turn);
  if (!turn.understood) { turn.understood = true; phaseRow(turn, 'Понял задачу', 'good'); }
  if (!turn.liveLabel) setLive(turn, 'Ждёт начала на ПК…', 'работает'); else setLive(turn, turn.liveLabel);
  openStream(R);
  R.poll = setInterval(() => pollTask(R), CFG.pollMs);
  R.apTimer = setInterval(() => scanApprovals(R), CFG.approvalsMs);
  updateComposer(); renderStatus();
  return R;
}
function onStreamEvent(R, ev, live) {
  if (R.done || !ev) return;
  if (ev.task_id != null && String(ev.task_id) !== String(R.taskId)) return;
  if (applyEvent(R.turn, ev, live)) R.lastAt = Date.now();
  const end = END_KIND[ev.kind];
  if (end) settle(R, null, end);
}
function openStream(R) {
  if (R.done) return;
  if (R.es) { try { R.es.close(); } catch { /* */ } }
  let es;
  try { es = new EventSource(`/m/api/tasks/${enc(R.taskId)}/events?after=${R.turn.lastSeq}`); } catch { R.es = null; scheduleReconnect(R); return; }
  R.es = es;
  es.onopen = () => { R.backoff = 0; R.sseErr = 0; setConn(true); };
  es.onmessage = (m) => { let ev; try { ev = JSON.parse(m.data); } catch { return; } onStreamEvent(R, ev, true); };
  es.onerror = () => {
    if (R.done || R.es !== es) return;
    R.sseErr += 1;
    // CLOSED (the server refused) or a reconnect loop: our own reconnect with the last seen seq + history replay
    if (es.readyState === 2 || R.sseErr >= 3) { try { es.close(); } catch { /* */ } R.es = null; scheduleReconnect(R); }
  };
}
function scheduleReconnect(R) {
  if (R.retryT || R.done) return;
  const delay = Math.min(15000, CFG.reconnectBaseMs * (2 ** R.backoff)); R.backoff = Math.min(R.backoff + 1, 6);
  R.retryT = setTimeout(async () => {
    R.retryT = 0;
    try { await replay(R); } catch { /* offline: the poller keeps trying */ }
    if (!R.done) openStream(R);
  }, delay);
}
async function replay(R) {
  const h = await api('GET', `/tasks/${enc(R.taskId)}/history`);
  const evs = (h && Array.isArray(h.events) ? h.events : []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  for (const ev of evs) { if (R.done) break; onStreamEvent(R, ev, true); }
}
async function pollTask(R) {
  if (R.done || R.polling) return;
  R.polling = true;
  try {
    const t = await api('GET', `/tasks/${enc(R.taskId)}`);
    if (t && TERMINAL.has(t.status)) {
      try { await replay(R); } catch { /* the result below is still shown */ }
      settle(R, t);
    } else if (!R.es || R.es.readyState !== 1) {
      try { await replay(R); } catch { /* next tick */ }
    }
  } catch { /* offline: status shows «нет связи» */ } finally { R.polling = false; }
}
async function scanApprovals(R) {
  if (R.done) return;
  try {
    const r = await api('GET', '/approvals');
    for (const it of (r && r.items) || []) {
      if (String(it.task_id) === String(R.taskId) && it.status !== 'approved' && it.status !== 'rejected') {
        approvalCard(R.turn, { id: it.id, approval_kind: it.kind, preview: it.preview });
      }
    }
  } catch { /* next tick */ }
}
async function settle(R, t, endStatus) {
  if (R.done) return;
  R.done = true; stopFollow(R);
  if (!t) { try { t = await api('GET', `/tasks/${enc(R.taskId)}`); } catch { t = null; } }
  if (!t || !TERMINAL.has(t.status)) t = { ...(t || {}), status: endStatus || (t && t.status) || 'completed' };
  finishTurn(R.turn, t);
  if (S.run === R) S.run = null;
  updateComposer(); renderStatus();
  loadTurnFiles(R.turn);
  const q = await loadQueue();
  if (q.length) followQueue();
}
async function loadTurnFiles(turn) {
  if (!S.threadId || turn.taskId == null) return;
  try {
    const t = await api('GET', `/threads/${enc(S.threadId)}`);
    const row = (t.turns || []).find((x) => String(x.task_id) === String(turn.taskId));
    if (row) showTurnFiles(turn, row.files);
  } catch { /* the answer is shown; files are optional */ }
}
function showTurnFiles(turn, files) {
  if (!Array.isArray(files) || !files.length) return;
  turn.files.replaceChildren(el('div', 'kv', 'Файлы от Bossman'));
  files.forEach((f) => turn.files.appendChild(mediaNode(f)));
  turn.files.hidden = false; stick();
}

/* ------------------------------------------------------------------ threads: one continuous conversation */
function clearLog() {
  if (S.run && !S.run.done) { stopFollow(S.run); S.run = null; }
  $('log').querySelectorAll('.turn, .sysrow').forEach((n) => n.remove());
  S.turns = new Map();
}
async function openThread(tid) {
  const t = await api('GET', `/threads/${enc(tid)}`);
  clearLog();
  S.threadId = t.id || tid; store.set('thread', S.threadId);
  renderTurns(t.turns || [], true);
  showEmpty(!S.turns.size && !$('log').querySelector('.sysrow'));
  stick(true);
  loadQueue();
  return t;
}
function sysRow(text) {
  const n = el('div', 'sysrow', text); $('log').appendChild(n); showEmpty(false); stick(); return n;
}
function renderTurns(turns, initial) {
  const recent = turns.length - 6;
  turns.forEach((row, i) => {
    if (row.kind === 'note') {
      const key = `note-${row.idx != null ? row.idx : i}`;
      if (S.turns.has(key)) return;
      S.turns.set(key, sysRow(row.text || 'Заметка Bossman'));
      return;
    }
    if (row.task_id == null || S.turns.has(String(row.task_id))) return;
    const turn = newTurn(row.text || '', Array.isArray(row.attachments) ? row.attachments : [], { historic: true });
    turn.taskId = row.task_id; turn.root.dataset.task = String(row.task_id); S.turns.set(String(row.task_id), turn);
    if (!TERMINAL.has(row.status)) {
      turn.t0 = performance.now(); turn.understood = true; phaseRow(turn, 'Понял задачу', 'good');
      if (!S.run || S.run.done) follow(turn, row.task_id);
      return;
    }
    if (row.result) turn.text = row.result;
    if (initial && i >= recent) {
      api('GET', `/tasks/${enc(row.task_id)}/history`).then((h) => {
        (h.events || []).forEach((ev) => {
          if (/^run\.tool|^approval|^tool\.denied|^run\.usage|^run\.reasoning/.test(ev.kind || '')) applyEvent(turn, ev, false);
        });
        renderFoot(turn);
      }).catch(() => {});
    }
    finishTurn(turn, { status: row.status, result: row.result, error: row.error, failure_summary: row.failure_summary }, { historic: true });
    showTurnFiles(turn, row.files);
  });
}
async function syncThread() {
  if (!S.threadId) return;
  try { const t = await api('GET', `/threads/${enc(S.threadId)}`); renderTurns(t.turns || [], false); }
  catch { /* next time */ }
}
async function resumeConversation() {
  const saved = store.get('thread');
  if (saved) {
    try { await openThread(saved); return; } catch (err) {
      if (err.status !== 404) { showEmpty(true); toast(`Не удалось открыть разговор: ${errText(err)}`); S.threadId = saved; return; }
      store.set('thread', null);
    }
  }
  try {
    const r = await api('GET', '/threads');
    const items = ((r && r.items) || []).slice().sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
    if (items[0]) { await openThread(items[0].id); return; }
  } catch { /* fresh conversation below */ }
  showEmpty(true);
}
function newConversation() {
  clearLog(); S.threadId = null; store.set('thread', null); S.queue = []; renderQueue([]);
  showEmpty(true); updateComposer(); renderStatus();
}
async function ensureThread(text) {
  if (!S.threadId) {
    const t = await api('POST', '/threads', { title: (text || 'Оператор ПК').slice(0, 60) });
    S.threadId = t.id; store.set('thread', t.id);
  }
  if (!S.patched.has(S.threadId)) {
    // AUTO without an explicit flag would mean «no approvals» on the PC: the flag follows the operator's total_control
    try {
      await api('PATCH', `/agent/sessions/${enc(S.threadId)}`, { mode: 'AUTO', bypass_permissions: !!(S.op && S.op.total_control) });
      S.patched.add(S.threadId);
    } catch { /* an older Bossman: the message still carries mode AUTO */ }
  }
  return S.threadId;
}

/* ------------------------------------------------------------------ send / queue / steer */
const newReqId = () => `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
async function sendMessage(text, atts, opts = {}) {
  const turn = opts.turn || newTurn(text, atts);
  turn.prompt = text; turn.atts = atts || []; turn.ended = false;
  const crid = opts.crid || newReqId();
  S.sending = true; updateComposer();
  setLive(turn, 'Отправляю…', 'отправляю');
  try {
    if (!S.op) await loadOperator();
    if (S.warmP) { setLive(turn, 'В очереди: модель загружается…', 'в очереди'); await S.warmP; }
    const agentId = opAgent();
    if (agentId == null) throw new Error((S.op && S.op.reason) || 'Оператор ПК не настроен в Bossman');
    const tid = await ensureThread(text);
    const payload = { text, mode: 'AUTO', agent_id: agentId, client_request_id: crid };
    if (turn.atts.length) payload.attachments = turn.atts.map((a) => a.id).filter(Boolean);
    const r = await api('POST', `/agent/sessions/${enc(tid)}/messages`, payload);
    if (r && r.admission && r.admission.ok === false) { failSend(turn, r.admission.reason || 'Bossman не принял задачу'); return; }
    const id = r && r.task && r.task.id != null ? r.task.id : (r ? r.task_id : null);
    if (id == null) { failSend(turn, 'Bossman не вернул задачу'); return; }
    follow(turn, id);
  } catch (err) {
    // a network failure may have reached the PC: «Повторить» reuses the same client_request_id (no duplicate task)
    failSend(turn, errText(err), err.status === 0 ? () => sendMessage(text, atts, { turn, crid }) : null);
  } finally {
    S.sending = false; updateComposer(); renderStatus();
  }
}
function failSend(turn, msg, retry) {
  endLive(turn);
  noticeRow(turn, 'err', `Ошибка: ${msg}`, retry || (() => sendMessage(turn.prompt, turn.atts)));
}
async function loadQueue() {
  if (!S.threadId) { renderQueue([]); return []; }
  try { const r = await api('GET', `/threads/${enc(S.threadId)}/queue`); renderQueue((r && r.items) || []); return S.queue; }
  catch { return S.queue; }
}
function renderQueue(items) {
  S.queue = items || [];
  const box = $('queue'); box.replaceChildren();
  const running = S.run && !S.run.done;
  S.queue.forEach((it) => {
    const row = el('div', 'qitem'); row.dataset.id = String(it.id);
    const head = el('div', 'qhead');
    head.append(el('span', 'qtag', it.kind === 'steer_leftover' ? 'следующим ходом' : 'в очереди'), el('span', 'qtext', it.text || ''));
    const x = btn('qx', null, 'Отменить сообщение из очереди', () => cancelQueued(it));
    x.appendChild(icon('x')); head.appendChild(x);
    row.appendChild(head);
    if (running) row.appendChild(btn('btn small', 'Дополнить текущую задачу', 'Дополнить текущую задачу этим сообщением', () => steerQueued(it)));
    box.appendChild(row);
  });
  box.hidden = !S.queue.length;
}
async function enqueue(text) {
  const tid = S.threadId;
  try {
    const r = await api('POST', `/threads/${enc(tid)}/queue`, { text, agent_id: opAgent() });
    if (r && Array.isArray(r.items)) renderQueue(r.items); else await loadQueue();
    toast('В очереди: уйдёт после текущей задачи');
  } catch (err) { $('input').value = text; grow(); toast(`Не удалось поставить в очередь: ${errText(err)}`); }
}
async function cancelQueued(it) {
  try { await api('DELETE', `/threads/${enc(S.threadId)}/queue/${enc(it.id)}`); } catch (err) { if (err.status !== 404) toast(errText(err)); }
  loadQueue();
}
async function steerQueued(it) {
  const run = S.run && !S.run.done ? S.run : null;
  try {
    await api('POST', `/threads/${enc(S.threadId)}/steer`, { text: it.text });
  } catch (err) {
    toast(err.code === 'CHAT_NOTHING_RUNNING' ? 'Задача уже закончилась — сообщение уйдёт следующим' : `Не удалось дополнить: ${errText(err)}`);
    return;
  }
  try { await api('DELETE', `/threads/${enc(S.threadId)}/queue/${enc(it.id)}`); } catch { /* already gone */ }
  if (run) { const li = el('li', 'tl-row steer'); li.append(el('span', 'mark', '+'), el('span', 'tl-label', `Вы дополнили: ${it.text}`)); run.turn.tl.appendChild(li); if (run.turn.live) run.turn.tl.appendChild(run.turn.live.li); }
  toast('Оператор увидит это на следующем шаге');
  loadQueue();
}
/* after a turn: the PC sends the next queued message by itself — show its turn as soon as it exists */
function followQueue() {
  const tid = S.threadId; let tries = 0;
  const tick = async () => {
    if (S.threadId !== tid || (S.run && !S.run.done) || ++tries > 20) return;
    await syncThread();
    if (S.run && !S.run.done) { loadQueue(); return; }
    setTimeout(tick, CFG.queueFollowMs);
  };
  setTimeout(tick, CFG.queueFollowMs);
}

/* ------------------------------------------------------------------ composer: text, Фото, Видео, Отправить */
function grow() {
  const i = $('input'); i.style.height = 'auto';
  i.style.height = `${Math.min(i.scrollHeight || 0, 156)}px`;
}
function updateComposer() {
  const running = !!(S.run && !S.run.done);
  const i = $('input'); const send = $('btn-send');
  const uploading = S.files.some((f) => f.state === 'up');
  const hasText = !!i.value.trim(); const hasFiles = S.files.some((f) => f.state === 'ok');
  send.disabled = S.sending || uploading || (!hasText && !hasFiles);
  send.setAttribute('aria-label', running ? 'Поставить сообщение в очередь' : 'Отправить');
  i.placeholder = running ? 'Следующее сообщение встанет в очередь…' : 'Задача для ПК…';
  const hint = $('hint');
  hint.textContent = running ? 'Идёт задача: новое сообщение встанет в очередь. STOP — вверху.' : (uploading ? 'Загружаю файл…' : '');
  hint.hidden = !hint.textContent;
  renderQueue(S.queue);
}
function renderFiles() {
  const box = $('files'); box.replaceChildren();
  S.files.forEach((f) => {
    const chip = el('div', `fchip ${f.state}`);
    if (f.kind === 'image' && f.url) { const img = el('img', 'fthumb'); img.alt = ''; img.src = f.url; chip.appendChild(img); }
    else { const g = el('span', 'fthumb glyph'); g.appendChild(icon(f.kind === 'video' ? 'video' : 'photo')); chip.appendChild(g); }
    const info = el('div', 'finfo');
    info.appendChild(el('span', 'fname', f.name));
    if (f.state === 'up') {
      const bar = el('div', 'fbar'); bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
      bar.setAttribute('aria-valuenow', String(Math.round(f.pct * 100))); bar.setAttribute('aria-label', `Загрузка ${f.name}`);
      const fill = el('i'); fill.style.width = `${Math.round(f.pct * 100)}%`; bar.appendChild(fill); info.appendChild(bar);
      f.fill = fill; f.bar = bar;
    } else if (f.state === 'err') {
      info.appendChild(el('span', 'ferr', f.error || 'не загрузилось'));
    } else info.appendChild(el('span', 'fok', 'готово'));
    chip.appendChild(info);
    if (f.state === 'err') chip.appendChild(btn('btn small', 'Повторить', `Повторить загрузку ${f.name}`, () => startUpload(f)));
    const x = btn('fx', null, `Убрать ${f.name}`, () => removeFile(f)); x.appendChild(icon('x')); chip.appendChild(x);
    box.appendChild(chip);
  });
  box.hidden = !S.files.length;
  updateComposer();
}
function removeFile(f) {
  if (f.xhrAbort) f.xhrAbort();
  if (f.url) { try { URL.revokeObjectURL(f.url); } catch { /* */ } }
  S.files = S.files.filter((x) => x !== f); renderFiles();
}
function addFiles(list, kind) {
  for (const file of Array.from(list || [])) {
    const k = kind || mediaKind({ name: file.name, mime: file.type });
    const f = { file, name: file.name || 'файл', kind: k, state: 'up', pct: 0, att: null, url: '', error: '' };
    if (k === 'image') { try { f.url = URL.createObjectURL(file); } catch { f.url = ''; } }
    S.files.push(f); startUpload(f);
  }
  renderFiles();
}
async function startUpload(f) {
  f.state = 'up'; f.pct = 0; f.error = ''; renderFiles();
  try {
    f.att = await uploadFile(f.file, (p) => {
      f.pct = p;
      if (f.fill) f.fill.style.width = `${Math.round(p * 100)}%`;
      if (f.bar) f.bar.setAttribute('aria-valuenow', String(Math.round(p * 100)));
    });
    if (!S.files.includes(f)) return;
    f.state = 'ok';
  } catch (err) {
    if (!S.files.includes(f)) return;
    f.state = 'err'; f.error = errText(err);
  }
  renderFiles();
}

async function onSubmit() {
  const i = $('input');
  const text = i.value.trim();
  if (S.files.some((f) => f.state === 'up')) { toast('Дождитесь загрузки файлов'); return; }
  if (S.files.some((f) => f.state === 'err')) { toast('Файл не загрузился: «Повторить» или уберите его'); return; }
  const files = S.files.filter((f) => f.state === 'ok');
  if (!text && !files.length) return;
  if (!files.length && /^\/[a-zа-яё]+$/i.test(text) && runSlash(text)) { i.value = ''; grow(); hideSlash(); updateComposer(); return; }
  const running = S.run && !S.run.done;
  if (running) {
    if (files.length) { toast('Фото и видео уйдут после текущей задачи — дождитесь её или нажмите STOP'); return; }
    i.value = ''; grow(); store.set('draft', null); updateComposer();
    await enqueue(text);
    return;
  }
  if (S.sending) return;                               // double tap
  const atts = files.map((f) => f.att);
  i.value = ''; grow(); store.set('draft', null);
  S.files.forEach((f) => { if (f.url) { try { URL.revokeObjectURL(f.url); } catch { /* */ } } });
  S.files = []; renderFiles(); hideSlash();
  await sendMessage(text || 'Посмотри вложения.', atts);
}

/* ------------------------------------------------------------------ slash commands (power-user path, no buttons) */
const SLASH = [
  { cmd: '/new', label: 'Новый разговор', run: () => { newConversation(); toast('Новый разговор'); } },
  { cmd: '/stop', label: 'Остановить задачу этого чата', run: () => stopThisChat() },
  { cmd: '/compact', label: 'Сжать контекст разговора', run: () => compact() },
  { cmd: '/sessions', label: 'Прошлые разговоры', run: () => openSessions() },
  { cmd: '/file', label: 'Прикрепить любой файл', run: () => $('pick-file').click() },
  { cmd: '/logout', label: 'Выйти на этом телефоне', run: () => logout() },
];
function runSlash(text) {
  const c = SLASH.find((x) => x.cmd === text.toLowerCase());
  if (!c) return false;
  c.run(); return true;
}
function renderSlash() {
  const i = $('input'); const box = $('slash');
  const v = i.value;
  if (!/^\/[a-zа-яё]*$/i.test(v)) { hideSlash(); return; }
  const hits = SLASH.filter((x) => x.cmd.startsWith(v.toLowerCase()));
  box.replaceChildren();
  hits.forEach((x) => {
    const b = btn('slash-item', null, `${x.cmd} — ${x.label}`, () => { i.value = ''; grow(); hideSlash(); updateComposer(); x.run(); });
    b.setAttribute('role', 'option');
    b.append(el('span', 'slash-cmd', x.cmd), el('span', 'slash-label', x.label));
    box.appendChild(b);
  });
  box.hidden = !hits.length;
}
function hideSlash() { $('slash').hidden = true; }
async function stopThisChat() {
  if (!S.threadId) { toast('Нечего останавливать'); return; }
  try { await api('POST', `/threads/${enc(S.threadId)}/stop`); toast('Задача остановлена'); if (S.run) setTimeout(() => S.run && pollTask(S.run), 300); }
  catch (err) { toast(errText(err)); }
}
async function compact() {
  if (!S.threadId) { toast('Разговор ещё пуст'); return; }
  const row = sysRow('Сжимаю контекст разговора…');
  try {
    const r = await api('POST', `/agent/sessions/${enc(S.threadId)}/compact`, opAgent() != null ? { agent_id: opAgent() } : {});
    row.textContent = r && r.status === 'failed' ? 'Сжать контекст не удалось' : 'Контекст сжимается на ПК — следующий ответ учтёт резюме';
  } catch (err) { row.textContent = `Сжать контекст не удалось: ${errText(err)}`; row.classList.add('bad'); }
}
async function logout() {
  try { await api('POST', '/logout'); } catch { /* the session ends anyway */ }
  store.set('thread', null); showLogin('Вы вышли');
}
async function openSessions() {
  const sheet = $('sheet'); const list = $('sheet-list');
  list.replaceChildren(el('div', 'muted pad', 'Загружаю…'));
  sheet.hidden = false; $('sheet-close').focus();
  try {
    const r = await api('GET', '/threads');
    const items = ((r && r.items) || []).slice().sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
    list.replaceChildren();
    list.appendChild(btn('btn primary block', 'Новый разговор', 'Начать новый разговор', () => { closeSheet(); newConversation(); }));
    if (!items.length) list.appendChild(el('div', 'muted pad', 'Разговоров пока нет'));
    items.forEach((t) => {
      const b = btn(`srow${t.id === S.threadId ? ' current' : ''}`, null, `Открыть: ${t.title || 'без названия'}`, async () => {
        closeSheet();
        try { await openThread(t.id); } catch (err) { toast(errText(err)); }
      });
      b.append(el('span', 'stitle', t.title || 'Без названия'), el('span', 'stime', fmtAgo(t.updated_at || t.created_at)));
      list.appendChild(b);
    });
  } catch (err) { list.replaceChildren(el('div', 'muted pad', errText(err))); }
}
function closeSheet() { $('sheet').hidden = true; }

/* ------------------------------------------------------------------ misc UI */
let toastT = 0;
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 3200);
}
async function copyText(text, b) {
  let ok = false;
  try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); ok = true; } } catch { /* fallback */ }
  if (!ok) {
    try {
      const ta = el('textarea', 'offscreen'); ta.value = text; ta.setAttribute('readonly', ''); document.body.appendChild(ta); ta.select();
      ok = typeof document.execCommand === 'function' && document.execCommand('copy'); ta.remove();
    } catch { ok = false; }
  }
  if (b) { b.textContent = ok ? 'Скопировано' : 'Не удалось'; setTimeout(() => { b.textContent = 'Копировать'; }, 1600); }
}
function viewport() {
  const vv = window.visualViewport; const root = document.documentElement;
  if (!vv) return;
  const apply = () => {
    root.style.setProperty('--vvh', `${Math.round(vv.height)}px`);
    root.style.setProperty('--vvt', `${Math.round(vv.offsetTop)}px`);
    if (S.atBottom) stick(true);
  };
  vv.addEventListener('resize', apply); vv.addEventListener('scroll', apply); apply();
}
async function resync() {
  if (!S.started) return;
  refreshStop();
  const R = S.run && !S.run.done ? S.run : null;
  if (R) {
    if (R.es) { try { R.es.close(); } catch { /* */ } R.es = null; }
    try { await replay(R); } catch { /* offline */ }
    if (!R.done) { pollTask(R); openStream(R); }
  } else {
    await syncThread(); loadQueue();
  }
}

/* ------------------------------------------------------------------ start */
function start() {
  $('login').hidden = true; $('app').hidden = false;
  if (S.started) { refreshStop(); loadOperator(); return; }
  S.started = true;
  refreshStop(); loadOperator(); resumeConversation();
  S.timers.push(setInterval(refreshStop, CFG.stopPollMs));
  S.timers.push(setInterval(() => { renderStatus(); if (S.run && !S.run.done) tickTurn(S.run.turn); }, 1000));
}
async function boot() {
  const m = /[#&]pair=([^&]+)/.exec(location.hash || '');
  if (m) {
    try { history.replaceState(null, '', location.pathname + location.search); } catch { /* */ }
    try { await login(decodeURIComponent(m[1])); start(); } catch (err) { showLogin(err.message); }
    return;
  }
  try { const s = await api('GET', '/session'); S.csrf = s.csrf; start(); } catch (err) { if (err.status !== 401) showLogin(err.message); }
}
function wire() {
  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const b = $('login-btn'); if (b.disabled) return;
    const v = $('login-token').value.trim();
    if (!v) { $('login-error').textContent = 'Введите код сопряжения'; return; }
    b.disabled = true; $('login-error').textContent = '';
    try { await login(v); $('login-token').value = ''; start(); } catch (err) { $('login-error').textContent = err.message; }
    finally { b.disabled = false; }
  });
  $('btn-stop').addEventListener('click', stopAll);
  const i = $('input');
  const d0 = store.get('draft'); if (d0) i.value = d0;
  i.addEventListener('input', () => { grow(); renderSlash(); updateComposer(); store.set('draft', i.value ? i.value.slice(0, 8000) : null); });
  const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
  i.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !coarse) { e.preventDefault(); onSubmit(); }   // phone: Enter = new line
    if (e.key === 'Escape') hideSlash();
  });
  $('composer').addEventListener('submit', (e) => { e.preventDefault(); onSubmit(); });
  $('btn-photo').addEventListener('click', () => $('pick-photo').click());
  $('btn-video').addEventListener('click', () => $('pick-video').click());
  $('pick-photo').addEventListener('change', (e) => { addFiles(e.target.files, 'image'); e.target.value = ''; });
  $('pick-video').addEventListener('change', (e) => { addFiles(e.target.files, 'video'); e.target.value = ''; });
  $('pick-file').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  const log = $('log');
  log.addEventListener('scroll', () => {
    S.atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    if (S.atBottom) $('to-bottom').hidden = true;
  }, { passive: true });
  $('to-bottom').addEventListener('click', () => { S.atBottom = true; stick(true); });
  document.querySelectorAll('[data-suggest]').forEach((c) => c.addEventListener('click', () => {
    i.value = c.dataset.suggest; grow(); updateComposer(); i.focus();
  }));
  $('btn-history').addEventListener('click', () => openSessions());
  $('sheet-logout').addEventListener('click', () => { closeSheet(); logout(); });
  $('sheet-close').addEventListener('click', closeSheet);
  $('sheet').addEventListener('click', (e) => { if (e.target === $('sheet')) closeSheet(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('sheet').hidden) closeSheet(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) resync(); });
  window.addEventListener('pageshow', (e) => { if (e.persisted) resync(); });
  window.addEventListener('online', () => resync());
  viewport(); grow(); updateComposer(); renderStatus();
}

wire();
boot();
