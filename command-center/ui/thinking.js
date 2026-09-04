/* Панель «Процесс работы» (UX 2.0): что система делает ПРЯМО СЕЙЧАС —
   состояние задач, шаг, модель, инструменты, ожидания, повторы, ошибки и
   прошедшее время. Источник — та же шина событий, что и остальной дашборд
   (task.*, tool.called, router.fallback, run.log, hook.*, approval.*, …).
   Никакого скрытого «хода мыслей» модели здесь нет и быть не может: события
   уже очищены от секретов сервером и содержат только типизированные факты. */
import { h, append, clear, icon, fmtDuration, fmtClock, parseTs } from './components.js';
import { pill, statusText } from './pages/_ui.js';

const MAX_EVENTS = 200;
const STORE_KEY = 'bx.think.open';

const KIND = {
  'task.queued': ['в очереди', 'idle'],
  'task.started': ['запущена', 'ok'],
  'task.progress': ['шаг', 'accent'],
  'task.paused': ['пауза', 'warn'],
  'task.stopped': ['остановлена', 'idle'],
  'tool.called': ['инструмент', 'accent'],
  'tool.denied': ['инструмент отклонён', 'err'],
  'router.fallback': ['повтор на другой модели', 'warn'],
  'model.status': ['модель', 'warn'],
  'run.log': ['лог', 'idle'],
  'approval.created': ['ждёт решения владельца', 'warn'],
  'evaluation.completed': ['проверка результата', 'accent'],
  'checkpoint.created': ['контрольная точка', 'idle'],
  'hook.degraded': ['телеметрия деградировала', 'warn'],
  'hook.critical_failure': ['критический гейт упал', 'err'],
  'hook.escalation_failed': ['эскалация не удалась', 'err'],
  'worker.error': ['ошибка воркера', 'err'],
  'cache.observation': ['кэш', 'idle'],
};

function describe(ev) {
  const kind = String(ev.kind || '');
  const [label, tone] = KIND[kind] || [kind, 'idle'];
  const d = ev;
  let text = '';
  switch (kind) {
    case 'task.progress':
      text = d.waiting_approval ? `ждёт решения владельца${d.tool ? ` (${d.tool})` : ''}${d.gate_hook_failed ? `, гейт ${d.gate_hook_failed} упал` : ''}`
        : `шаг ${d.step ?? '?'}${d.max_steps ? ` из ${d.max_steps}` : ''}${d.model ? ` · ${d.model}` : ''}${Array.isArray(d.tool_calls) && d.tool_calls.length ? ` · инструменты: ${d.tool_calls.join(', ')}` : ''}`;
      break;
    case 'tool.called':
      text = `${d.tool || '?'} ${d.ok === false ? 'с ошибкой' : 'ок'}${Number.isFinite(Number(d.duration_ms)) ? ` · ${fmtDuration(Number(d.duration_ms))}` : ''}`;
      break;
    case 'router.fallback': text = `${d.model_id ?? ''} → запасная модель${d.reason ? `: ${String(d.reason).slice(0, 120)}` : ''}`; break;
    case 'model.status': text = `${d.alias || d.id || ''}: ${statusText(d.status).word}${d.detail ? ` · ${String(d.detail).slice(0, 120)}` : ''}`; break;
    case 'run.log': text = `${d.level ? `[${d.level}] ` : ''}${String(d.message || '').slice(0, 200)}`; break;
    case 'evaluation.completed': text = `${d.verdict || ''}${d.reasons ? ` · ${String(d.reasons).slice(0, 160)}` : ''}`; break;
    case 'worker.error': text = String(d.message || '').slice(0, 200); break;
    case 'hook.degraded': case 'hook.critical_failure': case 'hook.escalation_failed':
      text = `${d.hook || ''} ${d.fn ? `(${d.fn})` : ''} ${d.error || ''}${d.reason ? `: ${String(d.reason).slice(0, 120)}` : ''}`; break;
    case 'cache.observation': text = `${d.state || ''}${d.model ? ` · ${d.model}` : ''}`; break;
    default:
      text = [d.task_id != null ? `задача ${d.task_id}` : '', d.run_id != null ? `run ${d.run_id}` : ''].filter(Boolean).join(' · ');
  }
  return { label, tone, text };
}

export function mountThinking({ bus, api, button }) {
  const runs = new Map();
  const events = [];
  let conn = 'idle';
  let open = false;
  try { open = localStorage.getItem(STORE_KEY) === '1'; } catch { /* приватный режим */ }

  const nowBox = h('div.bx-think-now');
  const logBox = h('div.bx-think-log', { role: 'log', 'aria-live': 'polite' });
  const connPill = h('span');
  const pane = h('aside.bx-think', { id: 'think-pane', 'aria-label': 'Процесс работы', hidden: !open },
    h('div.bx-think-head',
      h('div.bx-think-title', icon('system', 16), h('b', 'Процесс работы'), connPill),
      h('div.bx-think-actions',
        h('button.icon-btn', { type: 'button', id: 'think-clear', title: 'Очистить ленту', onclick: () => { events.length = 0; renderLog(); } }, icon('trash', 15)),
        h('button.icon-btn', { type: 'button', id: 'think-close', title: 'Закрыть', 'aria-label': 'Закрыть', onclick: () => setOpen(false) }, icon('close', 15)))),
    h('div.bx-think-sub', 'Только факты исполнения: состояние, шаг, модель, инструменты, ожидания, повторы, ошибки. Скрытых рассуждений модели здесь нет.'),
    h('div.bx-think-section', 'Сейчас'), nowBox,
    h('div.bx-think-section', 'Лента'), logBox);
  document.body.appendChild(pane);

  function setOpen(v) {
    open = !!v;
    pane.hidden = !open;
    document.body.classList.toggle('bx-think-open', open);
    if (button) button.setAttribute('aria-pressed', String(open));
    try { localStorage.setItem(STORE_KEY, open ? '1' : '0'); } catch { /* ignore */ }
    if (open) seed();
  }

  function renderConn() {
    const map = { open: ['live', 'ok'], connecting: ['подключение…', 'warn'], closed: ['нет соединения', 'err'], idle: ['…', 'idle'] };
    const [word, tone] = map[conn] || map.idle;
    connPill.replaceWith(pill(word, { tone, live: conn === 'open' }));
  }

  function runCard(r) {
    const elapsed = r.finished_at ? fmtDuration(r.finished_at - r.started_at) : fmtDuration(Date.now() - r.started_at);
    const st = statusText(r.state);
    return h('div.bx-think-card', { 'data-run': String(r.run_id), 'data-state': r.state },
      h('div.bx-think-card-head',
        h('b', r.title || `Задача ${r.task_id}`),
        pill(st.word, { tone: st.tone, live: !!st.live })),
      h('div.bx-think-grid',
        h('span', 'прошло'), h('b.bx-think-elapsed', { 'data-since': String(r.started_at), 'data-done': r.finished_at ? '1' : '' }, elapsed),
        h('span', 'шаг'), h('b', r.max_steps ? `${r.step ?? 0} из ${r.max_steps}` : String(r.step ?? '—')),
        h('span', 'модель'), h('b', r.model || '—'),
        h('span', 'инструмент'), h('b', r.last_tool ? `${r.last_tool}${r.last_tool_ms != null ? ` · ${fmtDuration(r.last_tool_ms)}` : ''}` : '—'),
        h('span', 'ожидание'), h('b', r.waiting ? (r.waiting_for ? `решение владельца (${r.waiting_for})` : 'решение владельца') : 'нет'),
        h('span', 'повторы'), h('b', String(r.retries)),
        h('span', 'ошибки'), h('b', String(r.errors))),
      r.note ? h('div.bx-think-note', r.note) : null);
  }

  function renderNow() {
    clear(nowBox);
    const live = [...runs.values()].sort((a, b) => b.updated - a.updated).slice(0, 8);
    if (!live.length) { append(nowBox, h('div.bx-think-empty', 'Активных задач нет. Запустите задачу — здесь появится её ход.')); return; }
    append(nowBox, live.map(runCard));
  }

  function renderLog() {
    clear(logBox);
    if (!events.length) { append(logBox, h('div.bx-think-empty', 'Событий пока нет.')); return; }
    append(logBox, events.slice(0, 80).map((ev) => {
      const { label, tone, text } = describe(ev);
      return h('div.bx-think-row', { 'data-kind': ev.kind },
        h('span.bx-think-time', fmtClock(parseTs(ev.ts) || new Date(), true)),
        pill(label, { tone }),
        h('span.bx-think-text', text));
    }));
  }

  function tick() {
    if (!open) return;
    for (const node of pane.querySelectorAll('.bx-think-elapsed')) {
      if (node.dataset.done) continue;
      node.textContent = fmtDuration(Date.now() - Number(node.dataset.since));
    }
  }
  setInterval(tick, 1000);

  function run(ev) {
    const id = ev.run_id != null ? Number(ev.run_id) : (ev.task_id != null ? `t${ev.task_id}` : null);
    if (id === null) return null;
    let r = runs.get(id);
    if (!r) {
      r = { run_id: id, task_id: ev.task_id, state: 'running', step: null, max_steps: null, model: '', last_tool: '', last_tool_ms: null,
            waiting: false, waiting_for: '', retries: 0, errors: 0, started_at: (parseTs(ev.ts) || new Date()).getTime(), finished_at: null, updated: Date.now(), note: '' };
      runs.set(id, r);
    }
    r.updated = Date.now();
    return r;
  }

  /* Одно и то же событие приходит двумя путями: живьём по шине и потом (или
     раньше) в ответе /api/activity. Без сверки оно попадало в ленту дважды, а
     его факты применялись дважды — «повторы» и «ошибки» на карточке показывали
     больше, чем случилось на самом деле. Отпечаток берётся из тех полей, что
     одинаковы у обоих путей. */
  const seen = new Set();
  let seeded = false;

  function mark(ev) {
    // Время нормализуем: по шине оно приходит строкой, из истории — тем, во
    // что его превратил сервер. Сравнивать надо момент, а не запись о нём.
    const at = parseTs(ev.ts);
    return [ev.kind, at ? at.getTime() : String(ev.ts ?? ''), ev.run_id ?? '',
            ev.task_id ?? '', ev.step ?? '', ev.tool ?? '', ev.message ?? ''].join('|');
  }

  function remember(ev) {
    const key = mark(ev);
    if (seen.has(key)) return false;
    seen.add(key);
    // Множество не должно расти вечно: панель живёт часами.
    if (seen.size > MAX_EVENTS * 2) {
      seen.clear();
      for (const e of events) seen.add(mark(e));
    }
    return true;
  }

  function handle(ev) {
    const kind = String(ev.kind || '');
    if (kind.startsWith('ws.')) { conn = kind.slice(3); renderConn(); return; }
    if (kind === 'system.metrics' || kind === 'hello') return;
    if (!remember(ev)) return;          // уже учтено из истории — не считаем второй раз
    events.unshift(ev);
    if (events.length > MAX_EVENTS) events.length = MAX_EVENTS;
    applyFacts(ev);
    if (open) scheduleRender();
  }

  /** Обновляет карточки прогонов по одному событию (без записи в ленту). */
  function applyFacts(ev) {
    const kind = String(ev.kind || '');
    const r = (kind.startsWith('task.') || kind.startsWith('tool.') || kind === 'router.fallback' || kind === 'run.log'
      || kind.startsWith('hook.') || kind === 'evaluation.completed' || kind === 'checkpoint.created') ? run(ev) : null;
    if (r) {
      if (kind === 'task.started') { r.state = 'running'; r.waiting = false; }
      if (kind === 'task.queued') r.state = 'queued';
      if (kind === 'task.paused') r.state = 'paused';
      if (kind === 'task.stopped') { r.state = 'stopped'; r.finished_at = Date.now(); }
      if (kind === 'task.progress') {
        if (ev.step != null) r.step = ev.step;
        if (ev.max_steps != null) r.max_steps = ev.max_steps;
        if (ev.model) r.model = ev.model;
        r.waiting = !!ev.waiting_approval; r.waiting_for = ev.tool || (ev.gate_hook_failed ? `гейт ${ev.gate_hook_failed}` : '');
        r.state = r.waiting ? 'waiting_approval' : 'running';
      }
      if (kind === 'tool.called') { r.last_tool = ev.tool || ''; r.last_tool_ms = Number.isFinite(Number(ev.duration_ms)) ? Number(ev.duration_ms) : null; if (ev.ok === false) r.errors += 1; }
      if (kind === 'tool.denied') r.errors += 1;
      if (kind === 'router.fallback') r.retries += 1;
      if (kind.startsWith('hook.')) { r.errors += 1; r.note = describe(ev).text; }
      if (kind === 'run.log' && ev.level === 'error') r.errors += 1;
      if (kind === 'evaluation.completed') { r.note = `проверка: ${ev.verdict || ''}`; if (ev.verdict === 'PASS') { r.state = 'completed'; r.finished_at = Date.now(); } }
    }
  }

  /* Поток событий бывает взрывным (десятки в секунду). Полная перерисовка на
     каждое событие — это и есть «панель тормозит через час работы»: рисуем не
     чаще одного кадра, независимо от плотности потока. */
  let renderQueued = false;
  let renderCount = 0;
  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    const run = () => { renderQueued = false; renderCount += 1; renderNow(); renderLog(); };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
    else setTimeout(run, 16);
  }

  async function seed() {
    try {
      const tasks = await api.tasks('running,queued,paused');
      for (const t of (Array.isArray(tasks) ? tasks : (tasks && tasks.items) || [])) {
        const lr = t.last_run || {};
        const id = lr.id != null ? Number(lr.id) : `t${t.id}`;
        if (!runs.has(id)) {
          const started = parseTs(lr.started_at || t.updated_at || t.created_at) || new Date();
          // Поля прогона приходят из /api/tasks: модель в `model_alias`, шаг — в checkpoint.
          const cp = (lr.checkpoint && typeof lr.checkpoint === 'object') ? lr.checkpoint : {};
          runs.set(id, { run_id: id, task_id: t.id, title: t.title, state: String(t.status || 'running'),
            step: cp.step ?? lr.step ?? null, max_steps: lr.max_steps ?? t.max_steps ?? null,
            model: lr.model_alias || lr.model || '', last_tool: '', last_tool_ms: null,
            waiting: String(t.status) === 'waiting_approval', waiting_for: '', retries: 0, errors: 0,
            started_at: started.getTime(), finished_at: null, updated: Date.now(), note: '' });
        }
      }
    } catch { /* без сервера — просто пусто */ }
    try {
      // История тянется один раз за сессию панели. Условие «лента пуста» тут
      // не годится: владелец мог очистить её сам — и получил бы её обратно при
      // следующем открытии, то есть кнопка «Очистить» ничего бы не значила.
      if (!seeded) {
        seeded = true;
        const recent = await api.activity();
        const rows = (Array.isArray(recent) ? recent : (recent && recent.items) || []).slice(0, 60);
        // Пока шёл запрос, живые события могли уже прийти и уже быть учтёнными.
        // Применять факты ко всей ленте нельзя: их применили бы повторно.
        const older = [];
        for (const row of rows) {
          const data = row.data && typeof row.data === 'object' ? row.data : {};
          const ev = { ...data, kind: row.kind, ts: row.ts };
          if (remember(ev)) older.push(ev);
        }
        events.push(...older);
        // Карточки должны согласоваться с лентой: факты из уже случившихся событий
        // применяем от старых к новым (сами события в ленту повторно не кладём).
        for (const ev of [...older].reverse()) applyFacts(ev);
      }
    } catch { /* лента не критична */ }
    renderNow(); renderLog();
  }

  bus.subscribe(handle);
  if (button) button.addEventListener('click', () => setOpen(!open));
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === '.') { e.preventDefault(); setOpen(!open); }
  });
  renderConn(); renderNow(); renderLog();
  if (open) setOpen(true);
  return { open: () => setOpen(true), close: () => setOpen(false), isOpen: () => open, runs, events,
    stats: () => ({ events: events.length, runs: runs.size, renders: renderCount, maxEvents: MAX_EVENTS,
                    rows: logBox.childElementCount, cards: nowBox.childElementCount }) };
}
