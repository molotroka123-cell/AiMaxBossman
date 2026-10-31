/* Owner 31.10, mobile pult chat (REAL app.js + index.html in jsdom, scripted /m/api, synthetic chats only):
   1) several chats at once — Send/STOP and the stream belong to the chat on screen; STOP stops only that chat;
   2) the next prompt typed while the model answers goes to the THREAD queue (visible, cancellable); ⚡ Steer sends now;
   3) live progress: queue reason, model thinking, tool, privacy notice — instead of a silent wait;
   4) a cloud answer is labelled «облако · 7API», a local one «локально»;
   5) the history does not fit the model window → a visible card, and the chat is compacted after the answer;
   6) «Перенести контекст из…» from the chat menu. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, closeAll, skip, sleep, until } from './_boot.mjs';

after(closeAll);

const AGENTS = { items: [
  { id: 1, name: 'Gemma', tools: [], model: { alias: 'gemma' }, local: true, cloud: false, allowed: true, provider: '' },
  { id: 4, name: 'GLM 5.3 Flash', tools: [], model: { alias: 'glm-5.3-flash' }, local: false, cloud: true, allowed: true, provider: '7API' },
], default_agent_id: 1 };

function world(o = {}) {
  const st = { tasks: 0, threads: o.threads || [{ id: 'A', title: 'Чат A', turns: 0 }, { id: 'B', title: 'Чат B', turns: 1 }],
    turns: o.turns || { A: [], B: [{ idx: 0, task_id: 3, text: 'старый вопрос', status: 'completed', result: 'старый ответ' }] },
    context: o.context || null, queue: { A: [], B: [] }, ...o };
  const routes = (path, method, body) => {
    const p = path.split('?')[0];
    if (p === '/agents') return AGENTS;
    if (p === '/threads' && method === 'GET') return { items: st.threads };
    if (p === '/threads' && method === 'POST') return { id: 'N', title: body && body.title };
    const m = /^\/threads\/([A-Z])(\/.*)?$/.exec(p);
    if (m) {
      const [, id, rest] = m;
      if (!rest && method === 'GET') return { id, turns: st.turns[id] || [] };
      if (rest === '/send') { st.tasks += 1; return { task: { id: 100 + st.tasks, status: 'queued' }, admission: { ok: true }, model: { alias: id === 'A' && st.cloud ? 'glm-5.3-flash' : 'gemma', locality: st.cloud ? 'cloud' : 'local' }, context: st.context }; }
      if (rest === '/stop') return { ok: true, task_id: 101, status: 'stopped' };
      if (rest === '/queue' && method === 'GET') return { items: st.queue[id] || [] };
      if (rest === '/queue' && method === 'POST') { const it = { id: 'q1', text: body.text, kind: 'queued' }; st.queue[id] = [...(st.queue[id] || []), it]; return { __status: 201, body: { item: it, items: st.queue[id] } }; }
      if (rest && rest.startsWith('/queue/') && method === 'DELETE') { st.queue[id] = []; return { ok: true }; }
      if (rest === '/steer') return { __status: 202, body: { ok: true, task_id: 101, pending: 1 } };
      if (rest === '/import-context') return { __status: 201, body: { ok: true, imported: true } };
    }
    if (/^\/tasks\/\d+$/.test(p)) return { id: Number(p.split('/')[2]), status: st.done ? 'completed' : 'running', result: st.done ? 'готово' : null };
    if (/^\/tasks\/\d+\/queue$/.test(p)) return { status: 'queued', workers: 3, reason: 'slots_busy', running: [{ task_id: 55, thread_title: 'Другой чат' }] };
    if (/^\/tasks\/\d+\/history$/.test(p)) return { events: [] };
    if (p === '/compacted-threads') return { items: [{ id: 'B', title: 'Чат B', summary_chars: 900, summary_tokens: 410 }] };
    if (p.startsWith('/agent/sessions/') && p.endsWith('/compact')) return { __status: 202, body: { status: 'running' } };
    if (p.startsWith('/agent/sessions/') && p.endsWith('/context')) return { compact: { state: 'idle' } };
    return undefined;
  };
  return { st, routes };
}

async function app(o = {}) {
  const { st, routes } = world(o);
  const sources = [];
  const ctx = await boot({ routes, before: (w) => {
    w.EventSource = class { constructor(u) { this.url = u; sources.push(this); } close() { this.closed = true; } };
    if (o.agent) w.localStorage.setItem('bm.agent', String(o.agent));
    if (o.thread) w.localStorage.setItem('bm.thread', o.thread);
  } });
  await until(() => ctx.$('model-name').textContent.includes(o.agent === 4 ? 'GLM' : 'Gemma'), 'agent loaded');
  const ask = async (text) => {
    ctx.$('input').value = text;
    ctx.$('composer').dispatchEvent(new ctx.w.Event('submit', { cancelable: true, bubbles: true }));
  };
  const push = (es, ev) => es.onmessage({ data: JSON.stringify(ev) });
  const openFromDrawer = async (id) => {
    ctx.$('btn-drawer').click();
    await until(() => [...ctx.d.querySelectorAll('#thread-list .thread')].length, 'threads listed');
    const row = [...ctx.d.querySelectorAll('#thread-list .thread')].find((b) => b.textContent.includes(`Чат ${id}`));
    row.click();
    await until(() => ctx.calls.some((c) => c.path === `/threads/${id}` && c.method === 'GET'), `thread ${id} opened`);
    await sleep(30);
  };
  return { ...ctx, st, sources, ask, push, openFromDrawer };
}

const posts = (calls, path) => calls.filter((c) => c.method === 'POST' && c.path === path);

test('another chat is free while one chat answers; STOP stops only the chat on screen', { skip }, async () => {
  const a = await app({ thread: 'A' });
  await a.ask('долгий вопрос в A');
  await until(() => a.sources.length === 1, 'A streams');
  assert.equal(a.$('btn-stop').hidden, false, 'A shows STOP while it answers');
  await a.openFromDrawer('B');
  assert.equal(a.sources[0].closed, true, 'leaving A only stops watching it');
  assert.equal(a.$('btn-stop').hidden, true, 'B is idle: no STOP (was: the red STOP stayed and B could not send)');
  assert.equal(a.$('btn-send').hidden, false);
  assert.ok(!a.$('btn-send').classList.contains('queue'), 'B sends normally, not into a queue');
  await a.ask('вопрос в B');
  await until(() => posts(a.calls, '/threads/B/send').length === 1, 'B sent while A still runs on the PC');
  assert.equal(posts(a.calls, '/threads/A/queue').length + posts(a.calls, '/threads/B/queue').length, 0);
  a.$('btn-stop').click();
  await until(() => posts(a.calls, '/threads/B/stop').length === 1, 'STOP goes to B');
  assert.equal(posts(a.calls, '/threads/A/stop').length, 0, 'A is not stopped');
});

test('a running turn of the reopened chat brings back its STOP; a reply for A after moving to B does not hijack B', { skip }, async () => {
  const turns = { A: [{ idx: 0, task_id: 9, text: 'идёт', status: 'running' }], B: [] };
  const a = await app({ thread: 'B', turns });
  await a.openFromDrawer('A');
  await until(() => a.sources.some((s) => s.url.includes('/tasks/9/')), 'A running turn followed');
  assert.equal(a.$('btn-stop').hidden, false);
  await a.openFromDrawer('B');
  assert.equal(a.$('btn-stop').hidden, true);
});

test('typed while answering -> thread queue (visible, cancellable); ⚡ Steer sends now', { skip }, async () => {
  const a = await app({ thread: 'A' });
  await a.ask('первый');
  await until(() => a.sources.length === 1, 'streaming');
  assert.equal(a.$('btn-steer').hidden, false, 'Steer is offered while the model answers');
  assert.ok(a.$('btn-send').classList.contains('queue') && /очередь/.test(a.$('btn-send').getAttribute('aria-label')));
  await a.ask('второй');
  await until(() => posts(a.calls, '/threads/A/queue').length === 1, 'queued');
  assert.deepEqual(posts(a.calls, '/threads/A/queue')[0].body, { text: 'второй', agent_id: 1 });
  assert.equal(posts(a.calls, '/threads/A/send').length, 1, 'not a second parallel turn');
  await until(() => !a.$('chat-queue').hidden && a.$('chat-queue').textContent.includes('второй'), 'queue visible');
  a.$('chat-queue').querySelector('.q-x').click();
  await until(() => a.calls.some((c) => c.method === 'DELETE' && c.path === '/threads/A/queue/q1'), 'cancel sent');
  a.$('input').value = 'стоп, сделай короче';
  a.$('btn-steer').click();
  await until(() => posts(a.calls, '/threads/A/steer').length === 1, 'steer sent');
  assert.deepEqual(posts(a.calls, '/threads/A/steer')[0].body, { text: 'стоп, сделай короче' });
  await until(() => a.d.querySelector('.msg.user.steer'), 'the steer is shown in the chat');
});

test('live progress: queue reason, thinking, tool; privacy notice; cloud/local badge', { skip }, async () => {
  const a = await app({ thread: 'A', agent: 4, cloud: true });
  await a.ask('вопрос облаку');
  await until(() => a.sources.length === 1, 'streaming');
  const es = a.sources[0]; const live = () => (a.d.querySelector('.live-status') || {}).textContent || '';
  a.push(es, { kind: 'task.queued', task_id: 101, attempt: 0 });
  await until(() => live().includes('заняты все 3 слота — «Другой чат»'), 'queue reason shown');
  a.push(es, { kind: 'task.started', task_id: 101 });
  assert.match(live(), /думает/);
  a.push(es, { kind: 'run.tool_use', task_id: 101, call_id: 'c1', tool: 'code.search', args: {} });
  assert.match(live(), /Инструмент: code\.search/);
  a.push(es, { kind: 'run.notice', task_id: 101, notice: 'privacy_withheld', reason_code: 'confidential_external_forbidden', rules: ['protected_secret_file'] });
  assert.ok(a.d.querySelector('.notice.privacy').textContent.includes('Облачная модель не получила этот запрос'));
  a.st.done = true;
  a.push(es, { kind: 'task.completed', task_id: 101 });
  await until(() => a.d.querySelector('.msg.bot .meta .badge-local'), 'answered');
  assert.match(a.d.querySelector('.msg.bot .meta').textContent, /облако не получило запрос/, 'the answer says it was made locally');
  assert.equal(a.d.querySelector('.live-status'), null, 'the live line goes away with the answer');
});

test('a cloud answer is labelled «облако · 7API»; a local one «локально»', { skip }, async () => {
  const c = await app({ thread: 'A', agent: 4, cloud: true });
  await c.ask('x'); await until(() => c.sources.length === 1, 'streaming');
  c.st.done = true; c.push(c.sources[0], { kind: 'task.completed', task_id: 101 });
  await until(() => c.d.querySelector('.msg.bot .meta span'), 'answered');
  assert.equal(c.d.querySelector('.msg.bot .meta span').textContent, 'облако · 7API');
  const l = await app({ thread: 'A', agent: 1 });
  await l.ask('x'); await until(() => l.sources.length === 1, 'streaming');
  l.st.done = true; l.push(l.sources[0], { kind: 'task.completed', task_id: 101 });
  await until(() => l.d.querySelector('.msg.bot .meta span'), 'answered');
  assert.equal(l.d.querySelector('.msg.bot .meta span').textContent, 'локально');
});

test('history does not fit the window: visible card, then automatic /compact after the answer', { skip }, async () => {
  const context = { turns_live: 20, turns_included: 8, turns_dropped: 12, compact_suggested: true, model: 'gemma', budget_tokens: 14000, used_tokens: 13900 };
  const a = await app({ thread: 'A', context });
  await a.ask('итог?');
  await until(() => a.d.querySelector('.ctx-fit-card'), 'card shown');
  assert.match(a.d.querySelector('.ctx-fit-card').textContent, /видит 8 из 20 ходов/);
  a.st.done = true; a.push(a.sources[0], { kind: 'task.completed', task_id: 101 });
  await until(() => posts(a.calls, '/agent/sessions/A/compact').length === 1, 'compacted automatically after the answer');
  assert.match(a.d.querySelector('.msg.bot .meta').textContent, /контекст: 8 из 20 ходов/);
});

test('switching the model inside a chat keeps the chat: the next turn goes to the new model with the whole chat', { skip }, async () => {
  const a = await app({ thread: 'B' });
  await until(() => a.calls.some((c) => c.path === '/threads/B' && c.method === 'GET'), 'chat B open');
  a.$('btn-model').click();
  const glm = [...a.d.querySelectorAll('.model-opt')].find((b) => b.textContent.includes('GLM'));
  assert.match(glm.textContent, /облако · 7API/, 'the picker labels the cloud agent');
  glm.click();
  await until(() => a.calls.some((c) => c.path === '/agent/sessions/B/context?agent_id=4'), 'gauge counts the new model');
  await a.ask('продолжи');
  await until(() => posts(a.calls, '/threads/B/send').length === 1, 'sent in the SAME chat (was: a new chat)');
  assert.equal(posts(a.calls, '/threads/B/send')[0].body.agent_id, 4);
  assert.equal(posts(a.calls, '/threads').length, 0, 'no new chat created');
});

test('negative control: history fits -> no card, no compaction; «Не сжимать» cancels the automatic one', { skip }, async () => {
  const fits = await app({ thread: 'A', context: { turns_live: 5, turns_included: 5, turns_dropped: 0, compact_suggested: false } });
  await fits.ask('x'); await until(() => fits.sources.length === 1, 'streaming');
  fits.st.done = true; fits.push(fits.sources[0], { kind: 'task.completed', task_id: 101 });
  await sleep(500);
  assert.equal(fits.d.querySelector('.ctx-fit-card'), null);
  assert.equal(posts(fits.calls, '/agent/sessions/A/compact').length, 0);
  const no = await app({ thread: 'A', context: { turns_live: 20, turns_included: 8, turns_dropped: 12, compact_suggested: true, model: 'gemma', budget_tokens: 14000 } });
  await no.ask('x'); await until(() => no.d.querySelector('.ctx-fit-card'), 'card');
  [...no.d.querySelectorAll('.ctx-fit-card button')].find((b) => b.textContent === 'Не сжимать').click();
  no.st.done = true; no.push(no.sources[0], { kind: 'task.completed', task_id: 101 });
  await sleep(500);
  assert.equal(posts(no.calls, '/agent/sessions/A/compact').length, 0);
});

test('«Перенести контекст из…»: compacted chats are listed and the pick is imported into this chat', { skip }, async () => {
  const a = await app({ thread: 'A' });
  a.$('btn-drawer').click();
  await until(() => a.d.querySelectorAll('#thread-list .thread').length === 2, 'listed');
  const rowA = [...a.d.querySelectorAll('#thread-list .thread')].find((b) => b.textContent.includes('Чат A'));
  rowA.dispatchEvent(new a.w.KeyboardEvent('keydown', { key: 'ContextMenu', bubbles: true, cancelable: true }));
  await until(() => a.d.querySelector('.chat-actions [data-act="import-from"]'), 'menu');
  a.d.querySelector('.chat-actions [data-act="import-from"]').click();
  await until(() => a.d.querySelector('.chat-actions [data-act="import-pick"]'), 'compacted list');
  assert.match(a.d.querySelector('.chat-actions [data-act="import-pick"]').textContent, /Чат B/);
  a.d.querySelector('.chat-actions [data-act="import-pick"]').click();
  await until(() => posts(a.calls, '/threads/A/import-context').length === 1, 'imported');
  assert.deepEqual(posts(a.calls, '/threads/A/import-context')[0].body, { source_thread_id: 'B', agent_id: 1 });
  await until(() => a.d.querySelector('.chat-actions .sheet-ok'), 'result shown');
});
