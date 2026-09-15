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
  input, textarea, fmtDateShort,
} from '../components.js';
import { pageHead, errorNote, blank } from './_ui.js';

const CodingPage = {
  id: 'coding',
  title: 'Coding-сессии',
  icon: 'edit',
  nav: 'more',
  section: 'studio',

  async render(ctx) {
    let sessions = []; let err = null;
    try { sessions = listOf(await api.raw('/api/coding-sessions'), 'sessions'); }
    catch (e) { err = e; }
    /* Путь «поручить задачу агенту» (аудит владельца 2026-09-08, F4): раньше
       страница управляла только worktree, и до OpenHands из интерфейса было
       не дотянуться. Готовность читается с сервера и показывается честно. */
    let ready = { available: false, reason: 'готовность не прочитана' }; let tasks = [];
    try { ready = await api.raw('/api/coding-tasks/readiness'); } catch (e) { ready = { available: false, reason: e.message || String(e) }; }
    try { tasks = listOf(await api.raw('/api/coding-tasks'), 'items'); } catch (e) { tasks = []; }

    const active = sessions.filter((s) => (s.status || 'active') === 'active').length;
    const head = pageHead('Coding-сессии', active
      ? `${active} активных · изоляция агента в отдельном git-worktree`
      : 'Изоляция агента в отдельном git-worktree: диф, ревью и merge без риска для исходного дерева.', {
      actions: [
        h('button.btn', { type: 'button', title: 'Обновить', 'aria-label': 'Обновить', onClick: () => ctx.refresh() }, icon('retry', 14)),
        h('button.btn', { type: 'button', onClick: () => createModal(ctx) }, icon('plus', 14), h('span', 'Новая сессия')),
        h('button.btn.btn-primary', { type: 'button', id: 'coding-new-task', disabled: !ready.available,
          title: ready.available ? 'Поручить задачу агенту OpenHands' : (ready.reason || 'OpenHands недоступен'),
          onClick: () => taskModal(ctx, ready) }, icon('bolt', 14), h('span', 'Новая задача агенту')),
      ],
    });

    const agentBlock = h('div.stack', { id: 'coding-tasks' },
      h('div.section-title', 'Задачи агенту (OpenHands)'),
      ready.available
        ? h('div.small.dim', `Агент работает в одноразовой копии репозитория без remote; результат — патч и доказательства. Push/merge/deploy у агента нет. Корни: ${(ready.roots || []).join(', ') || '—'}`)
        : h('div.small', { style: { color: 'var(--warn,#d99a2b)' }, id: 'coding-readiness' },
          `OpenHands сейчас недоступен: ${ready.reason || 'причина не названа'}`),
      tasks.length
        ? h('div.grid.auto-lg', tasks.map((t) => taskCard(t, ctx)))
        : h('div.small.dim', 'Задач агенту пока не было.'));

    const body = err
      ? errorNote(err, () => ctx.refresh())
      : sessions.length
        ? h('div.grid.auto-lg', sessions.map((s) => sessionCard(s, ctx)))
        : blank({
          iconName: 'apps', title: 'Сессий пока нет',
          hint: 'Создайте сессию для репозитория из разрешённых корней: агент получит отдельный worktree, а вы — честный diff и безопасный merge.',
        });

    return h('div.bx-page', head, agentBlock, h('div.section-title', 'Worktree-сессии'), body);
  },

  onEvent(ev) { return typeof ev.kind === 'string' && (ev.kind.startsWith('coding.session') || ev.kind.startsWith('coding.task')); },
};

const TASK_LABEL = { running: 'выполняется', completed: 'выполнено', failed: 'сбой', blocked: 'заблокировано' };

function taskCard(t, ctx) {
  const status = t.status || 'running';
  const cleanup = t.sandbox_cleanup || null;
  return h('div.card', { 'data-coding-task': t.id },
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title.truncate', t.instruction || '—'),
        h('div.card-sub.truncate', t.source_repo || '')),
      statusBadge(status, { live: status === 'running', label: TASK_LABEL[status] || status })),
    h('div.row.tight',
      h('span.badge', `файлов: ${(t.changed_files || []).length}`),
      h('span.badge', `область: ${(t.allowed_paths || []).join(', ') || '—'}`),
      (t.protected_paths || []).length ? h('span.badge', `защищено: ${t.protected_paths.join(', ')}`) : null,
      cleanup ? h('span.badge' + (cleanup.removed ? '' : '.badge-warn'),
        cleanup.removed ? 'песочница удалена' : `песочница осталась: ${cleanup.error || cleanup.path || ''}`) : null),
    t.error ? h('div.small', { style: { color: status === 'blocked' ? 'var(--warn,#d99a2b)' : 'var(--err)' } }, t.error) : null,
    h('div.row.tight',
      status !== 'running' ? actionButton('Diff и доказательства', () => showTask(t, ctx), { cls: 'btn btn-sm', iconName: 'search' }) : h('span.xsmall.dim', 'агент работает…')));
}

async function showTask(t, ctx) {
  const modal = openModal({ title: `Задача агенту · ${t.id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  let d;
  try { d = await api.raw(`/api/coding-tasks/${encodeURIComponent(t.id)}`); }
  catch (e) { modal.body.textContent = ''; modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'нет данных')); return; }
  modal.body.textContent = '';
  const ev = d.evidence || {};
  modal.body.appendChild(h('div.stack',
    h('div.row.tight',
      statusBadge(d.status, { label: TASK_LABEL[d.status] || d.status }),
      h('span.badge', `файлов: ${(d.changed_files || []).length}`),
      d.duration_seconds != null ? h('span.badge', `${d.duration_seconds} с`) : null,
      h('span.badge', 'push/merge: нет')),
    d.error ? h('div.small', { style: { color: 'var(--err)' } }, d.error) : null,
    (d.changed_files || []).length ? h('pre.block', d.changed_files.join('\n')) : null,
    d.diff ? h('pre.block', { style: { maxHeight: '45vh', overflow: 'auto' } }, d.diff) : h('div.small.dim', 'Патча нет.'),
    h('div.section-title', 'Доказательства'),
    h('pre.block', JSON.stringify({ head_before: ev.head_before, head_after: ev.head_after,
      untracked: ev.untracked_files, sidecar: d.sidecar, sandbox_cleanup: d.sandbox_cleanup }, null, 1)),
    h('div.small.dim', 'Патч — доказательство работы агента. Применение к репозиторию — отдельное решение владельца через worktree-сессию.')));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
}

function taskModal(ctx, ready) {
  const repoEl = input({ placeholder: 'путь к git-репозиторию из разрешённых корней', value: (ready.roots || [])[0] || '' });
  const instrEl = textarea({ rows: '4', placeholder: 'Что сделать: например, добавить тест на парсер дат и починить падение' });
  const allowEl = input({ placeholder: 'разрешённые пути через запятую, например src, tests' });
  const protectEl = input({ placeholder: 'защищённые пути через запятую (необязательно), например .github, secrets' });
  const modal = openModal({
    title: 'Новая задача агенту OpenHands',
    body: h('div.stack',
      h('div', h('div.section-title', 'Репозиторий'), repoEl),
      h('div', h('div.section-title', 'Задача'), instrEl),
      h('div', h('div.section-title', 'Область правок'), allowEl),
      h('div', h('div.section-title', 'Защищённые пути'), protectEl),
      h('div.small.dim', 'Агент получит одноразовую копию без remote. Итог — патч и доказательства; ничего не пушится и не вливается.')),
    footer: h('div'),
  });
  const split = (v) => String(v || '').split(',').map((x) => x.trim()).filter(Boolean);
  modal.footer.appendChild(actionButton('Поручить', async () => {
    try {
      await api.raw('/api/coding-tasks', { method: 'POST', body: {
        instruction: instrEl.value.trim(), source_repo: repoEl.value.trim(),
        allowed_paths: split(allowEl.value), protected_paths: split(protectEl.value) } });
      toastOk('Задача передана агенту');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось поручить задачу'); }
  }, { cls: 'btn btn-primary', iconName: 'bolt' }));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Отмена'));
}

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
