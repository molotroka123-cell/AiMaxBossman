/* ============================================================
   chat/composer.js — нижняя панель ввода и чипы над ней.

   «+» и перетаскивание/вставка файлов → POST /api/chat/attachments;
   выбор исполнителя (Auto · Local-first, агенты по месту работы модели и
   тарификации, подписки Claude/Codex — только через Agentic Rave);
   счётчик контекста; микрофон → /api/oss/speech/transcribe; STOP (виден,
   пока идёт ход); отправка (Enter, Shift+Enter — новая строка).

   Модуль строит DOM и хранит только то, что видно в поле ввода. Решения
   (что отправить, куда) принимает main.js через колбэки.

   Вложение сервер привязывает к треду, открытому при загрузке (в другом
   треде отправка получила бы 409). Поэтому при смене треда main.js зовёт
   dropAttachments(): чипы убираются с короткой заметкой, а не уходят в
   чужой чат.
   ============================================================ */

import { api } from '../api.js';
import { h, icon, iconButton, clear } from './dom.js';
import { billingBadge, localityBadge, fmtBytes, fmtClockTimer, subscriptionState, healthLabel } from './format.js';
import { Recorder, blobToWav16k, micUnsupportedReason, MAX_RECORD_MS } from './audio.js';

const DEFAULT_MAX_BYTES = 20 * 1024 * 1024;
const MAX_ATTACHMENTS = 8;

function entryBadges(e) {
  const out = [];
  const lb = localityBadge(e.locality);
  if (lb) out.push(h('span.badge', { dataset: { tone: lb.tone }, title: lb.title }, lb.text));
  const bb = billingBadge(e.billing);
  if (bb && e.billing !== 'local') out.push(h('span.badge', { dataset: { tone: bb.tone } }, bb.text));
  return out;
}

export function createComposer(ctx) {
  const st = {
    running: false,
    rave: false,
    picker: null,           // buildPickerModel(...)
    selection: { mode: 'auto' },
    attachments: [],
    menu: null,             // открытое меню/поповер
    speech: { ok: false, reason: 'Проверяю распознавание речи…' },
    recorder: null,
    recTimer: null,
    maxBytes: DEFAULT_MAX_BYTES,
    note: '',
  };

  /* ---------------- DOM ---------------- */
  const textarea = h('textarea.cmp-input', {
    id: 'chat-input', rows: 1, placeholder: 'Напишите задачу…', 'aria-label': 'Сообщение Bossman',
    spellcheck: 'true', autocomplete: 'off',
  });
  const fileInput = h('input', { type: 'file', multiple: true, hidden: true, id: 'chat-file', 'aria-hidden': 'true', tabindex: '-1' });
  const attachBtn = iconButton('plus', 'Прикрепить файлы (или перетащите их в окно)', () => fileInput.click(), { cls: 'cmp-attach', id: 'chat-attach' });
  const chipsEl = h('div.cmp-atts', { 'aria-live': 'polite' });
  const pickerBtn = h('button.cmp-picker', { type: 'button', id: 'chat-picker', 'aria-haspopup': 'listbox', 'aria-expanded': 'false',
    'aria-label': 'Исполнитель: Auto · Local-first', title: 'Кто выполнит сообщение' });
  const meter = h('span.cmp-meter', { id: 'chat-context', title: 'Контекст: занято / окно модели (токены последнего вызова модели)' },
    h('span.cmp-meter-bar', h('i')), h('span.cmp-meter-text', '—'));
  const micBtn = iconButton('mic', 'Голосовой ввод', () => toggleMic(), { cls: 'cmp-mic', id: 'chat-mic' });
  const recBox = h('span.cmp-rec', { hidden: true },
    h('span.rec-dot', { 'aria-hidden': 'true' }), h('span.rec-time', '00:00'),
    iconButton('close', 'Отменить запись', () => cancelMic(), { size: 14, id: 'chat-mic-cancel' }));
  const stopBtn = h('button.cmp-stop', { type: 'button', id: 'chat-stop', hidden: true, 'aria-label': 'Остановить (Esc)', title: 'Остановить (Esc)',
    onClick: () => ctx.onStop() }, icon('stop', 18));
  const sendBtn = h('button.cmp-send', { type: 'button', id: 'chat-send', 'aria-label': 'Отправить (Enter)', title: 'Отправить (Enter)',
    onClick: () => submit() }, icon('send', 20));
  const noteEl = h('div.cmp-note', { role: 'status', 'aria-live': 'polite' });

  const chipThinking = h('button.chip', { type: 'button', id: 'chip-thinking', 'aria-pressed': 'false', 'aria-label': 'Thinking — панель плана и действий (Ctrl+.)',
    title: 'Панель Thinking & Actions (Ctrl+.)', onClick: () => ctx.onTogglePanel() }, icon('spark', 16), h('span', 'Thinking'));
  const chipSources = h('button.chip', { type: 'button', id: 'chip-sources', 'aria-label': 'Источники ответа', title: 'Источники ответа',
    onClick: () => ctx.onShowSources() }, icon('link', 16), h('span', 'Источники'), h('span.chip-count', { hidden: true }));
  const chipMemory = h('button.chip', { type: 'button', id: 'chip-memory', 'aria-haspopup': 'dialog', 'aria-label': 'Память Bossman', title: 'Память Bossman',
    onClick: (e) => openPopover('memory', e.currentTarget) }, icon('memory', 16), h('span', 'Память'));
  const chipTools = h('button.chip', { type: 'button', id: 'chip-tools', 'aria-haspopup': 'dialog', 'aria-label': 'Инструменты исполнителя', title: 'Инструменты исполнителя',
    onClick: (e) => openPopover('tools', e.currentTarget) }, icon('tools', 16), h('span', 'Инструменты'));
  const chipRave = h('button.chip.chip-rave', { type: 'button', id: 'chip-rave', 'aria-pressed': 'false', 'aria-label': 'Режим Agentic Rave',
    title: 'Agentic Rave: одна задача — несколько агентов в изолированных копиях проекта', onClick: () => ctx.onToggleRave() },
  icon('bolt', 16), h('span', 'Agentic Rave'));

  const box = h('div.cmp-box',
    chipsEl,
    h('div.cmp-row', attachBtn, textarea),
    h('div.cmp-tools', pickerBtn, h('span.cmp-spacer'), meter, recBox, micBtn, stopBtn, sendBtn));
  const root = h('div.composer', { id: 'chat-composer' },
    h('div.chips', { role: 'toolbar', 'aria-label': 'Режимы и панели' }, chipThinking, chipSources, chipMemory, chipTools, chipRave),
    box, fileInput, noteEl);

  /* ---------------- ввод ---------------- */
  function autosize() {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 260)}px`;
  }
  textarea.addEventListener('input', () => { autosize(); syncSend(); });
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      submit();
    }
  });
  textarea.addEventListener('paste', (e) => {
    const files = e.clipboardData && e.clipboardData.files ? [...e.clipboardData.files] : [];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  fileInput.addEventListener('change', () => { addFiles([...fileInput.files]); fileInput.value = ''; });

  function setNote(text, tone = '') {
    st.note = text || '';
    noteEl.textContent = st.note;
    noteEl.dataset.tone = tone;
  }

  function uploading() { return st.attachments.some((a) => a.status === 'uploading'); }

  function syncSend() {
    const hasText = textarea.value.trim().length > 0;
    const ready = (hasText || st.attachments.some((a) => a.status === 'ready')) && !uploading();
    sendBtn.disabled = st.running || !ready;
    sendBtn.hidden = st.running;
    stopBtn.hidden = !st.running;
    sendBtn.title = uploading() ? 'Дождитесь загрузки вложений' : 'Отправить (Enter)';
  }

  function submit() {
    if (st.running) return;
    const text = textarea.value.trim();
    const ready = st.attachments.filter((a) => a.status === 'ready');
    if (uploading()) { setNote('Вложения ещё загружаются — отправлю, когда будут готовы.', 'warn'); return; }
    if (!text && !ready.length) return;
    ctx.onSend({ text, attachments: ready.map((a) => ({ id: a.id, name: a.name, size: a.size, kind: a.kind })) });
  }

  /* ---------------- вложения ---------------- */
  function renderChips() {
    clear(chipsEl);
    for (const a of st.attachments) {
      chipsEl.appendChild(h('span.att-chip', { dataset: { state: a.status }, title: a.error || a.name },
        a.status === 'uploading' ? h('span.spin.is-small', { 'aria-hidden': 'true' }) : icon(a.status === 'error' ? 'warn' : 'file', 14),
        h('span.att-name', a.name), h('span.att-size', a.status === 'error' ? a.error : fmtBytes(a.size)),
        iconButton('close', `Убрать вложение ${a.name}`, () => { st.attachments = st.attachments.filter((x) => x !== a); renderChips(); syncSend(); }, { size: 13 })));
    }
    chipsEl.hidden = !st.attachments.length;
  }

  async function upload(a, file) {
    /* вложение, убранное пока шла загрузка (смена треда, крестик), остаётся убранным */
    try {
      const qs = `filename=${encodeURIComponent(file.name || 'file')}${ctx.threadId() ? `&thread_id=${encodeURIComponent(ctx.threadId())}` : ''}`;
      const res = await api.raw(`/api/chat/attachments?${qs}`, { method: 'POST', body: new Blob([file], { type: 'application/octet-stream' }) });
      Object.assign(a, { status: 'ready', id: res.id, kind: res.kind, size: res.size ?? a.size, name: res.name || a.name });
    } catch (err) {
      if (err && err.isAuth) ctx.onAuth();
      Object.assign(a, { status: 'error', error: (err && err.message) || 'не загрузилось' });
    }
    renderChips();
    syncSend();
  }

  function addFiles(files) {
    for (const file of files) {
      if (st.attachments.length >= MAX_ATTACHMENTS) { setNote(`Не больше ${MAX_ATTACHMENTS} вложений в одном сообщении.`, 'warn'); break; }
      const a = { name: file.name || 'файл', size: file.size, status: 'uploading', id: null, kind: null, error: '' };
      st.attachments.push(a);
      if (file.size > st.maxBytes) {
        Object.assign(a, { status: 'error', error: `больше ${fmtBytes(st.maxBytes)}` });
        continue;
      }
      upload(a, file);
    }
    renderChips();
    syncSend();
  }

  /* ---------------- выбор исполнителя ---------------- */
  function selectionLabel() {
    if (st.rave) return { text: 'Agentic Rave', badges: [] };
    const sel = st.selection;
    if (!st.picker || sel.mode === 'auto') return { text: (st.picker && st.picker.auto && st.picker.auto.label) || 'Auto · Local-first', badges: [] };
    const e = st.picker.entries.find((x) => String(x.agentId) === String(sel.agentId));
    if (!e) return { text: 'Auto · Local-first', badges: [] };
    return { text: `${e.name} · ${e.alias}`, badges: entryBadges(e) };
  }

  function renderPickerBtn() {
    const { text, badges } = selectionLabel();
    clear(pickerBtn);
    pickerBtn.appendChild(icon(st.rave ? 'bolt' : 'spark', 15));
    pickerBtn.appendChild(h('span.cmp-picker-text', text));
    for (const b of badges) pickerBtn.appendChild(b);
    pickerBtn.appendChild(icon('chevronDown', 14));
    pickerBtn.setAttribute('aria-label', `Исполнитель: ${text}`);
  }

  function closeMenu() {
    if (!st.menu) return;
    const { el, anchor } = st.menu;
    el.remove();
    if (anchor) anchor.setAttribute('aria-expanded', 'false');
    st.menu = null;
  }

  function placeMenu(el, anchor) {
    root.appendChild(el);
    const a = anchor.getBoundingClientRect();
    const r = root.getBoundingClientRect();
    el.style.left = `${Math.max(0, Math.min(a.left - r.left, r.width - el.offsetWidth))}px`;
    el.style.bottom = `${r.bottom - a.top + 8}px`;
  }

  function menuKeys(el) {
    el.addEventListener('keydown', (e) => {
      const items = [...el.querySelectorAll('[role="option"]:not([aria-disabled="true"]), button.pop-item')];
      const i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[0])?.focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (items[i - 1] || items[items.length - 1])?.focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); const anchor = st.menu && st.menu.anchor; closeMenu(); anchor?.focus(); }
    });
  }

  function option({ label, sub, badges = [], selected, disabled, reason, onPick, iconName = 'spark' }) {
    return h('div.pick-opt', {
      role: 'option', tabindex: '0', 'aria-selected': selected ? 'true' : 'false', 'aria-disabled': disabled ? 'true' : 'false',
      title: disabled && reason ? reason : null,
      onClick: () => { if (!disabled) onPick(); },
      onKeydown: (e) => { if ((e.key === 'Enter' || e.key === ' ') && !disabled) { e.preventDefault(); onPick(); } },
    },
    icon(iconName, 16),
    h('span.pick-main', h('span.pick-label', label), sub ? h('span.pick-sub', sub) : null),
    ...badges,
    selected ? icon('check', 16, 'pick-check') : null);
  }

  function openPicker() {
    /* в режиме рейва кнопка выбирает агентов рейва, а не исполнителя обычного хода */
    if (st.rave) { openPopover('rave', pickerBtn); return; }
    if (st.menu && st.menu.kind === 'picker') { closeMenu(); return; }
    closeMenu();
    const pm = st.picker;
    const list = h('div.pick-menu.popover', { role: 'listbox', 'aria-label': 'Исполнитель сообщения', id: 'chat-picker-menu' });
    const pick = (sel) => { closeMenu(); ctx.onSelect(sel); pickerBtn.focus(); };
    list.appendChild(option({
      label: (pm && pm.auto && pm.auto.label) || 'Auto · Local-first',
      sub: (pm && pm.auto && pm.auto.hint) || 'Лучший доступный агент, сначала локальные модели',
      selected: !st.rave && st.selection.mode === 'auto', onPick: () => pick({ mode: 'auto' }),
    }));
    if (!pm) list.appendChild(h('div.pick-empty', 'Список агентов не загрузился — доступен только Auto.'));
    for (const g of pm ? pm.groups : []) {
      list.appendChild(h('div.pick-group', g.label));
      for (const e of g.items) {
        list.appendChild(option({
          label: `${e.name} · ${e.alias}`,
          sub: !e.usable ? (e.refusal || 'недоступен') : [e.provider, e.health && e.health !== 'ok' ? `состояние: ${healthLabel(e.health)}` : ''].filter(Boolean).join(' · '),
          badges: entryBadges(e), disabled: !e.usable, reason: e.refusal,
          selected: !st.rave && st.selection.mode === 'agent' && String(st.selection.agentId) === String(e.agentId),
          iconName: e.group === 'local' ? 'home' : 'link',
          onPick: () => pick({ mode: 'agent', agentId: e.agentId }),
        }));
      }
    }
    const subs = pm ? pm.subscriptions : [];
    if (subs.length) {
      list.appendChild(h('div.pick-group', 'Подписки — только через Agentic Rave'));
      for (const s of subs) {
        const ss = subscriptionState(s);
        const name = s.name === 'claude' ? 'Claude' : s.name === 'codex' ? 'Codex' : s.name;
        list.appendChild(option({
          label: `${name} · Subscription`, sub: `${ss.text}. ${s.note || 'Обычные сообщения на подписке не выполняются — только в режиме Agentic Rave.'}`,
          badges: [h('span.badge', { dataset: { tone: ss.ok ? 'free' : 'blocked' } }, ss.ok ? 'вход есть' : s.version_ok === false ? 'CLI устарел' : 'нет входа')],
          disabled: s.available === false || s.version_ok === false, reason: ss.text, iconName: 'bolt',
          selected: st.rave && (st.selection.subs || []).includes(s.name),
          onPick: () => pick({ mode: 'rave', sub: s.name }),
        }));
      }
    }
    placeMenu(list, pickerBtn);
    menuKeys(list);
    pickerBtn.setAttribute('aria-expanded', 'true');
    st.menu = { kind: 'picker', el: list, anchor: pickerBtn };
    (list.querySelector('[aria-selected="true"]') || list.querySelector('[role="option"]'))?.focus();
  }
  pickerBtn.addEventListener('click', openPicker);

  /* ---------------- поповеры памяти, инструментов, рейва ---------------- */
  async function openPopover(kind, anchor) {
    if (st.menu && st.menu.kind === kind) { closeMenu(); return; }
    closeMenu();
    const pop = h('div.popover.info-pop', { role: 'dialog', tabindex: '-1', 'aria-label': kind === 'memory' ? 'Память' : kind === 'tools' ? 'Инструменты' : 'Агенты рейва', id: `chat-pop-${kind}` },
      h('div.pop-loading', 'Загружаю…'));
    placeMenu(pop, anchor);
    menuKeys(pop);
    anchor.setAttribute('aria-expanded', 'true');
    st.menu = { kind, el: pop, anchor };
    let body;
    try {
      body = await ctx.popoverContent(kind);
    } catch (err) {
      body = h('div.pop-error', (err && err.message) || 'Не удалось получить данные');
    }
    if (!st.menu || st.menu.el !== pop) return;
    clear(pop);
    pop.appendChild(body);
    placeMenu(pop, anchor);
    (pop.querySelector('button, a, input') || pop).focus?.();
  }

  document.addEventListener('mousedown', (e) => {
    if (st.menu && !st.menu.el.contains(e.target) && !(st.menu.anchor && st.menu.anchor.contains(e.target))) closeMenu();
  });

  /* ---------------- микрофон ---------------- */
  function renderMic() {
    const recording = Boolean(st.recorder);
    micBtn.disabled = !recording && (!st.speech.ok || st.running);
    micBtn.classList.toggle('is-rec', recording);
    const label = recording ? 'Остановить запись и распознать' : st.speech.ok ? 'Голосовой ввод' : `Голосовой ввод недоступен: ${st.speech.reason}`;
    micBtn.setAttribute('aria-label', label);
    micBtn.title = label;
    micBtn.setAttribute('aria-pressed', recording ? 'true' : 'false');
    recBox.hidden = !recording;
  }

  async function toggleMic() {
    if (st.recorder) { await finishMic(); return; }
    if (!st.speech.ok) { setNote(st.speech.reason, 'warn'); return; }
    const rec = new Recorder();
    try {
      await rec.start();
    } catch (err) {
      setNote(`Микрофон недоступен: ${(err && err.message) || 'нет разрешения'}`, 'warn');
      return;
    }
    st.recorder = rec;
    setNote('Идёт запись. Нажмите микрофон ещё раз, чтобы распознать.', '');
    st.recTimer = setInterval(() => {
      recBox.querySelector('.rec-time').textContent = fmtClockTimer(rec.elapsed());
      if (rec.elapsed() >= MAX_RECORD_MS) finishMic();
    }, 250);
    renderMic();
  }

  function cancelMic() {
    if (!st.recorder) return;
    clearInterval(st.recTimer);
    st.recorder.cancel();
    st.recorder = null;
    setNote('Запись отменена.', '');
    renderMic();
  }

  async function finishMic() {
    const rec = st.recorder;
    if (!rec) return;
    clearInterval(st.recTimer);
    st.recorder = null;
    renderMic();
    setNote('Распознаю речь локально…', '');
    micBtn.disabled = true;
    try {
      const blob = await rec.stop();
      const wav = await blobToWav16k(blob);
      const res = await api.raw('/api/oss/speech/transcribe?language=auto', { method: 'POST', body: wav });
      const text = String((res && res.text) || '').trim();
      if (text) {
        textarea.value = textarea.value.trim() ? `${textarea.value.trim()} ${text}` : text;
        autosize();
        syncSend();
        textarea.focus();
        setNote(`Распознано${res.duration_seconds ? ` · ${Number(res.duration_seconds).toFixed(1)} с записи` : ''}. Проверьте текст и отправьте.`, 'ok');
      } else {
        setNote('Речь не распознана. Скажите ещё раз, ближе к микрофону.', 'warn');
      }
    } catch (err) {
      if (err && err.isAuth) ctx.onAuth();
      setNote(`Распознавание не удалось: ${(err && err.message) || 'ошибка'}${err && err.hint ? ` — ${err.hint}` : ''}`, 'error');
    }
    renderMic();
  }

  /* ---------------- публичное ---------------- */
  renderPickerBtn();
  renderChips();
  syncSend();
  renderMic();

  return {
    root,
    textarea,
    closeMenu,
    menuOpen: () => Boolean(st.menu),
    focus() { textarea.focus(); },
    setRunning(running) { st.running = Boolean(running); syncSend(); renderMic(); },
    setPicker(pm) { st.picker = pm; renderPickerBtn(); },
    setSelection(sel, rave) {
      st.selection = sel;
      st.rave = Boolean(rave);
      chipRave.setAttribute('aria-pressed', st.rave ? 'true' : 'false');
      root.classList.toggle('is-rave', st.rave);
      textarea.placeholder = st.rave ? 'Задача для Agentic Rave (агенты в изолированных копиях проекта)…' : 'Напишите задачу…';
      renderPickerBtn();
    },
    setPanelOpen(open) { chipThinking.setAttribute('aria-pressed', open ? 'true' : 'false'); },
    setSourcesCount(n) {
      const c = chipSources.querySelector('.chip-count');
      c.hidden = !n;
      c.textContent = n ? String(n) : '';
    },
    setMeter(m) {
      meter.querySelector('.cmp-meter-text').textContent = m.label;
      const bar = meter.querySelector('.cmp-meter-bar i');
      bar.style.width = m.ratio === null ? '0%' : `${Math.round(m.ratio * 100)}%`;
      meter.dataset.level = m.ratio === null ? '' : m.ratio > 0.9 ? 'high' : m.ratio > 0.7 ? 'mid' : 'low';
    },
    setSpeech(ok, reason) {
      const unsupported = micUnsupportedReason();
      st.speech = { ok: Boolean(ok) && !unsupported, reason: unsupported || reason || 'Распознавание речи не настроено' };
      renderMic();
    },
    setMaxBytes(n) { if (Number.isFinite(Number(n)) && Number(n) > 0) st.maxBytes = Number(n); },
    setNote,
    addFiles,
    takeInput() {
      const text = textarea.value;
      textarea.value = '';
      st.attachments = [];
      renderChips();
      autosize();
      syncSend();
      return text;
    },
    setText(text) { textarea.value = text || ''; autosize(); syncSend(); },
    /** Смена треда: вложения прошлого треда здесь не отправятся — убрать и сказать об этом. */
    dropAttachments(note = '') {
      if (!st.attachments.length) return false;
      st.attachments = [];
      renderChips();
      syncSend();
      if (note) setNote(note, 'warn');
      return true;
    },
  };
}
