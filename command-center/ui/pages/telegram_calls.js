/* ============================================================
   telegram_calls.js — «Telegram-звонки»: голосовой разговор ассистента с вашим ВТОРЫМ аккаунтом.

   Один экран, четыре раздела: Подключение · Тестовый собеседник · Звонок · Проверки.
   API (bcc/features/telegram_calls.py, под /api с обычной auth/CSRF): /api/telegram/calls/*.

   Правила, которые держит этот экран:
   * звонки выключены, пока владелец не включит их сам; собеседник ровно один и подтверждается отдельно;
   * у «Позвонить» нет выбора «кому»: звонок идёт на сохранённого собеседника;
   * STOP никогда не молчит и всегда доступен; «Продолжить» — только владельцем;
   * секреты (api_hash, код, пароль 2FA) уходят на сервер один раз и не показываются: только маски;
   * загрузка и опрос — без console.error и без 4xx на пустом состоянии; опрос живёт, пока страница в документе;
   * «ТЕСТ БЕЗ TELEGRAM» — красная плашка для тестового контура: он проверяет наш тракт, а не настоящий звонок.

   Модуль безопасен для импорта в Node: document трогается только внутри render().
   ============================================================ */

import { api } from '../api.js';
import { h, field, input, badge, toggle, toastOk, toastError } from '../components.js';
import { panel, pageHead } from './_ui.js';

export const BASE = '/api/telegram/calls'; // the ONE canonical path (the former /api/calls alias was removed from the backend)
export const EVENT_KINDS = ['telegram_call.state', 'telegram_call.ended']; // the ONE pair of bus events (bcc/features/telegram_calls.py)
export const POLL_MS = 1500;

/* ---------------------------------------------------------------- чистые функции (проверяются в Node) */

export const STATE_WORDS = {
  idle: 'Нет звонка', dialing: 'Набираем…', ringing: 'Звонит…', active: 'Идёт разговор',
  ending: 'Завершаем…', ended: 'Нет звонка',
};
export const PHASE_WORDS = { listening: 'слушает', thinking: 'думает', speaking: 'говорит' };
export const OUTCOME_WORDS = {
  completed: 'разговор завершён', declined: 'собеседник отклонил', busy: 'собеседник занят', no_answer: 'не ответил',
  connection_lost: 'связь потеряна', stopped: 'остановлен (STOP)', max_duration: 'достигнут лимит времени',
  silence_timeout: 'долгая тишина', failed: 'звонок не удался', unknown: 'исход неизвестен',
};
export const VERDICT_TONE = { PASS: 'ok', WARN: 'warn', BLOCKED: 'err', FAIL: 'err' };

/** Шаг подключения словами. Сырые статусы наружу не выходят. */
export function accountStep(st) {
  const a = (st && st.account) || {};
  const table = {
    no_credentials: { key: 'api', n: 1, text: 'Шаг 1 из 4. Введите api_id и api_hash с my.telegram.org.' },
    logged_out: { key: 'phone', n: 2, text: 'Шаг 2 из 4. Введите номер телефона основного аккаунта.' },
    code_sent: { key: 'code', n: 3, text: 'Шаг 3 из 4. Введите код, который пришёл в приложение Telegram.' },
    password_needed: { key: 'password', n: 4, text: 'Шаг 4 из 4. Введите пароль двухэтапной защиты.' },
    ready: { key: 'ready', n: 5, text: 'Аккаунт подключён.' },
    error: { key: 'api', n: 1, text: 'Сохранённые ключи не читаются. Введите api_id и api_hash заново.' },
  };
  return table[a.state] || table.no_credentials;
}

export function callWords(st) {
  const call = st && st.call;
  if (!call) return { state: STATE_WORDS.idle, phase: '', live: false };
  const state = STATE_WORDS[call.state] || 'Идёт звонок';
  const phase = call.state === 'active' && call.phase ? PHASE_WORDS[call.phase] || '' : '';
  return { state, phase, live: true };
}

export function fmtMs(v) {
  return typeof v === 'number' && Number.isFinite(v) ? `${Math.round(v)} мс` : '—';
}

export function isTestMode(st) {
  return !!(st && (st.mode === 'offline_test' || st.transport === 'loopback' || st.test_label));
}

/** Автоответчик словами (входящие звонки). Не обещает ничего сверх того, что известно: настоящий приём ещё не проверен в живую. */
export function answeringWords(st) {
  const a = st && st.answering;
  if (!a || !a.enabled) return { on: false, tone: 'dim', text: 'Автоответчик выключен (так задано по умолчанию): входящие звонят только вам.' };
  if (!a.armed) {
    return { on: true, tone: 'warn', text: 'Включён в настройках, но не запущен: входящие не принимаются. Подключите аккаунт и повторите включение.' };
  }
  const ready = { ready: 'готов', loading: 'модели загружаются — входящий будет пропущен', failed: 'модели не загрузились — входящий будет пропущен' }[a.ready_state] || 'готовится';
  let text = `Слушает, ${ready}. Если вы не возьмёте трубку за ${a.ring_delay_s} с, ответит Джефф (представится ассистентом).`;
  if (a.in_call) text += ' Сейчас отвечает на звонок.';
  else if (a.ringing) text += ' Идёт звонок: ждёт, ответите ли вы сами.';
  if (a.stopped) text += ' ОСТАНОВЛЕН (STOP): не отвечает, пока вы не нажмёте «Продолжить».';
  if (a.pending_reports) text += ` Отчётов, ещё не отправленных вам в Telegram: ${a.pending_reports}.`;
  if (!a.live_tested) text += ' Приём настоящих входящих ещё не проверен в живую.';
  return { on: true, tone: a.stopped || a.ready_state !== 'ready' ? 'warn' : 'ok', text };
}

/** Что можно нажать сейчас и ПОЧЕМУ нельзя остальное (title у каждой недоступной кнопки). */
export function controlState(st, { confirmUnknown = false } = {}) {
  const stopTitle = 'Остановить звонок и заблокировать звонки, пока вы не нажмёте «Продолжить»';
  if (!st) {
    const wait = 'Состояние ещё загружается';
    return { dial: { disabled: true, title: wait }, hangup: { disabled: true, title: wait },
      stop: { disabled: false, title: stopTitle }, resume: { disabled: true, title: wait } };
  }
  const account = (st.account && st.account.state) || 'no_credentials';
  const stopped = st.stop || {};
  let dialWhy = '';
  if (st.call_active || st.call) dialWhy = 'Звонок уже идёт';
  else if (account !== 'ready') dialWhy = 'Сначала подключите аккаунт Telegram (раздел «Подключение»)';
  else if (!st.peer) dialWhy = 'Сначала выберите тестового собеседника (раздел «Тестовый собеседник»)';
  else if (!st.enabled) dialWhy = 'Звонки выключены: включите переключатель «Разрешить звонки»';
  else if (stopped.active) {
    dialWhy = stopped.call ? 'Действует STOP звонков: нажмите «Продолжить»'
      : 'Действует общий STOP Bossman: снимите его в управлении компьютером';
  } else if (st.deps && st.deps.ready === false && !isTestMode(st)) {
    dialWhy = 'Не установлены зависимости звонков: нажмите «Установить зависимости» в разделе «Проверки»';
  } else if (st.uncertain_previous && !confirmUnknown) {
    dialWhy = 'Прошлый звонок закончился неизвестно: отметьте подтверждение рядом с кнопкой';
  }
  const hasCall = !!(st.call_active || st.call);
  return {
    dial: { disabled: !!dialWhy, title: dialWhy || 'Позвонить на выбранный второй аккаунт' },
    hangup: { disabled: !hasCall, title: hasCall ? 'Завершить разговор обычным образом' : 'Сейчас нет звонка' },
    stop: { disabled: false, title: stopTitle },
    resume: stopped.call
      ? { disabled: false, title: 'Снять STOP звонков (общий STOP Bossman снимается отдельно)' }
      : { disabled: true, title: 'STOP звонков не активен' },
  };
}

/** Можно ли выбирать собеседника: аккаунт подключён и владелец подтвердил «это мой второй аккаунт». */
export function peerPickState(st, confirmed) {
  const account = (st && st.account && st.account.state) || 'no_credentials';
  if (account !== 'ready') return { disabled: true, title: 'Сначала подключите аккаунт Telegram' };
  if (!confirmed) return { disabled: true, title: 'Отметьте «Это мой второй аккаунт»' };
  return { disabled: false, title: 'Сделать этот аккаунт тестовым собеседником' };
}

export function errorText(err) {
  if (!err) return null;
  return { message: String(err.message || 'Ошибка'), hint: String(err.hint || '') };
}

export function summarizeHistoryItem(item) {
  const lat = (item && item.latency_ms) || {};
  const post = (item && item.postcall) || {};
  const memory = post.memory || {};
  const drafts = post.drafts || {};
  const proposals = ((item && item.summary && item.summary.agreed_tasks) || []).length;
  const saved = memory.status === 'written' || memory.status === 'exists';
  return {
    outcome: OUTCOME_WORDS[item && item.outcome] || (item && item.outcome) || 'нет данных',
    turns: Array.isArray(item && item.turns) ? item.turns.length : 0,
    p50: lat.p50,
    test: !!(item && item.transport && item.transport !== 'telegram'),
    text: (item && item.summary && item.summary.text) || '',
    proposals,
    memorySaved: saved,
    memoryText: saved ? `записано в память (${memory.file || 'заметка'})`
      : memory.status === 'not_configured' ? 'память Bossman не настроена'
        : 'в память не записывалось',
    draftsCreated: (drafts.count || 0) > 0,
    draftsText: (drafts.count || 0) > 0 ? `черновиков задач: ${drafts.count}` : proposals ? `предложено задач: ${proposals}` : 'задач не предложено',
  };
}

/* ---------------------------------------------------------------- стили (один раз, только токены темы) */

const STYLE_ID = 'tc-style';
const CSS = `
.tc-page{display:flex;flex-direction:column;gap:16px;min-width:0}
.tc-page *{min-width:0}
.tc-banner-test{background:#b3261e;color:#fff;border-radius:var(--radius,10px);padding:10px 14px;font-weight:700;letter-spacing:.02em}
.tc-banner-test small{display:block;font-weight:500;opacity:.92;margin-top:2px}
.tc-note{border:1px solid var(--line);border-radius:var(--radius,10px);padding:10px 12px;font-size:13px;overflow-wrap:anywhere}
.tc-note-warn{border-color:color-mix(in srgb,var(--warn) 45%,transparent);background:color-mix(in srgb,var(--warn) 10%,transparent)}
.tc-note-err{border-color:color-mix(in srgb,var(--err) 45%,transparent);background:color-mix(in srgb,var(--err) 10%,transparent)}
.tc-note-hint{color:var(--dim);font-size:12px;margin-top:2px}
.tc-grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media(max-width:720px){.tc-grid2{grid-template-columns:minmax(0,1fr)}}
.tc-step{display:flex;flex-direction:column;gap:8px;padding:10px 0;border-top:1px solid var(--line-soft,var(--line))}
.tc-live{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;padding:10px 12px;border:1px solid var(--line);border-radius:var(--radius,10px)}
.tc-live-state{font-size:18px;font-weight:650}
.tc-live-on{border-color:color-mix(in srgb,var(--ok) 55%,transparent);background:color-mix(in srgb,var(--ok) 8%,transparent)}
.tc-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
@media(max-width:720px){.tc-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
.tc-metric{border:1px solid var(--line);border-radius:var(--radius-sm,8px);padding:8px 10px}
.tc-metric b{display:block;font-size:16px;font-variant-numeric:tabular-nums}
.tc-metric span{font-size:11.5px;color:var(--dim)}
.tc-list{display:flex;flex-direction:column;gap:6px}
.tc-item{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border:1px solid var(--line);border-radius:var(--radius-sm,8px);padding:8px 10px}
.tc-item-main{flex:1 1 180px;overflow-wrap:anywhere}
.tc-verdict{display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--line-soft,var(--line));overflow-wrap:anywhere}
.tc-verdict>div{flex:1 1 200px}
.tc-run-note{font-size:12px;color:var(--dim)}
.tc-hist-text{font-size:13px;overflow-wrap:anywhere;margin:4px 0}
`;

function injectStyle() {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = CSS;
  document.head.appendChild(el);
}

/* ---------------------------------------------------------------- вид (DOM живёт на месте: опрос его не пересоздаёт) */

function setBtn(btn, { disabled, title }) {
  btn.disabled = !!disabled;
  btn.title = title;
}

/** Кнопка с состоянием «идёт запрос». Доступность потом выставляет view.applyControls(). */
function asyncBtn(label, handler, { cls = 'btn', title, after } = {}) {
  const b = h('button', { class: cls, type: 'button', title: title || label }, h('span', label));
  b.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (b.classList.contains('busy')) return;
    b.classList.add('busy');
    b.disabled = true;
    b.setAttribute('aria-busy', 'true');
    try { await handler(e); } finally {
      b.classList.remove('busy');
      b.removeAttribute('aria-busy');
      b.disabled = false;
      if (after) after();
    }
  });
  return b;
}

class CallsView {
  constructor(ctx) {
    this.ctx = ctx;
    this.st = null;
    this.timer = null;
    this.polling = null;          // the poll in flight (a promise) or null
    this.pollAgain = false;       // an owner action / bus event came while it was in flight: one more poll right after it
    this.lastCallId = undefined;
    this.actionError = null;
    this.pickedConfirm = false;
    this.contacts = [];
    this.refs = {};
    this.root = this.build();
    this.timer = setInterval(() => this.tick(), POLL_MS);
    this.onVisible = () => { if (!document.hidden && this.root.isConnected) this.poll(); };
    document.addEventListener('visibilitychange', this.onVisible);
    this.poll();
    this.loadHistory();
  }

  /* ---- опрос: живёт, пока страница в документе; скрытая вкладка не опрашивает, при возврате — сразу опрос */
  tick() {
    if (!this.root.isConnected) { clearInterval(this.timer); this.timer = null; document.removeEventListener('visibilitychange', this.onVisible); return; }
    if (document.hidden) return;
    this.poll();
  }

  /* Один опрос за раз: тик, пока ответ не пришёл, пропускается (медленный сервер не долбим подряд).
     fresh — после действия владельца или события шины: ответ в полёте мог быть снят ДО действия, поэтому сразу после
     него идёт ещё один опрос, и вызывающий ждёт именно его (кнопки не остаются на устаревшем состоянии). */
  async poll(fresh = false) {
    if (this.polling) {
      if (fresh) this.pollAgain = true;
      return this.polling;
    }
    let settle;
    this.polling = new Promise((resolve) => { settle = resolve; });
    try {
      do {
        this.pollAgain = false;
        try {
          const st = await api.raw(`${BASE}/status`);
          this.st = st;
          this.refs.offline.hidden = true;
          this.apply(st);
        } catch (e) {
          this.refs.offline.hidden = false;
          this.refs.offline.textContent = 'Нет связи с Bossman. Состояние звонков не обновляется.';
          if (e && e.isAuth) this.refs.offline.textContent = 'Нужен вход в Bossman.';
        }
      } while (this.pollAgain);
    } finally { this.polling = null; settle(); }
  }

  async run(fn, { ok, fail = 'Не удалось выполнить действие', quiet = false } = {}) {
    try {
      const res = await fn();
      this.actionError = null;
      if (ok) toastOk(ok);
      await this.poll(true);
      return res;
    } catch (e) {
      this.actionError = errorText(e);
      if (!quiet) toastError(e, fail);
      this.renderActionError();
      await this.poll(true);
      return null;
    }
  }

  /* ---- построение */
  build() {
    const r = this.refs;
    r.offline = h('div.tc-note.tc-note-err', { hidden: true, role: 'status', 'data-testid': 'tc-offline' });
    r.testBanner = h('div.tc-banner-test', { hidden: true, role: 'status', 'data-testid': 'tc-test-banner' },
      'ТЕСТ БЕЗ TELEGRAM', h('small', 'Это проверка нашего тракта на тестовом собеседнике. Настоящий звонок в Telegram не совершается.'));
    r.stopNote = h('div.tc-note.tc-note-warn', { hidden: true, 'data-testid': 'tc-stop-note' });
    r.uncertainNote = h('div.tc-note.tc-note-warn', { hidden: true, 'data-testid': 'tc-uncertain-note' });

    const head = pageHead('Telegram-звонки',
      'Голосовой разговор ассистента с вашим вторым аккаунтом. Звонки выключены, пока вы не включите их сами.');

    return h('div.tc-page', { 'data-testid': 'telegram-calls' },
      head, r.offline, r.testBanner, r.stopNote,
      panel('Подключение', this.buildConnect()),
      panel('Тестовый собеседник', this.buildPeer()),
      panel('Звонок', this.buildCall()),
      panel('Проверки', this.buildChecks()));
  }

  buildConnect() {
    const r = this.refs;
    r.stepText = h('div.small', { 'data-testid': 'tc-step-text' }, 'Загрузка…');
    r.apiId = input({ type: 'text', inputmode: 'numeric', autocomplete: 'off', name: 'tc-api-id', placeholder: '1234567' });
    r.apiHash = input({ type: 'password', autocomplete: 'off', name: 'tc-api-hash', placeholder: '32 символа' });
    r.apiSaved = h('div.xsmall.dim', { 'data-testid': 'tc-api-saved' });
    r.saveApi = asyncBtn('Сохранить ключи', () => this.saveApi(), { cls: 'btn btn-primary', title: 'Сохранить api_id и api_hash в зашифрованном хранилище Bossman' });
    r.stepApi = h('div.tc-step', { 'data-testid': 'tc-step-api' },
      h('div.tc-grid2',
        field('api_id', r.apiId, 'Число с my.telegram.org → API development tools.'),
        field('api_hash', r.apiHash, 'После сохранения не показывается — только маска. Хранится зашифрованным.')),
      h('div.row', r.saveApi, r.apiSaved));

    r.phone = input({ type: 'tel', autocomplete: 'off', name: 'tc-phone', placeholder: '+79001234567' });
    r.sendCode = asyncBtn('Получить код', () => this.loginStart(), { cls: 'btn btn-primary', title: 'Telegram пришлёт код в приложение на этом аккаунте' });
    r.stepPhone = h('div.tc-step', { 'data-testid': 'tc-step-phone', hidden: true },
      field('Номер телефона', r.phone, 'Международный формат. Номер не сохраняется и не показывается.'), h('div.row', r.sendCode));

    r.code = input({ type: 'text', inputmode: 'numeric', autocomplete: 'off', name: 'tc-code', placeholder: 'код из Telegram' });
    r.sendLogin = asyncBtn('Войти', () => this.loginCode(), { cls: 'btn btn-primary', title: 'Отправить код' });
    r.stepCode = h('div.tc-step', { 'data-testid': 'tc-step-code', hidden: true },
      field('Код из Telegram', r.code, 'Не пересылайте код в чатах: Telegram аннулирует такой код.'), h('div.row', r.sendLogin));

    r.password = input({ type: 'password', autocomplete: 'off', name: 'tc-password', placeholder: 'пароль 2FA' });
    r.sendPassword = asyncBtn('Подтвердить', () => this.loginPassword(), { cls: 'btn btn-primary', title: 'Отправить пароль двухэтапной защиты' });
    r.stepPassword = h('div.tc-step', { 'data-testid': 'tc-step-password', hidden: true },
      field('Пароль двухэтапной защиты', r.password, 'Уходит на сервер один раз и нигде не сохраняется.'), h('div.row', r.sendPassword));

    r.accountLine = h('div.small', { 'data-testid': 'tc-account-line' });
    r.logout = asyncBtn('Выйти из аккаунта', () => this.logout(), { cls: 'btn btn-danger', title: 'Забыть сессию и сбросить выбранного собеседника' });
    r.stepReady = h('div.tc-step', { 'data-testid': 'tc-step-ready', hidden: true },
      r.accountLine, h('div.row', r.logout));

    return h('div.stack.sm', r.stepText, r.stepApi, r.stepPhone, r.stepCode, r.stepPassword, r.stepReady);
  }

  buildPeer() {
    const r = this.refs;
    r.peerNow = h('div', { 'data-testid': 'tc-peer-now' }, 'Не выбран');
    r.confirmBox = h('input', { type: 'checkbox', name: 'tc-peer-confirm' });
    r.confirmBox.addEventListener('change', () => { this.pickedConfirm = r.confirmBox.checked; this.applyControls(); });
    r.contactQuery = input({ type: 'text', autocomplete: 'off', name: 'tc-contact-q', placeholder: 'имя или @юзернейм' });
    r.findContacts = asyncBtn('Найти', () => this.loadContacts(), { title: 'Показать контакты и недавние личные диалоги' });
    r.contactList = h('div.tc-list', { 'data-testid': 'tc-contacts' });
    r.username = input({ type: 'text', autocomplete: 'off', name: 'tc-username', placeholder: '@юзернейм' });
    r.pickUsername = asyncBtn('Выбрать по юзернейму', () => this.pickPeer({ username: r.username.value.trim().replace(/^@/, '') }),
      { title: 'Выбрать по юзернейму' });
    r.clearPeer = asyncBtn('Сбросить выбор', () => this.clearPeer(), { title: 'Забыть тестового собеседника' });
    r.peerHint = h('div.xsmall.dim', { 'data-testid': 'tc-peer-hint' });
    return h('div.stack.sm',
      h('div.row', h('b', 'Сейчас: '), r.peerNow, h('div.spacer'), r.clearPeer),
      h('label.check', r.confirmBox, h('span', 'Это мой второй аккаунт')),
      r.peerHint,
      h('div.row', r.contactQuery, r.findContacts),
      r.contactList,
      h('div.row', r.username, r.pickUsername),
      h('div.xsmall.dim', 'Звонок возможен только на этот один аккаунт. Смена собеседника — только вручную здесь.'));
  }

  buildCall() {
    const r = this.refs;
    const sw = toggle(false, (on) => this.setEnabled(on), 'Разрешить звонки ассистента на выбранный второй аккаунт');
    r.enabledInput = sw.querySelector('input');
    r.enabledInput.name = 'tc-enabled';
    r.enabledInput.setAttribute('aria-label', 'Разрешить звонки');
    r.enabledHint = h('span.xsmall.dim');
    r.autoSaveBox = h('input', { type: 'checkbox', name: 'tc-autosave' });
    r.autoSaveBox.addEventListener('change', () => this.setAutoSave(r.autoSaveBox.checked));

    const ans = toggle(false, (on) => this.setAnswering(on), 'Автоответчик: Джефф берёт входящий вместо вас, если вы не ответили');
    r.ansInput = ans.querySelector('input');
    r.ansInput.name = 'tc-answering';
    r.ansInput.setAttribute('aria-label', 'Автоответчик для входящих звонков');
    r.ansStatus = h('div.small', { 'data-testid': 'tc-answering-status' });

    r.liveState = h('span.tc-live-state', { 'data-testid': 'tc-live-state' }, STATE_WORDS.idle);
    r.livePhase = h('span.small.dim', { 'data-testid': 'tc-live-phase' });
    r.live = h('div.tc-live', r.liveState, r.livePhase);

    r.dial = asyncBtn('Позвонить', () => this.dial(), { cls: 'btn btn-primary', after: () => this.applyControls() });
    r.hangup = asyncBtn('Завершить', () => this.hangup(), { after: () => this.applyControls() });
    r.stop = asyncBtn('STOP', () => this.stop(), { cls: 'btn btn-danger', after: () => this.applyControls(), title: 'STOP' });
    r.resume = asyncBtn('Продолжить', () => this.resume(), { after: () => this.applyControls() });
    r.confirmUnknownBox = h('input', { type: 'checkbox', name: 'tc-confirm-unknown' });
    r.confirmUnknownBox.addEventListener('change', () => this.applyControls());
    r.confirmUnknownWrap = h('label.check', { hidden: true }, r.confirmUnknownBox,
      h('span', 'Я проверил второй аккаунт: прошлый звонок мог остаться на линии, позвонить снова'));
    r.err = h('div.tc-note.tc-note-err', { hidden: true, role: 'alert', 'data-testid': 'tc-error' });

    r.mLast = h('b', '—'); r.mP50 = h('b', '—'); r.mP95 = h('b', '—'); r.mN = h('b', '—');
    const metric = (label, node) => h('div.tc-metric', node, h('span', label));
    r.models = h('div.small', { 'data-testid': 'tc-models' }, 'Модели: пока нет данных');
    r.history = h('div.stack.sm', { 'data-testid': 'tc-history' });

    return h('div.stack.sm',
      h('div.row', sw, h('b', 'Разрешить звонки'), r.enabledHint),
      h('label.check', r.autoSaveBox, h('span', 'Сразу записывать краткий итог звонка в память Bossman (по умолчанию выключено)')),
      h('div.row', ans, h('b', 'Автоответчик (входящие)')),
      r.ansStatus,
      r.uncertainNote,
      r.live,
      h('div.row', r.dial, r.hangup, r.stop, r.resume),
      r.confirmUnknownWrap,
      r.err,
      h('div.tc-metrics', metric('последняя задержка ответа', r.mLast), metric('медиана (p50)', r.mP50),
        metric('p95', r.mP95), metric('замеров', r.mN)),
      h('div.tc-run-note', 'Задержка — от конца вашей реплики до первого звука ответа. Считается только по этому звонку.'),
      r.models,
      h('h3', 'Последние звонки'),
      r.history);
  }

  buildChecks() {
    const r = this.refs;
    r.selftest = asyncBtn('Проверить аудиоконтур', () => this.selftest(), { title: 'Проверка без Telegram: тестовый собеседник на петле' });
    r.selftestOut = h('div', { 'data-testid': 'tc-selftest' });
    r.doctor = asyncBtn('Диагностика', () => this.doctor(), { title: 'Локальные проверки: зависимости, права, журнал, голос. Ничего не отправляет в Telegram' });
    r.doctorOut = h('div', { 'data-testid': 'tc-doctor' });
    r.installNote = h('div.small', { 'data-testid': 'tc-install-note' });
    r.install = asyncBtn('Установить зависимости', () => this.install(), { title: 'Установить пакеты звонков (Telethon, py-tgcalls, Whisper) в каталог Bossman' });
    r.installBox = h('div.stack.sm', { hidden: true }, r.installNote, h('div.row', r.install));
    return h('div.stack.sm',
      h('div.row', r.selftest, r.doctor),
      h('div.tc-run-note', 'Обе проверки локальные и никому не звонят. Итог проверки аудиоконтура помечен «ТЕСТ БЕЗ TELEGRAM».'),
      r.installBox, r.selftestOut, r.doctorOut);
  }

  /* ---- применение состояния (на месте, без пересоздания) */
  apply(st) {
    const r = this.refs;
    r.testBanner.hidden = !isTestMode(st);

    const step = accountStep(st);
    r.stepText.textContent = step.text;
    r.stepApi.hidden = !(step.key === 'api');
    r.stepPhone.hidden = !(step.key === 'phone' || step.key === 'code' || step.key === 'password');
    r.stepCode.hidden = step.key !== 'code';
    r.stepPassword.hidden = step.key !== 'password';
    r.stepReady.hidden = step.key !== 'ready';
    const a = st.account || {};
    r.apiSaved.textContent = a.has_api ? `Ключи сохранены (api_id ${a.api_id || '…'})` : '';
    r.accountLine.textContent = `Основной аккаунт подключён${a.phone ? ` (${a.phone})` : ''}.`;

    r.peerNow.textContent = st.peer ? `${st.peer.label || 'без имени'} (id ${st.peer.user_id})` : 'Не выбран';
    r.enabledInput.checked = !!st.enabled;
    r.enabledHint.textContent = st.enabled ? 'включены' : 'выключены';
    if (st.postcall) r.autoSaveBox.checked = !!st.postcall.auto_save_to_bossman_memory;
    const aw = answeringWords(st);
    r.ansInput.checked = aw.on;
    r.ansStatus.textContent = aw.text;
    r.ansStatus.className = `small tc-answering-${aw.tone}`;

    const stopped = st.stop || {};
    r.stopNote.hidden = !stopped.active;
    if (stopped.active) {
      r.stopNote.textContent = stopped.call
        ? 'Действует STOP звонков: набор заблокирован, пока вы не нажмёте «Продолжить».'
        : 'Действует общий STOP Bossman: набор заблокирован, пока вы не снимете его в управлении компьютером.';
    }
    r.uncertainNote.hidden = !st.uncertain_previous;
    if (st.uncertain_previous) {
      r.uncertainNote.textContent = 'Исход предыдущего звонка неизвестен. Проверьте второй аккаунт: возможно, звонок ещё идёт. Автоматического перезвона нет.';
    }
    r.confirmUnknownWrap.hidden = !st.uncertain_previous;
    if (!st.uncertain_previous) r.confirmUnknownBox.checked = false;

    const words = callWords(st);
    r.liveState.textContent = words.state;
    r.livePhase.textContent = words.phase ? `ассистент ${words.phase}` : '';
    r.live.classList.toggle('tc-live-on', words.live);

    const lat = st.latency || {};
    r.mLast.textContent = fmtMs(lat.last); r.mP50.textContent = fmtMs(lat.p50);
    r.mP95.textContent = fmtMs(lat.p95); r.mN.textContent = lat.n ? String(lat.n) : '—';
    const m = st.models || {};
    r.models.textContent = m.stt || m.llm || m.tts
      ? `Модели (как сообщает движок): распознавание — ${m.stt || '—'}; ответ — ${m.llm || '—'}; голос — ${m.tts || '—'}`
      : 'Модели: пока нет данных (появятся после первого звонка)';

    const failed = st.last_error && !this.actionError ? errorText(st.last_error) : null;
    this.renderActionError(failed);

    const inst = st.install;
    const missing = (st.deps && st.deps.missing) || [];
    r.installBox.hidden = !(missing.length || (inst && inst.state === 'running'));
    if (!r.installBox.hidden) {
      r.installNote.textContent = inst && inst.state === 'running'
        ? `Установка идёт… ${(inst.progress || []).slice(-1)[0] || ''}`
        : `Не хватает пакетов для настоящего звонка: ${missing.join(', ')}.`;
    }

    if (this.lastCallId !== ((st.last_call && st.last_call.call_id) || null)) {
      const first = this.lastCallId === undefined;
      this.lastCallId = (st.last_call && st.last_call.call_id) || null;
      if (!first) this.loadHistory();
    }
    this.applyControls();
  }

  renderActionError(fromStatus = undefined) {
    const shown = this.actionError || fromStatus;
    const box = this.refs.err;
    box.hidden = !shown;
    box.textContent = '';
    if (shown) {
      box.appendChild(h('div', h('b', shown.message)));
      if (shown.hint) box.appendChild(h('div.tc-note-hint', shown.hint));
    }
  }

  applyControls() {
    const r = this.refs;
    const c = controlState(this.st, { confirmUnknown: r.confirmUnknownBox.checked });
    setBtn(r.dial, c.dial); setBtn(r.hangup, c.hangup); setBtn(r.stop, c.stop); setBtn(r.resume, c.resume);
    const pick = peerPickState(this.st, this.pickedConfirm);
    for (const b of this.root.querySelectorAll('[data-pick]')) setBtn(b, pick);
    const unamePick = pick.disabled ? pick : (r.username.value.trim() ? pick : { disabled: true, title: 'Введите юзернейм' });
    setBtn(r.pickUsername, unamePick);
    const ready = ((this.st && this.st.account && this.st.account.state) || '') === 'ready';
    setBtn(r.findContacts, ready ? { disabled: false, title: 'Показать контакты и недавние личные диалоги' }
      : { disabled: true, title: 'Сначала подключите аккаунт Telegram' });
    setBtn(r.clearPeer, this.st && this.st.peer ? { disabled: false, title: 'Забыть тестового собеседника' }
      : { disabled: true, title: 'Собеседник не выбран' });
    r.peerHint.textContent = ready ? '' : 'Список контактов появится после подключения аккаунта.';
    setBtn(r.selftest, this.st && this.st.selftest_running ? { disabled: true, title: 'Проверка уже идёт' }
      : this.st && this.st.call_active ? { disabled: true, title: 'Во время звонка проверка недоступна' }
        : { disabled: false, title: 'Проверка без Telegram: тестовый собеседник на петле' });
    r.enabledInput.disabled = !this.st;
    r.enabledInput.title = this.st ? 'Разрешить звонки ассистента на выбранный второй аккаунт' : 'Состояние ещё загружается';
  }

  /* ---- действия */
  async saveApi() {
    const apiId = Number(String(this.refs.apiId.value).trim());
    const apiHash = this.refs.apiHash.value.trim();
    if (!Number.isInteger(apiId) || apiId <= 0 || apiHash.length !== 32) {
      this.actionError = { message: 'api_id — число, api_hash — 32 символа.', hint: 'Возьмите их на my.telegram.org → API development tools.' };
      this.renderActionError();
      toastError(this.actionError, 'Ключи не приняты');
      return;
    }
    const res = await this.run(() => api.raw(`${BASE}/credentials`, { method: 'POST', body: { api_id: apiId, api_hash: apiHash } }),
      { ok: 'Ключи сохранены', fail: 'Ключи не сохранены' });
    this.refs.apiHash.value = '';
    if (res) this.refs.apiId.value = '';
  }

  async loginStart() {
    const phone = this.refs.phone.value.trim();
    await this.run(() => api.raw(`${BASE}/login/start`, { method: 'POST', body: { phone } }),
      { ok: 'Код отправлен: посмотрите приложение Telegram', fail: 'Код не запрошен' });
    this.refs.phone.value = '';
  }

  async loginCode() {
    const code = this.refs.code.value.trim();
    this.refs.code.value = '';
    await this.run(() => api.raw(`${BASE}/login/code`, { method: 'POST', body: { code } }), { ok: 'Код принят', fail: 'Код не принят' });
  }

  async loginPassword() {
    const password = this.refs.password.value;
    this.refs.password.value = '';
    await this.run(() => api.raw(`${BASE}/login/password`, { method: 'POST', body: { password } }), { ok: 'Пароль принят', fail: 'Пароль не принят' });
  }

  async logout() {
    await this.run(() => api.raw(`${BASE}/logout`, { method: 'POST', body: {} }), { ok: 'Вы вышли из аккаунта', fail: 'Выйти не удалось' });
  }

  async loadContacts() {
    const q = this.refs.contactQuery.value.trim();
    const res = await this.run(() => api.raw(`${BASE}/contacts${q ? `?q=${encodeURIComponent(q)}` : ''}`), { fail: 'Контакты не загружены' });
    if (!res) return;
    this.contacts = res.contacts || [];
    const list = this.refs.contactList;
    list.textContent = '';
    if (!this.contacts.length) list.appendChild(h('div.small.dim', 'Никого не нашли. Проверьте, что второй аккаунт есть в контактах.'));
    for (const c of this.contacts) {
      const pickBtn = asyncBtn('Выбрать', () => this.pickPeer({ user_id: c.id }), { title: 'Отметьте «Это мой второй аккаунт»' });
      pickBtn.setAttribute('data-pick', '1');
      list.appendChild(h('div.tc-item', h('div.tc-item-main', h('b', c.label || 'без имени'),
        c.username ? h('span.xsmall.dim', ` @${c.username}`) : null), pickBtn));
    }
    this.applyControls();
  }

  async pickPeer(target) {
    const res = await this.run(() => api.raw(`${BASE}/peer`, { method: 'PUT', body: { ...target, confirm: this.pickedConfirm } }),
      { ok: 'Тестовый собеседник выбран', fail: 'Собеседник не выбран' });
    if (res) { this.refs.confirmBox.checked = false; this.pickedConfirm = false; this.refs.username.value = ''; }
  }

  async clearPeer() {
    await this.run(() => api.raw(`${BASE}/peer`, { method: 'DELETE' }), { ok: 'Выбор собеседника сброшен', fail: 'Не удалось сбросить' });
  }

  async setEnabled(on) {
    const res = await this.run(() => api.raw(`${BASE}/settings`, { method: 'PUT', body: { enabled: !!on } }),
      { ok: on ? 'Звонки разрешены' : 'Звонки выключены', fail: 'Настройка не сохранена' });
    if (!res && this.st) this.refs.enabledInput.checked = !!this.st.enabled;
  }

  async setAnswering(on) {
    const res = await this.run(() => api.raw(`${BASE}/settings`, { method: 'PUT', body: { answering_machine: !!on } }),
      { ok: on ? 'Автоответчик включён' : 'Автоответчик выключен', fail: 'Настройка не сохранена' });
    const err = res && res.answering && res.answering.error;
    if (err) toastError(err, 'Автоответчик включён в настройках, но не запущен');
    if (!res && this.st) this.refs.ansInput.checked = !!(this.st.answering && this.st.answering.enabled);
  }

  async setAutoSave(on) {
    const res = await this.run(() => api.raw(`${BASE}/settings`, { method: 'PUT', body: { auto_save_to_bossman_memory: !!on } }),
      { ok: on ? 'Итоги звонков будут записываться в память' : 'Итоги в память не записываются', fail: 'Настройка не сохранена' });
    if (!res && this.st && this.st.postcall) this.refs.autoSaveBox.checked = !!this.st.postcall.auto_save_to_bossman_memory;
  }

  async dial() {
    const confirmUnknown = this.refs.confirmUnknownBox.checked;
    const res = await this.run(() => api.raw(`${BASE}/call`, { method: 'POST', body: confirmUnknown ? { confirm_unknown: true } : {} }),
      { fail: 'Звонок не начат' });
    if (res) toastOk(res.test_label || (this.st && isTestMode(this.st)) ? 'Тестовый звонок начат (без Telegram)' : 'Звонок начат');
  }

  async hangup() {
    await this.run(() => api.raw(`${BASE}/hangup`, { method: 'POST', body: {} }), { ok: 'Разговор завершён', fail: 'Завершить не удалось' });
  }

  async stop() {
    try {
      const res = await api.raw(`${BASE}/stop`, { method: 'POST', body: {} });
      this.actionError = null;
      const extra = res && res.terminated ? ' Процесс звонков пришлось остановить принудительно.'
        : res && res.hangup_confirmed === false ? ' Завершение звонка пока не подтверждено — проверьте второй аккаунт.' : '';
      toastOk('STOP выполнен: звонки заблокированы до «Продолжить».' + extra);
    } catch (e) {
      /* STOP не молчит: даже при сбое запроса владелец видит, что именно не сработало */
      this.actionError = { message: 'STOP не подтверждён сервером: ' + ((e && e.message) || 'нет ответа'),
        hint: 'Закройте звонок на втором аккаунте вручную и повторите STOP.' };
      this.renderActionError();
      toastError(e, 'STOP не подтверждён');
    }
    await this.poll(true);
  }

  async resume() {
    const res = await this.run(() => api.raw(`${BASE}/resume`, { method: 'POST', body: {} }), { fail: 'Не удалось снять STOP' });
    if (res) {
      if (res.global_stop) toastOk('STOP звонков снят.', 'Общий STOP Bossman ещё действует: звонить нельзя, пока его не снимут.');
      else toastOk('STOP звонков снят');
    }
  }

  async selftest() {
    const out = this.refs.selftestOut;
    out.textContent = '';
    out.appendChild(h('div.small.dim', 'Проверяю аудиоконтур (около минуты)…'));
    const res = await this.run(() => api.raw(`${BASE}/selftest`, { method: 'POST', body: { scenario: 'all' } }),
      { fail: 'Проверка не выполнена' });
    out.textContent = '';
    if (!res) { out.appendChild(h('div.small.dim', 'Проверка не выполнена.')); return; }
    out.appendChild(h('div.tc-note.tc-note-warn', { 'data-testid': 'tc-selftest-label' }, 'ТЕСТ БЕЗ TELEGRAM — проверен наш тракт, не настоящий звонок.'));
    out.appendChild(h('div.row', h('b', 'Итог: '), badge(res.verdict === 'PASS' ? 'пройдено' : 'не пройдено', VERDICT_TONE[res.verdict] || '')));
    for (const item of res.results || []) {
      const checks = Object.entries(item.checks || {}).map(([k, v]) => `${v ? '✓' : '✗'} ${k}`).join(' · ');
      out.appendChild(h('div.tc-verdict', badge(item.verdict === 'PASS' ? 'пройдено' : 'не пройдено', VERDICT_TONE[item.verdict] || ''),
        h('div', h('b', item.scenario), h('div.xsmall.dim.mono', checks))));
    }
  }

  async doctor() {
    const out = this.refs.doctorOut;
    const res = await this.run(() => api.raw(`${BASE}/doctor`, { method: 'POST', body: {} }), { fail: 'Диагностика не выполнена' });
    out.textContent = '';
    if (!res) return;
    out.appendChild(h('div.row', h('b', 'Диагностика: '), badge(res.verdict, VERDICT_TONE[res.verdict] || '')));
    for (const row of res.rows || []) {
      out.appendChild(h('div.tc-verdict', badge(row.status, VERDICT_TONE[row.status] || ''),
        h('div', h('b', row.check), h('div.small', row.detail || ''), row.remedy ? h('div.xsmall.dim', `Что делать: ${row.remedy}`) : null)));
    }
  }

  async install() {
    await this.run(() => api.raw(`${BASE}/install`, { method: 'POST', body: {} }), { ok: 'Установка запущена', fail: 'Установка не запущена' });
  }

  /* ---- история и итоги звонков */
  async loadHistory() {
    let res = null;
    try { res = await api.raw(`${BASE}/history?limit=5`); } catch { return; }
    const box = this.refs.history;
    box.textContent = '';
    const items = (res && res.items) || [];
    if (!items.length) { box.appendChild(h('div.small.dim', 'Звонков ещё не было.')); return; }
    for (const item of items) box.appendChild(this.historyRow(item));
  }

  historyRow(item) {
    const s = summarizeHistoryItem(item);
    const when = item.started_at ? new Date(item.started_at * 1000).toLocaleString() : '';
    const memBtn = asyncBtn('Сохранить итог в память', async () => {
      await this.run(() => api.raw(`${BASE}/history/${encodeURIComponent(item.call_id)}/save-memory`, { method: 'POST', body: {} }),
        { ok: 'Итог записан в память Bossman', fail: 'Итог не записан' });
      this.loadHistory();
    }, { title: 'Записать краткий итог в память Bossman' });
    if (s.memorySaved) setBtn(memBtn, { disabled: true, title: 'Итог уже записан в память' });
    const taskBtn = asyncBtn('Создать черновики задач', async () => {
      await this.run(() => api.raw(`${BASE}/history/${encodeURIComponent(item.call_id)}/draft-tasks`, { method: 'POST', body: {} }),
        { ok: 'Черновики созданы: запустить их можете только вы', fail: 'Черновики не созданы' });
      this.loadHistory();
    }, { title: 'Создать черновики задач (ничего не запускается)' });
    if (!s.proposals) setBtn(taskBtn, { disabled: true, title: 'В этом звонке не предлагали задач' });
    else if (s.draftsCreated) setBtn(taskBtn, { disabled: true, title: 'Черновики уже созданы' });
    const err = item.error;
    return h('div.tc-item', { 'data-testid': 'tc-history-item' },
      h('div.tc-item-main',
        h('div', h('b', s.outcome), h('span.xsmall.dim', ` · ${when} · реплик: ${s.turns}${typeof s.p50 === 'number' ? ` · p50 ${fmtMs(s.p50)}` : ''}`),
          s.test ? h('span', ' ', badge('ТЕСТ БЕЗ TELEGRAM', 'err')) : null),
        s.text ? h('div.tc-hist-text', s.text) : null,
        err ? h('div.xsmall.dim', `${err.message}${err.hint ? ` ${err.hint}` : ''}`) : null,
        h('div.xsmall.dim', `${s.memoryText} · ${s.draftsText}`)),
      h('div.row', memBtn, taskBtn));
  }
}

/* ---------------------------------------------------------------- страница */

let live = null;

const TelegramCallsPage = {
  id: 'telegram_calls',
  title: 'Telegram-звонки',
  icon: 'activity',
  nav: 'more',
  section: 'apps',

  async render(ctx) {
    injectStyle();
    live = new CallsView(ctx);
    return live.root;
  },

  /* события шины только подгоняют состояние на месте: страница не пересоздаётся, набранное не пропадает */
  onEvent(ev) {
    const kind = String((ev && ev.kind) || '');
    if (EVENT_KINDS.includes(kind) && live && live.root.isConnected) live.poll(true);
    return false;
  },
};

export default TelegramCallsPage;
