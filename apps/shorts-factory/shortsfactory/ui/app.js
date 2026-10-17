"use strict";
// Shorts Factory UI. Media is fetched with the Authorization header and shown as blob: URLs (no tokens in URLs).
const API = "/api/v1";
let TOKEN = "";
try { TOKEN = sessionStorage.getItem("sf_token") || ""; } catch (e) { /* storage blocked */ }
let ROLE = null;

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.style.display = "block"; clearTimeout(toast.h); toast.h = setTimeout(() => (t.style.display = "none"), 4000); }

async function api(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { "Content-Type": "application/json", Authorization: "Bearer " + TOKEN, ...(opts.headers || {}) } });
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.blob();
  if (!r.ok) { const d = body && body.detail; throw new Error((d && (d.message || JSON.stringify(d))) || ("ошибка HTTP " + r.status)); }
  return body;
}

async function whoami() {
  const caps = ["owner", "editor", "viewer"];
  try {
    await api("/audit/verify"); ROLE = "owner";
  } catch (e) {
    try { await api("/sources"); ROLE = "editor?"; } catch (e2) { ROLE = null; }
  }
  $("#role").textContent = ROLE ? (ROLE === "owner" ? "владелец" : "просмотр/редактор") : "нет доступа";
  $("#role").className = "badge " + (ROLE === "owner" ? "ok" : ROLE ? "warn" : "bad");
  return caps;
}

const STATE_RU = { draft: "черновик", ready: "ждёт «да»", approved_by_owner: "одобрено", scheduled: "запланировано", published: "опубликовано", rejected: "отклонено" };
const PRIVACY_RU = { private: "приватно", unlisted: "по ссылке", public: "публично" };
const KIND_RU = { own: "моя запись", generated: "сгенерировано на ПК", licensed: "по лицензии" };
const TSTATUS_RU = { OK: "готово", NOT_RUN: "ещё не загружались", NOT_CONNECTED: "не подключено", ERROR: "ошибка", DRY_RUN: "пробный запуск" };
const STATE_CLS = { ready: "warn", approved_by_owner: "ok", scheduled: "ok", published: "ok", rejected: "bad", draft: "" };

async function blobUrl(path) { const b = await api(path); return URL.createObjectURL(b); }

function itemCard(it) {
  const m = it.metadata || {}, v = (it.validation || {}).ffprobe || {};
  const el = document.createElement("div");
  el.className = "card";
  el.innerHTML = `
    <div class="vid"><button class="play">▶ смотреть</button></div>
    <h3>${esc(m.title)}</h3>
    <div class="row">
      <span class="badge ${STATE_CLS[it.state] || ""}">${esc(STATE_RU[it.state] || it.state)}</span>
      <span class="badge">${esc(v.width)}×${esc(v.height)} · ${esc(v.duration)} с</span>
      ${m.contains_synthetic_media ? '<span class="badge warn">ИИ-контент: раскрытие</span>' : ""}
      ${it.blocked_consent ? '<span class="badge bad">заблокировано: нет согласия людей в кадре</span>' : ""}
      ${it.captions && !it.captions.burned ? '<span class="badge">без второго слоя субтитров</span>' : ""}
      <span class="badge">${esc(PRIVACY_RU[it.privacy || "private"] || it.privacy)}</span>
      ${(it.validation || {}).ok ? '<span class="badge ok">проверка ffprobe пройдена</span>' : '<span class="badge bad">проверка не пройдена</span>'}
    </div>
    <pre>${esc(m.description)}</pre>
    <div class="muted">${esc((it.highlight && (it.highlight.why || []).join(" · ")) || (it.story && ("Сюжет: " + it.story.lead)) || "")}</div>
    <div class="muted">${esc(it.framing && it.framing.note || "")}</div>
    <div class="row act"></div>`;
  $(".play", el).onclick = async (ev) => {
    ev.target.disabled = true;
    try {
      const url = await blobUrl(`/queue/${it.id}/video`);
      $(".vid", el).innerHTML = `<video controls playsinline src="${url}"></video>`;
    } catch (e) { toast("видео: " + e.message); ev.target.disabled = false; }
  };
  const act = $(".act", el);
  const btn = (label, cls, fn) => { const b = document.createElement("button"); b.textContent = label; b.className = cls; b.onclick = fn; act.appendChild(b); };
  if (ROLE === "owner") {
    if (it.state === "ready" && !it.blocked_consent) {
      btn("Да, одобрить", "pri", async () => { try { await api(`/queue/${it.id}/approve`, { method: "POST", body: JSON.stringify({ note: "да из UI", privacy: "private" }) }); toast("одобрено (приватно)"); loadQueue(); } catch (e) { toast(e.message); } });
    }
    if (it.state === "approved_by_owner" && !it.blocked_consent) {
      btn("Опубликовать в слот", "pri", async () => {
        if (!confirm("Загрузить на YouTube приватно с публикацией в ближайший слот?")) return;
        try { const r = await api(`/queue/${it.id}/publish`, { method: "POST", body: JSON.stringify({ use_slot: true }) }); toast("загружено: " + (r.youtube && r.youtube.url || "")); loadQueue(); } catch (e) { toast(e.message); }
      });
    }
    if (!["published", "rejected"].includes(it.state)) {
      btn("Отклонить", "bad", async () => { try { await api(`/queue/${it.id}/reject`, { method: "POST", body: JSON.stringify({ note: "нет" }) }); loadQueue(); } catch (e) { toast(e.message); } });
    }
  }
  return el;
}

async function loadQueue() {
  const st = $("#qstate").value;
  const box = $("#qlist");
  box.innerHTML = "<p class='muted'>загрузка…</p>";
  try {
    const r = await api("/queue" + (st ? "?state=" + st : ""));
    box.innerHTML = r.items.length ? "" : "<p class='muted'>пусто</p>";
    r.items.forEach((it) => box.appendChild(itemCard(it)));
  } catch (e) { box.innerHTML = `<p class='muted'>${esc(e.message)}</p>`; }
}

async function loadSources() {
  const box = $("#slist");
  try {
    const r = await api("/sources");
    box.innerHTML = r.items.map((s) => `
      <div class="card">
        <b>${esc(s.title)}</b> <span class="badge">${esc(KIND_RU[s.kind] || s.kind)}</span>
        ${s.approved_by_owner ? '<span class="badge ok">права подтверждены</span>' : '<span class="badge bad">права НЕ подтверждены</span>'}
        ${s.ai_generated ? '<span class="badge warn">ИИ</span>' : ""}
        ${s.people_in_frame ? (s.consent_recorded ? '<span class="badge ok">люди в кадре: согласие есть</span>' : '<span class="badge bad">люди в кадре: согласия НЕТ</span>') : ""}
        <div class="muted">${esc(s.rights_note)}${s.licence_url ? " · " + esc(s.licence_url) : ""}</div>
        <div class="muted">${esc(s.path ? s.path.split(/[\\/]/).pop() : "без файла")} · ${esc(s.id)}</div>
        ${s.rights_approval ? `<div class="muted">одобрил: ${esc(s.rights_approval.by)} ${esc(s.rights_approval.at)} — ${esc(s.rights_approval.note)}</div>` : ""}
        ${ROLE === "owner" ? `<div class="row"><button data-id="${esc(s.id)}" data-a="${s.approved_by_owner ? 0 : 1}" class="${s.approved_by_owner ? "bad" : "pri"} rights">${s.approved_by_owner ? "Отозвать права" : "Подтвердить права"}</button>
          ${s.people_in_frame ? `<button data-id="${esc(s.id)}" data-a="${s.consent_recorded ? 0 : 1}" class="${s.consent_recorded ? "bad" : "pri"} consent">${s.consent_recorded ? "Отозвать согласие" : "Согласие людей в кадре"}</button>` : ""}</div>` : ""}
      </div>`).join("") || "<p class='muted'>источников нет</p>";
    box.querySelectorAll("button.rights").forEach((b) => (b.onclick = async () => {
      const approve = b.dataset.a === "1";
      const confirmWord = approve ? prompt("Подтверждаю, что имею права на этот источник. Впишите ДА") : "";
      if (approve && !confirmWord) return;
      const note = prompt("Основание (коротко)", approve ? "моя запись" : "отзыв") || "";
      try { await api(`/sources/${b.dataset.id}/rights`, { method: "POST", body: JSON.stringify({ approve, confirm: confirmWord || "", note }) }); loadSources(); } catch (e) { toast(e.message); }
    }));
    box.querySelectorAll("button.consent").forEach((b) => (b.onclick = async () => {
      const consent = b.dataset.a === "1";
      const persons = consent ? (prompt("Кто в кадре (через запятую)?") || "").split(",").map((x) => x.trim()).filter(Boolean) : [];
      if (consent && !persons.length) return;
      const confirmWord = consent ? prompt("Эти люди согласны на публикацию их лица/голоса. Впишите ДА") : "";
      if (consent && !confirmWord) return;
      const note = prompt("Основание (коротко)", consent ? "согласие получено" : "отзыв") || "";
      try { await api(`/sources/${b.dataset.id}/consent`, { method: "POST", body: JSON.stringify({ consent, confirm: confirmWord || "", persons, note }) }); loadSources(); } catch (e) { toast(e.message); }
    }));
  } catch (e) { box.innerHTML = `<p class='muted'>${esc(e.message)}</p>`; }
}

$("#sform").onsubmit = async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const body = { title: f.get("title"), kind: f.get("kind"), path: f.get("path") || null, rights_note: f.get("rights_note"),
    licence_url: f.get("licence_url") || null, ai_generated: !!f.get("ai_generated"),
    people_in_frame: !!f.get("people_in_frame"), has_burned_captions: !!f.get("has_burned_captions") };
  try { await api("/sources", { method: "POST", body: JSON.stringify(body) }); ev.target.reset(); loadSources(); } catch (e) { toast(e.message); }
};

async function loadTrends(fetchNew) {
  const box = $("#tbox");
  try {
    const r = fetchNew ? await api("/trends/fetch", { method: "POST", body: JSON.stringify({ region: "CZ" }) }) : await api("/trends");
    if (r.status !== "OK") { box.innerHTML = `<div class="card"><b>Тренды: ${esc(TSTATUS_RU[r.status] || r.status)}</b><div class="muted">${esc(r.message || r.reason || (r.steps || []).join(" "))}</div></div>`; return; }
    const a = r.analysis || {};
    box.innerHTML = `<div class="card"><b>Идеи (ИИ, не прогноз)</b><div class="muted">${esc((a.topics || []).join(" · "))}</div>
      <div class="muted">${esc((a.hooks || []).join(" · "))}</div><div class="muted">${esc(a.notes || "")}</div></div>
      <div class="card"><table><tr><th>Видео</th><th>Канал</th><th>Просмотры</th><th>Длительность, с</th></tr>
      ${(r.items || []).slice(0, 30).map((i) => `<tr><td>${esc(i.title)}</td><td>${esc(i.channel)}</td><td>${esc(i.views)}</td><td>${esc(i.duration_s)}</td></tr>`).join("")}</table></div>`;
  } catch (e) { box.innerHTML = `<p class='muted'>${esc(e.message)}</p>`; }
}

function meter(label, val, target, pct, note) {
  const has = typeof val === "number";
  return `<div><b>${esc(label)}</b>: ${has ? esc(val.toLocaleString("ru-RU")) : esc(val)} / ${esc(target.toLocaleString("ru-RU"))}
    <div class="meter"><i style="width:${has ? Math.min(100, pct || 0) : 0}%"></i></div>${note ? `<div class="muted">${esc(note)}</div>` : ""}</div>`;
}

async function loadChannel() {
  const box = $("#cbox");
  try {
    const r = await api("/channel");
    const g = r.goal, st = r.status || {};
    box.innerHTML = `<div class="card"><b>Канал</b>: ${st.connected ? esc((st.channel || {}).title || "подключён") : "не подключён"}
      <div class="muted">${esc(st.connected ? "" : st.reason || "")}</div></div>
      <div class="card"><b>Цель монетизации (YPP)</b><p class="muted">${esc(g.disclaimer)}</p>
      ${meter("Подписчики", g.subscribers, g.targets.subscribers, g.subscribers_pct)}
      ${meter("Просмотры Shorts за 90 дней", g.shorts_views_90d, g.targets.shorts_views_90d, g.shorts_views_pct, g.shorts_views_90d_note)}
      <div class="muted">или ${esc(g.targets.watch_hours_365d)} часов просмотра длинных видео за 12 мес.: ${esc(g.watch_hours_365d)}</div></div>`;
  } catch (e) { box.innerHTML = `<p class='muted'>${esc(e.message)}</p>`; }
}

document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === b.dataset.tab));
  ({ queue: loadQueue, sources: loadSources, trends: () => loadTrends(false), channel: loadChannel })[b.dataset.tab]();
}));
$("#qreload").onclick = loadQueue;
$("#qstate").onchange = loadQueue;
$("#tfetch").onclick = () => loadTrends(true);
$("#tokform").onsubmit = async (ev) => { ev.preventDefault(); TOKEN = $("#tok").value.trim(); try { sessionStorage.setItem("sf_token", TOKEN); } catch (e) { /* ignore */ } await whoami(); loadQueue(); };

(async () => {
  try { const h = await (await fetch(API + "/health")).json(); $("#ver").textContent = "v" + h.version; } catch (e) { /* offline */ }
  if (TOKEN) { $("#tok").value = TOKEN; await whoami(); loadQueue(); }
})();
