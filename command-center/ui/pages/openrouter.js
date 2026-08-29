/* ============================================================
   openrouter.js — Feature 02/04: OpenRouter как first-class провайдер.
   Endpoints: POST /api/openrouter/{pid}/sync, GET /api/openrouter/{pid}/catalog,
   POST /api/openrouter/{pid}/pin, POST /api/openrouter/models/{mid}/probe,
   GET /api/openrouter/models/{mid}/capabilities.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, badge, statusBadge, debounce,
  toastOk, toastError, actionButton, field, input, select,
  fmtContext, fmtCost,
} from '../components.js';
import { panel, pageHead, errorNote, blank } from './_ui.js';

function looksLikeOpenRouter(p) {
  const s = `${p.name || ''} ${p.base_url || ''}`.toLowerCase();
  return s.includes('openrouter');
}

const OpenRouterPage = {
  id: 'openrouter',
  title: 'OpenRouter',
  icon: 'models',
  nav: 'more',

  async render(ctx) {
    let providers = []; let err = null;
    try { providers = listOf(await api.providers(), 'providers'); } catch (e) { err = e; }

    const head = pageHead('OpenRouter', 'Каталог облачных моделей OpenRouter: обновить список, закрепить нужные и проверить их возможности.');
    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));

    if (!providers.length) {
      return h('div.bx-page', head, blank({
        iconName: 'models', title: 'Поставщиков моделей нет',
        hint: 'Добавьте OpenRouter как поставщика на странице «Модели» (адрес https://openrouter.ai/api/v1 и ключ), затем вернитесь сюда.',
        action: h('button.btn.btn-primary', { type: 'button', onClick: () => ctx.navigate('models') }, 'К моделям'),
      }));
    }

    const state = ctx.state.openrouter || (ctx.state.openrouter = {});
    if (!state.providerId) {
      const guess = providers.find(looksLikeOpenRouter) || providers[0];
      state.providerId = String(pick(guess, ['id']));
    }

    const providerEl = select(providers.map((p) => ({ value: pick(p, ['id']), label: `${pick(p, ['name'], 'провайдер')}${looksLikeOpenRouter(p) ? ' · OpenRouter?' : ''}` })), { value: state.providerId });
    providerEl.addEventListener('change', () => { state.providerId = providerEl.value; ctx.refresh(); });

    const connectPanel = await buildConnectPanel(state.providerId, ctx);

    const providerRow = h('div.row', field('Поставщик', providerEl), h('div.spacer'),
      actionButton('Обновить список', async () => {
        try {
          const r = await api.raw(`/api/openrouter/${encodeURIComponent(state.providerId)}/sync?force=true`, { method: 'POST' });
          toastOk(r.cached ? 'Открыт сохранённый каталог' : `Список обновлён: ${r.synced} моделей`);
          ctx.refresh();
        } catch (e) { toastError(e, 'OpenRouter недоступен — показан сохранённый каталог'); ctx.refresh(); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'retry' }));

    const catalogPanel = await buildCatalogPanel(state.providerId, ctx);
    const pinnedPanel = await buildPinnedPanel(state.providerId, ctx);

    return h('div.bx-page', head, providerRow, connectPanel, catalogPanel, pinnedPanel);
  },

  onEvent(ev) { return ev.kind === 'model.created'; },
};

async function buildConnectPanel(providerId, ctx) {
  let st = null;
  try { st = await api.raw(`/api/openrouter/${encodeURIComponent(providerId)}/status`); }
  catch { st = { has_key: false, catalog_models: 0, last_synced_at: null }; }

  const out = h('div.stack.sm');
  const when = st.last_synced_at ? new Date(st.last_synced_at).toLocaleString() : null;
  if (st.has_key && st.catalog_models) {
    out.appendChild(h('div.row.tight',
      badge(st.has_key ? 'ключ сохранён' : 'ключа нет', st.has_key ? 'ok' : 'warn'),
      badge(`моделей в каталоге: ${st.catalog_models}`),
      when ? badge(`sync: ${when}`) : null));
    return panel('Подключение', out);
  }

  const keyEl = input({ placeholder: 'sk-or-… ключ OpenRouter', type: 'password' });
  const note = h('div.xsmall.dim', when ? `Ключ сохранён, но каталог пуст — последний sync: ${when}` : 'Вставьте ключ и нажмите Connect — каталог загрузится автоматически.');
  out.appendChild(field('API KEY', keyEl), note,
    actionButton('Connect', async () => {
      try {
        if (keyEl.value.trim()) {
          await api.raw(`/api/openrouter/${encodeURIComponent(providerId)}/key`, {
            method: 'PATCH', body: { api_key: keyEl.value.trim() },
          });
        }
        const r = await api.raw(`/api/openrouter/${encodeURIComponent(providerId)}/connect`, { method: 'POST' });
        toastOk(`Подключено: ${r.models} моделей в каталоге`);
        ctx.refresh();
      } catch (e) { toastError(e, 'Connect не удался — проверьте ключ'); }
    }, { cls: 'btn btn-primary', iconName: 'bolt' }));
  return panel('Подключение', out);
}

const FILTERS = [
  { id: 'all', label: 'ALL', fn: () => true },
  { id: 'free', label: 'FREE', fn: (m) => !m.price_in && !m.price_out },
  { id: 'coding', label: 'CODING', fn: (m) => `${m.remote_id} ${m.display_name}`.toLowerCase().includes('code') },
  { id: 'vision', label: 'VISION', fn: (m) => (m.input_modalities || []).includes('image') },
  { id: 'tools', label: 'TOOLS', fn: (m) => (m.supported_parameters || []).includes('tools') },
];

async function buildCatalogPanel(providerId, ctx) {
  const searchEl = input({ placeholder: 'поиск по названию модели…' });
  const tableOut = h('div.small.dim', 'Загрузка каталога…');
  const state = ctx.state.openrouter || (ctx.state.openrouter = {});
  if (!state.filter) state.filter = 'all';
  let lastRows = [];

  function renderRows() {
    const fn = (FILTERS.find((f) => f.id === state.filter) || FILTERS[0]).fn;
    const rows = lastRows.filter(fn);
    tableOut.textContent = '';
    if (!rows.length) { tableOut.appendChild(h('div.small.dim', lastRows.length ? 'Под фильтр ничего не подошло.' : 'Список пуст — нажмите «Обновить список».')); return; }
    tableOut.appendChild(h('div.stack.sm', { style: { overflowX: 'auto' } }, rows.map((m) => catalogRow(m, providerId, ctx))));
  }

  const filterRow = h('div.row.tight', FILTERS.map((f) => {
    const b = h('button.btn.btn-sm' + (state.filter === f.id ? '.btn-primary' : ''), { type: 'button' }, f.label);
    b.addEventListener('click', () => { state.filter = f.id; ctx.refresh(); });
    return b;
  }));

  async function loadCatalog(q) {
    tableOut.textContent = '';
    tableOut.appendChild(h('div.small.dim', 'Загрузка…'));
    try {
      const rows = await api.raw(`/api/openrouter/${encodeURIComponent(providerId)}/catalog?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`);
      lastRows = rows;
      renderRows();
    } catch (e) { tableOut.textContent = ''; tableOut.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось загрузить каталог')); }
  }

  const debouncedLoad = debounce((q) => loadCatalog(q), 250);
  searchEl.addEventListener('input', () => debouncedLoad(searchEl.value.trim()));
  await loadCatalog('');

  return panel('Каталог моделей OpenRouter', h('div.stack.sm', field('Поиск', searchEl), filterRow, tableOut));
}

function catalogRow(m, providerId, ctx) {
  const mods = [...(m.input_modalities || [])].join('/') || 'text';
  return h('div.card', { style: { padding: '10px 12px' } },
    h('div.row',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.mono.small', m.remote_id), h('div.xsmall.dim', m.display_name || '')),
      m.stale ? badge('устарело', 'idle') : null,
      actionButton('Закрепить', () => doPin(m, providerId, ctx), { cls: 'btn btn-sm btn-primary', iconName: 'plus' })),
    h('div.row.tight',
      badge(`контекст ${fmtContext(m.context_window)}`),
      badge(`вход ${fmtCost(m.price_in)}/1М`), badge(`ответ ${fmtCost(m.price_out)}/1М`),
      badge(mods),
      (m.supported_parameters || []).length ? badge(`${m.supported_parameters.length} настроек`) : null));
}

async function doPin(m, providerId, ctx) {
  try {
    const r = await api.raw(`/api/openrouter/${encodeURIComponent(providerId)}/pin`, {
      method: 'POST', body: { remote_id: m.remote_id, alias: m.remote_id },
    });
    toastOk(r.already ? `Уже закреплена как «${r.alias}»` : `Закреплена как «${r.alias}»`);
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось закрепить модель'); }
}

async function buildPinnedPanel(providerId, ctx) {
  let models = [];
  try { models = listOf(await api.models(), 'models').filter((m) => String(m.provider_id) === String(providerId)); }
  catch { models = []; }

  if (!models.length) {
    return blank({ iconName: 'models', title: 'Закреплённых моделей нет', hint: 'Нажмите «Закрепить» у модели в каталоге выше — она появится здесь и в общем списке моделей.' });
  }

  const rows = await Promise.all(models.map((m) => pinnedRow(m, ctx)));
  return panel(`Закреплённые модели (${models.length})`, h('div.stack.sm', rows));
}

async function pinnedRow(m, ctx) {
  const id = pick(m, ['id']);
  const out = h('div.row.tight', h('span.xsmall.dim', 'ещё не проверялась'));

  async function loadCaps() {
    try {
      const caps = await api.raw(`/api/openrouter/models/${encodeURIComponent(id)}/capabilities`);
      out.textContent = '';
      if (!caps.length) { out.appendChild(h('span.xsmall.dim', 'ещё не проверялась')); return; }
      for (const c of caps) {
        out.appendChild(badge(`${c.capability}: ${c.advertised ? 'заявлено' : 'не заявлено'} / ${c.verified ? 'проверено' : 'не проверено'}`,
          c.verified ? 'ok' : c.advertised ? 'warn' : 'idle'));
      }
    } catch { out.textContent = ''; out.appendChild(h('span.xsmall.dim', 'нет данных')); }
  }
  await loadCaps();

  return h('div.card', { style: { padding: '10px 12px' } },
    h('div.row',
      h('div', { style: { flex: '1', minWidth: 0 } }, h('b.small', pick(m, ['alias'], '')), h('span.xsmall.dim', ` · ${pick(m, ['name'], '')}`)),
      statusBadge(m.status || 'unknown'),
      actionButton('Проверить', async () => {
        try { await api.raw(`/api/openrouter/models/${encodeURIComponent(id)}/probe`, { method: 'POST' }); toastOk('Проверка выполнена'); await loadCaps(); }
        catch (e) { toastError(e, 'Проверка не удалась'); }
      }, { cls: 'btn btn-sm', iconName: 'bolt' })),
    out);
}

export default OpenRouterPage;
