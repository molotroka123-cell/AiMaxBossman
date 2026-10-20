/* B02-6: start() runs again after every re-login (an expired session shows the login, the owner pairs again) and each
   run added one more setInterval(refreshApprovals, 20000) — after N re-logins the phone polled /approvals N+1 times
   every 20 s, also while the app sat in the background. Now one poller per page; it skips while the page is hidden
   or the login is shown and catches up when the app becomes visible again. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, sleep, closeAll } from './_boot.mjs';

after(closeAll);

async function setup() {
  const timers = [];
  let expired = false;
  const env = await boot({
    routes: (p, m) => {
      if (p === '/login' && m === 'POST') { expired = false; return { csrf: 'tok2' }; }
      if (expired && p === '/status') return { __status: 401, body: {} };
      return undefined;
    },
    before(w) {
      const real = w.setInterval.bind(w);
      w.setInterval = (fn, ms, ...a) => { timers.push({ fn, ms }); return real(fn, ms, ...a); };
    },
  });
  return { ...env, timers, expire: () => { expired = true; } };
}
const approvalsPollers = (timers) => timers.filter((t) => t.ms === 20000);

test('re-login does not add a second /approvals poller', { skip }, async () => {
  const { d, $, timers, expire } = await setup();
  assert.equal(approvalsPollers(timers).length, 1);
  for (let i = 0; i < 2; i += 1) {
    expire();
    d.querySelector('.tab[data-tab="status"]').click();
    await until(() => !$('login').hidden, 'session expired -> login');
    $('login-token').value = 'pair-code';
    $('login-form').requestSubmit();
    await until(() => !$('app').hidden, 'logged in again');
  }
  assert.equal(approvalsPollers(timers).length, 1, 'still exactly one 20 s poller after two re-logins');
});

test('poller is quiet while hidden and catches up on return', { skip }, async () => {
  const { w, d, calls, timers } = await setup();
  const tick = approvalsPollers(timers)[0].fn;
  let hidden = true; Object.defineProperty(d, 'hidden', { configurable: true, get: () => hidden });
  const n0 = calls.filter((c) => c.path === '/approvals').length;
  tick(); tick(); await sleep(20);
  assert.equal(calls.filter((c) => c.path === '/approvals').length, n0, 'no request while the app is in the background');
  hidden = false; d.dispatchEvent(new w.Event('visibilitychange'));
  await until(() => calls.filter((c) => c.path === '/approvals').length === n0 + 1, 'one catch-up request on return');
});
