import { api } from '../api.js';
import { h, input, openModal, toastError, toastOk } from '../components.js';
import { btn, pageHead, panel, pill } from './_ui.js';

/* Poker Vision: панель источника изображения (как «демонстрация экрана»): выбор окна/экрана с превью → живой поток захвата в Bossman →
   поверх него overlay Vision (карты, числа, кнопки, confidence). Режимы: Наблюдение / Подсказки / Управление. Пауза, смена источника, STOP.
   Это ОТОБРАЖЕНИЕ захвата, а не перенос чужого приложения в Bossman. Страница — клиент ОДНОГО backend (/api/poker-vision); логики зрения здесь нет. */
let timer = null;
let overlayTimer = null;

const MODES = [
  { value: 'observe', label: 'Наблюдение' },
  { value: 'coach', label: 'Подсказки' },
  { value: 'control', label: 'Управление' },
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

function drawOverlay(canvas, img, ov, showLabels = true) {
  const w = img.clientWidth, hgt = img.clientHeight;
  if (!ov || !w || !hgt) { const c = canvas.getContext('2d'); c.clearRect(0, 0, canvas.width, canvas.height); return; }
  canvas.width = w; canvas.height = hgt; canvas.style.width = `${w}px`; canvas.style.height = `${hgt}px`;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, hgt);
  const kx = w / ov.w, ky = hgt / ov.h;
  ctx.font = '11px system-ui, sans-serif'; ctx.lineWidth = 2;
  for (const b of ov.boxes) {
    const col = b.ok ? '#3ddc84' : '#ff5d5d';
    ctx.strokeStyle = col; ctx.strokeRect(b.x * kx, b.y * ky, b.w * kx, b.h * ky);
    if (showLabels) {
      const text = `${b.label}${b.conf != null ? ' ' + Math.round(b.conf * 100) + '%' : ''}`;
      const tw = ctx.measureText(text).width + 6, ty = Math.max(b.y * ky - 14, 0);
      ctx.fillStyle = 'rgba(0,0,0,.72)'; ctx.fillRect(b.x * kx, ty, tw, 14);
      ctx.fillStyle = col; ctx.fillText(text, b.x * kx + 3, ty + 11);
    }
  }
  canvas.dataset.boxes = String(ov.boxes.length);
}

const Page = {
  id: 'poker-vision', title: 'Poker Vision', icon: 'search', nav: 'primary', section: 'apps',
  async render(ctx) {
    clearInterval(timer); clearInterval(overlayTimer);
    let attached = false;
    let lastOv = null;
    const msg = h('p', { role: 'status', 'aria-live': 'polite', id: 'pv-msg' });
    const statusBox = h('div', { id: 'pv-status' });
    const sourceBox = h('div', { id: 'pv-source' }, h('p.dim', 'Источник не выбран.'));
    const stateBox = h('div', { id: 'pv-state' });
    const uncBox = h('div', { id: 'pv-unc' });
    const histBox = h('div', { id: 'pv-history' });
    const recBox = h('div', { id: 'pv-rec' }, h('p.dim', 'Рекомендации появятся в режиме «Подсказки» или «Управление».'));
    const journalBox = h('div', { id: 'pv-journal' });
    const stage = h('div.pv-stage', { id: 'pv-stage', style: { position: 'relative', display: 'inline-block', maxWidth: '100%', background: '#111', borderRadius: '8px', minHeight: '120px', minWidth: '240px' } });
    const stream = h('img', { id: 'pv-stream', alt: 'Живой захват выбранного источника', style: { display: 'block', maxWidth: '100%', maxHeight: '640px' } });
    const canvas = h('canvas', { id: 'pv-overlay-canvas', style: { position: 'absolute', left: '0', top: '0', pointerEvents: 'none' } });
    stage.append(stream, canvas);
    const showLabels = h('input', { type: 'checkbox', id: 'pv-labels', checked: true });
    const confirmControl = h('input', { type: 'checkbox', id: 'pv-confirm-control' });
    const calDir = input({ id: 'pv-cal-dir', placeholder: 'папка размеченных кадров (images + truth.json)' });
    const calHeld = input({ id: 'pv-cal-held', placeholder: 'папка ОТЛОЖЕННЫХ кадров (другие раздачи)' });
    const calRois = h('textarea.textarea', { id: 'pv-cal-rois', rows: 4, placeholder: '{"pot":[0.40,0.30,0.20,0.08],"hero_cards":[x,y,w,h],"board":[x,y,w,h]}  — доли кадра: стол, места, карты, банк, стеки, кнопки, поле суммы' });
    const calOut = h('pre.small', { id: 'pv-cal-out' }, 'Неизвестная раскладка: калибровка → проверка на отложенных кадрах (масштаб, тема, положение окна). Пока проверка не пройдена, все поля UNKNOWN, подсказки и управление выключены.');

    const err = (e) => (e && e.detail && (e.detail.message || e.detail.hint)) || (e && e.message) || String(e);
    const startStream = () => { stream.src = `/api/poker-vision/stream.mjpeg?ts=${Date.now()}`; };

    const startDesk = async (src, mode) => {
      try {
        await api.raw('/api/poker-vision/desk/start', { method: 'POST', body: { source: src.spec, adapter: 'poker_train', desk_mode: mode, confirm_control: confirmControl.checked } });
        msg.textContent = 'Сессия запущена.';
        startStream(); await tick();
      } catch (e) { msg.textContent = err(e); toastError(e); }
    };

    const pickSource = async () => {
      let list = [];
      try { list = (await api.raw('/api/poker-vision/sources')).sources; } catch (e) { msg.textContent = err(e); toastError(e); return; }
      const modeSel = h('select.select', { id: 'pv-pick-mode', 'aria-label': 'Режим' }, ...MODES.map(m => h('option', { value: m.value }, m.label)));
      let modal = null;
      const cards = list.map(s => h('button.pv-card', { type: 'button', 'data-source': s.id,
        style: { textAlign: 'left', border: '1px solid var(--line, #3a4655)', borderRadius: '10px', padding: '8px', background: 'transparent', cursor: 'pointer', color: 'inherit', width: '240px' },
        onClick: async () => { modal.close(); await startDesk(s, modeSel.value); } },
        s.thumbnail ? h('img', { src: s.thumbnail, alt: '', style: { width: '100%', borderRadius: '6px', display: 'block', marginBottom: '6px' } }) : h('div', { style: { height: '90px', background: '#222', borderRadius: '6px', marginBottom: '6px' } }, ''),
        h('strong', s.title), h('div.small.dim', `процесс: ${s.process}${s.rect ? ` · ${s.rect[2]}×${s.rect[3]}` : ''}`),
        h('div.xsmall', `распознавание: ${s.proof.status} · подсказки: ${s.proof.coach ? 'да' : 'нет'} · управление: ${s.proof.control ? 'только тест без денег' : 'нет'}`),
        h('div.xsmall.dim', s.note || '')));
      modal = openModal({ title: 'Выбрать источник', wide: true,
        body: h('div.stack', h('p.small', 'Любое доступное для захвата окно или экран можно показать. Распознавание покера, подсказки и управление включаются отдельно для каждого интерфейса — только там, где есть подтверждающий прогон; остальное UNVERIFIED.'),
          h('div.row', h('span', 'Режим при старте:'), modeSel),
          h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '12px' } }, ...cards)) });
    };

    const changeMode = async (mode) => {
      try { await api.raw('/api/poker-vision/desk/mode', { method: 'POST', body: { mode, confirm_control: confirmControl.checked } }); msg.textContent = `Режим: ${mode}.`; await tick(); }
      catch (e) { msg.textContent = err(e); toastError(e); }
    };
    const setPause = async (p) => { try { await api.raw('/api/poker-vision/desk/pause', { method: 'POST', body: { paused: p } }); await tick(); } catch (e) { toastError(e); } };
    const stop = async () => {
      try { const r = await api.raw('/api/poker-vision/stop', { method: 'POST', body: {} }); msg.textContent = `STOP выполнен (${r.via}).`; toastOk('STOP'); await tick(); }
      catch (e) { toastError(e, 'STOP не дошёл'); }
    };
    const startService = async () => {
      try { await api.raw('/api/poker-vision/service/start', { method: 'POST', body: {} }); msg.textContent = 'Сервис запущен.'; }
      catch (e) { msg.textContent = err(e); toastError(e); }
    };
    const sandbox = async (cmd, args = {}) => {
      try { const r = await api.raw(`/api/poker-vision/desk/sandbox/${cmd}`, { method: 'POST', body: { args } }); msg.textContent = `Тестовое окно: ${cmd} → ${r.ok ? 'ок' : r.error}`; }
      catch (e) { msg.textContent = err(e); }
    };
    const calibrate = async () => {
      try {
        const r = await api.raw('/api/poker-vision/calibrate', { method: 'POST', body: { adapter: 'ton_poker', labelled_dir: calDir.value.trim(), heldout_dir: calHeld.value.trim(), rois: JSON.parse(calRois.value || '{}') } });
        calOut.textContent = JSON.stringify(r, null, 1);
      } catch (e) { calOut.textContent = err(e); }
    };

    const modeBar = h('div.row', { id: 'pv-modes', role: 'group', 'aria-label': 'Режим' });
    const renderModeBar = (desk) => {
      modeBar.replaceChildren(...MODES.map(m => {
        const allowed = !desk ? true : m.value === 'observe' ? true : (m.value === 'coach' ? desk.coach_allowed[0] : desk.control_allowed[0]);
        const why = !desk ? '' : (m.value === 'coach' ? desk.coach_allowed[1] : m.value === 'control' ? desk.control_allowed[1] : '');
        const on = desk && desk.mode === m.value;
        return h('button', { type: 'button', 'data-mode': m.value, disabled: !desk || !allowed, 'aria-pressed': on ? 'true' : 'false',
          title: allowed ? '' : why, class: on ? 'btn btn-primary' : 'btn', onClick: () => changeMode(m.value) }, m.label);
      }));
    };
    renderModeBar(null);

    const tick = async () => {
      if (stage.isConnected) attached = true;
      else { if (attached) { clearInterval(timer); clearInterval(overlayTimer); timer = overlayTimer = null; } return; }
      let st;
      try { st = await api.raw('/api/poker-vision/status'); } catch { return; }
      const s = st.session; const desk = s && s.desk;
      statusBox.replaceChildren(h('div.row',
        pill(st.service_up ? 'сервис работает' : 'сервис остановлен', { tone: st.service_up ? 'ok' : 'err' }),
        s ? pill(s.running ? 'идёт сессия' : (s.finished ? 'сессия завершена' : 'нет сессии'), { tone: s.running ? 'info' : 'idle', live: s.running }) : null,
        s?.stopped ? pill('STOP нажат', { tone: 'err' }) : null,
        desk?.paused ? pill('пауза', { tone: 'warn' }) : null,
        s ? pill(`кадров ${s.frames}`, { tone: 'idle' }) : null,
        s?.latency_ms?.p50 != null ? pill(`p50 ${s.latency_ms.p50} мс · p95 ${s.latency_ms.p95} мс`, { tone: 'idle' }) : null,
        s?.layout ? pill(`раскладка ${s.layout}`, { tone: 'idle' }) : null,
        desk?.sha ? pill(`SHA ${String(desk.sha).slice(0, 8)}`, { tone: 'idle' }) : null,
        desk?.lost ? pill(`источник потерян: ${desk.lost}`, { tone: 'err' }) : null,
        desk?.executor?.halted ? pill(`управление остановлено: ${desk.executor.halted}`, { tone: 'err' }) : null,
        s?.error ? pill(`ошибка: ${s.error}`, { tone: 'err' }) : null));
      renderModeBar(desk);
      if (desk) {
        const id = desk.identity;
        sourceBox.replaceChildren(
          h('div.small', `${desk.source.kind === 'sandbox' ? 'Мой Poker Train в тестовом рабочем столе' : desk.source.kind === 'replay' ? 'Запись' : 'Окно'} · режим: ${MODES.find(m => m.value === desk.mode)?.label}`),
          id ? h('div.xsmall.dim', `идентичность: процесс ${id.process} · pid ${id.pid} · handle ${id.handle} · старт ${new Date(id.started_at * 1000).toLocaleTimeString()}`) : null,
          desk.window_reasons.length ? h('div.small', { style: { color: '#ff8a8a' } }, `окно: ${desk.window_reasons.join('; ')} — управление остановлено`) : h('div.xsmall.dim', 'окно: проверки пройдены'),
          desk.executor ? h('div.xsmall.dim', `действий выполнено: ${desk.executor.n_actions}`) : null,
          desk.executor?.halted ? h('div.row', btn('Снять остановку после осмотра', async () => { await api.raw('/api/poker-vision/desk/resume-executor', { method: 'POST', body: {} }); await tick(); }, { variant: 'secondary' })) : null);
      }
      if (!st.service_up) return;
      try {
        const state = await api.raw('/api/poker-vision/state');
        if (state.fields) {
          stateBox.replaceChildren(fieldRows(state.fields),
            h('p.small', `кадр ${state.frame} · можно действовать: ${state.can_act ? 'да' : 'нет'} · ` + (state.committed?.blocked?.length ? `заблокировано: ${state.committed.blocked.join('; ')}` : 'блокировок нет')));
          uncBox.replaceChildren(state.uncertainty.length
            ? h('ul', ...state.uncertainty.map(u => h('li', { 'data-unc': u.field }, `${u.field}: ${u.why || u.reason}`)))
            : h('p.dim', 'Все поля кадра распознаны.'));
        }
        const rec = await api.raw('/api/poker-vision/recommendation');
        if (rec.ok) {
          recBox.replaceChildren(
            h('div', { 'data-rec-action': rec.action }, h('strong', `Рекомендация: ${rec.action}${rec.raise_to ? ' до ' + rec.raise_to : ''}`), h('span.dim', `  ·  эквити ≈ ${Math.round(rec.equity * 100)}% против случайных рук, шансы банка ${Math.round(rec.pot_odds * 100)}%`)),
            h('p.small', rec.explanation),
            h('table.pv-fields', h('tbody', rec.options.map(o => h('tr', { class: o.chosen ? 'pv-ok' : '' }, h('td', (o.chosen ? '▶ ' : '') + o.action), h('td', o.amount != null ? String(o.amount) : '—'), h('td', o.note))))),
            rec.uncertainty.length ? h('ul.small', ...rec.uncertainty.map(u => h('li', u))) : null,
            h('p.xsmall.dim', rec.disclaimer));
        } else {
          recBox.replaceChildren(h('p.dim', { 'data-rec-none': '1' }, rec.why_not || 'нет рекомендации'));
        }
        const hist = await api.raw('/api/poker-vision/history');
        histBox.replaceChildren(h('p.small', `раздач в истории: ${hist.hands.length} · решений тренажёра: ${hist.decisions.length}`),
          ...hist.hands.slice(-8).map(x => h('details', { 'data-hand': x.id },
            h('summary', `раздача #${x.id} · ${x.status} · кадров ${x.frames} · ${x.complete === false ? 'неполная' : 'полнота не доказана'}${x.linked === false ? ' · не связана с прошлой' : ''}`),
            h('pre.small', x.events.map(e => JSON.stringify(e)).join('\n') || 'событий не наблюдено'),
            h('p.xsmall.dim', `не наблюдаемо: ${(x.unobservable || []).join('; ')}`))));
        if (desk && desk.mode === 'control') {
          const j = await api.raw('/api/poker-vision/journal');
          const acts = j.records.filter(r => r.event === 'action' || r.event === 'halt' || r.event === 'decision_dropped');
          journalBox.replaceChildren(h('p.xsmall.dim', `журнал решений · SHA ${String(j.sha).slice(0, 12)} · записей ${acts.length}`),
            ...acts.slice(-8).map(r => h('div.small', { 'data-journal': r.event }, r.event === 'action'
              ? `${r.decision.kind}${r.decision.raise_to ? ' до ' + r.decision.raise_to : ''} → ${r.label}: ${r.verified ? 'подтверждено' : 'НЕ подтверждено'} (${r.verify}) · ${r.latency_ms} мс · кадры ${r.before} → ${r.after}`
              : `${r.event}: ${r.reason}`)));
        }
      } catch { /* следующий тик */ }
    };
    const pollOverlay = async () => {
      if (!stage.isConnected) return;
      try { lastOv = await api.raw('/api/poker-vision/overlay.json'); drawOverlay(canvas, stream, lastOv, showLabels.checked); } catch { /* ещё нет кадра */ }
    };
    stream.addEventListener('load', () => drawOverlay(canvas, stream, lastOv, showLabels.checked));

    const sbtn = (label, cmd, args) => btn(label, () => sandbox(cmd, args), { variant: 'secondary' });
    const sandboxPanel = panel('Тестовое окно (двойник ОС для проверки)', h('div.stack',
      h('p.xsmall.dim', 'Имитирует действия владельца над окном: перемещение, размер, перекрытие, сворачивание, закрытие и повторное открытие. Это тестовый двойник, а не поведение Windows.'),
      h('div.row', sbtn('Сдвинуть', 'move', { x: 160, y: 30 }), sbtn('Вернуть', 'move', { x: 40, y: 0 }), sbtn('Размер 420×760', 'resize', { w_: 420, h_: 760 }), sbtn('Размер 520×900', 'resize', { w_: 520, h_: 900 }),
        sbtn('Перекрыть', 'cover', { cid: 'cover1', rect: [120, 500, 400, 300] }), sbtn('Убрать перекрытие', 'uncover', { cid: 'cover1' }),
        sbtn('Свернуть', 'minimize', { on: true }), sbtn('Развернуть', 'minimize', { on: false }), sbtn('Закрыть', 'close', {}), sbtn('Открыть снова', 'reopen', {}))));

    try { await api.raw('/api/poker-vision/capabilities'); } catch (e) { msg.textContent = err(e) || 'Сервис Poker Vision не запущен'; }
    timer = setInterval(tick, 700);
    overlayTimer = setInterval(pollOverlay, 250);

    return h('div.bx-page.pv-page',
      pageHead('Poker Vision', 'Источник → поток захвата → поля с уверенностью → проверенное состояние → рекомендация → (только в моём тренажёре) действие и проверка. UNKNOWN — нормальный ответ.', {
        pills: [pill('внешние клиенты: только наблюдение', { tone: 'warn' })],
        actions: [btn('Запустить сервис', startService, { variant: 'secondary' }), btn('STOP', stop, { variant: 'danger', iconName: 'stop' })],
      }),
      panel('Источник изображения', h('div.stack',
        h('div.row', btn('Выбрать источник', pickSource, { variant: 'primary' }), btn('Сменить источник', async () => { await stop(); await pickSource(); }, { variant: 'secondary' }),
          btn('Пауза', () => setPause(true), { variant: 'secondary' }), btn('Продолжить', () => setPause(false), { variant: 'secondary' }), modeBar),
        h('label.row', confirmControl, h('span', 'Разрешаю режим «Управление» только в моём Poker Train (тестовые игры без денег). Во внешних клиентах он недоступен.')),
        h('label.row', showLabels, h('span', 'Подписи overlay')),
        msg, sourceBox, stage)),
      panel('Состояние сессии', statusBox),
      panel('Рекомендация (Подсказки / Управление)', recBox),
      panel('Журнал решений (Управление)', journalBox),
      panel('Распознанные поля: значение · уверенность · время · источник', stateBox),
      panel('Почему не знаю (объяснение неопределённости)', uncBox),
      panel('История раздач (наблюдённые события, пропуски помечены)', histBox),
      sandboxPanel,
      panel('Калибровка нового окна (например TON Poker): только наблюдение/replay', h('div.stack', calDir, calHeld, calRois, h('div.row', btn('Калибровать и проверить', calibrate)), calOut)));
  },
  onEvent() { return false; },
};

export default Page;
