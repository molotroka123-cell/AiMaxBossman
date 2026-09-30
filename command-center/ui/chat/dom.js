/* ============================================================
   chat/dom.js — маленький конструктор DOM и значки чата.

   Никакого innerHTML: узлы строятся createElement/createElementNS, текст —
   текстовыми узлами. Значки — простые линейные SVG 24×24 без внешних
   файлов и шрифтов (стандартный набор ui/components.js не содержит
   микрофона, скрепки и стрелки отправки).
   ============================================================ */

const SVG_NS = 'http://www.w3.org/2000/svg';

function isAttrs(v) {
  return v && typeof v === 'object' && !Array.isArray(v) && !(typeof Node !== 'undefined' && v instanceof Node);
}

function append(el, child) {
  if (child === null || child === undefined || child === false || child === true) return;
  if (Array.isArray(child)) { for (const c of child) append(el, c); return; }
  if (typeof child === 'string' || typeof child === 'number') { el.appendChild(document.createTextNode(String(child))); return; }
  el.appendChild(child);
}

/** h('div.cls#id', {attrs}, ...children). Обработчики — onClick, onKeydown и т.п. */
export function h(tag, attrs, ...children) {
  const m = /^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i.exec(tag) || [];
  const el = document.createElement(m[1] || 'div');
  const rest = m[2] || '';
  for (const part of rest.match(/[.#][\w-]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  if (isAttrs(attrs)) applyAttrs(el, attrs);
  else append(el, attrs);
  for (const c of children) append(el, c);
  return el;
}

export function applyAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') { for (const c of String(v).split(/\s+/).filter(Boolean)) el.classList.add(c); }
    else if (k === 'dataset') { for (const [dk, dv] of Object.entries(v)) if (dv !== undefined && dv !== null) el.dataset[dk] = String(dv); }
    else if (k === 'style' && typeof v === 'object') {
      for (const [sk, sv] of Object.entries(v)) {
        if (sk.startsWith('--')) el.style.setProperty(sk, sv);    /* CSS-переменная */
        else el.style[sk] = sv;
      }
    }
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'hidden' || k === 'disabled' || k === 'checked' || k === 'readOnly') el[k] = Boolean(v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  return el;
}

/* ---------------------------------------------------------------- значки */

const P = (d) => ['path', { d }];
const C = (cx, cy, r) => ['circle', { cx, cy, r }];
const R = (x, y, w, hh, rx = 2) => ['rect', { x, y, width: w, height: hh, rx }];

export const ICONS = {
  plus: [P('M12 5v14M5 12h14')],
  search: [C(11, 11, 7), P('m20 20-3.5-3.5')],
  collapse: [P('m11 17-5-5 5-5'), P('m18 17-5-5 5-5')],
  expand: [P('m13 17 5-5-5-5'), P('m6 17 5-5-5-5')],
  pin: [P('M9 4h6l-1 6 3 3v2H7v-2l3-3-1-6z'), P('M12 15v5')],
  star: [P('m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9L12 3.5z')],
  export: [P('M12 15V4'), P('m7.5 8.5 4.5-4.5 4.5 4.5'), P('M5 14v4.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V14')],
  more: [C(5, 12, 1.4), C(12, 12, 1.4), C(19, 12, 1.4)],
  edit: [P('M4 20h4L20 8l-4-4L4 16v4z'), P('m14 6 4 4')],
  close: [P('M6 6l12 12M18 6 6 18')],
  check: [P('m5 12.5 4.5 4.5L19 7.5')],
  checkCircle: [C(12, 12, 9), P('m8 12.3 2.8 2.8L16.2 9.6')],
  failCircle: [C(12, 12, 9), P('m9 9 6 6M15 9l-6 6')],
  clock: [C(12, 12, 9), P('M12 7.5V12l3 1.8')],
  chevronRight: [P('m9 6 6 6-6 6')],
  chevronDown: [P('m6 9 6 6 6-6')],
  send: [P('M12 19V5'), P('m5.5 11.5 6.5-6.5 6.5 6.5')],
  stop: [R(6.5, 6.5, 11, 11, 2)],
  mic: [R(9, 3, 6, 11.5, 3), P('M5.5 11a6.5 6.5 0 0 0 13 0'), P('M12 17.5V21')],
  settings: [C(12, 12, 3), P('M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z')],
  help: [C(12, 12, 9), P('M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6'), P('M12 17h.01')],
  sun: [C(12, 12, 4), P('M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4')],
  moon: [P('M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z')],
  spark: [P('M12 3v4M12 17v4M3 12h4M17 12h4'), P('m6.3 6.3 2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5')],
  link: [P('M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1'), P('M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1')],
  memory: [['ellipse', { cx: 12, cy: 6, rx: 7, ry: 3 }], P('M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6'), P('M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6')],
  tools: [R(4, 4, 6.5, 6.5, 1.5), R(13.5, 4, 6.5, 6.5, 1.5), R(4, 13.5, 6.5, 6.5, 1.5), R(13.5, 13.5, 6.5, 6.5, 1.5)],
  bolt: [P('M13.6 2 4.5 13.4h5.2L9 22l9.5-11.7h-5.4L13.6 2z')],
  folder: [P('M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5v-10z')],
  archive: [R(3.5, 4, 17, 4.5, 1), P('M5 8.5V19a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8.5'), P('M10 12.5h4')],
  lock: [R(5, 10.5, 14, 10, 2), P('M8 10.5V8a4 4 0 0 1 8 0v2.5')],
  copy: [R(8.5, 8.5, 11.5, 11.5, 2), P('M15.5 8.5V6A2 2 0 0 0 13.5 4H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5')],
  file: [P('M6 3.5h8l4.5 4.5v12a.5.5 0 0 1-.5.5H6a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z'), P('M13.5 3.5V8.5h5')],
  open: [P('M14 4h6v6'), P('M20 4 11 13'), P('M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10')],
  home: [P('M3 10.5 12 3l9 7.5'), P('M5.5 9.5V20h13V9.5')],
  shield: [P('M12 3 4 6.5v5c0 4.6 3.2 8.4 8 9.5 4.8-1.1 8-4.9 8-9.5v-5L12 3z')],
  warn: [P('M12 3.5 2.5 20h19L12 3.5z'), P('M12 10v4.5M12 17.2v.3')],
  list: [P('M8 6h12M8 12h12M8 18h12'), C(4, 6, 0.8), C(4, 12, 0.8), C(4, 18, 0.8)],
  play: [P('M7 4.8 19 12 7 19.2V4.8z')],
};

export function icon(name, size = 18, extraClass = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', `ic${extraClass ? ` ${extraClass}` : ''}`);
  for (const [tag, attrs] of ICONS[name] || ICONS.help) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    svg.appendChild(node);
  }
  return svg;
}

/** Кнопка-значок: у каждой обязательно есть aria-label и title. */
export function iconButton(name, label, onClick, { cls = '', pressed = null, size = 18, disabled = false, id = null } = {}) {
  const attrs = { type: 'button', class: `icon-btn${cls ? ` ${cls}` : ''}`, 'aria-label': label, title: label, onClick };
  if (pressed !== null) attrs['aria-pressed'] = pressed ? 'true' : 'false';
  if (disabled) attrs.disabled = true;
  if (id) attrs.id = id;
  return h('button', attrs, icon(name, size));
}

export function clear(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** Фабрика для markdown.js: те же узлы, что строит браузер, только через textContent. */
export const domFactory = {
  text: (s) => document.createTextNode(String(s ?? '')),
  el: (tag, attrs = {}, children = []) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    for (const c of children) el.appendChild(c);
    return el;
  },
};

export function prefersReducedMotion() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
}
