import { bearer, formatTime, SSEParser, can } from "./remote-core.mjs";

const state = {
  token: sessionStorage.getItem("bossman_session") || "",
  who: null,
  scopes: new Set(),
  streamAbort: null,
};

const $ = (q) => document.querySelector(q);
const escapeHTML = (s) => String(s ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}), ...bearer(state.token) };
  const res = await fetch(path, { ...options, headers, credentials: "omit", cache: "no-store" });
  let body = null;
  const text = await res.text();
  if (text) { try { body = JSON.parse(text); } catch (_) { body = { detail: text }; } }
  if (!res.ok) {
    const msg = body?.error?.message || body?.detail?.message || body?.detail || `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

function flash(message, bad = false) {
  const el = $("#flash");
  el.textContent = message;
  el.className = bad ? "flash bad" : "flash";
  clearTimeout(flash.t);
  flash.t = setTimeout(() => { el.textContent = ""; el.className = "flash"; }, 5000);
}

async function login() {
  const deviceToken = $("#device-token").value.trim();
  if (!deviceToken) return flash("Вставьте device token", true);
  const res = await fetch("/remote/auth", { method: "POST", headers: bearer(deviceToken), credentials: "omit", cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  // Device token is deliberately never persisted.
  $("#device-token").value = "";
  if (!res.ok || !body.session_token) return flash("Не удалось открыть сессию", true);
  state.token = body.session_token;
  sessionStorage.setItem("bossman_session", state.token);
  await bootAuthenticated();
}

async function logout() {
  try { if (state.token) await api("/remote/session/logout", { method: "POST" }); } catch (_) {}
  state.streamAbort?.abort();
  sessionStorage.removeItem("bossman_session");
  state.token = ""; state.who = null; state.scopes = new Set();
  $("#app").hidden = true; $("#login").hidden = false;
}

async function bootAuthenticated() {
  try {
    state.who = await api("/remote/whoami");
    state.scopes = new Set(state.who.scopes || []);
    $("#identity").textContent = `${state.who.name} · ${state.who.device_id}`;
    $("#login").hidden = true; $("#app").hidden = false;
    $("#approvals-panel").hidden = !can(state.scopes, "approve");
    $("#admin-panel").hidden = !can(state.scopes, "admin");
    await Promise.all([loadTasks(), loadAgents(), can(state.scopes, "approve") ? loadApprovals() : null]);
    startEvents();
  } catch (e) {
    await logout();
    flash(`Сессия недействительна: ${e.message}`, true);
  }
}

async function loadTasks() {
  const rows = await api("/remote/tasks?limit=50");
  $("#tasks").innerHTML = rows.length ? rows.map(t => `
    <article class="card">
      <div class="row"><strong>#${t.id} ${escapeHTML(t.agent || "auto")}</strong><span class="pill">${escapeHTML(t.status)}</span></div>
      <p>${escapeHTML(t.text)}</p>
      ${t.result ? `<pre>${escapeHTML(t.result)}</pre>` : ""}
      ${t.error ? `<pre class="error">${escapeHTML(t.error)}</pre>` : ""}
      <small>${escapeHTML(formatTime(t.created_at))}</small>
    </article>`).join("") : "<p class='muted'>Задач пока нет.</p>";
}

async function loadAgents() {
  const agents = await api("/remote/agents");
  $("#agent").innerHTML = `<option value="">Auto</option>` + agents.map(a => `<option value="${escapeHTML(a.name)}">${escapeHTML(a.title || a.name)} · ${escapeHTML(a.model)}</option>`).join("");
}

async function createTask() {
  const text = $("#task-text").value.trim();
  if (!text) return;
  const agent = $("#agent").value || null;
  await api("/remote/tasks", { method: "POST", body: JSON.stringify({ text, agent }) });
  $("#task-text").value = "";
  flash("Задача отправлена");
  await loadTasks();
}

async function loadApprovals() {
  const rows = await api("/remote/approvals?status=pending&limit=50");
  $("#approvals").innerHTML = rows.length ? rows.map(a => `
    <article class="card approval">
      <strong>Approval #${a.id}</strong>
      <pre>${escapeHTML(a.preview || a.kind || "Sensitive action")}</pre>
      <div class="row actions"><button data-id="${a.id}" data-decision="1">Разрешить</button><button class="danger" data-id="${a.id}" data-decision="0">Отклонить</button></div>
    </article>`).join("") : "<p class='muted'>Ожидающих подтверждений нет.</p>";
  $("#approvals").querySelectorAll("button[data-id]").forEach(btn => btn.addEventListener("click", async () => {
    const approve = btn.dataset.decision === "1";
    if (approve && !confirm("Подтвердить это чувствительное действие?")) return;
    await api(`/remote/approvals/${btn.dataset.id}`, { method: "POST", body: JSON.stringify({ approve }) });
    await loadApprovals();
  }));
}

async function emergencyLock() {
  const phrase = prompt('Аварийно заблокировать ВСЕ удалённые устройства? Введите LOCK ALL');
  if (phrase !== "LOCK ALL") return;
  await api("/remote/lock", { method: "POST", body: JSON.stringify({ locked: true, device_id: null }) });
  sessionStorage.removeItem("bossman_session");
  location.reload();
}

async function startEvents() {
  state.streamAbort?.abort();
  if (!can(state.scopes, "events")) return;
  const controller = new AbortController(); state.streamAbort = controller;
  try {
    const res = await fetch("/remote/events", { headers: bearer(state.token), credentials: "omit", cache: "no-store", signal: controller.signal });
    if (!res.ok || !res.body) throw new Error(`events HTTP ${res.status}`);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    const parser = new SSEParser(({ data }) => {
      const el = $("#live-event");
      el.textContent = typeof data === "string" ? data : JSON.stringify(data);
      if (data?.kind?.startsWith?.("task.") || data?.type?.startsWith?.("task.")) loadTasks().catch(() => {});
      if (can(state.scopes, "approve")) loadApprovals().catch(() => {});
    });
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(value);
    }
  } catch (e) {
    if (!controller.signal.aborted) setTimeout(() => startEvents(), 2500);
  }
}

$("#login-btn").addEventListener("click", () => login().catch(e => flash(e.message, true)));
$("#logout-btn").addEventListener("click", () => logout());
$("#send-task").addEventListener("click", () => createTask().catch(e => flash(e.message, true)));
$("#refresh").addEventListener("click", () => Promise.all([loadTasks(), can(state.scopes, "approve") ? loadApprovals() : null]).catch(e => flash(e.message, true)));
$("#lock-all").addEventListener("click", () => emergencyLock().catch(e => flash(e.message, true)));
$("#task-text").addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") createTask().catch(err => flash(err.message, true)); });

document.addEventListener("visibilitychange", () => { if (!document.hidden && state.token) bootAuthenticated().catch(() => {}); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/remote/app/sw.js", { scope: "/remote/app/" }).catch(() => {});
if (state.token) bootAuthenticated();
