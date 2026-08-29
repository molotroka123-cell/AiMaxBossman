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
import { idVal } from './_shared.js';
import { panel, pageHead, errorNote } from './_ui.js';

const forkState = { taskId: '', runId: '' };

const ForksPage = {
  id: 'forks',
  title: 'Развилки',
  icon: 'retry',
  nav: 'more',

  async render(ctx) {
    let tasks = []; let err = null;
    try { tasks = listOf(await api.tasks(), 'tasks'); } catch (e) { err = e; }
    const withRuns = tasks; // last_run присутствует у всех — фильтровать не будем, чтобы не скрывать draft-задачи

    const head = pageHead('Развилки', 'Продолжить любой запуск с любой сохранённой точки — с новой инструкцией, другим агентом или моделью.');
    if (err) return h('div.bx-page', head, errorNote(err, () => ctx.refresh()));

    const taskEl = select(
      [{ value: '', label: 'выберите задачу' }, ...withRuns.map((t) => ({ value: pick(t, ['id']), label: `#${pick(t, ['id'])} · ${pick(t, ['title'], '')}`.slice(0, 60) }))],
      { value: forkState.taskId },
    );
    const runOut = h('div.small.dim', 'Выберите задачу, чтобы увидеть её запуски.');
    const cpOut = h('div.small.dim', 'Выберите запуск, чтобы увидеть сохранённые точки.');
    const lineageOut = h('div.small.dim', '—');

    taskEl.addEventListener('change', async () => {
      forkState.taskId = taskEl.value;
      forkState.runId = '';
      await loadRuns();
      await loadLineage();
      cpOut.textContent = ''; cpOut.appendChild(h('div.small.dim', 'Выберите запуск.'));
    });

    async function loadRuns() {
      runOut.textContent = '';
      if (!forkState.taskId) { runOut.appendChild(h('div.small.dim', 'Выберите задачу.')); return; }
      runOut.appendChild(h('div.small.dim', 'Загрузка запусков…'));
      try {
        const detail = await api.task(idVal(forkState.taskId));
        const runs = listOf(detail.runs, 'runs');
        runOut.textContent = '';
        if (!runs.length) { runOut.appendChild(h('div.small.dim', 'У задачи ещё не было запусков.')); return; }
        const runEl = select(runs.map((r) => ({
          value: pick(r, ['id']),
          label: `запуск #${pick(r, ['id'])} · ${statusLabelOf(r.status)}${r.model_alias ? ` · ${r.model_alias}` : ''}`,
        })), { value: forkState.runId });
        runEl.addEventListener('change', async () => { forkState.runId = runEl.value; await loadCheckpoints(ctx); });
        runOut.appendChild(field('Запуск', runEl));
        if (!forkState.runId && runs.length) { forkState.runId = String(pick(runs[runs.length - 1], ['id'])); runEl.value = forkState.runId; }
        await loadCheckpoints(ctx);
      } catch (e) { runOut.textContent = ''; runOut.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message)); }
    }

    async function loadCheckpoints(ctx2) {
      cpOut.textContent = '';
      if (!forkState.runId) { cpOut.appendChild(h('div.small.dim', 'Выберите запуск.')); return; }
      cpOut.appendChild(h('div.small.dim', 'Загрузка точек…'));
      try {
        const cps = await api.raw(`/api/runs/${encodeURIComponent(forkState.runId)}/checkpoints`);
        cpOut.textContent = '';
        if (!cps.length) { cpOut.appendChild(h('div.small.dim', 'У этого запуска ещё нет сохранённых точек.')); return; }
        cpOut.appendChild(h('div.mini-list', cps.map((cp) => h('div.mini-row',
          h('span.badge.mono', `шаг ${cp.step}`),
          h('span.name', cp.note || `точка #${cp.id}`),
          h('span.xsmall.dim', fmtDateShort(cp.created_at)),
          actionButton('Ответвить', () => openForkModal(ctx2, forkState.runId, cp), { cls: 'btn btn-sm btn-primary', iconName: 'retry' })))));
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

    return h('div.bx-page', head,
      panel('Выбор запуска', h('div.stack.sm', field('Задача', taskEl), runOut)),
      panel('Сохранённые точки', cpOut),
      panel('Дерево ответвлений', lineageOut));
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
    title: `Ответвление · запуск #${runId}, шаг ${cp.step}`,
    body: h('div.stack',
      cp.note ? h('div.small.dim', cp.note) : null,
      field('Что сделать иначе в продолжении', instrEl),
      field('Другой агент (по желанию)', agentSel),
      field('Другая модель (по желанию)', modelSel)),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Создать ответвление', async () => {
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
          toastOk('Ответвление создано', `новая задача #${r.new_task_id}`);
          ctx.navigate('tasks', { task: r.new_task_id });
        } catch (e) { toastError(e, 'Не удалось создать ответвление'); }
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
