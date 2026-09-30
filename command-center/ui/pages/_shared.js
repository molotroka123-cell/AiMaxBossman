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

/* UX-11: одна человеческая подпись события на все страницы (раньше она жила только
   в home.js, а «Обзор» печатал сырой kind и JSON). */
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
  'apps.control_policy_changed': 'Изменено разрешение на запуск приложений',
  'autonomy.stop': 'Автономия остановлена (STOP)',
};

export function humanKind(kind) {
  if (EVENT_LABEL[kind]) return EVENT_LABEL[kind];
  const head = kind.split('.')[0];
  const byHead = {
    agent: 'Событие агента', model: 'Событие модели', mission: 'Событие миссии',
    task: 'Событие задачи', approval: 'Подтверждение', resource: 'Память и ресурсы',
    recovery: 'Восстановление', governor: 'Присмотр', apps: 'Приложения',
    autonomy: 'Автономия', telegram_call: 'Telegram-звонок', studio: 'Студия',
    rave: 'Agentic Rave', v15: 'Bossman 1.5',
  };
  return byHead[head] || 'Событие';
}
