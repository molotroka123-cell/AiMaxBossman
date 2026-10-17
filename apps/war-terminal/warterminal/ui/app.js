"use strict";
/* Bossman War & Procurement Terminal UI. Vanilla JS, no network except same-origin /api/v1. All API strings go through textContent. */
const API = "/api/v1";
const ROLES = ["viewer", "analyst", "compliance", "owner"];
const SECTIONS = [
  ["overview", "Карта"], ["conflicts", "Конфликты"], ["tenders", "Закупки"], ["fit", "Компания"], ["compliance", "Комплаенс"],
  ["finance", "Финансы"], ["deals", "Сделки"], ["learning", "Аналитика"], ["sources", "Источники"], ["approvals", "Одобрения"],
];
const S = { token: "", role: "viewer", view: "overview", tenders: null, sel: null, filters: { f: "all", q: "", category: "", country: "" },
  lastAssessment: {}, health: null, cache: {}, mapLayer: "conflict", mapSel: null, mapApi: null };
try { S.token = sessionStorage.getItem("wt_token") || ""; S.role = sessionStorage.getItem("wt_role") || "viewer"; } catch (e) { /* storage may be blocked */ }

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
  DISABLED: "b-grey", NOT_RUN: "b-grey", NOT_CONNECTED: "b-warn", DEMO: "b-warn", OK: "b-ok", WITHIN_LIMIT: "b-ok", MATCHED_DEAL: "b-warn", OVER_LIMIT: "b-bad",
  pending: "b-warn", approved: "b-ok", rejected: "b-bad" };
const sb = (s) => badge(s, STATUS_CLS[s]);
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
  const res = await fetch(API + path, { method, headers: Object.assign({ Authorization: "Bearer " + S.token }, body !== undefined ? { "Content-Type": "application/json" } : {}),
    body: body !== undefined ? JSON.stringify(body) : undefined });
  if (res.status === 401) { showGate("Токен не принят (401). Введите токен роли."); throw new ApiError(401, { code: "TOKEN_REQUIRED", message: "нужен токен роли" }); }
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
const fmtN = (n) => (typeof n === "number" && isFinite(n) ? n.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) : "—");

/* ---------- token gate ---------- */
function showGate(note) {
  const g = clear($("#gate"));
  g.hidden = false;
  g.style.cssText = "position:fixed;inset:0;background:rgba(3,8,15,.92);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px";
  const tok = h("input", { type: "password", id: "tok", autocomplete: "off", "aria-label": "Токен роли", value: S.token, style: "width:100%" });
  const role = sel("role", ROLES, S.role); role.setAttribute("aria-label", "Метка роли");
  const form = h("form", { class: "card", style: "max-width:420px;width:100%", onsubmit: (e) => {
    e.preventDefault();
    S.token = tok.value.trim(); S.role = role.value;
    try { sessionStorage.setItem("wt_token", S.token); sessionStorage.setItem("wt_role", S.role); } catch (x) { /* ignore */ }
    g.hidden = true; clear(g); boot();
  } },
  h("h2", {}, "Вход по токену роли"),
  note ? msgBox("err", note) : null,
  h("p", { class: "mut" }, "Токен хранится только в sessionStorage этой вкладки. Метка роли ниже — подсказка интерфейсу; реальную роль определяет сервер по токену."),
  h("div", { class: "form", style: "grid-template-columns:1fr" }, field("Токен", tok), field("Метка роли", role)),
  h("div", { class: "bar" }, h("button", { type: "submit" }, "Войти")));
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
    s = h("select", { id: "navsel", "aria-label": "Раздел", onchange: (e) => go(e.target.value) }, SECTIONS.map(([id, l]) => h("option", { value: id }, l)));
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
    const demo = hd.mode !== "LIVE" || hd.demo_tenders > 0;
    if (hd.watermark === null && !demo) { wm.textContent = "LIVE"; wm.className = "badge wm b-ok"; }
    else { wm.textContent = hd.watermark || "DEMO / NOT LIVE"; wm.className = "badge wm b-warn"; }
    wm.title = "mode=" + hd.mode + ", demo_tenders=" + hd.demo_tenders + ", live_enabled=" + hd.live_enabled + ", v" + hd.version;
    au.textContent = hd.audit_chain_ok ? "аудит: цепочка OK" : "аудит: НАРУШЕНА";
    au.className = "badge " + (hd.audit_chain_ok ? "b-ok" : "b-bad");
  } catch (e) {
    wm.textContent = "НЕТ СВЯЗИ С API"; wm.className = "badge wm b-bad";
    au.textContent = "аудит: неизвестно"; au.className = "badge b-grey";
  }
}

/* Header badges: FX date and sanctions list size. Hidden when absent or when the endpoint is not available; never invented. */
const isCnb = (s) => /ČNB|CNB|cnb\.cz/i.test(String(s || ""));
async function loadBadges() {
  const fb = $("#fxb"), sbg = $("#sancb");
  try {
    const fx = await GET("/fx");
    if (fx && fx.as_of) {
      fb.textContent = (isCnb(fx.source) ? "курсы ЧНБ: " : "курсы: ") + fx.as_of;
      fb.title = "источник: " + txt(fx.source) + (fx.url ? " — " + fx.url : "") + ", пар: " + Object.keys(fx.rates || {}).length;
      fb.hidden = false;
    } else fb.hidden = true;
  } catch (e) { fb.hidden = true; }
  try {
    const st = await GET("/sanctions/status");
    if (st && typeof st.count === "number" && st.count > 0) {
      sbg.textContent = "санкции: " + st.count;
      sbg.title = "as_of: " + txt(st.as_of) + ", list_version: " + txt(st.list_version) + ", источник: " + txt(st.source);
      sbg.className = "badge b-info"; sbg.hidden = false;
    } else if (st && st.count === 0) { sbg.textContent = "санкции: не загружены"; sbg.title = ""; sbg.className = "badge b-warn"; sbg.hidden = false; }
    else sbg.hidden = true;
  } catch (e) { sbg.hidden = true; }
}

/* ---------- generic loader ---------- */
async function load(el, fn) {
  clear(el).append(stateBox("load", "Загрузка…"));
  try { const out = await fn(); clear(el); if (out) el.append(out); }
  catch (e) { if (e.status === 401) return; clear(el).append(stateBox("err", "Ошибка: " + (e.message || e))); }
}
function card(title, ...kids) { return h("section", { class: "card", "aria-label": title }, h("h2", {}, title), kids); }
function submit(form, fn, out) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clear(out);
    try { const r = await fn(formData(form)); out.append(msgBox("ok", "OK: " + JSON.stringify(r))); return r; }
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
    geoP = fetch("vendor/countries-110m.geojson").then((r) => { if (!r.ok) throw new Error("геометрия карты: HTTP " + r.status); return r.json(); }).then((d) => {
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
  conflict: { label: "Интенсивность конфликтов", has: (c) => !!c && c.conflict_events > 0, val: (c) => c.conflict_deaths,
    bins: [1, 10, 100, 1000, 10000], binLabels: ["1–9", "10–99", "100–999", "1 000–9 999", "≥ 10 000"],
    colors: ["#5a2a24", "#8c3324", "#c2421f", "#ee6a2c", "#ffc06a"], zero: "#3b3550", zeroLabel: "0 (события без погибших)", unit: "погибших за год, лог. шкала" },
  procurement: { label: "Активность закупок", has: (c) => !!c && c.tenders > 0, val: (c) => c.open_tenders,
    bins: [1, 2, 5, 10, 50], binLabels: ["1", "2–4", "5–9", "10–49", "≥ 50"],
    colors: ["#17405f", "#1c5f8c", "#2382ba", "#40a8df", "#9ddcff"], zero: "#2d3a4a", zeroLabel: "0 открытых (есть закрытые)", unit: "открытых тендеров" },
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
  if (id === "conflict") return c && c.conflict_events > 0 ? `погибших: ${fmtN(c.conflict_deaths)} · событий: ${fmtN(c.conflict_events)} (UCDP ${txt(c.conflict_year)})` : "UCDP: событий в загруженных данных нет";
  return c && c.tenders > 0 ? `открытых тендеров: ${fmtN(c.open_tenders)} из ${fmtN(c.tenders)}` : "тендеров в базе нет";
}
const uniqYears = (lay) => uniq(Object.values(lay.countries || {}).map((c) => c.conflict_year)).sort();

function mapWidget(lay, opts) {
  const wrap = h("div", { class: "mapw" });
  const tabs = h("div", { class: "maptabs", role: "tablist", "aria-label": "Слой карты" });
  const box = h("div", { class: "mapbox" });
  const tip = h("div", { class: "maptip", hidden: true });
  const legend = h("div", { class: "legend" }), srcLine = h("div", { class: "legend src" });
  const svg = svgEl("svg", { viewBox: `0 0 ${MAP_W} ${MAP_H}`, role: "img", "aria-label": "Карта мира (Natural Earth 1:110m, проекция Equal Earth); страны закрашены по данным API" });
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
      h("span", {}, h("i", { class: "sq", style: "background:" + L.zero }), L.zeroLabel), h("span", {}, h("i", { class: "sq", style: "background:" + ND_FILL }), "нет данных"));
    const n = Object.values(countries).filter(L.has).length;
    clear(srcLine);
    if (S.mapLayer === "conflict") {
      srcLine.append(h("span", {}, "Источник: ", h("b", {}, lay.conflict_source ? "UCDP GED (" + lay.conflict_source + ")" : "нет данных UCDP")),
        h("span", {}, "Год: ", h("b", {}, uniqYears(lay).join(", ") || "—")), h("span", {}, "Стран с событиями: ", h("b", {}, String(n))),
        ...((lay.unmapped_conflicts || []).length ? [h("span", {}, "Не сопоставлено со страной: ", h("b", {}, lay.unmapped_conflicts.join(", ")))] : []));
    } else {
      srcLine.append(h("span", {}, "Источник: ", h("b", {}, "тендеры в базе (GET /map/layers)")), h("span", {}, "Стран с тендерами: ", h("b", {}, String(n))),
        h("span", {}, "Тендеров без страны (регионы): ", h("b", {}, String(lay.unmapped_tenders == null ? "—" : lay.unmapped_tenders))));
    }
    srcLine.append(h("span", {}, "Границы: ", h("b", {}, txt(lay.geometry_source))));
    if (!n) legend.append(h("span", { class: "warnt" }, "Для этого слоя данных нет — загрузите источник в разделе «Источники»."));
  }
  for (const [id, L] of Object.entries(LAYERS)) {
    tabs.append(h("button", { type: "button", role: "tab", "data-l": id, onclick: () => { S.mapLayer = id; paint(); opts.onLayer && opts.onLayer(id); } }, L.label));
  }
  // disabled tabs from the mockup that have no data source: shown honestly, not clickable
  for (const [l, need] of [["Оборонные расходы", "SIPRI"], ["Санкции по странам", "привязки санкций к странам"], ["Маршруты поставок", "данных о логистике"]]) {
    tabs.append(h("button", { type: "button", role: "tab", disabled: true, "aria-disabled": "true", title: "нет источника: " + need, class: "off" }, l));
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
    h("button", { type: "button", "aria-label": "Приблизить", title: "Приблизить", onclick: () => zoomAt(1.6, MAP_W / 2, MAP_H / 2) }, "+"),
    h("button", { type: "button", "aria-label": "Отдалить", title: "Отдалить", onclick: () => zoomAt(1 / 1.6, MAP_W / 2, MAP_H / 2) }, "−"),
    h("button", { type: "button", "aria-label": "Сбросить масштаб", title: "Сбросить масштаб", onclick: () => { k = 1; tx = 0; ty = 0; apply(); } }, "⟲"));
  // keyboard / screen-reader alternative to clicking the map
  const opts1 = Object.keys(countries).filter((i) => GEO.byIso3[i]).sort((a, b) => geoName(a).localeCompare(geoName(b)));
  const sel1 = h("select", { "aria-label": "Выбрать страну с данными", onchange: (e) => e.target.value && select(e.target.value) },
    h("option", { value: "" }, "Страна с данными (" + opts1.length + ")…"), opts1.map((i) => h("option", { value: i }, geoName(i))));
  box.append(svg, ctl, tip);
  wrap.append(tabs, box, legend, srcLine, h("div", { class: "bar mapfoot" }, sel1, h("span", { class: "mut" }, "Колесо — масштаб, перетаскивание — сдвиг, клик — панель страны")));
  apply(); paint();
  if (S.mapSel && els[S.mapSel]) setTimeout(() => select(S.mapSel), 0);
  S.mapApi = { select, paint };
  return wrap;
}

/* ---------- country panel (mockup "Ukraine" panel, real data only) ---------- */
const ACD_TYPE = { 1: "внесистемный", 2: "межгосударственный", 3: "внутренний", 4: "интернационализированный внутренний" };
function mini(label, value) { return h("div", { class: "mini" }, h("div", { class: "l" }, label), h("div", { class: "v" }, value)); }
function openTender(t, iso2) { S.sel = t.key; S.filters = { f: "all", q: "", category: "", country: iso2 || "" }; go("tenders"); }
function acdFor(conf, iso3) {
  const acd = conf && conf.acd;
  return ((acd && acd.items) || []).filter((a) => String(a.location || "").split(",").some((s) => toIso3(s) === iso3));
}
function panelHint(panel, lay) {
  const L = LAYERS[S.mapLayer], cs = lay.countries || {};
  const top = Object.keys(cs).filter((i) => L.has(cs[i]) && L.val(cs[i]) > 0 && GEO.byIso3[i]).sort((a, b) => L.val(cs[b]) - L.val(cs[a])).slice(0, 8);
  clear(panel).append(h("h2", {}, "Страна"), h("p", { class: "mut" }, "Нажмите на страну на карте (или выберите её под картой): здесь появятся данные UCDP, конфликты UCDP/PRIO и открытые тендеры."),
    h("h3", {}, "Топ стран · " + L.label.toLowerCase()),
    top.length ? h("ol", { class: "rank" }, top.map((i) => h("li", {}, h("button", { type: "button", class: "lnk", onclick: () => S.mapApi && S.mapApi.select(i) }, geoName(i)),
      h("span", { class: "num" }, fmtN(L.val(cs[i])))))) : stateBox("load", "Для этого слоя данных нет."));
}
function countryPanel(panel, key, lay, conf) {
  const m = GEO.byKey[key] || { p: {} }, p = m.p, iso3 = m.iso3, c = iso3 ? (lay.countries || {})[iso3] : null;
  clear(panel).append(h("div", { class: "cp-head" }, h("h2", {}, txt(p.name_long || p.name)), iso3 ? badge(iso3, "b-grey") : null,
    c && c.severity ? sevBadge(c.severity) : null, h("button", { type: "button", class: "x", "aria-label": "Закрыть панель страны", onclick: () => { S.mapSel = null; for (const el of document.querySelectorAll("path.ctry.sel")) el.classList.remove("sel"); panelHint(panel, lay); } }, "×")));
  if (!iso3) { panel.append(stateBox("load", "У этой территории нет ISO3-кода в Natural Earth — данные API к ней не привязываются.")); return; }
  panel.append(h("h3", {}, "Конфликт · UCDP GED" + (lay.conflict_source ? " (" + lay.conflict_source + ")" : "")));
  if (c && c.conflict_events > 0) {
    panel.append(h("div", { class: "cp-stats" }, mini("Погибших", fmtN(c.conflict_deaths)), mini("Событий", fmtN(c.conflict_events)),
      mini("Серьёзность", sevBadge(c.severity)), mini("Год данных", txt(c.conflict_year))));
  } else panel.append(stateBox("load", lay.conflict_source ? "В загруженных данных UCDP событий по этой стране нет." : "Данные UCDP не загружены."));
  const acd = conf && conf.acd, items = acdFor(conf, iso3);
  panel.append(h("h3", {}, "Вооружённые конфликты · UCDP/PRIO ACD" + (acd && acd.year ? " " + acd.year : "")));
  if (!acd) panel.append(stateBox("load", "Данные ACD не загружены."));
  else if (!items.length) panel.append(stateBox("load", "В ACD нет конфликтов с местом в этой стране."));
  else panel.append(h("ul", { class: "list" }, items.map((a) => h("li", {}, h("div", {}, txt(a.side_a), h("span", { class: "mut" }, " vs "), txt(a.side_b)),
    h("div", { class: "bar", style: "margin:4px 0 0" }, INTENSITY[a.intensity] ? badge(INTENSITY[a.intensity][0], INTENSITY[a.intensity][1]) : badge(a.intensity),
      h("span", { class: "mut" }, ACD_TYPE[a.type] || "тип " + txt(a.type)), h("span", { class: "mut" }, "место: " + txt(a.location)))))));
  panel.append(h("h3", {}, "Закупки"), h("div", { class: "cp-stats" }, mini("Открытых тендеров", fmtN(c ? c.open_tenders : 0)), mini("Всего в базе", fmtN(c ? c.tenders : 0))));
  const tb = h("div");
  panel.append(tb);
  load(tb, async () => {
    const d = await cached("openT");
    const mine = (d.items || []).filter((t) => toIso3(t.country) === iso3);
    if (!mine.length) return stateBox("load", "Открытых тендеров по этой стране нет.");
    return h("div", {}, h("div", { class: "mut" }, `Топ-5 из ${mine.length} открытых (порядок API: fit)` + (d.total > (d.items || []).length ? " · загружены не все открытые тендеры" : "")),
      h("ul", { class: "list" }, mine.slice(0, 5).map((t) => h("li", {}, h("div", { class: "tt" }, t.title == null ? "—" : t.title, t.demo ? [" ", badge("DEMO", "b-warn")] : null),
        h("div", { class: "bar", style: "margin:4px 0 0" }, sb(t.assessment && t.assessment.complexity), h("span", { class: "mut" }, "дедлайн " + txt((t.deadline || "").slice(0, 10))),
          t.estimated_value == null ? null : h("span", { class: "mut" }, fmtN(t.estimated_value) + " " + txt(t.currency)),
          h("button", { type: "button", class: "sm", onclick: () => openTender(t, p.iso2) }, "Открыть"))))),
      h("button", { type: "button", onclick: () => { S.sel = null; S.filters = { f: "all", q: "", category: "", country: p.iso2 || iso3 }; go("tenders"); } }, "Все тендеры страны в разделе «Закупки»"));
  });
  if (window.matchMedia && window.matchMedia("(max-width: 950px)").matches && panel.scrollIntoView) panel.scrollIntoView({ block: "start" });
}
/* map + side panel, shared by Overview and Conflicts */
async function mapBlock(mapEl, panel) {
  await load(mapEl, async () => {
    const [lay, conf] = await Promise.all([cached("layers"), cached("conf").catch(() => null), loadGeo()]);
    const w = mapWidget(lay, { onSelect: (key) => countryPanel(panel, key, lay, conf), onLayer: () => { if (!S.mapSel) panelHint(panel, lay); } });
    if (!S.mapSel) panelHint(panel, lay);
    return h("div", {}, w, conf && conf.safety ? h("div", { class: "safety" }, conf.safety) : null);
  });
  if (!mapEl.querySelector(".mapw")) clear(panel).append(h("h2", {}, "Страна"), stateBox("load", "Карта не загрузилась — панель страны недоступна."));
}
function svgEl(tag, attrs) { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; }
const SEV = { low: "#ffd16a", medium: "#ff9f43", high: "#f64b49" };
const sevBadge = (s) => badge(s, s === "high" ? "b-bad" : s === "medium" ? "b-warn" : "b-grey");
const uniq = (arr) => [...new Set(arr.filter((x) => x != null && x !== ""))];
/* source / licence / safety block for /conflicts (the map itself comes from /map/layers) */
function conflictMeta(d) {
  const wrap = h("div");
  const items = d.items || [], real = items.filter((c) => !c.demo);
  if (d.status === "NOT_CONNECTED") wrap.append(stateBox("load", "NOT_CONNECTED: " + txt(d.message)));
  if (real.length) {
    const PREC = "средняя точка событий страны за год; не координаты событий";
    const prec = uniq(real.map((c) => c.precision)).filter((p) => p !== PREC);
    wrap.append(h("div", { class: "legend src" },
      h("span", {}, "Источник: ", h("b", {}, uniq(real.map((c) => c.source_id)).join(", ") || "—")),
      h("span", {}, "Год: ", h("b", {}, uniq(real.map((c) => c.since)).join(", ") || "—")),
      h("span", {}, "Лицензия: ", h("b", {}, uniq(real.map((c) => c.licence)).join(", ") || "—")),
      h("span", {}, "Точность: " + PREC + (prec.length ? " (precision: " + prec.join(", ") + ")" : ""))));
  }
  if (items.some((c) => c.demo)) wrap.append(h("div", { class: "mut", style: "margin-top:6px" }, "DEMO-регионы (синтетические) на карту не наносятся — их список ниже."));
  if (!items.length) wrap.append(stateBox("load", "Нет данных о конфликтах."));
  return wrap;
}
function realConflictTable(items, limit) {
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : -1);
  const rows = items.filter((c) => !c.demo).slice().sort((a, b) => num(b.deaths) - num(a.deaths)).slice(0, limit || undefined);
  return table(["Страна", "Событий", "Погибших", "Серьёзность", "Год", "Источник", "Лицензия"], rows.map((c) => ({ cells: [
    h("span", {}, txt(c.country), c.region ? h("div", { class: "mut" }, c.region) : null),
    txt(c.events), txt(c.deaths), sevBadge(c.severity), txt(c.since),
    c.source_url && /^https?:\/\//.test(c.source_url) ? h("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer" }, txt(c.source_id)) : txt(c.source_id),
    h("span", { class: "lic" }, txt(c.licence))] })));
}
const INTENSITY = { war: ["война", "b-bad"], minor: ["малый", "b-warn"] };
function acdTable(acd) {
  const items = (acd && acd.items) || [];
  return table(["Место", "Стороны", "Интенсивность", "Тип", "Год"], items.map((c) => ({ cells: [
    txt(c.location), h("span", {}, txt(c.side_a), h("span", { class: "mut" }, " vs "), txt(c.side_b)),
    INTENSITY[c.intensity] ? badge(INTENSITY[c.intensity][0] + " (" + c.intensity + ")", INTENSITY[c.intensity][1]) : badge(c.intensity), txt(c.type), txt(c.year)] })));
}
function conflictList(items) {
  return table(["Название", "Страна", "Серьёзность", "С даты", "Источник", "Лицензия"], items.map((c) => ({ cells: [
    txt(c.name), txt(c.country), sevBadge(c.severity), txt(c.since),
    c.source_url ? h("a", { href: /^https?:\/\//.test(c.source_url) ? c.source_url : "#", target: "_blank", rel: "noopener noreferrer" }, txt(c.source_id)) : txt(c.source_id),
    txt(c.licence)].map((x, i) => (i === 0 && c.demo ? h("span", {}, x, " ", badge("DEMO", "b-warn")) : x)) })));
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
  if (!f.country) { p.set("limit", "200"); return GET("/tenders?" + p); }
  await loadGeo().catch(() => null);
  p.set("limit", "1000");
  const d = await GET("/tenders?" + p);
  const want = toIso3(f.country), raw = f.country.trim().toUpperCase();
  const items = (d.items || []).filter((t) => (want ? toIso3(t.country) === want : String(t.country || "").toUpperCase() === raw));
  return Object.assign({}, d, { items, total: items.length, partial: d.total > (d.items || []).length, countryIso3: want });
}
function tenderTable(items, onRow) {
  return table(["Страна", "Название", "Категория", "Стоимость", "Дедлайн", "Статус", "Сложность", "Fit"], items.map((t) => {
    const a = t.assessment || {};
    return { key: t.key, cls: S.sel === t.key ? "sel" : "", cells: [
      txt(t.country), h("span", {}, t.title == null ? "—" : t.title, t.demo ? [" ", badge("DEMO", "b-warn")] : null), txt(t.category),
      t.estimated_value == null ? "—" : `${t.estimated_value} ${t.currency || ""}`, txt(t.deadline), txt(t.status),
      sb(a.complexity), a.fit_score == null ? "—" : String(a.fit_score)] };
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
  clear(el).append(stateBox("load", "Загрузка показателей…"));
  const [openT, allT, lay, conf, sanc, src, fx] = await Promise.allSettled(["openT", "allT", "layers", "conf", "sanc", "src", "fx"].map(cached));
  if ([openT, allT, lay, conf, sanc, src, fx].some((r) => r.status === "rejected" && r.reason && r.reason.status === 401)) return;
  const err = (r) => "нет данных: " + (r.reason && r.reason.status ? "HTTP " + r.reason.status : String(r.reason && r.reason.message || r.reason));
  const tiles = [];
  tiles.push(openT.status === "fulfilled" ? kpiTile("doc", fmtN(openT.value.total), "Открытых тендеров", "status=open · все источники", "ok") : kpiTile("doc", "—", "Открытых тендеров", err(openT)));
  tiles.push(allT.status === "fulfilled" ? kpiTile("stack", fmtN(allT.value.total), "Тендеров в базе", "открытые + закрытые") : kpiTile("stack", "—", "Тендеров в базе", err(allT)));
  if (lay.status === "fulfilled") {
    const cs = Object.values(lay.value.countries || {}).filter((c) => c.conflict_events > 0), yrs = uniqYears(lay.value).join(", ");
    const ev = cs.reduce((a, c) => a + c.conflict_events, 0), de = cs.reduce((a, c) => a + c.conflict_deaths, 0);
    tiles.push(kpiTile("globe", fmtN(cs.length), "Стран с событиями UCDP", cs.length ? "UCDP GED · " + yrs : "UCDP не загружен", "r"));
    tiles.push(kpiTile("fire", fmtN(ev), "Событий UCDP" + (yrs ? " " + yrs : ""), cs.length ? "погибших: " + fmtN(de) : "UCDP не загружен", "r"));
  } else { tiles.push(kpiTile("globe", "—", "Стран с событиями UCDP", err(lay))); tiles.push(kpiTile("fire", "—", "Событий UCDP", err(lay))); }
  if (conf.status === "fulfilled" && conf.value.acd && (conf.value.acd.items || []).length) {
    const acd = conf.value.acd, ids = uniq(acd.items.map((a) => a.conflict_id)), wars = uniq(acd.items.filter((a) => a.intensity === "war").map((a) => a.conflict_id));
    tiles.push(kpiTile("target", fmtN(ids.length), "Вооружённых конфликтов", "UCDP/PRIO ACD " + txt(acd.year) + " · войн: " + wars.length, "r"));
  } else tiles.push(kpiTile("target", "—", "Вооружённых конфликтов", conf.status === "rejected" ? err(conf) : "нет источника: UCDP/PRIO ACD не загружен"));
  tiles.push(sanc.status === "fulfilled" ? kpiTile("shield", sanc.value.count ? fmtN(sanc.value.count) : "0", "Записей в санкц. списках", sanc.value.count ? txt(sanc.value.source) + " · " + txt(sanc.value.as_of) : "не загружены", "w")
    : kpiTile("shield", "—", "Записей в санкц. списках", err(sanc)));
  if (src.status === "fulfilled") {
    const rows = src.value.sources || [], live = rows.filter((s) => s.status === "LIVE");
    tiles.push(kpiTile("pulse", live.length + " / " + rows.length, "Источников LIVE", live.map((s) => s.id).join(", ") || "ни одного", "ok"));
  } else tiles.push(kpiTile("pulse", "—", "Источников LIVE", err(src)));
  tiles.push(fx.status === "fulfilled" && fx.value && fx.value.as_of ? kpiTile("fx", fx.value.as_of, "Дата курсов валют", txt(fx.value.source) + " · пар: " + Object.keys(fx.value.rates || {}).length)
    : kpiTile("fx", "—", "Дата курсов валют", fx.status === "rejected" ? err(fx) : "курсы не загружены"));
  clear(el).append(...tiles);
}

/* ---------- honest placeholders for mockup blocks without a data source ---------- */
function placeholder(title, need, srcId, srcs) {
  const row = srcId && srcs ? (srcs.sources || []).find((s) => s.id === srcId) : null;
  return h("section", { class: "card ph", "aria-label": title }, h("h2", {}, title), h("div", { class: "phmsg" }, "нет источника: " + need),
    row ? h("div", { class: "bar" }, h("span", { class: "mut" }, "реестр источников: " + txt(row.name)), sb(row.status), row.reason ? h("span", { class: "mut" }, row.reason) : null) : null);
}
async function placeholders(el) {
  const srcs = await cached("src").catch(() => null);
  clear(el).append(
    placeholder("Оборонные бюджеты", "датасет SIPRI Military Expenditure Database (годовые расходы по странам)", "sipri", srcs),
    placeholder("Компании сектора / котировки", "поставщик рыночных котировок (биржевые данные с лицензией на показ)", null, srcs),
    placeholder("Недавние награды контрактов", "эндпоинт чтения наград в API (USAspending / TED award notices); закрытые тендеры — не награды", "usaspending", srcs));
}

/* ---------- top conflicts (UCDP GED + ACD) ---------- */
async function topConflicts(el) {
  await load(el, async () => {
    const [lay, conf] = await Promise.all([cached("layers"), cached("conf"), loadGeo().catch(() => null)]);
    const cs = Object.entries(lay.countries || {}).filter(([, c]) => c.conflict_events > 0).sort((a, b) => b[1].conflict_deaths - a[1].conflict_deaths).slice(0, 6);
    const out = h("div");
    if (!cs.length) out.append(stateBox("load", "Данных UCDP нет. Загрузите их в разделе «Источники»."));
    else {
      const max = cs[0][1].conflict_deaths || 1;
      out.append(h("div", { class: "mut" }, "Погибших за " + uniqYears(lay).join(", ") + " (UCDP GED), топ-6 · клик — страна на карте"),
        h("ol", { class: "rank bars" }, cs.map(([i, c]) => h("li", {}, h("button", { type: "button", class: "lnk", onclick: () => { if (S.mapApi) { S.mapApi.select(i); } } }, geoName(i)),
          h("span", { class: "barw", "aria-hidden": "true" }, h("span", { style: "width:" + Math.max(2, Math.round(c.conflict_deaths / max * 100)) + "%" })),
          h("span", { class: "num" }, fmtN(c.conflict_deaths)), sevBadge(c.severity)))));
    }
    const acd = conf.acd, wars = ((acd && acd.items) || []).filter((a) => a.intensity === "war");
    out.append(h("h3", {}, "Войны (≥ 1 000 погибших в боях за год) · UCDP/PRIO ACD" + (acd && acd.year ? " " + acd.year : "")));
    out.append(!acd ? stateBox("load", "Данные ACD не загружены.") : wars.length ? h("ul", { class: "list" }, wars.map((a) => h("li", {}, h("b", {}, txt(a.location)), h("div", { class: "mut" }, txt(a.side_a) + " vs " + txt(a.side_b)))))
      : stateBox("load", "В ACD нет конфликтов интенсивности war."));
    return out;
  });
}

async function viewOverview(el) {
  const kpi = h("div", { class: "kpis", "aria-label": "Ключевые показатели" }), mapEl = h("div"), panel = h("aside", { class: "card cpanel", "aria-label": "Панель страны" });
  const tBox = h("div"), cBox = h("div"), phBox = h("div", { class: "phs" });
  el.append(kpi, h("div", { class: "mapgrid" }, h("section", { class: "card mapcard", "aria-label": "Карта мира" }, mapEl), panel),
    h("div", { class: "lower" }, card("Открытые тендеры", tBox), card("Конфликты · UCDP", cBox), phBox));
  await Promise.all([
    loadKpis(el.querySelector(".kpis")),
    mapBlock(mapEl, panel),
    topConflicts(cBox),
    placeholders(phBox),
    load(tBox, async () => {
      const d = await cached("openT"), items = d.items || [];
      if (!items.length) return stateBox("load", "Открытых тендеров нет. Загрузите данные в разделе «Источники».");
      const c = countsFrom(items);
      return h("div", {}, h("div", { class: "bar" }, badge("Все открытые: " + d.total, "b-info"), badge("EASY: " + c.EASY, "b-ok"), badge("MEDIUM: " + c.MEDIUM, "b-warn"),
        badge("HARD: " + c.HARD, "b-bad"), badge("BLOCKED: " + c.BLOCKED, "b-bad"), badge("REVIEW: " + c.REVIEW_REQUIRED, "b-info")),
      h("div", { class: "mut" }, "Первые 10 по порядку API (fit); клик — карточка тендера"),
      table(["Страна", "Название", "Дедлайн", "Сложность", "Fit"], items.slice(0, 10).map((t) => ({ key: t.key, cells: [txt(t.country),
        h("span", { class: "clip", title: t.title == null ? "" : t.title }, t.title == null ? "—" : t.title), txt((t.deadline || "").slice(0, 10)),
        sb(t.assessment && t.assessment.complexity), t.assessment && t.assessment.fit_score != null ? String(t.assessment.fit_score) : "—"] })),
        (r) => { S.sel = r.key; S.filters.country = ""; go("tenders"); }));
    }),
  ]);
}
async function viewConflicts(el) {
  const mapEl = h("div"), panel = h("aside", { class: "card cpanel", "aria-label": "Панель страны" });
  const metaBox = h("div"), realBox = h("div"), acdBox = h("div"), demoBox = h("div");
  S.mapLayer = "conflict";
  el.append(h("div", { class: "mapgrid" }, h("section", { class: "card mapcard", "aria-label": "Карта конфликтов" }, mapEl), panel),
    h("div", { class: "grid", style: "margin-top:12px;grid-template-columns:minmax(0,1fr)" },
      card("Источник и лицензия", metaBox), card("UCDP: агрегаты по странам (по числу погибших)", realBox), card("UCDP/PRIO ACD: вооружённые конфликты", acdBox), demoBox));
  await mapBlock(mapEl, panel);
  let d;
  await load(metaBox, async () => { d = await cached("conf"); return conflictMeta(d); });
  if (!d) { clear(realBox).append(stateBox("err", "Нет ответа /conflicts")); clear(acdBox); return; }
  const items = d.items || [], real = items.filter((c) => !c.demo), demo = items.filter((c) => c.demo);
  clear(realBox).append(real.length ? realConflictTable(real) : stateBox("load", "Реальных данных UCDP нет. Загрузите их в разделе «Источники» («Конфликты UCDP»)."));
  const acd = d.acd;
  clear(acdBox).append(acd && (acd.items || []).length ? h("div", {}, h("div", { class: "mut" }, "Год: " + txt(acd.year) + " · строк: " + acd.items.length + " · уникальных конфликтов: " + uniq(acd.items.map((a) => a.conflict_id)).length), acdTable(acd)) : stateBox("load", "Данных ACD нет."));
  if (demo.length) demoBox.append(card("DEMO-регионы (синтетические, не для решений)", conflictList(demo)));
}

const FILTERS = [["all", "Все"], ["EASY", "Лёгкие"], ["MEDIUM", "Средние"], ["HARD", "Сложные"], ["BLOCKED", "Не допущены"], ["REVIEW_REQUIRED", "На проверке"]];
async function viewTenders(el) {
  const listBox = h("div"), det = h("div", { class: "detail", id: "detail" });
  const bar = h("div", { class: "bar", role: "toolbar", "aria-label": "Фильтры тендеров" });
  const q = h("input", { type: "search", value: S.filters.q, placeholder: "Поиск q", "aria-label": "Текстовый поиск" });
  const cat = h("input", { value: S.filters.category, placeholder: "Категория", "aria-label": "Категория", size: 10 });
  const ctry = h("input", { value: S.filters.country || "все", placeholder: "Страна (ISO) или «все»", "aria-label": "Страна (ISO-код или «все» для всего мира)", title: "ISO-код страны, например CZE; «все» или пусто — весь мир", size: 12 });
  const ALL = ["", "все", "всё", "all", "*", "весь мир"];
  const refresh = () => {
    S.filters.q = q.value.trim(); S.filters.category = cat.value.trim();
    const c = ctry.value.trim(); S.filters.country = ALL.includes(c.toLowerCase()) ? "" : c;
    if (!S.filters.country) ctry.value = "все";
    renderTenders();
  };
  for (const [id, l] of FILTERS) bar.append(h("button", { type: "button", class: S.filters.f === id ? "on" : "", "aria-pressed": String(S.filters.f === id), onclick: () => { S.filters.f = id; refresh(); viewRefreshBar(); } }, l));
  function viewRefreshBar() { for (const b of bar.querySelectorAll("button")) { const on = b.textContent === FILTERS.find((x) => x[0] === S.filters.f)[1]; b.className = on ? "on" : ""; b.setAttribute("aria-pressed", String(on)); } }
  for (const i of [q, cat, ctry]) i.addEventListener("keydown", (e) => { if (e.key === "Enter") refresh(); });
  bar.append(q, cat, ctry, h("button", { type: "button", onclick: refresh }, "Применить"));
  el.append(card("Тендеры", bar, listBox), det);
  async function renderTenders() {
    await load(listBox, async () => {
      const d = await loadTenders(); S.tenders = d;
      if (!d.items.length) return stateBox("load", "Нет тендеров по этим фильтрам.");
      return h("div", {}, h("div", { class: "mut" }, `Показано ${d.items.length} из ${d.total} · страна: ${S.filters.country ? S.filters.country + (d.countryIso3 ? " (" + geoName(d.countryIso3) + ")" : "") : "весь мир"}` +
        (S.filters.country ? " · фильтр страны применён в интерфейсе по коду/названию" + (d.partial ? "; загружены не все тендеры" : "") : "")), tenderTable(d.items, (k) => { S.sel = k; renderTenders(); showDetail(det, k); }));
    });
  }
  await renderTenders();
  if (S.sel) showDetail(det, S.sel);
}

async function showDetail(el, key) {
  await load(el, async () => {
    const [d, cl] = await Promise.all([GET("/tenders/" + encodeURI(key)), GET("/tenders/" + encodeURI(key) + "/checklist").catch((e) => ({ error: e.message }))]);
    return detailView(d, cl, el);
  });
  el.scrollIntoView && el.scrollIntoView({ block: "nearest" });
}
function assessmentView(a) {
  const box = h("div");
  box.append(h("div", { class: "bar" }, h("div", { class: "score" }, a.fit.fit_score == null ? "—" : String(a.fit.fit_score)), sb(a.complexity), a.tier_if_cleared ? badge("tier_if_cleared: " + a.tier_if_cleared, STATUS_CLS[a.tier_if_cleared]) : null,
    sb(a.compliance_status), a.demo ? badge("DEMO", "b-warn") : null, a.dry_run === false ? badge("сохранено #" + a.id, "b-ok") : (a.dry_run ? badge("dry-run", "b-grey") : null)));
  box.append(h("p", { class: "msg", style: "background:#26365a" }, txt(a.fit.disclaimer)));
  box.append(kv([["Уверенность", a.fit.confidence], ["Неизвестные компоненты", (a.fit.unknowns || []).join(", ") || "нет"], ["USD-оценка", a.usd_value], ["back-to-back", String(a.back_to_back)], ["Оценено", a.assessed_at]]));
  box.append(h("h3", {}, "Проверки (gates)"));
  box.append(table(["Проверка", "Статус", "Причина", "Нужно подтвердить"], a.gates.map((g) => ({ cells: [g.id, sb(g.status), txt(g.reason), txt(g.evidence_needed)] }))));
  box.append(h("h3", {}, "Компоненты fit"));
  box.append(table(["Компонент", "Вес", "Значение", "Почему"], Object.entries(a.fit.components).map(([k, c]) => ({ cells: [k, txt(c.weight), c.value == null ? "— (неизвестно)" : String(c.value), txt(c.why)] }))));
  if (a.ai) {
    box.append(h("h3", {}, "AI-подсказка"));
    box.append(h("div", { class: "bar" }, sb(a.ai.status), a.ai.tier ? badge("AI tier: " + a.ai.tier, STATUS_CLS[a.ai.tier]) : null,
      a.ai_disagreement ? badge("AI не согласен с правилами", "b-warn") : null));
    box.append(h("pre", { class: "msg", style: "background:#0a1826;overflow:auto" }, JSON.stringify(a.ai, null, 2)));
  }
  return box;
}
function detailView(d, cl, host) {
  const t = d.tender, a = d.assessment;
  const out = h("div", { class: "card" });
  out.append(h("h2", {}, t.title == null ? t.key : t.title, t.demo ? [" ", badge("DEMO", "b-warn")] : null));
  const link = t.source_url && /^https?:\/\//.test(t.source_url) ? h("a", { href: t.source_url, target: "_blank", rel: "noopener noreferrer" }, t.source_url) : txt(t.source_url);
  out.append(kv([["Ключ", t.key], ["Покупатель", t.buyer], ["Страна", t.country], ["Категория", t.category], ["CPV", (t.cpv || []).join(", ")],
    ["Стоимость", t.estimated_value == null ? null : `${t.estimated_value} ${t.currency || ""}`], ["Дедлайн", t.deadline], ["Статус", t.status],
    ["Источник", link], ["retrieved_at", t.retrieved_at], ["data_quality", typeof t.data_quality === "object" && t.data_quality ? JSON.stringify(t.data_quality) : t.data_quality],
    ["Версии", (d.versions || []).join(", ")]]));
  const asBox = h("div", { style: "margin-top:10px" }, assessmentView(a));
  const ai = h("input", { type: "checkbox", id: "useai" });
  const b2b = h("input", { type: "checkbox", id: "b2b" });
  const res = h("div");
  const run = async (dry) => {
    clear(res);
    try {
      const r = await POST("/assessments", { tender_key: t.key, dry_run: dry, use_ai: ai.checked, back_to_back: b2b.checked });
      if (!dry && r.id != null) { S.lastAssessment[t.key] = r.id; try { sessionStorage.setItem("wt_last_" + t.key, String(r.id)); } catch (e) { /* */ } }
      clear(asBox).append(assessmentView(r));
      res.append(msgBox("ok", dry ? "Dry-run: ничего не сохранено." : "Оценка сохранена, id=" + r.id));
    } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); }
  };
  out.append(h("div", { class: "bar" }, h("button", { type: "button", onclick: () => run(true) }, "Оценить (dry-run)"), h("button", { type: "button", onclick: () => run(false) }, "Сохранить оценку"),
    h("label", { class: "chk", for: "useai" }, ai, " AI-подсказка"), h("label", { class: "chk", for: "b2b" }, b2b, " back-to-back")), res, asBox);
  out.append(h("h3", {}, "Чек-лист (черновик)"));
  if (cl.error) out.append(stateBox("err", cl.error));
  else {
    out.append(h("div", { class: "mut" }, txt(cl.note)));
    out.append(cl.items.length ? table(["Шаг", "Статус", "Почему / что нужно"], cl.items.map((i) => ({ cells: [i.step, sb(i.status), [i.why, i.evidence].filter(Boolean).join(" — ") || "—"] }))) : stateBox("load", "Пусто"));
  }
  if ((d.reviews || []).length) { out.append(h("h3", {}, "Подписанные проверки")); out.append(table(["Gate", "Решение", "Кто", "Заметка"], d.reviews.map((r) => ({ cells: [txt(r.gate_id), txt(r.decision), txt(r.reviewer || r.by), txt(r.note)] })))); }
  return out;
}

/* ---------- compliance ---------- */
async function viewCompliance(el) {
  const out = h("div"), res = h("div"), fb = h("div");
  const sk = S.sel || "";
  let aid = S.lastAssessment[sk];
  try { aid = aid || sessionStorage.getItem("wt_last_" + sk); } catch (e) { /* */ }
  const aInp = inp("aid", "number", aid || "", { min: 1, "aria-label": "id сохранённой оценки" });
  el.append(card("Подпись проверок (compliance / owner)", h("p", { class: "mut" }, "Выбранный тендер: " + (sk || "— (выберите в разделе Tenders)") + ". Подписываются только сохранённые оценки."),
    h("div", { class: "bar" }, field("id сохранённой оценки", aInp), h("button", { type: "button", onclick: loadA }, "Загрузить оценку")), out, res),
  card("Подача заявки", h("p", { class: "msg", style: "background:#4d252a" }, "Подача заявок, контакт с контрагентами и закупка контролируемых товаров недоступны без отдельного решения владельца и юридической проверки."),
    h("div", { class: "bar" }, h("button", { type: "button", disabled: true, "aria-disabled": "true", title: "Недоступно без юридической проверки и одобрения владельца" }, "Подать заявку"),
      h("button", { type: "button", onclick: async () => { clear(fb); try { await POST("/actions/submit-bid"); fb.append(msgBox("err", "неожиданно: сервер не отказал")); } catch (e) { if (e.status !== 401) fb.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); } } }, "Проверить отказ сервера")), fb));
  if (aid) loadA();
  async function loadA() {
    clear(res);
    await load(out, async () => {
      const a = await GET("/assessments/" + encodeURIComponent(aInp.value));
      const form = h("form", { class: "form" },
        field("Gate", sel("gate_id", a.gates.map((g) => [g.id, `${g.id} (${g.status})`]))), field("Решение", sel("decision", [["cleared", "cleared — снять"], ["rejected", "rejected — отклонить"]])),
        field("Заметка (мин. 5 символов)", inp("note", "text", "", { minlength: 5, required: true })), field("legal_reference (для controlled_goods)", inp("legal_reference")),
        h("button", { type: "submit" }, "Подписать"));
      submit(form, (v) => POST(`/assessments/${a.id}/review`, { gate_id: v.gate_id, decision: v.decision, note: v.note, legal_reference: v.legal_reference || null }), res);
      return h("div", {}, h("div", { class: "bar" }, badge("оценка #" + a.id), badge(a.tender_key), sb(a.compliance_status)),
        table(["Gate", "Статус", "Причина", "Нужно"], a.gates.map((g) => ({ cells: [g.id, sb(g.status), txt(g.reason), txt(g.evidence_needed)] }))), form);
    });
  }
}

/* ---------- company fit ---------- */
const PROFILE_FIELDS = [["name", "text"], ["country", "text"], ["registered", "checkbox"], ["vat_id", "text"], ["sam_uei", "text"], ["cpv_capabilities", "list"], ["certificates", "list"],
  ["min_prep_days", "number"], ["capital_limit_usd", "number"], ["base_currency", "text"], ["past_performance", "list"]];
async function viewFit(el) {
  const prof = h("div"), ap = h("div"), res = h("div");
  el.append(card("Профиль компании", prof, res), card("Ожидающие запросы изменения профиля", ap));
  await load(prof, async () => {
    const d = await GET("/company/profile"), p = d.profile || {};
    clear(ap).append(d.pending.length ? approvalsTable(d.pending, false) : stateBox("load", "Нет ожидающих запросов."));
    if (!Object.keys(p).length) prof.append(stateBox("load", "Профиль пуст."));
    const form = h("form", { class: "form" });
    for (const [k, ty] of PROFILE_FIELDS) {
      const v = p[k];
      if (ty === "checkbox") { const c = h("input", { type: "checkbox", name: k }); c.checked = !!v; form.append(h("label", { class: "chk" }, c, k)); }
      else if (ty === "list") form.append(field(k + " (через запятую)", inp(k, "text", Array.isArray(v) ? v.join(", ") : (v == null ? "" : v))));
      else form.append(field(k, inp(k, ty, v == null ? "" : v)));
    }
    form.append(h("button", { type: "submit" }, "Запросить изменение (нужно одобрение владельца)"));
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); clear(res);
      const changes = {};
      for (const [k, ty] of PROFILE_FIELDS) {
        const el2 = form.elements[k];
        if (ty === "checkbox") { if (el2.checked !== !!p[k]) changes[k] = el2.checked; continue; }
        let v = el2.value;
        if (ty === "number") v = v === "" ? null : Number(v);
        else if (ty === "list") v = v.split(",").map((s) => s.trim()).filter(Boolean);
        if (JSON.stringify(v) !== JSON.stringify(p[k] == null ? (ty === "list" ? [] : "") : p[k]) && !(v === "" && p[k] == null)) changes[k] = v;
      }
      if (!Object.keys(changes).length) { res.append(msgBox("err", "Нет изменений.")); return; }
      try { const r = await api("PATCH", "/company/profile", { changes }); res.append(msgBox("ok", "Создан запрос на одобрение #" + r.approval_id + ": " + JSON.stringify(changes))); viewRefresh(); }
      catch (x) { if (x.status !== 401) res.append(msgBox("err", "HTTP " + x.status + " — " + x.message)); }
    });
    return form;
  });
}
function viewRefresh() { render(); }

/* ---------- finance ---------- */
const FIN_FIELDS = [["currency", "text", "USD"], ["purchase", "number", ""], ["freight", "number", 0], ["insurance", "number", 0], ["duties_pct", "number", 0], ["certification", "number", 0],
  ["financing", "number", 0], ["risk_reserve_pct", "number", 0], ["overhead", "number", 0], ["bid_price", "number", ""], ["supplier_prepay_pct", "number", 30], ["delivery_day", "number", 30],
  ["acceptance_day", "number", 40], ["customer_pay_delay_days", "number", 30], ["vat_import", "number", 0], ["vat_recovery_days", "number", 60], ["logistics_cost", "number", 0],
  ["buyer_prepay_pct", "number", 0], ["annual_rate", "number", 0.12], ["capital_limit_usd", "number", 50000]];
async function viewFinance(el) {
  const res = h("div"), outBox = h("div");
  const form = h("form", { class: "form" });
  for (const [k, ty, def] of FIN_FIELDS) form.append(field(k, inp(k, ty, def, ty === "number" ? { step: "any", required: k === "purchase" || k === "bid_price" } : {})));
  form.append(field("fx (JSON, напр. {\"EUR\": 1.1})", inp("fx", "text", "{}")), chk("order_confirmed", "order_confirmed"), chk("supplier_contract_signed", "supplier_contract_signed"), h("button", { type: "submit" }, "Рассчитать"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); clear(res); clear(outBox);
    const v = formData(form), body = {};
    for (const [k, ty] of FIN_FIELDS) body[k] = ty === "number" ? Number(v[k]) : v[k];
    try { body.fx = JSON.parse(v.fx || "{}"); } catch (x) { res.append(msgBox("err", "fx: некорректный JSON")); return; }
    body.order_confirmed = v.order_confirmed; body.supplier_contract_signed = v.supplier_contract_signed;
    try { outBox.append(financeResult(await POST("/finance/estimate", body))); }
    catch (x) { if (x.status !== 401) res.append(msgBox("err", "HTTP " + x.status + " — " + x.message)); }
  });
  el.append(card("Оценка себестоимости и денежного потока", h("p", { class: "mut" }, "Все числа вводит человек; приложение не подставляет рыночных допущений. Суммы показаны как строки от API."), form, res), outBox);
}
function financeResult(r) {
  const box = h("div", { class: "grid", style: "margin-top:12px" });
  box.append(card("Landed cost (" + r.currency + ")", kv(Object.entries(r.landed).map(([k, v]) => [k, v]))));
  box.append(card("Маржа и наценка", kv([["bid", r.metrics.bid], ["landed_cost", r.metrics.landed_cost], ["profit", r.metrics.profit], ["margin_pct (от цены)", r.metrics.margin_pct], ["markup_pct (от себестоимости)", r.metrics.markup_pct]])));
  box.append(card("Cashflow", kv([["Пик потребности", r.cashflow.peak_funding_need], ["День пика", r.cashflow.peak_day], ["Итоговый баланс", r.cashflow.final_balance], ["Пик в USD", r.peak_funding_need_usd]]),
    table(["День", "Событие", "Сумма", "Накопленно"], r.cashflow.timeline.map((e) => ({ cells: [e.day, e.label, e.amount, e.cumulative] })))));
  box.append(card("Стресс: задержка оплаты", table(["Доп. дней", "Пик", "Доп. стоимость", "Прибыль", "Маржа %", "Итоговый баланс"], r.stress.map((s) => ({ cells: [s.extra_delay_days, s.peak_funding_need, s.extra_carry_cost, s.profit, txt(s.margin_pct), s.final_balance] })))));
  box.append(card("Проверка лимита капитала", h("div", { class: "bar" }, sb(r.capital.status), badge("allowed: " + r.capital.allowed), badge("нужно одобрение владельца: " + r.capital.needs_owner_approval)), h("p", {}, txt(r.capital.reason)), h("p", { class: "mut" }, txt(r.disclaimer))));
  return box;
}

/* ---------- deals ---------- */
async function viewDeals(el) {
  const res = h("div"), list = h("div");
  const form = h("form", { class: "form" },
    field("title", inp("title", "text", "", { required: true })), field("tender_key", inp("tender_key", "text", S.sel || "")), field("buyer_country", inp("buyer_country", "text", "", { required: true })),
    field("goods", inp("goods", "text", "", { required: true })), field("cpv (через запятую)", inp("cpv")), field("order_value", inp("order_value", "number", "", { step: "any" })), field("currency", inp("currency")),
    chk("order_confirmed", "order_confirmed"), chk("supplier_contract_signed", "supplier_contract_signed"),
    field("supplier.name", inp("s_name", "text", "", { required: true, minlength: 2 })), field("supplier.country", inp("s_country", "text", "", { required: true, minlength: 2, maxlength: 3 })),
    field("supplier.cpv_prefixes", inp("s_cpv")), field("supplier.source_url", inp("s_url")), chk("s_confirmed", "supplier.confirmed"), field("supplier.notes", inp("s_notes")),
    h("button", { type: "submit" }, "Создать сделку / состыковать"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); clear(res);
    const v = formData(form), csv = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const body = { title: v.title, tender_key: v.tender_key || null, buyer_country: v.buyer_country, goods: v.goods, cpv: csv(v.cpv), order_value: v.order_value === "" ? null : Number(v.order_value),
      currency: v.currency || null, order_confirmed: v.order_confirmed, supplier_contract_signed: v.supplier_contract_signed,
      supplier: { name: v.s_name, country: v.s_country, cpv_prefixes: csv(v.s_cpv), confirmed: v.s_confirmed, source_url: v.s_url || null, notes: v.s_notes } };
    try { const r = await POST("/deals", body); res.append(msgBox("ok", `Сделка #${r.id}: matched=${r.matched}, screening=${r.screening.screening}. ${r.next}`)); loadList(); }
    catch (x) { if (x.status !== 401) res.append(msgBox("err", "HTTP " + x.status + " — " + x.message)); }
  });
  el.append(card("Заказ и поставщик", form, res), card("Сделки", list));
  loadList();
  function loadList() {
    load(list, async () => {
      const d = await GET("/deals");
      if (!d.items.length) return stateBox("load", "Сделок пока нет.");
      return table(["#", "Название", "Тендер", "Страна", "Поставщик", "matched", "Санкции", "Одобрено владельцем", ""], d.items.map((x) => ({ cells: [x.id, x.title, txt(x.tender_key), x.buyer_country, x.supplier && x.supplier.name,
        badge(String(x.matched), x.matched ? "b-ok" : "b-grey"), x.screening && sb(x.screening.screening), badge(String(x.owner_approved), x.owner_approved ? "b-ok" : "b-grey"),
        h("button", { type: "button", onclick: async (ev) => { clear(res); try { const r = await POST(`/deals/${x.id}/request-approval`); res.append(msgBox("ok", "Запрос одобрения #" + r.approval_id)); } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); } } }, "Запросить одобрение владельца")] })));
    });
  }
}

/* ---------- learning ---------- */
async function viewLearning(el) {
  const cal = h("div"), prop = h("div"), res = h("div");
  const fd = h("form", { class: "form" }, field("tender_key", inp("tender_key", "text", S.sel || "", { required: true })), field("decision", sel("decision", ["go", "no_go", "watch"])),
    field("reasons (мин. 3)", inp("reasons", "text", "", { required: true, minlength: 3 })), h("button", { type: "submit" }, "Записать решение (owner)"));
  const fo = h("form", { class: "form" }, field("tender_key", inp("tender_key", "text", S.sel || "", { required: true })), field("result", sel("result", ["won", "lost", "withdrawn", "not_submitted"])),
    field("actual_margin_pct", inp("actual_margin_pct", "number", "", { step: "any" })), field("note", inp("note")), h("button", { type: "submit" }, "Записать исход (owner)"));
  submit(fd, (v) => POST("/learning/decisions", v), res);
  submit(fo, (v) => POST("/learning/outcomes", { tender_key: v.tender_key, result: v.result, actual_margin_pct: v.actual_margin_pct === "" ? null : Number(v.actual_margin_pct), note: v.note }), res);
  const dl = h("button", { type: "button", onclick: async () => {
    clear(res);
    try {
      const r = await raw("GET", "/learning/dataset");
      if (!r.ok) { const t = await r.text(); let d; try { d = JSON.parse(t).detail; } catch (e) { d = t; } throw new ApiError(r.status, d); }
      const blob = await r.blob(), url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: "learning_dataset.txt" }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
      res.append(msgBox("ok", "Датасет скачан (" + blob.size + " байт)."));
    } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); }
  } }, "Скачать датасет (owner)");
  const apply = h("button", { type: "button", onclick: async () => { clear(res); try { const r = await POST("/learning/proposal/apply"); res.append(msgBox("ok", "Запрос одобрения #" + r.approval_id)); } catch (e) { if (e.status !== 401) res.append(msgBox("err", "HTTP " + e.status + " — " + e.message)); } } }, "Предложить применить веса (owner)");
  el.append(card("Калибровка", cal), card("Предложение весов", prop, h("div", { class: "bar" }, apply)),
    card("Решения и исходы", h("h3", {}, "Решение владельца"), fd, h("h3", {}, "Исход тендера"), fo, h("div", { class: "bar" }, dl), res));
  load(cal, async () => {
    const d = await GET("/learning/calibration");
    return h("div", {}, h("p", {}, txt(d.note)), kv([["Закрытых исходов", d.total_closed_outcomes], ["Владелец vs модель", JSON.stringify(d.owner_vs_model)]]),
      Object.keys(d.by_predicted_tier).length ? table(["Tier", "n", "won", "сглаженный win rate", "достаточно данных"], Object.entries(d.by_predicted_tier).map(([k, r]) => ({ cells: [k, r.n, r.won, r.win_rate_smoothed, String(r.enough_data)] }))) : stateBox("load", "Данных калибровки нет."));
  });
  load(prop, async () => {
    const d = await GET("/learning/proposal");
    if (d.status !== "PROPOSAL") return h("div", {}, h("div", { class: "bar" }, badge(d.status, "b-grey")), h("p", {}, `Данных недостаточно: n=${d.n}, нужно ${d.needed}.`), kv(Object.entries(d.current_weights || {})));
    return h("div", {}, kv(Object.entries(d.weights)), h("pre", { class: "msg", style: "overflow:auto" }, JSON.stringify(d.evidence, null, 2)));
  });
}

/* ---------- sources ---------- */
async function viewSources(el) {
  const res = h("div"), box = h("div"), ref = h("div");
  /* each button posts once, shows the server result or its error verbatim, then refreshes status */
  const act = (label, path, body, role) => h("button", { type: "button", title: "POST /api/v1" + path + " " + JSON.stringify(body) + " — роль " + role, onclick: async (ev) => {
    const b = ev.currentTarget; clear(res); b.disabled = true;
    res.append(stateBox("load", label + ": выполняется…"));
    try { const r = await POST(path, body); clear(res).append(msgBox("ok", label + " — OK: " + JSON.stringify(r))); S.tenders = null; }
    catch (e) { clear(res); if (e.status !== 401) res.append(msgBox("err", label + " — HTTP " + e.status + " — " + e.message)); }
    finally { b.disabled = false; loadHealth(); loadBadges(); refresh(); refRefresh(); }
  } }, label);
  el.append(card("Источники данных",
    h("h3", {}, "Тендеры (роль analyst)"),
    h("div", { class: "bar" }, act("Загрузить DEMO", "/ingest", { source: "demo" }, "analyst"), act("TED: Чехия", "/ingest", { source: "ted", country: "CZE" }, "analyst"),
      act("TED: весь мир", "/ingest", { source: "ted", country: null }, "analyst"), act("World Bank (весь мир)", "/ingest", { source: "worldbank" }, "analyst")),
    h("h3", {}, "Справочники"),
    h("div", { class: "bar" }, act("Загрузить санкции (OFAC+ООН)", "/sanctions/fetch", { sources: ["ofac", "un"] }, "compliance"), act("Курсы ЧНБ", "/fx/fetch", {}, "analyst"),
      act("Конфликты UCDP", "/conflicts/fetch", {}, "compliance")),
    h("p", { class: "mut" }, "Внешние источники работают только при WAR_TERMINAL_LIVE=true; иначе сервер отвечает LIVE_DISABLED. Санкции и конфликты требуют роль compliance."),
    res, h("h3", {}, "Справочные данные"), ref, h("h3", {}, "Статус источников"), box));
  function refRefresh() {
    load(ref, async () => {
      const [sx, fx] = [await GET("/sanctions/status").catch((e) => ({ error: e })), await GET("/fx").catch((e) => ({ error: e }))];
      const errTxt = (e) => "HTTP " + e.status + " — " + e.message;
      return kv([
        ["Санкции: записей", sx.error ? errTxt(sx.error) : (sx.count ? String(sx.count) : "0 — не загружены")],
        ["Санкции: as_of", sx.error ? null : sx.as_of], ["Санкции: list_version", sx.error ? null : sx.list_version], ["Санкции: источник", sx.error ? null : sx.source],
        ["Курсы: as_of", fx.error ? errTxt(fx.error) : (fx.as_of || "не загружены")], ["Курсы: источник", fx.error ? null : fx.source],
        ["Курсы: пар", fx.error ? null : String(Object.keys(fx.rates || {}).length)],
        ["Курсы: URL", !fx.error && fx.url && /^https?:\/\//.test(fx.url) ? h("a", { href: fx.url, target: "_blank", rel: "noopener noreferrer" }, fx.url) : null]]);
    });
  }
  refRefresh();
  function refresh() {
    load(box, async () => {
      const d = await GET("/sources/status");
      return h("div", {}, kv([["Режим", d.data.mode], ["Live включён", String(d.data.live_enabled)], ["DEMO-тендеров", d.data.demo_tenders], ["Watermark", d.data.watermark]]),
        table(["Источник", "Тип", "Статус", "Причина", "Последний успех", "Устарел", "Кол-во"], d.sources.map((s) => ({ cells: [txt(s.name || s.id), txt(s.kind), sb(s.status), txt(s.reason || s.note), txt(s.last_success), s.stale == null ? "—" : String(s.stale), txt(s.count != null ? s.count : s.last_count)] }))));
    });
  }
  refresh();
}

/* ---------- approvals ---------- */
function approvalsTable(items, canDecide, onDone) {
  return table(["#", "Действие", "Данные", "Статус", "Кто просил", "Решил", "Заметка", canDecide ? "Решение" : ""], items.map((a) => {
    const note = h("input", { placeholder: "заметка", "aria-label": "Заметка к решению #" + a.id, size: 12 });
    const btn = (ok, label) => h("button", { type: "button", onclick: async (ev) => {
      try { await POST(`/approvals/${a.id}/decide`, { approve: ok, note: note.value }); onDone && onDone(); }
      catch (e) { if (e.status !== 401) ev.target.closest("td").append(msgBox("err", "HTTP " + e.status + " — " + e.message)); }
    } }, label);
    return { cells: [a.id, a.action, h("code", {}, JSON.stringify(a.payload)), sb(a.status), txt(a.requested_by), txt(a.decided_by), txt(a.note),
      canDecide && a.status === "pending" ? h("div", { class: "bar" }, note, btn(true, "Одобрить"), btn(false, "Отклонить")) : ""] };
  }));
}
async function viewApprovals(el) {
  const box = h("div"); el.append(card("Запросы на одобрение", h("p", { class: "mut" }, "Решение принимает только токен роли owner; иначе сервер ответит 403."), box));
  const reload = () => load(box, async () => { const d = await GET("/approvals"); return d.items.length ? approvalsTable(d.items, true, reload) : stateBox("load", "Запросов нет."); });
  reload();
}

/* ---------- router ---------- */
const VIEWS = { overview: viewOverview, conflicts: viewConflicts, tenders: viewTenders, fit: viewFit, compliance: viewCompliance, finance: viewFinance, deals: viewDeals,
  learning: viewLearning, sources: viewSources, approvals: viewApprovals };
async function render() {
  const m = clear($("#main"));
  S.cache = {}; S.mapApi = null;
  m.append(h("h2", { class: "vt" + (S.view === "overview" ? " sr" : "") }, SECTIONS.find((s) => s[0] === S.view)[1]));
  try { await VIEWS[S.view](m); } catch (e) { if (e.status !== 401) m.append(stateBox("err", "Ошибка: " + (e.message || e))); }
}
function boot() {
  $("#whoami").textContent = "роль: " + S.role;
  $("#logout").hidden = false;
  loadHealth(); renderNav(); render(); loadBadges();
}
document.addEventListener("DOMContentLoaded", () => {
  tick(); setInterval(tick, 30000);
  $("#logout").addEventListener("click", () => { try { sessionStorage.removeItem("wt_token"); } catch (e) { /* */ } S.token = ""; clear($("#main")); showGate(); });
  $("#gsearch").addEventListener("keydown", (e) => { if (e.key === "Enter") { S.filters.q = e.target.value.trim(); S.filters.f = "all"; go("tenders"); } });
  loadHealth();
  if (S.token) boot(); else showGate();
});
