import { api } from '../api.js';
import { h, input, select, toastError, toastOk } from '../components.js';
import { btn, pageHead, panel, pill } from './_ui.js';

/* Poker Vision: выбор источника, live/replay, наложение распознанных полей, история, объяснение неопределённости и STOP.
   Страница — клиент ОДНОГО backend (/api/poker-vision → приложение poker-vision); логики зрения здесь нет. */
let timer = null;

const MODES = [
  { value: 'replay', label: 'Replay: записанные кадры/видео' },
  { value: 'trainer', label: 'Живой: мой Poker Train (loopback)' },
  { value: 'window', label: 'Окно: только наблюдение' },
];

function fieldRows(fields) {
  const rows = [];
  const add = (name, f) => {
    if (Array.isArray(f)) { f.forEach((x, i) => add(`${name}[${i}]`, x)); return; }
    const known = f.status === 'OK';
    rows.push(h('tr', { 'data-field': name, class: known ? 'pv-ok' : 'pv-unknown' },
      h('td', name),
      h('td.pv-val', known ? (typeof f.value === 'object' ? JSON.stringify(f.value) : String(f.value)) : 'UNKNOWN'),
      h('td', known ? `${Math.round(f.confidence * 100)}%` : '—'),
      h('td', `${f.t_ms} мс`), h('td', f.source || ''),
      h('td', known ? '' : (f.why || f.reason || ''))));
  };
  Object.entries(fields).forEach(([k, v]) => add(k, v));
  return h('table.pv-fields', h('thead', h('tr', ...['поле', 'значение', 'уверенность', 'время кадра', 'источник', 'почему не знаю'].map(t => h('th', t)))), h('tbody', rows));
}

const Page = {
  id: 'poker-vision', title: 'Poker Vision', icon: 'search', nav: 'primary', section: 'apps',
  async render(ctx) {
    clearInterval(timer);
    const msg = h('p', { role: 'status', 'aria-live': 'polite', id: 'pv-msg' });
    const overlay = h('img.pv-overlay', { alt: 'Кадр с наложением распознанных полей', id: 'pv-overlay', style: { maxWidth: '100%', maxHeight: '620px', background: '#111', borderRadius: '8px' } });
    const statusBox = h('div', { id: 'pv-status' });
    const stateBox = h('div', { id: 'pv-state' });
    const uncBox = h('div', { id: 'pv-unc' });
    const histBox = h('div', { id: 'pv-history' });
    const mode = select(MODES, { id: 'pv-mode', 'aria-label': 'Режим' });
    const adapter = select([{ value: 'poker_train', label: 'Poker Train (мой тренажёр)' }, { value: 'ton_poker', label: 'TON Poker (наблюдение/replay, нужна калибровка)' }], { id: 'pv-adapter', 'aria-label': 'Адаптер' });
    const target = input({ id: 'pv-target', placeholder: 'папка кадров / видео  ·  или http://127.0.0.1:3000/', 'aria-label': 'Источник' });
    const windows = select([{ value: '', label: '— окно не выбрано —' }], { id: 'pv-window', 'aria-label': 'Окно' });
    const act = h('input', { type: 'checkbox', id: 'pv-act' });
    const boot = h('input', { type: 'checkbox', id: 'pv-boot' });
    const calDir = input({ id: 'pv-cal-dir', placeholder: 'папка размеченных кадров (images + truth.json)' });
    const calHeld = input({ id: 'pv-cal-held', placeholder: 'папка ОТЛОЖЕННЫХ кадров (другие раздачи)' });
    const calRois = h('textarea.textarea', { id: 'pv-cal-rois', rows: 4, placeholder: '{"pot":[0.40,0.30,0.20,0.08],"hero_cards":[x,y,w,h],"board":[x,y,w,h]}  — доли кадра' });
    const calOut = h('pre.small', { id: 'pv-cal-out' }, 'Неизвестная раскладка: калибровка → проверка на отложенных кадрах. Пока проверка не пройдена, все поля UNKNOWN.');
    const calibrate = async () => {
      try {
        const r = await api.raw('/api/poker-vision/calibrate', { method: 'POST', body: { adapter: adapter.value === 'ton_poker' ? 'ton_poker' : 'ton_poker', labelled_dir: calDir.value.trim(), heldout_dir: calHeld.value.trim(), rois: JSON.parse(calRois.value || '{}') } });
        calOut.textContent = JSON.stringify(r, null, 1);
      } catch (e) { calOut.textContent = (e.detail && e.detail.message) || e.message; }
    };
    let caps = null;
    let attached = false;

    const refreshCaps = async () => {
      try {
        caps = await api.raw('/api/poker-vision/capabilities');
        windows.replaceChildren(h('option', { value: '' }, '— окно не выбрано —'),
          ...(caps.windows || []).map(w => h('option', { value: JSON.stringify(w) }, `${w.title} (${w.rect[2]}×${w.rect[3]})`)));
        if (!caps.windows?.length) msg.textContent = `Список окон: ${caps.window_support?.enumeration ? 'пусто' : 'недоступен на этой платформе (' + caps.window_support?.platform + ')'}.`;
      } catch (e) { msg.textContent = e.message || 'Сервис Poker Vision не запущен'; }
    };

    const start = async () => {
      const body = { mode: mode.value, adapter: adapter.value, act: act.checked };
      if (mode.value === 'replay') body.path = target.value.trim();
      if (mode.value === 'trainer') { body.url = target.value.trim() || 'http://127.0.0.1:3000/'; if (boot.checked) body.bootstrap = 'cash_nl10'; }
      if (mode.value === 'window') { if (!windows.value) { msg.textContent = 'Выберите окно.'; return; } body.window = JSON.parse(windows.value); }
      try { await api.raw('/api/poker-vision/session', { method: 'POST', body }); msg.textContent = 'Сессия запущена.'; await tick(); }
      catch (e) { msg.textContent = (e.detail && e.detail.message) || e.message; toastError(e); }
    };
    const stop = async () => {
      try { const r = await api.raw('/api/poker-vision/stop', { method: 'POST', body: {} }); msg.textContent = `STOP выполнен (${r.via}).`; toastOk('STOP'); await tick(); }
      catch (e) { toastError(e, 'STOP не дошёл'); }
    };
    const startService = async () => {
      try { await api.raw('/api/poker-vision/service/start', { method: 'POST', body: {} }); msg.textContent = 'Сервис запущен.'; await refreshCaps(); }
      catch (e) { msg.textContent = (e.detail && (e.detail.message || e.detail.hint)) || e.message; toastError(e); }
    };

    const tick = async () => {
      // страница ещё не вставлена в DOM (render → await refreshCaps → attach): не гасим таймер, пока она не была подключена
      if (statusBox.isConnected) attached = true;
      else { if (attached) { clearInterval(timer); timer = null; } return; }
      let st;
      try { st = await api.raw('/api/poker-vision/status'); } catch { return; }
      const s = st.session;
      statusBox.replaceChildren(h('div.row',
        pill(st.service_up ? 'сервис работает' : 'сервис остановлен', { tone: st.service_up ? 'ok' : 'err' }),
        s ? pill(s.running ? 'идёт сессия' : (s.finished ? 'сессия завершена' : 'нет сессии'), { tone: s.running ? 'info' : 'idle', live: s.running }) : null,
        s?.stopped ? pill('STOP нажат', { tone: 'err' }) : null,
        s ? pill(`кадров ${s.frames}`, { tone: 'idle' }) : null,
        s?.latency_ms?.p50 != null ? pill(`p50 ${s.latency_ms.p50} мс · p95 ${s.latency_ms.p95} мс`, { tone: 'idle' }) : null,
        s?.layout ? pill(`раскладка ${s.layout}`, { tone: 'idle' }) : null,
        s?.actuator?.halted ? pill(`действия остановлены: ${s.actuator.halted}`, { tone: 'err' }) : null,
        s?.error ? pill(`ошибка: ${s.error}`, { tone: 'err' }) : null));
      if (!st.service_up) return;
      try {
        const state = await api.raw('/api/poker-vision/state');
        if (state.fields) {
          stateBox.replaceChildren(fieldRows(state.fields),
            h('p.small', `кадр ${state.frame} · можно действовать: ${state.can_act ? 'да' : 'нет'} · ` + (state.committed?.blocked?.length ? `заблокировано: ${state.committed.blocked.join('; ')}` : 'блокировок нет')));
          uncBox.replaceChildren(state.uncertainty.length
            ? h('ul', ...state.uncertainty.map(u => h('li', { 'data-unc': u.field }, `${u.field}: ${u.why || u.reason}`)))
            : h('p.dim', 'Все поля кадра распознаны.'));
          overlay.src = `/api/poker-vision/overlay.png?ts=${Date.now()}`;
        }
        const hist = await api.raw('/api/poker-vision/history');
        histBox.replaceChildren(h('p.small', `раздач в истории: ${hist.hands.length} · решений тренажёра: ${hist.decisions.length}`),
          ...hist.hands.slice(-8).map(x => h('details', { 'data-hand': x.id },
            h('summary', `раздача #${x.id} · ${x.status} · кадров ${x.frames} · ${x.complete === false ? 'неполная' : 'полнота не доказана'}${x.linked === false ? ' · не связана с прошлой' : ''}`),
            h('pre.small', x.events.map(e => JSON.stringify(e)).join('\n') || 'событий не наблюдено'),
            h('p.xsmall.dim', `не наблюдаемо: ${(x.unobservable || []).join('; ')}`))));
      } catch { /* следующий тик */ }
    };

    await refreshCaps();
    timer = setInterval(tick, 700);
    tick();
    return h('div.bx-page.pv-page',
      pageHead('Poker Vision', 'Экран → область стола → поля → сверка по кадрам → проверенное состояние → история. UNKNOWN — нормальный ответ.', {
        pills: [pill('внешние клиенты: только наблюдение', { tone: 'warn' })],
        actions: [btn('Запустить сервис', startService, { variant: 'secondary' }), btn('STOP', stop, { variant: 'danger', iconName: 'stop' })],
      }),
      panel('Источник', h('div.stack',
        h('div.row', mode, adapter, target, windows, btn('Обновить окна', refreshCaps)),
        h('label.row', act, h('span', 'Разрешить автоматические действия (только мой Poker Train, loopback; во внешнем клиенте запрещено)')),
        h('label.row', boot, h('span', 'Сам открыть стол NL10 в моём тренажёре (создаст тестовый профиль в его localStorage)')),
        h('div.row', btn('Старт', start, { variant: 'primary' })), msg)),
      panel('Состояние сессии', statusBox),
      h('div.pv-grid', { style: { display: 'grid', gridTemplateColumns: 'minmax(260px, 520px) 1fr', gap: '16px', alignItems: 'start' } },
        panel('Кадр с наложением (зелёное — прочитано, красное — UNKNOWN)', overlay),
        panel('Распознанные поля: значение · уверенность · время · источник', stateBox)),
      panel('Почему не знаю (объяснение неопределённости)', uncBox),
      panel('История раздач (наблюдённые события, пропуски помечены)', histBox),
      panel('Калибровка новой раскладки (например TON Poker): только наблюдение/replay', h('div.stack', calDir, calHeld, calRois, h('div.row', btn('Калибровать и проверить', calibrate)), calOut)));
  },
  onEvent() { return false; },
};

export default Page;
