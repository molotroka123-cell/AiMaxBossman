// Real locally configured translation/retrieval through the UI and canonical queue.
// Requires an explicitly designated disposable project containing real media.
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
    const page = await context.newPage(), errors = [], checks = []; page.on('pageerror', e => errors.push(e.message));
    const snapshot = async () => (await context.request.get(`${base}/api/video-studio/projects/${id}`)).json();
    const waitJob = async accepted => { for (let i = 0; i < 120; i++) { const job = await (await context.request.get(`${base}/api/video-studio/exports/${accepted.job_id}`)).json(); if (['completed', 'failed', 'stopped'].includes(job.status)) { assert.equal(job.status, 'completed', JSON.stringify(job)); return job; } await page.waitForTimeout(1000); } throw new Error('Job timeout'); };
    const queued = async action => { const [response] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/analysis') && r.request().method() === 'POST'), action()]); assert.equal(response.status(), 200, await response.text()); return waitJob(await response.json()); };
    await page.goto(base + '/#/video-studio?project_id=' + id);
    await page.getByRole('button', { name: 'Captions', exact: true }).click();
    const [imported] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/captions/import')), page.locator('dialog input[type=file]').setInputFiles({ name: 'english.srt', mimeType: 'text/plain', buffer: Buffer.from('1\n00:00:00,000 --> 00:00:00,900\nHello\n') })]);
    assert.equal(imported.status(), 200); const before = await snapshot();
    const translated = await queued(() => page.getByRole('button', { name: 'Translate saved EN → RU', exact: true }).click());
    assert.deepEqual((await snapshot()).captions, before.captions); assert.equal((await snapshot()).revision, before.revision); checks.push('actual local translation queues without mutating captions');
    assert.match(translated.analysis.captions[0].text, /Привет/); assert.equal(translated.analysis.captions[0].start, before.captions[0].start); assert.equal(translated.analysis.captions[0].end, before.captions[0].end);
    await page.getByRole('button', { name: 'Review translation', exact: true }).click();
    assert.match(await page.getByLabel('Cue 1 text').inputValue(), /Привет/);
    const [applied] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/commands')), page.locator('dialog').getByRole('button', { name: 'Apply', exact: true }).click()]); assert.equal(applied.status(), 200);
    const after = await snapshot(); assert.equal(after.revision, before.revision + 1); assert.equal(after.captions[0].id, before.captions[0].id); assert.match(after.captions[0].text, /Привет/); checks.push('human-reviewed translation apply retains cue IDs and times');
    await page.getByRole('button', { name: 'AI', exact: true }).click();
    await page.getByLabel('Material query', { exact: true }).fill('Привет');
    const search = await queued(() => page.getByRole('button', { name: 'Find moment', exact: true }).click());
    assert.equal(search.analysis.visual_understanding, false); assert.equal(search.analysis.matches[0].evidence, 'caption_text');
    await page.getByRole('button', { name: 'Go to result', exact: true }).first().click();
    assert.match(await page.locator('.vs-timecode').first().textContent(), /00:00:00:00/); checks.push('actual text-evidence search + explicit playhead navigation');
    const duplicate = await queued(() => page.getByRole('button', { name: 'Find duplicates', exact: true }).click());
    assert(duplicate.analysis.matches.some(m => m.kind === 'exact_content_sha256')); const preserved = await snapshot(); assert.equal(Object.keys(preserved.media).length, Object.keys(after.media).length); checks.push('actual duplicate evidence without deletion');
    await page.getByLabel('Material query', { exact: true }).fill('replacement');
    const broll = await queued(() => page.getByRole('button', { name: 'Suggest B-roll', exact: true }).click()); assert(broll.analysis.matches.length);
    const [added] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/commands')), page.locator('.vs-search-tools').getByRole('button', { name: 'Add to timeline', exact: true }).first().click()]); assert.equal(added.status(), 200); checks.push('existing-library B-roll candidates + explicit common-layer add');
    await page.screenshot({ path: '.audit-work/video-studio-ai-review.png', fullPage: true });
    assert.deepEqual(errors, []); console.log(JSON.stringify({ project_id: id, checks, translation_job: translated.job_id, revision: (await snapshot()).revision, errors }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
