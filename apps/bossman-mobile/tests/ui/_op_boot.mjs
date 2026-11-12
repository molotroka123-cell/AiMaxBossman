/* jsdom harness for the operator UI (bossmobile/ui/op/): the REAL op/index.html and the REAL op.js + md.js with a
   scripted /m/api and a controllable fake EventSource. Not a test file itself (no *.test.mjs suffix).
   jsdom cannot load ES modules, so md.js and op.js are joined into one scope with their import/export keywords
   stripped (the code is otherwise byte-for-byte what the phone runs). */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { jsdom, skip, sleep, until, UI } from './_boot.mjs';

export { skip, sleep, until };
export const OP = join(UI, 'op');

const opened = [];
export async function closeAll() { await sleep(120); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } }

export const OPERATOR = {
  agent: { id: 1, name: 'Qwen Оператор' },
  model: { id: 11, alias: 'local-qwen', name: 'qwen:latest', context_window: 32768, health: 'ok', loaded: true },
  candidates: [], total_control: false, ready: true, reason: null,
};

const DEFAULTS = {
  'GET /session': { csrf: 'tok' }, 'GET /stop': { active: false }, 'GET /approvals': { items: [] },
  'GET /threads': { items: [] }, 'GET /operator': OPERATOR,
  'POST /operator/warm': { ok: true, loaded: true, load_ms: 10, error: null },
  'POST /threads': { id: 'T9', title: 'x' }, 'PATCH /agent/sessions/T9': { ok: true },
  'POST /agent/sessions/T9/messages': { task: { id: 77, status: 'queued' }, task_id: 77 },
  'GET /tasks/77': { id: 77, status: 'running', result: null, error: null },
  'GET /tasks/77/history': { events: [] },
  'GET /threads/T9': { id: 'T9', title: 'x', turns: [] }, 'GET /threads/T9/queue': { items: [] },
  'POST /stop': { stop: { active: true } }, 'POST /threads/T9/stop': { ok: true },
  'POST /login': { ok: true, csrf: 'tok', csrf_header: 'X-BM-CSRF' },
};

/* routes(path, method, body) -> undefined (DEFAULTS) | object (200) | { __status, body } | Error (network) | Promise of those */
export async function bootOp({ routes, url = 'http://pc.local/m/op/', cfg = {}, before, waitApp = true } = {}) {
  const html = readFileSync(join(OP, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const calls = []; const sources = [];
  const dom = new jsdom.JSDOM(html, { url, runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.innerWidth = 390; w.innerHeight = 844;
      w.__opCfg = { pollMs: 60000, approvalsMs: 60000, stopPollMs: 60000, reconnectBaseMs: 20, queueFollowMs: 20, ...cfg };
      w.fetch = async (u, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(u).replace('/m/api', '');
        let body = null; try { body = init.body ? JSON.parse(init.body) : null; } catch { body = init.body; }
        calls.push({ method, path, body, csrf: (init.headers || {})['X-BM-CSRF'] });
        let out = routes ? await routes(path, method, body) : undefined;
        if (out instanceof Error) throw out;
        if (out === undefined) out = DEFAULTS[`${method} ${path.split('?')[0]}`] ?? {};
        const status = out && out.__status ? out.__status : 200;
        const payload = out && out.__status ? out.body : out;
        return { ok: status >= 200 && status < 300, status, json: async () => JSON.parse(JSON.stringify(payload ?? null)) };
      };
      w.EventSource = class FakeEventSource {
        constructor(u) { this.url = String(u); this.readyState = 0; this.closed = false; sources.push(this); }
        open() { this.readyState = 1; if (this.onopen) this.onopen({}); }
        emit(ev) { if (!this.closed && this.onmessage) this.onmessage({ data: JSON.stringify(ev) }); }
        fail(closed) { if (closed) this.readyState = 2; if (this.onerror) this.onerror({}); }
        close() { this.closed = true; this.readyState = 2; }
      };
      w.HTMLElement.prototype.scrollIntoView = () => {};
      w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
      if (before) before(w);
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  const strip = (s) => s.replace(/^import .*$/mg, '').replace(/^export /mg, '');
  w.eval(`(() => {\n${strip(readFileSync(join(OP, 'md.js'), 'utf8'))}\n${strip(readFileSync(join(OP, 'op.js'), 'utf8'))}\n`
    + 'window.__opTest = { renderMarkdown, mask };\n})();');
  if (waitApp) await until(() => !d.getElementById('app').hidden, 'app shown');
  const $ = (id) => d.getElementById(id);
  return {
    w, d, calls, sources, $,
    es: () => sources[sources.length - 1],
    count: (method, path) => calls.filter((c) => c.method === method && c.path === path).length,
    async send(text) {
      const i = $('input'); i.value = text; i.dispatchEvent(new w.Event('input'));
      $('composer').dispatchEvent(new w.Event('submit', { cancelable: true }));
    },
  };
}
