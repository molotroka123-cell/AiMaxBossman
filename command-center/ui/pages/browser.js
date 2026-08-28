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
import { pageHead, errorBanner, emptyPanel } from './_shared.js';

const BrowserPage = {
  id: 'browser',
  title: 'Браузер',
  icon: 'search',
  nav: 'primary',

  async render(ctx) {
    let sessions = []; let err = null;
    try { sessions = listOf(await api.raw('/api/browser/sessions'), 'sessions'); }
    catch (e) { err = e; }

    const head = pageHead('Браузер', sessions.length ? `${sessions.length} сессий` : 'Playwright, DOM-first, Human Take Over', [
      h('button.btn.btn-primary', { type: 'button', onClick: () => createSession(ctx) }, icon('plus', 14), h('span', 'Новая сессия')),
    ]);

    const body = err
      ? errorBanner(err, ctx)
      : sessions.length
        ? h('div.grid.auto-lg', sessions.map((s) => sessionCard(s, ctx)))
        : emptyPanel({
          iconName: 'search', title: 'Сессий пока нет',
          hint: 'Chromium с DOM-first снапшотами: navigate/click/type — авто, login/upload/submit — с подтверждением, платежи — запрещены.',
        });

    return h('div.stack.lg', head, body);
  },

  onEvent(ev) { return ev.kind === 'agent.tool_call' && ev.tool === 'browser'; },
};

function sessionCard(s, ctx) {
  return h('div.card.clickable', { onClick: () => openLivePanel(s.id, ctx), style: { cursor: 'pointer' } },
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', `Сессия #${s.id}`),
        h('div.card-sub.truncate', s.current_url || 'адрес пока не открыт')),
      statusBadge(s.status || 'created', { live: s.status === 'running' })),
    h('div.row.tight',
      s.takeover ? h('span.badge.badge-warn', 'Take Over') : null,
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
      return;
    }
    if (stopped) return;
    urlEl.value = st.url || '';
    stateOut.textContent = '';
    stateOut.appendChild(h('div.row.tight',
      statusBadge(st.paused ? 'paused' : 'running', { live: !st.paused }),
      st.takeover ? h('span.badge.badge-warn', 'Take Over активен') : null,
      h('span.xsmall.dim', st.title || '')));
    actionsRow.textContent = '';
    actionsRow.appendChild(h('div.spacer'));
    if (st.takeover) {
      actionsRow.appendChild(actionButton('Resume', async () => {
        try { await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/resume`, { method: 'POST' }); toastOk('Управление возвращено агенту'); await refreshState(); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось вернуть управление'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'play' }));
    } else {
      actionsRow.appendChild(actionButton('Take Over', async () => {
        try { await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/takeover`, { method: 'POST' }); toastOk('Вы взяли управление'); await refreshState(); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось перехватить управление'); }
      }, { cls: 'btn btn-sm', iconName: 'pause' }));
    }
    actionsRow.appendChild(actionButton('Stop', async () => {
      try { await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/stop`, { method: 'POST' }); toastOk('Сессия остановлена'); stopped = true; modal.close(); ctx.refresh(); }
      catch (e) { toastError(e, 'Не удалось остановить'); }
    }, { cls: 'btn btn-sm btn-danger', iconName: 'stop' }));
  }

  async function doNavigate() {
    if (!urlEl.value.trim()) { toast('Введите адрес', { type: 'warn' }); return; }
    try {
      await api.raw(`/api/browser/sessions/${encodeURIComponent(id)}/act`, {
        method: 'POST', body: { action: 'navigate', url: urlEl.value.trim(), actor: 'human', approved: true },
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
  const poll = setInterval(() => {
    if (stopped) { clearInterval(poll); return; }
    refreshShot();
    refreshState();
  }, 3000);
}

export default BrowserPage;
