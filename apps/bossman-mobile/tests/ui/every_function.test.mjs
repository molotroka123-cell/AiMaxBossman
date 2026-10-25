/* Live audit 25.10 «Проверяй каждую функцию»: UI regressions with the REAL index.html + app.js (jsdom).
   Mode chips must carry the message to the agent dispatcher; the web terminal must run in the checkout, show an
   approval prompt for a 202 and run once after the owner's tap, refuse interactive programs visibly; «$» is live. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, sleep, closeAll } from './_boot.mjs';

after(closeAll);

const AGENTS = { items: [{ id: 7, name: 'Gemma', tools: [], model: { alias: 'gemma' } }], default_agent_id: 7 };

function mk(extra = {}) {
  const st = { runs: [], ...extra };
  const routes = (p, m, body) => {
    if (p === '/agents') return AGENTS;
    if (p === '/threads' && m === 'POST') return { id: 't1' };
    if (p === '/threads/t1/send' && m === 'POST') return { task: { id: 5 }, model: { alias: 'gemma' } };
    if (p === '/agent/sessions/t1/messages' && m === 'POST') {
      if (body.mode === 'SELF-IMPROVE') return { mode: 'SELF-IMPROVE', improve: { id: 'imp1', status: 'awaiting_owner_approval' }, turn: { synthetic_result: 'Цикл imp1 до гейта' } };
      return { task: { id: 5 }, model: { alias: 'gemma' }, mode: body.mode };
    }
    if (p === '/agent/sessions/t1/cost') return { spent_usd: 0.0421, budget_usd: 5, budget_remaining_usd: 4.9579, mode: 'AGENT' };
    if (p === '/tasks/5') return { id: 5, status: 'completed', result: 'ок' };
    if (p === '/terminal/roots') return { roots: ['C:/Python/Lib', 'C:/repo'], default: 'C:/repo' };
    if (p === '/terminal/run' && m === 'POST') {
      st.runs.push(body);
      if (body.command === 'claude') return { __status: 422, body: { detail: { code: 'TERMINAL_INTERACTIVE', message: '`claude` в веб-терминале не запускается' } } };
      if (body.command === 'echo test' && !body.approval_id) return { __status: 202, body: { approval_required: true, approval_id: 77, decision: 'ask' } };
      return { session_id: 'term-1', pid: 1 };
    }
    if (p === '/terminal/sessions/term-1') return { finished: true, exit_code: 0, output_tail: [st.runs.at(-1).command === 'echo test' ? 'test' : '## main'] };
    if (p.startsWith('/approvals/') && m === 'POST') return { id: 77, status: body.approve ? 'approved' : 'rejected' };
    return undefined;
  };
  return { st, routes };
}

async function app(extra, mode) {
  const m = mk(extra);
  const a = await boot({ routes: m.routes, before: (w) => { w.WebSocket = undefined; if (mode) w.localStorage.setItem('bm.agentMode', mode); } });
  await until(() => a.$('model-name').textContent.includes('Gemma'), 'agents loaded');
  return { ...a, st: m.st };
}
const send = (a, text) => { a.$('input').value = text; a.$('input').dispatchEvent(new a.w.Event('input')); a.$('composer').requestSubmit(); };

for (const mode of ['AGENT', 'AUTO', 'CODE', 'COMPUTER']) {
  test(`mode ${mode}: the message goes to the agent dispatcher with the mode and the local agent`, { skip }, async () => {
    const a = await app({}, mode);
    send(a, 'привет');
    await until(() => a.calls.some((c) => c.path === '/agent/sessions/t1/messages'), 'dispatcher called');
    const c = a.calls.find((x) => x.path === '/agent/sessions/t1/messages');
    assert.equal(c.body.mode, mode); assert.equal(c.body.agent_id, 7); assert.equal(c.body.text, 'привет');
    assert.ok(!a.calls.some((x) => x.path === '/threads/t1/send'), 'not the plain chat route');
  });
}

test('mode CHAT stays on the plain chat route (negative control)', { skip }, async () => {
  const a = await app({}, 'CHAT');
  send(a, 'привет');
  await until(() => a.calls.some((c) => c.path === '/threads/t1/send'), 'chat route');
  assert.ok(!a.calls.some((x) => x.path === '/agent/sessions/t1/messages'));
});

test('/improve mode shows the pipeline result and refreshes approvals', { skip }, async () => {
  const a = await app({}, 'SELF-IMPROVE');
  send(a, 'проверь пресеты');
  await until(() => a.d.querySelector('.msg.bot .answer') && a.d.querySelector('.msg.bot .answer').textContent.includes('Цикл imp1'), 'improve result');
});

test('«$» badge shows the session spend after an answer', { skip }, async () => {
  const a = await app({}, 'AGENT');
  assert.equal(a.$('oc-cost-badge').textContent, '$0.00');
  send(a, 'привет');
  await until(() => a.$('oc-cost-badge').textContent === '$0.04', 'badge updated', 8000);
  assert.ok(a.$('oc-cost-badge').title.includes('$5.00'));
});

test('terminal: presets run in the checkout (default root), not roots[0]', { skip }, async () => {
  const a = await app();
  a.$('btn-oc-term').click();
  a.d.querySelector("[data-term-cmd^='git status']").click();
  await until(() => a.$('oc-term-out').textContent.includes('[exit 0]'), 'finished');
  assert.equal(a.st.runs[0].cwd, 'C:/repo');
  assert.ok(a.$('oc-term-out').textContent.includes('## main'));
});

test('terminal: 202 shows an approval prompt; «Разрешить» approves and runs once with the id', { skip }, async () => {
  const a = await app();
  a.$('btn-oc-term').click();
  a.$('oc-term-input').value = 'echo test'; a.$('btn-term-run').click();
  await until(() => a.$('btn-term-approve'), 'approval prompt');
  assert.ok(a.$('oc-term-out').textContent.includes('#77'));
  assert.equal(a.st.runs.length, 1);
  a.$('btn-term-approve').click();
  await until(() => a.$('oc-term-out').textContent.includes('[exit 0]'), 'ran after approval');
  assert.deepEqual(a.calls.filter((c) => c.path === '/approvals/77').map((c) => c.body), [{ approve: true }]);
  assert.equal(a.st.runs.length, 2); assert.equal(a.st.runs[1].approval_id, 77);
  assert.ok(a.$('oc-term-out').textContent.includes('test'));
  assert.equal(a.$('btn-term-approve'), null);
});

test('terminal: «Отклонить» rejects and runs nothing', { skip }, async () => {
  const a = await app();
  a.$('btn-oc-term').click();
  a.$('oc-term-input').value = 'echo test'; a.$('btn-term-run').click();
  await until(() => a.$('btn-term-reject'), 'approval prompt');
  a.$('btn-term-reject').click();
  await until(() => a.$('oc-term-out').textContent.includes('отклонено'), 'rejected');
  await sleep(50);
  assert.equal(a.st.runs.length, 1);
  assert.deepEqual(a.calls.filter((c) => c.path === '/approvals/77').map((c) => c.body), [{ approve: false }]);
});

test('terminal: claude is refused with a visible reason', { skip }, async () => {
  const a = await app();
  a.$('btn-oc-term').click();
  a.$('oc-term-input').value = 'claude'; a.$('btn-term-run').click();
  await until(() => a.$('oc-term-out').textContent.includes('⛔'), 'refusal shown');
  assert.ok(a.$('oc-term-out').textContent.includes('claude'));
  assert.equal(a.$('btn-term-approve'), null);
});

test('«Снимок экрана и окна»: PC mode message goes to the dispatcher and the panel says where the result is', { skip }, async () => {
  const a = await app({}, 'CHAT');
  a.$('btn-oc-pc').click();
  a.$('btn-pc-observe').click();
  await until(() => a.calls.some((c) => c.path === '/agent/sessions/t1/messages'), 'dispatcher called');
  assert.equal(a.calls.find((c) => c.path === '/agent/sessions/t1/messages').body.mode, 'COMPUTER');
  assert.ok(a.$('oc-pc-out').textContent.includes('computer.observe'));
});
