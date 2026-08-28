/* ============================================================
   app.js — оболочка BOSSMAN Command Center:
   вход по токену, роутер, WS, тема, командная палитра.
   ============================================================ */

import { api, ApiError, EventStream, hasSession, clearCsrf, listOf, UNAUTHORIZED_EVENT } from './api.js';
import {
  h, append, clear, replace, icon, dot, empty, loading, toast, toastError,
  closeTopModal, hasOpenModal, debounce, fmtGb,
} from './components.js';
import { PAGES, openTaskModal, openAgentModal, openScheduleModal, openModelWizard, stopAllRunning } from './pages.js';
import { FEATURE_PAGES } from './pages/index.js';

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
  connDot: document.getElementById('conn-dot'),
  connText: document.getElementById('conn-text'),
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
// пользуются, ниже — техническая часть. Страница объявляет свой раздел полем
// `section`; всё, что его не объявило, считается системным.
const SECTIONS = [
  { id: 'main', label: 'Основное' },
  { id: 'system', label: 'Система' },
];
const MAIN_ORDER = ['home-v3', 'apps', 'missions', 'agents', 'approvals'];

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
  icon(page.icon, 16),
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

function sectionOf(page) {
  if (page.section) return page.section;
  return MAIN_ORDER.includes(page.id) ? 'main' : 'system';
}

function buildNav() {
  clear(el.nav);
  clear(el.mobilenav);

  // Вытесненные посадочные страницы остаются доступны по прямой ссылке, но в
  // меню их нет: две «главных» рядом — это вопрос «а какая настоящая».
  const visible = PAGES.filter((p) => !SUPERSEDED.has(p.id));

  const buckets = new Map(SECTIONS.map((s) => [s.id, []]));
  for (const page of visible) {
    const bucket = buckets.get(sectionOf(page)) || buckets.get('system');
    bucket.push(page);
  }

  // Порядок основного раздела задан явно: он отражает частоту использования,
  // а не порядок, в котором страницы когда-то написали.
  const main = buckets.get('main');
  main.sort((a, b) => {
    const ia = MAIN_ORDER.indexOf(a.id);
    const ib = MAIN_ORDER.indexOf(b.id);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });

  for (const section of SECTIONS) {
    const pages = buckets.get(section.id) || [];
    if (!pages.length) continue;
    el.nav.appendChild(h('div.nav-section', section.label));
    for (const page of pages) el.nav.appendChild(navButton(page));
  }

  // Нижняя панель телефона — только основной раздел: системные страницы там
  // не помещаются и на телефоне почти не нужны. Они остаются в боковом меню
  // и в командной палитре.
  for (const page of main.slice(0, 5)) el.mobilenav.appendChild(mnavButton(page));
}

function syncNav() {
  for (const node of document.querySelectorAll('[data-page]')) {
    node.classList.toggle('active', node.dataset.page === currentPage);
  }
}

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

function syncConn(stateName) {
  const map = {
    open: ['dot dot-ok', 'live-обновления'],
    connecting: ['dot dot-warn dot-live', 'подключение…'],
    closed: ['dot dot-err', 'нет соединения'],
    idle: ['dot dot-idle', 'соединение…'],
  };
  const [cls, text] = map[stateName] || map.idle;
  el.connDot.className = cls;
  el.connText.textContent = text;
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
    syncConn(name);
    if (name === 'open' && state.ready) {
      /* при переподключении перечитываем данные активной страницы */
      renderPage();
      refreshApprovals();
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
    const warn = values.some((v) => ['degraded', 'warning', 'warn'].includes(v.toLowerCase()));
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
