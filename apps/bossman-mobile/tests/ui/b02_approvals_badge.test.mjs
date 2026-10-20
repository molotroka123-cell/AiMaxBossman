/* B02-10: the red counter on «Панель» (approvals waiting for the owner) was a bare number inside the tab, so VoiceOver
   read «Панель 3» with no hint that 3 decisions are waiting. With pending approvals the tab is now named
   «Панель, ждут вашего решения: N»; the decorative digit is hidden; without approvals the plain «Панель» is back. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);
// accessible name, simplified accname: aria-label wins, else text of descendants not hidden from assistive tech
const name = (tab) => tab.getAttribute('aria-label') || Array.from(tab.querySelectorAll('span')).filter((n) => !n.closest('[aria-hidden="true"],[hidden]')).map((n) => n.textContent).join('').trim();

test('pending approvals are part of the tab name; the name resets at zero', { skip }, async () => {
  let items = [{ id: 1 }, { id: 2 }, { id: 3 }];
  const { w, d, $ } = await boot({ routes: (p) => (p === '/approvals' ? { items } : undefined) });
  const tab = d.querySelector('.tab[data-tab="panel"]');
  await until(() => !$('badge-approvals').hidden, 'badge shown');
  assert.equal($('badge-approvals').textContent, '3', 'visual counter unchanged');
  assert.equal($('badge-approvals').getAttribute('aria-hidden'), 'true');
  assert.equal(name(tab), 'Панель, ждут вашего решения: 3');
  items = [];
  d.dispatchEvent(new w.Event('visibilitychange'));
  await until(() => $('badge-approvals').hidden, 'badge hidden');
  assert.equal(name(tab), 'Панель');
});
