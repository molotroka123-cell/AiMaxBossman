/* ============================================================
   missions.js — крупные цели («миссии»): BOSSMAN сам разбивает
   цель на шаги и ведёт их до результата.
   Endpoints: GET/POST /api/missions, GET /api/missions/{id},
   POST /api/missions/{id}/start|pause|resume|stop.
   ============================================================ */

import { api, listOf, pick } from '../api.js';
import {
  h, icon, dot,
  toast, toastOk, toastError, openModal, confirmDialog, actionButton,
  field, input, textarea, fmtDateShort, fmtRelative, fmtCost, fmtNum,
} from '../components.js';
import {
  pageHead, panel, tile, tag, meter, btn, blank, errorNote,
  statusPill, statusText, plural,
} from './_ui.js';

function missionStepCount(m) {
  const plan = m && typeof m.plan === 'object' ? m.plan : null;
  return plan && Array.isArray(plan.tasks) ? plan.tasks.length : null;
}

function missionTags(m) {
  const tags = [];
  const n = missionStepCount(m);
  if (n !== null) tags.push(tag(plural(n, 'шаг', 'шага', 'шагов')));
  if (m.max_workers) tags.push(tag(`до ${plural(m.max_workers, 'помощника', 'помощников', 'помощников')}`));
  if (m.cloud_budget_usd) tags.push(tag(`бюджет ${fmtCost(m.cloud_budget_usd)}`));
  return tags;
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

    const head = pageHead('Миссии',
      'Большая цель, а не одна задача: опишите, чего хотите — BOSSMAN сам разложит её на шаги и доведёт до результата.',
      {
        actions: [btn('Новая миссия', () => openCreateMission(ctx), { variant: 'primary', iconName: 'plus' })],
      });

    const body = err
      ? errorNote(err, () => ctx.refresh())
      : missions.length
        ? h('div.bx-cards.is-wide', missions.map((m) => missionCard(m, ctx)))
        : blank({
          iconName: 'bolt',
          title: 'Миссий пока нет',
          hint: 'Миссия — это цель целиком. BOSSMAN сам построит план из шагов и будет вести их, распределяя работу между помощниками.',
          action: btn('Создать первую миссию', () => openCreateMission(ctx), { variant: 'primary', iconName: 'plus' }),
        });

    return h('div.bx-page', head, body);
  },

  onEvent(ev) { return ev.kind.startsWith('mission.'); },
};

function missionCard(m, ctx) {
  const id = pick(m, ['id']);
  const status = String(m.status || 'draft');
  const progress = Number(m.progress || 0);
  const s = statusText(status);

  return tile({
    accent: 'var(--bx-violet)',
    onClick: () => openMissionDetail(ctx, id),
    title: pick(m, ['title'], `Миссия #${id}`),
    sub: `создана ${fmtRelative(pick(m, ['created_at']))}`,
    statusNode: statusPill(status),
    tags: missionTags(m),
    body: [
      m.goal ? h('p.bx-tile-text', String(m.goal).slice(0, 160)) : null,
      meter('Готово', progress * 100, 100, `${Math.round(progress * 100)}%`, { accent: 'var(--bx-violet)' }),
    ],
    actions: missionActions(m, id, ctx),
  });
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
    btns.push(btn(status === 'paused' ? 'Продолжить' : 'Запустить',
      () => call(status === 'paused' ? 'resume' : 'start'),
      { variant: 'primary', size: 'sm', iconName: 'play' }));
  }
  if (status === 'running') {
    btns.push(btn('Пауза', () => call('pause'), { variant: 'secondary', size: 'sm', iconName: 'pause' }));
  }
  if (['running', 'paused', 'queued'].includes(status)) {
    btns.push(btn('Остановить', async () => {
      const ok = await confirmDialog({ title: 'Остановить миссию?', text: 'Все шаги, которые сейчас идут, будут остановлены.', okText: 'Остановить', danger: true });
      if (!ok) return;
      await call('stop');
    }, { variant: 'subtle', size: 'sm', iconName: 'stop' }));
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
      h('div.row', statusPill(status),
        h('div.spacer'),
        h('span.xsmall.dim', `создана ${fmtDateShort(pick(mission, ['created_at']))}`)),
      mission.goal ? h('div.small.dim.wrap-any', mission.goal) : null,
      h('div', { style: { marginTop: '4px' } },
        meter('Готово', Number(mission.progress || 0) * 100, 100, `${Math.round(Number(mission.progress || 0) * 100)}%`, { accent: 'var(--bx-violet)' })),

      kpiKeys.length ? panel('Показатели цели', h('div.stack.sm',
        kpiKeys.map((k) => meter(k, Number(current[k] || 0), Number(targets[k] || 1),
          `${fmtNum(current[k] || 0, 1)} из ${fmtNum(targets[k], 1)}`)))) : null,

      panel(`Шаги плана · ${tasks.length}`, tasks.length
        ? h('div.mini-list', tasks.map((t) => h('div.mini-row',
          dot(t.status, { live: t.status === 'running' }),
          h('span.name', pick(t, ['title'], `Шаг #${pick(t, ['id'])}`)),
          h('span.badge', statusText(t.status).word))))
        : h('div.small.dim', 'План пока пуст.'))));

    modal.footer.appendChild(h('div.spacer'));
    modal.footer.appendChild(h('button.btn', { type: 'button', onClick: () => modal.close() }, 'Закрыть'));
    for (const b of missionActionsWide(mission, id, () => refreshDetail())) modal.footer.appendChild(b);
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
    btns.push(actionButton(status === 'paused' ? 'Продолжить' : 'Запустить', () => call(status === 'paused' ? 'resume' : 'start'),
      { cls: 'btn btn-primary', iconName: 'play' }));
  }
  if (status === 'running') btns.push(actionButton('Пауза', () => call('pause'), { cls: 'btn', iconName: 'pause' }));
  if (['running', 'paused', 'queued'].includes(status)) {
    btns.push(actionButton('Остановить', async () => {
      const ok = await confirmDialog({ title: 'Остановить миссию?', okText: 'Остановить', danger: true });
      if (!ok) return;
      await call('stop');
    }, { cls: 'btn btn-danger', iconName: 'stop' }));
  }
  return btns;
}

/* ---------------- Новая миссия ---------------- */

function openCreateMission(ctx) {
  const titleEl = input({ placeholder: 'Например: изучить конкурентов рынка' });
  const goalEl = textarea({ rows: 4, placeholder: 'Опишите цель обычными словами. Если укажете число («5 конкурентов»), BOSSMAN учтёт его при составлении плана.' });
  const durationEl = input({ type: 'number', min: '0', placeholder: 'без ограничения', class: 'input mono' });
  const workersEl = input({ type: 'number', min: '1', max: '32', value: '2', class: 'input mono' });
  const budgetEl = input({ type: 'number', min: '0', step: '0.5', value: '0', class: 'input mono' });

  const kpiRows = h('div.stack.sm');
  const kpiPairs = [];
  function addKpiRow(key = '', target = '') {
    const keyEl = input({ placeholder: 'что считаем, напр. «постов»', value: key });
    const targetEl = input({ type: 'number', placeholder: 'сколько', value: target, class: 'input mono', style: { maxWidth: '120px' } });
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
      field('Цель', goalEl, 'Свободный текст — по нему строится план из шагов.'),
      h('div.grid.cols-3',
        field('Сколько минут максимум', durationEl, 'Пусто — без ограничения по времени.'),
        field('Помощников одновременно', workersEl),
        field('Бюджет на облако, $', budgetEl, 'Сколько не жалко потратить на платные вызовы.')),
      field('Что считать успехом', h('div.stack.sm', kpiRows,
        h('button.btn.btn-sm', { type: 'button', onClick: () => addKpiRow() }, icon('plus', 12), h('span', 'Ещё показатель'))),
        'Необязательно: что и до какого числа довести — по этому считается готовность миссии.')),
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
