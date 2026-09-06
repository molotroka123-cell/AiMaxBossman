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
  generating: false, genNote: null,
  creating: false,
  dirty: false,               // в редакторе есть несохранённое
};

let frame = null;          // живой iframe превью (обновляется точечно)
let inspectorBox = null;   // контейнер инспектора — перерисовка без сброса страницы
let editorNode = null;     // textarea кода
let genNoteNode = null;    // строка прогресса генерации
let resizePreview = null;
window.addEventListener('resize', () => { if (resizePreview) resizePreview(); });

let verPill = null;        // пилюля версии в шапке

/* ---------------- сообщения из превью (пикер) ---------------- */

window.addEventListener('message', (ev) => {
  // Сообщение принимается только от САМОГО кадра превью. Поле source в теле —
  // это данные, а не удостоверение: любое окно может прислать 'bd-preview'.
  // Origin здесь не проверяется намеренно — у песочницы он 'null' по построению.
  if (!frame || ev.source !== frame.contentWindow) return;
  const d = ev && ev.data;
  if (!d || d.source !== 'bd-preview') return;
  if (d.type === 'ready' && frame && frame.contentWindow) {
    frame.contentWindow.postMessage({ source: 'bd-host', type: 'pick', enabled: state.pick }, '*');
    if (state.selected && state.selected.bd_id) {
      frame.contentWindow.postMessage({ source: 'bd-host', type: 'flash', bd_id: state.selected.bd_id }, '*');
    }
  } else if (d.type === 'select') {
    state.selected = d.el || null;
    if (inspectorBox) renderInspector(inspectorBox);
  }
});

/* ---------------- утилиты ---------------- */

const previewUrl = () => `/api/web-designer/projects/${state.id}/preview?t=${Date.now()}`;
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

async function reloadState() {
  const full = await api.raw(`/api/web-designer/projects/${state.id}`);
  state.meta = full.meta;
  state.code = full.code;
  state.versions = full.versions || [];
  /* несохранённый набор владельца не затираем: раньше достаточно было
     кликнуть мимо редактора, чтобы следующий reloadState стёр правку */
  if (editorNode && !state.dirty && document.activeElement !== editorNode) {
    editorNode.value = state.code;
  }
}

function reloadFrame() {
  if (frame) frame.src = previewUrl();
}

/* Версия, на которой построена текущая правка. Сервер сверяет её и отвечает
   409, если код успели изменить в другой вкладке или AI-правкой: чужая работа
   не затирается последним пришедшим. */
const baseVersion = () => (state.meta && Number(state.meta.version)) || 0;

/* One save lane for autosave and Ctrl+S. A response acknowledges only the
   exact project/editor/text sent, never later edits or another project's draft. */
let saveTimer = null;
let pendingSave = null;
function saveCode() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { saveTimer = null; void flushSave(); }, 900);
}

async function flushSave(toast) {
  clearTimeout(saveTimer); saveTimer = null;
  if (pendingSave) return pendingSave;
  if (!state.dirty) return true;
  if (!editorNode || !state.id) return false;
  const projectId = state.id;
  pendingSave = (async () => {
    try {
      // Bound continuous typing; leave dirty and refuse navigation if it never
      // settles. Server CAS remains mandatory and is not retried on conflict.
      for (let attempt = 0; attempt < 8; attempt++) {
        if (state.id !== projectId || !editorNode) return false;
        const node = editorNode, html = node.value;
        const res = await api.raw(`/api/web-designer/projects/${projectId}/code`,
          { method: 'PUT', body: { html, note: 'правка кода', base_version: baseVersion() } });
        if (!res || !res.ok || !res.meta) throw new Error('Сохранение не подтверждено сервером');
        if (state.id !== projectId || editorNode !== node) return false;
        state.meta = res.meta;
        state.code = html;
        state.dirty = node.value !== html;
        reloadFrame();
        if (!state.dirty) {
          if (toast) toastOk('Код сохранён');
          return true;
        }
      }
      return false;
    } catch (e) {
      toastError(e, 'Не удалось сохранить код');
      return false;
    }
  })();
  try { return await pendingSave; }
  finally { pendingSave = null; }
}

/* Операции, после которых нумерация элементов в документе СДВИГАЕТСЯ: тот же
   bd-N указывает уже на другой тег. Выделение после них обязано сброситься —
   раньше инспектор продолжал целиться по старому номеру и правил чужой
   элемент, подсвечивая его как подтверждение. */
const SHIFTS_IDS = new Set(['delete', 'replace']);

async function sendEdit(payload, okMsg) {
  const sel = state.selected;
  const body = Object.assign({
    base_version: baseVersion(),
    tag: sel && sel.tag ? sel.tag : undefined,
  }, payload);
  try {
    const res = await api.raw(`/api/web-designer/projects/${state.id}/edit`,
      { method: 'POST', body });
    toastOk(okMsg || 'Правка применена');
    state.meta = res.meta;
    if (SHIFTS_IDS.has(payload.op)) state.selected = null;
    await reloadState();
    reloadFrame();
    if (inspectorBox) renderInspector(inspectorBox);
  } catch (e) {
    /* устаревшее выделение — сбрасываем, чтобы следующий клик не повторил ошибку */
    state.selected = null;
    if (inspectorBox) renderInspector(inspectorBox);
    await reloadState();
    reloadFrame();
    toastError(e, 'Правка не прошла');
  }
}

/* ---------------- генерация «как стрим» ---------------- */

async function runGenerate(prompt, tpl, pal) {
  if (state.generating) return;
  state.generating = true;
  setGenNote('Собираем структуру…');
  try {
    const res = await api.raw(`/api/web-designer/projects/${state.id}/generate`,
      { method: 'POST', body: { prompt, template: tpl || 'auto', palette: pal || 'auto' } });
    const steps = res.steps || [];
    for (let i = 0; i < steps.length; i++) {
      setGenNote(`Генерируем: блок ${i + 1} из ${steps.length} — сайт растёт вживую`);
      if (frame) frame.srcdoc = steps[i];
      await sleep(i === steps.length - 1 ? 100 : 320);
    }
    if (frame) frame.removeAttribute('srcdoc');
    await reloadState();
    reloadFrame();
    toastOk(`Сайт собран: шаблон ${res.template}, палитра ${res.palette}`);
  } catch (e) { toastError(e, 'Генерация не удалась'); }
  state.generating = false;
  setGenNote(null);
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

    const aiPrompt = h('textarea', { rows: '2', placeholder: 'Например: сделай кнопку заметнее и добавь тень',
      style: { width: '100%', boxSizing: 'border-box', background: 'transparent', color: 'inherit',
        border: '1px solid color-mix(in srgb, currentColor 22%, transparent)', borderRadius: '10px', padding: '8px', font: 'inherit', fontSize: '12.5px' } });
    children.push(h('div.bd-row', h('label', 'AI-правка'), aiPrompt,
      btn('Спросить модель', async () => {
        if (!aiPrompt.value.trim()) return toastError(new Error('Опишите правку'));
        try {
          const res = await api.raw(`/api/web-designer/projects/${state.id}/ai-edit`,
            { method: 'POST', body: { prompt: aiPrompt.value, bd_id: sel.bd_id, path: sel.path,
              base_version: baseVersion() } });
          toastOk(`Модель ${res.model || ''} внесла правку`);
          await reloadState();
          reloadFrame();
        } catch (e) { toastError(e, 'AI-правка не удалась'); }
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
`);
}

function head(ctx) {
  const opts = state.projects.map((p) => h('option', { value: String(p.id), selected: Number(p.id) === Number(state.id) },
    `${p.name} · v${p.version}`));
  const sel = h('select', { style: { maxWidth: '220px' }, onChange: async () => {
    const next = Number(sel.value), previous = state.id;
    sel.disabled = true;
    if (!await flushSave()) { sel.value = String(previous); sel.disabled = false; return; }
    state.id = next; state.selected = null; state.creating = false;
    try { localStorage.setItem(LAST_KEY, String(state.id)); } catch { /* приватный режим */ }
    ctx.navigate('web_designer', { project: String(next) });
  } }, opts);
  sel.value = String(state.id);

  return pageHead('Веб-дизайн',
    'Пишем сайт и видим результат сразу: живое превью, выделение области кликом, точечные правки.',
    { pills: [verPill = pill(`v${(state.meta && state.meta.version) || 0}`, { tone: 'info' })],
      actions: [
        sel,
        btn('+ Проект', async () => {
          if (!await flushSave()) return;
          state.id = null; state.selected = null; state.creating = true;
          editorNode = null; frame = null;
          try { localStorage.removeItem(LAST_KEY); } catch { /* приватный режим */ }
          ctx.navigate('web_designer');
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
  editorNode = h('textarea.bd-code', { spellcheck: 'false',
    onInput: () => { state.dirty = true; saveCode(); },
    onKeyDown: (e) => {
      if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
        e.preventDefault();
        flushSave(true);
      }
    } }, state.code);
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
        const yes = await confirmDialog({ title: `Вернуть v${v.version}?`, text: 'Текущий код сохранится в истории — ничего не потеряется.', okText: 'Вернуть' });
        if (!yes) return;
        try {
          await api.raw(`/api/web-designer/projects/${state.id}/versions/${v.version}/restore`, { method: 'POST' });
          toastOk(`Версия v${v.version} возвращена`);
          await reloadState();
          reloadFrame();
          ctx.refresh();
        } catch (e) { toastError(e, 'Откат не удался'); }
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
    panel('Новый сайт',
      h('div', { style: { display: 'grid', gap: '12px', maxWidth: '720px' } },
        name, prompt,
        h('div.bd-row', pal,
          btn('Открыть проект', async () => {
            try {
              const res = await api.raw('/api/web-designer/projects', { method: 'POST',
                body: { name: name.value.trim() || 'Мой сайт', prompt: prompt.value.trim(),
                  template: chosen, palette: pal.value } });
              state.id = Number(res.meta.id);
              state.selected = null; state.creating = false; state.dirty = false;
              try { localStorage.setItem(LAST_KEY, String(state.id)); } catch { /* приватный режим */ }
              toastOk('Проект создан');
              ctx.navigate('web_designer', { project: String(state.id) });
            } catch (e) { toastError(e, 'Не удалось создать проект'); }
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
  section: 'main',

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
        state.id = Number(res.meta.id);
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

    if (state.creating && !(params && params.project)) return emptyState(ctx, catalog);
    if (params && params.project) state.creating = false;

    /* id проекта — ЧИСЛО везде: сервер отдаёт число, ссылка ?project=N и
       localStorage — строки. Сравнение по значению, иначе «последний проект»
       и deep-link не срабатывают никогда и панель открывает первый попавшийся. */
    if (params && params.project) state.id = Number(params.project) || null;
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
      generateBar(),
      h('div.bd-grid', editorPanel(), previewPanel(ctx), inspectorPanel()),
      versionsPanel(ctx));
  },
};

export default WebDesignerPage;
