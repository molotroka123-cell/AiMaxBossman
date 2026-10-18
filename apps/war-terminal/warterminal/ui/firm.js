"use strict";
/* Firm-data popup. Shown on EVERY opening of the terminal (every page load) and asks the owner for his firm's data.
   PRIVACY: the data lives in this tab (sessionStorage) and, only if the owner ticks the unchecked box, in localStorage of this device.
   It is sent to the local server per request (header X-Firm-Profile, only to /tenders, /assessments, /finance, /learning/decisions) and is
   NEVER attached to translation or AI calls, logs, git or exports; the server does not store it (tests/test_firm.py).
   Depends on app.js helpers (h, field, inp, sel, clear, msgBox, badge, kv, card, POST) and i18n.js (T, Tf, WT_LANG); loaded after app.js. */
const FIRM_KEY = "wt_firm", FIRM_SAVED_KEY = "wt_firm_saved";
const FIRM_PATHS = /^\/(tenders|assessments|finance\/|learning\/decisions)/;
const FIRM_COUNTRIES = ["CZ", "SK", "DE", "AT", "PL", "HU", "SI", "FR", "IT", "ES", "PT", "NL", "BE", "LU", "DK", "SE", "FI", "EE", "LV", "LT", "IE", "GR", "BG", "RO", "HR",
  "CY", "MT", "NO", "IS", "CH", "GB", "UA", "US", "GN", "AE", "TR"];
const FIRM_SECTORS = [["09", T("Топливо, энергия (CPV 09)")], ["15", T("Продукты питания (CPV 15)")], ["60", T("Транспорт и перевозки (CPV 60)")],
  ["63", T("Логистика, экспедирование (CPV 63)")], ["35", T("Безопасность, оборона (CPV 35)")], ["33", T("Медицина (CPV 33)")], ["45", T("Строительство (CPV 45)")],
  ["72", T("ИТ-услуги (CPV 72)")], ["18", T("Одежда, форма (CPV 18)")]];
const FIRM_CERTS = [["iso9001", "ISO 9001"], ["iso27001", "ISO 27001"], ["nato_aqap", "NATO AQAP"], ["facility_clearance", T("Допуск предприятия к секретным работам (FSC)")]];
const FIRM_LICS = [["dual_use", T("Лицензия на экспорт товаров двойного назначения")], ["military_export", T("Лицензия на экспорт военной продукции")],
  ["brokering", T("Лицензия на посредничество (brokering)")]];

/* Czech IČO: 8 digits, last digit = mod-11 check over the first seven (weights 8..2). Same rule as warterminal/firm.py. */
function firmIcoOk(v) {
  v = String(v || "").replace(/\s+/g, "");
  if (!/^\d{8}$/.test(v)) return false;
  let s = 0;
  for (let i = 0; i < 7; i++) s += Number(v[i]) * (8 - i);
  const r = s % 11, c = r === 0 ? 1 : r === 1 ? 0 : 11 - r;
  return c === Number(v[7]);
}
function firmDicOk(v) {
  v = String(v || "").replace(/\s+/g, "").toUpperCase();
  const m = /^CZ(\d{8,10})$/.exec(v);
  return !!m && (m[1].length === 8 ? firmIcoOk(m[1]) : true);
}
/* One validator for the popup (inline messages) and for tests in the browser. Returns "" when the value is acceptable. */
function firmCheckField(name, value, all) {
  const v = typeof value === "string" ? value.trim() : value;
  const cz = (all.country || "") === "CZ";
  switch (name) {
    case "legal_name": return v.length >= 2 ? "" : T("Укажите полное наименование фирмы (не короче 2 символов)");
    case "country": return /^[A-Z]{2}$/.test(v) ? "" : T("Выберите страну регистрации");
    case "reg_no":
      if (cz) return !/^\d{8}$/.test(v.replace(/\s+/g, "")) ? T("IČO: ровно 8 цифр") : firmIcoOk(v) ? "" : T("IČO: контрольная сумма (mod 11) не сходится — проверьте цифры");
      return /^[A-Za-z0-9][A-Za-z0-9 ./-]{2,29}$/.test(v) ? "" : T("Регистрационный номер: 3–30 символов (буквы, цифры, пробел, - . /)");
    case "vat_no":
      if (!v) return "";
      if (cz) return firmDicOk(v) ? "" : T("DIČ: CZ + 8–10 цифр (для юридического лица — действительный IČO)");
      return /^[A-Za-z]{2}[A-Za-z0-9]{2,12}$/.test(v.replace(/\s+/g, "")) ? "" : T("НДС-номер: две буквы страны и 2–12 знаков");
    case "cpv_extra": return !v || v.split(",").every((x) => /^\d{2,8}(-\d)?$/.test(x.trim())) ? "" : T("CPV: коды из 2–8 цифр через запятую");
    case "turnover_eur": case "guarantee_eur": return v === "" || (isFinite(Number(v)) && Number(v) >= 0) ? "" : T("Введите число не меньше нуля");
    case "staff": case "references": return v === "" || (/^\d+$/.test(String(v))) ? "" : T("Введите целое число не меньше нуля");
    default: return "";
  }
}

const FIRM = {
  data: null,                                         // validated firm object, or null = view-only
  stored() {                                          // prefill source: this tab first, then the device copy the owner asked to keep
    for (const [st, key] of [[() => sessionStorage, FIRM_KEY], [() => localStorage, FIRM_SAVED_KEY]]) {
      try { const raw = st().getItem(key); if (raw) { const d = JSON.parse(raw); if (d && typeof d === "object") return { data: d, remembered: key === FIRM_SAVED_KEY }; } } catch (e) { /* storage may be blocked */ }
    }
    return { data: null, remembered: false };
  },
  remembered() { try { return !!localStorage.getItem(FIRM_SAVED_KEY); } catch (e) { return false; } },
  ask(force) {                                        // resolves after the owner confirmed or chose view-only
    return new Promise((resolve) => firmOpen(resolve));
  },
  chip() {
    const b = $("#firmb");
    if (!b) return;
    b.hidden = false;
    b.textContent = FIRM.data ? T("Фирма: ") + (FIRM.data.legal_name.length > 22 ? FIRM.data.legal_name.slice(0, 21) + "…" : FIRM.data.legal_name) : T("Только просмотр");
    b.className = "badge firmchip " + (FIRM.data ? "b-ok" : "b-warn");
    b.title = FIRM.data ? T("Данные фирмы введены (только в этой вкладке). Нажмите, чтобы изменить.") : T("Данные фирмы не введены: оценка соответствия и финансы отключены. Нажмите, чтобы ввести.");
  },
  rows() {                                            // summary for the Company section: every field, "нет данных" where nothing was entered
    const d = FIRM.data, nd = T("нет данных"), yes = (a, list) => (a && a.length ? list.filter(([k]) => a.includes(k)).map(([, l]) => l).join(", ") : nd);
    const num = (n) => (n == null || n === "" ? nd : fmtN(Number(n)));
    return [[T("Наименование"), d.legal_name], [T("Страна"), firmCountryName(d.country)], [T("Регистрационный номер"), d.reg_no || nd], [T("НДС-номер (DIČ)"), d.vat_no || nd],
      [T("Адрес"), d.address || nd], [T("Секторы / CPV"), d.cpv && d.cpv.length ? d.cpv.join(", ") : nd], [T("Оборот за год, EUR"), num(d.turnover_eur)], [T("Сотрудников"), num(d.staff)],
      [T("Сертификаты"), yes(d.certs, FIRM_CERTS)], [T("Лицензии"), yes(d.licences, FIRM_LICS)], [T("Сопоставимых референсов"), num(d.references)], [T("Лимит банковской гарантии, EUR"), num(d.guarantee_eur)]];
  },
  banner() {                                          // shown wherever fit / finance are off because nothing was entered
    return h("div", { class: "fbanner", role: "status" }, h("span", {}, T("Режим «только просмотр»: данные фирмы не введены, поэтому оценка соответствия и калькулятор финансов отключены.")), " ",
      h("button", { type: "button", class: "sm", onclick: () => FIRM.reopen() }, T("Ввести данные фирмы")));
  },
  async reopen() { await FIRM.ask(true); S.tenders = null; render(); },
};
function firmCountryName(c) {
  try { return new Intl.DisplayNames([WT_LANG === "en" ? "en" : "ru"], { type: "region" }).of(c) + " (" + c + ")"; } catch (e) { return c; }
}
/* The only place where the firm header is built. It is never attached to translation or AI calls and only to the few paths that need it. */
function firmHeaders(path) {
  if (!FIRM.data || /^\/(translate|ai)/.test(path) || !FIRM_PATHS.test(path)) return {};
  const bytes = new TextEncoder().encode(JSON.stringify(FIRM.data));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return { "X-Firm-Profile": btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") };
}

function firmOpen(done) {
  const prev = document.activeElement, st = FIRM.stored(), d = st.data || {};
  const back = h("div", { class: "fm-back", id: "firmmodal" });
  const errs = {};
  const mk = (name, label, input, hint) => {
    const e = h("div", { class: "fm-err", id: "fe-" + name, "aria-live": "polite" });
    errs[name] = e;
    input.setAttribute("aria-describedby", "fe-" + name);
    return h("label", { class: "fm-f" }, h("span", {}, label), input, hint ? h("span", { class: "mut fm-hint" }, hint) : null, e);
  };
  const country = sel("country", [["", T("— выберите —")], ...FIRM_COUNTRIES.map((c) => [c, firmCountryName(c)])], d.country || "CZ");
  const f = {
    legal_name: inp("legal_name", "text", d.legal_name, { autocomplete: "organization", required: true, maxlength: 200 }),
    country, reg_no: inp("reg_no", "text", d.reg_no, { inputmode: "text", autocomplete: "off", required: true, maxlength: 30 }),
    vat_no: inp("vat_no", "text", d.vat_no, { autocomplete: "off", maxlength: 20 }), address: inp("address", "text", d.address, { autocomplete: "street-address", maxlength: 300 }),
    cpv_extra: inp("cpv_extra", "text", ""), turnover_eur: inp("turnover_eur", "number", d.turnover_eur, { min: 0, step: "any", inputmode: "decimal" }),
    staff: inp("staff", "number", d.staff, { min: 0, step: 1, inputmode: "numeric" }), references: inp("references", "number", d.references, { min: 0, step: 1, inputmode: "numeric" }),
    guarantee_eur: inp("guarantee_eur", "number", d.guarantee_eur, { min: 0, step: "any", inputmode: "decimal" }),
  };
  const known = FIRM_SECTORS.map(([k]) => k);
  f.cpv_extra.value = (d.cpv || []).filter((c) => !known.includes(c)).join(", ");
  const boxes = (name, list, chosen) => list.map(([k, l]) => { const c = h("input", { type: "checkbox", name, value: k }); c.checked = (chosen || []).includes(k); return h("label", { class: "chk" }, c, l); });
  const sectorBoxes = boxes("sector", FIRM_SECTORS, d.cpv), certBoxes = boxes("cert", FIRM_CERTS, d.certs), licBoxes = boxes("lic", FIRM_LICS, d.licences);
  const remember = h("input", { type: "checkbox", id: "fm-remember" });
  remember.checked = st.remembered;                  // unchecked by default; checked only when a device copy already exists (so confirming keeps it)
  const summary = h("div", { class: "msg err", role: "alert", hidden: true });
  const regLabel = h("span", {}, T("Регистрационный номер (IČO)"));
  const regField = mk("reg_no", regLabel, f.reg_no, T("Для Чехии: 8 цифр, проверяется контрольная сумма mod 11"));
  const syncRegLabel = () => { regLabel.textContent = f.country.value === "CZ" ? T("Регистрационный номер (IČO)") : T("Регистрационный номер компании"); };
  const values = () => ({ legal_name: f.legal_name.value.trim(), country: f.country.value, reg_no: f.reg_no.value.trim(), vat_no: f.vat_no.value.trim(), cpv_extra: f.cpv_extra.value,
    turnover_eur: f.turnover_eur.value, staff: f.staff.value, references: f.references.value, guarantee_eur: f.guarantee_eur.value });
  const checkOne = (name) => {
    const v = values(), msg = firmCheckField(name, v[name], v);
    errs[name].textContent = msg; f[name].setAttribute("aria-invalid", msg ? "true" : "false");
    return msg;
  };
  const NAMES = ["legal_name", "country", "reg_no", "vat_no", "cpv_extra", "turnover_eur", "staff", "references", "guarantee_eur"];
  const form = h("form", { class: "fm", role: "dialog", "aria-modal": "true", "aria-labelledby": "fm-title", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const bad = NAMES.filter(checkOne);
    if (bad.length) { summary.hidden = false; summary.textContent = T("Исправьте отмеченные поля.") + " (" + bad.length + ")"; f[bad[0]].focus(); return; }
    summary.hidden = true;
    const v = values(), num = (x) => (x === "" ? null : Number(x));
    const cpv = [...new Set([...form.querySelectorAll("input[name=sector]:checked")].map((c) => c.value).concat(v.cpv_extra.split(",").map((x) => x.trim()).filter(Boolean)))].sort();
    const data = { legal_name: v.legal_name, country: v.country, reg_no: v.country === "CZ" ? v.reg_no.replace(/\s+/g, "") : v.reg_no, vat_no: v.vat_no.replace(/\s+/g, "").toUpperCase(),
      address: f.address.value.trim(), cpv, turnover_eur: num(v.turnover_eur), staff: num(v.staff), references: num(v.references), guarantee_eur: num(v.guarantee_eur),
      certs: [...form.querySelectorAll("input[name=cert]:checked")].map((c) => c.value), licences: [...form.querySelectorAll("input[name=lic]:checked")].map((c) => c.value) };
    try {                                              // second check on the server (same IČO rule); loopback only, nothing is stored there
      const r = await POST("/firm/validate", data);
      if (!r.valid) { summary.hidden = false; summary.textContent = T("Сервер не принял поля: ") + r.fields.join(", "); return; }
    } catch (x) { if (x.status === 401) return; }
    FIRM.data = data;
    try { sessionStorage.setItem(FIRM_KEY, JSON.stringify(data)); } catch (x) { /* storage may be blocked: the data then lives in memory only */ }
    try { if (remember.checked) localStorage.setItem(FIRM_SAVED_KEY, JSON.stringify(data)); else localStorage.removeItem(FIRM_SAVED_KEY); } catch (x) { /* ignore */ }
    close();
  } },
  h("h2", { id: "fm-title" }, T("Данные вашей фирмы")),
  h("p", { class: "mut" }, T("Все данные — реальные, без демо. Введите данные фирмы при каждом открытии терминала: по ним считаются оценка соответствия и финансы.")),
  h("p", { class: "fm-priv" }, T("Приватность: данные остаются на этом компьютере. Они не уходят в облачные модели, переводчик, журналы, git или экспорт и не сохраняются на сервере; по умолчанию живут только в этой вкладке.")),
  h("fieldset", { class: "fm-g" }, h("legend", {}, T("Организация")),
    mk("legal_name", T("Юридическое наименование"), f.legal_name), mk("country", T("Страна регистрации"), f.country), regField,
    mk("vat_no", T("НДС-номер (DIČ)"), f.vat_no, T("Необязательно; для Чехии: CZ + 8–10 цифр")), mk("address", T("Юридический адрес"), f.address)),
  h("fieldset", { class: "fm-g" }, h("legend", {}, T("Сектора и коды CPV (можно несколько)")), h("div", { class: "fm-checks" }, sectorBoxes),
    mk("cpv_extra", T("Другие коды CPV"), f.cpv_extra, T("2–8 цифр через запятую, например 34100000"))),
  h("fieldset", { class: "fm-g" }, h("legend", {}, T("Масштаб")), mk("turnover_eur", T("Годовой оборот, EUR"), f.turnover_eur), mk("staff", T("Число сотрудников"), f.staff),
    mk("references", T("Сопоставимых выполненных контрактов (референсов)"), f.references), mk("guarantee_eur", T("Лимит банковской гарантии, EUR"), f.guarantee_eur)),
  h("fieldset", { class: "fm-g" }, h("legend", {}, T("Сертификаты")), h("div", { class: "fm-checks" }, certBoxes)),
  h("fieldset", { class: "fm-g" }, h("legend", {}, T("Лицензии на экспорт")), h("div", { class: "fm-checks" }, licBoxes)),
  h("p", { class: "mut" }, T("Пустое поле означает «нет данных»: терминал ничего не подставляет за вас.")),
  summary,
  h("div", { class: "fm-foot" },
    h("label", { class: "chk" }, remember, T("Запомнить на этом устройстве")),
    h("div", { class: "fm-btns" },
      h("button", { type: "button", id: "fm-skip", onclick: () => { FIRM.data = null; try { sessionStorage.removeItem(FIRM_KEY); } catch (x) { /* */ } close(); } }, T("Пропустить — только просмотр")),
      h("button", { type: "submit", class: "primary", id: "fm-ok" }, T("Подтвердить и открыть")))));
  for (const n of NAMES) { f[n].addEventListener("input", () => { checkOne(n); if (n === "country") { syncRegLabel(); checkOne("reg_no"); checkOne("vat_no"); } }); f[n].addEventListener("blur", () => checkOne(n)); }
  syncRegLabel();
  form.addEventListener("keydown", (e) => {            // keep the Tab cycle inside the dialog
    if (e.key !== "Tab") return;
    const els = [...form.querySelectorAll("input,select,button")].filter((x) => !x.disabled && !x.hidden && x.offsetParent !== null);
    if (!els.length) return;
    const first = els[0], last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  back.append(form);
  document.body.append(back);
  document.body.classList.add("fm-open");
  f.legal_name.focus();
  function close() {
    back.remove(); document.body.classList.remove("fm-open");
    FIRM.chip();
    if (prev && prev.focus && document.contains(prev)) prev.focus();
    done();
  }
}
