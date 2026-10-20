/* B02-5: when the PC did not answer, the gallery, the ready-video list, both queues and the chat list stayed silently
   empty (or stale) — `catch { }`. The owner could not tell «nothing there» from «no connection». Now a failed load
   shows «Не загрузилось: <причина>» with «Повторить»; the next successful load replaces it. 401 still goes to login. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);
const JOB = { job_id: 'j1', status: 'completed', model: 'm', raw_prompt: 'озеро', params: { width: 512, height: 512, seed: 1 }, elapsed_s: 3, result: { mime: 'image/png' } };
const errText = (box) => (box.querySelector('.load-error') || {}).textContent || '';

test('gallery: HTTP 500 -> message + «Повторить»; retry loads the photos', { skip }, async () => {
  let fail = true;
  const { d, $ } = await boot({ routes: (p, m) => (p === '/photo/jobs' && m === 'GET' ? (fail ? { __status: 500, body: { detail: { message: 'Bossman не отвечает' } } } : { jobs: [JOB] }) : undefined) });
  d.querySelector('.tab[data-tab="photo"]').click();
  const g = $('gallery');
  await until(() => g.querySelector('.load-error'), 'error row');
  assert.match(errText(g), /Не загрузилось: Bossman не отвечает/);
  assert.equal(g.querySelector('.load-error').getAttribute('role'), 'status');
  fail = false;
  g.querySelector('.load-error button').click();
  await until(() => g.querySelector('.thumb'), 'thumbnail after retry');
  assert.equal(g.querySelector('.load-error'), null, 'message gone after success');
});

test('photo queue and video list: network failure is said, not an empty list', { skip }, async () => {
  const down = new TypeError('Failed to fetch');
  const { d, $ } = await boot({ routes: (p) => (p === '/photo/queue' || p === '/video/jobs' || p === '/video/queue' ? down : undefined) });
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => $('photo-queue-list').querySelector('.load-error'), 'queue error');
  assert.match(errText($('photo-queue-list')), /нет связи с ПК/);
  d.querySelector('.tab[data-tab="video"]').click();
  await until(() => $('video-list').querySelector('.load-error') && $('video-queue-list').querySelector('.load-error'), 'video errors');
});

test('chat list in the drawer: failure is shown; a repeated failure does not stack messages', { skip }, async () => {
  const { $ } = await boot({ routes: (p, m) => (p === '/threads' && m === 'GET' ? { __status: 503, body: {} } : undefined) });
  $('btn-drawer').click();
  const list = $('thread-list');
  await until(() => list.querySelector('.load-error'), 'drawer error');
  assert.match(errText(list), /HTTP 503/);
  list.querySelector('.load-error button').click();
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(list.querySelectorAll('.load-error').length, 1);
});

test('401 is not shown as a list error (login screen takes over)', { skip }, async () => {
  let unauth = false;
  const { d, $ } = await boot({ routes: (p) => (unauth && p === '/photo/jobs' ? { __status: 401, body: {} } : undefined) });
  unauth = true;
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => !$('login').hidden, 'login shown');
  assert.equal($('gallery').querySelector('.load-error'), null);
});
