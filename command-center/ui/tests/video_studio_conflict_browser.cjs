// No mocks: concurrent writes use the same authenticated command API.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
  const base = process.env.VIDEO_UI_URL || 'http://127.0.0.1:8878', id = process.env.VIDEO_UI_PROJECT_ID;
  assert.equal(new URL(base).hostname, '127.0.0.1'); assert(id && process.env.VIDEO_UI_TOKEN_FILE);
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1720, height: 1100 } });
    const auth = await (await context.request.post(base + '/api/login', { data: { token: fs.readFileSync(process.env.VIDEO_UI_TOKEN_FILE, 'utf8').trim() } })).json();
    await context.addInitScript(value => { localStorage.setItem('bcc.csrf', value); localStorage.setItem('vs.language', '"en"'); }, auth.csrf);
    const page = await context.newPage(), errors = []; page.on('pageerror', e => errors.push(e.message));
    const snapshot = async () => (await context.request.get(`${base}/api/video-studio/projects/${id}`)).json();
    await page.goto(base + '/#/video-studio?project_id=' + id);
    const before = await snapshot(); assert(before.captions.length);
    await page.getByRole('button', { name: 'Captions', exact: true }).click();
    await page.getByLabel('Cue 1 text').fill('A stale dialog must not overwrite new state.');
    const [refreshed, changed] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/projects/' + id) && r.request().method() === 'GET'), context.request.post(base + '/api/video-studio/commands', { headers: { 'X-BCC-CSRF': auth.csrf }, data: { project_id: id, expected_revision: before.revision, operation_id: require('node:crypto').randomUUID(), command: { type: 'project.rename', name: before.name + ' concurrent' } } })]); assert.equal(changed.status(), 200); assert.equal(refreshed.status(), 200);
    // Actual websocket-triggered GET completed; let its callback retain modal's revision.
    await page.waitForTimeout(150);
    const [rejected] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/commands')), page.locator('dialog').getByRole('button', { name: 'Apply', exact: true }).click()]);
    assert.equal(rejected.status(), 409); const after = await snapshot(); assert.equal(after.revision, before.revision + 1); assert.deepEqual(after.captions, before.captions);
    assert.match(await page.locator('.vs-error').textContent(), /Revision conflict/); assert.deepEqual(errors, []);
    console.log(JSON.stringify({ project_id: id, before_revision: before.revision, remote_revision: after.revision, stale_dialog_status: rejected.status(), captions_preserved: true, page_errors: errors }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
