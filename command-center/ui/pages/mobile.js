/* ============================================================
   mobile.js — Feature 15: Mobile Command Mode («Пульт»).
   Крупные тач-таргеты (≥44px), без hover-only взаимодействий.

   V2.1 (фаза M): пульт показывает РЕАЛЬНЫЙ рантайм, а не витрину.
   Endpoints (все существуют на сервере — фейковых кнопок здесь нет):
     GET  /api/missions                       + /missions/{id}/pause|resume|stop
     GET  /api/approvals                      + POST /api/approvals/{id}   (в т.ч. kind=tool)
     GET  /api/agents, /api/agentmap, /api/models
     PATCH /api/agents/{id}                   — смена модели агента
     GET  /api/tasks/{id}                     + /tasks/{id}/pause|resume|stop
     GET  /api/terminal/sessions              + /terminal/sessions/{id}/kill
     GET  /api/browser/sessions               + /browser/sessions/{id}/takeover|resume|stop
     GET  /api/opencode/sessions, /opencode/health + /opencode/sessions/{id}/abort
     GET  /api/system, /api/resources         — здоровье и предупреждение по ресурсам
     POST /api/tasks                          — Quick Task
   Если бэкенд недоступен (например, нет бинаря opencode) — честная пустота
   с причиной, а не неактивная декоративная кнопка.

   Свой CSS: ui/mobile.css, инжектится один раз через <link>.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, dot, statusBadge, statusLabel, meter, badge,
  toast, toastOk, toastError, openModal, confirmDialog, textarea, select,
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

/** Результат allSettled → значение или fallback (страница не должна падать целиком). */
function ok(res, fallback) {
  return res && res.status === 'fulfilled' ? res.value : fallback;
}

/** Причина отказа в человеческом виде — для честных пустых состояний. */
function why(res) {
  if (!res || res.status !== 'rejected') return '';
  const e = res.reason || {};
  return e.message || 'сервер не ответил';
}

const ACTIVE_MISSION = ['running', 'queued', 'planning', 'paused'];
const LIVE_TASK = ['running', 'paused', 'queued', 'waiting_approval'];

const MobilePage = {
  id: 'command',
  title: 'Пульт',
  icon: 'bolt',
  nav: 'primary',

  async render(ctx) {
    ensureMobileCss();

    const [missionsR, approvalsR, agentsR, mapR, sysR, resR, modelsR,
      termR, browserR, ocR, ocHealthR] = await Promise.allSettled([
      api.raw('/api/missions'),
      api.approvals('pending'),
      api.agents(),
      api.raw('/api/agentmap'),
      api.system(),
      api.raw('/api/resources'),
      api.models(),
      api.raw('/api/terminal/sessions'),
      api.raw('/api/browser/sessions'),
      api.raw('/api/opencode/sessions'),
      api.raw('/api/opencode/health'),
    ]);

    const missions = listOf(ok(missionsR, []), 'missions');
    const approvals = listOf(ok(approvalsR, []), 'approvals');
    const agents = listOf(ok(agentsR, []), 'agents');
    const models = listOf(ok(modelsR, []), 'models');
    const graph = ok(mapR, { nodes: [] }) || { nodes: [] };
    const sys = ok(sysR, null);
    const resources = ok(resR, null);

    ctx.state.agents = agents;
    ctx.state.models = models;
    ctx.setBadge('approvals', approvals.length);

    const nodeByAgent = new Map((graph.nodes || [])
      .filter((n) => n.id && n.id.startsWith('agent:'))
      .map((n) => [n.id.slice(6), n]));

    const runtime = {
      terminal: { items: listOf(ok(termR, []), 'sessions'), error: why(termR) },
      browser: { items: listOf(ok(browserR, []), 'sessions'), error: why(browserR) },
      opencode: {
        items: listOf(ok(ocR, []), 'sessions'),
        error: why(ocR),
        health: ok(ocHealthR, null),
        healthError: why(ocHealthR),
      },
    };

    return h('div.cmd-wrap',
      resourceWarning(resources, sys),
      missionsCard(missions, ctx),
      approvalsCard(approvals, ctx),
      agentsCard(agents, nodeByAgent, models, ctx),
      runtimeCard(runtime, ctx),
      healthCard(sys, resources, resR),
      quickCard(agents, ctx));
  },

  onEvent(ev) {
    return ev.kind.startsWith('mission.') || ev.kind.startsWith('approval.')
      || ev.kind.startsWith('task.') || ev.kind.startsWith('agent.')
      || ev.kind.startsWith('tool.') || ev.kind.startsWith('resource.')
      || ev.kind.startsWith('snapshot.');
  },
};

/* ---------------- Предупреждение по ресурсам ---------------- */

function resourceWarning(resources, sys) {
  const notes = [];
  if (resources && typeof resources === 'object') {
    const free = Number(resources.available_mb);
    const floor = Number(resources.reserve_floor_mb);
    if (Number.isFinite(free) && Number.isFinite(floor) && free < floor) {
      notes.push(`Свободно ${fmtGb(free)} ГБ при резерве ${fmtGb(floor)} ГБ — новые модели не поднять`);
    }
  }
  const m = sys && sys.metrics ? sys.metrics : null;
  if (m && Number(m.ram_total_mb) > 0 && Number(m.ram_used_mb) / Number(m.ram_total_mb) >= 0.9) {
    notes.push(`RAM занята на ${Math.round((m.ram_used_mb / m.ram_total_mb) * 100)}%`);
  }
  if (m && Number(m.cpu_pct) >= 95) notes.push(`CPU ${Math.round(m.cpu_pct)}%`);
  const health = (sys && sys.health) || {};
  for (const [name, info] of Object.entries(health)) {
    if (info && info.status === 'error') notes.push(`${name}: ${info.detail || 'ошибка'}`);
  }
  if (!notes.length) return null;
  return h('div.cmd-card.cmd-warn',
    h('div.cmd-row', dot('error'), h('b', 'Внимание')),
    h('ul.cmd-warn-list', notes.map((n) => h('li', n))));
}

/* ---------------- Active missions ---------------- */

function missionsCard(missions, ctx) {
  const active = missions.filter((m) => ACTIVE_MISSION.includes(String(m.status || '')));
  return h('div.cmd-card',
    h('div.cmd-title', `Active Missions${active.length ? ` · ${active.length}` : ''}`),
    active.length
      ? h('div.stack', active.slice(0, 5).map((m) => missionRow(m, ctx)))
      : h('div.cmd-empty', 'Активных миссий нет'),
    h('button.cmd-btn.cmd-btn-primary', {
      type: 'button', style: { marginTop: '10px' }, onClick: () => ctx.navigate('missions'),
    }, 'Открыть миссии'));
}

function missionRow(m, ctx) {
  const id = pick(m, ['id']);
  const status = String(m.status || '');
  const act = async (action, confirmText) => {
    if (confirmText && !(await confirmDialog({
      title: 'Остановить миссию?', text: confirmText, okText: 'Остановить', danger: true,
    }))) return;
    try {
      await api.raw(`/api/missions/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
      toastOk('Готово');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось выполнить действие'); }
  };
  return h('div.cmd-item',
    h('div.cmd-row', statusBadge(status, { live: status === 'running' }), h('div.spacer')),
    h('div.cmd-mission-title', pick(m, ['title'], `#${id}`)),
    meter('Прогресс', Number(m.progress || 0) * 100, 100, pct(m.progress || 0)),
    h('div.cmd-actions',
      status === 'running'
        ? h('button.cmd-btn', { type: 'button', onClick: () => act('pause') }, 'Пауза') : null,
      status !== 'running'
        ? h('button.cmd-btn.cmd-btn-primary', { type: 'button', onClick: () => act('resume') }, 'Продолжить') : null,
      h('button.cmd-btn.cmd-btn-danger', {
        type: 'button',
        onClick: () => act('stop', `Миссия «${pick(m, ['title'], id)}» будет остановлена вместе с её задачами.`),
      }, 'Остановить')));
}

/* ---------------- Approvals (в т.ч. kind=tool из tool-loop) ---------------- */

function approvalsCard(approvals, ctx) {
  return h('div.cmd-card',
    h('div.cmd-title', `Needs You${approvals.length ? ` · ${approvals.length}` : ''}`),
    approvals.length
      ? h('div.stack', approvals.map((a) => approvalRow(a, ctx)))
      : h('div.cmd-empty', 'Ничего не ждёт решения'));
}

/** Из preview tool-approval вытаскиваем имя инструмента: «…выполнить terminal.run». */
function toolName(preview) {
  const m = String(preview || '').match(/выполнить\s+([\w.:_-]+)/);
  return m ? m[1] : '';
}

function approvalRow(a, ctx) {
  const id = pick(a, ['id']);
  const kind = String(pick(a, ['kind'], 'действие'));
  const preview = String(a.preview || '');
  const tool = kind === 'tool' ? toolName(preview) : '';
  const taskId = pick(a, ['task_id']);

  const decide = async (approve) => {
    try {
      await api.decideApproval(id, approve, 'mobile');
      toastOk(approve ? 'Подтверждено' : 'Отклонено');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось отправить решение'); }
  };

  const full = h('pre.cmd-pre', preview);
  full.hidden = true;
  const more = preview.length > 160
    ? h('button.cmd-link', {
      type: 'button',
      onClick: (e) => { full.hidden = !full.hidden; e.currentTarget.textContent = full.hidden ? 'Подробнее' : 'Свернуть'; },
    }, 'Подробнее')
    : null;

  return h('div.cmd-approval',
    h('div.cmd-row',
      h('b', tool || kind),
      tool ? badge('инструмент', 'warn') : null,
      h('div.spacer'),
      h('span.xsmall.dim', fmtRelative(pick(a, ['created_at'])))),
    taskId ? h('div.xsmall.dim', `задача #${taskId}`) : null,
    preview ? h('div.xsmall.dim.wrap-any', preview.slice(0, 160)) : null,
    more, full,
    h('div.cmd-approval-actions',
      h('button.cmd-btn.cmd-btn-ok', { type: 'button', onClick: () => decide(true) }, 'Разрешить'),
      h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: () => decide(false) }, 'Отклонить')));
}

/* ---------------- Agents ---------------- */

function agentsCard(agents, nodeByAgent, models, ctx) {
  const working = agents.filter((a) => {
    const n = nodeByAgent.get(String(pick(a, ['id'])));
    return n && (n.status === 'working' || n.status === 'queued');
  }).length;
  return h('div.cmd-card',
    h('div.cmd-title', `Agents${working ? ` · ${working} в работе` : ''}`),
    agents.length
      ? h('div.stack.sm', agents.map((a) => agentRow(a, nodeByAgent.get(String(pick(a, ['id']))), models, ctx)))
      : h('div.cmd-empty', 'Агентов пока нет'));
}

function agentRow(a, node, models, ctx) {
  const status = (node && node.status) || 'idle';
  const taskId = node && node.task ? Number(node.task) : null;
  return h('div.cmd-agent', {
    onClick: () => openAgentSheet(a, taskId, models, ctx), role: 'button', tabindex: '0',
  },
  dot(status, { live: status === 'working' }),
  h('span.cmd-agent-name', pick(a, ['name'], '')),
  h('span.xsmall.dim', taskId ? `задача #${taskId}` : statusLabel(status)));
}

async function openAgentSheet(a, taskId, models, ctx) {
  const agentId = pick(a, ['id']);
  const modal = openModal({
    title: pick(a, ['name'], 'Агент'),
    body: h('div.small.dim', 'Загрузка…'),
    footer: h('div'),
  });

  let task = null;
  if (taskId) {
    try { const d = await api.task(taskId); task = d.task || d; } catch { task = null; }
  }
  const status = task ? String(task.status || '') : '';

  /* Смена модели — реальный PATCH /api/agents/{id}. Честно предупреждаем, что
     переключение применяется к следующему запуску, а не к идущему шагу. */
  const modelEl = select(
    [{ value: '', label: models.length ? '— модель агента —' : 'моделей ещё нет' },
      ...models.map((m) => ({ value: pick(m, ['id']), label: pick(m, ['alias', 'name'], '') }))],
    { value: a.model_id === null || a.model_id === undefined ? '' : String(a.model_id) },
  );
  const switchModel = async () => {
    if (!modelEl.value) { toast('Выберите модель', { type: 'warn' }); return; }
    try {
      await api.updateAgent(agentId, { model_id: idVal(modelEl.value) });
      toastOk('Модель переключена', 'Применится со следующего запуска');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось сменить модель'); }
  };

  modal.body.textContent = '';
  modal.body.appendChild(h('div.stack.sm',
    task
      ? h('div.stack.sm',
        h('div.cmd-row', statusBadge(status, { live: status === 'running' })),
        h('div.small', { style: { fontWeight: 600 } }, pick(task, ['title'], `задача #${taskId}`)))
      : h('div.cmd-empty', 'Активной задачи нет'),
    h('div.cmd-title', { style: { marginTop: '6px' } }, 'Модель'),
    modelEl,
    h('button.cmd-btn', { type: 'button', style: { marginTop: '8px' }, onClick: switchModel },
      'Сменить модель'),
    h('div.xsmall.dim', 'Переключение применяется к следующему запуску агента.')));

  const run = async (action) => {
    try {
      await api.taskAction(taskId, action);
      toastOk('Готово');
      modal.close();
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось выполнить действие'); }
  };
  modal.footer.textContent = '';
  modal.footer.appendChild(h('div.cmd-actions', { style: { width: '100%' } },
    task && status === 'running'
      ? h('button.cmd-btn', { type: 'button', onClick: () => run('pause') }, 'Пауза') : null,
    task && status === 'paused'
      ? h('button.cmd-btn.cmd-btn-primary', { type: 'button', onClick: () => run('resume') }, 'Продолжить') : null,
    task && LIVE_TASK.includes(status)
      ? h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: () => run('stop') }, 'Остановить') : null,
    task && !LIVE_TASK.includes(status)
      ? h('div.small.dim', 'Задача уже завершена — действия недоступны.') : null));
}

/* ---------------- Runtime: терминал / браузер / OpenCode ---------------- */

function runtimeCard(rt, ctx) {
  return h('div.cmd-card',
    h('div.cmd-title', 'Что сейчас запущено'),
    section('Терминал', terminalBody(rt.terminal, ctx)),
    section('Браузер', browserBody(rt.browser, ctx)),
    section('OpenCode', opencodeBody(rt.opencode, ctx)));
}

function section(title, body) {
  return h('div.cmd-section', h('div.cmd-section-title', title), body);
}

function emptyNote(text, reason) {
  return h('div.cmd-empty', text, reason ? h('div.xsmall.dim', reason) : null);
}

function terminalBody(state, ctx) {
  if (state.error) return emptyNote('Терминал недоступен', state.error);
  const live = state.items.filter((s) => String(s.status || '') === 'running');
  if (!live.length) return emptyNote('Активных терминалов нет');
  return h('div.stack.sm', live.map((s) => {
    const kill = async () => {
      if (!(await confirmDialog({
        title: 'Убить процесс?', text: String(s.command || '').slice(0, 200),
        okText: 'Остановить', danger: true,
      }))) return;
      try {
        await api.raw(`/api/terminal/sessions/${encodeURIComponent(s.id)}/kill`, { method: 'POST' });
        toastOk('Процесс остановлен');
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось остановить процесс'); }
    };
    return h('div.cmd-item',
      h('div.cmd-row', dot('running', { live: true }),
        h('span.cmd-mono', String(s.command || '').slice(0, 80) || '—')),
      h('div.xsmall.dim.wrap-any', `${s.mode || ''} · ${s.cwd || ''}`),
      h('div.cmd-actions',
        h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: kill }, 'Остановить')));
  }));
}

function browserBody(state, ctx) {
  if (state.error) return emptyNote('Браузер недоступен', state.error);
  const live = state.items.filter((s) => ['created', 'running'].includes(String(s.status || '')));
  if (!live.length) return emptyNote('Сессий браузера нет');
  return h('div.stack.sm', live.map((s) => {
    const act = async (action) => {
      try {
        await api.raw(`/api/browser/sessions/${encodeURIComponent(s.id)}/${action}`, { method: 'POST' });
        toastOk(action === 'takeover' ? 'Управление у вас' : 'Готово');
        ctx.refresh();
      } catch (e) { toastError(e, 'Не удалось выполнить действие'); }
    };
    const taken = Boolean(s.takeover);
    return h('div.cmd-item',
      h('div.cmd-row', dot(taken ? 'paused' : 'running', { live: !taken }),
        h('span.cmd-agent-name', `#${s.id}`),
        taken ? badge('вы за рулём', 'warn') : null),
      h('div.xsmall.dim.wrap-any', String(s.current_url || 'страница не открыта').slice(0, 120)),
      h('div.cmd-actions',
        taken
          ? h('button.cmd-btn.cmd-btn-primary', { type: 'button', onClick: () => act('resume') }, 'Вернуть агенту')
          : h('button.cmd-btn', { type: 'button', onClick: () => act('takeover') }, 'Взять управление'),
        h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: () => act('stop') }, 'Закрыть')));
  }));
}

function opencodeBody(state, ctx) {
  const health = state.health || {};
  const unavailable = state.healthError
    || (health && health.ok === false)
    || (health && health.status && health.status !== 'ok');
  const reason = state.healthError
    || (health && (health.detail || health.error || health.message))
    || 'сервер opencode не отвечает';

  if (state.error) return emptyNote('OpenCode недоступен', state.error);
  const live = state.items.filter((s) => !['aborted', 'finished', 'completed', 'failed']
    .includes(String(s.status || '')));
  if (!live.length) {
    return emptyNote('Сессий OpenCode нет', unavailable ? String(reason) : '');
  }
  return h('div.stack.sm',
    unavailable ? h('div.xsmall.dim', `OpenCode недоступен: ${String(reason)} — прерывание может не сработать`) : null,
    ...live.map((s) => {
      const sid = s.session_id || s.id;
      const abort = async () => {
        if (!(await confirmDialog({
          title: 'Прервать сессию OpenCode?', text: String(sid), okText: 'Прервать', danger: true,
        }))) return;
        try {
          await api.raw(`/api/opencode/sessions/${encodeURIComponent(sid)}/abort`, { method: 'POST' });
          toastOk('Сессия прервана');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось прервать сессию'); }
      };
      return h('div.cmd-item',
        h('div.cmd-row', dot(String(s.status || 'running')),
          h('span.cmd-mono', String(sid).slice(0, 40))),
        s.worktree_path || s.project_path
          ? h('div.xsmall.dim.wrap-any', String(s.worktree_path || s.project_path)) : null,
        h('div.cmd-actions',
          h('button.cmd-btn.cmd-btn-danger', { type: 'button', onClick: abort }, 'Прервать')));
    }));
}

/* ---------------- Health ---------------- */

function healthCard(sys, resources, resR) {
  const d = sys && typeof sys === 'object' ? sys : {};
  // /api/system отдаёт {metrics, history, queue, health}; старый код читал d.current
  const cur = (d.metrics && typeof d.metrics === 'object') ? d.metrics : {};
  const cpu = cur.cpu_pct === undefined || cur.cpu_pct === null ? null : Number(cur.cpu_pct);
  const ramUsed = cur.ram_used_mb;
  const ramTotal = cur.ram_total_mb;
  const queue = (d.queue && typeof d.queue === 'object') ? d.queue : {};
  const health = (d.health && typeof d.health === 'object') ? d.health : {};

  const free = resources ? Number(resources.available_mb) : NaN;
  const totalRes = resources ? Number(resources.total_mb) : NaN;

  return h('div.cmd-card',
    h('div.cmd-title', 'Health'),
    !sys ? h('div.cmd-empty', 'Метрики недоступны') : null,
    h('div.cmd-health-row',
      sys ? meter('CPU', cpu ?? 0, 100, cpu === null ? '—' : `${Math.round(cpu)}%`) : null,
      ramTotal
        ? meter('RAM', ramUsed || 0, ramTotal, `${fmtGb(ramUsed)} / ${fmtGb(ramTotal)} ГБ`)
        : (sys ? h('div.small.dim', 'RAM: нет данных') : null),
      cur.disk_total_gb
        ? meter('Диск', cur.disk_used_gb || 0, cur.disk_total_gb,
          `${Math.round(cur.disk_used_gb)} / ${Math.round(cur.disk_total_gb)} ГБ`)
        : null,
      Number.isFinite(free) && Number.isFinite(totalRes) && totalRes > 0
        ? meter('Свободно под модели', totalRes - free, totalRes, `${fmtGb(free)} ГБ свободно`)
        : h('div.small.dim', resR && resR.status === 'rejected'
          ? `Ресурсы: ${why(resR)}` : 'Ресурсы: нет данных')),
    Object.keys(health).length
      ? h('div.cmd-chips', Object.entries(health).map(([name, info]) => h('span.cmd-chip',
        dot((info && info.status) || 'unknown'), name)))
      : null,
    Object.keys(queue).length
      ? h('div.xsmall.dim', 'Очередь: ' + Object.entries(queue)
        .map(([k, v]) => `${statusLabel(k)} ${v}`).join(' · '))
      : null);
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
