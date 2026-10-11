import test from 'node:test';
import assert from 'node:assert/strict';

// V2 editor logic (frame stepping, natural hair family, clean variants + manifest, session recovery) must be
// importable in Node: no document, window or fetch at the top level.
assert.equal(typeof document, 'undefined');
const core = await import('../pages/genjutsu_core.js');
const {
  PRESETS, SWATCHES, presetSettings, defaultSettings,
  frameIndex, frameTime, stepFrame, NATURAL_HAIR_IDS,
  sha256Hex, cleanVariantNames, buildManifest, serializeSession, restoreSession,
} = core;

test('frame stepping: index <-> time round trip at 24, 25, 29.97 and 60 fps', () => {
  for (const fps of [24, 25, 30000 / 1001, 60]) {
    for (const i of [0, 1, 2, 47, 240]) {
      assert.equal(frameIndex(frameTime(i, fps), fps), i, `fps ${fps} frame ${i}`);
    }
  }
});

test('stepFrame moves exactly one frame and clamps inside the clip', () => {
  const fps = 25; const duration = 2; // 50 frames: 0..49
  const t0 = frameTime(10, fps);
  assert.equal(frameIndex(stepFrame(t0, +1, fps, duration), fps), 11);
  assert.equal(frameIndex(stepFrame(t0, -1, fps, duration), fps), 9);
  assert.equal(frameIndex(stepFrame(frameTime(0, fps), -1, fps, duration), fps), 0);
  assert.equal(frameIndex(stepFrame(frameTime(49, fps), +1, fps, duration), fps), 49);
  assert.equal(frameIndex(stepFrame(t0, +10, fps, duration), fps), 20);
});

test('natural hair family: six natural colours, each a hair-only preset using a real swatch', () => {
  assert.ok(Array.isArray(NATURAL_HAIR_IDS) && NATURAL_HAIR_IDS.length >= 6);
  const swatches = new Set(SWATCHES.hair.map((s) => s[0].toLowerCase()));
  for (const id of NATURAL_HAIR_IDS) {
    const p = PRESETS.find((x) => x.id === id);
    assert.ok(p, `preset ${id}`);
    const s = presetSettings(p);
    assert.equal(s.hair.on, true);
    assert.equal(s.top.on || s.bottom.on || s.clothes.on, false, 'a hair preset never touches clothes');
    assert.ok(swatches.has(s.hair.color.toLowerCase()), `${id} colour is a named swatch`);
  }
  assert.equal(new Set(PRESETS.map((p) => p.id)).size, PRESETS.length);
});

test('clean variants: distinct names, manifest ties clean and effect files by sha256', async () => {
  const names = cleanVariantNames('frame', 12);
  assert.notEqual(names.clean, names.effect);
  assert.match(names.clean, /clean/);
  assert.match(names.effect, /effect/);
  assert.match(names.manifest, /manifest\.json$/);
  const a = await sha256Hex(new Uint8Array([1, 2, 3]));
  assert.equal(a, '039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81');
  const settings = defaultSettings(); settings.hair.on = true;
  const m = buildManifest({
    kind: 'genjutsu-frame', source: { name: 'a.webm', frame_index: 12, time_s: 0.5, width: 64, height: 32 },
    settings, engine: 'segformer',
    files: [{ name: names.clean, role: 'clean', sha256: a, bytes: 3 }, { name: names.effect, role: 'effect', sha256: 'b'.repeat(64), bytes: 9 }],
  });
  assert.equal(m.version, 1);
  assert.equal(m.files.length, 2);
  assert.equal(m.files[0].role, 'clean');
  assert.equal(m.source.frame_index, 12);
  assert.equal(m.settings.hair.on, true);
  assert.equal(m.local_only, true);
  assert.throws(() => buildManifest({ kind: 'x', source: {}, settings, files: [{ name: 'a', role: 'clean', sha256: 'zz', bytes: 1 }] }), /sha256/);
});

test('session recovery: settings survive a reload; junk and unknown regions are rejected', () => {
  const s = defaultSettings(); s.hair = { on: true, color: '#4a2d1c', strength: 70, lightness: 40 };
  const raw = serializeSession({ settings: s, region: 'hair', trigger: 'ohwx_anna', subject: 'woman', frame: { name: 'a.webm', frame_index: 7 } });
  const back = restoreSession(raw);
  assert.deepEqual(back.settings.hair, s.hair);
  assert.equal(back.region, 'hair');
  assert.equal(back.frame.frame_index, 7);
  assert.equal(restoreSession('{not json'), null);
  assert.equal(restoreSession(null), null);
  const evil = JSON.stringify({ v: 1, settings: { hair: { on: true, color: 'javascript:1', strength: 9999, lightness: -5 }, evil: { on: true } }, region: 'evil' });
  const r = restoreSession(evil);
  assert.match(r.settings.hair.color, /^#[0-9a-f]{6}$/i);
  assert.ok(r.settings.hair.strength <= 100 && r.settings.hair.lightness >= 0);
  assert.equal(r.settings.evil, undefined);
  assert.equal(r.region, 'hair');
});
