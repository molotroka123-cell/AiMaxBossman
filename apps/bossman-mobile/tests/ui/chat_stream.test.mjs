/* Owner 19.10: «чат на телефоне плохо листается при работе ответа и чуть подлагивает».

   Runs the REAL bossmobile/ui/app.js against the REAL index.html in jsdom with a scripted /m/api, a fake EventSource
   (the answer stream) and a MANUAL requestAnimationFrame (one call of frame() = one display frame), so DOM writes and
   scroll writes can be counted per streamed delta and per frame. BM_UI_DIR=<dir with app.js + index.html> runs the
   same tests against another build (negative control: the 18.10 build fails the perf/scroll tests).
   Skipped when jsdom is not installed. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const UI = process.env.BM_UI_DIR || resolve(here, '..', '..', 'bossmobile', 'ui');
const req = createRequire(import.meta.url);
function loadJsdom() {
  const paths = [process.env.BM_JSDOM_DIR, join(here, '..', '..', 'node_modules'), 'C:/Users/asd/Bossman/_tools/jsdom/node_modules'].filter(Boolean);
  for (const p of paths) { try { return req(join(p, 'jsdom')); } catch { /* next */ } }
  try { return req('jsdom'); } catch { return null; }
}
const jsdom = loadJsdom();
const skip = jsdom ? false : 'jsdom is not installed (set BM_JSDOM_DIR)';

const opened = [];
after(async () => { await new Promise((r) => setTimeout(r, 150)); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(5); }
  assert.fail(`timeout: ${what}`);
}

async function boot({ reduced = false, result = null, turns = null } = {}) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const sources = []; const rafQ = []; let rafId = 0; const scroll = { writes: 0, top: 0, behaviors: [] };
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      if (turns) w.localStorage.setItem('bm.thread', 'T1');
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', '');
        let out = {};
        if (path === '/session') out = { csrf: 'x' };
        else if (path === '/agents') out = { items: [{ id: 1, name: 'Gemma uncensored', tools: [], model: { alias: 'gemma' }, local: true }], default_agent_id: 1 };
        else if (path === '/threads' && method === 'GET') out = { items: [] };
        else if (path === '/threads' && method === 'POST') out = { id: 'T1' };
        else if (path === '/threads/T1' && method === 'GET') out = { id: 'T1', turns: turns || [] };
        else if (path === '/tasks/5/history') out = { events: [] };
        else if (path === '/threads/T1/send') out = { task: { id: 7, status: 'queued' }, admission: { ok: true }, model: { alias: 'gemma' } };
        else if (path === '/tasks/7') out = { id: 7, status: 'completed', result };
        else if (path === '/approvals') out = { items: [] };
        return { ok: true, status: 200, json: async () => out };
      };
      w.EventSource = class { constructor(u) { this.url = u; sources.push(this); } close() { this.closed = true; } };
      w.requestAnimationFrame = (cb) => { rafQ.push(cb); return ++rafId; };
      w.cancelAnimationFrame = () => {};
      w.matchMedia = (q) => ({ matches: reduced && /reduced-motion/.test(q), media: q, addEventListener() {}, removeEventListener() {}, addListener() {} });
      w.HTMLElement.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  const msgs = d.getElementById('messages');
  // geometry: a 600 px viewport; the content grows with the text (2 px per character + 1000 px of earlier history)
  Object.defineProperty(msgs, 'clientHeight', { configurable: true, get: () => 600 });
  Object.defineProperty(msgs, 'scrollHeight', { configurable: true, get: () => 1000 + msgs.textContent.length * 2 });
  Object.defineProperty(msgs, 'scrollTop', { configurable: true, get: () => scroll.top,
    set: (v) => { scroll.writes++; scroll.top = Math.max(0, Math.min(Number(v) || 0, 1000 + msgs.textContent.length * 2 - 600)); } });
  msgs.scrollTo = (o) => { scroll.behaviors.push(o && o.behavior); msgs.scrollTop = o && o.top != null ? o.top : 0; };
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  const frame = () => { const q = rafQ.splice(0); q.forEach((cb) => cb(w.performance.now())); return q.length; };
  const mo = new w.MutationObserver(() => {}); mo.observe(msgs, { childList: true, characterData: true, subtree: true });
  const userScroll = (top) => { scroll.top = top; msgs.dispatchEvent(new w.Event('scroll')); };
  async function ask(text = 'расскажи') {
    await until(() => d.getElementById('model-name').textContent.includes('Gemma'), 'agent loaded');
    d.getElementById('input').value = text;
    d.getElementById('composer').dispatchEvent(new w.Event('submit', { cancelable: true, bubbles: true }));
    await until(() => sources.length === 1, 'stream opened');
    frame(); mo.takeRecords();
    return sources[0];
  }
  const push = (es, ev) => es.onmessage({ data: JSON.stringify({ task_id: 7, ...ev }) });
  return { w, d, msgs, scroll, frame, mo, ask, push, userScroll, sources };
}

const WORDS = Array.from({ length: 100 }, (_, i) => `слово${i} `);

test('perf: 100 streamed deltas are batched per frame — no DOM write per delta, ≤ 1 DOM write per frame', { skip }, async () => {
  const app = await boot();
  const es = await app.ask();
  let syncWrites = 0; let total = 0; const perFrame = []; let frames = 0; let chars = 0;
  // characters (re)written into the DOM: whole text of every inserted node + the growth of every edited text node
  const weigh = (recs) => recs.forEach((r) => {
    if (r.type === 'characterData') chars += Math.max(0, r.target.data.length - (r.oldValue || '').length);
    else r.addedNodes.forEach((n) => { chars += (n.textContent || '').length; });
  });
  app.mo.disconnect(); app.mo.observe(app.msgs, { childList: true, characterData: true, characterDataOldValue: true, subtree: true });
  const w0 = app.scroll.writes;                 // scroll writes of sending the question itself are not counted
  for (let burst = 0; burst < 10; burst++) {
    for (let k = 0; k < 10; k++) {
      app.push(es, { kind: 'run.answer_delta', seq: burst * 10 + k + 1, text: WORDS[burst * 10 + k] });
      const recs = app.mo.takeRecords(); weigh(recs); syncWrites += recs.length; total += recs.length;   // written inside onmessage
    }
    app.frame(); frames++;
    const recs = app.mo.takeRecords(); weigh(recs); perFrame.push(recs.length); total += recs.length;
  }
  const scrollWrites = app.scroll.writes - w0;
  console.log('PERF ' + JSON.stringify({ deltas: 100, frames, dom_mutations_total: total, dom_mutations_in_onmessage: syncWrites,
    dom_mutations_per_frame_max: Math.max(...perFrame.slice(1)), chars_written: chars, answer_chars: WORDS.join('').length,
    scroll_writes: scrollWrites }));
  assert.equal(syncWrites, 0, 'no DOM write inside a delta handler (they are batched to the next frame)');
  assert.ok(perFrame.slice(1).every((n) => n <= 1), `≤ 1 DOM write per frame after the first: ${perFrame}`);
  assert.ok(scrollWrites <= frames, `scroll writes ${scrollWrites} ≤ frames ${frames}`);
  const ans = app.d.querySelector('.msg.bot .answer');
  assert.equal(ans.textContent, WORDS.join(''), 'shown text == concatenation of the deltas');
});

test('final text equals the concatenation of deltas (paragraphs, list, code) and matches the server result', { skip }, async () => {
  const full = 'Первый абзац.\n\nСписок:\n\n- один\n- два\n\n```js\nconst a = 1;\n\nconst b = 2;\n```\n\nКонец';
  const app = await boot({ result: full });          // the task's final result == the streamed text
  const es = await app.ask();
  const parts = full.match(/[\s\S]{1,3}/g);
  parts.forEach((p, i) => { app.push(es, { kind: 'run.answer_delta', seq: i + 1, text: p }); if (i % 4 === 3) app.frame(); });
  app.frame();
  const ans = app.d.querySelector('.msg.bot .answer');
  const streamedText = ans.textContent;
  assert.equal(streamedText.replace(/\s+/g, ''), full.replace(/```js|```/g, '').replace(/^- /gm, '').replace(/\s+/g, ''));
  app.push(es, { kind: 'task.completed', seq: 999 });
  await until(() => !ans.querySelector('.live') && ans.querySelector('pre'), 'final render');
  assert.equal(ans.querySelectorAll('p').length, 3, 'three paragraphs');
  assert.equal(ans.querySelectorAll('li').length, 2, 'list items');
  assert.equal(ans.querySelector('pre code').textContent, 'const a = 1;\n\nconst b = 2;', 'code block kept whole, blank line inside');
  assert.ok(!ans.classList.contains('caret') && !ans.querySelector('.caret'), 'caret gone');
  assert.equal(app.d.querySelector('.msg.bot .meta').textContent.includes('ошибка'), false);
  // no jump at the end: the streamed DOM is exactly what the same answer looks like when the thread is reopened
  const hist = await boot({ turns: [{ idx: 0, task_id: 5, text: 'расскажи', status: 'completed', result: full }] });
  await until(() => hist.d.querySelector('.msg.bot .answer pre'), 'history rendered');
  assert.equal(ans.innerHTML, hist.d.querySelector('.msg.bot .answer').innerHTML, 'stream result == history rendering');
});

test('no auto-scroll when the owner scrolled up while the answer streams; «вниз» appears and brings him back', { skip }, async () => {
  const app = await boot();
  const es = await app.ask();
  app.push(es, { kind: 'run.answer_delta', seq: 1, text: 'начало ' }); app.frame();
  const down = app.d.getElementById('btn-down');
  assert.ok(down, '«вниз» button exists');
  assert.equal(down.hidden, true, 'hidden while following the bottom');
  app.userScroll(0);                                   // the owner flicks up to re-read something
  app.frame();
  assert.equal(down.hidden, false, '«вниз» shown after scrolling up');
  const before = app.scroll.writes;
  for (let i = 0; i < 50; i++) { app.push(es, { kind: 'run.answer_delta', seq: i + 2, text: WORDS[i] }); if (i % 5 === 4) app.frame(); }
  app.frame();
  assert.equal(app.scroll.writes, before, 'nobody yanks the list down while he reads');
  assert.equal(app.scroll.top, 0);
  down.click(); app.frame();
  const bottom = app.msgs.scrollHeight - app.msgs.clientHeight;
  assert.equal(app.scroll.top, bottom, '«вниз» scrolls to the newest text');
  assert.equal(app.scroll.behaviors.at(-1), 'smooth');
  app.msgs.dispatchEvent(new app.w.Event('scroll'));
  assert.equal(down.hidden, true, 'hidden again at the bottom');
  // following again: new text keeps the bottom in view
  app.push(es, { kind: 'run.answer_delta', seq: 200, text: 'ещё текст '.repeat(20) }); app.frame();
  assert.equal(app.scroll.top, app.msgs.scrollHeight - app.msgs.clientHeight, 'follows the bottom again');
});

test('typing indicator until the first token, aria-busy while streaming, copy + regenerate after the answer', { skip }, async () => {
  const app = await boot();
  const es = await app.ask('привет');
  const bot = app.d.querySelector('.msg.bot');
  assert.ok(bot.querySelector('.typing'), 'typing dots while waiting for the first token');
  assert.equal(bot.getAttribute('aria-busy'), 'true');
  app.push(es, { kind: 'run.tool_use', seq: 1, call_id: 'c1', tool: 'x', args: {} }); app.frame();
  assert.ok(bot.querySelector('.typing'), 'still typing during a tool call (no flicker)');
  app.push(es, { kind: 'run.answer_delta', seq: 2, text: 'Здравствуйте' }); app.frame();
  assert.equal(bot.querySelector('.typing'), null, 'dots gone with the first token');
  app.push(es, { kind: 'task.completed', seq: 3 });
  await until(() => bot.getAttribute('aria-busy') === 'false', 'not busy');
  const acts = [...bot.querySelectorAll('.msg-actions button')].map((b) => b.getAttribute('aria-label'));
  assert.deepEqual(acts, ['Копировать ответ', 'Повторить запрос']);
  let copied = null; app.w.navigator.clipboard = { writeText: async (t) => { copied = t; } };
  bot.querySelector('.msg-actions button').click();
  await until(() => copied === 'Здравствуйте', 'copied');
  await until(() => app.d.getElementById('toast').textContent === 'Скопировано', 'toast');
  bot.querySelectorAll('.msg-actions button')[1].click();
  await until(() => app.sources.length === 2, 'regenerate re-sends the same request');
  assert.equal(app.d.querySelectorAll('.msg.user').length, 2);
  assert.equal(app.d.querySelectorAll('.msg.user .bubble')[1].textContent, 'привет');
  // every touch target of the chat is at least 44 px in the stylesheet
  const css = readFileSync(join(UI, 'app.css'), 'utf8');
  assert.match(css, /\.msg-actions button[^{]*\{[^}]*min-height:\s*44px/);
  assert.match(css, /\.down-btn[^{]*\{[^}]*height:\s*44px/);
});

test('reduced motion: animations off (CSS + class) and «вниз» jumps without smooth scrolling', { skip }, async () => {
  const css = readFileSync(join(UI, 'app.css'), 'utf8');
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[^@]*animation: none !important/);
  assert.match(css, /\.reduce-motion \*[^{]*\{[^}]*animation: none !important[^}]*transition: none !important/);
  const app = await boot({ reduced: true });
  assert.ok(app.d.documentElement.classList.contains('reduce-motion'), 'html.reduce-motion set from matchMedia');
  const es = await app.ask();
  app.push(es, { kind: 'run.answer_delta', seq: 1, text: 'x '.repeat(50) }); app.frame();
  app.userScroll(0); app.frame();
  app.d.getElementById('btn-down').click(); app.frame();
  assert.equal(app.scroll.behaviors.at(-1), 'auto', 'no smooth scroll under reduced motion');
  const normal = await boot({ reduced: false });
  assert.equal(normal.d.documentElement.classList.contains('reduce-motion'), false);
});
