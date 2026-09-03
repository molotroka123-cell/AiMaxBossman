/* ============================================================
   _ui.js — общий язык дизайна для feature-страниц.

   Главная (home.js) собрана на классах bx-* из theme.css вручную.
   Чтобы не копировать её разметку в каждый файл, здесь живут те же
   кирпичи в виде функций: заголовок страницы, панель, пилюля,
   плитка-карточка, метрика, пустое состояние, кнопка.

   Язык — человеческий. Ни одна функция здесь не показывает сырой
   технический статус: для статусов есть statusText(), которая
   переводит running/queued/LIVE/… на понятные владельцу слова.
   ============================================================ */

import { h, icon } from '../components.js';

/* Русская форма слова по числу: plural(3,'шаг','шага','шагов') → «3 шага». */
export function plural(n, one, few, many) {
  const num = Math.abs(Number(n)) % 100;
  const d = num % 10;
  let word;
  if (num > 10 && num < 20) word = many;
  else if (d > 1 && d < 5) word = few;
  else if (d === 1) word = one;
  else word = many;
  return `${n} ${word}`;
}

/* ---------------------------------------------------------------- заголовок страницы */

export function pageHead(title, sub, { pills = [], actions = [] } = {}) {
  const aside = [...pills, ...actions];
  return h('header.bx-pagehead',
    h('div', { style: { minWidth: 0 } },
      h('h1.bx-pagehead-title', title),
      sub ? h('p.bx-pagehead-sub', sub) : null),
    aside.length ? h('div.bx-pagehead-aside', aside) : null);
}

/* ---------------------------------------------------------------- панель с шапкой */

export function panel(title, body, { aside, icon: iconName } = {}) {
  return h('section.bx-panel',
    title
      ? h('div.bx-panel-head',
        iconName ? icon(iconName, 14) : null,
        h('h2', title), h('div.bx-spacer'), aside || null)
      : null,
    h('div.bx-panel-body', body));
}

/* ---------------------------------------------------------------- пилюля статуса */

export function pill(text, { tone = 'idle', value = '', live = false, title } = {}) {
  return h('span', { class: `bx-pill is-${tone}${live ? ' is-live' : ''}`, title: title || null },
    h('span.bx-pill-dot'),
    h('span', text),
    value !== '' && value !== null && value !== undefined
      ? h('span.bx-pill-val', String(value)) : null);
}

/* Перевод любого статуса в {word, tone, live} — человеческими словами. */
const STATUS_WORDS = {
  // задачи / миссии
  running: { word: 'выполняется', tone: 'info', live: true },
  planning: { word: 'составляем план', tone: 'info', live: true },
  queued: { word: 'в очереди', tone: 'warn' },
  paused: { word: 'на паузе', tone: 'warn' },
  completed: { word: 'готово', tone: 'ok' },
  finished: { word: 'готово', tone: 'ok' },
  done: { word: 'готово', tone: 'ok' },
  failed: { word: 'ошибка', tone: 'err' },
  error: { word: 'ошибка', tone: 'err' },
  stopped: { word: 'остановлено', tone: 'idle' },
  cancelled: { word: 'отменено', tone: 'idle' },
  draft: { word: 'черновик', tone: 'idle' },
  created: { word: 'создано', tone: 'idle' },
  killed: { word: 'остановлено', tone: 'err' },
  leased: { word: 'взято в работу', tone: 'info', live: true },
  // подтверждения
  pending: { word: 'ждёт решения', tone: 'warn', live: true },
  approved: { word: 'разрешено', tone: 'ok' },
  rejected: { word: 'отклонено', tone: 'err' },
  // приложения / модели / здоровье
  LIVE: { word: 'работает', tone: 'ok', live: true },
  DEGRADED: { word: 'с ошибками', tone: 'warn' },
  STOPPED: { word: 'остановлено', tone: 'idle' },
  NOT_CONFIGURED: { word: 'не настроено', tone: 'idle' },
  online: { word: 'на связи', tone: 'ok', live: true },
  offline: { word: 'не отвечает', tone: 'idle' },
  ok: { word: 'в норме', tone: 'ok' },
  healthy: { word: 'в норме', tone: 'ok' },
  up: { word: 'в норме', tone: 'ok' },
  degraded: { word: 'предупреждение', tone: 'warn' },
  warning: { word: 'предупреждение', tone: 'warn' },
  warn: { word: 'предупреждение', tone: 'warn' },
  down: { word: 'не работает', tone: 'err' },
  critical: { word: 'сбой', tone: 'err' },
  enabled: { word: 'включён', tone: 'ok' },
  disabled: { word: 'выключен', tone: 'idle' },
  working: { word: 'работает', tone: 'ok', live: true },
  idle: { word: 'свободен', tone: 'idle' },
  unknown: { word: 'нет данных', tone: 'idle' },
};

export function statusText(status) {
  const raw = String(status ?? '').trim();
  return STATUS_WORDS[raw] || STATUS_WORDS[raw.toLowerCase()]
    || { word: raw || 'нет данных', tone: 'idle' };
}

/** Пилюля из статуса, переведённого на человеческий. */
export function statusPill(status, { title } = {}) {
  const s = statusText(status);
  return pill(s.word, { tone: s.tone, live: !!s.live, title });
}

/* ---------------------------------------------------------------- плитка-карточка */

export function tile(opts) {
  const {
    accent = 'var(--bx-azure)', iconName, iconNode, title, sub, statusNode,
    body = [], tags, actions, onClick, muted = false, style,
  } = opts;

  const cls = 'bx-tile'
    + (onClick ? ' is-clickable' : '')
    + (muted ? ' is-muted' : '');

  const children = [
    h('div.bx-tile-head',
      (iconNode || iconName)
        ? h('span.bx-tile-icon', iconNode || icon(iconName, 20)) : null,
      h('div', { style: { minWidth: 0, flex: '1 1 auto' } },
        h('h3.bx-tile-title', title),
        sub ? h('p.bx-tile-sub', sub) : null),
      statusNode || null),
    tags && tags.length ? h('div.bx-tags', tags) : null,
    ...(Array.isArray(body) ? body : [body]),
    actions && actions.length ? h('div.bx-tile-actions', { onClick: (e) => e.stopPropagation() }, actions) : null,
  ];

  const attrs = { class: cls, style: { '--bx-accent': accent, ...(style || {}) } };
  if (onClick) attrs.onClick = onClick;
  return h('article', attrs, children);
}

/* ---------------------------------------------------------------- чипы */

export function tag(text, { accent = false, bold } = {}) {
  return h('span', { class: 'bx-tag' + (accent ? ' is-accent' : '') },
    bold ? [h('b', bold), text ? ' ' : null, text || null] : text);
}

/* ---------------------------------------------------------------- метрика с полосой */

export function bar(value, max, accent, tone) {
  const width = max > 0 ? Math.max(0, Math.min(100, (Number(value) / max) * 100)) : 0;
  return h('span', { class: 'bx-bar' + (tone ? ` is-${tone}` : ''), style: accent ? { '--bx-accent': accent } : null },
    h('i', { style: { width: `${width}%` } }));
}

export function meter(label, value, max, text, { accent, tone } = {}) {
  const pctVal = max > 0 ? (Number(value) / Number(max)) * 100 : 0;
  const autoTone = tone || (pctVal >= 90 ? 'err' : pctVal >= 78 ? 'warn' : '');
  return h('div', { class: 'bx-meter' + (autoTone ? ` is-${autoTone}` : '') },
    h('div.bx-meter-top',
      h('span.bx-meter-label', label),
      h('span.bx-meter-val', text !== undefined ? text : `${Math.round(pctVal)}%`)),
    h('span.bx-bar', accent ? { style: { '--bx-accent': accent } } : null,
      h('i', { style: { width: `${Math.max(0, Math.min(100, pctVal))}%` } })));
}

/* ---------------------------------------------------------------- строки статистики */

export function stat(label, value) {
  return h('div.bx-stat', h('span.bx-stat-label', label), h('span.bx-stat-value', String(value)));
}

/* ---------------------------------------------------------------- кнопки */

const VARIANT = {
  primary: 'bx-btn bx-btn-primary',
  secondary: 'bx-btn bx-btn-secondary',
  subtle: 'bx-btn bx-btn-subtle',
  ghost: 'bx-btn bx-btn-ghost',
  danger: 'bx-btn bx-btn-danger',
};

export function btn(label, onClick, { variant = 'secondary', iconName, size = '', title, block = false, disabled = false, accent } = {}) {
  const cls = VARIANT[variant] || VARIANT.secondary;
  const extra = (size === 'sm' ? ' bx-btn-sm' : size === 'lg' ? ' bx-btn-lg' : '') + (block ? ' bx-btn-block' : '');
  const el = h('button', { class: cls + extra, type: 'button', title: title || null, disabled, style: accent ? { '--bx-accent': accent } : null },
    iconName ? icon(iconName, size === 'sm' ? 13 : 14) : null,
    label ? h('span', label) : null);
  if (onClick) {
    el.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (el.classList.contains('is-loading')) return;
      const r = onClick(e);
      if (r && typeof r.then === 'function') {
        el.classList.add('is-loading');
        try { await r; } finally { el.classList.remove('is-loading'); }
      }
    });
  }
  return el;
}

/* ---------------------------------------------------------------- пустое состояние */

export function blank({ iconName = 'empty', title, hint, action } = {}) {
  return h('section.bx-panel', h('div.bx-blank',
    h('span.bx-blank-icon', icon(iconName, 24)),
    h('div.bx-blank-title', title),
    hint ? h('div.bx-blank-hint', hint) : null,
    action || null));
}

/* ---------------------------------------------------------------- баннер ошибки загрузки */

export function errorNote(err, onRetry) {
  const message = (err && err.message) || 'Не удалось получить данные';
  const hint = (err && err.hint) || 'Попробуйте обновить — возможно, сервер сейчас недоступен.';
  return h('section.bx-panel', { style: { borderColor: 'color-mix(in srgb, var(--bx-rose) 40%, transparent)' } },
    h('div.bx-panel-body',
      h('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--bx-3)', flexWrap: 'wrap' } },
        h('span.bx-pill.is-err', h('span.bx-pill-dot'), h('span', 'Ошибка')),
        h('div', { style: { flex: '1 1 auto', minWidth: 0 } },
          h('div', { style: { color: 'var(--bx-ink)', fontWeight: 600, fontSize: '13.5px' } }, message),
          h('div', { style: { color: 'var(--bx-ink-3)', fontSize: '12px', marginTop: '2px' } }, hint)),
        onRetry ? btn('Обновить', onRetry, { variant: 'subtle', size: 'sm', iconName: 'retry' }) : null)));
}

/* ---------------------------------------------------------------- моноблок */

export function codeBlock(text) {
  return h('pre.bx-code', String(text ?? ''));
}

/* ---------------------------------------------------------------- сегментированный переключатель */

export function segmented(options, current, onPick) {
  return h('div.bx-seg', options.map((o) => {
    const b = h('button', { type: 'button', class: current === o.value ? 'is-on' : '' }, o.label);
    b.addEventListener('click', () => onPick(o.value));
    return b;
  }));
}

/* ---------------------------------------------------------------- поле ввода */

export function field(label, control, note) {
  return h('div.bx-field',
    label ? h('span.bx-field-label', label) : null,
    control,
    note ? h('span.bx-field-note', note) : null);
}
