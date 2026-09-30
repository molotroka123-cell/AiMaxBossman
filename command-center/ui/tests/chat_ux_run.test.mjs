/* Регрессии, найденные при прогоне /chat.html в настоящем браузере (RUN_20261001_CHAT).

   Каждый тест здесь красный на коде до правки. Запускается из
   command-center/tests/test_chat_ux_run.py (node --test; на node < 20.19
   с флагом --experimental-detect-module, см. там). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve as resolvePath } from 'node:path';
import { pathToFileURL } from 'node:url';

import { markdownToHtml, parseInline } from '../chat/markdown.js';

/* ---------------------------------------------------------------- markdown: ссылка в подписи ссылки */

function countTag(html, tag) {
  return (html.match(new RegExp(`<${tag}[\\s>]`, 'g')) || []).length;
}

test('markdown: [https://a.com](https://a.com) is ONE link, never an <a> inside an <a>', () => {
  const html = markdownToHtml('см. [https://a.com](https://a.com) и дальше');
  assert.equal(countTag(html, 'a'), 1, html);
  assert.ok(html.includes('<a href="https://a.com" target="_blank" rel="noopener noreferrer">https://a.com</a>'), html);
});

test('markdown: an address inside the label of a link to another address stays text of THAT link', () => {
  const html = markdownToHtml('[см. https://x.org тут](https://a.com)');
  assert.equal(countTag(html, 'a'), 1, html);
  assert.ok(html.includes('href="https://a.com"'), html);
  assert.ok(html.includes('см. https://x.org тут'), html);
});

test('markdown: negative control - a bare address and a plain labelled link are still links', () => {
  assert.equal(countTag(markdownToHtml('https://b.org'), 'a'), 1);
  assert.equal(countTag(markdownToHtml('[docs](https://a.com) и https://b.org'), 'a'), 2);
  const nodes = parseInline('[docs](https://a.com)');
  assert.equal(nodes[0].type, 'link');
  assert.equal(nodes[0].children[0].type, 'text');
});

test('markdown: emphasis inside a link label still works (only nested links are refused)', () => {
  const html = markdownToHtml('[**важно**](https://a.com)');
  assert.equal(countTag(html, 'a'), 1);
  assert.ok(html.includes('<strong>важно</strong>'), html);
});

/* ---------------------------------------------------------------- микрофон: отмена записи */

/* CHAT_UX_AUDIO_MODULE — путь к другой версии audio.js (доказательство «красный на старом коде») */
const audioMod = await import(process.env.CHAT_UX_AUDIO_MODULE
  ? pathToFileURL(resolvePath(process.env.CHAT_UX_AUDIO_MODULE)).href : '../chat/audio.js');

function installFakeMedia(uncaught) {
  class FakeRecorder {
    constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      /* как в браузере: onstop приходит уже ПОСЛЕ возврата из stop() */
      setTimeout(() => { try { this.onstop && this.onstop(); } catch (err) { uncaught.push(err); } }, 0);
    }
  }
  globalThis.MediaRecorder = FakeRecorder;
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, writable: true,
    value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } },
  });
}

test('audio: cancelling a recording does not throw from the recorder onstop handler', async () => {
  const uncaught = [];
  installFakeMedia(uncaught);
  const rec = new audioMod.Recorder();
  await rec.start();
  rec.cancel();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(uncaught.map(String), [], 'onstop after cancel() must not touch the cleared recorder');
  assert.equal(rec.rec, null);
});

test('audio: negative control - stop() still resolves a Blob of the recorded type', async () => {
  const uncaught = [];
  installFakeMedia(uncaught);
  const rec = new audioMod.Recorder();
  await rec.start();
  rec.rec.ondataavailable({ data: new Blob([new Uint8Array([1, 2, 3])]) });
  const blob = await rec.stop();
  assert.equal(blob.type, 'audio/webm');
  assert.equal(blob.size, 3);
  assert.deepEqual(uncaught, []);
});

/* ---------------------------------------------------------------- скрытое рассуждение в итоге + дубль ответа */

const stateMod = await import(process.env.CHAT_UX_STATE_MODULE
  ? pathToFileURL(resolvePath(process.env.CHAT_UX_STATE_MODULE)).href : '../chat/state.js');
const { createTurn, applyEvent, applyTruth, displaySegments, answerText } = stateMod;

function thinkTurn() {
  /* как в браузерном прогоне: поток уже без <think> (сервер вырезает), а итог шага и задачи хранят его как есть */
  const turn = createTurn({ taskId: 7, text: 'THINK' });
  const live = 'Открытый ответ без рассуждений. Открытый ответ без рассуждений. ';
  const ev = (seq, kind, extra) => ({ seq, kind, run_id: 1, step: 1, attempt: 0, ...extra });
  applyEvent(turn, ev(1, 'run.answer_delta', { idx: 0, text: live }));
  applyEvent(turn, ev(2, 'run.assistant_message', { streamed: true, text: `<think>скрытая цепочка думай-тег</think>${live.trim()}` }));
  applyEvent(turn, ev(3, 'task.completed', {}));
  applyTruth(turn, { status: 'completed', result: `<think>скрытая цепочка думай-тег</think>${live.trim()}` });
  return turn;
}

test('think block in the stored answer is never shown and the streamed answer is not shown twice', () => {
  const turn = thinkTurn();
  const segs = displaySegments(turn).filter((s) => s.type === 'text');
  const shown = segs.map((s) => s.text).join('\n');
  assert.equal(segs.length, 1, `one text segment, got ${segs.length}: ${JSON.stringify(segs.map((s) => s.text))}`);
  assert.ok(!/<\/?think/i.test(shown), shown);
  assert.ok(!shown.includes('думай-тег'), shown);
  assert.ok(!answerText(turn).includes('думай-тег'));
  assert.ok(shown.includes('Открытый ответ без рассуждений.'));
});

test('think: an unclosed block at the start, a lone closing tag and fenced code are handled', () => {
  const t1 = createTurn({ taskId: 1 });
  applyEvent(t1, { seq: 1, kind: 'run.assistant_message', run_id: 1, step: 1, streamed: false, text: '<think>ещё думаю, закрытия нет' });
  assert.equal(displaySegments(t1).filter((s) => s.type === 'text').length, 0, 'nothing of an unfinished hidden block is shown');

  const t2 = createTurn({ taskId: 2 });
  applyEvent(t2, { seq: 1, kind: 'run.assistant_message', run_id: 1, step: 1, streamed: false, text: 'рассуждение без открывающего тега</think>Ответ.' });
  assert.equal(displaySegments(t2)[0].text, 'Ответ.');

  const code = 'Пример:\n```html\n<think>это пример тега в коде</think>\n```\nГотово.';
  const t3 = createTurn({ taskId: 3 });
  applyEvent(t3, { seq: 1, kind: 'run.assistant_message', run_id: 1, step: 1, streamed: false, text: code });
  assert.equal(displaySegments(t3)[0].text, code, 'a tag inside a fenced code block is content, not reasoning');
});

test('negative control: a final answer that really differs from the streamed text is still shown', () => {
  const turn = createTurn({ taskId: 8 });
  applyEvent(turn, { seq: 1, kind: 'run.answer_delta', run_id: 1, step: 1, attempt: 0, idx: 0, text: 'черновик' });
  applyTruth(turn, { status: 'completed', result: 'совсем другой итог' });
  const texts = displaySegments(turn).filter((s) => s.type === 'text').map((s) => s.text.trim());
  assert.deepEqual(texts, ['черновик', 'совсем другой итог']);
});

test('negative control: the same answer differing only in whitespace is shown once', () => {
  const turn = createTurn({ taskId: 9 });
  applyEvent(turn, { seq: 1, kind: 'run.answer_delta', run_id: 1, step: 1, attempt: 0, idx: 0, text: 'Строка один.\n\n\nСтрока  два.  ' });
  applyTruth(turn, { status: 'completed', result: 'Строка один.\n\nСтрока два.' });
  assert.equal(displaySegments(turn).filter((s) => s.type === 'text').length, 1);
});

/* ---------------------------------------------------------------- место работы модели по её псевдониму (история, F5) */

const fmt = await import('../chat/format.js');

const OPTIONS = {
  agents: [
    { id: 1, name: 'Локальный', enabled: true, model: { id: 1, alias: 'qwen-local', kind: 'local', locality: 'local', locality_detail: 'local', free: true } },
    { id: 2, name: 'Облако', enabled: true, model: { id: 2, alias: 'cloud-x', kind: 'cloud', locality: 'cloud', locality_detail: 'cloud', free: false } },
    { id: 3, name: 'Дубль A', enabled: true, model: { id: 3, alias: 'same', kind: 'local', locality: 'local', locality_detail: 'local', free: true } },
    { id: 4, name: 'Дубль B', enabled: true, model: { id: 4, alias: 'same', kind: 'cloud', locality: 'cloud', locality_detail: 'cloud', free: false } },
  ],
};

test('place of work for a reopened turn comes from the model alias (no LOCAL badge lost after F5)', () => {
  assert.equal(typeof fmt.placeOfAlias, 'function', 'placeOfAlias is exported');
  const pm = fmt.buildPickerModel(OPTIONS, null);
  assert.deepEqual(fmt.placeOfAlias(pm, 'qwen-local'), { locality: 'local', billing: 'local' });
  assert.equal(fmt.placeOfAlias(pm, 'cloud-x').locality, 'cloud');
  /* стоимость такого хода — «$0.00 local», а не «цена неизвестна» */
  const place = fmt.placeOfAlias(pm, 'qwen-local');
  assert.equal(fmt.costLabel({ cost_usd: 0, pricing_known: false }, place), '$0.00 local');
});

test('place of work: negative controls - unknown alias, no picker data and an ambiguous alias give no guess', () => {
  const pm = fmt.buildPickerModel(OPTIONS, null);
  assert.equal(fmt.placeOfAlias(pm, 'нет-такой'), null);
  assert.equal(fmt.placeOfAlias(pm, ''), null);
  assert.equal(fmt.placeOfAlias(null, 'qwen-local'), null);
  assert.equal(fmt.placeOfAlias(pm, 'same'), null, 'two agents share the alias with different places: do not guess LOCAL');
  assert.equal(fmt.costLabel({ cost_usd: 0, pricing_known: false }, {}), 'цена неизвестна');
});

/* ---------------------------------------------------------------- панель: десять одинаковых строк «Проверка» */

const panelMod = await import('../chat/panel.js');

test('panel: identical verifier verdicts are grouped into one row with a count', () => {
  assert.equal(typeof panelMod.groupEvaluations, 'function', 'groupEvaluations is exported');
  const ten = Array.from({ length: 10 }, () => ({ verdict: 'NOT_APPLICABLE', reasons: '' }));
  const rows = panelMod.groupEvaluations([...ten, { verdict: 'PASS', reasons: 'файл создан' }]);
  assert.deepEqual(rows, [
    { verdict: 'NOT_APPLICABLE', reasons: '', count: 10 },
    { verdict: 'PASS', reasons: 'файл создан', count: 1 },
  ]);
});

test('panel: negative control - different reasons or verdicts are never merged, order is kept', () => {
  const rows = panelMod.groupEvaluations([
    { verdict: 'FAIL', reasons: 'a' }, { verdict: 'FAIL', reasons: 'b' }, { verdict: 'FAIL', reasons: 'a' }, { verdict: 'PASS', reasons: 'a' },
  ]);
  assert.deepEqual(rows.map((r) => [r.verdict, r.reasons, r.count]), [['FAIL', 'a', 2], ['FAIL', 'b', 1], ['PASS', 'a', 1]]);
  assert.deepEqual(panelMod.groupEvaluations([]), []);
});

/* ---------------------------------------------------------------- тихий поток: ложное «Связь потеряна» */

const { TaskStream } = await import(process.env.CHAT_UX_STREAM_MODULE
  ? pathToFileURL(resolvePath(process.env.CHAT_UX_STREAM_MODULE)).href : '../chat/stream.js');

async function settle(rounds = 40) {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/** Ответ, который отдаёт кадры и потом молчит (как сервер с долгим первым токеном) до abort. */
function quietResponse(signal, frames) {
  const enc = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f));
      signal.addEventListener('abort', () => controller.error(new Error('aborted')));
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

function harness(second) {
  const urls = [];
  const states = [];
  const timers = [];
  const stream = new TaskStream({
    taskId: 5,
    idleMs: 1111,
    silentMs: 2222,
    fetchImpl: async (url, opts) => {
      urls.push(url);
      if (urls.length === 1) {
        return quietResponse(opts.signal, ['data: {"kind":"stream.open","after":0}\n\n',
          'id: 4\ndata: {"kind":"run.answer_delta","seq":4,"run_id":1,"step":1,"attempt":0,"idx":0,"text":"a"}\n\n']);
      }
      return second(url, opts);
    },
    setTimer: (fn, ms) => { timers.push({ fn, ms, live: true }); return timers.length; },
    clearTimer: (id) => { if (timers[id - 1]) timers[id - 1].live = false; },
    random: () => 0.5,
    onState: (s, info) => states.push([s, info && info.delay_ms]),
  });
  return { stream, urls, states, timers };
}

test('stream: a silent server (no frames for the idle window) reconnects quietly - no false "connection lost" banner', async () => {
  const h = harness((url, opts) => quietResponse(opts.signal, ['data: {"kind":"stream.open","after":4}\n\n']));
  h.stream.start();
  await settle();
  assert.deepEqual(h.states.map((s) => s[0]), ['connecting', 'open']);
  h.timers.find((t) => t.ms === 1111 && t.live).fn();          // сработал сторож тишины
  await settle();
  assert.equal(h.urls[1], '/api/events/stream?task_id=5&after=4', 'reconnects with the last seq, at once');
  assert.ok(!h.states.some((s) => s[0] === 'reconnecting'), `no banner state, got ${JSON.stringify(h.states)}`);
  assert.equal(h.states[h.states.length - 1][0], 'open');
  h.stream.stop();
});

test('stream: negative control - a silent reconnect that FAILS still shows the banner with the real pause', async () => {
  const h = harness(() => Promise.reject(new TypeError('network down')));
  h.stream.start();
  await settle();
  h.timers.find((t) => t.ms === 1111 && t.live).fn();
  await settle();
  assert.deepEqual(h.states[h.states.length - 1], ['reconnecting', 500]);
  h.stream.stop();
});

test('stream: negative control - a silent reconnect that HANGS shows the banner after silentMs', async () => {
  const h = harness(() => new Promise(() => {}));
  h.stream.start();
  await settle();
  h.timers.find((t) => t.ms === 1111 && t.live).fn();
  await settle();
  assert.ok(!h.states.some((s) => s[0] === 'reconnecting'), 'not yet: the reconnect may be just slow');
  h.timers.find((t) => t.ms === 2222 && t.live).fn();
  assert.equal(h.states[h.states.length - 1][0], 'reconnecting');
  h.stream.stop();
});

/* ---------------------------------------------------------------- Claude-коннектор со старым CLI (просьба lane rave-apps) */

test('rave agents: Claude with an outdated CLI (version_ok === false) is not offered; current or unknown is', () => {
  const pick = { local: true, claude: true, codex: true };
  const base = { local: {}, codex: { logged_in: true } };
  assert.deepEqual(fmt.raveAgentSpecs({ ...base, claude: { logged_in: true, version_ok: false } }, pick), ['local:local', 'codex:x']);
  assert.deepEqual(fmt.raveAgentSpecs({ ...base, claude: { logged_in: true, version_ok: true } }, pick), ['local:local', 'claude:c', 'codex:x']);
  assert.deepEqual(fmt.raveAgentSpecs({ ...base, claude: { logged_in: true } }, pick), ['local:local', 'claude:c', 'codex:x'],
    'an older server without the field does not block Claude');
  assert.deepEqual(fmt.raveAgentSpecs({ ...base, claude: { logged_in: false, version_ok: true } }, pick), ['local:local', 'codex:x']);
});

test('rave agents: the owner is told WHY a picked subscription agent was left out', () => {
  assert.equal(typeof fmt.raveSkipReasons, 'function', 'raveSkipReasons is exported');
  const conn = { claude: { logged_in: true, version_ok: false, version: '2.0.1', version_problem: 'версия Claude Code CLI 2.0.1 старше 2.1.259: обновите CLI' },
    codex: { logged_in: false } };
  const reasons = fmt.raveSkipReasons(conn, { claude: true, codex: true });
  assert.equal(reasons.length, 2);
  assert.ok(reasons[0].startsWith('Claude') && reasons[0].includes('2.1.259'), reasons[0]);
  assert.ok(reasons[1].startsWith('Codex') && reasons[1].includes('вход'), reasons[1]);
  /* негативный контроль: не выбранный и готовый агенты причин не дают */
  assert.deepEqual(fmt.raveSkipReasons(conn, { claude: false, codex: false }), []);
  assert.deepEqual(fmt.raveSkipReasons({ claude: { logged_in: true, version_ok: true } }, { claude: true }), []);
  /* без пояснения сервера — честный общий текст, а не пустая строка */
  assert.ok(fmt.raveSkipReasons({ claude: { logged_in: true, version_ok: false } }, { claude: true })[0].includes('устарел'));
});

test('subscription state: an outdated Claude CLI reads as not ready, with the version text', () => {
  const st = fmt.subscriptionState({ available: true, logged_in: true, version_ok: false, version_problem: 'версия Claude Code CLI 2.0.1 старше 2.1.259' });
  assert.equal(st.ok, false);
  assert.ok(st.text.includes('2.1.259'), st.text);
  assert.deepEqual(fmt.subscriptionState({ available: true, logged_in: true, version_ok: true }), { text: 'вход выполнен', ok: true });
  assert.deepEqual(fmt.subscriptionState({ available: true, logged_in: true }), { text: 'вход выполнен', ok: true });
});
