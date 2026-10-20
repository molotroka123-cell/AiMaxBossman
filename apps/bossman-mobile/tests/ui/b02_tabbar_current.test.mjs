/* B02-3: the bottom tab bar showed the open section only by colour (.active). Screen readers could not tell which tab
   is open (WCAG 1.4.1 / 4.1.2). Now exactly one tab carries aria-current="page", it follows every switch, the bar is a
   named navigation landmark and the decorative icons are hidden from assistive tech. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, closeAll } from './_boot.mjs';

after(closeAll);
const current = (d) => Array.from(d.querySelectorAll('.tab[aria-current="page"]')).map((t) => t.dataset.tab);

test('exactly one tab is aria-current and it follows the switch', { skip }, async () => {
  const { d } = await boot();
  assert.deepEqual(current(d), ['chat'], 'chat is current on start');
  for (const name of ['photo', 'swap', 'status', 'calls', 'chat']) {
    d.querySelector(`.tab[data-tab="${name}"]`).click();
    assert.deepEqual(current(d), [name], `after opening ${name}`);
    assert.equal(d.querySelector('.tab.active').dataset.tab, name, 'visual state unchanged');
  }
});

test('tab bar is a named landmark; icons are decorative', { skip }, async () => {
  const { d } = await boot();
  const nav = d.getElementById('tabbar');
  assert.equal(nav.tagName, 'NAV');
  assert.ok((nav.getAttribute('aria-label') || '').trim(), 'nav has a name');
  for (const t of nav.querySelectorAll('.tab')) {
    assert.equal(t.querySelector('svg').getAttribute('aria-hidden'), 'true', `${t.dataset.tab} icon hidden`);
    assert.ok(t.textContent.trim(), `${t.dataset.tab} has a text name`);
    assert.equal(t.getAttribute('type'), 'button');
  }
});
