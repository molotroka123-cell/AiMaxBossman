import test from 'node:test';
import assert from 'node:assert/strict';

// The constructor's logic must be importable in Node: no document, window or fetch at the top level.
assert.equal(typeof document, 'undefined');
const core = await import('../pages/genjutsu_core.js');
const {
  REGIONS, rgbToLab, labToRgb, imageToLab, prepare, recolor, createHistory, defaultSettings, presetSettings, PRESETS,
  captionFor, validateDataset, datasetName, makeZip, crc32, debounce, DEFAULT_SETTING,
} = core;

/* A 64x32 "fabric": grey stripes with folds (L varies by row and column). Left half = top, right = untouched. */
function fabric() {
  const w = 64; const h = 32; const rgba = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y += 1) for (let x = 0; x < w; x += 1) {
    const v = 90 + ((x * 7 + y * 13) % 60) + (y % 4 === 0 ? 30 : 0);
    const i = 4 * (y * w + x); rgba[i] = v; rgba[i + 1] = v; rgba[i + 2] = v; rgba[i + 3] = 255;
  }
  const top = new Uint8Array(w * h);
  for (let y = 0; y < h; y += 1) for (let x = 0; x < w / 2; x += 1) top[y * w + x] = 255;
  return { w, h, rgba, top };
}

const std = (xs) => { const m = xs.reduce((s, v) => s + v, 0) / xs.length; return Math.sqrt(xs.reduce((s, v) => s + (v - m) ** 2, 0) / xs.length); };

test('Lab round trip is within 1 level on every channel', () => {
  for (const [r, g, b] of [[0, 0, 0], [255, 255, 255], [200, 30, 40], [12, 140, 220], [128, 128, 128], [250, 220, 10]]) {
    const back = labToRgb(...rgbToLab(r, g, b));
    for (let k = 0; k < 3; k += 1) assert.ok(Math.abs(back[k] - [r, g, b][k]) <= 1, `${[r, g, b]} -> ${back}`);
  }
});

test('recolour keeps texture: lightness detail survives, hue moves, pixels outside the mask are untouched', () => {
  const { rgba, top } = fabric();
  const lab = imageToLab(rgba);
  const masks = { top };
  const prep = prepare(lab, masks);
  const settings = defaultSettings();
  settings.top = { on: true, color: '#b3262e', strength: 100, lightness: 0 };
  const out = new Uint8ClampedArray(rgba.length);
  const n = recolor({ src: rgba, lab, masks, prep, settings, out });
  assert.equal(n, top.filter(Boolean).length);
  const outLab = imageToLab(out);
  const Lin = []; const Lout = []; let redder = 0;
  for (let i = 0; i < top.length; i += 1) {
    if (top[i]) { Lin.push(lab[3 * i]); Lout.push(outLab[3 * i]); if (outLab[3 * i + 1] > 20) redder += 1; }
    else for (let k = 0; k < 4; k += 1) assert.equal(out[4 * i + k], rgba[4 * i + k]);
  }
  assert.ok(Math.abs(std(Lout) - std(Lin)) < 1.5, `folds flattened: ${std(Lin)} -> ${std(Lout)}`);
  assert.ok(redder / Lin.length > 0.95, 'the masked fabric did not turn red');
  // not flat paint: many distinct output colours remain
  const colours = new Set(); for (let i = 0; i < top.length; i += 1) if (top[i]) colours.add(`${out[4 * i]},${out[4 * i + 1]},${out[4 * i + 2]}`);
  assert.ok(colours.size > 20, `only ${colours.size} colours: flat fill`);
});

test('lightness slider moves the region mean toward the swatch, keeping the spread', () => {
  const { rgba, top } = fabric();
  const lab = imageToLab(rgba); const masks = { top }; const prep = prepare(lab, masks);
  const s = defaultSettings(); s.top = { on: true, color: '#ecebe6', strength: 100, lightness: 100 };
  const out = new Uint8ClampedArray(rgba.length);
  recolor({ src: rgba, lab, masks, prep, settings: s, out });
  const outLab = imageToLab(out);
  const L = []; for (let i = 0; i < top.length; i += 1) if (top[i]) L.push(outLab[3 * i]);
  const mean = L.reduce((a, b) => a + b, 0) / L.length;
  assert.ok(mean > prep.meanL.top + 15, `mean ${mean} vs ${prep.meanL.top}`);
});

test('a soft mask edge blends halfway; a specific zone beats "whole clothes"', () => {
  const rgba = new Uint8ClampedArray([100, 100, 100, 255, 100, 100, 100, 255]);
  const lab = imageToLab(rgba);
  const masks = { clothes: new Uint8Array([255, 255]), top: new Uint8Array([255, 0]), hair: new Uint8Array([0, 0]) };
  const prep = prepare(lab, masks);
  const s = defaultSettings();
  s.clothes = { on: true, color: '#1f3354', strength: 100, lightness: 0 };
  s.top = { on: true, color: '#b3262e', strength: 100, lightness: 0 };
  const out = new Uint8ClampedArray(8);
  recolor({ src: rgba, lab, masks, prep, settings: s, out });
  assert.ok(out[0] > out[2], 'pixel 0 is in top: red wins over navy');
  assert.ok(out[6] > out[4], 'pixel 1 is clothes only: navy (blue) wins');
  const half = { top: new Uint8Array([128]) };
  const one = new Uint8ClampedArray([100, 100, 100, 255]); const l1 = imageToLab(one);
  const full = new Uint8ClampedArray(4); const soft = new Uint8ClampedArray(4);
  recolor({ src: one, lab: l1, masks: { top: new Uint8Array([255]) }, prep: prepare(l1, { top: new Uint8Array([255]) }), settings: s, out: full });
  recolor({ src: one, lab: l1, masks: half, prep: prepare(l1, half), settings: s, out: soft });
  assert.ok(Math.abs(soft[0] - (one[0] + full[0]) / 2) <= 2, `${soft[0]} not halfway between ${one[0]} and ${full[0]}`);
});

test('nothing on -> output equals the original', () => {
  const { rgba, top } = fabric(); const lab = imageToLab(rgba);
  const out = new Uint8ClampedArray(rgba.length);
  assert.equal(recolor({ src: rgba, lab, masks: { top }, prep: prepare(lab, { top }), settings: defaultSettings(), out }), 0);
  assert.deepEqual(out, rgba);
});

test('undo / redo / reset are per region', () => {
  const hst = createHistory();
  hst.set('hair', { on: true, color: '#c46e3d' });
  hst.set('top', { on: true, color: '#1f3354' });
  hst.set('hair', { strength: 40 });
  assert.equal(hst.undo('hair'), true);
  assert.equal(hst.get('hair').strength, DEFAULT_SETTING.strength);
  assert.equal(hst.get('top').color, '#1f3354', 'undo of hair must not touch top');
  assert.equal(hst.redo('hair'), true);
  assert.equal(hst.get('hair').strength, 40);
  assert.equal(hst.redo('hair'), false);
  hst.reset('top');
  assert.equal(hst.get('top').on, false);
  assert.equal(hst.undo('top'), true, 'reset is undoable');
  assert.equal(hst.get('top').on, true);
  // dragging a slider = one step
  const before = hst.canUndo('bottom');
  hst.set('bottom', { strength: 10 }); hst.replace('bottom', { strength: 20 }); hst.replace('bottom', { strength: 30 });
  assert.equal(before, false);
  assert.equal(hst.undo('bottom'), true);
  assert.equal(hst.canUndo('bottom'), false);
  assert.equal(hst.set('bottom', hst.get('bottom')), false, 'no-op edits add no steps');
});

test('presets switch off zones they do not list; every preset is valid', () => {
  for (const p of PRESETS) {
    const s = presetSettings(p);
    assert.deepEqual(Object.keys(s).sort(), [...REGIONS].sort());
    for (const r of REGIONS) assert.equal(s[r].on, Boolean(p.regions[r] && p.regions[r].on), `${p.id}.${r}`);
  }
  const hst = createHistory();
  hst.set('hair', { on: true });
  hst.setAll(presetSettings(PRESETS.find((p) => p.id === 'office')));
  assert.equal(hst.get('hair').on, false);
  assert.equal(hst.undo('hair'), true);
  assert.equal(hst.get('hair').on, true, 'a preset is undoable per zone');
});

test('LoRA captions carry the trigger and English colour names; validation is honest', () => {
  const s = defaultSettings();
  s.hair = { on: true, color: '#c46e3d', strength: 80, lightness: 50 };
  s.top = { on: true, color: '#1f3354', strength: 80, lightness: 50 };
  assert.equal(captionFor('ohwx_anna', s, 'woman'), 'ohwx_anna, woman, copper hair, navy top');
  assert.equal(datasetName(0, 'ohwx_anna'), 'ohwx_anna_001');
  assert.deepEqual(validateDataset('ohwx_anna', [{ caption: 'ohwx_anna, woman' }]), []);
  assert.equal(validateDataset('Bad Trigger', [{ caption: 'x' }]).length, 2);
  assert.equal(validateDataset('ohwx', []).length, 1);
});

test('ZIP: CRC-32 check value, signatures, entry count', () => {
  assert.equal(crc32(new TextEncoder().encode('123456789')), 0xcbf43926);
  const zip = makeZip([{ name: 'a_001.png', data: new Uint8Array([1, 2, 3]) }, { name: 'a_001.txt', data: 'a, woman' }]);
  const dv = new DataView(zip.buffer);
  assert.equal(dv.getUint32(0, true), 0x04034b50);
  const end = zip.length - 22;
  assert.equal(dv.getUint32(end, true), 0x06054b50);
  assert.equal(dv.getUint16(end + 10, true), 2);
});

test('debounce runs once with the last arguments; flush runs now', async () => {
  const calls = [];
  const d = debounce((v) => calls.push(v), 10);
  d(1); d(2); d(3);
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(calls, [3]);
  d(4); d.flush(5);
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(calls, [3, 5]);
});

test('live speed: 1320x1108 frame with ~45% masked recolours fast enough for a slider', () => {
  const w = 1320; const h = 1108; const n = w * h;
  const rgba = new Uint8ClampedArray(n * 4);
  for (let i = 0; i < n; i += 1) { rgba[4 * i] = i % 251; rgba[4 * i + 1] = (i >> 3) % 241; rgba[4 * i + 2] = (i >> 5) % 239; rgba[4 * i + 3] = 255; }
  const hair = new Uint8Array(n); const top = new Uint8Array(n);
  for (let i = 0; i < n; i += 1) { if (i % 5 === 0) hair[i] = 255; if (i % 4 === 1) top[i] = 200; }
  const lab = imageToLab(rgba); const masks = { hair, top }; const prep = prepare(lab, masks);
  const s = defaultSettings(); s.hair.on = true; s.top.on = true;
  const out = new Uint8ClampedArray(rgba.length);
  const t0 = performance.now();
  recolor({ src: rgba, lab, masks, prep, settings: s, out });
  const ms = performance.now() - t0;
  console.log(`recolour 1320x1108, ${prep.active.length} px in zones: ${ms.toFixed(0)} ms`);
  assert.ok(ms < 1500, `${ms} ms`);
});

test('presets: hair colours blue/green/white/black and outfit presets recolour only their zones', () => {
  const need = ['hair-blue', 'hair-green', 'hair-white', 'hair-black', 'uniform', 'denim', 'emerald-night'];
  for (const id of need) assert.ok(PRESETS.find((p) => p.id === id), id);
  const hairOnly = presetSettings(PRESETS.find((p) => p.id === 'hair-blue'));
  assert.equal(hairOnly.hair.on, true);
  assert.equal(hairOnly.top.on, false);          // a hair preset never touches the clothes
  const uni = presetSettings(PRESETS.find((p) => p.id === 'uniform'));
  assert.equal(uni.top.on, true);
  assert.equal(uni.hair.on, false);              // the outfit preset never touches the hair
  assert.equal(new Set(PRESETS.map((p) => p.id)).size, PRESETS.length);   // unique ids
});
