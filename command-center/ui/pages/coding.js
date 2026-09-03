/* ============================================================
   coding.js — Coding Sessions: изолированные git-worktree сессии.
   Цепочка оператора: Session → Activity → Diff → Merge/Discard.
   Endpoints: GET/POST /api/coding-sessions, GET /api/coding-sessions/{id},
   GET .../diff, POST .../merge_preview | merge | discard.
   Бэкенд — единственный CodingWorktreeManager, второго движка нет.
   ============================================================ */

import { api, listOf } from '../api.js';
import {
  h, icon, statusBadge,
  toast, toastOk, toastError, openModal, actionButton,
  input, fmtDateShort,
} from '../components.js';
import { pageHead, errorNote, blank } from './_ui.js';

const CodingPage = {
  id: 'coding',
  title: 'Coding-сессии',
  icon: 'edit',
  nav: 'more',

  async render(ctx) {
    let sessions = []; let err = null;
    try { sessions = listOf(await api.raw('/api/coding-sessions'), 'sessions'); }
    catch (e) { err = e; }

    const active = sessions.filter((s) => (s.status || 'active') === 'active').length;
    const head = pageHead('Coding-сессии', active
      ? `${active} активных · изоляция агента в отдельном git-worktree`
      : 'Изоляция агента в отдельном git-worktree: диф, ревью и merge без риска для исходного дерева.', {
      actions: [
        h('button.btn', { type: 'button', title: 'Обновить', 'aria-label': 'Обновить', onClick: () => ctx.refresh() }, icon('retry', 14)),
        h('button.btn.btn-primary', { type: 'button', onClick: () => createModal(ctx) }, icon('plus', 14), h('span', 'Новая сессия')),
      ],
    });

    const body = err
      ? errorNote(err, () => ctx.refresh())
      : sessions.length
        ? h('div.grid.auto-lg', sessions.map((s) => sessionCard(s, ctx)))
        : blank({
          iconName: 'apps', title: 'Сессий пока нет',
          hint: 'Создайте сессию для репозитория из разрешённых корней: агент получит отдельный worktree, а вы — честный diff и безопасный merge.',
        });

    return h('div.bx-page', head, body);
  },

  onEvent(ev) { return typeof ev.kind === 'string' && ev.kind.startsWith('coding.session'); },
};

function sessionCard(s, ctx) {
  const status = s.status || 'active';
  return h('div.card',
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', s.session_id || '—'),
        h('div.card-sub.truncate', `${s.branch || ''} ← ${s.source_repo || ''}`)),
      statusBadge(status, { live: status === 'active' })),
    h('div.row.tight',
      h('span.badge', `база: ${String(s.base_ref || '').slice(0, 10) || '—'}`),
      s.created_at ? h('span.xsmall.dim', `создана ${fmtDateShort(s.created_at * 1000 || s.created_at)}`) : null),
    h('div.row.tight', status === 'active' ? [
      actionButton('Diff', () => showDiff(s, ctx), { cls: 'btn btn-sm', iconName: 'search' }),
      actionButton('Merge', () => doMerge(s, ctx), { cls: 'btn btn-sm btn-primary', iconName: 'play' }),
      actionButton('Отбросить', () => doDiscard(s, ctx), { cls: 'btn btn-sm btn-danger', iconName: 'stop' }),
    ] : [h('span.xsmall.dim', 'сессия закрыта — доступен только журнал')]));
}

function createModal(ctx) {
  const idEl = input({ placeholder: 'имя сессии, например fix-login' });
  const repoEl = input({ placeholder: 'путь к git-репозиторию из разрешённых корней' });
  const baseEl = input({ placeholder: 'HEAD', value: 'HEAD' });
  const modal = openModal({
    title: 'Новая coding-сессия',
    body: h('div.stack',
      h('div', h('div.section-title', 'Имя'), idEl),
      h('div', h('div.section-title', 'Репозиторий'), repoEl),
      h('div', h('div.section-title', 'Базовый ref'), baseEl)),
    footer: h('div'),
  });
  modal.footer.appendChild(actionButton('Создать', async () => {
    try {
      await api.raw('/api/coding-sessions', {
        method: 'POST',
        body: { session_id: idEl.value.trim(), source_repo: repoEl.value.trim(), base_ref: baseEl.value.trim() || 'HEAD' },
      });
      toastOk('Сессия создана — worktree готов');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось создать сессию'); }
  }, { cls: 'btn btn-primary', iconName: 'plus' }));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'));
}

async function showDiff(s, ctx) {
  const modal = openModal({ title: `Diff · ${s.session_id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  let d;
  try { d = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/diff`); }
  catch (e) { modal.body.textContent = ''; modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'нет дифа')); return; }
  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack',
    h('div.row.tight',
      h('span.badge', `файлов: ${(d.files || []).length}`),
      d.truncated ? h('span.badge.badge-warn', 'диф обрезан (400 КБ)') : null),
    d.stat ? h('pre.block', d.stat) : null,
    d.patch ? h('pre.block', { style: { maxHeight: '55vh', overflow: 'auto' } }, d.patch) : h('div.small.dim', 'Изменений против базы нет.')));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
}

async function doMerge(s, ctx) {
  let preview;
  try { preview = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/merge_preview`, { method: 'POST', body: {} }); }
  catch (e) { toastError(e, 'Не удалось получить превью слияния'); return; }
  if (preview && preview.clean === false) {
    toast('Merge отклонён политикой: конфликты не вливаются принудительно', { type: 'warn' });
    return;
  }
  try {
    const r = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/merge`, { method: 'POST', body: {} });
    toastOk(`Слито в ${r.into || 'базу'} · ${(r.head || '').slice(0, 10)}`);
    ctx.refresh();
  } catch (e) { toastError(e, 'Merge отклонён (конфликты или ошибка git)'); }
}

async function doDiscard(s, ctx) {
  try {
    await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/discard`, { method: 'POST', body: {} });
    toastOk('Сессия отброшена: worktree и ветка удалены');
    ctx.refresh();
  } catch (e) { toastError(e, 'Не удалось отбросить сессию'); }
}

export default CodingPage;
