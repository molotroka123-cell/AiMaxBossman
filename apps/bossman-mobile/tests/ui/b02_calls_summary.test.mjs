/* B02-7: the «Звонки» summary renders kv() rows (label span + value span) but only `.stat .kv` had layout rules, so in
   the calls card the label and the value ran together as one word: «Аккаунт Telegramвход выполнен». The rows now use
   the same two-column key/value layout as the «Статус» cards (real app.css applied in jsdom). */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);
const STATUS = { account: { state: 'ready', phone: '+420 777 000 000', api_id: 123 }, peer: {}, stop: {}, answering: {}, settings: {}, enabled: false, call_active: false, deps: { ready: true } };

test('calls summary: label and value are separate columns with a gap', { skip }, async () => {
  const { w, d, $ } = await boot({ css: true, routes: (p) => (p === '/calls/status' ? STATUS : p.startsWith('/calls/') ? { items: [] } : undefined) });
  d.querySelector('.tab[data-tab="calls"]').click();
  await until(() => $('calls-summary').querySelector('.kv'), 'summary rows');
  const row = $('calls-summary').querySelector('.kv');
  assert.equal(row.children[0].textContent, 'Аккаунт Telegram');
  const cs = w.getComputedStyle(row);
  assert.equal(cs.display, 'flex', 'two columns, not one run-on line');
  assert.equal(cs.justifyContent, 'space-between');
  assert.notEqual(cs.gap || cs.columnGap || '', '', 'a gap between label and value');
  assert.equal(w.getComputedStyle(row.children[1]).overflowWrap, 'anywhere', 'long phone/API values wrap at 390 px');
});

test('Статус cards keep the same key/value layout', { skip }, async () => {
  const { w, d, $ } = await boot({ css: true, routes: (p) => (p === '/status' ? { bossman: { up: true, build_sha_short: 'abc1234' }, ollama: {}, direct_gen: {}, war_terminal: {}, mobile: {} } : undefined) });
  d.querySelector('.tab[data-tab="status"]').click();
  await until(() => $('status-body').querySelector('.kv'), 'status rows');
  assert.equal(w.getComputedStyle($('status-body').querySelector('.kv')).display, 'flex');
});
