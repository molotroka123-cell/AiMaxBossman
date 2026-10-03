/* ============================================================
   chat/main.js — настольный чат Bossman (/chat.html).

   Это ещё одна поверхность ТОГО ЖЕ Bossman, а не отдельный продукт: тот же
   сервер Command Center, те же задачи, агенты, модели, память,
   подтверждения, STOP и бюджеты. Треды — это сессии `bossman chat` из
   терминала (/api/chat/threads), ход — обычная задача движка.

   Поток ответа — GET /api/events/stream (stream.js), итог — всегда
   GET /api/chat/threads/{id} (правда сервера, а не то, что успели
   нарисовать). Скрытые рассуждения модели не показываются нигде.

   Лента прилипает к низу по намерению владельца (scroll.js), потеря связи
   показывается баннером с «Сейчас» и не считается провалом задачи, итог
   хода один раз объявляется читалке экрана (#chat-announce).
   ============================================================ */

import { api, ApiError, hasSession, clearCsrf, UNAUTHORIZED_EVENT, listOf } from '../api.js';
import { h, icon, iconButton, clear, prefersReducedMotion } from './dom.js';
import { sphere, setSphereState } from './sphere.js';
import { createTurn, applyEvent, applyTruth, isTerminalStatus, memorySourcesFromRunEvents, answerText } from './state.js';
import {
  buildPickerModel, routeCard, contextMeter, tpsLabel, latencyLabel, costLabel, localityBadge, isLoopbackHost,
  exportMarkdown, exportFileName, technicalLogFileName, clientRequestId, raveAgentSpecs, raveSkipReasons, subscriptionState, raveIsLive, placeOfAlias,
} from './format.js';
import { TaskStream } from './stream.js';
import { collectTechnicalLogForTurn } from './technical-log.js';
import { userRow, assistantRow, updateAssistant, turnEndAnnouncement } from './render.js';
import { createStick } from './scroll.js';
import { renderPanel, sourcesCount } from './panel.js';
import { renderSidebar, MOD } from './sidebar.js';
import { createComposer } from './composer.js';

const LS = {
  theme: 'bcc.chat.theme',
  panel: 'bcc.chat.panel',
  sidebar: 'bcc.chat.sidebar',
  route: 'bcc.chat.route',
  groups: 'bcc.chat.groups',
};
/* живость рейва — raveIsLive (format.js): 'partial' и прочие итоги сервера его заканчивают */
const RAVE_LABEL = {
  starting: 'запуск', running: 'идёт', paused: 'пауза', done: 'готово', stopped: 'остановлен',
  partial: 'частично — см. агентов', error: 'ошибка',
};
const WAVE_BARS = 18;
const ATT_NOTE = 'Вложения убраны: они были загружены для другого чата. Прикрепите файлы снова.';

const raveLive = raveIsLive;

function lsGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function lsSet(key, value) { try { localStorage.setItem(key, value); } catch { /* приватный режим */ } }
const enc = encodeURIComponent;
const $ = (id) => document.getElementById(id);

function isAuthErr(err) { return err instanceof ApiError ? err.isAuth : Boolean(err && err.isAuth); }

/* ---------------------------------------------------------------- состояние */

const S = {
  booted: false,
  options: null,
  optionsError: null,
  picker: null,
  pickerModel: null,
  stopNoteTurn: null,
  threads: [],
  threadsLoading: false,
  threadsError: '',
  projects: [],
  filter: { q: '', project: null, archived: false },
  threadId: null,
  thread: null,
  turns: [],
  rows: new Map(),
  raves: new Map(),          // threadId|'' → [карточки рейва этой сессии окна]
  focusTurn: null,
  running: null,
  stream: null,
  streamState: 'idle',
  selection: { mode: 'auto' },
  rave: false,
  raveAgents: { local: true, claude: false, codex: false },
  connectors: null,
  connectorsAt: 0,
  lastRoute: null,
  jeff: null,
  panelOpen: lsGet(LS.panel) === '1',
  sidebarCollapsed: lsGet(LS.sidebar) === '1',
  collapsedGroups: new Set((lsGet(LS.groups) || '').split(',').filter(Boolean)),
  panelCollapsed: new Set(),
  approvalUi: new Map(),
  dirty: new Set(),
  raf: 0,
  seq: 0,
  wave: new Array(WAVE_BARS).fill(0),
  waveBucket: 0,
  waveTimer: 0,
  pendingSend: null,
  searchTimer: 0,
  hashSelf: '',
  modal: null,
  nav: 0,                    // растёт при каждом переходе между тредами (отправка сверяет его после await)
  reconnectTimer: 0,
  reconnectUntil: 0,
  announceTimer: 0,
  approvalTimer: 0,
};

let composer = null;
let stick = null;

/* ---------------------------------------------------------------- тема */

/* без localStorage (приватный режим) тема живёт до перезагрузки окна */
let themeMemory = null;
function currentTheme() { return (lsGet(LS.theme) || themeMemory) === 'light' ? 'light' : 'dark'; }

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'light' ? '#f5f6fa' : '#0b0c12');
}

function toggleTheme() {
  const next = currentTheme() === 'light' ? 'dark' : 'light';
  themeMemory = next;
  lsSet(LS.theme, next);
  applyTheme(next);
  renderSide();
}

/* ---------------------------------------------------------------- тосты и модальные окна */

function toast(text, tone = 'info') {
  const root = $('chat-toasts');
  if (!root) return;
  const el = h('div.toast', { dataset: { tone }, role: tone === 'error' ? 'alert' : 'status' }, text);
  root.appendChild(el);
  setTimeout(() => el.remove(), tone === 'error' ? 7000 : 3500);
}

function closeModal() {
  if (!S.modal) return;
  const { el, restore } = S.modal;
  el.remove();
  S.modal = null;
  if (restore && typeof restore.focus === 'function') restore.focus();
}

function openModal(title, body, actions) {
  closeModal();
  const restore = document.activeElement;
  const el = h('div.modal-backdrop', { onMousedown: (e) => { if (e.target === el) closeModal(); } },
    h('div.modal', { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div.modal-head', h('h2', title), iconButton('close', 'Закрыть окно', closeModal)),
      h('div.modal-body', body),
      actions ? h('div.modal-actions', actions) : null));
  document.body.appendChild(el);
  S.modal = { el, restore };
  (el.querySelector('input, textarea, select') || el.querySelector('button'))?.focus();
  return el;
}

/* ---------------------------------------------------------------- вход */

function showLogin(message = '') {
  stopStream();
  $('chat-app').hidden = true;
  $('chat-boot').hidden = true;
  $('chat-login').hidden = false;
  const err = $('chat-login-error');
  err.hidden = !message;
  err.textContent = message;
  $('chat-login-token').focus();
  api.loginHint().then((info) => {
    if (info && info.token_file) {
      $('chat-login-hint').textContent = `Токен лежит в файле ${info.token_file}. Откройте его Блокнотом, `
        + 'скопируйте строку и вставьте сюда (Ctrl+V).';
    }
  }).catch(() => { /* остаётся подсказка по умолчанию */ });
}

function wireLogin() {
  $('chat-login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('chat-login-token');
    const err = $('chat-login-error');
    const btn = $('chat-login-submit');
    const value = input.value.trim();
    if (!value) { err.hidden = false; err.textContent = 'Введите токен.'; return; }
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    err.hidden = true;
    try {
      await api.login(value);
      input.value = '';
      $('chat-login').hidden = true;
      await boot();
    } catch (ex) {
      clearCsrf();
      err.hidden = false;
      err.textContent = ex && ex.status === 401
        ? 'Токен не подошёл. Скопируйте его целиком из файла token.'
        : (ex && ex.message) || 'Не удалось войти.';
    } finally {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
    }
  });
}

/* ---------------------------------------------------------------- загрузка */

async function boot() {
  applyTheme(currentTheme());
  if (!hasSession()) { showLogin(); return; }
  try {
    S.options = await api.raw('/api/chat/options');
    S.optionsError = null;
  } catch (err) {
    if (isAuthErr(err)) { showLogin('Сессия недействительна. Войдите заново.'); return; }
    S.options = null;
    S.optionsError = err;
  }
  $('chat-boot').hidden = true;
  $('chat-login').hidden = true;
  $('chat-app').hidden = false;
  const first = !S.booted;
  if (first) buildShell();
  S.booted = true;
  applyOptions();
  loadPicker();
  loadThreads();
  loadProjects();
  loadJeff();
  if (S.pendingSend) {
    const turn = S.pendingSend;
    S.pendingSend = null;
    sendTurn(turn);
  } else if (first) {
    await route();
  } else if (S.threadId) {
    await openThread(S.threadId);
  }
  renderAll();
}

function applyOptions() {
  const o = S.options;
  const banner = $('chat-banner');
  clear(banner);
  banner.hidden = !S.optionsError;
  if (S.optionsError) {
    banner.appendChild(h('div.banner', { role: 'alert' },
      icon('warn', 16),
      h('div', h('b', 'Чат-сервис не ответил: '), S.optionsError.message || 'ошибка',
        S.optionsError.hint ? h('div.banner-hint', S.optionsError.hint) : null),
      h('button.btn.btn-ghost', { type: 'button', 'aria-label': 'Проверить связь с сервером снова', onClick: () => boot() }, 'Проверить снова')));
  }
  if (o && o.limits) composer.setMaxBytes(o.limits.attachment_max_bytes);
  if (o && o.speech) composer.setSpeech(o.speech.status === 'configured', o.speech.reason);
  else {
    api.raw('/api/oss/status').then((st) => {
      const sp = st && st.speech;
      composer.setSpeech(Boolean(sp && sp.status === 'configured'), sp && sp.reason);
    }).catch(() => composer.setSpeech(false, 'Состояние распознавания речи неизвестно'));
  }
  S.pickerModel = buildPickerModel(S.options, S.picker);
  composer.setPicker(S.pickerModel);
  restoreSelection();
}

/* Отдельной ручки /api/models/picker в сервере нет (каждая загрузка окна давала 404 в консоли):
   тарификация, доступность и место работы приходят в /api/chat/options, оттуда список и строится. */
function loadPicker() {
  S.picker = null;
  S.pickerModel = buildPickerModel(S.options, S.picker);
  composer.setPicker(S.pickerModel);
  restoreSelection();
  refreshPlaces();
  renderHeader();
  renderSide();
}

/** Место работы хода, у которого нет плана «до отправки» (тред открыт заново, F5, ход из CMD). */
function fillPlace(turn) {
  if (!turn || turn.locality) return false;
  const place = placeOfAlias(S.pickerModel, turn.model || (turn.run && turn.run.model_alias) || '');
  if (!place) return false;
  turn.locality = place.locality;
  if (place.billing && !turn.billing) turn.billing = place.billing;
  return true;
}

function refreshPlaces() {
  for (const turn of S.turns) if (fillPlace(turn)) scheduleTurnRender(turn);
}

function restoreSelection() {
  const saved = lsGet(LS.route) || 'auto';
  if (S.rave) return;
  if (saved.startsWith('agent:') && S.pickerModel) {
    const id = saved.slice(6);
    const e = S.pickerModel.entries.find((x) => String(x.agentId) === id && x.usable);
    S.selection = e ? { mode: 'agent', agentId: e.agentId } : { mode: 'auto' };
  } else {
    S.selection = { mode: 'auto' };
  }
  composer.setSelection(S.selection, false);
}

async function loadThreads() {
  S.threadsLoading = true;
  const q = [`limit=200`, `archived=${S.filter.archived ? 1 : 0}`];
  if (S.filter.q) q.push(`q=${enc(S.filter.q)}`);
  if (S.filter.project) q.push(`project=${enc(S.filter.project)}`);
  try {
    const res = await api.raw(`/api/chat/threads?${q.join('&')}`);
    S.threads = listOf(res, 'items');
    S.threadsError = '';
  } catch (err) {
    if (isAuthErr(err)) return;
    S.threadsError = `${err.message || 'ошибка'}${err.hint ? ` — ${err.hint}` : ''}`;
  }
  S.threadsLoading = false;
  renderSide();
}

async function loadProjects() {
  try {
    S.projects = listOf(await api.raw('/api/chat/projects'), 'items');
  } catch {
    S.projects = [];
  }
  renderSide();
}

async function loadJeff() {
  try {
    const st = await api.raw('/api/jeff-settings/status');
    const hb = st && st.heartbeat;
    const beats = hb ? [hb.telegram, hb.window].filter((x) => x && typeof x === 'object') : [];
    if (!beats.length) { S.jeff = null; renderHeader(); return; }
    const up = beats.some((b) => b.availability === 'up');
    const stale = beats.some((b) => b.availability === 'stale');
    S.jeff = up ? { tone: 'local', text: 'Jeff online' }
      : stale ? { tone: 'lan', text: 'Jeff: нет пульса' } : { tone: 'blocked', text: 'Jeff остановлен' };
  } catch {
    S.jeff = null;          // нет ответа — значок не показываем (не выдумываем статус)
  }
  renderHeader();
}

async function loadConnectors(force = false) {
  if (!force && S.connectors && Date.now() - S.connectorsAt < 30000) return S.connectors;
  S.connectors = await api.raw('/api/rave/connectors');
  S.connectorsAt = Date.now();
  return S.connectors;
}

/* ---------------------------------------------------------------- маршрут окна */

function parseHash() {
  const raw = (location.hash || '').replace(/^#/, '');
  const m = /(?:^|&)t=([A-Za-z0-9_-]{4,64})(?:&|$)/.exec(raw);
  return m ? { thread: m[1] } : { thread: null };
}

async function route() {
  const { thread } = parseHash();
  if (thread) await openThread(thread);
  else newChat({ keepHash: true });
}

function setHashThread(id) {
  const target = id ? `#t=${enc(id)}` : '';
  S.hashSelf = target;
  if (id) history.replaceState(null, '', `${location.pathname}${location.search}${target}`);
  else history.replaceState(null, '', `${location.pathname}${location.search}`);
}

/* ---------------------------------------------------------------- треды */

function stopStream() {
  if (S.stream) { S.stream.stop(); S.stream = null; }
  if (S.running) { S.running = null; composer && composer.setRunning(false); }
  hideReconnect();
  setStreamState('idle');
}

function newChat({ keepHash = false } = {}) {
  stopStream();
  S.nav += 1;
  if (S.threadId) composer.dropAttachments(ATT_NOTE);
  S.threadId = null;
  S.thread = null;
  S.turns = [];
  S.focusTurn = null;
  if (!keepHash) setHashThread(null);
  renderMessages();
  renderAll();
  scrollToBottom(true);
  composer.focus();
}

function turnFromDetail(t) {
  const atts = Array.isArray(t.attachments) ? t.attachments.map((a) => ({ id: a.id, name: a.name || 'файл', kind: a.kind, size: a.size })) : [];
  const turn = createTurn({ taskId: t.task_id ?? null, text: t.text || '', at: t.at || null, attachments: atts });
  turn.localId = `h${t.idx ?? S.seq++}`;
  turn.threadId = S.threadId;
  turn.truth = t;
  applyTruth(turn, t);
  turn.expanded = new Set();
  return turn;
}

async function openThread(id) {
  /* тот же тред, а сообщение ещё в пути (номера задачи нет): перезагрузка заменила бы
     ходы на экране и оторвала бы отправку от окна — ничего не делаем */
  if (S.threadId !== null && String(S.threadId) === String(id) && S.running && !S.running.taskId) return;
  if (S.threadId !== id) {
    stopStream();
    composer.dropAttachments(ATT_NOTE);
  }
  S.nav += 1;
  S.threadId = id;
  S.thread = S.threads.find((t) => String(t.id) === String(id)) || { id, title: '…' };
  S.turns = [];
  S.focusTurn = null;
  renderMessages({ loading: true });
  renderAll();
  let detail;
  try {
    detail = await api.raw(`/api/chat/threads/${enc(id)}`);
  } catch (err) {
    if (isAuthErr(err)) return;
    if (S.threadId !== id) return;
    renderMessages({ error: err });
    return;
  }
  if (S.threadId !== id) return;
  S.thread = detail;
  S.turns = (Array.isArray(detail.turns) ? detail.turns : []).map(turnFromDetail);
  for (const t of S.turns) fillPlace(t);
  const last = S.turns[S.turns.length - 1] || null;
  S.focusTurn = last;
  renderMessages();
  renderAll();
  scrollToBottom(true);
  if (last && last.taskId) {
    if (!isTerminalStatus(last.status)) {
      /* F5 или возврат к треду посреди хода: повтор истории с начала (after=0) */
      attachStream(last, 0);
    } else {
      loadTurnEvents(last);
    }
    loadBrief(last);
    loadRunSources(last);
  }
}

function upsertThread(summary) {
  if (!summary || !summary.id) return;
  const i = S.threads.findIndex((t) => String(t.id) === String(summary.id));
  if (i >= 0) S.threads[i] = { ...S.threads[i], ...summary };
  else S.threads.unshift(summary);
  S.threads.sort((a, b) => (Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)))
    || String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  if (S.thread && String(S.thread.id) === String(summary.id)) S.thread = { ...S.thread, ...summary };
}

async function patchThread(body, okText) {
  if (!S.threadId) return null;
  try {
    const res = await api.raw(`/api/chat/threads/${enc(S.threadId)}`, { method: 'PATCH', body });
    upsertThread(res);
    if (okText) toast(okText, 'ok');
    renderHeader();
    renderSide();
    loadProjects();
    return res;
  } catch (err) {
    if (!isAuthErr(err)) toast(`Не сохранено: ${err.message}${err.hint ? ` — ${err.hint}` : ''}`, 'error');
    return null;
  }
}

/* ---------------------------------------------------------------- события хода */

async function loadTurnEvents(turn) {
  if (!turn.taskId) return;
  let after = 0;
  try {
    for (let page = 0; page < 5; page += 1) {
      const res = await api.raw(`/api/tasks/${enc(turn.taskId)}/events?after=${after}&limit=2000`);
      for (const ev of (res && res.events) || []) applyEvent(turn, ev);
      after = res ? res.cursor : after;
      if (!res || !res.more) break;
    }
  } catch {
    return;                 // история шагов не загрузилась — итог хода уже показан
  }
  if (turn.truth) applyTruth(turn, turn.truth);
  scheduleTurnRender(turn);
}

function planFromMission(m) {
  const src = Array.isArray(m && m.plan) ? m.plan : Array.isArray(m && m.tasks) ? m.tasks : [];
  return {
    items: src.slice(0, 30).map((x) => {
      const status = typeof x === 'object' && x ? String(x.status || '') : '';
      return {
        title: typeof x === 'object' && x ? String(x.title || x.name || x.prompt || '—').slice(0, 160) : String(x),
        status,
        state: status === 'completed' ? 'done' : status === 'running' ? 'run' : '',
      };
    }),
  };
}

async function loadBrief(turn) {
  if (!turn || !turn.taskId || turn.briefLoading) return;
  turn.briefLoading = true;
  const hasRun = turn.runIds.length > 0 || Boolean(turn.run);
  const [taskR, routeR] = await Promise.allSettled([
    api.raw(`/api/tasks/${enc(turn.taskId)}`),
    hasRun ? api.raw(`/api/router/explain?task_id=${enc(turn.taskId)}`) : Promise.resolve(null),
  ]);
  if (taskR.status === 'fulfilled' && taskR.value) {
    const task = taskR.value.task || {};
    if (!turn.agent && task.agent_id !== null && task.agent_id !== undefined) {
      const a = ((S.options && S.options.agents) || []).find((x) => String(x.id) === String(task.agent_id));
      turn.agent = a ? { id: a.id, name: a.name } : { id: task.agent_id, name: `агент #${task.agent_id}` };
    }
    if (task.mission_id) {
      try { turn.plan = planFromMission(await api.raw(`/api/missions/${enc(task.mission_id)}`)); } catch { /* плана нет — честно */ }
    }
    for (const r of taskR.value.runs || []) if (r && r.id !== undefined && !turn.runIds.includes(r.id)) turn.runIds.push(r.id);
  }
  if (routeR.status === 'fulfilled' && routeR.value) turn.route = routeR.value.route || null;
  turn.briefLoading = false;
  if (turn === S.focusTurn) renderPanelNow();
}

async function loadRunSources(turn) {
  if (!turn) return;
  const ids = [...turn.runIds];
  if (turn.run && turn.run.id !== undefined && !ids.includes(turn.run.id)) ids.push(turn.run.id);
  const found = [];
  for (const id of ids.slice(-3)) {
    try {
      found.push(...memorySourcesFromRunEvents(await api.raw(`/api/runs/${enc(id)}/events?after=0&limit=1000`)));
    } catch { /* нет доступа к журналу прогона — источников памяти не показываем */ }
  }
  const seen = new Set();
  turn.memorySources = found.filter((s) => { const k = s.label; if (seen.has(k)) return false; seen.add(k); return true; });
  if (turn === S.focusTurn) { renderPanelNow(); syncSourcesChip(); }
}

function setStreamState(state) {
  S.streamState = state;
  if (state === 'streaming' || state === 'connecting') startWave();
  else stopWave();
  renderStatus();
  const brand = document.querySelector('.sb-brand .orb');
  setSphereState(brand, state === 'streaming' ? 'streaming' : state === 'connecting' || state === 'reconnecting' ? 'thinking'
    : state === 'error' ? 'error' : 'idle');
}

/* ---------------------------------------------------------------- потеря связи (P7) */

/** Баннер над полем ввода: связь потеряна, но задача идёт на сервере; «Сейчас» — повтор без паузы. */
function showReconnect(info = {}) {
  const box = $('chat-reconnect');
  if (!box) return;
  if (box.hidden) {
    box.hidden = false;
    const msg = $('chat-reconnect-msg');
    msg.textContent = '';
    /* текст — после показа: иначе область role=status могла бы промолчать */
    setTimeout(() => { if (!box.hidden) msg.textContent = 'Связь с сервером потеряна — задача продолжается на сервере.'; }, 60);
  }
  const delay = Number(info.delay_ms);
  S.reconnectUntil = Number.isFinite(delay) && delay > 0 ? Date.now() + delay : 0;
  tickReconnect();
  if (!S.reconnectTimer) S.reconnectTimer = setInterval(tickReconnect, 250);
}

function tickReconnect() {
  const count = $('chat-reconnect-count');
  if (!count) return;
  const left = S.reconnectUntil ? Math.ceil((S.reconnectUntil - Date.now()) / 1000) : 0;
  const text = left > 0 ? `Повтор через ${left} с` : 'Подключаюсь…';
  if (count.textContent !== text) count.textContent = text;
}

function hideReconnect() {
  clearInterval(S.reconnectTimer);
  S.reconnectTimer = 0;
  S.reconnectUntil = 0;
  const box = $('chat-reconnect');
  if (!box || box.hidden) return;
  /* «Сейчас» нажали с клавиатуры: баннер прячется — фокус в поле ввода, а не в <body> */
  if (composer && box.contains(document.activeElement)) composer.focus();
  box.hidden = true;
  $('chat-reconnect-msg').textContent = '';
  $('chat-reconnect-count').textContent = '';
}

/** Сеть вернулась, окно снова видно или нажато «Сейчас»: не ждать конца паузы. */
function retryStreamNow() {
  if (S.stream && S.streamState === 'reconnecting') S.stream.retryNow();
}

/* Место работы по факту, а не догадка до отправки: роутер или запасная модель могли
   увести ход в другое место. Сервер в run.usage места работы НЕ присылает (только
   псевдоним модели), поэтому оно выводится из модели, которой прогон реально
   пользовался, по списку выбора. Ветка ev.locality — задел на будущее поле сервера,
   сейчас она не срабатывает. Неизвестная модель — без значка, а не LOCAL. */
function applyMeasuredRoute(turn, ev) {
  if (ev.locality) {
    turn.locality = ev.locality;
    if (ev.billing) turn.billing = ev.billing;
  } else if (ev.model && turn.sentAlias && ev.model !== turn.sentAlias) {
    const e = S.pickerModel ? S.pickerModel.entries.find((x) => x.alias === ev.model) : null;
    turn.locality = e ? e.locality : null;
    turn.billing = e ? e.billing : null;
  } else if (ev.model && !turn.locality) {
    /* ход без плана «до отправки» (тред открыт заново, F5): место — по псевдониму модели прогона */
    const place = placeOfAlias(S.pickerModel, ev.model);
    if (!place) return;
    turn.locality = place.locality;
    if (place.billing && !turn.billing) turn.billing = place.billing;
  } else {
    return;
  }
  /* карточка Auto («Последний ответ») и значок в шапке — тоже по факту */
  const alias = ev.model || turn.model || null;
  if (S.lastRoute && S.lastRoute.alias === alias && S.lastRoute.locality === turn.locality) return;
  S.lastRoute = { alias, locality: turn.locality };
  renderSide();
  if (!headerBusy()) renderHeader();
}

/* шапку не пересобирать, пока владелец в ней работает: переименование или меню «Ещё» */
function headerBusy() {
  return Boolean(S.moreMenu) || Boolean(document.activeElement && document.activeElement.id === 'chat-title');
}

function attachStream(turn, after = turn.lastSeq) {
  if (S.stream) S.stream.stop();
  S.running = turn;
  composer.setRunning(true);
  setStreamState('connecting');
  const stream = new TaskStream({
    taskId: turn.taskId,
    after,
    onEvent: (ev) => {
      if (!applyEvent(turn, ev)) return;
      if (ev.kind === 'run.answer_delta') S.waveBucket += String(ev.text || '').length;
      if (ev.kind === 'run.usage') applyMeasuredRoute(turn, ev);
      if (ev.kind === 'approval.created') announceApprovalSoon(turn);
      scheduleTurnRender(turn);
    },
    onState: (state, info) => {
      if (S.stream !== stream) return;
      if (state === 'reconnecting') showReconnect(info);
      else if (state !== 'connecting') hideReconnect();
      /* обрыв связи — не провал хода: ход помечается только правдой сервера */
      if (state === 'open') setStreamState('streaming');
      else if (state === 'connecting') setStreamState('connecting');
      else if (state === 'reconnecting') setStreamState('reconnecting');
      else if (state === 'auth') { S.stream = null; showLogin('Сессия истекла. Войдите — ход продолжится с того же места.'); }
      else if (state === 'failed') { S.stream = null; finalizeTurn(turn); }
      else if (state === 'done') { S.stream = null; finalizeTurn(turn); }
    },
  });
  S.stream = stream;
  stream.start();
}

/* ---------------------------------------------------------------- объявления для читалки экрана (P3) */

function announce(text) {
  const el = $('chat-announce');
  if (!el || !text) return;
  clearTimeout(S.announceTimer);
  el.textContent = '';
  /* сначала пусто, потом текст: повтор той же фразы тоже объявляется */
  S.announceTimer = setTimeout(() => { el.textContent = text; }, 60);
}

/** Один раз на ход, при переходе из «идёт» в итог: «Ответ готов», «Остановлено», «Ошибка: …». */
function announceTurnEnd(turn) {
  if (!turn || turn.announced) return;
  const text = turnEndAnnouncement(turn);
  if (!text) return;           // итога ещё нет (правда сервера не пришла) — молчим, а не выдумываем
  turn.announced = true;
  announce(text);
}

/* Подтверждение ждёт владельца — сказать один раз: задача без решения стоит. Пауза нужна,
   чтобы повтор истории (F5, after=0), где за созданием сразу идёт решение, не объявлял
   уже решённое. */
function announceApprovalSoon(turn) {
  clearTimeout(S.approvalTimer);
  S.approvalTimer = setTimeout(() => {
    if (S.running !== turn) return;
    if (!turn.announcedApprovals) turn.announcedApprovals = new Set();
    const fresh = [...turn.approvals.values()].filter((a) => a.status === 'pending' && !turn.announcedApprovals.has(String(a.id)));
    if (!fresh.length) return;
    for (const a of fresh) turn.announcedApprovals.add(String(a.id));
    announce(`Нужно ваше решение: ${fresh.map((a) => a.kind || 'действие').join(', ')}`);
  }, 400);
}

async function finalizeTurn(turn) {
  if (S.running === turn) { S.running = null; composer.setRunning(false); }
  /* пока читается правда сервера, владелец мог уйти в другой чат и начать там ход:
     строку состояния, волну и объявление тогда не трогаем — они уже чужие */
  const nav = S.nav;
  const threadId = turn.threadId || S.threadId;
  let truth = null;
  if (threadId) {
    try {
      const detail = await api.raw(`/api/chat/threads/${enc(threadId)}`);
      truth = (detail.turns || []).find((x) => String(x.task_id) === String(turn.taskId)) || null;
      const { turns: _t, ...summary } = detail;
      upsertThread(summary);
    } catch { /* ниже — запасной путь через задачу */ }
  }
  if (!truth && turn.taskId) {
    try {
      const t = await api.raw(`/api/tasks/${enc(turn.taskId)}`);
      const run = (t.runs || [])[0] || null;
      truth = { status: t.task && t.task.status, result: t.result ?? (run && run.result) ?? null, error: t.error ?? (run && run.error) ?? null, run };
    } catch { /* сервер недоступен — остаётся то, что пришло потоком */ }
  }
  if (truth) { turn.truth = truth; applyTruth(turn, truth); }
  fillPlace(turn);
  if (S.stopNoteTurn === turn) { S.stopNoteTurn = null; composer.setNote(''); }
  const onScreen = S.nav === nav && S.turns.includes(turn);
  /* новый ход в этом же чате уже идёт — строка состояния и волна принадлежат ему */
  if (onScreen && !S.running) {
    setStreamState(turn.status === 'completed' ? 'done' : turn.status === 'stopped' ? 'stopped'
      : isTerminalStatus(turn.status) ? 'error' : 'idle');
  }
  if (onScreen) announceTurnEnd(turn);
  scheduleTurnRender(turn);
  renderSide();
  loadBrief(turn);
  loadRunSources(turn);
}

/* ---------------------------------------------------------------- отправка */

function selectedEntry() {
  if (S.selection.mode !== 'agent' || !S.pickerModel) return null;
  return S.pickerModel.entries.find((e) => String(e.agentId) === String(S.selection.agentId)) || null;
}

async function onSend({ text, attachments }) {
  if (S.running) return;
  if (S.rave) { startRave(text); return; }
  if (!text) { composer.setNote('Напишите, что сделать с вложениями.', 'warn'); return; }
  composer.takeInput();
  composer.setNote('');
  const turn = createTurn({ text, at: new Date().toISOString(), attachments });
  turn.localId = `l${S.seq++}`;
  turn.threadId = S.threadId;
  turn.clientRequestId = clientRequestId();
  turn.expanded = new Set();
  const entry = selectedEntry();
  if (entry) { turn.locality = entry.locality; turn.billing = entry.billing; }
  S.turns.push(turn);
  S.focusTurn = turn;
  if (S.turns.length === 1) renderMessages();
  else appendTurnRows(turn);
  scrollToBottom(true);
  await sendTurn(turn);
}

async function sendTurn(turn) {
  /* Пока идут запросы, владелец может уйти в другой тред. Ход живёт в своём треде на
     сервере, но окно тогда не трогаем: S.threadId, адрес, S.running и поток — уже
     чужие. Сверка после каждого await: номер перехода и что ход ещё на экране. */
  const nav = S.nav;
  const here = () => S.nav === nav && S.turns.includes(turn) && (turn.threadId || null) === (S.threadId || null);
  const release = () => { if (S.running === turn) { S.running = null; composer.setRunning(false); } };
  S.running = turn;
  composer.setRunning(true);
  setStreamState('connecting');
  turn.sentAt = Date.now();
  turn.sendError = null;
  turn.announced = false;      // «Отправить ещё раз» того же хода: его новый итог тоже объявляется
  scheduleTurnRender(turn);
  try {
    if (!turn.threadId) {
      const t = await api.raw('/api/chat/threads', { method: 'POST', body: S.filter.project ? { project: S.filter.project } : {} });
      const stay = here();
      turn.threadId = t.id;
      upsertThread(t);
      if (stay) {
        S.threadId = t.id;
        S.thread = { ...t, turns: [] };
        setHashThread(t.id);
        renderHeader();
      } else {
        renderSide();
      }
    }
    const entry = selectedEntry();
    const res = await api.raw(`/api/chat/threads/${enc(turn.threadId)}/send`, {
      method: 'POST',
      body: {
        text: turn.text,
        agent_id: entry ? entry.agentId : null,
        attachments: (turn.attachments || []).map((a) => a.id).filter(Boolean),
        client_request_id: turn.clientRequestId,
      },
    });
    turn.taskId = res.task ? res.task.id : null;
    turn.admission = res.admission || null;
    turn.agent = res.agent || null;
    turn.modelInfo = res.model || null;
    /* место работы: locality_detail сервера (local / lan / local_proxy / cloud), иначе из
       списка выбора; грубое local|cloud — последним, иначе LAN-модель выглядела бы
       «на этом устройстве» */
    const chosen = res.agent && S.pickerModel
      ? S.pickerModel.entries.find((x) => String(x.agentId) === String(res.agent.id)) : null;
    if (res.model) {
      turn.model = res.model.alias || turn.model;
      turn.sentAlias = res.model.alias || null;
      turn.locality = res.model.locality_detail || (chosen && chosen.locality) || res.model.locality || turn.locality;
      turn.billing = (chosen && chosen.billing) || res.model.billing || turn.billing;
      S.lastRoute = { alias: res.model.alias, locality: turn.locality };
    }
    if (res.turn && res.turn.at) turn.at = res.turn.at;
    if (res.thread) upsertThread(res.thread);
    if (!here()) {
      release();
      renderSide();
      const refused = Boolean(res.admission && res.admission.ok === false);
      toast(refused
        ? `Сообщение в чате, из которого вы перешли, не запущено: ${res.admission.reason || res.admission.code || 'допуск не выдан'}`
        : 'Сообщение ушло в чат, из которого вы перешли: ход идёт там.', refused ? 'warn' : 'info');
      return;
    }
    renderHeader();
    renderSide();
    if (res.admission && res.admission.ok === false) {
      turn.status = 'blocked';
      turn.blockCode = res.admission.code || null;
      turn.blockReason = res.admission.reason || null;
      release();
      setStreamState('error');
      announceTurnEnd(turn);
      scheduleTurnRender(turn);
      loadBrief(turn);
      return;
    }
    if (!turn.taskId) throw new ApiError('Сервер не вернул номер задачи', { status: 500 });
    attachStream(turn, 0);
    loadBrief(turn);
  } catch (err) {
    const stay = here();
    if (isAuthErr(err)) {
      if (stay) { S.pendingSend = turn; showLogin('Сессия истекла. После входа сообщение уйдёт само.'); return; }
      release();
      showLogin('Сессия истекла. Войдите заново.');
      return;
    }
    turn.sendError = { message: err.message || 'ошибка', hint: err.hint || '', code: err.code || '', offline: err.status === 0 };
    release();
    if (!stay) { toast(`Сообщение в другой чат не отправлено: ${turn.sendError.message}`, 'error'); return; }
    setStreamState('error');
    announceTurnEnd(turn);
    scheduleTurnRender(turn);
  }
}

function onResend(turn) {
  if (S.running) { toast('Дождитесь конца текущего хода или остановите его.', 'warn'); return; }
  if (turn.sendError && turn.sendError.offline) {
    /* сервер мог уже принять запрос: тот же client_request_id вернёт ту же задачу, а не создаст вторую */
    sendTurn(turn);
    return;
  }
  onSend({ text: turn.text, attachments: turn.attachments || [] });
}

async function onStop() {
  if (S.running && !S.running.taskId) { composer.setNote('Сообщение ещё отправляется — STOP станет доступен через мгновение.', 'warn'); return; }
  const live = currentRaves().find(raveLive);
  if (!S.running && live) { stopRave(live); return; }
  const turn = S.running;
  if (!turn) return;
  setStreamState('stopping');
  try {
    const res = await api.raw(`/api/chat/threads/${enc(turn.threadId || S.threadId)}/stop`, { method: 'POST', body: {} });
    if (res && (res.task_id === null || (res.ok === false && isTerminalStatus(res.status)))) {
      /* ход уже закончился сам — показываем итог сервера */
      composer.setNote(res.task_id === null ? 'Нечего останавливать: ход уже завершён.' : `Ход уже завершён (${res.status}).`, '');
      if (S.stream) { S.stream.stop(); S.stream = null; }
      finalizeTurn(turn);
      return;
    }
    composer.setNote('STOP отправлен — жду подтверждения от движка.', '');
    S.stopNoteTurn = turn;        // заметка живёт, пока движок не подтвердил: после «Остановлено» она уже неправда
  } catch (err) {
    if (isAuthErr(err)) return;
    if (err.status === 409) { finalizeTurn(turn); return; }
    setStreamState('streaming');
    composer.setNote(`STOP не прошёл: ${err.message}${err.hint ? ` — ${err.hint}` : ''}`, 'error');
  }
}

async function decideApproval(a, approve) {
  const key = String(a.id);
  S.approvalUi.set(key, { busy: true });
  rerenderApprovalTurns();
  try {
    await api.raw(`/api/approvals/${enc(a.id)}`, { method: 'POST', body: { approve, by: 'owner' } });
    a.status = approve ? 'approved' : 'rejected';
    S.approvalUi.set(key, { busy: false });
  } catch (err) {
    if (isAuthErr(err)) return;
    S.approvalUi.set(key, { busy: false, error: `${err.message}${err.hint ? ` — ${err.hint}` : ''}` });
  }
  rerenderApprovalTurns();
}

function rerenderApprovalTurns() {
  for (const t of S.turns) if (t.approvals.size) { t.version += 1; scheduleTurnRender(t); }
}

async function copyText(text, okText = 'Скопировано') {
  try {
    await navigator.clipboard.writeText(text);
    toast(okText, 'ok');
  } catch {
    toast('Буфер обмена недоступен в этом окне.', 'warn');
  }
}

/* Код из ответа: подтверждение — на самой кнопке (там, где фокус и взгляд), тост — только при сбое (P8). */
async function copyCode(btn, text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    toast('Буфер обмена недоступен в этом окне.', 'warn');
    return;
  }
  clearTimeout(btn._copiedTimer);
  btn.textContent = 'Скопировано ✓';
  btn.dataset.copied = '1';
  btn.setAttribute('aria-label', 'Код скопирован');
  btn._copiedTimer = setTimeout(() => {
    btn.textContent = 'Копировать';
    delete btn.dataset.copied;
    btn.setAttribute('aria-label', 'Скопировать код');
  }, 1500);
}

/* ---------------------------------------------------------------- Agentic Rave */

function currentRaves() {
  const key = S.threadId || '';
  if (!S.raves.has(key)) S.raves.set(key, []);
  return S.raves.get(key);
}

async function startRave(text) {
  if (!text) { composer.setNote('Напишите задачу для агентов рейва.', 'warn'); return; }
  let conn;
  try { conn = await loadConnectors(); } catch (err) {
    composer.setNote(`Коннекторы рейва недоступны: ${err.message}`, 'error');
    return;
  }
  const specs = raveAgentSpecs(conn, S.raveAgents);
  const skipped = raveSkipReasons(conn, S.raveAgents);
  if (!specs.length) {
    composer.setNote(`Нет доступных агентов рейва: ${skipped.length ? skipped.join(' ') : 'включите локального агента или войдите в Claude/Codex (кнопка выбора исполнителя).'}`, 'warn');
    return;
  }
  if (skipped.length) toast(`В рейв не попали: ${skipped.join(' ')}`, 'warn');
  composer.takeInput();
  const card = { key: `rv${S.seq++}`, id: null, prompt: text, specs, status: 'starting', data: null, events: [], cursor: 0, error: '', timer: 0 };
  currentRaves().push(card);
  renderMessages();
  scrollToBottom(true);
  try {
    const r = await api.raw('/api/rave', { method: 'POST', body: { prompt: text, agents: specs } });
    card.id = r.id;
    card.data = r;
    card.status = r.status || 'running';
    pollRave(card);
  } catch (err) {
    if (isAuthErr(err)) return;
    card.status = 'error';
    card.error = `${err.message}${err.hint ? ` — ${err.hint}` : ''}`;
  }
  renderRave(card);
}

async function pollRave(card) {
  clearTimeout(card.timer);
  if (!card.id) return;
  try {
    const [view, evs] = await Promise.all([
      api.raw(`/api/rave/${enc(card.id)}`),
      api.raw(`/api/rave/${enc(card.id)}/events?after=${card.cursor}`),
    ]);
    card.data = view;
    card.status = view.status || card.status;
    if (evs && Array.isArray(evs.events)) {
      card.events = [...card.events, ...evs.events].slice(-40);
      card.cursor = Number(evs.cursor) || card.cursor;
    }
    card.error = '';
  } catch (err) {
    if (isAuthErr(err)) return;
    card.error = `нет связи с рейвом: ${err.message}`;
  }
  renderRave(card);
  /* опрос — только пока рейв жив; 'partial' и прочие итоги его заканчивают */
  if (raveLive(card)) card.timer = setTimeout(() => pollRave(card), 2000);
}

async function stopRave(card) {
  try {
    await api.raw(`/api/rave/${enc(card.id)}/stop`, { method: 'POST', body: {} });
    toast('STOP рейва отправлен', 'ok');
  } catch (err) {
    if (!isAuthErr(err)) toast(`STOP рейва не прошёл: ${err.message}`, 'error');
  }
  pollRave(card);
}

function raveRow(card) {
  const agents = (card.data && card.data.agents) || [];
  const live = raveLive(card);
  const blocked = agents.filter((a) => a.status === 'blocked' && a.error);
  return h('div.msg.msg-bot.msg-rave', { dataset: { rave: card.key } },
    h('span.avatar-bot', sphere(30, live ? 'streaming' : card.status === 'error' ? 'error' : 'idle', 'Agentic Rave')),
    h('div.bot-body',
      h('div.rave-card',
        h('div.rave-head', icon('bolt', 16), h('b', 'Agentic Rave'), card.id ? h('code', card.id) : null,
          h('span.badge', { dataset: { tone: live ? 'lan' : card.status === 'done' ? 'local' : 'blocked' }, title: card.status },
            RAVE_LABEL[card.status] || card.status),
          h('span.rave-spacer'),
          live ? h('button.btn.btn-danger', { type: 'button', 'aria-label': 'Остановить рейв', onClick: () => stopRave(card) }, icon('stop', 14), 'STOP') : null,
          h('a.btn.btn-ghost', { href: '/#/rave', target: '_blank', rel: 'noopener noreferrer', 'aria-label': 'Открыть Agentic Rave в Command Center' }, icon('open', 14), 'Command Center')),
        h('div.rave-prompt', card.prompt),
        card.error ? h('div.error-msg', card.error) : null,
        agents.length ? h('ul.rave-agents', agents.map((a) => h('li',
          h('b', a.name), h('span.pnl-dim-inline', `${a.provider || a.connector || '—'} / ${a.model || '—'}`),
          h('span.badge', { dataset: { tone: a.status === 'done' ? 'local' : ['failed', 'blocked', 'interrupted'].includes(a.status) ? 'danger' : 'lan' } }, a.status),
          h('span.pnl-dim-inline', `шаг ${a.step || 0}/${a.steps_total || '?'}`),
          (a.error || a.answer) ? h('div.rave-text', String(a.error || a.answer).slice(0, 400)) : null)))
          : h('div.pnl-dim', card.id ? 'Агенты готовятся…' : `Агенты: ${card.specs.join(', ')}`),
        blocked.length ? h('div.note-card', { dataset: { tone: 'warn' } }, icon('shield', 16),
          h('span', 'Подписочные агенты работают только после вашего разрешения: '),
          h('a', { href: '/#/approvals', target: '_blank', rel: 'noopener noreferrer' }, 'Подтверждения')) : null,
        card.events.length ? h('ol.rave-events', card.events.slice(-8).map((e) => h('li',
          h('code', e.kind || ''), e.agent ? h('b', e.agent) : null, h('span', String(e.message || e.text || e.status || '').slice(0, 160))))) : null)));
}

function renderRave(card) {
  const old = document.querySelector(`[data-rave="${card.key}"]`);
  if (!old) return;
  old.replaceWith(raveRow(card));
  renderStatus();
}

/* ---------------------------------------------------------------- отрисовка сообщений */

function turnCtx(turn) {
  return {
    running: !isTerminalStatus(turn.status) && !turn.sendError,
    expanded: turn.expanded || (turn.expanded = new Set()),
    approvalUi: S.approvalUi,
    onToggle: (key) => {
      if (turn.expanded.has(key)) turn.expanded.delete(key); else turn.expanded.add(key);
      scheduleTurnRender(turn);
    },
    onDecide: decideApproval,
    onResend,
    onCopy: (t) => copyText(answerText(t) || '', 'Ответ скопирован'),
    onFocus: (t) => { focusTurn(t, true); },
  };
}

function focusTurn(turn, openPanel = false) {
  S.focusTurn = turn;
  if (openPanel && !S.panelOpen) setPanel(true);
  if (!turn.briefLoading) loadBrief(turn);
  if (!turn.memorySources) loadRunSources(turn);
  if (turn.taskId && isTerminalStatus(turn.status) && !turn.tools.size && !turn.progress.length) loadTurnEvents(turn);
  renderPanelNow();
  renderStatus();
}

function appendTurnRows(turn) {
  const list = $('chat-messages');
  const empty = list.querySelector('.empty-hero');
  if (empty) empty.remove();
  const user = userRow(turn, 'В');
  const bot = assistantRow(turn);
  S.rows.set(turn.localId, { user, bot });
  list.appendChild(user);
  list.appendChild(bot);
  updateAssistant(bot, turn, turnCtx(turn));
}

function emptyHero() {
  return h('div.empty-hero',
    sphere(64, 'idle', 'Bossman'),
    h('h1', 'Чем заняться?'),
    h('p', 'Напишите задачу — Bossman выберет исполнителя (сначала локальные модели), покажет шаги и попросит разрешения там, где это нужно.'),
    h('p.empty-keys', `${MOD}+N — новый чат · ${MOD}+K — поиск · ${MOD}+. — панель Thinking · / — поле ввода · Esc — STOP`));
}

function renderMessages({ loading = false, error = null } = {}) {
  const list = $('chat-messages');
  clear(list);
  S.rows.clear();
  if (loading) { list.appendChild(h('div.empty-hero.is-loading', sphere(48, 'thinking', 'Загрузка'), h('p', 'Загружаю чат…'))); return; }
  if (error) {
    list.appendChild(h('div.empty-hero',
      h('h1', error.status === 404 ? 'Чат не найден' : 'Чат не загрузился'),
      h('p', `${error.message || ''}${error.hint ? ` — ${error.hint}` : ''}`),
      h('button.btn.btn-primary', { type: 'button', 'aria-label': 'Начать новый чат', onClick: () => newChat() }, icon('plus', 16), 'Начать новый чат')));
    return;
  }
  const raves = currentRaves();
  if (!S.turns.length && !raves.length) { list.appendChild(emptyHero()); return; }
  for (const turn of S.turns) appendTurnRows(turn);
  for (const card of raves) list.appendChild(raveRow(card));
}

function scheduleTurnRender(turn) {
  S.dirty.add(turn);
  if (S.raf) return;
  const run = () => { S.raf = 0; flushRender(); };
  S.raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame(run) : setTimeout(run, 16);
}

function flushRender() {
  for (const turn of S.dirty) {
    const rows = S.rows.get(turn.localId);
    if (rows) updateAssistant(rows.bot, turn, turnCtx(turn));
  }
  const focusDirty = S.dirty.has(S.focusTurn);
  S.dirty.clear();
  if (focusDirty) { renderPanelNow(); syncSourcesChip(); }
  renderStatus();
  scrollToBottom(false);
}

/* Лента прилипает к низу по намерению владельца (scroll.js), а не по порогу в 120 px:
   force — отправка, открытие треда; иначе — держать низ, только если прилипли. */
function scrollToBottom(force) {
  if (!stick) return;
  if (force) stick.toBottom();
  else stick.follow();
}

function syncJump(state) {
  const btn = $('chat-jump');
  if (!btn) return;
  btn.hidden = Boolean(state.stuck);
  if (!state.stuck && state.grew) btn.dataset.new = '1';
  else delete btn.dataset.new;
}

function jumpLatest() {
  if (stick) stick.toBottom({ smooth: true });
}

/* ---------------------------------------------------------------- шапка, панель, строка состояния */

function routeLocality() {
  const e = selectedEntry();
  if (S.rave) return null;
  if (e) return e.locality;
  const t = S.focusTurn;
  return (t && t.locality) || (S.lastRoute && S.lastRoute.locality) || null;
}

function renderHeader() {
  const hdr = $('chat-header');
  if (!hdr) return;
  clear(hdr);
  const has = Boolean(S.threadId);
  const title = (S.thread && S.thread.title) || (has ? '…' : 'Новый чат');
  const input = h('input.hdr-title', {
    id: 'chat-title', value: title, 'aria-label': 'Название чата', maxlength: '120', readOnly: !has,
    onKeydown: (e) => {
      if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
      if (e.key === 'Escape') { e.preventDefault(); e.target.value = title; e.target.readOnly = true; e.target.blur(); }
    },
    onBlur: (e) => {
      e.target.readOnly = true;
      const v = e.target.value.trim();
      if (has && v && v !== title) patchThread({ title: v }, 'Чат переименован');
      else e.target.value = title;
    },
  });
  const badges = [];
  const loc = routeLocality();
  const lb = localityBadge(loc);
  if (S.rave) badges.push(h('span.badge', { dataset: { tone: 'lan' } }, icon('bolt', 12), 'RAVE'));
  else if (lb) badges.push(h('span.badge', { dataset: { tone: lb.tone }, title: lb.title }, lb.text));
  else badges.push(h('span.badge', { dataset: { tone: 'auto' }, title: 'Исполнитель выбирается при отправке' }, 'AUTO'));
  if (S.jeff) badges.push(h('span.badge', { dataset: { tone: S.jeff.tone }, title: 'Состояние Jeff по его пульсу (GET /api/jeff-settings/status)' }, S.jeff.text));
  if (isLoopbackHost(location.hostname)) {
    badges.push(h('span.badge', { dataset: { tone: 'auto' }, title: 'Окно открыто по локальному адресу этого компьютера (127.0.0.1)' }, icon('lock', 12), 'Только этот ПК'));
  }
  if (S.thread && S.thread.project) badges.push(h('span.badge', { dataset: { tone: 'auto' } }, icon('folder', 12), S.thread.project));
  if (S.thread && S.thread.archived) badges.push(h('span.badge', { dataset: { tone: 'blocked' } }, 'в архиве'));
  const pinned = Boolean(S.thread && S.thread.pinned);
  hdr.appendChild(h('div.hdr-left',
    h('div.hdr-title-row', input,
      iconButton('edit', 'Переименовать чат', () => { input.readOnly = false; input.focus(); input.select(); }, { size: 15, disabled: !has, cls: 'hdr-edit' })),
    h('div.hdr-badges', badges)));
  hdr.appendChild(h('div.hdr-actions',
    iconButton('export', 'Экспорт чата в Markdown', exportThread, { disabled: !has, id: 'chat-export' }),
    iconButton('star', pinned ? 'Открепить чат' : 'Закрепить чат', () => patchThread({ pinned: !pinned }, pinned ? 'Чат откреплён' : 'Чат закреплён'),
      { pressed: pinned, disabled: !has, id: 'chat-pin', cls: pinned ? 'is-on' : '' }),
    iconButton('more', 'Ещё действия с чатом', (e) => openMoreMenu(e.currentTarget), { disabled: !has, id: 'chat-more' })));
}

function openMoreMenu(anchor) {
  if (S.modal) return;
  const lastTask = [...S.turns].reverse().find((t) => t.taskId);
  const archived = Boolean(S.thread && S.thread.archived);
  const menu = h('div.popover.more-menu', { role: 'menu', 'aria-label': 'Действия с чатом' },
    h('button.pop-item', { type: 'button', role: 'menuitem', 'aria-label': 'Переименовать чат',
      onClick: () => { close(); const i = $('chat-title'); i.readOnly = false; i.focus(); i.select(); } }, icon('edit', 16), 'Переименовать'),
    h('button.pop-item', { type: 'button', role: 'menuitem', 'aria-label': 'Переместить чат в проект',
      onClick: () => { close(); moveToProject(); } }, icon('folder', 16), 'Переместить в проект'),
    h('button.pop-item', { type: 'button', role: 'menuitem', 'aria-label': archived ? 'Вернуть чат из архива' : 'Архивировать чат', onClick: async () => {
      close();
      const res = await patchThread({ archived: !archived }, archived ? 'Чат возвращён из архива' : 'Чат перемещён в архив');
      if (res && !archived) { loadThreads(); newChat(); }
    } }, icon('archive', 16), archived ? 'Вернуть из архива' : 'Архивировать'),
    h('button.pop-item', { type: 'button', role: 'menuitem', disabled: !lastTask, 'aria-label': 'Открыть последнюю задачу чата в Command Center',
      title: lastTask ? `Задача #${lastTask.taskId}` : 'В этом чате ещё нет задач',
      onClick: () => { close(); if (lastTask) window.open(`/#/tasks?task=${enc(lastTask.taskId)}`, '_blank', 'noopener'); } },
    icon('open', 16), 'Открыть задачу в Command Center'),
    h('button.pop-item', { type: 'button', role: 'menuitem', disabled: !lastTask,
      'aria-label': 'Скачать технические логи выполнения',
      title: 'JSON без текстов чата, аргументов и результатов инструментов',
      onClick: () => { close(); exportTechnicalLog(); } },
    icon('export', 16), 'Скачать технические логи'));
  const host = $('chat-header');
  host.appendChild(menu);
  const a = anchor.getBoundingClientRect();
  const r = host.getBoundingClientRect();
  menu.style.top = `${a.bottom - r.top + 6}px`;
  menu.style.right = `${Math.max(8, r.right - a.right)}px`;
  const onDoc = (e) => { if (!menu.contains(e.target) && e.target !== anchor) close(); };
  const onKey = (e) => {
    const items = [...menu.querySelectorAll('button:not([disabled])')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); anchor.focus(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
  };
  function close() {
    menu.remove();
    document.removeEventListener('mousedown', onDoc);
    menu.removeEventListener('keydown', onKey);
    S.moreMenu = null;
  }
  S.moreMenu = { close };
  document.addEventListener('mousedown', onDoc);
  menu.addEventListener('keydown', onKey);
  menu.querySelector('button').focus();
}

function moveToProject() {
  const current = (S.thread && S.thread.project) || '';
  const listId = 'chat-projects-list';
  const input = h('input.field', { id: 'chat-project-input', value: current, maxlength: '40', list: listId, 'aria-label': 'Название проекта', placeholder: 'например, Bossman Core' });
  const datalist = h('datalist', { id: listId }, S.projects.map((p) => h('option', { value: p.name })));
  const save = async () => {
    const v = input.value.trim();
    if (v.length > 40) { toast('Название проекта — до 40 символов.', 'warn'); return; }
    closeModal();
    await patchThread({ project: v || null }, v ? `Чат перемещён в проект «${v}»` : 'Чат убран из проекта');
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
  openModal('Переместить в проект', [h('p.modal-text', 'Проект — это метка для группировки чатов. Выберите существующий или введите новый.'), input, datalist], [
    h('button.btn.btn-ghost', { type: 'button', 'aria-label': 'Отмена', onClick: closeModal }, 'Отмена'),
    current ? h('button.btn.btn-ghost', { type: 'button', 'aria-label': 'Убрать чат из проекта', onClick: () => { input.value = ''; save(); } }, 'Убрать из проекта') : null,
    h('button.btn.btn-primary', { type: 'button', 'aria-label': 'Сохранить проект чата', onClick: save }, 'Сохранить'),
  ]);
}

function exportThread() {
  if (!S.threadId) return;
  const turns = S.turns.map((t) => ({
    text: t.text, at: t.at, answer: answerText(t), status: t.status, model: t.model, taskId: t.taskId,
    error: t.sendError ? t.sendError.message : (t.status === 'failed' || t.status === 'blocked') ? (t.error || t.blockReason) : null,
  }));
  const md = exportMarkdown(S.thread, turns);
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: exportFileName(S.thread && S.thread.title) });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast('Чат сохранён в Markdown', 'ok');
}

const TECH_LOG_MAX_TURNS = 20;

async function exportTechnicalLog() {
  const allTurns = S.turns.filter((turn) => turn.taskId);
  if (!S.threadId || !allTurns.length) return;
  const threadId = Number(S.threadId) || null;
  const turns = allTurns.slice(-TECH_LOG_MAX_TURNS);
  const button = $('chat-more');
  if (button) button.disabled = true;
  try {
    const collected = [];
    const turnOffset = allTurns.length - turns.length;
    for (const [index, turn] of turns.entries()) collected.push(await collectTechnicalLogForTurn(turn, index + turnOffset, (path) => api.raw(path)));
    const bundle = {
      schema: 'bossman.technical-log.v1',
      created_at: new Date().toISOString(),
      thread_id: threadId,
      redaction: {
        policy: 'technical-field-allowlist',
        limitations: ['Identifier syntax and known credential patterns only; not a universal secret detector.'],
        user_content_included: false,
        excluded_categories: ['chat prompts and answers', 'tool arguments and results', 'raw error messages', 'credentials'],
        omitted_field_count: collected.reduce((sum, x) => sum + x.omittedFields, 0),
      },
      coverage: {
        task_count: allTurns.length,
        exported_task_count: turns.length,
        omitted_turn_count: allTurns.length - turns.length,
        task_event_count: collected.reduce((sum, x) => sum + x.record.task_events.length, 0),
        run_event_count: collected.reduce((sum, x) => sum + x.record.run_events.length, 0),
        partial: allTurns.length > turns.length || collected.some((x) => x.failures.length > 0 || x.record.truncated),
        truncated_turns: collected.filter((x) => x.record.truncated).map((x) => x.record.turn),
        failures: collected.flatMap((x) => x.failures.map((failure) => ({ task_id: x.record.task_id, ...failure }))),
      },
      turns: collected.map((x) => x.record),
    };
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: technicalLogFileName() });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast(bundle.coverage.partial ? 'Техлоги скачаны частично — причины указаны в JSON' : 'Технические логи скачаны',
      bundle.coverage.partial ? 'warn' : 'ok');
  } catch {
    toast('Не удалось собрать технические логи. Повторите попытку позже.', 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function setPanel(open) {
  S.panelOpen = Boolean(open);
  lsSet(LS.panel, S.panelOpen ? '1' : '0');
  $('chat-app').classList.toggle('panel-open', S.panelOpen);
  $('chat-panel').hidden = !S.panelOpen;
  composer.setPanelOpen(S.panelOpen);
  if (S.panelOpen) {
    renderPanelNow();
    if (S.focusTurn && !S.focusTurn.memorySources) loadRunSources(S.focusTurn);
  }
}

function renderPanelNow() {
  if (!S.panelOpen) return;
  const turn = S.focusTurn;
  renderPanel($('chat-panel'), {
    turn,
    /* модель и место работы — те же, что в подвале ответа: модель прогона и уточнённое место
       (locality_detail, затем факт прогона), а не грубое local|cloud из ответа отправки */
    brief: turn ? { agent: turn.agent,
      model: turn.modelInfo ? { ...turn.modelInfo, alias: turn.model || turn.modelInfo.alias,
        locality: turn.locality ?? null, billing: turn.billing ?? null } : null,
      admission: turn.admission, route: turn.route } : null,
    plan: turn ? turn.plan || null : null,
    memorySources: turn ? turn.memorySources || [] : [],
    collapsed: S.panelCollapsed,
    onToggle: (id) => { if (S.panelCollapsed.has(id)) S.panelCollapsed.delete(id); else S.panelCollapsed.add(id); renderPanelNow(); },
    onClose: () => { setPanel(false); composer.focus(); },
  });
}

function syncSourcesChip() {
  const t = S.focusTurn;
  composer.setSourcesCount(t ? sourcesCount({ turn: t, memorySources: t.memorySources || [] }) : 0);
}

const STATE_LABEL = {
  idle: 'Готово', connecting: 'Подключение…', streaming: 'Streaming', reconnecting: 'Переподключение…',
  stopping: 'Останавливаю…', done: 'Готово', stopped: 'Остановлено', error: 'Ошибка',
};

function renderStatus() {
  const bar = $('chat-statusbar');
  if (!bar || !composer) return;
  const t = S.running || S.focusTurn;
  const usage = t ? t.usage : null;
  const clientTtft = t && t.firstDeltaAt && t.sentAt ? t.firstDeltaAt - t.sentAt : null;
  const lat = latencyLabel(usage, clientTtft);
  const entry = selectedEntry();
  const route = { locality: (t && t.locality) || (entry && entry.locality) || null, billing: (t && t.billing) || (entry && entry.billing) || null };
  const ctxWindow = (entry && entry.contextWindow) || (t && t.modelInfo && t.modelInfo.context_window) || null;
  composer.setMeter(contextMeter(usage, ctxWindow));
  clear(bar);
  const state = S.streamState;
  bar.appendChild(h('span.sb-state', { dataset: { state } }, h('span.state-dot', { 'aria-hidden': 'true' }), STATE_LABEL[state] || state));
  bar.appendChild(h('span.sb-sep', { 'aria-hidden': 'true' }));
  bar.appendChild(h('span', { title: 'Скорость генерации, измеренная сервером (gen_tps)' }, tpsLabel(usage)));
  bar.appendChild(h('span.sb-sep', { 'aria-hidden': 'true' }));
  bar.appendChild(h('span', { title: lat.source === 'client' ? 'Замер окна: от отправки до первого токена (включает очередь)' : 'Время до первого токена по данным сервера' }, lat.text));
  bar.appendChild(h('span.sb-sep', { 'aria-hidden': 'true' }));
  const cost = costLabel(t ? usage : null, route);
  bar.appendChild(h('span.sb-cost', { dataset: { local: cost.includes('local') ? '1' : '0' } }, cost));
  bar.appendChild(h('span.sb-grow'));
  const wave = h('span.wave', { 'aria-hidden': 'true', hidden: !(state === 'streaming' || state === 'connecting') || prefersReducedMotion() });
  const max = Math.max(1, ...S.wave);
  for (const v of S.wave) wave.appendChild(h('i', { style: { height: `${Math.max(12, Math.round((v / max) * 100))}%` } }));
  bar.appendChild(wave);
}

function startWave() {
  if (S.waveTimer) return;
  S.waveTimer = setInterval(() => {
    S.wave.push(S.waveBucket);
    S.wave = S.wave.slice(-WAVE_BARS);
    S.waveBucket = 0;
    renderStatus();
  }, 200);
}

function stopWave() {
  clearInterval(S.waveTimer);
  S.waveTimer = 0;
  S.wave = new Array(WAVE_BARS).fill(0);
}

function renderSide() {
  const root = $('chat-sidebar');
  if (!root) return;
  const active = document.activeElement && document.activeElement.id === 'chat-search';
  const caret = active ? document.activeElement.selectionStart : null;
  const entry = selectedEntry();
  renderSidebar(root, {
    threads: S.threads,
    activeId: S.threadId,
    loading: S.threadsLoading,
    error: S.threadsError,
    filter: S.filter,
    projects: S.projects,
    collapsed: S.collapsedGroups,
    collapsedBar: S.sidebarCollapsed,
    theme: currentTheme(),
    brandState: S.streamState === 'streaming' ? 'streaming' : 'idle',
    route: routeCard(S.rave ? { mode: 'rave' } : entry ? { mode: 'agent', locality: entry.locality, alias: entry.alias, provider: entry.provider }
      : { mode: 'auto', last: S.lastRoute }),
    handlers: {
      onNew: () => newChat(),
      onSearch: (q) => {
        S.filter.q = q.trim();
        clearTimeout(S.searchTimer);
        S.searchTimer = setTimeout(loadThreads, 250);
      },
      onOpen: (id) => { setHashThread(id); openThread(id); },
      onToggleGroup: (id) => {
        if (S.collapsedGroups.has(id)) S.collapsedGroups.delete(id); else S.collapsedGroups.add(id);
        lsSet(LS.groups, [...S.collapsedGroups].join(','));
        renderSide();
      },
      onProject: (name) => { S.filter.project = name; S.filter.archived = false; loadThreads(); },
      onArchive: (on) => { S.filter.archived = Boolean(on); S.filter.project = null; loadThreads(); },
      onCollapse: () => {
        S.sidebarCollapsed = !S.sidebarCollapsed;
        lsSet(LS.sidebar, S.sidebarCollapsed ? '1' : '0');
        $('chat-app').classList.toggle('sb-collapsed', S.sidebarCollapsed);
        renderSide();
      },
      onTheme: toggleTheme,
      onHelp: showHelp,
    },
  });
  if (active) {
    const input = $('chat-search');
    if (input) { input.focus(); try { input.setSelectionRange(caret, caret); } catch { /* type=search */ } }
  }
}

function renderAll() {
  renderHeader();
  renderSide();
  renderPanelNow();
  syncSourcesChip();
  renderStatus();
}

function showHelp() {
  const rows = [
    [`${MOD}+N`, 'Новый чат'], [`${MOD}+K`, 'Поиск чатов'], [`${MOD}+.`, 'Панель Thinking & Actions'],
    ['/', 'Перейти в поле ввода'], ['Enter', 'Отправить'], ['Shift+Enter', 'Новая строка'],
    ['End / Ctrl+End', 'К последнему сообщению (вне поля ввода)'],
    ['Esc', 'Закрыть меню или остановить ход (STOP); в названии чата и в поиске — только отмена'],
  ];
  openModal('Горячие клавиши', [
    h('table.keys', h('tbody', rows.map(([k, v]) => h('tr', h('td', h('kbd.kbd', k)), h('td', v))))),
    h('p.modal-text', 'Чат — ещё одна поверхность того же Bossman: те же задачи, память, подтверждения и STOP, что в Command Center и в `bossman chat`.'),
  ], [h('button.btn.btn-primary', { type: 'button', 'aria-label': 'Закрыть справку', onClick: closeModal }, 'Понятно')]);
}

/* ---------------------------------------------------------------- поповеры композитора */

function kv(label, value) { return h('div.pop-kv', h('span', label), h('b', value)); }

async function popoverContent(kind) {
  if (kind === 'memory') {
    const cfgR = await Promise.allSettled([api.raw('/api/memory/config')]).then((r) => r[0]);
    const cfg = cfgR.status === 'fulfilled' ? cfgR.value : null;
    // статистику просим только у подключённого хранилища: у неподключённого сервер честно отвечает 503,
    // и это был лишний запрос с ошибкой в консоли при каждом открытии окна «Память»
    const statsR = cfg && cfg.configured
      ? await Promise.allSettled([api.raw('/api/memory/stats')]).then((r) => r[0]) : null;
    const recall = S.options && S.options.memory ? S.options.memory.recall_enabled : null;
    const out = [h('h3.pop-title', icon('memory', 16), 'Память Bossman')];
    if (!cfg) out.push(h('p.pop-dim', `Настройки памяти не получены: ${cfgR.reason ? cfgR.reason.message : 'ошибка'}`));
    else if (!cfg.configured) {
      out.push(h('p.pop-dim', 'Хранилище памяти не подключено: Bossman отвечает без долгой памяти.'),
        h('a.pop-link', { href: '/#/settings', target: '_blank', rel: 'noopener noreferrer' }, 'Подключить в Command Center → Настройки'));
    } else {
      out.push(kv('Хранилище', cfg.root || '—'), kv('Движок', cfg.backend_class || cfg.backend || '—'));
      if (!statsR) { /* статистика не запрашивалась */ } else if (statsR.status === 'fulfilled' && statsR.value && statsR.value.stats && typeof statsR.value.stats === 'object') {
        for (const [k, v] of Object.entries(statsR.value.stats).slice(0, 6)) {
          if (v === null || typeof v === 'object') continue;
          out.push(kv(k, String(v)));
        }
      } else if (statsR.status === 'rejected') {
        out.push(h('p.pop-dim', `Статистика недоступна: ${statsR.reason.message}${statsR.reason.hint ? ` — ${statsR.reason.hint}` : ''}`));
      }
    }
    out.push(kv('Припоминание в начале хода', recall === true ? 'включено' : recall === false ? 'выключено (BCC_MEMORY_RECALL=0)' : 'неизвестно'));
    return h('div', out);
  }
  if (kind === 'tools') {
    const out = [h('h3.pop-title', icon('tools', 16), 'Инструменты')];
    const entry = selectedEntry();
    const [agentsR, capsR] = await Promise.allSettled([api.agents(), api.raw('/api/capabilities')]);
    if (entry) {
      const agent = agentsR.status === 'fulfilled' ? listOf(agentsR.value).find((a) => String(a.id) === String(entry.agentId)) : null;
      const tools = agent && Array.isArray(agent.tools) ? agent.tools : [];
      out.push(h('p.pop-dim', `Агент «${entry.name}»: ${tools.length ? `${tools.length} инструм.` : 'без инструментов'}`));
      if (tools.length) out.push(h('div.pop-chips', tools.slice(0, 40).map((t) => h('code', String(t)))));
    } else {
      out.push(h('p.pop-dim', 'Auto выбирает агента при отправке — набор инструментов зависит от выбранного агента.'));
    }
    if (capsR.status === 'fulfilled') {
      const caps = Array.isArray(capsR.value && capsR.value.capabilities) ? capsR.value.capabilities : [];
      const granted = caps.filter((c) => c.grant && c.grant.granted);
      out.push(kv('Способности процесса', `${granted.length} из ${caps.length} доступны`));
      out.push(h('ul.pop-caps', caps.slice(0, 24).map((c) => h('li', { dataset: { ok: c.grant && c.grant.granted ? '1' : '0' }, title: (c.grant && c.grant.reason) || c.description || '' },
        icon(c.grant && c.grant.granted ? 'check' : 'close', 13), h('code', c.tool || c.capability_id || '—'),
        c.approval_requirement === 'ask' ? h('span.pop-dim-inline', 'спросит разрешение') : null))));
    } else {
      out.push(h('p.pop-dim', `Манифест способностей недоступен: ${capsR.reason.message}`));
    }
    return h('div', out);
  }
  if (kind === 'rave') {
    const conn = await loadConnectors(true);
    const out = [h('h3.pop-title', icon('bolt', 16), 'Агенты Agentic Rave')];
    const row = (key, label, state, enabled, note) => h('label.pop-check', { title: note || '' },
      h('input', { type: 'checkbox', checked: Boolean(S.raveAgents[key]) && enabled, disabled: !enabled,
        onChange: (e) => { S.raveAgents[key] = e.target.checked; } }),
      h('span', label), h('span.pop-dim-inline', state));
    const local = conn && conn.local;
    out.push(row('local', 'Локальный агент', local ? `${local.default_model || 'модель по умолчанию'} · ${local.endpoint || ''}` : 'нет данных', Boolean(local)));
    for (const name of ['claude', 'codex']) {
      const c = conn && conn[name];
      const ss = subscriptionState(c ? { available: c.installed !== false, logged_in: c.logged_in,
        version_ok: c.version_ok, version_problem: c.version_problem } : null);
      out.push(row(name, name === 'claude' ? 'Claude · подписка' : 'Codex · подписка', ss.text, ss.ok,
        c && (c.version_ok === false && c.version_problem ? c.version_problem : c.reason)));
    }
    out.push(h('p.pop-dim', 'Подписки работают только в рейве и только после вашего разрешения. Bossman не выполняет вход в Claude/Codex сам.'),
      h('a.pop-link', { href: '/#/rave', target: '_blank', rel: 'noopener noreferrer' }, 'Agentic Rave в Command Center'));
    if (S.rave) {
      out.push(h('div.pop-actions', h('button.btn.btn-ghost', { type: 'button', 'aria-label': 'Выйти из режима Agentic Rave',
        onClick: () => { composer.closeMenu(); onToggleRave(); } }, 'Обычный чат')));
    }
    return h('div', out);
  }
  return h('div', 'Нет данных');
}

/* ---------------------------------------------------------------- выбор исполнителя */

function onSelect(sel) {
  if (sel.mode === 'rave') {
    S.rave = true;
    S.raveAgents = { ...S.raveAgents, local: true, [sel.sub]: true };
    composer.setSelection({ mode: 'rave', subs: Object.keys(S.raveAgents).filter((k) => S.raveAgents[k]) }, true);
    toast('Режим Agentic Rave: подписка работает только в рейве.', 'info');
  } else {
    S.rave = false;
    S.selection = sel;
    lsSet(LS.route, sel.mode === 'agent' ? `agent:${sel.agentId}` : 'auto');
    composer.setSelection(sel, false);
  }
  renderHeader();
  renderSide();
  renderStatus();
}

function onToggleRave() {
  S.rave = !S.rave;
  composer.setSelection(S.rave ? { mode: 'rave', subs: Object.keys(S.raveAgents).filter((k) => S.raveAgents[k]) } : S.selection, S.rave);
  renderHeader();
  renderSide();
}

/* ---------------------------------------------------------------- каркас */

function buildShell() {
  composer = createComposer({
    onSend,
    onStop,
    onSelect,
    onToggleRave,
    onTogglePanel: () => setPanel(!S.panelOpen),
    onShowSources: () => {
      S.panelCollapsed.delete('sources');
      setPanel(true);
      const sec = document.querySelector('#chat-panel [data-sec="sources"]');
      if (sec) sec.scrollIntoView({ block: 'nearest' });
    },
    onAuth: () => showLogin('Сессия истекла. Войдите заново.'),
    threadId: () => S.threadId,
    popoverContent,
    raveMode: () => S.rave,
  });
  $('chat-composer-slot').appendChild(composer.root);
  $('chat-app').classList.toggle('sb-collapsed', S.sidebarCollapsed);
  $('chat-app').classList.toggle('panel-open', S.panelOpen);
  $('chat-panel').hidden = !S.panelOpen;
  composer.setPanelOpen(S.panelOpen);

  /* копирование блоков кода из ответа */
  $('chat-messages').addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-copy="code"]');
    if (!btn) return;
    const pre = btn.closest('.md-code') && btn.closest('.md-code').querySelector('pre');
    if (pre) copyCode(btn, pre.textContent || '');
  });

  /* лента: прилипание к низу и кнопка «К последнему сообщению (End)» */
  stick = createStick($('chat-scroll'), $('chat-messages'), { onChange: syncJump, reducedMotion: prefersReducedMotion });
  const jump = $('chat-jump');
  jump.addEventListener('mousedown', (e) => e.preventDefault());      // щелчок не уводит фокус из поля ввода
  jump.addEventListener('click', () => {
    const fromKeyboard = document.activeElement === jump;
    jumpLatest();
    if (fromKeyboard) composer.focus();                                // кнопка прячется — фокус не теряется
  });

  /* потеря связи: «Сейчас», возврат сети и возврат к окну — повтор без паузы */
  $('chat-reconnect-now').addEventListener('click', retryStreamNow);
  window.addEventListener('online', retryStreamNow);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') retryStreamNow(); });

  /* перетаскивание файлов в окно */
  const main = $('chat-main');
  let depth = 0;
  main.addEventListener('dragenter', (e) => { if (hasFiles(e)) { depth += 1; main.classList.add('is-drop'); } });
  main.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
  main.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) main.classList.remove('is-drop'); });
  main.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    main.classList.remove('is-drop');
    composer.addFiles([...e.dataTransfer.files]);
  });

  window.addEventListener('hashchange', () => {
    const target = location.hash || '';
    if (target === S.hashSelf) return;
    /* чужой якорь (#chat-input и т.п.) — не переход между чатами: открытый тред и живой
       поток не трогаем, в адрес возвращаем текущий тред (иначе F5 открыл бы пустой чат) */
    if (target.length > 1 && !parseHash().thread) { setHashThread(S.threadId); return; }
    S.hashSelf = target;
    route();
  });
  document.addEventListener('keydown', onGlobalKey);
}

function hasFiles(e) {
  return Boolean(e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files'));
}

function isTyping(target) {
  if (!target) return false;
  const tag = (target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

function onGlobalKey(e) {
  if ($('chat-app').hidden) return;
  const mod = e.ctrlKey || e.metaKey;
  const key = (e.key || '').toLowerCase();
  if (mod && !e.shiftKey && !e.altKey && (key === 'n' || e.code === 'KeyN')) { e.preventDefault(); newChat(); return; }
  if (mod && !e.shiftKey && !e.altKey && (key === 'k' || e.code === 'KeyK')) {
    e.preventDefault();
    if (S.sidebarCollapsed) { S.sidebarCollapsed = false; lsSet(LS.sidebar, '0'); $('chat-app').classList.toggle('sb-collapsed', false); renderSide(); }
    const s = $('chat-search');
    if (s) { s.focus(); s.select(); }
    return;
  }
  if (mod && (key === '.' || e.code === 'Period')) { e.preventDefault(); setPanel(!S.panelOpen); return; }
  if (e.key === 'Escape') {
    /* Esc отменяет набор в IME (китайский, японский…) — это не STOP */
    if (e.isComposing || e.keyCode === 229) return;
    /* Esc уже обработан полем (отмена переименования) — это не STOP */
    if (e.defaultPrevented) return;
    if (S.modal) { e.preventDefault(); closeModal(); return; }
    if (S.moreMenu) { e.preventDefault(); S.moreMenu.close(); return; }
    if (composer.menuOpen()) { e.preventDefault(); composer.closeMenu(); return; }
    /* STOP — из поля сообщения или вне полей ввода; Esc в поиске чатов и других полях его не шлёт */
    if (S.running && (e.target === composer.textarea || !isTyping(e.target))) { e.preventDefault(); onStop(); return; }
    return;
  }
  /* End / Ctrl+End вне полей ввода и открытых меню — к последнему сообщению */
  if (e.key === 'End' && !e.shiftKey && !e.altKey && !isTyping(e.target) && !S.modal && !S.moreMenu && !composer.menuOpen()) {
    e.preventDefault();
    jumpLatest();
    return;
  }
  if (e.key === '/' && !mod && !isTyping(e.target) && !S.modal) { e.preventDefault(); composer.focus(); }
}

/* ---------------------------------------------------------------- старт */

function start() {
  applyTheme(currentTheme());
  wireLogin();
  /* «Перейти к полю ввода»: только фокус, адрес не меняется — смена якоря не должна
     трогать открытый тред */
  const skip = document.querySelector('.skip-link');
  if (skip) {
    skip.addEventListener('click', (e) => {
      e.preventDefault();
      if (composer && !$('chat-app').hidden) composer.focus();
      else if (!$('chat-login').hidden) $('chat-login-token').focus();
    });
  }
  window.addEventListener(UNAUTHORIZED_EVENT, () => {
    if (!$('chat-login').hidden) return;
    showLogin('Сессия истекла. Войдите заново — чат продолжится с того же места.');
  });
  boot().catch((err) => {
    $('chat-boot').hidden = false;
    $('chat-boot').textContent = `Чат не запустился: ${(err && err.message) || err}`;
  });
}

start();
