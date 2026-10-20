/* Owner 19.10: «возможность Гемма по запросу вызывать как ChatGPT генерацию фото: промт даст он сам, запустит и
   разгрузится для ускорения». The chat model writes the prompt; the owner sees it and taps «Запустить» or «Изменить»;
   only then the chat model is unloaded (POST /chat/unload) and the photo job is queued (POST /photo/jobs — the same
   Direct Generator call as the Фото tab). Real app.js + index.html in jsdom, scripted /m/api, fake EventSource.
   Skipped when jsdom is not installed. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const UI = process.env.BM_UI_DIR || resolve(here, '..', '..', 'bossmobile', 'ui');
const req = createRequire(import.meta.url);
function loadJsdom() {
  const paths = [process.env.BM_JSDOM_DIR, join(here, '..', '..', 'node_modules'), 'C:/Users/asd/Bossman/_tools/jsdom/node_modules'].filter(Boolean);
  for (const p of paths) { try { return req(join(p, 'jsdom')); } catch { /* next */ } }
  try { return req('jsdom'); } catch { return null; }
}
const jsdom = loadJsdom();
const skip = jsdom ? false : 'jsdom is not installed (set BM_JSDOM_DIR)';

const T2I = { id: 'z-image-turbo', label: 'Z-Image Turbo', available: true, accepts_image: false, modes: ['T2I'], min_steps: 1, max_steps: 20,
  default_steps: 8, default_resolution: '768x768', min_side: 256, max_side: 1536, side_multiple: 16 };
const JOB = 'f'.repeat(32);
const ANSWER = 'PROMPT: a red fox sitting in fresh snow, golden hour light, 85mm lens, soft bokeh';

const opened = [];
after(async () => { await new Promise((r) => setTimeout(r, 150)); while (opened.length) { try { opened.pop().close(); } catch { /* closed */ } } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(5); }
  assert.fail(`timeout: ${what}`);
}

/** script(method, path, body) may answer any call ({__status, detail} = error); the rest is canned. */
async function boot(script = () => undefined, { storage = {}, turns = null } = {}) {
  const html = readFileSync(join(UI, 'index.html'), 'utf8').replace(/<script[^>]*src=[^>]*><\/script>/g, '');
  const calls = []; const sources = [];
  const dom = new jsdom.JSDOM(html, { url: 'http://pc.local/m/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.__bmPollMs = 10;
      Object.entries(storage).forEach(([k, v]) => w.localStorage.setItem(k, v));
      w.fetch = async (url, init = {}) => {
        const method = (init.method || 'GET').toUpperCase(); const path = String(url).replace('/m/api', '');
        const body = init.body ? JSON.parse(init.body) : undefined; calls.push({ method, path, body });
        let out = await script(method, path, body);
        if (out === undefined) {
          if (path === '/session') out = { csrf: 'x' };
          else if (path === '/agents') out = { items: [{ id: 4, name: 'Gemma uncensored', tools: [], model: { alias: 'gemma' }, local: true }], default_agent_id: 4 };
          else if (path === '/threads' && method === 'GET') out = { items: [] };
          else if (path === '/threads' && method === 'POST') out = { id: 'T1' };
          else if (path === '/threads/T1' && method === 'GET') out = { id: 'T1', turns: turns || [] };
          else if (path === '/threads/T1/send') out = { task: { id: 7, status: 'queued' }, admission: { ok: true }, model: { alias: 'gemma' } };
          else if (path === '/tasks/7') out = { id: 7, status: 'completed', result: ANSWER };
          else if (path === '/approvals') out = { items: [] };
          else if (path === '/photo/models') out = { models: [T2I] };
          else if (path === '/chat/unload') out = { ok: true, unloaded: ['gemma3-uncensored:latest'] };
          else if (method === 'POST' && path === '/photo/jobs') out = { job_id: JOB, status: 'queued', stage: 'queued', params: { width: 768, height: 768, seed: 1 } };
          else if (path === `/photo/jobs/${JOB}`) out = { job_id: JOB, status: 'completed', stage: 'completed', model: T2I.id, raw_prompt: 'p', elapsed_s: 12, params: { width: 768, height: 768, seed: 1 }, result: { mime: 'image/png' } };
          else out = {};
        }
        const status = out && out.__status ? out.__status : 200;
        return { ok: status < 400, status, json: async () => (out && out.__status ? { detail: { message: out.detail } } : out) };
      };
      w.EventSource = class { constructor(u) { this.url = u; sources.push(this); } close() { this.closed = true; } };
      w.HTMLElement.prototype.scrollIntoView = () => {};
    } });
  const w = dom.window; const d = w.document; opened.push(w);
  w.eval(readFileSync(join(UI, 'app.js'), 'utf8'));
  await until(() => !d.getElementById('app').hidden, 'app shown');
  await until(() => d.getElementById('model-name').textContent.includes('Gemma'), 'agent loaded');
  const say = (text) => { d.getElementById('input').value = text; d.getElementById('composer').dispatchEvent(new w.Event('submit', { cancelable: true, bubbles: true })); };
  const push = (es, ev) => es.onmessage({ data: JSON.stringify({ task_id: 7, ...ev }) });
  const card = () => d.querySelector('.msg.bot .pcard');
  const btn = (label) => [...(card() || d).querySelectorAll('button')].find((b) => b.textContent === label);
  const idx = (m, p) => calls.findIndex((c) => c.method === m && c.path === p);
  return { w, d, calls, sources, say, push, card, btn, idx };
}

async function promptCard(app, text = 'нарисуй рыжую лису в снегу') {
  app.say(text);
  await until(() => app.sources.length === 1, 'chat model asked');
  const es = app.sources[0];
  ANSWER.match(/[\s\S]{1,7}/g).forEach((p, i) => app.push(es, { kind: 'run.answer_delta', seq: i + 1, text: p }));
  app.push(es, { kind: 'task.completed', seq: 99 });
  await until(() => app.card(), 'prompt card shown');
}

test('«нарисуй …»: the chat model writes the prompt; NOTHING is queued or unloaded before «Запустить»', { skip }, async () => {
  const app = await boot();
  await promptCard(app);
  const send = app.calls.find((c) => c.path === '/threads/T1/send');
  assert.equal(send.body.agent_id, 4, 'asked the selected local chat agent (Gemma by default)');
  assert.match(send.body.text, /^Фото по описанию: рыжую лису в снегу\n\n/);
  assert.match(send.body.text, /PROMPT: <промпт>/);
  assert.equal(app.d.querySelector('.msg.user .bubble').textContent, 'Фото по описанию: рыжую лису в снегу');
  assert.equal(app.card().querySelector('.pcard-text').textContent, 'a red fox sitting in fresh snow, golden hour light, 85mm lens, soft bokeh');
  assert.ok(app.btn('Запустить') && app.btn('Изменить'), 'owner keeps control: Запустить / Изменить');
  await sleep(60);
  assert.equal(app.idx('POST', '/photo/jobs'), -1, 'no job before the tap');
  assert.equal(app.idx('POST', '/chat/unload'), -1, 'no unload before the tap');
});

test('«Изменить» then «Запустить»: unload is sent BEFORE the job, the job carries the edited prompt only, the photo lands in the chat', { skip }, async () => {
  const app = await boot();
  await promptCard(app);
  app.btn('Изменить').click();
  const area = app.card().querySelector('textarea');
  assert.equal(area.hidden, false); assert.equal(area.value, 'a red fox sitting in fresh snow, golden hour light, 85mm lens, soft bokeh');
  area.value = 'a red fox in snow, blue hour';
  app.btn('Запустить').click();
  await until(() => app.idx('POST', '/photo/jobs') >= 0, 'job queued');
  const unload = app.idx('POST', '/chat/unload'); const job = app.idx('POST', '/photo/jobs');
  assert.ok(unload >= 0 && unload < job, `unload (#${unload}) before the job (#${job})`);
  assert.deepEqual(app.calls[unload].body, { agent_id: 4 });
  assert.deepEqual(app.calls[job].body, { model: 'z-image-turbo', prompt: 'a red fox in snow, blue hour', resolution: '768x768' });
  assert.equal(app.calls.filter((c) => c.method === 'POST' && c.path === '/photo/jobs').length, 1);
  await until(() => app.d.querySelector('.msg.bot .inline-photo img'), 'photo in the chat thread');
  assert.equal(app.d.querySelector('.msg.bot .inline-photo img').getAttribute('src'), `/m/api/photo/jobs/${JOB}/file`);
  assert.match(app.card().textContent, /модель чата выгружена из памяти/);
  assert.equal(app.btn('Запустить'), undefined, 'no second launch from the same card');
  assert.deepEqual(JSON.parse(app.w.localStorage.getItem('bm.chatPhoto.7')), { job_id: JOB, prompt: 'a red fox in snow, blue hour' });
});

test('errors are shown in Russian: unload failure is reported but the photo still goes; a refused job keeps the card usable', { skip }, async () => {
  let refuse = true;
  const app = await boot((m, p) => {
    if (p === '/chat/unload') return { __status: 502, detail: 'Ollama не отвечает — модель чата не выгружена' };
    if (m === 'POST' && p === '/photo/jobs' && refuse) return { __status: 422, detail: 'для этой модели шагов может быть от 1 до 20' };
    return undefined;
  });
  await promptCard(app);
  app.btn('Запустить').click();
  await until(() => /Не удалось запустить/.test(app.card().querySelector('.pcard-status').textContent), 'error shown');
  const st = app.card().querySelector('.pcard-status');
  assert.ok(st.classList.contains('err'));
  assert.match(st.textContent, /Не удалось запустить: для этой модели шагов может быть от 1 до 20/);
  assert.match(st.textContent, /модель чата не выгружена: Ollama не отвечает/);
  assert.equal(app.btn('Запустить').disabled, false, 'can try again');
  assert.equal(app.card().querySelector('.progress'), null, 'no job card');
  refuse = false; app.btn('Запустить').click();
  await until(() => app.d.querySelector('.msg.bot .inline-photo img'), 'second try lands');
  assert.match(app.card().textContent, /модель чата не выгружена: Ollama не отвечает/, 'unload failure stays visible');
});

test('chat model fails → the owner\'s own description is offered; photo button mode works without a command word', { skip }, async () => {
  const app = await boot((m, p) => (p === '/tasks/7' ? { id: 7, status: 'failed', result: null, error: 'модель не загрузилась' } : undefined));
  app.d.getElementById('btn-photo-mode').click();
  assert.equal(app.d.getElementById('btn-photo-mode').getAttribute('aria-pressed'), 'true');
  app.say('кот-космонавт на Луне');
  await until(() => app.sources.length === 1, 'asked');
  assert.match(app.calls.find((c) => c.path === '/threads/T1/send').body.text, /^Фото по описанию: кот-космонавт на Луне/);
  assert.equal(app.d.getElementById('btn-photo-mode').getAttribute('aria-pressed'), 'false', 'mode is one-shot');
  app.push(app.sources[0], { kind: 'task.failed', seq: 1 });
  await until(() => app.card(), 'card');
  assert.equal(app.card().querySelector('.pcard-text').textContent, 'кот-космонавт на Луне');
  assert.match(app.card().querySelector('.pcard-note').textContent, /Модель чата не написала промпт: модель не загрузилась/);
  assert.equal(app.idx('POST', '/photo/jobs'), -1);
});

test('an ordinary message is NOT a photo request; a reopened thread shows the card and the photo again', { skip }, async () => {
  const app = await boot();
  app.say('сделай мне план на день');
  await until(() => app.sources.length === 1, 'asked');
  assert.equal(app.calls.find((c) => c.path === '/threads/T1/send').body.text, 'сделай мне план на день');
  const shot = await boot();
  shot.say('сделай фото экрана и скажи, что открыто');
  await until(() => shot.sources.length === 1, 'asked');
  assert.equal(shot.calls.find((c) => c.path === '/threads/T1/send').body.text, 'сделай фото экрана и скажи, что открыто', 'a screenshot request stays a normal chat turn');
  const asked = 'Фото по описанию: лиса\n\n[задание для модели] Напиши ОДИН промпт… PROMPT: <промпт>';
  const again = await boot(undefined, { storage: { 'bm.thread': 'T1', 'bm.chatPhoto.7': JSON.stringify({ job_id: JOB, prompt: 'a fox' }) },
    turns: [{ idx: 0, task_id: 7, text: asked, status: 'completed', result: ANSWER }] });
  await until(() => again.d.querySelector('.msg.bot .inline-photo img'), 'photo restored');
  assert.equal(again.d.querySelector('.msg.user .bubble').textContent, 'Фото по описанию: лиса', 'the instruction is not shown');
  assert.equal(again.card().querySelector('.pcard-text').textContent, 'a fox');
  assert.equal(again.btn('Запустить'), undefined, 'already launched: no button');
  assert.equal(again.idx('POST', '/photo/jobs'), -1, 'reopening never queues again');
});
