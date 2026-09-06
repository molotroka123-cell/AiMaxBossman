import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Browser-native ES module; data import avoids changing the app's package setup.
const source = await readFile(new URL('../ui/pages/web_designer_viewport.js', import.meta.url), 'utf8');
const { VIEWPORT_PRESETS, viewportSettings, parseViewport, serializeViewport,
  viewportStorageKey, loadViewport, saveViewport, viewportGeometry } =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

class Storage {
  data = new Map();
  getItem(key) { return this.data.get(key) ?? null; }
  setItem(key, value) { this.data.set(key, value); }
}

test('real JSON round trip preserves dimensions, schema and numeric zoom', () => {
  for (const preset of VIEWPORT_PRESETS) {
    const original = viewportSettings(preset.width, preset.height, 0.75);
    assert.deepEqual(parseViewport(serializeViewport(original)), original);
    assert.equal(JSON.parse(serializeViewport(original)).schemaVersion, 1);
  }
  const storage = new Storage();
  const original = viewportSettings(1024, 768);
  assert.equal(saveViewport(storage, 1, original), true);
  assert.deepEqual(loadViewport(storage, 1), original);
  assert.deepEqual(loadViewport(storage, 2), viewportSettings());
  assert.notEqual(viewportStorageKey(1), viewportStorageKey(2));
});

test('future, partial, extra and corrupt schema never becomes accepted state', () => {
  const valid = viewportSettings();
  for (const input of [
    '', 'null', '[]', '{broken', 'x'.repeat(513),
    JSON.stringify({ ...valid, schemaVersion: 2 }),
    JSON.stringify({ ...valid, schemaVersion: '1' }),
    JSON.stringify({ ...valid, unexpected: true }),
    JSON.stringify({ width: 390, height: 844 }),
  ]) {
    assert.throws(() => parseViewport(input));
    const storage = new Storage();
    storage.setItem(viewportStorageKey(1), input);
    assert.deepEqual(loadViewport(storage, 1), viewportSettings());
    // Reading invalid settings must not overwrite a future schema or corrupt backup.
    assert.equal(storage.getItem(viewportStorageKey(1)), input);
  }
});

for (const value of [0, -1, 239, 4097, 390.5, NaN, Infinity, '390', true, null]) {
  test(`invalid dimension ${String(value)} leaves accepted storage intact`, () => {
    const storage = new Storage();
    saveViewport(storage, 1, viewportSettings());
    const before = storage.getItem(viewportStorageKey(1));
    for (const key of ['width', 'height']) {
      assert.throws(() => viewportSettings(key === 'width' ? value : 390, key === 'height' ? value : 844));
      assert.throws(() => saveViewport(storage, 1, { ...viewportSettings(), [key]: value }));
      assert.equal(storage.getItem(viewportStorageKey(1)), before);
    }
  });
}

test('invalid zoom and schema cannot rewrite accepted settings', () => {
  const storage = new Storage();
  saveViewport(storage, 1, viewportSettings());
  const before = storage.getItem(viewportStorageKey(1));
  for (const zoom of [0, -1, 3, NaN, Infinity, '100%', '1', true, null]) {
    assert.throws(() => viewportSettings(390, 844, zoom));
  }
  assert.throws(() => saveViewport(storage, 1, { ...viewportSettings(), schemaVersion: 2 }));
  assert.equal(storage.getItem(viewportStorageKey(1)), before);
});

test('storage errors do not break preview and do not claim persistence', () => {
  const storage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); } };
  for (const unavailable of [storage, null]) {
    assert.deepEqual(loadViewport(unavailable, 1), viewportSettings());
    assert.equal(saveViewport(unavailable, 1, viewportSettings()), false);
  }
  for (const id of [0, -1, '1', 1.2, Infinity, '../2', Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => viewportStorageKey(id));
  }
});

test('fit scales display only and keeps the real document viewport', () => {
  const result = viewportGeometry(viewportSettings(), 720, 600);
  assert.equal(result.width, 1440);
  assert.equal(result.height, 900);
  assert.equal(result.scale, 0.5);
  assert.equal(result.renderedWidth, 720);
  assert.equal(result.renderedHeight, 450);
  assert.equal(viewportGeometry(viewportSettings(390, 844), 1000, 422).scale, 0.5);
  assert.equal(viewportGeometry(viewportSettings(390, 844), 2000, 2000).scale, 1);
  assert.equal(viewportGeometry(viewportSettings(390, 844, 2), 200, 400).scale, 2);
});

test('rotation round trip is exact; invalid container geometry is rejected', () => {
  const initial = viewportSettings(390, 844, 0.75);
  const rotated = viewportSettings(initial.height, initial.width, initial.zoom);
  assert.deepEqual(viewportSettings(rotated.height, rotated.width, rotated.zoom), initial);
  for (const size of [0, -1, NaN, Infinity, '720']) {
    assert.throws(() => viewportGeometry(initial, size, 600));
    assert.throws(() => viewportGeometry(initial, 720, size));
  }
});
