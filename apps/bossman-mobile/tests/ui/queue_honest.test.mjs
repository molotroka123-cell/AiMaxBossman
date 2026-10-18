/* Queue cards must be honest about progress (owner 18.10: «95 % (оценка) · загрузка модели · ≈ осталось 1 с» while the
   engine was still loading the model and the job could take 10+ minutes).

   Runs the REAL bossmobile/ui/app.js against the REAL index.html in jsdom with a scripted /m/api (same harness idea as
   photo_form.test.mjs; started from test_photo_form_ui.py; skipped when jsdom is not installed). */
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

/** The text the OLD UI produced for a loading job (kept verbatim as the negative control). */
const OLD_WRONG = '95 % (оценка) загрузка модели ≈ осталось 1 с';
const WRONG_RE = /\d+\s*%.*оценка|осталось\s*≈?\s*1\s*с|ждать\s*1\s*с/;

async function bootQueue(jobs) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = async (url) => {
        const path = String(url).replace('/m/api', '');
        let out = {};
        if (path === '/session') out = { csrf: 'x' };
        else if (path === '/photo/models') out = { models: [T2I] };
        else if (path === '/photo/queue') out = { jobs };
        else if (path === '/photo/jobs') out = { jobs: [] };
        return { ok: true, status: 200, json: async () => out };
      };
      w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
      w.HTMLElement.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => d.querySelectorAll('#photo-queue-list .qitem').length > 0, 'queue rendered');
  return { w, d, text: () => d.getElementById('photo-queue-list').textContent.replace(/\s+/g, ' '), items: () => [...d.querySelectorAll('#photo-queue-list .qitem')] };
}

const photo = (id, ui, extra = {}) => ({ job_id: id, kind: 'photo', model: 'z-image-turbo', status: 'loading', stage: 'loading',
  params: { width: 512, height: 512, steps: 8 }, ...extra, ui });

test('negative control: the old wrong text is detected by the guard regex', () => {
  assert.match(OLD_WRONG, WRONG_RE);
});

test('loading: «загрузка модели · идёт N мин», no percent, no «осталось», typical time labelled as estimate', { skip }, async () => {
  const app = await bootQueue([photo('a', { state: 'running', indeterminate: true, real: false, percent: null, phase: 'загрузка модели',
    elapsed_s: 5 * 60 + 7, remaining_s: null, typical_s: 100, total_s: 100 })]);
  const t = app.text();
  assert.match(t, /загрузка модели/); assert.match(t, /идёт 5 мин/); assert.match(t, /обычно ≈ 2 мин \(оценка по замерам\)/);
  assert.doesNotMatch(t, WRONG_RE); assert.doesNotMatch(t, /%/); assert.doesNotMatch(t, /осталось/);
  assert.ok(app.d.querySelector('#photo-queue-list .bar.indet'), 'indeterminate bar');
});

test('loading with an UNMEASURED model shows no «обычно» at all', { skip }, async () => {
  const app = await bootQueue([photo('a', { state: 'running', indeterminate: true, real: false, percent: null, phase: 'загрузка модели',
    elapsed_s: 30, remaining_s: null, typical_s: null, total_s: 100 })]);
  assert.match(app.text(), /идёт 30 с/); assert.doesNotMatch(app.text(), /обычно/);
});

test('a legacy payload (percent 95, remaining 0, stage loading) is still rendered honestly', { skip }, async () => {
  const app = await bootQueue([photo('a', { state: 'running', real: false, percent: 95, phase: 'загрузка модели', remaining_s: 0, elapsed_s: 700 })]);
  const t = app.text();
  assert.doesNotMatch(t, WRONG_RE); assert.doesNotMatch(t, /осталось/); assert.match(t, /идёт 11 мин/);
});

test('the elapsed time counts up between polls', { skip }, async () => {
  const app = await bootQueue([photo('a', { state: 'running', indeterminate: true, real: false, percent: null, phase: 'загрузка модели', elapsed_s: 59 })]);
  const e = app.d.querySelector('.live-elapsed'); assert.equal(e.textContent, '59 с');
  e.dataset.at = String(Date.now() - 5000);                 // five seconds went by
  await until(() => e.textContent === '1 мин', 'ticker advanced to 1 мин', 2500);
});

test('real steps: real percent (<= 99) and an ETA from the measured pace', { skip }, async () => {
  const app = await bootQueue([photo('a', { state: 'running', real: true, percent: 99, step: 8, steps: 8, phase: 'декодирование', remaining_s: null, elapsed_s: 90 }, { stage: 'generating', status: 'generating' }),
    photo('b', { state: 'running', real: true, percent: 25, step: 2, steps: 8, phase: 'генерация', remaining_s: 120, elapsed_s: 90 }, { stage: 'generating', status: 'generating' })]);
  const [a, b] = app.items().map((x) => x.textContent.replace(/\s+/g, ' '));
  assert.match(a, /99 %/); assert.doesNotMatch(a, /100 %|осталось/);
  assert.match(b, /25 %/); assert.match(b, /шаг 2\/8/); assert.match(b, /≈ осталось 2 мин/); assert.doesNotMatch(b, /\(оценка\)/);
});

test('queue position: no «ждать» claim when the job ahead has an unknown time', { skip }, async () => {
  const app = await bootQueue([
    photo('r', { state: 'running', indeterminate: true, real: false, percent: null, phase: 'загрузка модели', elapsed_s: 10 }),
    { job_id: 'q', kind: 'photo', model: 'z-image-turbo', status: 'queued', stage: 'queued', ui: { state: 'queued', position: 1, wait_s: null, total_s: 100 } },
    { job_id: 'q2', kind: 'photo', model: 'z-image-turbo', status: 'queued', stage: 'queued', ui: { state: 'queued', position: 2, wait_s: 90, total_s: 100 } }]);
  const [, q, q2] = app.items().map((x) => x.textContent.replace(/\s+/g, ' '));
  assert.match(q, /в очереди №1/); assert.match(q, /время ожидания неизвестно/); assert.doesNotMatch(q, /≈ ждать/);
  assert.match(q2, /≈ ждать 2 мин/);                       // a KNOWN wait is still shown, as an estimate
});

test('estimate used up: «дольше обычного», never «≈ осталось 1 с»', { skip }, async () => {
  const app = await bootQueue([photo('a', { state: 'running', real: false, percent: 95, phase: 'генерация', remaining_s: null, elapsed_s: 900 }, { stage: 'generating', status: 'generating' })]);
  const t = app.text();
  assert.match(t, /95 % \(оценка\)/); assert.match(t, /дольше обычного/); assert.doesNotMatch(t, /осталось/);
});
