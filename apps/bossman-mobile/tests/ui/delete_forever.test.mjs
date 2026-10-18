/* «Удалить навсегда» under «Отправить в пульт»: confirm dialog, cancel does nothing, success removes the item from the
   gallery / queue without reload and shows «Удалено с ПК»; server errors are shown in Russian.

   Runs the REAL bossmobile/ui/app.js against the REAL index.html in jsdom with a scripted /m/api
   (started from test_photo_form_ui.py; skipped when jsdom is not installed). */
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
const photo = (id) => ({ job_id: id, status: 'completed', stage: 'completed', model: T2I.id, raw_prompt: 'p' + id, params: { width: 512, height: 512, seed: 1, steps: 8 },
  result: { mime: 'image/png' }, ui: { state: 'done', elapsed_s: 5 } });
const video = (id) => ({ job_id: id, status: 'completed', stage: 'completed', model: 'wan', kind: 'video', raw_prompt: 'v' + id, params: { width: 480, height: 272, frames: 33 },
  result: { mime: 'video/mp4', width: 480, height: 272, duration_s: 2 }, ui: { state: 'done', elapsed_s: 30 } });

const opened = [];
after(async () => { await new Promise((r) => setTimeout(r, 150)); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(10); }
  assert.fail(`timeout: ${what}`);
}

async function boot(state) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const calls = [];
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', ''); calls.push({ method, path });
        let out = {}; let status = 200;
        const del = /^\/(photo|video)\/jobs\/([^/]+)\/delete$/.exec(path);
        if (path === '/session') out = { csrf: 'x' };
        else if (path === '/photo/models') out = { models: [T2I] };
        else if (path === '/photo/jobs') out = { jobs: state.photos };
        else if (path === '/photo/queue') out = { jobs: state.photos };
        else if (path === '/video/jobs') out = { jobs: state.videos };
        else if (path === '/video/queue') out = { jobs: state.videos };
        else if (method === 'POST' && del) {
          if (state.fail) { status = 409; out = { detail: { message: 'задача ещё выполняется — сначала остановите её' } }; }
          else { state.photos = state.photos.filter((j) => j.job_id !== del[2]); state.videos = state.videos.filter((j) => j.job_id !== del[2]); out = { ok: true, deleted: true }; }
        }
        return { ok: status < 400, status, json: async () => out };
      };
      w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
      w.HTMLElement.prototype.scrollIntoView = () => {};
      w.HTMLMediaElement.prototype.pause = () => {}; w.HTMLMediaElement.prototype.load = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  const $ = (id) => d.getElementById(id);
  const dialog = () => d.querySelector('.confirm-card');
  const dels = (root) => [...root.querySelectorAll('button')].filter((b) => b.textContent === 'Удалить навсегда');
  const pressDialog = (act) => dialog().querySelector(`[data-act="${act}"]`).click();
  const posts = () => calls.filter((c) => c.method === 'POST' && /\/delete$/.test(c.path));
  return { w, d, $, calls, dialog, dels, pressDialog, posts };
}

test('photo gallery viewer: button under «Отправить в пульт», confirm required, cancel does nothing, success removes it', { skip }, async () => {
  const state = { photos: [photo('p1'), photo('p2')], videos: [] };
  const app = await boot(state); const { d, $, calls } = app;
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => d.querySelectorAll('#gallery .thumb').length === 2, 'gallery rendered');
  await until(() => d.querySelectorAll('#photo-queue-list .qitem').length === 2, 'queue rendered');
  const viewerBtns = [...$('viewer').querySelectorAll('.viewer-actions button')].map((b) => b.id);
  assert.ok(viewerBtns.indexOf('viewer-delete') === viewerBtns.indexOf('viewer-pult') + 1, 'directly under/after «Отправить в пульт»');
  assert.equal($('viewer-delete').textContent, 'Удалить навсегда');
  d.querySelector('#gallery .thumb[data-job="p1"] img').click();
  assert.equal($('viewer').hidden, false);
  // tap -> confirm dialog, nothing sent yet
  $('viewer-delete').click();
  await until(() => app.dialog(), 'confirm dialog');
  assert.equal(app.dialog().querySelector('p').textContent, 'Удалить навсегда с ПК? Это нельзя отменить.');
  assert.deepEqual([...app.dialog().querySelectorAll('button')].map((b) => b.textContent), ['Отмена', 'Удалить']);
  assert.equal(app.posts().length, 0, 'no request before confirming');
  // cancel: nothing happens
  app.pressDialog('cancel');
  await sleep(40);
  assert.equal(app.dialog(), null, 'dialog closed');
  assert.equal(app.posts().length, 0, 'cancel sends nothing');
  assert.equal(d.querySelectorAll('#gallery .thumb').length, 2);
  assert.equal($('viewer').hidden, false, 'viewer stays open');
  // confirm: DELETE via proxy, item gone, toast
  $('viewer-delete').click();
  await until(() => app.dialog(), 'confirm dialog again');
  app.pressDialog('confirm');
  await until(() => app.posts().length === 1, 'POST delete');
  assert.equal(app.posts()[0].path, '/photo/jobs/p1/delete');
  await until(() => $('toast').textContent === 'Удалено с ПК', 'toast');
  assert.equal(d.querySelector('[data-job="p1"]'), null, 'removed from gallery and queue');
  assert.ok(d.querySelector('#gallery [data-job="p2"]'), 'the other photo stays');
  assert.equal($('viewer').hidden, true, 'viewer closed');
  assert.equal(calls.filter((c) => c.path === '/session').length >= 1, true);
});

test('photo queue card has the button; server error is shown in Russian and the item stays', { skip }, async () => {
  const state = { photos: [photo('p1')], videos: [], fail: true };
  const app = await boot(state); const { d, $ } = app;
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => d.querySelector('#photo-queue-list .qitem[data-job="p1"]'), 'queue item');
  const btn = app.dels(d.querySelector('#photo-queue-list .qitem'))[0];
  assert.ok(btn, 'delete button on the queue card');
  btn.click(); await until(() => app.dialog(), 'dialog'); app.pressDialog('confirm');
  await until(() => $('toast').textContent.includes('остановите'), 'Russian error toast');
  assert.ok(d.querySelector('#gallery [data-job="p1"]'), 'not removed after an error');
  assert.equal(btn.disabled, false, 'usable again');
});

test('video list and video viewer: delete removes the card without reload', { skip }, async () => {
  const state = { photos: [], videos: [video('v1'), video('v2')] };
  const app = await boot(state); const { d, $ } = app;
  d.querySelector('.tab[data-tab="video"]').click();
  await until(() => d.querySelectorAll('#video-list .vcard').length === 2, 'video cards');
  const vb = [...$('vviewer').querySelectorAll('.viewer-actions button')].map((b) => b.id);
  assert.ok(vb.indexOf('vviewer-delete') === vb.indexOf('vviewer-pult') + 1, 'under «Отправить в пульт» in the video viewer');
  // from the card
  app.dels(d.querySelector('#video-list .vcard[data-job="v1"]'))[0].click();
  await until(() => app.dialog(), 'dialog'); app.pressDialog('confirm');
  await until(() => app.posts().length === 1, 'POST'); assert.equal(app.posts()[0].path, '/video/jobs/v1/delete');
  await until(() => !d.querySelector('[data-job="v1"]'), 'v1 removed');
  await until(() => $('toast').textContent === 'Удалено с ПК', 'toast');
  // from the viewer
  d.querySelector('#video-list .vcard[data-job="v2"] .btn.primary').click();
  assert.equal($('vviewer').hidden, false);
  $('vviewer-delete').click(); await until(() => app.dialog(), 'dialog 2'); app.pressDialog('confirm');
  await until(() => app.posts().length === 2, 'POST 2'); assert.equal(app.posts()[1].path, '/video/jobs/v2/delete');
  await until(() => $('vviewer').hidden === true, 'viewer closed');
  await until(() => !d.querySelector('[data-job="v2"]'), 'v2 removed');
});
