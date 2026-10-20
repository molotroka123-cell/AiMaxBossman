/* Owner 20.10: the global STOP stayed on for hours after a test and the owner did not know. While /m/api/stop says
   active, a banner sits above EVERY tab; «Продолжить» asks confirm() and only then POSTs /stop/resume (Bossman's own
   resume). The page re-checks when it becomes visible again.

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

// server.stop = what GET /stop answers; server.confirm = what confirm() returns
async function boot(server) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const calls = []; const asked = [];
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', '');
        calls.push({ method, path, csrf: (init.headers || {})['X-BM-CSRF'] });
        let out = {};
        if (path === '/session') out = { csrf: 'tok' };
        else if (path === '/stop' && method === 'GET') out = server.stop;
        else if (path === '/stop/resume' && method === 'POST') { server.stop = { active: false, since: null, age_min: null }; out = { ok: true, ...server.stop, generation: 3 }; }
        return { ok: true, status: 200, json: async () => out };
      };
      w.confirm = (msg) => { asked.push(msg); return server.confirm; };
      w.HTMLElement.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  return { w, d, calls, asked };
}

const since = new Date(Date.now() - 95 * 60 * 1000);
const hhmm = `${String(since.getHours()).padStart(2, '0')}:${String(since.getMinutes()).padStart(2, '0')}`;
const ACTIVE = { active: true, since: since.toISOString(), age_min: 95 };

test('no banner while STOP is off', { skip }, async () => {
  const { d, calls } = await boot({ stop: { active: false, since: null, age_min: null }, confirm: true });
  await until(() => calls.some((c) => c.path === '/stop'), 'stop polled on start');
  await sleep(30);
  assert.equal(d.getElementById('stop-banner').hidden, true);
  assert.equal(d.getElementById('app').classList.contains('stop-on'), false);
});

test('active STOP: banner with the start time, outside the views so it stays on every tab', { skip }, async () => {
  const { d } = await boot({ stop: ACTIVE, confirm: false });
  const banner = d.getElementById('stop-banner');
  await until(() => !banner.hidden, 'banner shown');
  assert.equal(d.getElementById('stop-text').textContent,
    `⛔ STOP включён с ${hhmm} — действия ПК и записи обменника заблокированы`);
  assert.equal(banner.closest('.view'), null, 'banner is not inside one tab view');
  for (const tab of ['swap', 'status', 'photo', 'chat']) {
    d.querySelector(`.tab[data-tab="${tab}"]`).click();
    assert.equal(banner.hidden, false, `visible on ${tab}`);
  }
  const btn = d.getElementById('stop-resume');
  assert.equal(btn.textContent, 'Продолжить');
  assert.ok(btn.classList.contains('btn') && btn.classList.contains('small'), '.btn.small = min-height 44px');
});

test('«Продолжить»: cancelled confirm sends nothing, accepted confirm resumes and hides the banner', { skip }, async () => {
  const server = { stop: ACTIVE, confirm: false };
  const { d, calls, asked } = await boot(server);
  await until(() => !d.getElementById('stop-banner').hidden, 'banner shown');
  d.getElementById('stop-resume').click();
  await sleep(50);
  assert.equal(asked.length, 1);
  assert.equal(calls.filter((c) => c.path === '/stop/resume').length, 0, 'no resume without the owner\'s yes');
  server.confirm = true;
  d.getElementById('stop-resume').click();
  await until(() => d.getElementById('stop-banner').hidden, 'banner hidden after resume');
  const posts = calls.filter((c) => c.path === '/stop/resume');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].method, 'POST');
  assert.equal(posts[0].csrf, 'tok', 'resume carries the session CSRF token');
});

test('returning to the app re-checks STOP (visibilitychange)', { skip }, async () => {
  const server = { stop: { active: false, since: null, age_min: null }, confirm: true };
  const { w, d, calls } = await boot(server);
  await until(() => calls.some((c) => c.path === '/stop'), 'first poll');
  server.stop = ACTIVE;
  const before = calls.filter((c) => c.path === '/stop').length;
  d.dispatchEvent(new w.Event('visibilitychange'));
  await until(() => !d.getElementById('stop-banner').hidden, 'banner after visibilitychange');
  assert.ok(calls.filter((c) => c.path === '/stop').length > before);
});
