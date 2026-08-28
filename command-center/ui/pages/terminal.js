/* ============================================================
   terminal.js — Feature 07: Terminal (sandbox/project_host/system_admin).
   Endpoints: GET/POST /api/terminal/roots, POST /api/terminal/preview,
   POST /api/terminal/run, GET /api/terminal/sessions,
   GET /api/terminal/sessions/{id}, POST /api/terminal/sessions/{id}/kill,
   POST /api/terminal/sessions/{id}/stdin.
   ============================================================ */

import { api } from '../api.js';
import {
  h, icon, statusBadge,
  toast, toastOk, toastError, openModal, confirmDialog, actionButton,
  field, input, textarea, fmtDateShort,
} from '../components.js';
import { panel, pageHead, errorBanner, emptyPanel } from './_shared.js';

const MODES = [
  { value: 'sandbox', label: 'Sandbox', hint: 'docker, cwd монтируется, изолирован (auto для безопасных команд).' },
  { value: 'project_host', label: 'Project host', hint: 'напрямую на хосте, только внутри разрешённых корней.' },
  { value: 'system_admin', label: 'System admin', hint: 'всегда требует подтверждения.' },
];

const termState = { mode: 'sandbox', cwd: '', command: '', preview: null };

const TerminalPage = {
  id: 'terminal',
  title: 'Терминал',
  icon: 'chevron',
  nav: 'primary',

  async render(ctx) {
    const [rootsR, sessionsR] = await Promise.allSettled([api.raw('/api/terminal/roots'), api.raw('/api/terminal/sessions')]);
    const roots = rootsR.status === 'fulfilled' ? (rootsR.value.roots || []) : [];
    const sessions = sessionsR.status === 'fulfilled' ? (Array.isArray(sessionsR.value) ? sessionsR.value : []) : [];

    if (!termState.cwd && roots.length) termState.cwd = roots[0];

    const runPanel = buildRunPanel(ctx, roots);
    const rootsPanel = buildRootsPanel(roots, ctx);

    const sessionsPanel = sessionsR.status === 'rejected'
      ? errorBanner(sessionsR.reason, ctx)
      : sessions.length
        ? panel(`Сессии (${sessions.length})`, h('div.mini-list', sessions.map((s) => sessionRow(s, ctx))))
        : emptyPanel({ iconName: 'chevron', title: 'Сессий ещё нет', hint: 'Запустите команду выше — появится здесь с живым выводом.' });

    return h('div.stack.lg',
      pageHead('Терминал', 'sandbox / project_host / system_admin — с политикой AUTO/ASK/DENY'),
      h('div.grid.cols-2', runPanel, rootsPanel),
      sessionsPanel);
  },

  onEvent(ev) { return ev.kind === 'agent.tool_call' || ev.kind === 'agent.warning'; },
};

function buildRunPanel(ctx, roots) {
  const modeSeg = h('div.seg', MODES.map((m) => h('button', {
    type: 'button', class: termState.mode === m.value ? 'on' : '', title: m.hint,
    onClick: () => { termState.mode = m.value; ctx.refresh(); },
  }, m.label)));

  const cwdEl = input({ placeholder: roots[0] || '/data', value: termState.cwd, class: 'input mono' });
  cwdEl.addEventListener('input', () => { termState.cwd = cwdEl.value; });
  const cmdEl = textarea({ rows: 3, class: 'textarea mono', placeholder: 'git status', value: termState.command });
  cmdEl.addEventListener('input', () => { termState.command = cmdEl.value; });
  const networkEl = h('input', { type: 'checkbox', checked: false });
  const networkField = h('label.check', networkEl, h('span', 'Разрешить сеть (sandbox)'));

  const decisionOut = h('div.small.dim',
    termState.preview
      ? decisionLine(termState.preview.decision)
      : 'Нажмите «Preview», чтобы увидеть решение политики до запуска.');

  const doPreview = async () => {
    if (!cmdEl.value.trim()) { toast('Введите команду', { type: 'warn' }); return; }
    try {
      const r = await api.raw('/api/terminal/preview', { method: 'POST', body: { mode: termState.mode, command: cmdEl.value, cwd: cwdEl.value || undefined } });
      termState.preview = r;
      decisionOut.textContent = '';
      decisionOut.appendChild(decisionLine(r.decision));
    } catch (e) { toastError(e, 'Не удалось получить решение'); }
  };

  const doRun = async (approved = false) => {
    if (!cmdEl.value.trim()) { toast('Введите команду', { type: 'warn' }); return; }
    try {
      const r = await api.raw('/api/terminal/run', {
        method: 'POST',
        body: { mode: termState.mode, command: cmdEl.value, cwd: cwdEl.value || undefined, approved, network: networkEl.checked },
      });
      if (r && r.session_id) {
        toastOk('Сессия запущена', `pid ${r.pid}`);
        cmdEl.value = ''; termState.command = ''; termState.preview = null;
        ctx.refresh();
        openSessionDetail(r.session_id, ctx);
        return;
      }
      if (r && r.approval_id) {
        const ok = await confirmDialog({
          title: 'Требуется подтверждение', okText: 'Запустить всё равно', danger: true,
          text: `Политика попросила подтверждение (approval #${r.approval_id}) для: ${cmdEl.value}`,
        });
        if (ok) await doRun(true);
      }
    } catch (e) { toastError(e, 'Команда запрещена или не запустилась'); }
  };

  return panel('Запуск команды', h('div.stack.sm',
    field('Режим', modeSeg),
    field('Рабочая директория', cwdEl),
    field('Команда', cmdEl),
    networkField,
    decisionOut,
    h('div.row', h('div.spacer'),
      h('button.btn.btn-sm', { type: 'button', onClick: doPreview }, icon('search', 13), h('span', 'Preview')),
      actionButton('Run', () => doRun(false), { cls: 'btn btn-sm btn-primary', iconName: 'play' }))));
}

function decisionLine(decision) {
  const label = { auto: 'AUTO — выполнится сразу', ask: 'ASK — потребует подтверждения', deny: 'DENY — запрещено политикой' }[decision] || decision;
  return h('div.row', statusBadge(decision), h('span.small', label));
}

function buildRootsPanel(roots, ctx) {
  const rows = h('div.stack.sm');
  const items = roots.length ? roots.slice() : [];
  const els = [];
  function addRow(value = '') {
    const el = input({ value, class: 'input mono' });
    const row = h('div.row.tight', el, h('button.btn.btn-sm.btn-ghost', {
      type: 'button', onClick: () => { row.remove(); const i = els.indexOf(el); if (i >= 0) els.splice(i, 1); },
    }, icon('trash', 12)));
    els.push(el);
    rows.appendChild(row);
  }
  if (items.length) items.forEach((r) => addRow(r)); else addRow('');

  return panel('Разрешённые корни (project_host)', h('div.stack.sm',
    rows,
    h('button.btn.btn-sm', { type: 'button', onClick: () => addRow('') }, icon('plus', 12), h('span', 'Ещё путь')),
    h('div.xsmall.dim', 'sandbox всегда ограничен своим cwd — эти корни только для project_host.'),
    h('div.row', h('div.spacer'),
      actionButton('Сохранить', async () => {
        const values = els.map((e) => e.value.trim()).filter(Boolean);
        try { await api.raw('/api/terminal/roots', { method: 'POST', body: { roots: values } }); toastOk('Корни сохранены'); ctx.refresh(); }
        catch (e) { toastError(e, 'Не удалось сохранить'); }
      }, { cls: 'btn btn-primary btn-sm', iconName: 'check' }))));
}

function sessionRow(s, ctx) {
  return h('div.mini-row.clickable', { onClick: () => openSessionDetail(s.id, ctx) },
    statusBadge(s.status || 'running', { live: s.status === 'running' }),
    h('div', { style: { flex: '1', minWidth: 0 } },
      h('div.mono.small.truncate', s.command),
      h('div.xsmall.dim', `${s.mode} · ${s.cwd}`)),
    h('span.xsmall.dim', s.pid ? `pid ${s.pid}` : ''),
    h('span.xsmall.dim', fmtDateShort(s.started_at)));
}

async function openSessionDetail(id, ctx) {
  const modal = openModal({
    title: `Сессия ${id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div'),
    onClose: () => { stopped = true; },
  });
  let stopped = false;

  async function tick() {
    if (stopped) return;
    let st;
    try { st = await api.raw(`/api/terminal/sessions/${encodeURIComponent(id)}`); }
    catch (e) {
      modal.body.textContent = '';
      modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Сессия недоступна (возможно, после рестарта сервера)'));
      return;
    }
    if (stopped) return;
    modal.body.textContent = '';
    modal.footer.textContent = '';
    modal.body.appendChild(h('div.stack.sm',
      h('div.row',
        h('span.badge.mono', st.mode), h('span.xsmall.dim.mono', st.cwd), h('div.spacer'),
        h('span.xsmall.dim', `pid ${st.pid ?? '—'}`)),
      h('div.mono.small', st.cmd),
      h('div.log',
        st.output_tail && st.output_tail.length
          ? st.output_tail.map((line) => h('div.log-line', h('span.log-msg', line)))
          : h('div.log-empty', 'Пока нет вывода.')),
      st.finished ? h('div.row', statusBadge(st.exit_code === 0 ? 'completed' : 'failed', { label: `завершено, код ${st.exit_code}` })) : null));

    modal.footer.appendChild(h('div.spacer'));
    modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
    if (!st.finished) {
      modal.footer.appendChild(actionButton('Kill', async () => {
        try { await api.raw(`/api/terminal/sessions/${encodeURIComponent(id)}/kill`, { method: 'POST' }); toastOk('Сессия остановлена'); ctx.refresh(); await tick(); }
        catch (e) { toastError(e, 'Не удалось остановить'); }
      }, { cls: 'btn btn-danger', iconName: 'stop' }));
      setTimeout(tick, 1500);
    }
  }
  tick();
}

export default TerminalPage;
