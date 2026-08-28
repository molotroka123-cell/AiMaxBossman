/* ============================================================
   _shared.js — общие мелочи для V2 feature-страниц (ui/pages/*.js).
   Не часть контракта регистрации (index.js), просто чтобы не дублировать
   один и тот же код в 15 файлах. Используйте компоненты из ../components.js
   и клиент из ../api.js напрямую — этот файл только для повторяющихся паттернов.
   ============================================================ */

import { h, dot, empty } from '../components.js';

/** '' -> null, '12' -> 12, 'abc' -> 'abc' (id может быть и строкой). */
export function idVal(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) && String(n) === String(v).trim() ? n : v;
}

/** Секция панели с заголовком (как panel() в pages.js). */
export function panel(title, bodyNode, { actions, flush = false, tight = false } = {}) {
  return h('section.panel',
    title ? h('div.panel-head',
      typeof title === 'string' ? h('h2', title) : title,
      h('div.spacer'),
      actions || null) : null,
    h('div', { class: 'panel-body' + (flush ? ' flush' : tight ? ' tight' : '') }, bodyNode));
}

/** Заголовок страницы: title + подзаголовок слева, actions справа. */
export function pageHead(title, sub, actions = []) {
  return h('div.row',
    h('div',
      h('div.section-title', { style: { margin: 0 } }, title),
      sub ? h('div.small.dim', sub) : null),
    h('div.spacer'),
    ...actions);
}

/** Баннер ошибки загрузки данных с кнопкой «Повторить» (как errorBanner в pages.js). */
export function errorBanner(err, ctx) {
  return h('section.panel', { style: { borderColor: 'color-mix(in srgb, var(--err) 40%, transparent)' } },
    h('div.panel-body', h('div.row',
      dot('error'),
      h('div', { style: { flex: '1' } },
        h('div.small', err && err.message ? err.message : 'Часть данных не загрузилась'),
        err && err.hint ? h('div.xsmall.dim', err.hint) : null),
      h('button.btn.btn-sm', { type: 'button', onClick: () => ctx.refresh() }, 'Повторить'))));
}

export function emptyPanel(opts) {
  return h('section.panel', empty(opts));
}

/** 0..1 -> «42%». */
export function pct(value, digits = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return `${(n * 100).toFixed(digits)}%`;
}

/** Кнопка-заглушка для нереализованного на бэкенде функционала: всегда disabled. */
export function notAvailable(label = 'Недоступно', title = 'Эта операция ещё не реализована на сервере') {
  return h('button.btn.btn-sm', { type: 'button', disabled: true, title }, label);
}

/** Моноширинный фрагмент текста инлайн. */
export function mono(text) {
  return h('span.mono', String(text ?? '—'));
}
