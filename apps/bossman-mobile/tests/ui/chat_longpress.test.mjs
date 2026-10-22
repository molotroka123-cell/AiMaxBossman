/* Long-press on a chat row (owner 22.10): 500 ms hold opens an action sheet — «Архивировать в зашифрованную папку»,
   «Удалить навсегда» (typed short id), «Отмена»; early release or movement over 8 px cancels; Shift+F10 / ContextMenu /
   right-click open it too; a failed operation never removes the chat from the list; the drawer lists the encrypted
   archive with «Восстановить» / «Удалить архив». Runs the REAL app.js against the REAL index.html in jsdom with a
   scripted /m/api (synthetic chats only; skipped when jsdom is not installed). */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { boot, skip, until, sleep, closeAll, UI } from './_boot.mjs';

after(closeAll);
const ID = '20261022-101010-abc123';
const SHORT = 'abc123';

function world(over = {}) {
  const st = { threads: [{ id: ID, title: 'Рабочий чат', turns: 2, updated_at: '2026-10-22T10:10:00' }], archive: [], archiveFail: null, deleteFail: null, ...over };
  const routes = (path, method, body) => {
    if (path === '/threads' && method === 'GET') return { items: st.threads };
    if (path === `/threads/${ID}/archive-encrypted` && method === 'POST') {
      if (st.archiveFail) return { __status: 503, body: { detail: { message: 'ключ шифрованного архива недоступен', code: 'CHAT_ARCHIVE_KEY_UNAVAILABLE' } } };
      st.archive.push({ id: ID, title: 'Рабочий чат', archived_at: '2026-10-22T10:11:00', size: 2048 });
      st.threads = st.threads.filter((t) => t.id !== ID);
      return { ok: true, encrypted: true, id: ID };
    }
    if (path === `/threads/${ID}/delete-forever` && method === 'POST') {
      if (st.deleteFail) return { __status: 422, body: { detail: { message: 'нужно подтверждение', code: 'BM_CONFIRM_REQUIRED' } } };
      st.threads = st.threads.filter((t) => t.id !== ID);
      return { ok: true, purged: true };
    }
    if (path === '/archive' && method === 'GET') return { items: st.archive };
    if (path === `/archive/${ID}/restore` && method === 'POST') {
      st.threads.push({ id: ID, title: 'Рабочий чат', turns: 2 }); st.archive = st.archive.filter((a) => a.id !== ID); return { ok: true };
    }
    if (path === `/archive/${ID}` && method === 'DELETE') { st.archive = st.archive.filter((a) => a.id !== ID); return { ok: true, deleted: true }; }
    if (path === `/threads/${ID}`) return { id: ID, turns: [] };
    return undefined;
  };
  return { st, routes };
}
const layout = (w) => { w.Element.prototype.getClientRects = function () { return this.closest('[hidden]') ? [] : [{ width: 10, height: 10 }]; }; };
const ptr = (w, node, type, x = 10, y = 10) => node.dispatchEvent(new w.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
async function openDrawer(opts) {
  const ctx = await boot({ vendor: true, before: layout, ...opts });
  ctx.$('btn-drawer').click();
  await until(() => ctx.d.querySelector('#thread-list .thread'), 'chat row');
  ctx.row = ctx.d.querySelector('#thread-list .thread');
  ctx.sheet = () => ctx.d.querySelector('.chat-actions');
  ctx.act = (name) => ctx.sheet().querySelector(`[data-act="${name}"]`);
  return ctx;
}
const posts = (calls, suffix) => calls.filter((c) => c.method === 'POST' && c.path.endsWith(suffix));

test('hold for 500 ms opens the sheet with the three actions; the click after the hold does not open the chat', { skip }, async () => {
  const { routes } = world();
  const buzz = [];
  const ctx = await openDrawer({ routes, before: (w) => { layout(w); Object.defineProperty(w.navigator, 'vibrate', { value: (n) => { buzz.push(n); return true; }, configurable: true }); } });
  ptr(ctx.w, ctx.row, 'pointerdown');
  await sleep(300); assert.equal(ctx.sheet(), null, 'not yet at 300 ms');
  await sleep(300);
  assert.ok(ctx.sheet(), 'sheet opened after 500 ms');
  assert.ok(buzz.length >= 1, 'haptic feedback requested');
  const sheet = ctx.sheet();
  assert.equal(sheet.getAttribute('role'), 'dialog'); assert.equal(sheet.getAttribute('aria-modal'), 'true');
  assert.deepEqual([...sheet.querySelectorAll('button')].map((b) => b.textContent),
    ['Архивировать в зашифрованную папку', 'Удалить навсегда', 'Отмена']);
  assert.ok(sheet.textContent.includes(`…${SHORT}`), 'short id is shown');
  ctx.row.dispatchEvent(new ctx.w.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));   // Android sends it after the hold
  assert.equal(ctx.d.querySelectorAll('.chat-actions').length, 1, 'no second sheet from the follow-up contextmenu');
  ptr(ctx.w, ctx.row, 'pointerup'); ctx.row.click();
  assert.equal(ctx.calls.filter((c) => c.path === `/threads/${ID}` && c.method === 'GET').length, 0, 'the chat was not opened by the release click');
  ctx.act('cancel').click();
  assert.equal(ctx.sheet(), null, 'Отмена closes');
  assert.equal(ctx.calls.filter((c) => c.method !== 'GET').length, 0, 'nothing was sent');
});

test('early release, movement over 8 px, and leaving the row cancel; a normal tap still opens the chat', { skip }, async () => {
  const { routes } = world();
  const ctx = await openDrawer({ routes });
  ptr(ctx.w, ctx.row, 'pointerdown'); await sleep(200); ptr(ctx.w, ctx.row, 'pointerup'); await sleep(450);
  assert.equal(ctx.sheet(), null, 'released at 200 ms');
  ptr(ctx.w, ctx.row, 'pointerdown', 10, 10); await sleep(100); ptr(ctx.w, ctx.row, 'pointermove', 10, 30); await sleep(550);
  assert.equal(ctx.sheet(), null, 'moved 20 px (scroll)');
  ptr(ctx.w, ctx.row, 'pointerdown', 10, 10); await sleep(100); ptr(ctx.w, ctx.row, 'pointermove', 14, 13); await sleep(500);
  assert.ok(ctx.sheet(), 'a 5 px jitter does not cancel');
  ctx.act('cancel').click();
  ptr(ctx.w, ctx.row, 'pointerdown'); await sleep(100); ptr(ctx.w, ctx.row, 'pointerleave'); await sleep(550);
  assert.equal(ctx.sheet(), null, 'finger left the row');
  ptr(ctx.w, ctx.row, 'pointerdown'); ptr(ctx.w, ctx.row, 'pointerup'); ctx.row.click();
  await until(() => ctx.calls.some((c) => c.path === `/threads/${ID}` && c.method === 'GET'), 'a short tap opens the chat');
});

test('keyboard (Shift+F10, ContextMenu) and right-click open the same sheet', { skip }, async () => {
  const { routes } = world();
  const ctx = await openDrawer({ routes });
  const key = (k, extra = {}) => ctx.row.dispatchEvent(new ctx.w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra }));
  key('F10'); assert.equal(ctx.sheet(), null, 'plain F10 does nothing');
  key('F10', { shiftKey: true }); assert.ok(ctx.sheet(), 'Shift+F10');
  await until(() => ctx.sheet().contains(ctx.d.activeElement), 'focus is inside the sheet');
  assert.equal(ctx.d.activeElement, ctx.act('cancel'), 'focus starts on the safe choice');
  ctx.d.activeElement.dispatchEvent(new ctx.w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await until(() => ctx.sheet() === null, 'Escape closes the sheet');
  key('ContextMenu'); assert.ok(ctx.sheet(), 'ContextMenu key'); ctx.act('cancel').click();
  const ev = new ctx.w.MouseEvent('contextmenu', { bubbles: true, cancelable: true }); ctx.row.dispatchEvent(ev);
  assert.ok(ctx.sheet(), 'right-click'); assert.equal(ev.defaultPrevented, true);
  assert.equal(ctx.d.querySelectorAll('.chat-actions').length, 1, 'one sheet only');
});

test('encrypted archive: one confirm tap, the result is shown, the row leaves the list only after success', { skip }, async () => {
  const { routes } = world();
  const ctx = await openDrawer({ routes });
  ctx.row.dispatchEvent(new ctx.w.KeyboardEvent('keydown', { key: 'ContextMenu', bubbles: true, cancelable: true }));
  ctx.act('archive').click();
  assert.equal(posts(ctx.calls, '/archive-encrypted').length, 0, 'the first tap only asks');
  ctx.act('archive-confirm').click();
  await until(() => ctx.act('done'), 'result shown');
  assert.ok(ctx.sheet().textContent.includes('Зашифровано, чат убран из списка. Ключ хранится на ПК (DPAPI)'));
  assert.equal(posts(ctx.calls, '/archive-encrypted').length, 1);
  assert.equal(posts(ctx.calls, '/archive-encrypted')[0].csrf, 'tok', 'CSRF header sent');
  await until(() => !ctx.d.querySelector('#thread-list .thread'), 'row gone after the reload');
  ctx.act('done').click(); assert.equal(ctx.sheet(), null);
});

test('FAILED archive keeps the row, shows the error and reloads the list', { skip }, async () => {
  const { routes } = world({ archiveFail: true });
  const ctx = await openDrawer({ routes });
  const loadsBefore = ctx.calls.filter((c) => c.path === '/threads' && c.method === 'GET').length;
  ctx.row.dispatchEvent(new ctx.w.KeyboardEvent('keydown', { key: 'ContextMenu', bubbles: true, cancelable: true }));
  ctx.act('archive').click(); ctx.act('archive-confirm').click();
  await until(() => ctx.sheet().querySelector('.sheet-error') && !ctx.sheet().querySelector('.sheet-error').hidden, 'error shown');
  assert.ok(ctx.sheet().querySelector('.sheet-error').textContent.includes('ключ шифрованного архива недоступен'));
  await until(() => ctx.calls.filter((c) => c.path === '/threads' && c.method === 'GET').length > loadsBefore, 'list reloaded');
  assert.ok(ctx.d.querySelector('#thread-list .thread'), 'the chat is still in the list');
  assert.equal(ctx.act('archive-confirm').disabled, false, 'the owner can retry');
  assert.ok(!ctx.sheet().textContent.includes('Зашифровано, чат убран'), 'no false success message');
});

test('delete forever needs the typed short id (last 6 characters); wrong text sends nothing', { skip }, async () => {
  const { routes, st } = world();
  const ctx = await openDrawer({ routes });
  ctx.row.dispatchEvent(new ctx.w.KeyboardEvent('keydown', { key: 'ContextMenu', bubbles: true, cancelable: true }));
  ctx.act('delete').click();
  const go = ctx.act('delete-confirm'); const inp = ctx.sheet().querySelector('input');
  assert.ok(ctx.sheet().textContent.includes(SHORT), 'the id to type is shown');
  assert.equal(go.disabled, true, 'disabled until typed');
  for (const wrong of ['', 'abc12', 'ABC123', ID, 'да', 'ok']) {
    inp.value = wrong; inp.dispatchEvent(new ctx.w.Event('input', { bubbles: true }));
    assert.equal(go.disabled, true, `"${wrong}" does not enable the button`); go.click();
  }
  assert.equal(posts(ctx.calls, '/delete-forever').length, 0, 'nothing sent for wrong text');
  assert.equal(st.threads.length, 1);
  inp.value = SHORT; inp.dispatchEvent(new ctx.w.Event('input', { bubbles: true }));
  assert.equal(go.disabled, false); go.click();
  await until(() => posts(ctx.calls, '/delete-forever').length === 1, 'sent');
  assert.deepEqual(posts(ctx.calls, '/delete-forever')[0].body, { confirm: SHORT });
  await until(() => ctx.sheet() === null, 'sheet closed');
  await until(() => !ctx.d.querySelector('#thread-list .thread'), 'row removed after success');
});

test('FAILED delete forever keeps the row and shows the error', { skip }, async () => {
  const { routes } = world({ deleteFail: true });
  const ctx = await openDrawer({ routes });
  ctx.row.dispatchEvent(new ctx.w.KeyboardEvent('keydown', { key: 'ContextMenu', bubbles: true, cancelable: true }));
  ctx.act('delete').click();
  const inp = ctx.sheet().querySelector('input'); inp.value = SHORT; inp.dispatchEvent(new ctx.w.Event('input', { bubbles: true }));
  ctx.act('delete-confirm').click();
  await until(() => !ctx.sheet().querySelector('.sheet-error').hidden, 'error shown');
  assert.ok(ctx.sheet().querySelector('.sheet-error').textContent.includes('нужно подтверждение'));
  await sleep(100);
  assert.ok(ctx.d.querySelector('#thread-list .thread'), 'row is still there');
});

test('drawer entry «Зашифрованный архив»: list, restore, delete archive with a confirm', { skip }, async () => {
  const { routes, st } = world({ threads: [], archive: [{ id: ID, title: 'Рабочий чат', archived_at: '2026-10-22T10:11:00', size: 2048 }] });
  const ctx = await boot({ routes, vendor: true, before: layout });
  ctx.$('btn-drawer').click();
  assert.equal(ctx.$('drawer-archive').textContent, 'Зашифрованный архив');
  ctx.$('drawer-archive').click();
  await until(() => ctx.d.querySelector('.archive-row'), 'archive row');
  const row = ctx.d.querySelector('.archive-row');
  assert.ok(row.textContent.includes('Рабочий чат') && row.textContent.includes('2 КБ'));
  row.querySelector('[data-act="archive-delete"]').click();
  await until(() => ctx.d.querySelector('.confirm [data-act="cancel"]'), 'confirm shown');
  ctx.d.querySelector('.confirm [data-act="cancel"]').click();
  await sleep(50);
  assert.equal(ctx.calls.filter((c) => c.method === 'DELETE').length, 0, 'cancel deletes nothing');
  row.querySelector('[data-act="restore"]').click();
  await until(() => !ctx.d.querySelector('.archive-row'), 'archive emptied after restore');
  assert.equal(st.threads.length, 1);
  assert.ok(ctx.d.querySelector('.archive-list').textContent.includes('Архив пуст'));
  st.archive.push({ id: ID, title: null, archived_at: '2026-10-22T10:12:00', size: 10 });
  ctx.d.querySelector('.chat-actions [data-act="close"]').click();
  ctx.$('drawer-archive').click();
  await until(() => ctx.d.querySelector('.archive-row'), 'archive row again');
  assert.ok(ctx.d.querySelector('.archive-row').textContent.includes(`…${SHORT}`), 'untitled archive shows the short id');
  ctx.d.querySelector('[data-act="archive-delete"]').click();
  await until(() => ctx.d.querySelector('.confirm [data-act="confirm"]'), 'confirm shown');
  ctx.d.querySelector('.confirm [data-act="confirm"]').click();
  await until(() => ctx.calls.some((c) => c.method === 'DELETE' && c.path === `/archive/${ID}`), 'archive delete sent');
  await until(() => !ctx.d.querySelector('.archive-row'), 'row gone');
});

test('zero innerHTML in the shell; the new code only uses textContent builders', () => {
  const js = readFileSync(join(UI, 'app.js'), 'utf8');
  assert.equal(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(js), false);
  const html = readFileSync(join(UI, 'index.html'), 'utf8');
  assert.ok(html.includes('id="drawer-archive"'));
  const sw = readFileSync(join(UI, 'sw.js'), 'utf8');
  assert.ok(Number(/bossman-shell-v(\d+)/.exec(sw)[1]) >= 19, 'service-worker cache bumped');
});
