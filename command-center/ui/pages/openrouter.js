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
import { panel, pageHead, errorBanner, emptyPanel } from './_shared.js';

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

    const head = pageHead('OpenRouter', 'Каталог удалённых моделей — синхронизация, pin в реестр, live-пробы возможностей');
    if (err) return h('div.stack.lg', head, errorBanner(err, ctx));

    if (!providers.length) {
      return h('div.stack.lg', head, emptyPanel({
        iconName: 'models', title: 'Провайдеров нет',
        hint: 'Добавьте провайдера OpenRouter (openai_compat, base_url https://openrouter.ai/api/v1, ключ) на странице «Модели», затем вернитесь сюда.',
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

    const providerRow = h('div.row', field('Провайдер', providerEl), h('div.spacer'),
      actionButton('Sync', async () => {
        try {
          const r = await api.raw(`/api/openrouter/${encodeURIComponent(state.providerId)}/sync`, { method: 'POST' });
          toastOk(`Каталог синхронизирован: ${r.synced} моделей`);
          ctx.refresh();
        } catch (e) { toastError(e, 'Синхронизация не удалась — проверьте API-ключ провайдера'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'retry' }));

    const catalogPanel = await buildCatalogPanel(state.providerId, ctx);
    const pinnedPanel = await buildPinnedPanel(state.providerId, ctx);

    return h('div.stack.lg', head, providerRow, catalogPanel, pinnedPanel);
  },

  onEvent(ev) { return ev.kind === 'model.created'; },
};

async function buildCatalogPanel(providerId, ctx) {
  const searchEl = input({ placeholder: 'поиск по имени/remote_id…' });
  const tableOut = h('div.small.dim', 'Загрузка каталога…');

  async function loadCatalog(q) {
    tableOut.textContent = '';
    tableOut.appendChild(h('div.small.dim', 'Загрузка…'));
    try {
      const rows = await api.raw(`/api/openrouter/${encodeURIComponent(providerId)}/catalog${q ? `?q=${encodeURIComponent(q)}` : ''}`);
      tableOut.textContent = '';
      if (!rows.length) { tableOut.appendChild(h('div.small.dim', 'Каталог пуст — нажмите «Sync».')); return; }
      tableOut.appendChild(h('div.stack.sm', { style: { overflowX: 'auto' } }, rows.map((m) => catalogRow(m, providerId, ctx))));
    } catch (e) { tableOut.textContent = ''; tableOut.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось загрузить каталог')); }
  }

  const debouncedLoad = debounce((q) => loadCatalog(q), 250);
  searchEl.addEventListener('input', () => debouncedLoad(searchEl.value.trim()));
  await loadCatalog('');

  return panel('Каталог OpenRouter', h('div.stack.sm', field('Поиск', searchEl), tableOut));
}

function catalogRow(m, providerId, ctx) {
  const mods = [...(m.input_modalities || [])].join('/') || 'text';
  return h('div.card', { style: { padding: '10px 12px' } },
    h('div.row',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.mono.small', m.remote_id), h('div.xsmall.dim', m.display_name || '')),
      m.stale ? badge('stale', 'idle') : null,
      actionButton('Pin', () => doPin(m, providerId, ctx), { cls: 'btn btn-sm btn-primary', iconName: 'plus' })),
    h('div.row.tight',
      badge(`ctx ${fmtContext(m.context_window)}`),
      badge(`in ${fmtCost(m.price_in)}/1M`), badge(`out ${fmtCost(m.price_out)}/1M`),
      badge(mods),
      (m.supported_parameters || []).length ? badge(`${m.supported_parameters.length} params`) : null));
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
    return emptyPanel({ iconName: 'models', title: 'Закреплённых моделей нет', hint: 'Нажмите «Pin» у модели в каталоге выше — она появится здесь и в общем реестре моделей.' });
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
        out.appendChild(badge(`${c.capability}: ${c.advertised ? 'заявлено' : 'не заявлено'} / ${c.verified ? 'подтверждено' : 'не подтверждено'}`,
          c.verified ? 'ok' : c.advertised ? 'warn' : 'idle'));
      }
    } catch { out.textContent = ''; out.appendChild(h('span.xsmall.dim', 'нет данных')); }
  }
  await loadCaps();

  return h('div.card', { style: { padding: '10px 12px' } },
    h('div.row',
      h('div', { style: { flex: '1', minWidth: 0 } }, h('b.small', pick(m, ['alias'], '')), h('span.xsmall.dim', ` · ${pick(m, ['name'], '')}`)),
      statusBadge(m.status || 'unknown'),
      actionButton('Probe', async () => {
        try { await api.raw(`/api/openrouter/models/${encodeURIComponent(id)}/probe`, { method: 'POST' }); toastOk('Пробы выполнены'); await loadCaps(); }
        catch (e) { toastError(e, 'Пробы не удались'); }
      }, { cls: 'btn btn-sm', iconName: 'bolt' })),
    out);
}

export default OpenRouterPage;
