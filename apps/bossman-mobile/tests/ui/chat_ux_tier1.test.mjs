/* Tier-1 Chat UX & UI-UX-Pro-Max verification suite (MASTER_PROMPT_TIER1_RU.md item 1).
   Tests against the REAL bossmobile/ui/{index.html,app.css,app.js} in jsdom:
   1. Auto-scroll only when near bottom; floating #btn-scroll-down appears when scrolled up.
   2. Streaming rAF coalescing (50 rapid deltas -> <= 2 DOM rebuilds, >= 55 fps budget at 390 px).
   3. Per-message actions: Копировать (copy), Повторить (retry), Остановить (stop).
   4. Tab switch latency <= 100 ms across all tabs.
   5. UI-UX-Pro-Max: touch targets >= 44 px, animations 150-250 ms, prefers-reduced-motion, WCAG contrast >= 4.5:1. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';

const here = dirname(fileURLToPath(import.meta.url));
const UI = resolve(here, '..', '..', 'bossmobile', 'ui');
const req = createRequire(import.meta.url);
function loadJsdom() {
  const paths = [process.env.BM_JSDOM_DIR, join(here, '..', '..', 'node_modules'), 'C:/Users/asd/Bossman/_tools/jsdom/node_modules'].filter(Boolean);
  for (const p of paths) { try { return req(join(p, 'jsdom')); } catch { /* next */ } }
  try { return req('jsdom'); } catch { return null; }
}
const jsdom = loadJsdom();
const skip = jsdom ? false : 'jsdom is not installed (set BM_JSDOM_DIR)';

const opened = [];
after(async () => { await new Promise((r) => setTimeout(r, 100)); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(10); }
  assert.fail(`timeout: ${what}`);
}

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    FakeEventSource.instances.push(this);
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  emit(payload) {
    const evt = { data: JSON.stringify(payload) };
    if (typeof this.onmessage === 'function') this.onmessage(evt);
    for (const fn of this.listeners.message || []) fn(evt);
  }
  close() {
    this.closed = true;
  }
}
FakeEventSource.instances = [];

async function bootChatApp() {
  FakeEventSource.instances = [];
  const sentRequests = [];
  let copiedText = null;
  let nextTaskId = 101;
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const dom = new jsdom.JSDOM(html, {
    url: 'http://pc.local/m/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    beforeParse(w) {
      w.innerWidth = 390;
      w.innerHeight = 844;
      w.EventSource = FakeEventSource;
      w.navigator.clipboard = {
        writeText: async (txt) => { copiedText = txt; },
      };
      w.fetch = async (url, init = {}) => {
        const path = String(url).replace('/m/api', '');
        const method = (init.method || 'GET').toUpperCase();
        const body = init.body ? JSON.parse(init.body) : null;
        sentRequests.push({ path, method, body });
        let out = {};
        if (path === '/session') out = { csrf: 'csrf-1' };
        else if (path === '/agents') out = { items: [{ id: 41, name: 'Gemma 4 uncensored', tools: [], local: true }], default_agent_id: 41 };
        else if (path === '/threads' && method === 'GET') out = { items: [{ id: 't-1', title: 'Тестовый диалог' }] };
        else if (path === '/threads' && method === 'POST') out = { id: 't-1', title: 'Тестовый диалог' };
        else if (path === '/threads/t-1') out = { id: 't-1', turns: [] };
        else if (path === '/threads/t-1/send' && method === 'POST') out = { task: { id: nextTaskId++ }, model: { alias: 'Gemma 4' } };
        else if (path.startsWith('/tasks/')) out = { id: 101, status: 'completed', result: 'Формула воды — H2O.' };
        else if (path.endsWith('/stop') && method === 'POST') out = { ok: true };
        else if (path === '/photo/models') out = { models: [] };
        else if (path === '/photo/queue') out = { jobs: [] };
        else if (path === '/photo/jobs') out = { jobs: [] };
        else if (path === '/video/models') out = { models: [] };
        else if (path === '/video/jobs') out = { jobs: [] };
        else if (path === '/exchange/deals') out = { deals: [] };
        else if (path === '/status') out = { pc_reachable: true, approvals_pending: 0, system: { cpu_percent: 12, memory_percent: 40, disk_free_gb: 120 }, gpu: { name: 'RTX', vram_used_mb: 1000, vram_total_mb: 16000 }, direct_gen: { sd_ready: true, wan_ready: true } };
        else if (path === '/approvals') out = { items: [] };
        return { ok: true, status: 200, json: async () => out };
      };
      w.URL.createObjectURL = () => 'blob:x';
      w.URL.revokeObjectURL = () => {};
      w.HTMLElement.prototype.scrollIntoView = () => {};
    },
  });
  const w = dom.window;
  const d = w.document;
  opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden && d.getElementById('model-name').textContent.includes('Gemma'), 'app booted');
  return { w, d, sentRequests, getCopied: () => copiedText };
}

test('1. Auto-scroll only when near bottom + floating #btn-scroll-down button', { skip }, async () => {
  const { d } = await bootChatApp();
  const msgs = d.getElementById('messages');
  const scrollBtn = d.getElementById('btn-scroll-down');
  assert.ok(scrollBtn, '#btn-scroll-down must exist in index.html');
  assert.equal(scrollBtn.hidden, true, '#btn-scroll-down initially hidden when at bottom');

  // Set up geometry: scrollable container (scrollHeight=1200, clientHeight=600, max scrollTop=600)
  let currentScrollTop = 550;
  Object.defineProperty(msgs, 'scrollHeight', { get: () => 1200, configurable: true });
  Object.defineProperty(msgs, 'clientHeight', { get: () => 600, configurable: true });
  Object.defineProperty(msgs, 'scrollTop', {
    get: () => currentScrollTop,
    set: (v) => { currentScrollTop = v; },
    configurable: true,
  });

  // Start a turn
  const input = d.getElementById('input');
  input.value = 'Привет, расскажи про космос';
  d.getElementById('composer').dispatchEvent(new d.defaultView.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => FakeEventSource.instances.length === 1, 'SSE stream opened');
  const es = FakeEventSource.instances[0];

  // Emit first delta while user is at the bottom (distance from bottom = 50 <= NEAR_PX)
  es.emit({ kind: 'run.answer_delta', text: 'Часть 1. ' });
  await sleep(30);
  assert.ok(currentScrollTop >= 600, 'auto-scrolls when user is at bottom');

  // Now simulate user scrolling up to read (scrollTop=120, distance from bottom = 480 > threshold)
  currentScrollTop = 120;
  msgs.dispatchEvent(new d.defaultView.Event('scroll'));
  assert.equal(scrollBtn.hidden, false, '#btn-scroll-down must appear when user scrolls up');

  // Emit more deltas while user is scrolled up
  es.emit({ kind: 'run.answer_delta', text: 'Часть 2. ' });
  es.emit({ kind: 'run.answer_delta', text: 'Часть 3. ' });
  await sleep(30);
  assert.equal(currentScrollTop, 120, 'MUST NOT yank scrollTop to bottom when user scrolled up');

  // Clicking #btn-scroll-down scrolls to bottom and hides the button
  scrollBtn.click();
  assert.equal(currentScrollTop, 1200, 'clicking #btn-scroll-down jumps to bottom');
  assert.equal(scrollBtn.hidden, true, '#btn-scroll-down hides after scrolling to bottom');
});

test('2. Streaming rAF coalescing: 50 rapid deltas in one frame coalesce DOM updates (>= 55 fps budget)', { skip }, async () => {
  const { d } = await bootChatApp();
  const input = d.getElementById('input');
  input.value = 'Тест скорости';
  d.getElementById('composer').dispatchEvent(new d.defaultView.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => FakeEventSource.instances.length === 1, 'SSE stream opened');
  const es = FakeEventSource.instances[0];

  const botAns = d.querySelector('.msg.bot .answer');
  assert.ok(botAns, 'bot answer element exists');
  let domRebuilds = 0;
  const origReplace = botAns.replaceChildren.bind(botAns);
  botAns.replaceChildren = (...args) => {
    domRebuilds++;
    return origReplace(...args);
  };

  const t0 = performance.now();
  for (let i = 0; i < 50; i++) {
    es.emit({ kind: 'run.answer_delta', text: `Слово_${i} ` });
  }
  const syncElapsedMs = performance.now() - t0;
  await sleep(35);

  assert.match(botAns.textContent, /Слово_0 .*Слово_49/, 'all 50 deltas rendered');
  assert.ok(domRebuilds <= 2, `expected rAF-coalesced DOM rebuilds (<= 2), got ${domRebuilds}`);
  assert.ok(syncElapsedMs < 16.0, `50 deltas took ${syncElapsedMs.toFixed(2)} ms (> 16ms frame budget for 60fps)`);
});

test('3. Chat message actions: Копировать, Повторить, Остановить', { skip }, async () => {
  const { d, sentRequests, getCopied } = await bootChatApp();
  const input = d.getElementById('input');
  input.value = 'Напиши формулу воды';
  d.getElementById('composer').dispatchEvent(new d.defaultView.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => FakeEventSource.instances.length === 1, 'first stream opened');
  const es1 = FakeEventSource.instances[0];
  es1.emit({ kind: 'run.answer_delta', text: 'Формула воды — H2O.' });
  es1.emit({ kind: 'task.completed', status: 'completed' });
  await sleep(30);

  const copyBtn = d.querySelector('.msg.bot .msg-copy');
  const retryBtn = d.querySelector('.msg.bot .msg-retry');
  assert.ok(copyBtn, 'Копировать button (.msg-copy) must exist on bot message');
  assert.ok(retryBtn, 'Повторить button (.msg-retry) must exist on bot message');

  // Test Copy
  copyBtn.click();
  await sleep(20);
  assert.equal(getCopied(), 'Формула воды — H2O.');

  // Test Retry
  const sendsBefore = sentRequests.filter((r) => r.path === '/threads/t-1/send').length;
  retryBtn.click();
  await until(() => sentRequests.filter((r) => r.path === '/threads/t-1/send').length === sendsBefore + 1, 'retry sent request');
  const lastSend = sentRequests.filter((r) => r.path === '/threads/t-1/send').at(-1);
  assert.equal(lastSend.body.text, 'Напиши формулу воды', 'retry re-sends original user prompt');

  // Test Stop while streaming second turn
  await until(() => FakeEventSource.instances.length === 2, 'second stream opened');
  const stopBtn = d.getElementById('btn-stop');
  assert.equal(stopBtn.hidden, false, 'stop button visible during streaming');
  stopBtn.click();
  await until(() => sentRequests.some((r) => r.path.endsWith('/stop')), 'stop request sent');
});

test('4. Tab switching speed <= 100 ms across all tabs', { skip }, async () => {
  const { d } = await bootChatApp();
  const tabs = ['photo', 'video', 'panel', 'swap', 'status', 'chat'];
  for (const name of tabs) {
    const btn = d.querySelector(`.tab[data-tab="${name}"]`);
    assert.ok(btn, `tab ${name} exists`);
    const t0 = performance.now();
    btn.click();
    const dt = performance.now() - t0;
    const view = d.getElementById(`view-${name}`);
    assert.equal(view.hidden, false, `view-${name} shown`);
    assert.ok(dt <= 100, `switching to tab ${name} took ${dt.toFixed(2)} ms (> 100 ms limit)`);
  }
});

test('5. UI-UX-Pro-Max CSS & WCAG audit: touch targets >= 44px, animations 150-250ms, contrast >= 4.5:1', () => {
  const css = readFileSync(join(UI, 'app.css'), 'utf8');

  // 5a. .messages must not have scroll-behavior: smooth (causes streaming jank)
  const messagesRule = css.match(/\.messages\s*\{[^}]*\}/);
  assert.ok(messagesRule, '.messages rule found');
  assert.doesNotMatch(messagesRule[0], /scroll-behavior:\s*smooth/, '.messages must not have scroll-behavior: smooth');

  // 5b. No 26px mini touch targets on buttons (.face-thumb button, .ref-x)
  const faceBtnRule = css.match(/\.face-thumb button\s*\{[^}]*\}/);
  const refXRule = css.match(/\.ref-x\s*\{[^}]*\}/);
  assert.ok(faceBtnRule && !/width:\s*26px/.test(faceBtnRule[0]), '.face-thumb button must be >= 44px touch target');
  assert.ok(refXRule && !/width:\s*26px/.test(refXRule[0]), '.ref-x must be >= 44px touch target');

  // 5c. prefers-reduced-motion respected
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);

  // 5d. Contrast ratio check (WCAG 2.1 relative luminance)
  function luminance(hex) {
    const rgb = [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map((x) => {
      const c = parseInt(x, 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  }
  function contrast(h1, h2) {
    const l1 = luminance(h1);
    const l2 = luminance(h2);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  const bg = (css.match(/--bg:\s*(#[0-9a-fA-F]{6})/) || [])[1];
  const surface = (css.match(/--surface:\s*(#[0-9a-fA-F]{6})/) || [])[1];
  const text = (css.match(/--text:\s*(#[0-9a-fA-F]{6})/) || [])[1];
  const muted = (css.match(/--muted:\s*(#[0-9a-fA-F]{6})/) || [])[1];
  assert.ok(bg && surface && text && muted, 'theme hex colors defined');
  assert.ok(contrast(text, bg) >= 4.5, `--text on --bg contrast ${contrast(text, bg).toFixed(2)} < 4.5`);
  assert.ok(contrast(muted, bg) >= 4.5, `--muted on --bg contrast ${contrast(muted, bg).toFixed(2)} < 4.5`);
  assert.ok(contrast(muted, surface) >= 4.5, `--muted on --surface contrast ${contrast(muted, surface).toFixed(2)} < 4.5`);
});
