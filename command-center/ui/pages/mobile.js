/* ============================================================
   mobile.js — Feature 15: Mobile Command Mode («Пульт»).
   Крупные тач-таргеты (≥44px), без hover-only взаимодействий.
   Endpoints: GET /api/missions, GET/POST /api/approvals(/{id}),
   GET /api/agents, GET /api/agentmap, GET /api/tasks/{id},
   POST /api/tasks/{id}/pause|resume|stop, GET /api/system,
   POST /api/tasks (Quick Task).
   Свой CSS: ui/mobile.css, инжектится один раз через <link>.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, dot, statusBadge, statusLabel, meter,
  toast, toastOk, toastError, openModal, textarea, select,
  fmtGb, fmtRelative,
} from '../components.js';
import { idVal, pct } from './_shared.js';

function ensureMobileCss() {
  if (document.getElementById('bcc-mobile-css')) return;
  const link = document.createElement('link');
  link.id = 'bcc-mobile-css';
  link.rel = 'stylesheet';
  link.href = 'mobile.css';
  document.head.appendChild(link);
}

function titleFromPrompt(prompt) {
  const line = String(prompt || '').trim().split('\n')[0].trim();
  return line ? (line.length > 70 ? `${line.slice(0, 67)}…` : line) : 'Задача';
}

const MobilePage = {
  id: 'command',
  title: 'Пульт',
  icon: 'bolt',
  nav: 'primary',

  async render(ctx) {
    ensureMobileCss();

    const [missionsR, approvalsR, agentsR, mapR, sysR] = await Promise.allSettled([
      api.raw('/api/missions'), api.approvals('pending'), api.agents(), api.raw('/api/agentmap'), api.system(),
    ]);
    const missions = missionsR.status === 'fulfilled' ? listOf(missionsR.value, 'missions') : [];
    const approvals = approvalsR.status === 'fulfilled' ? listOf(approvalsR.value, 'approvals') : [];
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    const graph = mapR.status === 'fulfilled' ? mapR.value : { nodes: [] };
    const sys = sysR.status === 'fulfilled' ? sysR.value : null;

    ctx.state.agents = agents;
    ctx.setBadge('approvals', approvals.length);

    const nodeByAgent = new Map((graph.nodes || []).filter((n) => n.id.startsWith('agent:')).map((n) => [n.id.slice(6), n]));

    return h('div.cmd-wrap',
      missionCard(missions, ctx),
      approvalsCard(approvals, ctx),
      agentsCard(agents, nodeByAgent, ctx),
      healthCard(sys),
      quickCard(agents, ctx));
  },

  onEvent(ev) {
    return ev.kind.startsWith('mission.') || ev.kind.startsWith('approval.')
      || ev.kind.startsWith('task.') || ev.kind.startsWith('agent.');
  },
};

/* ---------------- Active mission ---------------- */

function missionCard(missions, ctx) {
  const top = missions.find((m) => m.status === 'running') || missions.find((m) => ['queued', 'planning'].includes(m.status));
  return h('div.cmd-card',
    h('div.cmd-title', 'Active Mission'),
    top
      ? h('div.stack.sm',
        h('div.cmd-row', statusBadge(top.status, { live: top.status === 'running' }), h('div.spacer')),
        h('div.cmd-mission-title', pick(top, ['title'], `#${pick(top, ['id'])}`)),
        meter('Прогресс', Number(top.progress || 0) * 100, 100, pct(top.progress || 0)))
      : h('div.cmd-empty', 'Активных миссий нет'),
    h('button.cmd-btn.cmd-btn-primary', { type: 'button', style: { marginTop: '10px' }, onClick: () => ctx.navigate('missions') }, 'Открыть миссии'));
}

/* ---------------- Approvals ---------------- */

function approvalsCard(approvals, ctx) {
  return h('div.cmd-card',
    h('div.cmd-title', `Needs You${approvals.length ? ` · ${approvals.length}` : ''}`),
    approvals.length
      ? h('div.stack', approvals.map((a) => approvalRow(a, ctx)))
      : h('div.cmd-empty', 'Ничего не ждёт решения'));
}

function approvalRow(a, ctx) {
  const id = pick(a, ['id']);
  const decide = async (approve) => {
    try { await api.decideApproval(id, approve, 'mobile'); toastOk(approve ? 'Подтверждено' : 'Отклонено'); ctx.refresh(); }
    catch (e) { toastError(e, 'Не удалось отправить решение'); }
  };
  return h('div.cmd-approval',
    h('div.cmd-row', h('b', pick(a, ['kind'], 'действие')), h('div.spacer'), h('span.xsmall.dim', fmtRelative(pick(a, ['created_at'])))),
    a.preview ? h('div.xsmall.dim.wrap-any', String(a.preview).slice(0, 160)) : null,
    h('div.cmd-approval-actions',
      h('button.cmd-btn.cmd-btn-ok', { type: 'button', onClick: () => decide(true) }, 'Approve'),
      h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: () => decide(false) }, 'Reject')));
}

/* ---------------- Agents ---------------- */

function agentsCard(agents, nodeByAgent, ctx) {
  return h('div.cmd-card',
    h('div.cmd-title', 'Agents'),
    agents.length
      ? h('div.stack.sm', agents.map((a) => agentRow(a, nodeByAgent.get(String(pick(a, ['id']))), ctx)))
      : h('div.cmd-empty', 'Агентов пока нет'));
}

function agentRow(a, node, ctx) {
  const status = (node && node.status) || 'idle';
  const taskId = node && node.task ? Number(node.task) : null;
  return h('div.cmd-agent', { onClick: () => openAgentSheet(a, taskId, ctx), role: 'button', tabindex: '0' },
    dot(status, { live: status === 'working' }),
    h('span.cmd-agent-name', pick(a, ['name'], '')),
    h('span.xsmall.dim', taskId ? `задача #${taskId}` : statusLabel(status)));
}

async function openAgentSheet(a, taskId, ctx) {
  if (!taskId) { toast(`У «${pick(a, ['name'], 'агента')}» нет активной задачи`, { type: 'info' }); return; }
  const modal = openModal({ title: pick(a, ['name'], 'Агент'), body: h('div.small.dim', 'Загрузка задачи…'), footer: h('div') });
  let task;
  try { const d = await api.task(taskId); task = d.task || d; }
  catch (e) {
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Задача недоступна'));
    return;
  }
  const status = String(task.status || '');
  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack.sm',
    h('div.cmd-row', statusBadge(status, { live: status === 'running' })),
    h('div.small', { style: { fontWeight: 600 } }, pick(task, ['title'], `задача #${taskId}`))));

  const run = async (action) => {
    try { await api.taskAction(taskId, action); toastOk('Готово'); modal.close(); ctx.refresh(); }
    catch (e) { toastError(e, 'Не удалось выполнить действие'); }
  };
  modal.footer.textContent = '';
  modal.footer.appendChild(h('div.cmd-row', { style: { width: '100%' } },
    status === 'running' ? h('button.cmd-btn', { type: 'button', onClick: () => run('pause') }, 'Pause') : null,
    status === 'paused' ? h('button.cmd-btn.cmd-btn-primary', { type: 'button', onClick: () => run('resume') }, 'Resume') : null,
    ['running', 'paused', 'queued', 'waiting_approval'].includes(status)
      ? h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: () => run('stop') }, 'Stop') : null,
    !['running', 'paused', 'queued', 'waiting_approval'].includes(status)
      ? h('div.small.dim', 'Задача уже завершена — действия недоступны.') : null));
}

/* ---------------- Health ---------------- */

function healthCard(sys) {
  const d = sys && typeof sys === 'object' ? sys : {};
  const cur = (d.current && typeof d.current === 'object') ? d.current : d;
  const cpu = cur.cpu_pct === undefined || cur.cpu_pct === null ? null : Number(cur.cpu_pct);
  const ramUsed = cur.ram_used_mb; const ramTotal = cur.ram_total_mb;
  return h('div.cmd-card',
    h('div.cmd-title', 'Health'),
    h('div.cmd-health-row',
      meter('CPU', cpu ?? 0, 100, cpu === null ? '—' : `${Math.round(cpu)}%`),
      ramTotal ? meter('RAM', ramUsed || 0, ramTotal, `${fmtGb(ramUsed)} / ${fmtGb(ramTotal)} ГБ`) : h('div.small.dim', 'RAM: нет данных')));
}

/* ---------------- Quick Task ---------------- */

function quickCard(agents, ctx) {
  const textEl = textarea({ rows: 3, class: 'textarea', placeholder: 'Быстрая задача…' });
  const agentEl = select(
    [{ value: '', label: agents.length ? '— агент —' : 'агентов ещё нет' },
      ...agents.map((a) => ({ value: pick(a, ['id']), label: pick(a, ['name'], '') }))],
    { value: agents.length === 1 ? String(pick(agents[0], ['id'])) : '' },
  );
  const submit = async () => {
    const text = textEl.value.trim();
    if (!text) { toast('Опишите задачу', { type: 'warn' }); return; }
    if (!agentEl.value) { toast('Выберите агента', { type: 'warn' }); return; }
    try {
      await api.createTask({ title: titleFromPrompt(text), prompt: text, agent_id: idVal(agentEl.value), priority: 5, run_now: true });
      textEl.value = '';
      toastOk('Задача поставлена в очередь');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось создать задачу'); }
  };
  return h('div.cmd-card.cmd-quick',
    h('div.cmd-title', 'Quick Task'),
    textEl,
    agentEl,
    h('button.cmd-btn.cmd-btn-primary', { type: 'button', style: { marginTop: '10px' }, onClick: submit }, 'START'));
}

export default MobilePage;
