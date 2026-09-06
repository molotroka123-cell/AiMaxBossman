import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Load browser ESM without changing the project's package/module conventions.
const source = await readFile(new URL('../../ui/desktop.js', import.meta.url), 'utf8');
const { desktopPages, preferredTheme } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('launchers preserve registered routes and do not promise missing applications', () => {
  const pages = [{ id: 'home-v3', title: 'Home' }, { id: 'web_designer', title: 'Web' },
    { id: 'control', title: 'Control' }, { id: 'private-debug' }];
  const result = desktopPages(pages, 'home-v3');
  assert.deepEqual(result.map(p => p.id), ['home-v3', 'web_designer', 'control']);
  assert.equal(result[1], pages[1]);
});
test('fallback landing and shared routes appear once', () => {
  assert.deepEqual(desktopPages([{ id: 'apps' }], 'apps'), [{ id: 'apps' }]);
  assert.deepEqual(desktopPages([], 'home-v3'), []);
});
test('existing dark preference survives while new and corrupt preferences use light', () => {
  assert.equal(preferredTheme('dark'), 'dark');
  for (const saved of ['light', null, undefined, '', 'invalid']) {
    assert.equal(preferredTheme(saved), 'light');
  }
});

test('Video Studio becomes launchable only when the integrated registry provides it', () => {
  const video = { id: 'video-studio', title: 'Video Studio', icon: 'film' };
  assert.deepEqual(desktopPages([video], 'home-v3'), [video]);
});
