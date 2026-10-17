/* Bossman Mobile — app shell. Every server string goes into the DOM via textContent only. */
'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const S = { csrf: '', agents: [], agentId: null, threadId: null, sending: false, task: null, es: null,
    photoModels: [], photoJob: null, ref: null, panelLoaded: false, aspect: '1:1', side: 512 };
  const store = {
    get(k) { try { return localStorage.getItem('bm.' + k); } catch { return null; } },
    set(k, v) { try { if (v == null) localStorage.removeItem('bm.' + k); else localStorage.setItem('bm.' + k, String(v)); } catch { /* private mode */ } },
  };
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; };
  const icon = (name) => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); const u = document.createElementNS('http://www.w3.org/2000/svg', 'use'); u.setAttribute('href', '#i-' + name); s.appendChild(u); return s; };
  const fmtS = (s) => s == null ? '—' : (s < 60 ? `${s.toFixed(1)} с` : `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с`);
  const TASK_ST = { queued: 'в очереди', running: 'выполняется', completed: 'готово', failed: 'ошибка', stopped: 'остановлено', blocked: 'заблокировано', cancelled: 'отменено', missing: 'не найдено', waiting_approval: 'ждёт решения', error: 'ошибка' };
  const stRu = (s) => (s && TASK_ST[s]) || s || '';
  const tap = () => { try { navigator.vibrate && navigator.vibrate(8); } catch { /* iOS: no vibrate */ } };

  let toastTimer = 0;
  function toast(msg) { const t = $('toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3200); }

  class ApiError extends Error { constructor(status, body) { const d = (body && (body.detail || body.error)) || {}; super(typeof d === 'string' ? d : (d.message || `HTTP ${status}`)); this.status = status; this.code = d.code || ''; } }
  async function api(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['X-BM-CSRF'] = S.csrf;
    let res;
    try { res = await fetch('/m/api' + path, { method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch { throw new ApiError(0, { detail: { message: 'нет связи с ПК' } }); }
    let data = null; try { data = await res.json(); } catch { /* empty */ }
    if (res.status === 401) { showLogin(); throw new ApiError(401, data); }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  /* ================= вход ================= */
  function showLogin(msg) { $('app').hidden = true; $('login').hidden = false; $('login-error').textContent = msg || ''; }
  async function login(token) {
    const res = await fetch('/m/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ token }) });
    let data = null; try { data = await res.json(); } catch { /* */ }
    if (!res.ok) { const d = (data && data.detail) || {}; throw new Error(res.status === 429 ? `Слишком много попыток. Подождите ${d.retry_after_s || 60} с.` : (d.message || 'Неверный код')); }
    S.csrf = data.csrf;
  }
  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await login($('login-token').value.trim()); $('login-token').value = ''; start(); }
    catch (err) { $('login-error').textContent = err.message; }
  });

  async function boot() {
    const m = /[#&]pair=([^&]+)/.exec(location.hash);
    if (m) { history.replaceState(null, '', location.pathname); try { await login(decodeURIComponent(m[1])); return start(); } catch (err) { return showLogin(err.message); } }
    try { const s = await api('GET', '/session'); S.csrf = s.csrf; start(); } catch { showLogin(); }
  }

  function start() {
    $('login').hidden = true; $('app').hidden = false;
    loadAgents(); loadThreads(); refreshApprovals();
    const tid = store.get('thread'); if (tid) openThread(tid);
    setInterval(refreshApprovals, 20000);
    if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/m/sw.js', { scope: '/m/' }).catch(() => {});
  }

  /* ================= вкладки ================= */
  function showTab(name) {
    document.querySelectorAll('.view').forEach((v) => { v.hidden = v.dataset.view !== name; });
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    if (name === 'photo') openPhoto();
    if (name === 'panel') openPanel();
    if (name === 'status') loadStatus();
    store.set('tab', name);
  }
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => { tap(); showTab(t.dataset.tab); }));

  /* ================= модели (локальные агенты Bossman) ================= */
  async function loadAgents() {
    try {
      const r = await api('GET', '/agents');
      S.agents = r.items || [];
      const saved = Number(store.get('agent'));
      S.agentId = S.agents.some((a) => a.id === saved) ? saved : (r.default_agent_id ?? (S.agents[0] && S.agents[0].id) ?? null);
      renderModelChip();
    } catch (err) { $('model-name').textContent = 'нет моделей'; toast(err.message); }
  }
  const agentOf = (id) => S.agents.find((a) => a.id === id);
  function renderModelChip() {
    const a = agentOf(S.agentId);
    $('model-name').textContent = a ? `${a.name} · ${(a.model && (a.model.alias || a.model.name)) || ''}` : 'выберите модель';
  }
  $('btn-model').addEventListener('click', () => {
    const list = $('model-list'); list.replaceChildren();
    if (!S.agents.length) list.appendChild(el('p', 'muted', 'Локальных агентов нет. Создайте агента на локальной модели в Панели.'));
    S.agents.forEach((a) => {
      const b = el('button', 'model-opt' + (a.id === S.agentId ? ' sel' : ''));
      const t = el('div', 'mtxt'); t.append(el('b', '', a.name), el('span', '', `${(a.model && (a.model.alias || a.model.name)) || '—'}${a.tools.length ? ' · инструменты: ' + a.tools.join(', ') : ''}`));
      b.append(el('span', 'dot'), t, el('span', 'badge-local', 'локально'));
      b.addEventListener('click', () => { tap(); S.agentId = a.id; store.set('agent', a.id); renderModelChip(); $('model-sheet').hidden = true; });
      list.appendChild(b);
    });
    $('model-sheet').hidden = false;
  });
  $('model-sheet').addEventListener('click', (e) => { if (e.target === $('model-sheet')) $('model-sheet').hidden = true; });

  /* ================= ящик с чатами ================= */
  function drawer(open) { $('drawer').classList.toggle('open', open); $('scrim').hidden = !open; if (open) loadThreads(); }
  $('btn-drawer').addEventListener('click', () => { tap(); drawer(true); });
  $('scrim').addEventListener('click', () => drawer(false));
  async function loadThreads() {
    try {
      const r = await api('GET', '/threads');
      const list = $('thread-list'); list.replaceChildren();
      (r.items || []).forEach((t) => {
        const b = el('button', 'thread' + (t.id === S.threadId ? ' active' : ''));
        b.append(el('b', '', t.title || 'Без названия'), el('span', '', `${(t.updated_at || '').replace('T', ' ').slice(0, 16)} · ${t.turns || 0} сообщ.`));
        b.addEventListener('click', () => { drawer(false); openThread(t.id); });
        list.appendChild(b);
      });
      if (!list.children.length) list.appendChild(el('p', 'muted pad', 'Чатов пока нет'));
    } catch { /* offline */ }
  }
  function newChat() { closeStream(); S.threadId = null; store.set('thread', null); clearMessages(); drawer(false); $('input').focus(); }
  $('btn-new').addEventListener('click', () => { tap(); newChat(); });
  $('drawer-new').addEventListener('click', newChat);

  /* ================= сообщения ================= */
  const msgs = $('messages');
  function clearMessages() { msgs.replaceChildren($('chat-empty')); $('chat-empty').hidden = false; }
  function scrollDown() { requestAnimationFrame(() => { msgs.scrollTop = msgs.scrollHeight; }); }
  function addUser(text) { $('chat-empty').hidden = true; const m = el('div', 'msg user'); m.appendChild(el('div', 'bubble', text)); msgs.appendChild(m); scrollDown(); return m; }
  function addBot() {
    $('chat-empty').hidden = true;
    const m = el('div', 'msg bot'); const tools = el('div', 'tools'); const ans = el('div', 'answer'); const meta = el('div', 'meta');
    m.append(tools, ans, meta); msgs.appendChild(m); scrollDown();
    return { m, tools, ans, meta, cards: {}, text: '' };
  }
  function typing(b, on) {
    const old = b.ans.querySelector('.typing'); if (old) old.remove();
    if (on && !b.text) { const t = el('span', 'typing'); t.append(el('i'), el('i'), el('i')); b.ans.appendChild(t); }
  }

  /* небольшой безопасный markdown: абзацы, списки, `код`, **жирный**, ```блоки``` */
  function inline(parent, text) {
    const re = /(\*\*[^*]+\*\*|`[^`]+`)/g; let last = 0; let m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0]; parent.appendChild(tok.startsWith('**') ? el('strong', '', tok.slice(2, -2)) : el('code', '', tok.slice(1, -1)));
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }
  function rich(target, text, streaming) {
    target.replaceChildren(); target.classList.toggle('caret', !!streaming);
    const parts = String(text || '').split(/```[^\n]*\n?/);
    parts.forEach((part, i) => {
      if (i % 2 === 1) { const pre = el('pre'); pre.appendChild(el('code', '', part.replace(/\n$/, ''))); target.appendChild(pre); return; }
      part.split(/\n{2,}/).forEach((para) => {
        if (!para.trim()) return;
        const lines = para.split('\n');
        if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
          const ul = el('ul'); lines.forEach((l) => { const li = el('li'); inline(li, l.replace(/^\s*([-*•]|\d+\.)\s+/, '')); ul.appendChild(li); }); target.appendChild(ul);
        } else { const p = el('p'); lines.forEach((l, j) => { if (j) p.appendChild(el('br')); inline(p, l); }); target.appendChild(p); }
      });
    });
  }

  function argsLine(args) {
    if (!args || typeof args !== 'object') return '';
    return Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' ').slice(0, 140);
  }
  function toolCard(b, ev) {
    let c = b.cards[ev.call_id];
    if (!c) {
      const d = el('details', 'tool'); const s = el('summary');
      const st = el('span', 'spin'); s.append(icon('tool'), el('span', 'tname', ev.tool || 'инструмент'), el('span', 'targs', argsLine(ev.args)), st);
      const body = el('div', 'tbody'); d.append(s, body); b.tools.appendChild(d);
      c = b.cards[ev.call_id] = { d, s, st, body };
      if (ev.args && Object.keys(ev.args).length) { body.appendChild(el('div', 'muted', 'Аргументы')); body.appendChild(el('pre', '', JSON.stringify(ev.args, null, 2))); }
      scrollDown();
    }
    return c;
  }
  function toolResult(b, ev) {
    const c = toolCard(b, ev);
    const st = el('span', 'tstate ' + (ev.ok ? 'ok' : 'err'), ev.ok ? `готово${ev.duration_ms != null ? ' · ' + (ev.duration_ms / 1000).toFixed(1) + ' с' : ''}` : 'ошибка');
    c.st.replaceWith(st); c.st = st;
    if (ev.summary) c.body.appendChild(el('div', '', ev.summary));
    if (ev.preview) c.body.appendChild(el('pre', '', ev.preview + (ev.truncated ? '\n…' : '')));
  }
  function approvalCard(b, ev) {
    if (b.cards['ap' + ev.id]) return;
    const box = el('div', 'approval'); box.appendChild(el('h4', '', 'Нужно ваше решение'));
    box.appendChild(el('pre', '', ev.preview || ev.approval_kind || 'действие'));
    const row = el('div', 'row'); const no = el('button', 'btn', 'Отклонить'); const yes = el('button', 'btn primary', 'Разрешить');
    row.append(no, yes); box.appendChild(row); b.tools.appendChild(box); b.cards['ap' + ev.id] = box; scrollDown();
    const decide = async (approve) => {
      tap(); yes.disabled = no.disabled = true;
      try { await api('POST', `/approvals/${ev.id}`, { approve }); row.replaceWith(el('div', 'done', approve ? '✓ Разрешено вами' : '✕ Отклонено вами')); refreshApprovals(); }
      catch (err) { yes.disabled = no.disabled = false; toast(err.message); }
    };
    yes.addEventListener('click', () => decide(true)); no.addEventListener('click', () => decide(false));
  }
  function approvalDecided(b, ev) {
    const box = b.cards['ap' + ev.id]; if (!box) return; const row = box.querySelector('.row');
    if (row) row.replaceWith(el('div', 'done', ev.approve === false || ev.status === 'rejected' ? '✕ Отклонено' : '✓ Разрешено'));
  }

  function applyEvent(b, ev, live) {
    switch (ev.kind) {
      case 'run.answer_delta': b.text += ev.text || ''; rich(b.ans, b.text, live); if (live && !b.first) b.first = performance.now(); scrollDown(); break;
      case 'run.answer_reset': b.text = ''; rich(b.ans, '', live); break;
      case 'run.assistant_message': if (ev.text) { b.text = ev.text; rich(b.ans, b.text, false); } if (ev.model) b.model = ev.model; break;
      case 'run.tool_use': toolCard(b, ev); break;
      case 'run.tool_result': toolResult(b, ev); break;
      case 'tool.denied': { const c = toolCard(b, { call_id: 'd' + (ev.seq || Math.random()), tool: ev.tool, args: {} }); const st = el('span', 'tstate err', 'запрещено'); c.st.replaceWith(st); c.st = st; if (ev.reason) c.body.appendChild(el('div', '', ev.reason)); break; }
      case 'approval.created': approvalCard(b, ev); break;
      case 'approval.decided': approvalDecided(b, ev); break;
      default: break;
    }
  }

  async function openThread(id) {
    closeStream();
    try {
      const t = await api('GET', `/threads/${encodeURIComponent(id)}`);
      S.threadId = t.id; store.set('thread', t.id); clearMessages();
      const turns = t.turns || [];
      for (const turn of turns) {
        addUser(turn.text); const b = addBot();
        if (turn.status && !['completed', 'failed', 'stopped', 'blocked', 'missing'].includes(turn.status)) { follow(turn.task_id, b); continue; }
        if (turn.result) rich(b.ans, turn.result); else if (turn.error) { b.ans.classList.add('error'); b.ans.textContent = turn.error; }
        b.meta.textContent = `задача #${turn.task_id} · ${stRu(turn.status)}`;
        if (turns.indexOf(turn) >= turns.length - 6) {
          api('GET', `/tasks/${turn.task_id}/history`).then((h) => (h.events || []).forEach((ev) => { if (ev.kind.startsWith('run.tool') || ev.kind.startsWith('approval') || ev.kind === 'tool.denied') applyEvent(b, ev, false); })).catch(() => {});
        }
      }
      scrollDown();
    } catch (err) { if (err.status === 404) { store.set('thread', null); S.threadId = null; } }
  }

  /* ================= отправка ================= */
  const input = $('input');
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !('ontouchstart' in window)) { e.preventDefault(); $('composer').requestSubmit(); } });
  document.querySelectorAll('[data-suggest]').forEach((c) => c.addEventListener('click', () => { input.value = c.dataset.suggest; input.dispatchEvent(new Event('input')); input.focus(); }));
  $('composer').addEventListener('submit', async (e) => { e.preventDefault(); const text = input.value.trim(); if (!text || S.sending) return; tap(); input.value = ''; input.style.height = 'auto'; await send(text); });
  $('btn-stop').addEventListener('click', async () => { tap(); if (!S.threadId) return; try { await api('POST', `/threads/${encodeURIComponent(S.threadId)}/stop`); toast('Остановлено'); } catch (err) { toast(err.message); } });
  function busy(on) { S.sending = on; $('btn-send').hidden = on; $('btn-stop').hidden = !on; }

  const PHOTO_RE = /^\s*(сгенерируй|нарисуй|создай|сделай)\s+(мне\s+)?(фото|фотку|картинку|изображение|рисунок)\s*[:,-]?\s*/i;
  async function send(text) {
    if (PHOTO_RE.test(text)) return chatPhoto(text, text.replace(PHOTO_RE, '') || text);
    if (S.agentId == null) { toast('Выберите локальную модель'); return; }
    busy(true); addUser(text); const b = addBot(); typing(b, true); b.t0 = performance.now();
    try {
      if (!S.threadId) { const t = await api('POST', '/threads', { title: text.slice(0, 60) }); S.threadId = t.id; store.set('thread', t.id); }
      const r = await api('POST', `/threads/${encodeURIComponent(S.threadId)}/send`, { text, agent_id: S.agentId, client_request_id: 'm-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10) });
      if (r.admission && r.admission.ok === false) { finish(b, null, r.admission.reason || 'Bossman не принял задачу', 'blocked'); return; }
      b.model = r.model && (r.model.alias || r.model.id);
      follow(r.task.id, b);
    } catch (err) { finish(b, null, err.message, 'error'); }
  }
  function closeStream() { if (S.es) { S.es.close(); S.es = null; } if (S.poll) { clearInterval(S.poll); S.poll = null; } }
  function follow(taskId, b) {
    closeStream(); busy(true); typing(b, true); b.t0 = b.t0 || performance.now(); S.task = taskId;
    const es = new EventSource(`/m/api/tasks/${taskId}/events?after=0`); S.es = es;
    es.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch { return; }
      typing(b, false); applyEvent(b, ev, true); if (!b.text) typing(b, true);
      if (['task.completed', 'task.failed', 'task.stopped', 'task.blocked'].includes(ev.kind)) settle(taskId, b);
    };
    S.poll = setInterval(async () => { try { const t = await api('GET', `/tasks/${taskId}`); if (['completed', 'failed', 'stopped', 'blocked', 'cancelled'].includes(t.status)) settle(taskId, b, t); } catch { /* keep trying */ } }, 5000);
  }
  async function settle(taskId, b, t) {
    if (b.done) return; b.done = true; closeStream();
    try { t = t || await api('GET', `/tasks/${taskId}`); } catch { t = t || {}; }
    finish(b, t.result, t.status === 'completed' ? null : (t.error || `задача: ${stRu(t.status)}`), t.status, taskId);
  }
  function finish(b, result, error, status, taskId) {
    busy(false); typing(b, false); b.ans.classList.remove('caret');
    if (result) { b.text = result; rich(b.ans, result); } else if (error && !b.text) { b.ans.classList.add('error'); b.ans.textContent = error; }
    const total = b.t0 ? (performance.now() - b.t0) / 1000 : null; const first = b.first && b.t0 ? (b.first - b.t0) / 1000 : null;
    b.meta.replaceChildren(el('span', 'badge-local', 'локально'), el('span', '', [b.model, taskId ? `#${taskId}` : '', status && status !== 'completed' ? stRu(status) : '', total ? `${total.toFixed(1)} с` : '', first ? `первый токен ${first.toFixed(1)} с` : ''].filter(Boolean).join(' · ')));
    loadThreads(); scrollDown();
  }

  /* ---------- «сгенерируй фото …» прямо из чата: тот же Прямой генератор Bossman ---------- */
  async function chatPhoto(text, prompt) {
    busy(true); addUser(text); const b = addBot(); typing(b, true);
    try {
      await ensurePhotoModels();
      const m = S.photoModels.find((x) => x.id === 'z-image-turbo' && x.available) || S.photoModels.find((x) => x.available && !x.requires_image);
      if (!m) throw new Error('нет доступной фото-модели');
      const [w, h] = (m.default_resolution || '512x512').split('x');
      const job = await api('POST', '/photo/jobs', { model: m.id, prompt, resolution: `${w}x${h}` });
      const card = el('div', 'card progress'); b.tools.appendChild(card); typing(b, false);
      const final = await trackJob(job, card);
      if (final.status === 'completed') { const wrap = el('div', 'inline-photo'); const img = el('img'); img.src = `/m/api/photo/jobs/${final.job_id}/file`; img.alt = prompt; img.addEventListener('click', () => openViewer(final)); wrap.appendChild(img); card.replaceWith(wrap); }
      b.meta.replaceChildren(el('span', 'badge-local', 'на ПК'), el('span', '', `${m.label} · ${fmtS(final.elapsed_s)} · Прямой генератор`));
    } catch (err) { b.ans.classList.add('error'); b.ans.textContent = err.message; }
    busy(false); scrollDown();
  }

  /* ================= одобрения (значок на вкладке «Панель») ================= */
  async function refreshApprovals() {
    try { const r = await api('GET', '/approvals'); const n = (r.items || []).length; const bd = $('badge-approvals'); bd.hidden = !n; bd.textContent = n; } catch { /* */ }
  }

  /* ================= Фото ================= */
  async function ensurePhotoModels() { if (!S.photoModels.length) S.photoModels = (await api('GET', '/photo/models')).models || []; }
  async function openPhoto() {
    try {
      await ensurePhotoModels();
      const sel = $('photo-model');
      if (!sel.options.length) {
        S.photoModels.forEach((m) => { const o = el('option', '', `${m.label}${m.modes && m.modes.includes('I2I') ? ' · по фото' : ''}${m.available ? '' : ' — недоступна'}`); o.value = m.id; o.disabled = !m.available; sel.appendChild(o); });
        const saved = store.get('photoModel'); const first = S.photoModels.find((m) => m.id === saved && m.available) || S.photoModels.find((m) => m.id === 'z-image-turbo' && m.available) || S.photoModels.find((m) => m.available);
        if (first) sel.value = first.id;
        onModel();
      }
    } catch (err) { toast(err.message); }
    loadGallery(); photoStatus();
  }
  const curModel = () => S.photoModels.find((m) => m.id === $('photo-model').value);
  function onModel() {
    const m = curModel(); if (!m) return; store.set('photoModel', m.id);
    const f = $('photo-flags'); f.replaceChildren();
    if (m.uncensored_label) f.appendChild(el('span', 'badge-uc', 'без цензуры (по метке Bossman)'));
    f.appendChild(el('span', 'badge-local', 'на ПК'));
    if (m.license) f.appendChild(el('span', 'pill', m.license));
    $('ref-block').hidden = !m.accepts_image; $('ref-label').textContent = m.requires_image ? 'Выбрать фото (обязательно)' : 'Выбрать из «Фото»';
    const st = $('photo-steps'); st.min = m.min_steps || 1; st.max = m.max_steps || 50; st.value = m.default_steps || 20; setSteps(st.value);
    $('steps-range').textContent = `(от ${st.min} до ${st.max}, по умолчанию ${m.default_steps || 20})`;
    const base = Number((m.default_resolution || '512x512').split('x')[0]) || 512;
    const sides = [512, 768, 1024, 1280].filter((s) => s >= (m.min_side || 256) && s <= (m.max_side || 1536));
    S.side = sides.includes(base) ? base : sides[0];
    seg('photo-side', sides.map((s) => [s, `${s}`]), S.side, (v) => { S.side = v; showTime(); });
    seg('photo-aspect', [['1:1', '1:1'], ['3:4', '3:4'], ['4:3', '4:3'], ['9:16', '9:16'], ['16:9', '16:9']], S.aspect, (v) => { S.aspect = v; showTime(); });
    showTime();
  }
  /* ---- steps + approximate generation time (model: load + steps x s/step x pixels/512^2, see bossmobile/timing.py) ---- */
  function fmtDur(sec) {
    const q = sec >= 120 ? 10 : 5;
    const s = Math.max(1, Math.round(sec / q) * q);
    const h = Math.floor(s / 3600), mi = Math.floor((s % 3600) / 60), r = s % 60;
    if (h) return `${h} ч ${mi} мин`;
    if (mi) return r ? `${mi} мин ${r} с` : `${mi} мин`;
    return `${r} с`;
  }
  function timeParts(m, steps, res) {
    const t = m.timing || { load_s: 40, s_per_step: 8, kind: 'estimate' }; const [w, h] = res.split('x').map(Number);
    return { load: t.load_s, run: steps * t.s_per_step * (w * h) / (512 * 512) };
  }
  const tag = (t) => el('span', t.kind === 'measured' ? 'tag-m' : 'tag-e', t.kind === 'measured' ? 'по замеру' : 'оценка');
  function setSteps(v) {
    const st = $('photo-steps'); const n = Math.min(Number(st.max), Math.max(Number(st.min), Math.round(Number(v)) || Number(st.min)));
    st.value = n; $('steps-val').textContent = n; showTime();
  }
  function showTime() {
    const m = curModel(); const box = $('photo-time'); if (!m) { box.replaceChildren(); return; }
    const res = resolution(m); const steps = Number($('photo-steps').value); const t = m.timing || {}; const p = timeParts(m, steps, res);
    const b = el('b', '', `≈ ${fmtDur(p.load + p.run)}`);
    const note = el('span', 'muted', `${res.replace('x', '×')} · ${steps} шаг. · загрузка ${fmtDur(p.load)} + шаги ${fmtDur(p.run)}`);
    box.replaceChildren(b, tag(t), note);
    if (t.basis) box.appendChild(el('span', 'hint', t.basis));
    fillTable();
  }
  function fillTable() {
    const box = $('models-table'); if (!box) return; box.replaceChildren();
    S.photoModels.forEach((m) => {
      const t = m.timing || {}; const r = el('button', `mrow${m.id === $('photo-model').value ? ' on' : ''}`); r.type = 'button'; r.disabled = !m.available;
      r.appendChild(el('span', 'mn', m.label));
      r.appendChild(el('span', 'ms', `шаги ${m.min_steps}–${m.max_steps}, по умолч. ${m.default_steps} · ${(m.default_resolution || '512x512').replace('x', '×')}`));
      const dr = m.default_resolution || '512x512'; const p = timeParts(m, m.default_steps, dr); const mt = el('span', 'mt'); mt.append(el('b', '', `≈ ${fmtDur(p.load + p.run)}`), tag(t)); r.appendChild(mt);
      r.addEventListener('click', () => { tap(); $('photo-model').value = m.id; onModel(); });
      box.appendChild(r);
    });
  }
  function seg(id, opts, cur, onPick) {
    const box = $(id); box.replaceChildren();
    opts.forEach(([v, label]) => { const b = el('button', v === cur ? 'on' : '', label); b.type = 'button'; b.setAttribute('role', 'radio'); b.addEventListener('click', () => { tap(); box.querySelectorAll('button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); onPick(v); }); box.appendChild(b); });
  }
  function resolution(m) {
    const [a, c] = S.aspect.split(':').map(Number); const k = m.side_multiple || 16; const max = m.max_side || 1536; const min = m.min_side || 256;
    let w = S.side; let h = S.side;
    if (a > c) h = S.side * c / a; else if (c > a) w = S.side * a / c;
    const r = (v) => Math.min(max, Math.max(min, Math.round(v / k) * k));
    return `${r(w)}x${r(h)}`;
  }
  $('photo-model').addEventListener('change', onModel);
  $('photo-steps').addEventListener('input', () => setSteps($('photo-steps').value));
  $('steps-minus').addEventListener('click', () => { tap(); setSteps(Number($('photo-steps').value) - 1); });
  $('steps-plus').addEventListener('click', () => { tap(); setSteps(Number($('photo-steps').value) + 1); });
  $('photo-ref').addEventListener('change', async () => {
    const f = $('photo-ref').files[0]; if (!f) return;
    try { S.ref = await downscale(f, 1536); $('ref-preview').src = S.ref; $('ref-preview').hidden = false; $('ref-clear').hidden = false; }
    catch { toast('не удалось прочитать фото'); }
  });
  $('ref-clear').addEventListener('click', () => { S.ref = null; $('photo-ref').value = ''; $('ref-preview').hidden = true; $('ref-clear').hidden = true; });
  function downscale(file, max) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file); const img = new Image();
      img.onload = () => { const k = Math.min(1, max / Math.max(img.width, img.height)); const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k); c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url); resolve(c.toDataURL('image/jpeg', 0.92)); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('img')); }; img.src = url;
    });
  }
  $('photo-form').addEventListener('submit', async (e) => {
    e.preventDefault(); tap(); const m = curModel(); if (!m) return;
    if (m.requires_image && !S.ref) { toast('Этой модели нужно фото-референс'); return; }
    const seed = $('photo-seed').value.trim();
    const body = { model: m.id, prompt: $('photo-prompt').value.trim(), negative: $('photo-negative').value.trim(), resolution: resolution(m), steps: Number($('photo-steps').value) };
    if (seed) body.seed = Number(seed);
    if (S.ref && m.accepts_image) body.image_b64 = S.ref;
    if (!body.prompt) return;
    $('photo-go').disabled = true;
    try {
      const job = await api('POST', '/photo/jobs', body);
      const card = el('div', 'card progress'); $('photo-progress').replaceChildren(card); card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const final = await trackJob(job, card); photoStatus();
      if (final.status === 'completed') { const img = el('img'); img.src = `/m/api/photo/jobs/${final.job_id}/file`; img.alt = final.raw_prompt || ''; img.addEventListener('click', () => openViewer(final)); card.appendChild(img); loadGallery(); }
    } catch (err) { toast(err.message); }
    $('photo-go').disabled = false;
  });
  const STAGES = { queued: 'В очереди', loading: 'Загрузка модели', generating: 'Генерация', encoding: 'Сохранение', completed: 'Готово', failed: 'Ошибка', cancelled: 'Отменено' };
  function renderJob(card, j) {
    card.replaceChildren();
    const p = j.progress || {}; const pct = typeof p.percent === 'number' ? p.percent : null;
    const row = el('div', 'row'); row.append(el('span', 'stage', STAGES[j.stage || j.status] || j.stage || j.status));
    const done = ['completed', 'failed', 'cancelled'].includes(j.status);
    if (!done) { const c = el('button', 'btn small danger', 'Стоп'); c.type = 'button'; c.addEventListener('click', async () => { try { await api('POST', `/photo/jobs/${j.job_id}/cancel`); } catch (err) { toast(err.message); } }); row.append(c); }
    card.appendChild(row);
    if (!done) { const bar = el('div', 'bar' + (pct == null ? ' indet' : '')); const i = el('i'); if (pct != null) i.style.width = `${Math.max(2, pct)}%`; bar.appendChild(i); card.appendChild(bar); }
    const stats = el('div', 'stats');
    if (j.queue_position) stats.appendChild(el('span', '', `перед вами: ${j.queue_position}`));
    if (pct != null && !done) stats.appendChild(el('span', '', `${Math.round(pct)}% · шаг ${p.value}/${p.max}`));
    stats.appendChild(el('span', '', `прошло ${fmtS(j.elapsed_s)}`));
    const gen = (j.timeline || []).find((t) => t.stage === 'generating');
    if (gen && pct && pct > 3 && !done) { const g = (Date.now() - Date.parse(gen.at)) / 1000; stats.appendChild(el('span', '', `осталось ≈ ${fmtS(g * (100 - pct) / pct)}`)); }
    if (j.params) stats.appendChild(el('span', '', `${j.params.width}×${j.params.height} · сид ${j.params.seed}`));
    card.appendChild(stats);
    if (j.error) card.appendChild(el('div', 'error-text', typeof j.error === 'string' ? j.error : (j.error.message || JSON.stringify(j.error))));
  }
  async function trackJob(job, card) {
    let j = job; renderJob(card, j);
    while (!['completed', 'failed', 'cancelled'].includes(j.status)) {
      await new Promise((r) => setTimeout(r, 1500));
      try { j = await api('GET', `/photo/jobs/${job.job_id}`); renderJob(card, j); } catch (err) { if (err.status === 401) throw err; }
    }
    return j;
  }
  async function loadGallery() {
    try {
      const r = await api('GET', '/photo/jobs'); const g = $('gallery'); g.replaceChildren();
      const done = (r.jobs || []).filter((j) => j.status === 'completed' && j.result && String(j.result.mime || '').startsWith('image/'));
      done.forEach((j) => { const b = el('button', 'thumb'); const img = el('img'); img.loading = 'lazy'; img.src = `/m/api/photo/jobs/${j.job_id}/file`; img.alt = j.raw_prompt || ''; b.appendChild(img); b.addEventListener('click', () => openViewer(j)); g.appendChild(b); });
      if (!done.length) g.appendChild(el('p', 'muted', 'Пока пусто'));
    } catch { /* */ }
  }
  async function photoStatus() {
    try { const s = await api('GET', '/photo/status'); const q = s.queue || {}; $('photo-queue').textContent = q.running ? `идёт генерация · ждут ${q.waiting || 0}` : (q.waiting ? `ждут ${q.waiting}` : 'ПК свободен'); } catch { /* */ }
  }
  let viewing = null;
  function openViewer(j) {
    viewing = j; $('viewer-img').src = `/m/api/photo/jobs/${j.job_id}/file`;
    const m = S.photoModels.find((x) => x.id === j.model);
    $('viewer-meta').textContent = `${j.raw_prompt || ''}\n${m ? m.label : j.model} · ${j.params ? j.params.width + '×' + j.params.height + ' · сид ' + j.params.seed : ''} · ${fmtS(j.elapsed_s)}`;
    $('viewer').hidden = false;
  }
  $('viewer-close').addEventListener('click', () => { $('viewer').hidden = true; });
  $('viewer-save').addEventListener('click', async () => {
    if (!viewing) return; tap(); const url = `/m/api/photo/jobs/${viewing.job_id}/file`;
    try {
      const blob = await (await fetch(url, { credentials: 'same-origin' })).blob();
      const file = new File([blob], `bossman-${viewing.job_id.slice(0, 8)}.${blob.type.includes('jpeg') ? 'jpg' : 'png'}`, { type: blob.type });
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file] }); return; }
    } catch (err) { if (err && err.name === 'AbortError') return; }
    window.open(url + '?download=1', '_blank');
  });
  $('viewer-reuse').addEventListener('click', () => { if (!viewing) return; $('viewer').hidden = true; $('photo-prompt').value = viewing.raw_prompt || ''; showTab('photo'); $('photo-prompt').focus(); });

  /* ================= Панель (Bossman 1:1) ================= */
  function openPanel(force) {
    if (S.panelLoaded && !force) return;
    const f = document.createElement('iframe'); f.src = '/panel/'; f.title = 'Bossman'; f.setAttribute('allow', 'clipboard-write');
    $('panel-wrap').replaceChildren(f); S.panelLoaded = true;
  }
  $('panel-reload').addEventListener('click', () => { tap(); openPanel(true); });
  $('panel-open').addEventListener('click', () => window.open('/panel/', '_blank'));

  /* ================= Статус ================= */
  function kv(k, v) { const d = el('div', 'kv'); d.append(el('span', '', k), el('span', '', v == null ? '—' : v)); return d; }
  function stat(title, up, rows) { const c = el('div', 'card stat'); const h = el('h3'); h.append(el('span', 'led ' + (up === true ? 'on' : up === false ? 'off' : '')), document.createTextNode(title)); c.appendChild(h); rows.forEach((r) => c.appendChild(r)); return c; }
  async function loadStatus() {
    const body = $('status-body');
    try {
      const s = await api('GET', '/status'); body.replaceChildren();
      const bmv = s.bossman || {};
      body.appendChild(stat('Bossman', bmv.up, [el('div', 'big', bmv.up ? 'работает' : 'не отвечает'), kv('сборка', bmv.build_sha_short), kv('источник', bmv.source_identity), kv('ждут вашего решения', s.approvals_pending)]));
      const o = s.ollama || {}; const tags = el('div', 'tags'); (o.models || []).forEach((m) => tags.appendChild(el('span', '', `${m.name}${m.params ? ' · ' + m.params : ''}`)));
      body.appendChild(stat('Локальные модели (Ollama)', o.up, [kv('моделей', (o.models || []).length), kv('загружены сейчас', (o.loaded || []).join(', ') || 'нет'), kv('локальных агентов для чата', s.local_agents), tags]));
      const dg = s.direct_gen || {}; const q = dg.queue || {}; const mem = dg.memory || {};
      body.appendChild(stat('Прямой генератор', dg.sd_cli ? dg.sd_cli.present : null, [kv('сейчас', q.running ? 'идёт генерация' : 'свободен'), kv('в очереди', q.waiting), kv('ОЗУ свободно', mem.measured ? `${(mem.free_mb / 1024).toFixed(1)} из ${(mem.total_mb / 1024).toFixed(0)} ГБ` : '—')]));
      const w = s.war_terminal || {}; body.appendChild(stat('War Terminal', w.up, [kv('состояние', w.up ? `работает · ${w.version || ''} ${w.mode || ''}` : 'не запущен')]));
      const mob = s.mobile || {}; const out = el('button', 'btn danger', 'Выйти на этом телефоне'); out.addEventListener('click', async () => { try { await api('POST', '/logout'); } catch { /* */ } showLogin(); });
      body.appendChild(stat('Bossman Mobile', true, [kv('версия', mob.version), kv('активных сессий', mob.sessions), kv('модели чата', 'только локальные (сервер отклоняет облачные)'), out]));
    } catch (err) { body.replaceChildren(el('p', 'error-text pad', err.message)); }
  }
  $('status-reload').addEventListener('click', () => { tap(); loadStatus(); });

  boot();
})();
