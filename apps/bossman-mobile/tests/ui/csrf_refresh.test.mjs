/* Owner 19.10: «не пройдена CSRF-проверка» after a fresh login in another tab. A POST that gets 403 code=csrf must
   fetch /session once, take the new token and repeat the request ONCE; a second 403 is shown, never looped.

   Runs the REAL bossmobile/ui/app.js against the REAL index.html in jsdom with a scripted /m/api
   (skipped when jsdom is not installed). */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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
after(async () => { await new Promise((r) => setTimeout(r, 150)); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(10); }
  assert.fail(`timeout: ${what}`);
}

// server.session = token /session hands out; server.accept = token the POST accepts
async function boot(server) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const calls = [];
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', '');
        const sent = (init.headers || {})['X-BM-CSRF'];
        calls.push({ method, path, csrf: sent });
        let out = {}; let status = 200;
        if (path === '/session') out = { csrf: server.session };
        else if (method === 'POST' && path === '/exchange') {
          if (sent !== server.accept) { status = 403; out = { detail: { message: 'не пройдена CSRF-проверка', code: 'csrf' } }; }
          else out = { ok: true, reply: 'Курсы: ок', quote_id: null, stopped: false };
        } else if (path === '/exchange/deals') out = { deals: [] };
        return { ok: status < 400, status, json: async () => out };
      };
      w.HTMLElement.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  return { d, calls };
}

const exchangePosts = (calls) => calls.filter((c) => c.method === 'POST' && c.path === '/exchange');

test('stale CSRF after a newer login: refreshes the token once and the request succeeds', { skip }, async () => {
  const server = { session: 'old', accept: 'old' };
  const { d, calls } = await boot(server);
  server.session = 'new'; server.accept = 'new';          // another tab logged in: the cookie's session has a new token
  d.querySelector('.tab[data-tab="swap"]').click();
  d.querySelector('#swap-quick [data-cmd="курс"]').click();
  await until(() => d.getElementById('swap-reply').textContent === 'Курсы: ок', 'reply after retry');
  assert.deepEqual(exchangePosts(calls).map((c) => c.csrf), ['old', 'new'], 'one try with the stale token, one retry with the fresh one');
});

test('a second CSRF refusal is shown as an error, never retried in a loop', { skip }, async () => {
  const server = { session: 'old', accept: 'old' };
  const { d, calls } = await boot(server);
  server.session = 'still-wrong'; server.accept = 'unreachable';
  d.querySelector('.tab[data-tab="swap"]').click();
  d.querySelector('#swap-quick [data-cmd="курс"]').click();
  await until(() => /^Ошибка/.test(d.getElementById('swap-reply').textContent), 'error shown');
  await sleep(200);
  assert.equal(exchangePosts(calls).length, 2, 'exactly one retry');
  assert.match(d.getElementById('swap-reply').textContent, /CSRF/);
});
