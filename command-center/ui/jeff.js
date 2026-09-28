// Jeff window: participant chat over the loopback Jeff web transport (bcc.pit.web).
// Talks only to /api/jeff/*. No owner Command Center session, token or endpoint.
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const PREFS_KEY = 'jeff.ux.prefs.v1';
const defaults = { avatar: 'aurora', voice: '', rate: 1, pitch: 1, speak: false };

let prefs = loadPrefs();
let voices = [];
let busy = false;
let chatAbort = null;
let currentAudio = null;
let recorder = null;
let recordChunks = [];
let recordTimer = null;
let pendingTranscript = '';
let online = null;
let voiceCaps = { asr: false, tts: false };

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Object.assign({}, defaults, parsed && typeof parsed === 'object' ? parsed : {});
  } catch { return Object.assign({}, defaults); }
}
function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch {} }
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function formatReply(text) {
  // Escape first; then only **bold** and line breaks. Never raw model HTML.
  return escapeHtml(text).replace(/\*\*([^*]{1,200})\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>');
}
function clamp(n, min, max) { return Math.min(max, Math.max(min, Number(n) || 0)); }

class JeffError extends Error {
  constructor(status, code) { super(code || `HTTP ${status}`); this.status = status; this.code = code; }
}

async function call(path, { method = 'GET', json, raw, headers = {}, signal } = {}) {
  const init = { method, credentials: 'same-origin', headers: Object.assign({}, headers), signal };
  if (method !== 'GET') init.headers['X-Jeff-Request'] = '1';
  if (json !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
  if (raw !== undefined) init.body = raw;
  let res;
  try { res = await fetch(path, init); }
  catch (err) {
    if (err?.name === 'AbortError') throw err;
    setOnline(false);
    throw new JeffError(0, 'NETWORK_DOWN');
  }
  setOnline(true);
  const type = res.headers.get('content-type') || '';
  if (!res.ok) {
    let code = '';
    if (type.includes('json')) { try { code = (await res.json()).error || ''; } catch {} }
    throw new JeffError(res.status, code);
  }
  if (type.includes('json')) return res.json();
  return res.blob();
}

function setStatus(label, tone = 'ok') {
  const el = $('#connection-status');
  if (!el) return;
  el.dataset.tone = tone;
  el.querySelector('span:last-child').textContent = label;
}
function showError(text) {
  const el = $('#error-banner');
  if (!text) { el.hidden = true; el.textContent = ''; return; }
  el.textContent = text; el.hidden = false;
}
function setOnline(value) {
  if (online === value) return;
  const wasOffline = online === false;
  online = value;
  if (value) {
    setStatus('На связи', 'ok');
    if (wasOffline) { showError(''); reloadAfterReconnect(); }
  } else {
    setStatus('Нет связи', 'bad');
    showError('Нет связи с Jeff. Переподключаюсь автоматически…');
  }
}

// -- login -----------------------------------------------------------------------------
let signupMode = false;
function showLogin(signupOpen) {
  signupMode = !!signupOpen;
  $('#login').hidden = false; $('#app').hidden = true;
  $('#login-submit').textContent = signupMode ? 'Создать учётную запись' : 'Войти';
  $('#login-hint').textContent = signupMode
    ? 'Первый запуск: придумайте имя и пароль (от 8 символов). Это вход участника Jeff, не токен владельца Bossman.'
    : 'Войдите в свою учётную запись Jeff. Это отдельный вход участника — не токен владельца Bossman.';
  $('#login-password').autocomplete = signupMode ? 'new-password' : 'current-password';
}

async function submitLogin(ev) {
  ev.preventDefault();
  const error = $('#login-error');
  error.hidden = true;
  const body = { username: $('#login-username').value.trim(), password: $('#login-password').value };
  try {
    const res = await call(signupMode ? '/api/jeff/signup' : '/api/jeff/login', { method: 'POST', json: body });
    $('#login-password').value = '';
    await showApp(res.greeting || '');
  } catch (err) {
    const map = { LOGIN_FAILED: 'Неверное имя или пароль.', TOO_MANY_ATTEMPTS: 'Слишком много попыток. Подождите минуту.',
      PASSWORD_TOO_SHORT: 'Пароль — от 8 символов.', USERNAME_INVALID: 'Имя: 2–32 буквы, цифры, точка, дефис.',
      SIGNUP_CLOSED_USE_CLI: 'Учётная запись уже создана — войдите.', NETWORK_DOWN: 'Jeff не отвечает. Запущен ли он?' };
    error.textContent = map[err.code] || 'Не удалось войти.';
    error.hidden = false;
  }
}

// -- chat rendering ------------------------------------------------------------------------
function bubble(role, text, { disclosure = [], attachments = [], pending = false, stopped = false } = {}) {
  const el = document.createElement('article');
  el.className = `bubble ${role === 'user' ? 'user' : 'assistant'}${pending ? ' pending' : ''}${stopped ? ' stopped' : ''}`;
  const meta = role === 'user' ? 'Вы' : 'Jeff';
  let html = `<div class="bubble-meta">${meta}</div><p class="bubble-text">${pending ? 'Jeff думает…' : formatReply(text)}</p>`;
  for (const item of attachments) {
    if (item.kind === 'image' && /^data:image\/(png|jpeg|webp);base64,/.test(item.data_url || ''))
      html += `<img class="bubble-image" alt="Изображение от Jeff" src="${item.data_url}">`;
    else if (item.kind === 'document' && /^data:application\/json;base64,/.test(item.data_url || ''))
      html += `<a class="bubble-doc" download="${escapeHtml(item.name || 'jeff.json')}" href="${item.data_url}">⬇ ${escapeHtml(item.name || 'файл')}</a>`;
  }
  if (role !== 'user') {
    html += `<details class="disclosure"${pending ? '' : ''}><summary>Как Jeff ответил</summary><ul>${
      (pending ? ['Думаю над ответом…'] : (disclosure.length ? disclosure : ['Ответ из истории разговора']))
        .map((d) => `<li>${escapeHtml(d)}</li>`).join('')}</ul></details>`;
    if (!pending && text) html += `<button class="speak-btn ghost" type="button" title="Озвучить">🔊</button>`;
  }
  el.innerHTML = html;
  el.dataset.text = text || '';
  el.querySelector('.speak-btn')?.addEventListener('click', () => speak(el.dataset.text));
  return el;
}

function appendBubble(el) {
  const root = $('#chat');
  root.appendChild(el);
  root.scrollTop = root.scrollHeight;
  return el;
}

async function loadHistory(greeting) {
  const root = $('#chat');
  root.innerHTML = '';
  const data = await call('/api/jeff/history');
  if (!data.messages.length && greeting) appendBubble(bubble('assistant', greeting, { disclosure: ['Знакомство'] }));
  for (const m of data.messages) appendBubble(bubble(m.role, m.text));
}

function setBusy(value) {
  busy = value;
  $('#stop').disabled = !value && !currentAudio;
  $('#send').disabled = value;
}

async function sendText(text, via = 'text') {
  const value = String(text || '').trim();
  if (!value || busy) return;
  showError('');
  appendBubble(bubble('user', value));
  const pending = appendBubble(bubble('assistant', '', { pending: true }));
  setBusy(true);
  chatAbort = new AbortController();
  try {
    const res = await call('/api/jeff/chat', { method: 'POST', json: { text: value, via }, signal: chatAbort.signal });
    pending.replaceWith(bubble('assistant', res.reply, { disclosure: res.disclosure, attachments: res.attachments, stopped: res.stopped }));
    if (prefs.speak && res.reply && !res.stopped) speak(res.reply);
  } catch (err) {
    pending.replaceWith(bubble('assistant', err?.name === 'AbortError' ? 'Остановлено. Можно продолжать.'
      : errorText(err), { stopped: true }));
  } finally {
    chatAbort = null;
    setBusy(false);
    refreshMemory();
  }
}

function errorText(err) {
  if (err?.code === 'NETWORK_DOWN') return 'Нет связи с Jeff. Сообщение не отправлено — повторите, когда связь вернётся.';
  if (err?.status === 401) { showLogin(false); return 'Сессия закончилась — войдите снова.'; }
  return 'Не получилось ответить. Повторите чуть позже.';
}

async function stopAll() {
  if (currentAudio) { try { currentAudio.pause(); } catch {} currentAudio = null; }
  if ('speechSynthesis' in window) speechSynthesis.cancel();
  if (recorder && recorder.state === 'recording') { recorder.onstop = null; recorder.stop(); resetMic(); }
  try { await call('/api/jeff/stop', { method: 'POST', json: {} }); } catch {}
  if (chatAbort) chatAbort.abort();
  setBusy(false);
}

// -- upload ------------------------------------------------------------------------------------
async function uploadFile(file) {
  if (!file || busy) return;
  let type = file.type;
  if (!type && /\.md$/i.test(file.name)) type = 'text/markdown';
  if (!type && /\.txt$/i.test(file.name)) type = 'text/plain';
  const caption = $('#message').value.trim();
  const hex = Array.from(new TextEncoder().encode(caption)).map((b) => b.toString(16).padStart(2, '0')).join('');
  appendBubble(bubble('user', `📎 ${file.name}${caption ? ' — ' + caption : ''}`));
  $('#message').value = '';
  const pending = appendBubble(bubble('assistant', '', { pending: true }));
  setBusy(true);
  try {
    const res = await call('/api/jeff/upload', { method: 'POST', raw: file, headers: { 'Content-Type': type || 'application/octet-stream', 'X-Jeff-Caption': hex.slice(0, 1000) } });
    pending.replaceWith(bubble('assistant', res.reply, { disclosure: res.disclosure, attachments: res.attachments }));
  } catch (err) {
    const map = { UPLOAD_TYPE_UNSUPPORTED: 'Такой формат не принимаю: txt, md, jpg, png, webp.', UPLOAD_TOO_LARGE: 'Файл больше 10 МБ.' };
    pending.replaceWith(bubble('assistant', map[err.code] || errorText(err), { stopped: true }));
  } finally { setBusy(false); }
}

// -- voice: dictation --------------------------------------------------------------------------
function resetMic() {
  clearTimeout(recordTimer);
  $('#mic').setAttribute('aria-pressed', 'false');
  $('#mic').classList.remove('recording');
}

async function toggleMic() {
  if (recorder && recorder.state === 'recording') { recorder.stop(); return; }
  const note = $('#voice-note');
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    note.textContent = 'Запись голоса недоступна в этом окне — напишите текстом.'; return;
  }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (err) {
    note.textContent = err?.name === 'NotAllowedError' ? 'Доступ к микрофону запрещён. Разрешите его в настройках или пишите текстом.'
      : err?.name === 'NotFoundError' ? 'Микрофон не найден. Подключите его или пишите текстом.'
      : 'Микрофон сейчас недоступен — пишите текстом.';
    return;
  }
  recordChunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => { if (e.data?.size) recordChunks.push(e.data); };
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    resetMic();
    await transcribe(new Blob(recordChunks, { type: recorder.mimeType || 'audio/webm' }));
  };
  recorder.start();
  $('#mic').setAttribute('aria-pressed', 'true');
  $('#mic').classList.add('recording');
  note.textContent = 'Слушаю… нажмите 🎙 ещё раз, чтобы закончить.';
  recordTimer = setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); }, 30000);
}

async function blobToWav16k(blob) {
  const buf = await blob.arrayBuffer();
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await ctx.decodeAudioData(buf);
  ctx.close();
  const rate = 16000;
  const length = Math.max(1, Math.ceil(decoded.duration * rate));
  const off = new OfflineAudioContext(1, length, rate);
  const src = off.createBufferSource();
  src.buffer = decoded; src.connect(off.destination); src.start();
  const rendered = await off.startRendering();
  const pcm = rendered.getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); out.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  w(36, 'data'); out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
  return new Blob([out.buffer], { type: 'audio/wav' });
}

async function transcribe(blob) {
  const note = $('#voice-note');
  note.textContent = 'Распознаю…';
  let wav;
  try { wav = await blobToWav16k(blob); }
  catch { note.textContent = 'Запись не получилась. Попробуйте ещё раз или напишите текстом.'; return; }
  try {
    const res = await call('/api/jeff/voice/transcribe', { method: 'POST', raw: wav, headers: { 'Content-Type': 'audio/wav' } });
    window.__jeffLastAsr = { confidence: res.confidence, latency_ms: res.latency_ms, needs_confirm: res.needs_confirm };
    if (res.needs_confirm) {
      pendingTranscript = res.text;
      $('#voice-heard').textContent = res.text;
      $('#voice-confirm').hidden = false;
      note.textContent = 'Не уверен, что расслышал точно — проверьте.';
    } else {
      note.textContent = `Распознано за ${(res.latency_ms / 1000).toFixed(1)} с.`;
      await sendText(res.text, 'voice');
    }
  } catch (err) {
    const map = { VOICE_STT_UNAVAILABLE: 'Распознавание речи сейчас недоступно — напишите текстом.',
      VOICE_NO_SPEECH: 'Не расслышал речь. Скажите ещё раз, ближе к микрофону.',
      VOICE_BUSY: 'Распознаю предыдущую запись — подождите секунду.', VOICE_STOPPED: 'Остановлено.' };
    note.textContent = map[err.code] || 'Не удалось распознать. Напишите текстом.';
  }
}

// -- voice: speech -----------------------------------------------------------------------------
async function speak(text) {
  if (!text) return;
  if (currentAudio) { try { currentAudio.pause(); } catch {} currentAudio = null; }
  if (voiceCaps.tts) {
    try {
      const blob = await call('/api/jeff/voice/speak', { method: 'POST', json: { text } });
      const audio = new Audio(URL.createObjectURL(blob));
      currentAudio = audio;
      window.__jeffAudio = audio;
      $('#stop').disabled = false;
      audio.onended = () => { if (currentAudio === audio) currentAudio = null; setBusy(busy); };
      await audio.play();
      return;
    } catch (err) {
      if (err?.code === 'VOICE_STOPPED') return;
    }
  }
  if ('speechSynthesis' in window && speechSynthesis.getVoices().length) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const voice = voices.find((v) => v.name === prefs.voice);
    if (voice) u.voice = voice;
    u.lang = 'ru-RU';
    u.rate = clamp(prefs.rate || 1, 0.7, 1.4);
    speechSynthesis.speak(u);
    return;
  }
  $('#voice-note').textContent = 'Озвучка недоступна — ответ показан текстом.';
}

function hydrateVoiceList() {
  const select = $('#voice-select');
  if (!('speechSynthesis' in window)) { select.innerHTML = '<option value="">Нет системного голоса</option>'; return; }
  const load = () => {
    voices = speechSynthesis.getVoices();
    select.innerHTML = voices.length
      ? voices.map((v) => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)} · ${escapeHtml(v.lang)}</option>`).join('')
      : '<option value="">Системный голос</option>';
    if (prefs.voice && voices.some((v) => v.name === prefs.voice)) select.value = prefs.voice;
  };
  load();
  speechSynthesis.onvoiceschanged = load;
}

async function refreshVoiceStatus() {
  try {
    const h = await call('/api/jeff/health');
    voiceCaps = { asr: !!h.voice?.asr?.available, tts: !!h.voice?.tts?.available };
  } catch { voiceCaps = { asr: false, tts: false }; }
  $('#voice-status').textContent =
    `Распознавание речи: ${voiceCaps.asr ? 'локально, доступно' : 'недоступно — пишите текстом'}. ` +
    `Озвучка: ${voiceCaps.tts ? 'локальный русский голос' : 'недоступна — ответы текстом'}.`;
  document.documentElement.dataset.voiceTts = voiceCaps.tts ? 'on' : 'off';
  document.documentElement.dataset.voiceAsr = voiceCaps.asr ? 'on' : 'off';
}

// -- memory ------------------------------------------------------------------------------------
const AUDIT_LABEL = { write: 'Jeff записал', read: 'Jeff прочитал для ответа', view: 'Вы посмотрели', correct: 'исправлено',
  delete: 'удалено', forget: 'забыто', export: 'выгрузка', owner_passport_read: 'владелец: паспорт' };

async function refreshPrivacy() {
  try {
    const p = await call('/api/jeff/privacy');
    $('#privacy-memory').checked = !!p.memory_enabled;
    $('#privacy-context').checked = !!p.cloud_context_enabled;
  } catch {}
}

async function setPrivacy(patch) {
  try { await call('/api/jeff/privacy', { method: 'POST', json: patch }); }
  catch { showError('Не удалось изменить настройку приватности.'); }
  await refreshPrivacy();
  refreshMemory();
}

async function refreshMemory() {
  let data;
  try { data = await call('/api/jeff/memory'); } catch { return; }
  const list = $('#memory-facts');
  list.innerHTML = data.facts.length ? '' : '<li class="muted">Пока ничего не записано.</li>';
  for (const f of data.facts) {
    const li = document.createElement('li');
    li.className = 'fact';
    li.dataset.id = f.id;
    li.innerHTML = `<span class="fact-cat">${escapeHtml(f.category)}</span><input class="fact-value" value="${escapeHtml(f.value)}" maxlength="300">` +
      `<button class="ghost fact-save" type="button">Исправить</button><button class="ghost fact-del" type="button">Удалить</button>`;
    li.querySelector('.fact-save').addEventListener('click', async () => {
      try { await call('/api/jeff/memory/correct', { method: 'POST', json: { id: f.id, value: li.querySelector('.fact-value').value } }); }
      catch { showError('Не удалось исправить: похоже на секрет или пустое значение.'); }
      refreshMemory();
    });
    li.querySelector('.fact-del').addEventListener('click', async () => {
      try { await call('/api/jeff/memory/delete', { method: 'POST', json: { id: f.id } }); } catch {}
      refreshMemory();
    });
    list.appendChild(li);
  }
  $('#memory-audit').innerHTML = data.audit.slice().reverse().map((row) =>
    `<li data-action="${escapeHtml(row.action)}">${escapeHtml(row.at)} · ${escapeHtml(AUDIT_LABEL[row.action] || row.action)}` +
    `${row.count ? ' · фактов: ' + row.count : ''}${row.categories?.length ? ' · ' + escapeHtml(row.categories.join(', ')) : ''}</li>`).join('');
}

// -- app ---------------------------------------------------------------------------------------
async function loadIdentity() {
  try {
    const ident = await call('/api/jeff/identity');
    const el = $('#build');
    el.textContent = ident.build_sha_short || ident.source_identity || 'unknown';
    el.dataset.sha = ident.build_sha || '';
    el.dataset.identity = ident.source_identity || '';
  } catch {}
}

async function showApp(greeting) {
  $('#login').hidden = true; $('#app').hidden = false;
  applyAvatar();
  try {
    const me = await call('/api/jeff/me');
    $('#whoami').textContent = me.name ? `Вы вошли как ${me.name}` : '';
  } catch {}
  await Promise.all([loadIdentity(), refreshVoiceStatus(), loadHistory(greeting).catch(() => {}), refreshMemory(), refreshPrivacy()]);
  hydrateVoiceList();
}

async function reloadAfterReconnect() {
  if ($('#app').hidden || busy) return;
  await Promise.all([loadIdentity(), loadHistory('').catch(() => {}), refreshMemory()]);
}

setInterval(async () => {
  if ($('#app').hidden) return;
  try { await call('/api/jeff/identity'); } catch {}
}, 3000);

function applyAvatar() {
  document.documentElement.dataset.jeffAvatar = prefs.avatar || 'aurora';
  $$('.avatar-choice').forEach((btn) => btn.setAttribute('aria-pressed', String(btn.dataset.avatar === prefs.avatar)));
  $('#avatar-label').textContent = ({aurora:'Aurora', ember:'Ember', mono:'Mono', cobalt:'Cobalt'})[prefs.avatar] || 'Aurora';
}

function bindUi() {
  $('#login-form').addEventListener('submit', submitLogin);
  $('#logout').addEventListener('click', async () => { try { await call('/api/jeff/logout', { method: 'POST', json: {} }); } catch {} location.reload(); });
  $('#composer').addEventListener('submit', (ev) => { ev.preventDefault(); const v = $('#message').value; $('#message').value = ''; sendText(v); });
  $('#message').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); $('#composer').requestSubmit(); } });
  $('#stop').addEventListener('click', stopAll);
  $('#attach').addEventListener('click', () => $('#file').click());
  $('#file').addEventListener('change', (ev) => { const f = ev.target.files?.[0]; ev.target.value = ''; uploadFile(f); });
  $('#mic').addEventListener('click', toggleMic);
  $('#voice-send').addEventListener('click', () => { $('#voice-confirm').hidden = true; sendText(pendingTranscript, 'voice'); pendingTranscript = ''; });
  $('#voice-edit').addEventListener('click', () => { $('#voice-confirm').hidden = true; $('#message').value = pendingTranscript; $('#message').focus(); });
  $('#voice-cancel').addEventListener('click', () => { $('#voice-confirm').hidden = true; pendingTranscript = ''; $('#voice-note').textContent = 'Отменено.'; });
  $('#avatar-open').addEventListener('click', () => $('#avatar-dialog').showModal());
  $('#avatar-open-secondary').addEventListener('click', () => $('#avatar-dialog').showModal());
  $('#avatar-close').addEventListener('click', () => $('#avatar-dialog').close());
  $$('.avatar-choice').forEach((btn) => btn.addEventListener('click', () => {
    prefs.avatar = btn.dataset.avatar || 'aurora'; savePrefs(); applyAvatar(); $('#avatar-dialog').close();
  }));
  $('#privacy-memory').addEventListener('change', (ev) => setPrivacy({ memory_enabled: ev.target.checked }));
  $('#privacy-context').addEventListener('change', (ev) => setPrivacy({ cloud_context_enabled: ev.target.checked }));
  $('#voice-auto').checked = !!prefs.speak;
  $('#voice-auto').addEventListener('change', (ev) => { prefs.speak = ev.target.checked; savePrefs(); });
  $('#voice-select').addEventListener('change', (ev) => { prefs.voice = ev.target.value; savePrefs(); });
  $('#voice-rate').value = prefs.rate ?? 1;
  $('#voice-rate').addEventListener('input', (ev) => { prefs.rate = clamp(ev.target.value, 0.7, 1.4); savePrefs(); });
  $('#voice-preview').addEventListener('click', () => speak('Привет, я Джефф. Так звучит мой голос.'));
  $$('[data-scroll]').forEach((btn) => btn.addEventListener('click', () =>
    document.querySelector(btn.dataset.scroll)?.scrollIntoView({ behavior: 'smooth', block: 'start' })));
  $$('.capability:not(.locked)').forEach((btn) => btn.addEventListener('click', () => {
    if (btn.dataset.attach) { $('#file').click(); return; }
    $('#message').value = btn.dataset.prompt || ''; $('#message').focus();
  }));
}

bindUi();

(async () => {
  try {
    await call('/api/jeff/me');
    await showApp('');
  } catch (err) {
    if (err.status === 401) {
      let open = false;
      try { const r = await fetch('/api/jeff/me', { credentials: 'same-origin' }); open = !!(await r.json()).signup_open; } catch {}
      showLogin(open);
    } else {
      showLogin(false);
      $('#login-error').textContent = 'Jeff не отвечает на этом адресе. Откройте окно ярлыком Jeff.';
      $('#login-error').hidden = false;
    }
  }
})();
