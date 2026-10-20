/* B02-1: index.html had TWO elements with id="swap-form" (face swap in «Видео» and the exchanger command form in
   «Обмен»). getElementById returns the first, so the «Ок» of the exchanger got no handler: a native submit reloaded
   the page and the command never reached /exchange. Ids must be unique; «Ок» posts the typed command. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { boot, skip, until, closeAll, UI } from './_boot.mjs';

after(closeAll);

test('index.html: every id is unique', () => {
  const html = readFileSync(join(UI, 'index.html'), 'utf8');
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]);
  const dup = ids.filter((id, i) => ids.indexOf(id) !== i);
  assert.deepEqual(dup, [], `duplicate ids: ${dup.join(', ')}`);
});

test('exchanger «Ок» posts the typed command and does not navigate', { skip }, async () => {
  const { w, d, calls, $ } = await boot({ routes: (p, m) => (p === '/exchange' && m === 'POST' ? { reply: 'Курс USDT/CZK 23,1', quote_id: null } : undefined) });
  d.querySelector('.tab[data-tab="swap"]').click();
  $('swap-input').value = 'курс';
  const form = $('swap-input').closest('form');
  let prevented = null;
  form.addEventListener('submit', (e) => { setTimeout(() => { prevented = e.defaultPrevented; }, 0); });
  form.requestSubmit();
  await until(() => calls.some((c) => c.path === '/exchange' && c.method === 'POST'), 'POST /exchange');
  assert.equal(calls.find((c) => c.path === '/exchange').body.text, 'курс');
  await until(() => prevented !== null, 'submit observed');
  assert.equal(prevented, true, 'native submit (page reload) must be prevented');
  await until(() => $('swap-reply').textContent === 'Курс USDT/CZK 23,1', 'reply shown');
  assert.equal(w.location.pathname, '/m/');
});

test('face-swap form submit never sends an exchanger command', { skip }, async () => {
  const { d, calls } = await boot();
  const swap = d.getElementById('swap-consent').closest('form');
  swap.requestSubmit();
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(calls.filter((c) => c.path === '/exchange').length, 0);
});
