/* ============================================================
   overview.js — Home V2 (Feature 15 companion): статус-строка,
   Quick Command, ACTIVE MISSION, NEEDS YOU, AGENTS, COMPUTE,
   RECENT ACTIVITY. Приоритет Missions/Approvals/Agents/Resources
   над сырыми логами. Endpoints: /api/missions, /api/agents,
   /api/models, /api/approvals, /api/system, /api/resources,
   /api/agentmap, /api/activity, /api/tasks (Quick Command).
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, icon, dot, statusBadge, meter,
  toast, toastOk, toastError, actionButton, field, textarea, select,
  fmtGb, fmtClock,
} from '../components.js';
import { idVal, errorBanner, pct } from './_shared.js';

function titleFromPrompt(prompt) {
  const line = String(prompt || '').trim().split('\n')[0].trim();
  if (!line) return 'Задача';
  return line.length > 80 ? `${line.slice(0, 77)}…` : line;
}

function agentSelect(agents, value) {
  return select(
    [{ value: '', label: agents.length ? '— выберите агента —' : 'агентов ещё нет' },
      ...agents.map((a) => ({ value: pick(a, ['id']), label: pick(a, ['name'], 'без имени') }))],
    { value: value === null || value === undefined ? '' : String(value) },
  );
}

const RAW_LOG_KINDS = new Set(['run.log', 'ws.open', 'ws.closed', 'ws.connecting', 'ws.idle', 'system.metrics']);

const OverviewPage = {
  id: 'overview',
  title: 'Обзор',
  icon: 'home',
  nav: 'primary',

  async render(ctx) {
    const [missionsR, agentsR, modelsR, approvalsR, systemR, resourcesR, mapR, activityR] = await Promise.allSettled([
      api.raw('/api/missions'), api.agents(), api.models(), api.approvals('pending'),
      api.system(), api.raw('/api/resources'), api.raw('/api/agentmap'), api.activity(),
    ]);

    const missions = missionsR.status === 'fulfilled' ? listOf(missionsR.value, 'missions') : [];
    const agents = agentsR.status === 'fulfilled' ? listOf(agentsR.value, 'agents') : [];
    const models = modelsR.status === 'fulfilled' ? listOf(modelsR.value, 'models') : [];
    const approvals = approvalsR.status === 'fulfilled' ? listOf(approvalsR.value, 'approvals') : [];
    const sys = systemR.status === 'fulfilled' ? systemR.value : null;
    const resources = resourcesR.status === 'fulfilled' ? resourcesR.value : null;
    const graph = mapR.status === 'fulfilled' ? mapR.value : { nodes: [] };
    const activity = activityR.status === 'fulfilled' ? listOf(activityR.value, 'events', 'activity') : [];

    ctx.state.models = models;
    ctx.state.agents = agents;
    ctx.setBadge('approvals', approvals.length);

    const activeMissions = missions.filter((m) => m.status === 'running');
    const onlineModels = models.filter((m) => String(m.status) === 'online').length;
    const healthOk = systemR.status === 'fulfilled';

    const statusline = h('div.statusline',
      h('div.sl-item', h('span.sl-num', String(activeMissions.length)), h('span.sl-label', 'миссий активно')),
      h('div.sep'),
      h('div.sl-item', h('span.sl-num', String(agents.length)), h('span.sl-label', 'агентов')),
      h('div.sep'),
      h('div.sl-item', h('span.sl-num', String(onlineModels)), h('span.sl-label', `моделей online из ${models.length}`)),
      h('div.spacer'),
      h('div.sl-item', dot(healthOk ? 'online' : 'error'), h('span.sl-label', healthOk ? 'система в норме' : 'сервер не отвечает')));

    const quick = buildQuickCommand(ctx, agents);

    const activeMissionCard = buildActiveMissionCard(missions, ctx);
    const needsYouCard = buildNeedsYouCard(approvals, ctx);
    const agentsCard = buildAgentsCard(agents, graph, ctx);
    const computeCard = buildComputeCard(sys, resources, ctx);

    const activityPanel = buildActivityPanel(activity);

    return h('div.stack.lg',
      (missionsR.status === 'rejected' && systemR.status === 'rejected') ? errorBanner(systemR.reason, ctx) : null,
      statusline,
      quick,
      h('div.grid.cols-4', activeMissionCard, needsYouCard, agentsCard, computeCard),
      activityPanel);
  },

  onEvent(ev) {
    return ev.kind.startsWith('mission.') || ev.kind.startsWith('task.') || ev.kind.startsWith('approval.')
      || ev.kind.startsWith('resource.') || ev.kind === 'model.status' || ev.kind.startsWith('agent.');
  },
};

function buildQuickCommand(ctx, agents) {
  const qtText = textarea({ rows: 3, class: 'textarea composer-input', placeholder: 'Что должен сделать BOSSMAN?' });
  const qtAgent = agentSelect(agents, agents.length === 1 ? pick(agents[0], ['id']) : null);

  async function submit() {
    const text = qtText.value.trim();
    if (!text) { toast('Опишите задачу', { type: 'warn' }); qtText.focus(); return; }
    if (!qtAgent.value) {
      toast('Выберите агента', { type: 'warn', hint: agents.length ? 'Агент задаёт модель и system prompt.' : 'Сначала создайте агента на странице «Агенты».' });
      return;
    }
    try {
      await api.createTask({ title: titleFromPrompt(text), prompt: text, agent_id: idVal(qtAgent.value), priority: 5, run_now: true });
      qtText.value = '';
      toastOk('Задача поставлена в очередь');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось создать задачу'); }
  }
  qtText.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submit(); } });

  return h('section.panel',
    h('div.panel-head', h('h2', 'Quick Command'), h('div.spacer'), h('span.xsmall.dim', 'Ctrl/⌘ + Enter — START')),
    h('div.composer', qtText,
      h('div.composer-controls', field('Агент', qtAgent), h('div.spacer'),
        actionButton('START', submit, { cls: 'btn btn-primary', iconName: 'play' }))));
}

function buildActiveMissionCard(missions, ctx) {
  const top = missions.find((m) => m.status === 'running')
    || missions.find((m) => ['queued', 'planning'].includes(m.status));

  return h('section.panel',
    h('div.panel-head', h('h2', 'Active Mission'), h('div.spacer'),
      h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('missions') }, 'Все миссии')),
    top
      ? h('div.panel-body', h('div.stack.sm',
        h('div.row', statusBadge(top.status, { live: top.status === 'running' }), h('div.spacer')),
        h('div.small', { style: { fontWeight: 600 } }, pick(top, ['title'], `#${pick(top, ['id'])}`)),
        meter('Прогресс', Number(top.progress || 0) * 100, 100, pct(top.progress || 0))))
      : h('div.panel-body', h('div.small.dim', 'Активных миссий нет.'),
        h('button.btn.btn-sm', { type: 'button', style: { marginTop: '8px' }, onClick: () => ctx.navigate('missions') },
          icon('plus', 12), h('span', 'Новая миссия'))));
}

function buildNeedsYouCard(approvals, ctx) {
  return h('section.panel',
    h('div.panel-head', h('h2', 'Needs You'), h('div.spacer'),
      h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('approvals') }, 'Очередь')),
    h('div.panel-body',
      h('div', { class: 'big-num' + (approvals.length ? ' warn' : '') }, String(approvals.length)),
      h('div.small.dim', { style: { marginTop: '2px' } }, approvals.length ? 'ждут вашего решения' : 'ничего не ждёт решения')));
}

function buildAgentsCard(agents, graph, ctx) {
  const statusByAgent = new Map((graph.nodes || []).filter((n) => n.id.startsWith('agent:')).map((n) => [n.id.slice(6), n.status]));
  return h('section.panel',
    h('div.panel-head', h('h2', 'Agents'), h('div.spacer'),
      h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('agents') }, 'Все агенты')),
    agents.length
      ? h('div.mini-list', agents.slice(0, 6).map((a) => h('div.mini-row',
        dot(statusByAgent.get(String(pick(a, ['id']))) || 'idle', { live: statusByAgent.get(String(pick(a, ['id']))) === 'working' }),
        h('span.name', pick(a, ['name'], '')),
        h('span.xsmall.dim', statusByAgent.get(String(pick(a, ['id']))) || 'idle'))))
      : h('div.panel-body', h('div.small.dim', 'Агентов пока нет.')));
}

function buildComputeCard(sys, resources, ctx) {
  const d = sys && typeof sys === 'object' ? sys : {};
  const cur = (d.current && typeof d.current === 'object') ? d.current : d;
  const cpu = cur.cpu_pct === undefined || cur.cpu_pct === null ? null : Number(cur.cpu_pct);
  const ramUsed = resources ? resources.used_mb : cur.ram_used_mb;
  const ramTotal = resources ? resources.total_mb : cur.ram_total_mb;

  return h('section.panel',
    h('div.panel-head', h('h2', 'Compute'), h('div.spacer'),
      h('button.btn.btn-ghost.btn-sm', { type: 'button', onClick: () => ctx.navigate('resources') }, 'Ресурсы')),
    h('div.panel-body', h('div.stack.sm',
      meter('CPU', cpu ?? 0, 100, cpu === null ? '—' : `${Math.round(cpu)}%`),
      ramTotal ? meter('RAM', ramUsed || 0, ramTotal, `${fmtGb(ramUsed)} / ${fmtGb(ramTotal)} ГБ`) : h('div.small.dim', 'RAM: нет данных'),
      resources ? h('div.xsmall.dim', `доступно для новых задач: ${fmtGb(resources.available_mb)} ГБ · политика ${resources.policy}`) : null)));
}

function buildActivityPanel(activity) {
  const meaningful = activity.filter((e) => !RAW_LOG_KINDS.has(pick(e, ['kind', 'type'], '')));
  return h('section.panel',
    h('div.panel-head', icon('activity', 15), h('h2', 'Recent Activity')),
    meaningful.length
      ? h('div.feed', meaningful.slice(0, 8).map(activityRow))
      : h('div.panel-body', h('div.small.dim', 'Заметных событий пока нет.')));
}

function activityRow(e) {
  const kind = pick(e, ['kind', 'type'], 'event');
  const data = e.data && typeof e.data === 'object' ? e.data : {};
  const text = pick(e, ['message', 'text', 'title']) || pick(data, ['message', 'title', 'prompt', 'reason'])
    || (Object.keys(data).length ? JSON.stringify(data).slice(0, 140) : '');
  return h('div.feed-item',
    h('span.feed-time', fmtClock(pick(e, ['ts', 'created_at']))),
    h('span.feed-kind', kind),
    h('span.feed-text', text || '—'));
}

export default OverviewPage;
