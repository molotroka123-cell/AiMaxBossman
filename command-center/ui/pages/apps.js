/* ============================================================
   apps.js — лаунчер приложений и оболочка, внутри которой они живут.

   Приложение открывается ВНУТРИ BOSSMAN: сайдбар, верхняя строка и
   само название остаются на месте, меняется только рабочая область.
   Уводить человека на чужой интерфейс в отдельной вкладке значит
   разорвать оболочку — после этого «одного центра управления» больше
   нет, есть три разные программы.

   Приложения — самостоятельные HTTP-сервисы (их манифесты объявляют
   `imports_bossman: false`), поэтому внутри области открывается их
   собственная страница. Это честно: BOSSMAN не притворяется, что
   нарисовал её сам, но и не выбрасывает человека наружу.

   Запуск и остановка — тоже отсюда. Раньше на месте кнопки лежала
   подсказка `cd apps/<id> && <id> serve`: команда, которой у владельца
   нет, потому что консольные скрипты приложений не установлены. Теперь
   есть кнопка (её ручки — bcc/features/apps_control.py), а ручная
   команда осталась запасным путём — но в исполнимом виде: её присылает
   сервер, который выводит её из раскладки пакета, а не выдумывает здесь.

   Маршрут: `#/apps` — сетка, `#/apps?open=<id>` — приложение.
   ============================================================ */

import { api, listOf } from '../api.js';
import { h, icon, empty, toast, toastOk, toastError, clear } from '../components.js';
import { appCard, appIcon, appStatusPill } from './appcards.js';

const AppsPage = {
  id: 'apps',
  title: 'Приложения',
  icon: 'empty',
  nav: 'primary',
  section: 'main',

  async render(ctx, params) {
    let data;
    try {
      data = await api.raw('/api/apps');
    } catch (e) {
      return h('section.bx-panel', h('div.bx-panel-body', empty({
        iconName: 'info',
        title: 'Список приложений не загрузился',
        hint: (e && e.message) || 'Проверьте, что сервер BOSSMAN работает.',
        action: h('button.bx-btn.bx-btn-primary',
          { type: 'button', onClick: () => ctx.refresh() }, 'Повторить'),
      })));
    }

    const apps = listOf(data, 'apps');
    const openId = params && params.open;
    if (openId) {
      const app = apps.find((a) => a.id === openId);
      if (app) return appView(app, ctx);
    }
    return grid(apps, ctx);
  },
};

/* ---------------------------------------------------------------- управление процессом */

const url = (id, tail) => `/api/apps/${encodeURIComponent(id)}${tail}`;
const startApp = (id) => api.raw(url(id, '/start'), { method: 'POST' });
const stopApp = (id) => api.raw(url(id, '/stop'), { method: 'POST' });
const processInfo = (id) => api.raw(url(id, '/process'));

/* Список приложений кэшируется на сервере, поэтому после запуска его нужно
   перечитать принудительно: иначе карточка ещё десять секунд будет уверять,
   что приложение стоит, хотя оно уже отвечает. */
const refreshApps = () => api.raw('/api/apps?refresh=true');

const isRunning = (app) => app.status === 'LIVE' || app.status === 'DEGRADED';

/**
 * Кнопка «Запустить» / «Остановить».
 *
 * Пока идёт запуск, кнопка занята и говорит об этом: приложение поднимается
 * несколько секунд, и «нажал — и ничего» здесь читалось бы как сломанная кнопка.
 * onFailure получает честный отказ сервера (приложение не поднялось) — тот, кто
 * рисует место для подробностей, сам решает, куда их положить.
 */
function controlButton(app, ctx, { size = 'sm', onFailure } = {}) {
  const running = isRunning(app);
  const label = running ? 'Остановить' : 'Запустить';
  const text = h('span', label);
  const kind = running ? 'bx-btn-secondary' : 'bx-btn-primary';
  const btn = h(`button.bx-btn.${kind}.bx-btn-${size}`, {
    type: 'button',
    title: running
      ? `Остановить ${app.name}: BOSSMAN гасит только тот процесс, который сам запустил`
      : `Запустить ${app.name} на этой машине`,
  }, icon(running ? 'stop' : 'play', 14), text);

  const idle = () => {
    btn.disabled = false;
    btn.classList.remove('is-loading');
    text.textContent = label;
  };

  btn.addEventListener('click', async (event) => {
    event.stopPropagation();
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add('is-loading');
    text.textContent = running ? 'Останавливаю…' : 'Запускаю…';
    try {
      const res = running ? await stopApp(app.id) : await startApp(app.id);
      if (res && res.ok === false) {
        // Процесс родился, но так и не ответил (или сразу умер). Это не
        // «успешно запущено» и не ошибка сети — отдельный, честный исход.
        idle();
        if (onFailure) onFailure(res);
        else toast(res.message || 'Приложение не поднялось', {
          type: 'err', timeout: 9000,
          hint: 'Откройте приложение — там видны последние строки его вывода.',
        });
        await refreshApps();
        return;
      }
      toastOk((res && res.message) || (running ? 'Приложение остановлено' : 'Приложение запущено'));
      await refreshApps();
      ctx.refresh();                 // страница перерисуется целиком — кнопку не восстанавливаем
    } catch (e) {
      idle();
      toastError(e, running ? 'Не удалось остановить приложение'
        : 'Не удалось запустить приложение');
    }
  });
  return btn;
}

/* ---------------------------------------------------------------- сетка */

function grid(apps, ctx) {
  if (!apps.length) {
    return h('section.bx-panel', h('div.bx-panel-body', empty({
      iconName: 'empty',
      title: 'Приложений пока нет',
      hint: 'Приложение появляется здесь, когда в apps/<имя>/app.manifest.yaml '
        + 'есть блок ui:. Ничего перезапускать не нужно.',
    })));
  }

  const live = apps.filter((a) => a.status === 'LIVE').length;
  return h('div.bx-home',
    h('header.bx-hero',
      h('div',
        h('h1.bx-hero-title', { style: { fontSize: 'var(--bx-h1)' } }, 'Приложения'),
        h('p.bx-hero-sub',
          `${apps.length} установлено, ${live} в работе. Список читается из манифестов.`)),
      h('div.bx-hero-pills',
        h('button.bx-btn.bx-btn-subtle.bx-btn-sm', {
          type: 'button',
          onClick: async () => { await refreshApps(); ctx.refresh(); },
        }, icon('retry', 14), h('span', 'Проверить состояние')))),
    h('div.bx-apps-grid', apps.map((app) => cardWithControl(app, ctx))));
}

/**
 * Карточка приложения плюс кнопка управления.
 *
 * Кнопка добавляется в готовую карточку, а не рисуется заново: как выглядит
 * карточка, решает appcards.js, и второй её вариант разошёлся бы с первым.
 */
function cardWithControl(app, ctx) {
  const card = appCard(app, ctx);
  const actions = card.querySelector('.bx-app-actions');
  if (actions) {
    actions.appendChild(controlButton(app, ctx, {
      size: 'lg',
      // Хвост журнала в карточку не помещается — за подробностями открываем
      // само приложение, там для них есть место.
      onFailure: (res) => {
        toast(res.message || 'Приложение не поднялось', {
          type: 'err', timeout: 9000,
          hint: 'Открываю приложение: там видны последние строки его вывода.',
        });
        ctx.navigate('apps', { open: app.id });
      },
    }));
  }
  return card;
}

/* ---------------------------------------------------------------- одно приложение */

function appView(app, ctx) {
  const accent = app.accent || 'var(--bx-azure)';
  const running = isRunning(app);

  const head = h('div.bx-appview-head', { style: { '--bx-accent': accent } },
    h('button.bx-btn.bx-btn-ghost.bx-btn-sm', {
      type: 'button', onClick: () => ctx.navigate('apps'),
    }, h('span', { style: { transform: 'rotate(180deg)', display: 'inline-flex' } },
      icon('chevron', 14)), h('span', 'Все приложения')),
    h('span.bx-app-icon', {
      style: { '--bx-accent': accent, width: '38px', height: '38px' },
    }, appIcon(app.icon)),
    h('div', { style: { minWidth: 0 } },
      h('div', { style: { fontWeight: 700, fontSize: '16px', letterSpacing: '-.015em' } },
        app.name),
      app.subtitle ? h('div', {
        style: { fontSize: '12.5px', color: 'var(--bx-ink-3)' },
      }, app.subtitle) : null),
    h('div.bx-spacer'),
    appStatusPill(app),
    app.version ? h('span.bx-pill.is-idle', h('span', `v${app.version}`)) : null,
    running ? controlButton(app, ctx) : null);

  const body = running
    ? h('div.bx-appview-frame',
      h('iframe', {
        src: app.base_url, title: app.name,
        // Приложение — отдельный сервис, а не часть BOSSMAN. Песочница
        // ограничивает его тем, что нужно интерфейсу, и ничем сверх того.
        sandbox: 'allow-scripts allow-forms allow-same-origin allow-popups',
        loading: 'lazy',
      }))
    : notRunning(app, ctx);

  return h('div.bx-appview', head, body);
}

function codeBlock(text) {
  return h('pre', {
    style: {
      marginTop: '12px', padding: '12px 14px', borderRadius: '10px',
      background: 'var(--bx-surface)', border: '1px solid var(--bx-hairline)',
      color: 'var(--bx-ink-2)', fontFamily: 'var(--bx-mono)', fontSize: '12.5px',
      overflowX: 'auto', width: '100%', whiteSpace: 'pre-wrap',
    },
  }, text);
}

/** Последние строки вывода приложения — то, ради чего человек лезет в консоль. */
function logBlock(title, lines) {
  return h('div', { style: { marginTop: '12px', width: '100%' } },
    h('div', { style: { fontSize: '12.5px', color: 'var(--bx-ink-3)' } }, title),
    codeBlock(lines.join('\n')));
}

function notRunning(app, ctx) {
  // Команду присылает сервер: он один знает раскладку пакета приложения.
  // До ответа честно говорим, что она загружается, а не показываем догадку.
  const cmd = codeBlock('Загружаю команду запуска…');
  const details = h('div', { style: { width: '100%' } });
  const note = h('div', { style: { marginTop: '10px' } },
    'Приложение — самостоятельный сервис. BOSSMAN может запустить его здесь: '
    + 'он порождает один процесс, ждёт ответа и показывает результат.');

  const showFailure = (res) => {
    clear(details);
    details.appendChild(h('div', {
      style: { marginTop: '12px', color: 'var(--bx-rose)', fontWeight: 600 },
    }, res.message || 'Приложение не поднялось'));
    if (res.log_tail && res.log_tail.length) {
      details.appendChild(logBlock('Последние строки вывода приложения:', res.log_tail));
    } else {
      details.appendChild(h('div', { style: { marginTop: '6px' } },
        'Приложение не оставило ни строки вывода.'));
    }
  };

  const startBtn = controlButton(app, ctx, { onFailure: showFailure });

  const section = h('section.bx-panel',
    h('div.bx-panel-body',
      h('div.bx-empty',
        h('div', { style: { fontSize: '15px', color: 'var(--bx-ink)', fontWeight: 650 } },
          'Приложение не запущено'),
        h('div', app.detail
          || `BOSSMAN не получил ответа от ${app.base_url || 'приложения'}.`),
        note,
        h('div', { style: { display: 'flex', gap: '8px', marginTop: '12px' } },
          startBtn,
          h('button.bx-btn.bx-btn-subtle.bx-btn-sm', {
            type: 'button',
            onClick: async () => { await refreshApps(); ctx.refresh(); },
          }, icon('retry', 14), h('span', 'Проверить снова')),
          h('button.bx-btn.bx-btn-ghost.bx-btn-sm', {
            type: 'button', onClick: () => ctx.navigate('apps'),
          }, 'Назад к списку')),
        h('div', { style: { marginTop: '14px', fontSize: '12.5px', color: 'var(--bx-ink-3)' } },
          'Если удобнее руками — та же команда в терминале:'),
        cmd,
        details)));

  processInfo(app.id).then((info) => {
    cmd.textContent = info.manual_command
      || `cd apps/${app.id} && python -m <модуль приложения> serve`;
    if (info.enabled === false) {
      // Кнопка, которая гарантированно откажет, хуже честной надписи.
      startBtn.disabled = true;
      startBtn.title = 'Управление приложениями выключено';
      details.appendChild(h('div', { style: { marginTop: '12px' } },
        'Запуск из дашборда выключен. Чтобы разрешить его, поставьте '
        + 'BOSSMAN_APPS_CONTROL_ENABLED=1 и перезапустите Command Center.'));
    } else if (info.log_tail && info.log_tail.length) {
      details.appendChild(logBlock('Последние строки прошлого запуска:', info.log_tail));
    }
  }).catch(() => {
    cmd.textContent = `cd apps/${app.id} && python -m <модуль приложения> serve`;
  });

  return section;
}

export default AppsPage;
