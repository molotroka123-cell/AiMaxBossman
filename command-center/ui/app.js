/* ============================================================
   app.js — оболочка BOSSMAN Command Center:
   вход по токену, роутер, WS, тема, командная палитра.
   ============================================================ */

import { api, ApiError, EventStream, hasSession, clearCsrf, listOf, UNAUTHORIZED_EVENT } from './api.js';
import {
  h, append, clear, replace, icon, dot, empty, loading, toast, toastError, toastOk,
  closeTopModal, hasOpenModal, debounce, fmtGb, fmtClock, fmtDuration,
} from './components.js';
import { PAGES, openTaskModal, openAgentModal, openScheduleModal, openModelWizard, stopAllRunning } from './pages.js';
import { FEATURE_PAGES } from './pages/index.js';
import { mountThinking } from './thinking.js';
import { mountTestingPeriod } from './testing.js';
import { mountCommandBar } from './commandbar.js';

PAGES.push(...FEATURE_PAGES); // V2-страницы встают в общую навигацию

/* ---------------- Ссылки на DOM ---------------- */

const el = {
  login: document.getElementById('login'),
  loginForm: document.getElementById('login-form'),
  loginToken: document.getElementById('login-token'),
  loginError: document.getElementById('login-error'),
  loginSubmit: document.getElementById('login-submit'),
  shell: document.getElementById('shell'),
  nav: document.getElementById('nav'),
  mobilenav: document.getElementById('mobilenav'),
  view: document.getElementById('view'),
  title: document.getElementById('page-title'),
  stats: document.getElementById('topbar-stats'),
  themeBtn: document.getElementById('theme-toggle'),
  refreshBtn: document.getElementById('refresh-btn'),
  menuBtn: document.getElementById('mobile-menu'),
  scrim: document.getElementById('scrim'),
  conn: document.getElementById('conn'),
  connDot: document.getElementById('conn-dot'),
  connText: document.getElementById('conn-text'),
  stale: document.getElementById('stale-banner'),
  staleText: document.getElementById('stale-text'),
  staleRetry: document.getElementById('stale-retry'),
  staleNow: document.getElementById('stale-now'),
  paletteBtn: document.getElementById('palette-open'),
  paletteKbd: document.getElementById('palette-kbd'),
  palette: document.getElementById('palette'),
  paletteInput: document.getElementById('palette-input'),
  paletteList: document.getElementById('palette-list'),
};

const IS_MAC = /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent);
const THEME_KEY = 'bcc.theme';
const PAGE_BY_ID = new Map(PAGES.map((p) => [p.id, p]));
// Посадочная страница по старшинству: home-v3 (лаунчер) → overview (V2) → home (MVP).
// Прежние страницы остаются в PAGE_BY_ID, поэтому прямые ссылки #/home и
// #/overview продолжают работать; из навигации они уходят, чтобы не было двух
// «главных» одновременно.
const LANDING = ['home-v3', 'overview', 'home'];
const DEFAULT_PAGE = LANDING.find((id) => PAGE_BY_ID.has(id)) || 'home';
const SUPERSEDED = new Set(LANDING.slice(LANDING.indexOf(DEFAULT_PAGE) + 1));

// Сайдбар устроен от человека, а не от устройства системы: сверху то, чем
// пользуются каждый день, ниже — четыре сворачиваемых раздела по смыслу.
// Двадцать три страницы одним столбиком не помещались в экран и требовали
// прокрутки, чтобы найти нужную; раздел из пяти-семи строк читается сразу.
// Порядок внутри раздела — по частоте использования, а не по дате появления
// страницы. Страница может объявить раздел полем `section`; всё, что не
// названо ни здесь, ни там, попадает в «Систему», как и раньше.
const NAV_GROUPS = [
  { id: 'main', label: 'Основное', pinned: true,
    pages: ['home-v3', 'apps', 'missions', 'agents', 'approvals', 'mission_console'] },
  { id: 'work', label: 'Работа',
    pages: ['tasks', 'schedules', 'builder', 'orchestras', 'forks', 'agentmap', 'command'] },
  { id: 'models', label: 'Модели',
    pages: ['models', 'router', 'openrouter', 'benchmarks'] },
  { id: 'tools', label: 'Инструменты',
    pages: ['terminal', 'browser', 'web_research', 'coding', 'skills', 'images', 'trading_lab'] },
  { id: 'system', label: 'Система',
    pages: ['system', 'resources', 'governor', 'healing', 'settings'] },
];
const NAV_GROUPS_KEY = 'bcc.nav.groups';

/* ---------------- Состояние ---------------- */

const state = {
  models: [], agents: [], providers: [], tasks: [],
  approvals: 0,
  system: null,
  ready: false,
  countsKnown: false,
};

let currentPage = DEFAULT_PAGE;
let currentParams = {};
let renderToken = 0;
let pendingRefresh = false;
let lastRendered = null;

const bus = new EventStream();
/* UX 2.0: панель «Процесс работы» — открывается кнопкой в шапке или Ctrl+. */
const thinking = mountThinking({ bus, api, button: document.getElementById('think-open') });
window.__bxThinking = thinking;

/* ---------------- Тема ---------------- */

function getTheme() {
  try { return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'; }
  catch { return 'dark'; }
}

function setTheme(theme) {
  const value = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', value);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', value === 'light' ? '#f4f6f9' : '#0a0c10');
  try { localStorage.setItem(THEME_KEY, value); } catch { /* приватный режим */ }
  syncThemeButton();
}

function syncThemeButton() {
  if (!el.themeBtn) return;
  const next = getTheme() === 'dark' ? 'sun' : 'moon';
  clear(el.themeBtn);
  el.themeBtn.appendChild(icon(next, 16));
  el.themeBtn.title = getTheme() === 'dark' ? 'Светлая тема' : 'Тёмная тема';
}

setTheme(getTheme());

/* ---------------- Контекст для страниц ---------------- */

const ctx = {
  state,
  bus,
  navigate,
  refresh,
  scheduleRefresh,
  setBadge,
  getTheme,
  setTheme,
  logout,
  hasSession,
};

/* ---------------- Навигация ---------------- */

function parseHash() {
  const raw = String(location.hash || '').replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  const id = PAGE_BY_ID.has(path) ? path : DEFAULT_PAGE;
  const params = {};
  if (query) {
    for (const part of query.split('&')) {
      const [k, v] = part.split('=');
      if (k) params[decodeURIComponent(k)] = decodeURIComponent(v || '');
    }
  }
  return { id, params };
}

function navigate(id, params = null) {
  const query = params && Object.keys(params).length
    ? '?' + Object.entries(params).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&')
    : '';
  const next = `#/${id}${query}`;
  if (location.hash === next) { onRoute(); return; }
  location.hash = next;
}

function onRoute() {
  const { id, params } = parseHash();
  currentPage = id;
  currentParams = params;
  setMenu(false);
  syncNav();
  const page = PAGE_BY_ID.get(id);
  el.title.textContent = page ? page.title : 'Command Center';
  document.title = page ? `${page.title} · BOSSMAN` : 'BOSSMAN Command Center';
  renderPage();
}

async function renderPage() {
  const page = PAGE_BY_ID.get(currentPage);
  if (!page) return;
  const token = ++renderToken;

  if (page.enter) {
    try { await page.enter(ctx, currentParams); } catch (e) { console.error(e); }
  }
  if (token !== renderToken) return;

  /* Скелет — только при смене страницы. Обновления по WS не должны мигать. */
  if (lastRendered !== currentPage || !el.view.firstChild) replace(el.view, loading(3));

  try {
    const node = await page.render(ctx, currentParams);
    if (token !== renderToken) return;
    lastRendered = currentPage;
    replace(el.view, node);
    syncTopStats();
    if (typeof window.scrollTo === 'function') window.scrollTo({ top: 0 });
  } catch (e) {
    if (token !== renderToken) return;
    if (e instanceof ApiError && e.isAuth) { showLogin('Сессия не подтверждена — введите токен заново.'); return; }
    replace(el.view, h('section.panel', empty({
      iconName: 'info',
      title: (e && e.message) || 'Страница не загрузилась',
      hint: (e && e.hint) || 'Проверьте, что сервер Command Center работает, и повторите.',
      action: h('button.btn.btn-primary', { type: 'button', onClick: () => refresh() }, 'Повторить'),
    })));
  }
}

function refresh() {
  pendingRefresh = false;
  el.refreshBtn.classList.add('spin');
  setTimeout(() => el.refreshBtn.classList.remove('spin'), 600);
  renderPage();
}

function isTypingInView() {
  if (hasOpenModal()) return true;
  const active = document.activeElement;
  if (!active) return false;
  if (!el.view.contains(active)) return false;
  const tag = active.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}

const scheduleRefreshInner = debounce(() => {
  if (isTypingInView()) { pendingRefresh = true; return; }
  pendingRefresh = false;
  renderPage();
}, 350);

function scheduleRefresh() { scheduleRefreshInner(); }

/* Если пользователь печатал — довыполним отложенное обновление, когда освободится. */
setInterval(() => {
  if (pendingRefresh && !isTypingInView()) { pendingRefresh = false; renderPage(); }
}, 2500);

/* ---------------- Меню и навигация ---------------- */

function navButton(page) {
  return h('button', {
    type: 'button', class: 'nav-item', dataset: { page: page.id },
    onClick: () => navigate(page.id),
  },
  icon(page.icon, 15),
  h('span.nav-label', page.title),
  h('span', { class: 'nav-badge', dataset: { badge: page.id }, hidden: true }));
}

function mnavButton(page) {
  return h('button', {
    type: 'button', class: 'mnav-item', dataset: { page: page.id },
    onClick: () => navigate(page.id),
  },
  icon(page.icon, 18),
  h('span', page.title),
  h('span', { class: 'mnav-dot', dataset: { badge: page.id }, hidden: true }));
}

/* Раздел страницы: явное поле `section`, иначе — место в NAV_GROUPS, иначе «Система». */
function groupOf(page) {
  if (page.section && NAV_GROUPS.some((g) => g.id === page.section)) return page.section;
  const listed = NAV_GROUPS.find((g) => g.pages.includes(page.id));
  return listed ? listed.id : 'system';
}

/* Какие разделы владелец раскрыл сам. Хранится в браузере: это удобство
   просмотра, а не данные системы. Раздел с активной страницей открыт всегда,
   независимо от этой настройки, — иначе выбранное можно потерять из виду. */
function loadGroupPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(NAV_GROUPS_KEY) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch { return {}; }
}
function saveGroupPrefs(prefs) {
  try { localStorage.setItem(NAV_GROUPS_KEY, JSON.stringify(prefs)); } catch { /* приватный режим */ }
}

const navGroupPrefs = loadGroupPrefs();
const navGroupNodes = new Map();  // id раздела → { root, head, body, pages }
const navForcedOpen = new Set();  // раздел раскрыт автоматически из-за активной страницы

function isGroupOpen(group) {
  if (group.pinned) return true;
  if (navForcedOpen.has(group.id)) return true;
  return navGroupPrefs[group.id] === true;
}

function applyGroupState(group) {
  const node = navGroupNodes.get(group.id);
  if (!node) return;
  const open = isGroupOpen(group);
  node.root.classList.toggle('open', open);
  if (node.head.tagName === 'BUTTON') node.head.setAttribute('aria-expanded', open ? 'true' : 'false');
  node.body.hidden = !open;
}

function toggleGroup(group) {
  const open = isGroupOpen(group);
  navForcedOpen.delete(group.id);
  navGroupPrefs[group.id] = !open;
  saveGroupPrefs(navGroupPrefs);
  applyGroupState(group);
}

function buildNav() {
  clear(el.nav);
  clear(el.mobilenav);
  navGroupNodes.clear();

  // Вытесненные посадочные страницы остаются доступны по прямой ссылке, но в
  // меню их нет: две «главных» рядом — это вопрос «а какая настоящая».
  const visible = PAGES.filter((p) => !SUPERSEDED.has(p.id));

  const buckets = new Map(NAV_GROUPS.map((g) => [g.id, []]));
  for (const page of visible) buckets.get(groupOf(page)).push(page);

  // Внутри раздела порядок задан списком в NAV_GROUPS; страницы, которых там
  // нет, идут следом в порядке регистрации — ни одна не теряется.
  const rank = (group, page) => { const i = group.pages.indexOf(page.id); return i < 0 ? 99 : i; };

  for (const group of NAV_GROUPS) {
    const pages = buckets.get(group.id);
    if (!pages.length) continue;
    pages.sort((a, b) => rank(group, a) - rank(group, b));
    group.pageIds = pages.map((p) => p.id);

    const bodyId = `nav-group-${group.id}`;
    const body = h('div', { class: 'nav-group-body', id: bodyId, role: 'group', 'aria-label': group.label },
      pages.map(navButton));

    // Закреплённый раздел — просто подпись; остальные — кнопка-переключатель со
    // стрелкой и числом страниц, чтобы свёрнутый раздел не выглядел пустым.
    const head = group.pinned
      ? h('div.nav-group-head.is-static', h('span.nav-group-label', group.label))
      : h('button', {
        type: 'button', class: 'nav-group-head', dataset: { group: group.id },
        'aria-controls': bodyId, 'aria-expanded': 'false',
        onClick: () => toggleGroup(group),
      },
      icon('chevron', 12),
      h('span.nav-group-label', group.label),
      h('span.nav-group-count', { 'aria-hidden': 'true' }, String(pages.length)),
      h('span.nav-group-dot', { title: 'здесь открытая страница' }));

    const root = h('div', { class: 'nav-group', dataset: { group: group.id } }, head, body);
    navGroupNodes.set(group.id, { root, head, body, pages });
    el.nav.appendChild(root);
    applyGroupState(group);
  }

  // Нижняя панель телефона — только основной раздел: системные страницы там
  // не помещаются и на телефоне почти не нужны. Они остаются в боковом меню
  // и в командной палитре.
  for (const page of buckets.get('main').slice(0, 5)) el.mobilenav.appendChild(mnavButton(page));
}

function syncNav() {
  for (const node of document.querySelectorAll('[data-page]')) {
    const active = node.dataset.page === currentPage;
    node.classList.toggle('active', active);
    if (active) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  }
  // Раздел с открытой страницей раскрывается сам; если владелец его потом свернёт,
  // на заголовке останется точка — выбранное не исчезает из виду.
  for (const group of NAV_GROUPS) {
    const node = navGroupNodes.get(group.id);
    if (!node) continue;
    const hasActive = (group.pageIds || []).includes(currentPage);
    node.root.classList.toggle('has-active', hasActive);
    if (hasActive && !isGroupOpen(group)) navForcedOpen.add(group.id);
    if (!hasActive) navForcedOpen.delete(group.id);
    applyGroupState(group);
  }
}

/* Стрелки ходят по видимым строкам меню (страницы и заголовки разделов),
   Home/End — к краям. Tab по-прежнему работает как обычно. */
el.nav.addEventListener('keydown', (e) => {
  const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
  if (!keys.includes(e.key)) return;
  const rows = Array.from(el.nav.querySelectorAll('button.nav-item, button.nav-group-head'))
    .filter((b) => b.offsetParent !== null);
  const i = rows.indexOf(document.activeElement);
  if (i < 0) return;
  e.preventDefault();
  let next = i;
  if (e.key === 'ArrowDown') next = Math.min(i + 1, rows.length - 1);
  else if (e.key === 'ArrowUp') next = Math.max(i - 1, 0);
  else if (e.key === 'Home') next = 0;
  else next = rows.length - 1;
  rows[next].focus();
});

function setBadge(pageId, count) {
  if (pageId === 'approvals') state.approvals = count;
  for (const node of document.querySelectorAll(`[data-badge="${pageId}"]`)) {
    node.hidden = !count;
    node.textContent = count > 99 ? '99+' : String(count);
  }
}

function setMenu(open) {
  el.shell.classList.toggle('menu-open', !!open);
  if (!hasOpenModal()) el.scrim.hidden = !open;
}

/* ---------------- Верхняя строка состояния ---------------- */

function syncTopStats(sys) {
  if (sys) state.system = sys;
  const s = state.system;
  clear(el.stats);
  const items = [];

  if (state.countsKnown) {
    items.push(h('span.stat', h('b', String(state.agents.length)), h('span', 'агентов')));
    items.push(h('span.stat', h('b', String(state.models.length)), h('span', 'моделей')));
  }

  if (s) {
    if (s.cpu !== null && s.cpu !== undefined) items.push(h('span.stat', h('span', 'CPU'), h('b', `${Math.round(s.cpu)}%`)));
    if (s.ramTotal) items.push(h('span.stat', h('span', 'RAM'), h('b', `${fmtGb(s.ramUsed)}/${fmtGb(s.ramTotal)} ГБ`)));
    items.push(h('span.stat', dot(s.overall === 'ok' ? 'online' : s.overall === 'warn' ? 'warning' : 'error'),
      h('span', s.overall === 'ok' ? 'в норме' : s.overall === 'warn' ? 'предупреждение' : 'сбой')));
  }
  append(el.stats, items);
}

/* ---------------- Соединение: индикатор, обратный отсчёт, баннер устаревших данных ---------------- */

const conn = { state: 'idle', tick: null, wasDown: false };

function connLabel() {
  const left = bus.nextRetryAt ? Math.max(0, Math.ceil((bus.nextRetryAt - Date.now()) / 1000)) : 0;
  switch (conn.state) {
    case 'open': return 'live-обновления';
    case 'connecting': return bus.attempt > 0 ? `подключение… (попытка ${bus.attempt + 1})` : 'подключение…';
    case 'closed': return left > 0 ? `нет соединения · повтор через ${left} с` : 'нет соединения';
    default: return 'соединение…';
  }
}

function syncConn(stateName) {
  const map = {
    open: 'dot dot-ok',
    connecting: 'dot dot-warn dot-live',
    closed: 'dot dot-err',
    idle: 'dot dot-idle',
  };
  conn.state = map[stateName] ? stateName : 'idle';
  el.connDot.className = map[conn.state];
  el.connText.textContent = connLabel();
  el.conn.dataset.state = conn.state;
  clearInterval(conn.tick);
  conn.tick = null;
  if (conn.state === 'closed' || conn.state === 'connecting') {
    conn.tick = setInterval(() => { el.connText.textContent = connLabel(); syncStaleBanner(); }, 500);
  }
  syncStaleBanner();
}

function syncStaleBanner() {
  const b = el.stale;
  if (!b) return;
  // Баннер «данные устарели» имеет смысл только после первой успешной связи:
  // при самом первом подключении устаревать ещё нечему.
  const down = state.ready && (conn.state === 'closed' || conn.state === 'connecting')
    && bus.disconnectedAt > 0 && bus.lastOpenAt > 0;
  if (!down) { b.hidden = true; return; }
  const since = bus.disconnectedAt;
  const left = bus.nextRetryAt ? Math.max(0, Math.ceil((bus.nextRetryAt - Date.now()) / 1000)) : 0;
  el.staleText.textContent = `Нет связи с сервером с ${fmtClock(since / 1000, true)} (${fmtDuration(Date.now() - since)}). `
    + 'Данные на экране могли устареть.';
  el.staleRetry.textContent = conn.state === 'connecting'
    ? 'Подключаемся…'
    : (left > 0 ? `Повтор через ${left} с` : 'Повтор…');
  el.staleNow.disabled = conn.state === 'connecting';
  b.hidden = false;
}

function onConnRestored(ev) {
  /* при переподключении перечитываем данные активной страницы и говорим об этом владельцу */
  renderPage();
  refreshApprovals();
  const down = ev && ev.downtime_ms ? ` после ${fmtDuration(ev.downtime_ms)} без связи` : '';
  toastOk('Соединение восстановлено', `Данные обновлены${down}.`);
}

/* ---------------- Шина событий ---------------- */

const refreshApprovals = debounce(async () => {
  try { setBadge('approvals', listOf(await api.approvals('pending'), 'approvals').length); }
  catch { /* бейдж не критичен */ }
}, 400);

bus.subscribe((ev) => {
  const kind = String(ev.kind || '');

  if (kind.startsWith('ws.')) {
    const name = kind.slice(3);
    if (name === 'retry_scheduled') { syncConn(conn.state === 'open' ? 'closed' : conn.state); return; }
    syncConn(name);
    if (name === 'open' && state.ready) {
      if (ev.reconnected) onConnRestored(ev);
      else { renderPage(); refreshApprovals(); }
    }
    return;
  }

  if (kind.startsWith('approval.')) refreshApprovals();

  if (kind === 'system.metrics') {
    const data = ev.data && typeof ev.data === 'object' ? ev.data : ev;
    const cpu = Number(data.cpu_pct ?? data.cpu);
    const ramUsed = Number(data.ram_used_mb);
    const ramTotal = Number(data.ram_total_mb);
    state.system = {
      ...(state.system || {}),
      cpu: Number.isFinite(cpu) ? cpu : (state.system ? state.system.cpu : null),
      ramUsed: Number.isFinite(ramUsed) ? ramUsed : (state.system ? state.system.ramUsed : null),
      ramTotal: Number.isFinite(ramTotal) ? ramTotal : (state.system ? state.system.ramTotal : null),
      overall: state.system ? state.system.overall : 'ok',
    };
    syncTopStats();
  }

  const page = PAGE_BY_ID.get(currentPage);
  if (page && page.onEvent) {
    let wants = false;
    try { wants = page.onEvent(ev, ctx); } catch (e) { console.error(e); }
    if (wants) scheduleRefresh();
  }
});

/* ---------------- Командная палитра ---------------- */

let paletteItems = [];
let paletteSel = 0;

function paletteActions() {
  const activeCount = (state.tasks || []).filter((t) => ['running', 'queued', 'paused'].includes(String(t.status))).length;
  const items = [
    { group: 'Действия', title: 'Процесс работы', sub: 'что система делает прямо сейчас', iconName: 'system', keys: 'process thinking live progress', run: () => thinking.open() },
    { group: 'Действия', title: 'Новая задача', sub: 'композер + агент', iconName: 'play', keys: 'task new zadacha novaya', run: () => openTaskModal(ctx) },
    { group: 'Действия', title: 'Новый агент', sub: 'роль, prompt, модель', iconName: 'agents', keys: 'agent new agent novy', run: () => openAgentModal(ctx) },
    { group: 'Действия', title: 'Добавить модель', sub: 'провайдер → модель', iconName: 'models', keys: 'model add provider', run: () => openModelWizard(ctx) },
    { group: 'Действия', title: 'Новое расписание', sub: 'once / daily / interval', iconName: 'schedules', keys: 'schedule cron', run: () => openScheduleModal(ctx) },
    {
      group: 'Действия',
      title: 'Остановить все активные',
      sub: activeCount ? `сейчас активны: ${activeCount}` : 'проверить и остановить',
      iconName: 'stop', keys: 'stop all running ostanovit',
      run: () => stopAllRunning(ctx),
    },
  ];
  for (const page of PAGES) {
    items.push({ group: 'Страницы', title: `Открыть · ${page.title}`, iconName: page.icon, keys: `${page.id} ${page.title}`, run: () => navigate(page.id) });
  }
  items.push({ group: 'Прочее', title: getTheme() === 'dark' ? 'Светлая тема' : 'Тёмная тема', iconName: getTheme() === 'dark' ? 'sun' : 'moon', keys: 'theme tema', run: () => setTheme(getTheme() === 'dark' ? 'light' : 'dark') });
  items.push({ group: 'Прочее', title: 'Обновить данные', iconName: 'retry', keys: 'refresh obnovit', run: () => refresh() });
  items.push({ group: 'Прочее', title: 'Выйти (завершить сессию)', iconName: 'logout', keys: 'logout exit vyhod', run: () => logout() });
  return items;
}

function openPalette() {
  el.palette.hidden = false;
  el.paletteInput.value = '';
  paletteSel = 0;
  renderPalette('');
  setTimeout(() => el.paletteInput.focus(), 20);
}

function closePalette() {
  el.palette.hidden = true;
}

function renderPalette(query) {
  const q = String(query || '').trim().toLowerCase();
  const all = paletteActions();
  paletteItems = q
    ? all.filter((a) => `${a.title} ${a.sub || ''} ${a.keys || ''}`.toLowerCase().includes(q))
    : all;
  if (paletteSel >= paletteItems.length) paletteSel = Math.max(0, paletteItems.length - 1);

  clear(el.paletteList);
  if (!paletteItems.length) {
    el.paletteList.appendChild(h('div.palette-empty', 'Ничего не найдено'));
    return;
  }
  let lastGroup = null;
  paletteItems.forEach((item, i) => {
    if (item.group !== lastGroup) {
      lastGroup = item.group;
      el.paletteList.appendChild(h('div.palette-group', item.group));
    }
    el.paletteList.appendChild(h('button', {
      type: 'button',
      class: 'palette-item' + (i === paletteSel ? ' sel' : ''),
      onClick: () => runPalette(i),
      onMouseEnter: () => { paletteSel = i; highlightPalette(); },
    },
    h('span.p-icon', icon(item.iconName || 'bolt', 15)),
    h('span.p-text', item.title),
    item.sub ? h('span.p-sub', item.sub) : null));
  });
  highlightPalette();
}

function highlightPalette() {
  const nodes = el.paletteList.querySelectorAll('.palette-item');
  nodes.forEach((n, i) => n.classList.toggle('sel', i === paletteSel));
  const active = nodes[paletteSel];
  if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
}

function runPalette(index) {
  const item = paletteItems[index];
  if (!item) return;
  closePalette();
  try { item.run(); } catch (e) { toastError(e); }
}

el.paletteInput.addEventListener('input', () => { paletteSel = 0; renderPalette(el.paletteInput.value); });
el.paletteInput.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { e.preventDefault(); paletteSel = Math.min(paletteSel + 1, paletteItems.length - 1); highlightPalette(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); paletteSel = Math.max(paletteSel - 1, 0); highlightPalette(); }
  else if (e.key === 'Enter') { e.preventDefault(); runPalette(paletteSel); }
  else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
});
el.palette.addEventListener('mousedown', (e) => { if (e.target === el.palette) closePalette(); });

/* ---------------- Глобальные горячие клавиши ---------------- */

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    if (el.palette.hidden) openPalette(); else closePalette();
    return;
  }
  if (e.key === 'Escape') {
    if (!el.palette.hidden) { closePalette(); return; }
    if (closeTopModal()) return;
    if (el.shell.classList.contains('menu-open')) setMenu(false);
  }
});

/* ---------------- Вход и выход ---------------- */

function showLogin(message = '') {
  bus.stop();
  state.ready = false;
  lastRendered = null;
  el.shell.hidden = true;
  el.login.hidden = false;
  el.loginError.hidden = !message;
  el.loginError.textContent = message;
  setTimeout(() => el.loginToken.focus(), 40);
}

function showShell() {
  el.login.hidden = true;
  el.shell.hidden = false;
}

async function logout() {
  // выход инвалидирует сессию на сервере, а не только в браузере
  try { await api.logout(); } catch { clearCsrf(); }
  el.loginToken.value = '';
  showLogin('Сессия завершена. Введите токен, чтобы войти снова.');
}

el.loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const value = el.loginToken.value.trim();
  if (!value) {
    el.loginError.hidden = false;
    el.loginError.textContent = 'Введите токен.';
    return;
  }
  el.loginSubmit.classList.add('busy');
  el.loginError.hidden = true;
  try {
    await api.login(value);          // токен → серверная сессия (HttpOnly-cookie)
    el.loginToken.value = '';        // сам токен в браузере не оставляем
    await boot();
  } catch (err) {
    clearCsrf();
    el.loginError.hidden = false;
    el.loginError.textContent = err && err.status === 401
      ? 'Токен не подошёл. Скопируйте его из консоли сервера.'
      : (err && err.message) || 'Не удалось войти.';
  } finally {
    el.loginSubmit.classList.remove('busy');
  }
});

/* ---------------- Загрузка ---------------- */

async function boot() {
  if (!hasSession()) { showLogin(); return; }

  try {
    await api.system();
  } catch (err) {
    if (err instanceof ApiError && err.isAuth) { clearCsrf(); showLogin('Сессия недействительна. Токен печатается в консоли сервера при старте.'); return; }
    if (err instanceof ApiError && err.isOffline) {
      /* сервер не отвечает — покажем оболочку, страница сама предложит повторить */
      toast('Сервер не отвечает', { type: 'err', hint: 'Проверьте, что процесс Command Center запущен.', timeout: 9000 });
    }
  }

  showShell();
  state.ready = true;
  syncNav();
  bus.start();
  onRoute();

  /* Тестовый период: плашка и запись действий. Наблюдатель, а не условие
     запуска — его отказ не должен мешать приложению работать. */
  mountTestingPeriod().catch(() => {});

  /* Командная строка. Здесь же, а не в начале файла: каталог возможностей
     читается с сервера, и до входа этот запрос получил бы 401 — панель молча
     осталась бы пустой. При выключенном флаге она сама себя прячет. */
  mountCommandBar();

  /* стартовые данные для верхней строки и бейджа */
  refreshApprovals();
  loadCounts();
  loadTopStats();
  if (!statsTimer) statsTimer = setInterval(loadTopStats, 30000);
}

let statsTimer = null;

async function loadCounts() {
  const [modelsR, agentsR] = await Promise.allSettled([api.models(), api.agents()]);
  if (modelsR.status === 'fulfilled') state.models = listOf(modelsR.value, 'models');
  if (agentsR.status === 'fulfilled') state.agents = listOf(agentsR.value, 'agents');
  state.countsKnown = modelsR.status === 'fulfilled' || agentsR.status === 'fulfilled';
  syncTopStats();
}

async function loadTopStats() {
  if (!state.ready || document.hidden) return;
  try {
    const raw = await api.system();
    const d = raw && typeof raw === 'object' ? raw : {};
    const cur = (d.current && typeof d.current === 'object') ? d.current : d;
    const rawHealth = d.health || d.components || {};
    const values = Array.isArray(rawHealth)
      ? rawHealth.map((c) => String(c.status || c.state || 'unknown'))
      : Object.values(rawHealth).map((v) => (v && typeof v === 'object' ? String(v.status || v.state || 'unknown') : String(v)));
    const bad = values.some((v) => ['down', 'error', 'failed', 'false', 'offline'].includes(v.toLowerCase()));
    /* no-fake-green: пустые/неизвестные статусы не могут выглядеть зелёными */
    const warnSet = ['degraded', 'warning', 'warn', 'unknown', 'empty', 'stale', 'starting', 'stopped'];
    const warn = values.length === 0 || values.some((v) => warnSet.includes(v.toLowerCase()));
    syncTopStats({
      cpu: cur.cpu_pct === undefined || cur.cpu_pct === null ? null : Number(cur.cpu_pct),
      ramUsed: cur.ram_used_mb === undefined ? null : Number(cur.ram_used_mb),
      ramTotal: cur.ram_total_mb === undefined ? null : Number(cur.ram_total_mb),
      overall: bad ? 'err' : warn ? 'warn' : 'ok',
    });
  } catch { /* верхняя строка не критична */ }
}

/* ---------------- Прочие обработчики ---------------- */

el.themeBtn.addEventListener('click', () => setTheme(getTheme() === 'dark' ? 'light' : 'dark'));
el.refreshBtn.addEventListener('click', () => refresh());
if (el.staleNow) el.staleNow.addEventListener('click', () => { bus.reconnectNow(); syncConn(bus.state); });
window.__bxConn = { bus, label: connLabel };
window.__bxPages = PAGES.map((p) => ({ id: p.id, title: p.title, nav: p.nav || 'primary' }));
el.menuBtn.addEventListener('click', () => setMenu(!el.shell.classList.contains('menu-open')));
el.scrim.addEventListener('click', () => { if (el.shell.classList.contains('menu-open')) setMenu(false); });
el.paletteBtn.addEventListener('click', () => openPalette());
window.addEventListener('hashchange', onRoute);

/* Любой ответ 401/403 из api.js — сразу на экран входа. */
window.addEventListener(UNAUTHORIZED_EVENT, () => {
  if (!state.ready) return;
  showLogin('Токен больше не принимается. Введите актуальный токен из консоли сервера.');
});

el.paletteKbd.textContent = IS_MAC ? '⌘ K' : 'Ctrl K';

buildNav();
syncTopStats();
syncConn('idle');
boot();
