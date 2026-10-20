/* B02-9: iOS kills a backgrounded PWA without warning (switching to Telegram to copy something is enough); on return
   the page reloads and the half-typed message in the chat composer was gone. The unsent text is now kept in this
   phone's localStorage (bm.draft), restored on start and cleared when the message is sent. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);
const AGENTS = { items: [{ id: 7, name: 'Gemma', tools: [], model: { alias: 'gemma' } }], default_agent_id: 7 };
const routes = (p, m) => {
  if (p === '/agents') return AGENTS;
  if (p === '/threads' && m === 'POST') return { id: 't1' };
  if (p === '/threads/t1/send') return { task: { id: 5 }, model: { alias: 'gemma' } };
  if (p.startsWith('/tasks/')) return { id: 5, status: 'running' };
  return undefined;
};

test('a draft left before the app was killed is back in the composer', { skip }, async () => {
  const { $ } = await boot({ routes, before: (w) => w.localStorage.setItem('bm.draft', 'Сделай отчёт по продажам за') });
  assert.equal($('input').value, 'Сделай отчёт по продажам за');
});

test('typing saves the draft; sending clears it', { skip }, async () => {
  const { w, calls, $ } = await boot({ routes });
  await until(() => $('model-name').textContent.includes('Gemma'), 'agent');
  const input = $('input');
  input.value = 'Привет'; input.dispatchEvent(new w.Event('input'));
  assert.equal(w.localStorage.getItem('bm.draft'), 'Привет');
  $('composer').requestSubmit();
  await until(() => calls.some((c) => c.path === '/threads/t1/send'), 'sent');
  assert.equal(calls.find((c) => c.path === '/threads/t1/send').body.text, 'Привет');
  assert.equal(w.localStorage.getItem('bm.draft'), null, 'draft cleared after send');
});

test('«Выйти на этом телефоне» drops the draft', { skip }, async () => {
  const { d, w, $ } = await boot({ routes: (p) => (p === '/status' ? { bossman: {}, ollama: {}, direct_gen: {}, war_terminal: {}, mobile: {} } : routes(p)),
    before: (win) => win.localStorage.setItem('bm.draft', 'личное') });
  d.querySelector('.tab[data-tab="status"]').click();
  await until(() => $('status-body').querySelector('.btn.danger'), 'logout button');
  $('status-body').querySelector('.btn.danger').click();
  await until(() => !$('login').hidden, 'login');
  assert.equal(w.localStorage.getItem('bm.draft'), null); assert.equal($('input').value, '');
});

test('broken storage (private mode) does not break the composer', { skip }, async () => {
  const { w, $ } = await boot({ routes, before: (win) => { Object.defineProperty(win, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } }); } });
  $('input').value = 'x'; $('input').dispatchEvent(new w.Event('input'));
  assert.equal($('input').value, 'x');
});
