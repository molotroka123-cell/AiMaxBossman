"use strict";
/* Real-data helpers: provenance line (source, link, fetch time, LIVE marker) and the per-source status strip.
   There is no demo mode: a source that cannot be reached is shown as such, never replaced by old or sample data.
   Depends on app.js helpers (h, badge, clear, POST, GET, render) and i18n.js (T, Tf). Loaded after app.js. */
const SRC_NAME = { ted: "TED", cz_nen: "ISVZ/NEN (CZ)", browser_nen: "NEN (CZ)", nen: "NEN (CZ)", worldbank: "World Bank", prozorro: "Prozorro 🇺🇦", sam: "SAM.gov", usaspending: "USAspending", gdelt: "GDELT",
  ofac: "OFAC", un_sanctions: "UN SC", cnb: "CNB FX", ucdp: "UCDP", worldbank_ind: "World Bank WDI", unhcr: "UNHCR", ecb: "ECB", nominatim: "Nominatim", comtrade: "UN Comtrade" };
const srcName = (id) => SRC_NAME[id] || String(id || "").replace(/_[\d.]+$/, "") || "—";
const fetchedAt = (iso) => (iso ? String(iso).slice(0, 16).replace("T", " ") + " UTC" : T("нет данных"));

/* "LIVE · TED · link · fetched" for one record. Without a link or a fetch time the record is shown as unverified, never as live. */
function provLine(rec) {
  rec = rec || {};
  const url = rec.source_url && /^https:\/\//.test(rec.source_url) ? rec.source_url : null, at = rec.retrieved_at || rec.fetched_at || null;
  const ok = !!(url && at);
  const name = srcName(rec.source_id || rec.source);
  return h("span", { class: "prov" }, badge(ok ? T("LIVE") : T("не проверено"), ok ? "b-ok" : "b-warn"), " ",
    url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", title: url }, name) : h("span", {}, name), " ", h("span", { class: "mut" }, fetchedAt(at)));
}
function provShort(rec) {                              // compact form for table cells: link + date on two lines
  rec = rec || {};
  const url = rec.source_url && /^https:\/\//.test(rec.source_url) ? rec.source_url : null, at = rec.retrieved_at || rec.fetched_at || null;
  return h("div", { class: "prov s" }, url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", title: url }, srcName(rec.source_id || rec.source)) : srcName(rec.source_id || rec.source),
    h("div", { class: "mut" }, (url && at ? "● " : "○ ") + (at ? String(at).slice(0, 16).replace("T", " ") : T("нет данных"))));
}

const LIVE = {
  rows: [],
  async strip() {
    const box = $("#strip");
    if (!box) return;
    try {
      const d = await GET("/sources/status");
      LIVE.rows = d.sources || [];
      LIVE.paint(d.data || {});
    } catch (e) { if (e.status !== 401) { clear(box); box.append(h("span", { class: "chip bad" }, T("статус источников недоступен: ") + (e.message || e))); } }
  },
  paint(data) {
    const box = clear($("#strip"));
    box.hidden = false;
    box.append(h("span", { class: "strip-t" }, T("Источники (живые, только чтение):")));
    box.append(h("button", { type: "button", class: "sm", id: "refall", onclick: () => LIVE.refreshAll() }, T("Обновить из источников")));
    for (const s of LIVE.rows.filter((x) => x.adapter)) {
      let cls = "grey", txt = T("не запускался"), title = s.reason || s.note || "";
      if (s.status === "ERROR") { cls = "bad"; txt = T("недоступен"); title = s.reason || ""; }
      else if (s.status === "LIVE") { cls = s.stale ? "warn" : "ok"; txt = (s.stale ? T("устарел · ") : "") + fetchedAt(s.last_success).slice(5, 16); title = Tf(T("записей: {0}"), s.last_count == null ? "—" : s.last_count); }
      else if (s.status === "DISABLED") { cls = "warn"; txt = T("выключен"); }
      else if (s.status === "NOT_CONNECTED") { cls = "grey"; txt = T("нужен ключ"); }
      const c = h("span", { class: "chip " + cls, title: title || null, tabindex: 0 }, h("b", {}, srcName(s.id)), " ", txt);
      if (s.status === "ERROR") c.append(h("span", { class: "chip-why" }, " — " + String(s.reason || "").slice(0, 90)));
      box.append(c);
    }
    if (data && data.excluded_unverified) box.append(h("span", { class: "chip warn", title: T("Записи без источника или времени получения убраны из всех экранов и заархивированы") }, Tf(T("скрыто без источника: {0}"), data.excluded_unverified)));
  },
  /* one click: every source in turn; failures are reported per source and never hidden */
  async refreshAll() {
    const b = $("#refall"), out = $("#strip-out");
    if (b) b.disabled = true;
    const steps = [[T("TED: Чехия"), "/ingest", { source: "ted", country: "CZE" }], [T("TED: весь мир"), "/ingest", { source: "ted", country: null }], [T("ISVZ/NEN (Чехия)"), "/ingest", { source: "cz" }],
      [T("Всемирный банк (весь мир)"), "/ingest", { source: "worldbank" }], [T("Санкции (OFAC+ООН)"), "/sanctions/fetch", { sources: ["ofac", "un"] }], [T("Курсы ЧНБ"), "/fx/fetch", {}],
      [T("Конфликты UCDP"), "/conflicts/fetch", {}], [T("Показатели стран (WDI, UNHCR, ECB)"), "/indicators/fetch", {}]];
    const lines = [];
    clear(out);
    for (const [label, path, body] of steps) {
      out.textContent = T("Обновление: ") + label + "…";
      try { await POST(path, body); lines.push(["ok", label]); }
      catch (e) { if (e.status === 401) { if (b) b.disabled = false; return; } lines.push(["bad", label + " — " + (e.status ? "HTTP " + e.status + " — " : "") + (e.message || e)]); }
    }
    clear(out).append(...lines.map(([k, t]) => h("div", { class: "chip " + (k === "ok" ? "ok" : "bad") }, (k === "ok" ? "✓ " : "✗ ") + t)));
    S.tenders = null;
    await LIVE.strip(); loadHealth(); loadBadges(); render();
  },
};
