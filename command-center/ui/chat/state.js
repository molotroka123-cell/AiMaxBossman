/* ============================================================
   chat/state.js — состояние одного хода чата из событий задачи.

   Чистая логика без DOM (проверяется в node). Источник — ТОЛЬКО события
   сервера: SSE /api/events/stream и история /api/tasks/{id}/events. Здесь
   ничего не выдумывается: нет события — нет карточки.

   Правила отрисовки ответа (ux-shell.md §3):
   - run.answer_delta {run_id, step, attempt, idx, text} дописывается в
     шаг (run_id, step) по порядку idx;
   - run.answer_reset стирает показанный текст шага; поздние дельты старой
     попытки после этого игнорируются, но повтор шага движком (новый поток:
     attempt снова 0, idx с 0) принимается с первого куска;
   - run.assistant_message {streamed:false} — текст шага целиком
     (провайдер не стримил); при streamed:true остаётся живой текст.

   Скрытые рассуждения модели (run.reasoning_delta) НЕ сохраняются и не
   показываются: событие отбрасывается, запоминается только счётчик.

   Длительность хода — по времени сервера (`ts` событий task.started и
   task.completed/…), а не по часам окна: история и повтор после F5 иначе
   показывали бы «0 мс» или время от переподключения.
   ============================================================ */

export const TERMINAL_KINDS = new Set(['task.completed', 'task.failed', 'task.stopped', 'task.blocked']);
export const TERMINAL_STATUSES = new Set(['completed', 'failed', 'stopped', 'blocked', 'cancelled', 'missing']);
/** Виды событий, которые чат не показывает никогда. */
export const HIDDEN_KINDS = new Set(['run.reasoning_delta']);

const MAX_SOURCES = 40;
const MAX_NOTES = 60;

export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(String(status || ''));
}

function num(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * Время события по часам сервера, мс. Сервер пишет наивный UTC
 * (`2026-09-30T10:00:00.123456`) — без зоны он читается как UTC, дробь
 * секунд укорачивается до миллисекунд. Нет или не разобрано — fallback.
 */
export function eventTime(ev, fallback = null) {
  const raw = ev && typeof ev.ts === 'string' ? ev.ts.trim() : '';
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/i.exec(raw);
  if (!m) return fallback;
  const frac = m[3] ? m[3].slice(0, 4) : '';
  let zone = m[4] ? m[4].toUpperCase() : 'Z';
  if (zone !== 'Z' && !zone.includes(':')) zone = `${zone.slice(0, 3)}:${zone.slice(3)}`;
  const t = Date.parse(`${m[1]}T${m[2]}${frac}${zone}`);
  return Number.isFinite(t) ? t : fallback;
}

/* engine._clip_stream: text[:16000] + "\n…[обрезано: N симв.]" */
const CLIP_MARK = /\n…\[обрезано: \d+ симв\.\]\s*$/;

/** Текст шага без отметки обрезки, если он обрезан сервером; иначе null. */
export function clippedPrefix(text) {
  const src = String(text ?? '');
  const m = CLIP_MARK.exec(src);
  return m ? src.slice(0, m.index) : null;
}

function str(v, limit = 4000) {
  if (v === null || v === undefined) return '';
  const s = typeof v === 'string' ? v : String(v);
  return s.length > limit ? `${s.slice(0, limit)}…` : s;
}

export function createTurn({ taskId = null, text = '', at = null, attachments = [] } = {}) {
  return {
    taskId,
    text,
    at,
    attachments,
    lastSeq: 0,
    status: 'queued',
    error: null,
    blockCode: null,
    blockReason: null,
    answers: new Map(),     // `${run}:${step}` → {run, step, attempt, minAttempt, parts, text, final}
    timeline: [],           // [{type: 'text'|'tool'|'approval'|'note', id}]
    tools: new Map(),       // call_id → карточка инструмента
    approvals: new Map(),   // id → {id, kind, preview, status, by}
    notes: new Map(),       // id → {id, title, detail, tone}
    evaluations: [],        // {verdict, reasons}
    progress: [],           // [{step, maxSteps, model, tools, waiting}] по возрастанию шага
    usage: null,
    sources: [],            // [{kind: 'memory'|'url'|'file', label, href?}]
    memory: null,           // {state: 'recalled'|'skipped', message, runId}
    runIds: [],
    model: null,
    startedAt: null,
    firstDeltaAt: null,
    finishedAt: null,
    hiddenDropped: 0,
    resultText: null,
    run: null,              // сводка прогона из GET /api/chat/threads/{id}
    version: 0,             // растёт на каждое принятое событие (перерисовка)
  };
}

function pushTimeline(turn, type, id) {
  if (!turn.timeline.some((t) => t.type === type && t.id === id)) turn.timeline.push({ type, id });
}

function answerKey(ev) {
  const run = ev.run_id ?? 'r';
  const step = ev.step ?? 0;
  return `${run}:${step}`;
}

function answerEntry(turn, ev) {
  const key = answerKey(ev);
  let entry = turn.answers.get(key);
  if (!entry) {
    entry = { key, run: ev.run_id ?? null, step: ev.step ?? 0, attempt: num(ev.attempt) ?? 0,
      minAttempt: 0, parts: [], text: '', final: false, awaitingRestart: false, generation: 0 };
    turn.answers.set(key, entry);
  }
  return entry;
}

function noteRunId(turn, runId) {
  if (runId !== null && runId !== undefined && !turn.runIds.includes(runId)) turn.runIds.push(runId);
}

function addNote(turn, id, note) {
  if (turn.notes.size >= MAX_NOTES && !turn.notes.has(id)) return;
  turn.notes.set(id, { id, ...note });
  pushTimeline(turn, 'note', id);
}

export function addSource(turn, source) {
  if (!source || !source.label) return false;
  const key = `${source.kind}|${source.href || source.label}`;
  if (turn.sources.some((s) => `${s.kind}|${s.href || s.label}` === key)) return false;
  if (turn.sources.length >= MAX_SOURCES) return false;
  turn.sources.push(source);
  return true;
}

/** Адреса из текста результата инструмента (для раздела «Источники»). */
export function extractUrls(text, limit = 20) {
  const found = [];
  const re = /https?:\/\/[^\s<>"'`)\]]+/gi;
  let m;
  const src = String(text ?? '');
  while ((m = re.exec(src)) && found.length < limit) {
    let url = m[0];
    while (/[.,;:!?]$/.test(url)) url = url.slice(0, -1);
    if (!found.includes(url)) found.push(url);
  }
  return found;
}

/** Инструмент → короткий ярлык («чип»): terminal.run → CMD, browser.open → Browser. */
export function toolChip(name) {
  const n = String(name || '').toLowerCase();
  const head = n.split(/[.:/_]/)[0];
  const map = {
    terminal: 'CMD', shell: 'CMD', cmd: 'CMD', powershell: 'CMD',
    browser: 'Browser', web: 'Интернет', http: 'Интернет',
    github: 'GitHub', git: 'GitHub',
    memory: 'Память', code: 'Код', python: 'Код',
    computer: 'Компьютер', fs: 'Файлы', file: 'Файлы', files: 'Файлы',
    apps: 'Приложения', opencode: 'OpenCode', openclaw: 'OpenClaw',
  };
  return map[head] || (head ? head.charAt(0).toUpperCase() + head.slice(1) : 'Инструмент');
}

/** Одна строка по (уже отредактированным сервером) аргументам инструмента. */
export function summarizeArgs(args, limit = 90) {
  if (args === null || args === undefined) return '';
  if (typeof args !== 'object') return str(args, limit);
  for (const key of ['command', 'cmd', 'url', 'query', 'path', 'file', 'name', 'text']) {
    const v = args[key];
    if (typeof v === 'string' && v.trim()) return str(v.trim().replace(/\s+/g, ' '), limit);
    if (Array.isArray(v) && v.length) return str(v.join(' '), limit);
  }
  const pairs = Object.entries(args).slice(0, 2).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`);
  return str(pairs.join(' '), limit);
}

function mergeUsage(prev, ev) {
  const keys = ['step', 'model', 'step_tokens_in', 'step_tokens_out', 'tokens_in', 'tokens_out', 'cost_usd',
    'pricing_known', 'context_window', 'max_tokens_total', 'max_cost_usd', 'usage_reported',
    'latency_ms', 'ttft_ms', 'gen_tps', 'tps_method', 'step_cache_read_tokens', 'step_cache_write_tokens',
    'cache_state', 'streamed', 'locality', 'billing'];
  const out = { ...(prev || {}) };
  for (const k of keys) {
    if (ev[k] !== undefined && ev[k] !== null) out[k] = ev[k];
    else if (!(k in out)) out[k] = null;
  }
  return out;
}

/**
 * Применить событие к ходу. Возвращает true, если состояние изменилось.
 * Повтор (seq ≤ последнего) и скрытые виды отбрасываются.
 */
export function applyEvent(turn, ev, now = Date.now()) {
  if (!ev || typeof ev !== 'object') return false;
  const kind = String(ev.kind || '');
  if (typeof ev.seq === 'number' && Number.isFinite(ev.seq)) {
    if (ev.seq <= turn.lastSeq) return false;
    turn.lastSeq = ev.seq;
  }
  if (HIDDEN_KINDS.has(kind)) { turn.hiddenDropped += 1; return false; }
  noteRunId(turn, ev.run_id ?? null);
  const changed = reduce(turn, kind, ev, now);
  if (changed) turn.version += 1;
  return changed;
}

function setStatus(turn, status) {
  /* завершённый ход меняет состояние только новой попыткой (queued/started) */
  if (isTerminalStatus(turn.status) && !['queued', 'running'].includes(status)) return;
  turn.status = status;
}

function reduce(turn, kind, ev, now) {
  switch (kind) {
    case 'task.created':
    case 'task.queued': {
      if (isTerminalStatus(turn.status) && !ev.retry) return false;
      turn.status = 'queued';
      if (ev.retry) {
        addNote(turn, `retry:${ev.run_id ?? ''}:${ev.attempt ?? turn.notes.size}`, {
          tone: 'warn', title: 'Повторная попытка',
          detail: [ev.attempt ? `попытка ${ev.attempt}` : '', ev.recovery_rung ? `ступень ${ev.recovery_rung}` : '']
            .filter(Boolean).join(' · '),
        });
      }
      return true;
    }
    case 'task.started':
      turn.status = 'running';
      if (turn.startedAt === null) turn.startedAt = eventTime(ev, now);
      return true;
    case 'task.paused':
      setStatus(turn, 'paused');
      return true;
    case 'task.progress': {
      if (ev.waiting_approval) setStatus(turn, 'waiting_approval');
      else if (!isTerminalStatus(turn.status)) turn.status = 'running';
      if (ev.model) turn.model = str(ev.model, 200);
      const step = num(ev.step);
      if (step !== null) {
        const row = { step, maxSteps: num(ev.max_steps), model: ev.model ? str(ev.model, 200) : null,
          tools: Array.isArray(ev.tool_calls) ? ev.tool_calls.map((t) => str(t, 120)) : [],
          waiting: Boolean(ev.waiting_approval) };
        const idx = turn.progress.findIndex((p) => p.step === step);
        if (idx >= 0) turn.progress[idx] = { ...turn.progress[idx], ...row };
        else {
          turn.progress.push(row);
          turn.progress.sort((a, b) => a.step - b.step);
        }
      }
      return true;
    }
    case 'run.answer_delta': {
      const entry = answerEntry(turn, ev);
      const attempt = num(ev.attempt) ?? entry.attempt;
      const idx = num(ev.idx);
      if (attempt < entry.minAttempt) {
        /* После answer_reset движок повторяет шаг НОВЫМ потоком: attempt снова 0,
           idx с 0. Такой поток открывает новое поколение с первого куска; поздние
           куски отменённой попытки (idx > 0) по-прежнему отбрасываются. */
        if (!(entry.awaitingRestart && idx === 0)) return false;
        entry.generation += 1;
        entry.minAttempt = attempt;
        entry.attempt = attempt;
        entry.parts = [];
      }
      entry.awaitingRestart = false;
      if (attempt > entry.attempt) { entry.parts = []; entry.attempt = attempt; }
      const text = str(ev.text, 20000);
      if (idx !== null && idx >= 0 && idx < 100000) entry.parts[idx] = text;
      else entry.parts.push(text);
      entry.text = entry.parts.join('');
      entry.final = false;
      if (turn.firstDeltaAt === null) turn.firstDeltaAt = now;
      if (!isTerminalStatus(turn.status)) turn.status = 'running';
      pushTimeline(turn, 'text', entry.key);
      return true;
    }
    case 'run.answer_reset': {
      const entry = answerEntry(turn, ev);
      const attempt = num(ev.attempt) ?? entry.attempt;
      entry.parts = [];
      entry.text = '';
      entry.minAttempt = attempt + 1;
      entry.attempt = attempt + 1;
      entry.final = false;
      entry.awaitingRestart = true;
      return true;
    }
    case 'run.assistant_message': {
      const entry = answerEntry(turn, ev);
      const text = str(ev.text, 20000);
      if (!ev.streamed || !entry.text) { entry.text = text; entry.parts = [text]; }
      entry.final = true;
      if (ev.model) turn.model = str(ev.model, 200);
      if (entry.text) pushTimeline(turn, 'text', entry.key);
      return true;
    }
    case 'run.tool_use': {
      const id = str(ev.call_id || `${ev.run_id ?? ''}:${ev.step ?? ''}:${ev.tool ?? ''}:${turn.tools.size}`, 200);
      const prev = turn.tools.get(id) || {};
      turn.tools.set(id, {
        ...prev, id, tool: str(ev.tool, 120), chip: toolChip(ev.tool), step: num(ev.step),
        source: ev.source ? str(ev.source, 60) : null,
        args: ev.args && typeof ev.args === 'object' ? ev.args : null,
        argsSummary: summarizeArgs(ev.args), startedAt: prev.startedAt ?? now,
        ok: prev.ok ?? null,
      });
      pushTimeline(turn, 'tool', id);
      return true;
    }
    case 'run.tool_result': {
      const id = str(ev.call_id || `${ev.run_id ?? ''}:${ev.step ?? ''}:${ev.tool ?? ''}`, 200);
      const prev = turn.tools.get(id) || { id, tool: str(ev.tool, 120), chip: toolChip(ev.tool), step: num(ev.step),
        args: null, argsSummary: '', startedAt: null };
      const card = { ...prev, ok: Boolean(ev.ok), durationMs: num(ev.duration_ms),
        summary: str(ev.summary, 500), preview: str(ev.preview, 4000), truncated: Boolean(ev.truncated),
        finishedAt: now };
      turn.tools.set(id, card);
      pushTimeline(turn, 'tool', id);
      for (const url of extractUrls(`${card.summary}\n${card.preview}`)) {
        addSource(turn, { kind: 'url', label: url, href: url, via: card.tool });
      }
      return true;
    }
    case 'tool.denied':
      addNote(turn, `denied:${ev.tool ?? ''}:${turn.notes.size}`, {
        tone: 'warn', title: `Инструмент не разрешён: ${str(ev.tool, 120) || '—'}`, detail: str(ev.reason, 1000),
      });
      return true;
    case 'approval.created': {
      const id = ev.id;
      if (id === null || id === undefined) return false;
      const prev = turn.approvals.get(id) || {};
      turn.approvals.set(id, { ...prev, id, kind: str(ev.approval_kind || prev.kind || 'tool', 80),
        preview: str(ev.preview, 4000), status: prev.status || 'pending' });
      pushTimeline(turn, 'approval', id);
      setStatus(turn, 'waiting_approval');
      return true;
    }
    case 'approval.decided': {
      const id = ev.id;
      if (!turn.approvals.has(id)) return false;
      turn.approvals.set(id, { ...turn.approvals.get(id), status: str(ev.status, 40) || 'decided', by: str(ev.by, 40) });
      return true;
    }
    case 'evaluation.completed':
      turn.evaluations.push({ verdict: str(ev.verdict, 40), reasons: str(ev.reasons, 1000) });
      return true;
    case 'run.usage':
      turn.usage = mergeUsage(turn.usage, ev);
      if (ev.model) turn.model = str(ev.model, 200);
      return true;
    case 'router.fallback':
      addNote(turn, `fallback:${turn.notes.size}`, {
        tone: 'warn', title: 'Запасная модель', detail: str(ev.reason || ev.detail, 500),
      });
      return true;
    case 'run.budget_exceeded':
      addNote(turn, `budget:${ev.run_id ?? ''}`, {
        tone: 'error', title: 'Бюджет исчерпан', detail: str(ev.reason || ev.breach, 500),
      });
      return true;
    case 'run.log': {
      const lk = String(ev.log_kind || '');
      if (lk === 'memory.recalled') {
        turn.memory = { state: 'recalled', message: str(ev.message, 300), runId: ev.run_id ?? null };
        return true;
      }
      if (lk === 'memory.recall_skipped') {
        turn.memory = { state: 'skipped', message: str(ev.message, 300), runId: ev.run_id ?? null };
        return true;
      }
      return false;
    }
    case 'task.completed':
      turn.status = 'completed';
      turn.finishedAt = eventTime(ev, now);
      return true;
    case 'task.failed':
      turn.status = 'failed';
      turn.error = str(ev.error, 4000) || turn.error;
      turn.finishedAt = eventTime(ev, now);
      return true;
    case 'task.stopped':
      turn.status = 'stopped';
      turn.finishedAt = eventTime(ev, now);
      return true;
    case 'task.blocked':
      turn.status = 'blocked';
      turn.blockCode = str(ev.code, 80) || null;
      turn.blockReason = str(ev.reason, 2000) || null;
      turn.finishedAt = eventTime(ev, now);
      return true;
    default:
      return false;
  }
}

/** Источники памяти из строк run_events (GET /api/runs/{id}/events): kind memory.recalled, data.sources. */
export function memorySourcesFromRunEvents(rows) {
  const out = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || row.kind !== 'memory.recalled') continue;
    const sources = row.data && Array.isArray(row.data.sources) ? row.data.sources : [];
    for (const s of sources) {
      if (typeof s === 'string') out.push({ kind: 'memory', label: s });
      else if (s && typeof s === 'object') {
        const label = s.heading || s.title || s.source || s.path || s.file || s.id;
        if (label) out.push({ kind: 'memory', label: str(label, 200), detail: s.source && s.heading ? str(s.source, 200) : '' });
      }
    }
  }
  return out;
}

/** Итог по ответу сервера (GET /api/chat/threads/{id} → turns[i]) — окончательная правда. */
export function applyTruth(turn, detail) {
  if (!detail || typeof detail !== 'object') return false;
  if (detail.status) {
    if (isTerminalStatus(detail.status) || !isTerminalStatus(turn.status)) turn.status = String(detail.status);
  }
  if (typeof detail.result === 'string') turn.resultText = detail.result;
  if (detail.error) turn.error = str(detail.error, 4000);
  if (detail.run && typeof detail.run === 'object') turn.run = detail.run;
  if (!turn.model && detail.run && detail.run.model_alias) turn.model = str(detail.run.model_alias, 200);
  turn.version += 1;
  return true;
}

/**
 * Что рисовать в теле ответа, по порядку появления: текст шагов, карточки
 * инструментов, подтверждения, заметки. Итог задачи добавляется в конец,
 * только если он не совпадает с уже показанным текстом шага. Текст шага,
 * обрезанный сервером (…[обрезано: N симв.]), заменяется полным итогом,
 * если итог начинается с него — иначе длинный ответ был бы показан дважды.
 */
export function displaySegments(turn) {
  const segs = [];
  const shown = [];
  const full = typeof turn.resultText === 'string' ? turn.resultText : '';
  const result = full.trim();
  for (const item of turn.timeline) {
    if (item.type === 'text') {
      const entry = turn.answers.get(item.id);
      if (entry && entry.text) {
        const clipped = result ? clippedPrefix(entry.text) : null;
        if (clipped !== null && clipped.trim() && result.startsWith(clipped.trim())) {
          segs.push({ type: 'text', key: `t:${item.id}`, text: full, final: true });
          shown.push(result);
        } else {
          segs.push({ type: 'text', key: `t:${item.id}`, text: entry.text, final: entry.final });
          shown.push(entry.text.trim());
        }
      }
    } else if (item.type === 'tool') {
      const card = turn.tools.get(item.id);
      if (card) segs.push({ type: 'tool', key: `x:${item.id}`, card });
    } else if (item.type === 'approval') {
      const a = turn.approvals.get(item.id);
      if (a) segs.push({ type: 'approval', key: `a:${item.id}`, approval: a });
    } else if (item.type === 'note') {
      const n = turn.notes.get(item.id);
      if (n) segs.push({ type: 'note', key: `n:${item.id}`, note: n });
    }
  }
  if (result && !shown.includes(result)) {
    segs.push({ type: 'text', key: 't:final', text: turn.resultText, final: true });
  }
  return segs;
}

/** Весь видимый ответ хода одним текстом (копирование, экспорт). */
export function answerText(turn) {
  return displaySegments(turn).filter((s) => s.type === 'text').map((s) => s.text.trim()).join('\n\n');
}

export function toolCounts(turn) {
  let done = 0;
  for (const c of turn.tools.values()) if (c.ok !== null && c.ok !== undefined) done += 1;
  return { done, total: turn.tools.size };
}
