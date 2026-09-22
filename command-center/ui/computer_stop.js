/* ============================================================
   computer_stop.js — «СТОП» / «Продолжить» для Computer Use (R6).

   Пока модель управляет рабочим столом (или пока управление остановлено),
   владелец всегда видит плашку с большой кнопкой «СТОП». Нажатие — POST
   /api/computer/stop тем же путём, что и остальной UI (cookie-сессия +
   CSRF-заголовок через api.raw). Когда остановлено — «Продолжить».

   Чего здесь нет и не должно быть: модель этих кнопок нажать не может —
   инструмента для них нет, а без CSRF-токена вкладки сервер откажет.

   Семантика на сервере (bcc/features/tools_computer.py):
     * «Стоп» выигрывает гонки: действие, ждущее очереди, не выполнится;
     * «Продолжить» не возвращает старое наблюдение — модель обязана заново
       посмотреть на экран, прежде чем что-то делать;
     * «Стоп» переживает перезапуск Command Center.
   ============================================================ */

import { api } from './api.js';

const POLL_MS = 5000;

let root = null;
let state = { stopped: false, active: false, busy: false, available: false, stopped_at: null };
let pending = false;
let pendingKind = '';
let timer = null;
let lastError = '';

function styles() {
  if (document.getElementById('cu-stop-style')) return;
  const css = document.createElement('style');
  css.id = 'cu-stop-style';
  css.textContent = `
  .cu-stop{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:1000;
    display:flex;align-items:center;gap:12px;max-width:calc(100vw - 32px);
    padding:10px 12px 10px 16px;border-radius:14px;
    font:600 13px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    color:#fff;background:#1d1f27;border:2px solid #e5484d;box-shadow:0 8px 30px rgba(0,0,0,.45)}
  .cu-stop[hidden]{display:none}
  .cu-stop[data-state="stopped"]{border-color:#f5c451}
  .cu-stop-dot{width:10px;height:10px;border-radius:50%;background:#e5484d;flex:none;
    animation:cu-pulse 1.2s ease-in-out infinite}
  .cu-stop[data-state="stopped"] .cu-stop-dot{background:#f5c451;animation:none}
  @keyframes cu-pulse{50%{opacity:.35}}
  @media (prefers-reduced-motion:reduce){.cu-stop-dot{animation:none}}
  .cu-stop-text{display:flex;flex-direction:column;min-width:0}
  .cu-stop-note{font-weight:500;font-size:12px;opacity:.8}
  .cu-stop-err{font-weight:600;font-size:12px;color:#ff9b9b}
  .cu-stop-err:empty{display:none}
  .cu-stop-btn{appearance:none;border:0;border-radius:10px;cursor:pointer;flex:none;
    font:800 15px/1 inherit;letter-spacing:.06em;padding:12px 20px;min-height:44px}
  .cu-stop-btn:focus-visible{outline:3px solid #fff;outline-offset:2px}
  .cu-stop-btn[aria-disabled="true"]{opacity:.6;cursor:progress}
  .cu-stop-btn[hidden]{display:none}
  #cu-stop-btn{background:#e5484d;color:#fff}
  #cu-stop-btn:hover{background:#ff5c61}
  #cu-resume-btn{background:#f5c451;color:#15120a}
  @media (max-width:640px){.cu-stop{left:16px;right:16px;transform:none;flex-wrap:wrap}
    .cu-stop-btn{flex:1}}`;
  document.head.appendChild(css);
}

function fmtTime(ts) {
  if (!ts) return '';
  try { return new Date(Number(ts) * 1000).toLocaleTimeString(); } catch { return ''; }
}

function build() {
  styles();
  root = document.createElement('div');
  root.id = 'cu-stop-panel';
  root.className = 'cu-stop';
  root.hidden = true;
  root.dataset.state = 'hidden';
  root.setAttribute('role', 'region');
  root.setAttribute('aria-label', 'Управление компьютером');
  root.innerHTML = `
    <span class="cu-stop-dot" aria-hidden="true"></span>
    <span class="cu-stop-text">
      <span id="cu-stop-status" role="status" aria-live="assertive"></span>
      <span class="cu-stop-note" id="cu-stop-note"></span>
      <span class="cu-stop-err" id="cu-stop-err" role="alert"></span>
    </span>
    <button type="button" class="cu-stop-btn" id="cu-stop-btn"
      aria-label="СТОП: немедленно остановить управление компьютером">СТОП</button>
    <button type="button" class="cu-stop-btn" id="cu-resume-btn" hidden
      aria-label="Продолжить управление компьютером">Продолжить</button>`;
  document.body.appendChild(root);
  root.querySelector('#cu-stop-btn').addEventListener('click', () => send('stop'));
  root.querySelector('#cu-resume-btn').addEventListener('click', () => send('resume'));
}

function render() {
  if (!root) return;
  const focused = document.activeElement;
  const show = Boolean(state.stopped || state.active || state.busy || pending);
  const wasHidden = root.hidden;
  root.hidden = !show;
  root.dataset.state = !show ? 'hidden' : state.stopped ? 'stopped' : 'active';
  const status = root.querySelector('#cu-stop-status');
  const note = root.querySelector('#cu-stop-note');
  const stopBtn = root.querySelector('#cu-stop-btn');
  const resumeBtn = root.querySelector('#cu-resume-btn');
  if (state.stopped) {
    status.textContent = `Управление компьютером ОСТАНОВЛЕНО${state.stopped_at ? ` в ${fmtTime(state.stopped_at)}` : ''}`;
    note.textContent = 'После «Продолжить» модель сначала заново посмотрит на экран.';
  } else {
    status.textContent = state.busy ? 'Модель выполняет действие на компьютере' : 'Модель управляет компьютером';
    note.textContent = 'СТОП сработает сразу: следующее действие не начнётся.';
  }
  // «Остановлено» показываем только по ответу сервера — не раньше.
  if (pendingKind === 'stop') status.textContent = 'Останавливаю…';
  if (pendingKind === 'resume') status.textContent = 'Снимаю остановку…';
  stopBtn.hidden = Boolean(state.stopped);
  resumeBtn.hidden = !state.stopped;
  // Не disabled: отключённая кнопка теряет фокус, и владелец с клавиатуры
  // остался бы ни с чем. Повторное нажатие гасит сам send().
  stopBtn.setAttribute('aria-disabled', String(pending));
  resumeBtn.setAttribute('aria-disabled', String(pending));
  root.setAttribute('aria-busy', String(pending));
  root.querySelector('#cu-stop-err').textContent = lastError;
  // Кнопка, в которой был фокус, исчезла — фокус на ту, что её заменила,
  // чтобы владелец с клавиатуры не терял управление.
  if ((focused === stopBtn || focused === resumeBtn) && focused.hidden) {
    (state.stopped ? resumeBtn : stopBtn).focus();
  }
  if (wasHidden && show) root.dispatchEvent(new CustomEvent('cu-stop:shown'));
}

async function refresh() {
  try {
    const s = await api.raw('/api/computer/status');
    state = { ...state, ...s };
  } catch { /* нет сессии/сервера — плашку не трогаем, повторим */ }
  render();
}

async function send(kind) {
  if (pending) return;
  pending = true;
  lastError = '';
  pendingKind = kind;
  render();
  try {
    const res = await api.raw(`/api/computer/${kind}`, { method: 'POST' });
    state = { ...state, stopped: Boolean(res && res.stopped) };
  } catch (err) {
    lastError = `${kind === 'stop' ? 'СТОП' : 'Продолжить'} не прошёл: ${err && err.message ? err.message : err}`;
  } finally {
    pending = false;
    pendingKind = '';
  }
  await refresh();
}

function schedule() {
  clearInterval(timer);
  timer = setInterval(() => { if (document.visibilityState !== 'hidden') refresh(); }, POLL_MS);
}

/** Монтирует плашку. bus — EventStream приложения (события computer.*). */
export function mountComputerStop({ bus } = {}) {
  if (root) return { refresh };
  build();
  if (bus && typeof bus.subscribe === 'function') {
    bus.subscribe((ev) => {
      const kind = String((ev && ev.kind) || '');
      if (kind.startsWith('computer.')) refresh();
    });
  }
  schedule();
  refresh();
  return { refresh };
}
