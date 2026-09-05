/* ============================================================
   browser.js — Feature 09: Browser Live View (Playwright, Human Take Over).
   Endpoints: POST /api/browser/sessions, GET /api/browser/sessions,
   GET /api/browser/sessions/{id}/state, POST /api/browser/sessions/{id}/act,
   GET /api/browser/sessions/{id}/screenshot, POST .../takeover|resume|stop.
   ============================================================ */

import { api, listOf } from '../api.js';
import {
  h, icon, statusBadge,
  toast, toastOk, toastError, openModal, actionButton,
  input, fmtDateShort,
} from '../components.js';
import { pageHead, errorNote, blank } from './_ui.js';

const BrowserPage = {
  id: 'browser',
  title: 'Браузер',
  icon: 'search',
  nav: 'primary',

  async render(ctx) {
    let sessions = []; let err = null; let rt = null;
    try { sessions = listOf(await api.raw('/api/browser/sessions'), 'sessions'); }
    catch (e) { err = e; }
    try { rt = await api.raw('/api/browser/health'); } catch { /* честный unknown ниже */ }

    const head = pageHead('Браузер', sessions.length ? `${sessions.length} сессий` : 'Агент работает в настоящем браузере, а вы можете в любой момент взять управление на себя.', {
      actions: [h('button.btn.btn-primary', { type: 'button', onClick: () => createSession(ctx) }, icon('plus', 14), h('span', 'Новое окно'))],
    });

    /* P1 no-fake-green: рантайм недоступен — оператор видит OFFLINE, а не пустоту */
    const offlineBanner = (!err && rt && rt.available === false)
      ? h('div.card', { style: { borderLeft: '4px solid var(--err)', padding: '10px 14px', marginBottom: '12px' } },
          statusBadge('offline'),
          h('span.small', ' Рантайм браузера недоступен (нужен Playwright + Chromium). Создание сессий завершится ошибкой.'))
      : null;

    const body = err
      ? errorNote(err, () => ctx.refresh())
      : sessions.length
        ? h('div.grid.auto-lg', sessions.map((s) => sessionCard(s, ctx)))
        : blank({
          iconName: 'search', title: rt && rt.available === false ? 'Браузер недоступен' : 'Окон браузера пока нет',
          hint: rt && rt.available === false
            ? 'Состояние: OFFLINE. Установите Playwright и Chromium, чтобы агент мог работать в браузере.'
            : 'Состояние: EMPTY — сессий нет, рантайм готов. Агент сам открывает страницы и нажимает кнопки. Вход, загрузку файлов и отправку форм он делает только с вашего разрешения, а оплату — никогда.',
        });

    return h('div.bx-page', head, offlineBanner, body);
  },

  onEvent(ev) { return ev.kind === 'agent.tool_call' && ev.tool === 'browser'; },
};

function sessionCard(s, ctx) {
  // `live` приходит с бэкенда (P1-D): строка сессии в БД переживает рестарт
  // процесса, реальный Playwright-контекст — нет. Карточка обязана показать
  // это честно, а не звать в панель, которая тут же получит 404.
  const live = s.live !== false;
  return h('div.card.clickable', {
      onClick: () => openLivePanel(s.id, ctx),
      style: { cursor: 'pointer', opacity: live ? '1' : '0.6' },
    },
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', `Сессия #${s.id}`),
        h('div.card-sub.truncate', s.current_url || 'адрес пока не открыт')),
      live
        ? statusBadge(s.status || 'created', { live: s.status === 'running' })
        : statusBadge('offline', { label: 'не активна (после рестарта)' })),
    h('div.row.tight',
      s.takeover ? h('span.badge.badge-warn', 'вы за рулём') : null,
      s.agent_id ? h('span.badge', `агент #${s.agent_id}`) : null,
      s.task_id ? h('span.badge', `задача #${s.task_id}`) : null),
    h('div.xsmall.dim', s.last_action ? `последнее действие: ${s.last_action}` : `создана ${fmtDateShort(s.created_at)}`));
}

async function createSession(ctx) {
  try {
    const r = await api.raw('/api/browser/sessions', { method: 'POST', body: {} });
    toastOk('Сессия браузера запущена');
    ctx.refresh();
    openLivePanel(r.session_id, ctx);
  } catch (e) { toastError(e, 'Не удалось запустить браузер (нужен Chromium/Playwright)'); }
}

async function screenshotBlobUrl(id) {
  // сессия едет cookie — заголовок с токеном больше не нужен
  const res = await fetch(`/api/browser/sessions/${encodeURIComponent(id)}/screenshot`, {
    cache: 'no-store', credentials: 'same-origin',
  });
  if (!res.ok) throw new Error('нет скриншота');
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

function openLivePanel(id, ctx) {
  const modal = openModal({
    title: `Браузер · сессия #${id}`, wide: true,
    body: h('div.small.dim', 'Загрузка…'), footer: h('div'),
    onClose: () => { stopped = true; if (lastUrl) URL.revokeObjectURL(lastUrl); },
  });
  let stopped = false;
  let lastUrl = null;
  let poll = null;

  const shot = h('img', {
    style: { width: '100%', borderRadius: 'var(--radius-sm)', border: '1px solid var(--line-soft)', background: 'var(--bg-elev)', display: 'block' },
    alt: 'скриншот страницы',
  });
  const urlEl = input({ placeholder: 'https://…', class: 'input mono' });
  const stateOut = h('div.xsmall.dim', 'Загрузка состояния…');
  const actionsRow = h('div.row');

  async function refreshShot() {
    try {
      const url = await screenshotBlobUrl(id);
      if (stopped) { URL.revokeObjectURL(url); return; }
      const prev = lastUrl;
      shot.src = url;
      lastUrl = url;
      if (prev) URL.revokeObjectURL(prev);
    } catch { /* пока не критично — состояние покажет причину */ }
  }

  async function refreshState() {
    let st;
    try { st = await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/state`); }
    catch (e) {
      stateOut.textContent = '';
      stateOut.appendChild(h('span', { style: { color: 'var(--err)' } }, e.message || 'сессия не активна в этом процессе'));
      actionsRow.textContent = '';
      if (e.status === 404) {
        // Сессии больше нет в этом процессе (например, после рестарта) — это
        // настоящий 404, не сбой сети. Дальше опрашивать несуществующий ID
        // бессмысленно и нечестно (см. BCC-V2-SESSION-20783913FA36-P1-FIX-001,
        // P1-D): останавливаем поллинг и даём путь восстановления.
        stopped = true;
        if (poll) clearInterval(poll);
        stateOut.appendChild(h('div.xsmall.dim', { style: { marginTop: '6px' } },
          'Эта сессия больше не существует на сервере.'));
        actionsRow.appendChild(actionButton('Обновить список', () => { modal.close(); ctx.refresh(); },
          { cls: 'btn btn-sm', iconName: 'retry' }));
        actionsRow.appendChild(actionButton('Открыть новое окно', () => { modal.close(); createSession(ctx); },
          { cls: 'btn btn-sm btn-primary', iconName: 'plus' }));
        ctx.refresh();
      }
      return;
    }
    if (stopped) return;
    urlEl.value = st.url || '';
    stateOut.textContent = '';
    stateOut.appendChild(h('div.row.tight',
      statusBadge(st.paused ? 'paused' : 'running', { live: !st.paused }),
      st.takeover ? h('span.badge.badge-warn', 'вы управляете') : null,
      h('span.xsmall.dim', st.title || '')));
    actionsRow.textContent = '';
    actionsRow.appendChild(h('div.spacer'));
    if (st.takeover) {
      actionsRow.appendChild(actionButton('Вернуть агенту', async () => {
        try { await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/resume`, { method: 'POST' }); toastOk('Управление возвращено агенту'); await refreshState(); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось вернуть управление'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'play' }));
    } else {
      actionsRow.appendChild(actionButton('Взять управление', async () => {
        try { await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/takeover`, { method: 'POST' }); toastOk('Вы взяли управление'); await refreshState(); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось перехватить управление'); }
      }, { cls: 'btn btn-sm', iconName: 'pause' }));
    }
    actionsRow.appendChild(actionButton('Закрыть окно', async () => {
      try { await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/stop`, { method: 'POST' }); toastOk('Сессия остановлена'); stopped = true; modal.close(); ctx.refresh(); }
      catch (e) { toastError(e, 'Не удалось остановить'); }
    }, { cls: 'btn btn-sm btn-danger', iconName: 'stop' }));
  }

  async function doNavigate() {
    if (!urlEl.value.trim()) { toast('Введите адрес', { type: 'warn' }); return; }
    try {
      await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/act`, {
        method: 'POST', body: { action: 'navigate', url: urlEl.value.trim(), actor: 'human' },
      });
      await Promise.all([refreshState(), refreshShot()]);
    } catch (e) { toastError(e, 'Не удалось перейти'); }
  }

  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack',
    shot,
    h('div.row.tight', urlEl, h('button.btn.btn-sm', { type: 'button', onClick: doNavigate }, icon('play', 12), h('span', 'Перейти')),
      h('button.btn.btn-sm', { type: 'button', title: 'Обновить скриншот', onClick: refreshShot }, icon('retry', 12))),
    stateOut));
  modal.footer.textContent = '';
  modal.footer.appendChild(actionsRow);
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));

  refreshShot();
  refreshState();
  poll = setInterval(() => {
    if (stopped) { clearInterval(poll); return; }
    refreshShot();
    refreshState();
  }, 3000);
}

export default BrowserPage;
