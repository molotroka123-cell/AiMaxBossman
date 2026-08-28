/* ============================================================
   forks.js — Feature 05: Replay / Fork Session.
   Endpoints: GET /api/runs/{run_id}/checkpoints, POST /api/runs/{run_id}/fork,
   GET /api/forks?task_id=.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, dot,
  toastOk, toastError, openModal, actionButton,
  field, textarea, select, fmtDateShort,
} from '../components.js';
import { idVal, panel, pageHead, errorBanner } from './_shared.js';

const forkState = { taskId: '', runId: '' };

const ForksPage = {
  id: 'forks',
  title: 'Форки сессий',
  icon: 'retry',
  nav: 'more',

  async render(ctx) {
    let tasks = []; let err = null;
    try { tasks = listOf(await api.tasks(), 'tasks'); } catch (e) { err = e; }
    const withRuns = tasks; // last_run присутствует у всех — фильтровать не будем, чтобы не скрывать draft-задачи

    const head = pageHead('Форки сессий', 'Ответвиться от любого чекпоинта прогона с новой инструкцией, агентом или моделью');
    if (err) return h('div.stack.lg', head, errorBanner(err, ctx));

    const taskEl = select(
      [{ value: '', label: 'выберите задачу' }, ...withRuns.map((t) => ({ value: pick(t, ['id']), label: `#${pick(t, ['id'])} · ${pick(t, ['title'], '')}`.slice(0, 60) }))],
      { value: forkState.taskId },
    );
    const runOut = h('div.small.dim', 'Выберите задачу, чтобы увидеть её прогоны.');
    const cpOut = h('div.small.dim', 'Выберите прогон, чтобы увидеть чекпоинты.');
    const lineageOut = h('div.small.dim', '—');

    taskEl.addEventListener('change', async () => {
      forkState.taskId = taskEl.value;
      forkState.runId = '';
      await loadRuns();
      await loadLineage();
      cpOut.textContent = ''; cpOut.appendChild(h('div.small.dim', 'Выберите прогон.'));
    });

    async function loadRuns() {
      runOut.textContent = '';
      if (!forkState.taskId) { runOut.appendChild(h('div.small.dim', 'Выберите задачу.')); return; }
      runOut.appendChild(h('div.small.dim', 'Загрузка прогонов…'));
      try {
        const detail = await api.task(idVal(forkState.taskId));
        const runs = listOf(detail.runs, 'runs');
        runOut.textContent = '';
        if (!runs.length) { runOut.appendChild(h('div.small.dim', 'У задачи ещё нет прогонов.')); return; }
        const runEl = select(runs.map((r) => ({
          value: pick(r, ['id']),
          label: `run #${pick(r, ['id'])} · ${statusLabelOf(r.status)}${r.model_alias ? ` · ${r.model_alias}` : ''}`,
        })), { value: forkState.runId });
        runEl.addEventListener('change', async () => { forkState.runId = runEl.value; await loadCheckpoints(ctx); });
        runOut.appendChild(field('Прогон', runEl));
        if (!forkState.runId && runs.length) { forkState.runId = String(pick(runs[runs.length - 1], ['id'])); runEl.value = forkState.runId; }
        await loadCheckpoints(ctx);
      } catch (e) { runOut.textContent = ''; runOut.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message)); }
    }

    async function loadCheckpoints(ctx2) {
      cpOut.textContent = '';
      if (!forkState.runId) { cpOut.appendChild(h('div.small.dim', 'Выберите прогон.')); return; }
      cpOut.appendChild(h('div.small.dim', 'Загрузка чекпоинтов…'));
      try {
        const cps = await api.raw(`/api/runs/${encodeURIComponent(forkState.runId)}/checkpoints`);
        cpOut.textContent = '';
        if (!cps.length) { cpOut.appendChild(h('div.small.dim', 'У этого прогона ещё нет чекпоинтов.')); return; }
        cpOut.appendChild(h('div.mini-list', cps.map((cp) => h('div.mini-row',
          h('span.badge.mono', `шаг ${cp.step}`),
          h('span.name', cp.note || `checkpoint #${cp.id}`),
          h('span.xsmall.dim', fmtDateShort(cp.created_at)),
          actionButton('Fork', () => openForkModal(ctx2, forkState.runId, cp), { cls: 'btn btn-sm btn-primary', iconName: 'retry' })))));
      } catch (e) { cpOut.textContent = ''; cpOut.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message)); }
    }

    async function loadLineage() {
      lineageOut.textContent = '';
      if (!forkState.taskId) { lineageOut.appendChild(h('div.small.dim', '—')); return; }
      try {
        const tree = await api.raw(`/api/forks?task_id=${encodeURIComponent(forkState.taskId)}`);
        lineageOut.appendChild(lineageNode(tree, 0));
      } catch (e) { lineageOut.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message)); }
    }

    if (forkState.taskId) { loadRuns(); loadLineage(); }

    return h('div.stack.lg', head,
      panel('Выбор прогона', h('div.stack.sm', field('Задача', taskEl), runOut)),
      panel('Чекпоинты', cpOut),
      panel('Дерево форков', lineageOut));
  },

  onEvent(ev) { return ev.kind === 'session.forked'; },
};

function statusLabelOf(s) { return s || 'draft'; }

function lineageNode(node, depth) {
  return h('div.stack.sm', { style: { marginLeft: `${depth * 18}px` } },
    h('div.row.tight', dot(node.status), h('span.small', `#${node.id} · ${node.title || ''}`.slice(0, 60))),
    (node.forks || []).map((f) => lineageNode(f, depth + 1)));
}

function openForkModal(ctx, runId, cp) {
  const instrEl = textarea({ rows: 4, placeholder: 'Что изменить в продолжении (необязательно) — иначе продолжится как есть' });
  const agentSel = h('div.small.dim', 'Загрузка агентов…');
  const modelSel = h('div.small.dim', 'Загрузка моделей…');
  let agentEl = null; let modelEl = null;

  const modal = openModal({
    title: `Fork · run #${runId}, шаг ${cp.step}`,
    body: h('div.stack',
      cp.note ? h('div.small.dim', cp.note) : null,
      field('Инструкция для продолжения', instrEl),
      field('Агент (переопределить)', agentSel),
      field('Модель (переопределить)', modelSel)),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Создать форк', async () => {
        try {
          const r = await api.raw(`/api/runs/${encodeURIComponent(runId)}/fork`, {
            method: 'POST',
            body: {
              checkpoint_id: cp.id,
              instruction: instrEl.value.trim() || undefined,
              agent_id: agentEl ? idVal(agentEl.value) : null,
              model_id: modelEl ? idVal(modelEl.value) : null,
            },
          });
          handle.close();
          toastOk('Форк создан', `новая задача #${r.new_task_id}`);
          ctx.navigate('tasks', { task: r.new_task_id });
        } catch (e) { toastError(e, 'Не удалось создать форк'); }
      }, { cls: 'btn btn-primary', iconName: 'retry' }),
    ],
  });

  (async () => {
    try {
      const agents = listOf(await api.agents(), 'agents');
      agentEl = select([{ value: '', label: '— как в исходной задаче —' }, ...agents.map((a) => ({ value: pick(a, ['id']), label: pick(a, ['name'], '') }))]);
      agentSel.replaceWith(agentEl);
    } catch { agentSel.textContent = 'недоступно'; }
    try {
      const models = listOf(await api.models(), 'models');
      modelEl = select([{ value: '', label: '— роутер/агент решает —' }, ...models.map((m) => ({ value: pick(m, ['id']), label: pick(m, ['alias', 'name'], '') }))]);
      modelSel.replaceWith(modelEl);
    } catch { modelSel.textContent = 'недоступно'; }
  })();
}

export default ForksPage;
