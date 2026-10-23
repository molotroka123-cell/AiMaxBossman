/* Owner 23.10: «картинка после правки должна прийти сама в тот же чат». When the chat agent's photo.edit job ends,
   Bossman appends a note turn (kind=note) to the thread; the phone shows the picture (tap -> the full-screen viewer)
   or the honest failure reason, and an open thread picks the note up by itself (no reload, no «Генерации»).
   Real app.js + index.html in jsdom, scripted /m/api, fake EventSource; synthetic ids and text only. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, closeAll, skip, until } from './_boot.mjs';

after(closeAll);
const JOB = 'a'.repeat(32);
const AGENTS = { items: [{ id: 4, name: 'Gemma uncensored', tools: [], model: { alias: 'gemma' }, local: true }], default_agent_id: 4 };
const USER = { idx: 0, task_id: 7, text: 'сделай фон синим', status: 'completed', result: 'Поставил правку в очередь.' };
const NOTE_OK = { idx: 1, task_id: -5, text: `Правка фото готова #${JOB.slice(0, 8)} за 41.0 с · qwen-image-2.1-uc-edit · отправлена в Пульт.`,
  status: 'missing', kind: 'note',
  note: { key: `photo_edit:${JOB}`, type: 'photo_edit', job_id: JOB, status: 'completed', model: 'qwen-image-2.1-uc-edit', elapsed_s: 41,
    file_url: `/api/direct-gen/jobs/${JOB}/file`, mime: 'image/png', pult: { sent: true, message_id: 77 } } };
const NOTE_FAIL = { idx: 1, task_id: -6, text: `Правка фото #${JOB.slice(0, 8)} не удалась: sd-cli exited with code 1`, status: 'missing', kind: 'note',
  note: { key: `photo_edit:${JOB}`, type: 'photo_edit', job_id: JOB, status: 'failed', reason: 'sd-cli exited with code 1',
    pult: { sent: false, code: 'not_applicable' } } };
const JOBVIEW = { job_id: JOB, status: 'completed', model: 'qwen-image-2.1-uc-edit', raw_prompt: 'Make the background blue', elapsed_s: 41,
  params: { width: 768, height: 576, seed: 3 }, result: { mime: 'image/png' } };

test('a delivered edit is shown in the thread with the picture; tap opens the full-screen viewer', { skip }, async () => {
  const app = await boot({
    before: (w) => w.localStorage.setItem('bm.thread', 'T1'),
    routes: (p, m) => (p === '/agents' ? AGENTS : p === '/threads/T1' ? { id: 'T1', turns: [USER, NOTE_OK] } : p === `/photo/jobs/${JOB}` ? JOBVIEW : undefined),
  });
  await until(() => app.d.querySelector('.msg.note'), 'note rendered');
  const note = app.d.querySelector('.msg.note');
  assert.equal(app.d.querySelectorAll('.msg.user').length, 1, 'the note is not shown as an owner message');
  assert.match(note.querySelector('.note-text').textContent, /Правка фото готова #aaaaaaaa/);
  const img = note.querySelector('.inline-photo img');
  assert.equal(img.getAttribute('src'), `/m/api/photo/jobs/${JOB}/file`, 'picture through the phone\'s authenticated route');
  assert.match(note.querySelector('.meta').textContent, /41\.0 с/);
  assert.match(note.querySelector('.meta').textContent, /в Пульте/);
  img.click();
  await until(() => !app.$('viewer').hidden, 'viewer opened');
  assert.equal(app.$('viewer-img').getAttribute('src'), `/m/api/photo/jobs/${JOB}/file`);
  assert.match(app.$('viewer-meta').textContent, /Make the background blue/);
});

test('a failed edit shows the reason, no picture', { skip }, async () => {
  const app = await boot({
    before: (w) => w.localStorage.setItem('bm.thread', 'T1'),
    routes: (p) => (p === '/agents' ? AGENTS : p === '/threads/T1' ? { id: 'T1', turns: [USER, NOTE_FAIL] } : undefined),
  });
  await until(() => app.d.querySelector('.msg.note'), 'note rendered');
  const note = app.d.querySelector('.msg.note');
  assert.ok(note.querySelector('.note-err'), 'error styling');
  assert.match(note.textContent, /не удалась: sd-cli exited with code 1/);
  assert.equal(note.querySelector('img'), null);
});

test('live: after the agent\'s photo.edit the open thread receives the note by itself', { skip }, async () => {
  const sources = []; let delivered = false; let threadReads = 0;
  const app = await boot({
    before: (w) => {
      w.__bmNotePollMs = 15;
      w.EventSource = class { constructor(u) { this.url = u; sources.push(this); } close() { this.closed = true; } };
    },
    routes: (p, m) => {
      if (p === '/agents') return AGENTS;
      if (p === '/threads' && m === 'POST') return { id: 'T1' };
      if (p === '/threads/T1/send') return { task: { id: 7, status: 'queued' }, admission: { ok: true }, model: { alias: 'gemma' } };
      if (p === '/tasks/7') return { id: 7, status: 'completed', result: 'Поставил правку в очередь.' };
      if (p === '/threads/T1' && m === 'GET') { threadReads += 1; return { id: 'T1', turns: delivered ? [USER, NOTE_OK] : [USER] }; }
      return undefined;
    },
  });
  await until(() => app.$('model-name').textContent.includes('Gemma'), 'agent loaded');
  app.$('input').value = 'сделай фон синим';
  app.$('composer').dispatchEvent(new app.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => sources.length === 1, 'task stream opened');
  const push = (ev) => sources[0].onmessage({ data: JSON.stringify({ task_id: 7, ...ev }) });
  push({ kind: 'run.tool_use', seq: 1, call_id: 'c1', tool: 'photo.edit', args: { attachments: ['x'], prompt: 'blue' } });
  push({ kind: 'run.tool_result', seq: 2, call_id: 'c1', tool: 'photo.edit', ok: true,
    summary: `🎨 Правка фото поставлена в очередь Direct Gen: job ${JOB} · qwen-image-2.1-uc-edit · статус queued` });
  push({ kind: 'task.completed', seq: 3 });
  assert.deepEqual(JSON.parse(app.w.localStorage.getItem('bm.noteWait.T1')).jobs, [JOB], 'the phone remembers the edit on its way');
  await until(() => threadReads >= 2, 'thread re-read while waiting');
  assert.equal(app.d.querySelector('.msg.note'), null, 'nothing before the job ends');
  delivered = true;
  await until(() => app.d.querySelector('.msg.note .inline-photo img'), 'note arrived by itself');
  await until(() => app.w.localStorage.getItem('bm.noteWait.T1') === null, 'waiting cleared');
  const reads = threadReads; await new Promise((r) => setTimeout(r, 80));
  assert.equal(threadReads, reads, 'polling stops once the note is here');
  assert.equal(app.d.querySelectorAll('.msg.note').length, 1, 'shown once');
});
