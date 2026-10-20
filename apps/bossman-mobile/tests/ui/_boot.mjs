/* Shared jsdom harness for the B02 UX tests: the REAL bossmobile/ui/index.html (+ app.css, + vendor/*.js on request)
   and the REAL app.js, with a scripted /m/api. Not a test file itself (no *.test.mjs suffix). */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const UI = process.env.BM_UI_DIR ? resolve(process.env.BM_UI_DIR) : resolve(here, '..', '..', 'bossmobile', 'ui');
const req = createRequire(import.meta.url);
function loadJsdom() {
  const paths = [process.env.BM_JSDOM_DIR, join(here, '..', '..', 'node_modules'), 'C:/Users/asd/Bossman/_tools/jsdom/node_modules'].filter(Boolean);
  for (const p of paths) { try { return req(join(p, 'jsdom')); } catch { /* next */ } }
  try { return req('jsdom'); } catch { return null; }
}
export const jsdom = loadJsdom();
export const skip = jsdom ? false : 'jsdom is not installed (set BM_JSDOM_DIR)';
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(10); }
  assert.fail(`timeout: ${what}`);
}

const opened = [];
export async function closeAll() { await sleep(120); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } }

const DEFAULTS = {
  'GET /session': { csrf: 'tok' }, 'GET /stop': { active: false, since: null, age_min: null }, 'GET /approvals': { items: [] },
  'GET /agents': { items: [], default_agent_id: null }, 'GET /threads': { items: [] },
  'GET /photo/models': { models: [] }, 'GET /photo/queue': { jobs: [] }, 'GET /photo/jobs': { jobs: [] }, 'GET /photo/status': { queue: {} },
  'GET /video/models': { models: [], gpu: {} }, 'GET /video/queue': { jobs: [] }, 'GET /video/jobs': { jobs: [] },
  'GET /exchange/deals': { deals: [] }, 'GET /status': {},
};

/* routes(path, method, body) -> undefined (use DEFAULTS) | plain object (200) | { __status, body } | Error (network failure).
   before(w) runs before app.js (stubs, localStorage seeds). */
export async function boot({ routes, css = false, vendor = false, before, waitApp = true } = {}) {
  let html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  if (css) html = html.replace('</head>', `<style>${readFileSync(join(UI, 'app.css'), 'utf8')}</style></head>`);
  const calls = [];
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.innerWidth = 390; w.innerHeight = 844;
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', '');
        let body = null; try { body = init.body ? JSON.parse(init.body) : null; } catch { body = init.body; }
        calls.push({ method, path, body, csrf: (init.headers || {})['X-BM-CSRF'] });
        let out = routes ? routes(path, method, body) : undefined;
        if (out instanceof Error) throw out;
        if (out === undefined) out = DEFAULTS[`${method} ${path.split('?')[0]}`] ?? {};
        const status = out && out.__status ? out.__status : 200;
        const payload = out && out.__status ? out.body : out;
        return { ok: status >= 200 && status < 300, status, json: async () => payload };
      };
      w.EventSource = class { constructor(u) { this.url = u; } close() { this.closed = true; } };
      w.confirm = () => true;
      w.HTMLElement.prototype.scrollIntoView = () => {};
      w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
      if (before) before(w);
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  if (vendor) {
    for (const f of ['tabbable.umd.min.js', 'focus-trap.umd.min.js']) w.eval(readFileSync(join(UI, 'vendor', f), 'utf8'));
  }
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  if (waitApp) await until(() => !d.getElementById('app').hidden, 'app shown');
  return { w, d, calls, $: (id) => d.getElementById(id) };
}
