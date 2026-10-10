/* ============================================================
   genjutsu_core.js — чистая логика живого конструктора Genjutsu (без DOM, импортируется и в Node-тестах).

   Перекраска с сохранением фактуры: пиксели переводятся в CIE Lab один раз; при движении слайдера меняются
   только цветовые оси a/b (к цвету образца) и яркость L сдвигается целиком на (L образца − средняя L зоны),
   поэтому складки, швы, пряди и логотип (перепады L внутри зоны) остаются. Мягкая маска (0..255) смешивает
   результат с оригиналом — края волос не «вырезаны».
   Частные зоны важнее общей: «Верх»/«Низ» перекрывают «Одежду целиком» там, где они включены.
   ============================================================ */

export const REGIONS = ['hair', 'top', 'bottom', 'clothes'];
export const REGION_LABEL = { hair: 'Волосы', top: 'Верх', bottom: 'Низ', clothes: 'Одежда целиком' };
export const DEFAULT_SETTING = Object.freeze({ on: false, color: '#7a4b2a', strength: 80, lightness: 50 });

/* Палитра образцов: подпись в интерфейсе по-русски, имя для подписи датасета — по-английски (LoRA-модели
   обучены на английских подписях). */
export const SWATCHES = {
  hair: [
    ['#1f1714', 'Иссиня-чёрный', 'jet black'], ['#4a2d1c', 'Шоколад', 'chocolate brown'], ['#8a4526', 'Каштан', 'auburn'],
    ['#c46e3d', 'Медный', 'copper'], ['#d6c29a', 'Холодный блонд', 'ash blonde'], ['#ebe6da', 'Платиновый', 'platinum blonde'],
    ['#b0306a', 'Малиновый', 'magenta'], ['#3f62b8', 'Синий', 'blue'],
  ],
  clothes: [
    ['#15171b', 'Чёрный', 'black'], ['#ecebe6', 'Белый', 'white'], ['#7d8083', 'Серый', 'grey'], ['#1f3354', 'Тёмно-синий', 'navy'],
    ['#823b4c', 'Бордовый', 'burgundy'], ['#b3262e', 'Красный', 'red'], ['#667052', 'Оливковый', 'olive'],
    ['#b39c7e', 'Бежевый', 'beige'], ['#2f6b4a', 'Изумрудный', 'emerald green'], ['#e3b23c', 'Горчичный', 'mustard'],
  ],
};

/* Готовые образы (палитры): перечислены зоны, которые образ перекрашивает; остальные он выключает. */
export const PRESETS = [
  { id: 'natural', label: 'Как в оригинале', note: 'все зоны выключены', regions: {} },
  { id: 'office', label: 'Офис', note: 'тёмно-синий верх · серый низ', regions: {
    top: { on: true, color: '#1f3354', strength: 85, lightness: 60 }, bottom: { on: true, color: '#7d8083', strength: 80, lightness: 50 } } },
  { id: 'night', label: 'Вечер', note: 'чёрное · бордовые акценты', regions: {
    clothes: { on: true, color: '#15171b', strength: 70, lightness: 70 }, top: { on: true, color: '#823b4c', strength: 85, lightness: 45 } } },
  { id: 'copper', label: 'Медь и олива', note: 'медные волосы · оливковый верх', regions: {
    hair: { on: true, color: '#c46e3d', strength: 75, lightness: 35 }, top: { on: true, color: '#667052', strength: 80, lightness: 45 } } },
  { id: 'platinum', label: 'Платина', note: 'платиновые волосы · белый верх', regions: {
    hair: { on: true, color: '#ebe6da', strength: 70, lightness: 55 }, top: { on: true, color: '#ecebe6', strength: 60, lightness: 70 } } },
];

export function defaultSettings() {
  const out = {};
  for (const r of REGIONS) out[r] = { ...DEFAULT_SETTING };
  return out;
}

/* Пресет задаёт весь образ: зоны, которых в нём нет, выключаются. */
export function presetSettings(preset) {
  const out = defaultSettings();
  for (const [r, v] of Object.entries((preset && preset.regions) || {})) if (out[r]) out[r] = { ...DEFAULT_SETTING, ...v };
  return out;
}

/* ---------------------------------------------------------------- история по зонам */

/* Отмена / повтор / сброс отдельно для каждой зоны: отмена цвета волос не трогает верх. */
export function createHistory(initial = defaultSettings(), limit = 100) {
  const state = {};
  for (const r of REGIONS) state[r] = { past: [], now: { ...initial[r] }, future: [] };
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  return {
    get(region) { return { ...state[region].now }; },
    all() { const o = {}; for (const r of REGIONS) o[r] = { ...state[r].now }; return o; },
    set(region, value) {
      const s = state[region];
      const next = { ...s.now, ...value };
      if (same(next, s.now)) return false;
      s.past.push(s.now); if (s.past.length > limit) s.past.shift();
      s.now = next; s.future = [];
      return true;
    },
    /* Слайдер тянут — правка одна: пока зона «в работе», шаги не множатся. */
    replace(region, value) { state[region].now = { ...state[region].now, ...value }; },
    undo(region) { const s = state[region]; if (!s.past.length) return false; s.future.push(s.now); s.now = s.past.pop(); return true; },
    redo(region) { const s = state[region]; if (!s.future.length) return false; s.past.push(s.now); s.now = s.future.pop(); return true; },
    reset(region) { return this.set(region, { ...DEFAULT_SETTING }); },
    canUndo(region) { return state[region].past.length > 0; },
    canRedo(region) { return state[region].future.length > 0; },
    setAll(values) { let changed = false; for (const r of REGIONS) changed = this.set(r, values[r] || DEFAULT_SETTING) || changed; return changed; },
  };
}

/* ---------------------------------------------------------------- цвет */

const TO_LIN = new Float32Array(256);
for (let i = 0; i < 256; i += 1) { const c = i / 255; TO_LIN[i] = c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }
const LUT_N = 4096;
const TO_SRGB = new Uint8ClampedArray(LUT_N + 1);
for (let i = 0; i <= LUT_N; i += 1) { const c = i / LUT_N; TO_SRGB[i] = Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)); }
const XN = 0.95047; const ZN = 1.08883;
const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
const fi = (t) => (t > 0.206893 ? t * t * t : (t - 16 / 116) / 7.787);

export function rgbToLab(r, g, b) {
  const R = TO_LIN[r]; const G = TO_LIN[g]; const B = TO_LIN[b];
  const x = f((0.4124 * R + 0.3576 * G + 0.1805 * B) / XN);
  const y = f(0.2126 * R + 0.7152 * G + 0.0722 * B);
  const z = f((0.0193 * R + 0.1192 * G + 0.9505 * B) / ZN);
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
}

const enc = (v) => TO_SRGB[v <= 0 ? 0 : v >= 1 ? LUT_N : Math.round(v * LUT_N)];
export function labToRgb(L, a, b) {
  const y = (L + 16) / 116; const x = a / 500 + y; const z = y - b / 200;
  const X = XN * fi(x); const Y = fi(y); const Z = ZN * fi(z);
  return [enc(3.2406 * X - 1.5372 * Y - 0.4986 * Z), enc(-0.9689 * X + 1.8758 * Y + 0.0415 * Z), enc(0.0557 * X - 0.2040 * Y + 1.0570 * Z)];
}

export function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) throw new Error(`bad colour ${hex}`);
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/* Lab всего кадра — один раз на кадр (дальше перекраска читает готовые числа). */
export function imageToLab(rgba) {
  const n = rgba.length >> 2;
  const lab = new Float32Array(n * 3);
  for (let i = 0; i < n; i += 1) {
    const [L, A, B] = rgbToLab(rgba[4 * i], rgba[4 * i + 1], rgba[4 * i + 2]);
    lab[3 * i] = L; lab[3 * i + 1] = A; lab[3 * i + 2] = B;
  }
  return lab;
}

/* Средняя L каждой зоны (взвешенная мягкой маской) и список пикселей, где есть хоть одна зона. */
export function prepare(lab, masks) {
  const n = lab.length / 3;
  const meanL = {};
  for (const r of REGIONS) {
    const m = masks[r];
    let s = 0; let w = 0;
    if (m) for (let i = 0; i < n; i += 1) { const a = m[i]; if (a) { s += a * lab[3 * i]; w += a; } }
    meanL[r] = w ? s / w : 50;
  }
  const idx = [];
  for (let i = 0; i < n; i += 1) if (REGIONS.some((r) => masks[r] && masks[r][i])) idx.push(i);
  return { meanL, active: Uint32Array.from(idx) };
}

/* Перекрасить `out` (RGBA, уже содержит оригинал) по настройкам. Возвращает число изменённых пикселей. */
export function recolor({ src, lab, masks, prep, settings, out }) {
  const on = REGIONS.filter((r) => settings[r] && settings[r].on && masks[r]);
  if (out !== src) out.set(src);
  if (!on.length) return 0;
  const par = {};
  for (const r of on) {
    const s = settings[r];
    const [tl, ta, tb] = rgbToLab(...hexToRgb(s.color));
    par[r] = { ta, tb, k: Math.max(0, Math.min(100, s.strength)) / 100, dL: (tl - prep.meanL[r]) * Math.max(0, Math.min(100, s.lightness)) / 100 };
  }
  const topOn = on.includes('top'); const botOn = on.includes('bottom');
  let changed = 0;
  for (const i of prep.active) {
    const L = lab[3 * i]; const A = lab[3 * i + 1]; const B = lab[3 * i + 2];
    let R = src[4 * i]; let G = src[4 * i + 1]; let Bl = src[4 * i + 2];
    let touched = false;
    for (const r of on) {
      let w = masks[r][i] / 255;
      if (r === 'clothes') {                     // частная зона важнее общей
        if (topOn && masks.top) w *= 1 - masks.top[i] / 255;
        if (botOn && masks.bottom) w *= 1 - masks.bottom[i] / 255;
      }
      if (w <= 0.002) continue;
      const p = par[r];
      const nl = Math.max(0, Math.min(100, L + p.dL));
      const [nr, ng, nb] = labToRgb(nl, A + (p.ta - A) * p.k, B + (p.tb - B) * p.k);
      R += (nr - R) * w; G += (ng - G) * w; Bl += (nb - Bl) * w;
      touched = true;
    }
    if (touched) { out[4 * i] = R; out[4 * i + 1] = G; out[4 * i + 2] = Bl; changed += 1; }
  }
  return changed;
}

/* ---------------------------------------------------------------- прочее */

export function debounce(fn, ms) {
  let t = null;
  const d = (...args) => { if (t) clearTimeout(t); t = setTimeout(() => { t = null; fn(...args); }, ms); };
  d.flush = (...args) => { if (t) clearTimeout(t); t = null; fn(...args); };
  d.cancel = () => { if (t) clearTimeout(t); t = null; };
  return d;
}

export function nearestName(hex, list) {
  const [r, g, b] = hexToRgb(hex);
  const [L, A, B] = rgbToLab(r, g, b);
  let best = null; let bd = Infinity;
  for (const item of list) {
    const [l2, a2, b2] = rgbToLab(...hexToRgb(item[0]));
    const d = (L - l2) ** 2 + (A - a2) ** 2 + (B - b2) ** 2;
    if (d < bd) { bd = d; best = item; }
  }
  return best;
}

/* ---------------------------------------------------------------- LoRA-датасет */

export const TRIGGER_RE = /^[a-z][a-z0-9_]{2,31}$/;

/* Черновик подписи из конструктора: триггер + описание изменённых зон (по-английски). Человек правит текст. */
export function captionFor(trigger, settings, subject = 'person') {
  const parts = [trigger, subject];
  const h = settings.hair; const t = settings.top; const b = settings.bottom; const c = settings.clothes;
  if (h && h.on) parts.push(`${nearestName(h.color, SWATCHES.hair)[2]} hair`);
  if (t && t.on) parts.push(`${nearestName(t.color, SWATCHES.clothes)[2]} top`);
  if (b && b.on) parts.push(`${nearestName(b.color, SWATCHES.clothes)[2]} bottoms`);
  if (c && c.on && !(t && t.on) && !(b && b.on)) parts.push(`${nearestName(c.color, SWATCHES.clothes)[2]} outfit`);
  return parts.join(', ');
}

/* Имя файла в архиве: только безопасные символы, номер по порядку. */
export function datasetName(index, trigger) {
  return `${trigger}_${String(index + 1).padStart(3, '0')}`;
}

export function validateDataset(trigger, items) {
  const problems = [];
  if (!TRIGGER_RE.test(trigger || '')) problems.push('Триггер: 3–32 символа, латиница в нижнем регистре, цифры или _, начинается с буквы.');
  if (!items.length) problems.push('Добавьте хотя бы один кадр в набор.');
  items.forEach((it, i) => {
    if (!String(it.caption || '').trim()) problems.push(`Кадр ${i + 1}: пустая подпись.`);
    else if (!String(it.caption).includes(trigger)) problems.push(`Кадр ${i + 1}: в подписи нет триггера «${trigger}».`);
  });
  return problems;
}

/* ---------------------------------------------------------------- ZIP (без сжатия, без внешних библиотек) */

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) { let c = n; for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();

export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/* files: [{ name, data: Uint8Array | string }] -> Uint8Array (ZIP, метод STORE, имена UTF-8). */
export function makeZip(files, when = new Date(2026, 0, 1)) {
  const te = new TextEncoder();
  const time = ((when.getHours() << 11) | (when.getMinutes() << 5) | (when.getSeconds() >> 1)) & 0xffff;
  const date = (((when.getFullYear() - 1980) << 9) | ((when.getMonth() + 1) << 5) | when.getDate()) & 0xffff;
  const locals = []; const centrals = []; let offset = 0;
  for (const file of files) {
    const name = te.encode(file.name);
    const data = typeof file.data === 'string' ? te.encode(file.data) : file.data;
    const crc = crc32(data);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true); lh.setUint16(8, 0, true);
    lh.setUint16(10, time, true); lh.setUint16(12, date, true); lh.setUint32(14, crc, true);
    lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true); lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true); ch.setUint16(8, 0x0800, true);
    ch.setUint16(10, 0, true); ch.setUint16(12, time, true); ch.setUint16(14, date, true); ch.setUint32(16, crc, true);
    ch.setUint32(20, data.length, true); ch.setUint32(24, data.length, true); ch.setUint16(28, name.length, true);
    ch.setUint32(42, offset, true);
    locals.push(new Uint8Array(lh.buffer), name, data);
    centrals.push(new Uint8Array(ch.buffer), name);
    offset += 30 + name.length + data.length;
  }
  const cdSize = centrals.reduce((s, p) => s + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  const parts = [...locals, ...centrals, new Uint8Array(end.buffer)];
  const out = new Uint8Array(parts.reduce((s, p) => s + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}
