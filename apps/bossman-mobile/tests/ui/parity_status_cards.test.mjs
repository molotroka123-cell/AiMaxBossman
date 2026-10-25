/* Parity audit 24.10: the «Статус» tab carries what the phone could only reach through the full panel before —
   pending approvals with Разрешить/Отклонить, the owner's «СТОП всё» (confirm() first), Studio orders, the niche
   research run and coding tasks. Each card loads on its own: one failing source does not blank the others. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);

const ROUTES = {
  '/approvals': { items: [{ id: 41, kind: 'tool', preview: 'computer.act click', task_id: 7 }] },
  '/stop/active': { count: 3, planes: { tasks: 2, coding: 1, terminal: 0 }, errors: 0, stop: { active: false } },
  '/studio/orders': { total: 2, by_status: { quoted: 1, delivered: 1 }, orders: [{ order_id: '0123456789abcdef', status: 'quoted', price: '40', currency: 'CZK' }] },
  '/niche/status': { present: true, started: true, day: 3, run_days: 28, queue: { done: 5, running: 1, pending: 9, failed: 0 }, paid_today_usd: 0.25, paid_daily_limit_usd: 2, errors: 1, stopped: null },
  '/coding-tasks/status': { total: 4, active: 2, by_status: { running: 1, queued: 1 } },
};
const card = (d, id) => d.querySelector(`#status-body [data-card="${id}"]`);
const open = async (d, $) => { d.querySelector('.tab[data-tab="status"]').click(); await until(() => card(d, 'coding') && !card(d, 'coding').textContent.includes('загрузка'), 'cards loaded'); };

test('cards render and approvals are decided by the owner tap', { skip }, async () => {
  let pending = ROUTES['/approvals'];
  const { d, $, calls } = await boot({ routes: (p, m) => {
    if (p === '/approvals' && m === 'GET') return pending;
    if (p === '/approvals/41' && m === 'POST') { pending = { items: [] }; return { id: 41, status: 'approved' }; }
    return ROUTES[p.split('?')[0]] ?? (p === '/status' ? { bossman: { up: true } } : undefined);
  } });
  await open(d, $);
  assert.match(card(d, 'approvals').textContent, /computer\.act click/);
  assert.match(card(d, 'stop').textContent, /tasks: 2, coding: 1/);
  assert.match(card(d, 'studio').textContent, /quoted: 1, delivered: 1/);
  assert.match(card(d, 'niche').textContent, /3 из 28/);
  assert.match(card(d, 'coding').textContent, /сейчас идут2/);
  const allow = [...card(d, 'approvals').querySelectorAll('button')].find((b) => b.textContent === 'Разрешить');
  allow.click();
  await until(() => card(d, 'approvals').textContent.includes('Ничего не ждёт решения'), 'approval list refreshed');
  const post = calls.find((c) => c.method === 'POST' && c.path === '/approvals/41');
  assert.deepEqual(post.body, { approve: true });
  assert.equal(post.csrf, 'tok');
});

test('СТОП всё posts only after confirm(); a cancelled confirm sends nothing', { skip }, async () => {
  let answer = false;
  const { d, $, calls } = await boot({
    before: (w) => { w.confirm = () => answer; },
    routes: (p, m) => (p === '/stop' && m === 'POST' ? { ok: true, stopped: { tasks: 2 }, errors: 0, stop: { active: true, since: null } } : ROUTES[p.split('?')[0]]),
  });
  await open(d, $);
  const stopBtn = () => [...card(d, 'stop').querySelectorAll('button')].find((b) => b.textContent.includes('СТОП всё'));
  stopBtn().click();
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(calls.filter((c) => c.method === 'POST' && c.path === '/stop').length, 0, 'negative control: no POST without confirm');
  answer = true;
  stopBtn().click();
  await until(() => calls.some((c) => c.method === 'POST' && c.path === '/stop'), 'stop posted');
  assert.deepEqual(calls.find((c) => c.method === 'POST' && c.path === '/stop').body, { confirm: true });
  await until(() => !$('stop-banner').hidden, 'STOP banner shown');
});

test('one failing source keeps the other cards', { skip }, async () => {
  const { d, $ } = await boot({ routes: (p) => (p === '/studio/orders' ? { __status: 502, body: { detail: { message: 'Bossman не отвечает' } } } : ROUTES[p.split('?')[0]]) });
  await open(d, $);
  await until(() => card(d, 'studio').textContent.includes('Не загрузилось'), 'studio error shown');
  assert.match(card(d, 'studio').textContent, /Bossman не отвечает/);
  assert.match(card(d, 'niche').textContent, /3 из 28/);
  assert.match(card(d, 'approvals').textContent, /computer\.act click/);
});
