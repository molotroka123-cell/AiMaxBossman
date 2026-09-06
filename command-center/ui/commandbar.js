/* ============================================================
   commandbar.js — одно поле ввода, из которого доступна любая возможность
   системы, и список фоновых задач под ним.

   Почему панель устроена именно так:

   * подсказки берутся из каталога сервера (GET /api/command-bar), а каталог
     собран из настоящих маршрутов приложения. Своего списка возможностей у
     UI нет и быть не должно: он бы устарел молча;

   * между вводом и выполнением стоит ОТДЕЛЬНЫЙ шаг «разобрать»
     (POST /api/command-bar/parse). Владелец сначала видит, ЧТО будет сделано,
     и только потом нажимает «Выполнить». Необратимое действие требует
     сознательной отметки — кнопка до неё не работает;

   * значения параметров сервер обратно не отдаёт (в них бывают ключи), и
     панель их не восстанавливает: показывается либо то, что сервер назвал
     показываемым, либо отпечаток значения;

   * задачи опрашиваются с сервера, а не хранятся во вкладке. Уход со
     страницы и возврат на неё ничего не теряют — это свойство сервера, и
     панель обязана ему не мешать.

   Панель монтируется сама (как thinking.js) и ничего не знает про роутер:
   подключение — одна строка в app.js.
   ============================================================ */
import { api as defaultApi } from './api.js';
import { h, append, clear } from './components.js';

const POLL_MS = 2000;
const MAX_HINTS = 8;
const STYLE_ID = 'bx-cmd-style';

/* Стили держим здесь: панель — самостоятельный модуль, общий style.css
   принадлежит другим. Вставляются один раз на страницу. */
const CSS = `
.bx-cmd{position:fixed;left:50%;transform:translateX(-50%);bottom:16px;z-index:60;
  width:min(760px,calc(100vw - 32px));background:var(--panel,#12151c);
  border:1px solid var(--line,#2a2f3a);border-radius:12px;padding:10px 12px;
  box-shadow:0 10px 30px rgba(0,0,0,.35);font-size:13px;color:var(--fg,#e6e9ef)}
.bx-cmd-form{display:flex;gap:8px}
.bx-cmd-form input{flex:1;min-width:0;padding:8px 10px;border-radius:8px;
  border:1px solid var(--line,#2a2f3a);background:var(--bg,#0d1016);
  color:inherit;font:inherit}
.bx-cmd button{padding:8px 12px;border-radius:8px;border:1px solid var(--line,#2a2f3a);
  background:var(--bg,#0d1016);color:inherit;font:inherit;cursor:pointer}
.bx-cmd button[disabled]{opacity:.45;cursor:not-allowed}
.bx-cmd-hints{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.bx-cmd-chip{padding:2px 8px;border-radius:999px;border:1px solid var(--line,#2a2f3a);
  background:transparent;font-size:12px;cursor:pointer}
.bx-cmd-note{margin-top:6px;opacity:.8}
.bx-cmd-intent{margin-top:8px;border-top:1px solid var(--line,#2a2f3a);padding-top:8px}
.bx-cmd-sum{font-weight:600;margin-bottom:6px}
.bx-cmd-args{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:6px 0}
.bx-cmd-args span{opacity:.75}
.bx-cmd-warn{color:var(--err,#ff6b6b)}
.bx-cmd-ok{color:var(--ok,#4ade80)}
.bx-cmd-confirm{display:flex;align-items:center;gap:6px;margin:6px 0}
.bx-cmd-tasks{margin-top:8px;max-height:190px;overflow:auto}
.bx-cmd-task{display:flex;align-items:center;gap:8px;padding:3px 0}
.bx-cmd-state{padding:1px 7px;border-radius:999px;font-size:11px;
  border:1px solid var(--line,#2a2f3a)}
.bx-cmd-task[data-state="running"] .bx-cmd-state{border-color:var(--accent,#5b8cff)}
.bx-cmd-task[data-state="failed"] .bx-cmd-state{border-color:var(--err,#ff6b6b)}
.bx-cmd-task[data-state="done"] .bx-cmd-state{border-color:var(--ok,#4ade80)}
.bx-cmd-task b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
`;

const STATE_WORD = {
  queued: 'в очереди', running: 'идёт', done: 'готово',
  failed: 'отказ', stopped: 'остановлена',
};

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}

/** Значение параметра для показа. Скрытое сервером НЕ восстанавливаем. */
function argText(arg) {
  if (arg.shown) return `${JSON.stringify(arg.value)} (${arg.kind})`;
  return `значение скрыто · ${arg.fingerprint}`;
}

// Уже смонтированная панель. Второй вызов обязан вернуть её, а не построить
// вторую: у панели есть элементы с id, а два узла с одним id — это ошибка
// разметки, из-за которой querySelector отдаёт первую панель, а обработчики
// висят на второй. Владельцу это выглядит как «поле не реагирует».
let mounted = null;

export function mountCommandBar({ api = defaultApi, mount = document.body,
                                  poll = POLL_MS } = {}) {
  if (mounted && mounted.pane && mounted.pane.isConnected) return mounted;
  injectStyle();

  let catalog = [];        // возможности из каталога сервера
  let aliases = {};        // человеческие слова → возможность
  let intent = null;       // разобранное намерение (ещё не выполненное)
  let intentId = '';
  let enabled = false;
  let timer = null;

  const input = h('input', {
    id: 'cmdbar-input', type: 'text', autocomplete: 'off', spellcheck: 'false',
    'aria-label': 'Команда',
    placeholder: 'Команда: остановить 12, задачи, agents.delete 3',
  });
  const parseBtn = h('button', { id: 'cmdbar-parse', type: 'submit' }, 'Разобрать');
  const form = h('form.bx-cmd-form', { id: 'cmdbar-form' }, input, parseBtn);
  const hints = h('div.bx-cmd-hints', { id: 'cmdbar-hints' });
  const note = h('div.bx-cmd-note', { id: 'cmdbar-note' });
  const intentBox = h('div.bx-cmd-intent', { id: 'cmdbar-intent', hidden: true });
  const tasksBox = h('div.bx-cmd-tasks', { id: 'cmdbar-tasks' });
  const pane = h('section.bx-cmd', { id: 'cmdbar', 'aria-label': 'Командная строка' },
    form, hints, note, intentBox, tasksBox);
  mount.appendChild(pane);

  const call = (path, options) => api.raw(path, options);

  /* ---------------- каталог и подсказки ---------------- */

  async function loadCatalog() {
    const data = await call('/api/command-bar');
    enabled = Boolean(data && data.enabled);
    catalog = (data && data.capabilities) || [];
    aliases = (data && data.aliases) || {};
    form.hidden = !enabled;
    pane.hidden = !enabled;
    if (!enabled) {
      note.textContent = 'Командная строка выключена.';
      return;
    }
    note.textContent = `Возможностей: ${catalog.length}. Сначала разбор, потом выполнение.`;
    renderHints('');
  }

  /** Подсказки — префиксом по каталогу и псевдонимам. Никаких догадок: то же
      правило, по которому команду опознаёт сервер. */
  function renderHints(prefix) {
    clear(hints);
    if (!enabled) return;
    const word = String(prefix || '').trim().split(/\s+/)[0].toLowerCase();
    const pool = [...Object.keys(aliases), ...catalog.map((c) => c.id)];
    const list = (word ? pool.filter((name) => name.startsWith(word)) : Object.keys(aliases))
      .slice(0, MAX_HINTS);
    append(hints, list.map((name) => h('button.bx-cmd-chip', {
      type: 'button',
      onclick: () => { input.value = `${name} `; input.focus(); renderHints(name); },
    }, name)));
  }

  /* ---------------- намерение ---------------- */

  function renderIntent(data) {
    clear(intentBox);
    intentBox.hidden = false;
    if (!data || !data.understood) {
      intent = null; intentId = '';
      append(intentBox, h('div.bx-cmd-sum.bx-cmd-warn',
        (data && data.message) || 'Команда не опознана'));
      append(intentBox, h('div', { id: 'cmdbar-suggestions' },
        ((data && data.suggestions) || []).map((name) => h('button.bx-cmd-chip', {
          type: 'button', onclick: () => { input.value = `${name} `; input.focus(); },
        }, name))));
      return;
    }
    intent = data.intent;
    intentId = data.intent_id || '';
    const needsConfirm = Boolean(intent.requires_confirmation);
    const runBtn = h('button', {
      id: 'cmdbar-run', type: 'button', disabled: needsConfirm || !intent.runnable,
      onclick: () => execute(),
    }, 'Выполнить');

    append(intentBox, h('div.bx-cmd-sum', { id: 'cmdbar-summary' }, intent.summary));
    append(intentBox, h('div', { id: 'cmdbar-capability' },
      `${intent.capability.method} ${intent.capability.path} · ${intent.capability.id}`));
    append(intentBox, h('div.bx-cmd-args', { id: 'cmdbar-args' },
      (intent.arguments || []).flatMap((arg) => [
        h('span', `${arg.name} (${arg.where})`), h('b', argText(arg))])));
    append(intentBox, h('div', { id: 'cmdbar-reversible',
      class: needsConfirm ? 'bx-cmd-warn' : 'bx-cmd-ok' },
      needsConfirm ? `НЕОБРАТИМО: ${intent.reversible_why || ''}`
        : `Обратимо: ${intent.reversible_why || 'состояние можно вернуть'}`));
    if (intent.preview && intent.preview.available) {
      append(intentBox, h('div', { id: 'cmdbar-preview' },
        `Изменится строк: ${intent.preview.change_count}`));
    }
    if (!intent.runnable) {
      append(intentBox, h('div.bx-cmd-warn', { id: 'cmdbar-blocked' }, intent.blocked_reason));
    } else if (needsConfirm) {
      /* Подтверждение — отдельное действие владельца, а не значение по
         умолчанию: до отметки кнопка выполнения не работает. */
      const box = h('input', { id: 'cmdbar-confirm', type: 'checkbox',
        onchange: (e) => { runBtn.disabled = !e.target.checked; } });
      append(intentBox, h('label.bx-cmd-confirm', box,
        h('span', 'Понимаю, что действие необратимо — выполнить')));
    }
    append(intentBox, runBtn);
  }

  async function doParse() {
    const text = input.value.trim();
    if (!text) return;
    try {
      renderIntent(await call('/api/command-bar/parse', { method: 'POST', body: { text } }));
    } catch (err) {
      intent = null; intentId = '';
      intentBox.hidden = false;
      clear(intentBox);
      append(intentBox, h('div.bx-cmd-sum.bx-cmd-warn', err && err.message
        ? String(err.message) : 'Разбор не удался'));
    }
  }

  async function execute() {
    if (!intent || !intentId || !intent.runnable) return;
    const confirmBox = intentBox.querySelector('#cmdbar-confirm');
    const confirm = Boolean(confirmBox && confirmBox.checked);
    if (intent.requires_confirmation && !confirm) return;   // вторая линия к disabled
    try {
      await call('/api/command-bar/run',
        { method: 'POST', body: { intent_id: intentId, confirm } });
      input.value = '';
      intent = null; intentId = '';
      intentBox.hidden = true;
      await refreshTasks();
    } catch (err) {
      append(intentBox, h('div.bx-cmd-warn', { id: 'cmdbar-run-error' },
        err && err.message ? String(err.message) : 'Выполнить не удалось'));
    }
  }

  /* ---------------- фоновые задачи ---------------- */

  function renderTasks(rows) {
    clear(tasksBox);
    if (!rows.length) {
      append(tasksBox, h('div.bx-cmd-note', 'Фоновых задач нет.'));
      return;
    }
    append(tasksBox, rows.map((task) => h('div.bx-cmd-task',
      { 'data-id': task.id, 'data-state': task.state },
      h('span.bx-cmd-state', STATE_WORD[task.state] || task.state),
      h('b', task.capability),
      h('span', task.error ? String(task.error).slice(0, 80) : ''),
      (task.state === 'queued' || task.state === 'running')
        ? h('button.bx-cmd-chip', { type: 'button', 'data-stop': task.id,
            onclick: () => stopTask(task.id) }, 'Стоп')
        : null)));
  }

  async function refreshTasks() {
    if (!enabled) return;
    try {
      const data = await call('/api/command-bar/tasks');
      renderTasks((data && data.tasks) || []);
    } catch { /* сервер недоступен — панель молчит, а не мигает ошибкой */ }
  }

  async function stopTask(id) {
    try {
      await call(`/api/command-bar/tasks/${encodeURIComponent(id)}/stop`, { method: 'POST' });
    } catch { /* уже завершилась — обновление ниже покажет её настоящее состояние */ }
    await refreshTasks();
  }

  /* ---------------- события ---------------- */

  form.addEventListener('submit', (e) => { e.preventDefault(); doParse(); });
  input.addEventListener('input', () => renderHints(input.value));

  loadCatalog().then(refreshTasks).catch(() => {
    note.textContent = 'Командная строка недоступна.';
  });
  timer = setInterval(refreshTasks, poll);

  mounted = {
    pane,
    isEnabled: () => enabled,
    parse: doParse,
    execute,
    refresh: refreshTasks,
    intent: () => intent,
    destroy: () => { clearInterval(timer); pane.remove(); mounted = null; },
  };
  return mounted;
}
