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

   Маршрут: `#/apps` — сетка, `#/apps?open=<id>` — приложение.
   ============================================================ */

import { api, listOf } from '../api.js';
import { h, icon, empty } from '../components.js';
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
          onClick: async () => { await api.raw('/api/apps?refresh=true'); ctx.refresh(); },
        }, icon('retry', 14), h('span', 'Проверить состояние')))),
    h('div.bx-apps-grid', apps.map((app) => appCard(app, ctx))));
}

/* ---------------------------------------------------------------- одно приложение */

function appView(app, ctx) {
  const accent = app.accent || 'var(--bx-azure)';

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
    app.version ? h('span.bx-pill.is-idle', h('span', `v${app.version}`)) : null);

  const body = app.status === 'LIVE' || app.status === 'DEGRADED'
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

function notRunning(app, ctx) {
  const cmd = `cd apps/${app.id} && social-farm serve`.replace('social-farm', app.id);
  return h('section.bx-panel',
    h('div.bx-panel-body',
      h('div.bx-empty',
        h('div', { style: { fontSize: '15px', color: 'var(--bx-ink)', fontWeight: 650 } },
          'Приложение не запущено'),
        h('div', app.detail
          || `BOSSMAN не получил ответа от ${app.base_url || 'приложения'}.`),
        h('div', { style: { marginTop: '10px' } },
          'Приложения — самостоятельные сервисы: BOSSMAN их не запускает, '
          + 'чтобы не поднимать чужой процесс без ведома владельца.'),
        h('pre', {
          style: {
            marginTop: '12px', padding: '12px 14px', borderRadius: '10px',
            background: 'var(--bx-surface)', border: '1px solid var(--bx-hairline)',
            color: 'var(--bx-ink-2)', fontFamily: 'var(--bx-mono)', fontSize: '12.5px',
            overflowX: 'auto', width: '100%',
          },
        }, cmd),
        h('div', { style: { display: 'flex', gap: '8px', marginTop: '12px' } },
          h('button.bx-btn.bx-btn-primary.bx-btn-sm', {
            type: 'button',
            onClick: async () => { await api.raw('/api/apps?refresh=true'); ctx.refresh(); },
          }, icon('retry', 14), h('span', 'Проверить снова')),
          h('button.bx-btn.bx-btn-subtle.bx-btn-sm', {
            type: 'button', onClick: () => ctx.navigate('apps'),
          }, 'Назад к списку')))));
}

export default AppsPage;
