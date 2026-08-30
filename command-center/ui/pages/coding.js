/* ============================================================
   coding.js â€” Coding Sessions: Ð¸Ð·Ð¾Ð»Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð½Ñ‹Ðµ git-worktree ÑÐµÑÑÐ¸Ð¸.
   Ð¦ÐµÐ¿Ð¾Ñ‡ÐºÐ° Ð¾Ð¿ÐµÑ€Ð°Ñ‚Ð¾Ñ€Ð°: Session â†’ Activity â†’ Diff â†’ Merge/Discard.
   Endpoints: GET/POST /api/coding-sessions, GET /api/coding-sessions/{id},
   GET .../diff, POST .../merge_preview | merge | discard.
   Ð‘ÑÐºÐµÐ½Ð´ â€” ÐµÐ´Ð¸Ð½ÑÑ‚Ð²ÐµÐ½Ð½Ñ‹Ð¹ CodingWorktreeManager, Ð²Ñ‚Ð¾Ñ€Ð¾Ð³Ð¾ Ð´Ð²Ð¸Ð¶ÐºÐ° Ð½ÐµÑ‚.
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
  title: 'Coding-ÑÐµÑÑÐ¸Ð¸',
  icon: 'edit',
  nav: 'more',

  async render(ctx) {
    let sessions = []; let err = null;
    try { sessions = listOf(await api.raw('/api/coding-sessions'), 'sessions'); }
    catch (e) { err = e; }

    const active = sessions.filter((s) => (s.status || 'active') === 'active').length;
    const head = pageHead('Coding-ÑÐµÑÑÐ¸Ð¸', active
      ? `${active} Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ñ… Â· Ð¸Ð·Ð¾Ð»ÑÑ†Ð¸Ñ Ð°Ð³ÐµÐ½Ñ‚Ð° Ð² Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾Ð¼ git-worktree`
      : 'Ð˜Ð·Ð¾Ð»ÑÑ†Ð¸Ñ Ð°Ð³ÐµÐ½Ñ‚Ð° Ð² Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾Ð¼ git-worktree: Ð´Ð¸Ñ„, Ñ€ÐµÐ²ÑŒÑŽ Ð¸ merge Ð±ÐµÐ· Ñ€Ð¸ÑÐºÐ° Ð´Ð»Ñ Ð¸ÑÑ…Ð¾Ð´Ð½Ð¾Ð³Ð¾ Ð´ÐµÑ€ÐµÐ²Ð°.', {
      actions: [
        h('button.btn', { type: 'button', onClick: () => ctx.refresh() }, icon('retry', 14)),
        h('button.btn.btn-primary', { type: 'button', onClick: () => createModal(ctx) }, icon('plus', 14), h('span', 'ÐÐ¾Ð²Ð°Ñ ÑÐµÑÑÐ¸Ñ')),
      ],
    });

    const body = err
      ? errorNote(err, () => ctx.refresh())
      : sessions.length
        ? h('div.grid.auto-lg', sessions.map((s) => sessionCard(s, ctx)))
        : blank({
          iconName: 'apps', title: 'Ð¡ÐµÑÑÐ¸Ð¹ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚',
          hint: 'Ð¡Ð¾Ð·Ð´Ð°Ð¹Ñ‚Ðµ ÑÐµÑÑÐ¸ÑŽ Ð´Ð»Ñ Ñ€ÐµÐ¿Ð¾Ð·Ð¸Ñ‚Ð¾Ñ€Ð¸Ñ Ð¸Ð· Ñ€Ð°Ð·Ñ€ÐµÑˆÑ‘Ð½Ð½Ñ‹Ñ… ÐºÐ¾Ñ€Ð½ÐµÐ¹: Ð°Ð³ÐµÐ½Ñ‚ Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ñ‚ Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ñ‹Ð¹ worktree, Ð° Ð²Ñ‹ â€” Ñ‡ÐµÑÑ‚Ð½Ñ‹Ð¹ diff Ð¸ Ð±ÐµÐ·Ð¾Ð¿Ð°ÑÐ½Ñ‹Ð¹ merge.',
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
        h('div.card-title', s.session_id || 'â€”'),
        h('div.card-sub.truncate', `${s.branch || ''} â† ${s.source_repo || ''}`)),
      statusBadge(status, { live: status === 'active' })),
    h('div.row.tight',
      h('span.badge', `Ð±Ð°Ð·Ð°: ${String(s.base_ref || '').slice(0, 10) || 'â€”'}`),
      s.created_at ? h('span.xsmall.dim', `ÑÐ¾Ð·Ð´Ð°Ð½Ð° ${fmtDateShort(s.created_at * 1000 || s.created_at)}`) : null),
    h('div.row.tight', status === 'active' ? [
      actionButton('Diff', () => showDiff(s, ctx), { cls: 'btn btn-sm', iconName: 'search' }),
      actionButton('Merge', () => doMerge(s, ctx), { cls: 'btn btn-sm btn-primary', iconName: 'play' }),
      actionButton('ÐžÑ‚Ð±Ñ€Ð¾ÑÐ¸Ñ‚ÑŒ', () => doDiscard(s, ctx), { cls: 'btn btn-sm btn-danger', iconName: 'stop' }),
    ] : [h('span.xsmall.dim', 'ÑÐµÑÑÐ¸Ñ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð° â€” Ð´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð¶ÑƒÑ€Ð½Ð°Ð»')]));
}

function createModal(ctx) {
  const idEl = input({ placeholder: 'Ð¸Ð¼Ñ ÑÐµÑÑÐ¸Ð¸, Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€ fix-login' });
  const repoEl = input({ placeholder: 'Ð¿ÑƒÑ‚ÑŒ Ðº git-Ñ€ÐµÐ¿Ð¾Ð·Ð¸Ñ‚Ð¾Ñ€Ð¸ÑŽ Ð¸Ð· Ñ€Ð°Ð·Ñ€ÐµÑˆÑ‘Ð½Ð½Ñ‹Ñ… ÐºÐ¾Ñ€Ð½ÐµÐ¹' });
  const baseEl = input({ placeholder: 'HEAD', value: 'HEAD' });
  const modal = openModal({
    title: 'ÐÐ¾Ð²Ð°Ñ coding-ÑÐµÑÑÐ¸Ñ',
    body: h('div.stack',
      h('div', h('div.section-title', 'Ð˜Ð¼Ñ'), idEl),
      h('div', h('div.section-title', 'Ð ÐµÐ¿Ð¾Ð·Ð¸Ñ‚Ð¾Ñ€Ð¸Ð¹'), repoEl),
      h('div', h('div.section-title', 'Ð‘Ð°Ð·Ð¾Ð²Ñ‹Ð¹ ref'), baseEl)),
    footer: h('div'),
  });
  modal.footer.appendChild(actionButton('Ð¡Ð¾Ð·Ð´Ð°Ñ‚ÑŒ', async () => {
    try {
      await api.raw('/api/coding-sessions', {
        method: 'POST',
        body: { session_id: idEl.value.trim(), source_repo: repoEl.value.trim(), base_ref: baseEl.value.trim() || 'HEAD' },
      });
      toastOk('Ð¡ÐµÑÑÐ¸Ñ ÑÐ¾Ð·Ð´Ð°Ð½Ð° â€” worktree Ð³Ð¾Ñ‚Ð¾Ð²');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ ÑÐ¾Ð·Ð´Ð°Ñ‚ÑŒ ÑÐµÑÑÐ¸ÑŽ'); }
  }, { cls: 'btn btn-primary', iconName: 'plus' }));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'ÐžÑ‚Ð¼ÐµÐ½Ð°'));
}

async function showDiff(s, ctx) {
  const modal = openModal({ title: `Diff Â· ${s.session_id}`, wide: true, body: h('div.small.dim', 'Ð—Ð°Ð³Ñ€ÑƒÐ·ÐºÐ°â€¦'), footer: h('div') });
  let d;
  try { d = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/diff`); }
  catch (e) { modal.body.textContent = ''; modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Ð½ÐµÑ‚ Ð´Ð¸Ñ„Ð°')); return; }
  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack',
    h('div.row.tight',
      h('span.badge', `Ñ„Ð°Ð¹Ð»Ð¾Ð²: ${(d.files || []).length}`),
      d.truncated ? h('span.badge.badge-warn', 'Ð´Ð¸Ñ„ Ð¾Ð±Ñ€ÐµÐ·Ð°Ð½ (400 ÐšÐ‘)') : null),
    d.stat ? h('pre.block', d.stat) : null,
    d.patch ? h('pre.block', { style: { maxHeight: '55vh', overflow: 'auto' } }, d.patch) : h('div.small.dim', 'Ð˜Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹ Ð¿Ñ€Ð¾Ñ‚Ð¸Ð² Ð±Ð°Ð·Ñ‹ Ð½ÐµÑ‚.')));
  modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Ð—Ð°ÐºÑ€Ñ‹Ñ‚ÑŒ'));
}

async function doMerge(s, ctx) {
  let preview;
  try { preview = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/merge_preview`, { method: 'POST', body: {} }); }
  catch (e) { toastError(e, 'ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ñ‚ÑŒ Ð¿Ñ€ÐµÐ²ÑŒÑŽ ÑÐ»Ð¸ÑÐ½Ð¸Ñ'); return; }
  if (preview && preview.clean === false) {
    toast('Merge Ð¾Ñ‚ÐºÐ»Ð¾Ð½Ñ‘Ð½ Ð¿Ð¾Ð»Ð¸Ñ‚Ð¸ÐºÐ¾Ð¹: ÐºÐ¾Ð½Ñ„Ð»Ð¸ÐºÑ‚Ñ‹ Ð½Ðµ Ð²Ð»Ð¸Ð²Ð°ÑŽÑ‚ÑÑ Ð¿Ñ€Ð¸Ð½ÑƒÐ´Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð¾', { type: 'warn' });
    return;
  }
  try {
    const r = await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/merge`, { method: 'POST', body: {} });
    toastOk(`Ð¡Ð»Ð¸Ñ‚Ð¾ Ð² ${r.into || 'Ð±Ð°Ð·Ñƒ'} Â· ${(r.head || '').slice(0, 10)}`);
    ctx.refresh();
  } catch (e) { toastError(e, 'Merge Ð¾Ñ‚ÐºÐ»Ð¾Ð½Ñ‘Ð½ (ÐºÐ¾Ð½Ñ„Ð»Ð¸ÐºÑ‚Ñ‹ Ð¸Ð»Ð¸ Ð¾ÑˆÐ¸Ð±ÐºÐ° git)'); }
}

async function doDiscard(s, ctx) {
  try {
    await api.raw(`/api/coding-sessions/${encodeURIComponent(s.session_id)}/discard`, { method: 'POST', body: {} });
    toastOk('Ð¡ÐµÑÑÐ¸Ñ Ð¾Ñ‚Ð±Ñ€Ð¾ÑˆÐµÐ½Ð°: worktree Ð¸ Ð²ÐµÑ‚ÐºÐ° ÑƒÐ´Ð°Ð»ÐµÐ½Ñ‹');
    ctx.refresh();
  } catch (e) { toastError(e, 'ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ñ‚Ð±Ñ€Ð¾ÑÐ¸Ñ‚ÑŒ ÑÐµÑÑÐ¸ÑŽ'); }
}

export default CodingPage;
