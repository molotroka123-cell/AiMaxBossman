"use strict";
/* Bossman War & Procurement Terminal UI. Vanilla JS, no network except same-origin /api/v1. All API strings go through textContent. */
const API = "/api/v1";
const ROLES = ["viewer", "analyst", "compliance", "owner"];
const SECTIONS = [
  ["overview", T("Карта")], ["conflicts", T("Конфликты")], ["tenders", T("Закупки")], ["contacts", T("Контакты")], ["fit", T("Компания")], ["compliance", T("Комплаенс")],
  ["finance", T("Финансы")], ["fx", T("Курсы / FX")], ["deals", T("Сделки")], ["learning", T("Аналитика")], ["sources", T("Источники")], ["approvals", T("Одобрения")],
];
const S = { token: "", role: "viewer", view: "overview", tenders: null, sel: null, filters: { f: "all", q: "", category: "", country: "", source: "" },
  lastAssessment: {}, health: null, cache: {}, mapLayer: "conflict", mapSel: null, mapApi: null };
try { S.token = sessionStorage.getItem("wt_token") || localStorage.getItem("wt_token_saved") || ""; S.role = sessionStorage.getItem("wt_role") || localStorage.getItem("wt_role_saved") || "viewer"; const v = sessionStorage.getItem("wt_view"); if (v && SECTIONS.some((x) => x[0] === v)) S.view = v; } catch (e) { /* storage may be blocked */ }

/* ---------- DOM helpers ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}
const $ = (s) => document.querySelector(s);
const txt = (v) => (v == null || v === "" ? "—" : String(v));
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function badge(text, cls) { return h("span", { class: "badge " + (cls || "b-grey") }, txt(text)); }
const STATUS_CLS = { PASS: "b-ok", BLOCKED: "b-bad", REVIEW_REQUIRED: "b-info", EASY: "b-ok", MEDIUM: "b-warn", HARD: "b-bad", LIVE: "b-ok",
  DISABLED: "b-grey", NOT_RUN: "b-grey", NOT_CONNECTED: "b-warn", ERROR: "b-bad", NO_DATA: "b-grey", MISSING: "b-bad", DECLARED: "b-ok", INFO: "b-info", OK: "b-ok", WITHIN_LIMIT: "b-ok", MATCHED_DEAL: "b-warn", OVER_LIMIT: "b-bad",
  pending: "b-warn", approved: "b-ok", rejected: "b-bad" };
/* Display labels only: enum values sent to / received from the API stay unchanged. */
const STATUS_RU = { PASS: T("пройдено"), BLOCKED: T("заблокировано"), REVIEW_REQUIRED: T("нужна проверка"), EASY: T("лёгкий"), MEDIUM: T("средний"), HARD: T("сложный"),
  LIVE: T("живые данные"), DISABLED: T("отключён"), NOT_RUN: T("не запускался"), NOT_CONNECTED: T("не подключён"), ERROR: T("недоступен"), NO_DATA: T("нет данных"), MISSING: T("не заявлено"), DECLARED: T("заявлено"), INFO: T("справочно"), OFFLINE: T("источники выключены"), OK: "OK", WITHIN_LIMIT: T("в пределах лимита"),
  MATCHED_DEAL: T("сделка состыкована"), OVER_LIMIT: T("сверх лимита"), CLEAR: T("чисто"), POSSIBLE_MATCH: T("возможное совпадение"), MATCH: T("совпадение"), UNKNOWN: T("неизвестно"),
  PROPOSAL: T("предложение"), INSUFFICIENT_DATA: T("мало данных"), TODO: T("сделать"), REQUIRED: T("обязательно"),
  pending: T("ожидает"), approved: T("одобрено"), rejected: T("отклонено"), approval_required: T("нужно одобрение"), cleared: T("снято"),
  open: T("открыт"), closed: T("закрыт"), cancelled: T("отменён"), awarded: T("присуждён"), active: T("активен"), planned: T("запланирован") };
const ru = (s) => (s != null && STATUS_RU[s] != null ? STATUS_RU[s] : s);
const sb = (s) => badge(ru(s), STATUS_CLS[s]);
const yn = (v) => (v === true ? T("да") : v === false ? T("нет") : txt(v));
const ROLE_RU = { viewer: T("наблюдатель"), analyst: T("аналитик"), compliance: T("комплаенс"), owner: T("владелец") };
const WM_RU = { "OFFLINE / SOURCES OFF": T("ИСТОЧНИКИ ВЫКЛЮЧЕНЫ") };
/* Russian labels for form fields and result keys; the field name sent to the API stays the key. */
const LBL = { name: T("Название компании"), country: T("Страна"), registered: T("Компания зарегистрирована"), vat_id: T("VAT / ИНН"), sam_uei: "SAM UEI",
  cpv_capabilities: T("Коды CPV компании"), certificates: T("Сертификаты"), min_prep_days: T("Мин. дней на подготовку"), capital_limit_usd: T("Лимит капитала, USD"),
  base_currency: T("Базовая валюта"), past_performance: T("Опыт исполнения контрактов"),
  currency: T("Валюта"), purchase: T("Закупочная цена"), freight: T("Фрахт"), insurance: T("Страховка"), duties_pct: T("Пошлины, %"), certification: T("Сертификация"),
  financing: T("Финансирование"), risk_reserve_pct: T("Резерв на риски, %"), overhead: T("Накладные расходы"), bid_price: T("Цена заявки"), supplier_prepay_pct: T("Предоплата поставщику, %"),
  delivery_day: T("День поставки"), acceptance_day: T("День приёмки"), customer_pay_delay_days: T("Отсрочка оплаты заказчиком, дней"), vat_import: T("Импортный НДС"),
  vat_recovery_days: T("Возврат НДС, дней"), logistics_cost: T("Логистика"), buyer_prepay_pct: T("Предоплата заказчика, %"), annual_rate: T("Годовая ставка (доля, 0.12 = 12 %)"),
  order_confirmed: T("Заказ подтверждён"), supplier_contract_signed: T("Контракт с поставщиком подписан"), total: T("Итого"), landed_cost: T("Себестоимость с доставкой"),
  duties: T("Пошлины"), risk_reserve: T("Резерв на риски"), logistics: T("Логистика"), vat: T("НДС"),
  title: T("Название сделки"), tender_key: T("Ключ тендера"), buyer_country: T("Страна заказчика"), goods: T("Товар"), order_value: T("Сумма заказа"),
  s_name: T("Поставщик: название"), s_country: T("Поставщик: страна (ISO)"), s_cpv: T("Поставщик: префиксы CPV (через запятую)"), s_url: T("Поставщик: ссылка на источник"),
  s_confirmed: T("Поставщик подтверждён"), s_notes: T("Поставщик: заметки"),
  decision: T("Решение"), reasons: T("Причины (мин. 3 символа)"), result: T("Исход"), actual_margin_pct: T("Фактическая маржа, %"), note: T("Заметка") };
const lbl = (k) => LBL[k] || k;
const GATE_RU = { jurisdiction: T("Юрисдикция"), controlled_goods: T("Контролируемые товары"), deadline: T("Срок"), currency: T("Валюта"),
  eligibility: T("Квалификация"), documents: T("Документы"), counterparty_screening: T("Проверка контрагента"), capital: T("Капитал") };
const gname = (id) => GATE_RU[id] || id;
const COMP_RU = { qualification: T("Квалификация"), suppliers: T("Поставщики"), capital: T("Капитал"), time: T("Время"), experience: T("Опыт"), risk: T("Риск") };
function stateBox(kind, msg) { return h("div", { class: "state" + (kind === "err" ? " err" : ""), role: kind === "err" ? "alert" : "status" }, msg); }
function msgBox(kind, msg) { return h("div", { class: "msg " + kind, role: kind === "err" ? "alert" : "status" }, msg); }
function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of pairs) dl.append(h("dt", {}, k), h("dd", {}, v == null || v === "" ? "—" : v));
  return dl;
}
function table(cols, rows, onRow) {
  const t = h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col" }, c)))));
  const tb = h("tbody");
  for (const r of rows) {
    const tr = h("tr", r.cls ? { class: r.cls } : {}, r.cells.map((c) => h("td", {}, c)));
    if (onRow) {
      tr.classList.add("row"); tr.tabIndex = 0;
      tr.addEventListener("click", () => onRow(r));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRow(r); } });
    }
    tb.append(tr);
  }
  t.append(tb);
  return h("div", { class: "tw" }, t);
}
function field(label, input) { return h("label", {}, label, input); }
function inp(name, type, val, extra) { return h("input", Object.assign({ name, type: type || "text", value: val == null ? "" : val }, extra || {})); }
function sel(name, opts, val) {
  const s = h("select", { name }, opts.map((o) => h("option", { value: Array.isArray(o) ? o[0] : o }, Array.isArray(o) ? o[1] : o)));
  if (val != null) s.value = val;
  return s;
}
function chk(name, label) { return h("label", { class: "chk" }, h("input", { type: "checkbox", name }), label); }
function formData(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") o[el.name] = el.checked; else o[el.name] = el.value;
  }
  return o;
}

/* ---------- API ---------- */
class ApiError extends Error { constructor(status, detail) { super(fmtDetail(detail)); this.status = status; this.detail = detail; } }
function fmtDetail(d) {
  if (d == null) return "";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x && x.msg ? (x.loc ? x.loc.join(".") + ": " : "") + x.msg : JSON.stringify(x))).join("\n");
  if (d.message) return (d.code ? d.code + ": " : "") + d.message;
  return JSON.stringify(d);
}
async function raw(method, path, body) {
  const res = await fetch(API + path, { method, headers: Object.assign({ Authorization: "Bearer " + S.token }, firmHeaders(path), body !== undefined ? { "Content-Type": "application/json" } : {}),
    body: body !== undefined ? JSON.stringify(body) : undefined });
  if (res.status === 401) { try { localStorage.removeItem("wt_token_saved"); localStorage.removeItem("wt_role_saved"); } catch (e) { /* ignore */ } showGate(T("Токен не принят (401). Введите токен роли.")); throw new ApiError(401, { code: "TOKEN_REQUIRED", message: T("нужен токен роли") }); }
  return res;
}
/* Requests are serialized: the backend shares one sqlite connection across threads and races on concurrent requests (observed: "bad parameter or other API misuse"). */
let chain = Promise.resolve();
function api(method, path, body) {
  const p = chain.then(() => api1(method, path, body));
  chain = p.catch(() => {});
  return p;
}
async function api1(method, path, body) {
  let res = await raw(method, path, body);
  if (res.status === 500 && method === "GET") res = await raw(method, path, body); // backend sqlite race: one honest retry for reads
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!res.ok) throw new ApiError(res.status, data && data.detail !== undefined ? data.detail : data);
  return data;
}
const GET = (p) => api("GET", p);
const POST = (p, b) => api("POST", p, b === undefined ? {} : b);
/* Per-render read cache: several widgets of one view share a request; cleared on every navigation. */
const FETCH = { openT: () => GET("/tenders?status=open&limit=1000"), allT: () => GET("/tenders?limit=1"), layers: () => GET("/map/layers"),
  conf: () => GET("/conflicts"), src: () => GET("/sources/status"), sanc: () => GET("/sanctions/status"), fx: () => GET("/fx") };
function cached(k) { return S.cache[k] || (S.cache[k] = FETCH[k]().catch((e) => { delete S.cache[k]; throw e; })); }
const fmtN = (n) => (typeof n === "number" && isFinite(n) ? n.toLocaleString(WT_LANG === "en" ? "en-GB" : "ru-RU", { maximumFractionDigits: 0 }) : "—");

/* ---------- language + original / translation (T(), Tf(), srvT(), WT_LANG come from i18n.js) ----------
   Original text and its machine translation are ALWAYS two separate elements, each with its own language tag. A translation exists only
   when the server returned one (status TRANSLATED); otherwise the reader sees the original and an explicit "no translation" note. */
S.trmode = (() => { try { const v = localStorage.getItem("wt_trmode"); return v === "orig" || v === "tr" ? v : "both"; } catch (e) { return "both"; } })();
S.tr = new Map();       // "<target>|<original text>" -> server record {status, source_lang, translation, model, reason}
S.trInfo = null;        // last /translate/status-like block (limits, usage, cost)
const uniqT = (a) => [...new Set(a.map((x) => (x == null ? "" : String(x).trim())).filter(Boolean))];
const trKey = (t) => WT_LANG + "|" + t;
const TR_DONE = ["TRANSLATED", "SAME_LANGUAGE", "EMPTY"];
function applyTrMode() {
  document.body.dataset.trmode = S.trmode;
  for (const b of document.querySelectorAll("#trmode button")) { const on = b.dataset.m === S.trmode; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on)); }
}
function setTrMode(m) { S.trmode = m; try { localStorage.setItem("wt_trmode", m); } catch (e) { /* storage may be blocked */ } applyTrMode(); }
function setLang(l) {
  if (l === WT_LANG) return;
  try { localStorage.setItem("wt_lang", l); sessionStorage.setItem("wt_view", S.view); } catch (e) { /* storage may be blocked */ }
  location.reload();
}
async function trLookup(texts) {                      // language tags + CACHED translations only: no model call, no spend
  const need = uniqT(texts).filter((t) => !S.tr.has(trKey(t)));
  for (let i = 0; i < need.length; i += 60) {
    const part = need.slice(i, i + 60);
    try {
      const d = await POST("/translate/lookup", { texts: part, target: WT_LANG });
      S.trInfo = d;
      part.forEach((t, k) => { if (d.items[k]) S.tr.set(trKey(t), d.items[k]); });
    } catch (e) { if (e.status === 401) return; break; }
  }
}
async function trRun(texts) {                         // owner click: machine translation through free Bossman routes
  const part = uniqT(texts).slice(0, 60);
  const d = await POST("/translate", { texts: part, target: WT_LANG });
  part.forEach((t, k) => { if (d.items[k]) S.tr.set(trKey(t), d.items[k]); });
  S.trInfo = d;
  return d;
}
const ltag = (code, kind, cls) => h("span", { class: "ltag " + (cls || "") }, (code ? String(code).toUpperCase() : "??") + " · " + kind);
function dual(text, opts) {
  opts = opts || {};
  const src = text == null ? "" : String(text).trim();
  if (!src) return h("span", {}, "—");
  if (opts.keep) {                                    // organisation names: never translated, shown as the original only
    return h("span", { class: "keep" }, h("span", { class: "otxt" }, src), " ", h("span", { class: "ltag keepnote", title: T(T("Названия организаций не переводятся")) }, T(T("оригинал · не переводится"))));
  }
  const rec = S.tr.get(trKey(src)) || null;
  const lang = opts.lang || (rec && rec.source_lang) || null;
  if (lang === WT_LANG || (rec && rec.status === "SAME_LANGUAGE")) return h("span", { class: "otxt", lang: WT_LANG }, src);   // already in the UI language: nothing to translate
  const done = rec && rec.status === "TRANSLATED";
  const box = h("div", { class: "dual" + (done ? " has-tr" : "") });
  box.append(h("div", { class: "orig" }, ltag(lang, T(T("оригинал"))), h("span", { class: "otxt", lang: lang || null }, src)));
  const tr = h("div", { class: "trans" + (done ? "" : " none") });
  const target = WT_LANG.toUpperCase();
  if (done) {
    tr.append(h("span", { class: "ltag t" }, T(T("Перевод")) + " → " + target + " · " + T(T("машинный перевод")) + (rec.model ? " · " + rec.model : "")),
      h("span", { class: "ttxt", lang: WT_LANG }, rec.translation));
  } else {
    const note = h("span", { class: "mut" }, T(T("перевода нет")) + (rec && rec.reason && rec.status !== "NOT_TRANSLATED" ? " — " + (WT_LANG === "en" && srvT(rec.reason) != null ? srvT(rec.reason) : rec.reason) : ""));
    const btn = h("button", { type: "button", class: "sm", onclick: async (e) => {
      e.stopPropagation(); btn.disabled = true; note.textContent = T(T("перевод…"));
      try { await trRun([src]); box.replaceWith(dual(text, opts)); }
      catch (x) { btn.disabled = false; note.textContent = T(T("перевода нет")) + " — " + (x.message || x); }
    } }, T(T("Перевести")));
    tr.append(h("span", { class: "ltag t" }, T(T("Перевод")) + " → " + target), note, " ", btn);
  }
  box.append(tr);
  return box;
}
function srv(v) {                                     // text produced by the server in Russian (gate reasons, fit explanations, checklist)
  if (v == null || v === "") return "—";
  const s = String(v);
  if (WT_LANG === "ru") return s;
  const e = srvT(s);
  return e != null ? e : dual(s, { lang: "ru" });
}
function serverTexts(a, cl) {                         // Russian server texts that need a lookup in EN mode (those without a built-in English form)
  if (WT_LANG === "ru") return [];
  const all = [];
  if (a) {
    for (const g of a.gates || []) all.push(g.reason, g.evidence_needed);
    for (const c of Object.values((a.fit && a.fit.components) || {})) all.push(c.why);
    if (a.fit) all.push(a.fit.disclaimer);
  }
  if (cl && cl.items) { all.push(cl.note); for (const i of cl.items) all.push(i.step, i.why, i.evidence); }
  return all.filter((x) => x && srvT(String(x)) == null);
}
function trBar(getTexts, redraw) {                    // list-level control: translate what is on screen (<= 60 per click)
  const info = h("span", { class: "mut" }), out = h("div");
  const btn = h("button", { type: "button" });
  const pend = () => uniqT(getTexts()).filter((t) => { const r = S.tr.get(trKey(t)); return !r || !TR_DONE.includes(r.status); }).slice(0, 60);
  const upd = () => {
    const n = pend().length, i = S.trInfo;
    btn.textContent = Tf(T("Перевести видимые ({0})"), n); btn.disabled = n === 0;
    info.textContent = !i ? "" : i.configured === false ? T(T("перевод не подключён: нет шлюза Bossman"))
      : i.halted ? T(T("перевод остановлен: маршрут сообщил оплату")) : Tf(T("бесплатные модели Bossman · сегодня {0}/{1} · стоимость {2} USD"), i.used_today, i.limit_per_day, Number(i.cost_total_usd || 0).toFixed(2));
  };
  btn.addEventListener("click", async () => {
    btn.disabled = true; clear(out).append(msgBox("ok", T(T("Перевод…"))));
    try {
      const d = await trRun(pend());
      const bad = d.items.filter((r) => !TR_DONE.includes(r.status));
      clear(out).append(msgBox(bad.length ? "err" : "ok", Tf(T("Переведено: {0}"), d.fresh) + (d.models.length ? " · " + [...new Set(d.models)].join(", ") : "") +
        (bad.length ? " · " + Tf(T("без перевода: {0}"), bad.length) + " — " + txt(bad[0].reason) : "")));
      redraw();
    } catch (e) { if (e.status !== 401) clear(out).append(msgBox("err", T(T("Перевод не выполнен")) + ": " + (e.status ? "HTTP " + e.status + " — " : "") + e.message)); upd(); }
  });
  upd();
  return h("div", {}, h("div", { class: "bar trbar", role: "group", "aria-label": T(T("Перевод")) }, btn, info), out);
}
function applyStatic() {
  document.documentElement.lang = WT_LANG;
  document.title = T(T("Bossman · Терминал конфликтов и закупок"));
  const set = (sel, attr, ruText) => { const el = $(sel); if (!el) return; if (attr === "text") el.textContent = T(ruText); else el.setAttribute(attr, T(ruText)); };
  set(".brand span", "text", T("ТЕРМИНАЛ КОНФЛИКТОВ И ЗАКУПОК"));
  set("#wm", "aria-label", T("Режим данных")); set("#audit", "aria-label", T("Цепочка аудита")); set("#audit", "text", T("аудит: …"));
  set("#fxb", "aria-label", T("Дата курсов валют")); set("#sancb", "aria-label", T("Санкционный список")); set("#utc", "aria-label", T("Дата UTC"));
  set("#gsearch", "placeholder", T("Поиск по тендерам")); set("#gsearch", "aria-label", T("Поиск по тендерам")); set("#logout", "text", T("Выйти"));
  set("#nav", "aria-label", T("Разделы"));
  set("#strip", "aria-label", T("Статус источников"));
  set("#langsw", "aria-label", T("Язык интерфейса")); set("#trmode", "aria-label", T("Показ текста: оригинал и перевод"));
  const names = { orig: T("Оригинал"), tr: T("Перевод"), both: T("Оба") };
  for (const b of document.querySelectorAll("#trmode button")) { b.textContent = T(names[b.dataset.m]); b.addEventListener("click", () => setTrMode(b.dataset.m)); }
  for (const b of document.querySelectorAll("#langsw button")) {
    const on = b.dataset.l === WT_LANG; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on));
    b.addEventListener("click", () => setLang(b.dataset.l));
  }
  applyTrMode();
}

/* ---------- token gate ---------- */
function showGate(note) {
  const g = clear($("#gate"));
  g.hidden = false;
  g.style.cssText = "position:fixed;inset:0;background:rgba(3,8,15,.92);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px";
  const tok = h("input", { type: "password", id: "tok", autocomplete: "off", "aria-label": T("Токен роли"), value: S.token, style: "width:100%" });
  const role = sel("role", ROLES.map((x) => [x, ROLE_RU[x]]), S.role); role.setAttribute("aria-label", T("Метка роли"));
  const remember = h("input", { type: "checkbox", id: "remember-tok" }); remember.checked = true;   // owner 22.10: do not retype the token every visit
  const form = h("form", { class: "card", style: "max-width:420px;width:100%", onsubmit: (e) => {
    e.preventDefault();
    S.token = tok.value.trim(); S.role = role.value;
    try { sessionStorage.setItem("wt_token", S.token); sessionStorage.setItem("wt_role", S.role); } catch (x) { /* ignore */ }
    try { if (remember.checked) { localStorage.setItem("wt_token_saved", S.token); localStorage.setItem("wt_role_saved", S.role); } else { localStorage.removeItem("wt_token_saved"); localStorage.removeItem("wt_role_saved"); } } catch (x) { /* ignore */ }
    g.hidden = true; clear(g); boot();
  } },
  h("h2", {}, T("Вход по токену роли")),
  note ? msgBox("err", note) : null,
  h("p", { class: "mut" }, T("Токен хранится в этом браузере, если отмечено «Запомнить на этом устройстве», иначе только в этой вкладке. Метка роли ниже — подсказка интерфейсу; реальную роль определяет сервер по токену.")),
  h("div", { class: "form", style: "grid-template-columns:1fr" }, field(T("Токен"), tok), field(T("Метка роли"), role)),
  h("label", { class: "mut", style: "display:flex;gap:8px;align-items:center;margin:8px 0" }, remember, T("Запомнить на этом устройстве")),
  h("div", { class: "bar" }, h("button", { type: "submit" }, T("Войти"))));
  g.append(form);
  tok.focus();
}

/* ---------- chrome ---------- */
function renderNav() {
  const nav = clear($("#nav"));
  nav.setAttribute("role", "navigation");
  for (const [id, label] of SECTIONS) nav.append(h("button", { type: "button", "data-v": id, "aria-current": S.view === id ? "page" : null, onclick: () => go(id) }, label));
  let s = $("#navsel");
  if (!s) {
    s = h("select", { id: "navsel", "aria-label": T("Раздел"), onchange: (e) => go(e.target.value) }, SECTIONS.map(([id, l]) => h("option", { value: id }, l)));
    $("#main").before(s);
  }
  s.value = S.view;
}
function go(v) { S.view = v; renderNav(); render(); }
function tick() { $("#utc").textContent = new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC"; }
async function loadHealth() {
  const wm = $("#wm"), au = $("#audit");
  try {
    const hp = chain.then(() => fetch(API + "/health").then((r) => r.json())); chain = hp.catch(() => {});
    const hd = await hp; S.health = hd;
    if (hd.mode === "LIVE") { wm.textContent = T("ЖИВЫЕ ДАННЫЕ"); wm.className = "badge wm b-ok"; }
    else { wm.textContent = WM_RU[hd.watermark] || hd.watermark || WM_RU["OFFLINE / SOURCES OFF"]; wm.className = "badge wm b-warn"; }
    wm.title = T("режим: ") + ru(hd.mode) + T(", внешние источники: ") + (hd.live_enabled ? T("включены") : T("выключены")) + T(", скрыто без источника: ") + (hd.excluded_unverified || 0) + T(", версия ") + hd.version;
    au.textContent = hd.audit_chain_ok ? T("аудит: цепочка OK") : T("аудит: НАРУШЕНА");
    au.className = "badge " + (hd.audit_chain_ok ? "b-ok" : "b-bad");
  } catch (e) {
    wm.textContent = T("НЕТ СВЯЗИ С API"); wm.className = "badge wm b-bad";
    au.textContent = T("аудит: неизвестно"); au.className = "badge b-grey";
  }
}

/* Header badges: FX date and sanctions list size. Hidden when absent or when the endpoint is not available; never invented. */
const isCnb = (s) => /ČNB|CNB|cnb\.cz/i.test(String(s || ""));
async function loadBadges() {
  const fb = $("#fxb"), sbg = $("#sancb");
  try {
    const fx = await GET("/fx");
    if (fx && fx.as_of) {
      fb.textContent = (isCnb(fx.source) ? T("курсы ЧНБ: ") : T("курсы: ")) + fx.as_of;
      fb.title = T("источник: ") + txt(fx.source) + (fx.url ? " — " + fx.url : "") + T(", пар: ") + Object.keys(fx.rates || {}).length;
      fb.hidden = false;
    } else fb.hidden = true;
  } catch (e) { fb.hidden = true; }
  try {
    const st = await GET("/sanctions/status");
    if (st && typeof st.count === "number" && st.count > 0) {
      sbg.textContent = T("санкции: ") + st.count;
      sbg.title = T("дата: ") + txt(st.as_of) + T(", версия списка: ") + txt(st.list_version) + T(", источник: ") + txt(st.source);
      sbg.className = "badge b-info"; sbg.hidden = false;
    } else if (st && st.count === 0) { sbg.textContent = T("санкции: не загружены"); sbg.title = ""; sbg.className = "badge b-warn"; sbg.hidden = false; }
    else sbg.hidden = true;
  } catch (e) { sbg.hidden = true; }
}

/* ---------- generic loader ---------- */
async function load(el, fn) {
  clear(el).append(stateBox("load", T("Загрузка…")));
  try { const out = await fn(); clear(el); if (out) el.append(out); }
  catch (e) { if (e.status === 401) return; clear(el).append(stateBox("err", T("Ошибка: ") + (e.message || e))); }
}
function card(title, ...kids) { return h("section", { class: "card", "aria-label": title }, h("h2", {}, title), kids); }
function submit(form, fn, out) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(out);
    try { const r = await fn(formData(form)); out.append(msgBox("ok", T("Готово: ") + JSON.stringify(r))); return r; }
    catch (x) { if (x.status !== 401) out.append(msgBox("err", x.status ? "HTTP " + x.status + " — " + x.message : String(x))); }
  });
}

/* ---------- geo: Natural Earth 1:110m (public domain), served locally from ui/vendor ---------- */
const GEO = { data: null, byIso3: {}, byKey: {}, iso2: {}, nameIx: {}, paths: null };
const nkey = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\([^)]*\)/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
/* UCDP spellings that differ from Natural Earth names (parenthesised historic names are stripped by nkey). */
const GEO_ALIAS = { "dr congo": "COD", "congo": "COG", "ivory coast": "CIV", "united states of america": "USA", "bosnia herzegovina": "BIH",
  "russia": "RUS", "yemen": "YEM", "myanmar": "MMR", "cambodia": "KHM", "turkey": "TUR", "united kingdom": "GBR", "south sudan": "SSD",
  "central african republic": "CAF", "kingdom of eswatini": "SWZ", "czech republic": "CZE" };
const ISO3_RE = /^[A-Z]{3}$/;
let geoP = null;
function loadGeo() {
  if (!geoP) {
    geoP = fetch("vendor/countries-110m.geojson").then((r) => { if (!r.ok) throw new Error(T("геометрия карты: HTTP ") + r.status); return r.json(); }).then((d) => {
      GEO.data = d;
      (d.features || []).forEach((f, i) => {
        const p = f.properties || {}, iso3 = ISO3_RE.test(p.iso3 || "") ? p.iso3 : null;
        const key = iso3 || "_" + i;
        GEO.byKey[key] = { iso3, p };
        if (!iso3) return;
        GEO.byIso3[iso3] = p;
        if (p.iso2 && /^[A-Z]{2}$/.test(p.iso2)) GEO.iso2[p.iso2] = iso3;
        for (const k of ["name", "name_long", "admin", "formal"]) if (p[k]) GEO.nameIx[nkey(p[k])] = iso3;
      });
      for (const [k, v] of Object.entries(GEO_ALIAS)) if (!GEO.nameIx[k]) GEO.nameIx[k] = v;
      return GEO;
    });
    geoP.catch(() => { geoP = null; });
  }
  return geoP;
}
function toIso3(v) {
  if (v == null) return null;
  const s = String(v).trim(), u = s.toUpperCase();
  if (u.length === 3 && GEO.byIso3[u]) return u;
  if (u.length === 2 && GEO.iso2[u]) return GEO.iso2[u];
  return GEO.nameIx[nkey(s)] || null;
}
const geoName = (iso3) => (GEO.byIso3[iso3] && (GEO.byIso3[iso3].name_long || GEO.byIso3[iso3].name)) || iso3;

/* Equal Earth projection (Šavrič, Patterson, Jenny 2018), implemented inline. Antarctica is left out. */
const EE = { A1: 1.340264, A2: -0.081106, A3: 0.000893, A4: 0.003796, M: Math.sqrt(3) / 2 };
function eqEarth(lon, lat) {
  const l = lon * Math.PI / 180, th = Math.asin(EE.M * Math.sin(lat * Math.PI / 180)), t2 = th * th, t6 = t2 * t2 * t2;
  return [l * Math.cos(th) / (EE.M * (EE.A1 + 3 * EE.A2 * t2 + t6 * (7 * EE.A3 + 9 * EE.A4 * t2))), th * (EE.A1 + EE.A2 * t2 + t6 * (EE.A3 + EE.A4 * t2))];
}
const LAT_N = 84, LAT_S = -57;
const MAP_W = 1000, MAP_K = MAP_W / (2 * eqEarth(180, 0)[0]), MAP_TOP = eqEarth(0, LAT_N)[1];
const MAP_H = Math.round((MAP_TOP - eqEarth(0, LAT_S)[1]) * MAP_K);
const proj = (lon, lat) => { const [x, y] = eqEarth(lon, Math.max(LAT_S - 3, Math.min(LAT_N, lat))); return [MAP_W / 2 + x * MAP_K, (MAP_TOP - y) * MAP_K]; };
const ptsPath = (pts, close) => pts.map(([lo, la], i) => { const [x, y] = proj(lo, la); return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1); }).join("") + (close ? "Z" : "");
function range(a, b, step) { const r = []; for (let v = a; step > 0 ? v <= b : v >= b; v += step) r.push(v); return r; }
function outlinePath() {
  return ptsPath([...range(LAT_S, LAT_N, 3).map((la) => [-180, la]), ...range(-180, 180, 3).map((lo) => [lo, LAT_N]),
    ...range(LAT_N, LAT_S, -3).map((la) => [180, la]), ...range(180, -180, -3).map((lo) => [lo, LAT_S])], true);
}
function graticulePath() {
  let d = "";
  for (let lo = -150; lo <= 150; lo += 30) d += ptsPath(range(LAT_S, LAT_N, 3).map((la) => [lo, la]));
  for (const la of [-30, 0, 30, 60]) d += ptsPath(range(-180, 180, 3).map((lo) => [lo, la]));
  return d;
}
function geoPaths() {
  if (GEO.paths) return GEO.paths;
  GEO.paths = [];
  (GEO.data.features || []).forEach((f, i) => {
    const p = f.properties || {}, g = f.geometry;
    if (!g || p.iso3 === "ATA") return;
    const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    let d = "";
    for (const poly of polys) for (const ring of poly) d += ptsPath(ring, true);
    GEO.paths.push({ key: ISO3_RE.test(p.iso3 || "") ? p.iso3 : "_" + i, d });
  });
  return GEO.paths;
}

/* ---------- choropleth layers (values only from GET /map/layers) ---------- */
const LAYERS = {
  conflict: { label: T("Интенсивность конфликтов"), has: (c) => !!c && c.conflict_events > 0, val: (c) => c.conflict_deaths,
    bins: [1, 10, 100, 1000, 10000], binLabels: ["1–9", "10–99", "100–999", "1 000–9 999", "≥ 10 000"],
    colors: ["#5a2a24", "#8c3324", "#c2421f", "#ee6a2c", "#ffc06a"], zero: "#3b3550", zeroLabel: T("0 (события без погибших)"), unit: T("погибших за год, лог. шкала") },
  procurement: { label: T("Активность закупок"), has: (c) => !!c && c.tenders > 0, val: (c) => c.open_tenders,
    bins: [1, 2, 5, 10, 50], binLabels: ["1", "2–4", "5–9", "10–49", "≥ 50"],
    colors: ["#17405f", "#1c5f8c", "#2382ba", "#40a8df", "#9ddcff"], zero: "#2d3a4a", zeroLabel: T("0 открытых (есть закрытые)"), unit: T("открытых тендеров") },
};
const ND_FILL = "#1b2a3a";
function layerFill(L, c) {
  if (!L.has(c)) return ND_FILL;
  const v = L.val(c);
  if (!(v > 0)) return L.zero;
  let i = 0;
  while (i + 1 < L.bins.length && v >= L.bins[i + 1]) i++;
  return L.colors[i];
}
function layerLine(id, c) {
  if (id === "conflict") return c && c.conflict_events > 0 ? Tf(T("погибших: {0} · событий: {1} (UCDP {2})"), fmtN(c.conflict_deaths), fmtN(c.conflict_events), txt(c.conflict_year)) : T("UCDP: событий в загруженных данных нет");
  return c && c.tenders > 0 ? Tf(T("открытых тендеров: {0} из {1}"), fmtN(c.open_tenders), fmtN(c.tenders)) : T("тендеров в базе нет");
}
const uniqYears = (lay) => uniq(Object.values(lay.countries || {}).map((c) => c.conflict_year)).sort();

function mapWidget(lay, opts) {
  const wrap = h("div", { class: "mapw" });
  const tabs = h("div", { class: "maptabs", role: "tablist", "aria-label": T("Слой карты") });
  const box = h("div", { class: "mapbox" });
  const tip = h("div", { class: "maptip", hidden: true });
  const legend = h("div", { class: "legend" }), srcLine = h("div", { class: "legend src" });
  const svg = svgEl("svg", { viewBox: `0 0 ${MAP_W} ${MAP_H}`, role: "img", "aria-label": T("Карта мира (Natural Earth 1:110m, проекция Equal Earth); страны закрашены по данным API") });
  const vp = svgEl("g", {});
  vp.append(svgEl("path", { d: outlinePath(), class: "ocean" }), svgEl("path", { d: graticulePath(), class: "grat" }));
  const els = {};
  for (const g of geoPaths()) { const p = svgEl("path", { d: g.d, class: "ctry", "data-k": g.key }); els[g.key] = p; vp.append(p); }
  svg.append(vp);
  const countries = lay.countries || {};
  const cOf = (key) => { const m = GEO.byKey[key]; return m && m.iso3 ? countries[m.iso3] : null; };

  function paint() {
    const L = LAYERS[S.mapLayer];
    for (const [key, el] of Object.entries(els)) { const c = cOf(key); el.setAttribute("fill", layerFill(L, c)); el.classList.toggle("nd", !L.has(c)); }
    for (const b of tabs.children) b.setAttribute("aria-selected", String(b.dataset.l === S.mapLayer));
    clear(legend).append(h("span", { class: "mut" }, L.unit + ":"),
      ...L.colors.map((col, i) => h("span", {}, h("i", { class: "sq", style: "background:" + col }), L.binLabels[i])),
      h("span", {}, h("i", { class: "sq", style: "background:" + L.zero }), L.zeroLabel), h("span", {}, h("i", { class: "sq", style: "background:" + ND_FILL }), T("нет данных")));
    const n = Object.values(countries).filter(L.has).length;
    clear(srcLine);
    if (S.mapLayer === "conflict") {
      srcLine.append(h("span", {}, T("Источник: "), h("b", {}, lay.conflict_source ? "UCDP GED (" + lay.conflict_source + ")" : T("нет данных UCDP"))),
        h("span", {}, T("Год: "), h("b", {}, uniqYears(lay).join(", ") || "—")), h("span", {}, T("Стран с событиями: "), h("b", {}, String(n))),
        ...((lay.unmapped_conflicts || []).length ? [h("span", {}, T("Не сопоставлено со страной: "), h("b", {}, lay.unmapped_conflicts.join(", ")))] : []));
    } else {
      srcLine.append(h("span", {}, T("Источник: "), h("b", {}, T("тендеры в базе (GET /map/layers)"))), h("span", {}, T("Стран с тендерами: "), h("b", {}, String(n))),
        h("span", {}, T("Тендеров без страны (регионы): "), h("b", {}, String(lay.unmapped_tenders == null ? "—" : lay.unmapped_tenders))));
    }
    srcLine.append(h("span", {}, T("Границы: "), h("b", {}, txt(lay.geometry_source))));
    if (!n) legend.append(h("span", { class: "warnt" }, T("Для этого слоя данных нет — загрузите источник в разделе «Источники».")));
  }
  for (const [id, L] of Object.entries(LAYERS)) {
    tabs.append(h("button", { type: "button", role: "tab", "data-l": id, onclick: () => { S.mapLayer = id; paint(); opts.onLayer && opts.onLayer(id); } }, L.label));
  }
  // disabled tabs from the mockup that have no data source: shown honestly, not clickable
  for (const [l, need] of [[T("Оборонные расходы"), "SIPRI"], [T("Санкции по странам"), T("привязки санкций к странам")], [T("Маршруты поставок"), T("данных о логистике")]]) {
    tabs.append(h("button", { type: "button", role: "tab", disabled: true, "aria-disabled": "true", title: T("нет источника: ") + need, class: "off" }, l));
  }

  /* zoom / pan: transform on one group, strokes stay 1px via vector-effect */
  let k = 1, tx = 0, ty = 0;
  const apply = () => { vp.setAttribute("transform", `translate(${tx.toFixed(2)} ${ty.toFixed(2)}) scale(${k.toFixed(4)})`); svg.style.touchAction = k > 1.001 ? "none" : "pan-y"; };
  const clampPan = () => { tx = Math.min(0, Math.max(MAP_W * (1 - k), tx)); ty = Math.min(0, Math.max(MAP_H * (1 - k), ty)); };
  const zoomAt = (f, cx, cy) => { const nk = Math.min(16, Math.max(1, k * f)), r = nk / k; tx = cx - (cx - tx) * r; ty = cy - (cy - ty) * r; k = nk; clampPan(); apply(); };
  const toSvg = (e) => { const b = svg.getBoundingClientRect(); return [(e.clientX - b.left) / b.width * MAP_W, (e.clientY - b.top) / b.height * MAP_H]; };
  svg.addEventListener("wheel", (e) => { e.preventDefault(); const [x, y] = toSvg(e); zoomAt(e.deltaY < 0 ? 1.3 : 1 / 1.3, x, y); }, { passive: false });
  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false, target: e.target.closest ? e.target.closest("path.ctry") : null };
  });
  svg.addEventListener("pointermove", (e) => {
    if (drag && drag.id === e.pointerId) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      if (!drag.moved) { drag.moved = true; if (k > 1.001) { try { svg.setPointerCapture(e.pointerId); } catch (x) { /* */ } svg.classList.add("drag"); } tip.hidden = true; }
      if (k > 1.001) { const b = svg.getBoundingClientRect(); tx += dx / b.width * MAP_W; ty += dy / b.height * MAP_H; clampPan(); apply(); }
      drag.x = e.clientX; drag.y = e.clientY;
      return;
    }
    showTip(e);
  });
  const endDrag = (e) => {
    if (drag && e.type === "pointerup" && !drag.moved && drag.target) select(drag.target.getAttribute("data-k"));
    drag = null; svg.classList.remove("drag");
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("pointerleave", () => { if (!drag) tip.hidden = true; });
  function showTip(e) {
    const t = e.target.closest ? e.target.closest("path.ctry") : null;
    if (!t) { tip.hidden = true; return; }
    const key = t.getAttribute("data-k"), m = GEO.byKey[key] || { p: {} }, c = cOf(key);
    clear(tip).append(h("b", {}, txt(m.p.name_long || m.p.name)), h("div", {}, layerLine(S.mapLayer, c)),
      h("div", { class: "mut" }, layerLine(S.mapLayer === "conflict" ? "procurement" : "conflict", c)));
    tip.hidden = false;
    const b = box.getBoundingClientRect();
    let x = e.clientX - b.left + 14, y = e.clientY - b.top + 14;
    if (x + tip.offsetWidth > b.width - 4) x = Math.max(4, e.clientX - b.left - 14 - tip.offsetWidth);
    if (y + tip.offsetHeight > b.height - 4) y = Math.max(4, e.clientY - b.top - 14 - tip.offsetHeight);
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function select(key) {
    if (!key || !els[key]) return;
    for (const el of vp.querySelectorAll("path.sel")) el.classList.remove("sel");
    els[key].classList.add("sel"); vp.append(els[key]);
    S.mapSel = key; sel1.value = GEO.byKey[key] && GEO.byKey[key].iso3 && countries[GEO.byKey[key].iso3] ? key : "";
    opts.onSelect && opts.onSelect(key);
  }
  const ctl = h("div", { class: "mapctl" },
    h("button", { type: "button", "aria-label": T("Приблизить"), title: T("Приблизить"), onclick: () => zoomAt(1.6, MAP_W / 2, MAP_H / 2) }, "+"),
    h("button", { type: "button", "aria-label": T("Отдалить"), title: T("Отдалить"), onclick: () => zoomAt(1 / 1.6, MAP_W / 2, MAP_H / 2) }, "−"),
    h("button", { type: "button", "aria-label": T("Сбросить масштаб"), title: T("Сбросить масштаб"), onclick: () => { k = 1; tx = 0; ty = 0; apply(); } }, "⟲"));
  // keyboard / screen-reader alternative to clicking the map
  const opts1 = Object.keys(countries).filter((i) => GEO.byIso3[i]).sort((a, b) => geoName(a).localeCompare(geoName(b)));
  const sel1 = h("select", { "aria-label": T("Выбрать страну с данными"), onchange: (e) => e.target.value && select(e.target.value) },
    h("option", { value: "" }, T("Страна с данными (") + opts1.length + ")…"), opts1.map((i) => h("option", { value: i }, geoName(i))));
  box.append(svg, ctl, tip);
  wrap.append(tabs, box, legend, srcLine, h("div", { class: "bar mapfoot" }, sel1, h("span", { class: "mut" }, T("Колесо — масштаб, перетаскивание — сдвиг, клик — панель страны"))));
  apply(); paint();
  if (S.mapSel && els[S.mapSel]) setTimeout(() => select(S.mapSel), 0);
  S.mapApi = { select, paint };
  return wrap;
}

/* ---------- country panel (mockup "Ukraine" panel, real data only) ---------- */
const ACD_TYPE = { 1: T("внесистемный"), 2: T("межгосударственный"), 3: T("внутренний"), 4: T("интернационализированный внутренний") };
function mini(label, value) { return h("div", { class: "mini" }, h("div", { class: "l" }, label), h("div", { class: "v" }, value)); }
function openTender(t, iso2) { S.sel = t.key; S.filters = { f: "all", q: "", category: "", country: iso2 || "" }; go("tenders"); }
function acdFor(conf, iso3) {
  const acd = conf && conf.acd;
  return ((acd && acd.items) || []).filter((a) => String(a.location || "").split(",").some((s) => toIso3(s) === iso3));
}
function panelHint(panel, lay) {
  const L = LAYERS[S.mapLayer], cs = lay.countries || {};
  const top = Object.keys(cs).filter((i) => L.has(cs[i]) && L.val(cs[i]) > 0 && GEO.byIso3[i]).sort((a, b) => L.val(cs[b]) - L.val(cs[a])).slice(0, 8);
  clear(panel).append(h("h2", {}, T("Страна")), h("p", { class: "mut" }, T("Нажмите на страну на карте (или выберите её под картой): здесь появятся данные UCDP, конфликты UCDP/PRIO и открытые тендеры.")),
    h("h3", {}, T("Топ стран · ") + L.label.toLowerCase()),
    top.length ? h("ol", { class: "rank" }, top.map((i) => h("li", {}, h("button", { type: "button", class: "lnk", onclick: () => S.mapApi && S.mapApi.select(i) }, geoName(i)),
      h("span", { class: "num" }, fmtN(L.val(cs[i])))))) : stateBox("load", T("Для этого слоя данных нет.")));
}
function countryPanel(panel, key, lay, conf) {
  const m = GEO.byKey[key] || { p: {} }, p = m.p, iso3 = m.iso3, c = iso3 ? (lay.countries || {})[iso3] : null;
  clear(panel).append(h("div", { class: "cp-head" }, h("h2", {}, txt(p.name_long || p.name)), iso3 ? badge(iso3, "b-grey") : null,
    c && c.severity ? sevBadge(c.severity) : null, h("button", { type: "button", class: "x", "aria-label": T("Закрыть панель страны"), onclick: () => { S.mapSel = null; for (const el of document.querySelectorAll("path.ctry.sel")) el.classList.remove("sel"); panelHint(panel, lay); } }, "×")));
  if (!iso3) { panel.append(stateBox("load", T("У этой территории нет ISO3-кода в Natural Earth — данные API к ней не привязываются."))); return; }
  panel.append(h("h3", {}, T("Конфликт · UCDP GED") + (lay.conflict_source ? " (" + lay.conflict_source + ")" : "")));
  if (c && c.conflict_events > 0) {
    panel.append(h("div", { class: "cp-stats" }, mini(T("Погибших"), fmtN(c.conflict_deaths)), mini(T("Событий"), fmtN(c.conflict_events)),
      mini(T("Серьёзность"), sevBadge(c.severity)), mini(T("Год данных"), txt(c.conflict_year))));
  } else panel.append(stateBox("load", lay.conflict_source ? T("В загруженных данных UCDP событий по этой стране нет.") : T("Данные UCDP не загружены.")));
  const acd = conf && conf.acd, items = acdFor(conf, iso3);
  panel.append(h("h3", {}, T("Вооружённые конфликты · UCDP/PRIO ACD") + (acd && acd.year ? " " + acd.year : "")));
  if (!acd) panel.append(stateBox("load", T("Данные ACD не загружены.")));
  else if (!items.length) panel.append(stateBox("load", T("В ACD нет конфликтов с местом в этой стране.")));
  else panel.append(h("ul", { class: "list" }, items.map((a) => h("li", {}, h("div", {}, txt(a.side_a), h("span", { class: "mut" }, T(" против ")), txt(a.side_b)),
    h("div", { class: "bar", style: "margin:4px 0 0" }, INTENSITY[a.intensity] ? badge(INTENSITY[a.intensity][0], INTENSITY[a.intensity][1]) : badge(a.intensity),
      h("span", { class: "mut" }, ACD_TYPE[a.type] || T("тип ") + txt(a.type)), h("span", { class: "mut" }, T("место: ") + txt(a.location)))))));
  panel.append(h("h3", {}, T("Закупки")), h("div", { class: "cp-stats" }, mini(T("Открытых тендеров"), fmtN(c ? c.open_tenders : 0)), mini(T("Всего в базе"), fmtN(c ? c.tenders : 0))));
  const tb = h("div");
  panel.append(tb);
  load(tb, async () => {
    const d = await cached("openT");
    const mine = (d.items || []).filter((t) => toIso3(t.country) === iso3);
    await trLookup(mine.slice(0, 5).map((t) => t.title));
    if (!mine.length) return stateBox("load", T("Открытых тендеров по этой стране нет."));
    return h("div", {}, h("div", { class: "mut" }, Tf(T("Топ-5 из {0} открытых (порядок API: по соответствию)"), mine.length) + (d.total > (d.items || []).length ? T(" · загружены не все открытые тендеры") : "")),
      h("ul", { class: "list" }, mine.slice(0, 5).map((t) => h("li", {}, h("div", { class: "tt" }, dual(t.title)),
        h("div", { class: "bar", style: "margin:4px 0 0" }, sb(t.assessment && t.assessment.complexity), h("span", { class: "mut" }, T("дедлайн ") + txt((t.deadline || "").slice(0, 10))),
          t.estimated_value == null ? null : h("span", { class: "mut" }, fmtN(t.estimated_value) + " " + txt(t.currency)),
          h("button", { type: "button", class: "sm", onclick: () => openTender(t, p.iso2) }, T("Открыть"))), h("div", { class: "src-l" }, provLine(t))))),
      h("button", { type: "button", onclick: () => { S.sel = null; S.filters = { f: "all", q: "", category: "", country: p.iso2 || iso3 }; go("tenders"); } }, T("Все тендеры страны в разделе «Закупки»")));
  });
  if (window.matchMedia && window.matchMedia("(max-width: 950px)").matches && panel.scrollIntoView) panel.scrollIntoView({ block: "start" });
}
/* map + side panel, shared by Overview and Conflicts */
async function mapBlock(mapEl, panel) {
  await load(mapEl, async () => {
    const [lay, conf] = await Promise.all([cached("layers"), cached("conf").catch(() => null), loadGeo()]);
    const w = mapWidget(lay, { onSelect: (key) => countryPanel(panel, key, lay, conf), onLayer: () => { if (!S.mapSel) panelHint(panel, lay); } });
    if (!S.mapSel) panelHint(panel, lay);
    return h("div", {}, w, lay.conflict_source ? h("div", { class: "src-l" }, T("Конфликты: "), provLine({ source_id: lay.conflict_source, source_url: lay.conflict_source_url, retrieved_at: lay.conflict_fetched_at })) : null,
      conf && conf.safety ? h("div", { class: "safety" }, conf.safety) : null);
  });
  if (!mapEl.querySelector(".mapw")) clear(panel).append(h("h2", {}, T("Страна")), stateBox("load", T("Карта не загрузилась — панель страны недоступна.")));
}
function svgEl(tag, attrs) { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; }
const SEV = { low: "#ffd16a", medium: "#ff9f43", high: "#f64b49" };
const SEV_RU = { low: T("низкая"), medium: T("средняя"), high: T("высокая") };
const sevBadge = (s) => badge(SEV_RU[s] || s, s === "high" ? "b-bad" : s === "medium" ? "b-warn" : "b-grey");
const uniq = (arr) => [...new Set(arr.filter((x) => x != null && x !== ""))];
/* source / licence / safety block for /conflicts (the map itself comes from /map/layers) */
function conflictMeta(d) {
  const wrap = h("div");
  const items = d.items || [], real = items;
  if (d.status === "NOT_CONNECTED") wrap.append(stateBox("load", T("Нет данных: ") + txt(d.message)));
  if (real.length) {
    const PREC = T("средняя точка событий страны за год; не координаты событий");
    const prec = uniq(real.map((c) => c.precision)).filter((p) => p !== PREC);
    wrap.append(h("div", { class: "legend src" },
      h("span", {}, T("Источник: "), h("b", {}, uniq(real.map((c) => c.source_id)).join(", ") || "—")),
      h("span", {}, T("Год: "), h("b", {}, uniq(real.map((c) => c.since)).join(", ") || "—")),
      h("span", {}, T("Лицензия: "), h("b", {}, uniq(real.map((c) => c.licence)).join(", ") || "—")),
      h("span", {}, T("Точность: ") + PREC + (prec.length ? T(" (точность источника: ") + prec.join(", ") + ")" : ""))));
    wrap.append(h("div", { class: "src-l" }, provLine(real[0])));
  }
  if (!items.length) wrap.append(stateBox("load", T("Нет данных о конфликтах.")));
  return wrap;
}
function realConflictTable(items, limit) {
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : -1);
  const rows = items.slice().sort((a, b) => num(b.deaths) - num(a.deaths)).slice(0, limit || undefined);
  return table([T("Страна"), T("Событий"), T("Погибших"), T("Серьёзность"), T("Год"), T("Источник"), T("Получено"), T("Лицензия")], rows.map((c) => ({ cells: [
    h("span", {}, txt(c.country), c.region ? h("div", { class: "mut" }, c.region) : null),
    txt(c.events), txt(c.deaths), sevBadge(c.severity), txt(c.since),
    c.source_url && /^https?:\/\//.test(c.source_url) ? h("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer" }, txt(c.source_id)) : txt(c.source_id),
    fetchedAt(c.retrieved_at), h("span", { class: "lic" }, txt(c.licence))] })));
}
const INTENSITY = { war: [T("война"), "b-bad"], minor: [T("малый"), "b-warn"] };
function acdTable(acd) {
  const items = (acd && acd.items) || [];
  return table([T("Место"), T("Стороны"), T("Интенсивность"), T("Тип"), T("Год")], items.map((c) => ({ cells: [
    txt(c.location), h("span", {}, txt(c.side_a), h("span", { class: "mut" }, T(" против ")), txt(c.side_b)),
    INTENSITY[c.intensity] ? badge(INTENSITY[c.intensity][0], INTENSITY[c.intensity][1]) : badge(c.intensity), ACD_TYPE[c.type] || txt(c.type), txt(c.year)] })));
}
/* ---------- tenders ---------- */
/* The backend country filter only knows ~40 ISO3 codes (an unknown code matches region-only tenders), so the country
   filter is applied here: tender.country (ISO3 / ISO2 / name) is normalized with the Natural Earth index. */
async function loadTenders() {
  const f = S.filters;
  const p = new URLSearchParams();
  if (f.f === "EASY" || f.f === "MEDIUM" || f.f === "HARD") p.set("tier", f.f);
  if (f.f === "BLOCKED" || f.f === "REVIEW_REQUIRED") p.set("complexity", f.f);
  if (f.q) p.set("q", f.q);
  if (f.category) p.set("category", f.category);
  if (f.source) p.set("source", f.source);
  if (!f.country) { p.set("limit", "200"); return GET("/tenders?" + p); }
  await loadGeo().catch(() => null);
  p.set("limit", "1000");
  const d = await GET("/tenders?" + p);
  const want = toIso3(f.country), raw = f.country.trim().toUpperCase();
  const items = (d.items || []).filter((t) => (want ? toIso3(t.country) === want : String(t.country || "").toUpperCase() === raw));
  return Object.assign({}, d, { items, total: items.length, partial: d.total > (d.items || []).length, countryIso3: want });
}
function tenderTable(items, onRow) {
  return table([T("Страна"), T("Название"), T("Категория"), T("Стоимость"), T("Дедлайн"), T("Статус"), T("Сложность"), T("Соответствие"), T("Источник")], items.map((t) => {
    const a = t.assessment || {};
    return { key: t.key, cls: S.sel === t.key ? "sel" : "", cells: [
      txt(t.country), h("div", {}, dual(t.title)), txt(t.category),
      t.estimated_value == null ? "—" : `${t.estimated_value} ${t.currency || ""}`, txt(t.deadline), txt(ru(t.status)),
      sb(a.complexity), a.fit_score == null ? h("span", { class: "mut", title: FIRM.data ? T("нет данных") : T("Оценка отключена: данные фирмы не введены") }, "—") : String(a.fit_score), provShort(t)] };
  }), (r) => onRow(r.key));
}
function countsFrom(items) {
  const c = { total: items.length, EASY: 0, MEDIUM: 0, HARD: 0, BLOCKED: 0, REVIEW_REQUIRED: 0 };
  for (const t of items) { const a = t.assessment || {}; if (a.tier_if_cleared && c[a.tier_if_cleared] != null) c[a.tier_if_cleared]++; if (a.complexity === "BLOCKED") c.BLOCKED++; if (a.complexity === "REVIEW_REQUIRED") c.REVIEW_REQUIRED++; }
  return c;
}

/* ---------- KPI tiles (every value from the API; "—" + reason when a request fails) ---------- */
const ICONS = {
  doc: "M6 3h9l4 4v14H6zM15 3v4h4M9 12h7M9 16h7", stack: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
  globe: "M12 3a9 9 0 1 0 .01 0zM3 12h18M12 3c3.2 3 3.2 15 0 18M12 3c-3.2 3-3.2 15 0 18", fire: "M12 3l9.5 17h-19zM12 10v4.5M12 17.2v.3",
  target: "M12 3a9 9 0 1 0 .01 0zM12 8a4 4 0 1 0 .01 0zM12 1v4M12 19v4M1 12h4M19 12h4", shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  pulse: "M2 12h4l3-7 5 14 3-7h5", fx: "M5 8h13l-3-3M19 16H6l3 3",
};
function icon(name) { const s = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }); s.append(svgEl("path", { d: ICONS[name] })); return s; }
function kpiTile(ic, value, label, sub, tone) {
  return h("div", { class: "kpi " + (tone || "") }, icon(ic), h("div", { class: "kb" }, h("div", { class: "v" }, value), h("div", { class: "l" }, label), h("div", { class: "s", title: sub || "" }, sub || "")));
}
async function loadKpis(el) {
  clear(el).append(stateBox("load", T("Загрузка показателей…")));
  const [openT, allT, lay, conf, sanc, src, fx] = await Promise.allSettled(["openT", "allT", "layers", "conf", "sanc", "src", "fx"].map(cached));
  if ([openT, allT, lay, conf, sanc, src, fx].some((r) => r.status === "rejected" && r.reason && r.reason.status === 401)) return;
  const err = (r) => T("нет данных: ") + (r.reason && r.reason.status ? "HTTP " + r.reason.status : String(r.reason && r.reason.message || r.reason));
  const tiles = [];
  tiles.push(openT.status === "fulfilled" ? kpiTile("doc", fmtN(openT.value.total), T("Открытых тендеров"), T("статус «открыт» · все источники"), "ok") : kpiTile("doc", "—", T("Открытых тендеров"), err(openT)));
  tiles.push(allT.status === "fulfilled" ? kpiTile("stack", fmtN(allT.value.total), T("Тендеров в базе"), T("открытые + закрытые")) : kpiTile("stack", "—", T("Тендеров в базе"), err(allT)));
  if (lay.status === "fulfilled") {
    const cs = Object.values(lay.value.countries || {}).filter((c) => c.conflict_events > 0), yrs = uniqYears(lay.value).join(", ");
    const ev = cs.reduce((a, c) => a + c.conflict_events, 0), de = cs.reduce((a, c) => a + c.conflict_deaths, 0);
    tiles.push(kpiTile("globe", fmtN(cs.length), T("Стран с событиями UCDP"), cs.length ? "UCDP GED · " + yrs : T("UCDP не загружен"), "r"));
    tiles.push(kpiTile("fire", fmtN(ev), T("Событий UCDP") + (yrs ? " " + yrs : ""), cs.length ? T("погибших: ") + fmtN(de) : T("UCDP не загружен"), "r"));
  } else { tiles.push(kpiTile("globe", "—", T("Стран с событиями UCDP"), err(lay))); tiles.push(kpiTile("fire", "—", T("Событий UCDP"), err(lay))); }
  if (conf.status === "fulfilled" && conf.value.acd && (conf.value.acd.items || []).length) {
    const acd = conf.value.acd, ids = uniq(acd.items.map((a) => a.conflict_id)), wars = uniq(acd.items.filter((a) => a.intensity === "war").map((a) => a.conflict_id));
    tiles.push(kpiTile("target", fmtN(ids.length), T("Вооружённых конфликтов"), "UCDP/PRIO ACD " + txt(acd.year) + T(" · войн: ") + wars.length, "r"));
  } else tiles.push(kpiTile("target", "—", T("Вооружённых конфликтов"), conf.status === "rejected" ? err(conf) : T("нет источника: UCDP/PRIO ACD не загружен")));
  tiles.push(sanc.status === "fulfilled" ? kpiTile("shield", sanc.value.count ? fmtN(sanc.value.count) : "0", T("Записей в санкц. списках"), sanc.value.count ? txt(sanc.value.source) + " · " + txt(sanc.value.as_of) : T("не загружены"), "w")
    : kpiTile("shield", "—", T("Записей в санкц. списках"), err(sanc)));
  if (src.status === "fulfilled") {
    const rows = src.value.sources || [], live = rows.filter((s) => s.status === "LIVE");
    tiles.push(kpiTile("pulse", live.length + " / " + rows.length, T("Живых источников"), live.map((s) => s.id).join(", ") || T("ни одного"), "ok"));
  } else tiles.push(kpiTile("pulse", "—", T("Живых источников"), err(src)));
  tiles.push(fx.status === "fulfilled" && fx.value && fx.value.as_of ? kpiTile("fx", fx.value.as_of, T("Дата курсов валют"), txt(fx.value.source) + T(" · пар: ") + Object.keys(fx.value.rates || {}).length)
    : kpiTile("fx", "—", T("Дата курсов валют"), fx.status === "rejected" ? err(fx) : T("курсы не загружены")));
  clear(el).append(...tiles);
}

/* ---------- honest placeholders for mockup blocks without a data source ---------- */
function placeholder(title, need, srcId, srcs) {
  const row = srcId && srcs ? (srcs.sources || []).find((s) => s.id === srcId) : null;
  return h("section", { class: "card ph", "aria-label": title }, h("h2", {}, title), h("div", { class: "phmsg" }, T("нет источника: ") + need),
    row ? h("div", { class: "bar" }, h("span", { class: "mut" }, T("реестр источников: ") + txt(row.name)), sb(row.status), row.reason ? h("span", { class: "mut" }, srv(row.reason)) : null) : null);
}
async function placeholders(el) {
  const srcs = await cached("src").catch(() => null);
  clear(el).append(
    placeholder(T("Оборонные бюджеты"), T("датасет SIPRI Military Expenditure Database (годовые расходы по странам)"), "sipri", srcs),
    placeholder(T("Компании сектора / котировки"), T("поставщик рыночных котировок (биржевые данные с лицензией на показ)"), null, srcs),
    placeholder(T("Недавние награды контрактов"), T("эндпоинт чтения наград в API (USAspending / TED award notices); закрытые тендеры — не награды"), "usaspending", srcs));
}

/* ---------- top conflicts (UCDP GED + ACD) ---------- */
async function topConflicts(el) {
  await load(el, async () => {
    const [lay, conf] = await Promise.all([cached("layers"), cached("conf"), loadGeo().catch(() => null)]);
    const cs = Object.entries(lay.countries || {}).filter(([, c]) => c.conflict_events > 0).sort((a, b) => b[1].conflict_deaths - a[1].conflict_deaths).slice(0, 6);
    const out = h("div");
    if (!cs.length) out.append(stateBox("load", T("Данных UCDP нет. Загрузите их в разделе «Источники».")));
    else {
      const max = cs[0][1].conflict_deaths || 1;
      out.append(h("div", { class: "mut" }, T("Погибших за ") + uniqYears(lay).join(", ") + T(" (UCDP GED), топ-6 · клик — страна на карте")),
        h("ol", { class: "rank bars" }, cs.map(([i, c]) => h("li", {}, h("button", { type: "button", class: "lnk", onclick: () => { if (S.mapApi) { S.mapApi.select(i); } } }, geoName(i)),
          h("span", { class: "barw", "aria-hidden": "true" }, h("span", { style: "width:" + Math.max(2, Math.round(c.conflict_deaths / max * 100)) + "%" })),
          h("span", { class: "num" }, fmtN(c.conflict_deaths)), sevBadge(c.severity)))));
    }
    const acd = conf.acd, wars = ((acd && acd.items) || []).filter((a) => a.intensity === "war");
    out.append(h("h3", {}, T("Войны (≥ 1 000 погибших в боях за год) · UCDP/PRIO ACD") + (acd && acd.year ? " " + acd.year : "")));
    out.append(!acd ? stateBox("load", T("Данные ACD не загружены.")) : wars.length ? h("ul", { class: "list" }, wars.map((a) => h("li", {}, h("b", {}, txt(a.location)), h("div", { class: "mut" }, txt(a.side_a) + T(" против ") + txt(a.side_b)))))
      : stateBox("load", T("В ACD нет конфликтов интенсивности «война».")));
    return out;
  });
}

async function viewOverview(el) {
  const kpi = h("div", { class: "kpis", "aria-label": T("Ключевые показатели") }), mapEl = h("div"), panel = h("aside", { class: "card cpanel", "aria-label": T("Панель страны") });
  const tBox = h("div"), cBox = h("div"), phBox = h("div", { class: "phs" });
  el.append(kpi, h("div", { class: "mapgrid" }, h("section", { class: "card mapcard", "aria-label": T("Карта мира") }, mapEl), panel),
    h("div", { class: "lower" }, card(T("Открытые тендеры"), tBox), card(T("Конфликты · UCDP"), cBox), phBox));
  await Promise.all([
    loadKpis(el.querySelector(".kpis")),
    mapBlock(mapEl, panel),
    topConflicts(cBox),
    placeholders(phBox),
    load(tBox, async () => {
      const d = await cached("openT"), items = d.items || [];
      if (!items.length) return stateBox("load", T("Открытых тендеров нет. Загрузите данные в разделе «Источники»."));
      await trLookup(items.slice(0, 10).map((t) => t.title));
      const c = countsFrom(items);
      return h("div", {}, h("div", { class: "bar" }, badge(T("Все открытые: ") + d.total, "b-info"), badge(T("Лёгкие: ") + c.EASY, "b-ok"), badge(T("Средние: ") + c.MEDIUM, "b-warn"),
        badge(T("Сложные: ") + c.HARD, "b-bad"), badge(T("Не допущены: ") + c.BLOCKED, "b-bad"), badge(T("На проверке: ") + c.REVIEW_REQUIRED, "b-info")),
      h("div", { class: "mut" }, T("Первые 10 по порядку API (по соответствию); клик — карточка тендера")),
      table([T("Страна"), T("Название"), T("Дедлайн"), T("Сложность"), T("Соответствие"), T("Источник")], items.slice(0, 10).map((t) => ({ key: t.key, cells: [txt(t.country),
        h("div", { class: "clip2" }, dual(t.title)), txt((t.deadline || "").slice(0, 10)),
        sb(t.assessment && t.assessment.complexity), t.assessment && t.assessment.fit_score != null ? String(t.assessment.fit_score) : "—", provShort(t)] })),
        (r) => { S.sel = r.key; S.filters.country = ""; go("tenders"); }));
    }),
  ]);
}
async function viewConflicts(el) {
  const mapEl = h("div"), panel = h("aside", { class: "card cpanel", "aria-label": T("Панель страны") });
  const metaBox = h("div"), realBox = h("div"), acdBox = h("div");
  S.mapLayer = "conflict";
  el.append(h("div", { class: "mapgrid" }, h("section", { class: "card mapcard", "aria-label": T("Карта конфликтов") }, mapEl), panel),
    h("div", { class: "grid", style: "margin-top:12px;grid-template-columns:minmax(0,1fr)" },
      card(T("Источник и лицензия"), metaBox), card(T("UCDP: агрегаты по странам (по числу погибших)"), realBox), card(T("UCDP/PRIO ACD: вооружённые конфликты"), acdBox)));
  await mapBlock(mapEl, panel);
  let d;
  await load(metaBox, async () => { d = await cached("conf"); return conflictMeta(d); });
  if (!d) { clear(realBox).append(stateBox("err", T("Нет ответа /conflicts"))); clear(acdBox); return; }
  const items = d.items || [], real = items;
  clear(realBox).append(real.length ? realConflictTable(real) : stateBox("load", T("Реальных данных UCDP нет. Загрузите их в разделе «Источники» («Конфликты UCDP»).")));
  const acd = d.acd;
  clear(acdBox).append(acd && (acd.items || []).length ? h("div", {}, h("div", { class: "mut" }, T("Год: ") + txt(acd.year) + T(" · строк: ") + acd.items.length + T(" · уникальных конфликтов: ") + uniq(acd.items.map((a) => a.conflict_id)).length), acdTable(acd)) : stateBox("load", T("Данных ACD нет.")));
}

const FILTERS = [["all", T("Все")], ["EASY", T("Лёгкие")], ["MEDIUM", T("Средние")], ["HARD", T("Сложные")], ["BLOCKED", T("Не допущены")], ["REVIEW_REQUIRED", T("На проверке")]];
async function viewTenders(el) {
  const listBox = h("div"), det = h("div", { class: "detail", id: "detail" });
  const bar = h("div", { class: "bar", role: "toolbar", "aria-label": T("Фильтры тендеров") });
  const q = h("input", { type: "search", value: S.filters.q, placeholder: T("Поиск"), "aria-label": T("Текстовый поиск") });
  const cat = h("input", { value: S.filters.category, placeholder: T("Категория"), "aria-label": T("Категория"), size: 10 });
  const ctry = h("input", { value: S.filters.country || T("все"), placeholder: T("Страна (ISO) или «все»"), "aria-label": T("Страна (ISO-код или «все» для всего мира)"), title: T("ISO-код страны, например CZE; «все» или пусто — весь мир"), size: 12 });
  const src = sel("source", [["", T("Все источники")], ...["ted", "cz_nen", "worldbank", "prozorro"].map((id) => [id, srcName(id)])], S.filters.source);
  src.setAttribute("aria-label", T("Источник"));
  const ALL = ["", T("все"), T("всё"), "all", "*", T("весь мир"), "world"];   // "все", "всё", "весь мир" accepted in either UI language
  const refresh = () => {
    S.filters.q = q.value.trim(); S.filters.category = cat.value.trim(); S.filters.source = src.value;
    const c = ctry.value.trim(); S.filters.country = ALL.includes(c.toLowerCase()) ? "" : c;
    if (!S.filters.country) ctry.value = T("все");
    renderTenders();
  };
  for (const [id, l] of FILTERS) bar.append(h("button", { type: "button", class: S.filters.f === id ? "on" : "", "aria-pressed": String(S.filters.f === id), onclick: () => { S.filters.f = id; refresh(); viewRefreshBar(); } }, l));
  function viewRefreshBar() { for (const b of bar.querySelectorAll("button")) { const on = b.textContent === FILTERS.find((x) => x[0] === S.filters.f)[1]; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on)); } }
  for (const i of [q, cat, ctry]) i.addEventListener("keydown", (e) => { if (e.key === "Enter") refresh(); });
  src.addEventListener("change", refresh);
  bar.append(q, cat, ctry, src, h("button", { type: "button", onclick: refresh }, T("Применить")));
  el.append(card(T("Тендеры"), FIRM.data ? null : FIRM.banner(), bar, listBox), det);
  const listNode = (d) => {
    if (!d.items.length) return stateBox("load", T("Нет тендеров по этим фильтрам."));
    return h("div", {}, h("div", { class: "mut" }, Tf(T("Показано {0} из {1} · страна: {2}"), d.items.length, d.total, S.filters.country ? S.filters.country + (d.countryIso3 ? " (" + geoName(d.countryIso3) + ")" : "") : T("весь мир")) +
      (S.filters.country ? T(" · фильтр страны применён в интерфейсе по коду/названию") + (d.partial ? T("; загружены не все тендеры") : "") : "")),
    trBar(() => d.items.map((t) => t.title), () => clear(listBox).append(listNode(d))),
    tenderTable(d.items, (k) => { S.sel = k; renderTenders(); showDetail(det, k); }));
  };
  async function renderTenders() {
    await load(listBox, async () => {
      const d = await loadTenders(); S.tenders = d;
      await trLookup(d.items.map((t) => t.title));
      return listNode(d);
    });
  }
  await renderTenders();
  if (S.sel) showDetail(det, S.sel);
}

async function showDetail(el, key) {
  await load(el, async () => {
    const [d, cl] = await Promise.all([GET("/tenders/" + encodeURI(key)), GET("/tenders/" + encodeURI(key) + "/checklist").catch((e) => ({ error: e.message }))]);
    await trLookup([d.tender.title, ...serverTexts(d.assessment, cl)]);
    return detailView(d, cl, el);
  });
  el.scrollIntoView && el.scrollIntoView({ block: "nearest" });
}
const FCHK = { turnover: T("Стоимость тендера и оборот фирмы"), guarantee: T("Лимит гарантии и стоимость тендера"), licences: T("Лицензия на экспорт"), security_certs: T("AQAP / допуск к секретным работам") };
function firmChecksView(list) {
  if (!list) return null;
  return h("div", {}, h("h3", {}, T("Сверка с данными вашей фирмы")), table([T("Проверка"), T("Статус"), T("Значение")], list.map((c) => ({ cells: [FCHK[c.id] || c.id, sb(c.status),
    c.ratio_pct != null ? c.ratio_pct + " %" : c.status === "NO_DATA" ? T("нет данных") : "—"] }))), h("p", { class: "mut" }, T("Справочные соотношения, не решение: пороги задаёт документация тендера.")));
}
function assessmentView(a) {
  const box = h("div");
  box.append(h("div", { class: "bar" }, h("div", { class: "score" }, a.fit.fit_score == null ? "—" : String(a.fit.fit_score)), sb(a.complexity), a.tier_if_cleared ? badge(T("уровень после снятия проверок: ") + ru(a.tier_if_cleared), STATUS_CLS[a.tier_if_cleared]) : null,
    sb(a.compliance_status), a.dry_run === false ? badge(T("сохранено #") + a.id, "b-ok") : (a.dry_run ? badge(T("пробная оценка"), "b-grey") : null)));
  box.append(h("p", { class: "msg", style: "background:#26365a" }, srv(a.fit.disclaimer)));
  box.append(kv([[T("Уверенность"), a.fit.confidence], [T("Неизвестные компоненты"), (a.fit.unknowns || []).join(", ") || T("нет")], [T("Оценка в USD"), a.usd_value], [T("Встречный контракт (back-to-back)"), yn(a.back_to_back)], [T("Оценено"), a.assessed_at]]));
  box.append(h("h3", {}, T("Проверки")));
  box.append(table([T("Проверка"), T("Статус"), T("Причина"), T("Нужно подтвердить")], a.gates.map((g) => ({ cells: [gname(g.id), sb(g.status), srv(g.reason), srv(g.evidence_needed)] }))));
  box.append(h("h3", {}, T("Компоненты соответствия")));
  box.append(table([T("Компонент"), T("Вес"), T("Значение"), T("Почему")], Object.entries(a.fit.components).map(([k, c]) => ({ cells: [COMP_RU[k] || k, txt(c.weight), c.value == null ? T("— (неизвестно)") : String(c.value), srv(c.why)] }))));
  if (a.ai) {
    box.append(h("h3", {}, T("Подсказка ИИ")));
    box.append(h("div", { class: "bar" }, sb(a.ai.status), a.ai.tier ? badge(T("уровень по ИИ: ") + ru(a.ai.tier), STATUS_CLS[a.ai.tier]) : null,
      a.ai_disagreement ? badge(T("ИИ не согласен с правилами"), "b-warn") : null));
    box.append(h("pre", { class: "msg", style: "background:#0a1826;overflow:auto" }, JSON.stringify(a.ai, null, 2)));
  }
  return box;
}
function detailView(d, cl, host) {
  const t = d.tender, a = d.assessment;
  const out = h("div", { class: "card" });
  out.append(h("h2", { class: "dh" }, t.title == null ? t.key : dual(t.title)));
  const link = t.source_url && /^https?:\/\//.test(t.source_url) ? h("a", { href: t.source_url, target: "_blank", rel: "noopener noreferrer" }, t.source_url) : txt(t.source_url);
  out.append(kv([[T("Ключ"), t.key], [T("Покупатель"), t.buyer ? dual(t.buyer, { keep: true }) : null], [T("Страна"), t.country], [T("Категория"), t.category], ["CPV", (t.cpv || []).join(", ")],
    [T("Стоимость"), t.estimated_value == null ? null : `${t.estimated_value} ${t.currency || ""}`], [T("Дедлайн"), t.deadline], [T("Статус"), ru(t.status)],
    [T("Источник"), link], [T("Живые данные"), provLine(t)], [T("Получено"), t.retrieved_at], [T("Качество данных"), typeof t.data_quality === "object" && t.data_quality ? JSON.stringify(t.data_quality) : t.data_quality],
    [T("Версии"), (d.versions || []).join(", ")]]));
  const asBox = h("div", { style: "margin-top:10px" }, FIRM.data ? null : FIRM.banner(), assessmentView(a), firmChecksView(d.firm_checks));
  const ai = h("input", { type: "checkbox", id: "useai" });
  const b2b = h("input", { type: "checkbox", id: "b2b" });
  const res = h("div");
  const run = async (dry) => {
    clear(res);
    try {
      const r = await POST("/assessments", { tender_key: t.key, dry_run: dry, use_ai: ai.checked, back_to_back: b2b.checked });
      if (!dry && r.id != null) { S.lastAssessment[t.key] = r.id; try { sessionStorage.setItem("wt_last_" + t.key, String(r.id)); } catch (e) { /* */ } }
      await trLookup(serverTexts(r, null));
      clear(asBox).append(FIRM.data ? null : FIRM.banner(), assessmentView(r), firmChecksView(r.firm_checks));
      res.append(msgBox("ok", dry ? T("Пробная оценка: ничего не сохранено.") : T("Оценка сохранена, № ") + r.id));
    } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); }
  };
  out.append(h("div", { class: "bar" }, h("button", { type: "button", onclick: () => run(true) }, T("Оценить (пробно)")), h("button", { type: "button", onclick: () => run(false) }, T("Сохранить оценку")),
    h("label", { class: "chk", for: "useai" }, ai, T(" подсказка ИИ")), h("label", { class: "chk", for: "b2b" }, b2b, T(" встречный контракт (back-to-back)"))), res, asBox);
  out.append(h("h3", {}, T("Кому предложить")), offerBlock(t.key));
  out.append(h("h3", {}, T("Чек-лист (черновик)")));
  if (cl.error) out.append(stateBox("err", cl.error));
  else {
    out.append(h("div", { class: "mut" }, srv(cl.note)));
    out.append(cl.items.length ? table([T("Шаг"), T("Статус"), T("Почему / что нужно")], cl.items.map((i) => ({ cells: [srv(i.step), sb(i.status), [i.why, i.evidence].filter(Boolean).length ? [i.why, i.evidence].filter(Boolean).map((x, k) => [k ? " — " : null, srv(x)]) : "—"] }))) : stateBox("load", T("Пусто")));
  }
  if ((d.reviews || []).length) { out.append(h("h3", {}, T("Подписанные проверки"))); out.append(table([T("Проверка"), T("Решение"), T("Кто"), T("Заметка")], d.reviews.map((r) => ({ cells: [txt(gname(r.gate_id)), txt(ru(r.decision)), txt(r.reviewer || r.by), txt(r.note)] })))); }
  return out;
}

/* ---------- contacts: companies met at events, tender <-> company matches, owner votes ----------
   Matching and scores come from the server (CPV + keywords + learned weights); the page only shows them and sends 👍/👎. */
const CONTACT_STATUSES = [["new", T("новый")], ["contacted", T("написали")], ["meeting", T("встреча")], ["partner", T("партнёр")], ["declined", T("отказ")]];
const CST = Object.fromEntries(CONTACT_STATUSES);
const CST_CLS = { new: "b-info", contacted: "b-warn", meeting: "b-warn", partner: "b-ok", declined: "b-grey" };
const cstBadge = (s) => badge(CST[s] || s, CST_CLS[s]);
const KW_LANGS = ["en", "uk", "cs", "ru"];
const MB_CLS = { REVIEW_REQUIRED: "b-warn", NOT_CONNECTED: "b-grey" };
const errMsg = (e) => "HTTP " + e.status + " — " + e.message;
function matchBadges(list) {
  return h("div", { class: "bar", style: "margin:0" }, (list || []).map((b) => h("span", { class: "badge " + (MB_CLS[b.level] || "b-info"), title: b.why || "" }, srv(b.text))));
}
function whyList(it) { return h("ul", { class: "why" }, (it.reasons || []).map((r) => h("li", {}, srv(r)))); }
function scoreCell(it) {
  return h("div", {}, h("b", {}, String(it.score)), h("div", { class: "mut" }, Tf(T("база {0} · отзывы {1}"), it.base_score, (it.learned_adjustment >= 0 ? "+" : "") + it.learned_adjustment)));
}
function voteBar(tenderKey, contactId, current, onDone) {
  const reason = h("input", { placeholder: T("причина (необязательно)"), "aria-label": T("Причина оценки"), size: 12, maxlength: 500 });
  const out = h("span", { class: "mut", role: "status" });
  const btn = (vote, label, title) => h("button", { type: "button", class: "sm" + (current === vote ? " on" : ""), "aria-pressed": String(current === vote), title, "aria-label": title,
    onclick: async (ev) => {
      ev.stopPropagation(); out.textContent = T("сохранение…");
      try { const r = await POST("/matches/feedback", { tender_key: tenderKey, contact_id: contactId, vote: vote, reason: reason.value.trim() || null }); out.textContent = Tf(T("сохранено · меток всего: {0}"), r.labels); if (onDone) onDone(); }
      catch (e) { if (e.status !== 401) out.textContent = errMsg(e); }
    } }, label);
  return h("div", { class: "bar vote" }, btn("up", "👍", T("Подходит")), btn("down", "👎", T("Не подходит")), reason, out);
}
function metricsView(d) {
  const p = d.precision_at_5;
  return h("div", {}, h("div", { class: "bar" }, badge(d.status === "OK" ? T("измерено") : T("недостаточно данных"), d.status === "OK" ? "b-ok" : "b-grey"),
    h("span", { class: "mut" }, Tf(T("меток: {0} (👍 {1}) · обучение: {2} · тест: {3} · порог: {4}"), d.n_labels, d.n_up, d.n_train, d.n_test, d.min_labels))),
  p ? kv([[T("precision@5 без отзывов"), String(p.baseline)], [T("precision@5 с отзывами"), String(p.learned)], [T("Разница"), (p.delta >= 0 ? "+" : "") + p.delta],
    [T("Компаний в тесте"), String(d.n_queries)]]) : null,
  h("p", { class: "mut" }, srv(d.note)),
  h("p", { class: "mut" }, T("Это ранжирование по вашим отзывам (сглаженные log-odds по CPV, ключевым словам, источнику и стране заказчика), а не обучение модели. Тест — фиксированные 20 % пар.")));
}
function contactForm(c, onSave, label) {
  const res = h("div"), kw = c.keywords || {};
  const ta = (name, val) => h("textarea", { name, rows: 2 }, val || "");
  const form = h("form", { class: "form" },
    field(T("Компания"), inp("company", "text", c.company, { required: true, minlength: 2, maxlength: 200 })),
    field(T("Страна (ISO или название)"), inp("country", "text", c.country)), field(T("Сайт"), inp("website", "text", c.website)),
    field(T("Публичный e-mail (только напечатанный)"), inp("public_email", "email", c.public_email)), field(T("Где напечатан e-mail"), inp("public_email_source", "text", c.public_email_source)),
    field(T("Продукты / возможности"), ta("products", c.products)), field(T("Префиксы CPV (через запятую)"), inp("cpv_prefixes", "text", (c.cpv_prefixes || []).join(", "))),
    ...KW_LANGS.map((l) => field(Tf(T("Ключевые слова {0} (через запятую; * — начало слова)"), l.toUpperCase()), inp("kw_" + l, "text", (kw[l] || []).join(", ")))),
    field(T("Событие"), inp("event", "text", c.event)), field(T("Источник"), inp("source", "text", c.source)),
    field(T("Статус"), sel("status", CONTACT_STATUSES, c.status || "new")), field(T("Следующий шаг"), inp("next_step", "text", c.next_step)),
    field(T("Дата следующего шага"), inp("next_step_date", "date", c.next_step_date)), field(T("Заметки"), ta("notes", c.notes)),
    field(T("Имя человека (только если вводите сами)"), inp("person_name", "text", c.person_name, { autocomplete: "off" })),
    field(T("Телефон человека"), inp("person_phone", "tel", c.person_phone, { autocomplete: "off" })),
    h("button", { type: "submit" }, label));
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); clear(res);
    const v = formData(form), csv = (s) => String(s || "").split(",").map((x) => x.trim()).filter(Boolean), opt = (s) => String(s || "").trim() || null;
    const body = { company: v.company.trim(), country: opt(v.country), website: opt(v.website), public_email: opt(v.public_email), public_email_source: opt(v.public_email_source),
      products: v.products, cpv_prefixes: csv(v.cpv_prefixes), keywords: Object.fromEntries(KW_LANGS.map((l) => [l, csv(v["kw_" + l])])), event: opt(v.event), source: opt(v.source),
      status: v.status, next_step: opt(v.next_step), next_step_date: opt(v.next_step_date), notes: v.notes, person_name: opt(v.person_name), person_phone: opt(v.person_phone) };
    try { await onSave(body); res.append(msgBox("ok", T("Сохранено"))); }
    catch (x) { if (x.status !== 401) res.append(msgBox("err", errMsg(x))); }
  });
  return h("div", {}, form, res);
}
function matchesForContact(m, id, reload) {
  const st = sel("mstatus", [["open", T("только открытые")], ["all", T("все статусы")]], S.cmStatus || "open");
  st.setAttribute("aria-label", T("Какие тендеры показывать"));
  st.addEventListener("change", () => { S.cmStatus = st.value; reload(); });
  const head = h("div", { class: "bar" }, st, h("span", { class: "mut" }, Tf(T("найдено: {0} · показано: {1} · ваших отзывов по компании: {2}"), m.total, m.items.length, m.learning.labels)),
    m.blocked_total ? badge(Tf(T("заблокировано проверками: {0}"), m.blocked_total), "b-bad") : null);
  const blocked = (m.blocked || []).length ? h("details", {}, h("summary", {}, T("Заблокированные совпадения (санкции / эмбарго)")),
    h("ul", { class: "list" }, m.blocked.map((b) => h("li", {}, txt(b.title), b.reasons.map((r) => h("div", { class: "mut" }, srv(r))))))) : null;
  if (!m.items.length) return h("div", {}, head, stateBox("load", T("Совпадений нет: общих CPV и ключевых слов с тендерами в базе не найдено.")), blocked);
  return h("div", {}, head, table([T("Тендер"), T("Заказчик"), T("Стоимость"), T("Дедлайн"), T("Оценка"), T("Почему"), T("Проверки"), T("Ваш отзыв")], m.items.map((it) => {
    const t = it.tender;
    return { cells: [h("div", {}, dual(t.title), h("div", { class: "bar", style: "margin:4px 0 0" }, h("button", { type: "button", class: "sm", onclick: () => { S.sel = t.key; S.filters.country = ""; go("tenders"); } }, T("Открыть")),
      ru(t.status) ? h("span", { class: "mut" }, ru(t.status)) : null), h("div", { class: "src-l" }, provLine(t))),
    t.buyer ? dual(t.buyer, { keep: true }) : "—", t.estimated_value == null ? "—" : fmtN(t.estimated_value) + " " + txt(t.currency), txt((t.deadline || "").slice(0, 10)),
    scoreCell(it), whyList(it), matchBadges(it.badges), voteBar(t.key, id, it.vote, reload)] };
  })), blocked);
}
function download(text, name, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: name }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
}
async function showContact(el, id) {
  await load(el, async () => {
    const c = (await GET("/contacts/" + id)).contact;
    const res = h("div"), pv = h("div"), mBox = h("div");
    const pitch = h("button", { type: "button", onclick: async () => {
      clear(pv);
      try {
        const r = await raw("GET", "/contacts/" + id + "/pitch");
        const text = await r.text();
        if (!r.ok) { let d; try { d = JSON.parse(text).detail; } catch (e) { d = text; } throw new ApiError(r.status, d); }
        download(text, "pitch-contact-" + id + ".md", "text/markdown");
        pv.append(h("pre", { class: "msg pitch" }, text));
      } catch (e) { if (e.status !== 401) pv.append(msgBox("err", errMsg(e))); }
    } }, T("Предложение (.md): топ-5 открытых тендеров"));
    const lesson = h("button", { type: "button", title: T("Только по вашему нажатию; без имён, телефонов, e-mail и заметок"), onclick: async () => {
      clear(res);
      try { const r = await POST("/contacts/" + id + "/lesson"); res.append(msgBox("ok", Tf(T("Урок сохранён в памяти Bossman: {0}"), txt(r.path)))); }
      catch (e) { if (e.status !== 401) res.append(msgBox("err", errMsg(e))); }
    } }, T("Отправить урок в Bossman"));
    const kwText = KW_LANGS.filter((l) => (c.keywords || {})[l]).map((l) => l.toUpperCase() + ": " + c.keywords[l].join(", ")).join(" · ");
    const site = c.website ? h("a", { href: /^https?:\/\//i.test(c.website) ? c.website : "https://" + c.website, target: "_blank", rel: "noopener noreferrer" }, c.website) : null;
    const person = "person_name" in c ? [c.person_name, c.person_phone].filter(Boolean).join(" · ") : (c.has_person ? T("есть (скрыто для вашей роли)") : null);
    const out = h("div", { class: "card" }, h("h2", {}, c.company), h("div", { class: "bar" }, cstBadge(c.status), c.country ? badge(c.country, "b-grey") : null),
      kv([[T("Сайт"), site], [T("Публичный e-mail"), c.public_email ? c.public_email + " (" + txt(c.public_email_source) + ")" : null], [T("Продукты / возможности"), c.products],
        ["CPV", (c.cpv_prefixes || []).join(", ")], [T("Ключевые слова"), kwText], [T("Событие"), c.event], [T("Источник"), c.source],
        [T("Следующий шаг"), [c.next_step, c.next_step_date].filter(Boolean).join(" · ")], [T("Заметки"), c.notes], [T("Человек"), person || null]]),
      h("div", { class: "bar" }, pitch, lesson), res, pv,
      h("details", {}, h("summary", {}, T("Изменить контакт")), contactForm(c, async (body) => { await api("PATCH", "/contacts/" + id, body); showContact(el, id); }, T("Сохранить изменения"))),
      h("h3", {}, T("Подходящие тендеры")), mBox);
    const reload = () => load(mBox, async () => {
      const m = await GET("/contacts/" + id + "/matches?k=20&status=" + (S.cmStatus || "open"));
      await trLookup(m.items.map((i) => i.tender.title));
      return matchesForContact(m, id, reload);
    });
    reload();
    return out;
  });
  if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
}
function monetizationRadarView(d) {
  const s = d.summary || {}, items = d.items || [], pv = h("div");
  const dlBtn = h("button", { type: "button", onclick: async () => {
    clear(pv);
    try {
      const r = await raw("GET", "/monetization/playbook");
      const text = await r.text();
      if (!r.ok) { let detail; try { detail = JSON.parse(text).detail; } catch (e) { detail = text; } throw new ApiError(r.status, detail); }
      download(text, "monetization-playbook.md", "text/markdown");
      pv.append(h("pre", { class: "msg pitch" }, text));
    } catch (e) { if (e.status !== 401) pv.append(msgBox("err", errMsg(e))); }
  } }, T("Скачать плейбук монетизации (.md)"));
  const head = h("div", { class: "bar" },
    badge(Tf(T("Контактов с открытыми тендерами: {0} из {1} · пар: {2} · уникальных тендеров: {3} · быстрый старт: {4} · в лимите $50k: {5} · пайплайн USD: ${6} · агентская база (3%): ${7}"),
      s.contacts_with_open_matches, s.contacts_total, s.matched_pairs_total, s.unique_open_tenders_matched, s.fast_start_uncontrolled_pairs, s.b2b_within_50k_limit_pairs, s.unique_pipeline_usd, s.unique_agency_fee_base_usd), "b-ok"),
    dlBtn);
  if (!items.length) return h("div", {}, head, stateBox("load", T("Открытых возможностей монетизации пока нет.")));
  return h("div", {}, head, pv,
    table([T("Поставщик"), T("Тендер"), T("Стоимость"), T("Агентская 3%"), T("Back-to-Back (15% маржа)"), T("Трек / Лимит"), T("Следующий шаг")], items.slice(0, 15).map((it) => {
      const c = it.contact, t = it.tender, ec = it.economics, ag = ec.agency_introducer, b2b = ec.back_to_back_supply, reg = it.regulatory;
      const valTxt = ec.priced ? `${ec.estimated_value} ${ec.currency || ""}` + (ec.estimated_value_usd ? ` (~$${ec.estimated_value_usd})` : "") : T("рамочный");
      const agTxt = ec.priced ? `${ag.commission_base} ${ec.currency || ""}` + (ag.commission_base_usd ? ` (~$${ag.commission_base_usd})` : "") : "2–5 %";
      const b2bTxt = b2b ? `${b2b.gross_profit} ${ec.currency || ""}` + (b2b.gross_profit_usd ? ` (~$${b2b.gross_profit_usd})` : "") : "—";
      const cap = b2b && b2b.capital_unmatched ? b2b.capital_unmatched.status : "WITHIN_LIMIT";
      return { cells: [
        h("div", {}, h("b", {}, c.company), c.country ? h("span", { class: "mut" }, " · " + c.country) : null),
        h("div", {}, txt(t.key), h("div", { class: "mut" }, txt((t.title || "").slice(0, 65)))),
        txt(valTxt), txt(agTxt), txt(b2bTxt),
        h("div", { class: "bar", style: "margin:0" }, badge(reg.track, reg.fast_start ? "b-ok" : "b-warn"), sb(cap)),
        txt(it.next_step_ru)] };
    })));
}
async function viewContacts(el) {
  const listBox = h("div"), det = h("div", { class: "detail" }), metBox = h("div"), monBox = h("div");
  const st = sel("cstatus", [["", T("Все статусы")], ...CONTACT_STATUSES], S.cfilter || "");
  st.setAttribute("aria-label", T("Статус контакта"));
  const q = h("input", { type: "search", value: S.cq || "", placeholder: T("Поиск: компания, продукты, событие"), "aria-label": T("Поиск контактов") });
  const refresh = () => { S.cfilter = st.value; S.cq = q.value.trim(); renderList(); };
  st.addEventListener("change", refresh);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") refresh(); });
  const create = contactForm({}, async (body) => { const r = await POST("/contacts", body); S.contact = r.id; renderList(); showContact(det, r.id); }, T("Добавить контакт"));
  el.append(card(T("Контакты и компании"),
    h("p", { class: "mut" }, T("Компании с выставок и встреч. Совпадения с тендерами считаются по CPV и ключевым словам; ваши 👍/👎 меняют порядок. Имя и телефон человека видят только аналитик и владелец; в журнал аудита, предложение и урок Bossman они не попадают.")),
    h("div", { class: "bar" }, st, q, h("button", { type: "button", onclick: refresh }, T("Применить"))), listBox), det,
  card(T("Радар монетизации тендеров (Форум)"), monBox),
  card(T("Новый контакт"), h("details", {}, h("summary", {}, T("Открыть форму")), create)), card(T("Обучение по отзывам"), metBox));
  async function renderList() {
    await load(listBox, async () => {
      const p = new URLSearchParams();
      if (S.cfilter) p.set("status", S.cfilter);
      if (S.cq) p.set("q", S.cq);
      const d = await GET("/contacts" + (p.toString() ? "?" + p : ""));
      if (!d.items.length) return stateBox("load", T("Контактов нет. Добавьте компанию или запустите seed: python -m warterminal.seed_forum_20261021"));
      return table([T("Компания"), T("Страна"), T("Статус"), T("Следующий шаг"), T("Дата"), T("Событие")], d.items.map((c) => ({ key: c.id, cls: S.contact === c.id ? "sel" : "", cells: [
        h("b", {}, c.company), txt(c.country), cstBadge(c.status), txt(c.next_step), txt(c.next_step_date), txt(c.event)] })),
      (r) => { S.contact = r.key; renderList(); showContact(det, r.key); });
    });
  }
  load(monBox, async () => monetizationRadarView(await GET("/monetization/opportunities?k_per_contact=5&max_items=20")));
  load(metBox, async () => metricsView(await GET("/matches/metrics")));
  await renderList();
  if (S.contact) showContact(det, S.contact);
}
function offerBlock(key) {
  const box = h("div");
  const reload = () => load(box, async () => {
    const m = await GET("/tenders/" + encodeURI(key) + "/matches?k=20");
    const hidden = m.skipped_declined ? " " + Tf(T("(отказавшихся скрыто: {0})"), m.skipped_declined) : "";
    const blocked = (m.blocked || []).length ? h("div", { class: "mut" }, T("Заблокировано проверками: "), m.blocked.map((b) => h("div", {}, txt(b.company) + " — ", b.reasons.map((r) => srv(r))))) : null;
    if (!m.items.length) return h("div", {}, stateBox("load", T("Подходящих компаний среди контактов нет.") + hidden), blocked);
    return h("div", {}, h("div", { class: "mut" }, Tf(T("компаний: {0} · по CPV и ключевым словам с поправкой по вашим отзывам"), m.total) + hidden),
      table([T("Компания"), T("Статус"), T("Оценка"), T("Почему"), T("Проверки"), T("Ваш отзыв")], m.items.map((it) => ({ cells: [
        h("div", {}, h("button", { type: "button", class: "lnk", onclick: () => { S.contact = it.contact.id; go("contacts"); } }, it.contact.company), it.contact.country ? h("span", { class: "mut" }, " · " + it.contact.country) : null),
        cstBadge(it.contact.status), scoreCell(it), whyList(it), matchBadges(it.badges), voteBar(key, it.contact.id, it.vote, reload)] }))), blocked);
  });
  reload();
  return box;
}

/* ---------- compliance ---------- */
async function viewCompliance(el) {
  const out = h("div"), res = h("div"), fb = h("div");
  const sk = S.sel || "";
  let aid = S.lastAssessment[sk];
  try { aid = aid || sessionStorage.getItem("wt_last_" + sk); } catch (e) { /* */ }
  const aInp = inp("aid", "number", aid || "", { min: 1, "aria-label": T("№ сохранённой оценки") });
  el.append(card(T("Подпись проверок (комплаенс / владелец)"), h("p", { class: "mut" }, T("Выбранный тендер: ") + (sk || T("— (выберите в разделе «Закупки»)")) + T(". Подписываются только сохранённые оценки.")),
    h("div", { class: "bar" }, field(T("№ сохранённой оценки"), aInp), h("button", { type: "button", onclick: loadA }, T("Загрузить оценку"))), out, res),
  card(T("Подача заявки"), h("p", { class: "msg", style: "background:#4d252a" }, T("Подача заявок, контакт с контрагентами и закупка контролируемых товаров недоступны без отдельного решения владельца и юридической проверки.")),
    h("div", { class: "bar" }, h("button", { type: "button", disabled: true, "aria-disabled": "true", title: T("Недоступно без юридической проверки и одобрения владельца") }, T("Подать заявку")),
      h("button", { type: "button", onclick: async () => { clear(fb); try { await POST("/actions/submit-bid"); fb.append(msgBox("err", T("неожиданно: сервер не отказал"))); } catch (e) { if (e.status !== 401) fb.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); } } }, T("Проверить отказ сервера"))), fb));
  if (aid) loadA();
  async function loadA() {
    clear(res);
    await load(out, async () => {
      const a = await GET("/assessments/" + encodeURIComponent(aInp.value));
      await trLookup(serverTexts(a, null));
      const form = h("form", { class: "form" },
        field(T("Проверка"), sel("gate_id", a.gates.map((g) => [g.id, `${gname(g.id)} (${ru(g.status)})`]))), field(T("Решение"), sel("decision", [["cleared", T("снять проверку")], ["rejected", T("отклонить")]])),
        field(T("Заметка (мин. 5 символов)"), inp("note", "text", "", { minlength: 5, required: true })), field(T("Ссылка на юридическое заключение (для военных товаров)"), inp("legal_reference")),
        h("button", { type: "submit" }, T("Подписать")));
      submit(form, (v) => POST(`/assessments/${a.id}/review`, { gate_id: v.gate_id, decision: v.decision, note: v.note, legal_reference: v.legal_reference || null }), res);
      return h("div", {}, h("div", { class: "bar" }, badge(T("оценка #") + a.id), badge(a.tender_key), sb(a.compliance_status)),
        table([T("Проверка"), T("Статус"), T("Причина"), T("Нужно")], a.gates.map((g) => ({ cells: [gname(g.id), sb(g.status), srv(g.reason), srv(g.evidence_needed)] }))), form);
    });
  }
}

/* ---------- company: the firm entered in the popup (kept in this tab only, never stored on the server) ---------- */
async function viewFit(el) {
  const body = h("div");
  el.append(card(T("Данные вашей фирмы"), body));
  if (!FIRM.data) {
    body.append(FIRM.banner(), h("p", { class: "mut" }, T("Терминал ничего не подставляет за вас: без ваших данных оценка соответствия не считается.")));
    return;
  }
  body.append(kv(FIRM.rows()), h("p", { class: "mut" }, T("Данные хранятся только в этой вкладке браузера (и на этом устройстве, если вы отметили «Запомнить»). На сервере, в переводчике, журналах и экспорте их нет.")),
    h("div", { class: "bar" }, h("button", { type: "button", onclick: () => FIRM.reopen() }, T("Изменить данные фирмы"))));
}
function viewRefresh() { render(); }

/* ---------- finance ---------- */
const FIN_FIELDS = [["currency", "text", "USD"], ["purchase", "number", ""], ["freight", "number", 0], ["insurance", "number", 0], ["duties_pct", "number", 0], ["certification", "number", 0],
  ["financing", "number", 0], ["risk_reserve_pct", "number", 0], ["overhead", "number", 0], ["bid_price", "number", ""], ["supplier_prepay_pct", "number", 30], ["delivery_day", "number", 30],
  ["acceptance_day", "number", 40], ["customer_pay_delay_days", "number", 30], ["vat_import", "number", 0], ["vat_recovery_days", "number", 60], ["logistics_cost", "number", 0],
  ["buyer_prepay_pct", "number", 0], ["annual_rate", "number", 0.12], ["capital_limit_usd", "number", 50000]];
async function viewFinance(el) {
  if (!FIRM.data) {
    el.append(card(T("Оценка себестоимости и денежного потока"), FIRM.banner(), h("p", { class: "mut" }, T("Калькулятор отключён: он использует данные фирмы (оборот, гарантия). Без них приложение ничего не предполагает."))));
    return;
  }
  const res = h("div"), outBox = h("div");
  const form = h("form", { class: "form" });
  for (const [k, ty, def] of FIN_FIELDS) form.append(field(lbl(k), inp(k, ty, def, ty === "number" ? { step: "any", required: k === "purchase" || k === "bid_price" } : {})));
  form.append(field(T("Курсы к USD (JSON, напр. {\"EUR\": 1.1})"), inp("fx", "text", "{}")), chk("order_confirmed", lbl("order_confirmed")), chk("supplier_contract_signed", lbl("supplier_contract_signed")), h("button", { type: "submit" }, T("Рассчитать")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); clear(res); clear(outBox);
    const v = formData(form), body = {};
    for (const [k, ty] of FIN_FIELDS) body[k] = ty === "number" ? Number(v[k]) : v[k];
    try { body.fx = JSON.parse(v.fx || "{}"); } catch (x) { res.append(msgBox("err", T("Курсы: некорректный JSON"))); return; }
    body.order_confirmed = v.order_confirmed; body.supplier_contract_signed = v.supplier_contract_signed;
    try { outBox.append(financeResult(await POST("/finance/estimate", body))); }
    catch (x) { if (x.status !== 401) res.append(msgBox("err", "HTTP " + x.status + " — " + x.message)); }
  });
  el.append(card(T("Оценка себестоимости и денежного потока"), h("p", { class: "mut" }, T("Все числа вводит человек; приложение не подставляет рыночных допущений. Суммы показаны как строки от API.")), form, res), outBox);
}
function financeResult(r) {
  const box = h("div", { class: "grid", style: "margin-top:12px" });
  box.append(card(T("Себестоимость с доставкой (") + r.currency + ")", kv(Object.entries(r.landed).map(([k, v]) => [lbl(k), v]))));
  box.append(card(T("Маржа и наценка"), kv([[T("Цена заявки"), r.metrics.bid], [T("Себестоимость с доставкой"), r.metrics.landed_cost], [T("Прибыль"), r.metrics.profit], [T("Маржа, % (от цены)"), r.metrics.margin_pct], [T("Наценка, % (от себестоимости)"), r.metrics.markup_pct]])));
  box.append(card(T("Денежный поток"), kv([[T("Пик потребности"), r.cashflow.peak_funding_need], [T("День пика"), r.cashflow.peak_day], [T("Итоговый баланс"), r.cashflow.final_balance], [T("Пик в USD"), r.peak_funding_need_usd]]),
    table([T("День"), T("Событие"), T("Сумма"), T("Накопленно")], r.cashflow.timeline.map((e) => ({ cells: [e.day, srv(e.label), e.amount, e.cumulative] })))));
  box.append(card(T("Стресс: задержка оплаты"), table([T("Доп. дней"), T("Пик"), T("Доп. стоимость"), T("Прибыль"), T("Маржа %"), T("Итоговый баланс")], r.stress.map((s) => ({ cells: [s.extra_delay_days, s.peak_funding_need, s.extra_carry_cost, s.profit, txt(s.margin_pct), s.final_balance] })))));
  if (r.firm_context) box.append(card(T("Заявка и данные фирмы"), kv([[T("Цена заявки к годовому обороту, %"), r.firm_context.bid_vs_turnover_pct == null ? T("нет данных") : r.firm_context.bid_vs_turnover_pct],
    [T("Цена заявки к лимиту гарантии, %"), r.firm_context.bid_vs_guarantee_pct == null ? T("нет данных") : r.firm_context.bid_vs_guarantee_pct]])));
  box.append(card(T("Проверка лимита капитала"), h("div", { class: "bar" }, sb(r.capital.status), badge(T("разрешено: ") + yn(r.capital.allowed)), badge(T("нужно одобрение владельца: ") + yn(r.capital.needs_owner_approval))), h("p", {}, txt(r.capital.reason)), h("p", { class: "mut" }, txt(r.disclaimer))));
  return box;
}

/* ---------- fx tracker (CNB + ECB log, cross-rates, USDT/USD calculator) ---------- */
const fxn = (s, nd) => (s == null || s === "" || !isFinite(Number(s)) ? "—" : Number(s).toFixed(nd));
async function viewFx(el) {
  const box = h("div"), res = h("div");
  const amount = inp("amount", "number", 1000, { step: "any", min: "0", required: true }), base = inp("baseline", "date", "");
  const form = h("form", { class: "form" }, field(T("Сумма USDT / USD"), amount), field(T("Дата базы (пусто = первая запись)"), base), h("button", { type: "submit" }, T("Пересчитать")));
  form.addEventListener("submit", (e) => { e.preventDefault(); paint(); });
  el.append(card(T("Курсы / FX: ежедневный учёт"),
    h("p", { class: "mut" }, T("Источники: фиксинг Чешского национального банка и референсные курсы ЕЦБ; XE.com не используется (его условия запрещают автоматический сбор).")),
    h("p", { class: "mut" }, T("Только курсы «середина рынка»: комиссии и спреды бирж и обменников не учтены. USDT принят равным USD — допущение, не измерено.")),
    h("p", { class: "mut" }, T("Это учёт и статистика прошлых значений: прогнозов цен, торговых советов и утверждений о росте здесь нет.")), form, res), box);
  paint();
  function paint() {
    load(box, async () => {
      const q = "?amount=" + encodeURIComponent(amount.value || "1000") + (base.value ? "&baseline=" + encodeURIComponent(base.value) : "");
      const r = await GET("/fx/track/report" + q), out = h("div");
      if (!r.n_rows) return stateBox("load", T("Записей ещё нет: сбор курсов выполняет задача BossmanFX-AM / BossmanFX-PM в Планировщике."));
      out.append(card(T("Последние курсы"), table([T("Источник:пара"), T("Курс"), T("Дата курса"), T("Снято (UTC)")],
        Object.entries(r.latest).sort().map(([k, v]) => ({ cells: [k, v.rate, txt(v.rate_date), v.observed_at] }))),
        h("p", { class: "mut" }, Tf(T("Записей: {0}, в отчёте: {1}, помечено или отброшено: {2}"), r.n_rows, r.n_usable, r.n_flagged))));
      if (r.cross) {
        const c = r.cross, t = r.triangle;
        out.append(card(T("Кросс-курсы"), kv([["1 USD → EUR", fxn(c.USDEUR, 6)], ["1 USD → CZK", fxn(c.USDCZK, 4)], ["1 EUR → CZK", fxn(c.EURCZK, 4)], ["1 EUR → USD", fxn(c.EURUSD, 6)],
          ["1 CZK → EUR", fxn(c.CZKEUR, 6)], ["1 CZK → USD", fxn(c.CZKUSD, 6)], [T("USDT → USD"), T("1 (допущение, не измерено)")]]),
          h("h3", {}, T("Проверка треугольника USD → EUR → CZK")),
          kv([[T("Напрямую USD → CZK (ЧНБ)"), fxn(t.direct_usd_czk, 4)], [T("Через EUR (ЕЦБ × ЧНБ)"), fxn(t.via_eur_usd_czk, 4)], [T("Разрыв, %"), fxn(t.spread_pct, 3)]]),
          h("p", { class: "mut" }, T("Фиксинги публикуются в разное время суток, поэтому небольшой разрыв нормален."))));
      }
      const k = r.calc;
      if (k && k.status === "OK") {
        const row = (cur) => ({ cells: [cur, k.baseline[cur], k.today[cur], k.diff[cur].abs, k.diff[cur].pct + " %"] });
        out.append(card(Tf(T("Калькулятор: {0} USDT, база {1} → сегодня {2}"), k.amount_usdt, k.baseline_day, k.today_day),
          table([T("Валюта"), T("На дату базы"), T("Сегодня"), T("Разница"), T("Разница, %")], [row("EUR"), row("CZK")]),
          h("p", { class: "mut" }, T("Маршрут: крипта → USD → USDT → EUR (ЕЦБ) или CZK (ЧНБ). Комиссии и спреды не учтены."))));
      } else out.append(card(T("Калькулятор"), stateBox("load", T("Нет данных для расчёта: нужны ЧНБ (USD, EUR) и ЕЦБ (EUR/USD) за дату базы и сегодня."))));
      out.append(card(T("Статистика по датам курса"), table([T("Серия"), "n", "min", "max", T("среднее"), T("ст. откл."), T("изменение за период, %"), T("посл. дневное, %"), T("волатильность, %")],
        Object.entries(r.stats).sort().map(([key, s]) => ({ cells: [key, s.n, fxn(s.min, 4), fxn(s.max, 4), fxn(s.mean, 4), fxn(s.stdev, 4), fxn(s.change_pct_total, 3), fxn(s.last_daily_change_pct, 3), fxn(s.volatility_daily_pct, 3)] }))),
        h("p", { class: "mut" }, T("Волатильность — стандартное отклонение дневных изменений; нужны минимум 3 даты. Статистика ничего не предсказывает."))));
      const pts = [];
      for (const [key, arr] of Object.entries(r.series)) for (const p of arr) pts.push([p.rate_date, key, p.rate]);
      pts.sort().reverse();
      out.append(card(T("Серия (последние 60)"), table([T("Дата курса"), T("Серия"), T("Курс")], pts.slice(0, 60).map((p) => ({ cells: p })))));
      out.append(card(T("Последние сборы"), table([T("Время (UTC)"), T("Итог"), T("Детали")], (r.runs || []).map((x) => ({ cells: [x.started_at, x.outcome, txt(x.detail)] })))));
      return out;
    });
  }
}
/* ---------- deals ---------- */
async function viewDeals(el) {
  const res = h("div"), list = h("div");
  const form = h("form", { class: "form" },
    field(lbl("title"), inp("title", "text", "", { required: true })), field(lbl("tender_key"), inp("tender_key", "text", S.sel || "")), field(lbl("buyer_country"), inp("buyer_country", "text", "", { required: true })),
    field(lbl("goods"), inp("goods", "text", "", { required: true })), field(T("Коды CPV (через запятую)"), inp("cpv")), field(lbl("order_value"), inp("order_value", "number", "", { step: "any" })), field(lbl("currency"), inp("currency")),
    chk("order_confirmed", lbl("order_confirmed")), chk("supplier_contract_signed", lbl("supplier_contract_signed")),
    field(lbl("s_name"), inp("s_name", "text", "", { required: true, minlength: 2 })), field(lbl("s_country"), inp("s_country", "text", "", { required: true, minlength: 2, maxlength: 3 })),
    field(lbl("s_cpv"), inp("s_cpv")), field(lbl("s_url"), inp("s_url")), chk("s_confirmed", lbl("s_confirmed")), field(lbl("s_notes"), inp("s_notes")),
    h("button", { type: "submit" }, T("Создать сделку / состыковать")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); clear(res);
    const v = formData(form), csv = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const body = { title: v.title, tender_key: v.tender_key || null, buyer_country: v.buyer_country, goods: v.goods, cpv: csv(v.cpv), order_value: v.order_value === "" ? null : Number(v.order_value),
      currency: v.currency || null, order_confirmed: v.order_confirmed, supplier_contract_signed: v.supplier_contract_signed,
      supplier: { name: v.s_name, country: v.s_country, cpv_prefixes: csv(v.s_cpv), confirmed: v.s_confirmed, source_url: v.s_url || null, notes: v.s_notes } };
    try { const r = await POST("/deals", body); res.append(msgBox("ok", Tf(T("Сделка #{0}: состыкована — {1}, санкционная проверка — {2}. {3}"), r.id, yn(r.matched), ru(r.screening.screening), r.next))); loadList(); }
    catch (x) { if (x.status !== 401) res.append(msgBox("err", "HTTP " + x.status + " — " + x.message)); }
  });
  el.append(card(T("Заказ и поставщик"), form, res), card(T("Сделки"), list));
  loadList();
  function loadList() {
    load(list, async () => {
      const d = await GET("/deals");
      if (!d.items.length) return stateBox("load", T("Сделок пока нет."));
      return table(["#", T("Название"), T("Тендер"), T("Страна"), T("Поставщик"), T("Состыкована"), T("Санкции"), T("Одобрено владельцем"), ""], d.items.map((x) => ({ cells: [x.id, x.title, txt(x.tender_key), x.buyer_country, x.supplier && x.supplier.name,
        badge(yn(x.matched), x.matched ? "b-ok" : "b-grey"), x.screening && sb(x.screening.screening), badge(yn(x.owner_approved), x.owner_approved ? "b-ok" : "b-grey"),
        h("button", { type: "button", onclick: async (ev) => { clear(res); try { const r = await POST(`/deals/${x.id}/request-approval`); res.append(msgBox("ok", T("Запрос одобрения #") + r.approval_id)); } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); } } }, T("Запросить одобрение владельца"))] })));
    });
  }
}

/* ---------- learning ---------- */
async function viewLearning(el) {
  const cal = h("div"), prop = h("div"), res = h("div");
  const fd = h("form", { class: "form" }, field(lbl("tender_key"), inp("tender_key", "text", S.sel || "", { required: true })), field(lbl("decision"), sel("decision", [["go", T("участвовать")], ["no_go", T("не участвовать")], ["watch", T("наблюдать")]])),
    field(lbl("reasons"), inp("reasons", "text", "", { required: true, minlength: 3 })), h("button", { type: "submit" }, T("Записать решение (владелец)")));
  const fo = h("form", { class: "form" }, field(lbl("tender_key"), inp("tender_key", "text", S.sel || "", { required: true })), field(lbl("result"), sel("result", [["won", T("выигран")], ["lost", T("проигран")], ["withdrawn", T("отозван")], ["not_submitted", T("не подавали")]])),
    field(lbl("actual_margin_pct"), inp("actual_margin_pct", "number", "", { step: "any" })), field(lbl("note"), inp("note")), h("button", { type: "submit" }, T("Записать исход (владелец)")));
  submit(fd, (v) => POST("/learning/decisions", v), res);
  submit(fo, (v) => POST("/learning/outcomes", { tender_key: v.tender_key, result: v.result, actual_margin_pct: v.actual_margin_pct === "" ? null : Number(v.actual_margin_pct), note: v.note }), res);
  const dl = h("button", { type: "button", onclick: async () => {
    clear(res);
    try {
      const r = await raw("GET", "/learning/dataset");
      if (!r.ok) { const t = await r.text(); let d; try { d = JSON.parse(t).detail; } catch (e) { d = t; } throw new ApiError(r.status, d); }
      const blob = await r.blob(), url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: "learning_dataset.txt" }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
      res.append(msgBox("ok", T("Датасет скачан (") + blob.size + T(" байт).")));
    } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); }
  } }, T("Скачать датасет (владелец)"));
  const apply = h("button", { type: "button", onclick: async () => { clear(res); try { const r = await POST("/learning/proposal/apply"); res.append(msgBox("ok", T("Запрос одобрения #") + r.approval_id)); } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); } } }, T("Предложить применить веса (владелец)"));
  el.append(card(T("Калибровка"), cal), card(T("Предложение весов"), prop, h("div", { class: "bar" }, apply)),
    card(T("Решения и исходы"), h("h3", {}, T("Решение владельца")), fd, h("h3", {}, T("Исход тендера")), fo, h("div", { class: "bar" }, dl), res));
  load(cal, async () => {
    const d = await GET("/learning/calibration");
    return h("div", {}, h("p", {}, txt(d.note)), kv([[T("Закрытых исходов"), d.total_closed_outcomes], [T("Владелец против модели"), JSON.stringify(d.owner_vs_model)]]),
      Object.keys(d.by_predicted_tier).length ? table([T("Уровень"), T("Всего"), T("Выиграно"), T("Сглаженная доля побед"), T("Достаточно данных")], Object.entries(d.by_predicted_tier).map(([k, r]) => ({ cells: [ru(k), r.n, r.won, r.win_rate_smoothed, yn(r.enough_data)] }))) : stateBox("load", T("Данных калибровки нет.")));
  });
  load(prop, async () => {
    const d = await GET("/learning/proposal");
    if (d.status !== "PROPOSAL") return h("div", {}, h("div", { class: "bar" }, badge(ru(d.status), "b-grey")), h("p", {}, Tf(T("Данных недостаточно: есть {0}, нужно {1}."), d.n, d.needed)), kv(Object.entries(d.current_weights || {})));
    return h("div", {}, kv(Object.entries(d.weights)), h("pre", { class: "msg", style: "overflow:auto" }, JSON.stringify(d.evidence, null, 2)));
  });
}

/* ---------- sources ---------- */
async function viewSources(el) {
  const res = h("div"), box = h("div"), ref = h("div");
  /* each button posts once, shows the server result or its error verbatim, then refreshes status */
  const act = (label, path, body, role) => h("button", { type: "button", title: "POST /api/v1" + path + " " + JSON.stringify(body) + T(" — роль «") + ROLE_RU[role] + "»", onclick: async (ev) => {
    const b = ev.currentTarget; clear(res); b.disabled = true;
    res.append(stateBox("load", label + T(": выполняется…")));
    try { const r = await POST(path, body); clear(res).append(msgBox("ok", label + T(" — готово: ") + JSON.stringify(r))); S.tenders = null; }
    catch (e) { clear(res); if (e.status !== 401) res.append(msgBox("err", label + " — HTTP " + e.status + " — " + e.message)); }
    finally { b.disabled = false; loadHealth(); loadBadges(); LIVE.strip(); refresh(); refRefresh(); }
  } }, label);
  el.append(card(T("Источники данных"),
    h("h3", {}, T("Тендеры (роль «аналитик»)")),
    h("div", { class: "bar" }, act(T("TED: Чехия"), "/ingest", { source: "ted", country: "CZE" }, "analyst"),
      act(T("TED: весь мир"), "/ingest", { source: "ted", country: null }, "analyst"), act(T("ISVZ/NEN (Чехия)"), "/ingest", { source: "cz" }, "analyst"),
      act(T("Всемирный банк (весь мир)"), "/ingest", { source: "worldbank" }, "analyst"), act(T("Prozorro (Украина)"), "/ingest", { source: "prozorro" }, "analyst")),
    h("h3", {}, T("Справочники")),
    h("div", { class: "bar" }, act(T("Загрузить санкции (OFAC+ООН)"), "/sanctions/fetch", { sources: ["ofac", "un"] }, "compliance"), act(T("Курсы ЧНБ"), "/fx/fetch", {}, "analyst"),
      act(T("Конфликты UCDP"), "/conflicts/fetch", {}, "compliance")),
    h("p", { class: "mut" }, T("Все источники публичные и читаются только на чтение, без логинов и платных сервисов. Недоступный источник помечается ошибкой, старые или примерные данные вместо него не показываются. Санкции и конфликты требуют роль «комплаенс».")),
    res, h("h3", {}, T("Справочные данные")), ref, h("h3", {}, T("Статус источников")), box));
  function refRefresh() {
    load(ref, async () => {
      const [sx, fx] = [await GET("/sanctions/status").catch((e) => ({ error: e })), await GET("/fx").catch((e) => ({ error: e }))];
      const errTxt = (e) => "HTTP " + e.status + " — " + e.message;
      return kv([
        [T("Санкции: записей"), sx.error ? errTxt(sx.error) : (sx.count ? String(sx.count) : T("0 — не загружены"))],
        [T("Санкции: дата"), sx.error ? null : sx.as_of], [T("Санкции: версия списка"), sx.error ? null : sx.list_version], [T("Санкции: источник"), sx.error ? null : sx.source],
        [T("Курсы: дата"), fx.error ? errTxt(fx.error) : (fx.as_of || T("не загружены"))], [T("Курсы: источник"), fx.error ? null : fx.source],
        [T("Курсы: пар"), fx.error ? null : String(Object.keys(fx.rates || {}).length)],
        [T("Курсы: ссылка"), !fx.error && fx.url && /^https?:\/\//.test(fx.url) ? h("a", { href: fx.url, target: "_blank", rel: "noopener noreferrer" }, fx.url) : null]]);
    });
  }
  refRefresh();
  function refresh() {
    load(box, async () => {
      const d = await GET("/sources/status");
      return h("div", {}, kv([[T("Режим"), ru(d.data.mode)], [T("Внешние источники включены"), yn(d.data.live_enabled)], [T("Скрыто без источника"), d.data.excluded_unverified]]),
        table([T("Источник"), T("Тип"), T("Статус"), T("Причина"), T("Последний успех"), T("Устарел"), T("Кол-во")], d.sources.map((s) => ({ cells: [txt(s.name || s.id), txt(s.kind), sb(s.status), txt(s.reason || s.note), txt(s.last_success), s.stale == null ? "—" : yn(s.stale), txt(s.count != null ? s.count : s.last_count)] }))));
    });
  }
  refresh();
}

/* ---------- approvals ---------- */
function approvalsTable(items, canDecide, onDone) {
  return table(["#", T("Действие"), T("Данные"), T("Статус"), T("Кто просил"), T("Решил"), T("Заметка"), canDecide ? T("Решение") : ""], items.map((a) => {
    const note = h("input", { placeholder: T("заметка"), "aria-label": T("Заметка к решению #") + a.id, size: 12 });
    const btn = (ok, label) => h("button", { type: "button", onclick: async (ev) => {
      try { await POST(`/approvals/${a.id}/decide`, { approve: ok, note: note.value }); onDone && onDone(); }
      catch (e) { if (e.status !== 401) ev.target.closest("td").append(msgBox("err", "HTTP " + e.status + " — " + e.message)); }
    } }, label);
    return { cells: [a.id, a.action, h("code", {}, JSON.stringify(a.payload)), sb(a.status), txt(a.requested_by), txt(a.decided_by), txt(a.note),
      canDecide && a.status === "pending" ? h("div", { class: "bar" }, note, btn(true, T("Одобрить")), btn(false, T("Отклонить"))) : ""] };
  }));
}
async function viewApprovals(el) {
  const box = h("div"); el.append(card(T("Запросы на одобрение"), h("p", { class: "mut" }, T("Решение принимает только токен роли «владелец»; иначе сервер ответит 403.")), box));
  const reload = () => load(box, async () => { const d = await GET("/approvals"); return d.items.length ? approvalsTable(d.items, true, reload) : stateBox("load", T("Запросов нет.")); });
  reload();
}

/* ---------- router ---------- */
const VIEWS = { overview: viewOverview, conflicts: viewConflicts, tenders: viewTenders, contacts: viewContacts, fit: viewFit, compliance: viewCompliance, finance: viewFinance, fx: viewFx, deals: viewDeals,
  learning: viewLearning, sources: viewSources, approvals: viewApprovals };
async function render() {
  const m = clear($("#main"));
  S.cache = {}; S.mapApi = null;
  m.append(h("h2", { class: "vt" + (S.view === "overview" ? " sr" : "") }, SECTIONS.find((s) => s[0] === S.view)[1]));
  try { await VIEWS[S.view](m); } catch (e) { if (e.status !== 401) m.append(stateBox("err", T("Ошибка: ") + (e.message || e))); }
}
async function boot() {
  $("#whoami").textContent = T("роль: ") + (ROLE_RU[S.role] || S.role);
  $("#logout").hidden = false;
  loadHealth();
  await FIRM.ask();                                   // popup with the firm's data on EVERY opening; "view only" is always one click away
  renderNav(); render(); loadBadges(); LIVE.strip();
}
document.addEventListener("DOMContentLoaded", () => {
  applyStatic(); tick(); setInterval(tick, 30000);
  $("#logout").addEventListener("click", () => { try { sessionStorage.removeItem("wt_token"); localStorage.removeItem("wt_token_saved"); localStorage.removeItem("wt_role_saved"); } catch (e) { /* */ } S.token = ""; clear($("#main")); showGate(); });
  $("#gsearch").addEventListener("keydown", (e) => { if (e.key === "Enter") { S.filters.q = e.target.value.trim(); S.filters.f = "all"; go("tenders"); } });
  loadHealth();
  const fb = $("#firmb");
  if (fb) fb.addEventListener("click", () => FIRM.reopen());
  if (S.token) boot(); else showGate();
});
