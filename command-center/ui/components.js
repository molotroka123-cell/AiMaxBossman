/* ============================================================
   components.js — DOM-фабрика, форматтеры, тосты, модалки,
   статусные точки, спарклайны, пустые состояния.
   Без зависимостей и без innerHTML для пользовательских данных.
   ============================================================ */

/* ---------------- DOM-фабрика ---------------- */

const SVG_NS = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set(['svg', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'g', 'text', 'defs']);

function isChildLike(v) {
  return v === null || v === undefined || typeof v === 'string' || typeof v === 'number'
    || typeof v === 'boolean' || Array.isArray(v) || (v && v.nodeType);
}

/**
 * h('div.card', {onClick, class, style, ...attrs}, ...children)
 * Второй аргумент можно опустить — тогда он считается первым ребёнком.
 */
export function h(tag, attrs, ...children) {
  if (isChildLike(attrs)) { children.unshift(attrs); attrs = null; }

  const parts = String(tag).split('.');
  const name = parts[0] || 'div';
  const el = SVG_TAGS.has(name)
    ? document.createElementNS(SVG_NS, name)
    : document.createElement(name);

  const classes = parts.slice(1);
  if (classes.length) addClass(el, classes.join(' '));

  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;

      if (key === 'class' || key === 'className') { addClass(el, value); continue; }
      if (key === 'style') {
        if (typeof value === 'string') el.setAttribute('style', value);
        else Object.assign(el.style, value);
        continue;
      }
      if (key === 'dataset') { Object.assign(el.dataset, value); continue; }
      if (key === 'ref' && typeof value === 'function') { value(el); continue; }
      if (key === 'svgHtml') { el.innerHTML = value; continue; }  // только для наших констант-иконок
      if (key.startsWith('on') && typeof value === 'function') {
        el.addEventListener(key.slice(2).toLowerCase(), value);
        continue;
      }
      if (key === 'value' || key === 'checked' || key === 'disabled' || key === 'hidden'
        || key === 'selected' || key === 'indeterminate') {
        el[key] = value;
        continue;
      }
      el.setAttribute(key, value === true ? '' : String(value));
    }
  }

  append(el, children);
  return el;
}

function addClass(el, value) {
  const list = String(value).trim().split(/\s+/).filter(Boolean);
  for (const c of list) el.classList.add(c);
}

export function append(parent, child) {
  if (child === null || child === undefined || child === false || child === true) return parent;
  if (Array.isArray(child)) { for (const c of child) append(parent, c); return parent; }
  parent.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  return parent;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function replace(node, ...children) {
  clear(node);
  append(node, children);
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ---------------- Иконки (наши константы, безопасны для innerHTML) ---------------- */

const S = (d, extra = '') =>
  `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"${extra}>${d}</svg>`;

export const ICONS = {
  home:      S('<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/>'),
  models:    S('<rect x="4" y="4" width="16" height="16" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1.2"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/>'),
  agents:    S('<circle cx="12" cy="8" r="3.4"/><path d="M4.5 20c0-3.6 3.4-6 7.5-6s7.5 2.4 7.5 6"/>'),
  tasks:     S('<path d="M4 6h16M4 12h16M4 18h10"/><circle cx="19" cy="18" r="2.2"/>'),
  schedules: S('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>'),
  approvals: S('<path d="M12 3 4 6.5v5c0 4.6 3.2 8.4 8 9.5 4.8-1.1 8-4.9 8-9.5v-5L12 3z"/><path d="m9 12 2.2 2.2L15.5 10"/>'),
  system:    S('<rect x="3" y="4" width="18" height="12" rx="2.2"/><path d="M8 20h8M12 16v4"/><path d="M7 11.5l2.5-3 2 2.5 2-4 3.5 4.5"/>'),
  settings:  S('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 15H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9l-.1-.1a2 2 0 1 1 2.8-2.8L7.4 6A1.6 1.6 0 0 0 9 6.6h.1A1.6 1.6 0 0 0 10.7 5V5a2 2 0 1 1 4 0v.1A1.6 1.6 0 0 0 17.4 6l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>'),
  play:      S('<path d="M7 4.8 19 12 7 19.2V4.8z"/>'),
  stop:      S('<rect x="6" y="6" width="12" height="12" rx="2"/>'),
  pause:     S('<path d="M9.5 5v14M14.5 5v14"/>'),
  retry:     S('<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>'),
  plus:      S('<path d="M12 5v14M5 12h14"/>'),
  edit:      S('<path d="M4 20h4L20 8l-4-4L4 16v4z"/>'),
  trash:     S('<path d="M4 7h16M9.5 7V5h5v2M6.5 7l1 13h9l1-13"/>'),
  check:     S('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  close:     S('<path d="M6 6l12 12M18 6 6 18"/>'),
  bolt:      S('<path d="M13.6 2 4.5 13.4h5.2L9 22l9.5-11.7h-5.4L13.6 2z"/>'),
  search:    S('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  logout:    S('<path d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H15"/><path d="M10 8 6 12l4 4M6 12h10"/>'),
  activity:  S('<path d="M3 12h4l3-8 4 16 3-8h4"/>'),
  info:      S('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.6v.6"/>'),
  key:       S('<circle cx="8" cy="12" r="4"/><path d="M12 12h9M17.5 12v3.5M20.5 12v2.5"/>'),
  moon:      S('<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>'),
  sun:       S('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5 5l1.4 1.4M17.6 17.6 19 19M19 5l-1.4 1.4M6.4 17.6 5 19"/>'),
  chevron:   S('<path d="m9 6 6 6-6 6"/>'),
  empty:     S('<path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5v-7z"/><path d="M4 8.5 12 13l8-4.5M12 13v7"/>'),
};

export function icon(name, size = 17) {
  const span = h('span', { class: 'i', style: { display: 'grid', placeItems: 'center' }, svgHtml: ICONS[name] || ICONS.info });
  const svg = span.firstElementChild;
  if (svg && size !== 17) { svg.setAttribute('width', size); svg.setAttribute('height', size); }
  return span;
}

/* ---------------- Форматтеры ---------------- */

/** Разбор метки времени: unix-секунды, миллисекунды или ISO-строка (наивная считается UTC). */
export function parseTs(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return value;
  if (typeof value === 'number') {
    const ms = value > 1e11 ? value : value * 1000;
    return new Date(ms);
  }
  let s = String(value).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseTs(Number(s));
  // ISO без зоны → трактуем как UTC (сервер пишет UTC-метки)
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z';
  const d = new Date(s.replace(' ', 'T'));
  return Number.isNaN(d.getTime()) ? null : d;
}

const pad2 = (n) => String(n).padStart(2, '0');

export function fmtTime(value) {
  const d = parseTs(value);
  if (!d) return '—';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** Только часы: «14:03» или «14:03:27». */
export function fmtClock(value, withSeconds = false) {
  const d = parseTs(value);
  if (!d) return '—:—';
  const base = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return withSeconds ? `${base}:${pad2(d.getSeconds())}` : base;
}

export function fmtDateTime(value) {
  const d = parseTs(value);
  if (!d) return '—';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  if (sameDay) return `сегодня ${time}`;
  return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)} ${time}`;
}

export function fmtDateShort(value) {
  const d = parseTs(value);
  if (!d) return '—';
  return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** Длительность в миллисекундах → «2м 13с», «1ч 04м», «13с». */
export function fmtDuration(ms) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  let sec = Math.max(0, Math.round(ms / 1000));
  if (sec < 60) return `${sec}с`;
  const d = Math.floor(sec / 86400); sec -= d * 86400;
  const hh = Math.floor(sec / 3600); sec -= hh * 3600;
  const mm = Math.floor(sec / 60); const ss = sec - mm * 60;
  if (d > 0) return `${d}д ${pad2(hh)}ч`;
  if (hh > 0) return `${hh}ч ${pad2(mm)}м`;
  return `${mm}м ${pad2(ss)}с`;
}

/** Разница между двумя метками (или до «сейчас»). */
export function fmtElapsed(from, to) {
  const a = parseTs(from);
  if (!a) return '—';
  const b = parseTs(to) || new Date();
  return fmtDuration(b.getTime() - a.getTime());
}

export function fmtRelative(value) {
  const d = parseTs(value);
  if (!d) return '—';
  const diff = Date.now() - d.getTime();
  if (Math.abs(diff) < 45000) return 'только что';
  if (diff > 0) return `${fmtDuration(diff)} назад`;
  return `через ${fmtDuration(-diff)}`;
}

export function fmtNum(value, digits = 0) {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtGb(mb) {
  if (mb === null || mb === undefined || Number.isNaN(Number(mb))) return '—';
  return (Number(mb) / 1024).toFixed(1);
}

export function fmtTokens(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

export function fmtContext(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return '—';
  if (v < 1000) return String(v);
  // 32768 → «32k» (двоичные окна), 128000 → «128k» (десятичные)
  const base = v % 1024 === 0 ? 1024 : 1000;
  return `${Math.round(v / base)}k`;
}

export function fmtCost(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `$${n.toFixed(n >= 1 ? 2 : 4)}`;
}

/* ---------------- Статусы ---------------- */

const STATUS_TONE = {
  online: 'ok', ok: 'ok', healthy: 'ok', up: 'ok', ready: 'ok', completed: 'ok', approved: 'ok', done: 'ok', enabled: 'ok',
  running: 'info', leased: 'info', starting: 'info', busy: 'info', checking: 'info', working: 'info',
  queued: 'warn', pending: 'warn', paused: 'warn', waiting_approval: 'warn', warning: 'warn', degraded: 'warn', warn: 'warn',
  offline: 'err', error: 'err', failed: 'err', rejected: 'err', down: 'err', critical: 'err',
  unknown: 'idle', draft: 'idle', stopped: 'idle', disabled: 'idle', idle: 'idle',
  // V2: миссии/терминал/ресурсы/governor/healing/openrouter — общий словарь тонов
  planning: 'info', cancelled: 'idle', created: 'idle',
  auto: 'ok', ask: 'warn', deny: 'err',
  held: 'info', released: 'idle', expired: 'idle',
  escalated: 'err', started: 'info', switched: 'warn', throttled: 'warn',
  finished: 'ok', killed: 'err',
  verified: 'ok', unverified: 'idle', advertised: 'idle', stale: 'idle',
};

export const STATUS_LABEL = {
  draft: 'черновик', queued: 'в очереди', running: 'выполняется', paused: 'на паузе',
  waiting_approval: 'ждёт подтверждения', completed: 'завершена', failed: 'ошибка', stopped: 'остановлена',
  leased: 'взята воркером',
  online: 'online', offline: 'offline', error: 'ошибка', unknown: 'неизвестно',
  pending: 'ожидает', approved: 'подтверждено', rejected: 'отклонено',
  ok: 'в норме', healthy: 'в норме', degraded: 'предупреждение', down: 'недоступен',
  // V2
  planning: 'планирование', cancelled: 'отменена', created: 'создана',
  auto: 'авто', ask: 'спросить', deny: 'запрет',
  held: 'занято', released: 'освобождено', expired: 'истекло',
  escalated: 'эскалировано', started: 'начато', switched: 'модель заменена', throttled: 'ограничено',
  finished: 'завершён', killed: 'убит', working: 'работает',
  verified: 'подтверждено', unverified: 'не проверено', advertised: 'заявлено', stale: 'устарело',
};

export function statusTone(status) {
  return STATUS_TONE[String(status || '').toLowerCase()] || 'idle';
}

export function statusLabel(status) {
  const key = String(status || '').toLowerCase();
  return STATUS_LABEL[key] || (key || '—');
}

export function dot(status, { live = false } = {}) {
  const cls = `dot dot-${statusTone(status)}${live ? ' dot-live' : ''}`;
  return h('span', { class: cls, title: statusLabel(status) });
}

export function statusBadge(status, { live = false, label } = {}) {
  const tone = statusTone(status);
  return h('span', { class: 'badge' + (tone === 'idle' ? '' : ` badge-${tone}`) },
    dot(status, { live }),
    label || statusLabel(status));
}

export function badge(text, tone) {
  return h('span', { class: 'badge' + (tone ? ` badge-${tone}` : '') }, text);
}

/* ---------------- Пустое состояние / загрузка ---------------- */

export function empty({ title, hint, action, iconName = 'empty' }) {
  return h('div.empty',
    h('div.empty-icon', icon(iconName, 20)),
    h('div.empty-title', title),
    hint ? h('div.empty-hint', hint) : null,
    action || null);
}

export function loading(count = 3) {
  return h('div.stack', Array.from({ length: count }, () => h('div.skeleton.skeleton-panel')));
}

/* ---------------- Тосты ---------------- */

const TOAST_ROOT = () => document.getElementById('toast-root');

export function toast(message, { type = 'info', hint = '', timeout = 5200 } = {}) {
  const root = TOAST_ROOT();
  if (!root) return () => {};
  const node = h(`div.toast.toast-${type}`,
    h('div.toast-main',
      h('div.toast-msg', message),
      hint ? h('div.toast-hint', hint) : null),
    h('button.toast-close', { type: 'button', title: 'Закрыть', onClick: () => close() }, '×'));
  root.appendChild(node);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    node.classList.add('out');
    setTimeout(() => node.remove(), 160);
  };
  if (timeout) setTimeout(close, timeout);
  return close;
}

/** Человечное сообщение об ошибке API. */
export function toastError(err, fallback = 'Не удалось выполнить операцию') {
  const message = (err && err.message) || fallback;
  const hint = (err && err.hint) || '';
  console.error(err);
  return toast(message, { type: 'err', hint, timeout: 8000 });
}

export function toastOk(message, hint = '') {
  return toast(message, { type: 'ok', hint, timeout: 3600 });
}

/* ---------------- Модалки ---------------- */

const openModals = [];

function scrim(show) {
  const el = document.getElementById('scrim');
  if (el) el.hidden = !show;
}

/**
 * openModal({title, body, footer, wide, onClose}) → {close, el, body, footer}
 * body/footer — узлы или функции (handle) => узел.
 */
export function openModal({ title, body, footer, wide = false, onClose } = {}) {
  const wrap = h('div.modal-wrap');
  const bodyEl = h('div.modal-body');
  const footEl = h('div.modal-foot');
  const handle = { el: null, body: bodyEl, footer: footEl, close: () => {} };

  const modal = h(`div.modal${wide ? '.wide' : ''}`, { role: 'dialog', 'aria-modal': 'true' },
    h('div.modal-head',
      h('h2', title || ''),
      h('div.spacer'),
      h('button.icon-btn', { type: 'button', 'aria-label': 'Закрыть', onClick: () => close() }, icon('close', 15))),
    bodyEl,
    footEl);

  wrap.appendChild(modal);
  handle.el = modal;

  append(bodyEl, typeof body === 'function' ? body(handle) : body);
  const f = typeof footer === 'function' ? footer(handle) : footer;
  if (f) append(footEl, f); else footEl.remove();

  const onKey = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
  };
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
  document.addEventListener('keydown', onKey, true);

  let closed = false;
  function close(result) {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey, true);
    wrap.remove();
    const idx = openModals.indexOf(handle);
    if (idx >= 0) openModals.splice(idx, 1);
    if (!openModals.length) scrim(false);
    if (onClose) onClose(result);
  }
  handle.close = close;

  document.getElementById('modal-root').appendChild(wrap);
  openModals.push(handle);
  scrim(true);

  const focusable = modal.querySelector('input, textarea, select, button.btn-primary');
  if (focusable) setTimeout(() => focusable.focus(), 30);

  return handle;
}

export function closeTopModal() {
  const top = openModals[openModals.length - 1];
  if (top) { top.close(); return true; }
  return false;
}

export function hasOpenModal() { return openModals.length > 0; }

/** Диалог подтверждения. → Promise<boolean> */
export function confirmDialog({ title = 'Подтвердите действие', text = '', okText = 'Подтвердить', cancelText = 'Отмена', danger = false } = {}) {
  return new Promise((resolve) => {
    let decided = false;
    const modal = openModal({
      title,
      body: h('div.stack.sm', h('div', { class: 'small dim' }, text)),
      footer: () => [
        h('div.spacer'),
        h('button.btn', { type: 'button', onClick: () => modal.close() }, cancelText),
        h(`button.btn.${danger ? 'btn-danger' : 'btn-primary'}`, {
          type: 'button',
          onClick: () => { decided = true; modal.close(); resolve(true); },
        }, okText),
      ],
      onClose: () => { if (!decided) resolve(false); },
    });
  });
}

/* ---------------- Кнопка с состоянием загрузки ---------------- */

export function actionButton(label, handler, { cls = 'btn', iconName, title, disabled = false } = {}) {
  const btn = h(`button.${cls.split(' ').join('.')}`, {
    type: 'button', title: title || label, disabled,
  }, iconName ? icon(iconName, 14) : null, label ? h('span', label) : null);

  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (btn.classList.contains('busy')) return;
    btn.classList.add('busy');
    try { await handler(e); } finally { btn.classList.remove('busy'); }
  });
  return btn;
}

/* ---------------- Поля формы ---------------- */

/**
 * Поле формы. Намеренно <div>, а не <label>: внутрь кладём и чекбоксы,
 * и сегментированные кнопки — вложенные label ломали бы клики.
 * Доступность обеспечивается aria-label на самом контроле.
 */
export function field(label, control, note) {
  if (label && control && control.tagName && !control.getAttribute?.('aria-label')
    && ['INPUT', 'TEXTAREA', 'SELECT'].includes(control.tagName)) {
    control.setAttribute('aria-label', label);
  }
  return h('div.field',
    label ? h('span.field-label', label) : null,
    control,
    note ? h('span.field-note', note) : null);
}

export function input(attrs = {}) {
  return h('input.input', { type: 'text', ...attrs });
}

export function textarea(attrs = {}) {
  return h('textarea.textarea', attrs);
}

export function select(options, attrs = {}) {
  const el = h('select.select', attrs);
  for (const opt of options) {
    if (!opt) continue;
    el.appendChild(h('option', { value: opt.value === undefined ? '' : String(opt.value) }, opt.label));
  }
  if (attrs.value !== undefined && attrs.value !== null) el.value = String(attrs.value);
  return el;
}

export function checkbox(label, checked, attrs = {}) {
  return h('label.check',
    h('input', { type: 'checkbox', checked: !!checked, ...attrs }),
    h('span', label));
}

export function toggle(checked, onChange, title = '') {
  const inp = h('input', { type: 'checkbox', checked: !!checked, title, onChange: (e) => onChange(e.target.checked) });
  return h('label.switch', { title }, inp, h('span.switch-track'));
}

/* ---------------- Метрика с полосой ---------------- */

export function meter(label, value, max, text) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const tone = pct >= 90 ? 'err' : pct >= 75 ? 'warn' : '';
  return h('div.metric',
    h('div.metric-top',
      h('span.metric-label', label),
      h('span.metric-value', text !== undefined ? text : `${Math.round(pct)}%`)),
    h('div', { class: `meter${tone ? ' ' + tone : ''}` }, h('i', { style: { width: `${pct}%` } })));
}

/* ---------------- Спарклайн (inline SVG, без библиотек) ---------------- */

export function sparkline(values, { height = 46, min, max, tone = 'accent' } = {}) {
  const nums = (values || []).map(Number).filter((v) => Number.isFinite(v));
  const w = 100, hh = 100;   // работаем в viewBox-координатах, растягиваем по ширине
  const svg = h('svg.spark', {
    viewBox: `0 0 ${w} ${hh}`,
    preserveAspectRatio: 'none',
    style: { height: `${height}px` },
    'aria-hidden': 'true',
  });

  if (nums.length < 2) {
    svg.appendChild(h('line', { class: 'spark-grid', x1: 0, y1: hh - 1, x2: w, y2: hh - 1 }));
    return svg;
  }

  const lo = min !== undefined ? min : Math.min(...nums);
  let hi = max !== undefined ? max : Math.max(...nums);
  if (hi - lo < 1e-6) hi = lo + 1;

  const pts = nums.map((v, i) => {
    const x = (i / (nums.length - 1)) * w;
    const y = hh - ((v - lo) / (hi - lo)) * (hh - 6) - 3;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });

  svg.appendChild(h('polygon', {
    class: 'spark-area',
    points: `0,${hh} ${pts.join(' ')} ${w},${hh}`,
    style: tone !== 'accent' ? { fill: `var(--${tone})` } : null,
  }));
  svg.appendChild(h('polyline', {
    class: 'spark-line',
    points: pts.join(' '),
    style: tone !== 'accent' ? { stroke: `var(--${tone})` } : null,
  }));
  return svg;
}

/* ---------------- Прочее ---------------- */

/** Маска секрета, если сервер вдруг вернул сырое значение. */
export function maskSecret(value) {
  const s = String(value || '');
  if (!s) return '—';
  if (s.includes('…') || s.includes('***') || s.includes('•')) return s;
  if (s.length <= 8) return '••••';
  return `${s.slice(0, 3)}…${s.slice(-4)}`;
}

export function debounce(fn, ms = 120) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function kv(pairs) {
  const dl = h('dl.kv');
  for (const [k, v] of pairs) {
    if (v === null || v === undefined) continue;
    dl.appendChild(h('dt', k));
    dl.appendChild(h('dd', v));
  }
  return dl;
}
