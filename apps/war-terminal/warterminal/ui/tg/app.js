/* Owner-only Telegram Mini App: Czech defence/security tenders (monitoring only).
   All text goes through text nodes (h() below) - no HTML-string injection anywhere. */
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp;
  var inTelegram = !!(tg && tg.initData);
  var nativeButtons = inTelegram && tg.platform && tg.platform !== "unknown";
  var csrf = null;
  var items = [];
  var filter = "all";
  var current = null;
  var DAY = 86400000;

  var GATE_RU = { jurisdiction: "Юрисдикция", controlled_goods: "Контролируемые товары", deadline: "Срок", currency: "Валюта",
    eligibility: "Квалификация", documents: "Документы", counterparty_screening: "Проверка заказчика" };
  var STATUS_RU = { PASS: "ок", REVIEW_REQUIRED: "проверка", BLOCKED: "блок", TODO: "сделать", REQUIRED: "обязательно",
    EASY: "лёгкий", MEDIUM: "средний", HARD: "сложный" };
  function ru(v) { return v === null || v === undefined || v === "" ? "—" : (STATUS_RU[v] || String(v)); }
  function pct(v) { var n = Number(v); return v === null || v === undefined || !isFinite(n) ? "—" : Math.round(n * 100) + "%"; }

  // ------------------------------------------------------------------ DOM helpers (safe by construction)
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") el.className = v;
      else if (k === "onclick") el.addEventListener("click", v);
      else el.setAttribute(k, String(v));
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function $(id) { return document.getElementById(id); }

  // ------------------------------------------------------------------ theme
  function applyTheme() {
    var p = (tg && tg.themeParams) || {};
    var map = { bg_color: "--bg", text_color: "--text", hint_color: "--hint", link_color: "--link", button_color: "--btn",
      button_text_color: "--btn-text", secondary_bg_color: "--bg2", section_bg_color: "--section", destructive_text_color: "--danger",
      accent_text_color: "--accent", section_separator_color: "--sep", subtitle_text_color: "--subtitle" };
    Object.keys(map).forEach(function (k) {
      if (typeof p[k] === "string" && /^#[0-9a-fA-F]{3,8}$/.test(p[k])) document.documentElement.style.setProperty(map[k], p[k]);
    });
    if (tg && tg.colorScheme) document.documentElement.setAttribute("data-scheme", tg.colorScheme === "dark" ? "dark" : "light");
  }

  // ------------------------------------------------------------------ formatting
  function money(v, cur) {
    if (v === null || v === undefined || v === "") return "сумма не указана";
    var n = Number(v);
    if (!isFinite(n)) return String(v) + (cur ? " " + cur : "");
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(n) + " " + (cur || "?");
  }
  function daysLeft(d) {
    if (!d) return null;
    var t = Date.parse(d);
    if (isNaN(t)) return null;
    return Math.ceil((t - Date.now()) / DAY);
  }
  function dateRu(d) {
    var t = Date.parse(d || "");
    if (isNaN(t)) return "срок не указан";
    var x = new Date(t);
    return ("0" + x.getUTCDate()).slice(-2) + "." + ("0" + (x.getUTCMonth() + 1)).slice(-2) + "." + x.getUTCFullYear();
  }
  function deadlineText(d) {
    var n = daysLeft(d);
    if (n === null) return "срок не указан";
    return dateRu(d) + " · " + (n < 0 ? "прошёл" : n === 0 ? "сегодня" : "через " + n + " дн.");
  }
  function isLicence(it) { return ((it.assessment && it.assessment.open_gates) || []).indexOf("controlled_goods") >= 0; }
  function isHttp(u) { return typeof u === "string" && /^https?:\/\//i.test(u); }

  // ------------------------------------------------------------------ API (same-origin, relative to /tg/)
  function auth() {
    return fetch("api/auth", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: tg.initData }) }).then(function (r) {
      if (!r.ok) throw { status: r.status };
      return r.json();
    }).then(function (j) { csrf = j.csrf; });
  }
  function api(path, opts, retried) {
    opts = opts || {};
    var headers = { "X-TG-CSRF": csrf || "" };
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch("api/" + path, { method: opts.method || "GET", credentials: "same-origin", headers: headers, body: opts.body })
      .then(function (r) {
        if (r.status === 401 && !retried) return auth().then(function () { return api(path, opts, true); });
        if (!r.ok) throw { status: r.status };
        return r.json();
      });
  }

  // ------------------------------------------------------------------ list
  function notice(text, cls) { var l = $("list"); clear(l); l.appendChild(h("div", { class: "empty " + (cls || "") }, text)); }

  function renderKpis() {
    var lic = 0, soon = 0;
    items.forEach(function (it) {
      if (isLicence(it)) lic++;
      var n = daysLeft(it.deadline);
      if (n !== null && n >= 0 && n <= 14) soon++;
    });
    $("k-open").textContent = String(items.length);
    $("k-lic").textContent = String(lic);
    $("k-soon").textContent = String(soon);
  }

  function renderList() {
    var l = $("list");
    clear(l);
    var shown = items.filter(function (it) { return filter === "all" || (filter === "mil") === isLicence(it); });
    if (!shown.length) { l.appendChild(h("div", { class: "empty" }, "Нет открытых тендеров в этой группе.")); return; }
    shown.forEach(function (it) {
      var n = daysLeft(it.deadline);
      l.appendChild(h("button", { class: "card", type: "button", onclick: function () { openDetail(it); } },
        h("div", { class: "card-top" },
          isLicence(it) ? h("span", { class: "badge lic" }, "лицензия") : h("span", { class: "badge civ" }, "гражд."),
          it.demo ? h("span", { class: "badge demo" }, "ДЕМО") : null,
          h("span", { class: "dl" + (n !== null && n <= 14 ? " soon" : "") }, deadlineText(it.deadline))),
        h("div", { class: "card-title" }, it.title || "(без названия)"),
        h("div", { class: "card-meta" }, it.buyer || "заказчик не указан"),
        h("div", { class: "card-val" }, money(it.estimated_value, it.currency))));
    });
  }

  function load() {
    notice("Загрузка тендеров…");
    return api("tenders?country=CZE&category=security_defence&status=open").then(function (j) {
      items = (j.items || []).slice().sort(function (a, b) {
        var x = daysLeft(a.deadline), y = daysLeft(b.deadline);
        return (x === null ? 1e9 : x) - (y === null ? 1e9 : y);
      });
      var d = j.data || {};
      $("mode").textContent = (d.mode === "LIVE" ? "живые данные" : "ДЕМО / не живые данные") + " · " + items.length + " откр. · категория «безопасность и оборона»";
      renderKpis();
      renderList();
    });
  }

  // ------------------------------------------------------------------ detail sheet
  function gateRow(g) {
    return h("li", { class: "gate st-" + (g.status || "").toLowerCase() },
      h("div", { class: "gate-head" }, h("b", null, GATE_RU[g.id] || g.id), h("span", { class: "pill" }, STATUS_RU[g.status] || g.status)),
      g.reason ? h("div", { class: "gate-why" }, g.reason) : null,
      g.evidence_needed ? h("div", { class: "gate-ev" }, "Нужно: " + g.evidence_needed) : null);
  }

  function section(title, body) { return h("div", { class: "sec" }, h("div", { class: "sec-title" }, title), body); }

  function openDetail(it) {
    current = it;
    var s = $("sheet");
    clear(s);
    var a = it.assessment || {};
    var link = isHttp(it.source_url) ? h("button", { class: "link", type: "button", onclick: function () { openLink(it.source_url); } }, "Официальный источник ↗") : null;
    s.appendChild(h("div", { class: "grab" }));
    s.appendChild(h("div", { class: "d-head" },
      h("h2", { id: "d-title" }, it.title || "(без названия)"),
      nativeButtons ? null : h("button", { class: "x", type: "button", "aria-label": "Закрыть", onclick: closeDetail }, "×")));
    s.appendChild(h("div", { class: "d-meta" },
      h("div", null, h("span", { class: "k" }, "Заказчик"), it.buyer || "не указан"),
      h("div", null, h("span", { class: "k" }, "Сумма"), money(it.estimated_value, it.currency)),
      h("div", null, h("span", { class: "k" }, "Срок"), deadlineText(it.deadline)),
      h("div", null, h("span", { class: "k" }, "Номер"), it.official_notice_id || it.key),
      it.demo ? h("div", { class: "warn" }, "ДЕМО / синтетика: не реальный тендер") : null,
      link));
    s.appendChild(section("Соответствие", h("div", { class: "fit" },
      h("div", null, h("b", null, String(a.fit_score !== undefined ? a.fit_score : "—")), h("span", null, "балл")),
      h("div", null, h("b", null, pct(a.confidence)), h("span", null, "уверенность")),
      h("div", null, h("b", null, ru(a.complexity)), h("span", null, "класс")),
      h("div", null, h("b", null, ru(a.tier_if_cleared)), h("span", null, "если снять")))));
    var gatesBox = h("ul", { class: "gates" }, h("li", { class: "muted" }, "загрузка…"));
    var listBox = h("ol", { class: "check" }, h("li", { class: "muted" }, "загрузка…"));
    var resBox = h("div", { id: "assess-res" });
    s.appendChild(section("Проверки", gatesBox));
    s.appendChild(section("Чек-лист (черновик)", listBox));
    if (!nativeButtons) s.appendChild(h("button", { class: "main", type: "button", id: "assess-btn", onclick: assess }, "Оценить (пробно)"));
    s.appendChild(resBox);
    $("sheet-bg").hidden = false;
    s.hidden = false;
    s.scrollTop = 0;
    if (nativeButtons) {
      tg.BackButton.show();
      tg.MainButton.setText("Оценить (пробно)");
      tg.MainButton.show();
    }
    var key = encodeURIComponent(it.key);
    api("tenders/" + key).then(function (j) {
      if (current !== it) return;
      clear(gatesBox);
      ((j.assessment && j.assessment.gates) || []).forEach(function (g) { gatesBox.appendChild(gateRow(g)); });
    }).catch(function () { clear(gatesBox); gatesBox.appendChild(h("li", { class: "muted" }, "не удалось загрузить проверки")); });
    api("tenders/" + key + "/checklist").then(function (j) {
      if (current !== it) return;
      clear(listBox);
      (j.items || []).forEach(function (c) {
        listBox.appendChild(h("li", null, h("span", { class: "pill st-" + (c.status || "").toLowerCase() }, STATUS_RU[c.status] || c.status || ""), " ", c.step,
          c.evidence ? h("div", { class: "gate-ev" }, c.evidence) : null));
      });
    }).catch(function () { clear(listBox); listBox.appendChild(h("li", { class: "muted" }, "не удалось загрузить чек-лист")); });
  }

  function closeDetail() {
    current = null;
    $("sheet").hidden = true;
    $("sheet-bg").hidden = true;
    if (nativeButtons) { tg.MainButton.hideProgress(); tg.MainButton.hide(); tg.BackButton.hide(); }
  }

  function assess() {
    var it = current;
    if (!it) return;
    var box = $("assess-res");
    clear(box);
    box.appendChild(h("div", { class: "muted" }, "оценка…"));
    if (nativeButtons) tg.MainButton.showProgress();
    api("assessments", { method: "POST", body: JSON.stringify({ tender_key: it.key, dry_run: true }) }).then(function (a) {
      if (current !== it) return;
      clear(box);
      box.appendChild(h("div", { class: "res" },
        h("div", { class: "res-title" }, "Результат пробной оценки (не сохранён)"),
        h("div", null, "Соответствие правилам: ", h("b", null, ru(a.compliance_status)), " · класс: ", h("b", null, ru(a.complexity))),
        h("div", null, "Уровень, если проверки сняты: ", h("b", null, ru(a.tier_if_cleared))),
        h("div", null, "Балл соответствия: ", h("b", null, String(a.fit && a.fit.fit_score)), " · уверенность ", pct(a.fit && a.fit.confidence)),
        a.fit && a.fit.unknowns && a.fit.unknowns.length ? h("div", { class: "gate-ev" }, "Неизвестно: " + a.fit.unknowns.join(", ")) : null,
        h("div", { class: "gate-ev" }, "Это только расчёт. Никаких действий с заказчиком не выполнено.")));
      if (tg && tg.HapticFeedback) try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* optional */ }
    }).catch(function (e) {
      clear(box);
      box.appendChild(h("div", { class: "warn" }, "Оценка не выполнена (" + (e && e.status || "сеть") + ")"));
    }).then(function () { if (nativeButtons) tg.MainButton.hideProgress(); });
  }

  function openLink(url) {
    if (!isHttp(url)) return;
    if (tg && tg.openLink) tg.openLink(url); else window.open(url, "_blank", "noopener");
  }

  // ------------------------------------------------------------------ boot
  document.querySelectorAll(".seg button").forEach(function (b) {
    b.addEventListener("click", function () {
      filter = b.getAttribute("data-f");
      document.querySelectorAll(".seg button").forEach(function (x) { x.setAttribute("aria-selected", x === b ? "true" : "false"); });
      if (tg && tg.HapticFeedback) try { tg.HapticFeedback.selectionChanged(); } catch (e) { /* optional */ }
      renderList();
    });
  });
  $("sheet-bg").addEventListener("click", closeDetail);

  applyTheme();
  if (!inTelegram) {
    $("mode").textContent = "нет данных Telegram";
    notice("Откройте через кнопку «Оборонка» в пульте Telegram. Вне Telegram данные не показываются.", "deny");
    return;
  }
  tg.ready();
  tg.expand();
  if (tg.onEvent) tg.onEvent("themeChanged", applyTheme);
  if (nativeButtons) {
    tg.MainButton.onClick(assess);
    tg.BackButton.onClick(closeDetail);
  }
  auth().then(load).catch(function (e) {
    var st = e && e.status;
    $("mode").textContent = "нет доступа";
    notice(st === 403 ? "Доступ только владельцу пульта." : st === 503 ? "Пульт на этом ПК не настроен." : "Ошибка загрузки (" + (st || "сеть") + ").", "deny");
  });
})();
