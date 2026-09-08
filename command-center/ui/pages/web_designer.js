/* ============================================================
   web_designer.js — «Веб-дизайн»: визуальная панель написания сайтов.

   То, чего просил владелец: пишем сайт — и видим его сразу, как в Claude.
   Слева код, в центре живое превью (iframe), справа инспектор: клик по
   любому элементу в превью выделяет его, и правка ложится на сервер
   точечно (текст, цвет, отступы, замена, удаление, AI-правка).

   Серверные endpoint'ы (bcc/features/web_designer.py):
   GET/POST /api/web-designer/projects, GET/PUT .../code,
   POST .../generate (шаги для «стриминга» сборки), POST .../edit,
   POST .../ai-edit, GET .../preview (HTML с пикером), версии, удаление.

   Ничего не рисуем сами о состоянии сервера: сначала запрос, потом экран.
   ============================================================ */

import { api } from '../api.js';
import { h, toastOk, toastError, confirmDialog, fmtDateShort } from '../components.js';
import { pageHead, panel, btn, pill, tag, field } from './_ui.js';

const LAST_KEY = 'bd.lastProject';
import { VIEWPORT_PRESETS, VIEWPORT_ZOOMS, VIEWPORT_LIMITS, viewportSettings,
  loadViewport, saveViewport, viewportGeometry } from './web_designer_viewport.js';

const state = {
  projects: [], id: null, meta: null, code: '', versions: [],
  templates: [], palettes: [],
  selected: null, pick: true,
  models: [], defaultModel: '',        // реестр моделей для AI-правки и серверный «авто»
  modelId: (() => { try { const v = localStorage.getItem('bd.model'); return v ? Number(v) : null; } catch (e) { return null; } })(),
  generating: false, genNote: null,
  dirty: false,               // в редакторе есть несохранённое
  creating: false,            // explicit New project must not auto-open an old one
  saveConflict: false,
  mutating: false,
  recovery: null,
};

let frame = null;          // живой iframe превью (обновляется точечно)
let inspectorBox = null;   // контейнер инспектора — перерисовка без сброса страницы
let editorNode = null;     // textarea кода
let genNoteNode = null;    // строка прогресса генерации
let resizePreview = null;
let recoveryNode = null;
window.addEventListener('resize', () => { if (resizePreview) resizePreview(); });

let verPill = null;        // пилюля версии в шапке

/* ---------------- сообщения из превью (пикер) ---------------- */

/* Сообщение пикера принимается, только если оно пришло ИЗ САМОГО кадра И несёт
   одноразовый пропуск той страницы превью, которую панель туда открыла. Одной
   проверки окна мало: кадр с sandbox="allow-scripts" волен увести СЕБЯ на чужую
   страницу (meta refresh, location=), и та шлёт панели свой 'select' от имени
   пикера — инспектор показывает ложный элемент и подводит владельца к правке
   или удалению. Прочитать пропуск чужой странице нечем: он лежит в URL превью,
   а у песочницы непрозрачный origin — referrer пуст, parent.location закрыт.
   Origin сообщения не проверяется намеренно: у песочницы он 'null'. */
export function acceptsPickerMessage(ev, frame, nonce) {
  if (!ev || !frame || ev.source !== frame.contentWindow) return false;
  const d = ev.data;
  if (!d || d.source !== 'bd-preview') return false;
  return Boolean(nonce) && d.nonce === nonce;
}

window.addEventListener('message', (ev) => {
  if (!acceptsPickerMessage(ev, frame, previewNonce)) return;
  const d = ev.data;
  if (d.type === 'ready' && frame && frame.contentWindow) {
    frame.contentWindow.postMessage({ source: 'bd-host', type: 'pick', enabled: state.pick }, '*');
    if (state.selected && state.selected.bd_id) {
      /* Свежее описание того же элемента, а не подсветка старого: после
         правки кадр перезагружен, а state.selected ещё помнит ПРЕЖНИЙ текст и
         стили. Инспектор, отрисованный по старому описанию, подставлял в поле
         «Текст» сохранённое до правки значение, и второе «Применить»
         откатывало первую правку (наблюдение Astra/Codex на машине владельца,
         2026-09-08). Кадр ответит 'select' с актуальным describe(). */
      frame.contentWindow.postMessage({ source: 'bd-host', type: 'reselect', bd_id: state.selected.bd_id }, '*');
      frame.contentWindow.postMessage({ source: 'bd-host', type: 'flash', bd_id: state.selected.bd_id }, '*');
    }
  } else if (d.type === 'select') {
    state.selected = d.el || null;
    if (inspectorBox) renderInspector(inspectorBox);
  } else if (d.type === 'lost') {
    /* элемент исчез из кода (удалён/заменён) — выделение больше ничему не соответствует */
    if (state.selected && state.selected.bd_id === d.bd_id) {
      state.selected = null;
      if (inspectorBox) renderInspector(inspectorBox);
    }
  }
});

/* Оптимистичное обновление описания выделенного элемента сразу после
   принятой правки: до ответа кадра ('reselect' → 'select') инспектор уже
   показывает применённое значение, а не сохранённое до неё. Источник истины
   — код проекта; это лишь то, что инспектор показывает в промежутке. */
const STYLE_KEYS = { color: 'color', background: 'backgroundColor', 'background-color': 'backgroundColor',
  'font-size': 'fontSize', padding: 'padding', 'border-radius': 'borderRadius' };
export function applyEditLocally(selected, payload) {
  if (!selected || !payload) return selected;
  const next = Object.assign({}, selected, { styles: Object.assign({}, selected.styles || {}) });
  if (payload.op === 'text' && typeof payload.text === 'string') {
    next.text = payload.text.trim().replace(/\s+/g, ' ').slice(0, 200);
    if (payload.replace_children) next.children = 0;
  } else if (payload.op === 'style' && payload.props) {
    for (const [k, v] of Object.entries(payload.props)) {
      const key = STYLE_KEYS[k] || k;
      next.styles[key] = v;
    }
  }
  return next;
}

/* ---------------- утилиты ---------------- */

/* Пропуск одноразовый: каждая загрузка кадра получает свой, поэтому страница,
   на которую кадр ушёл после первой загрузки, остаётся без действительного. */
let previewNonce = '';
const newNonce = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '')
  : String(Date.now()) + String(Math.random()).slice(2));
const previewUrl = () => {
  previewNonce = newNonce();
  return `/api/web-designer/projects/${state.id}/preview?t=${Date.now()}&nonce=${previewNonce}`;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rgbToHex(value) {
  const m = /rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(String(value || ''));
  if (!m) return '#000000';
  return '#' + [1, 2, 3].map((i) => Number(m[i]).toString(16).padStart(2, '0')).join('');
}

function px(value, fallback) {
  const n = parseFloat(String(value || ''));
  return Number.isFinite(n) ? String(Math.round(n)) : fallback;
}

function setGenNote(text) {
  state.genNote = text;
  if (genNoteNode) genNoteNode.textContent = text || '';
}

/* ---------------- данные ---------------- */

async function reloadModels() {
  try {
    const res = await api.raw('/api/web-designer/models');
    state.models = res.items || [];
    const def = state.models.find((m) => m.default);
    state.defaultModel = def ? (def.alias || def.name) : '';
    if (state.modelId && !state.models.some((m) => Number(m.id) === Number(state.modelId))) state.modelId = null;
  } catch (e) { state.models = state.models || []; }
}

async function reloadState() {
  const pid = state.id;
  const full = await api.raw(`/api/web-designer/projects/${pid}`);
  if (state.id !== pid) return;
  state.meta = full.meta;
  state.code = full.code;
  state.versions = full.versions || [];
  if (verPill && verPill.children[1]) verPill.children[1].textContent = `v${baseVersion()}`;
  await reloadModels();
  /* несохранённый набор владельца не затираем: раньше достаточно было
     кликнуть мимо редактора, чтобы следующий reloadState стёр правку */
  if (editorNode && !state.dirty && document.activeElement !== editorNode) {
    editorNode.value = state.code;
  }
}

/* A failed refresh must never hide the original failed action. Keep the draft
   and the error on screen until the owner can recover. Only compare-and-swap
   writes get a retry; model calls, policy refusals and stale targets do not. */
function renderRecovery() {
  if (!recoveryNode) return;
  const r = state.recovery;
  recoveryNode.hidden = !r;
  if (!r) { recoveryNode.replaceChildren(); return; }
  const act = (label, fn) => btn(label, async (ev) => {
    const button = ev.currentTarget;
    if (button.disabled) return;
    button.disabled = true;
    try { await fn(); } finally { button.disabled = false; }
  }, { size: 'sm' });
  const actions = [act('Обновить состояние', async () => {
    await refreshRecovery(r);
    renderRecovery();
  })];
  if (r.canRetry) actions.push(act('Повторить безопасно', async () => {
    if (state.id !== r.pid || baseVersion() !== r.version || state.code !== r.code) return;
    await r.retry();
  }));
  if (state.saveConflict && r.fresh && state.dirty) {
    const version = baseVersion();
    actions.push(act(`Сохранить мой код поверх v${version}`, async () => {
      const yes = await confirmDialog({ title: 'Заменить сохранённый код?',
        text: `Ваш черновик заменит v${version}. Сначала сравните его с сохранённым кодом ниже. Предыдущая версия останется в истории.`,
        danger: true, okText: 'Сохранить мой код' });
      if (!yes || baseVersion() !== version || state.id !== r.pid) return;
      state.saveConflict = false;
      await flushSave(true);
    }));
  }
  recoveryNode.replaceChildren(
    h('strong', `${r.label}${r.error.status ? ` · HTTP ${r.error.status}` : ''}`),
    h('p', r.error.message || 'Ответ сервера не получен'),
    h('p', r.guidance), h('p', { role: 'status' }, r.note || 'Обновляем состояние…'),
    h('div.bd-row', actions),
    ...(state.saveConflict && r.fresh ? [h('details',
      h('summary', `Сравнить: сохранённый код v${baseVersion()} (ваш черновик остаётся в редакторе)`),
      h('textarea', { readOnly: true, rows: 6, 'aria-label': 'Сохранённый код для сравнения',
        style: { width: '100%' } }, state.code))] : []));
}

function recoveryPanel() {
  recoveryNode = h('section.bd-recovery', { role: 'alert', hidden: true,
    'data-testid': 'bd-recovery' });
  renderRecovery();
  return recoveryNode;
}

function clearRecovery() {
  state.recovery = null;
  renderRecovery();
}

async function refreshRecovery(r) {
  r.canRetry = false;
  r.fresh = false;
  if (state.id !== r.pid) return;
  try {
    if (!r.pid) {
      state.projects = (await api.raw('/api/web-designer/projects')).items || [];
      r.note = `Список обновлён: ${state.projects.length} проектов. Проверьте список перед повторным созданием.`;
      return;
    }
    await reloadState();
    r.fresh = true;
    r.note = `Сохранённое состояние обновлено: v${baseVersion()}. Ваш черновик сохранён в редакторе.`;
    // Read-back plus the ORIGINAL version guard is necessary. A changed
    // version, even with identical HTML, may be a committed first request.
    r.canRetry = Boolean(r.retry && baseVersion() === r.version && state.code === r.code);
    reloadFrame();
  } catch (error) {
    r.note = `Состояние обновить не удалось: ${error.message || 'нет связи'}. Действие не повторено.`;
  }
}

async function operationError(error, label, { retry = null, version = baseVersion(), code = state.code,
  model = false } = {}) {
  const status = Number(error.status || 0);
  if ([404, 409].includes(status)) {
    state.selected = null;
    if (inspectorBox) renderInspector(inspectorBox);
  }
  let guidance = 'Проверьте данные и сохранённое состояние перед новым действием.';
  if (status === 409) guidance = state.saveConflict
    ? 'Код изменён в другой вкладке. Автосохранение приостановлено: сравните версии и явно выберите замену.'
    : 'Обновите состояние и заново выберите элемент. Если нет модели, добавьте её в реестре.';
  if (status === 404) guidance = 'Проект, элемент или модель больше не доступны. Обновите состояние и выберите существующий объект. Черновик можно скачать кнопкой «Скачать HTML».';
  if (status === 413) guidance = 'Превышен допустимый размер. Уменьшите код или выберите отдельный элемент. Повтор без исправления не поможет.';
  if (status === 401 || status === 403) guidance = 'Действие запрещено: проверьте вход и разрешения. Повтор не обходит отказ политики.';
  if (model && (status === 0 || status >= 500)) guidance = 'Ответ модели не подтверждён. Запрос не повторён: повторное обращение может снова списать средства. Проверьте провайдера и сохранённое состояние.';
  const transient = [0, 502, 503, 504].includes(status);
  const r = { error, label, guidance, pid: state.id, version, code,
    retry: transient && !model ? retry : null, canRetry: false, fresh: false };
  state.recovery = r;
  renderRecovery();
  await refreshRecovery(r);
  renderRecovery();
  toastError(error, label);
}

function reloadFrame() {
  if (frame) frame.src = previewUrl();
}

/* Версия, на которой построена текущая правка. Сервер сверяет её и отвечает
   409, если код успели изменить в другой вкладке или AI-правкой: чужая работа
   не затирается последним пришедшим. */
const baseVersion = () => (state.meta && Number(state.meta.version)) || 0;

/* Отложенное и немедленное сохранение — ОДИН путь. Их было два, и отложенный,
   доживший до применённой правки, отправлял старый текст редактора с уже новой
   версией: сервер принимал запись, и правка исчезала без единой ошибки. */
const SAVE_DELAY_MS = 900;
let saveTimer = null;
let savePromise = null;

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { saveTimer = null; flushSave(); }, SAVE_DELAY_MS);
}

/* Немедленное сохранение: смена проекта, Ctrl+S и ЛЮБАЯ правка через сервер —
   иначе взведённый автосейв откатит её, как только сработает. */
async function flushSave(toast) {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (savePromise) return savePromise;
  if (!state.dirty || !editorNode || !state.id) return true;
  if (state.saveConflict) return false;
  savePromise = saveCode(toast).finally(() => { savePromise = null; });
  return savePromise;
}

async function saveCode(toast) {
  const sent = editorNode.value;
  const version = baseVersion();
  const code = state.code;
  try {
    const res = await api.raw(`/api/web-designer/projects/${state.id}/code`,
      { method: 'PUT', body: { html: sent, note: 'правка кода',
        base_version: version } });
    if (res && res.ok) {
      state.meta = res.meta;
      state.code = sent;
      /* сохранён ТОТ текст, что ушёл: пока шёл запрос, владелец мог печатать
         дальше — снимать признак несохранённого с нового набора нельзя */
      state.dirty = editorNode.value !== sent;
      if (state.dirty) scheduleSave();
      reloadFrame();
      clearRecovery();
      if (toast) toastOk('Код сохранён');
      return !state.dirty;
    }
    throw new Error('Сервер не подтвердил сохранение кода');
  } catch (e) {
    /* Отказ 409 «код изменился» оставлял meta устаревшей навсегда: каждое
       следующее нажатие клавиши повторяло тот же base_version и получало тот
       же отказ — владелец не мог сохранить НИЧЕГО до перезагрузки страницы.
       Набранное при этом не трогаем: reloadState бережёт его по dirty. */
    state.saveConflict = e.status === 409;
    await operationError(e, 'Код не сохранён', { version, code, retry: () => flushSave(true) });
    if ([0, 502, 503, 504].includes(Number(e.status || 0))
      && state.recovery.fresh && state.code === sent) {
      state.dirty = editorNode.value !== sent;
      state.recovery.note = 'Проверка сервера: отправленный код уже сохранён. Повторная запись не нужна.';
      renderRecovery();
    }
    return false;
  }
}

/* Операции, после которых нумерация элементов в документе СДВИГАЕТСЯ: тот же
   bd-N указывает уже на другой тег. Выделение после них обязано сброситься —
   раньше инспектор продолжал целиться по старому номеру и правил чужой
   элемент, подсвечивая его как подтверждение. */
const SHIFTS_IDS = new Set(['delete', 'replace']);

async function sendEdit(payload, okMsg) {
  if (state.mutating) return;
  state.mutating = true;
  try {
  if (!await flushSave()) return;
  const sel = state.selected;
  const body = Object.assign({
    base_version: baseVersion(),
    tag: sel && sel.tag ? sel.tag : undefined,
  }, payload);
  const version = baseVersion(), code = state.code;
  try {
    const res = await api.raw(`/api/web-designer/projects/${state.id}/edit`,
      { method: 'POST', body });
    toastOk(okMsg || 'Правка применена');
    state.meta = res.meta;
    if (SHIFTS_IDS.has(payload.op)) state.selected = null;
    else state.selected = applyEditLocally(state.selected, payload);
    await reloadState();
    clearRecovery();
    reloadFrame();
    if (inspectorBox) renderInspector(inspectorBox);
  } catch (e) {
    /* устаревшее выделение — сбрасываем, чтобы следующий клик не повторил ошибку */
    state.selected = null;
    if (inspectorBox) renderInspector(inspectorBox);
    // Never replay an element operation against refreshed element IDs. The
    // owner reselects the live target; saving code has the safe retry path.
    await operationError(e, 'Правка не прошла', { version, code });
  }
  } finally { state.mutating = false; }
}

/* ---------------- генерация «как стрим» ---------------- */

async function runGenerate(prompt, tpl, pal) {
  if (state.generating || state.mutating) return;
  state.generating = true;
  state.mutating = true;
  setGenNote('Собираем структуру…');
  try {
    if (!await flushSave()) return;
    const res = await api.raw(`/api/web-designer/projects/${state.id}/generate`,
      { method: 'POST', body: { prompt, template: tpl || 'auto', palette: pal || 'auto',
        base_version: baseVersion() } });
    const steps = res.steps || [];
    for (let i = 0; i < steps.length; i++) {
      setGenNote(`Генерируем: блок ${i + 1} из ${steps.length} — сайт растёт вживую`);
      if (frame) frame.srcdoc = steps[i];
      await sleep(i === steps.length - 1 ? 100 : 320);
    }
    if (frame) frame.removeAttribute('srcdoc');
    state.selected = null;
    await reloadState();
    clearRecovery();
    reloadFrame();
    toastOk(`Сайт собран: шаблон ${res.template}, палитра ${res.palette}`);
  } catch (e) { await operationError(e, 'Генерация не удалась'); }
  finally {
    if (frame) frame.removeAttribute('srcdoc');
    state.generating = false;
    state.mutating = false;
    setGenNote(null);
  }
}

/* ---------------- инспектор выбранного элемента ---------------- */

function renderInspector(box) {
  const sel = state.selected;
  if (!box) return;
  const children = [];
  if (!sel) {
    children.push(h('div', { style: { color: 'var(--bx-ink-3,#8b93a7)', fontSize: '13px' } },
      'Кликните по любому элементу в превью — он выделится, и здесь появятся точечные правки.'));
  } else {
    const info = [sel.tag, sel.id ? `#${sel.id}` : '', sel.classes && sel.classes.length ? `.${sel.classes.join('.')}` : '']
      .filter(Boolean).join('');
    children.push(
      h('div.bd-elinfo', info),
      sel.path ? h('div.bd-elinfo', { style: { opacity: 0.7 } }, sel.path) : null,
      sel.text ? h('div', { style: { fontSize: '12.5px', margin: '6px 0', color: 'var(--bx-ink-3,#8b93a7)' } },
        `«${sel.text.slice(0, 90)}${sel.text.length > 90 ? '…' : ''}»`) : null,
    );

    /* Поле подставляет textContent ВСЕГО поддерева. Для элемента с вложенными
       тегами «Применить» без единой правки означало бы «схлопнуть сотню
       потомков в одну строку» — и это уже сохранено. Спрашиваем явно. */
    const nested = Number(sel.children || 0) > 0;
    const textInput = h('input', { type: 'text', value: sel.text || '', style: { flex: '1' } });
    children.push(h('div.bd-row',
      h('label', 'Текст'), textInput,
      btn('Применить', async () => {
        if (nested) {
          const yes = await confirmDialog({
            title: 'Заменить всё содержимое текстом?',
            text: `Внутри <${sel.tag}> ${sel.children} вложенных элементов — они будут удалены и заменены одной строкой.`,
            danger: true, okText: 'Заменить' });
          if (!yes) return;
        }
        await sendEdit({ op: 'text', bd_id: sel.bd_id, path: sel.path, text: textInput.value,
          replace_children: nested }, 'Текст обновлён');
      }, { variant: 'primary', size: 'sm' })));
    if (nested) {
      children.push(h('div', { style: { fontSize: '11.5px', color: 'var(--bx-ink-3,#8b93a7)', marginTop: '-4px' } },
        `Внутри ${sel.children} вложенных элементов: выберите вложенный тег, чтобы править только его текст.`));
    }

    const color = h('input', { type: 'color', value: rgbToHex(sel.styles && sel.styles.color) });
    color.addEventListener('change', () => sendEdit({ op: 'style', bd_id: sel.bd_id, path: sel.path, props: { color: color.value } }, 'Цвет текста изменён'));
    const bg = h('input', { type: 'color', value: rgbToHex(sel.styles && sel.styles.backgroundColor) });
    bg.addEventListener('change', () => sendEdit({ op: 'style', bd_id: sel.bd_id, path: sel.path, props: { background: bg.value } }, 'Фон изменён'));
    children.push(h('div.bd-row', h('label', 'Цвет / фон'), color, bg));

    const size = h('input', { type: 'number', min: 8, max: 96, value: px(sel.styles && sel.styles.fontSize, '16') });
    size.addEventListener('change', () => sendEdit({ op: 'style', bd_id: sel.bd_id, path: sel.path, props: { 'font-size': `${size.value}px` } }, 'Размер шрифта изменён'));
    const pad = h('input', { type: 'number', min: 0, max: 120, value: px(sel.styles && sel.styles.padding, '0') });
    pad.addEventListener('change', () => sendEdit({ op: 'style', bd_id: sel.bd_id, path: sel.path, props: { padding: `${pad.value}px` } }, 'Отступы изменены'));
    const radius = h('input', { type: 'number', min: 0, max: 120, value: px(sel.styles && sel.styles.borderRadius, '0') });
    radius.addEventListener('change', () => sendEdit({ op: 'style', bd_id: sel.bd_id, path: sel.path, props: { 'border-radius': `${radius.value}px` } }, 'Скругление изменено'));
    children.push(h('div.bd-row', h('label', 'Кегль'), size,
      h('label', { style: { minWidth: '64px' } }, 'Отступ'), pad));
    children.push(h('div.bd-row', h('label', 'Скругление'), radius));

    const htmlArea = h('textarea', { rows: '4', placeholder: '<div>новый HTML элемента</div>',
      style: { width: '100%', boxSizing: 'border-box', background: 'transparent', color: 'inherit',
        border: '1px solid color-mix(in srgb, currentColor 22%, transparent)', borderRadius: '10px', padding: '8px', font: '12px/1.5 ui-monospace, monospace' } });
    children.push(h('div.bd-row', h('label', 'HTML'), htmlArea,
      btn('Заменить', async () => {
        if (!htmlArea.value.trim()) return toastError(new Error('Пустая замена'));
        await sendEdit({ op: 'replace', bd_id: sel.bd_id, path: sel.path, html: htmlArea.value }, 'Элемент заменён');
      }, { size: 'sm' })));

    /* Выбор модели для AI-правки. Раньше бэкенд брал models[0] — первую строку
       реестра, и владелец не мог выбрать GLM/Claude по построению (аудит
       владельца 2026-09-08, F3a). Список — канонический реестр моделей с их
       здоровьем; «авто» — выбор по здоровью на сервере. */
    const modelSel = h('select', { 'aria-label': 'Модель для AI-правки', style: { maxWidth: '100%' } },
      h('option', { value: '' }, `авто (по здоровью${state.defaultModel ? `: ${state.defaultModel}` : ''})`),
      ...(state.models || []).map((m) => h('option', {
        value: String(m.id), selected: String(state.modelId || '') === String(m.id),
        disabled: m.health && m.health.status && !['healthy', 'unmeasured'].includes(m.health.status) ? true : undefined,
      }, `${m.alias || m.name}${m.health && m.health.status ? ` · ${m.health.status}` : ''}`)));
    modelSel.addEventListener('change', () => {
      state.modelId = modelSel.value ? Number(modelSel.value) : null;
      try { localStorage.setItem('bd.model', modelSel.value); } catch (e) { /* без памяти выбора */ }
    });
    children.push(h('div.bd-row', h('label', 'Модель'), modelSel));
    if (!(state.models || []).length) {
      children.push(h('div', { style: { fontSize: '11.5px', color: 'var(--bx-ink-3,#8b93a7)', marginTop: '-4px' } },
        'В реестре нет моделей — AI-правка недоступна, пока не добавлена модель.'));
    }

    const aiPrompt = h('textarea', { rows: '2', placeholder: 'Например: сделай кнопку заметнее и добавь тень',
      style: { width: '100%', boxSizing: 'border-box', background: 'transparent', color: 'inherit',
        border: '1px solid color-mix(in srgb, currentColor 22%, transparent)', borderRadius: '10px', padding: '8px', font: 'inherit', fontSize: '12.5px' } });
    children.push(h('div.bd-row', h('label', 'AI-правка'), aiPrompt,
      btn('Спросить модель', async () => {
        if (!aiPrompt.value.trim()) return toastError(new Error('Опишите правку'));
        if (state.mutating) return;
        state.mutating = true;
        try {
          if (!await flushSave()) return;
          const res = await api.raw(`/api/web-designer/projects/${state.id}/ai-edit`,
            { method: 'POST', body: { prompt: aiPrompt.value, bd_id: sel.bd_id, path: sel.path,
              base_version: baseVersion(), model_id: state.modelId || null } });
          toastOk(`Модель ${res.model || ''} внесла правку${res.chosen_by === 'health_rank' ? ' (выбрана по здоровью)' : ''}`);
          state.selected = null;
          await reloadState();
          clearRecovery();
          reloadFrame();
        } catch (e) { await operationError(e, 'AI-правка не удалась', { model: true }); }
        finally { state.mutating = false; }
      }, { variant: 'primary', size: 'sm' })));

    children.push(h('div.bd-row',
      btn('Удалить элемент', async () => {
        const yes = await confirmDialog({ title: 'Удалить элемент?', text: `Тег <${sel.tag}> будет убран из кода — версия сохранится в истории.`, danger: true, okText: 'Удалить' });
        if (yes) await sendEdit({ op: 'delete', bd_id: sel.bd_id, path: sel.path }, 'Элемент удалён');
      }, { variant: 'danger', size: 'sm' })));
  }
  children.push(h('div', { style: { fontSize: '11.5px', color: 'var(--bx-ink-3,#8b93a7)', marginTop: '10px' } },
    state.pick ? 'Режим выделения включён: клик перехватывается превью.'
      : 'Режим выделения выключен — ссылки в превью кликаются как обычно.'));
  box.replaceChildren ? box.replaceChildren(...children.filter(Boolean)) : null;
}

/* ---------------- сборки экрана ---------------- */

function styleNode() {
  return h('style', `
.bd-grid{display:grid;grid-template-columns:minmax(260px,24%) minmax(0,1fr) minmax(250px,23%);gap:14px;align-items:start;margin-top:14px}
@media (max-width:1100px){.bd-grid{grid-template-columns:1fr}}
.bd-code{width:100%;box-sizing:border-box;min-height:540px;font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;border:1px solid color-mix(in srgb,currentColor 16%,transparent);border-radius:12px;background:transparent;color:inherit;padding:12px;resize:vertical;outline:none;tab-size:2}
.bd-code:focus{border-color:var(--bx-azure,#4f8cff)}
.bd-framewrap{border:1px solid color-mix(in srgb,currentColor 16%,transparent);border-radius:14px;overflow:hidden}
.bd-framebar{display:flex;gap:8px;align-items:center;padding:8px 10px;border-bottom:1px solid color-mix(in srgb,currentColor 12%,transparent);flex-wrap:wrap}
.bd-frame{border:0;background:#fff;display:block;transform-origin:top left;max-width:none}
.bd-viewport-stage{height:min(66vh,720px);min-height:320px;overflow:auto;padding:12px;box-sizing:border-box;background:color-mix(in srgb,currentColor 5%,transparent)}
.bd-viewport-canvas{position:relative;margin:0 auto}
.bd-viewport-canvas .bd-frame{position:absolute;left:0;top:0}
.bd-viewport-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid color-mix(in srgb,currentColor 12%,transparent)}
.bd-viewport-tools label{display:flex;gap:4px;align-items:center;font-size:12px}
.bd-viewport-tools select,.bd-viewport-tools input{box-sizing:border-box;max-width:100%;padding:5px 6px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:7px;background:transparent;color:inherit;font:inherit;font-size:12px}
.bd-viewport-tools input{width:76px}
.bd-viewport-status{padding:6px 10px;font-size:11.5px;color:var(--bx-ink-3,#8b93a7)}
.bd-row{display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap}
.bd-row label{font-size:12px;color:var(--bx-ink-3,#8b93a7);min-width:70px}
.bd-row input[type=number],.bd-row input[type=text]{padding:6px 8px;border-radius:8px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);background:transparent;color:inherit;font:inherit;font-size:12.5px;width:84px}
.bd-row input[type=color]{width:38px;height:30px;padding:2px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:8px;background:transparent;cursor:pointer}
.bd-elinfo{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700}
.bd-vers{display:flex;gap:10px;align-items:center;padding:7px 10px;border-radius:10px;border:1px solid color-mix(in srgb,currentColor 10%,transparent);margin:6px 0;font-size:12.5px}
.bd-genbar{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:12px}
.bd-genbar input,.bd-genbar select{padding:9px 10px;border-radius:10px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);background:transparent;color:inherit;font:inherit;font-size:13px}
.bd-tplgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin-top:12px}
.bd-tpl{border:1px solid color-mix(in srgb,currentColor 18%,transparent);border-radius:14px;padding:14px 16px;cursor:pointer;text-align:left;background:transparent;color:inherit;font:inherit}
.bd-tpl b{display:block;font-size:14.5px}
.bd-tpl span{font-size:12px;color:var(--bx-ink-3,#8b93a7)}
.bd-tpl:hover,.bd-tpl.is-on{border-color:var(--bx-azure,#4f8cff)}
.bd-mini{font-size:12px;color:var(--bx-ink-3,#8b93a7)}
.bd-dev{border:1px solid color-mix(in srgb,currentColor 22%,transparent);background:transparent;color:inherit;border-radius:8px;padding:4px 10px;font-size:12px;cursor:pointer}
.bd-dev.is-on{border-color:var(--bx-azure,#4f8cff);color:var(--bx-azure,#4f8cff)}
.bd-recovery{margin-top:14px;padding:14px;border:1px solid var(--bx-danger,#bc4343);border-radius:12px}
.bd-recovery p{margin:8px 0;font-size:13px}
`);
}

function head(ctx) {
  const opts = state.projects.map((p) => h('option', { value: String(p.id), selected: Number(p.id) === Number(state.id) },
    `${p.name} · v${p.version}`));
  const sel = h('select', { style: { maxWidth: '220px' }, onChange: async () => {
    if (state.mutating || !await flushSave()) { sel.value = String(state.id); return; }
    clearRecovery(); state.saveConflict = false;
    state.id = Number(sel.value); state.selected = null;
    try { localStorage.setItem(LAST_KEY, String(state.id)); } catch { /* приватный режим */ }
    ctx.refresh();
  } }, opts);
  sel.value = String(state.id);

  return pageHead('Веб-дизайн',
    'Пишем сайт и видим результат сразу: живое превью, выделение области кликом, точечные правки.',
    { pills: [verPill = pill(`v${(state.meta && state.meta.version) || 0}`, { tone: 'info' })],
      actions: [
        sel,
        btn('+ Проект', async () => {
          if (state.mutating || !await flushSave()) return;
          clearRecovery(); state.saveConflict = false;
          state.id = null; state.selected = null; state.creating = true;
          try { localStorage.removeItem(LAST_KEY); } catch { /* приватный режим */ }
          ctx.refresh();
        },
          { variant: 'ghost', size: 'sm', title: 'Новый проект веб-дизайна' }),
        btn('Скачать HTML', () => {
          const blob = new Blob([editorNode ? editorNode.value : state.code],
            { type: 'text/html;charset=utf-8' });
          const a = h('a', { href: URL.createObjectURL(blob),
            download: `${((state.meta && state.meta.name) || 'site').replace(/[\\/:*?"<>|]+/g, '_')}.html` });
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 3000);
        }, { variant: 'ghost', size: 'sm', title: 'Сохранить текущий код как файл' }),
        btn('Задача «сделать сайт»', async () => {
          try {
            const res = await api.createTask({
              title: `Сайт: ${(state.meta && state.meta.name) || 'проект'}`,
              prompt: `Сверстай и улучши сайт «${(state.meta && state.meta.name) || ''}». `
                + `Шаблон: ${(state.meta && state.meta.template) || 'landing'}, палитра: ${(state.meta && state.meta.palette) || 'indigo'}. `
                + `Текущий код проекта #${state.id} в панели «Веб-дизайн», работай точечными правками, сохраняй структуру.\n\n`
                + `HTML (начало): ${(state.code || '').slice(0, 3000)}`,
              run_now: true,
            });
            toastOk(`Задача #${res.task && res.task.id} создана — панель уже открыта`);
          } catch (e) { toastError(e, 'Не удалось создать задачу'); }
        }, { variant: 'ghost', size: 'sm' }),
      ] });
}

function editorPanel() {
  attachEditor(h('textarea.bd-code', { spellcheck: 'false',
    onInput: () => { state.dirty = true; scheduleSave(); },
    onKeyDown: (e) => {
      if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
        e.preventDefault();
        flushSave(true);
      }
    } }, state.code));
  return panel('Код сайта', editorNode, {
    aside: h('span.bd-mini', 'автосохранение · Ctrl+S — сохранить сейчас'),
  });
}

function previewPanel(ctx) {
  // sandbox БЕЗ allow-same-origin — превью получает непрозрачный origin и не
  // может ни прочитать cookie/localStorage панели, ни позвать её /api. Скрипты
  // разрешены: без них не работает ни пикер, ни сам сайт. Сервер выставляет то
  // же ограничение заголовком CSP, здесь — чтобы кадр был безопасен сразу.
  frame = h('iframe.bd-frame', { src: previewUrl(), title: 'Живое превью сайта',
                                 sandbox: 'allow-scripts' });
  const pickBtn = btn(state.pick ? 'Выделение: вкл' : 'Выделение: выкл', () => {
    state.pick = !state.pick;
    pickBtn.querySelector('span').textContent = state.pick ? 'Выделение: вкл' : 'Выделение: выкл';
    if (frame && frame.contentWindow) {
      frame.contentWindow.postMessage({ source: 'bd-host', type: 'pick', enabled: state.pick }, '*');
    }
    renderInspector(inspectorBox);
  }, { variant: 'ghost', size: 'sm' });
  // Scale the iframe visually without changing its CSS viewport or site code.
  // Settings belong to this project and are deliberately separate from HTML history.
  let storage = null;
  try { storage = window.localStorage; } catch { /* sandboxed/private storage */ }
  const projectId = Number(state.id); // API metadata stores IDs as strings.
  let viewport = loadViewport(storage, projectId);
  const previewFrame = frame;
  const canvas = h('div.bd-viewport-canvas', previewFrame);
  const stage = h('div.bd-viewport-stage', { 'data-testid': 'bd-viewport-stage' }, canvas);
  const status = h('div.bd-viewport-status', { role: 'status', 'aria-live': 'polite' });
  let persistenceNote = '';
  const preset = h('select', { 'aria-label': 'Размер экрана превью' },
    VIEWPORT_PRESETS.map((p) => h('option', { value: p.id }, p.label)),
    h('option', { value: 'custom' }, 'Свой размер'));
  const dimension = (label, value) => h('input', { type: 'number', min: VIEWPORT_LIMITS.min,
    max: VIEWPORT_LIMITS.max, step: 1, value, 'aria-label': label });
  const width = dimension('Ширина превью', viewport.width);
  const height = dimension('Высота превью', viewport.height);
  const zoom = h('select', { 'aria-label': 'Масштаб превью' },
    h('option', { value: 'fit' }, 'Вписать'),
    h('option', { value: 'width' }, 'По ширине'),
    VIEWPORT_ZOOMS.map((z) => h('option', { value: String(z) }, `${z * 100}%`)));
  function syncControls() {
    width.value = String(viewport.width); height.value = String(viewport.height);
    zoom.value = String(viewport.zoom);
    preset.value = (VIEWPORT_PRESETS.find((p) => p.width === viewport.width && p.height === viewport.height) || {}).id || 'custom';
  }
  function applyGeometry() {
    if (!stage.isConnected || stage.clientWidth <= 24 || stage.clientHeight <= 24) return;
    const g = viewportGeometry(viewport, stage.clientWidth - 24, stage.clientHeight - 24);
    previewFrame.style.width = `${g.width}px`;
    previewFrame.style.height = `${g.height}px`;
    previewFrame.style.transform = `scale(${g.scale})`;
    canvas.style.width = `${g.renderedWidth}px`;
    canvas.style.height = `${g.renderedHeight}px`;
    status.textContent = `${g.width} × ${g.height} CSS px · ${Math.round(g.scale * 100)}% · масштаб меняет только показ${persistenceNote}`;
  }
  function accept(next) {
    viewport = next;
    persistenceNote = saveViewport(storage, projectId, viewport) ? '' : ' · настройки только на этот сеанс';
    width.setCustomValidity(''); height.setCustomValidity('');
    syncControls(); applyGeometry();
  }
  preset.addEventListener('change', () => {
    const selected = VIEWPORT_PRESETS.find((p) => p.id === preset.value);
    if (selected) accept(viewportSettings(selected.width, selected.height, viewport.zoom));
    else width.focus();
  });
  const applyDimensions = () => {
    try { accept(viewportSettings(Number(width.value), Number(height.value), viewport.zoom)); }
    catch (e) { width.setCustomValidity(e.message); width.reportValidity(); }
  };
  width.addEventListener('input', () => width.setCustomValidity(''));
  height.addEventListener('input', () => width.setCustomValidity(''));
  for (const input of [width, height]) input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); applyDimensions(); }
  });
  zoom.addEventListener('change', () => accept(viewportSettings(viewport.width, viewport.height,
    ['fit', 'width'].includes(zoom.value) ? zoom.value : Number(zoom.value))));
  const tools = h('div.bd-viewport-tools', preset,
    h('label', 'Ш', width), h('label', 'В', height),
    btn('Применить размер', applyDimensions, { variant: 'ghost', size: 'sm' }),
    btn('Повернуть', () => accept(viewportSettings(viewport.height, viewport.width, viewport.zoom)),
      { variant: 'ghost', size: 'sm', title: 'Поменять ширину и высоту' }), zoom);
  syncControls();
  resizePreview = applyGeometry;
  requestAnimationFrame(applyGeometry);
  const openLink = h('a', { href: `/api/web-designer/projects/${state.id}/preview`, target: '_blank',
    rel: 'noopener', style: { fontSize: '12px', color: 'var(--bx-ink-3,#8b93a7)' } }, 'открыть в новой вкладке');
  return h('div.bd-framewrap',
    h('div.bd-framebar', pickBtn,
      h('span', { style: { flex: '1', textAlign: 'center', fontSize: '12px', color: 'var(--bx-ink-3,#8b93a7)' } },
        'клик по элементу — выделение и правки справа'),
      openLink, btn('Обновить', () => reloadFrame(), { variant: 'ghost', size: 'sm' })),
    tools, stage, status);
}

function inspectorPanel() {
  inspectorBox = h('div');
  renderInspector(inspectorBox);
  return panel('Инспектор', inspectorBox, {
    aside: h('span.bd-mini', 'точечные правки'),
  });
}

function versionsPanel(ctx) {
  const rows = (state.versions || []).slice().reverse().slice(0, 12).map((v) =>
    h('div.bd-vers',
      h('b', `v${v.version}`),
      h('span', v.note || ''),
      h('span.bd-mini', v.ts ? fmtDateShort(v.ts) : ''),
      h('span.spacer'),
      h('span.bd-mini', `${v.chars || ''} симв.`),
      btn('Вернуть', async () => {
        if (state.mutating) return;
        const yes = await confirmDialog({ title: `Вернуть v${v.version}?`, text: 'Текущий код сохранится в истории — ничего не потеряется.', okText: 'Вернуть' });
        if (!yes) return;
        if (state.mutating) return;
        state.mutating = true;
        try {
          if (!await flushSave()) return;
          await api.raw(`/api/web-designer/projects/${state.id}/versions/${v.version}/restore`, { method: 'POST' });
          toastOk(`Версия v${v.version} возвращена`);
          state.selected = null;
          await reloadState();
          clearRecovery();
          reloadFrame();
          ctx.refresh();
        } catch (e) { await operationError(e, 'Откат не удался'); }
        finally { state.mutating = false; }
      }, { variant: 'ghost', size: 'sm' })));
  return panel('История версий', rows.length
    ? h('div', rows)
    : h('div.bd-mini', 'Пока одна версия — правьте, и каждая правка здесь сохранится.'));
}

function generateBar() {
  const prompt = h('input', { type: 'text', placeholder: 'Опишите сайт: «портфолио фотографа в зелёных тонах»',
    style: { flex: '1', minWidth: '240px' } });
  const tpl = h('select', { style: { minWidth: '150px' } },
    h('option', { value: 'auto' }, 'Шаблон: авто'),
    state.templates.map((t) => h('option', { value: t.id }, `Шаблон: ${t.title}`)));
  const pal = h('select', { style: { minWidth: '140px' } },
    h('option', { value: 'auto' }, 'Палитра: авто'),
    state.palettes.map((p) => h('option', { value: p }, `Палитра: ${p}`)));
  genNoteNode = h('span.bd-gennote', { style: { fontSize: '12.5px', color: 'var(--bx-ink-3,#8b93a7)' } },
    state.genNote || '');
  return h('div.bd-genbar',
    prompt, tpl, pal,
    btn('Сгенерировать сайт', () => runGenerate(prompt.value, tpl.value, pal.value),
      { variant: 'primary', size: 'sm' }),
    genNoteNode);
}

/* ---------------- пустое состояние: создание проекта ---------------- */

function emptyState(ctx, catalog) {
  const name = h('input', { type: 'text', placeholder: 'Название проекта, например «Кофейня Север»',
    style: { width: '100%', boxSizing: 'border-box', padding: '10px 12px', borderRadius: '10px',
      border: '1px solid color-mix(in srgb, currentColor 22%, transparent)', background: 'transparent', color: 'inherit', font: 'inherit' } });
  const prompt = h('textarea', { rows: '3', placeholder: 'О чём сайт и как должен выглядеть: тема, стиль, цвета…',
    style: { width: '100%', boxSizing: 'border-box', padding: '10px 12px', borderRadius: '10px',
      border: '1px solid color-mix(in srgb, currentColor 22%, transparent)', background: 'transparent', color: 'inherit', font: 'inherit' } });
  let chosen = 'landing';
  const cards = catalog.items.map((t) => {
    const c = h('button.bd-tpl', { type: 'button', class: t.id === chosen ? 'is-on' : '' },
      h('b', t.title), h('span', t.hint));
    c.addEventListener('click', () => {
      chosen = t.id;
      cards.forEach((x) => x.classList.remove('is-on'));
      c.classList.add('is-on');
    });
    return c;
  });
  const pal = h('select', { style: { padding: '9px 10px', borderRadius: '10px',
    border: '1px solid color-mix(in srgb, currentColor 22%, transparent)', background: 'transparent', color: 'inherit' } },
    h('option', { value: 'auto' }, 'Палитра: авто (из описания)'),
    catalog.palettes.map((p) => h('option', { value: p }, `Палитра: ${p}`)));

  return h('div.bx-page', styleNode(),
    pageHead('Веб-дизайн', 'Создайте проект — и панель откроется: код, живое превью и точечные правки в одном экране.'),
    recoveryPanel(),
    panel('Новый сайт',
      h('div', { style: { display: 'grid', gap: '12px', maxWidth: '720px' } },
        name, prompt,
        h('div.bd-row', pal,
          btn('Открыть проект', async () => {
            try {
              const res = await api.raw('/api/web-designer/projects', { method: 'POST',
                body: { name: name.value.trim() || 'Мой сайт', prompt: prompt.value.trim(),
                  template: chosen, palette: pal.value } });
              state.id = Number(res.meta.id); state.creating = false;
              state.selected = null;
              try { localStorage.setItem(LAST_KEY, String(state.id)); } catch { /* приватный режим */ }
              toastOk('Проект создан');
              ctx.refresh();
            } catch (e) { await operationError(e, 'Не удалось создать проект'); }
          }, { variant: 'primary' }))),
      { icon: 'builder' }),
    panel('Или выберите заготовку',
      h('div.bd-tplgrid', cards),
      { icon: 'apps' }));
}

/* ---------------- страница ---------------- */

const WebDesignerPage = {
  id: 'web_designer',
  title: 'Веб-дизайн',
  icon: 'builder',
  nav: 'primary',
  section: 'studio',

  onEvent() {
    /* свою перерисовку страница не просит: редактор и выделение живут
       в состоянии страницы, общий refresh сбросил бы набор текста */
    return false;
  },

  async render(ctx, params) {
    /* deep-link из задачи: #/web_designer?task=<название> — создать проект из задачи */
    if (params && params.task && !state.id) {
      const title = String(params.task).slice(0, 100);
      try {
        const res = await api.raw('/api/web-designer/projects', { method: 'POST',
          body: { name: title.replace(/^сайт[:\s]*/i, '') || 'Новый сайт', prompt: title, template: 'auto', palette: 'auto' } });
        state.id = Number(res.meta.id); state.creating = false;
        try { localStorage.setItem(LAST_KEY, String(state.id)); } catch { /* приватный режим */ }
        toastOk('Панель веб-дизайна открыта под эту задачу');
      } catch (e) { toastError(e, 'Не удалось создать проект из задачи'); }
    }

    let catalog = { items: [], palettes: [] };
    const [listRes, tplRes] = await Promise.allSettled([
      api.raw('/api/web-designer/projects'),
      api.raw('/api/web-designer/templates'),
    ]);
    if (listRes.status !== 'fulfilled') throw listRes.reason;
    state.projects = listRes.value.items || [];
    if (tplRes.status === 'fulfilled') {
      state.templates = tplRes.value.items || [];
      state.palettes = tplRes.value.palettes || [];
      catalog = tplRes.value;
    }

    /* id проекта — ЧИСЛО везде: сервер отдаёт число, ссылка ?project=N и
       localStorage — строки. Сравнение по значению, иначе «последний проект»
       и deep-link не срабатывают никогда и панель открывает первый попавшийся. */
    if (params && params.project) { state.id = Number(params.project) || null; state.creating = false; }
    if (state.creating) return emptyState(ctx, catalog);
    if (!state.id) {
      try { state.id = Number(localStorage.getItem(LAST_KEY)) || null; } catch { /* приватный режим */ }
    }
    if (state.id && !state.projects.some((p) => Number(p.id) === Number(state.id))) state.id = null;
    if (!state.id && state.projects.length) state.id = Number(state.projects[0].id);
    if (!state.id) return emptyState(ctx, catalog);

    await reloadState();
    try { localStorage.setItem(LAST_KEY, String(state.id)); } catch { /* приватный режим */ }

    return h('div.bx-page', styleNode(),
      head(ctx),
      recoveryPanel(),
      generateBar(),
      h('div.bd-grid', editorPanel(), previewPanel(ctx), inspectorPanel()),
      versionsPanel(ctx));
  },
};

export default WebDesignerPage;

/* Открыто для ui/tests/web_designer_autosave.test.mjs. Автосохранение —
   единственное место страницы, где две правки владельца расходятся во времени,
   и проверять его надо без браузера; на поведение панели экспорт не влияет. */
export { state as autosaveState, flushSave, scheduleSave, sendEdit, SAVE_DELAY_MS };

export function attachEditor(node) {
  editorNode = node;
  return node;
}
