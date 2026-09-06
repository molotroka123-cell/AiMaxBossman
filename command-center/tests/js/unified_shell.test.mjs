import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../ui/desktop.js', import.meta.url), 'utf8');
const { desktopPages, preferredTheme } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const css = await readFile(new URL('../../ui/desktop.css', import.meta.url), 'utf8');

// Contract checks, not live browser or product acceptance.
test('one dock composes missions, applications, editors and owner control', () => {
  const ids = ['home-v3', 'mission_console', 'apps', 'video-studio', 'web_designer', 'control'];
  const pages = ids.map(id => ({ id }));
  assert.deepEqual(desktopPages(pages, 'home-v3'), pages);
});
test('legacy video aliases are not advertised as a second editor', () => {
  const pages = ['video', 'video_studio', 'video-studio'].map(id => ({ id }));
  assert.deepEqual(desktopPages(pages, 'home-v3').map(p => p.id), ['video-studio']);
});
test('an editor as landing does not duplicate it or create unavailable pages', () => {
  const video = { id: 'video-studio' };
  assert.deepEqual(desktopPages([video], video.id), [video]);
  assert.deepEqual(desktopPages([], video.id), []);
});
test('cosmetic consolidation does not reset an existing dark theme', () => {
  assert.equal(preferredTheme('dark'), 'dark');
  assert.equal(preferredTheme('light'), 'light');
});
test('overflowing dock can scroll from its first launcher, with inset keyboard focus', () => {
  assert.match(css, /overflow-x:\s*auto/);
  assert.match(css, /justify-content:\s*flex-start/);
  assert.match(css, /overflow-wrap:\s*anywhere/);
  assert.match(css, /\.desktop-dock-item:focus-visible\s*\{\s*outline-offset:\s*-3px/);
});
test('accessibility preferences and asset independence survive cosmetic changes', () => {
  for (const preference of ['prefers-reduced-motion', 'prefers-reduced-transparency', 'forced-colors']) {
    assert.ok(css.includes(preference));
  }
  assert.doesNotMatch(css, /@import|https?:\/\//);
});
