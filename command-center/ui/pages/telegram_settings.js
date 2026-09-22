/* ============================================================
   telegram_settings.js — раздел «Telegram» в Настройках.
   Endpoints (bcc/features/telegram_settings.py, под /api с обычной auth/CSRF):
     GET/PUT /api/telegram/settings, POST /api/telegram/models,
     POST /api/telegram/test, GET /api/telegram/status,
     POST /api/telegram/start, POST /api/telegram/stop.

   Telegram — только беседа с локальными моделями. Поручения (/task) и любое
   управление компьютером отсюда недоступны.
   Токен бота уходит на сервер один раз и обратно не приходит: показываются
   только последние 4 символа. Модели выбираются из того, что реально отдают
   локальные серверы (/v1/models), поэтому «точное имя» вводить не нужно.
   ============================================================ */

import { api } from '../api.js';
import { h, field, input, select, textarea, actionButton, toastOk, toastError, badge } from '../components.js';

export const SETTINGS_ENDPOINT = '/api/telegram/settings';

const STATE_LABEL = { running: 'работает', stopped: 'остановлен', error: 'ошибка' };
const STATE_TONE = { running: 'ok', stopped: '', error: 'err' };

function statusLine(st) {
  const s = st || {};
  return h('div.row', { 'data-testid': 'tg-status' },
    h('span.small', 'Компаньон: '),
    badge(STATE_LABEL[s.state] || 'неизвестно', STATE_TONE[s.state] || ''),
    s.last_error ? h('span.xsmall.dim.mono', ` · ${s.last_error}`) : null);
}

export function shortModel(id) {
  const name = String(id || '').split(/[\\/]/).pop().replace(/\.gguf$/i, '').replace(/-0*1-of-\d+$/, '');
  return name || String(id || '');
}

function port(url) { const m = /:(\d+)/.exec(String(url || '').replace(/^https?:\/\//, '')); return m ? m[1] : url; }

/* Варианты — все пары «сервер · модель», которые реально отдаются сейчас,
   плюс сохранённая пара (даже если сервер сейчас выключен). */
function choices(endpoints, saved) {
  const out = [];
  for (const ep of endpoints || []) {
    for (const model of ep.models || []) {
      const speed = ep.measured_tok_s ? ` · ~${ep.measured_tok_s} ток/с` : '';
      out.push({ url: ep.url, model, label: `${port(ep.url)} · ${shortModel(model)}${speed}` });
    }
  }
  if (saved && saved.model && !out.some((c) => c.url === saved.url && c.model === saved.model)) {
    out.push({ url: saved.url, model: saved.model, label: `${port(saved.url)} · ${shortModel(saved.model)} (сохранено, сервер не отвечает)` });
  }
  return out;
}

function fillSelect(sel, list, current, emptyLabel) {
  sel.textContent = '';
  if (emptyLabel !== null) sel.appendChild(h('option', { value: '' }, emptyLabel));
  list.forEach((c, i) => sel.appendChild(h('option', { value: String(i) }, c.label)));
  const idx = current ? list.findIndex((c) => c.url === current.url && c.model === current.model) : -1;
  sel.value = idx >= 0 ? String(idx) : (emptyLabel !== null ? '' : (list.length ? '0' : ''));
}

export async function telegramPanel(ctx) {
  let data = {};
  let loadErr = null;
  try { data = await api.raw(SETTINGS_ENDPOINT); } catch (e) { loadErr = e; }
  if (loadErr) {
    return h('section.panel', { id: 'telegram' }, h('div.panel-head', h('h2', 'Telegram')),
      h('div.panel-body', h('div.small.dim', loadErr.message)));
  }

  let list = [];
  const statusBox = h('div', statusLine(data.status));
  const setStatus = (st) => { statusBox.textContent = ''; statusBox.appendChild(statusLine(st)); };

  const tokenEl = input({ type: 'password', autocomplete: 'off', name: 'tg-token',
    placeholder: data.token_set ? `сохранён ${data.token_last4} — оставьте пустым, чтобы не менять` : '1234567890:AA…' });
  const ownerEl = input({ type: 'number', min: '1', step: '1', name: 'tg-owner', value: data.owner_id ?? '' });
  const guestsEl = input({ name: 'tg-guests', placeholder: 'через запятую, до 7', value: (data.guest_ids || []).join(', ') });
  const bestSel = select([], { name: 'tg-best' });
  const fastestSel = select([], { name: 'tg-fastest' });
  const routeSel = select([{ value: 'best', label: 'Лучшая (самая умная)' }, { value: 'fastest', label: 'Самая быстрая' }],
    { name: 'tg-route', value: data.default_route || 'best' });
  const visionSel = select([{ value: 'auto', label: 'Автоматически (модель, которая видит изображения)' },
    { value: 'best', label: 'Лучшая' }, { value: 'fastest', label: 'Самая быстрая' }],
    { name: 'tg-vision', value: data.vision_route || 'auto' });
  const visionNote = h('span.field-note', 'Нужна модель, запущенная с --mmproj');
  const imgEnabledEl = h('input', { type: 'checkbox', name: 'tg-img-enabled', checked: !!data.image_enabled });
  const imgModelSel = select([{ value: 'sdcpp:z-image-turbo', label: 'Z-Image-Turbo (sd.cpp, локально)' },
    { value: 'sdcpp:flux1-schnell', label: 'FLUX.1-schnell (sd.cpp, локально)' },
    { value: 'sdcpp:sdxl-base', label: 'SDXL 1.0 (sd.cpp, локально)' }],
    { name: 'tg-img-model', value: data.image_model || 'sdcpp:z-image-turbo' });
  const imgSizeSel = select([{ value: 512, label: '512×512 — быстро' }, { value: 768, label: '768×768' },
    { value: 1024, label: '1024×1024 — качество' }], { name: 'tg-img-size', value: data.image_size ?? 1024 });
  const imgStepsSel = select([{ value: 4, label: '4 шага — черновик' }, { value: 8, label: '8 шагов — обычно' },
    { value: 12, label: '12 шагов — тщательно' }], { name: 'tg-img-steps', value: data.image_steps ?? 8 });
  const imgGuestsEl = h('input', { type: 'checkbox', name: 'tg-img-guests', checked: !!data.image_guests });
  const imgNote = h('span.field-note', 'Проверяю движок…');
  api.raw('/api/studio/models').then((res) => {
    const m = (res.items || []).find((x) => x.id === imgModelSel.value);
    imgNote.textContent = m && m.available ? 'Движок sd.cpp и модель найдены в Bossman Studio'
      : 'Не настроено: нужен sd.cpp (BOSSMAN_SDCPP_BIN) и модели с MANIFEST.json (BOSSMAN_MEDIA_MODELS)';
  }).catch(() => { imgNote.textContent = 'Студия недоступна'; });
  const personaEl = textarea({ name: 'tg-persona', rows: 5, maxlength: 3000, value: data.persona || '' });
  const retentionEl = input({ type: 'number', min: '1', max: '3650', step: '1', name: 'tg-retention', value: data.retention_days ?? 90 });
  const priorityEl = h('input', { type: 'checkbox', name: 'tg-priority', checked: data.owner_priority !== false });
  const learnBoxes = {};
  const peopleBox = h('div.stack.sm', { 'data-testid': 'tg-people' }, h('div.xsmall.dim', 'Загрузка…'));

  const renderPeople = async () => {
    let items = [];
    try { items = (await api.raw('/api/telegram/people')).items || []; } catch (e) { items = []; }
    peopleBox.textContent = '';
    if (!items.length) { peopleBox.appendChild(h('div.xsmall.dim', 'Сохраните настройки — здесь появятся владелец и гости.')); return; }
    for (const person of items) {
      const uid = String(person.user_id);
      const box = h('input', { type: 'checkbox', name: `tg-learn-${uid}`, checked: (data.learning || {})[uid] !== false });
      learnBoxes[uid] = box;
      const profileEl = textarea({ name: `tg-profile-${uid}`, rows: 4, maxlength: 2000,
        value: person.profile ? person.profile.text : '', placeholder: 'Профиль ещё не построен' });
      const meta = person.profile
        ? `версия ${person.profile.version} · ${new Date(person.profile.updated * 1000).toLocaleString()}${person.profile.edited_by_owner ? ' · правлено вами' : ''}`
        : 'профиля нет';
      peopleBox.appendChild(h('div.panel-body', { style: 'border:1px solid var(--line, #3334);border-radius:8px' },
        h('div.row', h('b', `${person.role === 'owner' ? 'Владелец' : 'Гость'} ${uid}`),
          h('span.xsmall.dim', ` · записей: ${person.entries}${person.paused ? ' · пауза (/pause_learning)' : ''}${person.role === 'guest' && !person.notice_seen ? ' · уведомление ещё не показано' : ''}`)),
        h('label.check', box, h('span', 'Учиться на этом пользователе')),
        field('Цифровой профиль', profileEl, meta),
        h('div.row', { style: 'gap:8px' },
          actionButton('Сохранить профиль', async () => {
            try { await api.raw(`/api/telegram/profile/${uid}`, { method: 'PUT', body: { text: profileEl.value } }); toastOk('Профиль сохранён'); }
            catch (e) { toastError(e, 'Профиль не сохранён'); }
          }, { cls: 'btn btn-sm' }),
          actionButton('Удалить профиль', async () => {
            try { await api.raw(`/api/telegram/profile/${uid}`, { method: 'DELETE' }); profileEl.value = ''; toastOk('Профиль удалён'); }
            catch (e) { toastError(e, 'Профиль не удалён'); }
          }, { cls: 'btn btn-sm btn-danger' }))));
    }
  };
  renderPeople();

  const fallbackEl = h('input', { type: 'checkbox', name: 'tg-fallback', checked: data.fast_fallback !== false });
  const timeoutEl = input({ type: 'number', min: '1', max: '600', step: '1', name: 'tg-timeout', value: data.local_timeout ?? 180 });
  const tokensEl = input({ type: 'number', min: '64', max: '2048', step: '64', name: 'tg-max-tokens', value: data.max_tokens ?? 1024 });
  const enabledEl = h('input', { type: 'checkbox', name: 'tg-enabled', checked: data.enabled !== false });
  const delegationEl = h('input', { type: 'checkbox', name: 'tg-delegation', checked: false, disabled: true });

  const savedBest = { url: data.best_url, model: data.best_model };
  const savedFastest = { url: data.fastest_url, model: data.fastest_model };
  const redraw = (best, fastest) => {
    fillSelect(bestSel, list, best, list.length ? null : '— нажмите «Проверить модели» —');
    fillSelect(fastestSel, list, fastest, '— не использовать —');
  };
  list = choices([], savedBest);
  redraw(savedBest, savedFastest);

  const checkModels = async ({ quiet = false } = {}) => {
    const res = await api.raw('/api/telegram/models', { method: 'POST', body: {} });
    list = choices(res.endpoints, savedBest.model ? savedBest : null);
    if (savedFastest.model && !list.some((c) => c.url === savedFastest.url && c.model === savedFastest.model)) {
      list = list.concat(choices([], savedFastest));
    }
    const seeing = (res.endpoints || []).filter((e) => e.vision === true).map((e) => port(e.url));
    visionNote.textContent = seeing.length ? `Видят изображения: ${seeing.join(', ')}` : 'Ни один запущенный сервер не видит изображения (нужен --mmproj)';
    const sug = res.suggested || {};
    redraw(savedBest.model ? savedBest : sug.best, savedFastest.model ? savedFastest : sug.fastest);
    if (!quiet) {
      const up = (res.endpoints || []).filter((e) => (e.models || []).length).map((e) => port(e.url));
      toastOk(up.length ? `Отвечают серверы: ${up.join(', ')}` : 'Ни один локальный сервер моделей не отвечает',
        'Проверен только список моделей, не ответ модели.');
    }
  };

  const chosen = (sel) => (sel.value === '' ? null : list[Number(sel.value)] || null);

  const save = async () => {
    const guests = guestsEl.value.split(/[\s,;]+/).filter(Boolean);
    if (guests.some((g) => !/^\d+$/.test(g))) throw new Error('Telegram ID гостей — только цифры');
    const best = chosen(bestSel);
    if (!best) throw new Error('Выберите лучшую модель (нажмите «Проверить модели»)');
    const fastest = chosen(fastestSel);
    const body = {
      bot_token: tokenEl.value.trim() || null,
      owner_id: Number(ownerEl.value),
      guest_ids: guests.map(Number),
      best_url: best.url, best_model: best.model,
      fastest_url: fastest ? fastest.url : '', fastest_model: fastest ? fastest.model : '',
      default_route: routeSel.value, fast_fallback: fallbackEl.checked,
      local_timeout: Number(timeoutEl.value || 180), max_tokens: Number(tokensEl.value || 1024),
      vision_route: visionSel.value, delegation: false, enabled: enabledEl.checked,
      image_enabled: imgEnabledEl.checked, image_model: imgModelSel.value,
      image_size: Number(imgSizeSel.value), image_steps: Number(imgStepsSel.value), image_guests: imgGuestsEl.checked,
      persona: personaEl.value, retention_days: Number(retentionEl.value || 90), owner_priority: priorityEl.checked,
      learning: Object.fromEntries(Object.entries(learnBoxes).map(([uid, box]) => [uid, box.checked])),
    };
    const res = await api.raw(SETTINGS_ENDPOINT, { method: 'PUT', body });
    tokenEl.value = '';
    tokenEl.placeholder = res.token_set ? `сохранён ${res.token_last4} — оставьте пустым, чтобы не менять` : '';
    Object.assign(savedBest, { url: res.best_url, model: res.best_model });
    Object.assign(savedFastest, { url: res.fastest_url, model: res.fastest_model });
    setStatus(res.status);
    renderPeople();
    toastOk('Настройки Telegram сохранены', (res.warnings || []).join(' '));
  };

  const wrap = (fn, msg) => async () => { try { await fn(); } catch (e) { toastError(e, msg); } };

  const buttons = h('div.row', { style: 'gap:8px;flex-wrap:wrap' },
    actionButton('Сохранить', wrap(save, 'Настройки не сохранены'), { cls: 'btn btn-primary' }),
    actionButton('Проверить модели', wrap(() => checkModels(), 'Модели не проверены')),
    actionButton('Проверить бота', wrap(async () => {
      const res = await api.raw('/api/telegram/test', { method: 'POST' });
      if (res.ok) toastOk(`Бот @${res.username || '?'} отвечает`, 'Проверены только токен и отсутствие webhook; сообщений не отправлено.');
      else toastError(new Error(`Проверка бота: ${res.status}`));
    }, 'Бот не проверен')),
    actionButton('Команды бота в меню Telegram', wrap(async () => {
      const res = await api.raw('/api/telegram/commands', { method: 'POST' });
      if (res.ok) toastOk('Команды бота обновлены', (res.commands || []).map((c) => '/' + c).join(' '));
      else toastError(new Error(`Команды не обновлены: ${res.status}`));
    }, 'Команды не обновлены')),
    actionButton('Старт', wrap(async () => setStatus(await api.raw('/api/telegram/start', { method: 'POST' })), 'Компаньон не запущен')),
    actionButton('Стоп', wrap(async () => setStatus(await api.raw('/api/telegram/stop', { method: 'POST' })), 'Компаньон не остановлен')),
    actionButton('Обновить статус', wrap(async () => setStatus(await api.raw('/api/telegram/status')), 'Статус недоступен'), { cls: 'btn btn-sm' }));

  const form = h('form.stack.sm', { 'data-testid': 'telegram-settings', autocomplete: 'off', onSubmit: (e) => e.preventDefault() },
    h('div.xsmall.dim', 'Отдельный бот от @BotFather. Только беседа с локальными моделями, только с указанными Telegram ID в личном чате. ',
      'Облако не используется. Компьютером, браузером и файлами из Telegram управлять нельзя.'),
    h('label.check', enabledEl, h('span', 'Telegram включён')),
    field('Токен бота', tokenEl, data.token_set ? `Сохранён (${data.token_last4}). Токен не показывается.` : 'Хранится зашифрованным на этом компьютере.'),
    h('div.grid.cols-2',
      field('Ваш Telegram ID (владелец)', ownerEl, 'Числовой ID, например от @userinfobot'),
      field('Гости (Telegram ID)', guestsEl, 'Необязательно; только беседа, без /status')),
    h('div.grid.cols-2',
      field('Лучшая (самая умная)', bestSel, 'Отвечает по умолчанию и по /best'),
      field('Самая быстрая', fastestSel, 'Отвечает по /fast и когда лучшая не успела')),
    h('div.grid.cols-2',
      field('Модель для чата по умолчанию', routeSel, 'В чате можно переключить: /best или /fast'),
      field('Ожидание ответа лучшей модели, сек', timeoutEl, '1–600; рекомендуем 180')),
    h('div.grid.cols-2',
      field('Длина ответа, токенов', tokensEl, '64–2048; GPT-OSS тратит часть на рассуждения — рекомендуем 1024'),
      h('div.field', h('span.field-label', 'Модель для фото'), visionSel, visionNote)),
    h('label.check', fallbackEl, h('span', 'Если лучшая не ответила вовремя — отвечает самая быстрая (не облако)')),
    h('label.check', { title: 'Пока недоступно' }, delegationEl,
      h('span.dim', 'Поручения Bossman из Telegram (/task) — пока недоступно')),
    field('Манера общения', personaEl, 'Как Bossman разговаривает. Правила безопасности добавляются всегда и не отключаются.'),
    h('h3.small', 'Обучение на собеседниках (только локально)'),
    h('div.xsmall.dim', 'Переписка разрешённых людей хранится зашифрованной на этом компьютере; локальная модель ',
      'строит по ней краткий профиль каждого — он подставляется только в разговоры этого же человека. ',
      'Гости сначала получают уведомление; у них есть /privacy, /pause_learning, /forget.'),
    h('div.grid.cols-2',
      field('Хранить журнал, дней', retentionEl),
      h('label.check', priorityEl, h('span', 'Владелец отвечается первым, если модель занята'))),
    peopleBox,
    h('div.row', actionButton('Экспорт для обучения (JSONL)', async () => {
      try {
        const res = await api.raw('/api/telegram/export', { method: 'POST' });
        toastOk(`Экспортировано записей: ${res.records}`, res.dir ? `Папка: ${res.dir} (секреты, почта, телефоны вычищены)` : '');
      } catch (e) { toastError(e, 'Экспорт не выполнен'); }
    }, { cls: 'btn btn-sm' })),
    h('h3.small', 'Генерация картинок (/img)'),
    h('label.check', imgEnabledEl, h('span', 'Разрешить /img — рисовать картинки локальной моделью через Bossman Studio')),
    h('div.grid.cols-2',
      h('div.field', h('span.field-label', 'Модель'), imgModelSel, imgNote),
      field('Размер', imgSizeSel)),
    h('div.grid.cols-2', field('Шаги', imgStepsSel, 'Больше шагов — дольше и детальнее'), h('div')),
    h('label.check', imgGuestsEl, h('span', 'Разрешить гостям (по умолчанию только владелец)')),
    buttons,
    statusBox);

  // Заполнить списки из живых серверов сразу (только /v1/models, без запросов к модели).
  checkModels({ quiet: true }).catch(() => {});

  return h('section.panel', { id: 'telegram' },
    h('div.panel-head', h('h2', 'Telegram')),
    h('div.panel-body', form));
}
