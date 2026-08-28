/* ============================================================
   appcards.js — карточка приложения для лаунчера.

   Всё, что видно на карточке, приходит из `/api/apps`, то есть из
   `apps/<имя>/app.manifest.yaml`. Здесь нет ни одного названия
   приложения: функции ниже умеют рисовать ЛЮБУЮ карточку, а какие
   карточки существуют — решает файловая система.

   Единственное, что подбирается по имени, — декоративная картинка
   внутри карточки: она выбирается по полю `theme` из манифеста, и
   для незнакомой темы есть нейтральный вариант. Новое приложение
   не обязано трогать этот файл, чтобы появиться на главной.
   ============================================================ */

import { h, icon } from '../components.js';

/* ---------------------------------------------------------------- иконки приложений */

const SVG = (d) => `<svg viewBox="0 0 24 24" width="26" height="26" fill="none"
  stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
  stroke-linejoin="round">${d}</svg>`;

const APP_GLYPHS = {
  camera: SVG('<rect x="3" y="6.5" width="18" height="13" rx="3"/>'
    + '<circle cx="12" cy="13" r="3.6"/><path d="M8.5 6.5 10 4h4l1.5 2.5"/>'),
  cube: SVG('<path d="M12 2.6 20.5 7v10L12 21.4 3.5 17V7L12 2.6z"/>'
    + '<path d="M3.5 7 12 11.6 20.5 7M12 11.6V21.4"/>'),
  share: SVG('<circle cx="6" cy="12" r="2.6"/><circle cx="17.5" cy="6" r="2.6"/>'
    + '<circle cx="17.5" cy="18" r="2.6"/><path d="m8.4 10.8 6.8-3.4M8.4 13.2l6.8 3.4"/>'),
  box: SVG('<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/>'
    + '<path d="M8.5 12h7M12 8.5v7"/>'),
};

export function appIcon(name) {
  return h('span', { svgHtml: APP_GLYPHS[name] || APP_GLYPHS.box });
}

/* ---------------------------------------------------------------- визуальная часть */

const V = (inner) => `<svg viewBox="0 0 220 150" fill="none" stroke="currentColor"
  stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;

const VISUALS = {
  // наблюдение за помещением: кадр, зоны внимания, шкала активности
  azure: V(`
    <rect x="18" y="18" width="184" height="114" rx="8" opacity=".35"/>
    <rect x="30" y="30" width="74" height="52" rx="5" opacity=".8"/>
    <circle cx="67" cy="56" r="12" opacity=".9"/>
    <circle cx="67" cy="56" r="4.5" fill="currentColor" stroke="none" opacity=".9"/>
    <rect x="116" y="30" width="74" height="30" rx="5" opacity=".45"/>
    <path d="M124 45h20M124 51h34" opacity=".55"/>
    <g opacity=".85">
      <path d="M118 118v-16M128 118v-26M138 118v-9M148 118v-33M158 118v-20M168 118v-12M178 118v-24M188 118v-7"/>
    </g>
    <path d="M30 96h74" opacity=".35"/>
    <path d="M30 106h48" opacity=".25"/>`),

  // объём печати и деталь внутри него
  violet: V(`
    <path d="M40 112 110 132l70-20V52L110 32 40 52v60z" opacity=".38"/>
    <path d="M40 52l70 20 70-20M110 72v60" opacity=".3"/>
    <ellipse cx="110" cy="106" rx="26" ry="9" opacity=".7"/>
    <path d="M92 104c2-22 6-34 18-40 12 6 16 18 18 40" opacity=".95"/>
    <path d="M99 100c1.5-16 4-25 11-30 7 5 9.5 14 11 30" opacity=".6"/>
    <path d="M84 66h52" opacity=".25"/>
    <text x="41" y="46" font-size="8" fill="currentColor" stroke="none" opacity=".55">400</text>
    <text x="168" y="128" font-size="8" fill="currentColor" stroke="none" opacity=".55">320</text>`),

  // сетка публикаций и очередь согласований
  ember: V(`
    <rect x="26" y="24" width="46" height="46" rx="7" opacity=".8"/>
    <rect x="82" y="24" width="46" height="46" rx="7" opacity=".5"/>
    <rect x="138" y="24" width="46" height="46" rx="7" opacity=".3"/>
    <circle cx="49" cy="43" r="8" opacity=".9"/>
    <path d="M32 64l12-12 9 9 7-7 6 6" opacity=".8"/>
    <rect x="26" y="84" width="158" height="14" rx="7" opacity=".35"/>
    <rect x="26" y="84" width="86" height="14" rx="7" opacity=".85"/>
    <path d="M26 114h44M80 114h60M150 114h34" opacity=".25"/>`),
};

const NEUTRAL = V(`
  <rect x="46" y="34" width="128" height="82" rx="10" opacity=".4"/>
  <path d="M46 60h128" opacity=".3"/>
  <circle cx="59" cy="47" r="3" opacity=".6"/>
  <circle cx="70" cy="47" r="3" opacity=".4"/>
  <path d="M62 80h96M62 92h64" opacity=".45"/>`);

function appVisual(app) {
  return h('div.bx-app-visual', { 'aria-hidden': 'true' },
    h('span', { svgHtml: VISUALS[app.theme] || NEUTRAL }));
}

/* ---------------------------------------------------------------- статус */

const STATUS = {
  LIVE: { label: 'В работе', tone: 'ok', live: true },
  DEGRADED: { label: 'С ошибками', tone: 'warn', live: false },
  STOPPED: { label: 'Остановлено', tone: 'idle', live: false },
  NOT_CONFIGURED: { label: 'Не настроено', tone: 'idle', live: false },
};

export function appStatusPill(app) {
  const s = STATUS[app.status] || STATUS.STOPPED;
  return h('span', {
    class: `bx-pill is-${s.tone}${s.live ? ' is-live' : ''}`,
    title: app.detail || '',
  }, h('span.bx-pill-dot'), h('span', s.label));
}

/* ---------------------------------------------------------------- карточка */

export function appCard(app, ctx) {
  const accent = app.accent || 'var(--bx-azure)';

  // Действия приходят из манифеста. Первое с primary:true — крупная кнопка;
  // остальные становятся второстепенными. Так приложение само решает, что у
  // него главное, не трогая интерфейс.
  const actions = (app.actions && app.actions.length ? app.actions
    : [{ id: 'open', label: 'Открыть', primary: true }]);
  const primary = actions.find((a) => a.primary) || actions[0];
  const others = actions.filter((a) => a !== primary).slice(0, 1);

  const openBtn = h('button.bx-btn.bx-btn-primary.bx-btn-lg', { type: 'button' },
    h('span', primary.label || 'Открыть'), icon('chevron', 15));
  openBtn.addEventListener('click', () => ctx.navigate('apps', { open: app.id }));

  return h('article.bx-app', { style: { '--bx-accent': accent } },
    h('div.bx-app-badge', appStatusPill(app)),
    h('div.bx-app-body',
      h('div.bx-app-top',
        h('span.bx-app-icon', appIcon(app.icon)),
        h('div', { style: { minWidth: 0 } },
          h('h3.bx-app-name', app.name),
          app.subtitle ? h('p.bx-app-sub', app.subtitle) : null)),
      h('div.bx-app-facts',
        (app.facts || []).slice(0, 4).map((f) => h('div', {
          class: 'bx-app-fact' + (f.live ? ' is-live' : ''),
        },
        h('span.bx-fact-label', f.label),
        h('span.bx-fact-value', { title: String(f.value) }, String(f.value))))),
      h('div.bx-app-actions', openBtn,
        others.map((a) => h('button.bx-btn.bx-btn-secondary.bx-btn-lg', {
          type: 'button',
          onClick: () => ctx.navigate('apps', { open: app.id, action: a.id }),
        }, a.label)))),
    appVisual(app));
}
