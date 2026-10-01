/* ============================================================
   chat/format.js — подписи, группировка и расчёты для чата.

   Чистые функции без DOM (проверяются в node). Правило: нет данных —
   честное «—» или «неизвестно», а не ноль и не догадка.
   ============================================================ */

const DAY_MS = 24 * 60 * 60 * 1000;
const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

/** ISO / epoch → Date или null. ISO без зоны считается UTC (так пишет сервер). */
export function parseTime(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === 'number' && Number.isFinite(value)) return new Date(value > 1e11 ? value : value * 1000);
  let s = String(value).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseTime(Number(s));
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !/([zZ]|[+-]\d{2}:?\d{2})$/.test(s)) s = `${s.replace(' ', 'T')}Z`;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : new Date(t);
}

function startOfDay(d, shiftDays = 0) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + shiftDays).getTime();
}

export const GROUP_LABELS = {
  pinned: 'Закреплённые',
  today: 'Сегодня',
  yesterday: 'Вчера',
  week: 'На этой неделе',
  earlier: 'Ранее',
};

/** В какую группу боковой панели попадает момент времени (по местному календарю). */
export function dateBucket(value, now = new Date()) {
  const d = parseTime(value);
  if (!d) return 'earlier';
  const t = d.getTime();
  if (t >= startOfDay(now)) return 'today';
  if (t >= startOfDay(now, -1)) return 'yesterday';
  if (t >= startOfDay(now, -6)) return 'week';
  return 'earlier';
}

/**
 * Треды → группы [{id, label, items}] в порядке: закреплённые, сегодня,
 * вчера, на этой неделе, ранее. Пустые группы не возвращаются; порядок
 * внутри группы — как пришёл с сервера (он уже сортирует по updated_at).
 */
export function groupThreads(items, now = new Date()) {
  const order = ['pinned', 'today', 'yesterday', 'week', 'earlier'];
  const buckets = new Map(order.map((k) => [k, []]));
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || typeof item !== 'object') continue;
    const key = item.pinned ? 'pinned' : dateBucket(item.updated_at || item.created_at, now);
    buckets.get(key).push(item);
  }
  return order.filter((k) => buckets.get(k).length)
    .map((k) => ({ id: k, label: GROUP_LABELS[k], items: buckets.get(k) }));
}

function pad2(n) { return String(n).padStart(2, '0'); }

/** Короткое время у треда: сегодня/вчера — ЧЧ:ММ, неделя — день недели, раньше — ДД.ММ. */
export function threadTimeLabel(value, now = new Date()) {
  const d = parseTime(value);
  if (!d) return '';
  const bucket = dateBucket(d, now);
  if (bucket === 'today' || bucket === 'yesterday') return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  if (bucket === 'week') return WEEKDAYS[d.getDay()];
  const sameYear = d.getFullYear() === now.getFullYear();
  return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}${sameYear ? '' : `.${String(d.getFullYear()).slice(-2)}`}`;
}

export function clockLabel(value) {
  const d = parseTime(value);
  return d ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}` : '';
}

export function fmtBytes(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return '—';
  if (v < 1024) return `${v} Б`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(v < 10 * 1024 ? 1 : 0)} КБ`;
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} МБ`;
  return `${(v / 1024 / 1024 / 1024).toFixed(1)} ГБ`;
}

export function fmtTokens(n) {
  const v = Number(n);
  if (n === null || n === undefined || !Number.isFinite(v)) return '—';
  if (v < 1000) return String(Math.round(v));
  if (v < 10000) return `${(v / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  if (v < 1000000) return `${Math.round(v / 1000)}k`;
  return `${(v / 1000000).toFixed(1).replace(/\.0$/, '')}M`;
}

export function fmtDuration(ms) {
  const v = Number(ms);
  if (ms === null || ms === undefined || !Number.isFinite(v) || v < 0) return '';
  if (v < 1000) return `${Math.round(v)} мс`;
  const s = v / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} с`;
  const m = Math.floor(s / 60);
  return `${m} м ${Math.round(s - m * 60)} с`;
}

export function fmtClockTimer(ms) {
  const total = Math.max(0, Math.floor(Number(ms) / 1000) || 0);
  return `${pad2(Math.floor(total / 60))}:${pad2(total % 60)}`;
}

function num(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * Заполнение контекста. Занято — входные токены ПОСЛЕДНЕГО вызова модели
 * (step_tokens_in: это и есть размер контекста, который модель прочла);
 * если его нет — tokens_in последнего run.usage. Окно — из run.usage, иначе
 * у выбранной модели. До первого прогона — «—».
 */
export function contextMeter(usage, fallbackWindow = null) {
  const used = num(usage && usage.step_tokens_in) ?? num(usage && usage.tokens_in);
  const window = num(usage && usage.context_window) ?? num(fallbackWindow);
  const winLabel = window ? fmtTokens(window) : '?';
  if (used === null) {
    return { used: null, window, ratio: null, label: window ? `— / ${winLabel}` : '—' };
  }
  const ratio = window ? Math.max(0, Math.min(1, used / window)) : null;
  return { used, window, ratio, label: `${fmtTokens(used)} / ${winLabel}` };
}

/** Скорость генерации: только измеренная сервером (gen_tps). Иначе «—». */
export function tpsLabel(usage) {
  const v = num(usage && usage.gen_tps);
  return v === null ? '— tok/s' : `${v >= 100 ? Math.round(v) : v.toFixed(1)} tok/s`;
}

/**
 * Задержка до первого токена. Серверная ttft_ms, если есть; иначе замер
 * клиента от отправки до первой дельты (с пометкой: это ещё и очередь).
 */
export function latencyLabel(usage, clientTtftMs = null) {
  const v = num(usage && usage.ttft_ms);
  if (v !== null) return { text: `${(v / 1000).toFixed(1)} s TTFT`, source: 'server' };
  const c = num(clientTtftMs);
  if (c !== null) return { text: `${(c / 1000).toFixed(1)} s до 1-го токена`, source: 'client' };
  return { text: '— latency', source: null };
}

const ON_DEVICE = new Set(['local']);

export function isOnDevice(locality) {
  return ON_DEVICE.has(String(locality || ''));
}

/** Стоимость хода: локально — $0.00 local; облако — реальная сумма или «цена неизвестна». */
export function costLabel(usage, route = {}) {
  const locality = (usage && usage.locality) || route.locality || null;
  const billing = (usage && usage.billing) || route.billing || null;
  if (billing === 'local' || (billing === null && isOnDevice(locality))) return '$0.00 local';
  if (!usage) return '—';
  const cost = num(usage.cost_usd);
  if (usage.pricing_known === false) return 'цена неизвестна';
  if (cost === null) return usage.pricing_known === true ? '—' : 'цена неизвестна';
  return `$${cost < 0.01 && cost > 0 ? cost.toFixed(4) : cost.toFixed(2)}`;
}

export function localityBadge(locality) {
  switch (String(locality || '')) {
    case 'local': return { text: 'LOCAL', tone: 'local', title: 'Модель работает на этом компьютере' };
    case 'local_proxy': return { text: 'LOCAL PROXY', tone: 'lan', title: 'Локальный прокси: проверьте, куда он пересылает запросы' };
    case 'lan': return { text: 'LAN', tone: 'lan', title: 'Модель на другой машине вашей сети' };
    case 'cloud': return { text: 'CLOUD', tone: 'cloud', title: 'Запрос уходит облачному провайдеру' };
    default: return null;
  }
}

export function billingBadge(billing) {
  switch (String(billing || '')) {
    case 'local': return { text: 'локально', tone: 'local' };
    case 'free_cloud': return { text: 'бесплатно', tone: 'free' };
    case 'paid_capped': return { text: 'платно · лимит', tone: 'paid' };
    case 'paid': return { text: 'платно', tone: 'paid' };
    case 'blocked': return { text: 'запрещено', tone: 'blocked' };
    case 'unknown_price': return { text: 'цена неизвестна', tone: 'blocked' };
    default: return null;
  }
}

/** Нижняя карточка боковой панели: реальный режим маршрута, без обещаний сверх него. */
export function routeCard(route) {
  if (!route || route.mode === 'auto') {
    const last = route && route.last;
    const lastBadge = last ? localityBadge(last.locality) : null;
    return {
      tone: last && isOnDevice(last.locality) ? 'local' : 'auto',
      title: 'Auto · Local-first',
      sub: last && last.alias
        ? `Последний ответ: ${last.alias}${lastBadge ? ` · ${lastBadge.text}` : ''}`
        : 'Маршрут выбирается при отправке',
    };
  }
  if (route.mode === 'rave') {
    return { tone: 'cloud', title: 'Agentic Rave', sub: 'Агенты работают в изолированных копиях проекта' };
  }
  const loc = String(route.locality || '');
  if (loc === 'local') return { tone: 'local', title: 'Локальный режим', sub: 'Все данные на этом устройстве' };
  if (loc === 'lan') return { tone: 'lan', title: 'Сеть (LAN)', sub: 'Запросы уходят на машину в вашей сети' };
  if (loc === 'local_proxy') return { tone: 'lan', title: 'Локальный прокси', sub: 'Данные могут уйти дальше прокси' };
  if (loc === 'cloud') {
    return { tone: 'cloud', title: 'Облачный режим', sub: `Запрос и контекст уходят провайдеру${route.provider ? ` ${route.provider}` : ''}` };
  }
  return { tone: 'auto', title: route.alias || 'Модель', sub: 'Где работает модель — неизвестно' };
}

export function isLoopbackHost(hostname) {
  const h = String(hostname || '').toLowerCase();
  return h === 'localhost' || h === '::1' || h === '[::1]' || /^127(\.\d{1,3}){3}$/.test(h);
}

function groupOf(billing, locality) {
  if (billing === 'local') return 'local';
  if (billing === 'free_cloud') return 'free';
  if (billing === 'paid_capped' || billing === 'paid') return 'paid';
  if (!billing && (locality === 'local' || locality === 'lan' || locality === 'local_proxy')) return 'local';
  return 'other';
}

export const PICKER_GROUPS = [
  { id: 'local', label: 'Локальные' },
  { id: 'free', label: 'Облако (бесплатно)' },
  { id: 'paid', label: 'Облако (платно, с лимитом)' },
  { id: 'other', label: 'Недоступно или цена неизвестна' },
];

/**
 * Список выбора исполнителя: агенты из /api/chat/options, обогащённые
 * тарификацией модели из /api/models/picker (если он есть). Выбирается
 * агент: отдельной модели на одно сообщение сервер не принимает.
 */
export function buildPickerModel(options, picker) {
  const byModel = new Map();
  for (const m of (picker && Array.isArray(picker.items)) ? picker.items : []) {
    if (m && m.id !== undefined && m.id !== null) byModel.set(String(m.id), m);
  }
  const agents = (options && Array.isArray(options.agents)) ? options.agents : [];
  const entries = [];
  for (const a of agents) {
    if (!a || a.enabled === false) continue;
    const m = a.model || null;
    const pm = m && m.id !== undefined && m.id !== null ? byModel.get(String(m.id)) : null;
    /* locality_detail различает local / local_proxy / lan; грубое locality — только local|cloud */
    const locality = (pm && pm.locality) || (m && (m.locality_detail || m.locality))
      || (m && m.kind === 'local' ? 'local' : m && m.kind === 'cloud' ? 'cloud' : null);
    let billing = (pm && pm.billing) || (m && m.billing) || null;
    if (!billing && m) {
      if (locality === 'local') billing = 'local';
      else if (m.free === true) billing = 'free_cloud';
      else if (m.free === false) billing = 'paid';
      else if (locality === 'cloud') billing = 'unknown_price';
    }
    const usable = m ? ((pm || m).usable !== false) : false;
    entries.push({
      agentId: a.id,
      name: String(a.name || `агент ${a.id}`),
      role: a.role || '',
      modelId: m ? m.id : null,
      alias: (pm && (pm.alias || pm.name)) || (m && (m.alias || m.name)) || '—',
      provider: (pm && pm.provider_name) || '',
      locality,
      billing,
      usable,
      refusal: !m ? 'у агента не выбрана модель' : (pm && pm.refusal) || m.refusal || '',
      contextWindow: (pm && pm.context_window) ?? (m && m.context_window) ?? null,
      status: (pm && pm.status) || (m && m.status) || '',
      health: (pm && pm.health) || (m && m.health) || '',
      group: groupOf(billing, locality),
    });
  }
  const groups = PICKER_GROUPS.map((g) => ({ ...g, items: entries.filter((e) => e.group === g.id) }))
    .filter((g) => g.items.length);
  const subs = new Map();
  for (const s of [...((picker && picker.subscriptions) || []), ...((options && options.subscriptions) || [])]) {
    if (!s || !s.name) continue;
    subs.set(s.name, { ...(subs.get(s.name) || {}), ...Object.fromEntries(Object.entries(s).filter(([, v]) => v !== null && v !== undefined)) });
  }
  const auto = (options && options.auto) || (picker && picker.auto) || { label: 'Auto · Local-first', hint: '' };
  return { auto, entries, groups, subscriptions: [...subs.values()] };
}

/**
 * Место работы и тарификация модели по её псевдониму из списка выбора. Нужно ходам, у которых нет
 * плана «до отправки» (тред открыт заново, F5, ход из CMD): сервер в run.usage места не присылает,
 * а без него локальная модель выглядела бы «ценой неизвестна» и без значка LOCAL.
 * Неизвестный, пустой или двусмысленный псевдоним — null: не угадываем LOCAL.
 */
export function placeOfAlias(pickerModel, alias) {
  const a = String(alias || '').trim();
  if (!a || !pickerModel || !Array.isArray(pickerModel.entries)) return null;
  const hits = pickerModel.entries.filter((e) => e.alias === a);
  if (!hits.length) return null;
  const places = new Set(hits.map((e) => `${e.locality || ''}|${e.billing || ''}`));
  if (places.size !== 1) return null;
  const { locality, billing } = hits[0];
  if (!locality) return null;
  return { locality, billing: billing || null };
}

/** Первое предложение пояснения сервера, не длиннее 170 знаков: для строки в меню. */
function firstSentence(text) {
  const t = String(text || '').trim();
  if (!t) return '';
  const cut = t.indexOf('. ');
  const one = cut > 0 ? t.slice(0, cut) : t;
  return one.length > 170 ? `${one.slice(0, 167)}…` : one;
}

const OUTDATED_CLAUDE = 'CLI устарел: обновите Claude Code';

export function subscriptionState(sub) {
  if (!sub) return { text: 'нет данных', ok: false };
  if (sub.available === false) return { text: 'CLI не найден', ok: false };
  /* старый Claude Code CLI не запустит рейв без оператора: агент недоступен, причина — текстом сервера */
  if (sub.version_ok === false) return { text: firstSentence(sub.version_problem) || OUTDATED_CLAUDE, ok: false };
  if (sub.logged_in === true) return { text: 'вход выполнен', ok: true };
  if (sub.logged_in === false) return { text: 'вход не выполнен', ok: false };
  return { text: 'состояние входа неизвестно', ok: false };
}

/** Агенты для POST /api/rave из состояния коннекторов (/api/rave/connectors). */
export function raveAgentSpecs(connectors, pick = {}) {
  const out = [];
  const c = connectors || {};
  /* локальный агент без @модели работает на модели коннектора по умолчанию (default_model) */
  if (pick.local && c.local) out.push('local:local');
  /* version_ok === false — старый Claude Code CLI; нет поля (старый сервер) — не запрещаем */
  if (pick.claude && c.claude && c.claude.logged_in && c.claude.version_ok !== false) out.push('claude:c');
  if (pick.codex && c.codex && c.codex.logged_in) out.push('codex:x');
  return out;
}

/** Почему выбранный агент подписки не попал в рейв: по строке на каждого выбранного и неготового. */
export function raveSkipReasons(connectors, pick = {}) {
  const c = connectors || {};
  const out = [];
  if (pick.claude) {
    const k = c.claude;
    if (!k) out.push('Claude: нет данных о подписке.');
    else if (k.version_ok === false) out.push(`Claude: ${firstSentence(k.version_problem) || OUTDATED_CLAUDE}.`);
    else if (!k.logged_in) out.push('Claude: вход не выполнен.');
  }
  if (pick.codex) {
    const x = c.codex;
    if (!x) out.push('Codex: нет данных о подписке.');
    else if (!x.logged_in) out.push('Codex: вход не выполнен.');
  }
  return out;
}

/** Экспорт треда в Markdown из настоящих данных (заголовок, ходы, ответы, ошибки). */
export function exportMarkdown(thread, turns) {
  const title = String((thread && thread.title) || 'Чат Bossman');
  const lines = [`# ${title}`, ''];
  if (thread && thread.project) lines.push(`Проект: ${thread.project}`, '');
  for (const t of Array.isArray(turns) ? turns : []) {
    const when = parseTime(t.at);
    lines.push(`## Владелец${when ? ` · ${when.toISOString().slice(0, 16).replace('T', ' ')} UTC` : ''}`, '', String(t.text || ''), '');
    const meta = [t.model ? `модель ${t.model}` : '', t.status ? `статус ${t.status}` : '', t.taskId ? `задача #${t.taskId}` : '']
      .filter(Boolean).join(' · ');
    lines.push(`## Bossman${meta ? ` (${meta})` : ''}`, '');
    if (t.answer) lines.push(String(t.answer), '');
    if (t.error) lines.push(`> Ошибка: ${String(t.error).replace(/\n/g, '\n> ')}`, '');
    if (!t.answer && !t.error) lines.push('_(ответа нет)_', '');
  }
  return `${lines.join('\n').replace(/\n+$/, '')}\n`;
}

/** Имя файла экспорта: без запрещённых в Windows символов. */
export function exportFileName(title, now = new Date()) {
  const base = String(title || 'chat').replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ').trim().slice(0, 60) || 'chat';
  return `${base} ${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}.md`;
}

/** Идентификатор повтора отправки: 8..128 символов [A-Za-z0-9._:-]. */
export function clientRequestId(random = Math.random, now = Date.now()) {
  const tail = Math.floor(random() * 0xffffffff).toString(36);
  return `web-${now.toString(36)}-${tail}`.slice(0, 128);
}

/* Рейв жив, пока сервер считает его running/paused или хоть один агент ещё работает.
   Итог сервера 'partial' (часть агентов упала, заблокирована или ждёт разрешения) —
   конец, как done/stopped: карточка не опрашивает сервер вечно и не держит STOP. */
const RAVE_LIVE = new Set(['starting', 'running', 'paused']);
const RAVE_AGENT_LIVE = new Set(['queued', 'running', 'pausing', 'stopping']);

/** Карточка рейва ещё идёт (опрашивать, показывать STOP)? card: {id, status, data:{agents}}. */
export function raveIsLive(card) {
  if (!card || !card.id) return false;
  if (RAVE_LIVE.has(card.status)) return true;
  const agents = card.data && Array.isArray(card.data.agents) ? card.data.agents : [];
  return agents.some((a) => Boolean(a) && (a.live === true || RAVE_AGENT_LIVE.has(a.status)));
}

/** Итог проверки хода словами: серверные коды PASS/FAIL/NOT_APPLICABLE владельцу ничего не говорят. */
const VERDICT_WORDS = { PASS: 'пройдена', FAIL: 'не пройдена', NOT_APPLICABLE: 'не требуется' };
export function verdictLabel(verdict) {
  const code = String(verdict || '').trim().toUpperCase();
  return VERDICT_WORDS[code] || (verdict ? String(verdict) : '—');
}

export function backoffDelay(attempt, random = Math.random) {
  const base = Math.min(8000, 500 * Math.pow(2, Math.max(0, attempt - 1)));
  const jitter = 0.8 + random() * 0.4;
  return Math.round(Math.min(8000, base * jitter));
}

export { DAY_MS };
