// Реестр V2-страниц (контракты §8) — ЛЕНИВЫЙ (V6 §C).
//
// Раньше здесь были 28 статических импортов: оболочка тянула ~780 KiB кода
// всех страниц до первого кадра, хотя владелец открывает одну. Теперь реестр
// хранит только то, что нужно оболочке ДО открытия страницы (id, title, icon,
// nav, section — навигация, палитра, __bxPages), а код страницы грузится
// `import()` при первом render(). После первой отрисовки остальные модули
// подгружаются в простое (см. preloadFeaturePages), чтобы переходы оставались
// мгновенными.
//
// Каждый feature-агент добавляет РОВНО одну запись lazyPage(...). Статические
// поля обязаны совпадать с модулем: tests/test_v6_lazy_pages.py импортирует
// каждый модуль в Chromium и сверяет — расхождение манифеста с модулем это
// падение теста, а не тихо неправильная навигация.
//
// Страница экспортирует { id, title, icon, nav: 'primary'|'more', section?, render(ctx), onEvent(ev) }
// — тот же интерфейс, что страницы MVP в ../pages.js.

function lazyPage(meta, load, pick) {
  let real = null;
  let pending = null;
  const ensure = () => {
    if (real) return Promise.resolve(real);
    if (!pending) {
      pending = load().then((m) => { real = pick(m); return real; },
        (err) => { pending = null; throw err; });   /* сбой сети — следующая попытка честная */
    }
    return pending;
  };
  return {
    ...meta,
    async render(ctx, params) {
      const page = await ensure();
      return page.render(ctx, params);
    },
    /* до загрузки модуля событий для страницы нет — она ещё ничего не показала */
    onEvent(ev, ctx) {
      return real && typeof real.onEvent === 'function' ? real.onEvent(ev, ctx) : false;
    },
    __load: ensure,
    get __loaded() { return real !== null; },
  };
}

export const FEATURE_PAGES = [
  lazyPage({ id: 'video-studio', title: 'Video Studio', icon: 'film', nav: 'primary' },
    () => import('./video_studio.js'), (m) => m.default),
  lazyPage({ id: 'bossman-chat', title: 'История видео и чат', icon: 'terminal', section: 'main', nav: 'more' },
    () => import('../video_chat.js'), (m) => m.ChatPage),
  lazyPage({ id: 'home-v3', title: 'Главная', icon: 'home', nav: 'primary', section: 'main' },
    () => import('./home.js'), (m) => m.default),
  lazyPage({ id: 'apps', title: 'Приложения', icon: 'empty', nav: 'primary', section: 'main' },
    () => import('./apps.js'), (m) => m.default),
  lazyPage({ id: 'overview', title: 'Обзор', icon: 'home', nav: 'primary' },
    () => import('./overview.js'), (m) => m.default),
  lazyPage({ id: 'missions', title: 'Миссии', icon: 'bolt', nav: 'primary' },
    () => import('./missions.js'), (m) => m.default),
  lazyPage({ id: 'router', title: 'Выбор модели', icon: 'retry', nav: 'more' },
    () => import('./router.js'), (m) => m.default),
  lazyPage({ id: 'governor', title: 'Присмотр', icon: 'info', nav: 'more' },
    () => import('./governor.js'), (m) => m.default),
  lazyPage({ id: 'resources', title: 'Ресурсы', icon: 'system', nav: 'primary' },
    () => import('./resources.js'), (m) => m.default),
  lazyPage({ id: 'skills', title: 'Навыки', icon: 'edit', nav: 'primary' },
    () => import('./skills.js'), (m) => m.default),
  lazyPage({ id: 'terminal', title: 'Терминал', icon: 'chevron', nav: 'primary' },
    () => import('./terminal.js'), (m) => m.default),
  lazyPage({ id: 'benchmarks', title: 'Замеры моделей', icon: 'bolt', nav: 'more' },
    () => import('./benchmarks.js'), (m) => m.default),
  lazyPage({ id: 'browser', title: 'Браузер', icon: 'search', nav: 'primary' },
    () => import('./browser.js'), (m) => m.default),
  lazyPage({ id: 'coding', title: 'Coding-сессии', icon: 'edit', nav: 'more' },
    () => import('./coding.js'), (m) => m.default),
  lazyPage({ id: 'agentmap', title: 'Карта агентов', icon: 'agents', nav: 'more' },
    () => import('./agentmap.js'), (m) => m.default),
  lazyPage({ id: 'orchestras', title: 'Команды агентов', icon: 'plus', nav: 'more' },
    () => import('./orchestras.js'), (m) => m.default),
  lazyPage({ id: 'forks', title: 'Развилки', icon: 'retry', nav: 'more' },
    () => import('./forks.js'), (m) => m.default),
  lazyPage({ id: 'healing', title: 'Восстановление', icon: 'check', nav: 'more' },
    () => import('./healing.js'), (m) => m.default),
  lazyPage({ id: 'openrouter', title: 'OpenRouter', icon: 'models', nav: 'more' },
    () => import('./openrouter.js'), (m) => m.default),
  lazyPage({ id: 'command', title: 'Пульт', icon: 'bolt', nav: 'primary' },
    () => import('./mobile.js'), (m) => m.default),
  lazyPage({ id: 'builder', title: 'Конструктор миссий', icon: 'bolt', nav: 'primary' },
    () => import('./builder.js'), (m) => m.default),
  lazyPage({ id: 'images', title: 'Изображения', icon: 'models', nav: 'primary' },
    () => import('./images.js'), (m) => m.default),
  lazyPage({ id: 'trading_lab', title: 'Обучение трейдингу', icon: 'activity', nav: 'more' },
    () => import('./trading_lab.js'), (m) => m.default),
  lazyPage({ id: 'mission_console', title: 'Операторский канал', icon: 'activity', nav: 'primary', section: 'main' },
    () => import('./mission_console.js'), (m) => m.default),
  lazyPage({ id: 'web_research', title: 'Поиск в интернете', icon: 'browser', nav: 'more' },
    () => import('./web_research.js'), (m) => m.default),
  lazyPage({ id: 'control', title: 'Пульт', icon: 'home', nav: 'primary' },
    () => import('./control.js'), (m) => m.default),
  lazyPage({ id: 'web_designer', title: 'Веб-дизайн', icon: 'builder', nav: 'primary', section: 'main' },
    () => import('./web_designer.js'), (m) => m.default),
  lazyPage({ id: 'objectives', title: 'Цели', icon: 'target', nav: 'more' },
    () => import('./objectives.js'), (m) => m.default),
];

/* Предзагрузка остальных страниц в простое: по одной, чтобы не отбирать сеть и
   CPU у первой страницы и у шины. Выключается window.__bxPreload = false
   (замеры «без предзагрузки», тесты). Ошибки не пробрасываются: страница,
   которую не удалось предзагрузить, догрузится при переходе. */
export async function preloadFeaturePages() {
  if (typeof window !== 'undefined' && window.__bxPreload === false) return 0;
  let loaded = 0;
  for (const page of FEATURE_PAGES) {
    if (page.__loaded) continue;
    try { await page.__load(); loaded += 1; } catch { /* догрузится при переходе */ }
  }
  return loaded;
}
