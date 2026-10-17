"use strict";
/* Bossman War & Procurement Terminal UI. Vanilla JS, no network except same-origin /api/v1. All API strings go through textContent. */
const API = "/api/v1";
const ROLES = ["viewer", "analyst", "compliance", "owner"];
const SECTIONS = [
  ["overview", "Overview"], ["conflicts", "Conflicts"], ["tenders", "Tenders"], ["fit", "Company Fit"], ["compliance", "Compliance"],
  ["finance", "Finance"], ["deals", "Deals"], ["learning", "Learning"], ["sources", "Sources"], ["approvals", "Approvals"],
];
const S = { token: "", role: "viewer", view: "overview", tenders: null, sel: null, filters: { f: "all", q: "", category: "", country: "" },
  lastAssessment: {}, health: null };
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
    $(".layout").prepend(s);
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

/* ---------- map ---------- */
const MW = 720, MH = 360;
const px = (lon, lat) => [((lon + 180) / 360 * MW).toFixed(1), ((90 - lat) / 180 * MH).toFixed(1)];
const LAND = [ // schematic only, hand-written
  [[-168, 66], [-140, 70], [-95, 72], [-65, 60], [-55, 50], [-80, 25], [-97, 18], [-85, 10], [-105, 22], [-125, 40], [-135, 58]],
  [[-80, 10], [-60, 10], [-35, -6], [-40, -22], [-58, -38], [-68, -54], [-75, -45], [-71, -18], [-81, -5]],
  [[-10, 36], [-9, 43], [5, 50], [10, 58], [28, 70], [80, 74], [140, 72], [180, 67], [165, 58], [142, 48], [122, 30], [110, 18], [100, 8], [90, 22], [72, 20], [58, 25], [44, 12], [35, 32], [27, 38], [12, 38], [-5, 36]],
  [[-17, 21], [-6, 36], [32, 31], [43, 12], [51, 11], [40, -15], [33, -27], [20, -35], [12, -17], [9, 4], [-8, 5], [-17, 14]],
  [[114, -22], [131, -12], [142, -11], [153, -27], [146, -39], [130, -32], [115, -34]],
];
const SEV = { low: "#ffd16a", medium: "#ff9f43", high: "#f64b49" };
function svgEl(tag, attrs) { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; }
function worldMap(items) {
  const svg = svgEl("svg", { viewBox: `0 0 ${MW} ${MH}`, role: "img", "aria-label": "Схематичная карта мира с точками конфликтных регионов" });
  for (let lon = -180; lon <= 180; lon += 30) { const [x] = px(lon, 0); svg.append(svgEl("line", { x1: x, y1: 0, x2: x, y2: MH, stroke: "#1e3a55", "stroke-width": 0.5 })); }
  for (let lat = -90; lat <= 90; lat += 30) { const [, y] = px(0, lat); svg.append(svgEl("line", { x1: 0, y1: y, x2: MW, y2: y, stroke: "#1e3a55", "stroke-width": 0.5 })); }
  for (const poly of LAND) svg.append(svgEl("polygon", { points: poly.map(([lo, la]) => px(lo, la).join(",")).join(" "), fill: "#17324a", stroke: "#3b6288", "stroke-width": 0.8 }));
  const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
  const radius = (c) => { const d = num(c.deaths); return d == null ? 4 : Math.min(16, Math.max(3, 3 + Math.sqrt(Math.max(0, d)) / 20)); };
  // real dots largest first so small ones stay clickable on top; DEMO drawn last (outline only)
  const real = items.filter((c) => !c.demo).sort((a, b) => radius(b) - radius(a)), demo = items.filter((c) => c.demo);
  for (const c of real.concat(demo)) {
    if (typeof c.lat !== "number" || typeof c.lon !== "number") continue;
    const [x, y] = px(c.lon, c.lat);
    const col = SEV[c.severity] || "#9ab";
    const g = svgEl("g", {});
    let dot;
    if (c.demo) {
      dot = svgEl("circle", { cx: x, cy: y, r: 5, fill: col, "fill-opacity": "0.25", stroke: col, "stroke-width": 1.8, "stroke-dasharray": "2.5 2" });
    } else {
      dot = svgEl("circle", { cx: x, cy: y, r: radius(c).toFixed(1), fill: col, "fill-opacity": "0.78", stroke: "#07111f", "stroke-width": 0.8 });
    }
    const t = svgEl("title");
    t.textContent = c.demo ? `${txt(c.name)} (${txt(c.country)}) — ${txt(c.severity)} — DEMO`
      : `${txt(c.country)}: событий ${txt(c.events)}, погибших ${txt(c.deaths)}, год ${txt(c.since)}`;
    g.append(dot, t); svg.append(g);
  }
  return svg;
}
const sevBadge = (s) => badge(s, s === "high" ? "b-bad" : s === "medium" ? "b-warn" : "b-grey");
const uniq = (arr) => [...new Set(arr.filter((x) => x != null && x !== ""))];
function conflictsPanel(d, compact) {
  const wrap = h("div", { class: "map" });
  const items = d.items || [], real = items.filter((c) => !c.demo);
  if (d.status === "NOT_CONNECTED") wrap.append(stateBox("load", "NOT_CONNECTED: " + txt(d.message)));
  wrap.append(worldMap(items));
  const lg = h("div", { class: "legend" }, ...["low", "medium", "high"].map((k) => h("span", {}, h("i", { style: "background:" + SEV[k] }), k)));
  if (real.length) lg.append(h("span", {}, h("i", { style: "background:#9ab;width:6px;height:6px" }), h("i", { style: "background:#9ab;width:14px;height:14px" }), "радиус ∝ √погибших"));
  if (items.some((c) => c.demo)) lg.append(h("span", {}, h("i", { style: "border:1.5px dashed #ffcf71" }), "DEMO — пунктир, синтетические данные"));
  wrap.append(lg);
  if (real.length) {
    const PREC = "средняя точка событий страны за год; не координаты событий";
    const prec = uniq(real.map((c) => c.precision)).filter((p) => p !== PREC);
    wrap.append(h("div", { class: "legend src" },
      h("span", {}, "Источник: ", h("b", {}, uniq(real.map((c) => c.source_id)).join(", ") || "—")),
      h("span", {}, "Год: ", h("b", {}, uniq(real.map((c) => c.since)).join(", ") || "—")),
      h("span", {}, "Лицензия: ", h("b", {}, uniq(real.map((c) => c.licence)).join(", ") || "—")),
      h("span", {}, "Точность: " + PREC + (prec.length ? " (precision: " + prec.join(", ") + ")" : ""))));
  }
  wrap.append(h("div", { class: "safety" }, txt(d.safety)));
  if (!items.length) wrap.append(stateBox("load", "Нет данных о конфликтах."));
  else if (!compact) wrap.append(conflictList(items));
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
async function loadTenders() {
  const f = S.filters;
  const p = new URLSearchParams();
  if (f.f === "EASY" || f.f === "MEDIUM" || f.f === "HARD") p.set("tier", f.f);
  if (f.f === "BLOCKED" || f.f === "REVIEW_REQUIRED") p.set("complexity", f.f);
  if (f.q) p.set("q", f.q);
  if (f.category) p.set("category", f.category);
  if (f.country) p.set("country", f.country);
  p.set("limit", "200");
  return GET("/tenders?" + p);
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
function statCard(label, v, cls) { return h("div", { class: "card" }, h("div", { class: "mut" }, label), h("div", { class: "stat " + (cls || "") }, String(v))); }

async function viewOverview(el) {
  const top = h("div", { class: "grid stats" }), two = h("div", { class: "grid two" });
  const mapBox = h("div"), confBox = h("div"), tblBox = h("div");
  two.append(card("Карта конфликтов", mapBox), card("Конфликты", confBox));
  el.append(top, two, h("div", { style: "margin-top:12px" }, card("Тендеры", tblBox)));
  await Promise.all([
    load(top, async () => {
      const d = await GET("/tenders?limit=200"); const c = countsFrom(d.items);
      S.tenders = d;
      return h("div", { class: "grid stats", style: "grid-column:1/-1" }, statCard("Тендеров всего", d.total), statCard("Лёгкие (EASY)", c.EASY), statCard("Средние (MEDIUM)", c.MEDIUM),
        statCard("Сложные (HARD)", c.HARD), statCard("Не допущены (BLOCKED)", c.BLOCKED, "b"), statCard("На проверке (REVIEW)", c.REVIEW_REQUIRED, "w"));
    }),
    load(mapBox, async () => conflictsPanel(await GET("/conflicts"), true)),
    load(confBox, async () => {
      const d = await GET("/conflicts"); const items = d.items || [];
      if (!items.length) return stateBox("load", "Нет данных.");
      if (items.some((c) => !c.demo)) return h("div", {}, h("div", { class: "mut" }, "Топ-10 стран по числу погибших (UCDP); полный список — в разделе Conflicts"), realConflictTable(items, 10));
      return conflictList(items);
    }),
    load(tblBox, async () => { const d = S.tenders || await GET("/tenders?limit=200"); if (!d.items.length) return stateBox("load", "Тендеров нет. Загрузите данные в разделе Sources."); return tenderTable(d.items.slice(0, 25), (k) => { S.sel = k; go("tenders"); }); }),
  ]);
}
async function viewConflicts(el) {
  const box = h("div"), realBox = h("div"), acdBox = h("div"), demoBox = h("div");
  el.append(card("Карта конфликтов", box), h("div", { class: "grid", style: "margin-top:12px;grid-template-columns:minmax(0,1fr)" },
    card("UCDP: агрегаты по странам (по числу погибших)", realBox), card("UCDP/PRIO ACD: вооружённые конфликты", acdBox), demoBox));
  let d;
  await load(box, async () => { d = await GET("/conflicts"); return conflictsPanel(d, true); });
  if (!d) { clear(realBox).append(stateBox("err", "Нет ответа /conflicts")); clear(acdBox); return; }
  const items = d.items || [], real = items.filter((c) => !c.demo), demo = items.filter((c) => c.demo);
  clear(realBox).append(real.length ? realConflictTable(real) : stateBox("load", "Реальных данных UCDP нет. Загрузите их в разделе Sources («Конфликты UCDP»)."));
  const acd = d.acd;
  clear(acdBox).append(acd && (acd.items || []).length ? h("div", {}, h("div", { class: "mut" }, "Год: " + txt(acd.year) + " · конфликтов: " + acd.items.length), acdTable(acd)) : stateBox("load", "Данных ACD нет."));
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
      return h("div", {}, h("div", { class: "mut" }, `Показано ${d.items.length} из ${d.total} · страна: ${S.filters.country || "весь мир"}`), tenderTable(d.items, (k) => { S.sel = k; renderTenders(); showDetail(det, k); }));
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
  m.append(h("h2", { style: "margin:0 0 10px;font-size:16px" }, SECTIONS.find((s) => s[0] === S.view)[1]));
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
