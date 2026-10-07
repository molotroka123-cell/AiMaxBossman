import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// The module must be importable in Node: no top-level document, window or fetch.
assert.equal(typeof document, 'undefined');
const mod = await import('../pages/telegram_calls.js');
const { accountStep, callWords, controlState, peerPickState, isTestMode, summarizeHistoryItem, fmtMs, errorText, answeringWords, BASE, EVENT_KINDS } = mod;

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(join(here, '..', 'pages', 'telegram_calls.js'), 'utf8');
const INDEX = readFileSync(join(here, '..', 'pages', 'index.js'), 'utf8');

const READY = {
  mode: 'telegram', transport: 'telegram', enabled: true, peer: { user_id: 7, label: 'Второй' },
  account: { state: 'ready', has_api: true }, call: null, call_active: false, stop: { call: false, global: false, active: false },
  uncertain_previous: false, deps: { ready: true, missing: [] }, latency: { n: 0 }, models: {},
};
const st = (over = {}) => ({ ...READY, ...over });

test('the page exports exactly the manifest shape the lazy registry expects', () => {
  const page = mod.default;
  assert.deepEqual(Object.keys(page).sort(), ['icon', 'id', 'nav', 'onEvent', 'render', 'section', 'title']);
  assert.equal(page.id, 'telegram_calls');
  assert.equal(page.title, 'Telegram-звонки');
  assert.equal(page.nav, 'more');
  assert.equal(page.section, 'apps');
  assert.equal(BASE, '/api/telegram/calls');
  assert.match(INDEX, /id: 'telegram_calls', title: 'Telegram-звонки', icon: 'activity', nav: 'more', section: 'apps'/);
});

test('onEvent never re-renders the page (typed code and 2FA must survive bus events)', () => {
  assert.equal(mod.default.onEvent({ kind: 'telegram_call.state' }), false);
  assert.equal(mod.default.onEvent({ kind: 'telegram_call.ended' }), false);
  assert.equal(mod.default.onEvent({ kind: 'task.created' }), false);
  assert.equal(mod.default.onEvent(undefined), false);
});

test('one canonical API prefix and one pair of bus event names; the old aliases are gone', () => {
  assert.equal(BASE, '/api/telegram/calls');
  assert.deepEqual(EVENT_KINDS, ['telegram_call.state', 'telegram_call.ended']);
  assert.doesNotMatch(SOURCE, /['"`]\/api\/calls/, 'the removed /api/calls alias must not come back in the page');
  assert.doesNotMatch(SOURCE, /['"]calls\.(state|ended)['"]/, 'the removed calls.* bus events must not come back in the page');
});

test('empty state: nothing can be dialled, every disabled button says why, STOP is always available', () => {
  for (const status of [null, st({ account: { state: 'no_credentials' }, peer: null, enabled: false })]) {
    const c = controlState(status);
    assert.equal(c.dial.disabled, true);
    assert.ok(c.dial.title.length > 10);
    assert.equal(c.hangup.disabled, true);
    assert.ok(c.hangup.title);
    assert.equal(c.resume.disabled, true);
    assert.ok(c.resume.title);
    assert.equal(c.stop.disabled, false);
  }
  assert.match(controlState(st({ account: { state: 'no_credentials' } })).dial.title, /подключите аккаунт/i);
});

test('the reasons to refuse a dial are reported in the order the owner can fix them', () => {
  assert.match(controlState(st({ account: { state: 'logged_out' } })).dial.title, /подключите аккаунт/i);
  assert.match(controlState(st({ peer: null })).dial.title, /собеседника/i);
  assert.match(controlState(st({ enabled: false })).dial.title, /Разрешить звонки/);
  assert.match(controlState(st({ stop: { call: true, active: true } })).dial.title, /Продолжить/);
  assert.match(controlState(st({ stop: { call: false, global: true, active: true } })).dial.title, /общий STOP/i);
  assert.match(controlState(st({ call_active: true, call: { state: 'active' } })).dial.title, /уже идёт/);
  assert.match(controlState(st({ deps: { ready: false, missing: ['telethon'] } })).dial.title, /зависимост/i);
  const ok = controlState(st());
  assert.equal(ok.dial.disabled, false);
  assert.equal(ok.hangup.disabled, true);
});

test('a test-mode call does not require the real dependencies, a real one does (paired negative control)', () => {
  const noDeps = { ready: false, missing: ['telethon'] };
  assert.equal(controlState(st({ mode: 'offline_test', transport: 'loopback', deps: noDeps })).dial.disabled, false);
  assert.equal(controlState(st({ deps: noDeps })).dial.disabled, true);
});

test('after an uncertain call the dial needs the explicit confirmation, and STOP still wins over it', () => {
  const uncertain = st({ uncertain_previous: true });
  assert.equal(controlState(uncertain).dial.disabled, true);
  assert.match(controlState(uncertain).dial.title, /неизвестно/);
  assert.equal(controlState(uncertain, { confirmUnknown: true }).dial.disabled, false);
  const stopped = st({ uncertain_previous: true, stop: { call: true, active: true } });
  assert.equal(controlState(stopped, { confirmUnknown: true }).dial.disabled, true, 'a confirm never overrides STOP');
});

test('during a call: hangup available, dial not, STOP always, resume only while STOP is set', () => {
  const during = controlState(st({ call_active: true, call: { state: 'active', phase: 'speaking' } }));
  assert.equal(during.dial.disabled, true);
  assert.equal(during.hangup.disabled, false);
  assert.equal(during.stop.disabled, false);
  assert.equal(during.resume.disabled, true);
  const afterStop = controlState(st({ stop: { call: true, active: true } }));
  assert.equal(afterStop.resume.disabled, false);
  assert.equal(afterStop.stop.disabled, false);
});

test('every button in every state either is enabled or carries a title with a reason', () => {
  const account = ['no_credentials', 'logged_out', 'code_sent', 'password_needed', 'ready', 'error'];
  for (const a of account) for (const enabled of [true, false]) for (const peer of [null, { user_id: 1 }]) {
    for (const stop of [{ call: false, active: false }, { call: true, active: true }, { global: true, active: true }]) {
      for (const uncertain of [true, false]) {
        const c = controlState(st({ account: { state: a }, enabled, peer, stop, uncertain_previous: uncertain }));
        for (const [name, b] of Object.entries(c)) {
          assert.ok(b.title && b.title.length > 3, `${name} has no title`);
          if (name === 'stop') assert.equal(b.disabled, false);
        }
      }
    }
  }
});

test('the connection steps are worded, appear one by one and never show a raw status', () => {
  const steps = ['no_credentials', 'logged_out', 'code_sent', 'password_needed', 'ready'].map((s) => accountStep({ account: { state: s } }));
  assert.deepEqual(steps.map((s) => s.key), ['api', 'phone', 'code', 'password', 'ready']);
  assert.deepEqual(steps.map((s) => s.n), [1, 2, 3, 4, 5]);
  for (const s of steps) assert.doesNotMatch(s.text, /code_sent|password_needed|no_credentials|logged_out/);
  assert.equal(accountStep(null).key, 'api');
  assert.equal(accountStep({ account: { state: 'nonsense' } }).key, 'api');
});

test('live state is worded; the phase only shows while the call is active', () => {
  assert.deepEqual(callWords(st()), { state: 'Нет звонка', phase: '', live: false });
  assert.equal(callWords(st({ call: { state: 'dialing' } })).state, 'Набираем…');
  assert.equal(callWords(st({ call: { state: 'ringing' } })).state, 'Звонит…');
  const active = callWords(st({ call: { state: 'active', phase: 'thinking' } }));
  assert.deepEqual([active.state, active.phase, active.live], ['Идёт разговор', 'думает', true]);
  assert.equal(callWords(st({ call: { state: 'ringing', phase: 'speaking' } })).phase, '');
});

test('the red banner is shown for the offline test mode and for a loopback transport, never for a real call', () => {
  assert.equal(isTestMode({ mode: 'offline_test' }), true);
  assert.equal(isTestMode({ mode: 'telegram', transport: 'loopback' }), true);
  assert.equal(isTestMode({ test_label: true }), true);
  assert.equal(isTestMode({ mode: 'telegram', transport: 'telegram' }), false);
  assert.equal(isTestMode(null), false);
  assert.match(SOURCE, /ТЕСТ БЕЗ TELEGRAM/);
});

test('choosing the peer needs a connected account AND the explicit second-account confirmation', () => {
  assert.equal(peerPickState(st({ account: { state: 'logged_out' } }), true).disabled, true);
  assert.equal(peerPickState(st(), false).disabled, true);
  assert.match(peerPickState(st(), false).title, /второй аккаунт/);
  assert.equal(peerPickState(st(), true).disabled, false);
});

test('the dial request has no peer of any kind, only the optional confirmation', () => {
  const start = SOURCE.indexOf('async dial()');
  const body = SOURCE.slice(start, SOURCE.indexOf('async hangup()'));
  assert.ok(start > 0 && body.includes(`/call`));
  assert.doesNotMatch(body, /peer|user_id|username/);
  assert.match(body, /confirm_unknown/);
});

test('secrets: inputs are hidden or one-time, cleared after sending, never put into the DOM as text', () => {
  assert.match(SOURCE, /name: 'tc-api-hash'/);
  assert.match(SOURCE, /type: 'password', autocomplete: 'off', name: 'tc-api-hash'/);
  assert.match(SOURCE, /type: 'password', autocomplete: 'off', name: 'tc-password'/);
  assert.match(SOURCE, /this\.refs\.code\.value = '';\s*await this\.run/);
  assert.match(SOURCE, /this\.refs\.password\.value = '';\s*await this\.run/);
  assert.doesNotMatch(SOURCE, /innerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(SOURCE, /localStorage|sessionStorage/, 'nothing about the account is kept in the browser');
});

test('polling stops itself when the page leaves the document and there is a single interval', () => {
  assert.match(SOURCE, /if \(!this\.root\.isConnected\) \{ clearInterval\(this\.timer\)/);
  assert.equal((SOURCE.match(/setInterval\(/g) || []).length, 1);
  assert.match(SOURCE, /POLL_MS = 1500/);
});

test('every classList.add/toggle literal has a CSS rule (an invisible state is a bug)', () => {
  const css = SOURCE.slice(SOURCE.indexOf('const CSS = `'), SOURCE.indexOf('`;', SOURCE.indexOf('const CSS = `')));
  const shared = readFileSync(join(here, '..', 'style.css'), 'utf8') + readFileSync(join(here, '..', 'theme.css'), 'utf8');
  const used = [...SOURCE.matchAll(/classList\.(?:add|toggle)\(\s*'([a-z0-9-]+)'/g)].map((m) => m[1]);
  assert.ok(used.includes('tc-live-on') && used.includes('busy'));
  for (const cls of used) {
    assert.ok(new RegExp(`\\.${cls}\\b`).test(css) || new RegExp(`\\.${cls}\\b`).test(shared), `no CSS for .${cls}`);
  }
});

test('the styles use theme tokens only (both themes), no hard-coded page colours except the red test banner', () => {
  const css = SOURCE.slice(SOURCE.indexOf('const CSS = `'), SOURCE.indexOf('`;', SOURCE.indexOf('const CSS = `')));
  const hex = css.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepEqual([...new Set(hex)].sort(), ['#b3261e', '#fff']);
  assert.match(css, /@media\(max-width:720px\)/);
});

test('history rows: outcome words, test label, memory and draft state', () => {
  const written = summarizeHistoryItem({ outcome: 'completed', transport: 'loopback', turns: [{}, {}], latency_ms: { p50: 512.3 },
    summary: { text: 'Коротко.', agreed_tasks: ['a', 'b'] }, postcall: { memory: { status: 'written', file: 'telegram-call-x.md' }, drafts: { count: 2 } } });
  assert.equal(written.outcome, 'разговор завершён');
  assert.equal(written.test, true);
  assert.equal(written.turns, 2);
  assert.equal(written.memorySaved, true);
  assert.equal(written.draftsCreated, true);
  assert.match(written.memoryText, /telegram-call-x\.md/);
  const bare = summarizeHistoryItem({ outcome: 'unknown', transport: 'telegram', turns: [], summary: null });
  assert.equal(bare.test, false);
  assert.equal(bare.memorySaved, false);
  assert.equal(bare.proposals, 0);
  assert.equal(summarizeHistoryItem({ outcome: 'weird' }).outcome, 'weird');
});

test('formatting and error text helpers never invent numbers', () => {
  assert.equal(fmtMs(null), '—');
  assert.equal(fmtMs(undefined), '—');
  assert.equal(fmtMs(NaN), '—');
  assert.equal(fmtMs(511.6), '512 мс');
  assert.equal(errorText(null), null);
  assert.deepEqual(errorText({ message: 'Звонки выключены.', hint: 'Включите.' }), { message: 'Звонки выключены.', hint: 'Включите.' });
});

/* ---------------------------------------------------------------- the REAL CallsView polling, on a minimal DOM
   A status reply that was already in flight when the owner pressed «Завершить» / STOP was the last word: run() returned
   without a fresh poll and the button and the live state stayed stale for up to one interval. A hidden tab kept polling. */

const { api } = await import('../api.js');

function fakeDocument() {
  const listeners = {};
  class El {
    constructor(tag) {
      Object.assign(this, { tagName: String(tag).toUpperCase(), nodeType: 1, children: [], attrs: {}, ev: {}, style: { setProperty() {} },
        dataset: {}, hidden: false, disabled: false, checked: false, value: '', text: '', isConnected: true });
      const cls = new Set();
      this.classList = { add: (...c) => c.forEach((x) => cls.add(x)), remove: (...c) => c.forEach((x) => cls.delete(x)),
        toggle: (c, on) => ((on ?? !cls.has(c)) ? cls.add(c) : cls.delete(c)), contains: (c) => cls.has(c) };
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    appendChild(c) { this.children.push(c); return c; }
    addEventListener(type, fn) { (this.ev[type] ||= []).push(fn); }
    get textContent() { return this.text + this.children.map((c) => c.textContent).join(''); }
    set textContent(v) { this.text = String(v); this.children = []; }
    *walk() { for (const c of this.children) if (c.nodeType === 1) { yield c; yield* c.walk(); } }
    querySelector(sel) { for (const e of this.walk()) if (e.tagName === sel.toUpperCase()) return e; return null; }
    querySelectorAll(sel) { const m = /^\[([\w-]+)\]$/.exec(sel); return [...this.walk()].filter((e) => m && m[1] in e.attrs); }
  }
  return {
    hidden: false, head: new El('head'), listeners,
    createElement: (t) => new El(t), createElementNS: (_ns, t) => new El(t),
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }), getElementById: () => null,
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    removeEventListener: (type, fn) => { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); },
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

const flush = () => new Promise((r) => setImmediate(r));
const LIVE = st({ call_active: true, call: { state: 'active', phase: 'listening' } });
const IDLE = st();

/** Render the real page with a fake document, a manual interval and a scripted /status. */
async function mountPanel(t, firstStatus) {
  const saved = { document: globalThis.document, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval, raw: api.raw };
  const doc = fakeDocument();
  const timers = [];
  globalThis.document = doc;
  globalThis.setInterval = (fn, ms) => timers.push({ fn, ms, cleared: false });
  globalThis.clearInterval = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; };
  const panel = { requests: [], next: () => Promise.resolve(firstStatus) };
  api.raw = (path, opts = {}) => {
    panel.requests.push(`${opts.method || 'GET'} ${path.replace(BASE, '')}`);
    if (path === `${BASE}/status`) return panel.next();
    if (path.startsWith(`${BASE}/history`)) return Promise.resolve({ items: [] });
    return Promise.resolve({ ok: true });
  };
  const root = await mod.default.render({});
  t.after(() => {
    root.isConnected = false;                       // later onEvent calls in this file must not reach this view
    Object.assign(globalThis, { document: saved.document, setInterval: saved.setInterval, clearInterval: saved.clearInterval });
    api.raw = saved.raw;
  });
  await flush();
  const byTestId = (id) => [...root.walk()].find((e) => e.attrs['data-testid'] === id);
  const button = (label) => [...root.walk()].find((e) => e.tagName === 'BUTTON' && e.textContent === label);
  const click = (b) => Promise.all((b.ev.click || []).map((fn) => fn({ stopPropagation() {} })));
  const statusPolls = () => panel.requests.filter((r) => r === 'GET /status').length;
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 1500);
  return { doc, root, panel, timers, tick: () => timers[0].fn(), byTestId, button, click, statusPolls };
}

test('after «Завершить» the panel shows the state AFTER the hangup even when a poll was already in flight', async (t) => {
  const p = await mountPanel(t, LIVE);
  assert.equal(p.byTestId('tc-live-state').textContent, 'Идёт разговор');
  const before = deferred();
  p.panel.next = () => before.promise;          // the interval's poll: its reply is taken BEFORE the hangup
  p.tick();
  await flush();
  p.panel.next = () => Promise.resolve(IDLE);   // what the server says once the call is over
  const clicking = p.click(p.button('Завершить'));
  await flush();
  before.resolve(LIVE);
  await clicking;
  await flush();
  const hangupAt = p.panel.requests.indexOf('POST /hangup');
  assert.ok(hangupAt > 0);
  assert.ok(p.panel.requests.slice(hangupAt).includes('GET /status'), `no status poll after the hangup: ${p.panel.requests}`);
  assert.equal(p.byTestId('tc-live-state').textContent, 'Нет звонка');
  assert.equal(p.button('Завершить').disabled, true, 'the hangup button must not stay enabled on a stale status');
});

test('STOP refreshes after its reply too, and the refresh it waits for is the one AFTER the in-flight poll', async (t) => {
  const p = await mountPanel(t, LIVE);
  const before = deferred();
  p.panel.next = () => before.promise;
  p.tick();
  await flush();
  p.panel.next = () => Promise.resolve(st({ stop: { call: true, global: false, active: true } }));
  const clicking = p.click(p.button('STOP'));
  await flush();
  before.resolve(LIVE);
  await clicking;
  await flush();
  assert.equal(p.byTestId('tc-stop-note').hidden, false);
  assert.equal(p.button('Продолжить').disabled, false, '«Продолжить» is offered right after STOP');
  assert.equal(p.byTestId('tc-live-state').textContent, 'Нет звонка');
});

test('a hidden tab does not poll; it polls at once when shown again; leaving the page drops the listener', async (t) => {
  const p = await mountPanel(t, IDLE);
  assert.equal(p.statusPolls(), 1);
  p.doc.hidden = true;
  p.tick();
  p.tick();
  await flush();
  assert.equal(p.statusPolls(), 1, 'no status request while the tab is hidden');
  p.doc.hidden = false;
  for (const fn of p.doc.listeners.visibilitychange || []) fn({ type: 'visibilitychange' });
  await flush();
  assert.equal(p.statusPolls(), 2, 'the tab came back: one immediate poll, without waiting for the next interval');
  p.tick();
  await flush();
  assert.equal(p.statusPolls(), 3, 'visible again: the interval polls as before');
  p.root.isConnected = false;
  p.tick();
  assert.equal(p.timers[0].cleared, true);
  assert.equal((p.doc.listeners.visibilitychange || []).length, 0, 'the page left the document: no listener is left behind');
});

test('a slow status (the worker wait is up to 2 s, the interval 1.5 s) never stacks polls; a bus event during it gets ONE follow-up', async (t) => {
  const p = await mountPanel(t, IDLE);
  const slow = deferred();
  p.panel.next = () => slow.promise;
  p.tick();
  p.tick();
  p.tick();                                     // three intervals pass while the one poll waits
  await flush();
  assert.equal(p.statusPolls(), 2, 'one poll in flight at a time');
  slow.resolve(IDLE);
  await flush();
  assert.equal(p.statusPolls(), 2, 'skipped intervals are not replayed back-to-back against a slow backend');
  const slow2 = deferred();
  p.panel.next = () => slow2.promise;
  p.tick();
  await flush();
  p.panel.next = () => Promise.resolve(IDLE);
  mod.default.onEvent({ kind: 'telegram_call.ended' });
  mod.default.onEvent({ kind: 'telegram_call.state' });
  await flush();
  assert.equal(p.statusPolls(), 3);
  slow2.resolve(LIVE);
  await flush();
  await flush();
  assert.equal(p.statusPolls(), 4, 'the events arrived after the in-flight request was sent: exactly one more poll');
  assert.equal(p.byTestId('tc-live-state').textContent, 'Нет звонка');
});

test('answeringWords: off by default, honest about what is not verified, and says why it would not answer', () => {
  assert.equal(answeringWords(undefined).on, false);
  assert.equal(answeringWords(st()).on, false);
  assert.match(answeringWords(st({ answering: { enabled: false } })).text, /выключен/);
  const unarmed = answeringWords(st({ answering: { enabled: true, armed: false } }));
  assert.equal(unarmed.on, true);
  assert.equal(unarmed.tone, 'warn');
  assert.match(unarmed.text, /не запущен/);
  const live = answeringWords(st({ answering: { enabled: true, armed: true, ready_state: 'ready', ring_delay_s: 12, live_tested: false } }));
  assert.equal(live.tone, 'ok');
  assert.match(live.text, /за 12 с/);
  assert.match(live.text, /не проверен в живую/);
  assert.doesNotMatch(answeringWords(st({ answering: { enabled: true, armed: true, ready_state: 'ready', ring_delay_s: 12, live_tested: true } })).text, /не проверен/);
  assert.match(answeringWords(st({ answering: { enabled: true, armed: true, ready_state: 'loading', ring_delay_s: 12 } })).text, /будет пропущен/);
  const stopped = answeringWords(st({ answering: { enabled: true, armed: true, ready_state: 'ready', ring_delay_s: 5, stopped: true, pending_reports: 2 } }));
  assert.equal(stopped.tone, 'warn');
  assert.match(stopped.text, /STOP/);
  assert.match(stopped.text, /ещё не отправленных вам в Telegram: 2/);
});

test('the answering toggle only ever sends the answering_machine setting', () => {
  assert.match(SOURCE, /body: \{ answering_machine: !!on \}/);
  assert.doesNotMatch(SOURCE, /answer_greeting|answer_allow|answer_deny/);
});
