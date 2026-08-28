/* ============================================================
   missions.js — Feature 01+13: Autopilot Missions + KPI.
   Endpoints: GET/POST /api/missions, GET /api/missions/{id},
   POST /api/missions/{id}/start|pause|resume|stop, GET /api/missions/{id}/kpi.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, icon, dot, statusBadge, statusLabel, empty, meter,
  toast, toastOk, toastError, openModal, confirmDialog, actionButton,
  field, input, textarea, fmtDateShort, fmtRelative, fmtCost, fmtNum,
} from '../components.js';
import { panel, pageHead, errorBanner, pct } from './_shared.js';

function missionTaskCount(m) {
  const plan = m && typeof m.plan === 'object' ? m.plan : null;
  return plan && Array.isArray(plan.tasks) ? plan.tasks.length : null;
}

function missionSub(m) {
  const bits = [];
  const n = missionTaskCount(m);
  if (n !== null) bits.push(`${n} задач`);
  if (m.max_workers) bits.push(`до ${m.max_workers} воркеров`);
  if (m.cloud_budget_usd) bits.push(`бюджет ${fmtCost(m.cloud_budget_usd)}`);
  return bits.join(' · ') || 'без плана';
}

const MissionsPage = {
  id: 'missions',
  title: 'Миссии',
  icon: 'bolt',
  nav: 'primary',

  async render(ctx) {
    let missions = []; let err = null;
    try { missions = listOf(await api.raw('/api/missions'), 'missions'); }
    catch (e) { err = e; }

    const active = missions.filter((m) => m.status === 'running').length;

    const head = pageHead('Миссии', missions.length
      ? `${missions.length} миссий · ${active} выполняется`
      : 'Автопилот: цель → план задач → исполнение с лимитом воркеров', [
      h('button.btn.btn-primary', { type: 'button', onClick: () => openCreateMission(ctx) },
        icon('plus', 14), h('span', 'Новая миссия')),
    ]);

    const body = err
      ? errorBanner(err, ctx)
      : missions.length
        ? h('div.grid.auto-lg', missions.map((m) => missionCard(m, ctx)))
        : h('section.panel', empty({
          iconName: 'bolt',
          title: 'Миссий пока нет',
          hint: 'Миссия — это цель, а не одна задача: BOSSMAN сам строит план из подзадач и ведёт их до результата, с лимитом воркеров и KPI.',
          action: h('button.btn.btn-primary', { type: 'button', onClick: () => openCreateMission(ctx) },
            icon('plus', 14), h('span', 'Новая миссия')),
        }));

    return h('div.stack.lg', head, body);
  },

  onEvent(ev) { return ev.kind.startsWith('mission.'); },
};

function missionCard(m, ctx) {
  const id = pick(m, ['id']);
  const status = String(m.status || 'draft');
  const progress = Number(m.progress || 0);

  return h('div.card.clickable', { onClick: () => openMissionDetail(ctx, id), style: { cursor: 'pointer' } },
    h('div.card-head',
      h('div', { style: { flex: '1', minWidth: 0 } },
        h('div.card-title', pick(m, ['title'], `миссия #${id}`)),
        h('div.card-sub', missionSub(m))),
      statusBadge(status, { live: status === 'running' })),

    m.goal ? h('div.xsmall.dim.wrap-any', String(m.goal).slice(0, 160)) : null,

    meter('Прогресс', progress * 100, 100, pct(progress)),

    h('div.row.tight',
      h('span.xsmall.dim', `создана ${fmtRelative(pick(m, ['created_at']))}`),
      m.started_at ? h('span.xsmall.dim', `· старт ${fmtDateShort(m.started_at)}`) : null),

    h('div.card-actions', { onClick: (e) => e.stopPropagation() }, missionActions(m, id, ctx)));
}

function missionActions(m, id, ctx) {
  const status = String(m.status || 'draft');
  const call = async (action) => {
    try {
      await api.raw(`/api/missions/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
      toastOk('Готово');
      ctx.refresh();
    } catch (e) { toastError(e, 'Не удалось выполнить действие'); }
  };
  const btns = [];
  if (['draft', 'planning', 'queued', 'paused'].includes(status)) {
    btns.push(actionButton(status === 'paused' ? 'Resume' : 'Start', () => call(status === 'paused' ? 'resume' : 'start'),
      { cls: 'btn btn-sm btn-primary', iconName: 'play' }));
  }
  if (status === 'running') {
    btns.push(actionButton('Pause', () => call('pause'), { cls: 'btn btn-sm', iconName: 'pause' }));
  }
  if (['running', 'paused', 'queued'].includes(status)) {
    btns.push(actionButton('Stop', async () => {
      const ok = await confirmDialog({ title: 'Остановить миссию?', text: 'Активные задачи миссии будут остановлены.', okText: 'Остановить', danger: true });
      if (!ok) return;
      await call('stop');
    }, { cls: 'btn btn-sm btn-danger', iconName: 'stop' }));
  }
  return btns;
}

/* ---------------- Детали миссии ---------------- */

async function openMissionDetail(ctx, id) {
  const modal = openModal({ title: `Миссия #${id}`, wide: true, body: h('div.small.dim', 'Загрузка…'), footer: h('div') });
  let mission;
  try {
    mission = await api.raw(`/api/missions/${encodeURIComponent(id)}`);
  } catch (e) {
    modal.body.textContent = '';
    modal.body.appendChild(h('div.small', { style: { color: 'var(--err)' } }, e.message || 'Не удалось загрузить миссию'));
    return;
  }
  modal.el.querySelector('.modal-head h2').textContent = pick(mission, ['title'], `Миссия #${id}`);
  renderDetail();

  function renderDetail() {
    modal.body.textContent = '';
    modal.footer.textContent = '';
    const status = String(mission.status || 'draft');
    const kpi = mission.kpi || { current: {}, targets: mission.kpi_targets || {} };
    const targets = kpi.targets || {};
    const current = kpi.current || {};
    const kpiKeys = Object.keys(targets);

    const tasks = listOf(mission.tasks, 'tasks');

    modal.body.appendChild(h('div.stack',
      h('div.row', statusBadge(status, { live: status === 'running' }),
        h('div.spacer'),
        h('span.xsmall.dim', `создана ${fmtDateShort(pick(mission, ['created_at']))}`)),
      mission.goal ? h('div.small.dim.wrap-any', mission.goal) : null,
      meter('Прогресс миссии', Number(mission.progress || 0) * 100, 100, pct(mission.progress || 0)),

      kpiKeys.length ? panel('KPI', h('div.stack.sm',
        kpiKeys.map((k) => meter(k, Number(current[k] || 0), Number(targets[k] || 1),
          `${fmtNum(current[k] || 0, 1)} / ${fmtNum(targets[k], 1)}`)))) : null,

      panel(`План (${tasks.length})`, tasks.length
        ? h('div.mini-list', tasks.map((t) => h('div.mini-row',
          dot(t.status, { live: t.status === 'running' }),
          h('span.name', pick(t, ['title'], `задача #${pick(t, ['id'])}`)),
          h('span.badge', statusLabel(t.status)))))
        : h('div.small.dim', 'План пуст.'))));

    modal.footer.appendChild(h('div.spacer'));
    modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
    for (const btn of missionActionsWide(mission, id, () => refreshDetail())) modal.footer.appendChild(btn);
  }

  async function refreshDetail() {
    try { mission = await api.raw(`/api/missions/${encodeURIComponent(id)}`); renderDetail(); ctx.refresh(); }
    catch (e) { toastError(e, 'Не удалось обновить миссию'); }
  }
}

function missionActionsWide(m, id, onDone) {
  const status = String(m.status || 'draft');
  const call = async (action) => {
    try {
      await api.raw(`/api/missions/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
      toastOk('Готово');
      await onDone();
    } catch (e) { toastError(e, 'Не удалось выполнить действие'); }
  };
  const btns = [];
  if (['draft', 'planning', 'queued', 'paused'].includes(status)) {
    btns.push(actionButton(status === 'paused' ? 'Resume' : 'Start', () => call(status === 'paused' ? 'resume' : 'start'),
      { cls: 'btn btn-primary', iconName: 'play' }));
  }
  if (status === 'running') btns.push(actionButton('Pause', () => call('pause'), { cls: 'btn', iconName: 'pause' }));
  if (['running', 'paused', 'queued'].includes(status)) {
    btns.push(actionButton('Stop', async () => {
      const ok = await confirmDialog({ title: 'Остановить миссию?', okText: 'Остановить', danger: true });
      if (!ok) return;
      await call('stop');
    }, { cls: 'btn btn-danger', iconName: 'stop' }));
  }
  return btns;
}

/* ---------------- Новая миссия ---------------- */

function openCreateMission(ctx) {
  const titleEl = input({ placeholder: 'Исследовать конкурентов рынка X' });
  const goalEl = textarea({ rows: 4, placeholder: 'Опиши цель миссии. Число в тексте («5 задач») подсказывает планировщику размер плана.' });
  const durationEl = input({ type: 'number', min: '0', placeholder: 'без ограничения', class: 'input mono' });
  const workersEl = input({ type: 'number', min: '1', max: '32', value: '2', class: 'input mono' });
  const budgetEl = input({ type: 'number', min: '0', step: '0.5', value: '0', class: 'input mono' });

  const kpiRows = h('div.stack.sm');
  const kpiPairs = [];
  function addKpiRow(key = '', target = '') {
    const keyEl = input({ placeholder: 'ключ, напр. analyzed', value: key });
    const targetEl = input({ type: 'number', placeholder: 'цель', value: target, class: 'input mono', style: { maxWidth: '120px' } });
    const row = h('div.row.tight', keyEl, targetEl,
      h('button.btn.btn-sm.btn-ghost', { type: 'button', onClick: () => { row.remove(); const i = kpiPairs.findIndex((p) => p.row === row); if (i >= 0) kpiPairs.splice(i, 1); } }, icon('trash', 12)));
    kpiPairs.push({ row, keyEl, targetEl });
    kpiRows.appendChild(row);
  }
  addKpiRow();

  const modal = openModal({
    title: 'Новая миссия',
    wide: true,
    body: h('div.stack',
      field('Название', titleEl),
      field('Цель', goalEl, 'Свободный текст — из него строится детерминированный план подзадач.'),
      h('div.grid.cols-3',
        field('Длительность, мин', durationEl, 'Пусто — без лимита времени.'),
        field('Макс. воркеров', workersEl),
        field('Облачный бюджет, $', budgetEl)),
      field('KPI-цели', h('div.stack.sm', kpiRows,
        h('button.btn.btn-sm', { type: 'button', onClick: () => addKpiRow() }, icon('plus', 12), h('span', 'Ещё KPI'))),
        'Необязательно: ключ метрики и целевое значение — прогресс миссии считается по ним.')),
    footer: (handle) => [
      h('div.spacer'),
      h('button.btn', { type: 'button', onClick: () => handle.close() }, 'Отмена'),
      actionButton('Создать', async () => {
        const title = titleEl.value.trim();
        if (!title) { toast('Укажите название миссии', { type: 'warn' }); titleEl.focus(); return; }
        const kpi_targets = {};
        for (const p of kpiPairs) {
          const k = p.keyEl.value.trim();
          if (k && p.targetEl.value !== '') kpi_targets[k] = Number(p.targetEl.value) || 0;
        }
        try {
          await api.raw('/api/missions', {
            method: 'POST',
            body: {
              title,
              goal: goalEl.value.trim() || title,
              duration_minutes: durationEl.value ? Number(durationEl.value) : null,
              max_workers: Number(workersEl.value) || 1,
              cloud_budget_usd: Number(budgetEl.value) || 0,
              kpi_targets,
            },
          });
          handle.close();
          toastOk('Миссия создана и поставлена в очередь');
          ctx.refresh();
        } catch (e) { toastError(e, 'Не удалось создать миссию'); }
      }, { cls: 'btn btn-primary', iconName: 'check' }),
    ],
  });
  return modal;
}

export default MissionsPage;
