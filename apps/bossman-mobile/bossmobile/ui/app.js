/* Bossman Mobile — app shell. Every server string goes into the DOM via textContent only. */
'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const S = { csrf: '', agents: [], agentId: null, threadId: null, sending: false, task: null, es: null,
    photoModels: [], photoJob: null, refs: [], refsUsed: false, refsLoading: false, photoSending: false, panelLoaded: false, aspect: '1:1', side: 512 };
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
  let panelCsrf = '';
  async function callPassthrough(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') {
      if (!panelCsrf) {
        const lr = await fetch('/api/login', { method: 'POST', credentials: 'same-origin' });
        let ld = null; try { ld = await lr.json(); } catch { /* */ }
        panelCsrf = (ld && ld.csrf) || '';
      }
      headers['X-BCC-CSRF'] = panelCsrf;
    }
    const res = await fetch('/api/telegram' + path, { method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) });
    let data = null; try { data = await res.json(); } catch { /* */ }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }
  async function api(method, path, body, retried) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['X-BM-CSRF'] = S.csrf;
    let res;
    try { res = await fetch('/m/api' + path, { method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch { throw new ApiError(0, { detail: { message: 'нет связи с ПК' } }); }
    let data = null; try { data = await res.json(); } catch { /* empty */ }
    if (res.status === 401) { showLogin(); throw new ApiError(401, data); }
    if (res.status === 404 && path.startsWith('/calls/') && ((data && data.detail) || {}).code === 'BM_NOT_ALLOWED') {
      return callPassthrough(method, path, body);
    }
    // A newer login (e.g. the pinned «вход в одно касание» link in another tab) replaced the session cookie, so this
    // page's CSRF token is stale: fetch the current one and repeat the request ONCE (owner 19.10, «не пройдена CSRF»).
    if (res.status === 403 && !retried && method !== 'GET' && ((data && data.detail) || {}).code === 'csrf') {
      try { const s = await api('GET', '/session'); if (s && s.csrf) { S.csrf = s.csrf; return api(method, path, body, true); } }
      catch { /* fall through to the original error */ }
    }
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
    refreshStop(); if (!stopTimer) stopTimer = setInterval(refreshStop, 30000);
    if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/m/sw.js', { scope: '/m/' }).catch(() => {});
  }

  /* ================= общий STOP (owner 20.10: STOP часами висел после теста, и никто не знал) ================= */
  const pad2 = (n) => String(n).padStart(2, '0');
  function stopSinceText(iso) {
    const d = iso ? new Date(iso) : null;
    if (!d || Number.isNaN(d.getTime())) return '';
    const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    return d.toDateString() === new Date().toDateString() ? hm : `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)} ${hm}`;
  }
  function renderStop(s) {
    const on = !!(s && s.active);
    $('stop-banner').hidden = !on; $('app').classList.toggle('stop-on', on);
    if (!on) return;
    const t = stopSinceText(s.since);
    const text = `⛔ STOP включён${t ? ' с ' + t : ''} — действия ПК и записи обменника заблокированы`;
    if ($('stop-text').textContent !== text) $('stop-text').textContent = text;   // role=alert: no re-announce every 30 s
  }
  let stopTimer = 0; let stopBusy = false;
  async function refreshStop() {
    if ($('app').hidden) return;
    try { renderStop(await api('GET', '/stop')); } catch { /* keep the last known state; 401 already shows the login */ }
  }
  $('stop-resume').addEventListener('click', async () => {
    if (stopBusy) return;
    if (!confirm('Снять общий STOP? Bossman снова сможет управлять ПК, а обменник — записывать сделки.')) return;
    stopBusy = true; $('stop-resume').disabled = true;
    try { renderStop(await api('POST', '/stop/resume')); toast('STOP снят'); }
    catch (err) { toast('STOP не снят: ' + err.message); refreshStop(); }
    finally { stopBusy = false; $('stop-resume').disabled = false; }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStop(); });

  /* ================= вкладки ================= */
  function showTab(name) {
    document.querySelectorAll('.view').forEach((v) => { v.hidden = v.dataset.view !== name; });
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    if (name === 'photo') openPhoto();
    if (name === 'video') openVideo();
    if (name === 'panel') openPanel();
    if (name === 'status') loadStatus();
    if (name === 'swap') loadSwapDeals();
    if (name === 'calls') loadCallsView();
    store.set('tab', name);
  }

  /* ================= звонки Telegram ================= */
  const ACCT_RU = { ready: 'вход выполнен', logged_out: 'не выполнен вход', no_credentials: 'нет API ключей', code_sent: 'ожидает код из Telegram', password_required: 'ожидает 2FA пароль', error: 'ошибка ключей' };
  async function loadCallsView() {
    const sum = $('calls-summary');
    if (!sum) return;
    try {
      const [st, hist, reps] = await Promise.all([
        api('GET', '/calls/status'),
        api('GET', '/calls/history?limit=10').catch(() => ({ items: [] })),
        api('GET', '/calls/answering/reports?limit=10').catch(() => ({ items: [] })),
      ]);
      sum.replaceChildren();
      const acct = st.account || {};
      const peer = st.peer || {};
      const stop = st.stop || {};
      const ans = st.answering || {};
      const cfg = st.settings || {};
      sum.append(
        kv('Аккаунт Telegram', `${ACCT_RU[acct.state] || acct.state || '—'}${acct.phone ? ' (' + acct.phone + ')' : ''}${acct.api_id ? ' · API ' + acct.api_id : ''}`),
        kv('Собеседник', peer.user_id ? `${peer.label || ''} (ID ${peer.user_id})` : 'не выбран'),
        kv('Исходящие звонки', st.enabled ? 'разрешены' : 'выключены'),
        kv('Линия сейчас', st.call_active && st.call ? `${st.call.state || 'звонок'} (${st.call.call_id || ''})` : 'свободна'),
        kv('СТОП звонков', stop.active ? `АКТИВЕН (${stop.call ? 'звонки' : ''}${stop.global ? ' общий' : ''})` : 'снят'),
        kv('Автоответчик', ans.listening ? 'слушает входящие' : (ans.enabled ? 'включён в настройках (не запущен)' : 'выключен')),
        kv('Пакеты звонков', (st.deps && st.deps.ready) ? 'установлены' : 'не все пакеты')
      );
      $('calls-enabled').checked = !!cfg.enabled;
      $('calls-barge-in').checked = cfg.barge_in !== false;
      if (cfg.greeting != null && !$('calls-greeting').matches(':focus')) $('calls-greeting').value = cfg.greeting;
      $('calls-answering-enabled').checked = !!cfg.answering_machine;
      $('calls-allow-unknown').checked = cfg.answer_allow_unknown !== false;
      if (cfg.answer_ring_delay_s != null && !$('calls-ring-delay').matches(':focus')) $('calls-ring-delay').value = cfg.answer_ring_delay_s;
      if (cfg.answer_greeting != null && !$('calls-answer-greeting').matches(':focus')) $('calls-answer-greeting').value = cfg.answer_greeting;
      $('calls-peer-current').textContent = peer.user_id ? `Текущий собеседник: ${peer.label || 'без имени'} (ID ${peer.user_id})` : 'Текущий собеседник: не выбран';
      const hbox = $('calls-history'); hbox.replaceChildren();
      const items = (hist && hist.items) || [];
      const ritems = (reps && reps.items) || [];
      if (!items.length && !ritems.length) {
        hbox.appendChild(el('p', 'muted', 'Звонков и отчётов автоответчика пока нет.'));
      } else {
        items.forEach((c) => {
          const row = el('div', 'swap-deal');
          row.appendChild(el('b', '', `${c.call_id || 'звонок'} · ${c.outcome || 'завершён'}`));
          row.appendChild(el('span', '', ` · реплик: ${c.turns || 0}`));
          if (c.summary && c.summary.text) row.appendChild(el('small', 'muted', c.summary.text));
          hbox.appendChild(row);
        });
        ritems.forEach((r) => {
          const row = el('div', 'swap-deal');
          row.appendChild(el('b', '', `Автоответчик ${r.id || ''} · ${r.outcome || ''}`));
          if (r.summary) row.appendChild(el('small', 'muted', r.summary));
          hbox.appendChild(row);
        });
      }
    } catch (err) {
      sum.replaceChildren(el('p', 'error-text', 'Ошибка: ' + err.message));
    }
  }
  async function callsAct(fn, label) {
    const out = $('calls-live-output');
    if (out) out.textContent = label + '…';
    try {
      const r = await fn();
      if (out) out.textContent = typeof r === 'string' ? r : JSON.stringify(r, null, 2);
      await loadCallsView();
    } catch (err) {
      if (out) out.textContent = 'Ошибка: ' + err.message;
      toast(err.message);
    }
  }
  if ($('calls-reload')) {
    $('calls-reload').addEventListener('click', () => { tap(); loadCallsView(); });
    $('calls-dial-btn').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/call', { confirm_unknown: true }), 'Набор номера'); });
    $('calls-hangup-btn').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/hangup', {}), 'Сброс звонка'); });
    $('calls-stop-btn').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/stop', {}), 'Активация СТОП звонков'); });
    $('calls-resume-btn').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/resume', {}), 'Снятие СТОП звонков'); });
    $('calls-selftest-btn').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/selftest', { scenario: 'all' }), 'Проверка аудиоконтура без Telegram'); });
    $('calls-doctor-btn').addEventListener('click', () => {
      tap();
      callsAct(async () => {
        const d = await api('POST', '/calls/doctor', {});
        const lines = [`Вердикт: ${d.verdict}`].concat((d.rows || []).map((r) => `[${r.status}] ${r.check}: ${r.detail}${r.remedy ? ' -> ' + r.remedy : ''}`));
        return lines.join('\n');
      }, 'Диагностика звонков');
    });
    $('calls-settings-form').addEventListener('submit', (e) => {
      e.preventDefault(); tap();
      const body = {
        enabled: $('calls-enabled').checked,
        barge_in: $('calls-barge-in').checked,
        greeting: $('calls-greeting').value.trim(),
        answering_machine: $('calls-answering-enabled').checked,
        answer_allow_unknown: $('calls-allow-unknown').checked,
        answer_ring_delay_s: Math.min(60, Math.max(5, Number($('calls-ring-delay').value) || 12)),
        answer_greeting: $('calls-answer-greeting').value.trim(),
      };
      callsAct(() => api('PUT', '/calls/settings', body), 'Сохранение настроек');
    });
    $('calls-answering-start').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/answering/start', {}), 'Запуск автоответчика'); });
    $('calls-answering-stop').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/answering/stop', {}), 'Остановка автоответчика'); });
    $('calls-peer-form').addEventListener('submit', (e) => {
      e.preventDefault(); tap();
      const raw = $('calls-peer-input').value.trim();
      if (!raw) { toast('Укажите @username или ID собеседника'); return; }
      const body = { confirm: $('calls-peer-confirm').checked };
      if (/^\d+$/.test(raw)) body.user_id = Number(raw);
      else body.username = raw.replace(/^@/, '');
      callsAct(() => api('PUT', '/calls/peer', body), 'Выбор собеседника');
    });
    $('calls-peer-clear').addEventListener('click', () => { tap(); callsAct(() => api('DELETE', '/calls/peer'), 'Сброс собеседника'); });
    $('calls-save-creds').addEventListener('click', () => {
      tap();
      const api_id = Number($('calls-api-id').value.trim());
      const api_hash = $('calls-api-hash').value.trim();
      if (!api_id || api_hash.length !== 32) { toast('Введите числовой API ID и 32-символьный API Hash'); return; }
      callsAct(async () => {
        const res = await api('POST', '/calls/credentials', { api_id, api_hash });
        $('calls-api-hash').value = '';
        return res;
      }, 'Сохранение API ключей');
    });
    $('calls-send-code').addEventListener('click', () => {
      tap();
      const phone = $('calls-phone').value.trim();
      if (!phone) { toast('Введите номер телефона (+...)'); return; }
      callsAct(() => api('POST', '/calls/login/start', { phone }), 'Запрос кода входа Telegram');
    });
    $('calls-submit-code').addEventListener('click', () => {
      tap();
      const code = $('calls-code').value.trim();
      if (!code) { toast('Введите код из Telegram'); return; }
      callsAct(async () => {
        const res = await api('POST', '/calls/login/code', { code });
        $('calls-code').value = '';
        return res;
      }, 'Вход по коду');
    });
    $('calls-submit-2fa').addEventListener('click', () => {
      tap();
      const password = $('calls-2fa').value;
      if (!password) { toast('Введите облачный пароль 2FA'); return; }
      callsAct(async () => {
        const res = await api('POST', '/calls/login/password', { password });
        $('calls-2fa').value = '';
        return res;
      }, 'Подтверждение 2FA');
    });
    $('calls-logout-btn').addEventListener('click', () => { tap(); callsAct(() => api('POST', '/calls/logout', {}), 'Выход из аккаунта звонков'); });
  }

  /* ================= обменник: тот же помощник, что в пульте ================= */
  let swapQuote = null;
  async function swapSend(text) {
    if (!text) return;
    $('swap-reply').textContent = '…';
    $('swap-confirm').hidden = true;
    try {
      const r = await api('POST', '/exchange', { text });
      $('swap-reply').textContent = r.reply;
      swapQuote = r.quote_id;
      $('swap-confirm').hidden = !swapQuote;
      if (r.stopped) toast('Действует СТОП: записи заблокированы');
      if (/^Сделка|отменена/.test(r.reply)) loadSwapDeals();
    } catch (err) { $('swap-reply').textContent = 'Ошибка: ' + err.message; }
  }
  async function loadSwapDeals() {
    const box = $('swap-deals');
    try {
      const { deals } = await api('GET', '/exchange/deals');
      box.textContent = '';
      if (!deals.length) { box.appendChild(el('p', 'muted', 'Сделок пока нет.')); return; }
      deals.forEach((d) => {
        const row = el('div', 'swap-deal' + (d.status === 'reversed' ? ' reversed' : ''));
        row.appendChild(el('b', '', `D-${d.id}`));
        row.appendChild(el('span', '', ` ${d.give_amount} ${d.give_ccy} → ${d.get_amount} ${d.get_ccy}`));
        row.appendChild(el('small', 'muted', ` ${String(d.confirmed_at || '').slice(0, 16).replace('T', ' ')} UTC${d.status === 'reversed' ? ' · отменена' : ''}`));
        box.appendChild(row);
      });
    } catch (err) { box.textContent = 'Ошибка: ' + err.message; }
  }
  $('swap-form').addEventListener('submit', (e) => { e.preventDefault(); const v = $('swap-input').value.trim(); tap(); swapSend(v); });
  $('swap-quick').addEventListener('click', (e) => { const b = e.target.closest('[data-cmd]'); if (b) { tap(); swapSend(b.dataset.cmd); } });
  $('swap-confirm').addEventListener('click', () => {
    if (!swapQuote) return;
    if (!confirm(`Обмен по ${swapQuote} действительно выполнен? Сделка будет записана.`)) return;
    tap(); swapSend(`обмен выполнен ${swapQuote}`);
  });
  $('swap-deals-btn').addEventListener('click', () => { tap(); loadSwapDeals(); });
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
  /* Плавность (владелец 19.10: «плохо листается при работе ответа и подлагивает»). Было: каждый кусочек ответа
     перерисовывал весь ответ и дёргал список вниз (scroll-behavior: smooth перезапускался на каждом). Теперь:
     куски копятся и выводятся один раз за кадр (requestAnimationFrame), готовые абзацы добавляются, хвост дописывается
     в один текстовый узел; вниз прокручиваем только если владелец и так внизу; иначе — кнопка «вниз». */
  const RM = (() => { try { return window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null; } catch { return null; } })();
  const reducedMotion = () => !!(RM && RM.matches);
  const syncMotion = () => document.documentElement.classList.toggle('reduce-motion', reducedMotion());
  syncMotion(); try { if (RM && RM.addEventListener) RM.addEventListener('change', syncMotion); } catch { /* old Safari */ }
  const NEAR_PX = 72;
  const SC = { stick: true, touching: false, glide: 0, raf: 0 };
  const nearBottom = () => msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight <= NEAR_PX;
  function showDown(on) {
    const d = $('btn-down'); if (d && d.hidden === on) d.hidden = !on;
    const sd = $('btn-scroll-down'); if (sd && sd.hidden === on) sd.hidden = !on;
  }
  function stickNow() {                   // inside a frame, after that frame's DOM writes: one read, at most one write
    if (!SC.stick || SC.touching) return;
    const bottom = msgs.scrollHeight - msgs.clientHeight;
    if (msgs.scrollTop < bottom - 1) msgs.scrollTop = bottom;
  }
  function autoScroll(force) {            // new message, tool card, finished answer: follow only if already following
    if (force) { SC.stick = true; showDown(false); }
    if (SC.raf) return;
    SC.raf = requestAnimationFrame(() => { SC.raf = 0; stickNow(); });
  }
  msgs.addEventListener('scroll', () => {
    if (SC.glide) { if (nearBottom()) { clearTimeout(SC.glide); SC.glide = 0; SC.stick = true; showDown(false); } return; }
    SC.stick = nearBottom(); showDown(!SC.stick);
  }, { passive: true });
  msgs.addEventListener('touchstart', () => { SC.touching = true; if (SC.glide) { clearTimeout(SC.glide); SC.glide = 0; } }, { passive: true });
  const touchEnd = () => { SC.touching = false; SC.stick = nearBottom(); showDown(!SC.stick); };
  msgs.addEventListener('touchend', touchEnd, { passive: true }); msgs.addEventListener('touchcancel', touchEnd, { passive: true });
  msgs.addEventListener('wheel', (e) => { if (e.deltaY < 0) { SC.stick = false; showDown(true); } }, { passive: true });
  const scrollToDown = () => {
    tap(); SC.stick = true; showDown(false);
    const smooth = !reducedMotion();
    if (smooth) { clearTimeout(SC.glide); SC.glide = setTimeout(() => { SC.glide = 0; }, 900); }
    const top = msgs.scrollHeight;
    if (typeof msgs.scrollTo === 'function') msgs.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' }); else msgs.scrollTop = top;
  };
  $('btn-down').addEventListener('click', scrollToDown);
  if ($('btn-scroll-down')) $('btn-scroll-down').addEventListener('click', scrollToDown);

  function clearMessages() { msgs.replaceChildren($('chat-empty')); $('chat-empty').hidden = false; SC.stick = true; showDown(false); }
  function addUser(text) { $('chat-empty').hidden = true; const m = el('div', 'msg user'); m.appendChild(el('div', 'bubble', text)); msgs.appendChild(m); autoScroll(true); return m; }
  function addBot(prompt) {
    $('chat-empty').hidden = true;
    const m = el('div', 'msg bot'); const tools = el('div', 'tools'); const ans = el('div', 'answer'); const meta = el('div', 'meta');
    m.append(tools, ans, meta); msgs.appendChild(m); autoScroll();
    return { m, tools, ans, meta, cards: {}, text: '', prompt: prompt || '', committed: 0, live: null, raf: 0 };
  }
  function typing(b, on, label) {
    const old = b.ans.querySelector('.typing');
    if (on && !b.text) {
      if (old) return;
      const t = el('div', 'typing'); t.setAttribute('role', 'status'); t.setAttribute('aria-label', label || 'Bossman думает…');
      t.append(el('i'), el('i'), el('i')); if (label) t.appendChild(el('span', 'typing-label', label)); b.ans.appendChild(t);
    } else if (old) old.remove();
  }
  /* ---- поток ответа: накопить -> один вывод за кадр ---- */
  function schedulePaint(b) { if (!b.raf) b.raf = requestAnimationFrame(() => paint(b)); }
  function stopPaint(b) { if (b.raf) { cancelAnimationFrame(b.raf); b.raf = 0; } }
  // where the streamed text may be cut into finished blocks: after a blank-line run outside ``` code, once the next
  // character has arrived (so the run is complete). renderBlocks(head) + renderBlocks(rest) == renderBlocks(all) there.
  function commitPoint(text, from) {
    const re = /```|\n{2,}(?=[^\n])/g; re.lastIndex = from; let open = false; let cut = from; let m;
    while ((m = re.exec(text))) { if (m[0] === '```') open = !open; else if (!open) cut = m.index + m[0].length; }
    return cut;
  }
  function paint(b) {
    b.raf = 0; if (b.ended) return;
    if (b.full) { rich(b.ans, b.text); stickNow(); return; }      // rare: server replaced the text mid-stream
    if (!b.live) {
      const t = b.ans.querySelector('.typing'); const live = el('p', 'live caret'); b.liveText = document.createTextNode('');
      live.appendChild(b.liveText); b.live = live; b.committed = 0;
      if (t) t.replaceWith(live); else b.ans.appendChild(live);
    }
    const text = b.text; const cut = commitPoint(text, b.committed); const tail = text.slice(cut);
    if (cut > b.committed) {
      const f = document.createDocumentFragment(); renderBlocks(f, text.slice(b.committed, cut)); b.committed = cut;
      b.liveText.data = tail; b.ans.insertBefore(f, b.live);
    } else if (tail !== b.liveText.data) {
      if (tail.startsWith(b.liveText.data)) b.liveText.appendData(tail.slice(b.liveText.data.length)); else b.liveText.data = tail;
    }
    stickNow();
  }
  function endStream(b, finalText) {     // the stream ended: format the tail once (no full re-render when it matches)
    stopPaint(b);
    if (b.live && !b.full && finalText === b.text) {
      const f = document.createDocumentFragment(); renderBlocks(f, b.text.slice(b.committed)); b.live.replaceWith(f);
    } else rich(b.ans, finalText);
    b.live = null; b.full = false; b.ended = true;
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
  function rich(target, text) { target.replaceChildren(); renderBlocks(target, text); }
  function renderBlocks(target, text) {
    const parts = String(text || '').split(/```[^\n]*\n?/);
    parts.forEach((part, i) => {
      if (i % 2 === 1) { const pre = el('pre'); pre.appendChild(el('code', '', part.replace(/\n$/, ''))); target.appendChild(pre); return; }
      part.split(/\n{2,}/).forEach((para) => {
        if (!para.trim()) return;
        const lines = para.replace(/^\n+|\n+$/g, '').split('\n');
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
      autoScroll();
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
    row.append(no, yes); box.appendChild(row); b.tools.appendChild(box); b.cards['ap' + ev.id] = box; autoScroll();
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
      case 'run.answer_delta':
        if (!ev.text) break;
        b.text += ev.text;
        if (!live) { rich(b.ans, b.text); break; }
        if (!b.first) b.first = performance.now();
        schedulePaint(b); break;                                   // no DOM work here: one paint per frame
      case 'run.answer_reset':
        stopPaint(b); b.text = ''; b.live = null; b.full = false; b.committed = 0; b.ans.replaceChildren();
        if (live) typing(b, true); break;
      case 'run.assistant_message':
        if (ev.model) b.model = ev.model;
        if (ev.text && ev.text !== b.text) {
          b.text = ev.text;
          if (live) { b.full = true; schedulePaint(b); } else rich(b.ans, b.text);
        }
        break;
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
        const desc = photoDesc(turn.text);
        addUser(desc != null ? `${PHOTO_MARK} ${desc}` : turn.text); const b = addBot(turn.text);
        b.taskId = turn.task_id; if (desc != null) b.photo = { desc };
        if (turn.status && !['completed', 'failed', 'stopped', 'blocked', 'missing'].includes(turn.status)) { follow(turn.task_id, b); continue; }
        b.ended = true;
        if (b.photo) { b.text = turn.result || ''; restorePhotoTurn(b, turn); continue; }
        if (turn.result) { b.text = turn.result; rich(b.ans, turn.result); } else if (turn.error) { b.ans.classList.add('error'); b.ans.textContent = turn.error; }
        b.meta.textContent = `задача #${turn.task_id} · ${stRu(turn.status)}`;
        if (turn.result) msgActions(b);
        if (turns.indexOf(turn) >= turns.length - 6) {
          api('GET', `/tasks/${turn.task_id}/history`).then((h) => (h.events || []).forEach((ev) => { if (ev.kind.startsWith('run.tool') || ev.kind.startsWith('approval') || ev.kind === 'tool.denied') applyEvent(b, ev, false); })).catch(() => {});
        }
      }
      autoScroll(true);
    } catch (err) { if (err.status === 404) { store.set('thread', null); S.threadId = null; } }
  }

  /* ================= отправка ================= */
  const input = $('input');
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !('ontouchstart' in window)) { e.preventDefault(); $('composer').requestSubmit(); } });
  document.querySelectorAll('[data-suggest]').forEach((c) => c.addEventListener('click', () => { input.value = c.dataset.suggest; input.dispatchEvent(new Event('input')); input.focus(); }));
  $('composer').addEventListener('submit', async (e) => {
    e.preventDefault(); const text = input.value.trim(); if (!text || S.sending) return;
    tap(); input.value = ''; input.style.height = 'auto'; const photo = S.photoMode; setPhotoMode(false); await send(text, { photo });
  });
  $('btn-stop').addEventListener('click', async () => { tap(); if (!S.threadId) return; try { await api('POST', `/threads/${encodeURIComponent(S.threadId)}/stop`); toast('Остановлено'); } catch (err) { toast(err.message); } });
  function busy(on) { S.sending = on; $('btn-send').hidden = on; $('btn-stop').hidden = !on; }

  /* ---- «Фото по описанию» (владелец 19.10): модель чата (по умолчанию Gemma) пишет промпт, владелец видит его и жмёт
     «Запустить» или «Изменить»; перед постановкой в очередь модель чата выгружается из памяти ПК (keep_alive 0), чтобы
     видеокарта/ОЗУ ушли генератору. Фото ставится тем же Прямым генератором, что и вкладка «Фото» (/photo/jobs). ---- */
  function setPhotoMode(on) {
    S.photoMode = !!on; const b = $('btn-photo-mode');
    b.setAttribute('aria-pressed', on ? 'true' : 'false'); b.classList.toggle('on', !!on);
    input.placeholder = on ? 'Опишите картинку — модель чата напишет промпт' : 'Напишите Bossman…';
  }
  $('btn-photo-mode').addEventListener('click', () => { tap(); setPhotoMode(!S.photoMode); if (S.photoMode) toast('Фото по описанию: опишите картинку и отправьте'); input.focus(); });
  const PHOTO_RE = /^\s*(?:(?:сгенерируй|создай|сделай|нарисуй)\s+(?:мне\s+)?(?:фотографию|фото|фотку|картинку|изображение|рисунок)|нарисуй)(?=[\s:,.!-]|$)[\s:,.!-]*/i;
  const PHOTO_MARK = 'Фото по описанию:';
  const PHOTO_TASK = '[задание для модели]';
  const PHOTO_RULES = `${PHOTO_TASK} Напиши ОДИН промпт для локального генератора картинок по этому описанию: на английском, 30–120 слов — главный объект, детали, окружение, свет, ракурс, стиль. Смысл описания не меняй и ничего по сути не добавляй. Ответь одной строкой: PROMPT: <промпт>`;
  const photoAsk = (desc) => `${PHOTO_MARK} ${desc}\n\n${PHOTO_RULES}`;
  function photoDesc(text) {
    const s = String(text || ''); if (!s.startsWith(PHOTO_MARK)) return null;
    const i = s.indexOf('\n\n' + PHOTO_TASK); return s.slice(PHOTO_MARK.length, i < 0 ? undefined : i).trim();
  }
  function parsePrompt(text) {
    const s = String(text || '').replace(/```[^\n]*\n?/g, '\n');
    const m = /PROMPT\s*[:：]\s*([\s\S]*)/i.exec(s);
    const p = (m ? m[1] : s).trim().split(/\n\s*\n/)[0] || '';
    return p.replace(/\s+/g, ' ').trim().replace(/^["'«“`*]+|["'»”`*]+$/g, '').trim().slice(0, 2000);
  }
  const chatPhotoKey = (taskId) => 'chatPhoto.' + taskId;
  const chatPhotoModel = () => S.photoModels.find((x) => x.id === 'z-image-turbo' && x.available) || S.photoModels.find((x) => x.available && !x.requires_image);

  async function send(text, opts = {}) {
    let pm = PHOTO_RE.exec(text);
    if (pm && /^(экран|скрин|рабоч|окн)/i.test(text.slice(pm[0].length))) pm = null;   // «сделай фото экрана» = the PC operator
    if (opts.photo || pm) return photoFromChat((pm ? text.slice(pm[0].length) : text).replace(/^мне\s+/i, '').trim());
    if (S.agentId == null) { toast('Выберите локальную модель'); return; }
    busy(true); addUser(text); const b = addBot(text); typing(b, true); b.t0 = performance.now();
    await ask(b, text);
  }
  async function ask(b, text) {
    try {
      if (!S.threadId) { const t = await api('POST', '/threads', { title: text.slice(0, 60) }); S.threadId = t.id; store.set('thread', t.id); }
      const r = await api('POST', `/threads/${encodeURIComponent(S.threadId)}/send`, { text, agent_id: S.agentId, client_request_id: 'm-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10) });
      if (r.admission && r.admission.ok === false) { finish(b, null, r.admission.reason || 'Bossman не принял задачу', 'blocked'); return; }
      b.model = r.model && (r.model.alias || r.model.id); b.agentId = S.agentId;
      follow(r.task.id, b);
    } catch (err) { finish(b, null, err.message, 'error'); }
  }
  function closeStream() { if (S.es) { S.es.close(); S.es = null; } if (S.poll) { clearInterval(S.poll); S.poll = null; } }
  function follow(taskId, b) {
    closeStream(); busy(true); b.taskId = taskId; b.m.setAttribute('aria-busy', 'true');
    typing(b, true, b.photo ? 'Модель чата пишет промпт…' : ''); b.t0 = b.t0 || performance.now(); S.task = taskId;
    const es = new EventSource(`/m/api/tasks/${taskId}/events?after=0`); S.es = es;
    es.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch { return; }
      applyEvent(b, ev, true);
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
    busy(false); typing(b, false); b.m.setAttribute('aria-busy', 'false');
    if (result) { endStream(b, result); b.text = result; }
    else {
      if (b.text) endStream(b, b.text); else { stopPaint(b); b.ended = true; }
      if (error && !b.text) { b.ans.classList.add('error'); b.ans.textContent = error; }
    }
    const total = b.t0 ? (performance.now() - b.t0) / 1000 : null; const first = b.first && b.t0 ? (b.first - b.t0) / 1000 : null;
    b.meta.replaceChildren(el('span', 'badge-local', 'локально'), el('span', '', [b.model, taskId ? `#${taskId}` : '', status && status !== 'completed' ? stRu(status) : '', total ? `${total.toFixed(1)} с` : '', first ? `первый токен ${first.toFixed(1)} с` : ''].filter(Boolean).join(' · ')));
    if (b.photo) photoAfterPrompt(b, status, error); else if (b.text) msgActions(b);
    loadThreads(); autoScroll();
  }

  /* ---- под ответом: «Копировать» и «Повторить» ---- */
  async function copyText(text) {
    try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); toast('Скопировано'); return; } } catch { /* fallback below */ }
    try {
      const t = el('textarea'); t.value = text; t.setAttribute('readonly', ''); t.className = 'offscreen'; document.body.appendChild(t); t.select();
      const ok = typeof document.execCommand === 'function' && document.execCommand('copy'); t.remove(); toast(ok ? 'Скопировано' : 'Не удалось скопировать');
    } catch { toast('Не удалось скопировать'); }
  }
  function actButton(name, label, aria, onClick, extraCls = '') {
    const btn = el('button', extraCls ? `act ${extraCls}` : 'act'); btn.type = 'button'; btn.setAttribute('aria-label', aria);
    const ic = icon(name); ic.setAttribute('aria-hidden', 'true'); btn.append(ic, el('span', '', label));
    btn.addEventListener('click', onClick); return btn;
  }
  function msgActions(b) {
    if (b.actions) return;
    const row = el('div', 'msg-actions');
    row.appendChild(actButton('copy', 'Копировать', 'Копировать ответ', () => { tap(); copyText(b.text); }, 'msg-copy'));
    if (b.prompt) row.appendChild(actButton('refresh', 'Повторить', 'Повторить запрос', () => { if (S.sending) { toast('Дождитесь ответа'); return; } tap(); send(b.prompt); }, 'msg-retry'));
    b.m.appendChild(row); b.actions = row;
  }

  /* ---- фото по описанию ---- */
  async function photoFromChat(desc) {
    if (!desc) { toast('Опишите, что нарисовать'); return; }
    busy(true); addUser(`${PHOTO_MARK} ${desc}`); const b = addBot(); b.photo = { desc }; b.t0 = performance.now();
    if (S.agentId == null) { busy(false); b.ended = true; photoCard(b, desc, 'Модель чата не выбрана — в промпт пойдёт ваше описание как есть.'); return; }
    typing(b, true, 'Модель чата пишет промпт…'); b.m.setAttribute('aria-busy', 'true');
    await ask(b, photoAsk(desc));
  }
  function photoAfterPrompt(b, status, error) {
    const prompt = !error ? parsePrompt(b.text) : '';
    b.ans.classList.remove('error');
    if (prompt) { photoCard(b, prompt, ''); return; }
    photoCard(b, b.photo.desc, error ? `Модель чата не написала промпт: ${error}. Можно запустить с вашим описанием или изменить его.`
      : 'Модель чата не написала промпт — можно запустить с вашим описанием или изменить его.');
  }
  function photoCard(b, prompt, note) {
    const card = el('div', 'pcard'); card.setAttribute('role', 'group'); card.setAttribute('aria-label', 'Промпт для фото');
    const head = el('div', 'pcard-head'); const ic = icon('photo'); ic.setAttribute('aria-hidden', 'true');
    head.append(ic, el('b', '', 'Промпт для фото'), el('span', 'badge-local', 'на ПК'));
    const text = el('p', 'pcard-text', prompt);
    const area = el('textarea', 'field area pcard-edit'); area.rows = 5; area.hidden = true; area.setAttribute('aria-label', 'Промпт для фото');
    card.append(head, text, area);
    if (note) card.appendChild(el('p', 'pcard-note', note));
    const hint = el('p', 'hint', 'Перед запуском модель чата выгрузится из памяти ПК — видеокарта и ОЗУ уйдут генератору картинок.');
    const row = el('div', 'row'); const edit = el('button', 'btn', 'Изменить'); edit.type = 'button';
    const go = el('button', 'btn primary', 'Запустить'); go.type = 'button'; row.append(edit, go);
    const status = el('div', 'pcard-status'); status.setAttribute('role', 'status');
    card.append(hint, row, status);
    const ui = { card, text, area, edit, go, status, row, hint };
    edit.addEventListener('click', () => {
      tap();
      if (area.hidden) { area.value = text.textContent; area.hidden = false; text.hidden = true; edit.textContent = 'Готово'; area.focus(); }
      else { text.textContent = area.value.trim() || text.textContent; area.hidden = true; text.hidden = false; edit.textContent = 'Изменить'; }
    });
    go.addEventListener('click', () => {
      const p = (area.hidden ? text.textContent : area.value).trim();
      if (!p) { toast('Промпт пустой — напишите, что нарисовать'); return; }
      tap(); launchChatPhoto(b, p, ui);
    });
    b.ans.classList.remove('error'); b.ans.replaceChildren(card); b.card = ui; autoScroll();
    return ui;
  }
  async function launchChatPhoto(b, prompt, ui) {
    if (ui.card.dataset.state === 'starting') return;
    ui.card.dataset.state = 'starting'; ui.go.disabled = ui.edit.disabled = ui.area.disabled = true;
    ui.status.classList.remove('err'); ui.status.textContent = 'Выгружаю модель чата из памяти…';
    let note = ''; const agent = b.agentId != null ? b.agentId : S.agentId;
    if (agent != null) {          // before the job: free the chat model's RAM/VRAM; a failure is said, the photo still goes
      try { const u = await api('POST', '/chat/unload', { agent_id: agent }); note = (u.unloaded || []).length ? 'модель чата выгружена из памяти' : 'модель чата уже не в памяти'; }
      catch (err) { note = `модель чата не выгружена: ${err.message}`; }
    }
    ui.status.textContent = 'Ставлю фото в очередь…';
    let job = null; let m = null;
    try {
      await ensurePhotoModels(); m = chatPhotoModel(); if (!m) throw new Error('нет доступной фото-модели');
      const [w, h] = (m.default_resolution || '512x512').split('x');
      job = await api('POST', '/photo/jobs', { model: m.id, prompt, resolution: `${w}x${h}` });
    } catch (err) {
      ui.card.dataset.state = ''; ui.go.disabled = ui.edit.disabled = ui.area.disabled = false;
      ui.status.classList.add('err'); ui.status.textContent = `Не удалось запустить: ${err.message}${note ? ' · ' + note : ''}`; return;
    }
    ui.text.textContent = prompt; ui.area.hidden = true; ui.text.hidden = false; ui.row.remove(); ui.hint.remove();
    ui.card.dataset.state = 'queued'; ui.status.textContent = [m.label, note].filter(Boolean).join(' · ');
    if (b.taskId != null) store.set(chatPhotoKey(b.taskId), JSON.stringify({ job_id: job.job_id, prompt }));
    watchChatPhoto(b, ui, job, m.label);
  }
  async function watchChatPhoto(b, ui, job, label) {
    const prog = el('div', 'card progress'); ui.card.appendChild(prog); autoScroll();
    try {
      const final = await trackJob(job, prog);
      if (final.status !== 'completed') return;
      const wrap = el('div', 'inline-photo'); wrap.dataset.job = final.job_id; const img = el('img');
      img.alt = final.raw_prompt || 'фото по описанию'; img.addEventListener('load', () => autoScroll()); img.addEventListener('click', () => openViewer(final));
      img.src = `/m/api/photo/jobs/${final.job_id}/file`; wrap.appendChild(img); prog.replaceWith(wrap);
      b.meta.replaceChildren(el('span', 'badge-local', 'на ПК'), el('span', '', [label || final.model, fmtS(final.elapsed_s), 'Прямой генератор'].filter(Boolean).join(' · ')));
      autoScroll();
    } catch (err) { if (err && err.status !== 401) ui.card.appendChild(el('div', 'pcard-status err', `Фото: ${err.message}`)); }
  }
  function restorePhotoTurn(b, turn) {      // thread reopened: the card again; the photo if it was launched on this phone
    let saved = null; try { saved = JSON.parse(store.get(chatPhotoKey(turn.task_id)) || 'null'); } catch { saved = null; }
    const prompt = (saved && saved.prompt) || parsePrompt(turn.result) || b.photo.desc;
    const ui = photoCard(b, prompt, turn.result || saved ? '' : `Модель чата не написала промпт${turn.error ? ': ' + turn.error : ''}.`);
    b.meta.textContent = `задача #${turn.task_id} · ${stRu(turn.status)}`;
    if (!saved || !saved.job_id) return;
    ui.row.remove(); ui.hint.remove();
    api('GET', `/photo/jobs/${encodeURIComponent(saved.job_id)}`).then((j) => watchChatPhoto(b, ui, j))
      .catch((err) => { ui.card.appendChild(el('div', 'pcard-status err', err.status === 404 ? 'Фото удалено с ПК' : `Фото: ${err.message}`)); });
  }

  /* ================= одобрения (значок на вкладке «Панель») ================= */
  async function refreshApprovals() {
    try { const r = await api('GET', '/approvals'); const n = (r.items || []).length; const bd = $('badge-approvals'); bd.hidden = !n; bd.textContent = n; } catch { /* */ }
  }

  /* ================= В пульт: по кнопке, один файл (владелец 18.10: автодоставки нет) ================= */
  async function toPult(kind, jobId, btn) {
    if (btn && btn.disabled) return; tap(); if (btn) btn.disabled = true;
    try { await api('POST', `/${kind}/jobs/${jobId}/to-pult`); toast('Отправлено в пульт'); }
    catch (err) { toast(err && err.message ? err.message : 'Не удалось отправить в пульт'); }
    finally { if (btn) btn.disabled = false; }
  }
  function pultButton(kind, jobId) {
    const b = el('button', 'btn small', 'В пульт'); b.type = 'button'; b.setAttribute('aria-label', 'Отправить в пульт');
    b.addEventListener('click', (e) => { e.stopPropagation(); toPult(kind, jobId, b); }); return b;
  }

  /* Удалить навсегда с ПК: подтверждение -> POST /{kind}/jobs/{id}/delete -> элемент исчезает без перезагрузки */
  function confirmDelete() {
    return new Promise((resolve) => {
      const wrap = el('div', 'sheet confirm'); wrap.setAttribute('role', 'alertdialog'); wrap.setAttribute('aria-modal', 'true');
      const card = el('div', 'confirm-card'); card.appendChild(el('p', '', 'Удалить навсегда с ПК? Это нельзя отменить.'));
      const row = el('div', 'row'); const no = el('button', 'btn', 'Отмена'); no.type = 'button'; no.dataset.act = 'cancel';
      const yes = el('button', 'btn destroy', 'Удалить'); yes.type = 'button'; yes.dataset.act = 'confirm';
      const done = (v) => { wrap.remove(); resolve(v); };
      no.addEventListener('click', (e) => { e.stopPropagation(); done(false); }); yes.addEventListener('click', (e) => { e.stopPropagation(); done(true); });
      wrap.addEventListener('click', (e) => { if (e.target === wrap) done(false); });
      row.append(no, yes); card.appendChild(row); wrap.appendChild(card); document.body.appendChild(wrap); no.focus();
    });
  }
  async function deleteForever(kind, jobId, btn) {
    if (btn && btn.disabled) return; tap();
    if (!(await confirmDelete())) return;
    if (btn) btn.disabled = true;
    try {
      await api('POST', `/${kind}/jobs/${jobId}/delete`);
      document.querySelectorAll(`[data-job="${jobId}"]`).forEach((n) => n.remove());
      if (viewing && viewing.job_id === jobId) { viewing = null; $('viewer').hidden = true; }
      if (vviewing && vviewing.job_id === jobId) { vviewing = null; const v = $('vviewer-video'); try { v.pause(); } catch { /* */ } v.removeAttribute('src'); $('vviewer').hidden = true; }
      toast('Удалено с ПК');
      if (kind === 'photo') { const g = $('gallery'); if (g && !g.children.length) g.appendChild(el('p', 'muted', 'Пока пусто')); loadGallery(); loadQueue(); }
      else { loadVideos(); loadVQueue(); }
    } catch (err) { toast(err && err.message ? err.message : 'Не удалось удалить с ПК'); }
    finally { if (btn) btn.disabled = false; }
  }
  function deleteButton(kind, jobId) {
    const b = el('button', 'btn small danger-text', 'Удалить навсегда'); b.type = 'button'; b.setAttribute('aria-label', 'Удалить навсегда с ПК');
    b.addEventListener('click', (e) => { e.stopPropagation(); deleteForever(kind, jobId, b); }); return b;
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
    loadGallery(); photoStatus(); loadQueue();
  }
  const curModel = () => S.photoModels.find((m) => m.id === $('photo-model').value);
  function onModel() {
    const m = curModel(); if (!m) return; store.set('photoModel', m.id);
    const f = $('photo-flags'); f.replaceChildren();
    if (m.uncensored_label) f.appendChild(el('span', 'badge-uc', 'без цензуры (по метке Bossman)'));
    f.appendChild(el('span', 'badge-local', 'на ПК'));
    if (m.license) f.appendChild(el('span', 'pill', m.license));
    $('ref-block').hidden = !m.accepts_image;
    const cap = maxRefs(m);
    if (cap && S.refs.length > cap) { S.refs.length = cap; S.refFirst = S.refs[0] && S.refs[0].data; toast(cap > 1 ? `Эта модель берёт до ${cap} фото — лишние убраны` : 'Эта модель берёт одно фото — оставлено @1'); }
    S.refWH = S.refs.length ? S.refs[0].wh : null; renderRefs();
    const st = $('photo-steps'); st.min = m.min_steps || 1; st.max = m.max_steps || 50; st.value = m.default_steps || 20; setSteps(st.value);
    $('steps-range').textContent = `(от ${st.min} до ${st.max}, по умолчанию ${m.default_steps || 20})`;
    const base = Number((m.default_resolution || '512x512').split('x')[0]) || 512;
    const sides = [512, 768, 1024, 1280].filter((s) => s >= (m.min_side || 256) && s <= (m.max_side || 1536));
    S.side = sides.includes(base) ? base : sides[0]; S.sides = sides;
    if (S.aspect === 'ref' && !(S.refWH && m.accepts_image)) S.aspect = '1:1';
    if (S.refWH && m.accepts_image) { S.aspect = 'ref'; S.side = sideFor(sides, S.refWH); }
    photoSegs();
  }
  /* owner 17.10: a reference picks format + size by itself — the exact aspect of the photo and the
     largest allowed long side that does not upscale it (the user can still tap another one). */
  const PHOTO_ASPECTS = [['1:1', '1:1'], ['3:4', '3:4'], ['4:3', '4:3'], ['9:16', '9:16'], ['16:9', '16:9']];
  function sideFor(sides, wh) { const long = Math.max(wh.w, wh.h); const ok = sides.filter((s) => s <= long); return ok.length ? ok[ok.length - 1] : sides[0]; }
  const refLabel = (wh) => `как у фото ${wh.w}×${wh.h}`;
  function aspectOf(cur, wh) { return cur === 'ref' && wh ? [wh.w, wh.h] : (cur === 'ref' ? [1, 1] : cur.split(':').map(Number)); }
  function photoSegs() {
    seg('photo-side', S.sides.map((s) => [s, `${s}`]), S.side, (v) => { S.side = v; showTime(); });
    seg('photo-aspect', (S.refWH ? [['ref', refLabel(S.refWH)]] : []).concat(PHOTO_ASPECTS), S.aspect, (v) => { S.aspect = v; showTime(); });
    showTime();
  }
  function imageSize(file) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(file); const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve({ w: img.naturalWidth, h: img.naturalHeight }); };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(null); }; img.src = url;
    });
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
  // «по замеру N генераций» only when Bossman measured >= 3 runs of this model (timing.py label_for), else «примерно»
  const tag = (t) => { const m = t.kind === 'measured' && /^по замеру/.test(t.label || ''); return el('span', m ? 'tag-m' : 'tag-e', t.label || (t.kind === 'measured' ? 'по замеру' : 'оценка')); };
  function setSteps(v) {
    const st = $('photo-steps'); const n = Math.min(Number(st.max), Math.max(Number(st.min), Math.round(Number(v)) || Number(st.min)));
    st.value = n; $('steps-val').textContent = n; showTime();
  }
  function showTime() {
    const m = curModel(); const box = $('photo-time'); if (!m) { box.replaceChildren(); return; }
    const res = resolution(m); const steps = Number($('photo-steps').value); const t = m.timing || {}; const p = timeParts(m, steps, res);
    const b = el('b', '', `≈ ${fmtDur(p.load + p.run)}`);
    const warm = t.load_s_warm != null ? ` · следующая такая же в очереди: загрузка ≈ ${fmtDur(t.load_s_warm)}` : '';
    const note = el('span', 'muted', `${res.replace('x', '×')} · ${steps} шаг. · загрузка ${fmtDur(p.load)} + шаги ${fmtDur(p.run)}${warm}`);
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
    const [a, c] = aspectOf(S.aspect, S.refWH); const k = m.side_multiple || 16; const max = m.max_side || 1536; const min = m.min_side || 256;
    let w = S.side; let h = S.side;
    if (a > c) h = S.side * c / a; else if (c > a) w = S.side * a / c;
    const r = (v) => Math.min(max, Math.max(min, Math.round(v / k) * k));
    return `${r(w)}x${r(h)}`;
  }
  $('photo-model').addEventListener('change', onModel);
  $('photo-steps').addEventListener('input', () => setSteps($('photo-steps').value));
  $('steps-minus').addEventListener('click', () => { tap(); setSteps(Number($('photo-steps').value) - 1); });
  $('steps-plus').addEventListener('click', () => { tap(); setSteps(Number($('photo-steps').value) + 1); });
  /* ---- @-references, Higgsfield-style (owner 17.10): every attached photo is @1, @2, @3 (+ an optional
     name like @лицо); «лицо с @1, одежда с @2». Bossman rewrites the tags into the model's own words
     (Qwen Edit 2509: «Picture 1», Qwen 2.1: «<image1>»), photo order = number order. Unknown tag: refused here.
     Same grammar as bcc/direct_gen/ref_tags.py (no lookbehind: older iOS Safari cannot parse it). ---- */
  const TAG_RE = /@@|(^|[^0-9A-Za-zА-Яа-яЁё_.@])@(\d{1,2}(?![0-9A-Za-zА-Яа-яЁё_])|[0-9A-Za-zА-Яа-яЁё_-]{1,24})/g;
  const NAME_RE = /^[0-9A-Za-zА-Яа-яЁё_-]{1,24}$/;
  const PARTIAL_RE = /(^|[^0-9A-Za-zА-Яа-яЁё_.@])@([0-9A-Za-zА-Яа-яЁё_-]{0,24})$/;
  const maxRefs = (m) => (m ? (m.max_refs || (m.accepts_image ? 1 : 0)) : 0);
  function tagIndex(tag, refs) {
    if (/^\d+$/.test(tag)) { const n = Number(tag); return n >= 1 && n <= refs.length ? n : 0; }
    const low = tag.toLowerCase(); return refs.findIndex((r) => r.name && r.name.toLowerCase() === low) + 1;
  }
  function checkTags(text, refs) {
    const bad = [];
    for (const m of text.matchAll(TAG_RE)) if (m[2] && !tagIndex(m[2], refs) && !bad.includes('@' + m[2])) bad.push('@' + m[2]);
    if (!bad.length) return '';
    const can = refs.map((r, i) => `@${i + 1}` + (r.name ? ` (@${r.name})` : '')).join(', ') || 'нет прикреплённых фото';
    return `Нет такой отсылки: ${bad.join(', ')}. Можно: ${can}. Знак @ как текст: @@`;
  }
  const retag = (text, fn) => text.replace(TAG_RE, (all, pre, tag) => { if (tag == null) return all; const n = fn(tag); return n == null ? all : `${pre}@${n}`; });
  function insertAt(area, start, end, text) { area.value = area.value.slice(0, start) + text + area.value.slice(end); const c = start + text.length; area.focus(); area.setSelectionRange(c, c); }
  function tagSuggest(area, box, getRefs) {
    const show = () => {
      const refs = getRefs(); const pos = area.selectionStart == null ? area.value.length : area.selectionStart;
      const m = PARTIAL_RE.exec(area.value.slice(0, pos)); box.replaceChildren();
      if (!m || !refs.length) { box.hidden = true; return; }
      const q = m[2].toLowerCase();
      refs.forEach((r, i) => {
        const byName = !!(q && r.name && r.name.toLowerCase().startsWith(q) && !/^\d/.test(q));
        if (q && !byName && !String(i + 1).startsWith(q)) return;
        const b = el('button', 'tag-chip'); b.type = 'button'; b.setAttribute('role', 'option');
        if (r.thumb) { const im = el('img'); im.src = r.thumb; im.alt = ''; b.appendChild(im); }
        b.appendChild(el('span', '', `@${i + 1}` + (r.name ? ` · @${r.name}` : '')));
        b.addEventListener('pointerdown', (e) => e.preventDefault());      // keep the keyboard open
        b.addEventListener('click', () => { tap(); insertAt(area, pos - m[2].length - 1, pos, `@${byName ? r.name : i + 1} `); box.hidden = true; });
        box.appendChild(b);
      });
      box.hidden = !box.children.length;
    };
    area.addEventListener('input', show); area.addEventListener('click', show);
    area.addEventListener('keyup', (e) => { if (String(e.key).startsWith('Arrow')) show(); });
    area.addEventListener('blur', () => setTimeout(() => { box.hidden = true; }, 200));
    return show;
  }
  tagSuggest($('photo-prompt'), $('photo-tags'), () => S.refs.map((r) => ({ thumb: r.data, name: r.name })));
  function renderRefs() {
    const row = $('refs-row'); row.querySelectorAll('.ref-thumb').forEach((n) => n.remove());
    const m = curModel(); const cap = maxRefs(m);
    S.refs.forEach((r, i) => {
      const t = el('span', 'ref-thumb'); const im = el('img', 'ref-preview'); im.src = r.data; im.alt = `фото @${i + 1}`;
      im.addEventListener('click', () => {
        tap(); const a = $('photo-prompt'); const p = a.selectionStart == null ? a.value.length : a.selectionStart;
        insertAt(a, p, p, `${p && !/\s$/.test(a.value.slice(0, p)) ? ' ' : ''}@${r.name || i + 1} `);
      });
      const tag = el('button', 'ref-tag', r.name ? `@${i + 1}·${r.name}` : `@${i + 1}`); tag.type = 'button';
      tag.setAttribute('aria-label', `Имя для @${i + 1}`); tag.addEventListener('click', () => renameRef(i));
      const x = el('button', 'ref-x'); x.type = 'button'; x.setAttribute('aria-label', `Убрать @${i + 1}`); x.appendChild(icon('close'));
      x.addEventListener('click', () => { tap(); removeRef(i); });
      t.append(im, tag, x); row.appendChild(t);
    });
    $('ref-add').hidden = cap > 1 && S.refs.length >= cap;
    $('ref-label').textContent = S.refs.length ? (S.refsUsed ? 'Новое фото' : (cap > 1 ? 'Ещё фото' : 'Заменить')) : (m && m.requires_image ? 'Выбрать фото (обязательно)' : 'Выбрать из «Фото»');
    const fl = !!(S.refs.length && m && m.accepts_image);   // FaceLock only makes sense with a reference attached
    $('facelock-row').hidden = !fl; $('facelock-hint').hidden = !fl;
    $('ref-title').textContent = cap > 1 ? `Фото-референсы: до ${cap} (${S.refs.length}/${cap})` : 'Фото-референс';
    $('ref-hint').textContent = cap > 1
      ? 'В промпте ссылайтесь на фото: «лицо с @1, одежда с @2, фон с @3». Нажмите на фото — тег вставится; на тег — дать имя (@лицо). Формат и размер берутся с @1.'
      : 'Эта модель берёт одно фото; в промпте оно — @1.';
  }
  function refsChanged() {
    const first = S.refs.length ? S.refs[0] : null; const m = curModel();
    S.refWH = first ? first.wh : null;
    if ((first && first.data) !== S.refFirst) {        // the auto format/size follows @1 only
      S.refFirst = first && first.data;
      if (S.refWH && m && m.accepts_image) { S.aspect = 'ref'; S.side = sideFor(S.sides, S.refWH); } else if (S.aspect === 'ref') S.aspect = '1:1';
      photoSegs();
    }
    renderRefs();
  }
  function removeRef(i) {
    const gone = S.refs[i]; S.refs.splice(i, 1); const a = $('photo-prompt'); const n0 = i + 1;
    a.value = retag(a.value, (t) => {
      if (/^\d+$/.test(t)) { const n = Number(t); return n === n0 ? 'убрано' : (n > n0 ? String(n - 1) : null); }
      return gone.name && t.toLowerCase() === gone.name.toLowerCase() ? 'убрано' : null;
    });
    if (/(^|[^0-9A-Za-zА-Яа-яЁё_.@])@убрано/.test(a.value)) toast('Фото убрано: отсылки к нему в промпте стали @убрано — поправьте; номера дальше сдвинуты');
    refsChanged();
  }
  function renameRef(i) {
    tap(); const r = S.refs[i];
    const v = window.prompt(`Имя для @${i + 1}, например «лицо» (буквы, цифры, _ и -). Пусто — без имени.`, r.name || ''); if (v == null) return;
    const name = v.trim().replace(/^@/, '');
    if (name && (!NAME_RE.test(name) || /^\d+$/.test(name))) { toast('Имя: буквы, цифры, _ и -, до 24 знаков, не одни цифры'); return; }
    if (name && S.refs.some((x, j) => j !== i && x.name && x.name.toLowerCase() === name.toLowerCase())) { toast(`Имя @${name} уже занято`); return; }
    const old = r.name; r.name = name;
    if (old && old !== name) { const a = $('photo-prompt'); a.value = retag(a.value, (t) => (t.toLowerCase() === old.toLowerCase() ? (name || String(i + 1)) : null)); }
    renderRefs();
  }
  $('photo-ref').addEventListener('change', async () => {
    const files = Array.from($('photo-ref').files || []); $('photo-ref').value = ''; if (!files.length) return;
    const cap = Math.max(1, maxRefs(curModel()));
    if (S.refsUsed) { S.refs = []; S.refsUsed = false; S.refFirst = null; renderRefs(); }   // the previous job consumed them: a new pick starts a new set
    if (cap === 1) S.refs = [];
    S.refsLoading = true;
    for (const f of files) {
      if (S.refs.length >= cap) { toast(cap > 1 ? `Эта модель берёт не больше ${cap} фото` : 'Эта модель берёт одно фото'); break; }
      try { S.refs.push({ data: await downscale(f, 1536), wh: await imageSize(f), name: '' }); } catch { toast('не удалось прочитать фото'); }
    }
    S.refsLoading = false; refsChanged();
  });
  function downscale(file, max) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file); const img = new Image();
      img.onload = () => { const k = Math.min(1, max / Math.max(img.width, img.height)); const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k); c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url); resolve(c.toDataURL('image/jpeg', 0.92)); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('img')); }; img.src = url;
    });
  }
  /* Submit = hand the job to the PC and be ready again at once. The queue on the PC runs the jobs one after
     another; tracking one job (minutes) must never keep the form locked — that was the "reload the page" bug. */
  const POLL_MS = (window.__bmPollMs | 0) || 1500;
  const FL_REASONS = [['no_face_in_reference', 'на фото-референсе лицо не найдено'], ['no_face_in_result', 'на результате лицо не найдено'],
    ['face_count_mismatch', 'число лиц на фото и в результате разное'], ['multiple_faces_unsupported', 'на фото несколько лиц'],
    ['low_detector_confidence', 'лицо найдено неуверенно'], ['low_landmark_confidence', 'точки лица найдены неуверенно'],
    ['faces_do_not_match_pose', 'ракурс лица сильно отличается от фото'], ['implausible', 'лицо сильно отличается по размеру или наклону'],
    ['detector_unavailable', 'FaceFusion не установлен'], ['face_too_small', 'лицо слишком мелкое'], ['worker_', 'ошибка обработки']];
  function facelockText(j) {
    const f = j && j.facelock; if (!f || !f.requested) return null;
    if (f.status === 'applied') return { ok: true, text: 'FaceLock: лицо с @1 вставлено' };
    if (f.status === 'pending') return { ok: false, text: 'FaceLock: будет применён после генерации' };
    const r = String(f.reason || ''); const hit = FL_REASONS.find(([k]) => r.startsWith(k));
    return { ok: false, text: `FaceLock пропущен: ${hit ? hit[1] : (r || 'причина не указана')}` };
  }
  function facelockBadge(j) { const t = facelockText(j); if (!t) return null; return el('div', 'fl-badge' + (t.ok ? '' : ' skipped'), t.text); }
  function showPhotoResult(card, final) {
    const img = el('img'); img.src = `/m/api/photo/jobs/${final.job_id}/file`; img.alt = final.raw_prompt || ''; img.addEventListener('click', () => openViewer(final)); card.appendChild(img);
    const b = facelockBadge(final); if (b) card.appendChild(b);
    card.dataset.job = final.job_id;
    card.appendChild(pultButton('photo', final.job_id)); card.appendChild(deleteButton('photo', final.job_id));
    loadGallery();
  }
  async function watchPhotoJob(job) {          // background: never awaited by the form
    const card = el('div', 'card progress'); const box = $('photo-progress');
    box.insertBefore(card, box.firstChild); while (box.children.length > 4) box.removeChild(box.lastChild);
    try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch { /* old webview */ }
    try {
      const final = await trackJob(job, card); photoStatus(); loadQueue();
      if (final.status === 'completed') showPhotoResult(card, final);
    } catch (err) { if (err && err.status !== 401) toast(err.message); }
  }
  $('photo-form').addEventListener('submit', async (e) => {
    e.preventDefault(); tap(); const m = curModel(); if (!m || S.photoSending) return;
    if (S.refsLoading) { toast('Фото ещё читается — секунду'); return; }
    if (m.requires_image && !S.refs.length) { toast('Этой модели нужно фото-референс'); return; }
    const seed = $('photo-seed').value.trim();
    const body = { model: m.id, prompt: $('photo-prompt').value.trim(), negative: $('photo-negative').value.trim(), resolution: resolution(m), steps: Number($('photo-steps').value) };
    if (seed) body.seed = Number(seed);
    if (!body.prompt) return;
    const refs = m.accepts_image ? S.refs.slice(0, maxRefs(m)) : [];
    const why = m.accepts_image ? checkTags(body.prompt, refs) : ''; if (why) { toast(why); return; }
    if (refs.length === 1) body.image_b64 = refs[0].data; else if (refs.length) body.images_b64 = refs.map((r) => r.data);
    if (refs.some((r) => r.name)) body.ref_names = refs.map((r) => r.name || '');
    if (refs.length && $('photo-facelock').checked) body.facelock = true;
    if ($('photo-assist') && $('photo-assist').checked) body.assist = true;
    S.photoSending = true; $('photo-go').disabled = true;
    let job = null;
    try { job = await api('POST', '/photo/jobs', body); }
    catch (err) { toast(err.message); }
    finally {
      // ready for the next job at once: button on, file input emptied (choosing the SAME file fires `change` again)
      S.photoSending = false; $('photo-go').disabled = false; $('photo-ref').value = '';
    }
    if (!job) return;
    S.refsUsed = refs.length > 0; renderRefs();    // the next pick of a photo starts a new set; the prompt box stays editable
    loadQueue(); watchPhotoJob(job);
  });
  $('photo-facelock').checked = store.get('facelock') === '1';
  $('photo-facelock').addEventListener('change', () => store.set('facelock', $('photo-facelock').checked ? '1' : '0'));
  const STAGES = { lost: 'Связь потеряна', queued: 'В очереди', loading: 'Загрузка модели', generating: 'Генерация', encoding: 'Сохранение', completed: 'Готово', failed: 'Ошибка', cancelled: 'Отменено' };
  function renderJob(card, j) {
    card.replaceChildren();
    const p = j.progress || {}; const pct = typeof p.percent === 'number' ? p.percent : null;
    const row = el('div', 'row'); row.append(el('span', 'stage', STAGES[j.stage || j.status] || j.stage || j.status));
    const done = ['completed', 'failed', 'cancelled', 'lost'].includes(j.status);
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
    let j = job, bad = 0; renderJob(card, j);
    while (!['completed', 'failed', 'cancelled'].includes(j.status)) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      try { j = await api('GET', `/photo/jobs/${job.job_id}`); bad = 0; renderJob(card, j); }
      catch (err) {
        if (err.status === 401) throw err;
        // a job that is gone (404) or an unreachable PC must not spin forever: stop, say so, the queue list stays live
        if (err.status === 404 || ++bad >= 20) {
          j = Object.assign({}, j, { status: 'lost', stage: 'lost', error: err.status === 404 ? 'задача не найдена на ПК (Bossman перезапускали?)' : 'нет связи с ПК — смотрите «Очередь» ниже' });
          renderJob(card, j); return j;
        }
      }
    }
    return j;
  }
  // ---- Очередь: реальные шаги sd-cli, если есть; иначе честная оценка по времени (подпись «оценка»)
  const fmtMin = (s) => s == null ? '—' : (s < 60 ? `${Math.max(1, Math.round(s))} с` : `${Math.max(1, Math.round(s / 60))} мин`);
  const fmtDone = (s) => s == null ? '' : (s < 60 ? `${Math.round(s)} с` : `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с`);
  // Честный прогресс. Пока движок грузит модель (progress.kind == 'none'), реальный прогресс неизвестен и задача может идти 10+ минут:
  // никакого процента и никакого «осталось» — только «идёт N мин» (растёт) и, если замер настоящий, «обычно ≈ X».
  // С шагами (steps): реальный процент (не выше 99) и «осталось» только из темпа, измеренного на ЭТОЙ задаче.
  const fmtElapsed = (s) => { s = Math.max(0, Math.floor(s || 0)); return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин`; };
  function liveElapsed(sec) { const e = el('span', 'live-elapsed', fmtElapsed(sec)); e.dataset.base = String(sec || 0); e.dataset.at = String(Date.now()); return e; }
  setInterval(() => { document.querySelectorAll('.live-elapsed').forEach((e) => { e.textContent = fmtElapsed(Number(e.dataset.base) + (Date.now() - Number(e.dataset.at)) / 1000); }); }, 1000);
  function renderRunning(j, u, card, stats, unitDefault) {
    const loading = u.indeterminate || (!u.real && (j.stage || j.status) === 'loading');
    const bar = el('div', loading ? 'bar indet' : 'bar'); const i = el('i'); bar.appendChild(i); card.appendChild(bar);
    if (loading) {
      stats.appendChild(el('b', '', u.phase || 'загрузка модели'));
      const started = u.elapsed_s != null ? u.elapsed_s : (j.elapsed_s || 0);
      const t = el('span', ''); t.append('идёт ', liveElapsed(started)); stats.appendChild(t);
      if (u.typical_s != null) stats.appendChild(el('span', 'muted', `обычно ≈ ${fmtMin(u.typical_s)} (оценка по замерам)`));
      return;
    }
    const pct = Math.min(99, Math.max(0, u.percent || 0)); i.style.width = `${Math.max(2, pct)}%`;
    stats.appendChild(el('b', '', `${Math.round(pct)} %${u.real ? '' : ' (оценка)'}`));
    if (u.step != null) stats.appendChild(el('span', '', `${u.unit || unitDefault} ${u.step}/${u.steps}`));
    stats.appendChild(el('span', '', u.phase));
    if (u.remaining_s != null) stats.appendChild(el('span', '', `≈ осталось ${fmtMin(u.remaining_s)}`));
    else if (!u.real) stats.appendChild(el('span', 'muted', 'дольше обычного'));
  }
  function renderQueued(u, stats, withTotal) {
    stats.appendChild(el('b', '', `в очереди №${u.position}`));
    const total = withTotal && u.total_s != null ? `, займёт ≈ ${fmtMin(u.total_s)}` : '';
    stats.appendChild(el('span', '', u.wait_s == null ? `(время ожидания неизвестно: впереди задача без оценки${total})` : `(≈ ждать ${fmtMin(u.wait_s)}${total}, ${u.label || 'оценка'})`));
  }
  function renderQueueItem(j) {
    const u = j.ui || {}; const m = (S.photoModels || []).find((x) => x.id === j.model); const card = el('div', 'qitem ' + (u.state || '')); card.dataset.job = j.job_id;
    const head = el('div', 'row'); head.append(el('span', 'stage', m ? m.label : j.model));
    if (u.state === 'running' || u.state === 'queued') { const c = el('button', 'btn small danger', 'Стоп'); c.type = 'button'; c.addEventListener('click', async () => { try { await api('POST', `/photo/jobs/${j.job_id}/cancel`); loadQueue(); } catch (err) { toast(err.message); } }); head.append(c); }
    card.appendChild(head);
    const stats = el('div', 'stats');
    if (u.state === 'running') {
      renderRunning(j, u, card, stats, 'шаг');
    } else if (u.state === 'queued') {
      renderQueued(u, stats, false);
    } else if (u.state === 'done') {
      stats.appendChild(el('b', '', `готово за ${fmtDone(u.elapsed_s)}`));
      const flq = facelockText(j); if (flq) stats.appendChild(el('span', flq.ok ? '' : 'muted', flq.text));
      if (j.result && String(j.result.mime || '').startsWith('image/')) { head.append(pultButton('photo', j.job_id)); card.appendChild(deleteButton('photo', j.job_id)); }
      if (j.result && String(j.result.mime || '').startsWith('image/')) { card.classList.add('has-thumb'); const img = el('img', 'qthumb'); img.loading = 'lazy'; img.src = `/m/api/photo/jobs/${j.job_id}/file`; img.alt = ''; img.addEventListener('click', () => openViewer(j)); card.appendChild(img); }
    } else { stats.appendChild(el('span', '', STAGES[j.stage || j.status] || j.status)); }
    if (j.params) stats.appendChild(el('span', 'muted', `${j.params.width}×${j.params.height} · ${j.params.steps} шагов`));
    card.appendChild(stats); return card;
  }
  let queueTimer = null;
  async function loadQueue() {
    clearTimeout(queueTimer); let busy = false;
    try {
      const r = await api('GET', '/photo/queue'); const box = $('photo-queue-list'); box.replaceChildren();
      const jobs = r.jobs || []; jobs.forEach((j) => box.appendChild(renderQueueItem(j)));
      if (!jobs.length) box.appendChild(el('p', 'muted', 'Очередь пуста'));
      busy = jobs.some((j) => j.ui && (j.ui.state === 'running' || j.ui.state === 'queued'));
    } catch (err) { if (err.status === 401) return; }
    if (busy && !document.hidden) queueTimer = setTimeout(loadQueue, 4000);   // опрос только пока есть активные задачи
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && $('photo-queue-list')) loadQueue(); });
  async function loadGallery() {
    try {
      const r = await api('GET', '/photo/jobs'); const g = $('gallery'); g.replaceChildren();
      const done = (r.jobs || []).filter((j) => j.status === 'completed' && j.result && String(j.result.mime || '').startsWith('image/'));
      done.forEach((j) => { const b = el('button', 'thumb'); b.dataset.job = j.job_id; const img = el('img'); img.loading = 'lazy'; img.src = `/m/api/photo/jobs/${j.job_id}/file`; img.alt = j.raw_prompt || ''; b.appendChild(img); b.addEventListener('click', () => openViewer(j)); g.appendChild(b); });
      if (!done.length) g.appendChild(el('p', 'muted', 'Пока пусто'));
    } catch { /* */ }
  }
  async function photoStatus() {
    try { const s = await api('GET', '/photo/status'); const q = s.queue || {}; $('photo-queue').textContent = q.running ? `идёт генерация · ждут ${q.waiting || 0}` : (q.waiting ? `ждут ${q.waiting}` : 'ПК свободен'); } catch { /* */ }
  }
  let viewing = null;
  function openViewer(j) {
    viewing = j; $('viewer-img').src = `/m/api/photo/jobs/${j.job_id}/file`;
    const flv = facelockText(j); $('viewer-fl').hidden = !flv; $('viewer-fl').textContent = flv ? flv.text : '';
    const un = $('viewer-unlocked'); un.hidden = !(j.result_unlocked); un.textContent = 'До FaceLock'; un.dataset.on = '';
    const m = S.photoModels.find((x) => x.id === j.model);
    $('viewer-meta').textContent = `${j.raw_prompt || ''}\n${m ? m.label : j.model} · ${j.params ? j.params.width + '×' + j.params.height + ' · сид ' + j.params.seed : ''} · ${fmtS(j.elapsed_s)}`;
    $('viewer').hidden = false;
  }
  $('viewer-close').addEventListener('click', () => { $('viewer').hidden = true; });
  $('viewer-pult').addEventListener('click', () => { if (viewing) toPult('photo', viewing.job_id, $('viewer-pult')); });
  $('viewer-delete').addEventListener('click', () => { if (viewing) deleteForever('photo', viewing.job_id, $('viewer-delete')); });
  $('viewer-unlocked').addEventListener('click', () => {      // flip between the locked result and the model's own picture
    if (!viewing) return; const un = $('viewer-unlocked'); const on = un.dataset.on !== '1';
    un.dataset.on = on ? '1' : ''; un.textContent = on ? 'С FaceLock' : 'До FaceLock';
    $('viewer-img').src = `/m/api/photo/jobs/${viewing.job_id}/file${on ? '?variant=unlocked' : ''}`;
  });
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

  /* ================= Видео: модели Bossman + замена лица FaceFusion ================= */
  // Время: загрузка + шаги × с/шаг × масштаб(объём ролика) + сборка (bossmobile/video_timing.py, та же формула)
  const V = { cat: null, mode: 'gen', aspect: '16:9', side: 480, image: null, audio: null, audioDur: null,
    swapVideo: null, swapDur: null, faces: [], preset: 'fast', frame: 'original', busy: false };
  const ASPECTS = [['16:9', '16:9'], ['9:16', '9:16'], ['3:2', '3:2'], ['2:3', '2:3'], ['1:1', '1:1']];
  const LONG_SIDES = [320, 480, 640, 832, 1024, 1280];
  const PRESET_SHORT = { fast: 'Быстро', quality: 'Качество', lively: 'Живая мимика' };
  const framesFor = (fps, step, sec) => { const raw = Math.max(1, Math.round(sec * fps)); return Math.max(1, Math.round((raw - 1) / step)) * step + 1; };
  const vscale = (r, a) => (r <= 1 ? r : Math.pow(r, a));
  function wanSeconds(t, steps, w, h, frames) {
    const r = (w * h * frames) / t.ref; const run = steps * t.s_per_step * vscale(r, t.alpha);
    return { load: t.load_s, run, decode: t.decode_s || 0, total: t.load_s + run + (t.decode_s || 0), ratio: r };
  }
  function swapSeconds(t, preset, frames) {
    const p = (t.presets || {})[preset] || (t.presets || {}).quality || { gen_fps: 1.6 };
    const gen = frames / p.gen_fps; const post = frames * t.post_s_per_frame;
    return { load: t.load_s, gen, post, total: t.load_s + gen + post, p };
  }
  async function apiForm(path, fd, onUp) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest(); x.open('POST', '/m/api' + path); x.withCredentials = true;
      x.setRequestHeader('X-BM-CSRF', S.csrf); x.setRequestHeader('Accept', 'application/json');
      if (onUp) x.upload.onprogress = (e) => { if (e.lengthComputable) onUp(e.loaded / e.total); };
      x.onerror = () => reject(new ApiError(0, { detail: { message: 'нет связи с ПК' } }));
      x.onload = () => {
        let data = null; try { data = JSON.parse(x.responseText); } catch { /* */ }
        if (x.status === 401) { showLogin(); reject(new ApiError(401, data)); return; }
        if (x.status < 200 || x.status >= 300) { reject(new ApiError(x.status, data)); return; }
        resolve(data);
      };
      x.send(fd);
    });
  }
  function toBlob(file, max) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file); const img = new Image();
      img.onload = () => { const k = Math.min(1, max / Math.max(img.width, img.height)); const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k); c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url); c.toBlob((b) => (b ? resolve(b) : reject(new Error('img'))), 'image/jpeg', 0.92); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('img')); }; img.src = url;
    });
  }
  function mediaDuration(file, tagName) {
    return new Promise((resolve) => {
      const m = document.createElement(tagName); const url = URL.createObjectURL(file); m.preload = 'metadata';
      m.onloadedmetadata = () => { const d = m.duration; URL.revokeObjectURL(url); resolve(Number.isFinite(d) ? d : null); };
      m.onerror = () => { URL.revokeObjectURL(url); resolve(null); }; m.src = url;
    });
  }
  const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return `${n} ${m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many}`; };
  const kadr = (n) => plural(n, 'кадр', 'кадра', 'кадров');
  const mb = (n) => `${(n / 1048576).toFixed(n < 10 * 1048576 ? 1 : 0)} МБ`;

  async function loadVideoCat() { V.cat = await api('GET', '/video/models'); return V.cat; }
  async function openVideo() {
    try {
      await loadVideoCat();
      const sel = $('video-model');
      if (!sel.options.length) {
        V.cat.models.forEach((m) => { const o = el('option', '', `${m.label}${m.ready ? '' : m.available ? ' — сейчас не запустится' : ' — недоступна'}`); o.value = m.id; o.disabled = !m.available; sel.appendChild(o); });
        const saved = store.get('videoModel'); const first = V.cat.models.find((m) => m.id === saved && m.available) || V.cat.models.find((m) => m.ready) || V.cat.models.find((m) => m.available);
        if (first) sel.value = first.id;
        renderMode(); onVModel(); setupSwap();
      } else { onVModel(); setupSwap(); }
      const g = V.cat.gpu || {}; $('video-gpu').textContent = g.busy ? `видеокарта занята · ждут ${g.waiting || 0}` : (g.waiting ? `ждут ${g.waiting}` : 'видеокарта свободна');
    } catch (err) { toast(err.message); }
    loadVQueue(); loadVideos();
  }
  function renderMode() {
    const fs = V.cat && V.cat.faceswap;
    seg('video-mode', [['gen', 'Генерация'], ['swap', 'Замена лица']], V.mode, (v) => { V.mode = v; $('video-form').hidden = v !== 'gen'; $('swap-form').hidden = v !== 'swap'; if (v === 'swap') swapTime(); });
    if (!fs) { const b = $('video-mode').querySelectorAll('button')[1]; if (b) { b.disabled = true; b.title = 'Bossman не отдаёт замену лица'; } }
    $('video-form').hidden = V.mode !== 'gen'; $('swap-form').hidden = V.mode !== 'swap';
  }
  const curV = () => ((V.cat && V.cat.models) || []).find((m) => m.id === $('video-model').value);
  function onVModel() {
    const m = curV(); if (!m) return; store.set('videoModel', m.id);
    const f = $('video-flags'); f.replaceChildren();
    if (m.uncensored_label) f.appendChild(el('span', 'badge-uc', 'без цензуры (по метке Bossman)'));
    f.appendChild(el('span', 'badge-local', 'на ПК'));
    f.appendChild(el('span', 'pill', (m.modes || []).map((x) => ({ T2V: 'текст→видео', I2V: 'картинка→видео', S2V: 'речь→видео', ANIMATE: 'анимация' }[x] || x)).join(' · ')));
    if (m.license) f.appendChild(el('span', 'pill', m.license));
    $('video-reason').hidden = m.ready; $('video-reason').textContent = m.ready ? '' : m.reason;
    $('video-notes').hidden = !m.notes; $('video-notes').textContent = m.notes || '';
    $('vimg-block').hidden = !m.accepts_image;
    $('vimg-title').textContent = (m.requires_image ? 'Стартовая картинка (обязательно)' : 'Стартовая картинка (необязательно — без неё видео строится по тексту)') + ' · одна, в промпте это @1';
    $('vaud-block').hidden = !m.accepts_audio;
    if (!m.accepts_image) clearVImage(); if (!m.accepts_audio) clearVAudio();
    const d = $('video-dur'); d.min = 1; d.max = Math.max(1, Math.floor(m.max_seconds));
    const want = V.audioDur && m.accepts_audio ? Math.ceil(V.audioDur) : (m.requires_audio ? 4 : 2);
    d.value = Math.min(Number(d.max), want); $('vdur-range').textContent = `(от 1 до ${d.max} с)`;
    const [dw, dh] = String(m.default_resolution).split('x').map(Number); const dl = Math.max(dw, dh);
    V.aspect = m.default_aspect || '16:9';
    const sides = LONG_SIDES.filter((s) => s <= m.max_side && s >= m.min_side);
    V.side = sides.includes(dl) ? dl : (sides.find((s) => s >= dl) || sides[sides.length - 1]); V.sides = sides;
    if (V.image && V.refWH) { V.aspect = 'ref'; V.side = sideFor(sides, V.refWH); }
    videoSegs();
    const st = $('video-steps');
    if (m.steps) {
      $('vsteps-block').hidden = false; $('vsteps-fixed').hidden = true;
      st.min = m.steps.min_steps; st.max = m.steps.max_steps; st.value = m.steps.default_steps;
      $('vsteps-range').textContent = `(от ${st.min} до ${st.max}, по умолчанию ${m.steps.default_steps})`; setVSteps(st.value);
    } else {
      $('vsteps-block').hidden = true; $('vsteps-fixed').hidden = false;
      $('vsteps-fixed').textContent = m.fixed_steps ? `Шаги: ${m.fixed_steps} — заданы графом модели (Lightning), не меняются` : 'Шаги заданы графом модели и не меняются';
    }
    setVDur(d.value);
  }
  function vResolution(m) {
    const [a, c] = aspectOf(V.aspect, V.refWH); const k = m.side_multiple || 16;
    let w = V.side; let h = V.side; if (a > c) h = V.side * c / a; else if (c > a) w = V.side * a / c;
    const r = (v) => Math.min(m.max_side, Math.max(m.min_side, Math.round(v / k) * k));
    return [r(w), r(h)];
  }
  function setVDur(v) { const d = $('video-dur'); const n = Math.min(Number(d.max), Math.max(1, Math.round(Number(v)) || 1)); d.value = n; vTime(); }
  function setVSteps(v) { const st = $('video-steps'); const n = Math.min(Number(st.max), Math.max(Number(st.min), Math.round(Number(v)) || Number(st.min))); st.value = n; $('vsteps-val').textContent = n; vTime(); }
  function vSettings(m) {
    const sec = Number($('video-dur').value); const frames = framesFor(m.fps, m.frame_step, sec); const [w, h] = vResolution(m);
    const steps = m.steps ? Number($('video-steps').value) : (m.fixed_steps || 20);
    return { sec, frames, w, h, steps };
  }
  function vTime() {
    const m = curV(); const box = $('video-time'); if (!m) { box.replaceChildren(); return; }
    const s = vSettings(m); const t = m.timing || {}; const p = wanSeconds(t, s.steps, s.w, s.h, s.frames);
    $('vdur-val').textContent = `${s.sec} с`;
    box.replaceChildren(el('b', '', `≈ ${fmtDur(p.total)}`), tag(t),
      el('span', 'muted', `${s.w}×${s.h} · ${kadr(s.frames)} при ${m.fps} к/с (задано моделью) · ${s.steps} шаг. · загрузка ${fmtDur(p.load)} + генерация ${fmtDur(p.run)}${p.decode ? ' + сборка ' + fmtDur(p.decode) : ''}`));
    if (t.basis) box.appendChild(el('span', 'hint', t.basis));
    if (p.total > 3600) box.appendChild(el('span', 'warn', 'Больше часа — для видео на этом ПК это нормально; всё это время видеокарта занята.'));
    if (p.ratio > 3) box.appendChild(el('span', 'warn', 'Большой ролик для этой модели: сильно дольше и есть риск нехватки памяти ПК (Bossman проверит память перед стартом).'));
    if (m.accepts_audio && V.audioDur && s.sec < V.audioDur - 0.5) box.appendChild(el('span', 'warn', `Звук длиннее (${V.audioDur.toFixed(1)} с): в видео войдут первые ${s.sec} с.`));
    vTable(); vGo();
  }
  function vTable() {
    const box = $('video-table'); box.replaceChildren();
    ((V.cat && V.cat.models) || []).forEach((m) => {
      const r = el('button', `mrow${m.id === $('video-model').value ? ' on' : ''}`); r.type = 'button'; r.disabled = !m.available;
      r.appendChild(el('span', 'mn', m.label));
      const [w, h] = String(m.default_resolution).split('x').map(Number); const sec = m.requires_audio ? 4 : 2; const fr = framesFor(m.fps, m.frame_step, sec);
      const steps = m.steps ? m.steps.default_steps : (m.fixed_steps || 20);
      r.appendChild(el('span', 'ms', m.available ? `${w}×${h}, ${sec} с (${kadr(fr)}) · ${m.steps ? `шаги ${m.steps.min_steps}–${m.steps.max_steps}` : `шаги ${m.fixed_steps || '—'} (граф)`} · до ${m.max_seconds} с${m.ready ? '' : ' · ' + m.reason}` : m.reason));
      const mt = el('span', 'mt');
      if (m.available) { const p = wanSeconds(m.timing, steps, w, h, fr); mt.append(el('b', '', `≈ ${fmtDur(p.total)}`), tag(m.timing)); } else mt.append(el('b', '', '—'));
      r.appendChild(mt);
      r.addEventListener('click', () => { tap(); $('video-model').value = m.id; onVModel(); });
      box.appendChild(r);
    });
    const fs = V.cat && V.cat.faceswap;
    if (fs) {
      const r = el('button', 'mrow'); r.type = 'button'; r.disabled = !fs.available; r.appendChild(el('span', 'mn', fs.label));
      const p = swapSeconds(fs.timing, 'quality', 10 * fs.timing.assumed_fps);
      r.appendChild(el('span', 'ms', fs.available ? `ролик 10 с при ${fs.timing.assumed_fps} к/с, пресет «Качество»` : fs.reason));
      const mt = el('span', 'mt'); mt.append(el('b', '', `≈ ${fmtDur(p.total)}`), tag(p.p)); r.appendChild(mt);
      r.addEventListener('click', () => { tap(); V.mode = 'swap'; renderMode(); swapTime(); });
      box.appendChild(r);
    }
  }
  function vGo() {
    const m = curV(); const b = $('video-go'); if (!m) { b.disabled = true; return; }
    b.disabled = V.busy || !m.ready || (m.requires_image && !V.image) || (m.requires_audio && !V.audio);
  }
  function videoSegs() {
    seg('video-aspect', (V.refWH ? [['ref', refLabel(V.refWH)]] : []).concat(ASPECTS), V.aspect, (v) => { V.aspect = v; vTime(); });
    seg('video-side', V.sides.map((s) => [s, `${s}`]), V.side, (v) => { V.side = v; vTime(); });
  }
  function clearVImage() {
    V.refWH = null; if (V.aspect === 'ref') { const m = curV(); V.aspect = (m && m.default_aspect) || '16:9'; if (V.sides) videoSegs(); }
    V.image = null; V.imageURL = null; $('video-image').value = ''; $('vimg-thumb').hidden = true; $('vimg-clear').hidden = true; $('vimg-label').textContent = 'Выбрать из «Фото»'; }
  function clearVAudio() { V.audio = null; V.audioDur = null; $('video-audio').value = ''; $('vaud-clear').hidden = true; $('vaud-label').textContent = 'Выбрать аудио'; }
  $('video-model').addEventListener('change', onVModel);
  $('video-dur').addEventListener('input', () => setVDur($('video-dur').value));
  $('vdur-minus').addEventListener('click', () => { tap(); setVDur(Number($('video-dur').value) - 1); });
  $('vdur-plus').addEventListener('click', () => { tap(); setVDur(Number($('video-dur').value) + 1); });
  $('video-steps').addEventListener('input', () => setVSteps($('video-steps').value));
  $('vsteps-minus').addEventListener('click', () => { tap(); setVSteps(Number($('video-steps').value) - 1); });
  $('vsteps-plus').addEventListener('click', () => { tap(); setVSteps(Number($('video-steps').value) + 1); });
  $('video-image').addEventListener('change', async () => {
    const f = $('video-image').files[0]; if (!f) return; const m = curV();
    try { V.image = await toBlob(f, Math.min(1536, (m && m.max_side) || 1280)); V.imageURL = URL.createObjectURL(V.image); $('vimg-preview').src = V.imageURL; $('vimg-thumb').hidden = false; $('vimg-clear').hidden = false; $('vimg-label').textContent = 'Заменить'; }
    catch { toast('не удалось прочитать картинку'); vGo(); return; }
    V.refWH = await imageSize(f);
    if (V.refWH && V.sides) { V.aspect = 'ref'; V.side = sideFor(V.sides, V.refWH); videoSegs(); vTime(); }
    vGo();
  });
  $('vimg-clear').addEventListener('click', () => { clearVImage(); vGo(); });
  tagSuggest($('video-prompt'), $('video-tags'), () => (V.image && (curV() || {}).accepts_image ? [{ thumb: V.imageURL, name: '' }] : []));
  $('video-audio').addEventListener('change', async () => {
    const f = $('video-audio').files[0]; if (!f) return;
    if (f.size > 32 * 1048576) { toast('Звук больше 32 МБ — Bossman его не примет'); clearVAudio(); return; }
    V.audio = f; V.audioDur = await mediaDuration(f, 'audio');
    $('vaud-label').textContent = `${f.name.slice(0, 24)} · ${mb(f.size)}${V.audioDur ? ' · ' + V.audioDur.toFixed(1) + ' с' : ''}`; $('vaud-clear').hidden = false;
    const m = curV(); if (m && V.audioDur) setVDur(Math.min(Math.floor(m.max_seconds), Math.ceil(V.audioDur)));
    vGo();
  });
  $('vaud-clear').addEventListener('click', () => { clearVAudio(); vTime(); });
  function uploadCard(text, frac) {
    const c = $('video-upload'); c.hidden = false; c.replaceChildren();
    const row = el('div', 'row'); row.append(el('span', 'stage', text)); c.appendChild(row);
    if (frac != null) { const bar = el('div', 'bar'); const i = el('i'); i.style.width = `${Math.max(2, Math.round(frac * 100))}%`; bar.appendChild(i); c.appendChild(bar); c.appendChild(el('div', 'stats', `${Math.round(frac * 100)} %`)); }
  }
  $('video-form').addEventListener('submit', async (e) => {
    e.preventDefault(); tap(); const m = curV(); if (!m || V.busy) return;
    const prompt = $('video-prompt').value.trim(); if (!prompt) { toast('Напишите промпт'); return; }
    if (!m.ready) { toast(m.reason); return; }
    if (m.accepts_image) { const why = checkTags(prompt, V.image ? [{ name: '' }] : []); if (why) { toast(why); return; } }
    const s = vSettings(m); const fd = new FormData();
    fd.append('model', m.id); fd.append('prompt', prompt); fd.append('negative', $('video-negative').value.trim());
    fd.append('duration', String(s.sec)); fd.append('resolution', `${s.w}x${s.h}`);
    if (m.steps) fd.append('steps', String(s.steps));
    const seed = $('video-seed').value.trim(); if (seed) fd.append('seed', seed);
    if (V.image && m.accepts_image) fd.append('image', V.image, 'start.jpg');
    if (V.audio && m.accepts_audio) fd.append('audio', V.audio, V.audio.name || 'voice');
    V.busy = true; vGo();
    try {
      uploadCard('Отправка на ПК…', 0);
      const job = await apiForm('/video/jobs', fd, (f) => uploadCard('Отправка на ПК…', f));
      $('video-upload').hidden = true; toast(job.queue_position ? `Видео в очереди: перед вами ${job.queue_position}` : 'Видео запущено на ПК');
      loadVQueue(); $('video-queue-list').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) { $('video-upload').hidden = true; toast(err.message); }
    V.busy = false; vGo();
  });

  /* ---------- замена лица ---------- */
  function setupSwap() {
    const fs = V.cat && V.cat.faceswap; if (!fs) return;
    const f = $('swap-flags'); f.replaceChildren(el('span', 'badge-local', 'на ПК'), el('span', 'pill', 'FaceFusion')); if (fs.license) f.appendChild(el('span', 'pill', fs.license));
    $('swap-reason').hidden = fs.available; $('swap-reason').textContent = fs.available ? '' : fs.reason;
    if (!fs.presets.some((p) => p.id === V.preset)) V.preset = (fs.presets[0] || {}).id;
    seg('swap-preset', fs.presets.map((p) => [p.id, PRESET_SHORT[p.id] || p.id]), V.preset, (v) => { V.preset = v; swapTime(); });
    seg('swap-frame', fs.frames.map((x) => [x.id, x.label]), V.frame, (v) => { V.frame = v; });
    swapTime();
  }
  function swapTime() {
    const fs = V.cat && V.cat.faceswap; const box = $('swap-time'); if (!fs) { box.replaceChildren(); return; }
    const pr = fs.presets.find((p) => p.id === V.preset); $('swap-preset-hint').textContent = pr ? pr.label : '';
    const fps = fs.timing.assumed_fps; const sec = V.swapDur || 10; const p = swapSeconds(fs.timing, V.preset, Math.round(sec * fps));
    box.replaceChildren(el('b', '', `≈ ${fmtDur(p.total)}`), tag(p.p),
      el('span', 'muted', `${V.swapDur ? 'ваш ролик ' + sec.toFixed(1) + ' с' : 'для ролика 10 с'} при ${fps} к/с · загрузка ${fmtDur(p.load)} + лица ${fmtDur(p.gen)} + проверка позы и сборка ${fmtDur(p.post)}`),
      el('span', 'hint', `${p.p.basis}. Для ролика 60 к/с — примерно вдвое дольше.`));
    swapGo();
  }
  function swapGo() {
    const fs = V.cat && V.cat.faceswap;
    $('swap-go').disabled = V.busy || !fs || !fs.available || !V.swapVideo || !V.faces.length || !$('swap-consent').checked;
  }
  function renderFaces() {
    const row = $('swap-faces-row'); row.querySelectorAll('.face-thumb').forEach((n) => n.remove());
    V.faces.forEach((b, i) => {
      const t = el('div', 'face-thumb'); const img = el('img'); img.src = URL.createObjectURL(b); img.alt = `лицо ${i + 1}`;
      const x = el('button'); x.type = 'button'; x.setAttribute('aria-label', 'Убрать фото'); x.appendChild(icon('close'));
      x.addEventListener('click', () => { V.faces.splice(i, 1); renderFaces(); swapGo(); });
      t.append(img, x); row.appendChild(t);
    });
  }
  $('swap-video').addEventListener('change', async () => {
    const f = $('swap-video').files[0]; if (!f) return;
    const max = ((V.cat && V.cat.faceswap && V.cat.faceswap.max_video_mb) || 300) * 1048576;
    if (f.size > max) { toast(`Видео больше ${Math.round(max / 1048576)} МБ — Bossman его не примет`); $('swap-video').value = ''; return; }
    V.swapVideo = f; V.swapDur = await mediaDuration(f, 'video');
    $('swap-video-label').textContent = `${f.name.slice(0, 22)} · ${mb(f.size)}${V.swapDur ? ' · ' + V.swapDur.toFixed(1) + ' с' : ''}`; $('swap-video-clear').hidden = false;
    const pv = $('swap-preview'); pv.src = URL.createObjectURL(f); pv.hidden = false; swapTime();
  });
  $('swap-video-clear').addEventListener('click', () => { V.swapVideo = null; V.swapDur = null; $('swap-video').value = ''; $('swap-video-clear').hidden = true; $('swap-video-label').textContent = 'Выбрать видео'; $('swap-preview').hidden = true; $('swap-preview').removeAttribute('src'); swapTime(); });
  $('swap-faces').addEventListener('change', async () => {
    const files = Array.from($('swap-faces').files || []); $('swap-faces').value = '';
    for (const f of files) { if (V.faces.length >= 5) { toast('Не больше 5 фото лица'); break; } try { V.faces.push(await toBlob(f, 1536)); } catch { toast('не удалось прочитать фото'); } }
    renderFaces(); swapGo();
  });
  $('swap-consent').addEventListener('change', swapGo);
  $('swap-form').addEventListener('submit', async (e) => {
    e.preventDefault(); tap(); if (V.busy) return;
    if (!$('swap-consent').checked) { toast('Подтвердите согласие человека на фото'); return; }
    const fd = new FormData(); fd.append('video', V.swapVideo, V.swapVideo.name || 'clip.mp4');
    V.faces.forEach((b, i) => fd.append('faces', b, `face${i + 1}.jpg`));
    fd.append('preset', V.preset); fd.append('frame', V.frame); fd.append('consent', 'true');
    V.busy = true; swapGo();
    try {
      uploadCard('Загрузка видео на ПК…', 0);
      const job = await apiForm('/video/swap', fd, (f) => uploadCard('Загрузка видео на ПК…', f));
      $('video-upload').hidden = true; toast(job.queue_position ? `Замена лица в очереди: перед вами ${job.queue_position}` : 'Замена лица запущена на ПК');
      loadVQueue(); $('video-queue-list').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) { $('video-upload').hidden = true; toast(err.message); }
    V.busy = false; swapGo();
  });

  /* ---------- очередь видеокарты: реальные шаги/кадры, где Bossman их сообщает; иначе «оценка»; 100 % только у готового ---------- */
  function jobLabel(j) {
    if (j.kind === 'faceswap') return 'Замена лица';
    const m = ((V.cat && V.cat.models) || []).find((x) => x.id === j.model) || (S.photoModels || []).find((x) => x.id === j.model);
    return m ? m.label : j.model;
  }
  const KIND_RU = { photo: 'фото', video: 'видео', faceswap: 'замена лица' };
  function renderVQueueItem(j) {
    const u = j.ui || {}; const card = el('div', 'qitem ' + (u.state || '')); card.dataset.job = j.job_id;
    const head = el('div', 'row'); const name = el('span', 'stage', jobLabel(j)); name.appendChild(el('span', 'kind-tag', KIND_RU[j.kind] || j.kind)); head.append(name);
    if ((u.state === 'running' || u.state === 'queued') && j.kind !== 'photo') { const c = el('button', 'btn small danger', 'Стоп'); c.type = 'button'; c.addEventListener('click', async () => { tap(); try { await api('POST', `/video/jobs/${j.job_id}/cancel`); loadVQueue(); } catch (err) { toast(err.message); } }); head.append(c); }
    card.appendChild(head);
    const stats = el('div', 'stats');
    if (u.state === 'running') {
      renderRunning(j, u, card, stats, 'шаг');
      if (u.label && !u.indeterminate) stats.appendChild(el('span', 'muted', u.label));
    } else if (u.state === 'queued') {
      renderQueued(u, stats, true);
    } else if (u.state === 'done') {
      stats.appendChild(el('b', '', `готово за ${fmtDone(u.elapsed_s)}`));
      if (j.result && /^video\//.test(String(j.result.mime || ''))) { const b = el('button', 'btn small primary'); b.type = 'button'; b.append(icon('play'), document.createTextNode('Смотреть')); b.addEventListener('click', () => openVViewer(j)); head.append(b, pultButton('video', j.job_id)); card.appendChild(deleteButton('video', j.job_id)); }
    } else {
      stats.appendChild(el('span', '', STAGES[j.stage || j.status] || j.status));
      if (j.error) card.appendChild(el('div', 'err', typeof j.error === 'string' ? j.error : (j.error.message || j.error.code || '')));
    }
    const p = j.params || {};
    if (j.kind === 'faceswap') stats.appendChild(el('span', 'muted', `пресет «${PRESET_SHORT[p.preset] || p.preset}» · фото лица: ${p.faces}`));
    else if (p.width) stats.appendChild(el('span', 'muted', `${p.width}×${p.height}${p.frames > 1 ? ` · ${kadr(p.frames)}` : ''}${p.steps ? ` · ${p.steps} шагов` : ''}`));
    card.appendChild(stats); return card;
  }
  let vqTimer = null;
  async function loadVQueue() {
    clearTimeout(vqTimer); let active = false;
    try {
      const r = await api('GET', '/video/queue'); const box = $('video-queue-list'); box.replaceChildren();
      const jobs = r.jobs || []; jobs.forEach((j) => box.appendChild(renderVQueueItem(j)));
      if (!jobs.length) box.appendChild(el('p', 'muted', 'Очередь пуста'));
      active = jobs.some((j) => j.ui && (j.ui.state === 'running' || j.ui.state === 'queued'));
      if (!active && box.dataset.active === '1') loadVideos();
      box.dataset.active = active ? '1' : '';
    } catch (err) { if (err.status === 401) return; }
    if (active && !document.hidden && !$('view-video').hidden) vqTimer = setTimeout(loadVQueue, 5000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('view-video').hidden) loadVQueue(); });
  async function loadVideos() {
    try {
      const r = await api('GET', '/video/jobs'); const box = $('video-list'); box.replaceChildren();
      const done = (r.jobs || []).filter((j) => j.status === 'completed' && j.result && /^video\//.test(String(j.result.mime || '')));
      done.forEach((j) => {
        const c = el('div', 'card vcard'); c.dataset.job = j.job_id; const res = j.result || {};
        c.appendChild(el('b', '', jobLabel(j)));
        if (j.raw_prompt) c.appendChild(el('div', 'vp', j.raw_prompt));
        c.appendChild(el('div', 'muted small', [res.width && `${res.width}×${res.height}`, res.duration_s && `${Number(res.duration_s).toFixed(1)} с`, res.codec, res.bytes && mb(res.bytes), (j.created_at || '').replace('T', ' ').slice(0, 16)].filter(Boolean).join(' · ')));
        const row = el('div', 'row'); const play = el('button', 'btn small primary'); play.type = 'button'; play.append(icon('play'), document.createTextNode('Смотреть')); play.addEventListener('click', () => openVViewer(j));
        const dl = el('a', 'btn small'); dl.href = `/m/api/video/jobs/${j.job_id}/file?download=1`; dl.setAttribute('download', ''); dl.append(icon('down'), document.createTextNode('Скачать'));
        row.append(play, dl, pultButton('video', j.job_id)); c.appendChild(row); c.appendChild(deleteButton('video', j.job_id)); box.appendChild(c);
      });
      if (!done.length) box.appendChild(el('p', 'muted', 'Пока нет готовых видео'));
    } catch { /* */ }
  }
  let vviewing = null;
  function openVViewer(j) {
    vviewing = j; const v = $('vviewer-video'); const url = `/m/api/video/jobs/${j.job_id}/file`; v.src = url;
    const res = j.result || {};
    $('vviewer-meta').textContent = `${j.raw_prompt || ''}\n${jobLabel(j)} · ${res.width ? res.width + '×' + res.height : ''} · ${res.duration_s ? Number(res.duration_s).toFixed(1) + ' с' : ''} · ${fmtS(j.elapsed_s)}`;
    const webm = String(res.mime || '').includes('webm');
    $('vviewer-note').hidden = !webm; $('vviewer-note').textContent = webm ? 'Формат WebM: если iPhone не воспроизводит, нажмите «Скачать» и откройте в VLC.' : '';
    v.onerror = () => { $('vviewer-note').hidden = false; $('vviewer-note').textContent = 'Телефон не может воспроизвести этот файл — нажмите «Скачать».'; };
    $('vviewer-download').href = url + '?download=1'; $('vviewer-reuse').hidden = !j.raw_prompt;
    $('vviewer').hidden = false;
  }
  $('vviewer-close').addEventListener('click', () => { const v = $('vviewer-video'); v.pause(); v.removeAttribute('src'); v.load(); $('vviewer').hidden = true; });
  $('vviewer-pult').addEventListener('click', () => { if (vviewing) toPult('video', vviewing.job_id, $('vviewer-pult')); });
  $('vviewer-delete').addEventListener('click', () => { if (vviewing) deleteForever('video', vviewing.job_id, $('vviewer-delete')); });
  $('vviewer-save').addEventListener('click', async () => {
    if (!vviewing) return; tap(); const url = `/m/api/video/jobs/${vviewing.job_id}/file`; const res = vviewing.result || {};
    try {
      if ((res.bytes || 0) <= 150 * 1048576 && navigator.canShare) {
        const blob = await (await fetch(url, { credentials: 'same-origin' })).blob();
        const file = new File([blob], `bossman-${vviewing.job_id.slice(0, 8)}.${blob.type.includes('webm') ? 'webm' : 'mp4'}`, { type: blob.type });
        if (navigator.canShare({ files: [file] })) { await navigator.share({ files: [file] }); return; }
      }
    } catch (err) { if (err && err.name === 'AbortError') return; }
    window.open(url + '?download=1', '_blank');
  });
  $('vviewer-reuse').addEventListener('click', () => { if (!vviewing) return; $('vviewer-close').click(); V.mode = 'gen'; renderMode(); $('video-prompt').value = vviewing.raw_prompt || ''; $('video-prompt').focus(); });

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
