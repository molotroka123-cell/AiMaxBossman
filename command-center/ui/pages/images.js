/* ============================================================
   images.js — BOSSMAN Images: library + generation jobs.
   Exact vanilla-JS V2 page for the current Command Center.
   ============================================================ */

import { api, listOf } from '../api.js';
import {
  h, icon, statusBadge, toast, toastOk, toastError, fmtDateShort,
} from '../components.js';
import { pageHead, errorBanner, emptyPanel } from './_shared.js';

const PREVIEW_CACHE = new Map();
let selectedAssetId = null;
let activeTab = 'library';
let composerState = {
  prompt: 'Футуристический городской пейзаж на закате, неоновые огни, дождь, отражения на мокром асфальте, кинематографичный свет',
  model_alias: 'mock-image',
  aspect_ratio: '16:9',
  width: 1280,
  height: 720,
  count: 1,
  steps: 30,
};

const ImagesPage = {
  id: 'images',
  title: 'Изображения',
  icon: 'models',
  nav: 'primary',

  async render(ctx) {
    ensureStyles();
    let assets = []; let jobs = []; let collections = []; let models = [];
    let storage = null; let overview = null; let err = null;
    try {
      const [a, j, c, m, s, o] = await Promise.all([
        api.raw('/api/images/assets?limit=120'),
        api.raw('/api/images/jobs?limit=40'),
        api.raw('/api/images/collections'),
        api.raw('/api/images/models'),
        api.raw('/api/images/storage'),
        api.raw('/api/images/overview'),
      ]);
      assets = listOf(a);
      jobs = listOf(j);
      collections = listOf(c);
      models = listOf(m);
      storage = s;
      overview = o;
    } catch (e) { err = e; }

    const head = pageHead(
      'Изображения',
      'Создание картинок, ваша библиотека и коллекции.',
      [
        h('label.btn.btn-sm', { title: 'Загрузить файл с компьютера' },
          icon('plus', 13), h('span', 'Загрузить файл'),
          h('input', {
            type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif,image/svg+xml',
            hidden: true, onChange: (e) => importFile(e.target.files?.[0], ctx),
          })),
      ],
    );

    if (err) return h('div.stack.lg', head, errorBanner(err, ctx));

    if (selectedAssetId == null && assets.length) selectedAssetId = assets[0].id;
    const selected = assets.find((a) => a.id === selectedAssetId) || null;

    return h('div.stack.lg',
      head,
      statsStrip(overview, storage),
      tabs(ctx),
      composer(models, collections, ctx),
      h('div.images-workspace',
        librarySidebar(collections, assets, storage, ctx),
        mainContent(assets, jobs, ctx),
        inspector(selected, collections, ctx),
        recentJobs(jobs, ctx),
      ),
    );
  },

  onEvent(ev) {
    return String(ev.kind || '').startsWith('image.');
  },
};

function statsStrip(overview, storage) {
  return h('div.images-stats',
    miniStat('Библиотека', overview?.assets ?? 0),
    miniStat('Избранное', overview?.favorites ?? 0),
    miniStat('В работе', overview?.active_jobs ?? 0),
    miniStat('Ошибки', overview?.failed_jobs ?? 0, overview?.failed_jobs ? 'var(--err)' : ''),
    miniStat('Хранилище', fmtBytes(storage?.used_bytes || 0)),
  );
}

function miniStat(label, value, color = '') {
  return h('div.images-mini-stat',
    h('div.xsmall.dim', label),
    h('div', { style: { fontSize: '16px', fontWeight: '700', color: color || 'var(--text)' } }, String(value)));
}

function tabs(ctx) {
  const defs = [
    ['library', 'Библиотека'],
    ['generations', 'Генерации'],
    ['templates', 'Шаблоны'],
    ['queue', 'Очередь'],
  ];
  return h('div.images-tabs', defs.map(([id, label]) =>
    h('button', {
      type: 'button',
      class: 'images-tab' + (activeTab === id ? ' active' : ''),
      onClick: () => { activeTab = id; ctx.refresh(); },
    }, label)));
}

function composer(models, collections, ctx) {
  const prompt = h('textarea.images-prompt', {
    value: composerState.prompt,
    placeholder: 'Опишите изображение…',
    onInput: (e) => { composerState.prompt = e.target.value; },
  });

  const model = withValue(h('select.input', {
    onChange: (e) => { composerState.model_alias = e.target.value; },
  }, models.map((m) => h('option', { value: m.alias }, m.name || m.alias))),
  composerState.model_alias);

  const ratio = withValue(h('select.input', {
    onChange: (e) => setRatio(e.target.value),
  }, ['1:1', '16:9', '9:16', '4:3', '3:2'].map((r) => h('option', { value: r }, r))),
  composerState.aspect_ratio);

  const count = withValue(h('select.input', {
    onChange: (e) => { composerState.count = Number(e.target.value) || 1; },
  }, [1, 2, 4].map((n) => h('option', { value: n }, `${n} шт.`))),
  composerState.count);

  const folder = withValue(h('select.input', {
    onChange: (e) => { composerState.collection_id = e.target.value ? Number(e.target.value) : null; },
  }, h('option', { value: '' }, 'Без папки'),
  collections.map((c) => h('option', { value: c.id }, c.name))),
  composerState.collection_id ?? '');

  return h('section.panel.images-composer',
    h('div.panel-body',
      h('div.small', { style: { fontWeight: '700', marginBottom: '8px' } }, 'Промпт'),
      prompt,
      h('div.images-composer-grid',
        field('Модель', model),
        field('Соотношение', ratio),
        field('Количество', count),
        field('Коллекция', folder),
        h('div.images-runbox',
          h('button.btn.btn-primary', {
            type: 'button',
            onClick: () => createJob(ctx),
          }, icon('play', 14), h('span', 'Запустить')),
          h('div.xsmall.dim', `${composerState.width}×${composerState.height} · ${composerState.steps} шагов`),
        ),
      )));
}

function field(label, node) {
  return h('label.images-field', h('div.xsmall.dim', label), node);
}

/* h() выставляет value до добавления <option>, поэтому у select'ов начальное
   значение теряется — ставим его после сборки узла. */
function withValue(node, value) {
  node.value = value === null || value === undefined ? '' : String(value);
  return node;
}

function librarySidebar(collections, assets, storage, ctx) {
  const favoriteCount = assets.filter((x) => x.favorite).length;
  return h('aside.images-side',
    h('section.panel',
      h('div.panel-head', h('h2', 'Коллекции'), h('div.spacer'),
        h('button.btn.btn-sm', { type: 'button', title: 'Новая коллекция', 'aria-label': 'Новая коллекция', onClick: () => createCollection(ctx) }, icon('plus', 12))),
      h('div.panel-body.tight.images-collections',
        collectionRow('Все изображения', assets.length, true, () => { activeTab = 'library'; ctx.refresh(); }),
        collectionRow('Избранное', favoriteCount, false, () => { activeTab = 'library'; ctx.refresh(); }),
        ...collections.map((c) => collectionRow(c.name, c.count || 0, false, () => {}))),
    ),
    h('section.panel',
      h('div.panel-head', h('h2', 'Хранилище')),
      h('div.panel-body',
        h('div.small', fmtBytes(storage?.used_bytes || 0)),
        h('div.xsmall.dim', `${storage?.asset_count || 0} файлов`),
        h('div.images-storage-bar', h('span', { style: { width: storage?.used_bytes ? '12%' : '2%' } })),
      )),
  );
}

function collectionRow(name, count, active, onClick) {
  return h('button.images-collection-row', {
    type: 'button', class: active ? 'active' : '', onClick,
  }, h('span', name), h('span.xsmall.dim', String(count)));
}

function mainContent(assets, jobs, ctx) {
  if (activeTab === 'generations') return generationsTable(jobs, ctx);
  if (activeTab === 'queue') return generationsTable(jobs.filter((j) => ['queued', 'running'].includes(j.status)), ctx);
  if (activeTab === 'templates') return templatesPanel();
  if (!assets.length) {
    return emptyPanel({
      iconName: 'empty',
      title: 'Библиотека пока пустая',
      hint: 'Создайте первое изображение по описанию выше или загрузите свой файл.',
    });
  }
  return h('section.panel.images-library',
    h('div.panel-head',
      h('h2', `Библиотека · ${assets.length}`),
      h('div.spacer'),
      h('input.input.images-search', { placeholder: 'Поиск изображений…' })),
    h('div.panel-body', h('div.images-grid',
      assets.map((asset) => assetCard(asset, ctx)))));
}

function assetCard(asset, ctx) {
  const image = h('div.images-card-preview', loadingTile());
  loadProtectedImage(asset.file_url).then((url) => {
    image.textContent = '';
    image.appendChild(h('img', { src: url, alt: asset.title || 'image' }));
  }).catch(() => {
    image.textContent = '';
    image.appendChild(h('div.small.dim', 'Нет превью'));
  });

  return h('button.images-card', {
    type: 'button',
    class: selectedAssetId === asset.id ? 'selected' : '',
    onClick: () => { selectedAssetId = asset.id; ctx.refresh(); },
  },
  image,
  asset.favorite ? h('span.images-star', '★') : null,
  h('div.images-card-meta',
    h('div.small.truncate', { style: { fontWeight: '700' } }, asset.title || 'Без названия'),
    h('div.row.tight.xsmall.dim',
      h('span', asset.aspect_ratio || '—'),
      h('span', '·'),
      h('span.truncate', asset.model_alias || '—'))));
}

function inspector(asset, collections, ctx) {
  if (!asset) {
    return h('section.panel.images-inspector',
      h('div.panel-head', h('h2', 'Информация')),
      h('div.panel-body', h('div.small.dim', 'Выберите изображение')));
  }

  const preview = h('div.images-inspector-preview', loadingTile());
  loadProtectedImage(asset.file_url).then((url) => {
    preview.textContent = '';
    preview.appendChild(h('img', { src: url, alt: asset.title || 'image' }));
  }).catch(() => { preview.textContent = 'Нет превью'; });

  return h('section.panel.images-inspector',
    h('div.panel-head', h('h2', 'Выбрано: 1 изображение')),
    h('div.panel-body.stack',
      preview,
      h('div',
        h('div.xsmall.dim', 'Описание'),
        h('div.small.images-prompt-copy', asset.prompt || 'Загруженное изображение')),
      h('div.images-detail-grid',
        detail('Модель', asset.model_alias),
        detail('Размер', `${asset.width || '—'}×${asset.height || '—'}`),
        detail('Соотношение', asset.aspect_ratio || '—'),
        detail('Seed', asset.seed ?? '—'),
        detail('Создано', fmtDateShort(asset.created_at)),
        detail('ID', `#${asset.id}`)),
      field('Коллекция', withValue(h('select.input', {
        onChange: (e) => assignCollection(asset, e.target.value, ctx),
      }, h('option', { value: '' }, 'Без коллекции'),
      (collections || []).map((c) => h('option', { value: c.id }, c.name))),
      asset.collection_id ?? '')),
      h('div.stack.tight',
        h('button.btn.btn-sm', { type: 'button', onClick: () => reusePrompt(asset, ctx) }, 'Повторить описание'),
        h('button.btn.btn-sm', { type: 'button', onClick: () => variation(asset, ctx) }, 'Вариация'),
        h('button.btn.btn-sm', { type: 'button', onClick: () => toggleFavorite(asset, ctx) },
          asset.favorite ? 'Убрать из избранного' : 'В избранное'),
      )));
}

function detail(label, value) {
  return h('div.images-detail', h('div.xsmall.dim', label), h('div.xsmall', String(value ?? '—')));
}

function recentJobs(jobs, ctx) {
  return h('section.panel.images-recent',
    h('div.panel-head', h('h2', 'Последние генерации'), h('div.spacer'),
      h('button.btn.btn-sm', { type: 'button', onClick: () => { activeTab = 'generations'; ctx.refresh(); } }, 'Все')),
    h('div.panel-body.tight',
      jobs.length ? h('div.stack.tight', jobs.slice(0, 8).map((j) => jobMini(j, ctx)))
        : h('div.small.dim', 'Запусков пока нет')));
}

function jobMini(job, ctx) {
  return h('div.images-job-mini',
    h('div.row.tight',
      statusBadge(job.status || 'queued', { live: job.status === 'running' }),
      h('div.spacer'),
      h('span.xsmall.dim', `#${job.id}`)),
    h('div.small.truncate', job.prompt),
    h('div.row.tight.xsmall.dim',
      h('span', job.model_alias),
      h('span', '·'),
      h('span', job.aspect_ratio),
      h('span', '·'),
      h('span', `${Math.round((job.progress || 0) * 100)}%`)),
    ['failed', 'cancelled'].includes(job.status)
      ? h('button.btn.btn-sm', { type: 'button', onClick: () => retryJob(job.id, ctx) }, 'Повторить')
      : null);
}

function generationsTable(jobs, ctx) {
  return h('section.panel.images-library',
    h('div.panel-head', h('h2', activeTab === 'queue' ? 'Очередь' : 'Генерации')),
    h('div.panel-body',
      jobs.length ? h('div.stack.tight', jobs.map((j) => h('div.images-job-row',
        h('div', { style: { minWidth: 0 } },
          h('div.small.truncate', { style: { fontWeight: '700' } }, j.prompt),
          h('div.xsmall.dim', `${j.model_alias} · ${j.aspect_ratio} · #${j.id}`)),
        h('div.spacer'),
        statusBadge(j.status, { live: j.status === 'running' }),
        h('div.xsmall.dim', `${Math.round((j.progress || 0) * 100)}%`),
        j.status === 'queued' || j.status === 'running'
          ? h('button.btn.btn-sm.btn-danger', { type: 'button', onClick: () => cancelJob(j.id, ctx) }, 'Стоп')
          : h('button.btn.btn-sm', { type: 'button', onClick: () => retryJob(j.id, ctx) }, 'Повторить'),
      ))) : h('div.small.dim', 'Нет задач')));
}

function templatesPanel() {
  return h('section.panel.images-library',
    h('div.panel-head', h('h2', 'Шаблоны')),
    h('div.panel-body',
      h('div.small.dim', 'API шаблонов уже предусмотрен. Добавьте первые рабочие шаблоны после подключения реальных image providers.')));
}

async function createJob(ctx) {
  if (!composerState.prompt.trim()) {
    toast('Введите описание', { type: 'warn' }); return;
  }
  try {
    await api.raw('/api/images/jobs', {
      method: 'POST',
      body: {
        ...composerState,
        prompt: composerState.prompt.trim(),
        tags: [],
        reference_asset_ids: [],
        options: {},
      },
    });
    toastOk('Генерация поставлена в очередь');
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось создать генерацию'); }
}

async function variation(asset, ctx) {
  try {
    await api.raw('/api/images/jobs', {
      method: 'POST',
      body: {
        prompt: asset.prompt || asset.title || 'variation',
        model_alias: asset.model_alias === 'import' ? 'mock-image' : asset.model_alias,
        aspect_ratio: asset.aspect_ratio || '1:1',
        width: asset.width || 1024,
        height: asset.height || 1024,
        source_asset_id: asset.id,
        kind: 'variation',
        count: 1,
        tags: asset.tags || [],
      },
    });
    toastOk('Вариация поставлена в очередь');
    activeTab = 'queue'; ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось создать вариацию'); }
}

function reusePrompt(asset, ctx) {
  composerState = {
    ...composerState,
    prompt: asset.prompt || '',
    model_alias: asset.model_alias === 'import' ? 'mock-image' : asset.model_alias,
    aspect_ratio: asset.aspect_ratio || '1:1',
    width: asset.width || 1024,
    height: asset.height || 1024,
  };
  toastOk('Промпт перенесён в генератор');
  activeTab = 'library';
  ctx.refresh();
}

async function assignCollection(asset, rawValue, ctx) {
  const collection_id = rawValue ? Number(rawValue) : null;
  if (collection_id === (asset.collection_id ?? null)) return;
  try {
    await api.raw(`/api/images/assets/${encodeURIComponent(asset.id)}`, {
      method: 'PATCH', body: { collection_id },
    });
    toastOk(collection_id ? 'Изображение перенесено в коллекцию' : 'Изображение убрано из коллекции');
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось изменить коллекцию'); }
}

async function toggleFavorite(asset, ctx) {
  try {
    await api.raw(`/api/images/assets/${encodeURIComponent(asset.id)}`, {
      method: 'PATCH', body: { favorite: !asset.favorite },
    });
    toastOk(asset.favorite ? 'Убрано из избранного' : 'Добавлено в избранное');
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось обновить изображение'); }
}

async function retryJob(id, ctx) {
  try {
    await api.raw(`/api/images/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' });
    toastOk('Повтор поставлен в очередь'); ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось повторить'); }
}

async function cancelJob(id, ctx) {
  try {
    await api.raw(`/api/images/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
    toastOk('Генерация остановлена'); ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось остановить'); }
}

async function createCollection(ctx) {
  const name = window.prompt('Название новой коллекции');
  if (!name?.trim()) return;
  try {
    await api.raw('/api/images/collections', {
      method: 'POST', body: { name: name.trim() },
    });
    toastOk('Коллекция создана'); ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось создать коллекцию'); }
}

async function importFile(file, ctx) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) {
    toast('Файл больше 15 MB', { type: 'warn' }); return;
  }
  try {
    const data = await fileToBase64(file);
    await api.raw('/api/images/assets/import', {
      method: 'POST',
      body: { filename: file.name, data_base64: data, title: file.name, tags: [] },
    });
    toastOk('Изображение импортировано'); ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось импортировать'); }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error('read failed'));
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
    reader.readAsDataURL(file);
  });
}

function setRatio(ratio) {
  composerState.aspect_ratio = ratio;
  const sizes = {
    '1:1': [1024, 1024],
    '16:9': [1280, 720],
    '9:16': [720, 1280],
    '4:3': [1200, 900],
    '3:2': [1200, 800],
  };
  const [w, h_] = sizes[ratio] || [1024, 1024];
  composerState.width = w; composerState.height = h_;
}

async function loadProtectedImage(url) {
  if (PREVIEW_CACHE.has(url)) return PREVIEW_CACHE.get(url);
  // V2.1: доступ даёт HttpOnly-cookie сессии, не заголовок с токеном.
  const response = await fetch(url, {
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (!response.ok) throw new Error(`preview ${response.status}`);
  const blobUrl = URL.createObjectURL(await response.blob());
  PREVIEW_CACHE.set(url, blobUrl);
  if (PREVIEW_CACHE.size > 200) {
    const [firstKey, firstUrl] = PREVIEW_CACHE.entries().next().value;
    PREVIEW_CACHE.delete(firstKey);
    URL.revokeObjectURL(firstUrl);
  }
  return blobUrl;
}

function loadingTile() {
  return h('div.xsmall.dim', 'Загрузка…');
}

function fmtBytes(bytes) {
  let n = Number(bytes || 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${units[i]}`;
}

function ensureStyles() {
  if (document.getElementById('bossman-images-style')) return;
  const style = document.createElement('style');
  style.id = 'bossman-images-style';
  style.textContent = `
    .images-stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
    .images-mini-stat{padding:10px 12px;border:1px solid var(--line-soft);background:var(--bg-card);border-radius:var(--radius-sm)}
    .images-tabs{display:flex;gap:24px;border-bottom:1px solid var(--line-soft);overflow:auto}
    .images-tab{border:0;background:none;color:var(--muted);padding:0 0 10px;cursor:pointer;font:inherit;white-space:nowrap}
    .images-tab.active{color:var(--accent);border-bottom:2px solid var(--accent)}
    .images-composer .panel-body{padding:14px}
    .images-prompt{width:100%;min-height:78px;resize:vertical;border:1px solid var(--line);background:var(--bg-elev);color:var(--text);border-radius:var(--radius-sm);padding:10px 12px;font:inherit;line-height:1.45}
    .images-composer-grid{display:grid;grid-template-columns:1.25fr .8fr .65fr 1fr .9fr;gap:10px;margin-top:12px;align-items:end}
    .images-field{display:grid;gap:5px;min-width:0}
    .images-runbox{display:grid;gap:5px}
    .images-workspace{display:grid;grid-template-columns:210px minmax(440px,1fr) 300px 270px;gap:12px;align-items:start}
    .images-side{display:grid;gap:12px}
    .images-collections{display:grid;gap:4px}
    .images-collection-row{display:flex;justify-content:space-between;gap:8px;width:100%;border:0;background:transparent;color:var(--text);padding:8px 9px;border-radius:8px;cursor:pointer;text-align:left}
    .images-collection-row:hover,.images-collection-row.active{background:var(--bg-elev)}
    .images-storage-bar{height:6px;border-radius:9px;background:var(--line-soft);margin-top:9px;overflow:hidden}
    .images-storage-bar span{display:block;height:100%;min-width:3px;background:var(--accent);border-radius:9px}
    .images-library{min-width:0}
    .images-search{max-width:220px}
    .images-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    .images-card{position:relative;overflow:hidden;border:1px solid var(--line-soft);background:var(--bg-elev);border-radius:12px;color:var(--text);padding:0;cursor:pointer;text-align:left;min-width:0}
    .images-card:hover{border-color:color-mix(in srgb,var(--accent) 55%,var(--line-soft))}
    .images-card.selected{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 18%,transparent)}
    .images-card-preview{aspect-ratio:16/10;display:grid;place-items:center;background:#07101d;overflow:hidden}
    .images-card-preview img,.images-inspector-preview img{width:100%;height:100%;object-fit:cover;display:block}
    .images-card-meta{padding:8px 9px}
    .images-star{position:absolute;top:7px;right:7px;color:#ffd166;background:#07101dcc;border-radius:8px;padding:2px 6px}
    .images-inspector-preview{aspect-ratio:16/10;border:1px solid var(--line-soft);border-radius:10px;overflow:hidden;background:#07101d;display:grid;place-items:center}
    .images-detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
    .images-detail{padding:7px;border:1px solid var(--line-soft);border-radius:8px;background:var(--bg-elev)}
    .images-prompt-copy{line-height:1.45;max-height:116px;overflow:auto;margin-top:4px}
    .images-job-mini{padding:9px;border:1px solid var(--line-soft);border-radius:9px;background:var(--bg-elev);display:grid;gap:5px}
    .images-job-row{display:flex;gap:10px;align-items:center;border:1px solid var(--line-soft);border-radius:9px;padding:10px}
    @media(max-width:1450px){.images-workspace{grid-template-columns:190px minmax(400px,1fr) 290px}.images-recent{grid-column:2/4}.images-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
    @media(max-width:1100px){.images-workspace{grid-template-columns:180px minmax(0,1fr)}.images-inspector,.images-recent{grid-column:2}.images-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.images-composer-grid{grid-template-columns:1fr 1fr 1fr}.images-runbox{grid-column:auto}}
    @media(max-width:720px){.images-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.images-composer-grid{grid-template-columns:1fr 1fr}.images-workspace{grid-template-columns:1fr}.images-side,.images-inspector,.images-recent{grid-column:1}.images-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.images-search{max-width:140px}}
    @media(max-width:430px){.images-grid{grid-template-columns:1fr 1fr}.images-composer-grid{grid-template-columns:1fr}.images-tabs{gap:16px}.images-stats{grid-template-columns:1fr 1fr}}
    /* Тач-таргеты ≥44px на узких экранах — общее правило проекта (см. ui/mobile.css). */
    @media(max-width:900px){
      .images-tab{min-height:44px;padding:0 2px 10px}
      .images-collection-row{min-height:44px;align-items:center}
      .images-composer .btn,.images-inspector .btn,.images-recent .btn,
      .images-job-row .btn,.images-side .btn,.images-library .btn{min-height:44px}
      .images-composer .input,.images-inspector .input{min-height:44px}
    }
  `;
  document.head.appendChild(style);
}

export default ImagesPage;
