/* B02-8: on the pairing screen a dead connection showed the browser's own English «Failed to fetch» (iOS: «Load
   failed»), and the «Войти» button stayed live while a login was in flight, so a double tap sent two attempts against
   the server's login rate limit. Now: a Russian reason, one attempt at a time (button disabled + aria-busy). */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, sleep, closeAll } from './_boot.mjs';

after(closeAll);

async function loginScreen(onLogin) {
  const env = await boot({ waitApp: false, routes: (p, m) => (p === '/session' ? { __status: 401, body: {} } : p === '/login' && m === 'POST' ? onLogin() : undefined) });
  await until(() => !env.$('login').hidden, 'login screen');
  return env;
}

test('network failure: Russian reason instead of «Failed to fetch»', { skip }, async () => {
  const { $ } = await loginScreen(() => new TypeError('Failed to fetch'));
  $('login-token').value = 'code'; $('login-form').requestSubmit();
  await until(() => $('login-error').textContent, 'error shown');
  assert.match($('login-error').textContent, /^Нет связи с ПК/);
  assert.doesNotMatch($('login-error').textContent, /fetch|Load failed/i);
  assert.equal($('login-form').querySelector('button[type="submit"]').disabled, false, 'ready for another try');
});

test('a double tap sends one login; the button is busy until the answer', { skip }, async () => {
  let release; const gate = new Promise((r) => { release = r; });
  const { calls, $ } = await loginScreen(() => undefined);
  // hold the /login answer until we release it
  const w = $('login').ownerDocument.defaultView; const orig = w.fetch;
  w.fetch = async (url, init) => { if (String(url).endsWith('/login')) await gate; return orig(url, init); };
  $('login-token').value = 'code';
  $('login-form').requestSubmit(); $('login-form').requestSubmit();
  await sleep(30);
  const btn = $('login-form').querySelector('button[type="submit"]');
  assert.equal(btn.disabled, true, 'busy while waiting');
  assert.equal($('login-form').getAttribute('aria-busy'), 'true');
  release(); await until(() => !$('app').hidden, 'logged in');
  assert.equal(calls.filter((c) => c.path === '/login').length, 1, 'one attempt');
  assert.equal(btn.disabled, false);
});
