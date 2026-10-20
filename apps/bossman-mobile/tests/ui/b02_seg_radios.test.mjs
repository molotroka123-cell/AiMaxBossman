/* B02-2: the segmented choices (Формат, Размер, Пресет, Кадр, Режим видео) had role="radio" but no aria-checked and
   no group name, so VoiceOver read «radio» without saying which one is on or what the group is. Now: WAI-ARIA radio
   group — aria-checked, a name from the visible label, one Tab stop, arrow keys move and pick. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);
const MODEL = { id: 'z-image-turbo', label: 'Z-Image Turbo', available: true, default_resolution: '512x512', min_steps: 1, max_steps: 20, default_steps: 8, min_side: 256, max_side: 1536 };

async function photoTab() {
  const env = await boot({ routes: (p) => (p === '/photo/models' ? { models: [MODEL] } : undefined) });
  env.d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => env.$('photo-aspect').children.length > 0, 'aspect radios rendered');
  return env;
}
const checked = (box) => Array.from(box.children).filter((b) => b.getAttribute('aria-checked') === 'true').map((b) => b.textContent);

test('every radio says whether it is checked; exactly one is', { skip }, async () => {
  const { $ } = await photoTab();
  const box = $('photo-aspect');
  for (const b of box.children) assert.match(String(b.getAttribute('aria-checked')), /^(true|false)$/, `${b.textContent} has aria-checked`);
  assert.deepEqual(checked(box), ['1:1']);
  Array.from(box.children).find((b) => b.textContent === '16:9').click();
  assert.deepEqual(checked(box), ['16:9']);
  assert.equal(box.querySelector('.on').textContent, '16:9', 'visual state follows');
});

test('each radio group has an accessible name from its visible label', { skip }, async () => {
  const { d } = await photoTab();
  for (const g of d.querySelectorAll('[role="radiogroup"]')) {
    const ref = g.getAttribute('aria-labelledby');
    const name = ref ? (d.getElementById(ref) || {}).textContent : g.getAttribute('aria-label');
    assert.ok(name && name.trim(), `#${g.id} has a name`);
  }
  assert.equal(d.getElementById(d.getElementById('photo-aspect').getAttribute('aria-labelledby')).textContent, 'Формат');
});

test('one Tab stop per group; arrow keys move focus and pick', { skip }, async () => {
  const { w, $ } = await photoTab();
  const box = $('photo-aspect'); const btns = Array.from(box.children);
  assert.deepEqual(btns.filter((b) => b.tabIndex === 0).map((b) => b.textContent), ['1:1'], 'only the checked radio is tabbable');
  btns[0].focus();
  btns[0].dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(w.document.activeElement, btns[1]);
  assert.deepEqual(checked(box), [btns[1].textContent]);
  btns[1].dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  btns[0].dispatchEvent(new w.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  assert.equal(w.document.activeElement, btns[btns.length - 1], 'wraps around');
  assert.equal(btns[btns.length - 1].tabIndex, 0);
});
