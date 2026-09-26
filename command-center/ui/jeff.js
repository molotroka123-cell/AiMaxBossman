import { api, EventStream, hasSession, clearCsrf } from './api.js';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const PREFS_KEY = 'jeff.ux.prefs.v1';
const CHAT_KEY = 'jeff.ux.preview-chat.v1';
const MAX_LOCAL_MESSAGES = 30;
const defaults = { avatar: 'aurora', voice: '', rate: 1, pitch: 1 };

let prefs = loadJson(PREFS_KEY, defaults);
let previewMessages = loadJson(CHAT_KEY, []);
let voices = [];
let bus = null;
let refreshTimer = null;

function clone(v) { return JSON.parse(JSON.stringify(v)); }
function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' ? parsed : clone(fallback);
  } catch { return clone(fallback); }
}
function saveJson(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} }
function text(value) { return String(value ?? ''); }
function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function clamp(n, min, max) { return Math.min(max, Math.max(min, Number(n) || 0)); }

function setStatus(label, tone='ok') {
  const el = $('#connection-status');
  if (!el) return;
  el.dataset.tone = tone;
  el.querySelector('span:last-child').textContent = label;
}

async function login(token) {
  const error = $('#login-error');
  error.hidden = true;
  try {
    await api.login(token);
    $('#login').hidden = true;
    $('#app').hidden = false;
    await boot();
  } catch (err) {
    error.textContent = err?.message || 'Не удалось войти';
    error.hidden = false;
  }
}

async function boot() {
  applyAvatar();
  bindUi();
  hydrateVoiceList();
  renderPreviewChat();
  await refreshReadOnlyState();
  if (!bus) {
    bus = new EventStream();
    bus.subscribe((ev) => {
      if (ev.kind === 'ws.open') setStatus('На связи', 'ok');
      else if (ev.kind === 'ws.closed') setStatus('Переподключение…', 'warn');
      else if (ev.kind === 'ws.connecting') setStatus('Подключение…', 'warn');
      if (/^(task|run|resource|provider|model|worker)\./.test(ev.kind || '')) scheduleRefresh();
    });
    bus.start();
  }
  clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshReadOnlyState, 15000);
}

let bound = false;
function bindUi() {
  if (bound) return;
  bound = true;

  $('#login-form')?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    login($('#login-token').value.trim());
  });
  $('#logout')?.addEventListener('click', async () => {
    try { await api.logout(); } catch { clearCsrf(); }
    location.reload();
  });

  // Intentionally local-only until a participant-safe browser transport is
  // reviewed after the 1.5–1.7 freeze. No owner task is created here.
  $('#composer')?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const input = $('#message');
    const value = input.value.trim();
    if (!value) return;
    previewMessages.push({ role: 'user', text: value, at: Date.now() });
    previewMessages.push({
      role: 'assistant',
      text: 'Веб-чат пока работает как UX-preview. Живой Jeff остаётся в Telegram; participant-safe transport подключаем только после freeze 1.5–1.7.',
      at: Date.now(), preview: true,
    });
    previewMessages = previewMessages.slice(-MAX_LOCAL_MESSAGES);
    saveJson(CHAT_KEY, previewMessages);
    input.value = '';
    renderPreviewChat();
  });

  $('#clear-preview')?.addEventListener('click', () => {
    previewMessages = [];
    saveJson(CHAT_KEY, previewMessages);
    renderPreviewChat();
  });
  $('#speak-last')?.addEventListener('click', speakLast);
  $('#mic')?.addEventListener('click', startDictation);
  $('#avatar-open')?.addEventListener('click', () => $('#avatar-dialog').showModal());
  $('#avatar-open-secondary')?.addEventListener('click', () => $('#avatar-dialog').showModal());
  $('#avatar-close')?.addEventListener('click', () => $('#avatar-dialog').close());

  $$('.avatar-choice').forEach((btn) => btn.addEventListener('click', () => {
    prefs.avatar = btn.dataset.avatar || 'aurora';
    saveJson(PREFS_KEY, prefs);
    applyAvatar();
    $('#avatar-dialog').close();
  }));

  $('#voice-select')?.addEventListener('change', (ev) => {
    prefs.voice = ev.target.value;
    saveJson(PREFS_KEY, prefs);
  });
  $('#voice-rate')?.addEventListener('input', (ev) => {
    prefs.rate = clamp(ev.target.value, 0.7, 1.4);
    saveJson(PREFS_KEY, prefs);
  });
  $('#voice-pitch')?.addEventListener('input', (ev) => {
    prefs.pitch = clamp(ev.target.value, 0.7, 1.3);
    saveJson(PREFS_KEY, prefs);
  });
  $('#voice-preview')?.addEventListener('click', () => speak('Привет. Я Jeff. Голос работает локально через системный синтез речи.'));
  $('#open-command-center')?.addEventListener('click', () => { location.href = '/'; });

  $$('[data-scroll]').forEach((btn) => btn.addEventListener('click', () => {
    document.querySelector(btn.dataset.scroll)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
  $$('.capability:not(.locked)').forEach((btn) => btn.addEventListener('click', () => {
    $('#message').value = btn.dataset.prompt || '';
    $('#message').focus();
  }));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshReadOnlyState();
  });
}

function applyAvatar() {
  document.documentElement.dataset.jeffAvatar = prefs.avatar || 'aurora';
  $$('.avatar-choice').forEach((btn) =>
    btn.setAttribute('aria-pressed', String(btn.dataset.avatar === prefs.avatar)));
  const label = $('#avatar-label');
  if (label) label.textContent =
    ({aurora:'Aurora', ember:'Ember', mono:'Mono', cobalt:'Cobalt'})[prefs.avatar] || 'Aurora';
}

function renderPreviewChat() {
  const root = $('#chat');
  if (!root) return;
  const intro = `
    <article class="bubble assistant hero-bubble">
      <div class="bubble-meta">Jeff · participant assistant</div>
      <strong>Привет 👋</strong>
      <p>Это новый UX Jeff. Он изолирован от управляющих функций Bossman: без shell, Computer Use и owner authority.</p>
    </article>`;
  const rows = previewMessages.map((m) => `
    <article class="bubble ${m.role === 'user' ? 'user' : 'assistant'} ${m.preview ? 'preview' : ''}">
      <div class="bubble-meta">${m.role === 'user' ? 'Вы' : 'Jeff'}${m.preview ? ' · UX preview' : ''}</div>
      <p>${escapeHtml(m.text)}</p>
    </article>`).join('');
  root.innerHTML = intro + rows;
  root.scrollTop = root.scrollHeight;
}

function scheduleRefresh() {
  clearTimeout(scheduleRefresh.timer);
  scheduleRefresh.timer = setTimeout(refreshReadOnlyState, 350);
}

async function refreshReadOnlyState() {
  try {
    const [system, identity] = await Promise.all([api.system(), api.identity()]);
    setStatus('На связи', 'ok');
    const metrics = system?.metrics || {};
    setMeter('cpu', metrics.cpu_pct);
    const total = Number(metrics.ram_total_mb || 0);
    const used = Number(metrics.ram_used_mb || 0);
    setMeter('ram', total > 0 ? used / total * 100 : null);
    $('#build').textContent = identity?.build_sha ? identity.build_sha.slice(0, 8) : 'unknown';
    $('#route-label').textContent = 'Local-first / free fallback';
    $('#backend-health').textContent =
      system?.health && Object.values(system.health).some((x) => x?.status === 'error')
        ? 'Есть предупреждения' : 'Готов';
  } catch (err) {
    setStatus('Нет связи', 'bad');
    $('#backend-health').textContent = err?.message || 'Недоступен';
  }
}

function setMeter(name, value) {
  const bar = $(`[data-meter="${name}"] .meter-fill`);
  const label = $(`[data-meter="${name}"] .meter-value`);
  if (!bar || !label) return;
  if (!Number.isFinite(Number(value))) {
    bar.style.width = '0%'; label.textContent = '—'; return;
  }
  const pct = clamp(value, 0, 100);
  bar.style.width = `${pct}%`;
  label.textContent = `${Math.round(pct)}%`;
}

function hydrateVoiceList() {
  if (!('speechSynthesis' in window)) {
    $('#voice-select').innerHTML = '<option>Системный TTS недоступен</option>';
    $('#voice-preview').disabled = true;
    return;
  }
  const load = () => {
    voices = speechSynthesis.getVoices();
    const select = $('#voice-select');
    if (!select) return;
    select.innerHTML = voices.length
      ? voices.map((v) => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)} · ${escapeHtml(v.lang)}</option>`).join('')
      : '<option value="">Системный голос</option>';
    if (prefs.voice && voices.some((v) => v.name === prefs.voice)) select.value = prefs.voice;
    $('#voice-rate').value = prefs.rate ?? 1;
    $('#voice-pitch').value = prefs.pitch ?? 1;
  };
  load();
  speechSynthesis.onvoiceschanged = load;
}

function speak(value) {
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text(value));
  const voice = voices.find((v) => v.name === prefs.voice);
  if (voice) u.voice = voice;
  u.rate = clamp(prefs.rate || 1, 0.7, 1.4);
  u.pitch = clamp(prefs.pitch || 1, 0.7, 1.3);
  speechSynthesis.speak(u);
}

function speakLast() {
  const last = [...previewMessages].reverse().find((m) => m.role === 'assistant');
  speak(last?.text || 'Привет. Я Jeff.');
}

function startDictation() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    $('#voice-note').textContent = 'Распознавание речи недоступно в этом браузере. TTS при этом может работать.';
    return;
  }
  const r = new Recognition();
  r.lang = navigator.language || 'ru-RU';
  r.interimResults = false;
  r.maxAlternatives = 1;
  $('#voice-note').textContent = 'Слушаю…';
  r.onresult = (ev) => { $('#message').value = ev.results?.[0]?.[0]?.transcript || ''; };
  r.onerror = () => { $('#voice-note').textContent = 'Не удалось распознать речь.'; };
  r.onend = () => {
    if ($('#voice-note').textContent === 'Слушаю…')
      $('#voice-note').textContent = 'Готово. Проверьте текст перед отправкой.';
  };
  r.start();
}

window.addEventListener('beforeunload', () => {
  if (bus) bus.stop();
  clearInterval(refreshTimer);
});

if (hasSession()) {
  $('#login').hidden = true;
  $('#app').hidden = false;
  boot();
} else {
  $('#login').hidden = false;
  $('#app').hidden = true;
}