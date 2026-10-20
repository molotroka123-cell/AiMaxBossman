/* B02-4: modal layers (model sheet, chat drawer, photo viewer, «Удалить навсегда» confirm) let Tab walk out into the
   page behind them, had no Escape, and left focus wherever it was after closing; the off-screen drawer stayed
   reachable by Tab/VoiceOver. Now: focus-trap 7.8.0 + tabbable 6.5.0 (MIT, vendored in ui/vendor/) keep Tab inside the
   top layer, Escape closes it, focus returns to the button that opened it, the closed drawer is inert.
   getClientRects is stubbed to mimic layout (jsdom has none): elements under [hidden] have no boxes. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { boot, skip, until, sleep, closeAll, UI } from './_boot.mjs';

after(closeAll);
const layout = (w) => { w.Element.prototype.getClientRects = function () { return this.closest('[hidden]') ? [] : [{ width: 10, height: 10 }]; }; };
const AGENTS = { items: [{ id: 1, name: 'Gemma', tools: [], model: { alias: 'gemma' } }, { id: 2, name: 'Qwen', tools: [], model: { alias: 'qwen' } }], default_agent_id: 1 };
const JOB = { job_id: 'j1', status: 'completed', model: 'm', raw_prompt: 'озеро', params: { width: 512, height: 512, seed: 1 }, elapsed_s: 3, result: { mime: 'image/png' } };
const routes = (p) => (p === '/agents' ? AGENTS : p === '/photo/jobs' ? { jobs: [JOB] } : undefined);
const key = (w, target, k, extra = {}) => target.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra }));

test('vendored focus-trap/tabbable: MIT header, license text, loaded before app.js', () => {
  for (const [f, lic] of [['tabbable.umd.min.js', 'LICENSE-tabbable.txt'], ['focus-trap.umd.min.js', 'LICENSE-focus-trap.txt']]) {
    assert.match(readFileSync(join(UI, 'vendor', f), 'utf8').slice(0, 200), /@license MIT/);
    assert.ok(existsSync(join(UI, 'vendor', lic)), lic);
  }
  const html = readFileSync(join(UI, 'index.html'), 'utf8');
  const order = ['vendor/tabbable.umd.min.js', 'vendor/focus-trap.umd.min.js', 'app.js'].map((s) => html.indexOf(`src="${s}"`));
  assert.ok(order.every((i) => i > 0) && order[0] < order[1] && order[1] < order[2], 'script order');
  const sw = readFileSync(join(UI, 'sw.js'), 'utf8');
  assert.ok(sw.includes("'/m/vendor/focus-trap.umd.min.js'") && sw.includes("'/m/vendor/tabbable.umd.min.js'"), 'offline shell has them');
});

test('model sheet: Tab stays inside, Escape closes, focus returns to the model chip', { skip }, async () => {
  const { w, d, $ } = await boot({ routes, vendor: true, before: layout });
  await until(() => $('model-name').textContent.includes('Gemma'), 'agents');
  const chip = $('btn-model'); chip.focus(); chip.click();
  const sheet = $('model-sheet'); assert.equal(sheet.hidden, false);
  const opts = sheet.querySelectorAll('.model-opt');
  await until(() => sheet.contains(d.activeElement), 'focus moved into the sheet (focus-trap delays initial focus one tick)');
  assert.equal(d.activeElement, sheet.querySelector('.model-opt.sel'), 'starts on the selected model');
  opts[opts.length - 1].focus(); key(w, opts[opts.length - 1], 'Tab');
  assert.equal(d.activeElement, opts[0], 'Tab from the last option wraps to the first, not into the page');
  key(w, opts[0], 'Tab', { shiftKey: true });
  assert.equal(d.activeElement, opts[opts.length - 1], 'Shift+Tab wraps backwards');
  key(w, d.activeElement, 'Escape');
  assert.equal(sheet.hidden, true, 'Escape closes');
  assert.equal(d.activeElement, chip, 'focus back on the opener');
});

test('drawer: inert while closed; open = focus inside; Escape closes and is inert again', { skip }, async () => {
  const { w, d, $ } = await boot({ routes, vendor: true, before: layout });
  const dr = $('drawer');
  assert.ok(dr.hasAttribute('inert'), 'closed drawer is inert'); assert.equal(dr.getAttribute('aria-hidden'), 'true');
  const btn = $('btn-drawer'); btn.focus(); btn.click();
  assert.equal(dr.hasAttribute('inert'), false); assert.equal(dr.getAttribute('aria-hidden'), 'false');
  await until(() => dr.contains(d.activeElement), 'focus inside the open drawer');
  key(w, d.activeElement, 'Escape');
  assert.ok(dr.hasAttribute('inert') && !dr.classList.contains('open') && $('scrim').hidden, 'closed again');
  assert.equal(d.activeElement, btn);
});

test('photo viewer + delete confirm: Escape closes the top layer only, then the viewer', { skip }, async () => {
  const { w, d, $ } = await boot({ routes, vendor: true, before: layout });
  d.querySelector('.tab[data-tab="photo"]').click();
  await until(() => d.querySelector('#gallery .thumb'), 'gallery thumb');
  const thumb = d.querySelector('#gallery .thumb'); thumb.focus(); thumb.click();
  assert.equal($('viewer').hidden, false); await until(() => d.activeElement === $('viewer-close'), 'focus on close');
  $('viewer-delete').focus(); $('viewer-delete').click();
  await until(() => d.querySelector('.sheet.confirm'), 'confirm shown');
  const conf = d.querySelector('.sheet.confirm');
  await until(() => d.activeElement && d.activeElement.dataset.act === 'cancel', 'focus on «Отмена»');
  key(w, d.activeElement, 'Escape');
  await sleep(20);
  assert.equal(conf.isConnected, false, 'Escape = cancel');
  assert.equal($('viewer').hidden, false, 'viewer still open under it');
  assert.equal(d.activeElement, $('viewer-delete'), 'focus back on «Удалить навсегда»');
  key(w, d.activeElement, 'Escape');
  assert.equal($('viewer').hidden, true);
  assert.equal(d.activeElement, thumb, 'focus back on the thumbnail');
});

test('without the vendor files (old cached shell) Escape + focus return still work', { skip }, async () => {
  const { w, d, $ } = await boot({ routes, vendor: false, before: layout });
  await until(() => $('model-name').textContent.includes('Gemma'), 'agents');
  const chip = $('btn-model'); chip.focus(); chip.click();
  assert.ok($('model-sheet').contains(d.activeElement));
  key(w, d.activeElement, 'Escape');
  assert.equal($('model-sheet').hidden, true); assert.equal(d.activeElement, chip);
});
