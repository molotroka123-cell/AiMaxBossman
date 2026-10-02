import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// The module must be importable in Node: no top-level document, window or fetch.
assert.equal(typeof document, 'undefined');
const mod = await import('../pages/telegram_calls.js');
const { accountStep, callWords, controlState, peerPickState, isTestMode, summarizeHistoryItem, fmtMs, errorText, BASE } = mod;

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
  assert.equal(mod.default.onEvent({ kind: 'calls.state' }), false);
  assert.equal(mod.default.onEvent({ kind: 'calls.ended' }), false);
  assert.equal(mod.default.onEvent({ kind: 'task.created' }), false);
  assert.equal(mod.default.onEvent(undefined), false);
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
