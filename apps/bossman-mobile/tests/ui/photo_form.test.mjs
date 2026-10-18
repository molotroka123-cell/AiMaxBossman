/* Photo tab, no page reload between jobs (owner 18.10: "reload the page to insert the next photo").

   Runs the REAL bossmobile/ui/app.js against the REAL index.html in jsdom with a scripted /m/api. The file input
   behaves like a browser's: choosing the SAME file again fires no `change` unless the value was emptied.
   Started from apps/bossman-mobile/tests/test_photo_form_ui.py (node --test); skipped when jsdom is not installed. */
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

const EDIT = { id: 'qwen-image-2.1-uc-edit', label: 'Qwen Image Edit', available: true, accepts_image: true, requires_image: false, max_refs: 3,
  modes: ['T2I', 'I2I'], min_steps: 1, max_steps: 50, default_steps: 20, default_resolution: '512x512', min_side: 256, max_side: 1536, side_multiple: 16 };
const T2I = { id: 'z-image-turbo', label: 'Z-Image Turbo', available: true, accepts_image: false, modes: ['T2I'], min_steps: 1, max_steps: 20,
  default_steps: 8, default_resolution: '512x512', min_side: 256, max_side: 1536, side_multiple: 16 };

const opened = [];
after(async () => { await new Promise((r) => setTimeout(r, 150)); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(10); }
  assert.fail(`timeout: ${what}`);
}

/** Boot the app. `script(method, path, body)` answers job endpoints; the rest is canned. */
async function boot(script) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const calls = [];
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.__bmPollMs = 15;
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', '');
        const body = init.body ? JSON.parse(init.body) : undefined; calls.push({ method, path, body });
        let out = await script(method, path, body);
        if (out === undefined) {
          if (path === '/session') out = { csrf: 'x' };
          else if (path === '/photo/models') out = { models: [EDIT, T2I] };
          else if (path === '/photo/jobs' || path === '/photo/queue') out = { jobs: [] };
          else out = {};
        }
        const status = out && out.__status ? out.__status : 200;
        return { ok: status < 400, status, json: async () => (out && out.__status ? { detail: out.detail || 'x' } : out) };
      };
      w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
      w.Image = class { set src(_v) { this.naturalWidth = 768; this.naturalHeight = 1024; this.width = 768; this.height = 1024; setTimeout(() => this.onload && this.onload(), 0); } };
      w.HTMLCanvasElement.prototype.getContext = () => ({ drawImage() {} });
      w.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/jpeg;base64,AAAA';
      w.HTMLElement.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => d.getElementById('photo-model').options.length > 0, 'models loaded');
  const sel = d.getElementById('photo-model'); sel.value = EDIT.id; sel.dispatchEvent(new w.Event('change'));
  const $ = (id) => d.getElementById(id);
  // a file input that behaves like a browser: no `change` when the same file is chosen again without a reset
  const input = $('photo-ref'); let current = '';
  Object.defineProperty(input, 'value', { configurable: true, get: () => current, set: (v) => { if (v === '') current = ''; } });
  async function pick(name) {
    if (current === name) return false;           // a real browser fires nothing here: that was the symptom
    current = name; Object.defineProperty(input, 'files', { configurable: true, value: [{ name, size: 10, type: 'image/jpeg' }] });
    input.dispatchEvent(new w.Event('change'));
    await sleep(5); await until(() => $('refs-row').querySelectorAll('.ref-thumb').length > 0, 'preview shown'); return true;
  }
  const submit = () => $('photo-form').dispatchEvent(new w.Event('submit', { cancelable: true, bubbles: true }));
  return { w, d, $, calls, pick, submit, posts: () => calls.filter((c) => c.method === 'POST' && c.path === '/photo/jobs') };
}

test('two consecutive jobs without a reload: the first one still runs, the form is ready at once', { skip }, async () => {
  let n = 0;
  const app = await boot((method, path) => {
    if (method === 'POST' && path === '/photo/jobs') return { job_id: `j${++n}`, status: 'queued', stage: 'queued', params: { width: 512, height: 512, seed: 1 } };
    if (method === 'GET' && /^\/photo\/jobs\/j\d+$/.test(path)) return { job_id: path.split('/').pop(), status: 'generating', stage: 'generating', progress: { percent: 10, value: 1, max: 10 } };
  });
  const { $, pick, submit, posts } = app;
  await pick('first.jpg');
  $('photo-prompt').value = 'портрет @1 в красном пальто';
  submit(); await until(() => posts().length === 1, 'first POST');
  await until(() => !$('photo-go').disabled, 'button re-enabled while job 1 is still running');
  assert.equal($('photo-ref').value, '', 'file input emptied after submit');
  assert.equal($('photo-prompt').disabled, false);
  await until(() => $('photo-progress').textContent.includes('Генерация'), 'job 1 is tracked in its card');
  // the same file chosen again must fire `change` (value was reset) and start a second job without any reload
  assert.equal(await pick('first.jpg'), true, 'same file can be picked again');
  assert.equal($('refs-row').querySelectorAll('.ref-thumb').length, 1, 'new set replaced the used one');
  $('photo-prompt').value = 'портрет @1 в синем пальто';
  submit(); await until(() => posts().length === 2, 'second POST');
  assert.notEqual(posts()[0].body.prompt, posts()[1].body.prompt);
  assert.ok(posts()[1].body.image_b64, 'second job carries its reference');
  await until(() => $('photo-progress').children.length === 2, 'two progress cards');
});

test('a job that disappears (404) or an error on submit never locks the form', { skip }, async () => {
  let fail = true;
  const app = await boot((method, path) => {
    if (method === 'POST' && path === '/photo/jobs') return fail ? { __status: 500, detail: 'GPU занят' } : { job_id: 'gone', status: 'queued', stage: 'queued' };
    if (method === 'GET' && path === '/photo/jobs/gone') return { __status: 404, detail: 'нет' };
  });
  const { $, submit, posts } = app;
  $('photo-prompt').value = 'тест';
  submit(); await until(() => posts().length === 1, 'POST 1');
  await until(() => !$('photo-go').disabled, 'button back after a failed submit');
  assert.ok(!$('toast').hidden);
  fail = false; submit();
  await until(() => posts().length === 2, 'POST 2');
  await until(() => $('photo-progress').textContent.includes('Связь потеряна'), 'lost job stops polling and says so');
  assert.equal($('photo-go').disabled, false);
  const gets = app.calls.filter((c) => c.path === '/photo/jobs/gone').length;
  await sleep(120);
  assert.equal(app.calls.filter((c) => c.path === '/photo/jobs/gone').length, gets, 'polling stopped');
});

test('FaceLock toggle: only with a reference, default off, remembered, sent only when on', { skip }, async () => {
  const app = await boot((method, path) => (method === 'POST' && path === '/photo/jobs' ? { job_id: 'f1', status: 'queued', stage: 'queued' } : undefined));
  const { $, pick, submit, posts, w } = app;
  assert.equal($('facelock-row').hidden, true, 'hidden without a reference');
  assert.equal($('photo-facelock').checked, false, 'default OFF');
  await pick('face.jpg');
  assert.equal($('facelock-row').hidden, false, 'visible once a reference is attached');
  $('photo-prompt').value = 'портрет @1 на пляже';
  submit(); await until(() => posts().length === 1, 'POST');
  assert.equal('facelock' in posts()[0].body, false, 'off: field not sent');
  $('photo-facelock').checked = true; $('photo-facelock').dispatchEvent(new w.Event('change'));
  assert.equal(w.localStorage.getItem('bm.facelock'), '1', 'remembered per device');
  await until(() => !$('photo-go').disabled, 'ready');
  await pick('face2.jpg'); $('photo-prompt').value = 'портрет @1 в лесу'; submit();
  await until(() => posts().length === 2, 'POST 2');
  assert.equal(posts()[1].body.facelock, true);
  await until(() => !$('photo-go').disabled, 'ready again');
  // no reference -> never sent even though the box is ticked
  const t2i = app.d.getElementById('photo-model'); t2i.value = T2I.id; t2i.dispatchEvent(new w.Event('change'));
  $('photo-prompt').value = 'пейзаж'; submit(); await until(() => posts().length === 3, 'POST 3');
  assert.equal('facelock' in posts()[2].body, false);
});

test('FaceLock badge says applied / skipped with a human reason', { skip }, async () => {
  const done = (facelock) => ({ job_id: 'k1', status: 'completed', stage: 'completed', raw_prompt: 'p', facelock, params: { width: 512, height: 512, seed: 1 }, result: { mime: 'image/png' } });
  for (const [fl, want] of [[{ requested: true, status: 'applied', reason: '' }, 'лицо с @1 вставлено'],
    [{ requested: true, status: 'skipped', reason: 'face_count_mismatch ref=1 result=2' }, 'число лиц'],
    [{ requested: true, status: 'skipped', reason: 'no_face_in_result' }, 'на результате лицо не найдено']]) {
    const app = await boot((method, path) => {
      if (method === 'POST' && path === '/photo/jobs') return { job_id: 'k1', status: 'queued', stage: 'queued' };
      if (method === 'GET' && path === '/photo/jobs/k1') return done(fl);
    });
    app.$('photo-prompt').value = 'x'; app.submit();
    await until(() => app.$('photo-progress').textContent.includes(want), `badge ${want}`);
    }
});

test('«Отправить в пульт»: one POST per tap from the result card and the viewer, server text shown on error', { skip }, async () => {
  let mode = 'ok';
  const job = { job_id: 'p1', status: 'completed', stage: 'completed', raw_prompt: 'p', params: { width: 512, height: 512, seed: 1 }, result: { mime: 'image/png' }, model: EDIT.id };
  const app = await boot((method, path) => {
    if (method === 'POST' && path === '/photo/jobs') return { job_id: 'p1', status: 'queued', stage: 'queued' };
    if (method === 'GET' && path === '/photo/jobs/p1') return job;
    if (method === 'POST' && path === '/photo/jobs/p1/to-pult') return mode === 'ok' ? { ok: true, sent: true } : { __status: 409, detail: 'Пульт не настроен' };
  });
  const { $, submit, calls } = app;
  $('photo-prompt').value = 'x'; submit();
  await until(() => $('photo-progress').querySelector('img'), 'result card shown');
  const btn = [...$('photo-progress').querySelectorAll('button')].find((b) => b.textContent === 'В пульт');
  assert.ok(btn, 'pult button on the finished result card');
  btn.click();
  await until(() => calls.filter((c) => c.path === '/photo/jobs/p1/to-pult').length === 1, 'POST to-pult');
  await until(() => $('toast').textContent === 'Отправлено в пульт', 'toast');
  await until(() => !btn.disabled, 'button usable again (no dedupe: he may resend)');
  mode = 'fail';
  $('photo-progress').querySelector('img').click();            // opens the viewer
  assert.equal($('viewer').hidden, false);
  $('viewer-pult').click();
  await until(() => calls.filter((c) => c.path === '/photo/jobs/p1/to-pult').length === 2, 'second POST from the viewer');
  await until(() => $('toast').textContent.includes('Пульт не настроен'), 'server error text shown');
});
