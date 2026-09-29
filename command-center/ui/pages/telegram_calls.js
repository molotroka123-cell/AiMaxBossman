/* ============================================================
   telegram_calls.js — «Telegram-звонки»: голосовой звонок от вашего основного
   аккаунта на ОДИН выбранный второй аккаунт.
   Endpoints: /api/telegram/calls/* (bcc/features/telegram_calls.py, обычная auth/CSRF).

   Правила страницы:
   - пустое состояние (ничего не настроено) — это 200 и экран подключения, а не ошибка;
   - у кнопки «Позвонить» НЕТ выбора собеседника: звонок идёт только выбранному и
     подтверждённому второму аккаунту; недоступная кнопка объясняет причину в title;
   - секреты (api_hash, код, пароль 2FA) только в полях type=password, не пишутся в
     localStorage, не попадают в лог страницы и не показываются повторно;
   - STOP доступен всегда; «Продолжить» снимает только STOP звонков.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, confirmDialog, input, toggle, fmtClock } from '../components.js';
import { panel, pageHead, pill, btn, errorNote, field } from './_ui.js';

const BASE = '/api/telegram/calls';
const POLL_MS = 2500;
const LOG_LIMIT = 60;

const ACCOUNT_TEXT = {
  no_credentials: 'нет api_id / api_hash', logged_out: 'не вошли', code_sent: 'ждём код из Telegram',
  password_needed: 'нужен пароль 2FA', ready: 'подключён', error: 'ошибка',
};
const OUTCOME_TEXT = {
  completed: 'завершён', declined: 'отклонён', busy: 'занято', no_answer: 'нет ответа',
  connection_lost: 'связь потеряна', stopped: 'остановлен', max_duration: 'лимит времени',
  silence_timeout: 'тишина', failed: 'ошибка', unknown: 'исход неизвестен',
};

/** Почему «Позвонить» недоступна (пустая строка — можно). */
/* Fresh idempotency key per click: a replayed POST /dial is rejected by the server, never rings twice. */
export function newRequestId() {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
  } catch (e) { /* fall through */ }
  return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/* record_audio is not implemented yet: the switch is shown but disabled (the API refuses true as well). */
export function recordAudioToggle() {
  const title = 'Запись звука пока не реализована';
  const el = toggle(false, () => {}, title);
  const inp = el.querySelector && el.querySelector('input');
  if (inp) { inp.disabled = true; inp.checked = false; }
  el.setAttribute('aria-disabled', 'true');
  el.title = title;
  return el;
}

export function dialBlockReason(st) {
  const s = (st && st.settings) || {};
  const stop = (st && st.stop) || {};
  if (((st && st.account) || {}).state !== 'ready') return 'Сначала подключите аккаунт Telegram.';
  if (st && st.call) return 'Звонок уже идёт.';
  if (stop.global_stop) return 'Действует глобальный STOP компьютера: снимите его в разделе «Пульт».';
  if (stop.call_stop) return 'Действует STOP звонков: нажмите «Продолжить».';
  if (!s.enabled) return 'Включите переключатель «Разрешить звонки».';
  if (!s.peer) return 'Выберите второй аккаунт в списке контактов.';
  if (!s.peer_confirmed) return 'Подтвердите выбор второго аккаунта.';
  return '';
}

function structuralKey(st) {
  return JSON.stringify([(st.account || {}).state, st.settings, st.stop, !!st.call, (st.worker || {}).running]);
}

function eventText(e) {
  const d = e.data || {};
  const inner = d.data || {};
  if (e.event === 'record') return `звонок завершён: ${OUTCOME_TEXT[d.outcome] || d.outcome || '—'}${d.error_code ? ' (' + d.error_code + ')' : ''}`;
  if (e.event === 'state') return `состояние: ${d.state || '—'}`;
  if (e.event === 'log') return d.msg === 'stop' ? `STOP (${d.by || 'owner'})` : String(d.msg || 'лог');
  if (e.event === 'call_event') {
    if (d.kind === 'metric') return `ответ за ${Math.round(inner.response_latency_ms || 0)} мс`;
    return `${d.kind || 'событие'}${inner.state ? ': ' + inner.state : ''}`;
  }
  return e.event;
}

function latencyOf(events) {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const d = (events[i].data || {});
    const v = d.data && d.data.response_latency_ms;
    if (typeof v === 'number') return v;
  }
  return null;
}

const TelegramCallsPage = {
  id: 'telegram_calls',
  title: 'Telegram-звонки',
  icon: 'activity',
  nav: 'more',
  section: 'studio',

  async render(ctx) {
    let st;
    try { st = await api.raw(`${BASE}/status`); } catch (e) {
      return h('div.bx-page', pageHead('Telegram-звонки', 'Голосовой разговор с Bossman по звонку Telegram.'),
        errorNote(e, () => ctx.refresh()));
    }
    const view = new CallsView(ctx, st);
    return view.mount();
  },

  /* Форма входа не должна пропадать от чужих событий: страница обновляет себя сама. */
  onEvent() { return false; },
};

class CallsView {
  constructor(ctx, st) {
    this.ctx = ctx;
    this.st = st;
    this.events = [];
    this.since = 0;
    this.contacts = null;
    this.busy = new Set();
    this.root = h('div.bx-page', { 'data-testid': 'telegram-calls' });
    this.live = h('div.stack.sm', { 'data-testid': 'calls-live' });
    this.key = '';
    this.timer = null;
  }

  mount() {
    this.paint();
    this.timer = setInterval(() => this.poll(), POLL_MS);
    return this.root;
  }

  async call(label, fn) {
    try { return await fn(); } catch (e) { toastError(e, label); return null; }
  }

  async reload() {
    try { this.st = await api.raw(`${BASE}/status`); } catch { /* следующий опрос */ }
    this.paint();
  }

  async poll() {
    if (!this.root.isConnected) { clearInterval(this.timer); this.timer = null; return; }
    try {
      const [st, ev] = await Promise.all([api.raw(`${BASE}/status`), api.raw(`${BASE}/events?since=${this.since}`)]);
      this.st = st;
      const fresh = (ev && ev.events) || [];
      if (fresh.length) {
        this.events = this.events.concat(fresh).slice(-LOG_LIMIT);
        this.since = ev.seq || this.since;
      }
      const typing = this.root.contains(document.activeElement)
        && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
      if (structuralKey(st) !== this.key && !typing) this.paint();
      else this.paintLive();
    } catch { /* сервер перезапускается — следующий опрос */ }
  }

  paint() {
    const st = this.st;
    this.key = structuralKey(st);
    const ready = (st.account || {}).state === 'ready';
    this.root.replaceChildren(
      pageHead('Telegram-звонки',
        'Ваш основной аккаунт звонит на один выбранный второй аккаунт, и вы разговариваете с Bossman голосом. Больше никому.',
        { pills: [this.statusPill()], actions: [] }),
      ready ? this.controls() : this.connectPanel(),
      ...(ready ? [this.settingsPanel(), this.peerPanel()] : []),
      this.live,
      this.diagnosticsPanel(ready));
    this.paintLive();
  }

  statusPill() {
    const st = this.st;
    const stop = st.stop || {};
    if (stop.global_stop) return pill('Глобальный STOP', { tone: 'err' });
    if (stop.call_stop) return pill('STOP звонков', { tone: 'err' });
    if (st.call) return pill('Идёт звонок', { tone: 'ok', live: true });
    const acc = (st.account || {}).state;
    return pill(ACCOUNT_TEXT[acc] || 'не подключено', { tone: acc === 'ready' ? 'ok' : 'idle' });
  }

  /* ------------------------------------------------------------ connect */

  connectPanel() {
    const state = (this.st.account || {}).state || 'no_credentials';
    const msg = h('p.small.dim', { role: 'status', 'aria-live': 'polite' });
    const secret = (label, name, note) => {
      const el = input({ type: 'password', name, autocomplete: 'off', spellcheck: 'false' });
      return { el, node: field(label, el, note) };
    };
    const send = async (path, body, okText) => {
      msg.textContent = 'Выполняю…';
      const res = await this.call('Не удалось выполнить шаг', () => api.raw(`${BASE}${path}`, { method: 'POST', body }));
      msg.textContent = res ? okText : 'Шаг не выполнен — причина показана выше.';
      if (res) this.contacts = null;
      await this.reload();
    };
    let body;
    if (state === 'no_credentials') {
      const id = input({ type: 'text', name: 'api_id', inputmode: 'numeric', autocomplete: 'off' });
      const hash = secret('api_hash', 'api_hash', 'Получите на my.telegram.org → API development tools. Хранится зашифрованно.');
      body = [field('api_id', id, 'Число с my.telegram.org.'), hash.node,
        btn('Сохранить и продолжить', async () => {
          const n = Number(id.value.trim());
          if (!Number.isInteger(n) || n <= 0) { msg.textContent = 'api_id — это положительное число.'; return; }
          await send('/login/credentials', { api_id: n, api_hash: hash.el.value.trim() }, 'Сохранено.');
        }, { variant: 'primary', iconName: 'check', title: 'Сохранить api_id и api_hash' })];
    } else if (state === 'logged_out' || state === 'error') {
      const phone = secret('Номер телефона основного аккаунта', 'phone', 'Международный формат: +79001234567. Не показывается повторно.');
      body = [phone.node, btn('Запросить код', () => send('/login/start', { phone: phone.el.value.trim() }, 'Код отправлен в Telegram.'),
        { variant: 'primary', iconName: 'play', title: 'Telegram пришлёт код в приложение' })];
    } else if (state === 'code_sent') {
      const code = secret('Код из Telegram', 'code', 'Не пересылайте код никому.');
      body = [code.node, btn('Войти', () => send('/login/code', { code: code.el.value.trim() }, 'Код принят.'),
        { variant: 'primary', iconName: 'check' })];
    } else if (state === 'password_needed') {
      const pwd = secret('Пароль двухэтапной защиты', 'password', 'Только для входа; не сохраняется в открытом виде.');
      body = [pwd.node, btn('Подтвердить', () => send('/login/password', { password: pwd.el.value }, 'Пароль принят.'),
        { variant: 'primary', iconName: 'check' })];
    } else {
      body = [h('p', 'Неизвестное состояние аккаунта. Нажмите «Обновить».')];
    }
    const cancel = (state === 'code_sent' || state === 'password_needed')
      ? btn('Отмена', () => send('/login/cancel', {}, 'Вход отменён.'), { variant: 'ghost', size: 'sm' }) : null;
    return panel('Подключение аккаунта',
      h('form.stack.sm', { 'data-testid': 'calls-connect', autocomplete: 'off', onSubmit: (e) => e.preventDefault() },
        h('p.small.dim', `Шаг: ${ACCOUNT_TEXT[state] || state}. Дальше выберите второй аккаунт и подтвердите его.`),
        ...body, cancel, msg));
  }

  /* ------------------------------------------------------------ controls */

  controls() {
    const st = this.st;
    const stop = st.stop || {};
    const reason = dialBlockReason(st);
    const dial = btn('Позвонить', async () => {
      const s = st.settings || {};
      const uncertain = ['unknown', 'connection_lost'].includes(s.last_outcome);
      const label = (s.peer && (s.peer.label || s.peer.user_id)) || '';
      const ok = await confirmDialog({
        title: 'Начать звонок?', okText: 'Позвонить',
        text: `Реальный звонок пойдёт на ${label}.${uncertain ? ' Исход предыдущего звонка неизвестен: убедитесь, что второй аккаунт не занят.' : ''}`,
      });
      if (!ok) return;
      const res = await this.call('Звонок не начат', () => api.raw(`${BASE}/dial`, { method: 'POST', body: { confirm_unknown: uncertain, request_id: newRequestId() } }));
      if (res) toastOk('Звонок запущен');
      await this.reload();
    }, { variant: 'primary', iconName: 'play', disabled: Boolean(reason), title: reason || 'Позвонить выбранному второму аккаунту' });
    const hangup = btn('Положить трубку', async () => {
      await this.call('Не удалось', () => api.raw(`${BASE}/hangup`, { method: 'POST', body: {} }));
      await this.reload();
    }, { iconName: 'close', disabled: !st.call, title: st.call ? 'Завершить текущий звонок' : 'Сейчас нет звонка' });
    const stopBtn = btn('STOP', async () => {
      const res = await this.call('STOP не выполнен', () => api.raw(`${BASE}/stop`, { method: 'POST', body: {} }));
      if (res) toastOk('STOP: звонки остановлены');
      await this.reload();
    }, { variant: 'danger', iconName: 'stop', title: 'Немедленно завершить звонок и заблокировать новые' });
    const resumeReason = stop.global_stop ? 'Действует глобальный STOP компьютера: его снимает «Пульт», не эта страница.'
      : (!stop.call_stop ? 'STOP звонков не установлен.' : '');
    const resume = btn('Продолжить', async () => {
      await this.call('Не удалось', () => api.raw(`${BASE}/resume`, { method: 'POST', body: {} }));
      await this.reload();
    }, { iconName: 'retry', disabled: !stop.call_stop || Boolean(stop.global_stop),
      title: resumeReason || 'Снять STOP звонков (глобальный STOP компьютера не затрагивается)' });
    return panel('Звонок', h('div.stack.sm',
      h('div.row', dial, hangup, stopBtn, resume),
      reason ? h('p.small.dim', { 'data-testid': 'dial-reason' }, reason) : h('p.small.dim', 'Готово к звонку.')));
  }

  settingsPanel() {
    const s = this.st.settings || {};
    const patch = async (body, okText) => {
      const res = await this.call('Настройки не сохранены', () => api.raw(`${BASE}/settings`, { method: 'PUT', body }));
      if (res && okText) toastOk(okText);
      await this.reload();
    };
    const max = input({ type: 'number', min: '30', max: '3600', value: String(s.max_call_s ?? 900), class: 'input mono' });
    return panel('Настройки', h('div.stack.sm',
      h('div.row', { 'data-testid': 'calls-enable' },
        toggle(Boolean(s.enabled), (v) => patch({ enabled: v }, v ? 'Звонки разрешены' : 'Звонки выключены'), 'Разрешить звонки'),
        h('span', 'Разрешить звонки')),
      h('div.row',
        toggle(Boolean(s.keep_transcript), (v) => patch({ keep_transcript: v }), 'Хранить текст разговора'),
        h('span', 'Хранить текст разговора (по умолчанию выключено)')),
      h('div.row',
        recordAudioToggle(),
        h('span.dim', 'Записывать звук (пока не реализовано)')),
      h('div.row', field('Максимум, секунд', max), btn('Сохранить', () => patch({ max_call_s: Number(max.value) }, 'Сохранено'),
        { size: 'sm', iconName: 'check' }))));
  }

  /* ------------------------------------------------------------ peer */

  peerPanel() {
    const s = this.st.settings || {};
    const peer = s.peer;
    const busy = Boolean(this.st.call);
    const head = peer
      ? h('p', h('b', peer.label || `#${peer.user_id}`), ` (#${peer.user_id}) — `,
        s.peer_confirmed ? 'подтверждён' : 'НЕ подтверждён: звонок невозможен')
      : h('p.dim', 'Второй аккаунт не выбран.');
    const loadBtn = btn('Показать контакты', async () => {
      const res = await this.call('Контакты не загружены', () => api.raw(`${BASE}/contacts`));
      if (res) { this.contacts = res.contacts || []; this.paint(); }
    }, { size: 'sm', iconName: 'search', disabled: busy, title: busy ? 'Идёт звонок' : 'Запросить список контактов у Telegram' });
    const clearBtn = peer ? btn('Сбросить', async () => {
      await this.call('Не удалось', () => api.raw(`${BASE}/peer`, { method: 'DELETE' }));
      await this.reload();
    }, { size: 'sm', variant: 'ghost', disabled: busy, title: busy ? 'Идёт звонок' : 'Забыть выбранный аккаунт' }) : null;
    const confirmBtn = (peer && !s.peer_confirmed) ? btn('Подтвердить', () => this.confirmPeer(peer), { size: 'sm', variant: 'primary', iconName: 'check' }) : null;
    const list = this.contacts === null ? null
      : (this.contacts.length
        ? h('div.stack.sm', { 'data-testid': 'calls-contacts' }, this.contacts.map((c) => h('div.row',
          h('span', `${c.label || '—'}${c.username ? ' @' + c.username : ''}`), h('div.spacer'),
          btn('Выбрать', () => this.pickPeer(c), { size: 'sm', title: 'Выбрать как единственного собеседника' }))))
        : h('p.dim', 'В контактах нет подходящих пользователей.'));
    return panel('Второй аккаунт (собеседник)', h('div.stack.sm', head,
      h('div.row', loadBtn, confirmBtn, clearBtn), list));
  }

  async pickPeer(c) {
    const res = await this.call('Контакт не выбран', () => api.raw(`${BASE}/peer`, { method: 'POST', body: { user_id: c.user_id } }));
    if (!res) return;
    await this.reload();
    await this.confirmPeer({ user_id: c.user_id, label: c.label });
  }

  async confirmPeer(peer) {
    const ok = await confirmDialog({
      title: 'Разрешить звонки этому аккаунту?', okText: 'Да, только ему',
      text: `Звонки будут идти ТОЛЬКО на «${peer.label || peer.user_id}» (#${peer.user_id}). Другому человеку позвонить нельзя; сменить собеседника можно только здесь, вручную.`,
    });
    if (!ok) return;
    const res = await this.call('Выбор не подтверждён', () => api.raw(`${BASE}/peer/confirm`, { method: 'POST', body: { user_id: peer.user_id } }));
    if (res) toastOk('Собеседник подтверждён');
    await this.reload();
  }

  /* ------------------------------------------------------------ live: latency, log, history */

  paintLive() {
    const st = this.st;
    const lat = latencyOf(this.events);
    const last = st.last_call;
    const log = this.events.length
      ? h('div.log', { 'data-testid': 'calls-log' }, this.events.slice().reverse().map((e) => h('div.log-line',
        h('span.log-ts', fmtClock(e.at * 1000, true)), h('span.log-msg', eventText(e)))))
      : h('div.log-empty', 'Событий пока нет.');
    this.live.replaceChildren(
      panel('Состояние', h('div.stack.sm',
        h('div.row', pill(ACCOUNT_TEXT[(st.account || {}).state] || '—', { tone: (st.account || {}).state === 'ready' ? 'ok' : 'idle' }),
          pill(`процесс звонков: ${(st.worker || {}).running ? 'запущен' : 'не запущен'}`, { tone: (st.worker || {}).running ? 'ok' : 'idle' }),
          pill(`задержка ответа: ${lat === null ? '—' : Math.round(lat) + ' мс'}`, { tone: 'idle' })),
        last ? h('p.small.dim', `Последний звонок: ${OUTCOME_TEXT[last.outcome] || last.outcome || '—'}`) : h('p.small.dim', 'Звонков ещё не было.'))),
      panel('Журнал событий', log));
  }

  /* ------------------------------------------------------------ diagnostics */

  diagnosticsPanel(ready) {
    const out = h('pre.bx-code', { 'data-testid': 'calls-diag', hidden: true });
    const show = (text) => { out.hidden = false; out.textContent = text; };
    const doctor = btn('Диагностика', async () => {
      const res = await this.call('Диагностика не выполнена', () => api.raw(`${BASE}/doctor`));
      if (res) show((res.checks || []).map((c) => `${c.status}  ${c.id}: ${c.message || ''}`).join('\n') || 'Нет данных.');
    }, { size: 'sm', iconName: 'info', title: 'Проверить зависимости, модели и микрофонный тракт' });
    const selftest = btn('Самопроверка', async () => {
      const res = await this.call('Самопроверка не выполнена', () => api.raw(`${BASE}/selftest`, { method: 'POST', body: {} }));
      if (res) show(`${res.status}: ${res.message || ''}`);
    }, { size: 'sm', iconName: 'activity', title: 'Прогнать цепочку на встроенном тесте, без звонка в Telegram' });
    const install = btn('Установить зависимости', async () => {
      const ok = await confirmDialog({ title: 'Установить зависимости звонков?', okText: 'Установить',
        text: 'Будут скачаны закреплённые пакеты с pypi.org и проверены по контрольной сумме.' });
      if (!ok) return;
      const res = await this.call('Установка не выполнена', () => api.raw(`${BASE}/install`, { method: 'POST', body: { confirm: true } }));
      if (res) show(`${res.status}: ${res.message || ''}`);
    }, { size: 'sm', iconName: 'plus', disabled: Boolean(this.st.call), title: this.st.call ? 'Идёт звонок' : 'Скачать нужные пакеты (только по вашей кнопке)' });
    const logout = ready ? btn('Выйти из аккаунта', async () => {
      const ok = await confirmDialog({ title: 'Выйти из аккаунта?', okText: 'Выйти', danger: true,
        text: 'Сессия и выбранный собеседник будут стёрты.' });
      if (!ok) return;
      await this.call('Не удалось выйти', () => api.raw(`${BASE}/logout`, { method: 'POST', body: {} }));
      this.contacts = null;
      await this.reload();
    }, { size: 'sm', variant: 'ghost', iconName: 'logout', disabled: Boolean(this.st.call), title: this.st.call ? 'Идёт звонок' : 'Стереть сессию Telegram' }) : null;
    return panel('Диагностика', h('div.stack.sm', h('div.row', doctor, selftest, install, logout), out));
  }
}

export default TelegramCallsPage;
