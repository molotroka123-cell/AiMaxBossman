/* ============================================================
   openrouter.js — Feature 02/04: OpenRouter как first-class провайдер.
   Endpoints: GET  /api/openrouter/provider  (кто здесь OpenRouter),
   POST /api/openrouter/connect (ключ → провайдер → каталог),
   POST /api/openrouter/{pid}/sync, GET /api/openrouter/{pid}/catalog,
   POST /api/openrouter/{pid}/pin, POST /api/openrouter/models/{mid}/probe,
   GET /api/openrouter/models/{mid}/capabilities.

   AF-03. Раньше страница выбирала поставщика сама: панель ключа показывалась
   только на ПУСТОЙ базе, а если в базе был кто угодно другой (например, одна
   Ollama), выбор падал на `providers[0]`, и ключ OpenRouter уезжал в чужую
   строку. Наличие строки — не identity. Теперь страница не угадывает: она
   спрашивает сервер, какой провайдер здесь OpenRouter, и связывается ровно с
   этим id. Нет такого — показывается Connect, и ключ уходит на ручку без id,
   которая сама заводит своего поставщика. Чужой ключ и адрес не трогаются.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, badge, debounce, statusBadge,
  toastOk, toastError, actionButton, field, input,
  fmtContext, fmtCost,
} from '../components.js';
import { panel, pageHead, errorNote, blank } from './_ui.js';

/* Ручка identity и ручка ключа. Обе — БЕЗ provider_id: промахнуться мимо
   OpenRouter нечем, потому что подставлять в адрес нечего. */
export const IDENTITY_ENDPOINT = '/api/openrouter/provider';
export const KEY_ENDPOINT = '/api/openrouter/connect';

/**
 * Связка страницы с провайдером. Единственный источник — ответ сервера.
 *
 * Никакой эвристики по имени/адресу: подстрока «openrouter» делает своим любой
 * чужой прокси, а первый элемент списка — вообще случайного поставщика.
 * Всё, что не является явным «connected + пригодный id», ведёт в Connect,
 * а не в связку: не удалось определить identity — отказываемся, а не гадаем.
 */
export function providerBinding(identity, { error = null } = {}) {
  const total = Number(identity && identity.providers_total);
  const others = Number.isFinite(total) && total >= 0 ? total : null;
  if (error) return { mode: 'connect', providerId: null, reason: 'identity-unavailable', others };
  if (!identity || typeof identity !== 'object' || Array.isArray(identity)) {
    return { mode: 'connect', providerId: null, reason: 'identity-unreadable', others };
  }
  if (identity.connected !== true) {
    return { mode: 'connect', providerId: null, reason: 'openrouter-absent', others };
  }
  const raw = identity.provider_id;
  const id = typeof raw === 'number' || typeof raw === 'string' ? Number(raw) : NaN;
  if (!Number.isInteger(id) || id <= 0) {
    return { mode: 'connect', providerId: null, reason: 'identity-unusable', others };
  }
  return {
    mode: 'bound',
    providerId: String(id),
    reason: 'canonical',
    others,
    name: identity.name || 'OpenRouter',
    baseUrl: identity.base_url || '',
    hasKey: identity.has_key === true,
  };
}

/**
 * Куда уходит ВВЕДЁННЫЙ ключ. Адрес постоянный и не содержит provider_id:
 * связку с поставщиком делает сервер по своей канонической identity.
 * Ключ едет только в теле POST — не в URL, не в localStorage, не в лог.
 */
export function connectRequest(apiKey) {
  const key = String(apiKey == null ? '' : apiKey).trim();
  if (!key) return { ok: false, reason: 'empty-key' };
  return { ok: true, method: 'POST', path: KEY_ENDPOINT, body: { api_key: key } };
}

/** Повторное подключение БЕЗ ключа возможно только у связанного провайдера. */
export function refreshRequest(binding) {
  if (!binding || binding.mode !== 'bound' || !binding.providerId) return null;
  return { method: 'POST', path: `/api/openrouter/${encodeURIComponent(binding.providerId)}/connect` };
}

const OpenRouterPage = {
  id: 'openrouter',
  title: 'OpenRouter',
  icon: 'models',
  nav: 'more',
  section: 'brains',

  async render(ctx) {
    const state = ctx.state.openrouter || (ctx.state.openrouter = {});
    const head = pageHead('OpenRouter', 'Каталог облачных моделей OpenRouter: обновить список, закрепить нужные и проверить их возможности.');

    let identity = null; let identityError = null;
    try { identity = await api.raw(IDENTITY_ENDPOINT); } catch (e) { identityError = e; }
    const binding = providerBinding(identity, { error: identityError });
    // id живёт ровно столько, сколько его подтверждает сервер: залежавшийся
    // в состоянии страницы id — это тот же выбор наугад, только отложенный.
    state.providerId = binding.providerId;

    if (binding.mode !== 'bound') {
      return h('div.bx-page', head, buildKeyPanel(ctx, binding, identityError),
        h('div.xsmall.dim', 'Ключ хранится зашифрованным вместе с остальными поставщиками; '
          + 'подключение не запускает платных вызовов — только проверка ключа и список моделей.'));
    }

    const providerRow = h('div.row',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.small', h('b', binding.name), ' · поставщик OpenRouter этой установки'),
        h('div.xsmall.dim.mono', binding.baseUrl)),
      actionButton('Обновить список', async () => {
        try {
          const r = await api.raw(`/api/openrouter/${encodeURIComponent(binding.providerId)}/sync?force=true`, { method: 'POST' });
          state.catalogError = null;
          toastOk(r.cached ? 'Открыт сохранённый каталог' : `Список обновлён: ${r.synced} моделей`);
          ctx.refresh();
        } catch (e) {
          // Причина живёт до следующей попытки: тост исчезает, а пустой список
          // без объяснения остаётся на экране и выглядит как «моделей нет».
          state.catalogError = e.message || 'каталог не загрузился';
          state.catalogHint = e.hint || '';
          toastError(e, 'Каталог не обновился — показан сохранённый список');
          ctx.refresh();
        }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'retry' }));

    const connectPanel = await buildConnectPanel(binding, ctx);
    const catalogPanel = await buildCatalogPanel(binding.providerId, ctx);
    const pinnedPanel = await buildPinnedPanel(binding.providerId, ctx);

    return h('div.bx-page', head, providerRow, connectPanel, catalogPanel, pinnedPanel);
  },

  onEvent(ev) { return ev.kind === 'model.created'; },
};

/** Честная строка про то, ПОЧЕМУ страница просит ключ, без выдумывания чисел. */
function absenceNote(binding, identityError) {
  if (identityError) {
    return h('div.small', { style: { color: 'var(--err)' } },
      'Не удалось узнать, какой поставщик здесь OpenRouter: '
      + (identityError.message || 'ручка identity недоступна'),
      h('div.xsmall.dim', 'Пока это неизвестно, страница не связывается ни с одним поставщиком; '
        + 'Connect заведёт своего и чужих не тронет.'));
  }
  if (binding.others === null) return null;
  if (binding.others === 0) return h('div.xsmall.dim', 'Поставщиков пока нет.');
  return h('div.xsmall.dim',
    `Поставщиков в базе: ${binding.others}, OpenRouter среди них нет. `
    + 'Connect заведёт отдельного поставщика — ключ и адрес остальных не меняются.');
}

function buildKeyPanel(ctx, binding, identityError) {
  const state = ctx.state.openrouter || (ctx.state.openrouter = {});
  const keyEl = input({ placeholder: 'sk-or-… ключ OpenRouter', type: 'password' });
  const out = h('div.stack.sm', field('API KEY', keyEl),
    h('div.xsmall.dim', 'Вставьте ключ с openrouter.ai/keys — поставщик будет создан, ключ проверен, каталог загружен.'));
  const why = absenceNote(binding, identityError);
  if (why) out.appendChild(why);
  if (state.catalogError) out.appendChild(reasonNote(state));
  // Кнопка называется как на панели поставщика: «Connect» — одно действие, одно
  // слово на всю страницу. Глагол «Подключить…» здесь ещё и читался бы как
  // открывашка диалога (общая договорённость страниц UI), которой он не является.
  const button = actionButton('Connect', async () => {
    const req = connectRequest(keyEl.value);
    if (!req.ok) { toastError({ message: 'Вставьте ключ', hint: 'без ключа подключаться нечем' }); return; }
    button.disabled = true;                 // двойной клик не создаёт второго поставщика
    try {
      const r = await api.raw(req.path, { method: req.method, body: req.body });
      keyEl.value = '';                     // ключ не остаётся в DOM после отправки
      state.catalogError = r.catalog_error || null;
      state.catalogHint = r.catalog_hint || '';
      if (r.catalog_error) toastError({ message: r.catalog_error, hint: r.catalog_hint }, 'Ключ принят, каталог не загрузился');
      else toastOk(`Подключено: ${r.models} моделей в каталоге`);
      ctx.refresh();
    } catch (e) {
      state.catalogError = e.message || 'Подключение не удалось';
      state.catalogHint = e.hint || '';
      toastError(e, 'Подключение не удалось — проверьте ключ');
      ctx.refresh();
    } finally { button.disabled = false; }
  }, { cls: 'btn btn-primary', iconName: 'bolt' });
  out.appendChild(button);
  return panel('Подключение OpenRouter', out);
}

async function buildConnectPanel(binding, ctx) {
  const state = ctx.state.openrouter || (ctx.state.openrouter = {});
  let st = null;
  try { st = await api.raw(`/api/openrouter/${encodeURIComponent(binding.providerId)}/status`); }
  catch (e) {
    st = { has_key: binding.hasKey, catalog_models: 0, last_synced_at: null };
    state.catalogError = state.catalogError || e.message || 'состояние подключения недоступно';
  }

  const out = h('div.stack.sm');
  const when = st.last_synced_at ? new Date(st.last_synced_at).toLocaleString() : null;
  if (st.has_key && st.catalog_models) {
    out.appendChild(h('div.row.tight',
      badge(st.has_key ? 'ключ сохранён' : 'ключа нет', st.has_key ? 'ok' : 'warn'),
      badge(`моделей в каталоге: ${st.catalog_models}`),
      when ? badge(`sync: ${when}`) : null));
    if (state.catalogError) out.appendChild(reasonNote(state));
    return panel('Подключение', out);
  }

  const keyEl = input({ placeholder: 'sk-or-… ключ OpenRouter', type: 'password' });
  const note = h('div.xsmall.dim', when ? `Ключ сохранён, но каталог пуст — последний sync: ${when}` : 'Вставьте ключ и нажмите Connect — каталог загрузится автоматически.');
  out.append(field('API KEY', keyEl), note);
  // Пустой список без причины — главная жалоба владельца: ключ принят,
  // моделей нет, и непонятно, ключ ли виноват, адрес или сеть.
  if (state.catalogError) out.appendChild(reasonNote(state));
  out.appendChild(
    actionButton('Connect', async () => {
      // Ключ введён — уходит на ручку без id (её identity решает сервер).
      // Ключа нет — переподключаем ровно связанного провайдера.
      const typed = connectRequest(keyEl.value);
      const req = typed.ok ? typed : refreshRequest(binding);
      if (!req) { toastError({ message: 'Поставщик OpenRouter не определён', hint: 'вставьте ключ' }); return; }
      try {
        const r = await api.raw(req.path, { method: req.method, body: req.body });
        keyEl.value = '';
        state.catalogError = r.catalog_error || null;
        state.catalogHint = r.catalog_hint || '';
        if (r.catalog_error) toastError({ message: r.catalog_error, hint: r.catalog_hint }, 'Ключ принят, каталог не загрузился');
        else toastOk(`Подключено: ${r.models} моделей в каталоге`);
        ctx.refresh();
      } catch (e) {
        state.catalogError = e.message || 'Connect не удался';
        state.catalogHint = e.hint || '';
        toastError(e, 'Connect не удался — проверьте ключ');
        ctx.refresh();
      }
    }, { cls: 'btn btn-primary', iconName: 'bolt' }));
  return panel('Подключение', out);
}

function reasonNote(state) {
  return h('div.small', { style: { color: 'var(--err)' } },
    state.catalogError, state.catalogHint ? h('div.xsmall.dim', state.catalogHint) : null);
}

const FILTERS = [
  { id: 'all', label: 'ALL', fn: () => true },
  { id: 'free', label: 'FREE', fn: (m) => !m.price_in && !m.price_out },
  { id: 'coding', label: 'CODING', fn: (m) => `${m.remote_id} ${m.display_name}`.toLowerCase().includes('code') },
  { id: 'vision', label: 'VISION', fn: (m) => (m.input_modalities || []).includes('image') },
  { id: 'tools', label: 'TOOLS', fn: (m) => (m.supported_parameters || []).includes('tools') },
];

const CATALOG_PAGE = 100;      // столько строк за один запрос (ручка режет на 200)

async function buildCatalogPanel(providerId, ctx) {
  const searchEl = input({ placeholder: 'поиск по всему каталогу (например, glm)…' });
  const tableOut = h('div.small.dim', 'Загрузка каталога…');
  const countOut = h('div.xsmall.dim');
  const moreRow = h('div.row.tight');
  const state = ctx.state.openrouter || (ctx.state.openrouter = {});
  if (!state.filter) state.filter = 'all';
  // Каталог OpenRouter — сотни моделей, и «z-ai/…» стоит в самом конце алфавита.
  // Поэтому страница ведёт СВОЙ счётчик загруженного и никогда не выдаёт часть
  // за целое: подпись всегда говорит, сколько из скольких сейчас на экране.
  let rows = [];
  let total = 0;
  let hasMore = false;
  let query = '';

  function renderRows() {
    const fn = (FILTERS.find((f) => f.id === state.filter) || FILTERS[0]).fn;
    const shown = rows.filter(fn);
    tableOut.textContent = '';
    countOut.textContent = '';
    moreRow.textContent = '';
    if (!shown.length) {
      const why = rows.length ? 'Под фильтр ничего не подошло — снимите фильтр или уточните поиск.'
        : (state.catalogError ? `Список пуст: ${state.catalogError}`
          : (query ? `По запросу «${query}» ничего не найдено во всём каталоге (${total === 0 ? 'моделей: 0' : `всего ${total}`}).`
            : 'Список пуст — нажмите «Обновить список».'));
      tableOut.appendChild(h('div.small.dim', why));
    } else {
      tableOut.appendChild(h('div.stack.sm', { style: { overflowX: 'auto' } }, shown.map((m) => catalogRow(m, providerId, ctx))));
    }
    const filtered = shown.length !== rows.length ? ` · под фильтр подошло ${shown.length}` : '';
    countOut.textContent = total
      ? `показано ${rows.length} из ${total}${query ? ` по запросу «${query}»` : ''}${filtered}`
      : '';
    if (hasMore) {
      moreRow.appendChild(actionButton(`Показать ещё ${Math.min(CATALOG_PAGE, total - rows.length)}`,
        () => loadCatalog(query, { append: true }), { cls: 'btn btn-sm', iconName: 'plus' }));
    }
  }

  const filterRow = h('div.row.tight', FILTERS.map((f) => {
    const b = h('button.btn.btn-sm' + (state.filter === f.id ? '.btn-primary' : ''), { type: 'button' }, f.label);
    b.addEventListener('click', () => { state.filter = f.id; ctx.refresh(); });
    return b;
  }));

  async function loadCatalog(q, { append = false } = {}) {
    // Смена запроса начинает выдачу заново: дозагрузка со старым offset дала бы
    // строки из другого списка.
    const offset = append ? rows.length : 0;
    if (!append) { tableOut.textContent = ''; tableOut.appendChild(h('div.small.dim', 'Загрузка…')); }
    try {
      const url = `/api/openrouter/${encodeURIComponent(providerId)}/catalog`
        + `?limit=${CATALOG_PAGE}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ''}`;
      const page = await api.raw(url);
      const items = listOf(page, 'items');
      rows = append ? rows.concat(items) : items;
      total = typeof page.total === 'number' ? page.total : rows.length;
      hasMore = Boolean(page.has_more);
      query = q || '';
      renderRows();
    } catch (e) {
      // Ошибка загрузки — это НЕ «моделей нет»: владелец должен видеть разницу.
      tableOut.textContent = '';
      countOut.textContent = '';
      moreRow.textContent = '';
      tableOut.appendChild(h('div.small', { style: { color: 'var(--err)' } },
        e.message || 'Не удалось загрузить каталог',
        e.hint ? h('div.xsmall.dim', e.hint) : null));
    }
  }

  const debouncedLoad = debounce((q) => loadCatalog(q), 250);
  searchEl.addEventListener('input', () => debouncedLoad(searchEl.value.trim()));
  await loadCatalog('');

  return panel('Каталог моделей OpenRouter',
    h('div.stack.sm', field('Поиск', searchEl), filterRow, countOut, tableOut, moreRow));
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
