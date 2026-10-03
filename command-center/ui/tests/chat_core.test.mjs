/* Чистая логика настольного чата (ui/chat/*.js) без браузера.

   Проверяется то, от чего зависит честность экрана: разбор SSE по кускам,
   переподключение с курсором без повторов, сборка ответа из дельт
   (answer_reset, порядок idx), отбрасывание скрытых рассуждений модели,
   экранирование markdown, группировка тредов по датам и расчёты строки
   состояния при отсутствующих полях. Плюс живой рендер: замороженный
   префикс (свойство на случайных нарезках), лечение хвоста с негативными
   контролями, прилипание ленты к низу, retryNow без второго соединения,
   длительность хода по времени сервера. Запускается из
   command-center/tests/test_chat_ui_static.py (node --test). */
import test from 'node:test';
import assert from 'node:assert/strict';

import { createSseParser, parseFrame, TaskStream } from '../chat/stream.js';
import {
  createTurn, applyEvent, applyTruth, answerText, displaySegments, memorySourcesFromRunEvents, extractUrls, toolChip,
  eventTime, clippedPrefix,
} from '../chat/state.js';
import { markdownToHtml, safeHref, escapeHtml, stableCut, healTail, planTextRender } from '../chat/markdown.js';
import {
  groupThreads, threadTimeLabel, contextMeter, tpsLabel, latencyLabel, costLabel, buildPickerModel, routeCard,
  isLoopbackHost, exportMarkdown, technicalLogEvent, technicalLogFileName, clientRequestId, backoffDelay, raveAgentSpecs, fmtTokens, raveIsLive,
} from '../chat/format.js';
import { encodeWavPcm16 } from '../chat/audio.js';
import { nextStuck, STICK_EPSILON } from '../chat/scroll.js';
import { statusLine, turnEndAnnouncement } from '../chat/render.js';

/* ---------------------------------------------------------------- SSE */

test('SSE parser: a frame cut mid-line and mid-CRLF, comments and multi-line data', () => {
  const p = createSseParser();
  const out = [];
  for (const chunk of ['id: 7\nda', 'ta: {"kind":"a","seq":7}\r', '\n\r\n: keepalive\n\n', 'data: line1\ndata: line2\n', '\n']) {
    out.push(...p.push(chunk));
  }
  assert.deepEqual(out, [
    { id: '7', event: 'message', data: '{"kind":"a","seq":7}' },
    { comment: 'keepalive' },
    { id: null, event: 'message', data: 'line1\nline2' },
  ]);
});

test('SSE parser: an unfinished frame is not delivered early', () => {
  const p = createSseParser();
  assert.deepEqual(p.push('data: {"kind":"x"}\n'), []);
  assert.deepEqual(p.push('\n'), [{ id: null, event: 'message', data: '{"kind":"x"}' }]);
});

test('parseFrame: broken JSON and non-objects are dropped, not thrown', () => {
  assert.equal(parseFrame({ data: '{broken' }), null);
  assert.equal(parseFrame({ data: '[1,2]' }), null);
  assert.deepEqual(parseFrame({ data: '{"kind":"task.completed","seq":3}' }), { kind: 'task.completed', seq: 3 });
});

test('technical log export allowlists diagnostic fields and drops content-bearing fields', () => {
  const raw = { kind: 'run.tool_use', task_id: 7, run_id: 9, step: 2, tool: 'fs.read', ts: '2026-10-02T12:00:00Z',
    args: { path: 'C:/private.txt', token: 'secret' }, message: 'user prompt', preview: 'tool output',
    model: 'local-qwen-35b', duration_ms: 45 };
  const safe = technicalLogEvent(raw);
  assert.deepEqual(safe.event, { kind: 'run.tool_use', task_id: 7, run_id: 9, step: 2,
    tool: 'fs.read', ts: '2026-10-02T12:00:00Z', model: 'local-qwen-35b', duration_ms: 45 });
  assert.equal(safe.omittedFields, 3);
  assert.doesNotMatch(JSON.stringify(safe), /private\.txt|secret|user prompt|tool output/);
});

test('technical log export rejects free-form strings masquerading as technical identifiers', () => {
  const safe = technicalLogEvent({ kind: 'task.failed', model: 'private prompt content', status: 'failed',
    tool: 'terminal.run', error: 'raw exception with credentials',
    source: 'nvapi-Abcdefgh1234567890', ts: '2026-10-02T12:00:00Z credentials' });
  assert.deepEqual(safe.event, { kind: 'task.failed', status: 'failed', tool: 'terminal.run' });
  assert.equal(safe.omittedFields, 4);
});

test('technical log filename has a stable local date format', () => {
  assert.equal(technicalLogFileName(new Date('2026-10-02T12:00:00Z')), 'bossman-technical-log 2026-10-02.json');
});

function sseResponse(chunks) {
  const enc = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

async function settle(rounds = 40) {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

test('TaskStream: reconnects with after=<last seq> and delivers every event exactly once', async () => {
  const urls = [];
  const replies = [
    sseResponse([
      'data: {"kind":"stream.open","after":0}\n\n',
      'id: 5\ndata: {"kind":"run.answer_delta","seq":5,"run_id":1,"step":1,"attempt":0,"idx":0,"text":"При"}\n\n',
    ]),
    sseResponse([
      'data: {"kind":"stream.open","after":5}\n\n',
      'id: 5\ndata: {"kind":"run.answer_delta","seq":5,"run_id":1,"step":1,"attempt":0,"idx":0,"text":"При"}\n\n',
      ': keepalive\n\n',
      'id: 6\ndata: {"kind":"run.answer_delta","seq":6,"run_id":1,"step":1,"attempt":0,"idx":1,"text":"вет"}\n\n',
      'id: 7\ndata: {"kind":"task.completed","seq":7,"task_id":42}\n\n',
    ]),
  ];
  const timers = [];
  const seen = [];
  const states = [];
  const stream = new TaskStream({
    taskId: 42,
    after: 0,
    fetchImpl: async (url) => { urls.push(url); return replies.shift(); },
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimer: () => {},
    random: () => 0.5,
    lingerMs: 5,
    onEvent: (ev) => seen.push(ev.seq),
    onState: (state) => states.push(state),
  });
  stream.start();
  await settle();
  assert.deepEqual(urls, ['/api/events/stream?task_id=42&after=0']);
  const retry = timers.find((t) => t.ms === 500);
  assert.ok(retry, `a reconnect after 500 ms must be scheduled, got ${timers.map((t) => t.ms).join(',')}`);
  retry.fn();
  await settle();
  assert.equal(urls[1], '/api/events/stream?task_id=42&after=5');
  assert.deepEqual(seen, [5, 6, 7], 'the replayed seq 5 must not be delivered twice');
  const linger = timers.find((t) => t.ms === 5);
  assert.ok(linger, 'after a terminal event the stream closes after a short linger');
  linger.fn();
  assert.equal(states[states.length - 1], 'done');
  assert.ok(states.includes('open') && states.includes('reconnecting'));
  assert.equal(stream.stopped, true);
});

test('TaskStream.retryNow: cancels the pending pause and opens exactly one connection', async () => {
  const urls = [];
  const timers = new Map();
  const cleared = [];
  let nextId = 0;
  const states = [];
  const stream = new TaskStream({
    taskId: 9,
    /* первое подключение — обрыв сети, второе «висит» (подключение идёт) */
    fetchImpl: (url) => { urls.push(url); return urls.length === 1 ? Promise.reject(new TypeError('network down')) : new Promise(() => {}); },
    setTimer: (fn, ms) => { nextId += 1; timers.set(nextId, { fn, ms }); return nextId; },
    clearTimer: (id) => { cleared.push(id); },
    random: () => 0.5,
    onState: (s, info) => states.push([s, info && info.delay_ms]),
  });
  stream.start();
  await settle();
  assert.equal(urls.length, 1);
  const pending = stream.retryTimer;
  assert.notEqual(pending, null, 'a reconnect pause is scheduled after the network error');
  assert.deepEqual(states[states.length - 1], ['reconnecting', 500], 'the banner gets the real pause (delay_ms)');
  assert.equal(stream.retryNow(), true);
  await settle();
  assert.equal(urls.length, 2, 'retryNow connects at once');
  assert.ok(cleared.includes(pending), 'the pending pause is cancelled');
  assert.equal(stream.retryNow(), false, 'while a connection is in progress retryNow does nothing');
  timers.get(pending).fn();                  // даже если отменённый таймер всё же сработал
  await settle();
  assert.equal(urls.length, 2, 'a cancelled pause never opens a second connection');
  stream.stop();
  assert.equal(stream.retryNow(), false, 'a stopped stream never reconnects');
});

test('TaskStream: 401 stops the stream and asks for login instead of retrying forever', async () => {
  const timers = [];
  const states = [];
  const stream = new TaskStream({
    taskId: 1,
    fetchImpl: async () => new Response('{"error":{"message":"нужен вход"}}', { status: 401 }),
    setTimer: (fn, ms) => { timers.push(ms); return 1; },
    clearTimer: () => {},
    onState: (s) => states.push(s),
  });
  stream.start();
  await settle();
  assert.equal(states[states.length - 1], 'auth');
  assert.deepEqual(timers, []);
});

/* ---------------------------------------------------------------- состояние хода */

test('state: a repeated seq is ignored and hidden reasoning never reaches the turn', () => {
  const t = createTurn({ taskId: 1 });
  assert.equal(applyEvent(t, { kind: 'run.answer_delta', seq: 3, run_id: 9, step: 1, attempt: 0, idx: 0, text: 'Да' }), true);
  assert.equal(applyEvent(t, { kind: 'run.answer_delta', seq: 3, run_id: 9, step: 1, attempt: 0, idx: 0, text: 'Да' }), false);
  assert.equal(applyEvent(t, { kind: 'run.reasoning_delta', run_id: 9, step: 1, text: 'СКРЫТОЕ РАССУЖДЕНИЕ' }), false);
  assert.equal(t.hiddenDropped, 1);
  assert.equal(answerText(t), 'Да');
  const everything = JSON.stringify({
    answers: [...t.answers.values()], timeline: t.timeline, notes: [...t.notes.values()], segs: displaySegments(t),
  });
  assert.ok(!everything.includes('СКРЫТОЕ'), 'reasoning text must not be stored anywhere in the turn');
});

test('state: answer_reset clears the step; late deltas of the cancelled attempt are ignored; idx order wins', () => {
  const t = createTurn({ taskId: 1 });
  applyEvent(t, { kind: 'run.answer_delta', seq: 1, run_id: 2, step: 1, attempt: 0, idx: 0, text: 'Неверн' });
  applyEvent(t, { kind: 'run.answer_reset', seq: 2, run_id: 2, step: 1, attempt: 0 });
  assert.equal(answerText(t), '');
  assert.equal(applyEvent(t, { kind: 'run.answer_delta', seq: 3, run_id: 2, step: 1, attempt: 0, idx: 1, text: 'ый хвост' }), false);
  applyEvent(t, { kind: 'run.answer_delta', seq: 4, run_id: 2, step: 1, attempt: 1, idx: 1, text: ' мир' });
  applyEvent(t, { kind: 'run.answer_delta', seq: 5, run_id: 2, step: 1, attempt: 1, idx: 0, text: 'Привет' });
  assert.equal(answerText(t), 'Привет мир');
});

test('state: after answer_reset a retried step (fresh stream: attempt 0, idx 0) streams again; stale idx>0 stays dropped', () => {
  const t = createTurn({ taskId: 1 });
  const d = (seq, attempt, idx, text) => applyEvent(t, { kind: 'run.answer_delta', seq, run_id: 5, step: 2, attempt, idx, text });
  d(1, 0, 0, 'Сломан');
  d(2, 0, 1, 'ный');
  applyEvent(t, { kind: 'run.answer_reset', seq: 3, run_id: 5, step: 2, attempt: 0 });
  assert.equal(d(4, 0, 2, ' хвост'), false, 'a late delta of the cancelled attempt is dropped');
  assert.equal(answerText(t), '');
  assert.equal(d(5, 0, 0, 'Новый'), true, 'the engine retried the step with a fresh stream: accepted from idx 0');
  assert.equal(d(6, 0, 1, ' ответ'), true);
  assert.equal(answerText(t), 'Новый ответ');
  assert.equal(t.answers.get('5:2').generation, 1);
});

test('state: a server-clipped answer is replaced by the full result instead of being shown twice', () => {
  const full = `${'а'.repeat(16000)}конец`;
  const t = createTurn({ taskId: 1 });
  applyEvent(t, { kind: 'run.assistant_message', seq: 1, run_id: 1, step: 1, streamed: false,
    text: `${full.slice(0, 16000)}\n…[обрезано: 5 симв.]` });
  applyTruth(t, { status: 'completed', result: full });
  const texts = displaySegments(t).filter((s) => s.type === 'text');
  assert.equal(texts.length, 1);
  assert.equal(texts[0].text, full);
  assert.equal(texts[0].final, true);
  assert.equal(answerText(t), full);
  assert.equal(clippedPrefix('коротко'), null);
  /* негативный контроль: другой итог по-прежнему показывается отдельно */
  const other = createTurn({ taskId: 2 });
  applyEvent(other, { kind: 'run.assistant_message', seq: 1, run_id: 1, step: 1, streamed: false,
    text: `${'б'.repeat(16000)}\n…[обрезано: 3 симв.]` });
  applyTruth(other, { status: 'completed', result: 'Итог задачи другой' });
  assert.equal(displaySegments(other).filter((s) => s.type === 'text').length, 2);
});

test('state: turn duration uses server timestamps, so history and F5 replays are not "0 мс"', () => {
  const late = Date.UTC(2026, 8, 30, 12, 0, 0);
  const t = createTurn({ taskId: 3 });
  /* история: оба события применяются в один момент — длительность всё равно по ts сервера */
  applyEvent(t, { kind: 'task.started', seq: 1, ts: '2026-09-30T10:00:00.250000' }, late);
  applyEvent(t, { kind: 'task.completed', seq: 2, ts: '2026-09-30T10:00:42.750123' }, late);
  assert.equal(t.finishedAt - t.startedAt, 42500);
  assert.equal(eventTime({ ts: '2026-09-30T10:00:00Z' }), Date.UTC(2026, 8, 30, 10, 0, 0));
  assert.equal(eventTime({ ts: '2026-09-30T13:00:00+03:00' }), Date.UTC(2026, 8, 30, 10, 0, 0));
  assert.equal(eventTime({ ts: 'вчера' }, 7), 7);
  assert.equal(eventTime({}, null), null);
  const noTs = createTurn({ taskId: 4 });
  applyEvent(noTs, { kind: 'task.started', seq: 1 }, 1000);
  assert.equal(noTs.startedAt, 1000, 'without ts the window clock is the fallback');
});

test('render: the status line keeps its signature across deltas; the end of a turn is announced once, honestly', () => {
  const t = createTurn({ taskId: 1 });
  applyEvent(t, { kind: 'task.started', seq: 1 });
  applyEvent(t, { kind: 'task.progress', seq: 2, step: 1, max_steps: 8 });
  const before = statusLine(t).sig;
  applyEvent(t, { kind: 'run.answer_delta', seq: 3, run_id: 1, step: 1, attempt: 0, idx: 0, text: 'При' });
  applyEvent(t, { kind: 'run.answer_delta', seq: 4, run_id: 1, step: 1, attempt: 0, idx: 1, text: 'вет' });
  assert.equal(statusLine(t).sig, before, 'text deltas do not rebuild the status line');
  applyEvent(t, { kind: 'task.progress', seq: 5, step: 2, max_steps: 8 });
  assert.notEqual(statusLine(t).sig, before, 'a new step does');
  assert.equal(statusLine(t).live, true);
  assert.equal(turnEndAnnouncement(t), '', 'nothing is announced while the turn runs');
  applyEvent(t, { kind: 'task.completed', seq: 6 });
  assert.equal(statusLine(t).live, false);
  assert.equal(turnEndAnnouncement(t), 'Ответ готов');
  const stopped = createTurn();
  applyEvent(stopped, { kind: 'task.stopped', seq: 1 });
  assert.equal(turnEndAnnouncement(stopped), 'Остановлено');
  const failed = createTurn();
  applyEvent(failed, { kind: 'task.failed', seq: 1, error: 'нет модели' });
  assert.equal(turnEndAnnouncement(failed), 'Ошибка: Задача не выполнена');
});

test('render: a turn waiting for the owner is not "busy", so the approval card reaches a screen reader', () => {
  const t = createTurn({ taskId: 1 });
  applyEvent(t, { kind: 'task.started', seq: 1 });
  assert.equal(statusLine(t).live, true);
  assert.equal(statusLine(t).waiting, false, 'negative control: a running turn without approvals stays busy');
  applyEvent(t, { kind: 'approval.created', seq: 2, id: 5, approval_kind: 'terminal.run', preview: 'dir' });
  assert.equal(statusLine(t).live, true, 'the turn still runs on the server');
  assert.equal(statusLine(t).waiting, true);
  applyEvent(t, { kind: 'task.completed', seq: 3 });
  assert.equal(statusLine(t).waiting, false, 'a finished turn is neither live nor waiting');
});

test('state: assistant_message without streaming renders its text; with streaming the live text stays', () => {
  const whole = createTurn();
  applyEvent(whole, { kind: 'run.assistant_message', seq: 1, run_id: 1, step: 1, text: 'Целиком', streamed: false });
  assert.equal(answerText(whole), 'Целиком');
  applyTruth(whole, { status: 'completed', result: 'Целиком' });
  assert.equal(displaySegments(whole).filter((s) => s.type === 'text').length, 1, 'the final result is not duplicated');
  applyTruth(whole, { status: 'completed', result: 'Итог задачи другой' });
  assert.equal(displaySegments(whole).filter((s) => s.type === 'text').length, 2, 'a different final result is shown too');

  const live = createTurn();
  applyEvent(live, { kind: 'run.answer_delta', seq: 1, run_id: 1, step: 1, attempt: 0, idx: 0, text: 'Живой' });
  applyEvent(live, { kind: 'run.assistant_message', seq: 2, run_id: 1, step: 1, text: 'Живой текст', streamed: true });
  assert.equal(answerText(live), 'Живой');
});

test('state: tool_use + tool_result give one card with a chip, duration, redacted args and URL sources', () => {
  const t = createTurn();
  applyEvent(t, { kind: 'run.tool_use', seq: 1, run_id: 1, step: 1, call_id: 'c1', tool: 'terminal.run', args: { command: 'git status' } });
  applyEvent(t, { kind: 'run.tool_result', seq: 2, run_id: 1, step: 1, call_id: 'c1', tool: 'terminal.run', ok: true,
    duration_ms: 1200, summary: 'ok', preview: 'see https://example.com/docs.' });
  const cards = displaySegments(t).filter((s) => s.type === 'tool');
  assert.equal(cards.length, 1);
  assert.equal(cards[0].card.chip, 'CMD');
  assert.equal(cards[0].card.argsSummary, 'git status');
  assert.equal(cards[0].card.ok, true);
  assert.equal(cards[0].card.durationMs, 1200);
  assert.deepEqual(t.sources.map((s) => s.href), ['https://example.com/docs']);
  assert.equal(toolChip('browser.open'), 'Browser');
  assert.equal(toolChip('web.search'), 'Интернет');
});

test('state: terminal status sticks; failure and block reasons are kept verbatim', () => {
  const failed = createTurn();
  applyEvent(failed, { kind: 'task.failed', seq: 1, error: 'провайдер недоступен' });
  applyEvent(failed, { kind: 'task.progress', seq: 2, step: 3, max_steps: 8 });
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, 'провайдер недоступен');

  const blocked = createTurn();
  applyEvent(blocked, { kind: 'task.blocked', seq: 1, code: 'BLOCKED_CAPABILITY_UNAVAILABLE', reason: 'нет модели' });
  assert.equal(blocked.status, 'blocked');
  assert.equal(blocked.blockCode, 'BLOCKED_CAPABILITY_UNAVAILABLE');
  assert.equal(blocked.blockReason, 'нет модели');

  const retried = createTurn();
  applyEvent(retried, { kind: 'task.queued', seq: 1, run_id: 4, attempt: 1, retry: true, recovery_rung: 'retry_same' });
  assert.equal(retried.status, 'queued');
  assert.equal([...retried.notes.values()][0].title, 'Повторная попытка');
});

test('state: approvals wait for the owner and follow the decision event', () => {
  const t = createTurn();
  applyEvent(t, { kind: 'approval.created', seq: 1, id: 17, approval_kind: 'terminal.run', preview: 'rm -rf temp' });
  assert.equal(t.status, 'waiting_approval');
  assert.equal(t.approvals.get(17).status, 'pending');
  applyEvent(t, { kind: 'approval.decided', seq: 2, id: 17, status: 'approved', by: 'owner' });
  assert.equal(t.approvals.get(17).status, 'approved');
  assert.equal(displaySegments(t).filter((s) => s.type === 'approval').length, 1);
});

test('state: memory sources come from run_events data.sources only', () => {
  const rows = [
    { kind: 'memory.recalled', data: { sources: ['notes/plan.md', { heading: 'Итоги', source: 'vault/week.md' }] } },
    { kind: 'run.retry', data: { sources: ['must-not-appear'] } },
  ];
  assert.deepEqual(memorySourcesFromRunEvents(rows).map((s) => s.label), ['notes/plan.md', 'Итоги']);
  assert.deepEqual(extractUrls('a http://x.test/a, b https://y.test/b) c'), ['http://x.test/a', 'https://y.test/b']);
});

/* ---------------------------------------------------------------- markdown */

test('markdown: raw HTML stays text and never becomes markup', () => {
  const html = markdownToHtml('<script>alert(1)</script> и <img src=x onerror=alert(1)>');
  assert.ok(!html.includes('<script'), html);
  assert.ok(!html.includes('<img'), html);
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'), html);
  assert.equal(escapeHtml('"<&>\''), '&quot;&lt;&amp;&gt;&#39;');
});

test('markdown: headings, emphasis, inline code, lists, code fences and only safe links', () => {
  const html = markdownToHtml([
    '# Итог', '', '**жирный** и *курсив*, `a<b`', '', '- один', '- два', '', '1. первый', '2. второй', '',
    '```js', 'if (a < b) x();', '```', '',
    '[док](https://example.com/a?b=1) [плохо](javascript:void0) https://example.org/x.',
  ].join('\n'));
  assert.ok(html.includes('<h3 class="md-h">Итог</h3>'), html);
  assert.ok(html.includes('<strong>жирный</strong>'), html);
  assert.ok(html.includes('<em>курсив</em>'), html);
  assert.ok(html.includes('<code class="md-inline-code">a&lt;b</code>'), html);
  assert.ok(html.includes('<ul><li>один</li><li>два</li></ul>'), html);
  assert.ok(html.includes('<ol><li>первый</li><li>второй</li></ol>'), html);
  assert.ok(html.includes('<code data-lang="js">if (a &lt; b) x();</code>'), html);
  assert.ok(html.includes('<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer">док</a>'), html);
  assert.ok(html.includes('<a href="https://example.org/x" target="_blank" rel="noopener noreferrer">https://example.org/x</a>.'), html);
  assert.ok(!html.includes('javascript'), 'a javascript: link must not survive');
  assert.equal(safeHref('javascript:alert(1)'), null);
  assert.equal(safeHref('https://ok.test/a b'), null);
  assert.equal(safeHref('mailto:owner@example.com'), 'mailto:owner@example.com');
});

/* ---------------------------------------------------------------- живой рендер: замороженный префикс */

const MD_DOCS = [
  '# Итог\n\nПервый абзац с **жирным** и `кодом`.\n\n- один\n- два\n\n  продолжение\n\nТекст после списка.\n\n'
    + '```js\nconst a = 1;\n\nconst b = 2;\n```\n\n> цитата\n> ещё\n\nКонец https://example.com/x.',
  /* внутренний ``` короче внешнего ```` — не закрывает блок; пустая строка и «foo» внутри — всё ещё код */
  'intro\n\n````md\n```js\nx\n\n```\n\nfoo\n\n````\n\nafter ````\n\nend',
  /* ``` с отступом внутри пункта — часть пункта, а не блок кода верхнего уровня */
  '- a\n  ```\n\nfoo\n\n```\ncode\n\nbar\n```\n\nend',
  'line1\r\n\r\nline2 *em*\r\n\r\n- a\r\n\r\ntext\r\n\r\n```\r\nx\r\n\r\ny\r\n```\r\n\r\nz',
  '1. a\n\n   ```\n   code in list\n\n   ```\n\n2. b\n\ntext\n\n12) x\n\n13) y\n\nfin',
  'p\n\n\tindented\n\nq\n\n~~~\n```\n\nstill code\n~~~\n\nb **c**\n\n***\n\n## h\n\nlast',
  'a\rb\r\rc\r\r```\r\rcode\r```\r\rd',
  'para *emph that\n\ncontinues* across blank\n\n**bold [link](https://a.b/c_(d)) `code` done**\n\n> q\n\nlazy',
];

function lcg(seed) {
  let s = seed;
  return () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
}

test('markdown: frozen blocks + tail render exactly like the whole text at every chunk (random chunkings)', () => {
  for (const [di, doc] of MD_DOCS.entries()) {
    const want = markdownToHtml(doc);
    for (let seed = 1; seed <= 80; seed += 1) {
      const rnd = lcg(seed);
      let acc = '';
      let frozenAt = 0;
      let head = '';
      for (let i = 0; i < doc.length;) {
        const n = 1 + Math.floor(rnd() * 24);
        acc += doc.slice(i, i + n);
        i += n;
        const cut = stableCut(acc, frozenAt);
        assert.ok(cut >= frozenAt, `doc ${di} seed ${seed}: the cut never moves back`);
        assert.equal(cut, stableCut(acc), `doc ${di} seed ${seed}: a scan from the previous cut equals a scan from zero`);
        if (cut > frozenAt) { head += markdownToHtml(acc.slice(frozenAt, cut)); frozenAt = cut; }
        assert.equal(head + markdownToHtml(acc.slice(frozenAt)), markdownToHtml(acc), `doc ${di} seed ${seed} at ${acc.length}`);
      }
      assert.equal(head + markdownToHtml(acc.slice(frozenAt)), want, `doc ${di} seed ${seed}: final`);
    }
  }
});

test('markdown: stableCut freezes finished blocks only, never inside a fence or before an unfinished line', () => {
  assert.equal(stableCut(''), 0);
  assert.equal(stableCut('один абзац без конца'), 0);
  assert.equal(stableCut('a\n\nb'), 0, 'the next line is still being written');
  assert.equal(stableCut('a\n\nb\n'), 3);
  assert.equal(stableCut('```\na\n\nb\n\nc\n'), 0, 'inside an open fence nothing is final');
  assert.equal(stableCut('- a\n\n- b\n\nc\n'), 10, 'a list may continue after a blank line: cut only before «c»');
  assert.equal(stableCut('> q\n\n> r\n'), 0);
  assert.equal(stableCut('a\n\nb\n\nc\n', 3), 6, 'from the previous cut, only forward');
});

test('markdown: planTextRender appends finished blocks once, heals only the live tail and starts over on replaced text', () => {
  const first = 'Первый абзац.\n\nВторой [док](https://exa';
  let plan = planTextRender(null, first, false);
  assert.equal(plan.reset, true);
  assert.equal(plan.append, null);
  assert.ok(!markdownToHtml(plan.tail).includes('<a '), 'a half-typed link is not live while streaming');
  const more = 'Первый абзац.\n\nВторой [док](https://example.com)\n\nТретий';
  plan = planTextRender(plan, more, false);
  assert.equal(plan.reset, false);
  assert.equal(plan.append, 'Первый абзац.\n\n', 'only finished blocks are appended');
  assert.equal(plan.tail, 'Второй [док](https://example.com)\n\nТретий');
  const done = `${more} конец.`;
  const fin = planTextRender(plan, done, true);
  assert.equal(fin.append, null);
  assert.equal(fin.tail, done.slice(plan.frozenAt), 'final text: the tail is re-rendered as is');
  assert.equal(markdownToHtml(plan.append) + markdownToHtml(fin.tail), markdownToHtml(done));
  const replaced = planTextRender(fin, 'Совсем другой ответ', false);
  assert.equal(replaced.reset, true, 'text replaced by the server truth or a retry: full rebuild');
  assert.equal(replaced.frozenAt, 0);
  const history = planTextRender(null, 'См. [док](https://exa', true);
  assert.equal(history.tail, 'См. [док](https://exa', 'history and final text are never healed');
});

test('markdown: healTail hides half-typed links and URLs, closes an open `code`, leaves emphasis and fences alone', () => {
  const hasLink = (src) => markdownToHtml(src).includes('<a ');
  assert.ok(hasLink('См. [док](https://exa'), 'negative control: unhealed, the half link IS live (the defect)');
  assert.equal(healTail('См. [док](https://exa'), 'См. док');
  assert.ok(hasLink('see https://example.com/pa'), 'negative control: a URL being typed IS a link unhealed');
  const healedUrl = healTail('see https://example.com/pa');
  assert.ok(!hasLink(healedUrl), healedUrl);
  assert.equal(markdownToHtml(healedUrl), '<p>see https://example.com/pa</p>', 'the text stays exactly visible');
  assert.ok(!hasLink(healTail('a https://x.test/?u=https://y')), 'a nested URL does not become a link either');
  assert.equal(healTail('see https://example.com/pa '), 'see https://example.com/pa ', 'whitespace after it: finished');
  assert.equal(healTail('run `npm te'), 'run `npm te`');
  assert.equal(healTail('x ``a`b`'), 'x ``a`b``', 'a partly typed closing run is completed, not doubled');
  assert.equal(healTail('`a` и [b](https://ex'), '`a` и b');
  for (const same of ['2**10 = 1024', 'a * b', 'snake_case_na', '**жирн', '~~зачёркн', 'plain text', 'x `',
    '```\n[x](https://e', '```js\nsee https://exa', 'a\n\n', 'готово: https://example.com/a\n']) {
    assert.equal(healTail(same), same, JSON.stringify(same));
  }
  assert.equal(markdownToHtml('a\\:b'), '<p>a:b</p>', "':' is escapable, as in CommonMark");
});

test('markdown: healTail keeps a finished link at the end of the live tail; a bare URL after it is still neutralised', () => {
  const html = (src) => markdownToHtml(healTail(src));
  assert.ok(markdownToHtml('see [https\\://a.com](https\\://a.com)').includes('https\\://'),
    'negative control: a link whose address was escaped shows the backslash (what the owner saw mid-stream)');
  for (const done of ['see [docs](https://a.com)', 'see [docs](https://a.com).', 'see [https://a.com](https://a.com)']) {
    assert.equal(healTail(done), done, JSON.stringify(done));
    assert.ok(html(done).includes('<a href="https://a.com"'), `${done}: the finished link is live`);
    assert.ok(!html(done).includes('\\'), `${done}: no backslash on screen`);
  }
  const after = html('[a](https://x.test) и https://b.co');
  assert.ok(after.includes('<a href="https://x.test"'), after);
  assert.ok(!after.includes('href="https://b.co'), 'the address still being typed after the link is not live');
  assert.ok(after.includes('https://b.co'), 'and it stays visible as text');
});

test('markdown: an unclosed fence while streaming is code; snake_case is not italic', () => {
  const streaming = markdownToHtml('Пишу:\n```py\nprint(1)');
  assert.ok(streaming.includes('data-open="true"') && streaming.includes('print(1)'), streaming);
  assert.equal(markdownToHtml('snake_case_name'), '<p>snake_case_name</p>');
});

/* ---------------------------------------------------------------- подписи и расчёты */

test('format: threads are grouped by local calendar day, pinned first', () => {
  const now = new Date(2026, 8, 30, 12, 0, 0);          // среда, 30 сентября 2026, 12:00 местного
  const at = (d, h, m = 0) => new Date(2026, 8, d, h, m).toISOString();
  const items = [
    { id: 'a', updated_at: at(30, 9) },
    { id: 'b', updated_at: at(29, 23, 30) },
    { id: 'c', updated_at: at(27, 10) },
    { id: 'd', updated_at: at(10, 8) },
    { id: 'e', updated_at: at(1, 8), pinned: true },
    { id: 'f' },
  ];
  const groups = groupThreads(items, now);
  assert.deepEqual(groups.map((g) => [g.id, g.items.map((i) => i.id)]),
    [['pinned', ['e']], ['today', ['a']], ['yesterday', ['b']], ['week', ['c']], ['earlier', ['d', 'f']]]);
  assert.deepEqual(groups.map((g) => g.label), ['Закреплённые', 'Сегодня', 'Вчера', 'На этой неделе', 'Ранее']);
  assert.equal(threadTimeLabel(at(30, 9), now), '09:00');
  assert.equal(threadTimeLabel(at(29, 23, 30), now), '23:30');
  assert.equal(threadTimeLabel(at(27, 10), now), 'вс');
  assert.equal(threadTimeLabel(at(10, 8), now), '10.09');
});

test('format: usage math is honest when fields are null or absent', () => {
  assert.deepEqual(contextMeter(null, 131072), { used: null, window: 131072, ratio: null, label: '— / 131k' });
  assert.equal(contextMeter(undefined, null).label, '—');
  const m = contextMeter({ step_tokens_in: 42000, tokens_in: 90000, context_window: 128000 });
  assert.equal(m.label, '42k / 128k');
  assert.equal(m.ratio, 42000 / 128000);
  assert.equal(contextMeter({ tokens_in: 1500, step_tokens_in: null }, null).label, '1.5k / ?');
  assert.equal(tpsLabel({ gen_tps: null }), '— tok/s');
  assert.equal(tpsLabel(null), '— tok/s');
  assert.equal(tpsLabel({ gen_tps: 27.43 }), '27.4 tok/s');
  assert.equal(latencyLabel({ ttft_ms: 1200 }).text, '1.2 s TTFT');
  assert.deepEqual(latencyLabel({ ttft_ms: null }, 800), { text: '0.8 s до 1-го токена', source: 'client' });
  assert.equal(latencyLabel(null, null).text, '— latency');
  assert.equal(costLabel({ cost_usd: 0.5, pricing_known: true, locality: 'local' }, {}), '$0.00 local');
  assert.equal(costLabel({ cost_usd: null, pricing_known: false }, { locality: 'cloud' }), 'цена неизвестна');
  assert.equal(costLabel({ cost_usd: 0.0123, pricing_known: true }, { locality: 'cloud', billing: 'paid_capped' }), '$0.01');
  assert.equal(costLabel({ cost_usd: 0.0042, pricing_known: true }, { billing: 'free_cloud' }), '$0.0042');
  assert.equal(costLabel(null, { locality: 'cloud' }), '—');
  assert.equal(fmtTokens(null), '—');
});

test('format: the picker groups agents by billing and keeps refusals visible', () => {
  const pm = buildPickerModel({
    agents: [
      { id: 1, name: 'Локальный', enabled: true, model: { id: 10, alias: 'qwen-14b', kind: 'local', locality: 'local', context_window: 32768 } },
      { id: 2, name: 'Облако', enabled: true, model: { id: 20, alias: 'free-llm', kind: 'cloud', locality: 'cloud', free: true } },
      { id: 3, name: 'Платный', enabled: true, model: { id: 30, alias: 'big', kind: 'cloud', locality: 'cloud', free: false } },
      { id: 4, name: 'Выключен', enabled: false, model: null },
      { id: 5, name: 'Без модели', enabled: true, model: null },
      { id: 6, name: 'Сеть', enabled: true, model: { id: 60, alias: 'lan-14b', kind: 'local', locality: 'local', locality_detail: 'lan', billing: 'local', usable: true } },
    ],
    subscriptions: [{ name: 'claude', available: true, logged_in: false, via: 'rave' }],
  }, { items: [{ id: 30, billing: 'blocked', usable: false, refusal: 'цена не задана' }] });
  assert.deepEqual(pm.groups.map((g) => [g.id, g.items.map((e) => e.agentId)]), [['local', [1, 6]], ['free', [2]], ['other', [3, 5]]]);
  assert.equal(pm.entries.find((e) => e.agentId === 6).locality, 'lan', 'a LAN model is never reported as on-device');
  const paid = pm.entries.find((e) => e.agentId === 3);
  assert.equal(paid.usable, false);
  assert.equal(paid.refusal, 'цена не задана');
  assert.equal(pm.entries.find((e) => e.agentId === 1).contextWindow, 32768);
  assert.equal(pm.subscriptions[0].logged_in, false);
  assert.equal(pm.auto.label, 'Auto · Local-first');
  assert.deepEqual(buildPickerModel(null, null).groups, []);
});

test('format: the route card claims "на этом устройстве" only for a local route', () => {
  assert.deepEqual(routeCard({ mode: 'agent', locality: 'local' }),
    { tone: 'local', title: 'Локальный режим', sub: 'Все данные на этом устройстве' });
  const cloud = routeCard({ mode: 'agent', locality: 'cloud', provider: 'OpenRouter' });
  assert.equal(cloud.tone, 'cloud');
  assert.ok(!cloud.sub.includes('на этом устройстве'));
  assert.equal(routeCard({ mode: 'auto' }).sub, 'Маршрут выбирается при отправке');
  assert.equal(routeCard({ mode: 'agent', locality: 'lan' }).title, 'Сеть (LAN)');
  assert.ok(!routeCard({ mode: 'agent', locality: 'local_proxy' }).sub.includes('на этом устройстве'));
});

test('format: loopback detection, export, request ids, backoff and rave specs', () => {
  for (const hname of ['127.0.0.1', 'localhost', '::1', '[::1]', '127.1.2.3']) assert.equal(isLoopbackHost(hname), true, hname);
  for (const hname of ['192.168.1.5', '127.evil.test', 'example.com', '']) assert.equal(isLoopbackHost(hname), false, hname);

  const md = exportMarkdown({ title: 'Тест' }, [
    { text: 'Привет', at: '2026-09-30T10:00:00Z', answer: 'Ответ', status: 'completed', model: 'qwen', taskId: 5 },
    { text: 'Второй', error: 'сломалось', status: 'failed' },
  ]);
  assert.ok(md.startsWith('# Тест\n'), md);
  assert.ok(md.includes('## Владелец · 2026-09-30 10:00 UTC'), md);
  assert.ok(md.includes('## Bossman (модель qwen · статус completed · задача #5)'), md);
  assert.ok(md.includes('> Ошибка: сломалось'), md);
  assert.ok(md.endsWith('\n') && !md.endsWith('\n\n'));

  assert.match(clientRequestId(() => 0.5, 1700000000000), /^[A-Za-z0-9._:-]{8,128}$/);
  assert.deepEqual([1, 2, 3, 4, 5, 6].map((n) => backoffDelay(n, () => 0.5)), [500, 1000, 2000, 4000, 8000, 8000]);
  assert.ok(backoffDelay(9, () => 0.999) <= 8000);
  assert.deepEqual(raveAgentSpecs({ local: {}, claude: { logged_in: true }, codex: { logged_in: false } },
    { local: true, claude: true, codex: true }), ['local:local', 'claude:c']);
});

/* ---------------------------------------------------------------- лента и рейв */

test('scroll: stick to the bottom by intent — up un-sticks, near the bottom re-sticks, typing keys do not', () => {
  const at = (top, prevTop, programmatic = false) => ({ type: 'scroll', scrollTop: top, prevTop, scrollHeight: 2000, clientHeight: 500, programmatic });
  assert.equal(nextStuck(true, { type: 'wheel', deltaY: -40 }), false);
  assert.equal(nextStuck(true, { type: 'wheel', deltaY: 40 }), true);
  assert.equal(nextStuck(false, { type: 'wheel', deltaY: 40 }), false, 'wheeling down alone does not re-stick before the bottom');
  for (const key of ['PageUp', 'ArrowUp', 'Home']) assert.equal(nextStuck(true, { type: 'key', key, inInput: false }), false, key);
  assert.equal(nextStuck(true, { type: 'key', key: 'ArrowUp', inInput: true }), true, 'arrows in the message box move the caret');
  assert.equal(nextStuck(true, { type: 'key', key: 'a', inInput: false }), true);
  assert.equal(nextStuck(true, at(1400, 1500)), false, 'a manual scroll up un-sticks (the old 120 px rule pulled it back)');
  assert.equal(nextStuck(true, at(1400, 1500, true)), true, 'our own smooth scroll does not');
  assert.equal(nextStuck(false, at(1500 - STICK_EPSILON, 1400)), true, 'within 4 px of the bottom re-sticks');
  assert.equal(nextStuck(false, at(1500 - STICK_EPSILON - 1, 1400)), false, '5 px away is not the bottom');
  assert.equal(nextStuck(true, at(1450, 1400)), true, 'moving down keeps the state');
  assert.equal(nextStuck(true, { ...at(1498, 1500), userUp: true }), false,
    'a slow trackpad scroll up (2 px, within 4 px of the bottom) still un-sticks: the stream must not yank it back');
  assert.equal(nextStuck(true, { type: 'scroll', scrollTop: 1300, prevTop: 1500, scrollHeight: 1800, clientHeight: 500 }), true,
    'the feed shrank and the browser clamped scrollTop: not the owner, still at the bottom');
  assert.equal(nextStuck(false, { type: 'jump' }), true);
  assert.equal(nextStuck(true, null), true);
});

test('rave: a card is live only while running/paused or an agent still works; "partial" ends it', () => {
  assert.equal(raveIsLive({ id: 'rv-1', status: 'running', data: { agents: [] } }), true);
  assert.equal(raveIsLive({ id: 'rv-1', status: 'paused' }), true);
  assert.equal(raveIsLive({ id: null, status: 'starting' }), false, 'nothing to poll or stop before the server id');
  for (const status of ['partial', 'done', 'stopped', 'error']) {
    assert.equal(raveIsLive({ id: 'rv-1', status, data: { agents: [{ status: 'failed', live: false }, { status: 'blocked' }] } }), false, status);
  }
  assert.equal(raveIsLive({ id: 'rv-1', status: 'partial', data: { agents: [{ status: 'done' }, { status: 'stopping' }] } }), true,
    'an agent still stopping keeps it live');
  assert.equal(raveIsLive({ id: 'rv-1', status: 'partial', data: { agents: [{ status: 'done', live: true }] } }), true);
});

/* ---------------------------------------------------------------- микрофон */

test('audio: WAV header and PCM16 samples are exactly what the transcriber accepts', () => {
  const buf = encodeWavPcm16(new Float32Array([0, 1, -1, 0.5]), 16000);
  const v = new DataView(buf);
  const text = (o, n) => String.fromCharCode(...new Uint8Array(buf, o, n));
  assert.equal(buf.byteLength, 44 + 8);
  assert.equal(text(0, 4), 'RIFF');
  assert.equal(text(8, 4), 'WAVE');
  assert.equal(text(12, 4), 'fmt ');
  assert.equal(text(36, 4), 'data');
  assert.equal(v.getUint16(20, true), 1);        // PCM
  assert.equal(v.getUint16(22, true), 1);        // моно
  assert.equal(v.getUint32(24, true), 16000);
  assert.equal(v.getUint16(34, true), 16);
  assert.equal(v.getUint32(40, true), 8);
  assert.deepEqual([44, 46, 48, 50].map((o) => v.getInt16(o, true)), [0, 32767, -32767, 16384]);
});
