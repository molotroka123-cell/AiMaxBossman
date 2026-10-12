/* ============================================================
   genjutsu.js — «Genjutsu: живой конструктор образа» на странице «Прямой генератор».
   Endpoints (bcc/features/direct_gen.py, под /api):
     GET  /direct-gen/genjutsu          движки масок (доступность, лицензия), статус обучения LoRA
     POST /direct-gen/genjutsu/masks    маски зон для одного кадра (нужно согласие); ничего не сохраняется
   Маски считаются на сервере ОДИН раз на кадр; перекраска — здесь, в браузере (canvas, Lab), с задержкой
   60 мс на слайдерах: это и есть «живой» предпросмотр, без запроса на каждое движение.
   Честно: это перекраска по маскам, не генерация — фасон одежды не меняется. Обучение LoRA не подключено.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError } from '../components.js';
import { panel, pill, btn } from './_ui.js';
import {
  REGIONS, REGION_LABEL, SWATCHES, PRESETS, createHistory, presetSettings, imageToLab, prepare, recolor,
  debounce, captionFor, datasetName, validateDataset, makeZip,
  frameIndex, frameTime, stepFrame, frameCount, cleanVariantNames, sha256Hex, buildManifest, serializeSession, restoreSession,
} from './genjutsu_core.js';

const SESSION_KEY = 'bx.genjutsu.session.v1';
const DEFAULT_FPS = 30;

const base = '/api/direct-gen';
const MAX_SIDE = 4096;
const LIVE_MS = 60;

const ERR = {
  consent_required: 'Нужно согласие человека в кадре: поставьте галочку.',
  model_unavailable: 'Движок масок недоступен: причина ниже.',
  image_invalid: 'Кадр не принят: нужен PNG или JPEG со сторонами 16–4096 px.',
  image_required: 'Сначала выберите фото или кадр видео.',
  engine_error: 'Модель масок завершилась с ошибкой: текст ниже без правок.',
  bad_output: 'Модель масок вернула неполный результат.',
  gpu_busy: 'GPU занят другой задачей, а этот движок без GPU не работает. Выберите SegFormer или дождитесь конца задачи.',
  invalid_engine: 'Неизвестный движок масок.',
};

const pct = (v) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—');

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Изображение не открылось'));
    img.src = src;
  });
}

function canvasBlob(canvas, type = 'image/png') {
  return new Promise((resolve) => canvas.toBlob(resolve, type));
}

function download(blob, name) {
  const href = URL.createObjectURL(blob);
  const a = h('a', { href, download: name, hidden: true });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 4000);
}

export function genjutsuPanel() {
  /* ---- состояние ---- */
  const S = {
    info: null, engine: null, w: 0, h: 0, src: null, lab: null, masks: null, prep: null, out: null,
    region: 'hair', result: null, dataset: [], fileUrl: null, editing: {}, fileName: '', fps: DEFAULT_FPS, lastExport: null,
  };
  const history = createHistory();

  /* ---- источник ---- */
  const fileInput = h('input.dg-file', { id: 'gj-file', name: 'gj-file', type: 'file', accept: 'image/png,image/jpeg,video/*' });
  const video = h('video.gj-video', { controls: true, playsinline: true, muted: true, preload: 'metadata', hidden: true, 'aria-label': 'Исходное видео' });
  const grabBtn = btn('Взять этот кадр', () => grabFrame(), { variant: 'secondary', iconName: 'plus' });
  grabBtn.hidden = true; grabBtn.dataset.testid = 'gj-grab';
  const fpsInput = h('input.dg-input', { id: 'gj-fps', name: 'gj-fps', type: 'number', min: '1', max: '240', step: '0.001', value: String(DEFAULT_FPS), 'aria-label': 'Кадров в секунду' });
  const frameInput = h('input.dg-input', { id: 'gj-frame-no', name: 'gj-frame-no', type: 'number', min: '0', step: '1', value: '0', 'aria-label': 'Номер кадра' });
  const frameInfo = h('div.dg-small.dg-num', { dataset: { testid: 'gj-frame-info' }, 'aria-live': 'polite' });
  const stepBack = btn('−1 кадр', () => stepBy(-1), { variant: 'secondary', size: 'sm' });
  const stepFwd = btn('+1 кадр', () => stepBy(+1), { variant: 'secondary', size: 'sm' });
  const gotoBtn = btn('Перейти', () => gotoFrame(Number(frameInput.value)), { variant: 'secondary', size: 'sm' });
  stepBack.dataset.testid = 'gj-step-back'; stepFwd.dataset.testid = 'gj-step-fwd'; gotoBtn.dataset.testid = 'gj-goto';
  const stepRow = h('div.dg-row', { hidden: true, dataset: { testid: 'gj-step-row' } },
    stepBack, stepFwd,
    h('label.dg-label.gj-inline', { for: 'gj-frame-no' }, h('span', 'Кадр №'), frameInput), gotoBtn,
    h('label.dg-label.gj-inline', { for: 'gj-fps' }, h('span', 'к/с'), fpsInput), frameInfo);
  const engineSel = h('select.dg-input', { id: 'gj-engine', name: 'gj-engine' });
  const engineNote = h('div.dg-note', { dataset: { testid: 'gj-license' } });
  const consent = h('input', { type: 'checkbox', id: 'gj-consent', name: 'gj-consent' });
  const maskErr = h('div', { dataset: { testid: 'gj-error' } });
  const maskInfo = h('div.dg-small.dg-num', { dataset: { testid: 'gj-mask-info' }, 'aria-live': 'polite' });
  const maskBtn = btn('Найти зоны', () => findMasks(), { variant: 'primary', iconName: 'bolt', disabled: true,
    title: 'Нужны: фото или кадр видео, согласие людей в кадре и доступный движок сегментации.' });
  maskBtn.dataset.testid = 'gj-masks';

  /* ---- сцена: до / после ---- */
  const before = h('canvas.gj-canvas', { 'aria-label': 'Оригинал' });
  const after = h('canvas.gj-canvas.gj-after', { 'aria-label': 'Результат перекраски' });
  const overlay = h('canvas.gj-canvas.gj-overlay', { 'aria-hidden': 'true' });
  const splitLine = h('div.gj-split-line', { 'aria-hidden': 'true' });
  const stage = h('div.gj-stage', { dataset: { testid: 'gj-stage' } }, before, after, overlay, splitLine);
  const stageEmpty = h('div.dg-stage-ph', 'Выберите фото или видео, возьмите кадр и нажмите «Найти зоны».');
  const stageBox = h('div.gj-stage-box', stageEmpty);
  const split = h('input.gj-range', { type: 'range', min: '0', max: '100', value: '50', id: 'gj-split', name: 'gj-split', 'aria-label': 'Граница до / после' });
  const showMask = h('input', { type: 'checkbox', id: 'gj-show-mask', name: 'gj-show-mask' });
  const renderInfo = h('div.dg-small.dg-dim.dg-num', { 'aria-live': 'off' });

  /* ---- зоны ---- */
  const tabs = h('div.gj-tabs', { role: 'tablist', 'aria-label': 'Зона' });
  const regionBody = h('div.dg-stack', { role: 'tabpanel', id: 'gj-region-panel' });
  const presetBox = h('div.gj-presets', { role: 'group', 'aria-label': 'Готовые образы' });

  /* ---- LoRA ---- */
  const trigger = h('input.dg-input', { id: 'gj-trigger', name: 'gj-trigger', placeholder: 'например: ohwx_anna', autocomplete: 'off', spellcheck: 'false' });
  const subject = h('select.dg-input', { id: 'gj-subject', name: 'gj-subject' },
    h('option', { value: 'woman' }, 'woman (женщина)'), h('option', { value: 'man' }, 'man (мужчина)'), h('option', { value: 'person' }, 'person (человек)'));
  const dsList = h('ol.gj-ds', { dataset: { testid: 'gj-dataset' } });
  const dsErr = h('div');
  const trainPill = h('span');
  const trainNote = h('div.dg-note');

  const setEnabled = () => {
    maskBtn.disabled = !(S.src && consent.checked && S.engine);
    // A disabled button says what is missing (UI sweep: disabled_silent).
    maskBtn.title = !S.src ? 'Сначала выберите фото или возьмите кадр из видео.'
      : !consent.checked ? 'Отметьте согласие людей в кадре.'
      : !S.engine ? 'Движок сегментации недоступен на этом ПК.' : '';
  };

  /* ---------------------------------------------------------------- кадр */

  function setFrame(drawable, w, h2) {
    let W = w; let H = h2;
    const s = Math.min(1, MAX_SIDE / Math.max(W, H));
    W = Math.round(W * s); H = Math.round(H * s);
    for (const c of [before, after, overlay]) { c.width = W; c.height = H; }
    const ctx = before.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(drawable, 0, 0, W, H);
    S.w = W; S.h = H;
    S.src = ctx.getImageData(0, 0, W, H);
    S.lab = null; S.masks = null; S.prep = null;
    S.out = new ImageData(new Uint8ClampedArray(S.src.data), W, H);
    after.getContext('2d').putImageData(S.out, 0, 0);
    overlay.getContext('2d').clearRect(0, 0, W, H);
    if (stageBox.firstChild !== stage) stageBox.replaceChildren(stage);
    maskInfo.textContent = `Кадр ${W}×${H}. Зоны ещё не найдены.`;
    renderRegion(); setEnabled();
  }

  async function onFile() {
    const f = fileInput.files[0];
    maskErr.replaceChildren();
    if (S.fileUrl) { URL.revokeObjectURL(S.fileUrl); S.fileUrl = null; }
    if (!f) return;
    S.fileUrl = URL.createObjectURL(f);
    S.fileName = f.name;
    if (f.type.startsWith('video/')) {
      video.src = S.fileUrl; video.hidden = false; grabBtn.hidden = false; stepRow.hidden = false;
      S.src = null; setEnabled();
      // Seek to the centre of frame 0 instead of grabbing at an unseeked t=0: a video that was never presented
      // draws as an all-black canvas in some Chromium builds, and the mask pass would then find nothing.
      video.addEventListener('loadeddata', () => seekAndGrab(frameTime(0, fpsNow())), { once: true });
      return;
    }
    video.hidden = true; grabBtn.hidden = true; stepRow.hidden = true; video.removeAttribute('src');
    try { const img = await loadImage(S.fileUrl); setFrame(img, img.naturalWidth, img.naturalHeight); }
    catch (e) { toastError(e); }
  }

  function grabFrame() {
    if (!video.videoWidth) { toastError(new Error('Видео ещё не загрузилось')); return; }
    setFrame(video, video.videoWidth, video.videoHeight);   // setFrame drops the masks: they belong to the old frame
    showFrame();
    persist();
    toastOk('Кадр взят', `${video.currentTime.toFixed(2)} с`);
  }

  /* ---- покадровый переход: один кадр = один шаг, после перехода берётся именно этот кадр ---- */
  const fpsNow = () => { const v = Number(fpsInput.value); S.fps = v > 0 ? v : DEFAULT_FPS; return S.fps; };

  function showFrame() {
    const fps = fpsNow();
    const idx = frameIndex(video.currentTime || 0, fps);
    frameInput.value = String(idx);
    const total = Number.isFinite(video.duration) ? frameCount(video.duration, fps) : null;
    frameInfo.textContent = `Кадр ${idx}${total ? ` из ${total}` : ''} · ${(video.currentTime || 0).toFixed(3)} с · ${fps} к/с`;
  }

  function seekAndGrab(t) {
    return new Promise((resolve) => {
      const done = () => { video.removeEventListener('seeked', done); grabFrame(); resolve(); };
      video.addEventListener('seeked', done);
      video.pause();
      video.currentTime = t;
    });
  }

  async function stepBy(delta) {
    if (!video.videoWidth) { toastError(new Error('Видео ещё не загрузилось')); return; }
    await seekAndGrab(stepFrame(video.currentTime, delta, fpsNow(), video.duration || 1e9));
  }

  async function gotoFrame(n) {
    if (!video.videoWidth) { toastError(new Error('Видео ещё не загрузилось')); return; }
    const fps = fpsNow();
    const last = Number.isFinite(video.duration) ? frameCount(video.duration, fps) - 1 : Infinity;
    await seekAndGrab(frameTime(Math.min(last, Math.max(0, Math.round(n) || 0)), fps));
  }

  /* ---------------------------------------------------------------- маски */

  async function decodeMask(b64) {
    const img = await loadImage(`data:image/png;base64,${b64}`);
    const c = document.createElement('canvas'); c.width = S.w; c.height = S.h;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, S.w, S.h);
    const d = ctx.getImageData(0, 0, S.w, S.h).data;
    const m = new Uint8Array(S.w * S.h);
    for (let i = 0; i < m.length; i += 1) m[i] = d[4 * i];
    return m;
  }

  async function findMasks() {
    maskErr.replaceChildren();
    if (!S.src) { showErr({ code: 'image_required', message: '' }); return; }
    try {
      const blob = await canvasBlob(before);
      const buf = new Uint8Array(await blob.arrayBuffer());
      let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      const res = await api.raw(`${base}/genjutsu/masks`, { method: 'POST', body: { image_b64: btoa(bin), engine: S.engine, consent: true } });
      const t0 = performance.now();
      const masks = {};
      for (const r of REGIONS) if (res.regions[r]) masks[r] = await decodeMask(res.regions[r].png_b64);
      S.lab = imageToLab(S.src.data);
      S.masks = masks; S.prep = prepare(S.lab, masks); S.result = res;
      const prepMs = Math.round(performance.now() - t0);
      const ws = res.worker_seconds;
      const engineTime = ws && typeof ws === 'object' ? `модель ${ws.parse} с (+ загрузка ${ws.load} с)` : `модель ${ws} с`;
      maskInfo.textContent = `Зоны найдены за ${res.seconds_total} с: ${engineTime}, ${res.provider === 'cpu' ? 'CPU (GPU занят)' : 'GPU'}; `
        + `подготовка в браузере ${prepMs} мс. Площадь: ${REGIONS.map((r) => `${REGION_LABEL[r]} ${pct(res.regions[r] && res.regions[r].area)}`).join(' · ')}`
        + (res.approximate && res.approximate.length ? `. Приблизительно: ${res.approximate.map((r) => REGION_LABEL[r] || r).join(', ')}` : '');
      renderRegion(); render.flush();
    } catch (e) { toastError(e); showErr(e); }
  }

  function showErr(e) {
    const code = (e && e.code) || 'unknown';
    maskErr.replaceChildren(h('div.dg-alert', { role: 'alert' },
      h('div.dg-alert-title', ERR[code] || 'Не удалось найти зоны'),
      h('pre.dg-raw', `[${code}] ${(e && e.message) || ''}`)));
  }

  /* ---------------------------------------------------------------- перекраска */

  /* ---- восстановление после сбоя: настройки зон переживают перезагрузку страницы (кадр и маски — нет) ---- */
  const persist = debounce(() => {
    try {
      localStorage.setItem(SESSION_KEY, serializeSession({
        settings: history.all(), region: S.region, trigger: trigger.value, subject: subject.value,
        frame: S.fileName ? { name: S.fileName, frame_index: frameIndex(video.currentTime || 0, fpsNow()) } : null }));
    } catch { /* storage may be blocked: the editor works without it */ }
  }, 250);

  const render = debounce(() => {
    if (!S.src || !S.masks) return;
    const t0 = performance.now();
    const n = recolor({ src: S.src.data, lab: S.lab, masks: S.masks, prep: S.prep, settings: history.all(), out: S.out.data });
    after.getContext('2d').putImageData(S.out, 0, 0);
    drawOverlay();
    renderInfo.textContent = `Перекраска: ${Math.round(performance.now() - t0)} мс, изменено пикселей: ${n}`;
  }, LIVE_MS);

  function drawOverlay() {
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, S.w, S.h);
    const m = S.masks && S.masks[S.region];
    if (!showMask.checked || !m) return;
    const img = ctx.createImageData(S.w, S.h);
    for (let i = 0; i < m.length; i += 1) { img.data[4 * i] = 120; img.data[4 * i + 1] = 240; img.data[4 * i + 2] = 190; img.data[4 * i + 3] = m[i] * 0.55; }
    ctx.putImageData(img, 0, 0);
  }

  const applySplit = () => {
    const v = Number(split.value);
    after.style.clipPath = `inset(0 0 0 ${v}%)`;
    splitLine.style.left = `${v}%`;
  };

  /* ---------------------------------------------------------------- зона: контролы */

  function edit(region, value, final) {
    if (!S.editing[region]) { history.set(region, value); S.editing[region] = !final; }
    else { history.replace(region, value); if (final) S.editing[region] = false; }
    render(); persist();
    if (final) renderRegion();
  }

  function renderTabs() {
    tabs.replaceChildren(...REGIONS.map((r) => {
      const st = history.get(r);
      const b = h('button.gj-tab', { type: 'button', role: 'tab', id: `gj-tab-${r}`, 'aria-selected': r === S.region ? 'true' : 'false', 'aria-controls': 'gj-region-panel', dataset: { region: r } },
        REGION_LABEL[r], st.on ? h('span.gj-dot', { style: { background: st.color }, 'aria-label': 'перекрашивается' }) : null);
      b.addEventListener('click', () => { S.region = r; renderRegion(); drawOverlay(); });
      return b;
    }));
  }

  function renderRegion() {
    renderTabs();
    const r = S.region;
    const st = history.get(r);
    const res = S.result && S.result.regions[r];
    const approx = S.result && (S.result.approximate || []).includes(r);
    const sw = SWATCHES[r === 'hair' ? 'hair' : 'clothes'];
    const on = h('input', { type: 'checkbox', id: 'gj-on', name: 'gj-on', checked: st.on });
    on.addEventListener('change', () => edit(r, { on: on.checked }, true));
    const color = h('input.gj-color', { type: 'color', id: 'gj-color', name: 'gj-color', value: st.color, 'aria-label': 'Свой цвет' });
    color.addEventListener('input', () => edit(r, { color: color.value, on: true }, false));
    color.addEventListener('change', () => edit(r, { color: color.value, on: true }, true));
    const swatches = h('div.gj-swatches', { role: 'group', 'aria-label': 'Цвета' }, sw.map(([hex, name]) => {
      const b = h('button.gj-swatch', { type: 'button', title: name, 'aria-label': name, 'aria-pressed': st.on && st.color === hex ? 'true' : 'false', style: { background: hex } });
      b.addEventListener('click', () => edit(r, { color: hex, on: true }, true));
      return b;
    }));
    const slider = (key, label, hint) => {
      const id = `gj-${key}`;
      const val = h('span.dg-num', `${st[key]}%`);
      const input = h('input.gj-range', { type: 'range', min: '0', max: '100', value: String(st[key]), id, name: id });
      input.addEventListener('input', () => { val.textContent = `${input.value}%`; edit(r, { [key]: Number(input.value), on: true }, false); });
      input.addEventListener('change', () => edit(r, { [key]: Number(input.value), on: true }, true));
      return h('div', h('label.dg-label.gj-flex', { for: id }, h('span', label), val), input, h('div.dg-note', hint));
    };
    const undo = btn('Отменить', () => { if (history.undo(r)) { render(); persist(); renderRegion(); } }, { variant: 'subtle', size: 'sm', iconName: 'retry', disabled: !history.canUndo(r),
      title: history.canUndo(r) ? '' : 'Нечего отменять: в этой зоне ещё не было изменений.' });
    const redo = btn('Повторить', () => { if (history.redo(r)) { render(); persist(); renderRegion(); } }, { variant: 'subtle', size: 'sm', disabled: !history.canRedo(r),
      title: history.canRedo(r) ? '' : 'Нечего повторять: нет отменённых изменений.' });
    const reset = btn('Сбросить зону', () => { if (history.reset(r)) { render(); persist(); renderRegion(); } }, { variant: 'ghost', size: 'sm' });
    undo.dataset.testid = 'gj-undo'; redo.dataset.testid = 'gj-redo'; reset.dataset.testid = 'gj-reset';
    regionBody.replaceChildren(...[            // DOM replaceChildren prints null as text: filter it out
      h('div.dg-row', h('label.dg-label.gj-inline', { for: 'gj-on' }, on, h('span', `Перекрашивать: ${REGION_LABEL[r]}`)),
        res ? pill(`площадь ${pct(res.area)}`, { tone: res.area ? 'ok' : 'warn' }) : pill('зоны не найдены', { tone: 'idle' }),
        approx ? pill('граница приблизительная', { tone: 'warn', title: 'Этот движок делит одежду на верх и низ по середине фигуры' }) : null),
      res && !res.area ? h('div.dg-note', 'Модель не нашла эту зону в кадре — перекрашивать нечего.') : null,
      h('div', h('span.dg-label', 'Цвет'), h('div.dg-row', swatches, color)),
      slider('strength', 'Сила цвета', 'Насколько оттенок уходит к выбранному цвету. Яркость складок и прядей сохраняется.'),
      slider('lightness', 'Подстроить яркость под цвет', '0% — светлота как в оригинале; 100% — средняя светлота зоны как у образца (фактура остаётся).'),
      h('div.dg-row', undo, redo, reset)].filter(Boolean));
  }

  function renderPresets() {
    presetBox.replaceChildren(...PRESETS.map((p) => {
      const b = h('button.gj-preset', { type: 'button', dataset: { preset: p.id, group: p.group || '' } }, h('b', p.label), h('small', p.note));
      b.addEventListener('click', () => { if (history.setAll(presetSettings(p))) { render(); persist(); renderRegion(); } toastOk(`Образ «${p.label}»`, 'Каждую зону можно отменить отдельно'); });
      return b;
    }));
  }

  /* ---------------------------------------------------------------- экспорт и LoRA */

  async function savePng() {
    if (!S.masks) { toastError(new Error('Сначала найдите зоны')); return; }
    render.flush();
    download(await canvasBlob(after), 'genjutsu-result.png');
  }

  /* Clean frame (no effect) and effect frame are SEPARATE files; the manifest ties both by sha256. One ZIP. */
  async function exportVariants() {
    if (!S.src) { toastError(new Error('Сначала выберите кадр')); return; }
    render.flush();
    const idx = frameIndex(video.currentTime || 0, fpsNow());
    const names = cleanVariantNames(S.fileName.replace(/\.[^.]+$/, '') || 'frame', idx);
    const files = [];
    const entries = [];
    const add = async (name, role, blob) => {
      const bytes = new Uint8Array(await blob.arrayBuffer());
      files.push({ name, data: bytes });
      entries.push({ name, role, sha256: await sha256Hex(bytes), bytes: bytes.length });
    };
    await add(names.clean, 'clean', await canvasBlob(before));
    const withEffect = !!S.masks && Object.values(history.all()).some((v) => v.on);
    if (withEffect) await add(names.effect, 'effect', await canvasBlob(after));
    const manifest = buildManifest({
      kind: 'genjutsu-frame',
      source: { name: S.fileName, frame_index: idx, time_s: Number((video.currentTime || 0).toFixed(3)), fps: S.fps, width: S.w, height: S.h },
      settings: history.all(), engine: S.masks ? S.engine : null, files: entries,
    });
    files.push({ name: names.manifest, data: JSON.stringify(manifest, null, 1) });
    const zipName = names.clean.replace(/-clean\.png$/, '-variants.zip');
    download(new Blob([makeZip(files)], { type: 'application/zip' }), zipName);
    S.lastExport = { zip: zipName, manifest, with_effect: withEffect };
    exportInfo.textContent = `Сохранено ${zipName}: ${entries.map((e) => `${e.role} ${e.sha256.slice(0, 12)}…`).join(' · ')}`
      + (withEffect ? '' : ' · эффект не включён: только чистый кадр');
    exportInfo.dataset.manifest = JSON.stringify(manifest);
    toastOk('Варианты сохранены', 'чистый кадр, кадр с эффектом и manifest.json в одном ZIP');
  }

  async function addToDataset() {
    if (!S.src) { toastError(new Error('Сначала выберите кадр')); return; }
    render.flush();
    const blob = await canvasBlob(S.masks ? after : before);
    const thumb = URL.createObjectURL(blob);
    S.dataset.push({ blob, thumb, caption: captionFor(trigger.value.trim() || 'trigger', history.all(), subject.value) });
    renderDataset();
  }

  function renderDataset() {
    dsErr.replaceChildren();
    if (!S.dataset.length) { dsList.replaceChildren(h('li.dg-note', 'Набор пуст. Перекрасьте кадр и нажмите «Добавить кадр в набор».')); return; }
    dsList.replaceChildren(...S.dataset.map((it, i) => {
      const cap = h('textarea.dg-input.gj-caption', { rows: 2, id: `gj-cap-${i}`, name: `gj-cap-${i}`, 'aria-label': `Подпись кадра ${i + 1}` });
      cap.value = it.caption;
      cap.addEventListener('input', () => { it.caption = cap.value; });
      const rm = btn('Убрать', () => { URL.revokeObjectURL(it.thumb); S.dataset.splice(i, 1); renderDataset(); }, { variant: 'ghost', size: 'sm' });
      return h('li.gj-ds-item', h('img', { src: it.thumb, alt: `Кадр ${i + 1}` }), h('div.dg-stack', cap, h('div.dg-row', h('span.dg-note', `${i + 1}`), rm)));
    }));
  }

  async function exportZip() {
    const trig = trigger.value.trim();
    const problems = validateDataset(trig, S.dataset);
    if (problems.length) {
      dsErr.replaceChildren(h('div.dg-alert', { role: 'alert' }, h('div.dg-alert-title', 'Набор не готов'), h('ul', problems.map((p) => h('li', p)))));
      return;
    }
    const files = [];
    for (let i = 0; i < S.dataset.length; i += 1) {
      const name = datasetName(i, trig);
      files.push({ name: `${name}.png`, data: new Uint8Array(await S.dataset[i].blob.arrayBuffer()) });
      files.push({ name: `${name}.txt`, data: S.dataset[i].caption.trim() });
    }
    download(new Blob([makeZip(files)], { type: 'application/zip' }), `${trig}-lora-dataset.zip`);
    toastOk('Набор сохранён', `${S.dataset.length} кадров + подписи .txt`);
  }

  /* ---------------------------------------------------------------- разметка */

  fileInput.addEventListener('change', onFile);
  consent.addEventListener('change', setEnabled);
  engineSel.addEventListener('change', () => { S.engine = engineSel.value; showEngine(); setEnabled(); });
  split.addEventListener('input', applySplit);
  showMask.addEventListener('change', drawOverlay);
  applySplit();

  function showEngine() {
    const e = S.info && S.info.engines.find((x) => x.id === S.engine);
    if (!e) { engineNote.textContent = ''; return; }
    engineNote.replaceChildren(
      e.license.commercial ? 'Лицензия: допускает коммерческое использование. ' : h('b', 'Лицензия: только некоммерческое использование (исследование/оценка). '),
      `${e.license.text}.`, e.available ? '' : ` Недоступен: ${e.reason}`);
  }

  const allReset = btn('Сбросить всё', () => { if (history.setAll(presetSettings(null))) { render(); persist(); renderRegion(); } }, { variant: 'ghost', size: 'sm' });
  const saveBtn = btn('Сохранить PNG', savePng, { variant: 'secondary', iconName: 'check' });
  saveBtn.dataset.testid = 'gj-save';
  const variantsBtn = btn('Чистый кадр + эффект (ZIP с manifest)', exportVariants, { variant: 'secondary', iconName: 'check' });
  variantsBtn.dataset.testid = 'gj-variants';
  const exportInfo = h('div.dg-small.dg-num', { dataset: { testid: 'gj-export-info' }, 'aria-live': 'polite' });
  const zipBtn = btn('Скачать набор ZIP', exportZip, { variant: 'primary' });
  zipBtn.dataset.testid = 'gj-zip';

  const loraBox = h('details.dg-details', { dataset: { testid: 'gj-lora' } },
    h('summary', 'Режим датасета LoRA'),
    h('div.dg-details-body.dg-stack',
      h('div.dg-note', 'Набор для обучения LoRA: кадры (PNG) и подписи (.txt с тем же именем) в одном ZIP. Подписи по-английски — так их понимают тренеры LoRA. Черновик подписи собирается из выбранных цветов; правьте его.'),
      h('div.dg-fields',
        h('div', h('label.dg-label', { for: 'gj-trigger' }, 'Триггер-слово'), trigger),
        h('div', h('label.dg-label', { for: 'gj-subject' }, 'Кто в кадре'), subject)),
      h('div.dg-row', btn('Добавить кадр в набор', addToDataset, { variant: 'secondary' }), zipBtn),
      dsErr, dsList,
      h('div.dg-row', h('span.dg-label', { style: { margin: '0' } }, 'Обучение'), trainPill), trainNote));

  const el = panel('Genjutsu: живой конструктор образа', h('div.dg-stack.gj', { dataset: { testid: 'gj-panel' } },
    h('div.dg-info', 'Перекраска волос и одежды по маскам локальной модели. Это не генерация: фасон не меняется, меняется цвет; складки, пряди и швы берутся из оригинала. Кадр никуда не отправляется, кроме этого компьютера, и не сохраняется.'),
    h('div.dg-fields',
      h('div', h('label.dg-label', { for: 'gj-file' }, 'Фото или видео'), fileInput),
      h('div', h('label.dg-label', { for: 'gj-engine' }, 'Модель масок'), engineSel, engineNote)),
    video, h('div.dg-row', grabBtn), stepRow,
    h('label.dg-label.gj-inline', { for: 'gj-consent' }, consent, h('span', 'Человек в кадре согласен на изменение его изображения')),
    h('div.dg-actions', maskBtn), maskErr, maskInfo,
    h('div.gj-grid',
      h('div.dg-stack', stageBox,
        h('div', h('label.dg-label.gj-flex', { for: 'gj-split' }, h('span', 'До'), h('span', 'После')), split),
        h('div.dg-row', h('label.dg-label.gj-inline', { for: 'gj-show-mask' }, showMask, h('span', 'Подсветить маску зоны')), saveBtn, variantsBtn),
        exportInfo, renderInfo),
      h('div.dg-stack', tabs, regionBody,
        h('div', h('div.dg-row.gj-flex', h('span.dg-label', { style: { margin: '0' } }, 'Готовые образы'), allReset), presetBox))),
    loraBox), { icon: 'edit' });

  let restoredNote = null;
  try {
    const restored = restoreSession(localStorage.getItem(SESSION_KEY));
    if (restored) {
      history.setAll(restored.settings); S.region = restored.region;
      trigger.value = restored.trigger; subject.value = restored.subject;
      restoredNote = Object.entries(restored.settings).filter(([, v]) => v.on).map(([r]) => REGION_LABEL[r]).join(', ');
    }
  } catch { /* no storage: start clean */ }
  renderRegion(); renderPresets(); renderDataset();
  if (restoredNote !== null) {
    maskInfo.textContent = `Настройки прошлого сеанса восстановлены${restoredNote ? ` (зоны: ${restoredNote})` : ''}. Кадр и маски нужно загрузить заново.`;
    maskInfo.dataset.restored = '1';
  }
  fpsInput.addEventListener('change', () => { showFrame(); persist(); });
  trigger.addEventListener('input', persist); subject.addEventListener('change', persist);

  (async () => {
    try {
      const info = await api.raw(`${base}/genjutsu`);
      S.info = info;
      engineSel.replaceChildren(...info.engines.map((e) => h('option', { value: e.id, disabled: !e.available }, e.label + (e.available ? '' : ' — недоступен'))));
      S.engine = info.default_engine;
      if (S.engine) engineSel.value = S.engine;
      else maskErr.replaceChildren(h('div.dg-alert', { role: 'alert' }, h('div.dg-alert-title', 'Ни один движок масок недоступен'),
        h('pre.dg-raw', info.engines.map((e) => `${e.id}: ${e.reason}`).join('\n'))));
      showEngine(); setEnabled();
      const t = info.lora_training || {};
      trainPill.replaceChildren(pill(t.available ? 'подключено' : (t.status || 'не подключено'), { tone: t.available ? 'ok' : 'warn' }));
      trainNote.textContent = t.reason || '';
    } catch (e) {
      maskErr.replaceChildren(h('div.dg-alert', { role: 'alert' }, h('div.dg-alert-title', 'Состояние конструктора не получено'), h('pre.dg-raw', (e && e.message) || String(e))));
      trainPill.replaceChildren(pill('не подключено', { tone: 'warn' }));
    }
  })();

  return el;
}
